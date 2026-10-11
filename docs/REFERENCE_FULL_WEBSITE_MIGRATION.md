# Full website reference analysis and migration

## Summary

The additional uploaded archives describe several related systems rather than one complete deployable airline site. The POC combines their usable interaction patterns in one React + FastAPI project while keeping all offers, schedules, booking confirmation and payments synthetic.

## Reference families and what was carried over

| Reference archive family | Patterns found | Migration in this POC | Not copied / limitation |
|---|---|---|---|
| `nlp-booking-widget-search-engine-atlas_mongodb_integration.zip` | Airport/city natural-language lookup, tag matching, optional Azure embeddings and MongoDB Atlas Vector Search | Local airport catalogue with deterministic tag/text search, airport autocomplete and route-intent action | Azure credentials, managed vector index setup, and any live availability claim |
| `nlp-booking-widget-search-engine-Pay.zip` | Local FAISS/SentenceTransformer alternative to hosted vector search | Preserved the local/no-key search path and source catalog | Bundled model payload and unrelated environment-specific settings; branch name does not itself establish a payment gateway |
| `OrchestrationAgent.zip` | Intent route between booking/upgrade tool actions and FAQ/RAG responses | Flight-scoped site assistant with constrained route-fill/navigation actions, otherwise source-grounded FAQ | Azure OpenAI settings, internal legacy carrier actions/upgrade APIs, and any secrets in the archive |
| `pdp and agui runtime.zip` and PDP context branches | PDP context resolution, request/response events, passenger form progress, resume/context ideas | Passenger details form plus optional PDP guidance endpoint; chat only invokes PDP guidance for form-help intent | Microsoft Agent Framework/Azure runtime assumptions and any proprietary endpoint credentials |
| `PDP-Backend 1.zip` | Sequential passenger fields, contact data, consent and confirmation | Sequential passenger form, contact and consent gates | Automatic guessing of missing personal values; the POC leaves optional DOB blank instead of inferring it |
| `PDP_RAGpipeline.zip`, `Pdpfinal_rag 1.zip`, `pdp-agent-releases-pdp-agent-1.0.1.zip` | Chroma/RAG retrieval, chunking/versioning, FAQ/document ingestion | Local offline source-grounded FAQ corpus, citations and abstention on unsupported specifics | Legacy carrier policy answers are not promoted to Emirates-demo policy; no live crawler or model-based evaluator is enabled |
| `pdp-agent-feature-pdp_agent_cosmos-db.zip`, `pdp-agent-feature-pdp_agent_cosmos_merge_branch.zip`, Atlas/Mongo branch | Conversation/session persistence and metadata storage alternatives | Existing optional MongoDB session/audit/checkpoint infrastructure; local in-memory fallback remains for laptop demo | Cosmos-specific account setup and connection secrets; no cloud database credentials are embedded |
| `web-check-in-agent-*` references | Booking retrieval, SSR requirement checks, passenger×journey Timatic validations, seats, ancillaries and mock pass | Existing check-in workspace accessible from booking confirmation/manage booking | No actual Navitaire booking mutation, live immigration approval or valid travel document |

## Website journey implemented

1. **Book a flight:** origin/destination autocomplete from a 197-airport reference catalogue; one-way/round-trip dates; adult/child/infant counts; cabin selection.
2. **Search results:** three deterministic synthetic itineraries per direction, illustrative local times, stop/duration details and cabin-matched fare families.
3. **Select flights:** choose an outbound flight/fare and a return flight/fare when round trip.
4. **Passenger details:** sequential passenger forms, names as on documents, optional DOB, nationality/gender, contact details and explicit demo-terms consent.
5. **Extras:** synthetic baggage, lounge, Wi-Fi and meal options with transparent mock amounts.
6. **Review/payment simulation:** fare and extras summary with a success/decline simulator. No card field exists and no payment processor is called.
7. **Confirmation:** synthetic PNR, itinerary, fare total and a warning that the reference is not valid for travel.
8. **Manage booking:** synthetic booking lookup by record locator and surname, and a shortcut to mock check-in.
9. **Check in:** preserved multi-step check-in workspace with deterministic eligibility, passport mock validation, seat selection, ancillary confirmation and decorative pass.
10. **Flight status/help/chatbot:** demo-only flight status endpoint and flight-scoped FAQ/chat assistant with source citations and constrained UI actions.

## Backend API contracts

- `GET /api/v1/airports/popular`
- `GET /api/v1/airports/search?q=...&limit=...`
- `POST /api/v1/flights/search`
- `POST /api/v1/bookings/confirm`
- `GET /api/v1/flight-status?flight_number=EK203&departure_date=YYYY-MM-DD`
- `POST /api/v1/pdp/context`
- `POST /api/v1/site-assistant/chat`
- Existing check-in endpoints under `/api/v1/bookings/lookup` and `/api/v1/sessions/...`

Booking creation registers the generated PNR in the in-process mock Navitaire adapter, allowing the newly booked synthetic record to be retrieved in the same running backend. This registry is not durable across backend restarts or multiple workers. MongoDB session persistence does not turn the mock booking inventory into a distributed/authoritative reservation database.

## Important semantics and safety boundaries

- `MOCK_ONLY` stays true: search results, flight numbers, schedules, fares, availability and PNRs are synthetic.
- The “payment successful” button is a simulated outcome. It captures no card data and performs no charge.
- New synthetic bookings can be passed to the check-in workspace, but online check-in eligibility is still governed by the separate mock check-in rules/window.
- The FAQ corpus uses official public reference URLs for high-level public information and explicit project-policy docs for demo semantics. The uploaded legacy/third-party FAQ corpus remains reference-only and is not presented as Emirates policy.
- The chatbot cannot perform a payment or create a booking by itself. It can suggest/fill route fields and navigate; user confirmation remains mandatory.
- The production provider diagnostic workflow remains separate from the passenger website. No successful live provider call has been established by these local tests.
- The frontend is a Vite/React static application and backend is FastAPI. It is not a replica of Emirates' proprietary source code or an official Emirates service.

## Verification

The new backend commerce suite tests search, airport autocomplete, one-way/round-trip result shape, mock confirmation, PNR lookup in the same process, declined simulated payment, invalid fare IDs, consent, route intent and PDP/FAQ routing. Browser/production build verification still requires dependencies installed in a connected environment.
