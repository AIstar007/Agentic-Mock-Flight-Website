[[COVER]]

# EMIRATES

## Multi-Agent Flight Booking & Web Check-in POC

### Solution Design and Technical Architecture

**Version:** 1.0  
**Date:** 12 October 2026  
**Prepared by:** Alen Thomas  
**Classification:** Internal demonstrator · synthetic data only

A brand-inspired passenger journey demonstrator covering flight search, passenger details, personalised extras, seat selection, web-check-in gates, travel-document screening, a flight-focused assistant and mock boarding passes.

**Important:** This is an educational proof of concept. It is not an official Emirates website, a live reservation system, an IATA Timatic integration, a passport-authenticity service or a valid boarding-pass issuer.
===PAGE===
# Document Information

## Document Information

| Field | Value |
|---|---|
| Project Name | Emirates Passenger Journey POC |
| Prepared By | Alen Thomas |
| Document Version No. | 1.0 |
| Document Version Date | 12 October 2026 |
| Title | AI-Powered Multi-Agent Flight Booking and Web Check-in Assistant |
| Reviewed By | Pending review |
| Review Date | Pending review |
| Document Owner | Solution Engineering |

## Document Statistics

| Field | Value |
|---|---|
| Document Title | Emirates Passenger Journey – Solution Design |
| Document ID | E-PJ-POC-SD-001 |
| Template Version No. | 1.0 |
| Date of Release | 12 October 2026 |
| Data Classification | Internal demonstrator; synthetic passenger data |
| Document Status | Draft for technical review |
| Target Audience | Solution architects, engineering, QA, security and reviewers |

**Change control.** This document describes the repository and known limitations. Changes to routes, agent responsibilities, persistence, external integrations and validation policy must be reflected in a revision before being treated as approved production design.
===PAGE===
# Contents

1. **Introduction** ................................................................................................. 4  
   1.1 Scope and Integrations ................................................................................. 4  
   1.2 Out of Scope ................................................................................................. 5  
   Definitions, Acronyms and Abbreviations ........................................................... 5  
   1.3 Summary of Existing Functionality ............................................................... 5  
   1.4 Requirement Details ...................................................................................... 6–13

2. **General Overview and Design Guidelines/Approach** ..................................... 14  
   2.1 Assumptions / Constraints / Standards .......................................................... 14  
   2.2 Alignment with Target Architecture ............................................................. 15

3. **Current Architecture Design** ......................................................................... 16–22

4. **Non-Functional Requirements** ...................................................................... 23  
   4.1 Archiving · 4.2 Logging · 4.3 Auditing ........................................................ 23  
   4.4 Scalability · 4.5 Rate Limit · 4.6 Performance ............................................. 24  
   4.7 System Management and Monitoring · 4.8 Access Management .................. 25

5. **Risk and Mitigation** ..................................................................................... 26  
6. **Appendices and References** ........................................................................ 27

Page numbers indicate the intended section layout. This is a design specification for the current mock POC, not an assertion that every proposed production control has already been deployed.
===PAGE===
# 1. Introduction

The Emirates Passenger Journey POC is a browser-based, AI-assisted demonstrator for a passenger’s journey from flight search and booking inputs through passenger details, optional services, seat selection, web-check-in eligibility and document screening, ending in synthetic boarding-pass records.

The application combines a React/Vite interface, FastAPI, Pydantic schemas, a workflow orchestrator, schema-aware conversational slot parsing, deterministic rule evaluation, mock adapters, an evidence-limited flight FAQ knowledge layer, and Sarvam AI speech endpoints. The page and the chatbot share workflow context so supported actions can be completed through controls or natural-language requests.

Rather than handing every operation to one unconstrained language-model prompt, the workflow routes intent into typed fields and explicit actions. Deterministic validators check inputs and server-side preconditions. The orchestrator maintains the active phase, dispatches to the relevant mock adapter or rule set, and returns an explanation and supported next action.

The solution demonstrates five capabilities:

- Interactive flight search and a synthetic booking journey for one-way and round-trip travel.
- Passenger-detail capture, DOB rules, nationality selection, adult accompaniment, optional extras and passenger/journey seat selection.
- PNR-and-surname retrieval followed by mandatory eligibility and document checks before later check-in phases.
- A flight-focused assistant with current-page context, supported FAQ responses and voice I/O via Sarvam AI.
- Traceable gate outcomes, failure reasons, recommended corrections or escalation, and synthetic boarding-pass records.

**System boundary.** Availability, fares, references, transactions, adapter responses and boarding passes in this POC are synthetic. The visual theme is Emirates-inspired only and does not establish affiliation or live Emirates capabilities.

## 1.1 Scope

The scope includes client interactions, backend APIs, guided conversation, mock rule evaluation, local fixtures, seat/extra workflows, navigation, error presentation, tests and documented integration boundaries.
===PAGE===
# 1.1 Scope, Integrations and Components

## Integrations

1. React and Vite browser application.
2. FastAPI REST service with typed Pydantic schemas.
3. Orchestrator and schema-aware conversational slot parser.
4. Synthetic flight, booking and seat adapter based on the local mock Navitaire-style interface.
5. Mock Timatic-style evaluation adapter; the live adapter is a boundary, not enabled verification.
6. Self-corrective / evidence-aware retrieval for supported flight FAQs and local policy knowledge.
7. ISO-3166 country registry (`pycountry`) exposed through the country-search API.
8. Sarvam Saaras speech-to-text and Bulbul text-to-speech REST endpoints, enabled only when a backend API key is configured.
9. Session store with optional Redis/MongoDB configuration hooks; local POC can use in-memory state.
10. Python/pytest test automation and a Vite production frontend build.

## Components

1. **Passenger Web UI** — search, passenger details, extras, seats, check-in, document review and boarding passes.
2. **Floating Assistant** — page-aware chat, option cards, back/next, skip/modify prompts and current-field status.
3. **Chat API** — receives page/workflow state and returns an answer, pending slot, card and approved actions.
4. **Orchestrator** — selects the workflow branch/gate, invokes typed helpers/adapters and preserves step state.
5. **Schema/Rule Layer** — validates dates, country codes, names, documents, passenger age, seat inventory and prerequisites.
6. **Mock Navitaire Adapter** — mock bookings, journey segments, seat inventory and updates.
7. **Mock Timatic Adapter** — deterministic synthetic outcomes with evaluation codes, reasons and source notes.
8. **FAQ / Retrieval Layer** — answers supported aviation questions with available evidence and abstains when evidence is insufficient.
9. **Sarvam Voice Proxy** — keeps the key server-side and converts short audio clips to text and assistant prompts to audio.
10. **Session and Audit Layer** — stores workflow state, selections, gate results and bounded events according to configuration.
11. **Review/Boarding-Pass Output** — presents reasons, per-passenger decisions and synthetic records; does not issue travel documents.
===PAGE===
# 1.2 Out of Scope, Definitions and Existing Functionality

## 1.2 Out of Scope

The following are not part of the current implementation:

- Live Emirates inventory, reservations, ticket issuance, payment capture, check-in or boarding-pass issuance.
- A real Navitaire production connection or an enabled IATA Timatic AutoCheck integration.
- Biometrics, passport-chip reading, identity proofing, document authenticity detection or automatic visa approval.
- A complete multi-city booking/search/checkout engine. Current flight-search requests support one-way and round-trip journeys.
- Production incident response, airline-authorised policy overrides, staff workbenches or identity federation.
- Kafka-based event processing or a deployed production data platform; optional Redis/Mongo configuration is not evidence of production deployment.
- Legal or immigration advice, or claims that local sample rules reproduce every Emirates, destination or transit policy.

## Definitions, Acronyms and Abbreviations

| Term | Description |
|---|---|
| POC | Proof of Concept |
| PNR | Passenger Name Record / booking reference in the demo context |
| PDP | Passenger Details Page / passenger-detail stage |
| API | Application Programming Interface |
| LLM | Large Language Model |
| RAG | Retrieval-Augmented Generation |
| STT / TTS | Speech-to-Text / Text-to-Speech |
| ISO-3166 | Country-code standard; UI accepts alpha-2 and alpha-3 codes |
| PASS | Configured synthetic rules returned a passing result |
| FAIL | A synthetic rule deterministically rejected a field/condition |
| REVIEW_REQUIRED | Authoritative information is unavailable or a case needs review |
| Timatic | IATA travel-document information product; not queried live by this POC |

## 1.3 Summary of Existing Functionality

The application provides mock flight search, one-way/round-trip journeys, passenger data entry, senior flagging, child/infant categorisation, adult-companion selection, per-passenger add-ons, row 1–40 seat inventory, explicit seat-skip ledgers, PNR/surname retrieval, eligibility/document gates, chatbot actions, FAQ responses, navigation history and synthetic boarding-pass output. Regression tests exercise major API, form and browser paths.
===PAGE===
# 1.4 Requirement Details — Flight Search and Journey Selection

## 1. Flight-search inputs

The system collects origin, destination, trip type, departure date, optional return date, passenger counts and cabin. Airports must resolve to valid codes and origin/destination must differ. A one-way search must not retain a return date. Round-trip logic rejects return-before-departure values.

The chatbot accepts one command containing several values or a guided sequence. Visible defaults must not be treated as newly supplied answers. An explicit answer/action is required before a pending field advances.

## 2. Trip type and dates

- Supported types are one-way and round-trip.
- A valid future departure date is required by the booking flow.
- Return-date validation must use the current API rule consistently; invalid dates must be rejected instead of silently substituting a different journey.
- Date fields use `YYYY-MM-DD` at the API boundary and understandable validation text in the UI.
- Multi-city is a future capability, not a hidden alias for round-trip. Per-leg search, fare, seat and document workflows must be designed before it is promised.

## 3. Passenger counts and cabin

Adults, children, infants and senior citizens are captured separately. Passenger total and infant/adult relationship are validated before search. Senior entries use the current demo convention of ADT plus a senior flag; that convention does not guarantee any airline fare or inventory. Cabins include Economy, Premium Economy, Business and First, with aliases such as “Business Class” and “First Class”.

## 4. Chat/page parity

The assistant updates the visible search form only through supported actions. “Proceed” or “search flights” invokes search only when required inputs are complete. Back and Next share the main flow state. The chatbot must not fabricate a successful search, fare, booking or payment result.

## 5. Validation outcome

Rejected input identifies the field and expected pattern/prerequisite. Failure must not clear unrelated values or silently switch trip type. Modification keeps existing values until a valid replacement is provided.
===PAGE===
# 1.4 Requirement Details — Passenger Details and Accompaniment

## 6. Passenger details (PDP)

Each traveller needs title, first name, surname, date of birth, nationality and gender marker under the current mock schema. Names follow configured allowed characters and maximum lengths. Email and phone are validated separately. Demo consent must be accepted before simulated confirmation. The assistant asks required slots and updates the same visible passenger object.

## 7. Country nationality / issuing country

Booking nationality and check-in nationality/issuing-country fields use the complete country registry exposed by `/api/v1/countries`. The user may search by full/common/official name or ISO alpha-2/alpha-3 code. Suggestions display the full country name and both codes; the existing schema receives a normalized alpha-2 code. A string that does not resolve to a real country is not accepted as a valid selection.

## 8. Date of birth and category

Birth date must be valid, in the past and within the supported age range. Age is evaluated against departure for passenger-category consistency. A selected senior traveller must be at least 60 on departure in the POC. Status is derived from the initial passenger search rather than duplicated by another confusing check-in checkbox.

## 9. Accompanying adult / unaccompanied-minor rule

The form and guided chatbot offer eligible adult passengers as choices. A companion must be a different passenger, be part of the same booking, have valid DOB and be at least 16 on departure. Every infant must have a companion, and the current synthetic rule allows at most one infant per accompanying adult. Travellers under 16 without an eligible adult are blocked by this conservative POC rule.

This is not a statement of Emirates’ live unaccompanied-minor policy. Actual rules depend on age, route, service, guardian arrangements and operating carrier; a live service must use current authorised airline requirements.
===PAGE===
# 1.4 Requirement Details — Extras, Seats and Navigation

## 10. Passenger-specific extras

Optional add-ons are shown per passenger. Actions have explicit scope: select/toggle, remove, confirm and next, or skip this passenger. Totals derive from each passenger’s choices and are not multiplied across the party. Skipping one passenger must not erase another passenger’s selection. The options are synthetic; no payment is taken.

## 11. Seat selection by passenger and journey

The mock adapter exposes rows 1–40 with letters A–F; only seats in the inventory are accepted. Server-side validation checks format, availability, duplicate assignment per journey, passenger index and journey key. Round trips require a separate pair per passenger and journey. Focus advances only after assignment confirmation or explicit skip.

## 12. Explicit skip behaviour

Skipped seat assignments are tracked individually. Skipping one passenger/leg releases or leaves unassigned that pair while preserving others. A boarding-pass record must display `NOT_SELECTED` when skipped; it must never assign a random seat. Extras may be skipped individually. “Skip all” must be explicit and must not imply seats were reserved.

## 13. Back/Next and state preservation

Page navigation, the logo and browser history retain state when moving between workflow screens. An intentional reset to home/flight search may reset the current draft, and that reset should be explicit. Chat Back/Next calls the same handlers as visible buttons. Navigation is not confirmation of the next gate.

## 14. Continue to extras

Continue to Extras is available after mandatory passenger fields, companion checks and consent pass. If a required field fails, the UI explains it and does not advance. Chat “continue to extras” must update the same flow step and retain entered values.
===PAGE===
# 1.4 Requirement Details — Web Check-in Journey

## 15. Booking lookup and eligibility gate

The check-in path requests PNR first and surname second. The POC retrieves a synthetic booking through the mock adapter and creates a session token. Eligibility is evaluated before document validation, seats, extras finalisation or mock check-in. Chat and buttons use the same gates; the assistant explains but cannot bypass a gate.

## 16. Document collection

For each passenger, capture a fictional passport number, nationality, issuing country, DOB (optional/required by category), expiry date, purpose of stay and duration in days. Only synthetic passport values may be used. DOB cannot be in the future. Expiry must be valid and is compared with journey departure.

## 17. Automated document decision

The mock adapter returns one result per passenger and journey with a decision, evaluation code, reasons and source metadata. The user cannot manually select “verified” to force a pass. A non-PASS result blocks progression. Chat explains each failing/review-required passenger and journey, why the condition failed, the safe correction, or the reason an authorised current travel-rule review is necessary.

## 18. Seat and extras steps

After mandatory gates pass, each passenger/journey seat must be confirmed or explicitly skipped. Extras are handled per passenger with confirm/skip and next-passenger actions. Review requires prior gates. Final mock check-in returns synthetic boarding-pass records.

## 19. Boarding-pass output

Each record shows the synthetic PNR and surname and accurately reflects seat status. It is not a scannable travel credential or proof of real check-in. The mock-only warning remains visible and no random seat is created for a passenger who skipped selection.

## 20. Error messages and recovery

The chatbot explains the operation, precise rule/schema field, reason, correction and escalation path. State and completed steps should survive recoverable errors. If authoritative entry-rule data are unavailable locally, the result remains `REVIEW_REQUIRED`; the model must not infer a pass from general knowledge.
===PAGE===
# 1.4 Requirement Details — Agentic Assistant, Voice and Knowledge

## 21. Conversational assistant

The assistant receives current page, task, pending field, passenger/leg context and visible error. Typed and spoken inputs enter the same handler. Option-type questions use choice cards and may still accept valid typed/spoken answers. Text/personal-detail fields accept text or speech and then pass through the same validation as manual entry. Repeating a response must still submit it to the *current* pending field if the workflow has advanced; the UI must not discard a transcript merely because it matches an earlier value.

## 22. Current-question voice behaviour

The browser records a short clip and sends it through the backend Sarvam proxy. Saaras v4 uses `language_code=unknown` for automatic language detection and supported domain keyterms for place and booking vocabulary. The transcript is processed against current page/pending-field context. Automatic playback speaks only the new question or concise next action and only if the response belongs to the latest user interaction. Typing or starting another recording cancels previous speech.

## 23. Speed and latency

Bulbul speech uses `SARVAM_TTS_PACE` (default 1.5, bounded to the documented 0.5–2.0 range). TTS currently uses REST and returns the complete audio clip, so model generation latency precedes playback; faster playback does not remove upstream generation latency. The UI prefers the current pending-field question over a prior long answer, cancels stale TTS/STT requests when a newer user action begins, and sends an identical transcript again when it is a deliberate answer to the next slot. Text and speech enter the same request handler. The STT REST request explicitly sets `mode=transcribe` for either configured Saaras v3 or v4; `keyterms` are included only for v4. Sarvam documents streaming as a lower-latency next iteration, not a claim about the current code. The API key remains on the backend and is never bundled into frontend environment variables.

## 24. Self-corrective, evidence-aware RAG

The FAQ layer can retrieve local flight knowledge and available cited public material. A self-corrective pattern retrieves context, drafts an answer, compares claims against the retrieved evidence and page/route context, then abstains or asks a clarifying question when the evidence is missing, stale or contradictory. User utterances and generated text are not policy sources. RAG improves source management but cannot guarantee zero hallucination or replace an authoritative live rule feed.

## 25. Flight FAQ boundaries

The chatbot may answer supported booking, baggage, check-in, seat, airport, document and general aviation questions. It must not invent policy, claim a booking/seat/payment succeeded before the API confirms it, or present sample heuristics as universal Emirates/IATA requirements.
===PAGE===
# 1.4 Requirement Details — API Contracts and Session State

## 26. Key REST interfaces

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/flights/search` | Validate itinerary and return synthetic flight options |
| `POST /api/v1/bookings/confirm` | Confirm a synthetic booking draft |
| `POST /api/v1/bookings/lookup` | Retrieve demo booking using PNR and surname |
| `POST /api/v1/site-assistant/chat` | Booking/search/page assistant actions |
| `POST /api/v1/chat` | Check-in and document workflow assistant |
| `GET /api/v1/countries` | Search or list ISO country registry entries |
| `POST /api/v1/sessions/{sid}/eligibility` | Run required booking eligibility rules |
| `POST /api/v1/sessions/{sid}/documents` | Save synthetic document inputs |
| `POST /api/v1/sessions/{sid}/documents/validate` | Evaluate mock document rules |
| `GET/POST /api/v1/sessions/{sid}/seats` | Read/assign mock seat inventory |
| `POST /api/v1/sessions/{sid}/seats/skip` | Mark passenger/journey seat as skipped |
| `GET/POST /api/v1/sessions/{sid}/ancillaries` | Read/save individual extra choices |
| `POST /api/v1/sessions/{sid}/check-in` | Run final mock check-in gate |
| `POST /api/v1/voice/transcribe` | Sarvam speech-to-text proxy |
| `POST /api/v1/voice/speak` | Sarvam text-to-speech proxy |

The actual OpenAPI schema is the contract of record. Request validation and mock workflow gates must remain authoritative even if the chatbot suggests an action.

## 27. Typed response/action contract

Assistant responses may carry a readable answer, intent, active task, pending field, passenger index, context, a review card, citations and a bounded list of actions. The frontend consumes only supported action types. Actions such as setting a form field, navigating a flow step, loading a seat map, updating a passenger field, or requesting document validation must be checked by the corresponding page state and server validator.

## 28. Session state

Session state includes synthetic booking/journeys/passengers, document drafts and per-journey results, eligibility results, selected seat assignments and skipped pairs, add-on selections, check-in state and audit events. Session tokens protect session operations. Redis and MongoDB are configuration hooks; a local run may use in-memory state. A durable store, cleanup policy and TTL must be chosen and tested before production data is stored.
===PAGE===
# 1.4 Requirement Details — Data Model and Testing

## 29. Core entities

| Entity | Representative fields | Responsibility |
|---|---|---|
| FlightSearch | origin, destination, trip type, dates, party, cabin | Search input and itinerary validation |
| Booking | PNR, surname, passengers, journey segments, fares | Synthetic booking lifecycle |
| Passenger | key, category, DOB, nationality, senior flag, companion index | Age/category/accompaniment checks |
| Journey | key, origin/destination, departure, carrier, international flag | Per-leg requirements and seat selection |
| DocumentDraft | fictional passport, nationality, issue country, DOB, expiry, purpose, duration | Collected traveller inputs |
| DocumentResult | decision, evaluation code, reasons, source metadata | Per-passenger/journey gate result |
| SeatSelection | passenger, journey, seat code, confirmed/skipped state | Explicit seat selection ledger |
| AncillarySelection | passenger key and selected codes | Per-passenger extras |
| AssistantContext | task, pending field, page, current target, context | Guided multi-turn interaction |
| AuditEvent | timestamp, action/component, bounded metadata | Troubleshooting and traceability |

## 30. Test requirements

Automated tests cover field parsing, invalid dates, ISO alpha-2/alpha-3 codes, booking passenger nationality, seat codes through row 40, unavailable/duplicate seats, passenger age-category rules, companion validation, document outcome handling and endpoint state transitions. The UI build must pass, and browser tests must verify the most important full workflows rather than isolated controls alone.

## 31. Acceptance scenarios

- Search using keyboard/chat and verify the visible values match the request.
- Accept a repeated voice response as the current pending answer and advance to the next field.
- Select countries by full name and either code form and display the name in booking and check-in cards.
- Attempt a future DOB and invalid ISO code and receive a specific input error.
- Validate an expired passport or review-required purpose and see the decision, why, correction and safe escalation in the chatbot.
- Select seat `40C`, ensure `41A` is rejected, skip one passenger/leg and confirm no random seat is assigned.
- Complete PNR → surname → eligibility → documents → seat/extras → review → synthetic boarding passes.
- Type a new answer while TTS is playing and ensure the older utterance stops and cannot resume over the next question.

## 32. Exit criteria

A code/build/test pass is required, together with a documented limitation list. Sarvam live voice cannot be declared end-to-end verified until a valid backend key is configured and a real microphone transcription plus speech response is tested. The official document-validation integration cannot be declared active until authorised API access and current official response contracts are implemented and exercised.
===PAGE===
# 2. General Overview and Design Guidelines/Approach

The POC follows a page-oriented, multi-agent workflow. User interaction can arrive through visible controls, typed chat or recorded speech. The browser sends a typed request with page state and current pending field. The backend determines the matching task and uses schema-aware parsing, deterministic validation and the orchestrator to return the answer and supported actions. The browser applies approved actions to the existing form and re-renders the updated field values.

The major transaction path is:

**Passenger → UI/Chat → API Contract → Orchestrator → Rule/Adapter → Typed Result → UI + Explanation**

For booking, the path begins with synthetic flight search and passenger details, then individual extras, seat selection and simulated confirmation. For check-in, it begins with PNR/surname retrieval and a mandatory eligibility gate, then passenger-document collection and mock rule evaluation. Seats and extras follow only when the previous gate has passed; explicit skip is represented as a state rather than a hidden random allocation.

The design separates language understanding from decisions that must be deterministic. A language model or retrieval helper can explain and classify intent; it is not the authority that approves a travel document or overrides a validator. The current code mostly uses schema-aware conversational parsing and deterministic adapters, with evidence-aware FAQ retrieval for knowledge questions.

## Design guidelines

- **Typed boundaries:** Validate incoming data at the backend, not only with browser form constraints.
- **One source of page state:** Chat actions update the current visible fields and the workflow state together.
- **Gate ordering:** Eligibility and document outcomes control whether subsequent pages can proceed.
- **Explicit uncertainty:** `REVIEW_REQUIRED` is not a pass; missing official rule data stays unresolved.
- **Passenger/leg scope:** Every seat and add-on operation states which passenger and journey it affects.
- **Safe conversational UX:** Interrupt stale speech, ask the current pending question and keep earlier responses from overwriting newer interaction state.
- **Traceability:** Return codes, reasons, relevant mock sources and action outcomes without logging secrets.
===PAGE===
# 2.1 Assumptions / Constraints / Standards

## 2.1.1 Assumptions

- The application runs in a development/test environment with synthetic reservations and passenger values.
- The FastAPI backend is reachable from the Vite frontend using the configured API base URL.
- The country registry package is installed by backend requirements.
- The user grants microphone permission and opens the frontend in a secure context when using voice.
- Sarvam API access/key is configured on the backend for real speech requests; otherwise the endpoints intentionally return a clear configuration error.
- The locally configured mock catalog is the source of the POC's deterministic rule outcomes.
- Official Timatic integration remains disabled until authorised credentials, contract, tenant, data-processing terms and production failure behaviour are confirmed.
- Optional Redis/Mongo settings must be explicitly provisioned; a local in-memory session is not durable storage.

## 2.1.2 Constraints

- Mock availability, fares, booking references and boarding passes do not correspond to live airline systems.
- The synthetic document adapter does not evaluate every nationality/destination/transit combination and does not call live government or IATA datasets.
- The 180-day passport-validity screen and 90-day tourism-duration screen are local demo heuristics, not universal travel rules.
- One-way/round-trip search is implemented; multi-city booking creation is not complete.
- TTS uses a complete REST audio response, which has higher initial latency than streaming. The code cancels stale speech but cannot remove network/model generation time.
- Model, RAG and speech providers may return errors or be unavailable; unknown results must not be treated as approval.
- Session retention and audit lifecycle must be decided before persistent real passenger data is considered.

## 2.1.3 Standards and design references

- REST API and schema validation with stable, explicit request contracts.
- ISO-3166 alpha-2/alpha-3 country-code validation and name resolution.
- Least privilege, secret isolation, data minimisation and HTTPS for microphone/voice traffic.
- IATA Timatic AutoCheck as the recommended authorised travel-document rule source for any production integration; current POC does not call it.
- Sarvam REST Speech-to-Text and Text-to-Speech API contracts; use documented language auto-detection, keyterm rules and pace bounds.
- Automated test discipline: unit/API tests, Vite build and browser workflow checks.
===PAGE===
# 2.2 Alignment with Target Architecture

| Component | Current implementation | Architectural alignment / limitation |
|---|---|---|
| Browser | React + Vite pages | Interactive passenger workspace and persistent floating assistant |
| REST layer | FastAPI + Pydantic | Typed interface and backend validation |
| Orchestrator | Workflow-aware request routing | Controls phase, gates, pending slot and approved actions |
| Specialist logic | Booking, passenger, seat, extras, eligibility and document functions | Logical specialist responsibilities; not every function is a separate deployed agent process |
| Knowledge | Local rules/catalog plus evidence-aware FAQ retrieval | Grounded answers where evidence exists; not a guarantee of live policy freshness |
| Flight/seat service | Mock Navitaire-style adapter | Demonstrates state transitions; no production supplier calls |
| Travel documents | Mock Timatic-style adapter | Exercises decision codes and reason paths; no official IATA lookup |
| Country codes | Pycountry country registry + API | Full-name / alpha-2 / alpha-3 lookup and consistent UI display |
| Speech | Sarvam Saaras + Bulbul REST proxy | Requires backend key; latest-turn cancellation; TTS streaming remains future work |
| Session storage | In-memory plus optional Redis/Mongo config hooks | Local state can reset; persistent deployment needs TTL and operational tests |
| Observability | API logs, session events and gate results | Requires privacy review and a deployed metrics/alerting layer for production |
| Deployment | Local API + Vite build | Container/hosting choices need environment-specific validation |

The proposed production direction is to retain the typed gate/orchestration contract and replace selected mock adapters with approved services behind the same boundary. No supplier integration should be described as active until its real credentials, request/response contract, error codes and acceptance tests are present.
===PAGE===
# 3. Current Architecture Design

[[ARCHITECTURE_DIAGRAM]]

## Architecture components

The architecture combines a browser passenger experience, REST API, workflow orchestrator, schema validators, mock flight and document adapters, country registry, retrieval-based FAQ support and speech-proxy endpoints. The assistant runs inside the same user-facing web experience but only receives the action contracts exposed by the backend. It does not provide direct unrestricted access to backend storage or external airline systems.

## 1. Passenger / browser

**Purpose:** Enter itinerary and passenger data, select optional services and seats, review validation outcomes, retrieve a mock booking and view synthetic boarding-pass records.

**Role:** The passenger may complete supported steps manually or through the floating assistant. Both paths share the visible state. The browser must explain required fields and show the server’s final gate decision. It cannot claim that an action is confirmed until the backend responds successfully.

## 2. React/Vite application

**Purpose:** Present the Emirates-inspired visual experience and page navigation.

**Role:** `BookingPortal.jsx` and `CheckInWorkspace.jsx` render search, passenger data, add-ons, seat maps, document cards, review and the floating chat. `CountryCodeInput.jsx` resolves a country to its display name and normalized code. `sarvamVoice.js` records short clips and manages speech playback cancellation. Browser history and shared state preserve data while moving between pages; the explicit home/reset flow is the intentional reset boundary.
===PAGE===
# 3. Current Architecture Design — Service Components

## 3. FastAPI REST layer

**Purpose:** Provide the typed interface between frontend and orchestrator/service functions.

**Role:** Exposes flight search/confirmation, booking lookup, sessions, eligibility, documents, seats, ancillaries, mock check-in, country lookup, assistant chat and voice proxies. Pydantic validation returns field-level errors for malformed requests. The API is the authoritative gate; a frontend or model suggestion cannot bypass it.

## 4. Workflow Orchestrator

**Purpose:** Preserve transaction order and route each request to the correct workflow function.

**Role:** Uses the current task, pending field and page/session context to parse supported intent, retrieve current state, invoke rule/adapter methods, construct an answer/card and issue approved actions. It maintains the “check current field → validate → update state → compute next field” pattern. Errors and FAQ interruptions should retain pending steps whenever safe.

## 5. Specialist logic

Specialist responsibilities are logical domains within the current backend, not claims that every domain runs as a separately deployed process:

- **Booking / flight-search logic:** resolves airport, date, trip type, counts and cabin.
- **Passenger-details logic:** validates names, birth date, nationality, category and companion reference.
- **Ancillaries logic:** stores selections per passenger and confirms/skips the active passenger.
- **Seat logic:** validates seat code, availability, passenger index and journey; tracks confirmed/skipped assignments.
- **Eligibility logic:** evaluates the ordered preconditions for the synthetic booking/session.
- **Document-review logic:** validates passport fields and emits decision codes, reasons and sources per passenger/journey.
- **FAQ/RAG logic:** answers only to the extent supported by retrieved evidence and returns an abstention/clarification when insufficient.

## 6. Mock adapters and state store

The mock flight adapter supplies local booking/journey/seat fixtures. `MockTimaticAdapter` evaluates local structural and synthetic policy heuristics. The session store holds the booking snapshot, selected options and gate results. Optional Redis/Mongo configuration hooks do not imply that either dependency is active in every execution environment.
===PAGE===
# 3. Current Architecture Design — Frontend and Voice

## 7. Country registry service

The API endpoint `/api/v1/countries` uses the installed ISO country registry and supports listing/searching country names and code forms. The frontend suggestion shows the full country name with alpha-2 and alpha-3 codes. For compatibility with existing passenger schema, selection stores alpha-2; the displayed input and review card resolve that code back to the country name. Any code not in the registry should receive an actionable validation error.

## 8. Sarvam voice proxy

The voice UI sends recorded audio to `/api/v1/voice/transcribe` and speaks concise assistant responses through `/api/v1/voice/speak`. The backend holds `SARVAM_API_KEY`; it is not sent to the client. Saaras v4 auto-detects language when `language_code` is `unknown` and uses supported keyterms for common places, passenger fields and booking vocabulary. The v4 REST form omits the v3-only `mode` field. Bulbul uses a configurable pace (default 1.5 on the 0.5–2.0 scale). The client cancels a pending STT request when the user switches to typing or starts a newer recording; identical recognized text is still passed to the current pending-field handler rather than being discarded as a duplicate. The UI uses a generation/turn guard: if a new message or recording starts while the old audio is waiting or playing, it cancels the old response and will not start stale speech.

The recording duration is capped at 25 seconds to remain below the synchronous STT limit. Empty clips, denied microphone permission, API configuration errors, network errors and timeout errors are surfaced in chat. When the key is not configured, text chat remains available and the API returns a clear configuration message. Live STT/TTS was not validated without an API key.

## 9. Knowledge and retrieval

The FAQ layer uses the local rules/knowledge catalog and available cited resources. Retrieved content is evidence, not executable instruction. An agent should identify the question and required context (route, passenger type, travel date), retrieve the most relevant material, ensure the answer is supported and state what it could not verify. The FAQ layer must not mutate booking state unless a supported action is returned separately.

## 10. Output and error presentation

Responses can contain text, a review card, citations, gate codes and recommended actions. Document failures include passenger/journey identity, decision code, reason and a correction/escalation message. The full response is shown in the chatbot; the card summarises key results. For safety, the system remains blocked on failed or review-required mandatory checks.
===PAGE===
# 3. Current Architecture Design — End-to-End Sequence

## Sequence A — Booking

1. Passenger enters origin/destination, trip type, dates, party and cabin manually or in chat.
2. Browser validates basic required fields and sends `POST /api/v1/flights/search`.
3. Backend schema validates itinerary/count rules and mock commerce returns synthetic flights/fares.
4. Passenger selects flights/fares and fills the same passenger objects shown on the page.
5. The age checker evaluates DOB against departure; infant/under-16 companion selection points to a valid adult passenger. Senior-citizen age is checked against the departure date.
6. Add-ons are saved for each passenger. Each passenger/journey seat is confirmed or explicitly skipped.
7. Synthetic booking confirmation validates passengers, counts, fares, contacts, add-ons, seats and terms. It does not collect money or create a live reservation.

## Sequence B — Web check-in

1. Chat asks for PNR, then surname; the backend retrieves a synthetic booking and creates a session.
2. Eligibility rules run first. A non-PASS decision prevents downstream progression.
3. For each passenger and journey, required fictional document fields are collected and submitted to the mock document adapter.
4. The document evaluator returns PASS, FAIL or REVIEW_REQUIRED with evaluation codes and reasons. Any required non-PASS outcome leaves the flow blocked.
5. After gates pass, the passenger chooses or explicitly skips a seat per journey, then confirms or skips optional extras per passenger.
6. Review executes the final server-side checks. The API returns synthetic boarding-pass records with PNR, surname and actual seat/`NOT_SELECTED` status.

## Sequence C — Chat and voice

The current field/pending passenger are included in the chat request. Voice text enters the same handler as keyboard text. The response updates the visible UI through a supported action and moves the pending slot. Once microphone interaction has been initiated, concise voice follow-up remains active for either typed or spoken answers; playback begins only after the latest submitted answer has been processed, and the synthesized text targets the new pending field. Any newer input cancels stale playback/transcription and prevents stale speech from starting.
===PAGE===
# 3. Current Architecture Design — Document Rules and Recovery

## 11. Document result states

| Decision | Meaning in this POC | Allowed transition |
|---|---|---|
| PASS | Configured synthetic checks passed for the result evaluated | May proceed only if every other mandatory gate also passes |
| FAIL | A deterministic input/rule condition rejected the record | Stay blocked until factual data are corrected and checked again; do not override |
| REVIEW_REQUIRED | A route-specific requirement, unsupported purpose, data gap or review fixture needs authoritative verification | Stay blocked and escalate to a current approved source/operator |

## 12. Example reason codes and resolutions

| Example code | Interpretation | Expected resolution |
|---|---|---|
| `COUNTRY_CODE_INVALID` | Country name/code did not resolve against registry | Select the full country name or valid ISO-2/ISO-3 suggestion |
| `PASSPORT_EXPIRED` | Expiry precedes departure in synthetic date comparison | Check source value; use a valid document or escalate for a replacement |
| `PASSPORT_VALIDITY_REVIEW` | Demo validity threshold triggered | Review itinerary and actual destination requirement; obtain an authorised current check |
| `DOCUMENT_DOB_REQUIRED` | DOB missing for a child/infant/senior check | Enter the correct DOB shown by the fictional document |
| `DOB_MISMATCH` | Document DOB differs from booking passenger record | Reconcile actual source data and revalidate; escalate if unresolved |
| `PURPOSE_REQUIRES_DESTINATION_RULES` | Purpose may need a special permit | Obtain authoritative route/nationality-specific rule result; keep blocked |
| `DURATION_EXCEEDS_DEMO_THRESHOLD` | Stay exceeds demo screening limit | Verify truthful intended duration with a current destination-specific source |

## 13. Corrective behaviour

A failure card must name the passenger and journey wherever returned, show the raw rule reason, explain why it matters, then give the appropriate correction or escalation. A formatting error can be corrected in the form. An expired or mismatched document requires a valid source correction. A missing official entry-rule result cannot be fixed by changing the purpose or inventing a date; it requires an authorised document check. Error prose must not imply the passenger is approved until the underlying mandatory gates pass.
===PAGE===
# 4. Non-Functional Requirements — Security and Privacy

## 4.0 Security principles

- **Data minimisation:** Use only fictional passport numbers and synthetic passenger identifiers during development and demonstrations. Do not place real passenger PII in screenshots, fixtures, logs or test evidence.
- **Secret management:** `SARVAM_API_KEY` and persistence credentials are backend secrets. Do not commit real `.env` files or include keys in frontend bundle variables.
- **Transport security:** Microphone access requires a secure browser context (HTTPS or localhost). Production deployments must use HTTPS for all application/API traffic.
- **Session protection:** Session-specific API routes validate the session token. Tokens and credentials must not be printed in logs or chatbot messages.
- **Input validation:** Validate at UI and API. Treat the browser and LLM output as untrusted. Never assume a country code, PNR, seat code or document value is valid just because it was generated by chat.
- **No privilege by prompt:** The assistant can request actions but the API and gate rules remain authoritative. Chat is not permitted to change a failed result into a pass.

## Sensitive data and retention

The local demo can use in-memory state. If Redis/Mongo persistence is enabled, set a short configurable TTL and define whether document drafts/results or only workflow metadata may be stored. Preserve only the events needed for reproducing synthetic test outcomes. Do not persist real passport numbers in an unauthorised POC. Audit records should use opaque passenger keys where feasible and must exclude secrets and full audio payloads.

## Voice privacy

The microphone is user-initiated and should show recording state. The client sends recorded audio to the backend proxy, which sends it to Sarvam when configured. Explain this data path in any demonstration using actual user speech. The current design stops clips at 25 seconds and releases local audio resources after playback/cancellation; production deployment requires a formal retention and data-processing review.
===PAGE===
# 4. Non-Functional Requirements

## 4.1 Archiving

The POC should retain enough synthetic artifacts to reproduce a test flow, understand a gate decision and diagnose a failure. Any retained artifact must be clearly labelled as demo data. The repository itself provides versioned source code; session records may be in-memory or backed by configured persistence. The current code does not imply a production archival service.

| Data / artifact | Proposed handling |
|---|---|
| Synthetic booking and workflow session | In memory for local demo; configurable TTL when persistent store is enabled |
| Document draft and validation result | Synthetic values only; minimum retention necessary for the test flow |
| Seat/add-on choices and skip state | Retain for the active session so each passenger/journey remains consistent |
| Assistant exchanges | Keep bounded conversation context; exclude secrets/audio blobs |
| Backend diagnostic events | Keep only required metadata; configure rotation/TTL before shared deployment |
| Generated solution document and build/test evidence | Store with versioned project documentation and review artifacts |
| Model/speech request bodies | Do not archive by default; no raw voice audio is written to long-term store |

Retention must be explicit and tested. If persistent session storage is enabled, expired sessions and related document state should be removed together where possible. Production-like use requires a data owner, retention period, deletion path and privacy approval.

## 4.2 Logging

Logging must be sufficient to trace the workflow without exposing sensitive values. Useful events include request correlation ID, route, component, status code, latency, current workflow phase, gate evaluation code, number of results and adapter outcome. Do not log session tokens, API keys, raw passport identifiers, unredacted audio or complete personal-detail messages.

Failures should identify the failed component and retryability. User-facing errors should be actionable but should not expose stack traces, secrets, internal file paths or full untrusted provider payloads. Test runs should verify both the presence of useful diagnostics and absence of prohibited fields.

## 4.3 Auditing

The demonstrator can record events needed to understand synthetic workflow transitions: booking lookup, eligibility run, document result, seat choice/skip, ancillary confirmation/skip, mock check-in completion and generated record count. Events should be time-stamped and tied to an opaque session/passenger/journey key. The audit record is diagnostic evidence, not a legal airline record.

A production implementation should define event schema/version, actor identity, immutable storage requirements, retention and access audit. Changes to rules catalog, model prompts, retrieval sources, adapter contract and decision mapping must be associated with a release/configuration version.
===PAGE===
# 4.4 Scalability, 4.5 Rate Limit and 4.6 Performance

## 4.4 Scalability

The current demonstrator is a single API/browser architecture with local mock adapters. A production deployment should keep application instances stateless where feasible and place durable session state in a configured store. Long-running speech generation, retrieval and document-provider calls should have bounded timeouts and explicit concurrency limits. Per-passenger/per-journey state must remain isolated across concurrent sessions.

Scalability depends on:

- API worker count and CPU/memory capacity.
- Session-store latency, connection limits and TTL cleanup.
- Provider quotas, speech request concurrency and maximum recording duration.
- RAG index/search capacity and size of retrieved context.
- Browser resource usage for long conversations and multiple audio blobs.
- Country-registry load/caching strategy (the ISO list is small enough to cache per process/client).

## 4.5 Rate Limit

Apply limits to user-facing APIs and upstream calls. In particular, throttle repeated search, voice transcription/synthesis, FAQ/retrieval and session mutation requests. Retry only idempotent/transient operations, use exponential backoff with jitter where appropriate, and do not automatically retry booking confirmations or other state-changing operations without an idempotency contract. Handle HTTP 429 and provider quota responses explicitly.

Recommended controls: per-session request ceiling, voice clip size/duration limit, concurrent upstream-call cap, bounded messages/audio/text, and a circuit breaker for repeated upstream service failure. Do not amplify provider rate limits with a loop of retries.

## 4.6 Performance

No production SLA has been measured for this mock POC. Track browser interaction latency, API p50/p95/p99, session-store time, rule-evaluation time, retrieval latency, STT/TTS request-to-response time and time to first audible response. Separate network latency from model/provider latency.

Voice currently uses Sarvam REST endpoints and playback begins after the TTS response returns. The frontend interrupts previous speech and aborts a pending transcription when typing or a newer recording starts. Once voice mode has been initiated, typed or spoken answers both trigger concise playback of the response’s new pending-field question, rather than replaying a previous long answer. Identical spoken text is still processed as a new turn if it answers the current pending field. Speech playback pace defaults to 1.5. For lower dead-air latency, implement and test Sarvam streaming STT/TTS (or chunked playback), not simply faster playback of a complete clip. Suggested baseline acceptance: no stale speech after a new input; no duplicate submission from one microphone stop; and next-question playback never begins until the current answer has been submitted and accepted by the workflow.
===PAGE===
# 4.7 System Management and Monitoring

Monitoring should cover the full passenger journey, not only API availability. Because this is a POC, the list below is a target operational checklist rather than a statement that a production metrics platform is deployed.

## Application / workflow monitoring

- Search request success/failure, latency and schema validation failure rate.
- Booking lookup success, session creation/expiry and invalid session-token attempts.
- Eligibility decision distribution and the rule codes contributing to non-PASS outcomes.
- Document validation decisions by code, missing fields and review-required reasons.
- Passenger/journey seat confirmation and explicit skip counts; conflicts or invalid codes.
- Ancillary selection/save/confirm/skip success rate.
- Mock check-in completion and boarding-pass record generation errors.

## AI / knowledge monitoring

- Assistant turn latency and unanswered/abstained question rate.
- Tool/action validation failure, malformed slot answer and recovery success.
- Retrieval relevance, missing citation/source availability and stale-source flag.
- STT success/empty transcript/invalid media/timeout/429 outcomes.
- TTS latency, cancelled audio, playback error and rate-limit responses.
- Token/character use where exposed, but not raw secrets or full personal details.

## Integration monitoring

- Country registry endpoint health and country-resolution failures.
- Redis/Mongo availability if enabled and in-memory fallback/reset events.
- Sarvam endpoint health, HTTP status distribution, timeouts and key configuration status.
- Official Timatic connector remains disabled; monitor it as *not configured* until an approved provider integration exists.

Alerts should be based on sustained error rates, unexpected increases in review-required outcomes, stale retrieval sources or provider degradation. Every alert needs an owner and a safe recovery action.

## 4.8 User Role and Access Management

| Role | Responsibility |
|---|---|
| Passenger / demo user | Operates synthetic booking/check-in UI |
| Developer | Maintains API/UI logic, adapters and regression tests |
| QA engineer | Runs workflow, validation, security and browser tests |
| Platform / operations owner | Configures secrets, hosting, session store, monitoring and retention |
| Document-rule owner | Reviews official rule source and determines approved update procedure |
| Security/privacy reviewer | Approves data handling, speech-provider use and logging controls |

Production controls should include secure authentication/authorization appropriate to each role, least privilege, secret management, HTTPS, dependency review, logging redaction, audit trail protection and controlled operator access. The current POC's demo session token is not a substitute for enterprise user identity or airline staff authorisation.
===PAGE===
# 5. Risk and Mitigation

| Risk | Impact | Current / recommended mitigation |
|---|---|---|
| Mock rules mistaken for official rules | False approval or unsafe guidance | Mark outputs mock-only; keep unsupported cases `REVIEW_REQUIRED`; integrate authorised Timatic AutoCheck before live use |
| External requirements become stale | Incorrect travel guidance | Version official sources, track freshness, refresh on schedule, abstain when data are missing or stale |
| Assistant overstates unsupported answer | Wrong booking or document action | Typed action allow-list, schema validation, evidence checks and deterministic backend gates |
| Voice repeats or speaks a stale question | Wrong field / confusing interaction | Use current pending-slot context, turn IDs, cancellation on new input and concise current-question playback; stream speech as future work |
| Country/DOB/passenger data inconsistent | Invalid passenger record | Registry-backed name/code selector, server validation, DOB/category/senior checks and adult-companion links |
| Child/infant rules misapplied | Unsafe or invalid accompaniment assumptions | Explicit adult choice and age validation; replace conservative demo rule with current carrier policy before live use |
| Seat/skip state incorrect | Wrong seat or false assignment | Validate inventory/uniqueness per journey; track skip explicitly; never random-assign `NOT_SELECTED` passengers |
| Document failure is not actionable | Passenger cannot recover safely | Show person/route/code, exact reason, correction or authorised escalation; never override non-PASS gates |
| Secrets/PII leak | Security/privacy incident | Keep secrets server-side; use synthetic data; redact logs; minimise persistence and review dependencies |
| Session/provider failure | Lost or misapplied state / unavailable voice | Test session expiry, bound retries/timeouts, keep text fallback, preserve confirmed state and define TTL/monitoring |

**Residual risk.** No POC test proves compliance with every destination’s entry rules. Production decisions require current authorised operational sources and airline policy.
===PAGE===
# 6. Appendices and References

## Appendix A — Official and technical references

1. IATA, **Timatic AutoCheck** — automated passport, visa and health-document checks: https://www.iata.org/en/services/compliance/timatic/autocheck/ (reference only; not connected by this POC).
2. IATA, **Timatic solutions** — travel-document compliance product overview: https://www.iata.org/en/services/compliance/timatic/ (reference only).
3. Sarvam AI, **Speech-to-Text REST API** — `unknown` language auto-detection and keyterm prompting: https://docs.sarvam.ai/api-reference/speech-to-text/transcribe.
4. Sarvam AI, **Text-to-Speech REST API** — Bulbul models and pace range: https://docs.sarvam.ai/api-reference/text-to-speech/convert.
5. Sarvam AI, **Bulbul v3 best practices** — REST versus streaming latency: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/bulbul-v3-best-practices.
6. `pycountry`, FastAPI/Pydantic, React and Vite documentation — country registry, REST schema and frontend implementation references.

## Appendix B — Representative application files

| File | Purpose |
|---|---|
| `frontend/src/BookingPortal.jsx` / `CheckInWorkspace.jsx` | Booking, PDP, check-in, document review, seat/extras and boarding-pass flows |
| `frontend/src/CountryCodeInput.jsx` | Full-name / ISO-2 / ISO-3 search and normalisation |
| `frontend/src/sarvamVoice.js` | Microphone capture, Sarvam proxy, playback cancellation and current-turn speech |
| `frontend/src/theme-overrides.css` | Floating assistant, responsive page and country-picker styles |
| `backend/app/main.py` | REST routes, assistant routing, country and voice proxies |
| `backend/app/orchestrator.py` / `assistant_schema.py` | Check-in workflow, typed slots, country/date/seat parsing and companion helper |
| `backend/app/commerce.py` / `adapters/navitaire.py` | Booking/seat/add-on schemas, age checks and mock inventory |
| `backend/app/adapters/timatic.py` | Mock document outcomes and disabled official integration boundary |
| `backend/tests/test_api.py` / `test_commerce.py` | API, country, age, seat, document and workflow regressions |

## Appendix C — Environment configuration

Set `SARVAM_API_KEY` only on the backend host. Current defaults are `SARVAM_STT_MODEL=saaras:v4`, `SARVAM_TTS_MODEL=bulbul:v3`, `SARVAM_TTS_PACE=1.5` and `SARVAM_TIMEOUT_SECONDS=45`. Pace is passed to Sarvam within the supported 0.5–2.0 range; actual speech latency still depends on the network and the upstream API response time. `VITE_API_BASE_URL` contains only the API base URL, never provider secrets. Configure optional persistence separately and verify TTL/retention.

## Appendix D — Validation and approval status

The repository was checked with Python compilation, backend `pytest`, Vite production build and browser workflow scripts. These validate synthetic demo behaviour, not production certification. Sarvam live voice requires a valid backend key for end-to-end tests. Official Timatic access needs approval, credentials, the current API contract and tests for provider errors/timeouts. Multi-city booking creation remains out of scope. A document-rule owner must approve any transition from mock rules to live passenger use.
