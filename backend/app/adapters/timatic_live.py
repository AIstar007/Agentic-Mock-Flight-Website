from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.adapters.provider_http import ProviderAPIError, _json_response, response_schema_summary
from app.reference_workflow import parse_reference_timatic_response

DEFAULT_TIMATIC_TOKEN_URL = "https://staging.timatic.iata.org/security-token-service/oauth2/token"
DEFAULT_TIMATIC_API_BASE = "https://staging.timatic.iata.org/query-interface-service/api/v1"


class TimaticStagingDiagnosticsAdapter:
    """IATA Timatic staging client based on the provided Bruno collection.

    It supports token acquisition, country/parameter reference lookups and documentRequest.
    It returns only response-schema metadata; it never returns raw provider response bodies.
    Unknown decision payloads map to REVIEW_REQUIRED until an authoritative response schema is
    supplied and explicitly mapped. It does not prove visa eligibility or immigration admission.
    """

    def __init__(
        self,
        *,
        token_url: str = DEFAULT_TIMATIC_TOKEN_URL,
        api_base_url: str = DEFAULT_TIMATIC_API_BASE,
        username: str,
        password: str,
        grant_type: str = "client_credentials",
        user_key: str = "",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token_url.startswith("https://") or not api_base_url.startswith("https://"):
            raise ValueError("Timatic URLs must use HTTPS")
        if not username.strip() or not password:
            raise ValueError("Timatic staging client credentials are required")
        self.token_url = token_url
        normalized_base = api_base_url.rstrip("/")
        if normalized_base.endswith("/query-interface-service/api/v1"):
            self.api_base_url = normalized_base
        elif normalized_base.endswith("/query-interface-service"):
            self.api_base_url = normalized_base + "/api/v1"
        else:
            # The user may paste either the Timatic host or the API base shown in Bruno.
            self.api_base_url = normalized_base + "/query-interface-service/api/v1"
        self.username = username
        self.password = password
        self.grant_type = grant_type
        self.user_key = user_key.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._token = ""
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds),
            verify=True,
            follow_redirects=False,
            transport=self.transport,
        )

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._token and now < self._expires_at - 60:
            return self._token
        async with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._expires_at - 60:
                return self._token
            try:
                response = await client.post(
                    self.token_url,
                    data={"grant_type": self.grant_type},
                    auth=(self.username, self.password),
                    headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", **({"user-key": self.user_key} if self.user_key else {})},
                )
            except httpx.TimeoutException as exc:
                raise ProviderAPIError("Timatic", "token_timeout") from exc
            except httpx.RequestError as exc:
                raise ProviderAPIError("Timatic", "token_network_or_dns_error") from exc
            body = _json_response(response, "Timatic token")
            if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
                raise ProviderAPIError("Timatic", "access_token_missing", response.status_code)
            try:
                expires_in = max(60, int(body.get("expires_in", 3600)))
            except (TypeError, ValueError):
                expires_in = 3600
            self._token = body["access_token"]
            self._expires_at = time.monotonic() + expires_in
            return self._token

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> tuple[Any, int]:
        try:
            async with await self._client() as client:
                token = await self._get_token(client)
                response = await client.request(
                    method,
                    self.api_base_url + "/" + path.lstrip("/"),
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **({"user-key": self.user_key} if self.user_key else {}), **kwargs.pop("headers", {})},
                    **kwargs,
                )
        except httpx.TimeoutException as exc:
            raise ProviderAPIError("Timatic", "request_timeout") from exc
        except httpx.RequestError as exc:
            raise ProviderAPIError("Timatic", "network_or_dns_error") from exc
        return _json_response(response, "Timatic"), response.status_code

    async def country_lookup(self, code: str, name: str) -> dict[str, Any]:
        if not code or not name:
            raise ValueError("Country code and name are required")
        body, status = await self._request_json(
            "GET", "locations/COUNTRY", params={"code": code.upper(), "name": name}
        )
        return {
            "provider": "TIMATIC_STAGING",
            "operation": "country_lookup",
            "http_status": status,
            "response_schema": response_schema_summary(body),
            "raw_response_returned": False,
        }

    async def parameter_lookup(self, code: str) -> dict[str, Any]:
        if not code:
            raise ValueError("Parameter code is required")
        body, status = await self._request_json("GET", "parameters", params={"code": code})
        return {
            "provider": "TIMATIC_STAGING",
            "operation": "parameter_lookup",
            "http_status": status,
            "response_schema": response_schema_summary(body),
            "raw_response_returned": False,
        }

    async def evaluate_document_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Call Timatic and return a strict structured outcome for workflow orchestration.

        Raw provider payloads are parsed in memory only and are never returned or logged. Only an
        explicit Timatic evaluationResult=YES maps to PASS in the reference response contract.
        """
        required = {"documentDetails", "itineraryDetails", "passengerDetails", "passengerId", "transactionId"}
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError("Missing required Timatic request fields: " + ", ".join(missing))
        if not isinstance(payload.get("documentDetails"), list) or not payload["documentDetails"]:
            raise ValueError("documentDetails must be a non-empty list")
        itinerary = payload.get("itineraryDetails")
        if not isinstance(itinerary, dict) or not isinstance(itinerary.get("segments"), list) or not itinerary["segments"]:
            raise ValueError("itineraryDetails.segments must be a non-empty list")
        body, status = await self._request_json("POST", "documentRequest", json=payload)
        decision, code, reasons = parse_reference_timatic_response(body)
        # The reference's provider model uses the top-level evaluationResult enum. Do not map
        # generic fields like decision=OK_TO_BOARD to approval until IATA's actual response
        # contract for this tenant explicitly documents that alternative shape.
        return {
            "provider": "TIMATIC_STAGING",
            "http_status": status,
            "decision": decision,
            "evaluation_code": code,
            "reasons": reasons,
            "transaction_id": str(payload.get("transactionId", "")),
            "mock_only": False,
            "response_schema": response_schema_summary(body),
            "raw_response_returned": False,
        }

    async def document_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Diagnostics response: decision plus schema metadata, never raw values."""
        result = await self.evaluate_document_request(payload)
        code = result["evaluation_code"]
        return {
            "provider": "TIMATIC_STAGING",
            "operation": "document_request",
            "http_status": result["http_status"],
            "decision": result["decision"],
            "evaluation_code": code,
            "reason_count": len(result["reasons"]),
            "response_schema": result["response_schema"],
            "raw_response_returned": False,
            "message": (
                "Timatic response was mapped against the reference evaluationResult contract."
                if code != "UNMAPPED_PROVIDER_RESPONSE"
                else "Request succeeded, but the response decision schema is unmapped. Manual review is required; no approval is inferred."
            ),
        }
