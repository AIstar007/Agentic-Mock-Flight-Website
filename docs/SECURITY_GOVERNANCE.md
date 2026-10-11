# Security, governance and privacy plan

**Status:** design guidance for a synthetic POC; not an Emirates security approval, audit report, privacy assessment, or compliance attestation.

## Data and environment boundary

- The distributable fixtures are synthetic. Do not replace them with real Emirates passenger data in a personal or unapproved environment.
- `.env`, provider secrets, tokens, passport numbers and raw provider requests must not be committed to source control or copied to LLM prompts, audit records, tracing attributes or support logs.
- The reference ZIPs included `.env`/credential-like material; none is included in this fresh bundle. Some reference paths logged token fragments and a request path used TLS verification disabled. Those patterns are excluded.
- The future provider adapter is intentionally non-operational until authorized credentials, endpoints, schemas and data processing terms are agreed. A public API description is not a license/credential.

## Trust boundaries

1. Browser and chat message — untrusted.
2. FastAPI request validation/session authorization — server enforcement boundary.
3. LangGraph orchestration and tool routing — trusted application code but can be influenced by user content if free-form model routing is added later.
4. Navitaire/Timatic adapters — external trust boundaries in any future live integration.
5. Redis/checkpoint store — can persist workflow metadata; strict access and retention required.
6. MongoDB audit store — restricted, redacted operational telemetry.
7. Neo4j and knowledge corpus — administrative writes only; user input cannot create/change authoritative rules.
8. Optional RAG model/retriever provider — not called by default; if configured it is an external data processor and requires source, privacy, network, retention and contract review.

## Dependency security review (checked 10 October 2026)

- LangGraph's official advisory list includes unsafe checkpoint JSON/msgpack deserialization issues. The checked-in dependency lower bounds were raised to current patched release floors from the reviewed ecosystem releases: `langgraph>=1.2.14`, `langgraph-checkpoint-redis>=0.5.2`, and `redis>=8.1.0`; MongoDB driver floor is `pymongo>=4.18.3`. The `LANGGRAPH_STRICT_MSGPACK=true` hardening setting is enforced before LangGraph import. These floors reduce exposure to the specific advisories reviewed; they are **not** a substitute for resolving, locking, and auditing the entire transitive dependency graph.
- Current package metadata for `langgraph-checkpoint-redis` requires RedisJSON and RediSearch modules (included in Redis 8+ distributions) and a compatible checkpoint serializer. The optional checkpointer path has not been executed in this offline authoring environment; verify the exact resolved wheel set and Redis server image in connected CI.
- Frontend package versions are directly pinned in `frontend/package.json`, but this environment could not create `package-lock.json` because the npm registry was unreachable. Generate and commit a lockfile in a connected trusted environment before using the CI workflow as a reproducible release gate.
- The GitHub workflow runs `pip-audit` and `npm audit` as blocking checks. A passing CI run is required before review; this current bundle has not had those network-dependent scans run to completion.

## Minimum security controls in this build

- Session token generated with a cryptographically strong random source, stored as a SHA-256 digest server-side and required by all session endpoints.
- Booking lookup requires exact record-locator/surname match to a synthetic fixture.
- Pydantic request models reject unexpected properties for operation bodies.
- Specialist agents only expose an allowlisted set of service actions via the orchestrator.
- Eligibility and document decisions are deterministic; unknown document outcomes fail closed into review.
- Every seat/ancillary/check-in operation re-checks prerequisite gates server-side.
- Chat refuses common prompt-injection/credential-disclosure asks with deterministic checks; this is a basic guard, not a complete prompt-injection defense.
- Security response headers, frontend CORS origin constraint, small request-size schemas and demo-level in-process rate limiting.
- Audit messages are summarized, and do not include raw PNR/passport values or full chat prompts. Browser-safe session serialization masks passport values.
- Redis-backed LangGraph checkpoint state excludes raw payload/result; only action names, random refs and opaque session IDs are checkpointed.
- Mock provider adapters are labelled and live adapter operation is disabled.

## Important limitations (do not overstate)

- The built-in session token is a local bearer token, not corporate SSO or passenger identity proof.
- Rate limiting is in process, not a distributed gateway/WAF and not sufficient for internet exposure.
- In-memory seat locks are single-process only. A production design must rely on provider atomicity or a distributed transaction strategy.
- There is no formal CSRF design because the current UI uses a bearer token rather than a cookie-based session; reassess this if auth changes.
- Redis/MongoDB/Neo4j network encryption/auth hardening depends on how those optional services are deployed.
- Session state contains the submitted synthetic passport values in this demo. Use synthetic values only. If a company approves actual documents, encryption, access control, field minimization, backup/TTL behavior, incident handling and residency need explicit design and review.
- Redis/Mongo TTL does not promise exact-time physical deletion from replicas, backups or logs. Expiry of the application session and legal retention/deletion are separate concerns.
- `MOCK_VERIFIED` and `mock_timatic_case` are synthetic controls; not official visa evidence.
- Live web search is disabled by default. RAG defaults to the active approved SQLite FAQ corpus, uses a LangGraph retrieve/grade/rewrite workflow, source-scope checks and source-grounded fallback, and abstains when evidence is weak. An optional SearXNG-compatible endpoint can broaden retrieval over allowlisted official-source domains. Optional LLM synthesis is disabled unless explicitly configured; it must use an approved HTTPS endpoint (or a loopback development endpoint) and its output is checked against retrieved passages. Optional reference retrieval and official web search are explicit and fail closed on outage. Search queries leave the application to reach the configured search provider, so only non-sensitive FAQ queries may be sent.

## Threats and mitigations

| Threat | Mitigation | Remaining limitation |
|---|---|---|
| Guess or steal session token | Random bearer token, digest at rest, no raw-token audit logging, `sessionStorage` | XSS could exfiltrate token; implement CSP, dependency scanning and stronger auth before exposure |
| Access another session by changing ID | Every session route checks token digest; test cross-session access | No full passenger identity provider/booking ownership system |
| Prompt injection tries to override rules | Fixed action allowlist, deterministic hard gates; do not make an LLM the rule authority | Text-pattern guard is not a general solution if arbitrary LLM/tool integration is added |
| PII exposure in logs/checkpoints | Redacted event sink, no raw payload in graph state, public serializer masks passport | Session record still stores synthetic field values; external trace instrumentation must be configured with redaction |
| Unsafe deserialization | No user-controlled LangChain `load`/`loads`; ordinary JSON + Pydantic | Review selected framework versions/advisories after dependency resolution |
| TLS interception against provider API | Live adapter requires HTTPS and no `verify=False` | Certificate/hostname verification and mTLS/auth must be implemented per actual provider contract |
| Unknown Timatic code coerced to success | Unknown provider outcome must be non-pass/review | Mapping must be audited against the actual AutoCheck schema when access exists |
| Stale or unsupported RAG advice | Active approved source versions, retrieval grading, bounded rewrites, HTTPS/trust/freshness gate for high-risk queries, answer-support check, source citations and abstention | The included corpus is small; the lexical support heuristic is not a semantic proof; source verification and expiry alerts require operational ownership before live use |
| Seat double assignment/retry | Mock async lock, gate checks, per-session assignment | Not distributed/production concurrency; provider idempotency needed |
| Denial-of-service via repeated requests | Request schema bounds + simple rate limit | Not distributed; deploy behind a company-approved gateway/WAF for load-facing testing |
| Malicious dependency/update | Lockfile/SBOM/advisory scans and CI proposed | Does not replace security review or signed release process |

## Open-source and dependency governance

Before any review/deployment:

1. Generate an exact lockfile and SPDX/CycloneDX SBOM from resolved runtime dependencies.
2. Run `pip-audit`, `npm audit --omit=dev` (and separate development audit), image scanning, license checks and secret scanning.
3. Review package provenance, maintainers, release cadence, transitive dependencies and upstream security advisories.
4. Pin tested versions; patch known exploitable issues with tested upgrades; don't downgrade blindly for compatibility.
5. Verify LangGraph persistence serializer behavior; don't allow untrusted checkpoints or untrusted serialized objects.
6. Review current advisory status on the exact versions of LangGraph, its SDK/checkpointers, Redis, MongoDB driver, Neo4j driver, FastAPI/Pydantic, Python base image and frontend packages.
7. Use dependency update pull requests with regression + security tests before upgrade.
8. Store secrets in an approved secrets manager; never in `.env` on a shared machine or in chat.

## Governance framework

Use the NIST AI RMF and its Generative AI Profile as a voluntary risk-management structure, and OWASP API Security/LLM guidance as security threat references. These frameworks are not themselves proof of compliance. Emirates policies, legal/privacy instructions, contract requirements and sector-specific aviation processes take precedence.

Governance artefacts expected:

- named business, product, engineering, security, privacy and operational owners;
- intended-use and prohibited-use statement;
- data-flow, classification and retention map;
- source/provenance registry and rule ownership;
- model/provider inventory and change-control record (for any model added later);
- validation and adversarial evaluation dataset versions;
- human-review/escalation process and incident plan;
- approved environment, credentials, region, vendor/contract and API scope;
- risk acceptance records for remaining findings.

## Source reading list

- NIST AI RMF: https://www.nist.gov/itl/ai-risk-management-framework
- NIST Generative AI Profile: https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- OWASP API Security Top 10: https://owasp.org/API-Security/
- LangGraph advisories: https://github.com/langchain-ai/langgraph/security/advisories
- LangChain advisories: https://github.com/langchain-ai/langchain/security/advisories
- Redis security: https://redis.io/docs/latest/operate/oss_and_stack/management/security/
- MongoDB TTL behavior: https://www.mongodb.com/docs/manual/core/index-ttl/
- Neo4j Cypher parameters: https://neo4j.com/docs/cypher-manual/current/syntax/parameters/
- Timatic AutoCheck: https://www.iata.org/en/services/compliance/timatic/autocheck/
- Navitaire New Skies: https://www.navitaire.com/new-skies-reservation-system

## Security sign-off status

No Emirates security, architecture, legal or privacy review has been performed. This project is a local synthetic POC and should only be presented as such. Do not connect it to production endpoints or real customer information without explicit authorization and formal review.

## Local container exposure and Redis license note

The default Docker Compose ports for the frontend and backend are bound to `127.0.0.1`, not all host interfaces. Optional Redis, MongoDB and Neo4j ports are also loopback-bound. This is a developer default, not an internet-facing deployment blueprint. Redis persistence is enabled with AOF for this local profile, but persistence does not itself provide encryption, authentication, backup governance or an approved retention/deletion story.

The optional Redis image in Compose uses Redis 8 (which includes the RedisJSON and RediSearch modules required by the checked-in LangGraph Redis checkpointer). Before distributing or operating Redis in a company environment, review the exact Redis server version's licensing terms and approved deployment route with legal/open-source governance. Redis 8 licensing terms can differ from older versions; do not assume a version/license is acceptable solely because it is open source or available as a public image.
