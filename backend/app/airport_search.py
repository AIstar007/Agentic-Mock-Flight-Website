from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_AIRPORT_CATALOG = Path(__file__).resolve().parents[1] / "data" / "airport_catalog.json"
STOP_WORDS = {
    "a", "an", "the", "to", "from", "of", "in", "at", "airport", "airports", "city", "cities",
    "where", "can", "i", "fly", "flight", "flights", "book", "booking", "please", "find", "search",
    "show", "me", "destination", "destinations", "near", "and", "with", "for", "want", "go", "travel",
}
SYNONYMS = {
    "beach": {"coast", "island", "resort", "tropical", "sea"},
    "mountain": {"highlands", "hills", "trekking", "alpine"},
    "culture": {"historic", "heritage", "museum", "art"},
    "food": {"cuisine", "culinary", "restaurants"},
    "desert": {"dunes", "safari", "arid"},
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.casefold()) if t not in STOP_WORDS and len(t) > 1}


class AirportSearchIndex:
    """Local booking-widget airport search, adapted from the uploaded NLP search reference.

    The reference's Azure embeddings + MongoDB Atlas Vector Search path is an optional
    deployment design. This no-credential local version uses deterministic lexical/tag
    scoring and does not represent live flight availability or bookable schedules.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_AIRPORT_CATALOG
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.airports: list[dict[str, Any]] = payload.get("airports", [])
        self._by_iata = {str(a.get("iata", "")).upper(): a for a in self.airports}

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", query).strip().casefold()
        if not normalized:
            return {"query": query, "results": [], "mock_only": True, "mode": "local_tag_search"}
        tokens = _tokens(normalized)
        expanded = set(tokens)
        for token in list(tokens):
            expanded.update(SYNONYMS.get(token, set()))
        results: list[tuple[float, dict[str, Any], str]] = []
        for airport in self.airports:
            iata = str(airport.get("iata", "")).upper()
            city = str(airport.get("city", ""))
            airport_name = str(airport.get("airport", ""))
            country = str(airport.get("country", ""))
            tags = [str(t) for t in airport.get("tags", [])]
            text = f"{iata} {city} {airport_name} {country} {airport.get('country_code','')} {' '.join(tags)}".casefold()
            words = _tokens(text)
            score = 0.0
            reason = "tag_or_text_match"
            if normalized == iata.casefold():
                score, reason = 100.0, "exact_iata"
            elif normalized == city.casefold():
                score, reason = 95.0, "exact_city"
            elif city.casefold().startswith(normalized):
                score, reason = 85.0, "city_prefix"
            elif normalized in airport_name.casefold():
                score, reason = 78.0, "airport_name_match"
            else:
                overlap = len(expanded & words)
                if overlap:
                    coverage = overlap / max(1, len(expanded))
                    title_overlap = len(expanded & _tokens(f"{city} {iata} {country} {airport_name}"))
                    tag_overlap = len(expanded & _tokens(" ".join(tags)))
                    score = (coverage * 45.0) + (title_overlap * 7.0) + (tag_overlap * 4.0)
                    reason = "semantic_tag_match" if tag_overlap else "text_match"
            if score > 0:
                item = {
                    "iata": iata,
                    "city": city,
                    "airport": airport_name,
                    "country": country,
                    "country_code": airport.get("country_code", ""),
                    "tags": tags[:6],
                    "score": round(score, 3),
                    "match_reason": reason,
                    "mock_only": True,
                }
                results.append((score, item, iata))
        results.sort(key=lambda x: (-x[0], x[2]))
        capped = max(1, min(int(limit), 10))
        return {
            "query": query,
            "results": [item for _, item, _ in results[:capped]],
            "total_matches": len(results),
            "mode": "local_tag_search",
            "mock_only": True,
            "notice": "Airport suggestions are reference-derived. Live schedules, fares, inventory, and booking are not queried.",
        }

    def by_iata(self, iata: str) -> dict[str, Any] | None:
        a = self._by_iata.get(iata.strip().upper())
        if not a:
            return None
        return {
            "iata": a["iata"], "city": a["city"], "airport": a["airport"],
            "country": a["country"], "country_code": a["country_code"],
            "latitude": a.get("latitude"), "longitude": a.get("longitude"),
            "tags": a.get("tags", [])[:6], "mock_only": True,
        }
