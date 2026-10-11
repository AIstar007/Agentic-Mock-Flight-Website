"""Idempotently add a protected retrieval-only API to the supplied Pdpfinal_rag.

Usage from the extracted reference project root:
  python ../chatbot-rag-upgrade/integration/install_pdp_rag_bridge.py .

The script never edits environment.json/.env and never copies reference documents.
It backs up files before changing them. Review changes before using the service.
"""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import stat

ENDPOINT_MARKER = '"/retrieve-context"'
ENDPOINT = '''\n\n# Self-corrective RAG bridge: retrieval-only, active-version-filtered, bearer-protected.\nimport hmac as _rag_hmac\nimport os as _rag_os\nfrom fastapi import Header as _RagHeader, HTTPException as _RagHTTPException\n\n@router.post("/retrieve-context")\nasync def retrieve_context_for_assistant(\n    request: QueryRequest,\n    authorization: str | None = _RagHeader(default=None),\n):\n    expected = _rag_os.getenv("REFERENCE_RAG_BRIDGE_API_KEY", "").strip()\n    presented = (authorization or "").removeprefix("Bearer ").strip()\n    if not expected or not _rag_hmac.compare_digest(presented, expected):\n        raise _RagHTTPException(status_code=401, detail="Unauthorized retrieval client")\n    contexts = retrieve_context(request.query, request.top_k)\n    return {"contexts": [{\n        "doc_id": str(c["doc_id"]),\n        "version_number": int(c["version_number"]),\n        "chunk_order": int(c["chunk_order"]),\n        "rerank_score": float(c.get("rerank_score", 0.0)),\n        "distance": float(c.get("distance", 1.0)),\n        "text": str(c["text"]),\n        "verified": False,\n        "status": "active",\n    } for c in contexts]}\n'''

def backup(path: Path, root: Path) -> None:
    suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_root = root / ".reference-rag-bridge-backups"
    backup_path = backup_root / f"{path.relative_to(root)}.bak-{suffix}"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)

def ensure_writable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWUSR)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    router_path = root / "app" / "routers" / "query.py"
    embedding_path = root / "app" / "services" / "embedding.py"
    if not router_path.exists() or not embedding_path.exists():
        raise SystemExit("Expected app/routers/query.py and app/services/embedding.py; run from Pdpfinal_rag root")
    source = router_path.read_text(encoding="utf-8")
    if ENDPOINT_MARKER not in source:
        backup(router_path, root)
        ensure_writable(router_path)
        router_path.write_text(source + ENDPOINT, encoding="utf-8")
        print("Added protected /v1/retrieve-context endpoint")
    else:
        print("Retrieval endpoint already present; not modified")
    embedding = embedding_path.read_text(encoding="utf-8")
    if "httpx.Client(verify=False)" in embedding:
        backup(embedding_path, root)
        ensure_writable(embedding_path)
        embedding_path.write_text(embedding.replace("httpx.Client(verify=False)", "httpx.Client(verify=True)"), encoding="utf-8")
        print("Changed disabled TLS verification to verify=True")
    else:
        print("No verify=False pattern found in embedding client")
    print("Set REFERENCE_RAG_BRIDGE_API_KEY in the RAG service environment, then test /v1/retrieve-context.")
    print("Source provenance is not in the original schema; this endpoint intentionally sets verified=False so high-risk travel questions still abstain.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
