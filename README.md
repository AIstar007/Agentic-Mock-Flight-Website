# Emirates-Inspired Flight Booking + Web Check-in POC

> **Unofficial, synthetic proof of concept.** It is not an Emirates product, is not connected to Emirates, Navitaire, Timatic, immigration authorities or a live airline departure-control system, and does not generate valid travel documents. All bookings, passengers, seat inventory, ancillary prices and international-document decisions are mock data.

A reference-informed, full-stack demonstration of an airline-style booking website plus a page-based multi-agent check-in journey. It replaces the reference repositories' Microsoft Agent Framework runtime with **LangGraph + FastAPI**, preserves the key workflow concepts from the supplied sources, and separates **Navitaire** (booking/check-in) from **Timatic** (international travel-document requirements). The passenger-facing journey remains mocked by default. An opt-in, token-protected staging workflow now mirrors the reference order from Navitaire booking retrieval and SSR checks through Timatic per passenger × journey and Navitaire passenger-document validation. It is read-only and cannot issue boarding passes; see [`docs/REFERENCE_WORKFLOW_MIGRATION.md`](docs/REFERENCE_WORKFLOW_MIGRATION.md).

## Full flight-booking website journey

The public website shell now includes **Book a flight**, **Manage booking**, **Check in**, **Flight status**, **Help/FAQ**, and a flight-scoped chatbot. The booking path is:

1. Select origin and destination from airport autocomplete, trip type, dates, passenger counts and cabin.
2. Search synthetic itineraries and choose an outbound fare, plus a return fare for round trips.
3. Enter passenger names, nationality, gender and contact details; date of birth is optional for a regular adult in this demo, but required for child/infant and explicitly marked senior-citizen rule checks. Explicitly accept demo terms.
4. Select illustrative extras (baggage, lounge, Wi-Fi, meal).
5. Review the itinerary and total, then choose a simulated payment success or decline. **No payment card fields, real gateway, charge or reservation mutation exists.**
6. Receive a synthetic booking reference that can be opened in Manage booking or handed into the existing mock check-in workflow within the same running backend process.

The booking website's flight numbers, schedules, seat/fare availability, amounts, status results and booking references are synthetic. Newly generated demo bookings are kept by the in-process mock booking adapter, so they are not durable across backend restarts or shared across multiple backend workers. The local airport catalogue and source-grounded FAQ are based on the supplied references; they do not provide live flight inventory.

The booking UI and mapping decisions from the new reference archives are documented in [`docs/REFERENCE_FULL_WEBSITE_MIGRATION.md`](docs/REFERENCE_FULL_WEBSITE_MIGRATION.md).

## Features

- Responsive Emirates-inspired flight-booking portal plus preserved check-in workspace, both with flight-only assistant support.
- Synthetic flight search, fare families, passenger details, ancillary selection, simulated payment, confirmation/PNR, Manage booking and check-in lookup.
- Reference-derived booking eligibility fan-out/fan-in using deterministic Python rule functions (never an LLM verdict).
- Passenger and passport details form with Pydantic validation.
- Per-passenger × per-journey mock Timatic-style validation, aggregation, and `PASS` / `FAIL` / `REVIEW_REQUIRED` decisions.
- Seat map and mock atomic reservation adapter.
- Ancillary catalogue, per-passenger selection, explicit confirmation even when no add-ons are selected.
- Final review and decorative mock boarding-pass cards (not scannable or valid for travel).
- LangGraph state graph for routing page/agent actions; `MemorySaver` by default or optional Redis-backed checkpointer.
- Session context uses a 7-day default TTL; MongoDB redacted operational event sink and TTL index are optional.
- Optional Neo4j rule provenance/explanation index; local JSON + deterministic Python validators remain the source of truth.
- LangGraph-driven Agentic RAG: query classification → dynamic query planning/decomposition → multi-source retrieval → evidence relevance grading/reranking → bounded self-correction/retries → high-risk authority/freshness checks → grounded synthesis → answer evaluation/correction or abstention. It is not a fixed query-to-answer map. By default it uses the active versioned SQLite FAQ corpus; optional private Chroma/SQLite retrieval and a general SearXNG-compatible official-site search endpoint expand coverage to unseen topics.
- Synthetic demo scenarios for clear path, visa review, expired document, domestic validation, codeshare, and balance due.
- Security headers, input models, session token authorization, redacted agent events, and tests for blocked actions.

## Source/reference analysis

See:

- [`docs/REFERENCE_ANALYSIS.md`](docs/REFERENCE_ANALYSIS.md) — what each uploaded reference family contributes and what was not copied.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — component boundaries and the two-provider model.
- [`docs/ADAPTER_CONTRACTS.md`](docs/ADAPTER_CONTRACTS.md) — mock provider contract and future live adapter conditions.
- [`docs/SECURITY_GOVERNANCE.md`](docs/SECURITY_GOVERNANCE.md) — threat model, security controls, data handling and governance review.
- [`docs/EVALUATION_PLAN.md`](docs/EVALUATION_PLAN.md) — evaluation matrix and release gates.
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — phased work breakdown and known gaps.

## Prerequisites

- Python 3.11 or 3.12 recommended (Docker image uses Python 3.12).
- Node.js 22+ and npm.
- Optional: Docker Desktop for containerized local services.

## Quick start — Windows PowerShell

### 1) Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000 --env-file .env
```

Open `http://localhost:8000/api/v1/health`. The API docs are at `http://localhost:8000/docs` in development.

### 2) Frontend — a new terminal

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

If the frontend cannot reach the backend, set `VITE_API_BASE_URL=http://localhost:8000` in `frontend/.env.local`, restart Vite, and check that the backend `FRONTEND_ORIGIN` is `http://localhost:5173`.

## Quick start — Docker Compose

```bash
docker compose up --build
```

Open `http://localhost:5173`. For a local optional persistence stack, copy `.env.example` to `.env`, set the service URLs in that file (using the Compose service hostnames shown in the comments), then start `docker compose --profile stateful up --build`. Add `--profile graph` as well if enabling Neo4j. A configured backend connection must be started with the matching profile; otherwise startup intentionally fails instead of falling back silently. Do not expose database ports beyond loopback on a developer machine.

The base stack intentionally works without Redis/MongoDB/Neo4j. Published ports bind to `127.0.0.1` for local development. Memory-backed session state and an in-process audit sink are appropriate only for a single-process local demonstration; they are not a production storage architecture. The optional Redis profile uses Redis 8 to support the LangGraph Redis checkpointer modules; review its exact licensing terms before company use.

## Demo journey

1. Start from the booking page. Use a synthetic scenario button or enter a PNR + surname:
   - `EM8K2Q` / `THOMAS` — intended international happy path.
   - `EM5M8Q` / `CONNECT` — two-journey international itinerary; document checks fan out by passenger × journey and every journey requires a seat.
   - `EM4R9Z` / `KHAN` — deterministic visa-review escalation; cannot check in.
   - `EM6D1M` / `JOHNSON` — enter a past expiry date to trigger document failure.
   - `DM3A1B` / `TESTER` — synthetic domestic validation branch; this route is not a claim about actual Emirates service.
   - `EM9C3X` / `PARTNER` — codeshare/operating-carrier mock restriction.
   - `EM7Q1P` / `BALANCE` — outstanding balance mock restriction.
2. Open passenger/document details and fill fictional passport fields. For the success fixture, use the passenger's existing synthetic date of birth, a future expiry with at least 180 days remaining, and `MOCK_VERIFIED`. The 180-day threshold and evidence flag are fixture rules, **not universal requirements or actual visa verification**.
3. Save and run eligibility checks plus mock Timatic-style validation. Unknown or uncertain rules block progress.
4. Browse the full 40-row mock cabin seat map and select one seat per passenger/journey, or explicitly skip seat selection.
5. Select optional ancillaries or explicitly confirm no add-ons. The chatbot can confirm before editing a section, review its inputs one by one, and keep an existing value when you say “skip”.
6. Review all outcomes and create a decorative synthetic pass.
7. Use the sidebar for FAQ and supported workflow commands, for example `lookup EM8K2Q THOMAS`, `check my eligibility`, `show seats`, or `What is the online check-in window?`.

The sidebar is intentionally limited to airline/check-in questions. Its rule-based command parser is a UI demonstration, not an open-ended autonomous controller. Session-scoped commands require the session token that the UI keeps in `sessionStorage`; it is cleared by **Reset session**. This is a local demonstration token scheme, not a replacement for Emirates identity and booking ownership controls.

## Provider separation: Navitaire **and** Timatic

- **Navitaire adapter**: the passenger journey uses mock booking lookup, seat inventory/assignment and ancillary catalogue. The protected reference staging workflow can retrieve the live/sandbox booking, read SSR requirements, and perform the read-only passenger document-check operation after Timatic approval.
- **Timatic adapter**: the passenger journey uses synthetic document rules and per-passenger/per-journey aggregation. The protected reference staging workflow builds provider-shaped requests from the normalized booking and maps only `evaluationResult=YES/NO/CONDITIONAL`; actual responses must be verified against the current authorized tenant's contract before operational use.
- The mock adapter result carries `provider: MOCK_TIMATIC` and `mock_only: true`. It cannot claim visa approval, document authenticity or legal permission to fly.

Provider reference pages:

- [Navitaire New Skies](https://www.navitaire.com/new-skies-reservation-system)
- [Navitaire Digital Platform](https://www.navitaire.com/digital-platform)
- [IATA Timatic AutoCheck](https://www.iata.org/en/services/compliance/timatic/autocheck/)
- [IATA Travel Centre](https://www.iata.org/en/travel-centre/)
- [Emirates online check-in FAQ](https://www.emirates.com/english/help/faq-topics/preparing-to-travel/faq/how-do-i-use-online-check-in/)

These public references are architecture/knowledge sources only. They do not grant API credentials or imply Emirates approved this POC.

## Optional data/services

### MongoDB sessions + LangGraph checkpointing (recommended for free hosting)

Set `MONGODB_URI` to enable persistent sessions, LangGraph checkpoints, and the audit sink. The project includes `langgraph-checkpoint-mongodb` so the free-hosted profile does not require Redis. Session data and graph checkpoints use a seven-day TTL policy; MongoDB TTL deletion is asynchronous, so expiration is not an exact deletion deadline. The graph checkpoint stores only an allowlisted action, opaque session ID, and random request/result references; raw document payloads and tool outputs are kept out of graph state. Audit events use a SHA-256 session fingerprint and store operation metadata, outcomes and correlation IDs only; they intentionally do not store raw PNR, name, passport values, messages or prompts.

### Optional Redis session store + LangGraph checkpointer

Redis remains optional for a paid TLS-enabled deployment. Do not use Redis Cloud Free over a public connection because its Free Essentials plan does not support TLS. If `REDIS_URL`/`LANGGRAPH_REDIS_URL` are configured, the app uses Redis for sessions and graph checkpoints; compatible RedisJSON and RediSearch commands are required by the pinned LangGraph Redis integration. Keep `LANGGRAPH_STRICT_MSGPACK=true`, review current advisories, and fail closed if configured Redis is unavailable. When Redis is blank and MongoDB is configured, the app uses MongoDB for sessions and graph checkpoints.

Reference: [langgraph-redis](https://github.com/redis-developer/langgraph-redis) and [LangGraph MongoDB checkpointer](https://github.com/langchain-ai/langchain-mongodb/tree/main/libs/langgraph-checkpoint-mongodb). Review compatibility, serialization behavior and current advisories before pinning versions for any company environment.

### Neo4j rule graph

Set `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD`, then run `backend/data/rule_graph.cypher` in Neo4j Browser. The graph is an explanatory/provenance layer. `rules_catalog.json` and Python deterministic validators remain authoritative in the demo; a graph result must not independently approve a passenger.

## Updating the FAQ knowledge corpus

The website chatbot uses a local, SQLite-backed **versioned and approved** FAQ corpus. On first startup, `backend/data/knowledge.json` seeds the initial approved version; generated `.sqlite3` state is intentionally not shipped. To create and activate a new version after editing and reviewing that seed file, run from the `backend` directory:

```powershell
python -m app.faq_cli --label passenger-assistant-2026-10-11 --change-note "Expanded booking/check-in schema and passenger FAQ knowledge" --activate
```

Use a unique label for each version. Only HTTPS public sources or explicitly scoped project-policy documents should be included. Do not promote any other airline’s reference material as Emirates policy. The passenger assistant implementation guide is `docs/PASSENGER_ASSISTANT_KNOWLEDGE.md`. The booking sidebar can populate a complete labelled one-line search request or collect each search field, passenger name/contact/consent values and extras through the same schema validators as the visible form. The check-in sidebar supports per-passenger synthetic document fields, labelled multi-field responses, seat selection and explicit extras confirmation. Invalid/ambiguous inputs preserve state and ask a follow-up; schema errors and non-pass eligibility/document rules are surfaced with actionable explanations. No chat action bypasses server-side workflow gates. Restart FastAPI after activation so the in-memory retriever reloads the current corpus. The default retrieval component is deterministic lexical retrieval over the active versioned corpus, not a live web crawler or production semantic judge. It records operational stages in `retrieval_trace` (without storing private reasoning). The default answer is extracted from a cited source. If an optional model generates an unsupported answer, the generation is discarded; if the source extract cannot be supported, RAG abstains. Visa/immigration/entry and similar high-risk questions require current HTTPS authoritative evidence with the specific rule represented; the currently seeded sources do not provide a passenger-specific Timatic decision, so those questions should remain unapproved/review-required.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Mode and adapter status |
| GET | `/api/v1/demo-bookings` | Synthetic scenario index |
| GET | `/api/v1/airports/popular` | Default airport suggestions |
| GET | `/api/v1/airports/search?q=...&limit=...` | NLP/tag-assisted airport autocomplete |
| POST | `/api/v1/flights/search` | Synthetic itinerary and fare search |
| POST | `/api/v1/bookings/confirm` | Create a synthetic booking after simulated payment result |
| GET | `/api/v1/flight-status` | Deterministic illustrative flight status |
| POST | `/api/v1/pdp/context` | Optional passenger-form next-field guidance |
| POST | `/api/v1/site-assistant/chat` | Flight-scoped website FAQ and safe UI actions |
| GET | `/api/v1/rules` | Rule catalog / optional Neo4j read |
| POST | `/api/v1/bookings/lookup` | PNR + surname lookup; creates session token |
| GET | `/api/v1/sessions/{sid}` | Authorized public session summary |
| POST | `/api/v1/sessions/{sid}/eligibility` | Deterministic booking rules |
| POST | `/api/v1/sessions/{sid}/documents` | Save synthetic passport fields |
| POST | `/api/v1/sessions/{sid}/documents/validate` | Mock Timatic-style per-passenger/per-journey checks |
| GET/POST | `/api/v1/sessions/{sid}/seats` | Seat map / mock assignment |
| GET/POST | `/api/v1/sessions/{sid}/ancillaries` | Catalogue / passenger selection |
| POST | `/api/v1/sessions/{sid}/ancillaries/confirm` | Explicitly confirm add-ons or no add-ons |
| POST | `/api/v1/sessions/{sid}/check-in` | Generate mock pass if every hard gate passes |
| GET | `/api/v1/sessions/{sid}/events` | Redacted event list |
| POST | `/api/v1/chat` | FAQ and allowlisted chat actions |
| POST | `/api/v1/provider-tests/navitaire/booking-retrieve` | Optional protected, read-only New Skies staging probe |
| POST | `/api/v1/provider-tests/workflows/document-validation` | Protected reference-style booking prechecks → Timatic per passenger/journey → Navitaire document check; read-only, never issues a boarding pass |
| GET | `/api/v1/provider-tests/timatic/country` | Optional protected Timatic staging country lookup |
| GET | `/api/v1/provider-tests/timatic/parameters` | Optional protected Timatic staging parameter lookup |
| POST | `/api/v1/provider-tests/timatic/document-request` | Optional protected Timatic staging documentRequest; unknown decisions fail to review |

Session endpoints require `X-Session-Token`; see the OpenAPI spec in development mode for request schemas. The app has demo-level rate limiting and security headers, not enterprise identity integration.

## Tests and security checks

```powershell
cd backend
python -m pytest -q
pip-audit -r requirements.txt
```

```bash
cd frontend
npm install
npm run build
```

These commands are test instructions. The included CI workflow runs Python tests and a frontend build. Dependency advisory results must be interpreted against the exact resolved lockfile; do not claim “zero vulnerabilities” merely because the workflow passed. See [`docs/EVALUATION_PLAN.md`](docs/EVALUATION_PLAN.md) for adversarial cases and release gate ideas, and [`docs/VALIDATION_RESULTS.md`](docs/VALIDATION_RESULTS.md) for the exact checks completed and items still pending in this offline environment.

## Limitations / not implemented

- The end-user booking/check-in workflow remains mock-only. The “successful payment” control only simulates a successful response and does not contact a payment gateway or collect card data. The opt-in, protected reference diagnostic workflow can execute authorized staging reads through both providers: Navitaire booking retrieval and SSR requirements, deterministic reference prechecks, Timatic documentRequest per passenger × journey, then Navitaire passenger-document check only after explicit Timatic `YES`. It never submits a document update, changes a seat/ancillary, completes check-in or issues a boarding pass. See [`docs/PROVIDER_DIAGNOSTICS.md`](docs/PROVIDER_DIAGNOSTICS.md) and [`docs/REFERENCE_WORKFLOW_MIGRATION.md`](docs/REFERENCE_WORKFLOW_MIGRATION.md).
- No official Emirates airline rule engine, identity provider, payments, boarding document, inventory, DCS transaction or actual reservation update.
- No live visa lookup. `MOCK_VERIFIED` is just a synthetic control value.
- No actual passport authenticity check, image/OCR document analysis, biometric verification or immigration decision.
- RAG is a LangGraph Agentic RAG POC: dynamic search planning/decomposition, active-version retrieval, optional private Chroma/SQLite and allowlisted live official-site search, reranking/evidence grading, bounded rewrite/retry, optional checked synthesis, correction, and abstention. The evidence verifier is a conservative heuristic rather than a formal entailment proof. It is not an official Timatic decision engine. High-risk travel-rule queries abstain unless current, authoritative, specific evidence is available.
- Redis/MongoDB/Neo4j are optional; local memory fallbacks are single-process demo only.
- No promise of compliance or security certification. Emirates/AlonOS security, legal, privacy, architecture and aviation stakeholders would need to approve any internal or production use.

## License / reference repository use

This POC was rebuilt from scratch as a synthetic demonstration, using the supplied reference archives to understand workflow concepts and data contracts. The downloaded reference archives are not copied into this bundle, and their `.env`, credential material, log token values and internal files are deliberately excluded. Review upstream project licences and company authorization before using any original reference code or provider snippets in a company deliverable.


## Hosted deployment

For a public URL accessible from anywhere, use the Render Blueprint and MongoDB Atlas Free as documented in [`docs/HOSTED_DEPLOYMENT.md`](docs/HOSTED_DEPLOYMENT.md). This requires a one-time setup in your own Render and MongoDB Atlas accounts; credentials are never embedded in this repository. Redis and Neo4j Aura are optional.


## General flight-domain Agentic RAG (not fixed FAQ queries)

The existing FAQ/chat API now runs the LangGraph workflow in `backend/app/agentic_rag.py`. It does not map question strings to prewritten answers. It dynamically plans and decomposes searches from each question, retrieves from the active versioned FAQ corpus and optional private semantic/live-source backends, reranks and grades candidates, checks source scope, retries weak retrieval, evaluates answer support, corrects unsupported synthesis, and abstains when evidence is missing. The seed corpus includes general sources for baggage, refunds, disruptions, pets, onboard Wi-Fi/services, special assistance, dangerous goods/power banks, and other flight topics; it is not an exhaustive rule database.

For topics not present in the local FAQ corpus, configure an **approved SearXNG-compatible search endpoint**. `RAG_WEB_SEARCH_URL` should point at its `/search` API, and its JSON format must be enabled. Result URLs are accepted only if they are on `RAG_WEB_ALLOWED_HOSTS` or the built-in HTTPS allowlist; redirects to other hosts are refused when page text is fetched. Set `RAG_WEB_FETCH_PAGES=0` to test using search snippets only, or `2` to fetch a maximum of two allowlisted result pages per query for fuller evidence and source-date metadata.

Suggested free/local approach: run a private SearXNG instance on loopback and configure its search JSON format. Do not use an unknown public search proxy for sensitive requests; although the chat blocks common passenger identifiers, queries still leave the app to reach the configured search service. Never send passenger details, booking references or passport data to the FAQ chatbot.

Official SearXNG setup documentation: https://docs.searxng.org/admin/installation-docker.html
Official SearXNG settings template: https://github.com/searxng/searxng/blob/master/utils/templates/etc/searxng/settings.yml

Environment example:

```dotenv
RAG_MAX_RETRIES=2
RAG_TOP_K=5
RAG_WEB_SEARCH_URL=http://127.0.0.1:8080/search
RAG_WEB_ALLOWED_HOSTS=emirates.com,iata.org,icao.int,dgca.gov.in,gcaa.gov.ae,gov.uk,gov.au,gov.in,gov.ae,europa.eu,travel.state.gov
RAG_WEB_FETCH_PAGES=2
```

Azure OpenAI is the preferred model provider for the planner and grounded answer-synthesis steps. Configure the backend environment with your Azure resource and deployed chat model:

```dotenv
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=YOUR-DEPLOYMENT-NAME
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=v1
AZURE_OPENAI_REASONING_MODEL=auto
```

Use the endpoint shown on your Azure OpenAI/Foundry deployment page (which may use an `openai.azure.com` or `services.ai.azure.com` host) and the deployment name from Azure AI Foundry. Copy these variables into `backend/.env` for local development, or set them as secrets/environment variables in your hosting service. Put the real key in your local or hosted secret configuration—not in Git—and never paste passenger identifiers into chat. The RAG loads `backend/.env` automatically, with existing process/host environment values taking precedence. Azure settings take precedence over the legacy `RAG_LLM_*` compatibility settings. With Azure credentials absent, the agent still runs deterministic planning and source-extractive synthesis. See Microsoft's [Azure OpenAI chat REST reference](https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/chat) for endpoint and `api-key` header details.

Leave `RAG_WEB_SEARCH_URL` blank to run offline against the active corpus. For an existing installation with an older SQLite corpus, activate the expanded source set from the `backend` directory:

```powershell
$env:PYTHONPATH = "."
python -m app.faq_cli --label passenger-assistant-2026-10-11 --change-note "Expand flight FAQ sources, booking/check-in schema and passenger guidance" --activate
```

The active corpus is refreshed on each question without restarting FastAPI. A configured but unavailable retriever/search service fails closed rather than silently switching sources. Exact airline-specific answers are scoped to the named carrier; other airline sources are identified as such. For any topic not represented in the active corpus, broad online coverage requires an approved `RAG_WEB_SEARCH_URL`; without it the offline agent abstains rather than inventing policy.

**Hosting:** `http://127.0.0.1:8080/search` is only an example for local development when FastAPI and SearXNG can reach each other on the same machine/network. A Render backend cannot call your personal computer's `127.0.0.1`; set `RAG_WEB_SEARCH_URL` in the hosted backend to a separately hosted, reachable HTTPS search service, or leave it blank and accept offline corpus-only retrieval. Set `FRONTEND_ORIGIN` to the deployed frontend origin and set Vercel's `VITE_API_BASE_URL` to the public HTTPS URL of the deployed FastAPI backend. Keep Azure/search API credentials only in the backend/hosting environment, never in `VITE_*` variables or the browser bundle.

## Video-reference workflow and Emirates-inspired UI

The browser UI now provides Emirates-inspired charcoal/red/gold styling across the booking portal and web-check-in workspace, a minimized circular blue aircraft chatbot launcher, page-aware error explanations, speech input/read-aloud controls, and a mock seat-map stage between optional extras and payment review. Both page controls and assistant actions update the same frontend state; backend workflow gates remain authoritative. Payment methods and QR are presentation-only and no payment provider is called. Booking and check-in data are synthetic. See `docs/VIDEO_WORKFLOW_IMPLEMENTATION.md` for the implemented journey and the validated limitations.

### Local-first passenger assistant knowledge

The website chat imports `app.agentic_rag.SelfCorrectiveRAG`, which uses the activated SQLite corpus as a local fallback and uses the persistent Chroma index if `chromadb` is installed. On a clean environment, run `python -m pip install -r backend/requirements.txt` (from repository root) to install the optional Chroma dependency included by `backend/requirements.txt`, then from `backend` run `python scripts/sync_chroma.py` to create/sync the local index. Check `GET /api/v1/knowledge/status` for active version, source counts and Chroma status. Leave `RAG_RETRIEVER_URL` unset for local-first website retrieval; set it only when intentionally using the optional configured bridge. The present approved source corpus has 37 reviewed documents and is not a country-by-country exhaustive legal database. High-risk travel eligibility remains source/authority gated.


### Passenger age rules and chat sidebar

The booking and check-in assistants open from a floating launcher at the bottom-right and operate on the same form/workflow state as the page. The flight-search chatbot collects origin, destination, trip type, departure/return dates, adult/child/infant/senior-citizen counts, and cabin class in sequence; direct field edits update the visible form, and `proceed` launches search once the questions are answered. Cabin aliases include Business Class and First Class. Search results remain synthetic.

Booking add-ons are selected and confirmed per passenger, with a per-passenger skip option. Seat assignment is also per passenger and journey: confirming a seat or skipping that passenger advances the workflow, and the demo will not invent a seat for a skipped passenger. On web check-in, chat begins with booking reference/PNR, then passenger surname, and requires eligibility before collecting fictional passport number, nationality, issuing country, DOB as required, passport expiry, purpose of stay and duration. Verification status is computed automatically and cannot be manually selected. A regular adult may skip an unprovided DOB in the document card; other required fields and server-side gates cannot be skipped. The generated mock boarding-pass cards show `PNR - <reference>` and `Last Name - <first adult surname>` to illustrate the details used for manual lookup, with `NOT_SELECTED` when no seat was assigned.

The booking confirmation API validates the selected INF/CHD/ADT category against age on outbound departure; it blocks infants under seven days and infant-to-child age transitions across a return itinerary for airline-office handling. The check-in mock applies a conservative under-16/no-adult gate. Public references are in `backend/data/official_source_catalog.json`; active reviewed summaries are in `backend/data/knowledge.json` and the versioned SQLite corpus. These are mock safeguards, not live Emirates/Timatic eligibility. The exact Emirates logo badge is stored at `frontend/public/emirates-logo-badge.svg`. Browser-back navigation preserves a valid app view, and the Emirates logo returns to the flight-search home.


## Updated passenger validation and Sarvam voice

- The booking passenger form validates names, date of birth, nationality, gender, email, telephone format, travel age-category and adult-companion links. A future date of birth is rejected; a selected senior traveller must be at least 60 on departure. An infant must be explicitly linked to one adult passenger, and the local strict-under-16 rule blocks a minor without an eligible adult companion. This is a synthetic POC policy, not a replacement for official airline unaccompanied-minor handling.
- Passenger nationality and check-in nationality/issuing country use a searchable full ISO-3166 registry: enter a full country name, alpha-2 code or alpha-3 code. Suggestions display the full country name and both codes; the saved value is normalized to alpha-2 and is checked against the server-side registry.
- Seat codes must exist in the displayed mock inventory (`1A` through `40F`, by actual row/letter combinations). Each passenger/journey seat can be selected or explicitly skipped; skipping one does not silently random-assign a seat to that passenger.
- The document screen asks for purpose and duration of stay, checks passport expiry, ISO country codes and supplied DOB, and derives a mock `PASS`, `FAIL` or `REVIEW_REQUIRED` result automatically. The 180-day passport-validity and 90-day tourism/business thresholds are demonstration heuristics only. **No official IATA Timatic lookup, immigration service or passport-authenticity service is called by this POC.** To integrate Timatic/AutoCheck, obtain authorised API access and confirm the official request contract, service terms and applicable policy requirements; until then the UI must not present local rules as official travel advice.
- Both chat interfaces collapse to a floating bottom-right launcher. Back/Next, passenger toggles, per-person seat/add-on choices, and field Skip/Modify controls retain the current form state while moving between workflow pages. Returning to the first/home/flight-search view is the intentional flow reset.
- Voice input/output is proxied server-side to Sarvam AI; browser-native speech recognition/synthesis is not used. Configure the following on the **backend** host, then restart FastAPI:

```dotenv
SARVAM_API_KEY=your_sarvam_subscription_key
SARVAM_STT_MODEL=saaras:v4
SARVAM_TTS_MODEL=bulbul:v3
SARVAM_TTS_PACE=1.5
SARVAM_TIMEOUT_SECONDS=45
```

The frontend must use HTTPS (or localhost) for microphone access, and the user must grant microphone permission. Voice input automatically stops at 25 seconds to fit Sarvam's synchronous REST limit. A repeated spoken response is processed through the current pending-field handler; new typing/recording cancels old audio and any stale transcription. After the microphone has been used, voice follow-up remains active for typed or spoken replies, but the next prompt is synthesized only after that answer has been processed. Automatic playback reads the current question or concise next action at the configured pace. TTS uses the complete REST response rather than streaming, so model/network latency can still occur before playback starts. The implementation follows the official [Saaras Speech-to-Text REST API](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe) and [Bulbul Text-to-Speech REST API](https://docs.sarvam.ai/api-reference/text-to-speech/convert). If the key is not configured, text chat remains available and the voice endpoint returns a clear configuration error; the key must never be placed in a `VITE_*` variable or bundled into the frontend. 


## Current-turn voice and country search

`GET /api/v1/countries?q=IND&limit=10` searches the ISO country registry (249 entries at the current package version). The booking and check-in country selector displays the country name and both codes; a code such as `IND` or `ARE` and a full name such as `India` or `United Arab Emirates` resolve to the same country record.

Both assistant UIs route typed text and Sarvam transcripts through the same submit handler. While speech is waiting or playing, a new answer cancels the older audio and stale transcription. Once the mic is used, concise voice follow-up remains active even if the user types the next answer. The assistant reads the newly pending question only after the latest answer has been accepted by the workflow, never an older long answer. The backend sends `mode=transcribe` for Saaras v3/v4 and only adds `keyterms` when v4 is configured. `SARVAM_TTS_PACE=1.5` is the default; the accepted range is bounded to 0.5–2.0. This REST implementation does not stream audio; a streaming endpoint is a future latency improvement.

The document failure chatbot now displays each non-PASS passenger/journey, evaluation code, why it failed or needs review, and a code-specific correction or escalation. Cases requiring authoritative entry rules remain blocked until an approved current source can decide them.

The matching solution design document is `docs/Emirates_Passenger_Journey_Solution_Design.docx` (27 authored pages following the supplied template structure).
