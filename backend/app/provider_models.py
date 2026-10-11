from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderTestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class NavitaireBookingRetrieveTest(ProviderTestModel):
    record_locator: str = Field(min_length=6, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    last_name: str = Field(min_length=1, max_length=64)


class TimaticDocumentDetail(ProviderTestModel):
    document_code: str = Field(alias="documentCode", min_length=1, max_length=12)
    document_expiry_date: datetime = Field(alias="documentExpiryDate")
    document_issue_country: str = Field(alias="documentIssueCountry", min_length=2, max_length=3)
    nationality: str = Field(min_length=2, max_length=3)
    birth_date: date = Field(alias="birthDate")

    @field_validator("document_expiry_date")
    @classmethod
    def expiry_datetime_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("documentExpiryDate must include a timezone")
        return value


class TimaticPoint(ProviderTestModel):
    date_time: datetime = Field(alias="dateTime")
    point: str = Field(min_length=3, max_length=4)
    type: str = Field(default="AIRPORT", min_length=1, max_length=20)

    @field_validator("date_time")
    @classmethod
    def date_time_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("itinerary dateTime must include a timezone")
        return value


class TimaticDurationOfStay(ProviderTestModel):
    duration: int = Field(ge=0, le=3650)
    time_unit: str = Field(alias="timeUnit", pattern=r"^(DAYS|HOURS)$")


class TimaticSegment(ProviderTestModel):
    arrival: TimaticPoint
    departure: TimaticPoint
    duration_of_stay: TimaticDurationOfStay | None = Field(default=None, alias="durationOfStay")
    segment_type: str | None = Field(default=None, alias="segmentType", max_length=20)
    luggage_collected: bool = Field(default=True, alias="luggageCollected")
    operating_carrier: str = Field(alias="operatingCarrier", min_length=2, max_length=3)
    processing_entity: str = Field(alias="processingEntity", min_length=2, max_length=20)


class TimaticItineraryDetails(ProviderTestModel):
    segments: list[TimaticSegment] = Field(min_length=1, max_length=12)


class TimaticPassengerDetails(ProviderTestModel):
    birth_country: str = Field(alias="birthCountry", min_length=2, max_length=3)
    birth_date: date = Field(alias="birthDate")
    gender: str = Field(min_length=1, max_length=2)
    nationality: str = Field(min_length=2, max_length=3)
    resident_country_code: str = Field(alias="residentCountryCode", min_length=2, max_length=3)


class TimaticDocumentRequest(ProviderTestModel):
    channel: str = Field(default="Web", max_length=24)
    document_details: list[TimaticDocumentDetail] = Field(alias="documentDetails", min_length=1, max_length=12)
    itinerary_details: TimaticItineraryDetails = Field(alias="itineraryDetails")
    language: str = Field(default="EN", min_length=2, max_length=5)
    passenger_details: TimaticPassengerDetails = Field(alias="passengerDetails")
    passenger_id: UUID = Field(alias="passengerId")
    transaction_id: str = Field(alias="transactionId", min_length=1, max_length=80)

    def to_provider_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
