"""Deterministic schema-aware slot parsing for the synthetic flight assistant."""
from datetime import date, datetime, timedelta
import re

try:
    import pycountry
except ImportError:  # fallback still permits ISO-shaped values when running without optional data package
    pycountry = None

class FieldParseError(ValueError): pass

FIELD_QUESTIONS = {
 "origin":"Which city or airport are you departing from?",
 "destination":"Which city or airport are you travelling to?",
 "trip_type":"Is this one-way or a return trip?",
 "departure_date":"What is your departure date? Use YYYY-MM-DD or 21 November 2026.",
 "return_date":"What date would you like to return? You can also say 'one-way'.",
 "adults":"How many adults are travelling? Enter 1–9.",
 "children":"How many children are travelling? Enter 0–9.",
 "infants":"How many infants are travelling? There cannot be more infants than adults.",
 "senior_citizens":"How many senior-citizen passengers are travelling? They are separate from other adults in this demo.",
 "cabin":"Which cabin: Economy, Premium Economy, Business or First Class? You can say Business Class or First Class.",
 "first_name":"What is the passenger's first name, exactly as shown on the travel document?",
 "last_name":"What is the passenger's surname, exactly as shown on the travel document?",
 "middle_name":"What middle name should I record? Say 'skip' to leave it blank.",
 "date_of_birth":"What is the passenger's date of birth? Use YYYY-MM-DD.",
 "nationality":"What is the passenger's nationality? Enter a 2- or 3-letter ISO code such as IN or IND (a country name is also accepted).",
 "gender":"What gender marker should I record: M, F, X or U (unspecified)?",
 "accompanying_adult_index":"Which adult passenger (age 16 or older on departure) will accompany this infant or under-16 traveller? Choose a passenger number from the options. Infants must have one adult companion; this demo blocks under-16 travel without an adult.",
 "contact_email":"What contact email should the synthetic booking use?",
 "contact_phone":"What contact phone number should the synthetic booking use?",
 "accepted_terms":"Do you accept the synthetic demo terms? Answer yes or no.",
 "record_locator":"What is the 6–8 character synthetic booking reference? Do not use a real PNR.",
 "surname":"What passenger surname is associated with that demo booking reference?",
 "passport_number":"Enter a fictional passport number for the demo. Do not enter a real passport number.",
 "issuing_country":"Which country issued the fictional passport? Enter a 2- or 3-letter ISO code such as IN or IND (a country name is also accepted).",
 "expiry_date":"What is the fictional passport expiry date? Use YYYY-MM-DD.",
 "purpose_of_stay":"Select or enter the purpose of stay: tourism, business, transit, study, work, family or medical.",
 "duration_of_stay_days":"How many days is the intended stay? Enter a whole number from 1 to 365.",
 "seat_code":"Which available demo seat? Enter a seat such as 14A.",
}
COUNTRIES = {"india":"IN","indian":"IN","in":"IN","uae":"AE","united arab emirates":"AE","ae":"AE","uk":"GB","united kingdom":"GB","gb":"GB","usa":"US","united states":"US","us":"US","canada":"CA","ca":"CA","australia":"AU","au":"AU","germany":"DE","de":"DE","france":"FR","fr":"FR","singapore":"SG","sg":"SG","pakistan":"PK","pk":"PK","nepal":"NP","np":"NP","sri lanka":"LK","lk":"LK"}

def normalize_country_code(value: str) -> str:
    """Resolve any ISO-3166 alpha-2/alpha-3 code or official country name."""
    raw = str(value or "").strip()
    low = raw.casefold()
    if low in COUNTRIES:
        return COUNTRIES[low]
    upper = raw.upper()
    if pycountry is not None:
        if len(upper) == 2:
            country = pycountry.countries.get(alpha_2=upper)
            if country: return upper
        if len(upper) == 3:
            country = pycountry.countries.get(alpha_3=upper)
            if country: return upper
        try:
            matches = pycountry.countries.search_fuzzy(raw)
            if matches: return matches[0].alpha_2
        except LookupError:
            pass
        raise FieldParseError("Use a valid ISO-3166 country name or 2/3-letter country code, e.g. India, IN or IND.")
    if re.fullmatch(r"[A-Z]{2,3}", upper): return upper
    raise FieldParseError("Use a country name we recognize or a 2–3 letter ISO-3166 code such as IN or IND.")
ALIASES = {
 "title":{"mr":"MR","mrs":"MRS","ms":"MS","miss":"MISS","mstr":"MSTR","master":"MSTR","dr":"DR"},
 "trip_type":{"one way":"one_way","one-way":"one_way","oneway":"one_way","single":"one_way","return":"round_trip","round trip":"round_trip","round-trip":"round_trip"},
 "cabin":{"economy":"Economy","premium economy":"Premium Economy","premium":"Premium Economy","business":"Business","business class":"Business","business cabin":"Business","first":"First","first class":"First","first-class":"First","first cabin":"First"},
 "gender":{"m":"M","male":"M","f":"F","female":"F","x":"X","u":"U","unspecified":"U","prefer not to say":"U"},
 "purpose_of_stay":{"tourism":"TOURISM","tourist":"TOURISM","holiday":"TOURISM","business":"BUSINESS","transit":"TRANSIT","study":"STUDY","student":"STUDY","work":"WORK","employment":"WORK","family":"FAMILY","visiting family":"FAMILY","medical":"MEDICAL","medical treatment":"MEDICAL"},
}


# Named-value extraction lets a passenger answer several schema fields in one message.
# Only explicitly labelled values are extracted; unlabeled/ambiguous values still trigger follow-up.
FIELD_LABELS = {
    "record_locator": ("booking reference", "record locator", "pnr"),
    "surname": ("passenger surname", "surname", "last name"),
    "origin": ("origin", "departure airport"),
    "destination": ("destination", "arrival airport"),
    "trip_type": ("trip type", "journey type"),
    "departure_date": ("departure date", "depart date", "departing on"),
    "return_date": ("return date", "returning on"),
    "adults": ("adults", "adult passengers", "adult count"),
    "children": ("children", "child", "child passengers", "children count"),
    "infants": ("infants", "infant", "infant passengers", "infants count"),
    "senior_citizens": ("senior citizens", "senior citizen passengers", "seniors", "senior passengers"),
    "cabin": ("cabin class", "travel class", "cabin"),
    "first_name": ("first name", "given name", "forename"),
    "last_name": ("last name", "family name", "surname"),
    "middle_name": ("middle name", "middle names"),
    "title": ("title", "salutation"),
    "date_of_birth": ("date of birth", "birth date", "dob"),
    "nationality": ("nationality", "nationality code"),
    "gender": ("gender", "gender marker"),
    "contact_email": ("contact email", "email address", "email"),
    "contact_phone": ("contact phone", "phone number", "mobile number", "phone"),
    "accepted_terms": ("accepted terms", "terms", "consent"),
    "passport_number": ("passport number", "passport no", "passport #", "passport"),
    "issuing_country": ("issuing country", "country of issue", "issued by"),
    "expiry_date": ("passport expiry date", "expiry date", "expiration date", "expires"),
    "purpose_of_stay": ("purpose of stay", "travel purpose", "purpose"),
    "duration_of_stay_days": ("duration of stay", "stay duration", "days of stay", "number of days"),
    "visa_evidence_status": ("visa evidence status", "visa status"),
    "seat_code": ("seat", "seat number"),
}

def extract_labeled_field_values(text: str, allowed_fields: set[str] | None = None) -> dict[str, str]:
    """Extract only unambiguous, explicitly labelled values from one-line replies.

    Examples: ``passport: MOCK12345, nationality: India, expiry date: 2030-01-01``.
    Values are still passed through ``parse_field_value`` before any state is changed.
    """
    candidates = []
    for field, labels in FIELD_LABELS.items():
        allowed = allowed_fields is None or field in allowed_fields
        for label in sorted(labels, key=len, reverse=True):
            # Every recognized label delimits values; disallowed legacy fields (e.g. manual visa status)
            # are never returned to callers, but must not become part of the preceding value.
            pattern = re.compile(r"(?<![A-Za-z])" + re.escape(label) + r"\s*(?::|=|\bis\b|\bare\b)\s*", re.IGNORECASE)
            for match in pattern.finditer(text):
                candidates.append((match.start(), match.end(), field, label, allowed))
    if not candidates:
        return {}
    # For duplicate synonyms mapping to the same slot, retain the earliest specific label.
    candidates.sort(key=lambda x: (x[0], -int(x[4]), -(x[2] == x[3]), -(x[1]-x[0])))
    result = {}
    selected = []
    for item in candidates:
        start, end, field, label, *_ = item
        if any(not (end <= s or start >= e) for s, e, *_ in selected):
            continue
        selected.append(item)
    selected.sort(key=lambda x: x[0])
    for idx, (_, value_start, field, _, allowed) in enumerate(selected):
        if field in result or not allowed:
            continue
        value_end = selected[idx + 1][0] if idx + 1 < len(selected) else len(text)
        value = text[value_start:value_end].strip().strip(" ,;|\t\n")
        value = re.sub(r"^(?:is|equals)\s+", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"[ ,;|]+$", "", value).strip()
        value = re.sub(r"\s+(?:and|then)$", "", value, flags=re.IGNORECASE).strip()
        if value:
            result[field] = value
    return result

def parse_date(value: str) -> str:
    s=value.strip(); low=s.casefold(); today=date.today()
    if low=="tomorrow": return (today+timedelta(days=1)).isoformat()
    if low=="today": return today.isoformat()
    for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d %B %Y","%d %b %Y","%B %d, %Y","%b %d, %Y","%d %B","%d %b","%B %d","%b %d"):
        try:
            d=datetime.strptime(s,fmt).date()
            if "%Y" not in fmt and d<today: d=d.replace(year=today.year+1)
            return d.isoformat()
        except ValueError: pass
    raise FieldParseError("I couldn't read that date. Please use YYYY-MM-DD or a date like 21 November 2026.")

def parse_field_value(field: str, text: str, context: dict|None=None) -> dict:
    s=text.strip().strip(chr(34)).strip(); low=s.casefold(); ctx=context or {}
    # Allow natural labels, e.g. \"email is demo@example.com\" while preserving the expected schema slot.
    prefixes={"first_name":"first name","last_name":"last name","middle_name":"middle name","surname":"surname","contact_email":"email","contact_phone":"phone","date_of_birth":"date of birth","nationality":"nationality","issuing_country":"issuing country","passport_number":"passport number","expiry_date":"expiry date","record_locator":"booking reference","departure_date":"departure date","return_date":"return date"}
    prefix=prefixes.get(field)
    if prefix:
        s=re.sub(r"^"+re.escape(prefix)+r"\s*(?:is\s*)?[:=]?\s*","",s,flags=re.I).strip(); low=s.casefold()
    if field=="middle_name" and low in {"skip","none","no middle name","not provided"}: return {field:""}
    if field in {"first_name","last_name","middle_name","surname"}:
        if not s or len(s)>60 or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*",s): raise FieldParseError("Use a name up to 60 characters, with letters, spaces, hyphens, apostrophes or periods.")
        if field=="first_name" and not ctx.get("last_name","").strip() and len(s.split())>1:
            parts=s.split(); return {"first_name":parts[0],"last_name":" ".join(parts[1:])}
        return {field: s.upper() if field in {"last_name","surname"} else s}
    if field in {"departure_date","return_date","date_of_birth","expiry_date"}:
        d=parse_date(s)
        parsed_day = date.fromisoformat(d)
        if field in {"departure_date","return_date"} and parsed_day < date.today(): raise FieldParseError("This date cannot be in the past for the booking demo.")
        if field == "date_of_birth" and parsed_day > date.today(): raise FieldParseError("Date of birth cannot be in the future. Use the actual past birth date in YYYY-MM-DD format.")
        if field == "date_of_birth" and parsed_day < date.today().replace(year=date.today().year - 120): raise FieldParseError("Date of birth is outside the supported validation range (maximum age 120 years).")
        if field == "date_of_birth" and ctx.get("senior_citizen") and ctx.get("departure_date"):
            try:
                travel_day = date.fromisoformat(str(ctx.get("departure_date"))[:10])
                age = travel_day.year - parsed_day.year - ((travel_day.month, travel_day.day) < (parsed_day.month, parsed_day.day))
                if age < 60: raise FieldParseError(f"This passenger was {age} on departure; senior-citizen passengers must be at least 60.")
            except ValueError: pass
        if field=="expiry_date" and parsed_day<=date.today(): raise FieldParseError("The fictional passport expiry date must be in the future; please check it.")
        return {field:d}
    if field in {"nationality","issuing_country"}:
        code=normalize_country_code(s)
        return {field:code}
    if field == "duration_of_stay_days":
        match = re.fullmatch(r"\s*(\d{1,3})(?:\s*(?:days?|nights?))?\s*", low)
        if not match:
            raise FieldParseError("Enter the duration as a whole number of days from 1 to 365.")
        days = int(match.group(1))
        if not 1 <= days <= 365:
            raise FieldParseError("Stay duration must be between 1 and 365 days.")
        return {field: days}
    if field == "visa_evidence_status":
        raise FieldParseError("Document verification status is calculated automatically from the configured rules; it cannot be entered or changed manually.")
    if field == "accompanying_adult_index":
        match = re.fullmatch(r"\s*(?:(?:adult|passenger)\s*)?(\d+)(?:\s*[·:–-].*)?\s*", s, re.IGNORECASE)
        if not match or int(match.group(1)) < 1:
            raise FieldParseError("Choose an adult passenger using the option card or enter a passenger number such as ‘Passenger 1’.")
        return {field: int(match.group(1)) - 1}
    if field in ALIASES:
        result=ALIASES[field].get(low)
        if not result: raise FieldParseError("Please choose one of the supported values shown in the previous prompt.")
        return {field:result}
    if field=="contact_email":
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",s) or len(s)>254: raise FieldParseError("Please enter a valid email such as demo@example.com.")
        return {field:s.lower()}
    if field=="contact_phone":
        if not re.fullmatch(r"\+?[0-9 ()-]{7,24}", s) or not 7<=len(re.sub(r"\D","",s))<=18: raise FieldParseError("Enter a phone number with 7–18 digits; only a leading +, spaces, parentheses and hyphens are allowed.")
        return {field:s}
    if field=="accepted_terms":
        if low in {"yes","y","accept","accepted","agree","i agree","i accept","confirmed"}: return {field:True}
        if low in {"no","n","decline","do not accept","not now"}: return {field:False}
        raise FieldParseError("Please answer yes or no.")
    if field=="record_locator":
        value=re.sub(r"\s+","",s).upper()
        if not re.fullmatch(r"[A-Z0-9]{6,8}",value): raise FieldParseError("A demo reference must contain 6–8 letters or digits. Use a reference from the demo examples.")
        return {field:value}
    if field=="passport_number":
        value=re.sub(r"\s+","",s).upper()
        if not re.fullmatch(r"[A-Z0-9]{5,20}",value): raise FieldParseError("For this demo only, use a fictional 5–20 character alphanumeric passport value. Do not enter a real passport number.")
        return {field:value}
    if field=="seat_code":
        value=s.upper().removeprefix("SEAT ").strip()
        if not re.fullmatch(r"(?:[1-9]|[1-3][0-9]|40)[A-F]",value): raise FieldParseError("Choose a seat in the current inventory from row 1 through row 40 (e.g. 14A or 40C).")
        return {field:value}
    if field in {"adults","children","infants","senior_citizens"}:
        words={"zero":0,"none":0,"no":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9}
        m=re.search(r"\d+",s); n=int(m.group()) if m else words.get(low)
        if n is None or not 0<=n<=9: raise FieldParseError("Enter a whole number from 0 to 9.")
        return {field:n}
    raise FieldParseError(f"No parser is registered for the {field} field.")

def _date_value(value):
    if isinstance(value, date): return value
    try: return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError): return None


def age_on_date(dob, travel_date=None):
    birth = _date_value(dob); day = _date_value(travel_date) or date.today()
    if birth is None or birth > date.today(): return None
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def booking_pdp_field_order(passengers: list[dict], departure_date=None):
    ordered = []
    for idx, passenger in enumerate(passengers):
        fields = ["title", "first_name", "middle_name", "last_name", "date_of_birth", "nationality", "gender"]
        category = str(passenger.get("passenger_type", "ADT")).upper()
        age = age_on_date(passenger.get("date_of_birth"), departure_date)
        if category in {"CHD", "CHILD", "INF", "INFANT"} or (age is not None and age < 16): fields.append("accompanying_adult_index")
        ordered.extend((field, idx) for field in fields)
    ordered.extend([("contact_email", None), ("contact_phone", None), ("accepted_terms", None)])
    return ordered


def next_booking_field(passengers: list[dict], contact: dict, accepted_terms: bool, departure_date=None):
    for i, p in enumerate(passengers):
        for f in ("title", "first_name", "last_name"):
            if not str(p.get(f, "")).strip(): return f, i
        if not str(p.get("date_of_birth", "")).strip(): return "date_of_birth", i
        if not str(p.get("nationality", "")).strip(): return "nationality", i
        if not str(p.get("gender", "")).strip(): return "gender", i
        category = str(p.get("passenger_type", "ADT")).upper()
        age = age_on_date(p.get("date_of_birth"), departure_date)
        required = category in {"INF", "INFANT"} or (age is not None and age < 16)
        if required and p.get("accompanying_adult_index") in (None, ""): return "accompanying_adult_index", i
    email = str(contact.get("email", contact.get("contact_email", ""))).strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email): return "contact_email", None
    if len(re.sub(r"\D", "", str(contact.get("phone", contact.get("contact_phone", ""))))) < 7: return "contact_phone", None
    if not accepted_terms: return "accepted_terms", None
    return None, None


def detailed_checkin_error(step: str, message: str, status_code: int|None=None) -> str:
    low=(message or "").casefold(); heading=f"I couldn't complete the {step.lower()} step."
    if status_code==401 or "session token" in low: cause="The demo session is unauthorized or its token has expired."; action="Reload the synthetic booking with its demo reference and surname."
    elif status_code==404 or "not found" in low or "expired" in low: cause="The synthetic booking/session was not found, has expired, or the reference and surname did not match."; action="Check the demo reference/surname, then start a new session."
    elif any(x in low for x in ("passport_expired","expiry","expired document")): cause="The mock rules detected a past-dated document expiry."; action="Review the fictional expiry date in Passenger details and re-run validation."
    elif any(x in low for x in ("not_verified","visa_evidence_unverified","review_required","review required")): cause="Synthetic document evidence is unverified or the result requires manual review. This POC cannot assess real visa eligibility."; action="Read the reason codes/source notes, correct known demo input errors, or contact the official airline/authorized document-check channel for a real trip."
    elif any(x in low for x in ("eligibility","payment","balance","codeshare","carrier","queue","ssr","infant","journey")): cause="A deterministic booking rule did not pass. The reference-derived checks can include payment/status, journeys, passenger fields, carrier/codeshare, SSR, check-in timing and infant conditions; some are fixture-specific, not universal Emirates rules."; action="Open Booking eligibility rules, read every non-PASS reason, verify demo data, and request authorized review if unresolved."
    elif "seat" in low: cause="The seat may be unavailable, already assigned, tied to another journey, or blocked by earlier eligibility/document checks."; action="Refresh the mock seat map and select another available seat for the correct passenger/journey after the earlier gates pass."
    elif status_code==422 or any(x in low for x in ("invalid","required","must")): cause="A submitted value or prerequisite did not match the expected schema."; action="Review the rejected field, allowed format and earlier steps, then retry."
    elif status_code==409 or any(x in low for x in ("cannot","blocked","gate")): cause="A workflow precondition failed, so the transaction was safely blocked."; action="Return to the blocking step, resolve the issue and retry. The chatbot cannot override the gate."
    elif status_code in {502,503,504} or any(x in low for x in ("timeout","unavailable","connection","provider","network")): cause="A backend dependency/service did not respond reliably."; action="Retry after a pause and check the hosted API health/logs if it persists. Unknown provider outcomes stay REVIEW_REQUIRED."
    else: cause="The operation returned an error that cannot safely be classified more specifically."; action="Keep the workflow blocked, inspect visible validation details, correct the issue and retry."
    return f"{heading}\n\n**What happened:** {cause}\n**What to do next:** {action}\n**Details:** {message[:400] or 'No further safe diagnostic detail was returned.'}"


def next_document_field(doc: dict, passenger_type: str | None = None) -> str | None:
    """Return first invalid/missing document slot; regular-adult DOB may be skipped."""
    category = str(passenger_type or doc.get("passenger_type") or "ADT").strip().upper().replace("-", "_")
    needs_dob = category in {"CHD", "CHILD", "INF", "INFANT", "SENIOR", "SENIOR_CITIZEN", "SENIOR CITIZEN"} or bool(doc.get("senior_citizen"))
    for field in ("passport_number", "nationality", "issuing_country", "date_of_birth", "expiry_date", "purpose_of_stay", "duration_of_stay_days"):
        if field == "date_of_birth" and not needs_dob and not str(doc.get(field, "")).strip():
            continue
        value = str(doc.get(field, "")).strip()
        if field == "passport_number" and not re.fullmatch(r"[A-Z0-9]{5,20}", value.upper()):
            return field
        if field in {"nationality", "issuing_country"}:
            try:
                normalize_country_code(value)
            except FieldParseError:
                return field
        if field in {"date_of_birth", "expiry_date"}:
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d").date()
                if field == "date_of_birth" and parsed > date.today(): return field
                if field == "expiry_date" and parsed <= date.today(): return field
            except ValueError:
                return field
        if field == "purpose_of_stay" and value.upper() not in {"TOURISM", "BUSINESS", "TRANSIT", "STUDY", "WORK", "FAMILY", "MEDICAL"}:
            return field
        if field == "duration_of_stay_days":
            try: days = int(value)
            except ValueError: return field
            if not 1 <= days <= 365: return field
    return None
