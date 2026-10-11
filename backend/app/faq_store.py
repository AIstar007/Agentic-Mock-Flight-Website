from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

DEFAULT_KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge.json"
DEFAULT_STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "faq_corpus.sqlite3"
ALLOWED_BRAND_SCOPES = {"emirates_demo", "global_aviation", "project_policy"}
ALLOWED_SOURCE_TYPES = {
    "official_public_webpage", "official_regulation", "approved_internal_policy", "project_policy"
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FAQCorpusError(ValueError):
    pass


class FAQCorpusStore:
    """SQLite-backed, versioned FAQ corpus inspired by the provided PDP RAG pipeline.

    Documents are immutable within a version. A version becomes retrievable only after an
    explicit activation. It is a lexical/self-corrective retriever, not a claim of vector
    retrieval or model-graded factuality. Airline-specific reference documents are not
    eligible for the Emirates demo corpus.
    """

    def __init__(self, db_path: str | Path | None = None, seed_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_STORE_PATH
        self.seed_path = Path(seed_path) if seed_path else DEFAULT_KNOWLEDGE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS faq_versions (
                    version_id TEXT PRIMARY KEY,
                    corpus_name TEXT NOT NULL,
                    version_label TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('DRAFT','ACTIVE','SUPERSEDED')),
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    change_note TEXT NOT NULL DEFAULT '',
                    document_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_faq_versions_active
                    ON faq_versions(corpus_name, status, created_at);
                CREATE TABLE IF NOT EXISTS faq_documents (
                    version_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    brand_scope TEXT NOT NULL,
                    approved_for_use INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(version_id, document_id),
                    FOREIGN KEY(version_id) REFERENCES faq_versions(version_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_faq_documents_version
                    ON faq_documents(version_id, approved_for_use);
                """
            )

    def _seed_if_empty(self) -> None:
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM faq_versions").fetchone()[0]
        if count:
            return
        try:
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
            docs = payload["documents"]
        except Exception as exc:
            raise RuntimeError(f"Cannot load seed FAQ corpus: {type(exc).__name__}") from exc

        mapped = []
        for doc in docs:
            source_url = str(doc.get("source_url", ""))
            source_type = str(doc.get("source_type", "project_policy"))
            host = (urlparse(source_url).hostname or "").lower().rstrip(".")
            is_emirates_host = host == "emirates.com" or host.endswith(".emirates.com")
            if source_type == "project_policy":
                scope = "project_policy"
            elif is_emirates_host:
                scope = "emirates_demo"
            elif any(domain in host for domain in ("iata.org", "icao.int", "navitaire.com")) or source_type == "official_regulation":
                scope = "global_aviation"
            else:
                raise FAQCorpusError(f"Seed source {doc.get('id', 'unknown')} has no safe source scope; review it before enabling the FAQ corpus")
            mapped.append({
                "id": doc["id"], "title": doc["title"], "category": doc["category"],
                "jurisdiction": doc.get("jurisdiction", "unknown"), "source_type": source_type,
                "source_url": source_url, "verified_at": doc.get("verified_at", ""),
                "content": doc["content"], "brand_scope": scope, "approved_for_use": True,
            })
        self.create_version(
            version_label="emirates-demo-initial",
            documents=mapped,
            change_note="Seeded from the POC's reviewed knowledge.json file.",
            activate=True,
            version_id="emirates-demo-initial",
        )

    @staticmethod
    def validate_document(doc: dict[str, Any]) -> dict[str, Any]:
        required = ("id", "title", "category", "jurisdiction", "source_type", "source_url", "verified_at", "content", "brand_scope")
        missing = [key for key in required if not str(doc.get(key, "")).strip()]
        if missing:
            raise FAQCorpusError("FAQ document is missing required fields: " + ", ".join(missing))
        normalized = {key: str(doc[key]).strip() for key in required}
        if len(normalized["id"]) > 120 or len(normalized["title"]) > 240:
            raise FAQCorpusError("FAQ document ID or title is too long")
        if len(normalized["content"]) > 15000:
            raise FAQCorpusError("FAQ content exceeds 15,000 characters")
        if normalized["brand_scope"] not in ALLOWED_BRAND_SCOPES | {"legacy_airline_reference_only"}:
            raise FAQCorpusError("brand_scope must be emirates_demo, global_aviation, project_policy, or legacy_airline_reference_only")
        if normalized["source_type"] not in ALLOWED_SOURCE_TYPES | {"reference_only"}:
            raise FAQCorpusError("Unsupported source_type")
        host = (urlparse(normalized["source_url"]).hostname or "").lower().rstrip(".")
        is_emirates_host = host == "emirates.com" or host.endswith(".emirates.com")
        if normalized["brand_scope"] == "emirates_demo" and normalized["source_type"] != "project_policy" and not is_emirates_host:
            raise FAQCorpusError("Only an approved Emirates-hosted source or project-policy document may use emirates_demo scope; third-party airline material must not be presented as Emirates policy")
        if normalized["source_type"] != "project_policy" and not normalized["source_url"].startswith("https://"):
            raise FAQCorpusError("Non-project FAQ sources must use HTTPS URLs")
        normalized["approved_for_use"] = bool(doc.get("approved_for_use", False))
        return normalized

    def create_version(
        self,
        *,
        version_label: str,
        documents: Iterable[dict[str, Any]],
        change_note: str = "",
        activate: bool = False,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        if not version_label.strip() or len(version_label) > 100:
            raise FAQCorpusError("version_label is required and must be at most 100 characters")
        docs = [self.validate_document(d) for d in documents]
        if not docs:
            raise FAQCorpusError("A version must contain at least one FAQ document")
        if len(docs) > 500:
            raise FAQCorpusError("A single FAQ version cannot contain more than 500 documents")
        total_chars = sum(len(d["content"]) for d in docs)
        if total_chars > 1_500_000:
            raise FAQCorpusError("FAQ version exceeds the 1.5 MB text limit")
        ids = [d["id"] for d in docs]
        if len(ids) != len(set(ids)):
            raise FAQCorpusError("Document IDs must be unique within a FAQ version")
        version_id = version_id or ("faq-" + uuid.uuid4().hex[:16])
        now = utc_now()
        status = "DRAFT"
        with self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO faq_versions(version_id, corpus_name, version_label, status, created_at, change_note, document_count) VALUES (?, 'emirates_demo', ?, ?, ?, ?, ?)",
                    (version_id, version_label, status, now, change_note[:1000], len(docs)),
                )
                db.executemany(
                    """INSERT INTO faq_documents(version_id, document_id, title, category, jurisdiction, source_type,
                       source_url, verified_at, content, brand_scope, approved_for_use)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(
                        version_id, d["id"], d["title"], d["category"], d["jurisdiction"], d["source_type"],
                        d["source_url"], d["verified_at"], d["content"], d["brand_scope"], int(d["approved_for_use"]),
                    ) for d in docs],
                )
            except sqlite3.IntegrityError as exc:
                raise FAQCorpusError("FAQ version ID already exists") from exc
        if activate:
            self.activate_version(version_id)
        return self.get_version(version_id)

    def activate_version(self, version_id: str) -> dict[str, Any]:
        with self._connect() as db:
            version = db.execute("SELECT * FROM faq_versions WHERE version_id=? AND corpus_name='emirates_demo'", (version_id,)).fetchone()
            if version is None:
                raise FAQCorpusError("FAQ version not found")
            docs = db.execute("SELECT * FROM faq_documents WHERE version_id=?", (version_id,)).fetchall()
            if not docs:
                raise FAQCorpusError("Cannot activate an empty FAQ version")
            for doc in docs:
                if not doc["approved_for_use"]:
                    raise FAQCorpusError(f"Document {doc['document_id']} is not marked approved_for_use")
                if doc["brand_scope"] not in ALLOWED_BRAND_SCOPES:
                    raise FAQCorpusError(f"Document {doc['document_id']} is not eligible for the Emirates demo corpus")
                host = (urlparse(doc["source_url"]).hostname or "").lower().rstrip(".")
                is_emirates_host = host == "emirates.com" or host.endswith(".emirates.com")
                if doc["brand_scope"] == "emirates_demo" and doc["source_type"] != "project_policy" and not is_emirates_host:
                    raise FAQCorpusError("Third-party airline material cannot be activated as Emirates FAQ content")
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE faq_versions SET status='SUPERSEDED' WHERE corpus_name='emirates_demo' AND status='ACTIVE'")
            db.execute(
                "UPDATE faq_versions SET status='ACTIVE', activated_at=? WHERE version_id=?",
                (utc_now(), version_id),
            )
        return self.get_version(version_id)

    def get_version(self, version_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM faq_versions WHERE version_id=?", (version_id,)).fetchone()
            if row is None:
                raise FAQCorpusError("FAQ version not found")
            return dict(row)

    def list_versions(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT version_id, corpus_name, version_label, status, created_at, activated_at, change_note, document_count FROM faq_versions WHERE corpus_name='emirates_demo' ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_active_version(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT version_id, version_label, status, activated_at FROM faq_versions WHERE corpus_name='emirates_demo' AND status='ACTIVE' ORDER BY activated_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def get_active_documents(self) -> list[dict[str, Any]]:
        version = self.get_active_version()
        if not version:
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_id, title, category, jurisdiction, source_type, source_url, verified_at, content, brand_scope FROM faq_documents WHERE version_id=? AND approved_for_use=1 AND brand_scope IN ('emirates_demo','global_aviation','project_policy')",
                (version["version_id"],),
            ).fetchall()
        return [{**dict(row), "id": row["document_id"], "corpus_version": version["version_id"], "version_label": version["version_label"]} for row in rows]

    def search_raw(self, query: str, limit: int = 5) -> dict[str, Any]:
        version = self.get_active_version()
        return {
            "query": query,
            "version": version,
            "results": [
                {"id": doc["id"], "title": doc["title"], "category": doc["category"], "content": doc["content"], "source_url": doc["source_url"], "source_type": doc["source_type"], "verified_at": doc["verified_at"]}
                for doc in self.get_active_documents()[:max(1, min(limit, 20))]
            ],
        }
