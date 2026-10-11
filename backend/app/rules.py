from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.models import Decision, EligibilityResult, EligibilityRuleResult

RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "rules_catalog.json"


def flight_type(booking: dict[str, Any]) -> str:
    return "International" if any(j.get("international") for j in booking.get("journeys", [])) else "Domestic"


def _rule(rule_id: str, name: str, passed: bool, message: str, source_type: str = "reference_derived_mock_policy") -> EligibilityRuleResult:
    return EligibilityRuleResult(rule_id=rule_id, name=name, decision=Decision.PASS if passed else Decision.FAIL, message=message, source_type=source_type)


def _rule_tasks(booking: dict[str, Any], *, carrier_mode: str = "demo_emirates", window_profile: str = "demo_emirates", provider_allowed_carrier: str = "") -> list[Callable[[], EligibilityRuleResult]]:
    """Create isolated deterministic rule tasks for fan-out/fan-in evaluation.

    Every callable is pure with respect to the booking input. No LLM participates in the
    allow/block decision. Rules copied from the supplied references remain flagged as mock
    reference-derived policy until an airline owner approves their applicability.
    """
    passengers = booking.get("passengers") or []
    journeys = booking.get("journeys") or []
    ftype = flight_type(booking)

    def journey_present():
        ok = bool(journeys) and all(j.get("origin") and j.get("destination") for j in journeys)
        msg = "Journey structure is present." if ok else "At least one journey with origin and destination is required."
        return _rule("JOURNEY_PRESENT", "Journey is present", ok, msg)

    def booking_confirmed():
        ok = str(booking.get("booking_status", "")).upper() == "CONFIRMED"
        return _rule("BOOKING_CONFIRMED", "Booking confirmed", ok, "Booking is confirmed." if ok else "Booking is not confirmed; contact the airline.")

    def payment_complete():
        try:
            balance = float(booking.get("balance_due", -1))
        except (TypeError, ValueError):
            balance = -1
        ok = str(booking.get("payment_status", "")).upper() == "PAID_IN_FULL" and balance == 0
        msg = "Payment status is paid in full and balance due is zero." if ok else "Payment status or balance does not meet the mock check-in requirement."
        return _rule("PAYMENT_COMPLETE", "Payment complete", ok, msg)

    def passenger_names():
        ok = bool(passengers) and all(
            1 <= len(str(p.get("first_name", ""))) <= 32
            and 1 <= len(str(p.get("last_name", ""))) <= 32
            and 1 <= len(str(p.get("title", ""))) <= 6
            for p in passengers
        )
        msg = "Required passenger name fields are present." if ok else "First name, last name and title must be present and within reference length limits."
        return _rule("PASSENGER_NAME_COMPLETE", "Passenger names complete", ok, msg)

    def adult_present():
        # Use age on outbound departure when a DOB is available; passenger_type alone
        # can be stale or incorrectly entered. A guardian must be at least 16 for this
        # public-reference rule. Unknown ages fail closed for accompaniment assessment.
        departure = None
        if journeys:
            try: departure = datetime.fromisoformat(str(journeys[0].get("departure_date", ""))).date()
            except (TypeError, ValueError): departure = None
        ages = []
        for passenger in passengers:
            try:
                dob = datetime.fromisoformat(str(passenger.get("date_of_birth", ""))).date()
                if departure is None: raise ValueError("departure date missing")
                age = departure.year - dob.year - ((departure.month, departure.day) < (dob.month, dob.day))
                ages.append(age)
            except (TypeError, ValueError):
                ages.append(None)
        adult_guardian = any(age is not None and age >= 16 for age in ages)
        # Legacy seeded demo records predate DOB enforcement. A typed ADT with no DOB
        # may keep the existing synthetic test flow moving, but new bookings require DOB
        # and are age-validated at confirmation; never infer a minor's age as adult.
        adult_guardian = adult_guardian or any(age is None and str(p.get("passenger_type", "")).upper() == "ADT" for p, age in zip(passengers, ages))
        unknown_age = any(age is None for age in ages)
        minor_present = any(age is not None and age < 16 for age in ages)
        ok = adult_guardian and not (minor_present and not adult_guardian)
        if unknown_age and not adult_guardian:
            ok = False
            msg = "Passenger age could not be established for accompaniment. Enter valid date of birth for every passenger; unknown age is not treated as adult approval."
        elif minor_present and not adult_guardian:
            ok = False
            msg = "A passenger under 16 has no accompanying passenger aged 16 or over. Emirates requires airport-desk check-in for under-16 passengers travelling without an adult; this demo blocks online check-in."
        else:
            ok = adult_guardian
            msg = "An accompanying passenger aged 16 or over is present." if ok else "No passenger aged 16 or over could be verified as an accompanying adult."
        return _rule("ADULT_ACCOMPANIMENT", "Age and adult accompaniment", ok, msg, "official_public_reference_plus_mock")

    def carrier_supported():
        if carrier_mode == "provider_reference":
            # Release 1.0.7 rejects non-empty externalIdentifier values and any carrierCode
            # that differs from the environment's configured airline. The carrier is explicit
            # configuration because provider staging configuration is external to the synthetic Emirates-inspired UI.
            allowed = str(provider_allowed_carrier or "").strip().upper()
            if not allowed:
                return _rule("PROVIDER_CARRIER_CONFIGURED", "Provider carrier configured", False,
                             "Set NAVITAIRE_ALLOWED_CARRIER from the authorized provider environment before live validation.")
            ok = bool(journeys) and all(
                not j.get("codeshare", False)
                and str(j.get("marketing_carrier", "")).upper() == allowed
                and str(j.get("operating_carrier", "")).upper() == allowed
                for j in journeys
            )
            msg = f"All journeys match configured provider carrier {allowed} with no codeshare." if ok else "A codeshare or carrier mismatch was detected; reference workflow blocks check-in."
            return _rule("CODESHARE_RESTRICTION", "Provider carrier and codeshare restriction", ok, msg)
        ok = all(
            not j.get("codeshare", False)
            and str(j.get("marketing_carrier", "")).upper() == "EK"
            and str(j.get("operating_carrier", "")).upper() == "EK"
            for j in journeys
        )
        msg = "Synthetic EK-only workflow rule passed." if ok else "This mock workflow blocks non-EK or codeshare journeys; verify actual carrier rules with the airline."
        return _rule("CODESHARE_RESTRICTION", "Mock operating-carrier rule", ok, msg)

    def queues_clear():
        ok = not bool(booking.get("queues"))
        return _rule("QUEUE_EMPTY", "Booking queue clear", ok, "No booking queues are present." if ok else "Booking has a queue flag and requires manual handling.")

    def ssr_valid():
        ok = booking.get("ssr_requirements_valid") is True
        return _rule("SSR_REQUIREMENTS_VALID", "Special service requirements", ok, "Mock SSR requirements are valid." if ok else "Special service requirements are missing or invalid; manual handling required.")

    def minor_accompanied():
        has_adult = any(str(p.get("passenger_type", "")).upper() == "ADT" for p in passengers)
        minor_without_adult = any(str(p.get("passenger_type", "")).upper() in {"CHD", "INF"} for p in passengers) and not has_adult
        return _rule("MINOR_ACCOMPANIMENT", "Minor accompaniment", not minor_without_adult, "No unaccompanied minor condition was detected by the mock rule." if not minor_without_adult else "A minor without an adult is not eligible under the reference-derived mock rule.")

    def intl_infant():
        blocked = ftype == "International" and any(p.get("infant") for p in passengers)
        msg = "No international-infant block detected." if not blocked else "The reference workflow blocks this synthetic international infant case; verify actual carrier policy."
        return _rule("INTERNATIONAL_INFANT_CHECKIN", "International infant check-in condition", not blocked, msg)

    def checkin_window():
        ok = bool(journeys)
        messages: list[str] = []
        for journey in journeys:
            offset = journey.get("departure_offset_hours")
            if not isinstance(offset, (int, float)):
                ok = False
                messages.append(f"{journey.get('journey_key', 'journey')}: departure time missing")
                continue
            if window_profile == "reference":
                # Matches release 1.0.7 validation_tasks.py: domestic 1-48h, international 1-24h.
                lower = 1.0
                upper = 24.0 if journey.get("international") else 48.0
                profile_label = "reference"
            else:
                # Emirates-inspired demo policy only: online check-in 48h to 90m in this mock.
                lower = 1.5 if journey.get("international") else 1.0
                upper = 48.0
                profile_label = "demo"
            if not lower <= float(offset) <= upper:
                ok = False
                messages.append(f"{journey.get('journey_key', 'journey')}: departure outside {profile_label} window ({offset:.1f}h remaining)")
            else:
                messages.append(f"{journey.get('journey_key', 'journey')}: {offset:.1f}h remaining; inside {profile_label} window")
        return _rule("CHECKIN_WINDOW", "Check-in window", ok, "; ".join(messages), "official_public_reference_plus_mock")

    return [journey_present, booking_confirmed, payment_complete, passenger_names, adult_present,
            carrier_supported, queues_clear, ssr_valid, minor_accompanied, intl_infant, checkin_window]


def _result(booking: dict[str, Any], rules: list[EligibilityRuleResult]) -> EligibilityResult:
    decision = Decision.PASS if all(item.decision == Decision.PASS for item in rules) else Decision.FAIL
    return EligibilityResult(decision=decision, flight_type=flight_type(booking), rules=rules, evaluated_at=datetime.now(timezone.utc))


def run_booking_eligibility(booking: dict[str, Any]) -> EligibilityResult:
    """Serial deterministic path used by unit tests and dependency-light execution."""
    return _result(booking, [task() for task in _rule_tasks(booking)])


async def run_eligibility_concurrently(
    booking: dict[str, Any], *, carrier_mode: str = "demo_emirates", window_profile: str = "demo_emirates",
    provider_allowed_carrier: str = "",
) -> EligibilityResult:
    """Fan out independent rules; provider-reference mode uses explicit provider carrier policy."""
    tasks = _rule_tasks(
        booking,
        carrier_mode=carrier_mode,
        window_profile=window_profile,
        provider_allowed_carrier=provider_allowed_carrier,
    )
    results = await asyncio.gather(*(asyncio.to_thread(task) for task in tasks))
    # asyncio.gather preserves task order, making the result stable for UI and audits.
    return _result(booking, list(results))


def rule_catalog() -> list[dict[str, Any]]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
