from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.adapters.provider_http import ProviderAPIError, _json_response, response_schema_summary

DEFAULT_BOOKING_RETRIEVE_PATH = "/api/v2/graph/BookingRetrieve"
DEFAULT_BOOKING_STATE_PATH = "/api/nsk/v1/booking"
DEFAULT_CHECKIN_REQUIREMENTS_PATH = "/api/nsk/v2/booking/checkin/journey/{journey_key}/requirements"
DEFAULT_NAV_DOCUMENT_CHECK_PATH = "/api/nsk/v1/booking/passengers/{passenger_key}/documents/check"

# Query from the supplied release 1.0.7 Navitaire client. Kept configurable because field
# selection is deployment/schema-specific and must match the authorized New Skies environment.
DEFAULT_BOOKING_RETRIEVE_QUERY = """mutation BookingRetrievev3($recordLocator: String!, $lastName: String!) {
  bookingRetrievev3(request: { recordLocator: $recordLocator, lastName: $lastName }) {
    journeys { journeyKey segments { segmentKey international passengerSegment { key value { seats { unitKey } } } legs { legInfo { departureTimeUtc } } externalIdentifier { carrierCode } } }
    info { paidStatus status }
    breakdown { balanceDue }
    passengers { key value { passengerTypeCode name { title first last } infant { name { title first last } } } }
    queues { code }
  }
}"""


class NavitaireLiveDiagnosticsAdapter:
    """Authorized Navitaire/New Skies test adapter.

    Supports a read-only booking retrieval plus the reference's read-only requirements and
    passenger-document-check calls. It does not issue seat, ancillary, check-in or booking-update
    mutations. Raw provider payloads remain internal; diagnostic routes return shape summaries.
    """

    def __init__(
        self,
        *,
        base_url: str,
        agent_token: str,
        user_key: str,
        api_key: str = "",
        api_key_header: str = "",
        auth_scheme: str = "raw",
        booking_path: str = DEFAULT_BOOKING_RETRIEVE_PATH,
        booking_state_path: str = DEFAULT_BOOKING_STATE_PATH,
        checkin_requirements_path: str = DEFAULT_CHECKIN_REQUIREMENTS_PATH,
        document_check_path: str = DEFAULT_NAV_DOCUMENT_CHECK_PATH,
        graphql_query: str = "",
        timeout_seconds: float = 12.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Navitaire URL must use HTTPS")
        if not agent_token.strip() or not user_key.strip():
            raise ValueError("Navitaire agent token and user_key are required")
        if auth_scheme not in {"raw", "bearer"}:
            raise ValueError("NAVITAIRE_AUTH_SCHEME must be 'raw' or 'bearer'")
        if api_key and not api_key_header:
            raise ValueError("Set NAVITAIRE_API_KEY_HEADER to the header name specified by the provider")
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token.strip()
        self.user_key = user_key.strip()
        self.api_key = api_key.strip()
        self.api_key_header = api_key_header.strip()
        self.auth_scheme = auth_scheme
        self.booking_path = "/" + booking_path.lstrip("/")
        self.booking_state_path = "/" + booking_state_path.lstrip("/")
        self.checkin_requirements_path = "/" + checkin_requirements_path.lstrip("/")
        self.document_check_path = "/" + document_check_path.lstrip("/")
        self.graphql_query = graphql_query.strip() or DEFAULT_BOOKING_RETRIEVE_QUERY
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        # Keep one client for the adapter lifetime, as in the reference BaseExternalClient.
        # This preserves provider-issued cookies/session context between BookingRetrievev3 and
        # the follow-up REST booking-state, SSR and document-check calls.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            verify=True,
            follow_redirects=False,
            transport=self.transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        token = self.agent_token
        if self.auth_scheme == "bearer" and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": token,
            "user_key": self.user_key,
        }
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers

    async def _request_json(self, method: str, path: str, *, json_body: Any = None) -> tuple[Any, int]:
        try:
            response = await self._client.request(
                method, self.base_url + path, json=json_body, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise ProviderAPIError("Navitaire", "timeout") from exc
        except httpx.RequestError as exc:
            raise ProviderAPIError("Navitaire", "network_or_dns_error") from exc
        return _json_response(response, "Navitaire"), response.status_code

    async def _graphql_booking(self, record_locator: str, last_name: str) -> tuple[dict[str, Any], int]:
        if not record_locator or not last_name:
            raise ValueError("Record locator and surname are required")
        body, status = await self._request_json(
            "POST",
            self.booking_path,
            json_body={
                "query": self.graphql_query,
                "variables": {"recordLocator": record_locator.upper(), "lastName": last_name.strip()},
            },
        )
        if not isinstance(body, dict):
            raise ProviderAPIError("Navitaire", "unexpected_response_shape", status)
        errors = body.get("errors")
        if errors:
            raise ProviderAPIError("Navitaire", f"graphql_errors_{min(len(errors), 99)}", status)
        data = body.get("data")
        result = data.get("bookingRetrievev3") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise ProviderAPIError("Navitaire", "booking_result_missing", status)
        return result, status

    async def booking_retrieve(self, record_locator: str, last_name: str) -> dict[str, Any]:
        """Safe connectivity diagnostic; no booking fields are returned."""
        result, status = await self._graphql_booking(record_locator, last_name)
        journeys = result.get("journeys") if isinstance(result.get("journeys"), list) else []
        passengers = result.get("passengers") if isinstance(result.get("passengers"), list) else []
        return {
            "provider": "NAVITAIRE_NEW_SKIES_SANDBOX",
            "http_status": status,
            "operation": "BookingRetrievev3",
            "booking_found": bool(journeys or passengers),
            "journey_count": len(journeys),
            "passenger_count": len(passengers),
            "response_schema": response_schema_summary({"data": {"bookingRetrievev3": result}}),
            "raw_response_returned": False,
            "notice": "Read-only provider diagnostic. No booking mutations were executed.",
        }

    async def get_booking_state(self) -> dict[str, Any]:
        """Read current booking state using the REST path used by the supplied reference code."""
        body, _ = await self._request_json("GET", self.booking_state_path)
        if not isinstance(body, dict):
            raise ProviderAPIError("Navitaire", "booking_state_unexpected_shape")
        return body

    async def get_checkin_requirements(self, journey_key: str) -> dict[str, Any]:
        if not journey_key or len(journey_key) > 512:
            raise ValueError("Journey key is required")
        path = self.checkin_requirements_path.format(journey_key=quote(journey_key, safe=""))
        body, _ = await self._request_json("GET", path)
        if not isinstance(body, dict):
            raise ProviderAPIError("Navitaire", "requirements_unexpected_shape")
        return body

    async def document_check(self, passenger_key: str) -> dict[str, Any]:
        if not passenger_key or len(passenger_key) > 512:
            raise ValueError("Passenger key is required")
        path = self.document_check_path.format(passenger_key=quote(passenger_key, safe=""))
        body, _ = await self._request_json("GET", path)
        if not isinstance(body, dict):
            raise ProviderAPIError("Navitaire", "document_check_unexpected_shape")
        return body

    async def get_booking_for_validation(self, record_locator: str, last_name: str) -> dict[str, Any]:
        """Verify PNR via GraphQL, then load richer REST state required by the reference mapper.

        The REST endpoint can be session/tenant-specific. Failure is explicit and fail-closed; this
        method deliberately does not fall back to a partial GraphQL response with missing itinerary.
        """
        graph_booking, _ = await self._graphql_booking(record_locator, last_name)
        rest_payload = await self.get_booking_state()
        # Accept common response envelopes; the reference workflow calls GET /api/nsk/v1/booking
        # after authenticating the booking context. If the response includes a locator, verify it
        # before using any of its passenger/document data.
        if isinstance(rest_payload.get("data"), dict) and isinstance(rest_payload["data"].get("bookingRetrievev3"), dict):
            booking_data = rest_payload["data"]["bookingRetrievev3"]
        elif isinstance(rest_payload.get("bookingRetrievev3"), dict):
            booking_data = rest_payload["bookingRetrievev3"]
        elif isinstance(rest_payload.get("data"), dict):
            booking_data = rest_payload["data"]
        else:
            booking_data = rest_payload
        if not isinstance(booking_data, dict):
            raise ProviderAPIError("Navitaire", "booking_state_missing")

        def find_locator(value: Any) -> str | None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower().replace("_", "") in {"recordlocator", "recordlocatorkey", "pnr"} and isinstance(child, str) and child.strip():
                        return child.strip().upper()
                for child in value.values():
                    found = find_locator(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value[:100]:
                    found = find_locator(child)
                    if found:
                        return found
            return None

        observed_locator = find_locator(booking_data)
        if observed_locator and observed_locator != record_locator.strip().upper():
            raise ProviderAPIError("Navitaire", "active_booking_does_not_match_requested_locator")

        # Merge only fields absent from the richer REST response. This never replaces provider data
        # with a synthetic fallback and never forwards the raw data to the client.
        merged = dict(booking_data)
        for key in ("journeys", "passengers", "info", "breakdown", "queues"):
            if key not in merged and key in graph_booking:
                merged[key] = graph_booking[key]
        return merged
