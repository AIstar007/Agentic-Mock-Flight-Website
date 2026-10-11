from __future__ import annotations

"""Persistent local Chroma index for approved, source-labelled airline knowledge.

The activated versioned SQLite corpus remains the source of truth. Chroma stores a
searchable copy of those reviewed records. If Chroma or its embedding runtime is not
installed, the caller can fall back to the SQLite lexical retriever.
"""
import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_CLIENT = None
_COLLECTION = None
_SIGNATURE = None


def chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        return True
    except Exception:
        return False


def _paths():
    root = Path(__file__).resolve().parents[1] / "data"
    persist = Path(os.getenv("CHROMA_PERSIST_DIR", str(root / "chroma_db"))).expanduser()
    collection = os.getenv("CHROMA_COLLECTION", "emirates_aviation_knowledge_v1").strip() or "emirates_aviation_knowledge_v1"
    return persist, collection


def _approved_docs():
    from app.faq_store import FAQCorpusStore
    store = FAQCorpusStore()
    active = store.get_active_version()
    docs = store.get_active_documents()
    if not active or not docs:
        return [], "none"
    return docs, str(active.get("version_id") or active.get("version_label") or "active")


def _get_collection():
    global _CLIENT, _COLLECTION, _SIGNATURE
    import chromadb
    persist, collection_name = _paths()
    persist.mkdir(parents=True, exist_ok=True)
    docs, version = _approved_docs()
    signature = hashlib.sha256((version + "|" + str(len(docs)) + "|" + "|".join(str(d.get("id")) for d in docs)).encode()).hexdigest()
    with _LOCK:
        if _COLLECTION is not None and _SIGNATURE == signature:
            return _COLLECTION, docs
        _CLIENT = chromadb.PersistentClient(path=str(persist))
        try:
            collection = _CLIENT.get_collection(collection_name)
        except Exception:
            collection = _CLIENT.create_collection(collection_name, metadata={"hnsw:space": "cosine", "purpose": "approved airline knowledge retrieval"})
        if docs:
            ids, texts, metas = [], [], []
            for doc in docs:
                doc_id = str(doc.get("id", "unknown"))
                content = str(doc.get("content", "")).strip()
                if not content:
                    continue
                key = hashlib.sha256((version + "|" + doc_id).encode()).hexdigest()[:32]
                ids.append(key)
                texts.append("Title: " + str(doc.get("title", "")) + "\nCategory: " + str(doc.get("category", "")) + "\nJurisdiction: " + str(doc.get("jurisdiction", "")) + "\n" + content)
                metas.append({
                    "document_id": doc_id,
                    "title": str(doc.get("title", ""))[:240],
                    "category": str(doc.get("category", "general"))[:120],
                    "jurisdiction": str(doc.get("jurisdiction", "unspecified"))[:180],
                    "source_type": str(doc.get("source_type", "official_public_webpage"))[:80],
                    "source_url": str(doc.get("source_url", ""))[:1000],
                    "verified_at": str(doc.get("verified_at", ""))[:40],
                    "brand_scope": str(doc.get("brand_scope", "emirates_demo"))[:60],
                    "corpus_version": str(doc.get("corpus_version", version))[:120],
                })
            # Ensure this collection contains only the currently activated knowledge snapshot.
            existing = collection.get(include=[])
            existing_ids = existing.get("ids", [])
            if existing_ids:
                collection.delete(ids=existing_ids)
            for offset in range(0, len(ids), 64):
                collection.upsert(ids=ids[offset:offset+64], documents=texts[offset:offset+64], metadatas=metas[offset:offset+64])
        _COLLECTION, _SIGNATURE = collection, signature
        return collection, docs


def search_chroma(query: str, limit: int = 5) -> list[tuple[float, dict[str, Any]]]:
    """Return active-source documents with cosine-distance-derived relevance scores."""
    q = str(query or "").strip()[:1000]
    if not q:
        return []
    collection, docs = _get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(query_texts=[q], n_results=max(1, min(int(limit), 10)), include=["documents", "metadatas", "distances"])
    rows = []
    source_map = {str(doc.get("id")): doc for doc in docs}
    id_lists = result.get("ids") or [[]]
    distances = (result.get("distances") or [[]])[0]
    metadata_list = (result.get("metadatas") or [[]])[0]
    for identifier, distance, metadata in zip(id_lists[0], distances, metadata_list):
        metadata = dict(metadata or {})
        original = source_map.get(str(metadata.get("document_id", "")))
        if not original or not original.get("approved_for_use", True) or original.get("brand_scope") == "legacy_airline_reference_only":
            continue
        try:
            score = max(0.0, min(1.0, 1.0 - float(distance)))
        except (TypeError, ValueError):
            score = 0.0
        # Preserve original source content for support evaluation/citations.
        rows.append((score, {**original, "_semantic": True, "_retrieval_source": "local_chroma", "_chroma_id": str(identifier), "_overlap": 0}))
    return rows


def index_status() -> dict[str, Any]:
    try:
        collection, docs = _get_collection()
        return {"available": True, "collection": collection.name, "indexed_count": int(collection.count()), "approved_source_count": len(docs), "persist_dir": str(_paths()[0])}
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__, "collection": _paths()[1], "indexed_count": 0}
