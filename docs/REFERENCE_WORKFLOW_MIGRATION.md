# Reference workflow migration — Navitaire + Timatic

## Purpose and scope

This document records what was ported from the uploaded repository snapshots into the LangGraph/FastAPI POC. The reference repositories are the source for workflow structure, not a blanket source of current Emirates policy. Provider calls run only through opt-in protected diagnostics; the passenger-facing website remains `MOCK_MODE=true` and cannot mutate a live reservation or issue a real boarding pass.

## Source locations reviewed

Primary reference: `web-check-in-agent-releases-web-check-in-agent-1.0.7`.

- `app/infrastructure/external/navitaire/client.py` — GraphQL booking retrieval, current booking state, per-journey check-in requirements, passenger document check, optional booking comments.
- `app/domain/services/booking_service.py` — booking retrieval + per-journey SSR requirement enrichment.
- `app/domain/services/timatic.py` — Timatic request creation per passenger × journey, token acquisition/caching, `evaluationResult` parsing.
- `app/application/workflows/document_validation/workflow.py` and `executors/` — limit check, build/reuse requests, fan-out, Timatic call, parse/update status, conditional Navitaire document check, aggregate results.
- `app/domain/services/codified_eligibility.py` and `app/shared/utils/validation_tasks.py` — concurrent rule checks, payment/status, names, adult/infant, carrier/codeshare, queues, SSR requirements and check-in window.
- `app/application/workflows/document_update/` and `app/infrastructure/external/skyplus/client.py` — separate natural-language document-update workflow using the SkyPlus passenger-documents API. This is a different service and operation from Timatic validation and the SkyPlus upgrade APIs shown in Bruno.
- `feature-Nova-31`, `feature-Nova-38`, `DomesticCodified`, and `ValidationByAgent` — architectural snapshots for agent/page separation, eligibility validation and supporting workflows.

## Migrated execution path

`POST /api/v1/provider-tests/workflows/document-validation` implements the read-only provider validation sequence:

1. Validate provider diagnostics are explicitly enabled and the caller holds the configured local diagnostics token.
2. Send `BookingRetrievev3` with record locator and surname to the configured Navitaire endpoint.
3. Reuse the same `httpx.AsyncClient`/cookie jar for the follow-up `GET /api/nsk/v1/booking`, as the reference client reuses a persistent HTTP client. Verify a locator returned in the REST response if one is present.
4. Normalize the returned booking into internal fields (journeys/segments, passenger names/type, travel documents, DOB, gender, nationality/residence/birth country, paid status, balance and queues). Unknown or missing fields are not fabricated for a live Timatic request.
5. Fetch Navitaire check-in requirements for each journey. Reject malformed, failed or restricted responses; unlike one source helper that skipped requirement errors, this migration fails closed on unresolved mandatory data.
6. Run deterministic reference-derived booking checks concurrently: journey/time window, booking status, payment/balance, passenger name fields, adult presence, configured carrier/codeshare, queues, SSR requirement results and the source-specific international-infant condition. These rules are labeled reference-derived and are not automatically valid Emirates policy.
7. Build a Timatic payload for every passenger × journey combination. Use each passenger's actual mapped document list, itinerary segments, date/times, airports, carrier, configured processing entity, passenger DOB, actual birth country, nationality, gender and residence country. Generate distinct opaque `passengerId` and `transactionId` values. Never substitute nationality for birth country in live mode.
8. Call the Timatic document endpoint concurrently within a bounded semaphore. Only the reference schema's `evaluationResult=YES` maps to `PASS`; `NO` maps to `FAIL`; `CONDITIONAL`, missing or unknown values map to `REVIEW_REQUIRED`.
9. Only after a Timatic `PASS`, call Navitaire `GET /api/nsk/v1/booking/passengers/{passenger_key}/documents/check`. Reference status enum: `Unknown=0` → review, `Ok=1` → pass, `Conditional=2` → review, `NotOk=3` → fail.
10. Aggregate all passenger/journey outcomes: any `FAIL` means aggregate `FAIL`; otherwise any review/unmapped outcome means `REVIEW_REQUIRED`; only all explicit passes means `PASS`.
11. Return indexed results and counts only. No raw PNR/passenger/document key, token, raw request or provider response is included. No document update, seat/ancillary change, check-in mutation or boarding pass is executed.

## Codified eligibility validators migrated from 1.0.7

The release registers `determine_flight_type` plus ten business validation operations (`validate_journey`, `validate_payment_status`, `validate_pnr_status`, `validate_passenger_name`, `validate_unaccompanied_minor`, `validate_codeshare_segment`, `validate_queue_code`, `validate_checkin_window`, `validate_ssr_requirements`, and `validate_international_infant`). The POC represents these as deterministic typed rules in `backend/app/rules.py`, evaluated concurrently and aggregated; the provider workflow supplies normalized New Skies booking fields and actual journey SSR results to the same rule layer.

| Reference validator | Migrated behavior | Deliberate safety/portability correction |
|---|---|---|
| `determine_flight_type` | A booking is international if any segment is marked international; output includes overall flight type. | Missing/unknown itinerary data cannot be used to invent real provider request fields. |
| `validate_journey` | Requires a non-empty, structurally usable itinerary. | Also requires origin and destination for each journey; live Timatic requires full segment dates/times/airports. |
| `validate_payment_status` | Requires `PaidInFull` and zero balance due; negative or unknown balance fails. | Unknown values fail closed. |
| `validate_pnr_status` | Requires the provider status `Confirmed`. | Unknown status fails closed after normalization. |
| `validate_passenger_name` | Requires first/last/title and the reference length limits. | Missing or malformed passenger structures fail closed. |
| `validate_unaccompanied_minor` | Reference checks that at least one `ADT` passenger is present. | Reference JSON omits `is_valid` in this validator, so its batch aggregator may treat it as valid by default. The POC fixes this by emitting an explicit Boolean rule result. |
| `validate_codeshare_segment` | Checks external identifiers and carrier codes. | Original reference hardcodes `6E`; POC requires an explicitly configured carrier and blocks codeshare/carrier mismatch rather than treating `6E` as Emirates policy. |
| `validate_queue_code` | Queues must be empty. | Malformed queue data fails closed. |
| `validate_checkin_window` | Reference window is 1–48 hours for domestic and 1–24 hours for international flights. | The source aggregates with an `any journey passes` pattern. POC requires every journey to pass its applicable window so a non-eligible leg cannot be masked by another leg. These are reference-derived windows, not confirmed Emirates policy. |
| `validate_ssr_requirements` | Requires valid check-in requirements across journeys and checks passenger/document/address/SSR restrictions. | The reference treats an unexpected SSR payload shape as valid in one path; POC blocks unknown or missing requirement shapes. |
| `validate_international_infant` | The reference blocks web check-in when an international booking contains infant data. | Preserved as a clearly labeled reference-derived rule only; applicability must be confirmed by the target airline before any live policy use. |

Independent rule evaluation is performed in parallel, then a deterministic all-rules gate produces the aggregate. LLM output does not participate in the allow/block decision.

## Reference-derived data details and corrections

- Original booking query operation: `BookingRetrievev3` at `/api/v2/graph/BookingRetrieve`; follow-up booking state: `/api/nsk/v1/booking`.
- Journey requirement endpoint: `/api/nsk/v2/booking/checkin/journey/{journey_key}/requirements`.
- Passenger document-check endpoint: `/api/nsk/v1/booking/passengers/{passenger_key}/documents/check`.
- Timatic paths visible in Bruno: `/security-token-service/oauth2/token`, `/query-interface-service/api/v1/locations/COUNTRY`, `/query-interface-service/api/v1/parameters`, `/query-interface-service/api/v1/documentRequest`.
- Document mapping accepts the source Navitaire codes `P`, `V`, `OCI` and the canonical values shown in Bruno (`PASSPORT`, `VVV`). Unsupported codes are not silently reclassified.
- Carrier and processing entity are explicit settings. Carrier and processing entity are explicit settings. Any third-party carrier code must be confirmed with the exact authorised environment; never reuse a sample carrier code with an Emirates tenant or treat another provider’s staging response as Emirates validation.
- Unlike the source's hardcoded `6E` and `VACATION`, the live mapper does not infer carrier or purpose from a brand assumption. `TIMATIC_PROCESSING_ENTITY` and `NAVITAIRE_ALLOWED_CARRIER` must be filled from the authorized environment. No live call is made through the reference workflow when these settings are missing.
- Gender normalization accepts documented values `1/M` as M and `2/F` as F; other values remain missing and cannot pass live payload validation.
- The source has a permissive Timatic parser branch that can report success when no error text is found. This migration intentionally corrects that: no known top-level Timatic decision means review, never success.
- The original client sets `verify_ssl=False` in some branches and logs sensitive material in some code paths. The new adapters enforce HTTPS/TLS verification, bound timeouts, avoid raw-body logs and redact diagnostic output.

## Configuration and operation

Set all of the following only with credentials from the same authorized test environment:

- `ENABLE_PROVIDER_DIAGNOSTICS=true`
- `PROVIDER_DIAGNOSTICS_TOKEN=<random local secret>`
- `NAVITAIRE_BASE_URL`, `NAVITAIRE_AGENT_TOKEN`, `NAVITAIRE_USER_KEY`, optional API-key header if required
- `NAVITAIRE_ALLOWED_CARRIER=<verified carrier for the selected environment>`
- `TIMATIC_USERNAME`, `TIMATIC_PASSWORD`, `TIMATIC_TOKEN_URL`, `TIMATIC_BASE_URL`, optional `TIMATIC_USER_KEY`
- `TIMATIC_PROCESSING_ENTITY=<verified processing entity for the selected Timatic tenant>`

Use `X-Provider-Diagnostics-Token` on calls to the protected endpoints. The routes are hidden in production even if the token is configured. Provider tokens/credentials must not be stored in Bruno exported files, logs, screenshots or version control. The user's previous screenshots exposed token and `user_key` values; genuine values should be rotated.

## What remains intentionally out of scope

- The ordinary passenger pages still use mock Navitaire/Timatic providers; real provider results do not authorize the UI journey.
- The SkyPlus document-update workflow (`POST /v2/passengerdocuments/traveldocument`) is a separate potentially mutating operation and has not been connected without a sanitized successful contract, authorized credentials and an explicit review/confirmation gate.
- SkyPlus UAT `flightupgrade` eligibility/retrieve/upgrade endpoints shown in Bruno are not used by this document-validation path.
- Seats, ancillaries, check-in mutation, boarding-pass generation, payment changes and booking comments remain mock-only.
- The actual Timatic successful `documentRequest` response schema still needs to be captured from the authorized staging environment. Until then, only the source reference `evaluationResult` enum is trusted.
- This work does not assert that Emirates uses Navitaire New Skies or that credentials from a different carrier or tenant are authorised for Emirates use.
