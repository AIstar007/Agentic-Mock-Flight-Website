from __future__ import annotations

import re
from typing import Any

import httpx


class ProviderAPIError(RuntimeError):
    """Safe provider error. Never stores a raw response body or credential."""

    def __init__(self, provider: str, category: str, http_status: int | None = None):
        self.provider = provider
        self.category = category
        self.http_status = http_status
        message = f"{provider} request failed ({category})"
        if http_status is not None:
            message += f", HTTP {http_status}"
        super().__init__(message)


def response_schema_summary(value: Any, *, max_depth: int = 5, max_fields: int = 80) -> dict[str, Any]:
    """Return response shape only; do not expose names, document numbers, tokens, or raw values."""
    fields: list[dict[str, str]] = []
    sensitive = re.compile(
        r"(token|secret|password|authorization|cookie|passport|documentnumber|documentid|"
        r"birthdate|dateofbirth|firstname|lastname|fullname|passengername|recordlocator|pnr|email|phone)",
        re.IGNORECASE,
    )

    def visit(item: Any, path: str, depth: int) -> None:
        if len(fields) >= max_fields:
            return
        if isinstance(item, dict):
            if depth >= max_depth:
                fields.append({"path": path or "$", "type": "object"})
                return
            for key, child in item.items():
                key_str = str(key)
                # Provider payloads frequently use passenger IDs, journey keys, PNRs, or
                # opaque tokens as dictionary keys. Schema diagnostics must not echo those
                # values in a JSON path. Keep conventional lower-camel/snake schema names;
                # redact all non-schema-like map keys (e.g. PAX-001, EM8K2Q, opaque IDs).
                safe_key = key_str if re.fullmatch(r"[a-z][A-Za-z0-9_]*", key_str) else "<map-key>"
                child_path = f"{path}.{safe_key}" if path else safe_key
                if sensitive.search(key_str):
                    fields.append({"path": child_path, "type": "redacted"})
                elif isinstance(child, (dict, list)):
                    visit(child, child_path, depth + 1)
                else:
                    fields.append({"path": child_path, "type": _type_name(child)})
                if len(fields) >= max_fields:
                    break
        elif isinstance(item, list):
            if not item:
                fields.append({"path": f"{path}[]", "type": "empty_array"})
            else:
                visit(item[0], f"{path}[]", depth + 1)
        else:
            fields.append({"path": path or "$", "type": _type_name(item)})

    visit(value, "", 0)
    return {"fields": fields, "truncated": len(fields) >= max_fields}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _json_response(response: httpx.Response, provider: str) -> Any:
    if response.status_code < 200 or response.status_code >= 300:
        if response.status_code in (401, 403):
            category = "authentication_or_authorization"
        elif response.status_code == 404:
            category = "endpoint_not_found"
        elif response.status_code == 429:
            category = "rate_limited"
        elif response.status_code >= 500:
            category = "provider_server_error"
        else:
            category = "provider_http_error"
        raise ProviderAPIError(provider, category, response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderAPIError(provider, "invalid_json_response", response.status_code) from exc
