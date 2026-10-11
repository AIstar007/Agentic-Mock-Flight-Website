from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any, TypedDict

from app.adapters.navitaire import BookingNotFound, MockNavitaireAdapter, SeatUnavailable
from app.adapters.timatic import MockTimaticAdapter
from app.agents import (
    AncillaryAgent, BookingRetrievalAgent, CheckinAgent, DocumentValidationAgent,
    EligibilityAgent, FAQAgent, SeatAgent,
)
from app.audit import AuditSink
from app.rag import SelfCorrectiveRAG
from app.state_store import SessionStore
from app.assistant_schema import FIELD_QUESTIONS, FieldParseError, extract_labeled_field_values, next_document_field, parse_field_value

logger = logging.getLogger(__name__)


def _looks_like_faq_interrupt(message: str) -> bool:
    low = message.casefold().strip()
    if re.match(r"^(?:what|why|how|can i|could i|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", low):
        return True
    faq_topics = ("baggage", "luggage", "refund", "cancel", "check-in window", "check in window", "online check-in", "flight status", "power bank", "dangerous goods", "wheelchair", "special assistance", "visa requirement", "travel document", "seat fee", "wifi", "wi-fi", "lounge", "meal", "privacy")
    return "?" in low and any(term in low for term in faq_topics)


class ConversationIntentAgent:
    """Local-first intent/slot router for the passenger chat.

    It intentionally classifies rather than generates policy facts. Workflow actions are
    emitted only for explicit commands; short replies stay attached to their pending field,
    and FAQ-shaped questions are handed to retrieval without invoking transaction tools.
    """
    FAQ_START = re.compile(r"^(?:what|why|how|can i|could i|do i|is it|is there|tell me|explain|where|when|who|should i|does|will|would|are)\b", re.I)
    EDIT_START = re.compile(r"^(?:(?:i want to|i'd like to|please)\s+)?(?:edit|modify|change|update)\b", re.I)
    WORKFLOW = (
        "check my eligibility", "run eligibility", "validate documents", "validate document",
        "show seats", "seat map", "available seats", "select seat", "choose seat",
        "skip seat", "confirm extras", "confirm ancillaries", "save extras", "lookup ",
        "look up ", "retrieve booking", "start check-in", "start check in", "begin check-in",
        "begin check in", "complete mock check-in", "search flights", "book a flight by chat",
    )
    FAQ_TOPICS = ("baggage", "luggage", "refund", "check-in window", "check in window", "online check-in",
                  "flight status", "power bank", "dangerous goods", "wheelchair", "special assistance",
                  "visa requirement", "travel document", "seat fee", "wifi", "wi-fi", "lounge", "meal", "privacy")

    def classify(self, message: str, *, pending_field: str | None, active_step: str, session_exists: bool) -> dict[str, Any]:
        text = str(message or "").strip()
        lower = text.casefold()
        if self.EDIT_START.match(lower):
            return {"intent": "edit_request", "route": "confirm_edit", "confidence": 0.99, "pending_field": pending_field, "active_step": active_step}
        if any(command in lower for command in self.WORKFLOW) or any(phrase in lower for phrase in ("check my eligibility", "validate documents", "skip seat selection", "show seats")):
            return {"intent": "workflow_command", "route": "transaction", "confidence": 0.95, "pending_field": pending_field, "active_step": active_step}
        question = bool(self.FAQ_START.match(lower)) or ("?" in text and any(topic in lower for topic in self.FAQ_TOPICS))
        if question:
            return {"intent": "faq", "route": "grounded_retrieval", "confidence": 0.94, "pending_field": pending_field, "active_step": active_step}
        if pending_field:
            return {"intent": "slot_answer", "route": "pending_slot", "confidence": 0.90, "pending_field": pending_field, "active_step": active_step}
        return {"intent": "unresolved", "route": "grounded_retrieval", "confidence": 0.45, "pending_field": pending_field, "active_step": active_step}


class WorkflowState(TypedDict, total=False):
    action: str
    session_id: str
    request_id: str
    result_ref: str


def document_failure_resolution(row: dict[str, Any]) -> str:
    code = str(row.get("evaluation_code", "")).upper()
    reasons = " ".join(str(reason) for reason in (row.get("reasons") or [])).lower()
    if code == "COUNTRY_CODE_INVALID": return "Use a valid country selected from the searchable ISO list, or enter its recognised alpha-2/alpha-3 code/full name. This resolves country-format failures, not visa eligibility."
    if code in {"PASSPORT_EXPIRED"} or "expires before" in reasons: return "Check the real fictional passport expiry against every itinerary leg. If the document is expired, renew/replace it; do not falsify the date."
    if code == "PASSPORT_VALIDITY_REVIEW": return "The local demo applies a remaining-validity threshold, not a universal rule. Confirm the actual requirement for this nationality, destination and transit route with an authorised current document-check service. Keep blocked until reviewed."
    if code in {"DOCUMENT_DATE_INVALID", "DOCUMENT_DOB_INVALID", "DOB_IN_FUTURE"}: return "Correct the date format and verify it against the fictional source document. DOB must be a valid past date; expiry must be valid and future-dated."
    if code == "DOCUMENT_DOB_REQUIRED": return "Enter DOB for the child, infant or senior category so age rules can run. Use the source value, not an invented date."
    if code == "DOB_MISMATCH": return "Reconcile the DOB in booking and document details against the source document. If this cannot be resolved, keep blocked and refer for authorised document review."
    if code in {"PURPOSE_OF_STAY_MISSING"}: return "Select the truthful purpose of stay and rerun. Do not select tourism merely to pass the mock gate."
    if code == "PURPOSE_REQUIRES_DESTINATION_RULES": return "This purpose can require destination-specific authorisation. The local demo has no live nationality/route rule feed; get authorised current verification and keep the gate blocked."
    if code == "DURATION_OF_STAY_INVALID": return "Enter the intended duration as an integer from 1 to 365 days."
    if code == "DURATION_EXCEEDS_DEMO_THRESHOLD": return "Do not alter the genuine duration to pass. This exceeds a synthetic screening threshold; confirm the destination-specific permitted stay with an authorised source."
    if code in {"VISA_REQUIREMENT_REVIEW", "VISA_EVIDENCE_UNVERIFIED", "VISA_EVIDENCE_INVALID"}: return "A current nationality/destination/transit-specific entry requirement needs authorised review. This POC does not contact live Timatic or immigration services, so it cannot approve the case."
    if code in {"DOCUMENT_MISSING", "NO_DOCUMENT"} or "missing" in reasons: return "Complete each mandatory field listed for this passenger (passport number, valid country codes, expiry, purpose/duration, and DOB when required) and validate again."
    return "Resolve the specific reason above using verified source data and revalidate. If current official rules or passport authenticity are involved, escalate to an authorised document-check service; the chatbot cannot override the gate."


class Orchestrator:
    """LangGraph dispatcher with a deterministic fallback for dependency-light local tests.

    Raw request payloads and agent results live in one-call process memory, not in graph state.
    Only opaque references, an allowlisted action name and a random session ID are checkpointed.
    This matters because checkpoint stores can otherwise become a second copy of passenger PII.
    """
    ALLOWED_ACTIONS = {
        "lookup", "eligibility", "documents_save", "documents_validate", "seats_list",
        "seat_select", "seat_skip", "ancillary_catalog", "ancillary_select", "ancillary_confirm", "checkin_complete", "faq",
    }

    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, timatic: MockTimaticAdapter, audit: AuditSink):
        self.store, self.navitaire, self.timatic, self.audit = store, navitaire, timatic, audit
        self.booking_agent = BookingRetrievalAgent(store, navitaire, audit)
        self.eligibility_agent = EligibilityAgent(store, audit)
        self.document_agent = DocumentValidationAgent(store, navitaire, timatic, audit)
        self.seat_agent = SeatAgent(store, navitaire, audit)
        self.ancillary_agent = AncillaryAgent(store, navitaire, audit)
        self.checkin_agent = CheckinAgent(store, navitaire, audit)
        self.faq_agent = FAQAgent(SelfCorrectiveRAG())
        self.conversation_agent = ConversationIntentAgent()
        self._payloads: dict[str, dict[str, Any]] = {}
        self._results: dict[str, Any] = {}
        self._ephemeral_lock = asyncio.Lock()
        self._graph = None
        self._redis_cm = None
        self._mongo_checkpointer = None
        self._mongo_checkpoint_client = None
        self.graph_mode = "deterministic_fallback"

        self._handlers = {
            "lookup": lambda sid, p, cid: self.booking_agent.lookup(p, cid),
            "eligibility": lambda sid, p, cid: self.eligibility_agent.run(sid, cid),
            "documents_save": lambda sid, p, cid: self.document_agent.save_documents(sid, p.get("documents", {}), cid),
            "documents_validate": lambda sid, p, cid: self.document_agent.validate(sid, cid),
            "seats_list": lambda sid, p, cid: self.seat_agent.list_seats(sid, p.get("journey_key")),
            "seat_select": lambda sid, p, cid: self.seat_agent.select(sid, p["passenger_key"], p["seat_code"], p.get("journey_key"), cid),
            "seat_skip": lambda sid, p, cid: self.seat_agent.skip(sid, cid, p.get("passenger_key"), p.get("journey_key")),
            "ancillary_catalog": lambda sid, p, cid: self.ancillary_agent.catalog(),
            "ancillary_select": lambda sid, p, cid: self.ancillary_agent.select(sid, p["passenger_key"], p.get("ancillary_codes", []), cid),
            "ancillary_confirm": lambda sid, p, cid: self.ancillary_agent.confirm(sid, cid),
            "checkin_complete": lambda sid, p, cid: self.checkin_agent.complete(sid, cid),
            "faq": lambda sid, p, cid: self.faq_agent.answer(p.get("message", "")),
        }

    async def initialize(self, graph_redis_url: str = "", *, mongodb_uri: str = "",
                         mongodb_database: str = "emirates_checkin_demo",
                         checkpoint_ttl_seconds: int = 604800) -> None:
        # Security hardening for LangGraph checkpoint deserialization; keep enabled for all modes.
        os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
        try:
            from langgraph.graph import StateGraph, START, END
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer: Any = MemorySaver()
            if graph_redis_url:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                self._redis_cm = AsyncRedisSaver.from_conn_string(graph_redis_url)
                checkpointer = await self._redis_cm.__aenter__()
                await checkpointer.asetup()
                self.graph_mode = "langgraph+redis_checkpointer"
            elif mongodb_uri:
                # MongoDBSaver provides an async interface by delegating blocking PyMongo
                # operations to an executor. This avoids requiring a hosted Redis service.
                from pymongo import MongoClient
                from langgraph.checkpoint.mongodb import MongoDBSaver
                self._mongo_checkpoint_client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=3000)
                await asyncio.to_thread(self._mongo_checkpoint_client.admin.command, "ping")
                self._mongo_checkpointer = MongoDBSaver(
                    self._mongo_checkpoint_client,
                    db_name=mongodb_database,
                    checkpoint_collection_name="langgraph_checkpoints",
                    writes_collection_name="langgraph_checkpoint_writes",
                    ttl=checkpoint_ttl_seconds,
                )
                checkpointer = self._mongo_checkpointer
                self.graph_mode = "langgraph+mongodb_checkpointer"
            else:
                self.graph_mode = "langgraph+in_memory_checkpointer"

            builder = StateGraph(WorkflowState)
            node_names = {
                "lookup": "booking_agent",
                "eligibility": "eligibility_agent",
                "documents_save": "document_input_agent",
                "documents_validate": "document_validation_agent",
                "seats_list": "seat_agent",
                "seat_select": "seat_agent",
                "seat_skip": "seat_agent",
                "ancillary_catalog": "ancillary_agent",
                "ancillary_select": "ancillary_agent",
                "ancillary_confirm": "ancillary_agent",
                "checkin_complete": "checkin_agent",
                "faq": "faq_agent",
            }
            for node in sorted(set(node_names.values())):
                builder.add_node(node, self._node_runner)
            route_map = {key: node for key, node in node_names.items()}
            builder.add_conditional_edges(START, lambda state: state["action"], route_map)
            for node in set(node_names.values()):
                builder.add_edge(node, END)
            self._graph = builder.compile(checkpointer=checkpointer)
        except ImportError as exc:
            if graph_redis_url:
                raise RuntimeError("Redis-backed LangGraph persistence was configured, but its required packages are unavailable") from exc
            if mongodb_uri:
                raise RuntimeError("MongoDB-backed LangGraph persistence was configured, but its required packages are unavailable") from exc
            self.graph_mode = "deterministic_fallback (LangGraph package not installed)"
            logger.warning("LangGraph not installed; using deterministic dispatcher for local test execution")

    async def close(self) -> None:
        if self._redis_cm is not None:
            await self._redis_cm.__aexit__(None, None, None)
        if self._mongo_checkpoint_client is not None:
            await asyncio.to_thread(self._mongo_checkpoint_client.close)

    async def _node_runner(self, state: WorkflowState) -> dict[str, Any]:
        request_id = state["request_id"]
        result_ref = state["result_ref"]
        async with self._ephemeral_lock:
            payload = self._payloads.pop(request_id, {})
        action = state["action"]
        sid = state.get("session_id", "")
        correlation_id = str(uuid.uuid4())
        try:
            result = await self._handlers[action](sid, payload, correlation_id)
        except Exception as exc:
            async with self._ephemeral_lock:
                self._results[result_ref] = {"__error__": type(exc).__name__, "message": str(exc)}
            return {"action": action, "session_id": sid, "request_id": request_id, "result_ref": result_ref}
        async with self._ephemeral_lock:
            self._results[result_ref] = result
        # This deliberately omits raw arguments, PNRs, document values, tool output and model prompts.
        return {"action": action, "session_id": sid, "request_id": request_id, "result_ref": result_ref}

    async def dispatch(self, action: str, session_id: str = "", payload: dict[str, Any] | None = None) -> Any:
        if action not in self.ALLOWED_ACTIONS:
            raise ValueError("Unsupported workflow action")
        payload = payload or {}
        request_id, result_ref = str(uuid.uuid4()), str(uuid.uuid4())
        async with self._ephemeral_lock:
            self._payloads[request_id] = payload
        state: WorkflowState = {"action": action, "session_id": session_id, "request_id": request_id, "result_ref": result_ref}
        try:
            if self._graph is not None:
                thread_id = session_id or f"pre-session-{uuid.uuid4()}"
                output = await self._graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            else:
                output = await self._node_runner(state)
            async with self._ephemeral_lock:
                result = self._results.pop(result_ref, None)
            if result is None:
                raise RuntimeError("Agent did not return a result")
            if isinstance(result, dict) and "__error__" in result:
                name = result["__error__"]
                message = result["message"]
                if name == "BookingNotFound":
                    raise LookupError(message)
                if name == "SeatUnavailable":
                    raise SeatUnavailable(message)
                if name == "ValueError":
                    raise ValueError(message)
                raise AgentError(message)
            return result
        finally:
            async with self._ephemeral_lock:
                self._payloads.pop(request_id, None)
                self._results.pop(result_ref, None)

    async def chat(self, request: dict[str, Any], session_token: str | None = None) -> dict[str, Any]:
        message = request["message"].strip()
        sid = request.get("session_id")
        lower = message.lower()
        context = dict(request.get("context") or {})
        pending_field = str(request.get("pending_field") or context.get("pending_field") or "")
        correlation_id = str(uuid.uuid4())

        # Flight-domain scope and simple prompt-injection refusal are deterministic boundaries.
        dangerous = [r"ignore (all )?(previous|prior) instructions", r"reveal (the )?(system prompt|secrets|credentials|tokens)", r"bypass (the )?(eligibility|document|check.?in) (check|gate)", r"access another passenger", r"show.*passport number"]
        if any(re.search(p, lower) for p in dangerous):
            return {"answer": "I can't reveal internal instructions or credentials, access another passenger's data, or bypass eligibility and document checks. I can help with the supported check-in workflow and flight FAQs.", "intent": "guardrail_block", "citations": [], "actions": []}

        # Use the actual active page/error state supplied by the check-in UI when the
        # passenger asks for next-step guidance. This is contextual help only; it never
        # advances a workflow or overrides a server-side gate.
        page_state = dict(context.get("page_state") or {})
        active_step = str(context.get("active_step") or context.get("active_page") or page_state.get("active_step") or "booking").lower()
        visible_error = str(context.get("page_error") or "").strip()
        asks_document_failure = sid and any(phrase in lower for phrase in (
            "why did my document fail", "why did the document fail", "why did it fail", "why was document validation blocked",
            "explain document failure", "explain document validation", "what failed on my passport", "how do i fix document",
            "how can i fix this document", "why is my document not pass", "document validation reason", "reason for document failure",
        ))
        if asks_document_failure:
            try:
                state = await self.store.get(sid)
                booking = state.get("booking", {})
                passengers = booking.get("passengers", [])
                journeys = booking.get("journeys", [])
                failed_rows = [dict(row) for row in (state.get("document_checks") or {}).values() if str(row.get("decision", "")).upper() != "PASS"]
                if failed_rows:
                    sections = []
                    for row in failed_rows:
                        passenger_index = next((i for i, person in enumerate(passengers) if person.get("passenger_key") == row.get("passenger_key")), -1)
                        journey = next((leg for leg in journeys if leg.get("journey_key") == row.get("journey_key")), None)
                        who = f"Passenger {passenger_index + 1}" if passenger_index >= 0 else "Passenger"
                        route = f"{journey.get('origin', '?')} → {journey.get('destination', '?')}" if journey else str(row.get("journey_key", "journey"))
                        reasons = "; ".join(str(x) for x in (row.get("reasons") or [])) or "The validator did not provide a reason string."
                        sections.append(f"{who} · {route} · {row.get('decision', 'REVIEW_REQUIRED')} [{row.get('evaluation_code', 'DOCUMENT_REVIEW')}]\nWhy it failed / needs review: {reasons}\nRecommended correction / resolution: {document_failure_resolution(row)}")
                    answer = "Document validation is still blocked. Here are the current per-passenger/per-journey results from the session, not a generic FAQ answer:\n\n" + "\n\n".join(sections) + "\n\nDo not change a source field just to obtain PASS. Revalidate after correcting confirmed input errors. If the decision depends on current nationality/route rules or passport authenticity, escalate to an authorised document-check service; this POC is not a live Timatic decision."
                    return {"answer": answer, "intent": "document_failure_explanation", "pending_field": pending_field or None, "target_passenger_key": context.get("target_passenger_key"), "context": context, "actions": [], "citations": [], "card": {"type": "documents", "title": "Document failure explanation", "status": f"{len(failed_rows)} result(s) need attention", "fields": [{"label": f"Result {index + 1}", "value": f"{row.get('evaluation_code', 'DOCUMENT_REVIEW')} · {row.get('decision', 'REVIEW_REQUIRED')} · {document_failure_resolution(row)}"} for index, row in enumerate(failed_rows[:5])], "note": "Reasons are taken from this session's deterministic results. No official Timatic lookup is performed."}}
                if state.get("document_decision") == "PASS":
                    return {"answer": "The latest saved session results show document validation PASS for the checked passenger/journey pairs. If you are viewing an older error, rerun validation to refresh the result list. This PASS is synthetic and not an official travel-document approval.", "intent": "document_failure_explanation", "pending_field": pending_field or None, "context": context, "actions": [], "citations": [], "card": {"type": "documents", "title": "Latest document result", "status": "PASS · synthetic only", "note": "No official Timatic, immigration or passport-authenticity check was performed."}}
                return {"answer": "I cannot find row-level document results in this session yet. Open Passenger & travel documents and run validation. If it returns REVIEW_REQUIRED without specific reasons, keep check-in blocked and inspect the server-side document-adapter correlation logs; do not bypass the gate.", "intent": "document_failure_explanation", "pending_field": pending_field or None, "context": context, "actions": [], "citations": []}
            except (KeyError, ValueError):
                return {"answer": "I could not retrieve the current session's document-validation results. Reload the synthetic booking and rerun the document checks; the chatbot cannot override this gate.", "intent": "document_failure_explanation", "pending_field": pending_field or None, "context": context, "actions": [], "citations": []}
        asks_context_help = any(term in lower for term in (
            "what should i do next", "what do i do next", "what now", "where am i",
            "why am i blocked", "why can't i continue", "why can t i continue",
            "explain this error", "explain the error", "why did it fail", "what does this error mean",
        ))
        if asks_context_help:
            guidance = {
                "booking": "Enter a synthetic booking reference and matching surname, then retrieve the booking. No session is created until lookup succeeds.",
                "eligibility": "Open the eligibility panel and inspect each non-passing rule. Correct the relevant synthetic booking data or seek manual review; do not attempt documents, seats or check-in past a failed gate.",
                "documents": "Review the highlighted fictional document fields. Correct the fields that failed, then run document validation again; the agent cannot bypass the result.",
                "seats": "Select an available seat for the currently selected passenger and journey. If a seat is unavailable, refresh the map and choose another.",
                "extras": "Review the selected optional services for each passenger, save them, and explicitly confirm the extras step before moving on.",
                "review": "Review the itinerary, seat assignments and extras. Check-in can proceed only after the required server-side gates have passed.",
                "boarding": "The synthetic check-in workflow is complete. Any displayed boarding pass is a demo artefact and is not valid for travel.",
            }.get(active_step, "Review the current step's validation panel. Resolve all blocking rule results before attempting a later step.")
            reason = f"\n\nCurrent page: {active_step}." + (f"\nVisible error: {visible_error[:400]}" if visible_error else "\nNo active page error was supplied with this message.")
            return {"answer": "Here is guidance for the page you are on.\n\n" + guidance + reason + "\n\nI have not changed any fields or workflow state.", "intent": "page_context_help", "citations": [], "actions": [], "page_context": {"active_step": active_step, "session_stage": page_state.get("session_stage"), "eligibility": page_state.get("eligibility"), "document_decision": page_state.get("document_decision")}}

        # Multi-turn, schema-bound collection for a demo booking lookup.
        start_checkin = any(x in lower for x in ("start check-in", "start check in", "help me check in", "begin check-in", "begin check in", "web check-in"))
        if not sid and start_checkin and not pending_field:
            return {"answer": FIELD_QUESTIONS["record_locator"], "intent": "slot_prompt", "pending_field": "record_locator", "context": {}, "actions": []}
        if not sid and pending_field in {"record_locator", "surname"} and _looks_like_faq_interrupt(message):
            faq = await self.dispatch("faq", "", {"message": message})
            return {"answer": faq.get("answer", "I couldn't verify that from the approved sources."), "intent": faq.get("intent", "faq"), "pending_field": pending_field, "context": context, "citations": faq.get("citations", []), "actions": []}
        if not sid and pending_field == "record_locator" and start_checkin:
            return {"answer": FIELD_QUESTIONS["record_locator"], "intent": "slot_prompt", "pending_field": "record_locator", "context": context, "actions": []}
        if not sid and pending_field == "record_locator":
            try:
                parsed = parse_field_value("record_locator", message)
            except FieldParseError as exc:
                return {"answer": f"{exc}\n\n{FIELD_QUESTIONS['record_locator']}", "intent": "slot_retry", "pending_field": "record_locator", "context": context, "actions": []}
            next_context = {**context, **parsed}
            return {"answer": FIELD_QUESTIONS["surname"], "intent": "slot_filled", "pending_field": "surname", "context": next_context, "actions": []}
        if not sid and pending_field == "surname":
            try:
                parsed = parse_field_value("surname", message)
                locator = str(context.get("record_locator", "")).upper()
                if not locator:
                    return {"answer": FIELD_QUESTIONS["record_locator"], "intent": "slot_prompt", "pending_field": "record_locator", "context": {}, "actions": []}
                result = await self.dispatch("lookup", payload={"record_locator": locator, "last_name": parsed["surname"]})
                state = await self.store.get(result["session_id"])
                first_key = (state.get("booking", {}).get("passengers") or [{}])[0].get("passenger_key", "")
                chat_context = {"target_passenger_key": first_key, "workflow_phase": "eligibility"}
                return {"answer": "Synthetic booking retrieved. I’ll guide you through check-in in order. First, we must run booking eligibility; say ‘yes’ or ‘check my eligibility’. I’ll then collect and review each passenger’s document fields, validate them, show the full cabin seat map (or let you skip seats), handle extras, and review the final gate. No real airline approval is issued.", "intent": "booking_lookup", "session_id": result["session_id"], "session_token": result["session_token"], "pending_field": "checkin_eligibility", "context": chat_context, "target_passenger_key": first_key, "actions": [{"type": "SESSION_CREATED", "target": "documents"}, {"type": "NAVIGATE_STEP", "target": "documents"}], "citations": [], "card": {"type": "booking", "title": "Synthetic booking retrieved", "status": "Ready for eligibility review", "fields": [{"label": "Reference", "value": locator}, {"label": "Passenger surname", "value": parsed["surname"]}], "note": "Synthetic demo booking only."}}
            except FieldParseError as exc:
                return {"answer": f"{exc}\n\n{FIELD_QUESTIONS['surname']}", "intent": "slot_retry", "pending_field": "surname", "context": context, "actions": []}

        # Route genuine questions to RAG before broad transaction matching. In particular,
        # "What is the online check-in window?" must never attempt checkin_complete.
        workflow_commands = (
            "check my eligibility", "run eligibility", "validate documents", "validate document",
            "check document", "passport check", "show seats", "seat map", "available seats",
            "select seat", "choose seat", "skip seat selection", "skip seats", "fill document details",
            "fill passport", "edit passport fields", "lookup ", "look up ", "retrieve booking",
            "start check-in", "start check in", "begin check-in", "begin check in", "confirm extras",
            "confirm ancillaries", "save extras", "select flight", "book a flight by chat",
        )
        intent_result = self.conversation_agent.classify(message, pending_field=pending_field or None, active_step=active_step, session_exists=bool(sid))
        faq_question = intent_result["route"] == "grounded_retrieval" and _looks_like_faq_interrupt(message) and not any(term in lower for term in workflow_commands)
        if faq_question and not asks_context_help:
            faq = await self.dispatch("faq", sid or "", {"message": message})
            return {
                "answer": faq.get("answer", "I couldn't verify that from the approved aviation sources."),
                "intent": faq.get("intent", "faq"), "pending_field": pending_field or None,
                "target_passenger_key": context.get("target_passenger_key"), "context": context,
                "citations": faq.get("citations", []), "actions": [],
                "card": {"type": "faq", "title": "Flight information", "status": ("Sources available" if faq.get("citations") else "Evidence-limited"),
                         "note": "Demo knowledge only; this answer does not change your booking or check-in workflow."},
            }

        # Section edits are an explicit two-turn interaction. Do not mutate the current
        # draft until the passenger confirms; declining restores the prior pending slot.
        if sid and pending_field == "confirm_edit":
            yn = lower.strip()
            previous_pending = context.get("edit_previous_pending_field")
            previous_context = dict(context.get("edit_previous_context") or {})
            target_page = str(context.get("edit_target_page") or active_step).lower()
            if yn in {"yes", "y", "yes please", "confirm", "edit", "modify", "go ahead"}:
                if target_page in {"documents", "passengers", "document", "passenger details"}:
                    state = await self.store.get(sid)
                    passengers = state.get("booking", {}).get("passengers", [])
                    pax = context.get("target_passenger_key") or (passengers[0].get("passenger_key") if passengers else "")
                    return {"answer": "Okay, let's edit the passenger document section one field at a time. The existing values are preserved; say ‘skip’ on any field to keep its current valid value. First, enter a fictional passport number or say skip.", "intent": "edit_started", "pending_field": "passport_number", "target_passenger_key": pax, "context": {**previous_context, "target_passenger_key": pax, "workflow_phase": "documents", "document_edit_mode": True}, "actions": [{"type": "NAVIGATE_STEP", "target": "documents"}], "citations": [], "card": {"type": "document_draft", "title": "Edit passenger documents", "status": "Editing · existing values preserved", "note": "Use fictional values only. Skip keeps a valid existing value; required gates still apply."}}
                if target_page == "seats":
                    return {"answer": "Okay, seat editing is active. Choose another available seat from the full cabin map, or say ‘skip seat selection’ to continue without seats.", "intent": "edit_started", "pending_field": None, "context": {**previous_context, "workflow_phase": "seats"}, "actions": [{"type": "NAVIGATE_STEP", "target": "seats"}], "citations": [], "card": {"type": "seats", "title": "Edit seat selection", "status": "Ready", "note": "Seat availability and earlier eligibility/document gates are rechecked by the backend."}}
                if target_page == "extras":
                    return {"answer": "Okay, optional-service editing is active. Your previous selections are retained; say ‘skip’ to keep them or name an add-on to change it.", "intent": "edit_started", "pending_field": None, "context": {**previous_context, "workflow_phase": "extras"}, "actions": [{"type": "NAVIGATE_STEP", "target": "extras"}], "citations": [], "card": {"type": "extras", "title": "Edit optional extras", "status": "Existing selections preserved", "note": "Nothing is purchased; confirm extras again before final review."}}
                return {"answer": "This section can be resumed without clearing completed fields. Tell me which area to edit: passenger documents, seats or extras.", "intent": "edit_section_picker", "pending_field": None, "context": previous_context, "actions": [], "citations": []}
            if yn in {"no", "n", "cancel", "resume", "continue", "keep current", "don't change", "do not change"}:
                return {"answer": "No changes made. I restored the previous workflow context; we can continue from where we paused.", "intent": "edit_declined", "pending_field": previous_pending, "context": previous_context, "actions": [], "citations": [], "card": {"type": "workflow", "title": "Workflow resumed", "status": "No changes made", "note": "Previously completed fields and the pending step were preserved."}}
            return {"answer": "Would you like to modify this section? Reply ‘yes’ to edit its fields one at a time or ‘no’ to continue from the previous step. Say ‘skip’ for individual existing fields you want to keep.", "intent": "edit_confirmation", "pending_field": "confirm_edit", "context": context, "actions": [], "citations": []}

        edit_section_request = bool(re.match(r"^(?:i want to |i'd like to |please )?(?:edit|modify|change|update)\b", lower)) and any(x in lower for x in ("section", "details", "document", "passport", "seat", "extras", "passenger", "booking")) and not re.search(r"\bto\s+[^,?]+\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", lower)
        if sid and pending_field != "confirm_edit" and edit_section_request:
            edit_context = {**context, "edit_previous_pending_field": pending_field or None, "edit_previous_context": context, "edit_target_page": active_step}
            return {"answer": f"You are on the {active_step.replace('_', ' ')} section. Would you like to modify it? Reply ‘yes’ and I’ll walk through the section fields one by one, or ‘no’ to continue exactly where you left off. Existing values will be preserved unless you change them.", "intent": "edit_confirmation", "pending_field": "confirm_edit", "context": edit_context, "actions": [], "citations": [], "card": {"type": "workflow", "title": f"Modify {active_step.replace('_', ' ')}?", "status": "Awaiting confirmation", "note": "No fields have changed yet. You can skip fields to retain current values."}}

        doc_slots = {"passport_number", "nationality", "issuing_country", "date_of_birth", "expiry_date", "purpose_of_stay", "duration_of_stay_days"}
        labeled_doc_fields = extract_labeled_field_values(message, doc_slots)
        if sid and labeled_doc_fields and pending_field not in doc_slots and pending_field != "confirm_edit":
            edit_context = {**context, "edit_previous_pending_field": pending_field or None, "edit_previous_context": context, "edit_target_page": "documents", "edit_proposed_fields": labeled_doc_fields}
            return {"answer": "I found passenger-document fields in your message. These values have not been applied yet. Would you like to modify the passenger-document section? Reply ‘yes’ and I’ll walk through the fields one by one, or ‘no’ to continue with the existing values unchanged.", "intent": "edit_confirmation", "pending_field": "confirm_edit", "context": edit_context, "target_passenger_key": context.get("target_passenger_key"), "actions": [], "citations": [], "card": {"type": "document_draft", "title": "Modify passenger documents?", "status": "Awaiting confirmation", "note": "No new document values have been applied. Confirm before editing."}}
        start_documents = bool(sid) and any(x in lower for x in ("help me fill document", "fill document details", "passport details", "enter document information", "help with passenger documents", "edit passport fields", "fill passport", "passenger document details"))
        next_passenger_request = bool(sid) and ("next passenger" in lower or "switch passenger" in lower or bool(re.search(r"\bpassenger\s+\d+\b", lower)))
        if sid and pending_field == "checkin_eligibility" and lower.strip() in {"yes", "y", "ok", "okay", "continue", "go ahead", "check eligibility", "check my eligibility", "run eligibility", "yes please"}:
            return {"answer": "I'll run the booking eligibility rules before asking for document details.", "intent": "eligibility_request", "pending_field": "checkin_eligibility", "context": context, "actions": [{"type": "RUN_ELIGIBILITY"}], "citations": [], "card": {"type": "eligibility", "title": "Booking eligibility", "status": "Checking", "note": "Required gate · cannot be skipped or overridden by chat."}}
        if sid and pending_field == "checkin_eligibility" and not _looks_like_faq_interrupt(message) and not any(term in lower for term in ("eligibility", "check-in", "check in", "help")):
            return {"answer": "Before I can collect or validate travel documents, we must run the synthetic booking-eligibility gate. Please say ‘yes’ or ‘check my eligibility’. Your message has not changed any fields.", "intent": "slot_prompt", "pending_field": "checkin_eligibility", "context": context, "actions": [], "citations": [], "card": {"type": "eligibility", "title": "Next required step", "status": "Awaiting confirmation", "note": "Eligibility is mandatory in this demo and cannot be skipped."}}

        if sid and (start_documents or next_passenger_request) and (not pending_field or next_passenger_request):
            if not session_token:
                raise PermissionError("Session token required")
            state = await self.store.get(sid)
            passengers = state.get("booking", {}).get("passengers", [])
            if not passengers:
                return {"answer": "This demo booking has no passenger rows to edit. Retrieve a valid synthetic booking first.", "intent": "workflow_error", "pending_field": None, "actions": []}
            requested = re.search(r"\bpassenger\s+(\d+)\b", lower)
            current_key = context.get("target_passenger_key")
            current_idx = next((i for i, p in enumerate(passengers) if p.get("passenger_key") == current_key), 0)
            if requested:
                target_idx = int(requested.group(1)) - 1
                if target_idx < 0 or target_idx >= len(passengers):
                    return {"answer": f"This synthetic booking has {len(passengers)} passenger(s). Please choose a passenger from 1 to {len(passengers)}; no fields were changed.", "intent": "slot_retry", "pending_field": None, "actions": []}
            elif next_passenger_request:
                target_idx = current_idx + 1
                if target_idx >= len(passengers):
                    return {"answer": f"There are no more passengers after passenger {current_idx + 1} in this booking. You can say 'passenger 1' (or another listed number) to select a specific passenger.", "intent": "slot_retry", "pending_field": None, "target_passenger_key": current_key, "actions": []}
            else:
                target_idx = current_idx
            target_key = passengers[target_idx].get("passenger_key")
            draft = dict(context.get("document_draft") or {}) if target_key == current_key else dict(state.get("documents", {}).get(target_key, {}))
            # Start at the first page field even if the form has defaults. The passenger
            # may answer with a one-liner per prompt or say "skip" to retain that field.
            field = "passport_number"
            return {"answer": f"Let's complete the fictional document fields for passenger {target_idx + 1}. I will ask for passport number, nationality and issuing-country ISO codes (alpha-2 or alpha-3), date of birth, passport expiry, purpose of stay and duration in days. The check result is computed automatically. {FIELD_QUESTIONS[field]}\n\nEnter synthetic test values only; this does not perform a real Timatic or immigration eligibility check.", "intent": "slot_prompt", "pending_field": field, "target_passenger_key": target_key, "actions": [{"type": "NAVIGATE_STEP", "target": "documents"}]}

        if sid and pending_field in doc_slots and not extract_labeled_field_values(message, doc_slots) and _looks_like_faq_interrupt(message):
            faq = await self.dispatch("faq", sid or "", {"message": message})
            return {"answer": faq.get("answer", "I couldn't verify that from the available sources."), "intent": faq.get("intent", "faq"), "pending_field": pending_field, "target_passenger_key": context.get("target_passenger_key"), "citations": faq.get("citations", []), "actions": []}

        if sid and pending_field in doc_slots and re.match(r"^(?:please\s+)?(?:edit|modify|change|update)\b", lower) and any(term in lower for term in ("document", "passport", "field", "details", "date", "nationality", "country")):
            saved_draft = dict(context.get("document_draft") or {})
            first_field = "passport_number"
            return {"answer": "I can update the document fields. Nothing changes until you provide a new value; say ‘skip’ to keep the existing value. " + FIELD_QUESTIONS[first_field], "intent": "edit_started", "assistant_task": "documents", "pending_field": first_field, "target_passenger_key": context.get("target_passenger_key"), "context": {**context, "document_draft": saved_draft, "document_edit_mode": True}, "actions": []}
        if sid and pending_field in doc_slots:
            if not session_token:
                raise PermissionError("Session token required")
            try:
                state = await self.store.get(sid)
                passengers = state.get("booking", {}).get("passengers", [])
                target_key = context.get("target_passenger_key") or (passengers[0].get("passenger_key") if passengers else "")
                if target_key not in {p.get("passenger_key") for p in passengers}:
                    return {"answer": "I couldn't match that passenger to this booking. Please say 'fill passport details' and choose a passenger in this booking.", "intent": "slot_retry", "pending_field": pending_field, "actions": []}
                draft = dict(context.get("document_draft") or {})
                if lower.strip() in {"skip", "keep", "keep it", "leave unchanged", "same", "no change"}:
                    current_value = str(draft.get(pending_field, "")).strip()
                    target_passenger = next((p for p in passengers if p.get("passenger_key") == target_key), {})
                    category = str(target_passenger.get("passenger_type", "ADT")).upper()
                    dob_is_optional = pending_field == "date_of_birth" and category in {"ADT", "ADULT"} and not bool(draft.get("senior_citizen")) and not bool(target_passenger.get("senior_citizen"))
                    if not current_value and not dob_is_optional:
                        return {"answer": f"There is no existing value to keep for {pending_field.replace('_', ' ')}. This field is required for this passenger; please provide a fictional test value. {FIELD_QUESTIONS[pending_field]}", "intent": "slot_retry", "pending_field": pending_field, "target_passenger_key": target_key, "context": context, "actions": [], "citations": []}
                    target_passenger = next((p for p in passengers if p.get("passenger_key") == target_key), {})
                    category = str(target_passenger.get("passenger_type", "ADT")).upper()
                    needs_dob = category not in {"ADT", "ADULT"} or bool(target_passenger.get("senior_citizen"))
                    ordered_fields = ["passport_number", "nationality", "issuing_country", "date_of_birth", "expiry_date", "purpose_of_stay", "duration_of_stay_days"]
                    try: active_index = ordered_fields.index(pending_field)
                    except ValueError: active_index = -1
                    next_field = ordered_fields[active_index + 1] if 0 <= active_index + 1 < len(ordered_fields) else None
                    required_for_skip = next_field in {"passport_number", "nationality", "issuing_country", "expiry_date", "purpose_of_stay", "duration_of_stay_days"} or (next_field == "date_of_birth" and needs_dob)
                    if next_field == pending_field:
                        return {"answer": f"The existing {pending_field.replace('_', ' ')} value still needs attention. Please enter a valid fictional value; a required invalid field cannot be skipped. {FIELD_QUESTIONS[pending_field]}", "intent": "slot_retry", "pending_field": pending_field, "target_passenger_key": target_key, "context": context, "actions": [], "citations": []}
                    return {"answer": f"Kept the existing {pending_field.replace('_', ' ')} value. {FIELD_QUESTIONS[next_field] if next_field else 'The passenger document fields are ready for review. Say “validate documents” when ready.'}", "intent": "slot_skipped", "pending_field": next_field, "target_passenger_key": target_key, "context": context, "actions": [], "citations": []}
                labelled = extract_labeled_field_values(message, doc_slots)
                supplied = labelled or {pending_field: message}
                parsed_fields = {}
                candidate_context = {**draft}
                for field_name, field_value in supplied.items():
                    parsed = parse_field_value(field_name, field_value, candidate_context)
                    parsed_fields.update(parsed)
                    candidate_context.update(parsed)
                target_passenger = next((p for p in passengers if p.get("passenger_key") == target_key), {})
                category = str(target_passenger.get("passenger_type", "ADT")).upper()
                needs_dob = category not in {"ADT", "ADULT"} or bool(target_passenger.get("senior_citizen"))
                ordered_fields = ["passport_number", "nationality", "issuing_country", "date_of_birth", "expiry_date", "purpose_of_stay", "duration_of_stay_days"]
                try: active_index = ordered_fields.index(pending_field)
                except ValueError: active_index = -1
                later_fields = ordered_fields[active_index + 1:] if active_index >= 0 else ordered_fields
                next_field = next((field for field in later_fields if field not in parsed_fields), None)
                mapped_names = ", ".join(k.replace("_", " ") for k in parsed_fields)
                answer = f"I mapped your response to: {mapped_names}. The current fictional values are listed in the document review card below."
                answer += "\n\n" + ("Review the passenger document summary card before validation. Say 'validate documents' to run the mock checks, 'next passenger' to continue, or edit a value in chat/page first. Use 'skip' only to keep a previously saved valid value." if next_field is None else FIELD_QUESTIONS[next_field])
                return {"answer": answer, "intent": "slot_filled", "pending_field": next_field, "target_passenger_key": target_key, "context": {**context, "target_passenger_key": target_key, "workflow_phase": "documents"}, "actions": [{"type": "UPDATE_DOCUMENT_FIELDS", "passenger_key": target_key, "fields": parsed_fields}, {"type": "NAVIGATE_STEP", "target": "documents"}], "citations": [], "card": {"type": "document_draft", "title": "Passenger document review", "status": "Draft · not validated", "note": "Fictional data only. Review all entries before running the mock document checks."}}
            except FieldParseError as exc:
                return {"answer": f"I couldn't map that response to a valid document field: {exc}\n\n{FIELD_QUESTIONS[pending_field]} You can also provide multiple values in one line using labels, e.g. 'passport number: MOCK12345, nationality: IND, issuing country: AE, date of birth: 1990-01-01, expiry date: 2030-01-01, purpose of stay: tourism, duration of stay: 14 days'.", "intent": "slot_retry", "pending_field": pending_field, "target_passenger_key": context.get("target_passenger_key"), "actions": []}

        if sid and context.get("workflow_phase") in {"documents", "document_review"} and any(term in lower for term in ("next passenger", "next traveler", "next traveller")):
            state = await self.store.get(sid)
            pax_list = state.get("booking", {}).get("passengers", [])
            current_key = context.get("target_passenger_key") or context.get("selected_passenger_key")
            current_idx = next((i for i, passenger in enumerate(pax_list) if passenger.get("passenger_key") == current_key), -1)
            next_idx = current_idx + 1
            if next_idx < len(pax_list):
                target_key = pax_list[next_idx]["passenger_key"]
                return {"answer": f"Let's collect the next passenger's fictional document data ({next_idx + 1} of {len(pax_list)}). {FIELD_QUESTIONS['passport_number']} Then I'll ask nationality and issuing-country ISO codes (2 or 3 letters), date of birth, passport expiry, purpose and duration of stay.", "intent": "slot_prompt", "pending_field": "passport_number", "target_passenger_key": target_key, "context": {**context, "target_passenger_key": target_key, "workflow_phase": "documents"}, "actions": [{"type": "NAVIGATE_STEP", "target": "documents"}], "citations": []}
            return {"answer": "All passengers have been reviewed in the document conversation. Say ‘validate documents’ to run the required mock checks. The chatbot cannot skip missing required passport fields, and no real passport or immigration validation is performed.", "intent": "document_review_ready", "pending_field": None, "context": {**context, "workflow_phase": "documents"}, "actions": [{"type": "NAVIGATE_STEP", "target": "documents"}], "citations": []}

        lookup = re.search(r"\b(?:lookup|look up|retrieve|find booking)\s+([A-Z0-9]{6,8})\s+([A-Z][A-Z'-]{0,63})\b", message, re.I)
        if lookup:
            result = await self.dispatch("lookup", payload={"record_locator": lookup.group(1), "last_name": lookup.group(2)})
            state = await self.store.get(result["session_id"])
            first_key = (state.get("booking", {}).get("passengers") or [{}])[0].get("passenger_key", "")
            chat_context = {"target_passenger_key": first_key, "workflow_phase": "eligibility"}
            return {"answer": "Synthetic booking retrieved. I’ll guide you through check-in in order. First, we must run booking eligibility; say ‘yes’ or ‘check my eligibility’. I’ll then collect and review each passenger’s document fields, validate them, show the full cabin seat map (or let you skip seats), handle extras, and review the final gate. No real airline approval is issued.", "intent": "booking_lookup", "session_id": result["session_id"], "session_token": result["session_token"], "pending_field": "checkin_eligibility", "context": chat_context, "target_passenger_key": first_key, "actions": [{"type": "SESSION_CREATED", "target": "documents"}, {"type": "NAVIGATE_STEP", "target": "documents"}], "citations": [], "card": {"type": "booking", "title": "Synthetic booking retrieved", "status": "Ready for eligibility review", "fields": [{"label": "Reference", "value": lookup.group(1).upper()}, {"label": "Passenger surname", "value": lookup.group(2).upper()}], "note": "Synthetic demo booking only."}}

        if sid:
            if not session_token:
                raise PermissionError("Session token required")
            # All session-scoped actions validate session token at the API layer before this method.
            if any(x in lower for x in ["check document", "validate passport", "validate document", "passport check", "validate documents", "run document validation"]):
                return {"answer": "I'll save the visible fictional document fields, run the deterministic eligibility rules and document checks, and report every blocking reason here.", "intent": "document_validation_request", "actions": [{"type": "REQUEST_DOCUMENT_VALIDATION"}], "citations": []}
            if "eligibility" in lower or "check my booking" in lower:
                return {"answer": "I'll re-run the deterministic booking eligibility rules and explain each non-passing rule here.", "intent": "eligibility_request", "actions": [{"type": "RUN_ELIGIBILITY"}], "citations": []}
            if any(term in lower for term in ("skip this passenger seat", "skip this passenger's seat", "skip passenger seat", "skip this seat")):
                state = await self.store.get(sid)
                pax = context.get("selected_passenger_key") or context.get("target_passenger_key") or (state["booking"]["passengers"][0]["passenger_key"] if state["booking"]["passengers"] else "")
                journey = context.get("selected_journey_key") or (state["booking"]["journeys"][0]["journey_key"] if state["booking"]["journeys"] else "")
                result = await self.dispatch("seat_skip", sid, {"passenger_key": pax, "journey_key": journey})
                return {"answer": f"Skipped the seat only for the currently selected passenger on journey {journey}. Other passenger and journey seat assignments remain unchanged. Choose another passenger or confirm the extras step when ready.", "intent": "seat_selection_skipped", "actions": [{"type": "SKIP_SEAT_PASSENGER", "passenger_key": pax, "journey_key": journey}], "citations": [], "card": {"type": "seats", "title": "Passenger seat skipped", "status": "NOT_SELECTED", "note": "No random seat is assigned to a skipped passenger."}}
            if any(term in lower for term in ("skip seat selection", "skip seats", "no seat", "don't select a seat", "do not select a seat")):
                result = await self.dispatch("seat_skip", sid)
                return {"answer": "Seat selection skipped for this synthetic journey. The backend recorded that choice, and the seat gate will accept it; eligibility and document checks still apply. Continue to extras.", "intent": "seat_selection_skipped", "pending_field": None, "context": {**context, "workflow_phase": "extras"}, "actions": [{"type": "SKIP_SEAT_SELECTION", "status": result.get("status", "SKIPPED")}, {"type": "NAVIGATE_STEP", "target": "extras"}], "citations": [], "card": {"type": "seats", "title": "Seat selection", "status": "Skipped by passenger", "note": "No seat was assigned. This choice does not bypass eligibility or document gates."}}
            if "show seats" in lower or "seat map" in lower or "available seats" in lower:
                state = await self.store.get(sid)
                if not state.get("eligibility") or state["eligibility"].get("decision") != "PASS" or state.get("document_decision") != "PASS":
                    return {"answer": "I can show the cabin map after both booking eligibility and document validation pass. A seat list is not a validation bypass. Please resolve the blocking gate on the Passenger details page first.", "intent": "workflow_blocked", "actions": [{"type": "NAVIGATE_STEP", "target": "documents"}], "citations": [], "card": {"type": "seats", "title": "Seat selection locked", "status": "Earlier gate required", "note": "Eligibility and document validation must both pass before seats become selectable."}}
                journey = context.get("selected_journey_key") or (state["booking"]["journeys"][0]["journey_key"] if state["booking"]["journeys"] else None)
                result = await self.dispatch("seats_list", sid, {"journey_key": journey})
                all_available = [s["seat_code"] for s in result["seats"] if s["available"]]
                available = all_available[:12]
                return {"answer": f"The interactive cabin map is ready from row 1 through row 40 ({len(result['seats'])} demo seats across the full cabin). {len(all_available)} seat(s) are currently available. Examples: " + ", ".join(available) + ". The full map is open on the Seats page; choose a seat for each passenger/journey or say ‘skip seat selection’. Earlier eligibility/document gates still apply.", "intent": "seat_map", "actions": [{"type": "LOAD_SEAT_MAP", "journey_key": journey}], "citations": [], "card": {"type": "seats", "title": "Cabin seat map", "status": f"{len(all_available)} seats available", "fields": [{"label": "Cabin coverage", "value": "Rows 1–40 · 6 seats per row"}, {"label": "Available examples", "value": ", ".join(available)}], "note": "Use the interactive seat map to browse front-to-rear. Seat selection is optional in this demo, but eligibility and documents are mandatory."}}
            if any(term in lower for term in ("confirm seat", "confirm this seat", "confirm selected seat", "yes confirm seat", "next passenger")) and context.get("workflow_phase") == "seats":
                state = await self.store.get(sid)
                pax_key = context.get("selected_passenger_key") or context.get("target_passenger_key") or (state["booking"]["passengers"][0]["passenger_key"] if state["booking"]["passengers"] else "")
                journey_key = context.get("selected_journey_key") or (state["booking"]["journeys"][0]["journey_key"] if state["booking"]["journeys"] else "")
                assignment_key = f"{pax_key}#{journey_key}"
                if assignment_key not in state.get("seat_assignments", {}):
                    return {"answer": "No seat is currently assigned to this passenger and journey. Pick an available seat from the map or say ‘skip this passenger seat’ before confirming.", "intent": "seat_confirmation_blocked", "actions": [{"type": "LOAD_SEAT_MAP", "journey_key": journey_key}], "citations": []}
                pax_index = next((i for i, pax in enumerate(state["booking"]["passengers"]) if pax["passenger_key"] == pax_key), 0)
                return {"answer": f"Confirmed the selected seat for passenger {pax_index + 1}. The page will move to the next passenger/journey that still needs a seat confirmation, or open extras when everyone has confirmed or skipped.", "intent": "seat_confirmed", "actions": [{"type": "SEAT_CONFIRMED", "passenger_key": pax_key, "journey_key": journey_key}], "citations": []}
            seat_match = re.search(r"\b(?:select|choose|book|pick)\s+(?:seat\s+)?((?:[1-9]|[1-3][0-9]|40)[A-F])\b", message, re.I)
            if seat_match:
                state = await self.store.get(sid)
                pax = context.get("selected_passenger_key") or context.get("target_passenger_key") or (state["booking"]["passengers"][0]["passenger_key"] if state["booking"]["passengers"] else "")
                journey = context.get("selected_journey_key") or (state["booking"]["journeys"][0]["journey_key"] if state["booking"]["journeys"] else "")
                result = await self.dispatch("seat_select", sid, {"passenger_key": pax, "journey_key": journey, "seat_code": seat_match.group(1).upper()})
                return {"answer": f"Mock seat {result['seat_code']} selected for the currently selected passenger and journey. It is not a real reservation.", "intent": "seat_selection", "actions": [{"type": "SEAT_SELECTED", "seat_code": result["seat_code"], "passenger_key": pax, "journey_key": journey}], "citations": []}
            if any(term in lower for term in ("skip this passenger extras", "skip this passenger's extras", "skip passenger extras")):
                state = await self.store.get(sid)
                pax_list = state["booking"]["passengers"]
                active_idx = max(0, next((i for i, p in enumerate(pax_list) if p.get("passenger_key") == (context.get("target_passenger_key") or context.get("selected_passenger_key"))), 0))
                target_key = pax_list[active_idx]["passenger_key"] if pax_list else ""
                return {"answer": f"Skipping optional add-ons for passenger {active_idx + 1} only. Choose add-ons for the next passenger, or say ‘confirm extras’ when all passengers have been reviewed.", "intent": "ancillary_passenger_skipped", "actions": [{"type": "SKIP_AND_ADVANCE_CHECKIN_ANCILLARIES", "passenger_key": target_key, "passenger_index": active_idx}], "citations": []}
            if any(term in lower for term in ("confirm passenger extras", "confirm these extras", "next passenger extras", "confirm passenger add-ons", "confirm passenger addons")):
                state = await self.store.get(sid)
                pax_list = state["booking"]["passengers"]
                active_idx = max(0, next((i for i, p in enumerate(pax_list) if p.get("passenger_key") == (context.get("target_passenger_key") or context.get("selected_passenger_key"))), 0))
                target_key = pax_list[active_idx]["passenger_key"] if pax_list else ""
                return {"answer": f"Confirming passenger {active_idx + 1}'s visible add-on choices and moving to the next passenger. Use ‘confirm extras’ after reviewing everyone to continue to final review.", "intent": "ancillary_passenger_confirmed", "actions": [{"type": "SAVE_AND_ADVANCE_CHECKIN_ANCILLARIES", "passenger_key": target_key, "passenger_index": active_idx}], "citations": []}
            if any(x in lower for x in ("confirm extras", "confirm ancillaries", "save extras", "continue from extras", "continue to review")):
                return {"answer": "I'll save the visible optional-service selections for every passenger and run the server-side gate. Nothing is purchased in this demo.", "intent": "ancillary_confirmation_request", "actions": [{"type": "CONFIRM_ANCILLARIES"}], "citations": []}
            if any(x in lower for x in ["add bag", "extra baggage", "lounge", "wi-fi", "wifi", "meal", "remove bag", "remove baggage", "no extras"]):
                codes = []
                if "bag" in lower or "baggage" in lower: codes.append("EXTRA_BAG")
                if "lounge" in lower: codes.append("LOUNGE")
                if "wifi" in lower or "wi-fi" in lower: codes.append("WIFI")
                if "meal" in lower: codes.append("MEAL")
                replacing = "no extras" in lower
                removing = "remove" in lower
                if replacing: codes = []
                pax_match = re.search(r"\bpassenger\s+(\d+)\b", lower)
                state = await self.store.get(sid)
                if "everyone" in lower or "all passengers" in lower:
                    target_keys = [p["passenger_key"] for p in state["booking"]["passengers"]]
                else:
                    default_key = context.get("target_passenger_key") or context.get("selected_passenger_key") or (state["booking"]["passengers"][0]["passenger_key"] if state["booking"]["passengers"] else "")
                    idx = int(pax_match.group(1))-1 if pax_match else next((i for i,p in enumerate(state["booking"]["passengers"]) if p["passenger_key"] == default_key), 0)
                    if idx < 0 or idx >= len(state["booking"]["passengers"]):
                        return {"answer": "That passenger number is not in this booking. Choose a passenger number shown on the page.", "intent": "slot_retry", "actions": []}
                    target_keys = [state["booking"]["passengers"][idx]["passenger_key"]]
                who = "all passengers" if "everyone" in lower or "all passengers" in lower else "the selected passenger"
                verb = "remove" if removing else ("clear optional extras for" if replacing else "add")
                active_index = next((i for i, p in enumerate(state["booking"]["passengers"]) if p["passenger_key"] in target_keys), 0)
                return {"answer": f"I'll {verb} the selected optional-service codes for {who}. Review this passenger's visible selections, then say ‘confirm passenger extras’ to move on or ‘skip this passenger extras’ to leave this passenger without add-ons. When everyone is reviewed, say ‘confirm extras’ to continue; no purchase is made.", "intent": "ancillary_selection_request", "actions": [{"type": "UPDATE_CHECKIN_ANCILLARIES", "passenger_keys": target_keys, "passenger_index": active_index, "codes": codes, "replace": replacing, "remove": removing}], "citations": []}
            if "check in" in lower or "check-in" in lower or "boarding pass" in lower:
                result = await self.dispatch("checkin_complete", sid)
                return {"answer": "Mock check-in completed. The generated pass is for demonstration only and is not valid for travel.", "intent": "checkin", "actions": [{"type": "CHECKIN_COMPLETED"}], "citations": []}

        faq = await self.dispatch("faq", sid or "", {"message": message})
        return {"answer": faq["answer"], "intent": faq["intent"], "citations": faq.get("citations", []), "actions": [], "retrieval_trace": faq.get("retrieval_trace")}


class AgentError(Exception):
    pass
