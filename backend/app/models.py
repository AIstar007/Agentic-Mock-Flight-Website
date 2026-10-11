from __future__ import annotations

from datetime import datetime, timezone
from datetime import date as calendar_date
import re

try:
    import pycountry
except ImportError:
    pycountry = None
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_RUN = "NOT_RUN"


class Stage(StrEnum):
    BOOKING = "BOOKING"
    DOCUMENTS = "DOCUMENTS"
    SEATS = "SEATS"
    ANCILLARIES = "ANCILLARIES"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Passenger(StrictModel):
    passenger_key: str
    passenger_type: str = "ADT"
    first_name: str
    middle_name: str = ""
    last_name: str
    title: str = "MR"
    nationality: str = "IN"
    residence_country: str = "IN"
    date_of_birth: str = "1990-01-01"
    senior_citizen: bool = False
    gender: str = "U"
    infant: bool = False


class Journey(StrictModel):
    journey_key: str
    origin: str
    destination: str
    international: bool
    departure_offset_hours: float = 24
    marketing_carrier: str = "EK"
    operating_carrier: str = "EK"
    codeshare: bool = False
    transit_points: list[str] = Field(default_factory=list)


class Booking(StrictModel):
    record_locator: str
    last_name: str
    booking_status: str = "CONFIRMED"
    payment_status: str = "PAID_IN_FULL"
    balance_due: float = 0
    passengers: list[Passenger]
    journeys: list[Journey]
    seat_assignments: dict[str, str] = Field(default_factory=dict)
    queues: list[str] = Field(default_factory=list)
    ssr_requirements_valid: bool = True
    mock_timatic_case: str = "CLEAR"
    demo_notes: str = ""


class BookingLookupRequest(StrictModel):
    record_locator: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    last_name: str = Field(min_length=1, max_length=64)

    @field_validator("record_locator")
    @classmethod
    def normalize_locator(cls, value: str) -> str:
        return value.upper()

    @field_validator("last_name")
    @classmethod
    def normalize_last_name(cls, value: str) -> str:
        return value.strip().upper()


class PassportInput(StrictModel):
    passport_number: str = Field(min_length=5, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    nationality: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]{2,3}$")
    issuing_country: str = Field(min_length=2, max_length=3, pattern=r"^[A-Za-z]{2,3}$")
    date_of_birth: str | None = None
    expiry_date: str
    purpose_of_stay: str = Field(default="TOURISM", pattern=r"^(TOURISM|BUSINESS|TRANSIT|STUDY|WORK|FAMILY|MEDICAL)$")
    duration_of_stay_days: int = Field(default=7, ge=1, le=365)
    # Kept as a private-compatible legacy field for saved demo sessions, but the UI does not expose it
    # and the mock rules derive their outcome from destination, dates, nationality and purpose/stay.
    visa_evidence_status: str = Field(default="NOT_VERIFIED", pattern=r"^(NOT_VERIFIED|MOCK_VERIFIED|NOT_APPLICABLE)$")
    senior_citizen: bool = False

    @field_validator("date_of_birth", "expiry_date")
    @classmethod
    def iso_date_only(cls, value: str | None, info) -> str | None:
        if value is None or value == "":
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Use a valid date in YYYY-MM-DD format") from exc
        if info.field_name == "date_of_birth" and parsed > calendar_date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value

    @field_validator("nationality", "issuing_country")
    @classmethod
    def valid_iso_country(cls, value: str) -> str:
        code = value.strip().upper()
        if pycountry is not None:
            found = pycountry.countries.get(alpha_2=code) if len(code) == 2 else pycountry.countries.get(alpha_3=code) if len(code) == 3 else None
            if not found:
                raise ValueError("Use a real ISO-3166 alpha-2 or alpha-3 country code (for example IN or IND)")
            return code
        if not re.fullmatch(r"[A-Z]{2,3}", code):
            raise ValueError("Use a 2- or 3-letter ISO country code")
        return code

    @field_validator("passport_number")
    @classmethod
    def normalize_passport(cls, value: str) -> str:
        compact = value.upper().replace(" ", "")
        if not re.fullmatch(r"[A-Z0-9]{5,20}", compact):
            raise ValueError("Use 5–20 letters and digits only; spaces and angle brackets are not valid passport input")
        return compact


class SaveDocumentsRequest(StrictModel):
    documents: dict[str, PassportInput]


class SeatSelectionRequest(StrictModel):
    passenger_key: str
    journey_key: str | None = None
    seat_code: str = Field(pattern=r"(?i)^(?:[1-9]|[1-3][0-9]|40)[A-F]$")

    @field_validator("seat_code")
    @classmethod
    def normalize_seat(cls, value: str) -> str:
        return value.upper()


class SeatSkipRequest(StrictModel):
    passenger_key: str | None = None
    journey_key: str | None = None


class AncillarySelection(StrictModel):
    passenger_key: str
    ancillary_codes: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("ancillary_codes")
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        allowed = {"EXTRA_BAG", "LOUNGE", "WIFI", "MEAL"}
        normalized = [value.upper() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Duplicate ancillary selections are not allowed")
        if not set(normalized).issubset(allowed):
            raise ValueError("Unknown ancillary code")
        return normalized


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    # Explicit pending slot/context enables one-line answers to be validated against a field schema.
    pending_field: str | None = Field(default=None, max_length=64)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentEvent(StrictModel):
    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    component: str
    action: str
    outcome: str
    message: str
    correlation_id: str | None = None


class EligibilityRuleResult(StrictModel):
    rule_id: str
    name: str
    decision: Decision
    message: str
    source_type: str = "reference_derived_mock_policy"


class EligibilityResult(StrictModel):
    decision: Decision
    flight_type: str
    rules: list[EligibilityRuleResult]
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    policy_notice: str = "Synthetic demonstration rules; not an official travel or airline decision."


class TimaticJourneyResult(StrictModel):
    passenger_key: str
    journey_key: str
    transaction_id: str
    decision: Decision
    evaluation_code: str
    reasons: list[str] = Field(default_factory=list)
    sources: list[dict[str, str]] = Field(default_factory=list)
    provider: str = "MOCK_TIMATIC"
    mock_only: bool = True


class ChatResponse(StrictModel):
    answer: str
    intent: str
    session_id: str | None = None
    session_token: str | None = None
    citations: list[dict[str, str]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
