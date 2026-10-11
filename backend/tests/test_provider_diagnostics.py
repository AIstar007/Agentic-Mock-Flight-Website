from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.adapters.navitaire_live import NavitaireLiveDiagnosticsAdapter
from app.adapters.provider_http import ProviderAPIError
from app.adapters.timatic_live import TimaticStagingDiagnosticsAdapter
from app.provider_models import TimaticDocumentRequest


@pytest.mark.asyncio
async def test_navitaire_readonly_booking_probe_sends_contract_and_returns_shape_only():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"bookingRetrievev3": {"journeys": [{"journeyKey": "opaque-key"}]}}})

    adapter = NavitaireLiveDiagnosticsAdapter(
        base_url="https://dotrezapi.test.6e.navitaire.com",
        agent_token="synthetic-test-token",
        user_key="synthetic-test-user-key",
        booking_path="/api/v2/graph/BookingRetrieve",
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.booking_retrieve("QQG6YV", "Saqib")
    assert captured["url"].endswith("/api/v2/graph/BookingRetrieve")
    assert captured["headers"]["authorization"] == "synthetic-test-token"
    assert captured["headers"]["user_key"] == "synthetic-test-user-key"
    assert captured["payload"]["variables"] == {"recordLocator": "QQG6YV", "lastName": "Saqib"}
    assert result["journey_count"] == 1
    assert result["raw_response_returned"] is False
    assert "opaque-key" not in json.dumps(result)
    assert "Saqib" not in json.dumps(result)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_navitaire_probe_maps_auth_failure_without_leaking_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "token synthetic-test-token rejected"})

    adapter = NavitaireLiveDiagnosticsAdapter(
        base_url="https://dotrezapi.test.6e.navitaire.com",
        agent_token="synthetic-test-token",
        user_key="synthetic-test-user-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderAPIError) as info:
        await adapter.booking_retrieve("QQG6YV", "Saqib")
    assert info.value.category == "authentication_or_authorization"
    assert "synthetic-test-token" not in str(info.value)
    await adapter.aclose()


@pytest.mark.asyncio
async def test_timatic_token_country_and_document_request_are_called_with_bearer_token():
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/security-token-service/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form.get("grant_type") == ["client_credentials"]
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "synthetic-bearer-token", "expires_in": 3600, "token_type": "Bearer"})
        assert request.headers["authorization"] == "Bearer synthetic-bearer-token"
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/locations/COUNTRY"):
            return httpx.Response(200, json={"country": {"code": "IN", "name": "India"}})
        return httpx.Response(200, json={"evaluationResult": "YES", "note": "Synthetic successful decision"})

    adapter = TimaticStagingDiagnosticsAdapter(
        token_url="https://staging.timatic.iata.org/security-token-service/oauth2/token",
        api_base_url="https://staging.timatic.iata.org",
        username="synthetic-client-id",
        password="synthetic-client-secret",
        transport=httpx.MockTransport(handler),
    )
    country = await adapter.country_lookup("IN", "India")
    assert country["http_status"] == 200
    result = await adapter.document_request({
        "documentDetails": [{"documentCode": "PASSPORT", "documentExpiryDate": "2030-01-01T00:00:00+00:00", "documentIssueCountry": "IN", "nationality": "IN", "birthDate": "1999-01-01"}],
        "itineraryDetails": {"segments": [{"departure": {"dateTime": "2027-01-01T09:00:00+05:30", "point": "DEL", "type": "AIRPORT"}, "arrival": {"dateTime": "2027-01-01T12:00:00+04:00", "point": "DXB", "type": "AIRPORT"}, "luggageCollected": True, "operatingCarrier": "EK", "processingEntity": "EK"}]},
        "passengerDetails": {"birthCountry": "IN", "birthDate": "1999-01-01", "gender": "M", "nationality": "IN", "residentCountryCode": "IND"},
        "passengerId": "a5a5a5a5-0000-4000-8000-000000000001", "transactionId": "synthetic-tx-001", "channel": "Web", "language": "EN",
    })
    assert result["decision"] == "PASS"
    assert result["raw_response_returned"] is False
    assert len(calls) == 2
    assert calls[-1][1].endswith("/documentRequest")
    assert calls[-1][2]["documentDetails"][0]["documentCode"] == "PASSPORT"


@pytest.mark.asyncio
async def test_timatic_generic_http_ok_does_not_automatically_pass_eligibility():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200, json={"status": "OK", "message": "Request accepted"})

    adapter = TimaticStagingDiagnosticsAdapter(
        token_url="https://staging.timatic.iata.org/security-token-service/oauth2/token",
        api_base_url="https://staging.timatic.iata.org/query-interface-service/api/v1",
        username="client", password="secret", transport=httpx.MockTransport(handler),
    )
    result = await adapter.document_request({
        "documentDetails": [{"documentCode": "PASSPORT"}],
        "itineraryDetails": {"segments": [{}]},
        "passengerDetails": {}, "passengerId": "synthetic", "transactionId": "synthetic",
    })
    assert result["decision"] == "REVIEW_REQUIRED"
    assert result["evaluation_code"] == "UNMAPPED_PROVIDER_RESPONSE"


def test_timatic_reference_contract_requires_evaluation_result_enum():
    from app.reference_workflow import parse_reference_timatic_response
    assert parse_reference_timatic_response({"status": "OK"})[0] == "REVIEW_REQUIRED"
    assert parse_reference_timatic_response({"decision": "OK_TO_BOARD"})[0] == "REVIEW_REQUIRED"
    assert parse_reference_timatic_response({"evaluationResult": "YES"})[0] == "PASS"
    assert parse_reference_timatic_response({"evaluationResult": "NO"})[0] == "FAIL"


def test_provider_diagnostic_routes_are_disabled_by_default():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/v1/provider-tests/timatic/country?code=IN&name=India")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"].lower()


def test_timatic_provider_request_schema_accepts_synthetic_bruno_contract():
    payload = {
        "channel": "Web",
        "documentDetails": [{
            "documentCode": "PASSPORT",
            "documentExpiryDate": "2030-12-23T00:00:00+00:00",
            "documentIssueCountry": "IN",
            "nationality": "IN",
            "birthDate": "1990-06-20",
        }],
        "itineraryDetails": {"segments": [{
            "departure": {"dateTime": "2027-02-26T13:45:00+05:30", "point": "DEL", "type": "AIRPORT"},
            "arrival": {"dateTime": "2027-02-27T12:00:00+04:00", "point": "DXB", "type": "AIRPORT"},
            "luggageCollected": True,
            "operatingCarrier": "6E",
            "processingEntity": "6E",
        }]},
        "language": "EN",
        "passengerDetails": {
            "birthCountry": "IN", "birthDate": "1990-06-20", "gender": "M",
            "nationality": "IN", "residentCountryCode": "IND",
        },
        "passengerId": "a5a5a5a5-0000-4000-8000-000000000001",
        "transactionId": "synthetic-test-transaction-001",
    }
    request = TimaticDocumentRequest.model_validate(payload)
    provider_payload = request.to_provider_payload()
    assert provider_payload["documentDetails"][0]["documentCode"] == "PASSPORT"
    assert provider_payload["itineraryDetails"]["segments"][0]["departure"]["point"] == "DEL"
    assert provider_payload["passengerDetails"]["residentCountryCode"] == "IND"


@pytest.mark.asyncio
async def test_navitaire_reuses_client_cookies_between_graphql_and_rest_booking_state():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"set-cookie": "nav-session=synthetic-context; Path=/; HttpOnly"},
                json={"data": {"bookingRetrievev3": {"journeys": [], "passengers": []}}},
            )
        assert request.headers.get("cookie") == "nav-session=synthetic-context"
        return httpx.Response(200, json={"data": {"journeys": [], "passengers": []}})

    adapter = NavitaireLiveDiagnosticsAdapter(
        base_url="https://dotrezapi.test.6e.navitaire.com",
        agent_token="synthetic-token",
        user_key="synthetic-user-key",
        transport=httpx.MockTransport(handler),
    )
    try:
        await adapter.get_booking_for_validation("SYNTH1", "Synthetic")
    finally:
        await adapter.aclose()
    assert calls == [
        ("POST", "/api/v2/graph/BookingRetrieve"),
        ("GET", "/api/nsk/v1/booking"),
    ]


def test_response_schema_summary_does_not_echo_dynamic_provider_ids():
    from app.adapters.provider_http import response_schema_summary

    output = json.dumps(response_schema_summary({
        "passengers": {
            "PAX-SECRET-001": {"name": {"first": "Synthetic", "last": "Person"}},
            "EM8K2Q": {"passportNumber": "SYNTHETIC-DOCUMENT"},
        },
        "status": "ok",
    }))
    assert "PAX-SECRET-001" not in output
    assert "EM8K2Q" not in output
    assert "Synthetic" not in output
    assert "SYNTHETIC-DOCUMENT" not in output
