# Agentic RAG troubleshooting

## What the previous issue looked like

The old lexical ranker could return a check-in article for a baggage query or a privacy document for a refund query because it used shared generic words. That behavior is now rejected by query-aware evidence grading and answer support evaluation. The chatbot runs the LangGraph agent in `backend/app/agentic_rag.py` through the compatibility import in `backend/app/rag.py`.

## Agentic workflow

The graph dynamically plans queries, decomposes compound questions, retrieves active FAQ records and configured optional sources, grades relevance against the original question and subquery anchors, retries weak searches up to the configured bounded budget, checks authority/freshness for high-risk rules, grounds synthesis in evidence, evaluates claim support, and uses an extractive correction or abstains. It is not a fixed question/answer table.

## Broad coverage for uncommon questions

The default local SQLite corpus is free and contains common Emirates flight FAQ source summaries, including checked baggage, refunds, disruptions, pets/animals, onboard Wi-Fi/services, special assistance, devices, and dangerous goods/power banks. It cannot possibly contain every route-specific or newly changed aviation rule. To search beyond it, set `RAG_WEB_SEARCH_URL` to a private/approved SearXNG-compatible `/search` endpoint with JSON response format enabled. `RAG_WEB_ALLOWED_HOSTS` controls accepted HTTPS source hosts; redirects to other hosts are rejected. `RAG_WEB_FETCH_PAGES` limits page fetches across planned subqueries in a retrieval batch. Do not use a random public search proxy for any passenger information or confidential query.

SearXNG docs: https://docs.searxng.org/admin/installation-docker.html
SearXNG settings template: https://github.com/searxng/searxng/blob/master/utils/templates/etc/searxng/settings.yml

## Activate updated source files in an existing checkout

The corpus is immutable and versioned. Editing `backend/data/knowledge.json` alone does not change an existing database. From PowerShell in `backend/`, run:

```powershell
$env:PYTHONPATH = "."
python -m app.faq_cli --label agentic-rag-general-flight-faq-current --change-note "Expand flight FAQ sources and enable LangGraph Agentic RAG" --activate
```

Then check `GET /api/v1/health` for `faq.corpus_version`, `faq.retrieval_mode`, and `faq.synthesis_mode`. The RAG refreshes the active SQLite version before answering; restarting the backend is not required.

## How to interpret abstentions

- An `evidence_gap` stage means none of the candidates passed relevance grading for the actual query.
- `authority_freshness_gate` with `review_required` means a high-risk question lacked current, authoritative, specific evidence. General help text or an IATA overview is not a passenger-specific Timatic decision.
- `configured_backend_unavailable` means a configured semantic/web source failed; the graph abstains instead of silently switching to a different corpus.
- `answer_evaluation` with `failed` indicates unsupported generation; the graph tries an extractive correction, otherwise abstains.

The support verifier is deterministic and conservative, not a formal semantic-entailment proof. Even when an answer is returned, it is informational and never changes check-in eligibility, reservations, seats, payments or boarding-pass state.
