from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Awaitable

from app.config import settings

logger = logging.getLogger(__name__)


class SessionNotFound(Exception):
    pass


class SessionUnauthorized(Exception):
    pass


class SessionExpired(Exception):
    pass


class SessionStore:
    """Session repository with Redis or MongoDB persistence; memory is local-demo only.

    Atlas M0 can serve the free hosted POC without sending database credentials over
    an unencrypted public Redis connection. Per-session updates are serialized within
    one application process; a multi-replica production system needs stronger atomicity.
    """

    def __init__(self, redis_url: str = "", ttl_seconds: int = 604800,
                 mongo_uri: str = "", database_name: str = "emirates_checkin_demo"):
        self.redis_url = redis_url
        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self.ttl_seconds = ttl_seconds
        self._redis = None
        self._mongo_client = None
        self._mongo_sessions = None
        self._memory: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self.redis_url:
            try:
                from redis.asyncio import Redis
                client = Redis.from_url(self.redis_url, decode_responses=True, socket_connect_timeout=2)
                await client.ping()
                self._redis = client
                logger.info("Session backend initialized: Redis")
                return
            except Exception as exc:
                logger.exception("REDIS_URL is configured but Redis is unavailable; refusing silent memory fallback")
                raise RuntimeError("Configured Redis session backend is unavailable") from exc
        if self.mongo_uri:
            try:
                from pymongo import AsyncMongoClient
                self._mongo_client = AsyncMongoClient(self.mongo_uri, serverSelectionTimeoutMS=3000)
                await self._mongo_client.admin.command("ping")
                self._mongo_sessions = self._mongo_client[self.database_name]["checkin_sessions"]
                await self._mongo_sessions.create_index("expires_at", expireAfterSeconds=0)
                logger.info("Session backend initialized: MongoDB")
                return
            except Exception as exc:
                if self._mongo_client:
                    await self._mongo_client.close()
                self._mongo_client = None
                self._mongo_sessions = None
                logger.exception("MONGODB_URI is configured but MongoDB sessions are unavailable")
                raise RuntimeError("Configured MongoDB session backend is unavailable") from exc

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
        if self._mongo_client:
            await self._mongo_client.close()

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    async def create(self, session: dict[str, Any]) -> None:
        sid = session["session_id"]
        async with await self._lock_for(sid):
            await self._write(sid, session)

    async def _write(self, sid: str, session: dict[str, Any]) -> None:
        if self._redis:
            await self._redis.setex(f"checkin:session:{sid}", self.ttl_seconds, json.dumps(session, separators=(",", ":")))
        elif self._mongo_sessions is not None:
            data = json.loads(json.dumps(session, default=_json_default))
            expires = datetime.fromisoformat(data["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            await self._mongo_sessions.replace_one(
                {"_id": sid},
                {"_id": sid, "session": data, "expires_at": expires, "updated_at": datetime.now(timezone.utc)},
                upsert=True,
            )
        else:
            self._memory[sid] = json.loads(json.dumps(session, default=_json_default))

    async def get(self, sid: str) -> dict[str, Any]:
        if self._redis:
            raw = await self._redis.get(f"checkin:session:{sid}")
            if raw is None:
                raise SessionNotFound("Session not found or expired")
            data = json.loads(raw)
        elif self._mongo_sessions is not None:
            doc = await self._mongo_sessions.find_one({"_id": sid})
            if doc is None:
                raise SessionNotFound("Session not found or expired")
            expires = doc.get("expires_at")
            if expires:
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    raise SessionExpired("Session has expired")
            data = doc.get("session", {})
            data = json.loads(json.dumps(data, default=_json_default))
        else:
            data = self._memory.get(sid)
            if data is None:
                raise SessionNotFound("Session not found or expired")
            expires = datetime.fromisoformat(data["expires_at"])
            if expires <= datetime.now(timezone.utc):
                self._memory.pop(sid, None)
                raise SessionExpired("Session has expired")
            data = json.loads(json.dumps(data, default=_json_default))
        return data

    async def update(self, sid: str, mutator: Callable[[dict[str, Any]], Any | Awaitable[Any]]) -> dict[str, Any]:
        """Serialize single-process updates. Use a provider-side transaction for shared booking mutations."""
        async with await self._lock_for(sid):
            state = await self.get(sid)
            result = mutator(state)
            if asyncio.iscoroutine(result):
                await result
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            state["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)).isoformat()
            await self._write(sid, state)
            return state


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
