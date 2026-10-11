from __future__ import annotations

"""Reference-derived document-validation workflow.

Implements the workflow structure in the supplied 1.0.7 reference:
  * build one Timatic request per passenger x journey;
  * call Timatic;
  * only after an explicit Timatic YES, call Navitaire passenger document-check;
  * aggregate the results with fail-closed semantics.

This module does not decide immigration admission. Provider-specific payloads/results must be
validated against the provider's authorized API contract before a sandbox adapter is enabled.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

DOCUMENT_CODE_MAPPING = {
    # Reference Navitaire codes and the canonical values visible in the supplied Bruno request.
    "P": "PASSPORT",
    "PASSPORT": "PASSPORT",
    "V": "VVV",
    "VVV": "VVV",
    "VISA": "VVV",
    "OCI": "OCI",
}


def _as_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value).strip()
    return text.split("T", 1)[0]


def build_timatic_request(
    booking: dict[str, Any],
    passenger: dict[str, Any],
    journey: dict[str, Any],
    document: dict[str, Any] | list[dict[str, Any]],
    *,
    processing_entity: str = "EK",
    live_contract: bool = False,
) -> dict[str, Any]:
    """Build a Timatic documentRequest payload from one passenger and one journey.

    ``document`` can be one synthetic document from the UI or the provider's complete
    travelDocuments list, matching the reference workflow's one-request-per-passenger/journey
    contract. No document numbers are copied into this request unless the verified provider schema
    explicitly requires them; the supplied reference model does not contain that field.
    """
    transaction_id = str(uuid.uuid4())
    document_items = document if isinstance(document, list) else [document]
    document_items = [item for item in document_items if isinstance(item, dict)]
    if not document_items:
        raise ValueError("No travel documents are available for Timatic request construction")

    nationality = str(
        document_items[0].get("nationality", passenger.get("nationality", ""))
    ).upper()
    dob = _as_date(
        document_items[0].get(
            "date_of_birth",
            document_items[0].get("birthDate", passenger.get("date_of_birth", "")),
        )
    )
    birth_country = str(
        passenger.get("country_of_birth", passenger.get("birth_country", ""))
    ).upper()
    resident_country = str(
        passenger.get("residence_country", passenger.get("residentCountryCode", ""))
    ).upper()
    gender = str(passenger.get("gender", "")).upper()

    document_details: list[dict[str, Any]] = []
    for item in document_items:
        raw_code = str(item.get("document_type_code", item.get("documentTypeCode", "P"))).upper()
        mapped_code = DOCUMENT_CODE_MAPPING.get(raw_code)
        if not mapped_code:
            # Ignore document types not explicitly mapped by the reference contract.
            continue
        expiry_date = _as_date(
            item.get("expiry_date", item.get("expirationDate", item.get("documentExpiryDate")))
        )
        issue_country = str(
            item.get("issuing_country", item.get("issuedByCode", item.get("documentIssueCountry", "")))
        ).upper()
        doc_nationality = str(item.get("nationality", nationality)).upper()
        if not expiry_date or not issue_country or not doc_nationality:
            if live_contract:
                raise ValueError("A supported travel document is missing expiry, issuing country or nationality")
            # Mock-only UI records are allowed to reach deterministic mock validation, not real provider calls.
            continue
        expiry_value = item.get("documentExpiryDate")
        if not expiry_value:
            expiry_value = f"{expiry_date}T00:00:00+00:00"
        document_details.append({
            "documentCode": mapped_code,
            "documentExpiryDate": expiry_value,
            "documentIssueCountry": issue_country,
            "nationality": doc_nationality,
            # Present in the supplied Bruno request and accepted by its typed POC contract.
            "birthDate": _as_date(item.get("date_of_birth", item.get("birthDate", dob))),
        })
    if not document_details:
        raise ValueError("No supported, complete travel documents were available for Timatic")

    segments = journey.get("segments")
    if not isinstance(segments, list) or not segments:
        if live_contract:
            raise ValueError("Live Timatic requests require actual Navitaire segment data; synthetic fallback is forbidden")
        departure = datetime.now(timezone.utc) + timedelta(hours=float(journey.get("departure_offset_hours", 24)))
        arrival = departure + timedelta(hours=float(journey.get("duration_hours", 6)))
        segments = [{
            "departure": {"dateTime": departure.isoformat(), "point": journey.get("origin", ""), "type": "AIRPORT"},
            "arrival": {"dateTime": arrival.isoformat(), "point": journey.get("destination", ""), "type": "AIRPORT"},
            "luggageCollected": True,
            "operatingCarrier": journey.get("operating_carrier", processing_entity),
            "processingEntity": processing_entity,
        }]
    else:
        mapped_segments: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            departure_obj = segment.get("departure", {}) if isinstance(segment.get("departure", {}), dict) else {}
            arrival_obj = segment.get("arrival", {}) if isinstance(segment.get("arrival", {}), dict) else {}
            departure_time = departure_obj.get("dateTime", departure_obj.get("date_time", ""))
            arrival_time = arrival_obj.get("dateTime", arrival_obj.get("date_time", ""))
            origin = departure_obj.get("point", departure_obj.get("origin", ""))
            destination = arrival_obj.get("point", arrival_obj.get("destination", ""))
            if not all((departure_time, arrival_time, origin, destination)):
                designator = segment.get("designator", {}) if isinstance(segment.get("designator", {}), dict) else {}
                departure_time = departure_time or designator.get("departure", "")
                arrival_time = arrival_time or designator.get("arrival", "")
                origin = origin or designator.get("origin", "")
                destination = destination or designator.get("destination", "")
            if not all((departure_time, arrival_time, origin, destination)):
                if live_contract:
                    raise ValueError("A live itinerary segment lacks actual departure/arrival date, time or airport")
                continue
            operating_carrier = str(
                segment.get("operatingCarrier") or segment.get("operating_carrier")
                or segment.get("carrierCode") or segment.get("carrier_code")
                or journey.get("operating_carrier", "")
            ).strip().upper()
            segment_processing_entity = str(
                segment.get("processingEntity") or segment.get("processing_entity") or processing_entity
            ).strip().upper()
            if live_contract and (not operating_carrier or not segment_processing_entity):
                raise ValueError("A live itinerary segment lacks an explicit operating carrier or processing entity")
            mapped_segments.append({
                "departure": {"dateTime": departure_time, "point": origin, "type": "AIRPORT"},
                "arrival": {"dateTime": arrival_time, "point": destination, "type": "AIRPORT"},
                "luggageCollected": bool(segment.get("luggageCollected", segment.get("luggage_collected", True))),
                "operatingCarrier": operating_carrier or processing_entity,
                "processingEntity": segment_processing_entity,
            })
        segments = mapped_segments

    if not segments:
        raise ValueError("Journey does not contain sufficient segment data for Timatic")
    if not birth_country and not live_contract:
        # Compatibility only for synthetic fixtures. Never assume nationality equals birth country
        # when constructing a real provider request.
        birth_country = nationality
    if live_contract and (not birth_country or not nationality or not dob or not resident_country or gender not in {"M", "F"}):
        raise ValueError("Required passenger birth-country, nationality, birth-date, residence or gender data is missing")
    if live_contract and not processing_entity.strip():
        raise ValueError("TIMATIC_PROCESSING_ENTITY must be set from the authorized provider environment")

    return {
        "channel": "Web",
        "documentDetails": document_details,
        "itineraryDetails": {"segments": segments},
        "language": "EN",
        "passengerDetails": {
            "birthCountry": birth_country,
            "birthDate": dob,
            "gender": gender,
            "nationality": nationality,
            "residentCountryCode": resident_country,
        },
        # Bruno's contract shows a UUID, not the opaque Navitaire passenger key.
        "passengerId": str(uuid.uuid4()),
        "transactionId": transaction_id,
    }


def parse_reference_timatic_response(payload: Any) -> tuple[str, str, list[str]]:
    """Parse the response schema used in the supplied 1.0.7 reference models.

    Returns (decision, code, reasons). Only evaluationResult=YES is a pass. NO is a fail;
    CONDITIONAL, malformed and unknown values require manual review.
    """
    if not isinstance(payload, dict):
        return "REVIEW_REQUIRED", "UNMAPPED_PROVIDER_RESPONSE", ["Timatic response was not an object; manual review is required."]

    raw_result = payload.get("evaluationResult")
    normalized = str(raw_result or "").strip().upper()
    reason_texts: list[str] = []
    seen: set[str] = set()

    def add_text(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            reason_texts.append(text[:800])

    def parse_regulation(regulation: Any) -> None:
        if not isinstance(regulation, dict):
            return
        reg_result = str(regulation.get("evaluationResult", "")).upper()
        # Only include regulation text when the regulation itself did not pass.
        if reg_result and reg_result != "YES":
            for text_item in regulation.get("texts", []) if isinstance(regulation.get("texts"), list) else []:
                add_text(text_item.get("text") if isinstance(text_item, dict) else text_item)

    segments = payload.get("segmentResults", [])
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            seg_result = str(segment.get("segmentEvaluationResult", "")).upper()
            for rule_set in segment.get("ruleSetEvaluations", []) if isinstance(segment.get("ruleSetEvaluations"), list) else []:
                if not isinstance(rule_set, dict):
                    continue
                if str(rule_set.get("evaluationResult", "")).upper() == "YES":
                    continue
                for reg in rule_set.get("regulations", []) if isinstance(rule_set.get("regulations"), list) else []:
                    parse_regulation(reg)
                for document_result in rule_set.get("documentResults", []) if isinstance(rule_set.get("documentResults"), list) else []:
                    if not isinstance(document_result, dict):
                        continue
                    for reg in document_result.get("regulations", []) if isinstance(document_result.get("regulations"), list) else []:
                        parse_regulation(reg)
            for reg in segment.get("regulations", []) if isinstance(segment.get("regulations"), list) else []:
                parse_regulation(reg)
            if seg_result == "NO" and not reason_texts:
                add_text("A segment-level Timatic rule did not pass.")

    if normalized == "YES":
        # Top-level YES is authoritative only when its value is an exact recognized Timatic enum.
        return "PASS", "TIMATIC_EVALUATION_YES", []
    if normalized == "NO":
        return "FAIL", "TIMATIC_EVALUATION_NO", reason_texts or ["Timatic returned an overall NO evaluation."]
    if normalized == "CONDITIONAL":
        return "REVIEW_REQUIRED", "TIMATIC_EVALUATION_CONDITIONAL", reason_texts or ["Timatic returned a CONDITIONAL evaluation; manual verification is required."]
    return "REVIEW_REQUIRED", "UNMAPPED_PROVIDER_RESPONSE", reason_texts or ["Timatic response did not contain a recognized evaluationResult."]


def decision_from_navitaire_document_status(raw_status: Any) -> tuple[str, str]:
    """Map the reference DocumentCheckStatus enum (Unknown=0, Ok=1, Conditional=2, NotOk=3)."""
    try:
        status = int(raw_status)
    except (TypeError, ValueError):
        return "REVIEW_REQUIRED", "NAVITAIRE_DOCUMENT_STATUS_UNKNOWN"
    if status == 1:
        return "PASS", "NAVITAIRE_DOCUMENT_CHECK_OK"
    if status == 3:
        return "FAIL", "NAVITAIRE_DOCUMENT_CHECK_NOT_OK"
    if status == 2:
        return "REVIEW_REQUIRED", "NAVITAIRE_DOCUMENT_CHECK_CONDITIONAL"
    return "REVIEW_REQUIRED", "NAVITAIRE_DOCUMENT_STATUS_UNKNOWN"


class ReferenceStyleDocumentValidationWorkflow:
    """Concurrent fan-out/fan-in validation compatible with both mock and sandbox adapters."""

    def __init__(
        self,
        *,
        timatic_validator: Callable[[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | list[dict[str, Any]] | None], Awaitable[dict[str, Any]]],
        navitaire_document_check: Callable[[str], Awaitable[Any]],
        concurrency: int = 8,
        mock_only: bool = True,
        provider_label: str = "REFERENCE_WORKFLOW",
    ) -> None:
        self._timatic_validator = timatic_validator
        self._navitaire_document_check = navitaire_document_check
        self._semaphore = asyncio.Semaphore(max(1, min(concurrency, 16)))
        self._mock_only = mock_only
        self._provider_label = provider_label

    async def validate(self, booking: dict[str, Any], documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        passengers = booking.get("passengers") or []
        journeys = booking.get("journeys") or []
        if not passengers or not journeys:
            return {
                "decision": "REVIEW_REQUIRED",
                "results": [],
                "counts": {"PASS": 0, "FAIL": 0, "REVIEW_REQUIRED": 1},
                "provider": self._provider_label,
                "mock_only": self._mock_only,
                "message": "Booking has no passenger or journey records; manual review is required.",
            }

        async def run_pair(passenger: dict[str, Any], journey: dict[str, Any]) -> dict[str, Any]:
            passenger_key = str(passenger.get("passenger_key", "")).strip()
            journey_key = str(journey.get("journey_key", "")).strip()
            pair_base = {
                "passenger_key": passenger_key,
                "journey_key": journey_key,
                "transaction_id": str(uuid.uuid4()),
                "provider": "MOCK_TIMATIC" if self._mock_only else "TIMATIC_STAGING",
                "mock_only": self._mock_only,
                "timatic_decision": "NOT_RUN",
                "navitaire_decision": "NOT_RUN",
                "decision": "REVIEW_REQUIRED",
                "evaluation_code": "VALIDATION_NOT_COMPLETED",
                "reasons": [],
            }
            async with self._semaphore:
                document = documents.get(passenger_key)
                if not isinstance(document, (dict, list)) or not document:
                    pair_base.update(
                        decision="REVIEW_REQUIRED",
                        evaluation_code="PASSPORT_NOT_FOUND",
                        reasons=["No document details were supplied for this passenger; refer to airport staff."],
                    )
                    return pair_base
                try:
                    timatic_result = await self._timatic_validator(booking, passenger, journey, document)
                except Exception as exc:
                    # Do not expose exception text or request details (could include credentials/PII).
                    pair_base.update(
                        decision="REVIEW_REQUIRED",
                        evaluation_code=f"TIMATIC_PROVIDER_ERROR_{type(exc).__name__}",
                        reasons=["Timatic could not complete the request; eligibility was not approved."],
                    )
                    return pair_base

                timatic_decision = str(timatic_result.get("decision", "REVIEW_REQUIRED")).upper()
                pair_base.update(
                    provider=str(timatic_result.get("provider", "MOCK_TIMATIC")),
                    mock_only=bool(timatic_result.get("mock_only", True)),
                    transaction_id=str(timatic_result.get("transaction_id") or pair_base["transaction_id"]),
                    timatic_decision=timatic_decision,
                    evaluation_code=str(timatic_result.get("evaluation_code", "UNMAPPED_PROVIDER_RESPONSE")),
                    reasons=[str(x)[:800] for x in (timatic_result.get("reasons") or [])][:20],
                )
                if timatic_decision != "PASS":
                    # Reference workflow only invokes the Navitaire document-check endpoint after
                    # Timatic returns YES. NO blocks; CONDITIONAL and unknown responses require review.
                    pair_base["decision"] = "FAIL" if timatic_decision == "FAIL" else "REVIEW_REQUIRED"
                    return pair_base

                try:
                    nav_status = await self._navitaire_document_check(passenger_key)
                    if isinstance(nav_status, dict):
                        raw = nav_status.get("documentCheckStatus", nav_status.get("status"))
                        if isinstance(nav_status.get("data"), dict):
                            raw = nav_status["data"].get("documentCheckStatus", raw)
                    else:
                        raw = nav_status
                    nav_decision, nav_code = decision_from_navitaire_document_status(raw)
                except Exception as exc:
                    nav_decision, nav_code = "REVIEW_REQUIRED", f"NAVITAIRE_PROVIDER_ERROR_{type(exc).__name__}"
                pair_base["navitaire_decision"] = nav_decision
                pair_base["evaluation_code"] = nav_code if nav_decision != "PASS" else pair_base["evaluation_code"]
                if nav_decision == "PASS":
                    pair_base["decision"] = "PASS"
                    pair_base["reasons"] = []
                else:
                    pair_base["decision"] = nav_decision
                    pair_base["reasons"] = ["Navitaire's passenger document-check did not return an explicit OK status."]
                return pair_base

        results = await asyncio.gather(
            *(run_pair(passenger, journey) for passenger in passengers for journey in journeys),
            return_exceptions=False,
        )
        decisions = [str(result["decision"]) for result in results]
        overall = "FAIL" if "FAIL" in decisions else ("REVIEW_REQUIRED" if "REVIEW_REQUIRED" in decisions or not decisions else "PASS")
        counts = {name: decisions.count(name) for name in ("PASS", "FAIL", "REVIEW_REQUIRED")}
        return {
            "decision": overall,
            "results": results,
            "counts": counts,
            "provider": self._provider_label,
            "mock_only": all(bool(result.get("mock_only", self._mock_only)) for result in results),
            "message": "Timatic is evaluated per passenger and journey; Navitaire document-check runs only after an explicit Timatic YES. Unknown or conditional results never pass.",
        }


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_navitaire_booking(
    raw_booking: dict[str, Any],
    *,
    record_locator: str,
    last_name: str,
) -> dict[str, Any]:
    """Normalize reference-style New Skies booking envelopes into the POC's internal contract."""
    root: Any = raw_booking
    for _ in range(4):
        if not isinstance(root, dict):
            break
        if isinstance(root.get("bookingRetrievev3"), dict):
            root = root["bookingRetrievev3"]
            break
        if isinstance(root.get("data"), dict):
            root = root["data"]
            continue
        break
    if not isinstance(root, dict):
        raise ValueError("Navitaire booking response does not contain a booking object")

    raw_journeys = root.get("journeys", [])
    if isinstance(raw_journeys, dict):
        raw_journeys = list(raw_journeys.values())
    journeys: list[dict[str, Any]] = []
    for index, item in enumerate(raw_journeys if isinstance(raw_journeys, list) else []):
        if not isinstance(item, dict):
            continue
        raw_journey_key = item.get("journeyKey", item.get("journey_key"))
        if not isinstance(raw_journey_key, str) or not raw_journey_key.strip():
            raise ValueError("Navitaire journey is missing its provider journeyKey")
        journey_key = raw_journey_key.strip()
        raw_segments = item.get("segments", [])
        if isinstance(raw_segments, dict):
            raw_segments = list(raw_segments.values())
        segments: list[dict[str, Any]] = []
        origins: list[str] = []
        destinations: list[str] = []
        departure_times: list[datetime] = []
        international = False
        carriers: list[str] = []
        operating_carriers: list[str] = []
        codeshare = False
        for seg in raw_segments if isinstance(raw_segments, list) else []:
            if not isinstance(seg, dict):
                continue
            designator = seg.get("designator", {}) if isinstance(seg.get("designator", {}), dict) else {}
            departure = seg.get("departure", {}) if isinstance(seg.get("departure", {}), dict) else {}
            arrival = seg.get("arrival", {}) if isinstance(seg.get("arrival", {}), dict) else {}
            origin = str(designator.get("origin", departure.get("point", seg.get("origin", "")))).upper()
            destination = str(designator.get("destination", arrival.get("point", seg.get("destination", "")))).upper()
            dep_value = designator.get("departure", departure.get("dateTime", ""))
            arr_value = designator.get("arrival", arrival.get("dateTime", ""))
            if not dep_value or not arr_value:
                legs = seg.get("legs", [])
                if isinstance(legs, dict):
                    legs = list(legs.values())
                first_leg = legs[0] if isinstance(legs, list) and legs and isinstance(legs[0], dict) else {}
                leg_info = first_leg.get("legInfo", {}) if isinstance(first_leg.get("legInfo", {}), dict) else {}
                dep_value = dep_value or leg_info.get("departureTimeUtc", "")
                arr_value = arr_value or leg_info.get("arrivalTimeUtc", "")
            dep_dt = _parse_datetime(dep_value)
            arr_dt = _parse_datetime(arr_value)
            if dep_dt:
                departure_times.append(dep_dt)
            if origin:
                origins.append(origin)
            if destination:
                destinations.append(destination)
            int_raw = seg.get("international", False)
            international = international or (int_raw is True or str(int_raw).lower() == "true")
            ext = seg.get("externalIdentifier")
            if isinstance(ext, dict) and ext:
                codeshare = True
                carrier = str(ext.get("carrierCode", "")).upper()
                if carrier:
                    carriers.append(carrier)
            raw_carrier = seg.get("operatingCarrier", seg.get("carrierCode", ""))
            if raw_carrier:
                normalized_carrier = str(raw_carrier).upper()
                carriers.append(normalized_carrier)
                operating_carriers.append(normalized_carrier)
            segments.append({
                "departure": {"dateTime": dep_dt.isoformat() if dep_dt else str(dep_value or ""), "point": origin, "type": "AIRPORT"},
                "arrival": {"dateTime": arr_dt.isoformat() if arr_dt else str(arr_value or ""), "point": destination, "type": "AIRPORT"},
                "luggageCollected": bool(seg.get("luggageCollected", True)),
                # Never infer the operating carrier from externalIdentifier: in the reference,
                # a non-empty externalIdentifier is itself a codeshare marker.
                "operatingCarrier": str(raw_carrier or "").upper(),
                "processingEntity": str(seg.get("processingEntity", "")).upper(),
            })
        dep_dt = min(departure_times) if departure_times else None
        hours = ((dep_dt - datetime.now(timezone.utc)).total_seconds() / 3600) if dep_dt else None
        origin = origins[0] if origins else str(item.get("origin", "")).upper()
        destination = destinations[-1] if destinations else str(item.get("destination", "")).upper()
        journeys.append({
            "journey_key": journey_key,
            "origin": origin,
            "destination": destination,
            "international": international,
            "departure_offset_hours": hours,
            "marketing_carrier": carriers[0] if carriers else "",
            "operating_carrier": str(item.get("operatingCarrier", item.get("operating_carrier", "")) or (operating_carriers[-1] if operating_carriers else "")).upper(),
            "codeshare": codeshare,
            "segments": segments,
        })

    raw_passengers = root.get("passengers", [])
    if isinstance(raw_passengers, dict):
        passenger_items = [{"key": key, "value": value} for key, value in raw_passengers.items()]
    else:
        passenger_items = raw_passengers if isinstance(raw_passengers, list) else []
    passengers: list[dict[str, Any]] = []
    for item in passenger_items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", item.get("passengerKey", item.get("passenger_key", "")))).strip()
        if not key:
            raise ValueError("Navitaire passenger is missing its provider passenger key")
        value = item.get("value", item)
        if not isinstance(value, dict):
            value = item
        name = value.get("name", {}) if isinstance(value.get("name", {}), dict) else {}
        info = value.get("info", {}) if isinstance(value.get("info", {}), dict) else {}
        infant = value.get("infant")
        travel_documents = value.get("travelDocuments", value.get("travel_documents", []))
        raw_gender = str(info.get("gender", value.get("gender", ""))).strip().upper()
        if raw_gender in {"1", "M", "MALE"}:
            normalized_gender = "M"
        elif raw_gender in {"2", "F", "FEMALE"}:
            normalized_gender = "F"
        else:
            # Unknown provider gender codes remain unknown; never infer a legal-document field.
            normalized_gender = ""
        passengers.append({
            "passenger_key": key or f"PAX-{len(passengers) + 1:03d}",
            "passenger_type": str(value.get("passengerTypeCode", value.get("passenger_type", "ADT"))).upper(),
            "first_name": str(name.get("first", value.get("first_name", ""))).strip(),
            "last_name": str(name.get("last", value.get("last_name", ""))).strip(),
            "title": str(name.get("title", value.get("title", ""))).strip(),
            "nationality": str(info.get("nationality", value.get("nationality", ""))).upper(),
            "residence_country": str(info.get("residentCountry", value.get("residentCountryCode", ""))).upper(),
            "country_of_birth": str(info.get("birthCountry", value.get("birthCountry", ""))).upper(),
            "date_of_birth": _as_date(info.get("dateOfBirth", value.get("dateOfBirth", ""))),
            "gender": normalized_gender,
            "infant": bool(infant),
            "provider_passenger_id": str(value.get("passengerId", key)),
            "travel_documents": travel_documents if isinstance(travel_documents, list) else [],
        })

    info = root.get("info", {}) if isinstance(root.get("info", {}), dict) else {}
    breakdown = root.get("breakdown", {}) if isinstance(root.get("breakdown", {}), dict) else {}
    paid_status = str(info.get("paidStatus", "")).strip()
    status = str(info.get("status", "")).strip()
    payment_status = "PAID_IN_FULL" if paid_status.lower() == "paidinfull" else paid_status.upper().replace(" ", "_")
    raw_queues = root.get("queues", [])
    if isinstance(raw_queues, dict):
        raw_queues = list(raw_queues.values())
    queues = [str(q.get("code", "")).strip() for q in raw_queues if isinstance(q, dict) and str(q.get("code", "")).strip()] if isinstance(raw_queues, list) else []
    balance = breakdown.get("balanceDue", root.get("balance_due", -1))
    try:
        balance = float(balance)
    except (TypeError, ValueError):
        balance = -1.0

    return {
        "record_locator": record_locator.upper(),
        "last_name": last_name.upper(),
        "booking_status": "CONFIRMED" if status.lower() == "confirmed" else status.upper().replace(" ", "_"),
        "payment_status": payment_status,
        "balance_due": balance,
        "passengers": passengers,
        "journeys": journeys,
        "queues": queues,
        "ssr_requirements_valid": False,
        "ssr_requirements_reason": "SSR requirements have not yet been retrieved.",
        "mock_timatic_case": "LIVE_PROVIDER_TEST",
        "demo_notes": "Normalized from authorized Navitaire non-production data; do not use for actual travel decisions.",
    }


def evaluate_navitaire_ssr_requirements(payload: Any) -> tuple[bool, str]:
    """Evaluate reference Navitaire SSR/check-in requirements, failing closed on unknown shapes.

    Passenger RestrictionType values and top-level CheckinRestrictionType values are distinct
    enums in the 1.0.7 reference. The reference BookingService marks any top-level booking
    restriction invalid; we preserve that fail-closed behavior rather than silently ignoring a code.
    """
    if not isinstance(payload, dict):
        return False, "Navitaire SSR requirements response is missing or malformed."
    if payload.get("errors"):
        return False, "Navitaire returned an error for the journey's SSR requirements."
    root = payload.get("data", payload)
    if not isinstance(root, dict):
        return False, "Navitaire SSR requirements response is malformed."

    reasons: list[str] = []
    passenger_blocked_codes = {0, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14, 15, 16, 17}
    # The 1.0.7 BookingService marks any booking-level restriction invalid. The separate
    # check-in-window rule also runs later; do not silently ignore a provider restriction.
    top_level_ignored_codes: set[int] = set()
    recognized = any(key in root for key in ("passengers", "restrictions", "isValid"))

    passengers = root.get("passengers")
    if isinstance(passengers, dict):
        for pax_data in passengers.values():
            if not isinstance(pax_data, dict):
                reasons.append("Passenger requirements response had an unknown shape.")
                continue
            if pax_data.get("isValid") is False:
                reasons.append("A passenger's SSR requirements are invalid.")
            doc_requirements = pax_data.get("documentRequirements")
            if isinstance(doc_requirements, dict):
                if doc_requirements.get("isValid") is False:
                    reasons.append("A passenger's required travel documents are incomplete.")
                instances = doc_requirements.get("governmentDocumentInstances", [])
                for instance in instances if isinstance(instances, list) else []:
                    if not isinstance(instance, dict):
                        continue
                    try:
                        missing_count = int(instance.get("numberOfMissingDocuments", 0) or 0)
                    except (TypeError, ValueError):
                        missing_count = 1
                    if missing_count > 0:
                        reasons.append("A passenger has missing government-required documents.")
                    if instance.get("documentVerificationRequired") is True:
                        reasons.append("A passenger has documents requiring verification.")
            elif doc_requirements is not None:
                reasons.append("A passenger document-requirements field has an unknown shape.")
            address_requirements = pax_data.get("addressRequirements")
            if isinstance(address_requirements, dict) and address_requirements.get("isValid") is False:
                reasons.append("A passenger's required address data is incomplete.")
            elif address_requirements is not None and not isinstance(address_requirements, dict):
                reasons.append("A passenger address-requirements field has an unknown shape.")
            invalid_ssrs = pax_data.get("invalidSsrs")
            if isinstance(invalid_ssrs, list) and invalid_ssrs:
                reasons.append("A passenger has invalid SSR entries.")
            elif invalid_ssrs is not None and not isinstance(invalid_ssrs, list):
                reasons.append("A passenger invalid-SSR field has an unknown shape.")
            restrictions = pax_data.get("restrictions", [])
            if isinstance(restrictions, list):
                for restriction in restrictions:
                    if not isinstance(restriction, dict):
                        reasons.append("A passenger restriction has an unknown shape.")
                        continue
                    try:
                        code = int(restriction.get("restriction", -1))
                    except (TypeError, ValueError):
                        code = -1
                    if code in passenger_blocked_codes:
                        reasons.append("A passenger has a reference-defined check-in restriction.")
            elif restrictions is not None:
                reasons.append("A passenger restrictions field has an unknown shape.")
    elif passengers is not None:
        reasons.append("Passenger requirements field has an unrecognized shape.")

    top_restrictions = root.get("restrictions")
    if isinstance(top_restrictions, list):
        for restriction in top_restrictions:
            if isinstance(restriction, dict):
                raw_code = restriction.get("restriction", restriction.get("code", -1))
            else:
                raw_code = restriction
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                code = -1
            if code not in top_level_ignored_codes:
                reasons.append("Navitaire returned a booking-level check-in restriction.")
    elif top_restrictions is not None:
        reasons.append("Booking-level restrictions have an unrecognized shape.")

    if root.get("isValid") is False:
        reasons.append("Navitaire marked the journey check-in requirements invalid.")
    if not recognized:
        return False, "Navitaire SSR requirements response lacked recognized validation fields."
    if reasons:
        return False, "; ".join(dict.fromkeys(reasons))[:800]
    return True, "All recognized passenger and booking SSR requirements passed."
