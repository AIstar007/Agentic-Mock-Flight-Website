#!/usr/bin/env python3
"""Build/refresh the local Chroma index from the activated, reviewed SQLite corpus."""
from __future__ import annotations
import json
from app.chroma_store import index_status
from app.faq_store import FAQCorpusStore

if __name__ == '__main__':
    store = FAQCorpusStore()
    print(json.dumps({"active_version": store.get_active_version(), "status": index_status()}, indent=2))
