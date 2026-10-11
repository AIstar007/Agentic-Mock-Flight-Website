# LangGraph Agentic RAG for the flight assistant

## Workflow (dynamic, not a fixed FAQ map)

`classify/scope -> plan and decompose queries -> multi-source retrieve -> relevance grade/rerank -> (rewrite/retrieve bounded retries if evidence is weak) -> authority/freshness/scope gate -> synthesize from selected evidence -> claim-support evaluation -> corrective source extract/retry or abstain -> citations`

The graph is implemented in `backend/app/agentic_rag.py`; `backend/app/rag.py` preserves the website's import contract, so the existing chat routes and FAQ agent use the graph. It plans from the query itself, dynamically uses corpus terms/synonyms, and optionally asks an approved OpenAI-compatible model for search-query suggestions. Query planning never accepts the model as the source of facts. Compound user requests are decomposed into subqueries, and the grader allows sources to cover individual subquestions when their anchor terms match.

## Retrieval sources

1. **Active versioned SQLite FAQ corpus** — default, local and free; respects FAQ version activation and approval/scope controls.
2. **Optional reference retriever** — set `RAG_RETRIEVER_URL` / `RAG_RETRIEVER_API_KEY` to the private retrieval-only endpoint connected to the supplied Chroma/SQLite RAG reference. The service must be private; an unavailable configured source causes abstention rather than a silent local fallback.
3. **Optional general official-site search** — set `RAG_WEB_SEARCH_URL` to a private SearXNG-compatible `/search` endpoint with JSON format enabled. It searches on dynamically planned query variants. Returned URLs are checked against the configured/built-in HTTPS host allowlist. The optional page fetcher follows only redirects within that allowlist, refuses unexpected hosts/ports/IP literals, caps response size, and extracts readable page content plus available source date metadata.

The live search option is intentionally **opt-in**. A random public search proxy may log questions; use a trusted/private endpoint and never include names, booking refs, passport numbers or other passenger details in queries. Built-in allowlist includes Emirates, IATA/ICAO, selected regulators/government sources, and common airline domains. Use `RAG_WEB_ALLOWED_HOSTS` to narrow/extend approved domains with your governance approval. When the question names a carrier, sources from other carriers are rejected; if a generic question uses another airline source, the response states that carrier-specific rules differ.

## Configuration

```dotenv
RAG_MAX_RETRIES=2
RAG_TOP_K=5
RAG_RETRIEVER_URL=
RAG_RETRIEVER_API_KEY=
RAG_WEB_SEARCH_URL=
RAG_WEB_ALLOWED_HOSTS=
RAG_WEB_FETCH_PAGES=2
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=YOUR-DEPLOYMENT-NAME
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_API_VERSION=v1
AZURE_OPENAI_REASONING_MODEL=auto
# Optional compatibility with older OpenAI-compatible proxies only:
RAG_LLM_BASE_URL=
RAG_LLM_API_KEY=
RAG_LLM_MODEL=
```

Azure OpenAI is the preferred model provider for dynamic query planning and source-grounded answer synthesis. Set the resource endpoint, exact deployment name, API key and API version in `backend/.env` for local development or the host's environment/secret settings. The agent loads the local `.env` automatically without overriding environment variables already set by the host. The Azure API key is sent only in the HTTPS `api-key` header; credentials are never placed in prompts or logs. Use a secret store/environment variables rather than committing `.env`. If Azure settings are incomplete or an Azure call fails, the graph falls back to dynamic deterministic query planning and source-extractive synthesis; the generic `RAG_LLM_*` endpoint remains a compatibility fallback only. No model credentials are required for the local default. For broad/unseen FAQs, configure the approved general search endpoint; without it, the assistant must abstain when no approved source exists.

Azure OpenAI reference: https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/chat (v1 API endpoint, deployment-name routing and `api-key` authentication). Deployment names are aliases configured in Azure AI Foundry, not necessarily the underlying model IDs: https://learn.microsoft.com/en-us/azure/ai-studio/ai-services/concepts/endpoints.

SearXNG setup docs: https://docs.searxng.org/admin/installation-docker.html
SearXNG settings template (set `search.formats` to include `json`): https://github.com/searxng/searxng/blob/master/utils/templates/etc/searxng/settings.yml

## Evidence grading, freshness and correction

- The grader considers original-query concepts, subquery anchors, source title/category, relevance score and scope. Incidental mentions in a general article must not qualify as answers to specific policies.
- Rewrites are bounded; if relevant evidence cannot be found, the graph returns `faq_abstain` with an evidence-gap trace and no unsupported citations.
- Visa, immigration, entry/transit, health and comparable high-risk queries require current HTTPS authoritative sources and topic-specific coverage. A general IATA overview or generic airline page is not a passenger-specific Timatic result.
- Optional model output is evaluated sentence-by-sentence against retrieved passages; ungrounded generations are discarded and a source-extractive correction is attempted. The support check is a conservative lexical heuristic, not proof of semantic entailment.
- Citation metadata includes source host, source scope, `verified_at` when available, retrieval date for live web results and corpus version.

## Safety boundaries

The chat API only answers FAQs; no graph node books, cancels, selects seats, issues boarding passes, accepts payments or overrides deterministic check-in/document gates. Passenger eligibility remains owned by the workflow/provider. The RAG source set is not live Timatic and cannot certify a passenger's right to travel.
