# Video workflow implementation notes

The uploaded workflow recording is treated as the interaction reference. This POC reproduces the major page structure and actions in an Emirates-inspired style; it does not copy Emirates production assets, connect to Emirates systems, issue real tickets, process payments, or validate real travel documents.

## Booking journey

1. Home page with Book / Manage / Experience / Where we fly / Skywards / Help navigation and Book, Manage, Check-in, Flight Status shortcuts.
2. Search route/date/trip type/passengers/cabin, with city/airport selection.
3. Browse synthetic flights and fare-family choices.
4. Complete passenger/contact details and required demo consent.
5. Select optional extras.
6. Select mock seat(s) for each passenger and journey, or skip. In-page and chat selection update the same React state. The server validates seat syntax, reserved-seat exclusions, passenger indexes, duplicate seat assignments, and outbound/return journey consistency before accepting a booking.
7. Review fare/passenger/seat/extras summary. Choose a demo payment method (UPI/QR, card, net banking or wallet) and a separate simulated approval/decline outcome. The QR is intentionally a labeled non-payable placeholder; payment processors are not called.
8. Confirm creates only a synthetic booking reference that can then be used in the mock Manage Booking / Web Check-in journey.

## Web check-in journey

The separate workspace uses the same black/charcoal, Emirates red and muted gold visual system. A synthetic booking is retrieved with reference + surname; the backend continues to gate eligibility, passenger document checks, seat assignment, extras confirmation and completion. Any mock boarding-pass artifact is not valid for travel. Page-level form errors open the assistant with a reason, technical detail and recovery suggestion.

## Page and assistant parity

- The booking assistant starts minimized as a circular blue plane button. The check-in assistant has the same minimized launcher style. Opening either shows a floating bottom-right panel rather than reserving a permanent page column.
- Both composers provide typed input, browser speech recognition when supported/permissioned, and read-aloud using browser speech synthesis. Voice and text use the same assistant endpoint/action dispatch; voice does not receive an expanded permission set.
- Booking chat receives active section/step, route form, selected flights/fare, passenger/contact values, consent, selected extras/seats, payment method/outcome, Manage Booking draft and the latest page error.
- Check-in chat receives active step, session stage, eligibility/document outcomes, selected passenger/journey, selected mock extras, visible page error and sanitized page state. It does not receive passport numbers through the added page-state metadata.
- Assistant actions update the visible page model via the existing event handlers. Booking lookup uses the same lookup endpoint as the page form; seat selection and payment-method selection are explicit actions. Errors remain visible in the page and are also explained in the assistant.
- The deterministic workflow/API remains authoritative. Assistant instructions cannot override backend eligibility, document, seat or completion gates.

## Live information and mock data

The booking schedule, fares, availability, seat map, PNRs, payment responses, check-in results and boarding-pass artifacts in this demo are synthetic. Live airline inventory and live flight status are not implied. The separate RAG setup and its web-search endpoint must be configured and checked independently; this visual/workflow update does not by itself make external live retrieval available.

## Validation performed

- Frontend production bundle built with Node.js 22.12.0 and the repository lockfile (`npm ci`, then `npm run build`).
- Backend test suite: 84 tests passed after adding coverage for seat assignment persistence/validation, chat seat selection, page-context help, Manage Booking lookup action, and chat payment-method selection.

Run locally:

```bash
# terminal 1
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm ci
npm run dev
```

Use a Node version compatible with the frontend `engines` field (>=22.12.0). All real booking/payment/immigration/provider transactions must remain disabled for this demo.

## Public-site research references

The visual/navigation structure was cross-checked against these official public Emirates pages on 11 October 2026:

- Homepage and top-level service entry points: https://www.emirates.com/in/english/
- Manage booking fields and supported functions: https://www.emirates.com/english/manage-booking/
- Online check-in entry and general window: https://www.emirates.com/in/english/manage-booking/online-check-in/

The local demo uses the familiar Book / Manage / Experience / Destinations / Skywards / Help structure and visible booking/check-in shortcuts, but all records, prices and transactions remain synthetic.

## Chat continuity and local-first retrieval update (11 October 2026)

- Booking assistant task, pending field and validated draft values persist in session storage across page switches/reloads. The API merges a follow-up into the existing draft; it no longer treats `from Delhi to Mumbai` as a date because the departure-date slot was pending.
- Natural-language turns such as `from Delhi to Mumbai`, `one way tomorrow`, and `proceed` are handled as route/date/task actions against the same visible form and flight-search action. Explicitly labelled all-fields-at-once input is parsed atomically, including 0 children/infants.
- The site chat explicitly imports `app.agentic_rag.SelfCorrectiveRAG`. Its normal retrieval path uses local Chroma when installed, with the active, source-reviewed SQLite corpus as lexical fallback. The separate optional remote bridge remains strict when `RAG_RETRIEVER_URL` is configured; unset that environment variable to use only the local-first site corpus, or configure its endpoint deliberately.
- `backend/data/faq_corpus.sqlite3` is refreshed and activated at version `emirates-demo-2026-10-11-poc-knowledge-refresh` (37 approved documents). The current bundled corpus contains curated Emirates and general/global aviation sources, not the complete current rules of every jurisdiction. Visa, transit, health, passport-validity, and country-specific entry queries remain authority/freshness-gated unless the corpus/source configuration can establish the exact current rule.
- Voice requests use the same validated action dispatch as typed requests. The booking and check-in launcher rings and backgrounds are Emirates red (`#c8102e`).
- Validation on this revision: 88 backend tests pass and the Vite production build passes.

## Passenger-age and persistent-sidebar repair (11 October 2026)

- The booking assistant is open by default on the booking website, and the check-in assistant is open by default on the check-in workspace. The prior CSS/layout work had been present, but the booking component still initialized `open=false`; the visible assistant was therefore only a launcher until clicked. Both workflows now start with the Copilot-style sidebar visible and keep minimize/reopen controls.
- The numbered flight-choice parser now uses actual regex word boundaries (`\b`) rather than a literal backslash sequence. `Choose flight 1` now selects a visible synthetic option and asks the user to choose a displayed fare; it no longer falls through to a generic FAQ response.
- Starting “Book a flight by chat” checks all current form fields using the same next-field validator. When the form is already complete, it confirms the values and offers `search flights` instead of incorrectly asking for the departure date again.
- Passenger date of birth is required in the booking form and validated server-side against the selected outbound date: INF under 2, CHD 2–11, ADT 12+. The mock blocks infants under 7 days, and blocks an infant who turns two before the return leg for airline-office handling. At check-in, the age/accompaniment rule uses DOB and departure date where available; passengers under 16 travelling without an accompanying person aged 16+ are blocked from online mock check-in. This implements conservative POC guardrails from public Emirates pages, not a live airline eligibility decision.
- The official Emirates logo badge asset from `https://c.ekstatic.net/ecl/logos/emirates/emirates-logo-badge.svg` is stored at `frontend/public/emirates-logo-badge.svg` and used in both booking and check-in headers. The red rectangular badge background matches the provided reference screenshot.
- `knowledge.json`, `official_source_catalog.json`, and the activated SQLite FAQ version now include official Emirates infant, child, unaccompanied-minor and under-16 online check-in guidance. The active version is `emirates-demo-2026-10-11-passenger-age-chat-fix` with 40 documents. This remains a curated POC corpus, not exhaustive country-by-country rules or live Timatic.
- Regression checks on this revision: 92 backend tests pass. A headless browser check confirmed the sidebar is visible on initial booking load, the official logo image is present, `Book a flight by chat` no longer asks for an already-populated date, `search flights` displays synthetic flight options, and `Choose flight 1` requests a fare choice. Check-in browser check confirmed the sidebar is open and `lookup EM8K2Q THOMAS` retrieves the seeded synthetic booking.
