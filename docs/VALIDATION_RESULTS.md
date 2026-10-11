# Validation results

**Run date:** 11 October 2026  
**Scope:** source bundle validation in this authoring environment; not a formal security assessment or test against live provider systems.

## Completed locally

- `python -m compileall -q app tests` — passed.
- `PYTHONPATH=. pytest -q` from `backend/` — **70 passed**, including booking-portal commerce contracts, assistant/PDP routing, LangGraph query planning/decomposition, general-topic retrieval from active sources, compound multi-source answers, live-search mock responses, source-scope checking, evidence-gap abstention, sensitive-input blocking, high-risk abstention, unsupported-generation correction, optional reference RAG bridge, Azure OpenAI v1 and dated-version request construction, Azure-first provider precedence, query planning, and grounded synthesis.
- Unit tests exercise the Navitaire GraphQL request/auth contract, cookie persistence between GraphQL and REST booking state, SSR/document status mapping, Timatic token + Bearer flow, country lookup, documentRequest schema, strict `evaluationResult` decisions, passenger×journey fan-out, fail-closed aggregation, configured carrier guard, and the diagnostics gate.
- `backend/data/knowledge.json` is loaded by the FAQ/store and Agentic RAG tests; Python compilation succeeded for app and test modules.
- Azure OpenAI REST requests were validated with mocked HTTP responses for v1 and dated API-version routes; no live Azure deployment was called from this environment.
- The Vite production build succeeded with Node.js 22.15.0: 1,897 modules transformed; output was generated successfully. The build was checked after RAG code changes (the frontend itself was unchanged).
- Manual code scan confirmed no `verify=False` usage in executable Python, no reference `.env`/credential files copied into the bundle, and no provider secrets embedded in source.

## Flight website coverage

The website exposes booking search, fare selection, PDP/contact/consent, extras, simulated payment and synthetic confirmation; the FAQ assistant now runs the LangGraph Agentic RAG graph over the active versioned local corpus and optional configured retrieval/search services. It exposes source/corpus provenance in citations and health metadata; it also includes Manage booking, flight status, Help/FAQ and a constrained route-filling assistant. Commerce tests validate that a new mock PNR can be retrieved by the existing check-in lookup while the same backend process remains running. The simulated payment intentionally accepts only `approve`/`decline`; no payment-card data or payment gateway is involved.

## Scenarios covered by automated tests

The prior mock passenger-workflow scenarios remain covered: mock-only service status, booking mismatch, complete synthetic flow, visa/document review gate, expired passport, prompt-injection refusal, FAQ citation, multi-journey fan-out and seats, cross-session authorization, payment/codeshare blocks, and FAQ abstention/qualification cases plus RAG query retries, sensitive-value blocking, high-risk authority gates, unsupported-model-output correction, trusted-host validation and optional reference retriever behavior.

Provider tests use `httpx.MockTransport` and synthetic responses; one full endpoint test fakes both providers to confirm the reference order, request construction and that the route never issues a boarding pass.

## Not verified in this environment

- **No live Navitaire or Timatic provider request was made by this test suite.** The tests use `httpx.MockTransport` and synthetic responses.
- The supplied screenshots do not include a successful Timatic `documentRequest` response body or the complete response contract. Decision normalization therefore remains deliberately conservative.
- The reference screenshots contain several distinct hosts/products (booking retrieval, non-production authentication and UAT upgrade endpoints). Their credentials and sessions have not been proven interoperable; use only a provider-approved, internally consistent environment.
- `npm ci` uses the checked-in `frontend/package-lock.json` and still requires package-registry access in the user environment.
- `pip-audit`, `npm audit`, SBOM and image vulnerability results: not completed; CI includes blocking audit steps.
- A complete external search service and an actual private Chroma/SQLite reference service were not available for live end-to-end testing; these optional adapters were tested with mock HTTP responses.
- The actual LangGraph Agentic RAG graph was exercised by the 70-test suite. Redis-backed LangGraph checkpointer and Redis/MongoDB/Neo4j external service integration were not comprehensively integration-tested.
- Browser automation, multi-worker race/load tests, penetration testing, or company/security/legal approval were not performed.

## Release interpretation

The 70-test suite verifies local mock behavior, workflow mapping, the Agentic RAG graph, Azure OpenAI request construction, and adapters against synthetic HTTP responses/fakes. It does not certify live Azure OpenAI connectivity, live provider connectivity, correctness of the complete Timatic schema, production access, absence of vulnerabilities, legal compliance, or Emirates approval. Before an internal review, run connected CI, dependency/SBOM/secret scans, tests using authorized provider-issued synthetic sandbox records, and capture actual response schemas without secrets or passenger PII.
