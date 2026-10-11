# Reference repository analysis

## Scope and handling

Seven ZIP archives were supplied. The two uploads named `web-check-in-agent-DomesticCodified (1).zip` and `(2).zip` were byte-identical, so they were treated as duplicate snapshots. The unnumbered `DomesticCodified` archive had only a minor metadata/content variation. The versions were compared as different snapshots, not treated as independent sources for every repeated file.

This report describes the observed architecture and business flow. It does not certify that the rules are current Emirates policy or that the supplied repositories are safe to deploy. The rebuild deliberately does not copy `.env` files, tokens, potentially proprietary implementation, or TLS settings that turn certificate validation off.

## Reference families and their contribution

### 1. `web-check-in-agent-DomesticCodified` snapshots

**Contribution:** The clearest source for the codified eligibility branch and concurrent validation idea.

Observed workflow patterns:

- Retrieve a booking/PNR from a provider adapter.
- Determine the flight type: a booking is international if any included journey is marked international.
- Validate journey presence, payment status/balance, booking status, passenger name fields and length constraints, an adult passenger, carrier/codeshare conditions, queues, special-service requirements, the configured check-in window, and an international-infant condition.
- Run independent checks and aggregate their outcomes into an eligibility verdict.
- Distinguish the deterministic/codified path from the agentic path.

**Applicability boundary:** Some observed restrictions are specific to the original airline/project context, notably carrier rules and the international infant condition. These are preserved as *reference-derived synthetic rules* in the POC, not asserted to be universal airline or Emirates rules. The check-in windows in those snapshots differ by flow and must be confirmed by the target airline owner.

### 2. `web-check-in-agent-feature-Nova-31`

**Contribution:** API/server organization, provider/tool adapters, workflow integration points, and context passing around the check-in actions.

**Observation:** This feature snapshot uses Microsoft Agent Framework in its execution path. The new POC uses LangGraph as requested, retaining the architectural concept of an orchestrator dispatching to a constrained agent/service for each workflow function rather than retaining the Microsoft runtime.

### 3. `web-check-in-agent-feature-Nova-38`

**Contribution:** Planning/spec-driven workflow material and the continuation of page/service responsibility separation.

**Use in rebuild:** The phased task plan and documented acceptance criteria adopt this spec-oriented style. Any source-specific requirements not clear from the source have been called out rather than inferred as a requirement.

### 4. `web-check-in-agent-releases-web-check-in-agent-1.0.7`

**Contribution:** The most complete operational model among the provided snapshots.

Observed patterns include:

- A FastAPI/clean-architecture boundary.
- A dual codified-vs-agentic eligibility path.
- Timatic-style request-envelope building.
- Fan-out across passengers and journey/segment combinations, followed by response aggregation.
- Separate Navitaire and Timatic provider boundaries (with SkyPlus-related adapter references).
- Redis use for session/checkpoint or chat-history concepts and MongoDB for short-lived error/diagnostic records.
- An AG-UI/document workflow and explicit request/transaction correlation patterns.

**Use in rebuild:** The mock Timatic adapter remains the normal passenger journey. An opt-in provider workflow now reproduces the reference-style booking-to-document-validation order against authorized staging APIs, including per-passenger/per-journey request construction, strict `evaluationResult` parsing, Navitaire document-check only after Timatic `YES`, and result aggregation. Live workflow results are returned as indexed summaries only; they do not issue passes. Missing carrier and processing-entity settings block live testing.

### 5. `web-check-in-agent-ValidationByAgent`

**Contribution:** Agent-mediated validation and specialist validation concepts.

**Security concerns observed:** Some paths log or otherwise expose token material, and a Timatic request uses `verify=False`. These patterns are not reproduced. The POC keeps TLS verification enabled, reuses the Navitaire HTTP client across the booking retrieval sequence, and redacts response details. After the Bruno screenshots, the opt-in diagnostics grew into a protected read-only reference workflow: Navitaire booking retrieval + per-journey SSR checks, deterministic reference prechecks, Timatic per passenger × journey, conditional Navitaire document-check, and fail-closed aggregation. The regular passenger workflow remains mocked; SkyPlus document-update/write operations, seat/ancillary mutations, actual check-in and boarding-pass issuance remain disabled pending explicit API contracts and approvals.

## Consolidated target flow

1. Booking Retrieval Agent -> Navitaire adapter.
2. Eligibility Orchestrator -> concurrent deterministic booking-rule checks and fan-in.
3. Passenger/Document Agent -> validate input schema, then create a mock Timatic request for every passenger/journey.
4. Timatic adapter returns `PASS`, `FAIL`, or `REVIEW_REQUIRED` with synthetic codes and provenance.
5. Aggregator applies fail-closed precedence: any `FAIL` => `FAIL`; else any `REVIEW_REQUIRED` => `REVIEW_REQUIRED`; otherwise `PASS`. A booking eligibility failure cannot be overridden by a positive document mock result.
6. Seat Agent -> mock Navitaire seat inventory/assignment; one seat per passenger required.
7. Ancillary Agent -> mock catalog/selection; explicit confirmation required even when empty.
8. Review/PDP -> show validated state, selected seat, add-ons and blocking issues.
9. Check-in Agent -> only after gates pass, create decorative mock boarding-pass records.
10. FAQ Agent -> retrieve from a local approved-source allowlist, retry with domain expansion if weak, and abstain/escalate when relevance is inadequate.

## Divergences and intentionally excluded content

- All `.env` and credential-like values were excluded from the deliverable. If any values were genuine and archives were shared outside approved recipients, their owners should rotate them.
- `verify=False` was removed from the new design; TLS certificate checking must stay enabled.
- The new app does not copy the original Microsoft Agent Framework implementation.
- The POC does not infer that Emirates uses Navitaire New Skies. Navitaire is an explicit replaceable provider adapter because the user's scenario requests it; live system identity/integration must be confirmed.
- Timatic is not treated as public unrestricted API access. Real AutoCheck API access, contract, tenant credentials, permitted data use and schema must be approved.
- Mock route/carrier/passenger records and mock rules are intentionally fictional.
- Agent trace and public API output avoid raw passport values and credentials.

## Questions to resolve when process-owner references arrive

- Exact page order and required fields for the real PDP / review page.
- Which steps are optional and whether each ancillary selection triggers a booking service call.
- Authoritative provider endpoint/schema per Navitaire product and version, plus confirmation that the New Skies sandbox host, token issuer and `user_key` belong to the same environment. SkyPlus UAT upgrade endpoints are treated as a separate integration.
- Successful Timatic `documentRequest` response schema, supported document types, authoritative response codes, handling of transit points and any manual document checks. The screenshots show request bodies but not the successful response, so only the source reference `evaluationResult` enum is trusted; unknown results remain `REVIEW_REQUIRED`.
- Exact issuer/nationality/residency/visa evidence inputs and country rules in scope.
- Real eligibility rule ownership and rule precedence.
- When the system requires airport intervention vs allowing another document upload.
- Data retention, checkpoint encryption, region/residency, permitted model providers and approved source corpus.
- Boarding pass format and what provider result is authoritative.
