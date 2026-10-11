from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any


class BookingNotFound(Exception):
    pass


class SeatUnavailable(Exception):
    pass


class MockNavitaireAdapter:
    """Deterministic local provider based on the reference workflow's booking contracts."""

    def __init__(self, fixtures_path: str | None = None):
        path = Path(fixtures_path) if fixtures_path else Path(__file__).resolve().parents[2] / "data" / "bookings.json"
        self._path = path
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._bookings: dict[str, dict[str, Any]] = {b["record_locator"]: b for b in payload["bookings"]}
        self._seat_owner: dict[str, dict[str, tuple[str, str]]] = {}
        self._seat_lock = asyncio.Lock()
        self._ancillaries = [
            {"code": "EXTRA_BAG", "name": "Additional baggage 10 kg", "label": "Additional baggage 10 kg", "description": "Synthetic checked-baggage add-on", "price": 180, "currency": "AED"},
            {"code": "LOUNGE", "name": "Airport lounge access (demo)", "label": "Lounge access", "description": "Fictional lounge access selection", "price": 250, "currency": "AED"},
            {"code": "WIFI", "name": "Onboard Wi-Fi package (demo)", "label": "Onboard Wi-Fi", "description": "Fictional connectivity package", "price": 65, "currency": "AED"},
            {"code": "MEAL", "name": "Special meal request (demo)", "label": "Meal preference", "description": "Synthetic meal request; no real catering request is sent", "price": 0, "currency": "AED"},
        ]

    def register_booking(self, booking: dict[str, Any]) -> None:
        """Register a synthetic booking created by the public demo booking flow."""
        locator = str(booking.get("record_locator", "")).upper().strip()
        surname = str(booking.get("last_name", "")).upper().strip()
        if not locator or not surname:
            raise ValueError("A synthetic booking requires a record locator and surname")
        self._bookings[locator] = copy.deepcopy(booking)

    async def retrieve_booking(self, record_locator: str, last_name: str) -> dict[str, Any]:
        booking = self._bookings.get(record_locator.upper())
        if not booking or booking["last_name"].upper() != last_name.upper():
            raise BookingNotFound("Booking not found. Check the reference and passenger surname.")
        return copy.deepcopy(booking)

    async def booking_for_session(self, booking: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(booking)

    @staticmethod
    def _seat_catalog() -> list[dict[str, Any]]:
        seats: list[dict[str, Any]] = []
        for row in range(1, 41):
            for letter in "ABCDEF":
                price = 0 if row >= 30 else (75 if row >= 20 else 120)
                seats.append({"seat_code": f"{row}{letter}", "row": row, "letter": letter, "price": price, "currency": "AED", "category": "STANDARD" if price == 0 else "PREFERRED"})
        return seats

    async def list_seats(self, session: dict[str, Any], journey_key: str) -> list[dict[str, Any]]:
        journey = next((j for j in session["booking"]["journeys"] if j["journey_key"] == journey_key), None)
        if not journey:
            raise ValueError("Journey not found")
        flight_key = self._flight_key(session["booking"], journey)
        reservations = self._seat_owner.setdefault(flight_key, {})
        results = []
        for seat in self._seat_catalog():
            owner = reservations.get(seat["seat_code"])
            # Any committed seat is unavailable to other assignments, including another
            # passenger in this session. The selected seat is still shown from session state.
            results.append({**seat, "available": owner is None})
        return results

    async def assign_seat(self, session: dict[str, Any], passenger_key: str, seat_code: str, journey_key: str | None = None) -> dict[str, Any]:
        booking = session["booking"]
        if passenger_key not in {p["passenger_key"] for p in booking["passengers"]}:
            raise ValueError("Passenger is not part of this booking")
        journey = next((j for j in booking["journeys"] if j["journey_key"] == journey_key), None) if journey_key else booking["journeys"][0]
        if journey is None:
            raise ValueError("Journey is not part of this booking")
        catalog = {s["seat_code"]: s for s in self._seat_catalog()}
        if seat_code not in catalog:
            raise ValueError("Seat code is not in the mock seat map")
        flight_key = self._flight_key(booking, journey)
        async with self._seat_lock:
            reservations = self._seat_owner.setdefault(flight_key, {})
            owner = reservations.get(seat_code)
            if owner and owner != (session["session_id"], passenger_key):
                raise SeatUnavailable("That seat was just selected by another demo session")
            # Drop this passenger's old seat before assigning the new one.
            for old_seat, old_owner in list(reservations.items()):
                if old_owner == (session["session_id"], passenger_key) and old_seat != seat_code:
                    reservations.pop(old_seat, None)
            reservations[seat_code] = (session["session_id"], passenger_key)
        return {**catalog[seat_code], "passenger_key": passenger_key, "journey_key": journey["journey_key"]}

    async def release_session_seats(self, session: dict[str, Any]) -> None:
        """Release every mock seat committed by this session when the passenger skips seats."""
        session_id = str(session.get("session_id", ""))
        async with self._seat_lock:
            for flight_key, reservations in list(self._seat_owner.items()):
                for seat_code, owner in list(reservations.items()):
                    if owner and owner[0] == session_id:
                        reservations.pop(seat_code, None)
                if not reservations:
                    self._seat_owner.pop(flight_key, None)

    async def release_passenger_seat(self, session: dict[str, Any], passenger_key: str, journey_key: str) -> None:
        journey = next((j for j in session["booking"]["journeys"] if j["journey_key"] == journey_key), None)
        if journey is None:
            raise ValueError("Journey is not part of this booking")
        flight_key = self._flight_key(session["booking"], journey)
        session_id = str(session.get("session_id", ""))
        async with self._seat_lock:
            reservations = self._seat_owner.get(flight_key, {})
            for seat_code, owner in list(reservations.items()):
                if owner and owner[0] == session_id and owner[1] == passenger_key:
                    reservations.pop(seat_code, None)
            if not reservations:
                self._seat_owner.pop(flight_key, None)

    def _flight_key(self, booking: dict[str, Any], journey: dict[str, Any]) -> str:
        return f"{booking['record_locator']}:{journey['journey_key']}"

    def ancillary_catalog(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._ancillaries)

    async def document_check(self, passenger_key: str) -> dict[str, Any]:
        """Reference-shaped mock for Navitaire's read-only passenger document-check endpoint."""
        return {
            "data": {"documentCheckStatus": 1},
            "provider": "MOCK_NAVITAIRE",
            "mock_only": True,
            "passenger_key": passenger_key,
        }

    async def get_checkin_requirements(self, journey_key: str) -> dict[str, Any]:
        """Reference-shaped mock for per-journey SSR/check-in requirement retrieval."""
        return {
            "data": {
                "passengers": {},
                "restrictions": [],
                "isValid": True,
            },
            "journey_key": journey_key,
            "provider": "MOCK_NAVITAIRE",
            "mock_only": True,
        }

    async def add_booking_comments(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"accepted": True, "provider": "MOCK_NAVITAIRE", "mock_only": True}


class NavitaireHttpAdapter:
    """Guarded integration scaffold; requires authorized provider credentials and contract review.

    The source reference contains example endpoint shapes. This adapter deliberately does not
    guess provider endpoints or issue requests automatically until enabled by an approved deployment.
    """
    def __init__(self, *, base_url: str, api_key: str, agent_token: str, user_key: str = ""):
        if not base_url.startswith("https://"):
            raise ValueError("Live Navitaire adapter requires HTTPS")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_token = agent_token
        self.user_key = user_key

    async def retrieve_booking(self, *_args, **_kwargs):
        raise NotImplementedError("Confirm the authorized New Skies API product, role, query and response schema before enabling live calls")
