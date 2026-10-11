import json

import pytest

from app.faq_store import FAQCorpusError, FAQCorpusStore
from app.rag import SelfCorrectiveRAG


def test_seeded_faq_corpus_has_active_version_and_only_approved_sources(tmp_path):
    store = FAQCorpusStore(db_path=tmp_path / "faq.sqlite3")
    active = store.get_active_version()
    docs = store.get_active_documents()
    assert active is not None
    assert active["status"] == "ACTIVE"
    assert docs
    assert all(doc["brand_scope"] in {"emirates_demo", "global_aviation", "project_policy"} for doc in docs)
    assert all(doc["source_type"] and doc["source_url"] for doc in docs)
    assert all(doc["brand_scope"] != "legacy_airline_reference_only" for doc in docs)


def test_third_party_airline_source_cannot_be_mislabeled_as_emirates_faq():
    with pytest.raises(FAQCorpusError, match="Only an approved Emirates-hosted source"):
        FAQCorpusStore.validate_document({
            "id": "bad-policy", "title": "Third-party airline policy", "category": "faq",
            "jurisdiction": "IN", "source_type": "official_public_webpage",
            "source_url": "https://foreign-carrier.example/help.html", "verified_at": "2026-10-11",
            "content": "A policy excerpt", "brand_scope": "emirates_demo", "approved_for_use": True,
        })


def test_unapproved_faq_version_cannot_be_activated(tmp_path):
    store = FAQCorpusStore(db_path=tmp_path / "faq.sqlite3")
    original = store.get_active_documents()[0]
    draft_doc = {key: original[key] for key in ("id", "title", "category", "jurisdiction", "source_type", "source_url", "verified_at", "content", "brand_scope")}
    draft_doc["id"] = "unapproved-draft"
    draft_doc["approved_for_use"] = False
    version = store.create_version(version_label="unapproved-test", documents=[draft_doc], activate=False)
    with pytest.raises(FAQCorpusError, match="not marked approved_for_use"):
        store.activate_version(version["version_id"])


def test_rag_citations_expose_active_corpus_version():
    rag = SelfCorrectiveRAG()
    result = rag.answer("What is the online check-in window?")
    if result["intent"] == "faq":
        assert result["citations"][0]["corpus_version"]


def test_self_corrective_rag_exposes_bounded_retrieval_and_evaluation_stages():
    rag = SelfCorrectiveRAG()
    result = rag.answer("What is the Emirates online check-in window?")
    assert result["intent"] == "faq"
    stages = [item["stage"] for item in result["retrieval_trace"]["stages"]]
    assert "retrieve" in stages
    assert "relevance_grade" in stages
    assert "answer_generation" in stages
    assert "answer_evaluation" in stages
    assert "final" in stages
    assert result["retrieval_trace"]["corpus_version"]


def test_rag_blocks_passport_value_before_search_but_allows_format_question():
    rag = SelfCorrectiveRAG()
    blocked = rag.answer("My passport number is AB1234567")
    assert blocked["intent"] == "sensitive_input_blocked"
    assert blocked["citations"] == []
    blocked_stages = [item["stage"] for item in blocked["retrieval_trace"]["stages"]]
    assert "privacy_guard" in blocked_stages
    assert "retrieve" not in blocked_stages
    general = rag.answer("What is passport number format?")
    assert general["intent"] != "sensitive_input_blocked"


def test_rag_does_not_use_overview_source_as_personal_visa_decision():
    rag = SelfCorrectiveRAG()
    result = rag.answer("Do I need a visa for Nepal?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert any(item["stage"] == "authority_freshness_gate" for item in result["retrieval_trace"]["stages"])


def test_rag_discards_unsupported_llm_answer_and_uses_source_fallback():
    rag = SelfCorrectiveRAG()
    rag._try_llm = lambda query, candidates: "Every passenger receives a free lifetime upgrade on every flight."
    result = rag.answer("How do I book a flight online?")
    assert result["intent"] == "faq"
    assert "free lifetime upgrade" not in result["answer"].lower()
    stages = result["retrieval_trace"]["stages"]
    assert any(item.get("status") == "discarded_ungrounded_generation" for item in stages)
    assert any(item.get("status") == "extractive_fallback" for item in stages)


def test_rag_host_allowlist_rejects_lookalikes_and_plain_http():
    from app.rag_agentic import _trusted_https
    assert not _trusted_https("https://emirates.com.fake.example.org/policy")
    assert not _trusted_https("http://iata.org/travel")
    assert _trusted_https("https://www.emirates.com/english/help/")


def test_rag_optional_reference_chroma_sqlite_bridge_uses_retrieved_active_chunks(monkeypatch):
    import json
    from datetime import date
    from app.rag import SelfCorrectiveRAG

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, _limit=-1):
            return json.dumps({"contexts": [{
                "doc_id": "demo-fares", "version_number": 4, "chunk_order": 2,
                "rerank_score": 0.92, "distance": 0.08,
                "text": "Select an itinerary and fare before entering passenger and contact details.",
                "status": "active", "verified": False,
            }]}).encode("utf-8")

    monkeypatch.setenv("RAG_RETRIEVER_URL", "http://127.0.0.1:8123/v1/retrieve-context")
    monkeypatch.setenv("RAG_RETRIEVER_API_KEY", "test-bridge-key")
    monkeypatch.delenv("RAG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    def fake_urlopen(request, timeout=8):
        assert request.full_url.endswith("/v1/retrieve-context")
        assert request.get_header("Authorization") == "Bearer test-bridge-key"
        return FakeResponse()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = SelfCorrectiveRAG().answer("How do I select a fare?")
    assert result["intent"] == "faq"
    assert result["citations"]
    assert result["citations"][0]["corpus_version"] == "demo-fares-v4"
    assert "chunk 2" in result["citations"][0]["title"]


def test_rag_configured_remote_http_bridge_fails_closed(monkeypatch):
    from app.rag import SelfCorrectiveRAG
    monkeypatch.setenv("RAG_RETRIEVER_URL", "http://example.com/v1/retrieve-context")
    result = SelfCorrectiveRAG().answer("How do I select a fare?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert any(item.get("status") == "configured_backend_unavailable" for item in result["retrieval_trace"]["stages"])


def test_rag_blocks_cvv_value_before_retrieval():
    rag = SelfCorrectiveRAG()
    result = rag.answer("My CVV is 123")
    assert result["intent"] == "sensitive_input_blocked"
    assert result["citations"] == []


def test_rag_baggage_question_never_returns_checkin_or_privacy_source():
    rag = SelfCorrectiveRAG()
    # Simulate the stale corpus visible in the user's current output: no baggage FAQ yet.
    rag.documents = [doc for doc in rag.documents if doc.get("category") not in {"baggage", "dangerous_goods"} and doc.get("id") not in {"EMIRATES_BAGGAGE_POLICY", "EMIRATES_BAGGAGE_LOST_PROPERTY", "EMIRATES_POWER_BANKS_BATTERIES", "POC_FAQ_TOPICS_AND_ESCALATION"}]
    rag._refresh_active_corpus = lambda: True
    result = rag.answer("What is the baggage policy of Emirates?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert any(x.get("stage") == "evidence_gap" for x in result["retrieval_trace"]["stages"])
    assert all("check-in" not in c["title"].lower() and "privacy" not in c["title"].lower() for c in result["citations"])


def test_rag_refund_question_never_returns_privacy_source():
    rag = SelfCorrectiveRAG()
    rag.documents = [doc for doc in rag.documents if doc.get("category") != "refunds" and doc.get("id") not in {"EMIRATES_REFUND_POLICY", "POC_FAQ_TOPICS_AND_ESCALATION"}]
    rag._refresh_active_corpus = lambda: True
    result = rag.answer("What is the refund policy?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert all("privacy" not in c["title"].lower() for c in result["citations"])


def test_rag_selects_dedicated_baggage_and_refund_articles():
    rag = SelfCorrectiveRAG()
    # Use the new source file directly so this test is independent of an older active DB version.
    import json
    from pathlib import Path
    raw = json.loads((Path(__file__).resolve().parents[1] / "data" / "knowledge.json").read_text(encoding="utf-8"))["documents"]
    rag.documents = raw
    rag._refresh_active_corpus = lambda: True
    baggage = rag.answer("What is the baggage policy of Emirates?")
    assert baggage["intent"] == "faq"
    assert any("baggage" in c["title"].lower() for c in baggage["citations"])
    refund = rag.answer("What is the refund policy?")
    assert refund["intent"] == "faq"
    assert any("refund" in c["title"].lower() for c in refund["citations"])


def test_rag_refreshes_active_corpus_version_without_process_restart(tmp_path):
    import json
    from pathlib import Path
    from app.faq_store import FAQCorpusStore
    from app.rag import SelfCorrectiveRAG

    knowledge_path = Path(__file__).resolve().parents[1] / "data" / "knowledge.json"
    payload = json.loads(knowledge_path.read_text(encoding="utf-8"))
    all_docs = payload["documents"]
    old_payload = {"documents": [d for d in all_docs if d["category"] not in {"baggage", "refunds"} and d["id"] not in {"EMIRATES_POWER_BANKS_BATTERIES"}]}
    old_seed = tmp_path / "old-knowledge.json"
    old_seed.write_text(json.dumps(old_payload), encoding="utf-8")
    store = FAQCorpusStore(db_path=tmp_path / "faq.sqlite3", seed_path=old_seed)
    rag = SelfCorrectiveRAG(corpus_store=store)

    before = rag.answer("What is the baggage policy of Emirates?")
    assert before["intent"] == "faq_abstain"
    assert before["citations"] == []

    mapped = []
    for doc in all_docs:
        url = str(doc.get("source_url", ""))
        source_type = str(doc.get("source_type", "project_policy"))
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        is_emirates = host == "emirates.com" or host.endswith(".emirates.com")
        scope = "project_policy" if source_type == "project_policy" else ("emirates_demo" if is_emirates else "global_aviation")
        mapped.append({"id": doc["id"], "title": doc["title"], "category": doc["category"],
                       "jurisdiction": doc.get("jurisdiction", "unknown"), "source_type": source_type,
                       "source_url": url, "verified_at": doc.get("verified_at", ""), "content": doc["content"],
                       "brand_scope": scope, "approved_for_use": True})
    draft = store.create_version(version_label="baggage-refund-test", documents=mapped, activate=False)
    store.activate_version(draft["version_id"])

    after = rag.answer("What is the baggage policy of Emirates?")
    assert after["intent"] == "faq"
    assert any("baggage" in citation["title"].lower() for citation in after["citations"])
    assert after["retrieval_trace"]["corpus_version"] == "baggage-refund-test"


def test_agentic_rag_answers_unseen_flight_subtopic_from_configured_official_search(monkeypatch):
    import json
    from datetime import date
    from app.rag import SelfCorrectiveRAG

    class FakeResponse:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, _limit=-1):
            return json.dumps({"results": [{
                "title": "Emirates inflight entertainment Bluetooth audio pairing",
                "url": "https://www.emirates.com/english/experience/inflight-entertainment/",
                "content": "Emirates provides an inflight entertainment experience on many aircraft. Bluetooth audio pairing availability can depend on the aircraft and cabin equipment; travellers should check the current onboard instructions for their flight. This search result is a demonstration fixture rather than a verified live policy statement.",
                "publishedDate": date.today().isoformat(),
                "score": 0.94,
            }]}).encode("utf-8")

    monkeypatch.setenv("RAG_WEB_SEARCH_URL", "https://searx.example/search")
    monkeypatch.setenv("RAG_WEB_FETCH_PAGES", "0")
    monkeypatch.delenv("RAG_RETRIEVER_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    def fake_urlopen(request, timeout=5):
        assert request.full_url.startswith("https://searx.example/search?")
        return FakeResponse()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SelfCorrectiveRAG().answer("Does Emirates inflight entertainment support Bluetooth audio pairing?")
    assert result["intent"] == "faq"
    assert result["citations"]
    assert "emirates.com" in result["citations"][0]["url"]
    stages = result["retrieval_trace"]["stages"]
    assert any(item["stage"] == "query_planning" for item in stages)
    assert any(item["stage"] == "multi_source_retrieval" and item.get("web_search_enabled") for item in stages)
    assert result["retrieval_trace"]["graph"] == "langgraph_agentic_rag"


def test_agentic_rag_reports_gap_instead_of_answering_with_incidental_mention():
    rag = SelfCorrectiveRAG()
    rag.documents = [doc for doc in rag.documents if doc.get("category") not in {"baggage", "dangerous_goods"} and doc.get("id") not in {"EMIRATES_BAGGAGE_POLICY", "EMIRATES_BAGGAGE_LOST_PROPERTY", "EMIRATES_POWER_BANKS_BATTERIES", "POC_FAQ_TOPICS_AND_ESCALATION"}]
    rag._refresh_active_corpus = lambda: True
    result = rag.answer("What is the baggage policy of Emirates?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert any(item["stage"] == "evidence_gap" for item in result["retrieval_trace"]["stages"])


def test_agentic_rag_covers_multiple_general_flight_topics_from_active_sources():
    rag = SelfCorrectiveRAG()
    import json
    from pathlib import Path
    from datetime import date
    rag.documents = json.loads((Path(__file__).resolve().parents[1] / "data" / "knowledge.json").read_text(encoding="utf-8"))["documents"]
    for doc in rag.documents:
        doc["verified_at"] = date.today().isoformat()
    rag._refresh_active_corpus = lambda: True
    cases = [
        ("Do Emirates flights have Wi-Fi?", "onboard services"),
        ("What is the policy for pets on Emirates?", "pets and animals"),
        ("Can I carry a power bank on my flight?", "dangerous goods"),
        ("Can I take an electric scooter on a plane?", "dangerous goods"),
    ]
    for query, expected_title_fragment in cases:
        result = rag.answer(query)
        assert result["intent"] == "faq", (query, result["answer"])
        assert any(expected_title_fragment in citation["title"].lower() for citation in result["citations"]), query
        assert all(citation["url"].startswith("https://") for citation in result["citations"])
        assert result["retrieval_trace"]["graph"] == "langgraph_agentic_rag"


def test_agentic_rag_decomposes_compound_query_and_synthesizes_multiple_sources():
    rag = SelfCorrectiveRAG()
    import json
    from pathlib import Path
    rag.documents = json.loads((Path(__file__).resolve().parents[1] / "data" / "knowledge.json").read_text(encoding="utf-8"))["documents"]
    rag._refresh_active_corpus = lambda: True
    result = rag.answer("What are the baggage allowance and refund conditions?")
    assert result["intent"] == "faq"
    titles = " ".join(citation["title"].lower() for citation in result["citations"])
    assert "baggage" in titles and "refund" in titles
    assert any(item["stage"] == "query_planning" and item.get("compound_query_decomposed") for item in result["retrieval_trace"]["stages"])
    assert any(item["stage"] == "evidence_selection" and item.get("multi_source") for item in result["retrieval_trace"]["stages"])


def test_named_emirates_query_does_not_cite_other_airline_rules(monkeypatch):
    import json
    from pathlib import Path
    from app.rag import SelfCorrectiveRAG
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit=-1):
            return json.dumps({"results": [{
                "title": "Qatar Airways cabin baggage policy",
                "url": "https://www.qatarairways.com/en/baggage.html",
                "content": "Qatar Airways baggage policies vary by route, ticket and cabin. Passengers should check their Qatar Airways ticket for the exact allowance.",
                "publishedDate": "2026-10-11", "score": 0.99,
            }]}).encode("utf-8")
    monkeypatch.setenv("RAG_WEB_SEARCH_URL", "https://searx.example/search")
    monkeypatch.setenv("RAG_WEB_FETCH_PAGES", "0")
    monkeypatch.delenv("RAG_RETRIEVER_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=5: FakeResponse())
    result = SelfCorrectiveRAG().answer("What is the baggage policy of Emirates?")
    assert result["intent"] == "faq"
    assert result["citations"]
    assert all("qatarairways.com" not in citation["url"] for citation in result["citations"])


def test_configuring_web_search_does_not_expand_chat_scope_to_unrelated_questions(monkeypatch):
    from app.rag import SelfCorrectiveRAG
    monkeypatch.setenv("RAG_WEB_SEARCH_URL", "https://searx.example/search")
    monkeypatch.setenv("RAG_WEB_FETCH_PAGES", "0")
    result = SelfCorrectiveRAG().answer("What is the weather in my hometown?")
    assert result["intent"] == "out_of_scope"
    assert result["citations"] == []
    assert not any(item["stage"] == "multi_source_retrieval" for item in result["retrieval_trace"]["stages"])


def test_agentic_rag_blocks_booking_references_and_email_before_external_search(monkeypatch):
    from app.rag import SelfCorrectiveRAG
    monkeypatch.setenv("RAG_WEB_SEARCH_URL", "https://searx.example/search")
    result = SelfCorrectiveRAG().answer("Please look up PNR: QWERTY7")
    assert result["intent"] == "sensitive_input_blocked"
    assert result["citations"] == []
    assert "privacy_guard" in [item["stage"] for item in result["retrieval_trace"]["stages"]]
    email_result = SelfCorrectiveRAG().answer("My email is passenger@example.com; what is the check-in window?")
    assert email_result["intent"] == "sensitive_input_blocked"
    assert email_result["citations"] == []


def test_agentic_rag_requires_page_verification_date_for_high_risk_live_search(monkeypatch):
    import json
    from app.rag import SelfCorrectiveRAG

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit=-1):
            return json.dumps({"results": [{
                "title": "Official-looking current visa entry rule",
                "url": "https://www.emirates.com/english/help/faq-topics/preparing-to-travel/",
                "content": "Travellers need the right passport and visa documents for the destination, based on nationality and the precise route. This result is only an overview and is not a passenger-specific clearance.",
                "publishedDate": __import__("datetime").date.today().isoformat(),
                "score": 0.99,
            }]}).encode("utf-8")
    monkeypatch.setenv("RAG_WEB_SEARCH_URL", "https://searx.example/search")
    monkeypatch.setenv("RAG_WEB_FETCH_PAGES", "0")
    monkeypatch.delenv("RAG_RETRIEVER_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=5: FakeResponse())
    result = SelfCorrectiveRAG().answer("Do I need a visa to enter Nepal?")
    assert result["intent"] == "faq_abstain"
    assert result["citations"] == []
    assert any(item["stage"] == "authority_freshness_gate" for item in result["retrieval_trace"]["stages"])


def test_azure_openai_v1_is_used_for_agentic_planner_and_grounded_synthesis(monkeypatch):
    import json
    from app.rag import SelfCorrectiveRAG

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test-resource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "flight-chat-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test-secret")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "v1")
    monkeypatch.setenv("AZURE_OPENAI_REASONING_MODEL", "false")
    # Azure must take precedence over any legacy OpenAI-compatible variables.
    monkeypatch.setenv("RAG_LLM_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("RAG_LLM_API_KEY", "legacy-test-secret")
    monkeypatch.setenv("RAG_LLM_MODEL", "legacy-model")
    monkeypatch.delenv("RAG_RETRIEVER_URL", raising=False)
    monkeypatch.delenv("RAG_WEB_SEARCH_URL", raising=False)

    calls = []
    class FakeResponse:
        def __init__(self, content): self.content = content
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit=-1):
            return json.dumps({"choices": [{"message": {"content": self.content}}]}).encode()

    def fake_urlopen(request, timeout=8):
        headers = {key.lower(): value for key, value in request.header_items()}
        body = json.loads(request.data.decode())
        calls.append((request.full_url, headers, body, timeout))
        assert request.full_url == "https://test-resource.openai.azure.com/openai/v1/chat/completions"
        assert headers.get("api-key") == "azure-test-secret"
        assert "authorization" not in headers
        assert body["model"] == "flight-chat-deployment"
        system = body["messages"][0]["content"].lower()
        if "retrieval planner" in system:
            return FakeResponse('{"queries":["Emirates battery rules", "airline lithium battery restrictions"]}')
        return FakeResponse("The policy says to check the official airline rules for this itinerary.")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    rag = SelfCorrectiveRAG()
    assert rag.synthesis_mode == "azure_openai_checked"
    assert rag._try_llm_plan("Can I take a power bank on a flight?") == ["Emirates battery rules", "airline lithium battery restrictions"]
    source = {"title": "Power bank policy", "source_url": "https://www.emirates.com/example", "corpus_version": "test-v1", "content": "Check the official airline rules for this itinerary."}
    answer = rag._try_llm("What is the power bank policy?", [(0.9, source)])
    assert answer == "The policy says to check the official airline rules for this itinerary."
    assert len(calls) == 2
    assert all("azure-test-secret" not in json.dumps(call[2]) for call in calls)
    assert any("Power bank policy" in item[2]["messages"][0]["content"] for item in calls)


def test_azure_openai_legacy_versioned_endpoint_uses_deployment_path(monkeypatch):
    import json
    from app.rag import SelfCorrectiveRAG
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test-resource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "chat-prod")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test-secret")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    monkeypatch.setenv("AZURE_OPENAI_REASONING_MODEL", "false")

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit=-1):
            return json.dumps({"choices": [{"message": {"content": "grounded response"}}]}).encode()

    def fake_urlopen(request, timeout=8):
        assert request.full_url == "https://test-resource.openai.azure.com/openai/deployments/chat-prod/chat/completions?api-version=2024-10-21"
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers.get("api-key") == "azure-test-secret"
        body = json.loads(request.data.decode())
        assert "model" not in body
        assert body["max_tokens"] == 1200
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = SelfCorrectiveRAG()._chat_completion([{"role": "user", "content": "hello"}])
    assert result == "grounded response"


def test_azure_openai_reasoning_deployments_skip_temperature_override(monkeypatch):
    import json
    from app.rag import SelfCorrectiveRAG
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test-resource.openai.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-reasoning-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test-secret")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "v1")
    monkeypatch.setenv("AZURE_OPENAI_REASONING_MODEL", "true")

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit=-1):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def fake_urlopen(request, timeout=8):
        assert request.full_url == "https://test-resource.openai.azure.com/openai/v1/chat/completions"
        body = json.loads(request.data.decode())
        assert body["model"] == "my-reasoning-deployment"
        assert body["max_completion_tokens"] == 1200
        assert "temperature" not in body
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert SelfCorrectiveRAG()._chat_completion([{"role": "user", "content": "hello"}]) == "ok"


def test_rag_retrieves_internal_form_guidance_without_weakening_real_visa_gate():
    rag = SelfCorrectiveRAG()
    form = rag.answer("How do I enter passport information in this demo form?")
    assert form["intent"] == "faq"
    assert any("passport" in c["title"].lower() or "document" in c["title"].lower() or "assistant" in c["title"].lower() for c in form["citations"])
    blocked = rag.answer("Can I enter Dubai without a visa?")
    assert blocked["intent"] == "faq_abstain"
    assert blocked["citations"] == []


def test_rag_finds_internal_workflow_guidance_for_blocked_checkin():
    rag = SelfCorrectiveRAG()
    result = rag.answer("What should I do if check-in is blocked?")
    assert result["intent"] == "faq"
    assert result["citations"]
    assert any("check-in" in c["title"].lower() or "error" in c["title"].lower() for c in result["citations"])
