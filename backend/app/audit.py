from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import AgentEvent

SENSITIVE_KEYS = {"passport_number", "document_number", "passport", "visa_number", "password", "token", "authorization", "secret", "full_name", "last_name", "first_name", "birth_date", "date_of_birth", "pnr", "record_locator"}


def session_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class AuditSink:
    def __init__(self, mongo_uri: str = "", database_name: str = "emirates_checkin_demo", ttl_seconds: int = 604800):
        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self.ttl_seconds = ttl_seconds
        self._client = None
        self._collection = None
        self.events: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        if not self.mongo_uri:
            return
        try:
            from pymongo import AsyncMongoClient
            self._client = AsyncMongoClient(self.mongo_uri, serverSelectionTimeoutMS=2000)
            await self._client.admin.command("ping")
            self._collection = self._client[self.database_name]["agent_audit_events"]
            await self._collection.create_index("expires_at", expireAfterSeconds=0)
        except Exception as exc:
            if self._client:
                await self._client.close()
            self._client = None
            self._collection = None
            raise RuntimeError("Configured MongoDB audit backend is unavailable") from exc

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def emit(self, session_id: str, component: str, action: str, outcome: str, message: str, correlation_id: str | None = None) -> AgentEvent:
        now = datetime.now(timezone.utc)
        event = AgentEvent(event_id=str(uuid.uuid4()), timestamp=now, component=component, action=action, outcome=outcome, message=message[:300], correlation_id=correlation_id)
        # Do not record passenger details, PNRs, passport values, prompts, or raw tool arguments.
        doc = {
            "event_id": event.event_id,
            "timestamp": now,
            "expires_at": now + timedelta(seconds=self.ttl_seconds),
            "session_fingerprint": session_fingerprint(session_id),
            "component": component,
            "action": action,
            "outcome": outcome,
            "message": message[:300],
            "correlation_id": correlation_id,
        }
        if self._collection is not None:
            await self._collection.insert_one(doc)
        else:
            self.events.append({**doc, "timestamp": now.isoformat(), "expires_at": doc["expires_at"].isoformat()})
            cutoff = now - timedelta(seconds=self.ttl_seconds)
            self.events = [e for e in self.events if datetime.fromisoformat(e["timestamp"]) >= cutoff]
        return event
