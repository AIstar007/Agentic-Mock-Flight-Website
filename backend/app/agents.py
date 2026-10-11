from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.navitaire import BookingNotFound, MockNavitaireAdapter, SeatUnavailable
from app.adapters.timatic import MockTimaticAdapter
from app.audit import AuditSink
from app.models import BookingLookupRequest, Decision, EligibilityResult, PassportInput, Stage
from app.rag import SelfCorrectiveRAG
from app.reference_workflow import ReferenceStyleDocumentValidationWorkflow
from app.rules import run_eligibility_concurrently
from app.state_store import SessionStore


class AgentError(Exception):
    pass


class BookingRetrievalAgent:
    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, audit: AuditSink):
        self.store, self.navitaire, self.audit = store, navitaire, audit

    async def lookup(self, payload: dict[str, Any], correlation_id: str | None = None) -> dict[str, Any]:
        request = BookingLookupRequest.model_validate(payload)
        booking = await self.navitaire.retrieve_booking(request.record_locator, request.last_name)
        now = datetime.now(timezone.utc)
        sid = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        initial_stage = Stage.DOCUMENTS.value
        session = {
            "session_id": sid,
            "session_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.store.ttl_seconds)).isoformat(),
            "stage": initial_stage,
            "booking": booking,
            "documents": {},
            "document_checks": {},
            "document_decision": Decision.NOT_RUN.value,
            "eligibility": None,
            "seat_assignments": {},
            "seat_selection_skipped": False,
            "seat_selection_skipped_assignments": [],
            "ancillary_selections": {},
            "ancillary_stage_complete": False,
            "checked_in": False,
            "boarding_passes": [],
            "events": [],
        }
        await self.store.create(session)
        event = await self.audit.emit(sid, "booking_agent", "booking_lookup", "SUCCESS", "Synthetic booking retrieved", correlation_id)
        await self.store.update(sid, lambda state: state["events"].append(event.model_dump(mode="json")))
        return {"session_id": sid, "session_token": token, "session": await self._public_session(sid)}

    async def _public_session(self, sid: str) -> dict[str, Any]:
        state = await self.store.get(sid)
        return public_session(state)


class EligibilityAgent:
    def __init__(self, store: SessionStore, audit: AuditSink):
        self.store, self.audit = store, audit

    async def run(self, sid: str, correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        result = await run_eligibility_concurrently(state["booking"])
        result_json = result.model_dump(mode="json")
        next_stage = Stage.DOCUMENTS.value if result.decision == Decision.PASS else Stage.REVIEW_REQUIRED.value

        def mutate(s: dict[str, Any]) -> None:
            s["eligibility"] = result_json
            s["stage"] = next_stage if not s.get("checked_in") else Stage.COMPLETED.value
            s["boarding_passes"] = []
            s["checked_in"] = False
        await self.store.update(sid, mutate)
        failed = [r for r in result.rules if r.decision != Decision.PASS]
        event = await self.audit.emit(sid, "eligibility_agent", "deterministic_eligibility", "PASS" if not failed else "BLOCKED", f"Eligibility checks evaluated; failed_rules={len(failed)}", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return {"eligibility": result_json, "failed_rules": len(failed), "mock_only": True}


class DocumentValidationAgent:
    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, timatic: MockTimaticAdapter, audit: AuditSink):
        self.store, self.navitaire, self.timatic, self.audit = store, navitaire, timatic, audit

    async def save_documents(self, sid: str, documents: dict[str, dict[str, Any]], correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        allowed = {p["passenger_key"] for p in state["booking"]["passengers"]}
        if not set(documents).issubset(allowed):
            raise ValueError("Document payload contains a passenger outside this booking")
        validated = {key: PassportInput.model_validate(value).model_dump(mode="json") for key, value in documents.items()}
        def mutate(s: dict[str, Any]) -> None:
            s["documents"].update(validated)
            s["document_checks"] = {}
            s["document_decision"] = Decision.NOT_RUN.value
            s["boarding_passes"] = []
            s["checked_in"] = False
            s["stage"] = Stage.DOCUMENTS.value
        await self.store.update(sid, mutate)
        event = await self.audit.emit(sid, "document_agent", "documents_saved", "SUCCESS", f"Document fields saved for {len(validated)} synthetic passenger record(s); values omitted from audit", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return {"saved_passengers": len(validated), "status": "SAVED", "message": "Synthetic document fields saved. This does not establish real document authenticity."}

    async def validate(self, sid: str, correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        booking = state["booking"]
        docs = state.get("documents", {})

        workflow = ReferenceStyleDocumentValidationWorkflow(
            timatic_validator=self.timatic.validate,
            navitaire_document_check=self.navitaire.document_check,
        )
        validation = await workflow.validate(booking, docs)
        normalized = validation.get("results", [])
        overall = str(validation.get("decision", Decision.REVIEW_REQUIRED.value))

        # A document pass can never override a failed booking-eligibility gate.
        eligibility = state.get("eligibility") or {}
        if eligibility.get("decision") != Decision.PASS.value and overall == Decision.PASS.value:
            overall = Decision.REVIEW_REQUIRED.value

        mapping = {
            f"{row['passenger_key']}#{row['journey_key']}": {
                key: value for key, value in row.items()
                if key not in {"raw_request", "request", "document_number", "passport_number"}
            }
            for row in normalized
        }

        def mutate(s: dict[str, Any]) -> None:
            s["document_checks"] = mapping
            s["document_decision"] = overall
            s["boarding_passes"] = []
            s["checked_in"] = False
            if overall == Decision.PASS.value and s.get("eligibility", {}).get("decision") == Decision.PASS.value:
                s["stage"] = Stage.SEATS.value
            else:
                s["stage"] = Stage.REVIEW_REQUIRED.value

        await self.store.update(sid, mutate)
        counts = validation.get("counts", {"PASS": 0, "FAIL": 0, "REVIEW_REQUIRED": len(normalized)})
        mock_only = bool(validation.get("mock_only", True))
        event = await self.audit.emit(
            sid,
            "document_agent",
            "reference_style_document_validation",
            overall,
            f"Per-passenger/per-journey checks={len(normalized)}; pass={counts.get('PASS', 0)}; fail={counts.get('FAIL', 0)}; review={counts.get('REVIEW_REQUIRED', 0)}; provider_mode={'mock' if mock_only else 'provider'}",
            correlation_id,
        )
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        public_results = [
            {key: value for key, value in row.items() if key not in {"raw_request", "request", "document_number", "passport_number"}}
            for row in normalized
        ]
        return {
            "decision": overall,
            "results": public_results,
            "counts": counts,
            "provider": validation.get("provider", "REFERENCE_STYLE_WORKFLOW"),
            "mock_only": mock_only,
            "message": validation.get("message", "Reference-style document workflow completed.") + (
                " Booking eligibility has not passed, so downstream check-in remains blocked."
                if eligibility.get("decision") != Decision.PASS.value and overall == Decision.REVIEW_REQUIRED.value else ""
            ),
        }


class SeatAgent:
    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, audit: AuditSink):
        self.store, self.navitaire, self.audit = store, navitaire, audit

    async def list_seats(self, sid: str, journey_key: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        if not journey_key:
            journey_key = state["booking"]["journeys"][0]["journey_key"]
        return {"journey_key": journey_key, "seats": await self.navitaire.list_seats(state, journey_key), "selected": state["seat_assignments"]}

    async def select(self, sid: str, passenger_key: str, seat_code: str, journey_key: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        self._require_gates(state)
        selected = await self.navitaire.assign_seat(state, passenger_key, seat_code, journey_key)
        actual_journey_key = selected["journey_key"]
        assignment_key = f"{passenger_key}#{actual_journey_key}"
        selected["assignment_key"] = assignment_key
        def mutate(s: dict[str, Any]) -> None:
            s["seat_assignments"][assignment_key] = selected
            s["seat_selection_skipped"] = False
            s["seat_selection_skipped_assignments"] = [k for k in s.get("seat_selection_skipped_assignments", []) if k != assignment_key]
            s["boarding_passes"] = []
            s["checked_in"] = False
            s["stage"] = Stage.SEATS.value
        await self.store.update(sid, mutate)
        event = await self.audit.emit(sid, "seat_agent", "seat_selected", "SUCCESS", "Mock seat assignment committed", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return selected

    async def skip(self, sid: str, correlation_id: str | None = None, passenger_key: str | None = None, journey_key: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        self._require_gates(state)
        if passenger_key and journey_key:
            pax_keys = {p["passenger_key"] for p in state["booking"]["passengers"]}
            journey_keys = {j["journey_key"] for j in state["booking"]["journeys"]}
            if passenger_key not in pax_keys or journey_key not in journey_keys:
                raise ValueError("Passenger and journey must belong to this booking")
            assignment_key = f"{passenger_key}#{journey_key}"
            await self.navitaire.release_passenger_seat(state, passenger_key, journey_key)
            def mutate_one(s: dict[str, Any]) -> None:
                s["seat_assignments"].pop(assignment_key, None)
                skipped = set(s.get("seat_selection_skipped_assignments", [])); skipped.add(assignment_key)
                s["seat_selection_skipped_assignments"] = sorted(skipped)
                required = {f"{p['passenger_key']}#{j['journey_key']}" for p in s["booking"]["passengers"] for j in s["booking"]["journeys"]}
                s["seat_selection_skipped"] = False
                s["boarding_passes"] = []; s["checked_in"] = False; s["stage"] = Stage.SEATS.value
            await self.store.update(sid, mutate_one)
            event = await self.audit.emit(sid, "seat_agent", "passenger_seat_skipped", "SUCCESS", "Passenger explicitly skipped one mock seat assignment", correlation_id)
            await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
            return {"status": "PASSENGER_SKIPPED", "assignment_key": assignment_key, "mock_only": True}
        await self.navitaire.release_session_seats(state)
        def mutate(s: dict[str, Any]) -> None:
            s["seat_assignments"] = {}; s["seat_selection_skipped_assignments"] = []
            s["seat_selection_skipped"] = True; s["boarding_passes"] = []; s["checked_in"] = False; s["stage"] = Stage.SEATS.value
        await self.store.update(sid, mutate)
        event = await self.audit.emit(sid, "seat_agent", "seat_selection_skipped", "SUCCESS", "Passenger explicitly skipped all mock seat assignments", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return {"status": "SKIPPED", "seat_selection_skipped": True, "mock_only": True}

    @staticmethod
    def _require_gates(state: dict[str, Any]) -> None:
        if not state.get("eligibility") or state["eligibility"].get("decision") != Decision.PASS.value:
            raise ValueError("Run and pass booking eligibility checks before selecting seats")
        if state.get("document_decision") != Decision.PASS.value:
            raise ValueError("International document validation must pass before selecting seats")


class AncillaryAgent:
    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, audit: AuditSink):
        self.store, self.navitaire, self.audit = store, navitaire, audit

    async def catalog(self) -> dict[str, Any]:
        return {"items": self.navitaire.ancillary_catalog(), "mock_only": True}

    async def select(self, sid: str, passenger_key: str, codes: list[str], correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        SeatAgent._require_gates(state)
        pax = {p["passenger_key"] for p in state["booking"]["passengers"]}
        if passenger_key not in pax:
            raise ValueError("Passenger is not part of this booking")
        catalog = {i["code"]: i for i in self.navitaire.ancillary_catalog()}
        normalized = sorted(set(c.upper() for c in codes))
        if not set(normalized).issubset(catalog):
            raise ValueError("Unknown ancillary")
        values = [{**catalog[c]} for c in normalized]
        def mutate(s: dict[str, Any]) -> None:
            s["ancillary_selections"][passenger_key] = values
            s["ancillary_stage_complete"] = False
            s["boarding_passes"] = []
            s["checked_in"] = False
            s["stage"] = Stage.ANCILLARIES.value
        await self.store.update(sid, mutate)
        return {"passenger_key": passenger_key, "items": values, "total": sum(i["price"] for i in values), "currency": "AED", "mock_only": True}

    async def confirm(self, sid: str, correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        SeatAgent._require_gates(state)
        required_seats = {f"{p['passenger_key']}#{j['journey_key']}" for p in state["booking"]["passengers"] for j in state["booking"]["journeys"]}
        fulfilled = set(state.get("seat_assignments", {})) | set(state.get("seat_selection_skipped_assignments", []))
        if not state.get("seat_selection_skipped") and not required_seats.issubset(fulfilled):
            raise ValueError("Select or explicitly skip the seat for every passenger on every journey before confirming ancillary choices")
        required_passengers = {p["passenger_key"] for p in state["booking"]["passengers"]}
        def mutate(s: dict[str, Any]) -> None:
            # Empty selections are valid if the passenger explicitly reaches and confirms the step.
            for passenger_key in required_passengers:
                s["ancillary_selections"].setdefault(passenger_key, [])
            s["ancillary_stage_complete"] = True
            s["stage"] = Stage.REVIEW.value
        await self.store.update(sid, mutate)
        event = await self.audit.emit(sid, "ancillary_agent", "ancillaries_confirmed", "SUCCESS", "Ancillary step explicitly confirmed", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return {"status": "CONFIRMED", "selections": (await self.store.get(sid))["ancillary_selections"], "mock_only": True}


class CheckinAgent:
    def __init__(self, store: SessionStore, navitaire: MockNavitaireAdapter, audit: AuditSink):
        self.store, self.navitaire, self.audit = store, navitaire, audit

    async def complete(self, sid: str, correlation_id: str | None = None) -> dict[str, Any]:
        state = await self.store.get(sid)
        if not state.get("eligibility") or state["eligibility"].get("decision") != Decision.PASS.value:
            raise ValueError("Check-in blocked: booking eligibility must pass")
        if state.get("document_decision") != Decision.PASS.value:
            raise ValueError("Check-in blocked: document status must be PASS; unresolved cases require manual review")
        required_seats = {f"{p['passenger_key']}#{j['journey_key']}" for p in state["booking"]["passengers"] for j in state["booking"]["journeys"]}
        fulfilled = set(state.get("seat_assignments", {})) | set(state.get("seat_selection_skipped_assignments", []))
        if not state.get("seat_selection_skipped") and not required_seats.issubset(fulfilled):
            raise ValueError("Check-in blocked: choose or explicitly skip a seat for every passenger and journey")
        if not state.get("ancillary_stage_complete"):
            raise ValueError("Check-in blocked: ancillary/review step must be confirmed, including no add-ons")
        # This is the point where a live Navitaire adapter would execute check-in and retrieve the provider result.
        passes = []
        first_adult = next((p for p in state["booking"]["passengers"] if str(p.get("passenger_type", "ADT")).upper() in {"ADT", "ADULT"}), None)
        booking_reference = state.get("booking", {}).get("record_locator", "DEMO-PNR")
        manual_checkin_surname = (first_adult or (state["booking"]["passengers"] or [{}])[0]).get("last_name", "NOT_SET")
        for pax in state["booking"]["passengers"]:
            for journey in state["booking"]["journeys"]:
                assignment_key = f"{pax['passenger_key']}#{journey['journey_key']}"
                passes.append({
                    "boarding_pass_id": "DEMO-" + uuid.uuid4().hex[:10].upper(),
                    "passenger_key": pax["passenger_key"],
                    "journey_key": journey["journey_key"],
                    "passenger_name": f"{pax['first_name']} {pax['last_name']}",
                    "record_locator": booking_reference,
                    "manual_checkin_last_name": manual_checkin_surname,
                    "flight_number": "EK-DEMO",
                    "origin": journey["origin"],
                    "destination": journey["destination"],
                    "departure_in_hours": journey["departure_offset_hours"],
                    "seat": state.get("seat_assignments", {}).get(assignment_key, {}).get("seat_code", "NOT_SELECTED"),
                    "boarding_group": "DEMO",
                    "mock_only": True,
                    "qr_notice": "Decorative placeholder only; not scannable or valid for travel",
                })
        def mutate(s: dict[str, Any]) -> None:
            s["boarding_passes"] = passes
            s["checked_in"] = True
            s["stage"] = Stage.COMPLETED.value
        await self.store.update(sid, mutate)
        await self.navitaire.add_booking_comments()
        event = await self.audit.emit(sid, "checkin_agent", "mock_checkin_completed", "SUCCESS", f"Mock boarding-pass records created={len(passes)}; no airline transaction performed", correlation_id)
        await self.store.update(sid, lambda s: s["events"].append(event.model_dump(mode="json")))
        return {"status": "COMPLETED", "boarding_passes": passes, "mock_only": True, "notice": "Not a real boarding pass and not valid for travel."}


class FAQAgent:
    def __init__(self, rag: SelfCorrectiveRAG):
        self.rag = rag

    async def answer(self, message: str) -> dict[str, Any]:
        return self.rag.answer(message)


def public_session(state: dict[str, Any]) -> dict[str, Any]:
    """Return browser-safe workflow state; never return credential hash or raw passport values."""
    docs_summary = {}
    for pax_key, doc in state.get("documents", {}).items():
        docs_summary[pax_key] = {
            "saved": True,
            "passport_masked": ("••••" + doc.get("passport_number", "")[-2:]) if doc.get("passport_number") else None,
            "nationality": doc.get("nationality"),
            "expiry_date": doc.get("expiry_date"),
            "visa_evidence_status": doc.get("visa_evidence_status"),
        }
    return {
        "session_id": state["session_id"],
        "created_at": state["created_at"],
        "expires_at": state["expires_at"],
        "stage": state["stage"],
        "booking": state["booking"],
        "documents": docs_summary,
        "document_checks": state.get("document_checks", {}),
        "document_decision": state.get("document_decision", Decision.NOT_RUN.value),
        "eligibility": state.get("eligibility"),
        "seat_assignments": state.get("seat_assignments", {}),
        "seat_selection_skipped": bool(state.get("seat_selection_skipped", False)),
        "seat_selection_skipped_assignments": state.get("seat_selection_skipped_assignments", []),
        "ancillary_selections": state.get("ancillary_selections", {}),
        "ancillary_stage_complete": state.get("ancillary_stage_complete", False),
        "checked_in": state.get("checked_in", False),
        "boarding_passes": state.get("boarding_passes", []),
        "events": state.get("events", [])[-40:],
        "mock_only": True,
    }
