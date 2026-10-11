# Provider adapter contracts

## Goal

Keep application logic independent from provider-specific API details. The application must run in `MOCK_MODE=true` when neither provider credential nor API is configured.

## Navitaire adapter

The local `MockNavitaireAdapter` implements:

- `retrieve_booking(record_locator, last_name)` — reads only the synthetic fixture JSON.
- `list_seats(session, journey_key)` — returns a mock seat map.
- `assign_seat(session, passenger_key, seat_code)` — checks the passenger and seat, then reserves under an async lock for this single process.
- `ancillary_catalog()` — returns fictional services and AED prices.
- `add_booking_comments()` — accepts a local mock result only.

This local lock is not a distributed lock and the local fixtures are not an authoritative system of record. Production seat inventory and booking mutations would need to use the provider's atomic operation/result and idempotency semantics.

`NavitaireLiveDiagnosticsAdapter` implements protected, read-only calls for `BookingRetrievev3`, the reference's `GET /api/nsk/v1/booking` state, per-journey check-in requirements, and passenger document-check. It reuses one `httpx.AsyncClient` so cookies/session context can survive the GraphQL → REST sequence in the same way as the original long-lived `BaseExternalClient`. The individual booking probe returns only a response-schema summary; the reference workflow maps data in memory and returns redacted status indexes. It does not implement seat/ancillary/check-in mutations.

The reference screenshots show different hosts for booking retrieval, non-production authentication, and a separate UAT upgrade service. They must not be assumed interchangeable; credentials and base URL must be confirmed for the same approved API product/environment.

### Expected integration checklist

- Confirm the authorized New Skies/Digital API product and exact version/host.
- Obtain vendor-issued credentials, client/agent role permissions and IP allowlist approval where required.
- Use TLS verification; never set `verify=False` or suppress certificate failures.
- Validate response schemas and classify provider errors vs passenger-actionable errors.
- Use provider-native idempotency/transaction identifiers or a suitable request ledger.
- Avoid logging credentials, auth headers, raw PNR/passport data or full provider bodies.
- Define timeouts, bounded retry policy, circuit breaker, and status reconciliation when a timeout occurs after provider commit.
- Confirm whether each endpoint is sandbox/test or production, and test only against authorized sandbox data until approved.

## Timatic adapter

The local `MockTimaticAdapter`:

- Creates a schema-shaped synthetic request envelope containing document, passenger and journey attributes.
- Skips international rule lookup for a synthetic domestic journey.
- Performs explicit mock checks for missing data, date parsing, document expiry, date-of-birth consistency, mock minimum-validity threshold and fixture visa-review status.
- Fans out concurrent calls across every passenger × journey combination and aggregates the results deterministically.
- Returns source metadata and a generated transaction ID, with no raw request envelope exposed through the API.

The 180-day rule and `MOCK_VERIFIED` evidence flag are test conditions only. Neither represents a universal passport rule, official visa check, or proof of document authenticity.

`TimaticStagingDiagnosticsAdapter` implements the visible staging token, country lookup, parameter lookup and `documentRequest` routes from the supplied Bruno screenshots. It uses TLS verification, Basic token acquisition, Bearer calls, explicit timeouts, token caching, and redacted response summaries. The reference-derived workflow trusts only the top-level `evaluationResult` enum from the supplied 1.0.7 model: `YES` maps to `PASS`, `NO` to `FAIL`, and `CONDITIONAL`/unknown to `REVIEW_REQUIRED`. Generic HTTP `200`, `status: OK`, or `decision: OK_TO_BOARD` is not considered an approval. The actual successful response schema for this tenant remains to be verified.

These routes are controlled by `ENABLE_PROVIDER_DIAGNOSTICS` plus a local `PROVIDER_DIAGNOSTICS_TOKEN`; they are hidden in production and do not replace the mocked user-facing workflow. In addition to individual probes, `POST /api/v1/provider-tests/workflows/document-validation` applies reference-derived booking and SSR gates, creates Timatic requests per passenger × journey, invokes Navitaire document-check only after explicit Timatic `YES`, and aggregates decisions. It requires explicit `NAVITAIRE_ALLOWED_CARRIER` and `TIMATIC_PROCESSING_ENTITY` configuration from the same authorized sandbox. The workflow is read-only and never creates a check-in or boarding pass.

### Expected integration checklist

- Confirm IATA AutoCheck entitlement, API version, schema, supported jurisdictions and service-level expectations.
- Determine whether PII/document data is sent to IATA and under what permitted purpose, retention and contractual terms.
- Validate response signatures/authentication according to the actual contract, and verify TLS certificates.
- Treat provider errors, timeouts, unrecognized codes and out-of-scope routes as `REVIEW_REQUIRED` or other contract-approved non-pass outcomes.
- Store the minimum decision evidence needed for audit; avoid persisting raw passport details in traces or long-term logs.
- Distinguish “data found / rule matched”, “provider says OK to board”, “document checked”, and immigration/legal admission decisions; only the provider contract and authorized operational process defines the meaning.

## Internal response model (conceptual)

```json
{
  "provider": "MOCK_TIMATIC",
  "mock_only": true,
  "transaction_id": "synthetic-uuid",
  "passenger_key": "PAX-001",
  "journey_key": "JNY-001",
  "decision": "REVIEW_REQUIRED",
  "evaluation_code": "VISA_EVIDENCE_UNVERIFIED",
  "reasons": ["Synthetic evidence status is not marked as verified."],
  "sources": [{"title": "Mock document rules", "source_status": "SYNTHETIC_RULE_FIXTURE"}]
}
```

## Source of truth

The provider response is the source for a provider-owned operation after an authorized live integration. This local demo's deterministic mock rules are the source for *demo behavior only*. An LLM, knowledge graph, RAG answer or memory record must not override provider state or deterministic gates.
