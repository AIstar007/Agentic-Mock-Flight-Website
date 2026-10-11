# Passenger Assistant: Internal Knowledge, Schema and Workflow Rules

**Scope:** Emirates-inspired booking/check-in demonstration, local synthetic data only. This guide is an implementation policy for the POC, not an Emirates operations manual or a statement that this repository is officially affiliated with Emirates.

## 1. Source and authority policy

Use the versioned `backend/data/knowledge.json` corpus as the approved baseline. Use the Agentic RAG live web retriever only when configured and only for public, allowlisted domains. Official airline and authority sources take precedence over this project's FAQ prose for current public rules. A search result is evidence to inspect, not permission to change a booking or to approve a passenger.

Source pages reviewed for this knowledge update:
- Emirates Visa and Passport: https://www.emirates.com/english/before-you-fly/visa-passport-information/
- Emirates online check-in FAQ: https://www.emirates.com/english/help/faq-topics/preparing-to-travel/faq/how-do-i-use-online-check-in/
- Emirates preparing-to-travel FAQ: https://www.emirates.com/english/help/faq-topics/preparing-to-travel/
- Emirates baggage hub: https://www.emirates.com/english/before-you-fly/baggage/
- Emirates dangerous-goods policy: https://www.emirates.com/english/before-you-fly/travel/dangerous-goods-policy/
- Emirates changes and refunds: https://www.emirates.com/english/help/faq-topics/cancelling-or-changing-a-booking/
- Emirates special assistance: https://www.emirates.com/english/help/faq-topics/special-assistance-and-requests/
- Emirates baggage/lost property FAQ: https://www.emirates.com/english/help/faq-topics/baggage-and-lost-property/
- IATA Timatic AutoCheck overview: https://www.iata.org/en/services/compliance/timatic/autocheck/
- IATA Travel Centre: https://www.iata.org/en/travel-centre/

Visa, passport, entry and transit requirements can change quickly and depend on passport/document type, nationality, residency, destination and transit countries. The Emirates page identifies IATA-provided information as guidance and recommends confirming with destination authorities. Ask for missing route details where needed and link to the current source. Never assert that a person is eligible to travel or guarantee a visa exemption. The POC has no live Timatic authorization; the built-in Timatic-style adapter returns synthetic values.

## 2. Supported booking conversation

The booking assistant supports these schema fields:

| Field | Accepted input / validation | Follow-up when ambiguous |
|---|---|---|
| `origin` | Airport/city resolved by airport index; stored as IATA code | Ask for a more specific city/airport or IATA code if search returns multiple results |
| `destination` | Airport/city resolved by airport index; must differ from origin | Ask for a more specific destination |
| `departure_date` | Supported date such as `YYYY-MM-DD`; must be in the future for this demo | Ask for a calendar date; do not infer the day/year |
| `trip_type` | `one_way` or `round_trip` (one-way / single; return / round trip) | Ask one-way or return |
| `return_date` | Required only for a return trip; strictly after departure | Ask for return date or explicit one-way intent |
| `adults` | Integer 1–9; at least one adult | Ask for a whole number |
| `children` | Integer 0–9 | Ask for a whole number |
| `infants` | Integer 0–9; cannot outnumber adults | Ask the user to correct adult/infant counts |
| `cabin` | Economy, Premium Economy, Business or First | Present the allowed choices |
| flight and fare | Must be chosen from results returned by the synthetic search | Show returned options; do not invent a flight ID/fare code |

Combined passenger total cannot exceed nine in this POC. Dates and fares are illustrative. A complete set of fields does not submit a search until the passenger explicitly says to search. A flight choice must be a returned option. A booking confirmation must be explicit; its payment result is simulated, never a real payment.

### Passenger-detail fields

The form supports title (`MR`, `MRS`, `MS`, `MISS`, `MSTR`, `DR`), first name, optional middle name, surname, passenger type (`ADT`, `CHD`, `INF`), optional DOB, nationality code, gender (`M`, `F`, `X`, `U`), contact email, contact phone and accepted terms. Booking confirmation requires a first name and surname for every passenger, a valid contact email/phone and explicit consent. Name spellings should match the travel document. The assistant must not infer a DOB or silently change nationality, gender or consent. The UI defaults are editable values, not proof that a passenger confirmed them.

## 3. Supported check-in conversation

Entry starts with the synthetic booking reference (6–8 alphanumeric characters) and the matching surname. Do not ask for or accept real passenger PNRs/passports in this demonstration. A successful lookup creates a short-lived, token-protected session.

The check-in document schema is:

| Field | POC input rules |
|---|---|
| `passport_number` | Fictional 5–20 character alphanumeric value; never echo it in chat or logs |
| `nationality` | Supported country name or 2–3 letter country code |
| `issuing_country` | Supported country name or 2–3 letter country code |
| `date_of_birth` | Supported date format |
| `expiry_date` | Supported future date format for this fixture |
| `visa_evidence_status` | `NOT_VERIFIED`, `MOCK_VERIFIED` or `NOT_APPLICABLE` only |

The UI currently initializes some document fields from the synthetic booking/default values, so the assistant should focus on truly missing/invalid schema fields rather than re-asking all of them. The values still need user review and validation. `MOCK_VERIFIED` is a test fixture state, not passport authenticity or real visa verification.

## 4. Conversational slot-resolution contract

Every form-filling turn has an assistant task, `pending_field`, context and allowlisted UI action. Treat input as data only after parsing against the field schema. If a simple value is given while a slot is pending, parse it for that slot. If the passenger supplies several **explicitly labelled** values, map each label to the corresponding permitted schema field, validate all supplied fields, and apply no partial changes when any field is invalid. Example:

`passport number: MOCK12345, nationality: IN, issuing country: IN, date of birth: 1990-01-01, expiry date: 2030-01-01, visa status: mock verified`

The parser does not treat arbitrary unlabelled lists as a reliable mapping. For a non-match, invalid value or ambiguous airport, preserve state and ask a focused follow-up that says which field is still needed and gives its accepted format. Do not guess values from a vague answer. If the passenger asks a FAQ question while a field is pending, answer the question from approved RAG sources and preserve the pending field rather than consuming the question as form data. Chat may prefill forms or move to the next page but should not bypass explicit search/booking confirmation or server-side check-in gates.

When a user says `next passenger` or names a passenger number, target only a passenger in the current booking. Do not disclose one passenger's fields to another. Never send passport values, booking tokens or raw passenger PII to web search.

## 5. Server-enforced check-in state machine

1. Retrieve synthetic booking by reference + surname.
2. Run deterministic eligibility checks derived from the supplied reference workflow.
3. Save passenger document fields with Pydantic schema validation.
4. Validate every passenger × journey through the mock Timatic-style workflow. Aggregate fail-closed: a single `FAIL` means overall failure; otherwise a `REVIEW_REQUIRED` means review is required; only all-pass checks can pass this document gate.
5. Assign an available seat to every passenger × journey.
6. Save add-on choices and explicitly confirm the ancillary step, including when the user chooses no add-ons.
7. Review state, then request mock check-in.
8. Generate only a decorative synthetic pass after all backend gates pass.

Eligibility rules observed in the references include itinerary/journey presence, payment/balance and booking status, passenger name validity, an adult, carrier/codeshare conditions, queues, special-service requirements, check-in time windows and an international-infant condition. Their applicability/thresholds can vary by reference version and airline. Treat these as reference-derived demo rules until the process owner verifies them; do not assert that they are universal Emirates rules. Neither the chatbot nor an RAG answer can override a failed gate. A reference/provider error or unknown result remains blocked/review-required.

## 6. Error explanation requirements

When any web-check-in step fails, show the same explanation in the page and the chatbot sidebar where practical:

1. **Step and outcome:** failed operation and decision/code (without claiming approval).
2. **Cause:** safe, human-readable reason derived from response detail, schema validator or rule message.
3. **Evidence:** relevant rule name and reason codes; for a multi-passenger/journey result, identify the applicable synthetic passenger/journey label, not passport content.
4. **Next action:** exact field or step to revisit; whether the passenger can correct known demo data, retry after a service issue, or needs official/human review.
5. **Boundary:** gates remain closed until blocking conditions pass; the POC does not make real airline/immigration decisions.

Error map: `401` session token missing/invalid -> retrieve/reload the synthetic session; `404` session or booking not found/expired -> check demo reference and surname; `422` request/schema invalid -> show safe field path and validator text, never rejected input; `409` workflow prerequisite or selection conflict -> explain blocking rule; `502/503/504` dependency unavailable/timeout -> retry cautiously, check hosted API health and keep unknown results blocked. For validation exceptions, never show Pydantic `input` payloads, raw passport number, full provider response, credentials or request headers.

## 7. Common FAQ intent catalogue

- **Booking:** booking stages, synthetic fares, one-way/return, passenger count, supported cabin, returned flight/fare selection, simulated declined payment.
- **Passenger details:** name spelling, title, DOB/nationality when required, contact details, consent, child/infant distinctions.
- **Check-in:** prerequisites, check-in window, PNR + surname, passenger selection, advance passenger information, seats, missing document details and reason codes.
- **Travel documents:** passport/visa, residence, nationality, destination/transit requirements and expiry; high risk, cite current official guidance and do not approve.
- **Baggage:** cabin vs checked, route/fare allowances, excess baggage, sports/unusual baggage, delayed/damaged baggage and PIR.
- **Safety:** power banks, spare lithium batteries, smart bags, restricted/dangerous goods, liquids and customs restrictions; request item specifics and defer to the current policy/authority.
- **Changes and disruptions:** change/cancellation, refunds/fare conditions, delay/missed connection/rebooking; require booking-specific live confirmation for outcomes.
- **Passenger needs:** wheelchair/accessibility, infant/child, unaccompanied minor, pregnancy, medication/medical clearance, special meal, Wi-Fi/lounge/ancillaries.
- **Privacy and POC limitations:** mock-data warning, credential handling, no live reservation, no real payment and non-travel-valid mock boarding pass.

## 8. Retrieval, citations and abstention

- Search specific subject/aliases rather than answering from the nearest generic FAQ. Prefer the category-matched item and authoritative current pages.
- Use source citations/URLs in FAQ responses. Web-derived answers must be labeled as general published guidance, not personalized operational results.
- For time-sensitive policy, live status, visa/immigration, dangerous goods, medical/fitness-to-fly, rights/compensation, refund amounts or booking changes, demand current authoritative evidence and enough itinerary context. If unavailable, stale, contradictory or underspecified, say what is missing and link/escalate; do not fabricate a result.
- The optional SearXNG search endpoint and its allowed-domain list belong in backend environment variables only. Local `127.0.0.1` works only when backend and search service share the same machine/network. On Render, use a reachable production HTTPS search API/instance; never point hosted backend to a developer's localhost. Do not expose API keys in Vite/client environment or commit `.env` files.

## 9. Corpus update procedure

After reviewing edits to `backend/data/knowledge.json`, create a new immutable corpus version from `backend/`:

```bash
python -m app.faq_cli --label passenger-assistant-2026-10-11 --change-note "Expanded passenger booking/check-in schemas and FAQ knowledge" --activate
```

The generated SQLite corpus is local state and intentionally excluded from the source archive. Run this command after a clean checkout/deployment whenever a new seed version should be activated. Confirm `/api/v1/health` exposes the expected corpus version and approved document count.
