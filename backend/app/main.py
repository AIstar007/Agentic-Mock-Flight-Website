from __future__ import annotations

import asyncio
import base64
import binascii
import json
import hashlib
import hmac
import logging
import re
import time
from functools import lru_cache
import uuid
from datetime import date, timedelta
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
import httpx

from app.adapters.navitaire import BookingNotFound, MockNavitaireAdapter, SeatUnavailable
from app.airport_search import AirportSearchIndex
from app.commerce import (
    BookingConfirmationRequest, FlightSearchRequest, MockFlightCommerce, PaymentDeclined,
    PDPContextRequest, SiteAssistantRequest, resolve_pdp_next_step,
)
from app.rag_agentic import SelfCorrectiveRAG
from app.assistant_schema import FIELD_QUESTIONS, FieldParseError, age_on_date, booking_pdp_field_order, extract_labeled_field_values, next_booking_field, parse_field_value
from app.adapters.timatic import MockTimaticAdapter
from app.adapters.navitaire_live import NavitaireLiveDiagnosticsAdapter
from app.adapters.timatic_live import (
    DEFAULT_TIMATIC_API_BASE, DEFAULT_TIMATIC_TOKEN_URL, TimaticStagingDiagnosticsAdapter,
)
from app.adapters.provider_http import ProviderAPIError
from app.agents import public_session
from app.reference_workflow import (
    ReferenceStyleDocumentValidationWorkflow, build_timatic_request,
    evaluate_navitaire_ssr_requirements, normalize_navitaire_booking,
)
from app.audit import AuditSink
from app.config import settings
from app.models import SeatSkipRequest, AncillarySelection, BookingLookupRequest, ChatRequest, SaveDocumentsRequest, SeatSelectionRequest
from app.provider_models import NavitaireBookingRetrieveTest, TimaticDocumentRequest
from app.orchestrator import Orchestrator
from app.rules import rule_catalog, run_eligibility_concurrently
from app.rule_graph import RuleGraph
from app.state_store import SessionExpired, SessionNotFound, SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("emirates_checkin_poc")

store = SessionStore(settings.redis_url, settings.session_ttl_seconds, settings.mongodb_uri, settings.mongodb_database)
audit = AuditSink(settings.mongodb_uri, settings.mongodb_database, settings.audit_ttl_seconds)
navitaire = MockNavitaireAdapter()
timatic = MockTimaticAdapter()
orchestrator = Orchestrator(store, navitaire, timatic, audit)
airport_index = AirportSearchIndex()
commerce = MockFlightCommerce(airport_index, navitaire)
site_rag = SelfCorrectiveRAG()
rule_graph = RuleGraph(settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password)
neo4j_status = "disabled_local_catalog"

@asynccontextmanager
async def lifespan(_: FastAPI):
    global neo4j_status
    if not settings.mock_mode:
        raise RuntimeError("This distribution only implements MOCK_MODE=true; live provider mode is intentionally disabled")
    if settings.require_persistent_storage and not settings.mongodb_uri:
        raise RuntimeError("Hosted mode requires MONGODB_URI. MongoDB Atlas can provide sessions, audit events, and LangGraph checkpoints for the free-hosted profile.")
    await store.initialize()
    await audit.initialize()
    await orchestrator.initialize(
        settings.graph_redis_url,
        mongodb_uri=settings.mongodb_uri,
        mongodb_database=settings.mongodb_database,
        checkpoint_ttl_seconds=settings.session_ttl_seconds,
    )
    try:
        if settings.neo4j_uri:
            await asyncio.to_thread(rule_graph.initialize)
            neo4j_status = "connected"
        else:
            neo4j_status = "disabled_local_catalog"
    except Exception as exc:
        rule_graph.close()
        neo4j_status = "unavailable_local_catalog_fallback"
        logger.warning("Optional Neo4j rule graph unavailable; using local rule catalog (error_type=%s)", type(exc).__name__)
        if settings.neo4j_required:
            raise RuntimeError("NEO4J_REQUIRED=true but the Neo4j graph could not be initialized") from exc
    logger.info("Application startup complete; mode=MOCK_ONLY graph_mode=%s neo4j_status=%s", orchestrator.graph_mode, neo4j_status)
    try:
        yield
    finally:
        rule_graph.close()
        if _navitaire_diagnostics_adapter.cache_info().currsize:
            try:
                await _navitaire_diagnostics_adapter().aclose()
            finally:
                _navitaire_diagnostics_adapter.cache_clear()
        await orchestrator.close()
        await audit.close()
        await store.close()

app = FastAPI(
    title="Emirates-Inspired Flight Booking and Check-in POC",
    version="0.5.0",
    description="Synthetic flight booking and check-in POC, not an official Emirates service. The passenger workflow remains mock-only; optional protected local endpoints can probe authorized Navitaire/Timatic staging APIs.",
    lifespan=lifespan,
    docs_url=None if settings.app_env.lower() == "production" else "/docs",
    redoc_url=None if settings.app_env.lower() == "production" else "/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Token", "X-Correlation-ID", "X-Provider-Diagnostics-Token"],
)

_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = asyncio.Lock()

@app.middleware("http")
async def security_headers_and_rate_limit(request: Request, call_next):
    correlation_id = request.headers.get("x-correlation-id", "")
    if not (8 <= len(correlation_id) <= 80) or any(ord(c) < 32 for c in correlation_id):
        correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    async with _rate_lock:
        bucket = _rate_buckets[client_ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= settings.rate_limit_per_minute:
            return Response("Rate limit exceeded", status_code=429, headers={"Retry-After": "60", "X-Correlation-ID": correlation_id})
        bucket.append(now)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

async def authorized_state(sid: str, x_session_token: str = Header(default="", alias="X-Session-Token")) -> dict[str, Any]:
    try:
        state = await store.get(sid)
    except (SessionNotFound, SessionExpired):
        raise HTTPException(status_code=404, detail="Session not found or expired")
    actual = hashlib.sha256(x_session_token.encode()).hexdigest() if x_session_token else ""
    if not x_session_token or not __import__("hmac").compare_digest(actual, state.get("session_token_hash", "")):
        raise HTTPException(status_code=401, detail="Session token is invalid")
    return state


def correlation(request: Request) -> str:
    return request.state.correlation_id


@app.post("/api/v1/voice/transcribe")
async def sarvam_voice_transcribe(request: Request) -> dict[str, Any]:
    """Proxy short user-consented microphone clips to Sarvam Saaras; keep API key server-side."""
    if not settings.sarvam_api_key:
        raise HTTPException(status_code=503, detail="Sarvam voice is not configured. Set SARVAM_API_KEY in the backend environment.")
    try:
        payload = await request.json()
        audio_b64 = str(payload.get("audio_base64", ""))
        if "," in audio_b64 and audio_b64.startswith("data:"):
            audio_b64 = audio_b64.split(",", 1)[1]
        audio = base64.b64decode(audio_b64, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(status_code=422, detail="A valid base64 microphone recording is required.")
    if not audio or len(audio) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Voice clip must be non-empty and no larger than 8 MB.")
    content_type = str(payload.get("content_type", "audio/webm")).split(";", 1)[0]
    ext = {"audio/webm":"webm", "audio/ogg":"ogg", "audio/mp4":"m4a", "audio/mpeg":"mp3", "audio/wav":"wav"}.get(content_type, "webm")
    # Saaras v4 and v3 both support explicit output modes; keep the intent explicit.
    form = {"model": settings.sarvam_stt_model, "mode": "transcribe", "language_code": str(payload.get("language_code", "unknown"))}
    if settings.sarvam_stt_model == "saaras:v4":
        form["keyterms"] = json.dumps(["Emirates", "New Delhi", "Dubai", "Abu Dhabi", "passenger", "booking reference", "PNR", "passport", "nationality", "issuing country", "date of birth", "passport expiry", "outbound", "return", "seat selection", "Wi-Fi", "Timatic", "Sarvam"])
    try:
        async with httpx.AsyncClient(timeout=settings.sarvam_timeout_seconds) as client:
            response = await client.post(
                "https://api.sarvam.ai/speech-to-text",
                headers={"api-subscription-key": settings.sarvam_api_key},
                data=form, files={"file": (f"voice-input.{ext}", audio, content_type)},
            )
        if response.status_code >= 400:
            logger.warning("Sarvam STT request failed status=%s", response.status_code)
            raise HTTPException(status_code=502, detail=f"Sarvam speech recognition returned HTTP {response.status_code}. Please try again or type your answer.")
        result = response.json()
        transcript = str(result.get("transcript", "")).strip()
        if not transcript:
            raise HTTPException(status_code=422, detail="Sarvam did not detect speech. Please try again in a quieter place.")
        return {"transcript": transcript, "language_code": result.get("language_code"), "provider": "SARVAM_SAARAS", "request_id": result.get("request_id")}
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Sarvam voice transcription timed out. Try a shorter recording.") from exc
    except httpx.RequestError as exc:
        logger.warning("Sarvam STT network request failed category=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not reach Sarvam speech recognition. Please retry or type your answer.") from exc


@app.post("/api/v1/voice/speak")
async def sarvam_voice_speak(request: Request) -> dict[str, Any]:
    """Proxy text-to-speech to Sarvam Bulbul; no browser-native speech synthesis is used."""
    if not settings.sarvam_api_key:
        raise HTTPException(status_code=503, detail="Sarvam voice is not configured. Set SARVAM_API_KEY in the backend environment.")
    try:
        payload = await request.json()
        text = str(payload.get("text", "")).strip()
        language_code = str(payload.get("language_code", "en-IN"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Provide text to speak.")
    if not text or len(text) > 2500:
        raise HTTPException(status_code=422, detail="Text-to-speech text must contain 1 to 2500 characters.")
    body = {"text": text, "model": settings.sarvam_tts_model, "language_code": language_code, "output_audio_codec": "wav", "pace": max(0.5, min(float(settings.sarvam_tts_pace), 2.0))}
    try:
        async with httpx.AsyncClient(timeout=settings.sarvam_timeout_seconds) as client:
            response = await client.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": settings.sarvam_api_key, "Content-Type": "application/json"}, json=body)
        if response.status_code >= 400:
            logger.warning("Sarvam TTS request failed status=%s", response.status_code)
            raise HTTPException(status_code=502, detail=f"Sarvam speech synthesis returned HTTP {response.status_code}.")
        result = response.json()
        audios = result.get("audios") or []
        if not audios or not isinstance(audios[0], str):
            raise HTTPException(status_code=502, detail="Sarvam did not return playable speech audio.")
        return {"audio_base64": audios[0], "content_type": "audio/wav", "provider": "SARVAM_BULBUL", "request_id": result.get("request_id")}
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Sarvam speech synthesis timed out. Try a shorter answer.") from exc
    except httpx.RequestError as exc:
        logger.warning("Sarvam TTS network request failed category=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Could not reach Sarvam speech synthesis.") from exc

@app.get("/api/v1/countries")
async def list_iso_countries(q: str = "", limit: int = 300) -> dict[str, Any]:
    """Return the complete ISO country registry, or rank search matches by code/name."""
    from app.assistant_schema import normalize_country_code
    try:
        import pycountry
    except ImportError:
        raise HTTPException(status_code=503, detail="The ISO country registry is unavailable; install the backend pycountry dependency.")
    query = str(q or "").strip().casefold()
    limit = max(1, min(int(limit or 300), 300))
    records = []
    for country in pycountry.countries:
        name = getattr(country, "common_name", None) or country.name
        record = {"name": name, "official_name": getattr(country, "official_name", country.name), "alpha_2": country.alpha_2, "alpha_3": country.alpha_3}
        records.append(record)
    if query:
        exact, starts, contains = [], [], []
        for item in records:
            values = [item["name"], item["official_name"], item["alpha_2"], item["alpha_3"]]
            vals = [v.casefold() for v in values]
            if query in vals: exact.append(item)
            elif any(v.startswith(query) for v in vals): starts.append(item)
            elif any(query in v for v in vals): contains.append(item)
        # The API returns only actual ISO countries; broad query strings remain bounded.
        return {"countries": (exact + starts + contains)[:limit], "total": len(records), "query": q}
    records.sort(key=lambda c: (c["name"].casefold(), c["alpha_2"]))
    return {"countries": records[:limit], "total": len(records), "query": ""}


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    try:
        from app.chroma_store import index_status
        chroma = index_status()
    except Exception as exc:
        chroma = {"available": False, "reason": type(exc).__name__, "indexed_count": 0}
    return {
        "status": "ok",
        "mode": "MOCK_ONLY",
        "provider_diagnostics_enabled": bool(settings.enable_provider_diagnostics and settings.app_env.lower() != "production"),
        "graph_mode": orchestrator.graph_mode,
        "storage": {
            "redis_sessions_configured": bool(settings.redis_url),
            "mongodb_sessions_configured": bool(settings.mongodb_uri and not settings.redis_url),
            "mongodb_audit_configured": bool(settings.mongodb_uri),
            "langgraph_redis_checkpoint_configured": bool(settings.graph_redis_url),
            "langgraph_mongodb_checkpoint_configured": bool(settings.mongodb_uri and not settings.graph_redis_url),
            "neo4j_status": neo4j_status,
            "persistent_storage_required": settings.require_persistent_storage,
        },
        "providers": {"navitaire": "mock", "timatic": "mock"},
        "faq": {"mode": "self_corrective_rag", "agentic_mode": "self_corrective_agentic_rag", "retrieval_mode": site_rag.retrieval_mode, "synthesis_mode": site_rag.synthesis_mode, "max_retries": site_rag.max_retries, "top_k": site_rag.top_k, "corpus_version": site_rag.corpus_version, "approved_document_count": len(site_rag.documents), "chroma": chroma},
        "message": "No live airline or Timatic operation is used by the passenger workflow. Optional provider diagnostics are read-only, opt-in staging probes; no official immigration decision is made by this application.",
    }

@app.get("/api/v1/demo-bookings")
async def demo_bookings() -> dict[str, Any]:
    # These are synthetic records. This endpoint is intentionally only for the local demo.
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data" / "bookings.json").read_text(encoding="utf-8"))
    return {"bookings": [{"record_locator": b["record_locator"], "last_name": b["last_name"], "route": " → ".join(f"{j['origin']}-{j['destination']}" for j in b["journeys"]), "scenario": b["mock_timatic_case"], "notice": b.get("demo_notes", "")} for b in data["bookings"]], "mock_only": True}

@app.get("/api/v1/rules")
async def get_rules() -> dict[str, Any]:
    return {"rules": await asyncio.to_thread(rule_graph.list_rules), "notice": "Reference-derived rules are not automatically Emirates-approved policies; the graph is explanatory, not authoritative for live eligibility."}


@app.get("/api/v1/airports/popular")
async def popular_airports() -> dict[str, Any]:
    codes = ["DXB", "LHR", "DEL", "BOM", "SIN", "JFK", "CDG", "SYD", "MLE", "BKK", "IST", "NRT"]
    return {"airports": [airport_index.by_iata(code) for code in codes if airport_index.by_iata(code)], "mock_only": True}


@app.get("/api/v1/airports/search")
async def search_airports(q: str = "", limit: int = 6) -> dict[str, Any]:
    if len(q) > 120:
        raise HTTPException(status_code=422, detail="Airport search query is too long")
    return airport_index.search(q, limit=limit)


@app.post("/api/v1/flights/search")
async def search_flights(body: FlightSearchRequest) -> dict[str, Any]:
    try:
        return commerce.search(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/bookings/confirm")
async def confirm_mock_booking(body: BookingConfirmationRequest, request: Request) -> dict[str, Any]:
    try:
        result = commerce.confirm(body)
        logger.info("Synthetic booking created correlation_id=%s passenger_count=%d", correlation(request), len(result.get("passengers", [])))
        return result
    except PaymentDeclined as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/flight-status")
async def get_mock_flight_status(flight_number: str, departure_date: date | None = None) -> dict[str, Any]:
    flight_number = flight_number.strip().upper()
    if not __import__("re").fullmatch(r"EK[0-9]{1,4}", flight_number):
        raise HTTPException(status_code=422, detail="Enter a flight number in the demo format EK followed by 1–4 digits")
    seed = int(hashlib.sha256(f"{flight_number}|{departure_date or date.today()}".encode()).hexdigest()[:4], 16)
    statuses = [("ON TIME", "Your demo flight is currently scheduled on time."), ("SCHEDULED", "Your demo flight is scheduled."), ("DELAYED", "This synthetic scenario includes a short delay.")]
    status, note = statuses[seed % len(statuses)]
    return {"flight_number": flight_number, "departure_date": (departure_date or date.today()).isoformat(), "status": status, "message": note, "mock_only": True, "notice": "Illustrative status only. No live airline operations or flight tracker was queried."}


@app.post("/api/v1/pdp/context")
async def pdp_context(body: PDPContextRequest) -> dict[str, Any]:
    return {**resolve_pdp_next_step(body), "agent": "PDP_CONTEXT_AGENT", "mock_only": True, "notice": "Context-aware field guidance only; the passenger remains in control of all form values."}


@app.get("/api/v1/knowledge/status")
async def knowledge_status() -> dict[str, Any]:
    """Expose index health and source counts for local POC troubleshooting."""
    from app.faq_store import FAQCorpusStore
    from app.chroma_store import index_status
    corpus = FAQCorpusStore()
    active = corpus.get_active_version()
    docs = corpus.get_active_documents()
    scopes: dict[str, int] = {}
    for doc in docs:
        key = str(doc.get("brand_scope", "unspecified"))
        scopes[key] = scopes.get(key, 0) + 1
    return {"status": "ok" if active and docs else "needs_attention", "active_corpus": active, "approved_sources": len(docs), "brand_scope_counts": scopes, "chroma": index_status(), "live_web_search_configured": bool(__import__("os").getenv("RAG_WEB_SEARCH_URL", "").strip()), "optional_remote_retriever_configured": bool(__import__("os").getenv("RAG_RETRIEVER_URL", "").strip())}


def _merge_explicit_booking_values(message: str, workflow: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Extract common multi-value flight booking utterances without guessing missing slots."""
    from datetime import date, timedelta
    value = dict(workflow or {})
    lower = message.casefold()
    changed: list[str] = []
    trip = None
    if re.search(r"\b(one[ -]?way|single trip)\b", lower): trip = "one_way"
    elif re.search(r"\b(round[ -]?trip|return trip|there and back)\b", lower): trip = "round_trip"
    if trip and value.get("trip_type") != trip:
        value["trip_type"] = trip; changed.append("trip_type")
        if trip == "one_way": value["return_date"] = None
    date_value = None
    date_pattern = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{4})?|tomorrow|day after tomorrow|today)"
    return_match = re.search(r"\b(?:return|returning|back)\s+(?:date\s*)?(?:is\s*)?(?:to\s*)?(?:on\s*)?" + date_pattern + r"\b", lower, re.I)
    if return_match:
        raw_return = return_match.group(1)
        try:
            if raw_return == "tomorrow": return_date = (date.today() + timedelta(days=1)).isoformat()
            elif raw_return == "day after tomorrow": return_date = (date.today() + timedelta(days=2)).isoformat()
            elif raw_return == "today": return_date = date.today().isoformat()
            else: return_date = parse_field_value("return_date", raw_return).get("return_date")
            if value.get("return_date") != return_date:
                value["return_date"] = return_date; changed.append("return_date")
        except Exception: pass
    else:
        if re.search(r"\bday after tomorrow\b", lower): date_value = (date.today() + timedelta(days=2)).isoformat()
        elif re.search(r"\btomorrow\b", lower): date_value = (date.today() + timedelta(days=1)).isoformat()
        elif re.search(r"\btoday\b", lower): date_value = date.today().isoformat()
        else:
            m = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{4})?)\b", message)
            # Don't accidentally treat a date labelled as the return date as departure.
            if m and not re.search(r"\b(?:return|returning|back)\s+(?:date\s*)?(?:is\s*)?(?:to\s*)?(?:on\s*)?$", lower[:m.start()], re.I):
                try: date_value = parse_field_value("departure_date", m.group(1)).get("departure_date")
                except Exception: pass
        if date_value and value.get("departure_date") != date_value:
            value["departure_date"] = date_value; changed.append("departure_date")
    for field, pattern in (("adults", r"\b(\d+)\s+adults?\b"), ("children", r"\b(\d+)\s+children?\b"), ("infants", r"\b(\d+)\s+infants?\b"), ("senior_citizens", r"\b(\d+)\s+(?:senior\s+citizens?|seniors?)\b")):
        m = re.search(pattern, lower)
        if m:
            try:
                parsed = parse_field_value(field, m.group(1))[field]
                if value.get(field) != parsed: value[field] = parsed; changed.append(field)
            except Exception: pass
    cabin_map = (("premium economy", "Premium Economy"), ("business class", "Business"), ("business cabin", "Business"), ("business", "Business"), ("first class", "First"), ("first-class", "First"), ("first cabin", "First"), ("first", "First"), ("economy", "Economy"))
    for term, cabin in cabin_map:
        if re.search(r"\b" + re.escape(term) + r"\b", lower):
            if value.get("cabin") != cabin: value["cabin"] = cabin; changed.append("cabin")
            break
    return value, changed


def _booking_chat_next_field(workflow: dict[str, Any]) -> str | None:
    """Chat-mode sequencing asks optional counts explicitly instead of treating form defaults as answers."""
    completed = set(workflow.get("_chat_completed_fields") or [])
    order = ["origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"]
    for field in order:
        if field == "return_date" and workflow.get("trip_type") == "one_way":
            continue
        if field not in completed:
            return field
    return None


def _booking_next_field(workflow: dict[str, Any]) -> str | None:
    order = ["origin", "destination", "trip_type", "departure_date"]
    if workflow.get("trip_type") == "round_trip": order.append("return_date")
    order += ["adults", "children", "infants", "senior_citizens", "cabin"]
    for field in order:
        v = workflow.get(field)
        if field == "trip_type" and v in {"one_way", "round_trip"}: continue
        if field in {"children", "infants", "senior_citizens"} and (v in (None, 0, "0")): continue
        if field == "return_date" and workflow.get("trip_type") == "one_way": continue
        if v in (None, "", {}): return field
    return None


@app.post("/api/v1/site-assistant/chat")
async def site_assistant_chat(body: SiteAssistantRequest) -> dict[str, Any]:
    """Flight-scoped booking assistant: route search/context intent, otherwise source-grounded FAQ."""
    message = body.message.strip()
    lower = message.casefold()
    context = dict(body.context or {})
    page = body.page.casefold()
    # The form contains live values while assistant_values contains workflow metadata (chat sequence/edit mode).
    # Merge rather than letting the fresh form payload erase pending chatbot steps between turns.
    form_workflow = dict(context.get("workflow") or {})
    saved_assistant_values = dict(context.get("assistant_values") or {})
    if form_workflow or saved_assistant_values:
        context["workflow"] = {**{k: v for k, v in saved_assistant_values.items() if str(k).startswith("_")}, **form_workflow}
    task = str(context.get("assistant_task") or "")
    pending_field = str(context.get("pending_field") or "")
    pdp_help_intent = any(term in lower for term in ("what's missing", "what is missing", "what should i enter", "what do i enter", "which field", "what next", "help me fill", "fill passenger", "passenger details", "continue this form"))
    start_booking_chat = any(term in lower for term in ("book by chat", "book a flight by chat", "start booking by chat", "help me book a flight", "book me a flight"))
    start_pdp_chat = any(term in lower for term in ("fill passenger details by chat", "fill passenger details", "enter passenger details", "help me with passenger details", "complete passenger details"))
    slot_field_sets = {
        "booking": {"origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"},
        "passenger_details": {"first_name", "last_name", "middle_name", "title", "date_of_birth", "nationality", "gender", "contact_email", "contact_phone", "accepted_terms"},
    }
    booking_labels = extract_labeled_field_values(message, slot_field_sets["booking"])
    passenger_labels = extract_labeled_field_values(message, slot_field_sets["passenger_details"])
    labelled_slots = extract_labeled_field_values(message, slot_field_sets.get(task, set())) if task else {}
    # If a short follow-up arrives after another screen changed, infer the active task from
    # the visible booking context and current page instead of dropping into generic FAQ.
    if not task and page in {"search", "home", "book", "outbound", "return"} and (context.get("workflow") or context.get("assistant_values")):
        task = "booking"
        pending_field = str(pending_field or _booking_next_field(dict(context.get("workflow") or context.get("assistant_values") or {})) or "")
    # Keep the visible page authoritative: do not let a stale pending field from a prior page
    # consume short replies meant for extras, seats, payment, or check-in FAQs.
    site_booking_pages = {"search", "home", "book", "outbound", "return"}
    site_passenger_pages = {"passengers", "passenger-details", "pdp"}
    if task == "booking" and page in site_passenger_pages and (context.get("pdp") or {}).get("passengers"):
        pdp_data = dict(context.get("pdp") or {})
        task = "passenger_details"
        pending_field, inferred_index = next_booking_field(list(pdp_data.get("passengers") or []), dict(pdp_data.get("contact") or {}), bool(pdp_data.get("accepted_terms", False)))
        if context.get("pending_passenger_index") is None:
            context["pending_passenger_index"] = inferred_index
    elif (task == "booking" and page not in site_booking_pages) or (task == "flight_fare" and page not in {"outbound", "return"}) or (task == "passenger_details" and page not in site_passenger_pages):
        task = ""
        pending_field = ""
        labelled_slots = {}

    question_start = bool(re.match(r"^(?:what|why|how|can i|can you|could i|could you|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", lower))
    question_topic = message.rstrip().endswith("?") and any(topic in lower for topic in ("baggage", "luggage", "refund", "check-in", "check in", "visa", "passport", "flight status", "power bank", "wifi", "wi-fi", "seat", "wheelchair", "lounge", "meal", "payment", "privacy", "fare", "booking window"))
    explicit_action_start = bool(re.match(r"^(?:please )?(?:edit|modify|change|update|add|remove|delete|deselect|select|choose|pick|skip|continue|go to|book|search|find flights|validate|run|check my eligibility|lookup|look up|retrieve|confirm|complete|set|use)\b", lower))
    explicit_workflow_phrase = any(phrase in lower for phrase in ("check my eligibility", "validate documents", "skip seat selection", "show seats", "search flights", "book a flight by chat", "lookup ", "look up ", "complete mock check-in"))
    if (question_start or question_topic) and not explicit_action_start and not explicit_workflow_phrase and not (pdp_help_intent and page in site_passenger_pages):
        try:
            faq = site_rag.answer(message)
        except Exception as exc:
            faq = {"answer": "I couldn't retrieve a grounded answer from the local aviation corpus. Your visible page and any entered values remain unchanged.", "citations": [], "intent": "faq_abstain", "retrieval_trace": {"error_type": type(exc).__name__}}
        return {"answer": faq.get("answer", "I couldn't verify that from the approved sources."), "intent": faq.get("intent", "faq"), "assistant_task": task or None, "assistant_values": dict(context.get("assistant_values") or {}), "pending_field": pending_field or None, "pending_passenger_index": context.get("pending_passenger_index"), "citations": faq.get("citations", []), "actions": [], "retrieval_trace": faq.get("retrieval_trace"), "card": {"type": "faq", "title": "Flight information", "status": "Sources available" if faq.get("citations") else "Evidence-limited", "note": "This answer does not submit a booking, apply a field change, or advance your workflow."}}

    # Repeated booking sections and explicit edit requests use a confirmation turn.
    # Proposed changes never hit the visible form until the user explicitly says yes.
    if task == "booking" and pending_field == "confirm_booking_edit":
        yn = lower.strip()
        saved = dict(context.get("assistant_values") or {})
        current = dict(context.get("workflow") or {k:v for k,v in saved.items() if not k.startswith("_")})
        resume_field = saved.get("_resume_pending_field")
        direct_date_or_cabin = ("departure date" in lower or "return date" in lower or ("cabin" in lower and any(w in lower for w in ("economy", "business", "first", "premium"))))
        if direct_date_or_cabin:
            updated, changed = _merge_explicit_booking_values(message, current)
            if changed:
                if updated.get("trip_type") == "round_trip" and updated.get("departure_date") and updated.get("return_date") and str(updated["return_date"]) <= str(updated["departure_date"]):
                    return {"answer": "The return date must be after departure. Please enter a later return date.", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": {**current, "_resume_pending_field": resume_field}, "pending_field": "confirm_booking_edit", "citations": [], "actions": []}
                actions = [{"type": "SET_SEARCH_FORM_FIELD", "field": key, "value": updated[key]} for key in changed]
                return {"answer": "Updated the visible flight-search form: " + ", ".join(key.replace("_", " ") for key in changed) + ". The new values are retained; say ‘proceed’ or ‘search flights’ to search using them.", "intent": "booking_fields_updated", "assistant_task": "booking", "assistant_values": {**updated, "_chat_started": False, "_chat_completed_fields": []}, "pending_field": None, "citations": [], "actions": actions, "card": {"type": "booking", "title": "Flight-search details updated", "status": "Updated · not searched", "fields": [{"label": key.replace("_", " "), "value": updated.get(key)} for key in changed], "note": "Only the named fields changed. Search remains a separate explicit action."}}
        if yn in {"yes", "y", "yes please", "confirm", "edit", "modify", "go ahead", "change"}:
            current_order = ["origin", "destination", "trip_type", "departure_date"]
            if current.get("trip_type") == "round_trip": current_order.append("return_date")
            current_order += ["adults", "children", "infants", "senior_citizens", "cabin"]
            first_field = current_order[0]
            return {"answer": "Okay, let's review the booking search inputs one at a time. Your current values will stay in place unless you change them. Answer the prompt or say ‘skip’ to keep its current value. " + FIELD_QUESTIONS[first_field], "intent": "edit_started", "assistant_task": "booking", "assistant_values": {**current, "_edit_mode": True}, "pending_field": first_field, "citations": [], "actions": [{"type": "NAVIGATE", "target": "book"}], "card": {"type": "booking", "title": "Edit flight search", "status": "Editing · existing values preserved", "fields": [{"label": k.replace("_", " "), "value": current.get(k, "Not set")} for k in current_order], "note": "Say ‘skip’ to retain a current value. Search and booking are separate actions."}}
        if yn in {"no", "n", "cancel", "resume", "continue", "keep current", "don't change", "do not change", "no thanks"}:
            return {"answer": "No changes made. I restored the previous booking task and pending field. Say ‘search flights’ when you’re ready to continue.", "intent": "edit_declined", "assistant_task": "booking", "assistant_values": current, "pending_field": resume_field, "citations": [], "actions": [], "card": {"type": "workflow", "title": "Booking resumed", "status": "No changes made", "note": "Previous search details are preserved."}}
        return {"answer": "Would you like to modify the flight-search section? Reply ‘yes’ to review its fields one at a time, or ‘no’ to resume the previous booking task without changes.", "intent": "edit_confirmation", "assistant_task": "booking", "assistant_values": saved, "pending_field": "confirm_booking_edit", "citations": [], "actions": []}

    explicit_booking_edit = bool(re.match(r"^(?:i want to |i'd like to |please )?(?:edit|modify|change|update)\b", lower)) and (task == "booking" or page in {"search", "home", "book", "outbound", "return"}) and any(x in lower for x in ("booking", "search", "flight", "route", "date", "destination", "origin", "passenger", "cabin", "details", "section"))
    if explicit_booking_edit and pending_field != "confirm_booking_edit":
        current = dict(context.get("workflow") or context.get("assistant_values") or {})
        return {"answer": "I have your current flight-search details. Do you want to modify them? Reply ‘yes’ and I’ll walk through the search fields in order, or ‘no’ to keep your current values and continue where you left off.", "intent": "edit_confirmation", "assistant_task": "booking", "assistant_values": {**current, "_resume_pending_field": pending_field or None}, "pending_field": "confirm_booking_edit", "citations": [], "actions": [], "card": {"type": "booking", "title": "Modify flight search?", "status": "Awaiting confirmation", "fields": [{"label": k.replace("_", " "), "value": current.get(k, "Not set")} for k in ("origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin") if current.get(k) is not None], "note": "No fields have changed yet."}}

    # Passenger-details edits also use yes/no confirmation; the form snapshot is retained.
    if task == "passenger_details" and pending_field == "confirm_pdp_edit":
        yn = lower.strip()
        saved = dict(context.get("assistant_values") or {})
        previous = saved.get("_pdp_edit_resume_pending_field")
        if yn in {"yes", "y", "yes please", "confirm", "edit", "modify", "go ahead"}:
            pdp = dict(context.get("pdp") or {})
            pax = list(pdp.get("passengers") or [])
            if not pax:
                return {"answer": "There are no passenger rows to edit yet. Search and choose a synthetic flight first; your current booking form remains unchanged.", "intent": "passenger_details_prerequisite", "assistant_task": "passenger_details", "pending_field": None, "assistant_values": {}, "citations": [], "actions": []}
            return {"answer": "Okay, let's review passenger details one field at a time. Current values remain in the form unless you change them. Say ‘skip’ to keep a field unchanged. " + FIELD_QUESTIONS["title"] + " for passenger 1.", "intent": "edit_started", "assistant_task": "passenger_details", "assistant_values": {"_pdp_edit_mode": True}, "pending_field": "title", "pending_passenger_index": 0, "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "passengers"}], "card": {"type": "passenger", "title": "Edit passenger details", "status": "Editing · existing values preserved", "fields": [{"label": "Passengers in form", "value": str(len(pax))}], "note": "No booking is submitted by editing. Skip retains the visible current value."}}
        if yn in {"no", "n", "cancel", "resume", "continue", "keep current", "don't change", "do not change"}:
            return {"answer": "No changes made. Passenger details and the previous pending field are restored; continue from where you left off.", "intent": "edit_declined", "assistant_task": "passenger_details", "assistant_values": {}, "pending_field": previous, "pending_passenger_index": context.get("pending_passenger_index"), "citations": [], "actions": [], "card": {"type": "workflow", "title": "Passenger details resumed", "status": "No changes made", "note": "The visible passenger form is unchanged."}}
        return {"answer": "Would you like to modify passenger details? Reply ‘yes’ to review inputs one by one or ‘no’ to keep the current form and resume the previous step.", "intent": "edit_confirmation", "assistant_task": "passenger_details", "assistant_values": saved, "pending_field": "confirm_pdp_edit", "citations": [], "actions": []}

    explicit_pdp_edit = bool(re.match(r"^(?:i want to |i'd like to |please )?(?:edit|modify|change|update)\b", lower)) and (task == "passenger_details" or page in {"passengers", "passenger-details", "pdp"}) and any(x in lower for x in ("passenger", "name", "details", "contact", "dob", "birth"))
    if explicit_pdp_edit and pending_field != "confirm_pdp_edit":
        saved = dict(context.get("assistant_values") or {})
        saved["_pdp_edit_resume_pending_field"] = pending_field or None
        return {"answer": "Your passenger form already has saved values. Would you like to modify this section? Reply ‘yes’ to review its fields one by one, or ‘no’ to continue the previous task unchanged.", "intent": "edit_confirmation", "assistant_task": "passenger_details", "assistant_values": saved, "pending_field": "confirm_pdp_edit", "pending_passenger_index": context.get("pending_passenger_index"), "citations": [], "actions": [], "card": {"type": "passenger", "title": "Modify passenger details?", "status": "Awaiting confirmation", "note": "Nothing has been changed yet. You can skip fields to retain the existing value."}}

    # Page-aware section editing: confirm first, then preserve skipped values and review one input at a time.
    section_pages = {"extras", "seats", "payment", "confirmation"}
    edit_verb = bool(re.match(r"^(?:i want to |i'd like to |please )?(?:edit|modify|change|update)\b", lower))
    if task == "section_edit" and pending_field == "confirm_section_edit":
        saved = dict(context.get("assistant_values") or {})
        target = str(saved.get("_edit_section") or page).lower()
        yes = lower.strip() in {"yes", "y", "yes please", "confirm", "edit", "modify", "go ahead"}
        no = lower.strip() in {"no", "n", "cancel", "resume", "continue", "keep current", "don't change", "do not change", "no thanks"}
        if no:
            return {"answer": "No changes made. I restored the previous assistant task and pending field; the visible section is unchanged.", "intent": "edit_declined", "assistant_task": saved.get("_prev_task"), "assistant_values": dict(saved.get("_prev_values") or {}), "pending_field": saved.get("_prev_pending"), "citations": [], "actions": [], "card": {"type": "workflow", "title": "Workflow resumed", "status": "No changes made", "note": "Existing form values and selections were preserved."}}
        if yes and target == "extras":
            codes = list(saved.get("_edit_codes") or [])
            return {"answer": f"Let's review optional extras one at a time. Additional baggage is currently {'selected' if 'EXTRA_BAG' in codes else 'not selected'}. Say ‘keep’/‘skip’ to retain the value, ‘add’ to select it, or ‘remove’ to deselect it.", "intent": "edit_started", "assistant_task": "section_edit", "assistant_values": {**saved, "_edit_codes": codes, "_editing": True}, "pending_field": "section_extra_EXTRA_BAG", "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "extras"}], "card": {"type": "extras", "title": "Edit optional extras", "status": "Editing · choices preserved", "fields": [{"label": c.replace('_', ' '), "value": 'Selected' if c in codes else 'Not selected'} for c in ('EXTRA_BAG','LOUNGE','WIFI','MEAL')], "note": "No purchase occurs. Each optional service is reviewed separately."}}
        if yes and target == "seats":
            pc = dict(context.get("page_context") or {}); pax = list(pc.get("passengers") or []); seats = dict(saved.get("_edit_seats") or pc.get("selected_seats") or {})
            round_trip = bool(pc.get("is_round_trip") or (context.get("workflow") or {}).get("trip_type") == "round_trip")
            legs = ['outbound', 'return'] if round_trip else ['outbound']
            pairs = [{"key":f"{leg}:{i}","leg":leg,"index":i,"name":f"{x.get('first_name') or 'Passenger '+str(i+1)} {x.get('last_name') or ''}".strip()} for leg in legs for i,x in enumerate(pax)]
            if not pairs: return {"answer":"Select a flight and enter passenger details before editing seats. No changes were made.","intent":"edit_prerequisite","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[]}
            first=pairs[0]
            return {"answer":f"Let's review seat assignments one at a time. {first['name']} · {first['leg']} currently has {seats.get(first['key'],'no seat selected')}. Enter a seat such as 12A to change it, or say ‘skip’ to retain the current value. Say ‘skip seat selection’ to clear all seats.","intent":"edit_started","assistant_task":"section_edit","assistant_values":{**saved,"_edit_seats":seats,"_edit_pairs":pairs,"_editing":True},"pending_field":f"section_seat_{first['leg']}_{first['index']}","citations":[],"actions":[{"type":"NAVIGATE_FLOW_STEP","target":"seats"}],"card":{"type":"seats","title":"Edit seat assignments","status":"Editing · current seats preserved","fields":[{"label":f"{v['name']} · {v['leg']}", "value":seats.get(v['key'],'No seat selected')} for v in pairs],"note":"Synthetic seats only. Skip keeps the current choice; availability checks still apply."}}
        if yes and target == "payment":
            pc = dict(context.get("page_context") or {}); method=str(pc.get("payment_method") or "upi"); result=str(pc.get("payment_simulation") or "approve")
            return {"answer":f"Let's review payment simulation one field at a time. The payment method is currently {method}. Say ‘skip’ to keep it, or choose UPI, card, net banking, or wallet.","intent":"edit_started","assistant_task":"section_edit","assistant_values":{**saved,"_edit_method":method,"_edit_result":result,"_editing":True},"pending_field":"section_payment_method","citations":[],"actions":[{"type":"NAVIGATE_FLOW_STEP","target":"payment"}],"card":{"type":"payment","title":"Edit payment simulation","status":"Demo only","fields":[{"label":"Payment method","value":method},{"label":"Simulated outcome","value":result}],"note":"No payment details are collected and no money moves."}}
        if yes and target == "confirmation":
            return {"answer":"The confirmation receipt is read-only after a synthetic booking is submitted. I will not overwrite it. Return to the relevant section before submitting a new demo booking.","intent":"read_only_section","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[]}
        return {"answer":"Would you like to modify this section? Reply ‘yes’ and I’ll review its inputs one by one, or ‘no’ to continue with no changes. Say ‘skip’ on an input to keep its current value.","intent":"edit_confirmation","assistant_task":"section_edit","assistant_values":saved,"pending_field":"confirm_section_edit","citations":[],"actions":[]}

    if task != "section_edit" and edit_verb and page in section_pages and any(x in lower for x in ("section","detail","extra","seat","payment","booking","these","this")):
        pc = dict(context.get("page_context") or {}); vals = dict(context.get("assistant_values") or {})
        saved = {"_edit_section":page,"_prev_task":task or None,"_prev_pending":pending_field or None,"_prev_values":vals,"_edit_codes":list(pc.get("selected_extras") or []),"_edit_seats":dict(pc.get("selected_seats") or {}),"_edit_method":pc.get("payment_method") or "upi","_edit_result":pc.get("payment_simulation") or "approve"}
        label = {"extras":"optional extras","seats":"seat assignments","payment":"payment simulation","confirmation":"confirmation receipt"}[page]
        return {"answer":f"You're on the {label} section. Would you like to modify it? Reply ‘yes’ to review its inputs one by one, or ‘no’ to continue with no changes. You can say ‘skip’ for any input you want to retain.","intent":"edit_confirmation","assistant_task":"section_edit","assistant_values":saved,"pending_field":"confirm_section_edit","citations":[],"actions":[],"card":{"type":"workflow","title":f"Modify {label}?","status":"Awaiting confirmation","note":"No form values or selections have changed yet."}}

    # FAQs may interrupt section editing without consuming the current field.
    if task == "section_edit" and pending_field != "confirm_section_edit" and bool(re.match(r"^(?:what|why|how|can i|could i|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", lower)):
        faq = site_rag.answer(message)
        return {"answer": faq.get("answer", "I couldn't verify that from the approved sources.") + "\n\nYour section-edit step is still active; you can continue with the same field.", "intent": faq.get("intent", "faq"), "assistant_task": "section_edit", "assistant_values": dict(context.get("assistant_values") or {}), "pending_field": pending_field, "citations": faq.get("citations", []), "actions": [], "retrieval_trace": faq.get("retrieval_trace")}

    if task == "section_edit" and pending_field.startswith("section_extra_"):
        saved = dict(context.get("assistant_values") or {}); code = pending_field.removeprefix("section_extra_")
        labels = {"EXTRA_BAG":"Additional baggage","LOUNGE":"Lounge access","WIFI":"Onboard Wi-Fi","MEAL":"Meal preference"}
        codes = list(saved.get("_edit_codes") or []); selected = code in codes; action = None
        if lower.strip() in {"skip","keep","keep it","same","no change","leave unchanged","yes","y"}:
            pass
        elif lower.strip() in {"add","select","include","want","yes please"}:
            if not selected: codes.append(code); action = {"type":"TOGGLE_EXTRAS","codes":[code]}
        elif lower.strip() in {"remove","delete","deselect","no","n","don't want","do not want"}:
            if selected: codes = [item for item in codes if item != code]; action = {"type":"REMOVE_EXTRAS","codes":[code]}
        else:
            return {"answer":f"For {labels[code]}, say ‘keep’/‘skip’, ‘add’, or ‘remove’. No change has been applied.","intent":"slot_retry","assistant_task":"section_edit","assistant_values":saved,"pending_field":pending_field,"citations":[],"actions":[]}
        ordered = ["EXTRA_BAG","LOUNGE","WIFI","MEAL"]; idx=ordered.index(code); nxt=ordered[idx+1] if idx+1 < len(ordered) else None
        if nxt:
            return {"answer":f"{labels[code]} is now {'selected' if code in codes else 'not selected'}. {labels[nxt]} is currently {'selected' if nxt in codes else 'not selected'}. Say ‘keep’/‘skip’, ‘add’, or ‘remove’.","intent":"slot_filled","assistant_task":"section_edit","assistant_values":{**saved,"_edit_codes":codes},"pending_field":f"section_extra_{nxt}","citations":[],"actions":[action] if action else [],"card":{"type":"extras","title":"Optional extras review","status":f"{len(codes)} selected","fields":[{"label":labels[x],"value":"Selected" if x in codes else "Not selected"} for x in ordered],"note":"Choices remain visible on the form. No purchase has occurred."}}
        return {"answer":"All optional extras have been reviewed. Your approved choices are visible on the page. Continue to seats or payment when ready; no purchase was made.","intent":"section_edit_complete","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[action] if action else [],"card":{"type":"extras","title":"Optional extras summary","status":f"{len(codes)} selected · draft","fields":[{"label":labels[x],"value":"Selected" if x in codes else "Not selected"} for x in ordered],"note":"Synthetic options only. No money was taken."}}

    if task == "section_edit" and pending_field.startswith("section_seat_"):
        saved = dict(context.get("assistant_values") or {}); pairs=list(saved.get("_edit_pairs") or []); seats=dict(saved.get("_edit_seats") or {})
        try: current_idx=next(i for i,item in enumerate(pairs) if pending_field==f"section_seat_{item['leg']}_{item['index']}")
        except StopIteration: current_idx=-1
        if current_idx < 0: return {"answer":"I lost the current seat-edit step. No changes were made; open the Seats section again.","intent":"slot_retry","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[]}
        item=pairs[current_idx]; key=item["key"]; before=seats.get(key); seat_match=re.search(r"\b((?:[1-9]|[1-3][0-9]|40)[A-F])\b", message, re.I); action=None
        if any(term in lower for term in ("skip seat selection","skip all seats","no seats")):
            return {"answer":"Seat selection cleared as requested. No seats are assigned; continue when ready.","intent":"section_edit_complete","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[{"type":"CLEAR_BOOKING_SEATS"}],"card":{"type":"seats","title":"Seat selection skipped","status":"No seats assigned","note":"Synthetic seat choice only."}}
        if lower.strip() not in {"skip","keep","keep it","same","no change","leave unchanged"}:
            if not seat_match: return {"answer":"Enter a seat such as 12A, say ‘skip’ to keep the current value, or say ‘skip seat selection’ to clear all seats.","intent":"slot_retry","assistant_task":"section_edit","assistant_values":saved,"pending_field":pending_field,"citations":[],"actions":[]}
            seat=seat_match.group(1).upper(); unavailable={"1B","2D","3A","5F","7C","9E","10B"}; taken={value for other_key,value in seats.items() if other_key.startswith(item['leg']+':') and other_key != key}
            if seat in unavailable or seat in taken: return {"answer":f"Seat {seat} is unavailable or already assigned in this journey. Choose another seat or say ‘skip’ to keep the current value.","intent":"slot_retry","assistant_task":"section_edit","assistant_values":saved,"pending_field":pending_field,"citations":[],"actions":[]}
            seats[key]=seat
            if seat != before: action={"type":"SELECT_BOOKING_SEAT","seat_code":seat,"passenger_index":item['index'],"leg":item['leg']}
        nxt=pairs[current_idx+1] if current_idx+1 < len(pairs) else None
        if nxt: return {"answer":f"Reviewed {item['name']} · {item['leg']}. Next, {nxt['name']} · {nxt['leg']} has {seats.get(nxt['key'],'no seat selected')}. Enter a seat or say ‘skip’ to keep the current value.","intent":"slot_filled","assistant_task":"section_edit","assistant_values":{**saved,"_edit_seats":seats},"pending_field":f"section_seat_{nxt['leg']}_{nxt['index']}","citations":[],"actions":[action] if action else [],"card":{"type":"seats","title":"Seat-edit progress","status":f"{current_idx+1} of {len(pairs)} reviewed","fields":[{"label":f"{x['name']} · {x['leg']}","value":seats.get(x['key'],'No seat selected')} for x in pairs],"note":"Current mock assignments remain visible on the seat map."}}
        missing=[x for x in pairs if not seats.get(x['key'])]
        return {"answer":"All seat fields have been reviewed. "+("Some pairs still have no seat; select them or say ‘skip seat selection’ to clear assignments." if missing else "All passenger/journey pairs have seats assigned. Review the seat map before continuing."),"intent":"section_edit_complete","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[action] if action else [],"card":{"type":"seats","title":"Seat assignments summary","status":f"{len(pairs)-len(missing)} of {len(pairs)} assigned","fields":[{"label":f"{x['name']} · {x['leg']}","value":seats.get(x['key'],'No seat selected')} for x in pairs],"note":"Synthetic seat choices only."}}

    if task == "section_edit" and pending_field == "section_payment_method":
        saved=dict(context.get("assistant_values") or {}); low=lower.strip(); method=saved.get("_edit_method","upi"); action=None
        if low not in {"skip","keep","keep it","same","no change","leave unchanged"}:
            if "net" in low: method="netbanking"
            elif "wallet" in low: method="wallet"
            elif "card" in low: method="card"
            elif "upi" in low or "qr" in low: method="upi"
            else: return {"answer":"Choose UPI/QR, card, net banking or wallet, or say ‘skip’ to keep the current value.","intent":"slot_retry","assistant_task":"section_edit","assistant_values":saved,"pending_field":pending_field,"citations":[],"actions":[]}
            action={"type":"SET_PAYMENT_METHOD","value":method}
        return {"answer":f"Payment method is {method}. Next, the current mock outcome is {saved.get('_edit_result','approve')}. Say ‘skip’ to retain it, ‘approve/success’ or ‘decline/fail’ to change it.","intent":"slot_filled","assistant_task":"section_edit","assistant_values":{**saved,"_edit_method":method},"pending_field":"section_payment_result","citations":[],"actions":[action] if action else [],"card":{"type":"payment","title":"Payment method","status":"Demo only","fields":[{"label":"Method","value":method}],"note":"No payment is attempted."}}
    if task == "section_edit" and pending_field == "section_payment_result":
        saved=dict(context.get("assistant_values") or {}); low=lower.strip(); outcome=saved.get("_edit_result","approve"); action=None
        if low not in {"skip","keep","keep it","same","no change","leave unchanged"}:
            if any(x in low for x in ("decline","fail","unsuccessful")): outcome="decline"
            elif any(x in low for x in ("approve","success","successful")): outcome="approve"
            else: return {"answer":"Say ‘skip’ to retain the current outcome, ‘approve/success’ or ‘decline/fail’. No payment is executed.","intent":"slot_retry","assistant_task":"section_edit","assistant_values":saved,"pending_field":pending_field,"citations":[],"actions":[]}
            action={"type":"SET_PAYMENT_SIMULATION","value":outcome}
        return {"answer":"Payment section reviewed. These selections only affect the visible simulation; no provider was contacted and no money moved.","intent":"section_edit_complete","assistant_task":saved.get("_prev_task"),"assistant_values":dict(saved.get("_prev_values") or {}),"pending_field":saved.get("_prev_pending"),"citations":[],"actions":[action] if action else [],"card":{"type":"payment","title":"Payment simulation summary","status":"Demo only","fields":[{"label":"Payment method","value":saved.get("_edit_method","upi")},{"label":"Simulated outcome","value":outcome}],"note":"No payment details were collected or transmitted."}}

    current_booking_values = dict(context.get("workflow") or context.get("assistant_values") or {})
    if task == "booking" and page in {"search", "home", "book", "outbound", "return"} and booking_labels and pending_field != "confirm_booking_edit" and not current_booking_values.get("_edit_mode") and (pending_field is None or pending_field not in booking_labels):
        changed_existing = [field for field in booking_labels if current_booking_values.get(field) not in (None, "")]
        if changed_existing:
            snapshot = {**current_booking_values, "_resume_pending_field": pending_field or None, "_proposed_booking_fields": booking_labels}
            return {"answer": "I noticed input(s) for a booking field you’ve already populated. Would you like to modify the flight-search section? Reply ‘yes’ and I’ll review the page inputs one by one, or ‘no’ to leave every field unchanged and resume where you were.", "intent": "edit_confirmation", "assistant_task": "booking", "assistant_values": snapshot, "pending_field": "confirm_booking_edit", "citations": [], "actions": [], "card": {"type": "booking", "title": "Modify booking inputs?", "status": "Awaiting confirmation", "fields": [{"label": field.replace('_', ' '), "value": current_booking_values.get(field, 'Not set')} for field in changed_existing], "note": "No form values have changed yet. You may skip existing values during the edit flow."}}

    # Explicit workflow controls are handled before general FAQ routing.
    if task == "booking" and any(term in lower for term in ("cancel booking chat", "stop booking", "cancel this search", "start over", "reset booking chat")):
        return {"answer": "Okay, I have ended the current chat-led search. The visible page values are unchanged; you can restart when ready.", "intent": "booking_task_cancelled", "assistant_task": None, "pending_field": None, "assistant_values": {}, "citations": [], "actions": []}
    if task == "booking" and not booking_labels and (any(term in lower for term in ("tomorrow", "day after tomorrow", "one way", "one-way", "round trip", "round-trip", " adults", " children", " infant", " economy", " business", " premium economy", " first class", " departure date", " return date")) or ("cabin" in lower and any(term in lower for term in ("business", "first", "economy", "premium")))):
        current = dict(context.get("workflow") or context.get("assistant_values") or {})
        merged, changed = _merge_explicit_booking_values(message, current)
        if changed:
            if merged.get("_chat_started"):
                completed = set(merged.get("_chat_completed_fields") or []); completed.update(changed)
                # Mark explicit values as answered even when they equal a prefilled value.
                # The wizard must not repeat a count merely because the value was unchanged.
                for field, pattern in (("adults", r"\b\d+\s+adults?\b"), ("children", r"\b\d+\s+children?\b"), ("infants", r"\b\d+\s+infants?\b"), ("senior_citizens", r"\b\d+\s+(?:senior\s+citizens?|seniors?)\b")):
                    if re.search(pattern, lower): completed.add(field)
                if re.search(r"\b(one[ -]?way|single trip|round[ -]?trip|return trip|there and back)\b", lower): completed.add("trip_type")
                if re.search(r"\b(?:first class|first-class|first cabin|business class|business cabin|business|premium economy|premium|economy)\b", lower): completed.add("cabin")
                if re.search(r"\b(?:return|returning|back)\s+(?:date\s*)?(?:is\s*)?(?:to\s*)?(?:on\s*)?(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+[a-z]{3,9}(?:\s+\d{4})?|tomorrow|day after tomorrow|today)\b", lower): completed.add("return_date")
                elif re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2}\s+[a-z]{3,9}(?:\s+\d{4})?|tomorrow|day after tomorrow|today)\b", lower): completed.add("departure_date")
                merged["_chat_completed_fields"] = sorted(completed)
            if merged.get("trip_type") == "round_trip" and merged.get("departure_date") and merged.get("return_date") and str(merged["return_date"]) <= str(merged["departure_date"]):
                merged["return_date"] = (date.fromisoformat(str(merged["departure_date"])) + timedelta(days=7)).isoformat()
                changed.append("return_date")
            if merged.get("_chat_started"):
                completed = set(merged.get("_chat_completed_fields") or []); completed.update(changed); merged["_chat_completed_fields"] = sorted(completed)
                next_field = _booking_chat_next_field(merged)
            else:
                next_field = _booking_next_field(merged)
            actions = [{"type": "SET_SEARCH_FORM_FIELD", "field": key, "value": merged.get(key)} for key in changed]
            answer = "Updated the current booking draft with " + ", ".join(k.replace("_", " ") for k in changed) + ". "
            answer += (FIELD_QUESTIONS[next_field] if next_field else "All required search fields are ready. Say ‘proceed’ or ‘search flights’ to find synthetic options.")
            return {"answer": answer, "intent": "slot_filled" if next_field else "booking_search_ready", "assistant_task": "booking", "assistant_values": merged, "pending_field": next_field, "citations": [], "actions": actions}
    if task == "booking" and lower.strip() in {"proceed", "continue", "go ahead", "next", "proceed to search", "search flights", "find flights", "show flights", "book ticket now"}:
        wf = dict(context.get("workflow") or context.get("assistant_values") or {})
        missing = _booking_chat_next_field(wf) if wf.get("_chat_started") else _booking_next_field(wf)
        if missing:
            return {"answer": "Let's continue the same booking. " + FIELD_QUESTIONS[missing], "intent": "slot_prompt", "assistant_task": "booking", "assistant_values": wf, "pending_field": missing, "citations": [], "actions": [{"type": "NAVIGATE", "target": "book"}]}
        if not wf.get("origin") or not wf.get("destination") or not wf.get("departure_date"):
            return {"answer": "I still need an origin, destination and future departure date. I'll keep this booking task active while we complete them.", "intent": "slot_prompt", "assistant_task": "booking", "assistant_values": wf, "pending_field": next((f for f in ("origin", "destination", "departure_date") if not wf.get(f)), "origin"), "citations": [], "actions": []}
        if wf.get("trip_type") == "round_trip" and (not wf.get("return_date") or str(wf["return_date"]) <= str(wf["departure_date"])):
            return {"answer": "The return date must be after departure. " + FIELD_QUESTIONS["return_date"], "intent": "slot_prompt", "assistant_task": "booking", "assistant_values": wf, "pending_field": "return_date", "citations": [], "actions": []}
        if int(wf.get("adults", 0) or 0) < 1 or sum(int(wf.get(f, 0) or 0) for f in ("adults", "children", "infants", "senior_citizens")) > 9 or int(wf.get("infants", 0) or 0) > (int(wf.get("adults", 0) or 0) + int(wf.get("senior_citizens", 0) or 0)):
            return {"answer": "Please correct the passenger counts: one adult minimum, nine passengers maximum, and infants cannot outnumber adults.", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": wf, "pending_field": "adults", "citations": [], "actions": []}
        return {"answer": "Searching the synthetic schedule using the same values shown in the flight-search form.", "intent": "run_flight_search", "assistant_task": "booking", "assistant_values": wf, "pending_field": None, "citations": [], "actions": [{"type": "RUN_FLIGHT_SEARCH"}]}
    # Route intents take precedence over pending date/count slots; the city pair must
    # never be sent to a date parser just because the current missing field is departure_date.
    route_match_active = re.search(r"\bfrom\s+([a-zA-Z .'-]{2,45}?)\s+to\s+([a-zA-Z .'-]{2,45}?)(?:\s+(?:on|for|next|this|tomorrow|today)|$)", message, re.I)
    if task == "booking" and route_match_active:
        origin_query, destination_query = route_match_active.group(1).strip(), route_match_active.group(2).strip()
        origin_results = airport_index.search(origin_query, limit=6).get("results", [])
        dest_results = airport_index.search(destination_query, limit=6).get("results", [])
        origin = next((a for a in origin_results if a.get("city", "").casefold() == origin_query.casefold() or a.get("iata", "").casefold() == origin_query.casefold()), origin_results[0] if len(origin_results) == 1 else None)
        destination = next((a for a in dest_results if a.get("city", "").casefold() == destination_query.casefold() or a.get("iata", "").casefold() == destination_query.casefold()), dest_results[0] if len(dest_results) == 1 else None)
        if not origin or not destination:
            return {"answer": "I couldn't resolve both ends of that route. Please provide a city or airport code for each, for example ‘from Delhi to Mumbai’. The booking task is still active.", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": context.get("workflow") or context.get("assistant_values") or {}, "pending_field": pending_field or "origin", "citations": [], "actions": []}
        if origin.get("iata") == destination.get("iata"):
            return {"answer": "Origin and destination must be different airports. Which destination should I use?", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": context.get("workflow") or context.get("assistant_values") or {}, "pending_field": "destination", "citations": [], "actions": []}
        wf = dict(context.get("workflow") or context.get("assistant_values") or {})
        wf.update({"origin": origin["iata"], "destination": destination["iata"]})
        # Route utterances may also contain dates/trip type; parse these before choosing the next slot.
        shortcut_values, _changed = _merge_explicit_booking_values(message, wf)
        wf.update(shortcut_values)
        if wf.get("_chat_started"):
            completed = set(wf.get("_chat_completed_fields") or []); completed.update(["origin", "destination"]); completed.update(_changed for _changed in [])
            wf["_chat_completed_fields"] = sorted(completed)
            next_field = _booking_chat_next_field(wf)
        else:
            next_field = _booking_next_field(wf)
        route_actions = [{"type": "SET_SEARCH_FORM_FIELD", "field": "origin", "value": origin["iata"]}, {"type": "SET_SEARCH_FORM_FIELD", "field": "destination", "value": destination["iata"]}]
        for field in ("trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"):
            if field in shortcut_values and wf.get(field) != context.get("workflow", {}).get(field):
                route_actions.append({"type": "SET_SEARCH_FORM_FIELD", "field": field, "value": wf.get(field)})
        prompt_field = next_field or "departure_date"
        answer = f"I filled {origin['city']} ({origin['iata']}) to {destination['city']} ({destination['iata']}) into the same booking form. "
        if next_field:
            answer += FIELD_QUESTIONS[next_field] + " You can include details together, for example ‘one way tomorrow, one adult, Economy’ or say ‘proceed’ when all required fields are complete."
        else:
            answer += "The route and required search details are ready. Say ‘proceed’ or ‘search flights’ to search the synthetic schedule."
        return {"answer": answer, "intent": "slot_filled" if next_field else "booking_search_ready", "assistant_task": "booking", "assistant_values": wf, "pending_field": next_field, "citations": [], "actions": route_actions}

    if task == "booking" and pending_field and (context.get("workflow") or context.get("assistant_values") or {}).get("_chat_started") and lower.strip() in {"skip", "keep", "keep it", "same", "no change", "leave unchanged"}:
        wf = dict(context.get("workflow") or context.get("assistant_values") or {})
        optional_zero = {"children", "infants", "senior_citizens"}
        current_value = wf.get(pending_field)
        if current_value in (None, "") and pending_field in optional_zero:
            current_value = 0; wf[pending_field] = 0
        if current_value in (None, ""):
            return {"answer": f"There is no saved value for {pending_field.replace('_', ' ')} to keep. This field is required here, so please provide it instead of skipping. {FIELD_QUESTIONS.get(pending_field, '')}", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": wf, "pending_field": pending_field, "citations": [], "actions": []}
        completed = set(wf.get("_chat_completed_fields") or []); completed.add(pending_field); wf["_chat_completed_fields"] = sorted(completed)
        next_field = _booking_chat_next_field(wf)
        if next_field:
            return {"answer": f"Kept the current {pending_field.replace('_', ' ')} value. {FIELD_QUESTIONS[next_field]}", "intent": "slot_skipped", "assistant_task": "booking", "assistant_values": wf, "pending_field": next_field, "citations": [], "actions": [], "card": {"type": "booking", "title": "Current value retained", "status": "Skipped · current value kept", "fields": [{"label": pending_field.replace('_', ' '), "value": current_value}], "note": "A skip retains the current value; required booking details cannot be blank."}}
        return {"answer": "All flight-search questions have been answered or explicitly skipped. Review the visible values, then say ‘proceed’ or ‘search flights’. No booking has been submitted.", "intent": "booking_search_ready", "assistant_task": "booking", "assistant_values": wf, "pending_field": None, "citations": [], "actions": []}

    if task == "passenger_details" and pending_field and not (context.get("assistant_values") or {}).get("_pdp_edit_mode") and lower.strip() in {"skip", "keep", "keep it", "same", "no change", "leave unchanged"}:
        pdp = dict(context.get("pdp") or {}); pax = list(pdp.get("passengers") or []); contact = dict(pdp.get("contact") or {})
        idx = context.get("pending_passenger_index")
        field_value = None
        if pending_field in {"contact_email", "contact_phone"}:
            field_value = contact.get("email" if pending_field == "contact_email" else "phone")
        elif pending_field == "accepted_terms":
            field_value = pdp.get("accepted_terms")
        elif idx is not None and 0 <= int(idx) < len(pax):
            field_value = pax[int(idx)].get(pending_field)
        optional = pending_field == "middle_name"
        if field_value in (None, "") and not optional:
            return {"answer": f"{FIELD_QUESTIONS.get(pending_field, 'This field')} is required and has no saved value to keep. Please provide it; the assistant will not skip a required name, date of birth, nationality, gender, contact or consent field.", "intent": "slot_retry", "assistant_task": "passenger_details", "assistant_values": {}, "pending_field": pending_field, "pending_passenger_index": idx, "citations": [], "actions": []}
        if optional and field_value in (None, "") and idx is not None:
            return {"answer": f"Left {pending_field.replace('_',' ')} blank as allowed. {FIELD_QUESTIONS.get('nationality','')} for passenger {int(idx)+1}.", "intent": "slot_skipped", "assistant_task": "passenger_details", "assistant_values": {}, "pending_field": "nationality", "pending_passenger_index": int(idx), "citations": [], "actions": [{"type":"UPDATE_PASSENGER_FIELD","field":pending_field,"passenger_index":int(idx),"value":""}]}
        if (context.get("assistant_values") or {}).get("_pdp_edit_mode"):
            pass
        return {"answer": f"Kept the saved {pending_field.replace('_',' ')} value. Please continue in the passenger form or tell me which remaining required field to edit.", "intent": "slot_skipped", "assistant_task": "passenger_details", "assistant_values": {}, "pending_field": None, "citations": [], "actions": []}

    if task == "booking" and pending_field and context.get("assistant_values", {}).get("_edit_mode") and lower.strip() in {"skip", "keep", "keep it", "same", "no change", "leave unchanged"}:
        wf = dict(context.get("workflow") or context.get("assistant_values") or {})
        current_value = wf.get(pending_field)
        if current_value in (None, ""):
            return {"answer": f"There is no saved value for {pending_field.replace('_', ' ')} to keep. Please provide it, or modify the value on the form. Required booking fields cannot be skipped when blank.", "intent": "slot_retry", "assistant_task": "booking", "assistant_values": {**wf, "_edit_mode": True}, "pending_field": pending_field, "citations": [], "actions": []}
        order = ["origin", "destination", "trip_type", "departure_date"]
        if wf.get("trip_type") == "round_trip": order.append("return_date")
        order += ["adults", "children", "infants", "senior_citizens", "cabin"]
        try: idx = order.index(pending_field)
        except ValueError: idx = -1
        remaining = order[idx + 1:] if idx >= 0 else order
        if wf.get("trip_type") == "one_way": remaining = [field for field in remaining if field != "return_date"]
        next_field = remaining[0] if remaining else None
        next_values = {k:v for k,v in wf.items() if not k.startswith("_")}
        if next_field:
            return {"answer": f"Kept the existing {pending_field.replace('_', ' ')} value. {FIELD_QUESTIONS[next_field]}", "intent": "slot_skipped", "assistant_task": "booking", "assistant_values": {**next_values, "_edit_mode": True}, "pending_field": next_field, "citations": [], "actions": [], "card": {"type": "booking", "title": "Current value retained", "status": f"Skipped · {pending_field.replace('_', ' ')}", "fields": [{"label": pending_field.replace('_', ' '), "value": wf.get(pending_field, "Not set")}], "note": "No change was applied to this field."}}
        return {"answer": "All editable search inputs have been reviewed. Say ‘search flights’ to search using the displayed values, or change a value on the form first.", "intent": "booking_search_ready", "assistant_task": "booking", "assistant_values": next_values, "pending_field": None, "citations": [], "actions": []}

    if task == "passenger_details" and pending_field and (context.get("assistant_values") or {}).get("_pdp_edit_mode") and lower.strip() in {"skip", "keep", "keep it", "same", "no change", "leave unchanged"}:
        pdp = dict(context.get("pdp") or {}); pax = list(pdp.get("passengers") or []); contact = dict(pdp.get("contact") or {})
        ordered = booking_pdp_field_order(pax, (context.get("workflow") or {}).get("departure_date"))
        needle = (pending_field, context.get("pending_passenger_index") if pending_field not in {"contact_email", "contact_phone", "accepted_terms"} else None)
        try: idx = ordered.index(needle)
        except ValueError: idx = -1
        pair = ordered[idx + 1] if 0 <= idx + 1 < len(ordered) else None
        if pair:
            field, pax_idx = pair
            val = contact.get("email" if field == "contact_email" else "phone") if field in {"contact_email", "contact_phone"} else (pdp.get("accepted_terms") if field == "accepted_terms" else pax[pax_idx].get(field))
            answer = f"Kept the existing {pending_field.replace('_', ' ')} value ({val if val not in (None, '') else 'blank'}). {FIELD_QUESTIONS[field]}"
            if pax_idx is not None: answer += f" for passenger {pax_idx + 1}."
            return {"answer": answer, "intent": "slot_skipped", "assistant_task": "passenger_details", "assistant_values": {"_pdp_edit_mode": True}, "pending_field": field, "pending_passenger_index": pax_idx, "citations": [], "actions": [], "card": {"type": "passenger", "title": "Current value retained", "status": f"Skipped · {pending_field.replace('_', ' ')}", "fields": [{"label": pending_field.replace('_', ' '), "value": val if val not in (None, '') else 'Blank'}], "note": "No change was applied to this field."}}
        return {"answer": "All editable passenger fields have been reviewed. Check the visible form and continue when ready.", "intent": "pdp_complete", "assistant_task": "passenger_details", "assistant_values": {}, "pending_field": None, "citations": [], "actions": []}

    # FAQ questions can interrupt a transaction, but return to the same pending field afterwards.
    faq_interrupt = bool(re.match(r"^(?:what|why|how|can i|could i|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", lower)) or (message.rstrip().endswith("?") and any(t in lower for t in ("baggage", "luggage", "refund", "check-in", "check in", "visa", "passport", "flight status", "power bank", "wifi", "seat", "wheelchair", "lounge", "meal", "privacy", "motto", "tagline")))
    if task and pending_field and faq_interrupt and not labelled_slots and not (task == "booking" and any(x in lower for x in ("tomorrow", "one way", "round trip", "adult", "children", "infant"))):
        try:
            faq = site_rag.answer(message)
        except Exception as exc:
            faq = {"answer": "I couldn't retrieve a verified answer from the local aviation knowledge base just now. Your current booking/check-in task is still active; you can continue with the requested field.", "citations": [], "intent": "faq_abstain", "retrieval_trace": {"stages": [{"stage": "faq_backend", "status": "local_recovery", "error_type": type(exc).__name__}]}}
        return {"answer": faq.get("answer", "I couldn't verify that from the approved sources.") + "\n\nYour current " + ("booking" if task == "booking" else "passenger-details") + " task is still active. " + FIELD_QUESTIONS.get(pending_field, "Continue with the next required field."), "intent": faq.get("intent", "faq"), "assistant_task": task, "assistant_values": context.get("workflow") or context.get("assistant_values") or {}, "pending_field": pending_field, "pending_passenger_index": context.get("pending_passenger_index"), "citations": faq.get("citations", []), "actions": [], "retrieval_trace": faq.get("retrieval_trace")}
    # Permit a complete, explicitly-labelled one-line task declaration, as well as prompted turns.
    if start_booking_chat and booking_labels:
        task = "booking"
        pending_field = next(iter(booking_labels))
        labelled_slots = booking_labels
    if start_pdp_chat and passenger_labels:
        pdp_for_start = dict(context.get("pdp", {}))
        if not pdp_for_start.get("passengers"):
            return {"answer": "Passenger detail fields open after you search and select an outbound flight (and a return flight for a round trip). No passenger fields were changed. Say 'book a flight by chat' to start with the route, dates and passenger counts.", "intent": "passenger_details_prerequisite", "citations": [], "actions": []}
        task = "passenger_details"
        pending_field = next(iter(passenger_labels))
        labelled_slots = passenger_labels
    if start_booking_chat and not booking_labels:
        existing = dict(context.get("workflow") or context.get("assistant_values") or {})
        # The page has prefilled demo defaults. Starting the guided chat must still ask
        # every field explicitly; defaults are not evidence of what the passenger chose.
        existing["_chat_started"] = True
        existing["_chat_completed_fields"] = []
        pending = _booking_chat_next_field(existing)
        return {"answer": "I'll guide the full flight-search form here in chat, including departure/return dates, adults, children, infants, senior citizens and cabin class. For each field, enter a value or say ‘skip’ to keep a saved value. Optional passenger counts may be skipped as zero only when there is no conflicting saved count. " + FIELD_QUESTIONS[pending or "origin"], "intent": "slot_prompt", "assistant_task": "booking", "assistant_values": existing, "pending_field": pending or "origin", "citations": [], "actions": [{"type": "NAVIGATE", "target": "book"}]}
    if (start_pdp_chat and not passenger_labels) or (task == "passenger_details" and any(x in lower for x in ("what next", "what is missing", "what's missing", "continue")) and not labelled_slots):
        try:
            pdp_data = dict(context.get("pdp", {}))
            pax = list(pdp_data.get("passengers", []))
            if not pax:
                return {"answer": "Passenger detail fields open after you search and select an outbound flight (and a return flight for a round trip). Say 'book a flight by chat' to start from the first booking field, or choose a flight in the main form first.", "intent": "passenger_details_prerequisite", "citations": [], "actions": []}
            current_contact = dict(pdp_data.get("contact", {}))
            field, passenger_index = next_booking_field(pax, current_contact, bool(pdp_data.get("accepted_terms", False)), (context.get("workflow") or {}).get("departure_date"))
            if field:
                label = f" for passenger {passenger_index + 1}" if passenger_index is not None else ""
                return {"answer": f"{FIELD_QUESTIONS[field]}{label}", "intent": "slot_prompt", "assistant_task": "passenger_details", "pending_field": field, "pending_passenger_index": passenger_index, "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "passengers"}]}
            return {"answer": "The required passenger names, contact email, phone and demo consent are complete. I can move to extras now, or you can say “modify [field]” to change a value. Confirm to continue; no booking is submitted by this message.", "intent": "pdp_complete", "assistant_task": "passenger_details", "pending_field": None, "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "extras"}]}
        except Exception:
            return {"answer": "I couldn't read the current passenger form schema. Open Passenger details and try again; I won't guess or overwrite fields.", "intent": "pdp_context_error", "citations": [], "actions": []}
    if task == "passenger_details" and lower in {"continue to extras", "continue", "next step", "go to extras", "skip to extras"}:
        return {"answer": "Opening the optional extras step. Review it before moving to payment; no purchase is made by selecting this step.", "intent": "booking_step", "assistant_task": None, "pending_field": None, "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "extras"}]}
    if task == "booking" and lower in {"search flights", "search", "find flights", "show flights", "go", "proceed", "continue", "go ahead", "proceed to search", "find me flights", "book ticket", "book ticket now"}:
        wf = dict(context.get("workflow", {}))
        if not wf.get("origin") or not wf.get("destination") or not wf.get("departure_date"):
            return {"answer": "I still need an origin, destination and future departure date before searching. Which field would you like to complete next?", "intent": "slot_prompt", "assistant_task": "booking", "pending_field": next((f for f in ("origin", "destination", "departure_date") if not wf.get(f)), "origin"), "citations": [], "actions": []}
        if wf.get("trip_type") == "round_trip" and (not wf.get("return_date") or str(wf["return_date"]) <= str(wf["departure_date"])):
            return {"answer": "The return date must be after departure. " + FIELD_QUESTIONS["return_date"], "intent": "slot_prompt", "assistant_task": "booking", "pending_field": "return_date", "citations": [], "actions": []}
        if not int(wf.get("adults", 0)) or int(wf.get("adults", 0)) + int(wf.get("children", 0)) + int(wf.get("infants", 0)) + int(wf.get("senior_citizens", 0)) > 9 or int(wf.get("infants", 0)) > int(wf.get("adults", 0)):
            return {"answer": "The passenger counts do not match the booking schema: at least one adult is required, there may be no more than 9 passengers, and infants cannot outnumber adults. Please update the counts on the form.", "intent": "slot_prompt", "assistant_task": "booking", "pending_field": "adults", "citations": [], "actions": []}
        return {"answer": "Searching the synthetic schedule using the values shown in the booking form.", "intent": "run_flight_search", "assistant_task": "booking", "pending_field": None, "citations": [], "actions": [{"type": "RUN_FLIGHT_SEARCH"}]}
    # Select a listed synthetic flight and fare through chat; the UI still shows the itinerary.
    flight_options = context.get("flight_options") or []
    flight_leg = str(context.get("flight_leg") or page or "outbound")
    fare_number = context.get("pending_flight_index")
    if task == "flight_fare" and pending_field == "fare_family" and flight_options:
        try:
            idx = int(fare_number or 0)
            flight = flight_options[idx]
            fares = flight.get("fare_options", [])
            query = lower.replace("-", " ").strip()
            fare = next((f for f in fares if str(f.get("code", "")).casefold() in lower or str(f.get("label", "")).casefold() in lower), None)
            if not fare:
                fare = next((f for f in fares if any(word in query for word in str(f.get("label", "")).casefold().split() if len(word) > 3)), None)
            if not fare:
                choices = ", ".join(str(f.get("label") or f.get("code")) for f in fares)
                return {"answer": "I haven't selected a fare yet. Choose one of these fares for that flight: " + choices + ". Which one should I use?", "intent": "slot_retry", "assistant_task": task, "pending_field": "fare_family", "pending_flight_index": idx, "citations": [], "actions": []}
            return {"answer": "Selected flight option " + str(idx + 1) + " with the " + str(fare.get("label") or fare.get("code")) + " synthetic fare. Review the itinerary on the page.", "intent": "flight_selected", "assistant_task": None, "pending_field": None, "citations": [], "actions": [{"type": "SELECT_FLIGHT", "leg": flight_leg, "flight_id": flight.get("flight_id"), "fare_code": fare.get("code")}]}
        except (IndexError, TypeError, ValueError):
            return {"answer": "I lost the selected flight context. Please choose a flight from the visible options again.", "intent": "slot_retry", "assistant_task": None, "pending_field": None, "citations": [], "actions": []}
    if page in {"outbound", "return"} and flight_options and not pending_field:
        choice = re.search(r"\b(?:choose|select|book|take|pick)\s+(?:flight|option|number)?\s*#?(\d+)\b", lower)
        if choice:
            idx = int(choice.group(1)) - 1
            if idx < 0 or idx >= len(flight_options):
                return {"answer": "That option number is outside the displayed list. Please choose a number from 1 to " + str(len(flight_options)) + ".", "intent": "slot_retry", "citations": [], "actions": []}
            flight = flight_options[idx]
            fares = flight.get("fare_options", [])
            fare = next((f for f in fares if str(f.get("code", "")).casefold() in lower or str(f.get("label", "")).casefold() in lower), None)
            if not fare:
                choices = ", ".join(str(f.get("label") or f.get("code")) for f in fares)
                return {"answer": "Flight option " + str(idx + 1) + " is available. Which fare should I choose? " + choices, "intent": "slot_prompt", "assistant_task": "flight_fare", "pending_field": "fare_family", "pending_flight_index": idx, "citations": [], "actions": []}
            return {"answer": "Selected flight option " + str(idx + 1) + " with the " + str(fare.get("label") or fare.get("code")) + " synthetic fare.", "intent": "flight_selected", "citations": [], "actions": [{"type": "SELECT_FLIGHT", "leg": page, "flight_id": flight.get("flight_id"), "fare_code": fare.get("code")}]}
    if page == "extras" and any(term in lower for term in ("skip this passenger", "skip passenger extras", "skip this passenger's extras", "skip this passengers extras")):
        page_context = dict(context.get("page_context") or {})
        index = max(0, int(page_context.get("active_extras_passenger") or 0))
        count = len(page_context.get("passengers") or [])
        target = "seats" if not count or index + 1 >= count else "extras"
        return {"answer": ("Skipped optional add-ons for this passenger only. " + ("All passengers are reviewed; opening seat selection." if target == "seats" else f"Moving to passenger {index + 2} of {count} for add-on selection.")), "intent": "extras_passenger_skipped", "citations": [], "actions": [{"type": "SKIP_EXTRAS_PASSENGER", "passenger_index": index}, {"type": "NAVIGATE_FLOW_STEP", "target": target}]}
    if page == "extras" and any(term in lower for term in ("confirm passenger extras", "confirm these extras", "next passenger", "confirm add-ons", "confirm addons")):
        page_context = dict(context.get("page_context") or {})
        index = max(0, int(page_context.get("active_extras_passenger") or 0))
        count = len(page_context.get("passengers") or [])
        target = "seats" if not count or index + 1 >= count else "extras"
        return {"answer": ("Confirmed the visible optional add-ons for this passenger. " + ("All passengers have been reviewed. Opening seat selection." if target == "seats" else f"Now selecting add-ons for passenger {index + 2} of {count}.")), "intent": "extras_passenger_confirmed", "citations": [], "actions": [{"type": "ADVANCE_EXTRAS_PASSENGER", "passenger_index": index}, {"type": "NAVIGATE_FLOW_STEP", "target": target}]}
    if page == "extras" and any(term in lower for term in ("continue to payment", "go to payment", "skip extras", "no extras", "continue", "continue to seat selection")):
        return {"answer": "Opening seat selection. Optional add-ons remain passenger-specific as saved. Choose a seat or skip a specific passenger/whole seat selection. No purchase is made.", "intent": "booking_step", "citations": [], "actions": [{"type": "NAVIGATE_FLOW_STEP", "target": "seats"}]}
    if page == "extras":
        codes = []
        if "bag" in lower or "baggage" in lower: codes.append("EXTRA_BAG")
        if "wifi" in lower or "wi-fi" in lower: codes.append("WIFI")
        if "lounge" in lower: codes.append("LOUNGE")
        if "meal" in lower: codes.append("MEAL")
        if codes:
            removing = any(term in lower for term in ("remove", "delete", "unselect", "deselect", "take off", "don't want", "do not want"))
            action_type = "REMOVE_EXTRAS" if removing else "TOGGLE_EXTRAS"
            verb = "Removed from" if removing else "Updated in"
            page_context = dict(context.get("page_context") or {})
            passenger_index = max(0, int(page_context.get("active_extras_passenger") or 0))
            passenger_name = (page_context.get("passengers") or [{}] * (passenger_index + 1))[passenger_index].get("first_name") or f"Passenger {passenger_index + 1}"
            return {"answer": verb + f" the selected optional demo extras for {passenger_name}: " + ", ".join(codes) + ". Confirm this passenger's choices to move to the next passenger, or say ‘skip this passenger’.", "intent": "extras_removed" if removing else "extras_selected", "citations": [], "actions": [{"type": action_type, "codes": codes, "passenger_index": passenger_index}]}
    if page == "seats":
        page_context = dict(context.get("page_context") or {})
        seat_match = re.search(r"\b(?:select|choose|pick|take|seat)\s+(?:seat\s+)?((?:[1-9]|[1-3][0-9]|40)[A-F])\b", message, re.I)
        if any(term in lower for term in ("skip this passenger seat", "skip this passenger's seat", "skip passenger seat", "skip this seat")):
            index = max(0, min(8, int(page_context.get("active_seat_passenger") or 0)))
            leg = str(page_context.get("active_seat_leg") or "outbound").lower()
            return {"answer": f"Seat selection is skipped for passenger {index + 1} on the {leg} journey only. I'll move focus to the next passenger/journey pair; all earlier eligibility and document checks remain required.", "intent": "booking_seat_skipped", "citations": [], "actions": [{"type": "SKIP_BOOKING_SEAT", "passenger_index": index, "leg": leg}]}
        if any(term in lower for term in ("confirm seat", "next passenger", "confirm and next")) and not seat_match:
            index = max(0, min(8, int(page_context.get("active_seat_passenger") or 0)))
            leg = str(page_context.get("active_seat_leg") or "outbound").lower()
            selected = dict(page_context.get("selected_seats") or {})
            if not selected.get(f"{leg}:{index}"):
                return {"answer": "Choose a seat from the visible map or skip this passenger's seat first. I won't advance an unconfirmed seat.", "intent": "booking_seat_incomplete", "citations": [], "actions": []}
            return {"answer": "Seat confirmed for this passenger. Moving to the next passenger/journey pair.", "intent": "booking_seat_confirmed", "citations": [], "actions": [{"type": "NEXT_BOOKING_SEAT", "passenger_index": index, "leg": leg}]}
        if seat_match:
            seat_code = seat_match.group(1).upper()
            leg = str(page_context.get("active_seat_leg") or "outbound").lower()
            if leg not in {"outbound", "return"}:
                leg = "outbound"
            passenger_index = max(0, min(8, int(page_context.get("active_seat_passenger") or 0)))
            return {"answer": f"I will select mock seat {seat_code} for passenger {passenger_index + 1} on the {leg} journey. The page will confirm availability and update the same seat map.", "intent": "booking_seat_selection", "citations": [], "actions": [{"type": "SELECT_BOOKING_SEAT", "seat_code": seat_code, "passenger_index": passenger_index, "leg": leg}]}
        if any(term in lower for term in ("skip seat selection", "skip all seats", "no seats", "continue to payment", "go to payment", "continue")):
            selected = dict(page_context.get("selected_seats") or {})
            passenger_count = len(page_context.get("passengers") or [])
            trip_type = str((context.get("workflow") or {}).get("trip_type") or "one_way")
            required = passenger_count * (2 if trip_type == "round_trip" else 1)
            if selected and passenger_count and len(selected) < required and not any(term in lower for term in ("skip seat", "skip seats", "no seat")):
                return {"answer": "Some but not all requested seats are selected. Choose seats for the remaining passenger/journey pairs, or explicitly say ‘skip seat selection’ to clear the partial selection and continue.", "intent": "booking_seat_incomplete", "citations": [], "actions": []}
            actions = [{"type": "CLEAR_BOOKING_SEATS"}] if any(term in lower for term in ("skip seat", "skip seats", "no seat")) else []
            actions.append({"type": "NAVIGATE_FLOW_STEP", "target": "payment"})
            return {"answer": "Continuing to the demo payment step. " + ("Seat assignments have been skipped; no seat was reserved." if actions and actions[0]["type"] == "CLEAR_BOOKING_SEATS" else "The current seat selections stay visible for review.") + " No payment will be processed.", "intent": "booking_step", "citations": [], "actions": actions}
        return {"answer": "Choose a seat from the visible mock seat map—for example, ‘select seat 1A’—or say ‘skip seat selection’ to continue without seats.", "intent": "seat_selection_help", "citations": [], "actions": []}

    if page == "payment" and any(term in lower for term in ("choose upi", "upi", "qr", "choose card", "credit card", "debit card", "net banking", "netbanking", "wallet")):
        if "net banking" in lower or "netbanking" in lower:
            method = "netbanking"
        elif "wallet" in lower:
            method = "wallet"
        elif "card" in lower:
            method = "card"
        else:
            method = "upi"
        label = {"upi": "UPI / QR", "card": "credit or debit card", "netbanking": "net banking", "wallet": "digital wallet"}[method]
        return {"answer": f"Selected {label} as the visible demo payment method. No payment details are requested and no payment provider is contacted.", "intent": "payment_method_selected", "citations": [], "actions": [{"type": "SET_PAYMENT_METHOD", "value": method}]}

    if page == "payment" and ("simulate" in lower or ("payment" in lower and any(x in lower for x in ("success", "approve", "decline", "fail")))):
        simulation = "decline" if any(x in lower for x in ("decline", "fail")) else "approve"
        return {"answer": "Set the mock payment outcome to " + ("declined" if simulation == "decline" else "successful") + ". No payment is attempted. Say 'confirm demo booking' only if you want to submit this synthetic booking test.", "intent": "payment_simulation", "citations": [], "actions": [{"type": "SET_PAYMENT_SIMULATION", "value": simulation}]}
    if page == "payment" and any(x in lower for x in ("confirm demo booking", "confirm this demo booking")):
        return {"answer": "Submitting the explicitly requested synthetic booking confirmation. The payment outcome is simulated and no real money moves.", "intent": "booking_confirmation", "citations": [], "actions": [{"type": "CONFIRM_DEMO_BOOKING"}]}
    faq_interrupt = bool(re.match(r"^(?:what|why|how|can i|could i|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", lower)) or (message.rstrip().endswith("?") and any(t in lower for t in ("baggage", "luggage", "refund", "check-in", "check in", "visa", "passport", "flight status", "power bank", "wifi", "seat", "wheelchair", "lounge", "meal", "privacy", "motto", "tagline")))
    if pending_field and task and not labelled_slots and faq_interrupt:
        faq = site_rag.answer(message)
        return {"answer": faq.get("answer", "I couldn't verify that from the approved sources."), "intent": faq.get("intent", "faq"), "assistant_task": task, "pending_field": pending_field, "pending_passenger_index": context.get("pending_passenger_index"), "citations": faq.get("citations", []), "actions": [], "retrieval_trace": faq.get("retrieval_trace")}
    if task == "passenger_details" and labelled_slots and not (context.get("pdp", {}) or {}).get("passengers"):
        return {"answer": "There are no passenger rows in this form yet. Search and select a flight first; no fields were changed.", "intent": "passenger_details_prerequisite", "assistant_task": task, "pending_field": pending_field or None, "citations": [], "actions": []}
    if task and (pending_field or labelled_slots):
        try:
            pdp_data = dict(context.get("pdp", {})) if task == "passenger_details" else {}
            passenger_index = context.get("pending_passenger_index")
            old_passenger = (pdp_data.get("passengers") or [{}])[int(passenger_index or 0)] if task == "passenger_details" and pdp_data.get("passengers") else {}
            if labelled_slots:
                updates, normalized, parsed = {}, {}, {}
                parse_context = dict(old_passenger)
                if task == "booking":
                    parse_context.update(dict(context.get("workflow") or context.get("assistant_values") or {}))
                for field_name, field_value in labelled_slots.items():
                    if field_name in {"origin", "destination"}:
                        matches = airport_index.search(field_value, limit=6).get("results", [])
                        exact = [a for a in matches if str(a.get("iata", "")).casefold() == field_value.casefold() or str(a.get("city", "")).casefold() == field_value.casefold()]
                        airport = exact[0] if exact else (matches[0] if len(matches) == 1 else None)
                        if not airport:
                            raise FieldParseError(f"I couldn't resolve the labelled {field_name.replace('_', ' ')} value to one airport. Use an IATA code or a more specific city/airport name.")
                        updates[field_name] = airport
                        normalized[field_name] = airport.get("iata")
                        parsed[field_name] = airport.get("iata")
                    else:
                        value_map = parse_field_value(field_name, field_value, parse_context)
                        updates.update(value_map); normalized.update(value_map); parsed.update(value_map)
                        parse_context.update(value_map)
            elif pending_field in {"origin", "destination"}:
                matches = airport_index.search(message, limit=6).get("results", [])
                exact = [a for a in matches if str(a.get("iata", "")).casefold() == lower or str(a.get("city", "")).casefold() == lower]
                airport = exact[0] if exact else (matches[0] if len(matches) == 1 else None)
                if not airport:
                    return {"answer": f"I couldn't resolve '{message}' to a unique airport. Please enter an IATA airport code or a more specific city/airport name.", "intent": "slot_retry", "assistant_task": task, "pending_field": pending_field, "citations": [], "actions": []}
                updates = {pending_field: airport}; normalized = {pending_field: airport.get("iata")}; parsed = dict(normalized)
            elif task == "booking":
                # Conversational shortcuts commonly used by passengers. Merge explicit
                # values into the current task so a route/date utterance never erases prior slots.
                candidate = message.strip()
                parsed = {}
                existing_values = dict(context.get("workflow") or context.get("assistant_values") or {})
                low_candidate = candidate.casefold()
                merged_values, changed_fields = _merge_explicit_booking_values(candidate, existing_values)
                # An unlabelled date answer belongs to the field the assistant just asked for.
                # Otherwise a return-date answer is mistakenly parsed as a departure-date change.
                plain_date_answer = pending_field in {"departure_date", "return_date"} and not re.search(r"\b(?:departure|return|returning|back)\s+date\b", low_candidate)
                if plain_date_answer:
                    normalized = dict(parse_field_value(pending_field, candidate, existing_values))
                    updates = dict(normalized)
                # The merger returns a full snapshot. Only treat fields explicitly supplied
                # in this utterance as new slot values; defaults already in the page must
                # not mark future chatbot questions as answered.
                elif not changed_fields and pending_field:
                    if pending_field in {"origin", "destination"}:
                        matches = airport_index.search(candidate, limit=6).get("results", [])
                        exact = [a for a in matches if str(a.get("iata", "")).casefold() == low_candidate or str(a.get("city", "")).casefold() == low_candidate]
                        airport = exact[0] if exact else (matches[0] if len(matches) == 1 else None)
                        if not airport:
                            raise FieldParseError("I couldn't resolve that to one city or airport. Please use a city or IATA code.")
                        normalized = {pending_field: airport.get("iata")}
                        updates = {pending_field: airport}
                    else:
                        normalized = dict(parse_field_value(pending_field, candidate, existing_values))
                        updates = dict(normalized)
                else:
                    normalized = {key: merged_values[key] for key in changed_fields if key in merged_values}
                    updates = dict(normalized)
            else:
                parsed = parse_field_value(pending_field, message, old_passenger)
                updates = dict(parsed); normalized = dict(parsed)

            if task == "booking":
                wf = dict(context.get("workflow") or context.get("assistant_values") or {}); wf.update(normalized)
                if wf.get("origin") and wf.get("destination") and wf["origin"] == wf["destination"]:
                    raise FieldParseError("Origin and destination must be different airports.")
                count_fields = ("adults", "children", "infants", "senior_citizens")
                total_passengers = sum(int(wf.get(f, 0) or 0) for f in count_fields if wf.get(f) is not None)
                if "adults" in wf and wf.get("adults") is not None and int(wf.get("adults", 0) or 0) < 1:
                    raise FieldParseError("At least one adult is required.")
                if total_passengers > 9:
                    raise FieldParseError("The demo supports at most 9 passengers. Reduce one of the passenger counts.")
                if wf.get("infants") is not None and wf.get("adults") is not None and int(wf.get("infants", 0) or 0) > (int(wf.get("adults", 0) or 0) + int(wf.get("senior_citizens", 0) or 0)):
                    raise FieldParseError("There cannot be more infants than adults. Please correct the infant/adult counts.")
                if wf.get("trip_type") == "round_trip" and wf.get("return_date") and wf.get("departure_date") and str(wf["return_date"]) <= str(wf["departure_date"]):
                    raise FieldParseError("The return date must be after the departure date.")
                edit_mode = bool((context.get("assistant_values") or {}).get("_edit_mode"))
                if wf.get("_chat_started"):
                    completed = set(wf.get("_chat_completed_fields") or [])
                    if pending_field in {"origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"}: completed.add(pending_field)
                    completed.update(k for k in normalized if k in {"origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"})
                    wf["_chat_completed_fields"] = sorted(completed)
                    next_field = _booking_chat_next_field(wf)
                elif edit_mode:
                    edit_order = ["origin", "destination", "trip_type", "departure_date"]
                    if wf.get("trip_type") == "round_trip": edit_order.append("return_date")
                    edit_order += ["adults", "children", "infants", "senior_citizens", "cabin"]
                    try: edit_index = edit_order.index(pending_field)
                    except ValueError: edit_index = -1
                    next_field = edit_order[edit_index + 1] if 0 <= edit_index + 1 < len(edit_order) else None
                else:
                    next_field = _booking_next_field(wf)
                # Labeled zero values are explicit answers; preserve them as completed slots.
                explicit_fields = set(labelled_slots) if labelled_slots else set(normalized)
                if task == "booking" and not edit_mode and not wf.get("_chat_started") and all(k in wf for k in ("origin", "destination", "trip_type", "departure_date", "adults", "cabin")) and (wf.get("trip_type") == "one_way" or wf.get("return_date")):
                    if ("children" in explicit_fields or wf.get("children") == 0) and ("infants" in explicit_fields or wf.get("infants") == 0):
                        next_field = None
                actions = [{"type": "SET_SEARCH_FORM_FIELD", "field": k, "value": v} for k, v in updates.items()]
                if "trip_type" in normalized and normalized["trip_type"] == "one_way":
                    actions.append({"type": "SET_SEARCH_FORM_FIELD", "field": "return_date", "value": None})
                mapping_text = ", ".join(k.replace("_", " ") for k in normalized) or pending_field.replace("_", " ")
                if next_field:
                    return {"answer": f"Mapped your response to {mapping_text}. {FIELD_QUESTIONS[next_field]}", "intent": "slot_filled", "assistant_task": task, "assistant_values": ({**wf, "_edit_mode": True} if edit_mode else wf), "pending_field": next_field, "citations": [], "actions": actions, "card": {"type": "booking", "title": "Flight-search input", "status": "Draft · not booked", "fields": [{"label": k.replace('_', ' '), "value": wf.get(k, 'Not set')} for k in normalized], "note": "This input was applied to the visible form. You can still review or edit it before searching."}}
                return {"answer": "The flight-search fields are complete. Review the current values, then say 'search flights' to find synthetic options. I will not submit a booking until you explicitly confirm it.", "intent": "booking_search_ready", "assistant_task": task, "assistant_values": wf, "pending_field": None, "citations": [], "actions": actions, "card": {"type": "booking", "title": "Flight-search summary", "status": "Ready to search · not booked", "fields": [{"label": k.replace('_', ' '), "value": wf.get(k, 'Not set')} for k in ("origin", "destination", "trip_type", "departure_date", "return_date", "adults", "children", "infants", "senior_citizens", "cabin") if wf.get(k) is not None], "note": "Review and search to see synthetic flight options. This is not a confirmed booking."}}

            if task == "passenger_details":
                pax = [dict(p) for p in pdp_data.get("passengers", [])]
                contact_data = dict(pdp_data.get("contact", {}))
                if "accompanying_adult_index" in parsed:
                    minor_index = int(passenger_index or 0)
                    companion_index = int(parsed["accompanying_adult_index"])
                    travel_day = (context.get("workflow") or {}).get("departure_date")
                    if not 0 <= companion_index < len(pax) or companion_index == minor_index:
                        raise FieldParseError("Choose another passenger in this booking; a traveller cannot accompany themself.")
                    companion = pax[companion_index]
                    if str(companion.get("passenger_type", "ADT")).upper() != "ADT":
                        raise FieldParseError("Only an adult passenger (ADT) can be selected as the companion.")
                    adult_age = age_on_date(companion.get("date_of_birth"), travel_day)
                    if adult_age is None or adult_age < 16:
                        raise FieldParseError("The selected companion must have a valid date of birth and be at least 16 on departure.")
                    target_category = str(pax[minor_index].get("passenger_type", "ADT")).upper()
                    if target_category in {"INF", "INFANT"} and any(i != minor_index and str(p.get("passenger_type", "")).upper() in {"INF", "INFANT"} and p.get("accompanying_adult_index") == companion_index for i, p in enumerate(pax)):
                        raise FieldParseError("This demo permits only one infant per accompanying adult. Choose a different adult passenger.")
                new_accepted = bool(pdp_data.get("accepted_terms", False))
                actions = []
                for field, value in parsed.items():
                    if field in {"contact_email", "contact_phone"}:
                        contact_key = "email" if field == "contact_email" else "phone"
                        contact_data[contact_key] = value
                        actions.append({"type": "UPDATE_CONTACT_FIELD", "field": contact_key, "value": value})
                    elif field == "accepted_terms":
                        new_accepted = bool(value)
                        actions.append({"type": "UPDATE_ACCEPTED_TERMS", "value": new_accepted})
                    else:
                        idx = int(passenger_index or 0)
                        if 0 <= idx < len(pax):
                            pax[idx][field] = value
                            actions.append({"type": "UPDATE_PASSENGER_FIELD", "field": field, "passenger_index": idx, "value": value})
                edit_pdp_mode = bool((context.get("assistant_values") or {}).get("_pdp_edit_mode"))
                if edit_pdp_mode:
                    ordered = booking_pdp_field_order(pax, (context.get("workflow") or {}).get("departure_date"))
                    needle = (pending_field, passenger_index if pending_field not in {"contact_email", "contact_phone", "accepted_terms"} else None)
                    try: edit_index = ordered.index(needle)
                    except ValueError: edit_index = -1
                    next_pair = ordered[edit_index + 1] if 0 <= edit_index + 1 < len(ordered) else None
                    next_field, next_index = next_pair if next_pair else (None, None)
                else:
                    next_field, next_index = next_booking_field(pax, contact_data, new_accepted, (context.get("workflow") or {}).get("departure_date"))
                if next_field:
                    label = f" for passenger {next_index + 1}" if next_index is not None else ""
                    safe_answer = f"Mapped your response to {', '.join(k.replace('_', ' ') for k in parsed)}. I won't repeat contact details in chat. {FIELD_QUESTIONS[next_field]}{label}"
                    return {"answer": safe_answer, "intent": "slot_filled", "assistant_task": task, "assistant_values": ({"_pdp_edit_mode": True} if edit_pdp_mode else {}), "pending_field": next_field, "pending_passenger_index": next_index, "citations": [], "actions": actions, "card": {"type": "passenger", "title": "Passenger details updated", "status": "Draft · not booked", "fields": [{"label": k.replace('_', ' '), "value": ('Saved in the visible form' if k in {"contact_email", "contact_phone"} else str(v))} for k, v in parsed.items()], "note": "The visible form has been updated. Review every passenger and contact field before proceeding."}}
                return {"answer": "The required passenger names, contact email, phone and consent fields are complete. Say 'continue to extras' when you're ready. I have not submitted the booking.", "intent": "pdp_complete", "assistant_task": task, "pending_field": None, "citations": [], "actions": actions}
        except FieldParseError as exc:
            question = FIELD_QUESTIONS.get(pending_field, "Please provide the requested field in the expected format.")
            return {"answer": f"I couldn't validate that response against the booking schema: {exc}\n\n{question} You can supply multiple labelled fields in one line; I will validate each field before applying any change.", "intent": "slot_retry", "assistant_task": task, "pending_field": pending_field, "citations": [], "actions": []}
    if page in {"passengers", "passenger-details", "pdp"} and pdp_help_intent:
        try:
            pdp = resolve_pdp_next_step(PDPContextRequest.model_validate(context.get("pdp", {})))
            if not pdp["complete"]:
                return {"answer": pdp["prompt"], "intent": "pdp_context", "citations": [], "actions": [{"type": "PDP_FIELD_GUIDANCE", "step": pdp["step"], "field": pdp["field"], "passenger_index": pdp.get("passenger_index")}], "pdp_context": pdp}
        except Exception:
            pass
    # Keep transactional chat commands explicit: the assistant can fill forms, not silently book or charge.
    # Prefer the live chat task/context to a one-off route shortcut. This keeps the
    # pending field and visible booking form synchronized across turns.
    route_match = re.search(r"\bfrom\s+([a-zA-Z .'-]{2,45}?)\s+to\s+([a-zA-Z .'-]{2,45}?)(?:\s+(?:on|for|next|this|tomorrow|today)|$)", message, re.I)
    if route_match:
        origin_query, destination_query = route_match.group(1).strip(), route_match.group(2).strip()
        origin_results = airport_index.search(origin_query, limit=6).get("results", [])
        dest_results = airport_index.search(destination_query, limit=6).get("results", [])
        origin = next((a for a in origin_results if a.get("city", "").casefold() == origin_query.casefold() or a.get("iata", "").casefold() == origin_query.casefold()), origin_results[0] if len(origin_results) == 1 else None)
        destination = next((a for a in dest_results if a.get("city", "").casefold() == destination_query.casefold() or a.get("iata", "").casefold() == destination_query.casefold()), dest_results[0] if len(dest_results) == 1 else None)
        if origin and destination and origin.get("iata") != destination.get("iata"):
            route_actions = [{"type": "FILL_FLIGHT_SEARCH", "origin": origin["iata"], "destination": destination["iata"]}]
            if task == "booking":
                wf = dict(context.get("workflow") or context.get("assistant_values") or {})
                wf.update({"origin": origin["iata"], "destination": destination["iata"]})
                order = ["departure_date", "trip_type", "return_date", "adults", "children", "infants", "senior_citizens", "cabin"]
                next_field = next((field for field in order if wf.get(field) in (None, "", {}) and not (field == "return_date" and wf.get("trip_type") == "one_way")), "departure_date")
                if wf.get("trip_type") == "one_way":
                    next_field = next((field for field in ("departure_date", "adults", "children", "infants", "senior_citizens", "cabin") if wf.get(field) in (None, "", {})), None)
                answer = f"I filled {origin['city']} ({origin['iata']}) to {destination['city']} ({destination['iata']}) into the same booking form. "
                if next_field:
                    answer += FIELD_QUESTIONS[next_field] + " You can also say ‘one way tomorrow’ or provide the fields together."
                else:
                    answer += "The route and dates are ready. Say ‘search flights’ or ‘proceed’ to search the synthetic schedule."
                return {"answer": answer, "intent": "slot_filled", "assistant_task": "booking", "assistant_values": wf, "pending_field": next_field, "citations": [], "actions": route_actions + [{"type": "SET_SEARCH_FORM_FIELD", "field": "origin", "value": origin["iata"]}, {"type": "SET_SEARCH_FORM_FIELD", "field": "destination", "value": destination["iata"]}]}
            return {"answer": f"I found {origin['city']} ({origin['iata']}) to {destination['city']} ({destination['iata']}) and filled the same form. Choose dates and passenger counts, then ask me to search.", "intent": "flight_search", "citations": [], "actions": route_actions}

    if page == "manage":
        manage_context = dict((context.get("page_context") or {}).get("manage") or {})
        match = re.match(r"^\s*(?:(?:lookup|find|retrieve|show|open)\s+(?:my\s+)?booking\s+)?([A-Z0-9]{6,8})[\s,]+([A-Z][A-Z'-]{1,63})\s*$", message, re.I)
        if match:
            return {"answer": "I will retrieve that synthetic booking with the same booking-lookup endpoint used by the Manage Booking form. The result will be shown on the page.", "intent": "manage_booking_lookup", "citations": [], "actions": [{"type": "LOOKUP_MANAGE_BOOKING", "record_locator": match.group(1).upper(), "last_name": match.group(2).upper()}]}
        if any(term in lower for term in ("retrieve my booking", "lookup my booking", "look up my booking", "show my booking", "find my booking")):
            pnr = str(manage_context.get("pnr") or "").strip().upper()
            surname = str(manage_context.get("surname") or "").strip().upper()
            if pnr and surname:
                return {"answer": "I will look up the booking reference and surname currently entered in the Manage Booking page.", "intent": "manage_booking_lookup", "citations": [], "actions": [{"type": "LOOKUP_MANAGE_BOOKING", "record_locator": pnr, "last_name": surname}]}
            return {"answer": "Enter the booking reference and surname in the page form, or send them together as ‘lookup booking ABC123 SMITH’. I will use the same lookup service as the form.", "intent": "manage_booking_lookup_prompt", "citations": [], "actions": []}

    manage_navigation_intent = any(term in lower for term in ("open manage booking", "go to manage booking", "take me to manage booking", "open my booking", "retrieve my booking", "show my booking form"))
    checkin_navigation_intent = any(term in lower for term in ("open check-in", "open check in", "go to check-in", "go to check in", "take me to check-in", "take me to check in", "start check-in", "start check in", "launch check-in", "launch check in", "check me in"))
    if manage_navigation_intent or checkin_navigation_intent:
        target = "manage" if manage_navigation_intent else "checkin"
        return {"answer": "Use the main navigation to open the requested section. Enter your synthetic booking reference and surname when asked; I won't infer or disclose booking details in chat.", "intent": "booking_navigation", "citations": [], "actions": [{"type": "NAVIGATE", "target": target}]}
    try:
        answer = site_rag.answer(message)
    except Exception as exc:
        logger.warning("Site FAQ retrieval failed; local corpus recovery is available correlation-free error_type=%s", type(exc).__name__)
        try:
            from app.rag_agentic import SelfCorrectiveRAG as LocalFAQ
            answer = LocalFAQ().answer(message)
        except Exception:
            answer = {"answer": "The local aviation knowledge search could not verify a source for this question. Please retry or consult the cited official site. Booking and check-in actions remain available.", "intent": "faq_abstain", "citations": [], "retrieval_trace": {"stages": [{"stage": "recovery", "status": "unavailable"}]}}
    return {"answer": answer.get("answer", "I couldn't find an approved source for that question."), "intent": answer.get("intent", "faq"), "citations": answer.get("citations", []), "actions": [], "retrieval_trace": answer.get("retrieval_trace"), "mock_only": True}

def _require_provider_diagnostics(x_provider_diagnostics_token: str = Header(default="", alias="X-Provider-Diagnostics-Token")) -> None:
    """Gate provider probes: disabled by default, never exposed in production, token-protected locally."""
    if settings.app_env.lower() == "production" or not settings.enable_provider_diagnostics:
        raise HTTPException(status_code=404, detail="Provider diagnostic endpoints are disabled")
    expected = settings.provider_diagnostics_token
    if not expected:
        raise HTTPException(status_code=503, detail="Set PROVIDER_DIAGNOSTICS_TOKEN to enable protected local provider tests")
    if not x_provider_diagnostics_token or not hmac.compare_digest(x_provider_diagnostics_token, expected):
        raise HTTPException(status_code=401, detail="Provider diagnostics token is invalid")


@lru_cache(maxsize=1)
def _timatic_diagnostics_adapter() -> TimaticStagingDiagnosticsAdapter:
    if not settings.timatic_username or not settings.timatic_password:
        raise HTTPException(status_code=503, detail="Configure TIMATIC_USERNAME and TIMATIC_PASSWORD with the authorized staging client credentials")
    return TimaticStagingDiagnosticsAdapter(
        token_url=settings.timatic_token_url or DEFAULT_TIMATIC_TOKEN_URL,
        api_base_url=settings.timatic_base_url or DEFAULT_TIMATIC_API_BASE,
        username=settings.timatic_username,
        password=settings.timatic_password,
        grant_type=settings.timatic_grant_type or "client_credentials",
        user_key=settings.timatic_user_key,
        timeout_seconds=settings.timatic_timeout_seconds,
    )


@lru_cache(maxsize=1)
def _navitaire_diagnostics_adapter() -> NavitaireLiveDiagnosticsAdapter:
    if not settings.navitaire_base_url or not settings.navitaire_agent_token or not settings.navitaire_user_key:
        raise HTTPException(status_code=503, detail="Configure NAVITAIRE_BASE_URL, NAVITAIRE_AGENT_TOKEN and NAVITAIRE_USER_KEY with matching authorized sandbox credentials")
    return NavitaireLiveDiagnosticsAdapter(
        base_url=settings.navitaire_base_url,
        agent_token=settings.navitaire_agent_token,
        user_key=settings.navitaire_user_key,
        api_key=settings.navitaire_api_key,
        api_key_header=settings.navitaire_api_key_header,
        auth_scheme=settings.navitaire_auth_scheme,
        booking_path=settings.navitaire_booking_path,
        booking_state_path=settings.navitaire_booking_state_path,
        checkin_requirements_path=settings.navitaire_checkin_requirements_path,
        document_check_path=settings.navitaire_document_check_path,
        graphql_query=settings.navitaire_graphql_query,
        timeout_seconds=settings.timatic_timeout_seconds,
    )


@app.post("/api/v1/provider-tests/navitaire/booking-retrieve")
async def provider_test_navitaire_booking(
    body: NavitaireBookingRetrieveTest,
    _: None = Depends(_require_provider_diagnostics),
) -> dict[str, Any]:
    """Read-only New Skies booking-retrieval probe. Does not create a check-in session."""
    try:
        adapter = _navitaire_diagnostics_adapter()
        return await adapter.booking_retrieve(body.record_locator, body.last_name)
    except ProviderAPIError as exc:
        logger.warning("Navitaire diagnostic failed category=%s status=%s", exc.category, exc.http_status)
        raise HTTPException(status_code=502, detail={"provider": "Navitaire", "category": exc.category, "http_status": exc.http_status}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Navitaire diagnostic configuration is incomplete or invalid") from exc


@app.post("/api/v1/provider-tests/workflows/document-validation")
async def provider_test_reference_document_workflow(
    body: NavitaireBookingRetrieveTest,
    _: None = Depends(_require_provider_diagnostics),
) -> dict[str, Any]:
    """Run the reference-style read-only Navitaire -> Timatic -> Navitaire document-check flow.

    This is a protected non-production diagnostic. It does not commit a seat, update documents,
    issue a boarding pass or authorize real check-in. All provider outputs are redacted before return.
    """
    if not settings.navitaire_allowed_carrier or not settings.timatic_processing_entity:
        raise HTTPException(
            status_code=503,
            detail="Set NAVITAIRE_ALLOWED_CARRIER and TIMATIC_PROCESSING_ENTITY from the same authorized provider environment before running the reference workflow",
        )
    try:
        # Adapter construction belongs inside the guarded block: malformed/missing provider
        # configuration must return a controlled error, not an unhandled 500 response.
        nav_adapter = _navitaire_diagnostics_adapter()
        timatic_adapter = _timatic_diagnostics_adapter()
        raw_booking = await nav_adapter.get_booking_for_validation(body.record_locator, body.last_name)
        booking = normalize_navitaire_booking(
            raw_booking,
            record_locator=body.record_locator,
            last_name=body.last_name,
        )

        async def check_requirements(journey: dict[str, Any]) -> tuple[bool, str]:
            try:
                requirements = await nav_adapter.get_checkin_requirements(str(journey.get("journey_key", "")))
            except (ProviderAPIError, ValueError):
                return False, "Navitaire check-in requirements could not be retrieved; validation is blocked."
            return evaluate_navitaire_ssr_requirements(requirements)

        requirement_results = await asyncio.gather(*(check_requirements(journey) for journey in booking.get("journeys", [])))
        booking["ssr_requirements_valid"] = bool(requirement_results) and all(ok for ok, _ in requirement_results)
        booking["ssr_requirements_reason"] = "; ".join(reason for _, reason in requirement_results if reason)[:800]

        eligibility = await run_eligibility_concurrently(
            booking,
            carrier_mode="provider_reference",
            window_profile="reference",
            provider_allowed_carrier=settings.navitaire_allowed_carrier,
        )
        eligibility_summary = {
            "decision": str(eligibility.decision),
            "flight_type": eligibility.flight_type,
            "rules": [
                {"rule_id": rule.rule_id, "decision": str(rule.decision), "message": rule.message}
                for rule in eligibility.rules
            ],
            "policy_notice": "Reference-derived prechecks only; not an official airline decision and not an Emirates-approved policy.",
        }
        if str(eligibility.decision) != "PASS":
            return {
                "provider": "NAVITAIRE+TIMATIC_STAGING",
                "decision": "BLOCKED_BY_BOOKING_PRECHECK",
                "booking_precheck": eligibility_summary,
                "checkin_requirements_journeys": len(requirement_results),
                "timatic_requests_attempted": 0,
                "document_results": [],
                "mock_only": False,
                "boarding_pass_issued": False,
                "message": "Reference-derived booking/SSR prechecks did not pass. Timatic was not called; review the reported rule IDs and provider contract.",
            }

        documents_by_passenger = {
            str(passenger.get("passenger_key", "")): passenger.get("travel_documents", [])
            for passenger in booking.get("passengers", [])
        }

        async def timatic_validator(
            current_booking: dict[str, Any],
            passenger: dict[str, Any],
            journey: dict[str, Any],
            documents: dict[str, Any] | list[dict[str, Any]] | None,
        ) -> dict[str, Any]:
            payload = build_timatic_request(
                current_booking,
                passenger,
                journey,
                documents or [],
                processing_entity=settings.timatic_processing_entity,
                live_contract=True,
            )
            return await timatic_adapter.evaluate_document_request(payload)

        workflow = ReferenceStyleDocumentValidationWorkflow(
            timatic_validator=timatic_validator,
            navitaire_document_check=nav_adapter.document_check,
            concurrency=6,
            mock_only=False,
            provider_label="NAVITAIRE+TIMATIC_STAGING",
        )
        validation = await workflow.validate(booking, documents_by_passenger)
        passenger_index = {str(p.get("passenger_key", "")): index + 1 for index, p in enumerate(booking.get("passengers", []))}
        journey_index = {str(j.get("journey_key", "")): index + 1 for index, j in enumerate(booking.get("journeys", []))}
        safe_results = []
        for result in validation.get("results", []):
            safe_results.append({
                "passenger_index": passenger_index.get(str(result.get("passenger_key", ""))),
                "journey_index": journey_index.get(str(result.get("journey_key", ""))),
                "timatic_decision": result.get("timatic_decision", "NOT_RUN"),
                "navitaire_decision": result.get("navitaire_decision", "NOT_RUN"),
                "decision": result.get("decision", "REVIEW_REQUIRED"),
                "evaluation_code": result.get("evaluation_code", "UNMAPPED_PROVIDER_RESPONSE"),
                "reason_count": len(result.get("reasons", [])),
            })
        return {
            "provider": "NAVITAIRE+TIMATIC_STAGING",
            "decision": validation.get("decision", "REVIEW_REQUIRED"),
            "booking_precheck": eligibility_summary,
            "checkin_requirements_journeys": len(requirement_results),
            "timatic_requests_attempted": sum(1 for result in validation.get("results", []) if result.get("timatic_decision") != "NOT_RUN"),
            "counts": validation.get("counts", {}),
            "document_results": safe_results,
            "mock_only": False,
            "boarding_pass_issued": False,
            "message": "Provider test only: Timatic is evaluated per passenger and journey; Navitaire document-check runs only after explicit Timatic YES. No check-in mutation or boarding pass was performed.",
        }
    except ProviderAPIError as exc:
        logger.warning("Reference provider workflow failed provider=%s category=%s status=%s", exc.provider, exc.category, exc.http_status)
        raise HTTPException(status_code=502, detail={"provider": exc.provider, "category": exc.category, "http_status": exc.http_status}) from exc
    except ValueError as exc:
        logger.warning("Reference provider workflow input rejected category=%s", type(exc).__name__)
        raise HTTPException(status_code=422, detail="Provider response or request did not satisfy the validated workflow contract; check-in remains blocked") from exc


@app.get("/api/v1/provider-tests/timatic/country")
async def provider_test_timatic_country(
    code: str,
    name: str,
    _: None = Depends(_require_provider_diagnostics),
) -> dict[str, Any]:
    """Test the Timatic country reference endpoint using staging credentials."""
    adapter = _timatic_diagnostics_adapter()
    try:
        return await adapter.country_lookup(code, name)
    except ProviderAPIError as exc:
        logger.warning("Timatic country diagnostic failed category=%s status=%s", exc.category, exc.http_status)
        raise HTTPException(status_code=502, detail={"provider": "Timatic", "category": exc.category, "http_status": exc.http_status}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Timatic country lookup parameters are invalid") from exc


@app.get("/api/v1/provider-tests/timatic/parameters")
async def provider_test_timatic_parameters(
    code: str,
    _: None = Depends(_require_provider_diagnostics),
) -> dict[str, Any]:
    """Test Timatic parameter metadata lookup. Returns schema only, not raw body values."""
    adapter = _timatic_diagnostics_adapter()
    try:
        return await adapter.parameter_lookup(code)
    except ProviderAPIError as exc:
        logger.warning("Timatic parameter diagnostic failed category=%s status=%s", exc.category, exc.http_status)
        raise HTTPException(status_code=502, detail={"provider": "Timatic", "category": exc.category, "http_status": exc.http_status}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Timatic parameter lookup code is invalid") from exc


@app.post("/api/v1/provider-tests/timatic/document-request")
async def provider_test_timatic_document_request(
    body: TimaticDocumentRequest,
    _: None = Depends(_require_provider_diagnostics),
) -> dict[str, Any]:
    """Run a staging documentRequest using the schema from the supplied Bruno collection.

    This is a diagnostics endpoint, not the passenger workflow. A response is never treated as an
    official check-in/immigration decision until its result-code contract has been verified.
    """
    adapter = _timatic_diagnostics_adapter()
    try:
        return await adapter.document_request(body.to_provider_payload())
    except ProviderAPIError as exc:
        logger.warning("Timatic document diagnostic failed category=%s status=%s", exc.category, exc.http_status)
        raise HTTPException(status_code=502, detail={"provider": "Timatic", "category": exc.category, "http_status": exc.http_status}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Timatic request body is invalid for the local staging probe contract") from exc

@app.post("/api/v1/bookings/lookup")
async def booking_lookup(body: BookingLookupRequest, request: Request) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("lookup", payload=body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Booking not found. Check the reference and passenger surname.") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid booking lookup input") from exc
    except Exception as exc:
        logger.warning("Booking lookup failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Booking lookup could not be completed") from exc

@app.get("/api/v1/sessions/{sid}")
async def get_session(sid: str, state: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    return public_session(state)

@app.post("/api/v1/sessions/{sid}/eligibility")
async def validate_eligibility(sid: str, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("eligibility", sid)
    except Exception as exc:
        logger.warning("Eligibility failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Eligibility evaluation failed safely; retry or escalate") from exc

@app.post("/api/v1/sessions/{sid}/documents")
async def save_documents(sid: str, body: SaveDocumentsRequest, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("documents_save", sid, body.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Document save failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Document input could not be saved") from exc

@app.post("/api/v1/sessions/{sid}/documents/validate")
async def validate_documents(sid: str, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("documents_validate", sid)
    except Exception as exc:
        logger.warning("Document validation failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Document validation could not be completed; check-in remains blocked") from exc

@app.get("/api/v1/sessions/{sid}/seats")
async def list_seats(sid: str, journey_key: str | None = None, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("seats_list", sid, {"journey_key": journey_key})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/v1/sessions/{sid}/seats/skip")
async def skip_seat_selection(sid: str, request: Request, body: SeatSkipRequest | None = None, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("seat_skip", sid, (body.model_dump(exclude_none=True) if body else {}))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Seat-skip action failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Seat selection could not be skipped safely") from exc


@app.post("/api/v1/sessions/{sid}/seats")
async def select_seat(sid: str, body: SeatSelectionRequest, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("seat_select", sid, body.model_dump())
    except SeatUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Seat selection failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Seat selection could not be completed") from exc

@app.get("/api/v1/sessions/{sid}/ancillaries")
async def ancillary_catalog(sid: str, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    return await orchestrator.dispatch("ancillary_catalog", sid)

@app.post("/api/v1/sessions/{sid}/ancillaries")
async def select_ancillaries(sid: str, body: AncillarySelection, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("ancillary_select", sid, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Ancillary selection failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Ancillary selection could not be completed") from exc

@app.post("/api/v1/sessions/{sid}/ancillaries/confirm")
async def confirm_ancillaries(sid: str, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("ancillary_confirm", sid)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/v1/sessions/{sid}/check-in")
async def complete_checkin(sid: str, request: Request, _: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    try:
        return await orchestrator.dispatch("checkin_complete", sid)
    except ValueError as exc:
        # Return conflict because the workflow gates have not been satisfied.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Check-in completion failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="Mock check-in could not be completed; no approval was issued") from exc

@app.get("/api/v1/sessions/{sid}/events")
async def get_events(sid: str, state: dict[str, Any] = Depends(authorized_state)) -> dict[str, Any]:
    return {"events": state.get("events", [])[-40:], "mock_only": True, "notice": "Event records are redacted operational summaries."}

@app.post("/api/v1/chat")
async def chat(body: ChatRequest, request: Request, x_session_token: str = Header(default="", alias="X-Session-Token")) -> dict[str, Any]:
    sid = body.session_id
    if sid:
        try:
            state = await store.get(sid)
        except (SessionNotFound, SessionExpired):
            raise HTTPException(status_code=404, detail="Session not found or expired")
        actual = hashlib.sha256(x_session_token.encode()).hexdigest() if x_session_token else ""
        if not x_session_token or not __import__("hmac").compare_digest(actual, state.get("session_token_hash", "")):
            raise HTTPException(status_code=401, detail="Session token is invalid")
    try:
        return await orchestrator.chat(body.model_dump(), x_session_token or None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Booking not found. Check the reference and passenger surname.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Session token required") from exc
    except SeatUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Chat orchestration failed correlation_id=%s error_type=%s", correlation(request), type(exc).__name__)
        raise HTTPException(status_code=400, detail="The request could not be completed safely") from exc
