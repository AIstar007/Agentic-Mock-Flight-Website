from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.faq_store import FAQCorpusStore

KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge.json"

def main() -> None:
    parser = argparse.ArgumentParser(description="Create an immutable version of the demo FAQ corpus.")
    parser.add_argument("--label", required=True, help="Version label for reviewers, e.g. emirates-demo-2026-10-11")
    parser.add_argument("--change-note", default="", help="Short change rationale for auditability")
    parser.add_argument("--activate", action="store_true", help="Activate after all documents pass source/scope/approval validation")
    args = parser.parse_args()
    payload = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    docs = []
    for doc in payload.get("documents", []):
        url = str(doc.get("source_url", ""))
        source_type = str(doc.get("source_type", "project_policy"))
        if source_type == "project_policy":
            scope = "project_policy"
        elif "iata.org" in url.lower():
            scope = "global_aviation"
        else:
            scope = "emirates_demo"
        docs.append({
            "id": doc["id"], "title": doc["title"], "category": doc["category"],
            "jurisdiction": doc.get("jurisdiction", "unknown"), "source_type": source_type,
            "source_url": url, "verified_at": doc.get("verified_at", ""),
            "content": doc["content"], "brand_scope": scope, "approved_for_use": True,
        })
    store = FAQCorpusStore()
    result = store.create_version(version_label=args.label, documents=docs, change_note=args.change_note, activate=args.activate)
    print(json.dumps({"version_id": result["version_id"], "version_label": result["version_label"], "status": result["status"], "document_count": result["document_count"], "activated": bool(args.activate)}, indent=2))

if __name__ == "__main__":
    main()
