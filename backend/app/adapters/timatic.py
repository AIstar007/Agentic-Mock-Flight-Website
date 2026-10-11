from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any


class MockTimaticAdapter:
    """Timatic-style contract for synthetic data only. Never an official visa/boarding decision."""

    def build_request(self, booking: dict[str, Any], passenger: dict[str, Any], journey: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        departure_dt = datetime.now(timezone.utc) + timedelta(hours=float(journey["departure_offset_hours"]))
        # Duration is synthetic and used only to build a coherent mock itinerary envelope.
        arrival_dt = departure_dt + timedelta(hours=float(journey.get("duration_hours", 6)))
        return {
            "channel": "WEB_CHECKIN_DEMO",
            "documentDetails": [{
                "documentCode": "P",
                "documentNumber": doc.get("passport_number", ""),
                "documentExpiryDate": doc.get("expiry_date", ""),
                "documentIssueCountry": doc.get("issuing_country", ""),
                "nationality": doc.get("nationality", ""),
                "purposeOfStay": doc.get("purpose_of_stay", "TOURISM"),
                "durationOfStayDays": doc.get("duration_of_stay_days", 7),
            }],
            "itineraryDetails": {"segments": [{
                "departure": {"dateTime": departure_dt.isoformat(), "point": journey["origin"], "type": "DEPARTURE"},
                "arrival": {"dateTime": arrival_dt.isoformat(), "point": journey["destination"], "type": "ARRIVAL"},
                "luggageCollected": True,
                "operatingCarrier": journey["operating_carrier"],
                "processingEntity": "MOCK_AIRLINE",
            }]},
            "language": "en",
            "passengerDetails": {
                "birthDate": doc.get("date_of_birth", passenger.get("date_of_birth", "")),
                "gender": passenger.get("gender", "U"),
                "nationality": doc.get("nationality", passenger.get("nationality", "")),
                "residentCountryCode": passenger.get("residence_country", ""),
                "purposeOfStay": doc.get("purpose_of_stay", "TOURISM"),
                "durationOfStayDays": doc.get("duration_of_stay_days", 7),
            },
            "passengerId": passenger["passenger_key"],
            "transactionId": str(uuid.uuid4()),
            "providerMode": "MOCK_ONLY",
        }

    async def validate(self, booking: dict[str, Any], passenger: dict[str, Any], journey: dict[str, Any], doc: dict[str, Any] | None) -> dict[str, Any]:
        transaction_id = str(uuid.uuid4())
        if not doc:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DOCUMENT_MISSING", ["Passport details are missing; document review is required."])
        try:
            import pycountry
            for field in ("nationality", "issuing_country"):
                code = str(doc.get(field, "")).strip().upper()
                found = pycountry.countries.get(alpha_2=code) if len(code) == 2 else pycountry.countries.get(alpha_3=code) if len(code) == 3 else None
                if not found:
                    return self._result(passenger, journey, transaction_id, "FAIL", "COUNTRY_CODE_INVALID", [f"{field.replace('_', ' ').capitalize()} must be a valid ISO-3166 alpha-2 or alpha-3 code; '{code or 'blank'}' is not recognized."])
        except ImportError:
            if not all(len(str(doc.get(field, "")).strip()) in (2, 3) and str(doc.get(field, "")).isalpha() for field in ("nationality", "issuing_country")):
                return self._result(passenger, journey, transaction_id, "FAIL", "COUNTRY_CODE_INVALID", ["Nationality and issuing country must use 2- or 3-letter country codes."])
        purpose = str(doc.get("purpose_of_stay", "")).upper()
        if purpose not in {"TOURISM", "BUSINESS", "TRANSIT", "STUDY", "WORK", "FAMILY", "MEDICAL"}:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "PURPOSE_OF_STAY_MISSING", ["Choose a valid purpose of stay before this document review can be evaluated."])
        try:
            duration = int(doc.get("duration_of_stay_days", 0))
        except (TypeError, ValueError):
            duration = 0
        if not 1 <= duration <= 365:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DURATION_OF_STAY_INVALID", ["Intended stay duration must be a whole number between 1 and 365 days."])
        try:
            expiry = date.fromisoformat(doc["expiry_date"])
        except (ValueError, KeyError, TypeError):
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DOCUMENT_DATE_INVALID", ["Passport expiry is missing or invalid."])
        category = str(passenger.get("passenger_type", "ADT")).upper()
        needs_dob = category in {"CHD", "CHILD", "INF", "INFANT", "SENIOR", "SENIOR_CITIZEN"} or bool(doc.get("senior_citizen"))
        birth = None
        if needs_dob:
            try:
                birth = date.fromisoformat(doc["date_of_birth"])
            except (ValueError, KeyError, TypeError):
                return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DOCUMENT_DOB_REQUIRED", ["Date of birth is required for this child/infant/senior passenger category."])
        elif doc.get("date_of_birth"):
            try: birth = date.fromisoformat(doc["date_of_birth"])
            except (ValueError, TypeError):
                return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DOCUMENT_DOB_INVALID", ["The optional date of birth, if supplied, must be valid."])
        departure_day = (datetime.now(timezone.utc) + timedelta(hours=float(journey["departure_offset_hours"]))).date()
        if expiry < departure_day:
            return self._result(passenger, journey, transaction_id, "FAIL", "PASSPORT_EXPIRED", ["Synthetic rule: passport expires before the journey departure."])
        if birth and birth > date.today():
            return self._result(passenger, journey, transaction_id, "FAIL", "DOB_IN_FUTURE", ["Document date of birth cannot be in the future."])
        if birth and passenger.get("date_of_birth") and birth.isoformat() != str(passenger.get("date_of_birth"))[:10]:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DOB_MISMATCH", ["Document date of birth differs from the booking; refer for document review."])
        if not journey.get("international"):
            try: request = self.build_request(booking, passenger, journey, doc)
            except Exception: request = None
            return {"passenger_key": passenger["passenger_key"], "journey_key": journey["journey_key"], "transaction_id": transaction_id, "decision": "PASS", "evaluation_code": "DOMESTIC_DOCUMENTS_PASSED", "reasons": ["Required passport, ISO country-code, expiry, DOB (when needed), purpose and duration structure passed. International visa rules do not apply to this synthetic domestic leg. No official Timatic call was made."], "sources": [{"title": "Mock document rules", "url": "backend/data/rules_catalog.json", "source_status": "SYNTHETIC_RULE_FIXTURE"}], "provider": "MOCK_TIMATIC", "mock_only": True, "request": request}
        # The 180-day threshold is a test rule in this mock dataset, not a universal country rule.
        if (expiry - departure_day).days < 180:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "PASSPORT_VALIDITY_REVIEW", ["Synthetic rule: remaining passport validity is below the demo threshold; requirements vary by route and nationality."])
        if booking.get("mock_timatic_case") == "VISA_REVIEW":
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "VISA_REQUIREMENT_REVIEW", ["The synthetic itinerary has been flagged for entry-permit verification; visa/entry-document requirements vary by nationality, destination and transit points. No official Timatic lookup is available in this local demo."])
        if purpose in {"WORK", "STUDY", "TRANSIT", "FAMILY", "MEDICAL"}:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "PURPOSE_REQUIRES_DESTINATION_RULES", [f"The stated purpose of stay ({purpose.lower()}) may require a work/study/transit/family/medical authorization. This local rule set cannot establish the destination's exact permit requirement; check-in remains blocked pending authoritative verification."])
        if duration > 90:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "DURATION_EXCEEDS_DEMO_THRESHOLD", [f"The intended stay is {duration} days, above the demo's 90-day screening threshold. Destination-specific admission duration rules have not been queried; authoritative verification is required."])
        if doc.get("visa_evidence_status") not in {None, "", "NOT_VERIFIED", "MOCK_VERIFIED", "NOT_APPLICABLE"}:
            return self._result(passenger, journey, transaction_id, "REVIEW_REQUIRED", "VISA_EVIDENCE_INVALID", ["Legacy visa-evidence state was malformed; the status is calculated by the mock rules and cannot be manually set by the passenger."])
        try:
            request = self.build_request(booking, passenger, journey, doc)
        except Exception:
            request = None
        return {
            "passenger_key": passenger["passenger_key"], "journey_key": journey["journey_key"],
            "transaction_id": transaction_id, "decision": "PASS", "evaluation_code": "MOCK_RULES_PASSED",
            "reasons": [f"Synthetic structure, expiry, country-code and purpose/duration rules passed for {purpose.lower()} lasting {duration} days. No official Timatic lookup or country-specific visa database was queried."],
            "sources": [{"title": "IATA Timatic AutoCheck overview", "url": "https://www.iata.org/en/services/compliance/timatic/autocheck/", "source_status": "REFERENCE_ONLY_NOT_QUERIED"}],
            "provider": "MOCK_TIMATIC", "mock_only": True, "request": request,
        }

    @staticmethod
    def _result(passenger: dict[str, Any], journey: dict[str, Any], transaction_id: str, decision: str, code: str, reasons: list[str]) -> dict[str, Any]:
        return {
            "passenger_key": passenger["passenger_key"], "journey_key": journey["journey_key"],
            "transaction_id": transaction_id, "decision": decision, "evaluation_code": code,
            "reasons": reasons,
            "sources": [{"title": "Mock document rules", "url": "backend/data/rules_catalog.json", "source_status": "SYNTHETIC_RULE_FIXTURE"}],
            "provider": "MOCK_TIMATIC", "mock_only": True, "request": None,
        }


class TimaticHttpAdapter:
    """Provider integration boundary, disabled in this POC until official contract/access is approved."""
    def __init__(self, *, base_url: str, username: str, password: str, user_key: str):
        if not base_url.startswith("https://"):
            raise ValueError("Live Timatic adapter requires HTTPS")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.user_key = user_key

    async def validate(self, *_args, **_kwargs):
        raise NotImplementedError("Confirm IATA AutoCheck API contract, credentials, tenant and data-processing terms before enabling live calls")
