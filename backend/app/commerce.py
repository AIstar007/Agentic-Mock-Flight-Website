from __future__ import annotations

import hashlib
import math
import re
import secrets
import string
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.airport_search import AirportSearchIndex
from app.adapters.navitaire import MockNavitaireAdapter

try:
    import pycountry
except ImportError:
    pycountry = None


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FlightSearchRequest(StrictInput):
    origin: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    destination: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    trip_type: Literal["round_trip", "one_way"] = "round_trip"
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)
    cabin: Literal["Economy", "Premium Economy", "Business", "First"] = "Economy"

    @field_validator("origin", "destination")
    @classmethod
    def uppercase_iata(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def check_search(self):
        if self.origin == self.destination:
            raise ValueError("Origin and destination must be different")
        if self.departure_date < date.today():
            raise ValueError("Departure date must be today or later")
        if self.trip_type == "round_trip":
            if self.return_date is None:
                raise ValueError("Choose a return date for a round-trip search")
            if self.return_date < self.departure_date:
                raise ValueError("Return date cannot be before departure")
        if self.trip_type == "one_way" and self.return_date is not None:
            raise ValueError("Return date must be empty for a one-way search")
        if self.infants > self.adults:
            raise ValueError("There cannot be more infants than adults in this demo")
        if self.adults + self.children + self.infants > 9:
            raise ValueError("This demo supports up to 9 passengers per booking")
        return self


class PassengerInput(StrictInput):
    title: Literal["MR", "MRS", "MS", "MISS", "MSTR", "DR"] = "MR"
    first_name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z .'-]*$")
    middle_name: str | None = Field(default=None, max_length=60, pattern=r"^[A-Za-z .'-]*$")
    last_name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z .'-]*$")
    passenger_type: Literal["ADT", "CHD", "INF"] = "ADT"
    date_of_birth: date | None = None
    senior_citizen: bool = False
    accompanying_adult_index: int | None = Field(default=None, ge=0, le=8)
    nationality: str = Field(default="IN", min_length=2, max_length=3, pattern=r"^[A-Za-z]{2,3}$")
    gender: Literal["M", "F", "X", "U"] = "U"

    @field_validator("nationality")
    @classmethod
    def normalize_nationality(cls, value: str) -> str:
        code = value.strip().upper()
        if pycountry is not None:
            found = pycountry.countries.get(alpha_2=code) if len(code) == 2 else pycountry.countries.get(alpha_3=code) if len(code) == 3 else None
            if not found:
                raise ValueError("Use a valid ISO-3166 alpha-2 or alpha-3 country code (for example IN or IND)")
        return code


class BookingConfirmationRequest(StrictInput):
    search_id: str = Field(min_length=12, max_length=80)
    outbound_flight_id: str = Field(min_length=4, max_length=100)
    outbound_fare_family: str = Field(min_length=1, max_length=40)
    return_flight_id: str | None = None
    return_fare_family: str | None = None
    passengers: list[PassengerInput] = Field(min_length=1, max_length=9)
    contact_email: str = Field(min_length=5, max_length=254)
    contact_phone: str = Field(min_length=7, max_length=24)
    accepted_terms: bool
    extras: list[Literal["EXTRA_BAG", "LOUNGE", "WIFI", "MEAL"]] = Field(default_factory=list, max_length=20)
    passenger_extras: dict[str, list[Literal["EXTRA_BAG", "LOUNGE", "WIFI", "MEAL"]]] = Field(default_factory=dict, max_length=9)
    selected_seats: dict[str, str] = Field(default_factory=dict, max_length=18)
    payment_simulation: Literal["approve", "decline"] = "approve"

    @field_validator("contact_email")
    @classmethod
    def email_like(cls, value: str) -> str:
        value = value.strip()
        if value.count("@") != 1 or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("contact_phone")
    @classmethod
    def phone_like(cls, value: str) -> str:
        value = value.strip()
        compact = "".join(c for c in value if c.isdigit())
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or len(compact) < 7 or len(compact) > 18:
            raise ValueError("Enter a phone number containing 7 to 18 digits and only +, spaces, parentheses or hyphens")
        return value

    @field_validator("selected_seats")
    @classmethod
    def validate_selected_seats(cls, values: dict[str, str]) -> dict[str, str]:
        unavailable = {"1B", "2D", "3A", "5F", "7C", "9E", "10B"}
        by_leg: dict[str, list[str]] = {"outbound": [], "return": []}
        for key, seat in values.items():
            if not re.fullmatch(r"(?:outbound|return):[0-8]", str(key)):
                raise ValueError("Seat assignments must use outbound:N or return:N passenger keys")
            leg, _ = key.split(":", 1)
            code = str(seat).upper()
            if not re.fullmatch(r"(?:[1-9]|[1-3][0-9]|40)[A-F]", code):
                raise ValueError("Seat code must be an inventory seat from 1A through 40F (for example 1A, 12F or 40C)")
            if code in unavailable:
                raise ValueError(f"Seat {code} is unavailable in the mock seat inventory")
            if code in by_leg[leg]:
                raise ValueError(f"Seat {code} is already assigned on the {leg} journey")
            by_leg[leg].append(code)
        return {key: str(value).upper() for key, value in values.items()}

    @model_validator(mode="after")
    def check_confirmation(self):
        if not self.accepted_terms:
            raise ValueError("Terms and conditions must be accepted")
        if self.payment_simulation == "approve" and self.return_flight_id and not self.return_fare_family:
            raise ValueError("Return fare family is required")
        for idx, codes in self.passenger_extras.items():
            if not re.fullmatch(r"0|[1-8]", str(idx)):
                raise ValueError("Passenger add-on keys must be zero-based passenger indices")
            if int(idx) >= len(self.passengers):
                raise ValueError("Passenger add-ons refer to a passenger that is not in this booking")
            if len(codes) != len(set(codes)):
                raise ValueError("Duplicate add-ons are not allowed for one passenger")
        for marker in self.selected_seats:
            leg, passenger_index = marker.split(":", 1)
            if int(passenger_index) >= len(self.passengers):
                raise ValueError("Seat assignment refers to a passenger that is not in this booking")
            if leg == "return" and not self.return_flight_id:
                raise ValueError("Return-journey seats cannot be assigned to a one-way booking")
        if len(self.extras) != len(set(self.extras)):
            raise ValueError("Duplicate ancillary selections are not allowed")
        passenger_types = [p.passenger_type for p in self.passengers]
        adults = [i for i, passenger in enumerate(self.passengers) if passenger.passenger_type == "ADT"]
        infant_assignments: set[int] = set()
        outbound = None
        # Age rules are evaluated against the selected outbound date in confirm(),
        # while this structural gate ensures an infant points to an actual adult row.
        for idx, passenger in enumerate(self.passengers):
            companion = passenger.accompanying_adult_index
            if passenger.passenger_type == "INF" and companion is None:
                raise ValueError(f"Passenger {idx + 1}: an infant must be linked to an accompanying adult passenger.")
            if companion is not None:
                if companion >= len(self.passengers) or companion == idx or self.passengers[companion].passenger_type != "ADT":
                    raise ValueError(f"Passenger {idx + 1}: choose an accompanying passenger from the adult passengers only.")
                if passenger.passenger_type == "INF":
                    if companion in infant_assignments:
                        raise ValueError("Each adult may accompany at most one infant in this demo.")
                    infant_assignments.add(companion)
        if passenger_types.count("ADT") == 0:
            raise ValueError("At least one adult passenger is required")
        if passenger_types.count("INF") > passenger_types.count("ADT"):
            raise ValueError("There cannot be more infants than adults")
        return self


class MockFlightCommerce:
    """Synthetic flight search and booking service adapted from airport-NLP/PDP references.

    All schedules, fare availability, prices, payment results and PNRs are generated mock data.
    This service is not an airline availability or payment gateway.
    """

    def __init__(self, airports: AirportSearchIndex, navitaire: MockNavitaireAdapter):
        self.airports = airports
        self.navitaire = navitaire
        self._searches: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _route_minutes(origin: dict[str, Any], dest: dict[str, Any]) -> int:
        lat1, lon1 = math.radians(float(origin.get("latitude", 0))), math.radians(float(origin.get("longitude", 0)))
        lat2, lon2 = math.radians(float(dest.get("latitude", 0))), math.radians(float(dest.get("longitude", 0)))
        hav = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        distance = 6371 * 2 * math.asin(min(1, math.sqrt(max(0, hav))))
        return max(65, int(distance / 760 * 60 + 45))

    @staticmethod
    def _seed(*parts: str) -> int:
        return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)

    @staticmethod
    def _format_minutes(minutes: int) -> str:
        return f"{minutes // 60}h {minutes % 60:02d}m"

    def _flight(self, search: FlightSearchRequest, direction: str, index: int, dep_date: date) -> dict[str, Any]:
        origin_obj = self.airports.by_iata(search.origin if direction == "outbound" else search.destination)
        dest_obj = self.airports.by_iata(search.destination if direction == "outbound" else search.origin)
        if not origin_obj or not dest_obj:
            raise ValueError("Airport code is not present in the demo airport catalogue")
        origin = origin_obj["iata"]
        dest = dest_obj["iata"]
        seed = self._seed(origin, dest, dep_date.isoformat(), direction, str(index), search.cabin)
        distance_minutes = self._route_minutes(origin_obj, dest_obj)
        departure_minutes = [165, 575, 1130][index % 3] + ((seed >> 5) % 35)
        departure_hour, departure_minute = divmod(departure_minutes % 1440, 60)
        # Mock local clock times, not published timetables.
        arrival_total = departure_hour * 60 + departure_minute + distance_minutes
        arrival_hour, arrival_minute = divmod(arrival_total % 1440, 60)
        next_day = arrival_total >= 1440
        long_route = distance_minutes >= 600
        stops = 1 if distance_minutes > 660 and index == 1 else 0
        duration = distance_minutes + (stops * 100)
        base = max(290, round((distance_minutes / 60 * 95 + 260) / 10) * 10) + index * 75
        currency = "AED"
        fare_defs = [
            {"code": "ECONOMY_SPECIAL", "label": "Economy Special", "cabin": "Economy", "surcharge": 0, "perks": ["Cabin baggage", "Standard check-in"]},
            {"code": "ECONOMY_SAVER", "label": "Economy Saver", "cabin": "Economy", "surcharge": 110, "perks": ["Cabin baggage", "Changes may incur fees"]},
            {"code": "ECONOMY_FLEX", "label": "Economy Flex", "cabin": "Economy", "surcharge": 320, "perks": ["More flexibility", "Standard seat options"]},
            {"code": "PREMIUM_ECONOMY", "label": "Premium Economy", "cabin": "Premium Economy", "surcharge": base * 0.72 + 420, "perks": ["More space", "Enhanced meal service"]},
            {"code": "BUSINESS", "label": "Business Class", "cabin": "Business", "surcharge": base * 2.5 + 850, "perks": ["Business cabin", "Lounge options"]},
            {"code": "FIRST", "label": "First Class", "cabin": "First", "surcharge": base * 5.2 + 1800, "perks": ["First cabin", "Premium service"]},
        ]
        fare_options = []
        for f in fare_defs:
            # Fare families are presented as illustrative demo tiers.
            fare_options.append({**f, "price": round((base + f["surcharge"]) / 10) * 10, "currency": currency, "mock_only": True})
        code = 100 + (seed % 800)
        return {
            "flight_id": f"{direction}-{origin}-{dest}-{dep_date.strftime('%Y%m%d')}-{index+1}-{seed%997}",
            "flight_number": f"EK{code}",
            "origin": origin_obj,
            "destination": dest_obj,
            "departure_date": dep_date.isoformat(),
            "departure_time": f"{departure_hour:02d}:{departure_minute:02d}",
            "arrival_date": (dep_date + timedelta(days=1 if next_day else 0)).isoformat(),
            "arrival_time": f"{arrival_hour:02d}:{arrival_minute:02d}",
            "duration_minutes": duration,
            "duration_label": self._format_minutes(duration),
            "stops": stops,
            "stop_label": "1 stop via Dubai (demo)" if stops else "Non-stop (demo)",
            "cabin_requested": search.cabin,
            "fare_options": fare_options,
            "lowest_fare": min(f["price"] for f in fare_options if f["cabin"] == search.cabin),
            "currency": currency,
            "operated_by": "Emirates-inspired demo carrier",
            "mock_only": True,
        }

    def search(self, request: FlightSearchRequest) -> dict[str, Any]:
        origin = self.airports.by_iata(request.origin)
        dest = self.airports.by_iata(request.destination)
        if not origin or not dest:
            raise ValueError("Select an origin and destination from the airport suggestions")
        search_id = uuid.uuid4().hex
        outbound = [self._flight(request, "outbound", i, request.departure_date) for i in range(3)]
        inbound = [self._flight(request, "return", i, request.return_date) for i in range(3)] if request.trip_type == "round_trip" and request.return_date else []
        result = {
            "search_id": search_id,
            "search": request.model_dump(mode="json"),
            "origin": origin,
            "destination": dest,
            "outbound_flights": outbound,
            "return_flights": inbound,
            "passenger_count": request.adults + request.children + request.infants,
            "mock_only": True,
            "notice": "Illustrative demo schedules, fares and availability. No live airline inventory is queried.",
        }
        with self._lock:
            self._searches[search_id] = {"result": result, "created_at": datetime.now(timezone.utc)}
            # Keep only recent search objects in memory.
            if len(self._searches) > 250:
                for key in list(self._searches)[:100]:
                    self._searches.pop(key, None)
        return result

    def get_search(self, search_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._searches.get(search_id)
        if not entry:
            raise LookupError("Flight search expired. Search flights again.")
        if (datetime.now(timezone.utc) - entry["created_at"]).total_seconds() > 1800:
            with self._lock:
                self._searches.pop(search_id, None)
            raise LookupError("Flight search expired. Search flights again.")
        return entry["result"]

    def confirm(self, request: BookingConfirmationRequest) -> dict[str, Any]:
        search_result = self.get_search(request.search_id)
        outbound = next((x for x in search_result["outbound_flights"] if x["flight_id"] == request.outbound_flight_id), None)
        inbound = next((x for x in search_result["return_flights"] if x["flight_id"] == request.return_flight_id), None) if request.return_flight_id else None
        if outbound is None:
            raise ValueError("Selected outbound flight is not in the saved search")
        if search_result["search"]["trip_type"] == "round_trip" and inbound is None:
            raise ValueError("Select a return flight from the saved search")
        if search_result["search"]["trip_type"] == "one_way" and request.return_flight_id:
            raise ValueError("A return flight cannot be selected for a one-way trip")
        if len(request.passengers) != search_result["passenger_count"]:
            raise ValueError("Passenger details must match the number of passengers in the search")
        # Passenger category is derived against each travel date, not guessed from a title.
        # This is an explicit POC policy gate based on public Emirates age bands.
        outbound_date = date.fromisoformat(outbound["departure_date"])
        inbound_date = date.fromisoformat(inbound["departure_date"]) if inbound else None
        def age_on(dob: date, travel_date: date) -> int:
            return travel_date.year - dob.year - ((travel_date.month, travel_date.day) < (dob.month, dob.day))
        for idx, passenger in enumerate(request.passengers, start=1):
            if passenger.date_of_birth is None:
                raise ValueError(f"Passenger {idx}: date of birth is required to validate passenger category, age and under-16 accompaniment.")
            if passenger.date_of_birth > min(outbound_date, date.today()):
                raise ValueError(f"Passenger {idx}: date of birth cannot be in the future; enter a real past date.")
            outbound_age = age_on(passenger.date_of_birth, outbound_date)
            if passenger.senior_citizen and outbound_age < 60:
                raise ValueError(f"Passenger {idx}: senior-citizen passengers must be at least 60 on departure; the selected date of birth gives age {outbound_age}.")
            if outbound_age < 16 and passenger.accompanying_adult_index is None:
                raise ValueError(f"Passenger {idx}: traveller is under 16 and has no selected adult companion. This demo applies its strict unaccompanied-minor rule and blocks booking.")
            if passenger.accompanying_adult_index is not None:
                companion = request.passengers[passenger.accompanying_adult_index]
                if companion.date_of_birth is None or age_on(companion.date_of_birth, outbound_date) < 16:
                    raise ValueError(f"Passenger {idx}: selected companion must be an adult aged 16 or over on departure.")
            expected_type = "INF" if outbound_age < 2 else "CHD" if outbound_age < 12 else "ADT"
            if passenger.passenger_type != expected_type:
                label = {"INF": "infant (under 2)", "CHD": "child (2–11)", "ADT": "adult (12+)"}[expected_type]
                raise ValueError(f"Passenger {idx}: age on outbound departure is {outbound_age}; select the {label} passenger category and fare.")
            if outbound_age == 0 and (outbound_date - passenger.date_of_birth).days < 7:
                raise ValueError(f"Passenger {idx}: infants younger than 7 days require exceptional medical/compassionate handling and cannot be booked in this demo.")
            if inbound_date and age_on(passenger.date_of_birth, inbound_date) >= 2 and expected_type == "INF":
                raise ValueError(f"Passenger {idx}: this infant turns 2 before the return flight. Emirates directs this age-transition case to an airline office; the demo will not create an incorrect infant return fare.")
        if request.payment_simulation == "decline":
            raise PaymentDeclined("The synthetic payment test was declined. No charge was made.")

        def fare_for(flight: dict[str, Any], code: str | None) -> dict[str, Any]:
            fare = next((f for f in flight["fare_options"] if f["code"] == code), None)
            if fare is None:
                raise ValueError("Selected fare family does not belong to the chosen flight")
            return fare

        out_fare = fare_for(outbound, request.outbound_fare_family)
        in_fare = fare_for(inbound, request.return_fare_family) if inbound and request.return_fare_family else None
        if inbound and in_fare is None:
            raise ValueError("Select a return fare family")
        passengers = []
        passenger_total = 0
        for idx, p in enumerate(request.passengers, start=1):
            ptype = p.passenger_type
            factor = 0.1 if ptype == "INF" else 0.75 if ptype == "CHD" else 1.0
            passengers.append({
                "passenger_key": f"PAX-{idx:03d}",
                "passenger_type": ptype,
                "title": p.title,
                "first_name": p.first_name.strip().upper(),
                "middle_name": p.middle_name.strip().upper() if p.middle_name else "",
                "last_name": p.last_name.strip().upper(),
                "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else "",
                "senior_citizen": p.senior_citizen,
                "nationality": p.nationality.upper(),
                "residence_country": p.nationality.upper(),
                "gender": p.gender,
                "infant": ptype == "INF",
            })
            passenger_total += round((out_fare["price"] + (in_fare["price"] if in_fare else 0)) * factor)
        extras_catalog = {
            "EXTRA_BAG": {"label": "Additional baggage 10 kg", "price": 180},
            "LOUNGE": {"label": "Lounge access (demo)", "price": 250},
            "WIFI": {"label": "Onboard Wi-Fi (demo)", "price": 65},
            "MEAL": {"label": "Meal preference", "price": 0},
        }
        passenger_extras: dict[str, list[dict[str, Any]]] = {}
        all_extra_codes: list[str] = []
        extra_total = 0
        for idx, passenger in enumerate(passengers):
            codes = list(request.passenger_extras.get(str(idx), request.extras)) if request.passenger_extras else list(request.extras)
            pax_items = [{"code": code, **extras_catalog[code]} for code in codes]
            passenger["extras"] = pax_items
            passenger_extras[str(idx)] = pax_items
            extra_total += sum(item["price"] for item in pax_items)
            all_extra_codes.extend(codes)
        union_codes = list(dict.fromkeys(all_extra_codes))
        extras = [{"code": code, **extras_catalog[code]} for code in union_codes]
        total = passenger_total + extra_total
        locator_alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        locator = "".join(secrets.choice(locator_alphabet) for _ in range(6))
        while locator in self.navitaire._bookings:
            locator = "".join(secrets.choice(locator_alphabet) for _ in range(6))
        journeys = []
        for idx, (flight, fare) in enumerate([(outbound, out_fare)] + ([(inbound, in_fare)] if inbound and in_fare else []), start=1):
            origin = flight["origin"]
            destination = flight["destination"]
            international = origin.get("country_code") != destination.get("country_code")
            dep = datetime.combine(date.fromisoformat(flight["departure_date"]), time.fromisoformat(flight["departure_time"]), tzinfo=timezone.utc)
            offset = max(0, (dep - datetime.now(timezone.utc)).total_seconds() / 3600)
            journeys.append({
                "journey_key": f"JNY-{idx:02d}",
                "origin": origin["iata"], "destination": destination["iata"],
                "international": international, "departure_offset_hours": round(offset, 2),
                "marketing_carrier": "EK", "operating_carrier": "EK", "codeshare": False,
                "transit_points": ["DXB"] if flight["stops"] and origin["iata"] != "DXB" and destination["iata"] != "DXB" else [],
                "flight_number": flight["flight_number"], "departure_date": flight["departure_date"],
                "departure_time": flight["departure_time"], "arrival_date": flight["arrival_date"],
                "arrival_time": flight["arrival_time"], "duration_minutes": flight["duration_minutes"],
                "fare_family": fare["label"], "fare_amount": fare["price"],
            })
        booking = {
            "record_locator": locator,
            "last_name": passengers[0]["last_name"],
            "booking_status": "CONFIRMED", "payment_status": "PAID_IN_FULL", "balance_due": 0,
            "passengers": passengers, "journeys": journeys, "queues": [], "ssr_requirements_valid": True,
            "mock_timatic_case": "CLEAR", "seat_assignments": request.selected_seats, "demo_notes": "Synthetic booking created by the Emirates-inspired booking POC.",
            "commerce": {"total": total, "currency": "AED", "outbound_fare": out_fare["label"], "return_fare": in_fare["label"] if in_fare else None, "extras": extras, "passenger_extras": passenger_extras, "payment_status": "SIMULATED_APPROVED"},
        }
        self.navitaire.register_booking(booking)
        return {
            "booking_reference": locator, "surname": passengers[0]["last_name"],
            "itinerary": journeys, "passengers": [{"title": p["title"], "first_name": p["first_name"], "last_name": p["last_name"], "passenger_type": p["passenger_type"]} for p in passengers],
            "fare": {"outbound": out_fare, "return": in_fare}, "extras": extras, "passenger_extras": passenger_extras, "selected_seats": request.selected_seats,
            "total": total, "currency": "AED", "payment_status": "SIMULATED_APPROVED",
            "created_at": datetime.now(timezone.utc).isoformat(), "mock_only": True,
            "notice": "Booking and payment are simulated. This reference cannot be used to travel.",
        }


class PaymentDeclined(ValueError):
    pass

class SiteAssistantRequest(StrictInput):
    message: str = Field(min_length=1, max_length=1000)
    page: str = Field(default="home", max_length=40)
    context: dict[str, Any] = Field(default_factory=dict)


class PDPContextRequest(StrictInput):
    current_passenger_index: int = Field(default=0, ge=0, le=8)
    passengers: list[dict[str, Any]] = Field(default_factory=list, max_length=9)
    contact: dict[str, Any] = Field(default_factory=dict)
    accepted_terms: bool = False


def resolve_pdp_next_step(body: PDPContextRequest) -> dict[str, Any]:
    """Deterministic Passenger Details Page context resolver inspired by supplied PDP agent prompt.

    Returns one missing step at a time; it does not infer missing personal details or write data.
    """
    passengers = body.passengers
    if not passengers:
        return {"step": "passengers", "field": "passenger_list", "prompt": "Add at least one passenger to continue.", "complete": False}
    idx = min(body.current_passenger_index, len(passengers) - 1)
    passenger = passengers[idx]
    display_name = f"passenger {idx + 1}"
    required_fields = [
        ("title", "title", "Choose a title"),
        ("first_name", "first name", "Enter the first name exactly as shown on the travel document"),
        ("last_name", "last name", "Enter the surname exactly as shown on the travel document"),
    ]
    for key, label, prompt in required_fields:
        if not str(passenger.get(key, "")).strip():
            return {"step": "passengers", "passenger_index": idx, "field": key, "prompt": f"For {display_name}, {prompt.lower()}.", "complete": False}
    # Collect DOB only for categories whose synthetic rules depend on age.
    passenger_type = str(passenger.get("passenger_type", "ADT")).upper()
    needs_dob = passenger_type in {"CHD", "CHILD", "INF", "INFANT", "SENIOR", "SENIOR_CITIZEN"} or bool(passenger.get("senior_citizen"))
    if needs_dob and not str(passenger.get("date_of_birth", "")).strip():
        return {"step": "passengers", "passenger_index": idx, "field": "date_of_birth", "prompt": f"Add the date of birth for {display_name}; it is required for this passenger category.", "complete": False}
    for next_idx, p in enumerate(passengers):
        if any(not str(p.get(k, "")).strip() for k in ("title", "first_name", "last_name")):
            return {"step": "passengers", "passenger_index": next_idx, "field": "passenger_details", "prompt": f"Passenger {next_idx + 1} still has required name fields missing. Complete passengers in order without re-entering saved fields.", "complete": False}
    email = str(body.contact.get("email", "")).strip()
    phone = "".join(c for c in str(body.contact.get("phone", "")) if c.isdigit())
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return {"step": "contact", "field": "email", "prompt": "Enter a valid contact email so the demo can show where a booking confirmation would be sent.", "complete": False}
    if len(phone) < 7:
        return {"step": "contact", "field": "phone", "prompt": "Enter a valid contact phone number to complete the contact section.", "complete": False}
    if not body.accepted_terms:
        return {"step": "consent", "field": "accepted_terms", "prompt": "Review the demo terms and confirm that the passenger and contact details are accurate before proceeding.", "complete": False}
    return {"step": "complete", "field": None, "prompt": "All mandatory passenger, contact and consent fields are complete. Review the information and continue to the simulated payment step.", "complete": True}
