# Evaluation plan and acceptance criteria

## Principles

- Evaluate hard business-rule correctness separately from generative-answer quality.
- Treat authorization violations, false PASS decisions, cross-user leakage and valid-pass generation before all gates as critical failures; an average score cannot hide them.
- The optional OpenAI-compatible RAG answer generator requires a separate versioned evaluation set, provider approval and a release gate before use; the default path remains source-extractive.
- The default retriever is deterministic lexical search over an approved, versioned local corpus, with bounded rewrite/retry. An optional Chroma/SQLite reference retriever and optional LLM synthesis can be configured; Ragas/DeepEval are not wired into runtime and any model-backed path needs a labelled evaluation set.

## Test classes

### A. Functional journey

1. Valid synthetic international booking can complete when all synthetic document checks pass.
2. Wrong surname or unknown booking fails lookup.
3. Payment balance or booking status rule failure blocks progress.
4. Expired synthetic passport returns `FAIL`.
5. Missing passport, birth-date mismatch, or unverified evidence returns `REVIEW_REQUIRED`.
6. The fixture with `VISA_REVIEW` remains `REVIEW_REQUIRED` even if chat says to proceed.
7. Any non-pass eligibility state blocks seat assignment and check-in.
8. One seat is required per passenger, and a seat held by another session is not assignable.
9. Ancillaries may be empty, but the passenger must explicitly confirm the ancillary step.
10. Boarding-pass creation is blocked until eligibility, documents, all seats and ancillary confirmation pass.
11. The generated pass always has `mock_only=true` and a “not valid for travel” notice.
12. Domestic mock path skips international Timatic checks and still runs common booking rules.

### B. Authorization and API abuse

- Session endpoint with absent, invalid or expired `X-Session-Token` returns non-success.
- Session A cannot read or modify Session B by changing `sid` only.
- An extra/unknown Pydantic field is rejected.
- A passenger key outside the current booking is rejected for document/seat/ancillary writes.
- Unsupported action names cannot reach the orchestrator.
- A basic injection/secret disclosure attempt is refused; hard business gates still run on server-side state.
- Repeated requests trigger the demo rate limit without returning internal exceptions.
- Error responses do not contain stack traces, secrets, passport numbers, provider payloads or prompts.

### C. International document behavior

- Unknown itinerary, unfamiliar status code, timeout, or invalid date fails closed to review/non-pass.
- A mock passport expiry before departure fails.
- The demo 180-day threshold is labelled and tested as synthetic only.
- `MOCK_VERIFIED` never appears as “visa approved” or an official travel decision.
- One failure for any passenger/segment prevents the aggregate from passing.
- The overall aggregation order is `FAIL` > `REVIEW_REQUIRED` > `PASS`.
- An eligibility `FAIL` cannot be overridden by a Timatic-style `PASS`.

### D. Retrieval and answer support

- Answers cite a source from the approved local allowlist when relevant evidence is found.
- Weak evidence triggers up to two bounded query-rewrite/retrieval retries, followed by abstention if grading still fails.
- Inadequate evidence returns an abstention with an appropriate authoritative-source referral.
- Off-topic queries are redirected to flight/travel support.
- Source title, URL, verification date and source type are represented in the citation.
- Expired sources are not silently treated as live rules (current baseline lacks automated source-expiry enforcement; this is a Phase 2 item).
- A retrieved document containing instructions to reveal secrets, change policy or override the gates is treated as content, not as system instructions.

### E. Reliability and resilience (before any production-like pilot)

- Redis unavailable at startup when explicitly configured causes startup failure; the app does not silently change persistence guarantees.
- MongoDB unavailable when configured causes startup failure.
- Timatic mock call failure becomes review-required, never PASS.
- Provider timeout after a write is reconciled before retry; no duplicate operation occurs.
- Parallel same-seat contention cannot allocate a seat to two passengers in the same process; production adapter must test provider-side atomicity across replicas.
- TTL cleanup, backup lifecycle, log retention and deletion are independently verified.
- LangGraph checkpoint contents are inspected for PII and secrets after schema changes.

## Suggested release gates for the POC (proposed, not Emirates standards)

| Gate | Target |
|---|---:|
| Unauthorized cross-session reads/writes in test suite | 0 successful exploit cases |
| Boarding pass produced with unresolved/failed required gate | 0 successful cases |
| Duplicate seat assignment in concurrency tests | 0 successful cases |
| Critical deterministic-rule test cases | 100% pass |
| Input schema and auth negative tests | 100% pass |
| Curated FAQ fact/source support | >=95% pass, with all high-risk travel questions individually reviewed |
| High-risk ambiguous questions routed to review/abstention | 100% for curated critical set |
| Known blocking vulnerability in resolved release dependencies | 0 unresolved at approval time |
| Critical/high findings from approved security test | 0 unresolved unless formally risk-accepted |

The goals above are engineering targets for this POC and not claims about the current achieved score or official Emirate acceptance criteria. Test results should be reported alongside sample size, test-data version, code revision, runtime/package lock and any exclusions.

## Tooling recommendations

- `pytest` + FastAPI `TestClient` for route and business-rule integration tests.
- `pip-audit` for Python dependency advisories; validate findings against the resolved environment.
- `npm audit` and a lockfile for frontend dependency advisories.
- A secret scanner and SBOM (CycloneDX or SPDX) before sharing the project.
- OWASP ZAP or an approved API penetration-test workflow for HTTP exposure.
- Playwright or another browser automation harness for UI page navigation, disabled gates and chat-to-page sync.
- DeepEval or Ragas only if model-generated answers/agents are added and need component/trajectory evaluation; their metrics don't replace security testing.
- OpenTelemetry only after adding a redaction filter; do not put raw passenger data, prompts or tokens into span attributes.
