# Authorized provider staging diagnostics

> These endpoints are read-only diagnostic probes and are **disabled by default**. They do not switch the passenger journey from mock mode, do not perform check-in, do not change seats or ancillaries, and do not issue boarding passes.

The supplied Bruno screenshots support the following request shapes:

- **Timatic staging** token URL: `https://staging.timatic.iata.org/security-token-service/oauth2/token`
- **Timatic staging** query API base: `https://staging.timatic.iata.org/query-interface-service/api/v1`
- Timatic country lookup: `GET /locations/COUNTRY?code=IN&name=India`
- Timatic parameter lookup: `GET /parameters?code=documentCode`
- Timatic document check: `POST /documentRequest`
- **New Skies-style Navitaire test**: `POST https://dotrezapi.test.6e.navitaire.com/api/v2/graph/BookingRetrieve`
- The separate SkyPlus UAT upgrade URLs are not the same service or contract as New Skies booking retrieval. They are not wired into this adapter.

The screenshots did not include a successful `documentRequest` response body or a full approved response schema. The reference release model uses top-level `evaluationResult` with `YES`, `NO`, and `CONDITIONAL`. The adapter maps only that schema: `YES` → `PASS`, `NO` → `FAIL`, and `CONDITIONAL`/unknown → `REVIEW_REQUIRED`. Generic fields like `status: OK` or `decision: OK_TO_BOARD` do not pass. Reconfirm the current tenant contract before live operational use.

## 1. Enable local diagnostics

Keep the normal passenger workflow mocked:

```dotenv
MOCK_MODE=true
APP_ENV=development
ENABLE_PROVIDER_DIAGNOSTICS=true
PROVIDER_DIAGNOSTICS_TOKEN=<long-random-local-secret>
PROVIDER_HTTP_TIMEOUT_SECONDS=15
```

Generate the diagnostics secret locally in PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add the resulting secret to `backend/.env` and pass the same value as the `X-Provider-Diagnostics-Token` header when invoking the diagnostic routes. Do not use these diagnostic routes in production: they are hidden when `APP_ENV=production`, and the feature must remain disabled there.

Restart Uvicorn after editing `.env`; environment values are loaded at process startup.

## 2. Timatic staging settings

```dotenv
TIMATIC_TOKEN_URL=https://staging.timatic.iata.org/security-token-service/oauth2/token
TIMATIC_BASE_URL=https://staging.timatic.iata.org/query-interface-service/api/v1
TIMATIC_USERNAME=<authorized staging client id or username>
TIMATIC_PASSWORD=<authorized staging client secret or password>
TIMATIC_GRANT_TYPE=client_credentials
TIMATIC_PROCESSING_ENTITY=<processing entity approved for the same staging tenant>
```

The screenshot shows Basic authentication and `application/x-www-form-urlencoded` for token acquisition, and a Bearer token for query-interface calls. The implementation requests a token using `grant_type=client_credentials`, which is a standard OAuth client-credentials pattern but was not visible in the supplied Body-tab screenshot. If the saved Bruno `create token` request includes different form fields, follow the contract issued for your IATA account and adjust the token request accordingly. Never set TLS certificate verification off.

`TIMATIC_USER_KEY` remains in `.env.example` for compatibility with the previous scaffold but is not sent by default because the shown country/parameter/document request headers display only `Authorization` for those endpoints. Add it only if IATA's contract for your specific integration requires it and document its exact header name.

### Probe endpoints

All endpoints require `X-Provider-Diagnostics-Token`.

- `GET /api/v1/provider-tests/timatic/country?code=IN&name=India`
- `GET /api/v1/provider-tests/timatic/parameters?code=documentCode`
- `POST /api/v1/provider-tests/timatic/document-request`

Example request body using synthetic/future itinerary values:

```json
{
  "channel": "Web",
  "documentDetails": [
    {
      "documentCode": "PASSPORT",
      "documentExpiryDate": "2030-12-23T00:00:00+00:00",
      "documentIssueCountry": "IN",
      "nationality": "IN",
      "birthDate": "1990-06-20"
    }
  ],
  "itineraryDetails": {
    "segments": [
      {
        "departure": {
          "dateTime": "2027-02-26T13:45:00+05:30",
          "point": "DEL",
          "type": "AIRPORT"
        },
        "arrival": {
          "dateTime": "2027-02-27T12:00:00+04:00",
          "point": "DXB",
          "type": "AIRPORT"
        },
        "luggageCollected": true,
        "operatingCarrier": "6E",
        "processingEntity": "6E"
      }
    ]
  },
  "language": "EN",
  "passengerDetails": {
    "birthCountry": "IN",
    "birthDate": "1990-06-20",
    "gender": "M",
    "nationality": "IN",
    "residentCountryCode": "IND"
  },
  "passengerId": "a5a5a5a5-0000-4000-8000-000000000001",
  "transactionId": "synthetic-test-transaction-001"
}
```

Use an explicitly authorized test scenario and conform to the IATA staging contract. The sample is a schema illustration, not a claim that these facts represent a valid travel itinerary or that this exact payload will pass business validation.

The diagnostic API intentionally returns HTTP status, field paths/types and a cautious normalized decision—not raw Timatic bodies, document values or provider text. The normalized decision uses the source reference `evaluationResult` enum only. A plain HTTP `200`, `status: OK`, `decision: OK_TO_BOARD`, or unknown field/value never yields `PASS`.

## 3. Navitaire/New Skies booking retrieval settings

The supplied screenshots show more than one Navitaire-related host. Keep a single environment’s base URL and credentials together; do not combine a token or `user_key` from one non-production gateway with a separate `dotrezapi.test.6e.navitaire.com` test endpoint unless the API owner confirms they are intended to interoperate.

```dotenv
NAVITAIRE_BASE_URL=https://dotrezapi.test.6e.navitaire.com
NAVITAIRE_BOOKING_RETRIEVE_PATH=/api/v2/graph/BookingRetrieve
NAVITAIRE_AGENT_TOKEN=<fresh authorized sandbox token>
NAVITAIRE_USER_KEY=<authorized user key for the same environment>
NAVITAIRE_AUTH_SCHEME=raw
NAVITAIRE_ALLOWED_CARRIER=<carrier code approved for this sandbox and matching API tenant>
NAVITAIRE_BOOKING_STATE_PATH=/api/nsk/v1/booking
NAVITAIRE_CHECKIN_REQUIREMENTS_PATH=/api/nsk/v2/booking/checkin/journey/{journey_key}/requirements
NAVITAIRE_DOCUMENT_CHECK_PATH=/api/nsk/v1/booking/passengers/{passenger_key}/documents/check
NAVITAIRE_API_KEY=
NAVITAIRE_API_KEY_HEADER=
```

The screenshot's `Authorization` value appears to be a bare token. `NAVITAIRE_AUTH_SCHEME=raw` reproduces that shape. Change to `bearer` only if the provider's API contract for this environment requires `Authorization: Bearer <token>`. If an API key is required, configure both `NAVITAIRE_API_KEY` and the exact `NAVITAIRE_API_KEY_HEADER` documented for your API product; the code will not guess an API-key header.

### Probe endpoint

`POST /api/v1/provider-tests/navitaire/booking-retrieve`

```json
{
  "record_locator": "<provider-issued-synthetic-test-PNR>",
  "last_name": "<test-surname>"
}
```

Use a provider-issued synthetic booking, not a passenger's live record. The operation submits the `BookingRetrievev3` GraphQL mutation shown in the screenshots and returns only a redacted response shape and journey count. It does not return PNRs, names, journey keys, or the raw API response. It does not create a normal application session.

If the request fails, the diagnostic response categorizes network/DNS, authentication, endpoint, rate-limit and provider-server errors without echoing any credential or upstream response body. A `404` or `401/403` should be resolved against the exact product/environment credentials rather than by switching hosts at random.

## 4. Reference-style end-to-end document-validation probe

`POST /api/v1/provider-tests/workflows/document-validation`

This protected, read-only workflow migrates the sequence found in release `1.0.7`:

1. `BookingRetrievev3` with record locator and surname, followed by the richer `GET /api/nsk/v1/booking` state used in the original client. The adapter reuses one HTTP client so provider cookies/context persist between related calls.
2. Read-only check-in requirements for every journey. Any failed, malformed, or unresolved requirements response stops the workflow.
3. Concurrent deterministic booking checks (confirmed booking, paid/balance, names, adult, queues, codeshare/carrier, SSR and check-in-window rules). These are labelled reference-derived checks, not Emirates policy.
4. One Timatic request per passenger × journey, using the actual provider itinerary/document fields. `TIMATIC_PROCESSING_ENTITY` and `NAVITAIRE_ALLOWED_CARRIER` must be set explicitly from the same authorized test environment; no EK/6E value is guessed in live mode.
5. Only after exact `evaluationResult=YES`, call Navitaire's passenger document-check endpoint. Map the reference enum `Ok=1`, `NotOk=3`, `Conditional=2`; unknown values require review.
6. Aggregate statuses with fail-closed precedence and return passenger/journey indexes, counts and decision codes only. No raw passenger identifiers, raw Timatic response, seat/ancillary mutation, document update, check-in or boarding pass is returned or performed.

For any third-party staging example, set `NAVITAIRE_ALLOWED_CARRIER` and `TIMATIC_PROCESSING_ENTITY` only if the API owner confirms that the token, `user_key`, Navitaire host and Timatic staging tenant are intended to interoperate. A staging response is not proof of Emirates production eligibility. Never mix unrelated test credentials and booking records.

The regular passenger website remains mock-only. Enable the workflow only in local development with `ENABLE_PROVIDER_DIAGNOSTICS=true` and a random `PROVIDER_DIAGNOSTICS_TOKEN`. It is disabled in production by design.

## 5. How to invoke in Swagger

1. Start FastAPI locally with `--env-file .env`.
2. Open `http://localhost:8000/docs`.
3. Choose the appropriate `/api/v1/provider-tests/...` endpoint.
4. Add the `X-Provider-Diagnostics-Token` header when Swagger asks for the header parameter.
5. Send only authorized synthetic test data.

Bruno can call the same local endpoints by adding `X-Provider-Diagnostics-Token` and the request body shown above.

## 6. Integration boundary and safety

- The end-user booking/check-in UI and its business workflow remain mock-only. These routes are provider connectivity probes, not live customer operations.
- The protected reference workflow wires read-only New Skies operations for `BookingRetrievev3`, follow-up booking state, per-journey check-in requirements, and passenger document-check after an explicit Timatic `YES`. The successful provider response contracts have not been verified with live calls in this environment; these results do not drive the passenger UI or a boarding pass. Seat inventory, ancillaries, document updates, check-in mutation, and boarding-pass issuance remain mock-only.
- The SkyPlus upgrade APIs (`eligibility`, `retrieve`, `upgrade`) are distinct from this read-only New Skies booking probe and are deliberately not called.
- Timatic's request payload uses the fields visible in the submitted Bruno screenshots. A complete successful `documentRequest` response sample and current provider response specification are still needed before making a business decision from its response.
- Provider staging access does not confer production access or prove an Emirates production integration. Obtain the relevant data-processing, security and operational approvals before sending non-synthetic passenger information.
- Rotate exposed credentials/tokens from previously shared screenshots, and keep secrets only in a local ignored `.env` file or approved secret store.
