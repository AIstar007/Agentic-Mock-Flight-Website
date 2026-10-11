# Implementation plan, task split and status

## Workstreams

| Workstream | Responsibilities | Current status |
|---|---|---|
| 1. Reference analysis | Compare DomesticCodified, Nova features, 1.0.7 release and ValidationByAgent; document assumptions | Complete for visible repository behavior; a few provider semantics remain unknown |
| 2. Core backend | FastAPI, typed models, agent dispatch, deterministic eligibility gates | Implemented; 67 tests pass in local deterministic mode and provider workflow tests use synthetic HTTP/fakes |
| 3. Navitaire boundary | Synthetic booking lookup, seat inventory, ancillary catalogue, check-in boundary | Mock workflow complete; protected staging route maps booking state, per-journey SSR requirements and passenger-document-check responses for the reference flow; seat/ancillary/check-in mutations remain mock-only |
| 4. Timatic boundary | Request-shape mapping, passenger×journey concurrency, aggregation and fail-closed outcomes | Mock workflow complete; protected staging token/country/parameter/documentRequest diagnostics plus per-passenger/per-journey reference workflow implemented; only the reference `evaluationResult` enum is trusted until a successful tenant response contract is confirmed |
| 5. UI | Full booking portal, airport NLP autocomplete, fares, PDP/contact/consent, extras, simulated payment, Manage booking, status, FAQ/chat, plus check-in workspace | Implemented in React/Vite; JSX syntax passed, production bundle not verified because npm install timed out |
| 6. Persistence | In-memory local mode, optional Redis session store/checkpointer, Mongo TTL audit, optional Neo4j graph | Opt-in connectors implemented; deployment compatibility must be verified |
| 7. RAG/guardrails | LangGraph agentic planning/decomposition, active-version and optional semantic/web retrieval, relevance grading/reranking, bounded rewrite/retry, authority/freshness/scope gates, grounding evaluation, corrective extract and abstention | Rule-based support checks remain heuristic and not a production semantic judge |
| 8. Security/governance | Threat model, PII redaction, no credentials in bundle, source-of-truth separation, review checklist | Documentation and implementation baseline included; formal sign-off not performed |
| 9. Evaluation | API and workflow tests, adversarial test ideas, release gates | 67 tests pass, including full reference-workflow, booking-commerce, FAQ corpus versioning and assistant-routing tests against synthetic provider fakes; real sandbox calls, external dependency scans and browser E2E tests remain pending |
| 10. CI/release | Python test workflow and frontend production build | Python suite passes (47 tests); JSX syntax passes; Vite production build, lockfile generation and dependency scans need connected CI |

## Critical path

1. Freeze a clean synthetic API contract independent from provider schemas.
2. Run deterministic booking eligibility in concurrent rule tasks.
3. Run the Timatic-style mock per passenger and itinerary journey, returning a non-pass outcome on uncertainty.
4. Enforce server-side gates before seats, add-on confirmation and boarding-pass creation.
5. Wire each React page to an authenticated API call and refresh state after chatbot-triggered actions.
6. Verify start-to-end successful and blocked flows with tests.
7. Validate exact dependencies, package advisories and the UI build.
8. Use the optional protected reference workflow with authorized staging credentials and provider-issued synthetic bookings. Keep the passenger workflow mock-only until successful response contracts, authorization, data rules, and mutation semantics are approved.

## Next iteration priorities

### Priority 0 — required before an internal pilot

- Obtain real process owner approval for the page order, official eligibility rules, PDP fields, error messages and escalation destinations.
- Review session auth; current local session bearer is not enterprise SSO or passenger identity authentication.
- Implement a robust, user-bound identity/booking access model if any real booking data is integrated.
- Run and resolve dependency/security scans against exact generated lockfiles and container images.
- Verify frontend build and browser-driven workflow tests.
- Define secure cookie/XSS/CSP policy and deploy only behind an approved gateway.
- Add server-side input-size limits and distributed rate limiting at the gateway.

### Priority 1 — quality and resilience

- Add automated source freshness tracking and versioned rule ingestion, with review/approval workflow.
- Build exact rule-owner map and rule tests from airline-approved specifications.
- Add true human-in-the-loop review queue with ownership/status/SLA; current flow only blocks and instructs manual review.
- Add idempotency store and recovery for any provider call that might commit before a timeout.
- Add browser tests covering sidebar commands, back-navigation, session refresh and gate locking.
- Add observability with redaction, operational dashboards and alerting.

### Priority 2 — optional scale/advanced AI

- Consider Chroma as a vector retrieval backend after corpus/versioning policy is defined; deterministic lexical baseline is sufficient for first demonstration.
- Add LLM-powered query rewriting or answer synthesis behind typed interfaces only after LLM-provider approval, prompt-injection test data, offline evaluation, cost/time limits, and source-grounded output checks.
- Evaluate NeMo Guardrails for conversational input/output and tool intent protection. Do not delegate actual authorization or eligibility to a guardrail framework.
- Add Kafka or Redis Streams if there is a demonstrated need for durable event fan-out and independent consumers; not required to run the current user journey.

## Acceptance checklist

- [ ] Clean install instructions work from a fresh clone.
- [x] `python -m pytest -q` passes in the local deterministic fallback mode (47 tests; provider HTTP is mocked in tests).
- [ ] `npm ci && npm run build` passes in connected CI after generating and committing `frontend/package-lock.json`.
- [x] Valid synthetic international path completes and produces only decorative mock pass.
- [x] Expired document is blocked.
- [x] Unknown/visa-review scenario cannot generate pass.
- [x] Payment-due/codeshare scenarios fail eligibility and cannot select seats.
- [x] Unauthorized session access is rejected, including cross-session token reuse.
- [x] FAQ source citation, out-of-scope behavior, and abstention for unsupported visa-exemption details are covered.
- [x] No uploaded `.env` files, token values, or credential material are copied into the bundle.
- [ ] Lockfile/SBOM/secret/dependency scan results reviewed before sharing.
