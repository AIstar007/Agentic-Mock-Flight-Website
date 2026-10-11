from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.rules import run_eligibility_concurrently
from app.reference_workflow import (
    ReferenceStyleDocumentValidationWorkflow,
    build_timatic_request,
    decision_from_navitaire_document_status,
    evaluate_navitaire_ssr_requirements,
    normalize_navitaire_booking,
    parse_reference_timatic_response,
)


def _booking(passenger_count: int = 2, journey_count: int = 2) -> tuple[dict, dict]:
    departure = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    arrival = (datetime.now(timezone.utc) + timedelta(hours=16)).isoformat()
    passengers = [
        {
            "passenger_key": f"PAX-{i}", "first_name": f"Passenger{i}", "last_name": "Synthetic",
            "title": "MR", "passenger_type": "ADT", "nationality": "IN",
            "residence_country": "IND", "country_of_birth": "IN", "date_of_birth": "1990-06-20",
            "gender": "M", "travel_documents": [],
        }
        for i in range(1, passenger_count + 1)
    ]
    journeys = [
        {
            "journey_key": f"JNY-{i}", "origin": "DEL", "destination": "DXB", "international": True,
            "departure_offset_hours": 10, "marketing_carrier": "EK", "operating_carrier": "EK",
            "codeshare": False,
            "segments": [{
                "departure": {"dateTime": departure, "point": "DEL", "type": "AIRPORT"},
                "arrival": {"dateTime": arrival, "point": "DXB", "type": "AIRPORT"},
                "operatingCarrier": "EK", "processingEntity": "EK", "luggageCollected": True,
            }],
        }
        for i in range(1, journey_count + 1)
    ]
    booking = {
        "record_locator": "SYNTH1", "last_name": "SYNTHETIC", "booking_status": "CONFIRMED",
        "payment_status": "PAID_IN_FULL", "balance_due": 0, "passengers": passengers,
        "journeys": journeys, "queues": [], "ssr_requirements_valid": True,
    }
    documents = {
        p["passenger_key"]: [{
            "document_type_code": "P", "expiry_date": "2034-12-31", "issuing_country": "IN",
            "nationality": "IN", "date_of_birth": "1990-06-20",
        }]
        for p in passengers
    }
    return booking, documents


def test_timatic_response_matches_reference_enum_and_fails_closed_for_unknowns():
    assert parse_reference_timatic_response({"evaluationResult": "YES"})[0] == "PASS"
    assert parse_reference_timatic_response({"evaluationResult": "NO"})[0] == "FAIL"
    assert parse_reference_timatic_response({"evaluationResult": "CONDITIONAL"})[0] == "REVIEW_REQUIRED"
    assert parse_reference_timatic_response({"status": "OK"})[0] == "REVIEW_REQUIRED"
    assert parse_reference_timatic_response({"decision": "OK_TO_BOARD"})[0] == "REVIEW_REQUIRED"
    assert parse_reference_timatic_response(None)[0] == "REVIEW_REQUIRED"


def test_navitaire_document_check_status_uses_reference_enum():
    assert decision_from_navitaire_document_status(1)[0] == "PASS"
    assert decision_from_navitaire_document_status(3)[0] == "FAIL"
    assert decision_from_navitaire_document_status(2)[0] == "REVIEW_REQUIRED"
    assert decision_from_navitaire_document_status(0)[0] == "REVIEW_REQUIRED"
    assert decision_from_navitaire_document_status("unexpected")[0] == "REVIEW_REQUIRED"


def test_build_timatic_request_supports_both_reference_and_bruno_document_codes():
    booking, documents = _booking(passenger_count=1, journey_count=1)
    passenger = booking["passengers"][0]
    journey = booking["journeys"][0]
    docs = documents[passenger["passenger_key"]] + [{
        "document_type_code": "VVV", "expiry_date": "2030-01-01", "issuing_country": "QA",
        "nationality": "IN", "date_of_birth": "1990-06-20",
    }]
    payload = build_timatic_request(booking, passenger, journey, docs, processing_entity="EK", live_contract=True)
    assert [d["documentCode"] for d in payload["documentDetails"]] == ["PASSPORT", "VVV"]
    assert payload["passengerDetails"]["birthCountry"] == "IN"
    assert payload["itineraryDetails"]["segments"][0]["operatingCarrier"] == "EK"
    assert payload["itineraryDetails"]["segments"][0]["processingEntity"] == "EK"
    assert "documentNumber" not in str(payload)
    assert payload["passengerId"] and payload["transactionId"]


def test_live_timatic_request_refuses_invented_birth_country_or_carrier():
    booking, documents = _booking(passenger_count=1, journey_count=1)
    passenger = booking["passengers"][0]
    journey = booking["journeys"][0]
    passenger["country_of_birth"] = ""
    with pytest.raises(ValueError, match="birth-country"):
        build_timatic_request(booking, passenger, journey, documents[passenger["passenger_key"]], processing_entity="EK", live_contract=True)

    passenger["country_of_birth"] = "IN"
    journey["segments"][0].pop("operatingCarrier")
    journey["operating_carrier"] = ""
    with pytest.raises(ValueError, match="operating carrier"):
        build_timatic_request(booking, passenger, journey, documents[passenger["passenger_key"]], processing_entity="EK", live_contract=True)


def test_navitaire_ssr_requirements_fail_closed_for_reference_restriction_codes():
    assert evaluate_navitaire_ssr_requirements({"data": {"passengers": {}, "restrictions": [], "isValid": True}})[0] is True
    assert evaluate_navitaire_ssr_requirements({"data": {"passengers": {}, "restrictions": [4], "isValid": True}})[0] is False
    assert evaluate_navitaire_ssr_requirements({"data": {"passengers": {}, "restrictions": [0], "isValid": True}})[0] is False
    assert evaluate_navitaire_ssr_requirements({"data": {"passengers": {"P1": {"restrictions": [{"restriction": 4}]}}, "restrictions": [], "isValid": True}})[0] is False
    assert evaluate_navitaire_ssr_requirements({"errors": [{"message": "synthetic"}]})[0] is False


def test_normalizer_maps_navitaire_graphql_booking_envelope():
    future = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=16)).isoformat()
    raw = {
        "data": {"bookingRetrievev3": {
            "journeys": [{"journeyKey": "opaque-journey", "segments": [{
                "designator": {"origin": "DEL", "destination": "DXB", "departure": future, "arrival": later},
                "international": True, "externalIdentifier": None, "carrierCode": "EK",
            }]}],
            "info": {"paidStatus": "PaidInFull", "status": "Confirmed"},
            "breakdown": {"balanceDue": 0},
            "passengers": [{"key": "opaque-pax", "value": {
                "passengerTypeCode": "ADT", "name": {"title": "MR", "first": "Synthetic", "last": "Passenger"},
                "info": {"nationality": "IN", "residentCountry": "IND", "birthCountry": "IN", "dateOfBirth": "1990-06-20", "gender": "M"},
                "travelDocuments": [{"documentTypeCode": "P", "expirationDate": "2034-12-31", "issuedByCode": "IN", "nationality": "IN"}],
            }}],
            "queues": [],
        }}
    }
    normalized = normalize_navitaire_booking(raw, record_locator="SYNTH1", last_name="Passenger")
    assert normalized["booking_status"] == "CONFIRMED"
    assert normalized["payment_status"] == "PAID_IN_FULL"
    assert normalized["balance_due"] == 0
    assert normalized["journeys"][0]["origin"] == "DEL"
    assert normalized["journeys"][0]["destination"] == "DXB"
    assert normalized["passengers"][0]["country_of_birth"] == "IN"
    assert normalized["passengers"][0]["travel_documents"][0]["documentTypeCode"] == "P"


@pytest.mark.asyncio
async def test_reference_workflow_fans_out_passenger_journey_pairs_and_aggregates():
    booking, documents = _booking(passenger_count=2, journey_count=2)
    timatic_calls: list[tuple[str, str]] = []
    nav_calls: list[str] = []

    async def timatic(booking_arg, passenger, journey, docs):
        timatic_calls.append((passenger["passenger_key"], journey["journey_key"]))
        return {"provider": "TIMATIC_STAGING", "mock_only": False, "decision": "PASS", "evaluation_code": "TIMATIC_EVALUATION_YES"}

    async def navitaire(passenger_key):
        nav_calls.append(passenger_key)
        return {"data": {"documentCheckStatus": 1}}

    workflow = ReferenceStyleDocumentValidationWorkflow(
        timatic_validator=timatic,
        navitaire_document_check=navitaire,
        concurrency=3,
        mock_only=False,
        provider_label="NAVITAIRE+TIMATIC_STAGING",
    )
    result = await workflow.validate(booking, documents)
    assert len(timatic_calls) == 4
    assert len(nav_calls) == 4
    assert result["decision"] == "PASS"
    assert result["counts"] == {"PASS": 4, "FAIL": 0, "REVIEW_REQUIRED": 0}


@pytest.mark.asyncio
async def test_navitaire_document_check_is_skipped_for_timatic_conditional():
    booking, documents = _booking(passenger_count=1, journey_count=1)
    nav_calls: list[str] = []

    async def timatic(booking_arg, passenger, journey, docs):
        return {"provider": "TIMATIC_STAGING", "mock_only": False, "decision": "REVIEW_REQUIRED", "evaluation_code": "TIMATIC_EVALUATION_CONDITIONAL"}

    async def navitaire(passenger_key):
        nav_calls.append(passenger_key)
        return {"documentCheckStatus": 1}

    result = await ReferenceStyleDocumentValidationWorkflow(
        timatic_validator=timatic,
        navitaire_document_check=navitaire,
        mock_only=False,
    ).validate(booking, documents)
    assert result["decision"] == "REVIEW_REQUIRED"
    assert nav_calls == []
    assert result["results"][0]["navitaire_decision"] == "NOT_RUN"


@pytest.mark.asyncio
async def test_one_failed_passenger_journey_blocks_aggregate():
    booking, documents = _booking(passenger_count=2, journey_count=1)

    async def timatic(booking_arg, passenger, journey, docs):
        decision = "FAIL" if passenger["passenger_key"] == "PAX-2" else "PASS"
        return {"provider": "TIMATIC_STAGING", "mock_only": False, "decision": decision, "evaluation_code": "synthetic"}

    async def navitaire(passenger_key):
        return {"documentCheckStatus": 1}

    result = await ReferenceStyleDocumentValidationWorkflow(
        timatic_validator=timatic,
        navitaire_document_check=navitaire,
        mock_only=False,
    ).validate(booking, documents)
    assert result["decision"] == "FAIL"
    assert result["counts"]["FAIL"] == 1
    assert result["counts"]["PASS"] == 1


@pytest.mark.asyncio
async def test_reference_carrier_gate_requires_explicit_allowed_carrier_and_blocks_codeshare():
    booking, _ = _booking(passenger_count=1, journey_count=1)
    missing_config = await run_eligibility_concurrently(
        booking, carrier_mode="provider_reference", window_profile="reference"
    )
    assert missing_config.decision.value == "FAIL"
    assert any(rule.rule_id == "PROVIDER_CARRIER_CONFIGURED" and rule.decision.value == "FAIL" for rule in missing_config.rules)

    configured = await run_eligibility_concurrently(
        booking, carrier_mode="provider_reference", window_profile="reference", provider_allowed_carrier="EK"
    )
    assert configured.decision.value == "PASS"

    booking["journeys"][0]["codeshare"] = True
    codeshare = await run_eligibility_concurrently(
        booking, carrier_mode="provider_reference", window_profile="reference", provider_allowed_carrier="EK"
    )
    assert codeshare.decision.value == "FAIL"


@pytest.mark.asyncio
async def test_protected_reference_endpoint_executes_full_navitaire_timatic_sequence(monkeypatch):
    from types import SimpleNamespace

    import app.main as main
    from app.provider_models import NavitaireBookingRetrieveTest

    departure = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    arrival = (datetime.now(timezone.utc) + timedelta(hours=16)).isoformat()
    raw_booking = {
        "journeys": [{
            "journeyKey": "synthetic-journey-1",
            "segments": [{
                "segmentKey": "synthetic-segment-1",
                "international": True,
                "carrierCode": "EK",
                "externalIdentifier": None,
                "designator": {"origin": "DEL", "destination": "DXB", "departure": departure, "arrival": arrival},
            }],
        }],
        "info": {"paidStatus": "PaidInFull", "status": "Confirmed"},
        "breakdown": {"balanceDue": 0},
        "passengers": [{
            "key": "synthetic-passenger-1",
            "value": {
                "passengerTypeCode": "ADT",
                "name": {"title": "MR", "first": "Synthetic", "last": "Passenger"},
                "info": {
                    "nationality": "IN", "residentCountry": "IND", "birthCountry": "IN",
                    "dateOfBirth": "1990-06-20", "gender": "1",
                },
                "travelDocuments": [{
                    "documentTypeCode": "P", "expirationDate": "2034-12-31T00:00:00+00:00",
                    "issuedByCode": "IN", "nationality": "IN",
                }],
            },
        }],
        "queues": [],
    }

    class FakeNavitaire:
        async def get_booking_for_validation(self, record_locator, last_name):
            assert record_locator == "SYNTH1"
            return raw_booking

        async def get_checkin_requirements(self, journey_key):
            assert journey_key == "synthetic-journey-1"
            return {"data": {"passengers": {}, "restrictions": [], "isValid": True}}

        async def document_check(self, passenger_key):
            assert passenger_key == "synthetic-passenger-1"
            return {"data": {"documentCheckStatus": 1}}

    class FakeTimatic:
        calls = 0

        async def evaluate_document_request(self, payload):
            self.calls += 1
            assert payload["itineraryDetails"]["segments"][0]["operatingCarrier"] == "EK"
            assert payload["itineraryDetails"]["segments"][0]["processingEntity"] == "EK"
            assert "documentNumber" not in str(payload)
            return {
                "provider": "TIMATIC_STAGING", "mock_only": False,
                "decision": "PASS", "evaluation_code": "TIMATIC_EVALUATION_YES",
                "transaction_id": payload["transactionId"], "reasons": [],
            }

    fake_nav = FakeNavitaire()
    fake_timatic = FakeTimatic()
    monkeypatch.setattr(main, "settings", SimpleNamespace(navitaire_allowed_carrier="EK", timatic_processing_entity="EK"))
    monkeypatch.setattr(main, "_navitaire_diagnostics_adapter", lambda: fake_nav)
    monkeypatch.setattr(main, "_timatic_diagnostics_adapter", lambda: fake_timatic)

    result = await main.provider_test_reference_document_workflow(
        NavitaireBookingRetrieveTest(record_locator="SYNTH1", last_name="Passenger"), None
    )
    assert result["decision"] == "PASS"
    assert result["timatic_requests_attempted"] == 1
    assert result["counts"] == {"PASS": 1, "FAIL": 0, "REVIEW_REQUIRED": 0}
    assert result["boarding_pass_issued"] is False
    assert result["document_results"][0]["navitaire_decision"] == "PASS"
    assert fake_timatic.calls == 1


def test_live_navitaire_normalizer_refuses_fabricated_journey_or_passenger_keys():
    import pytest
    from app.reference_workflow import normalize_navitaire_booking

    with pytest.raises(ValueError, match="provider journeyKey"):
        normalize_navitaire_booking(
            {"journeys": [{"segments": []}], "passengers": []},
            record_locator="SYNTH1", last_name="Passenger",
        )

    with pytest.raises(ValueError, match="provider passenger key"):
        normalize_navitaire_booking(
            {
                "journeys": [{"journeyKey": "J-1", "segments": []}],
                "passengers": [{"value": {"name": {"first": "Synthetic", "last": "Passenger"}}}],
            },
            record_locator="SYNTH1", last_name="Passenger",
        )
