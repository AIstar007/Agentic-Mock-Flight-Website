from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


def search_payload(trip_type="round_trip"):
    departure = date.today() + timedelta(days=35)
    return {
        "origin": "DEL",
        "destination": "DXB",
        "trip_type": trip_type,
        "departure_date": departure.isoformat(),
        "return_date": (departure + timedelta(days=7)).isoformat() if trip_type == "round_trip" else None,
        "adults": 1,
        "children": 0,
        "infants": 0,
        "cabin": "Economy",
    }


def passenger():
    return {
        "title": "MR",
        "first_name": "ALEX",
        "last_name": "DEMO",
        "passenger_type": "ADT",
        "date_of_birth": "1990-05-20",
        "nationality": "IN",
        "gender": "U",
    }


def test_airport_search_and_flight_search_are_mock_only():
    with TestClient(app) as client:
        airports = client.get("/api/v1/airports/search", params={"q": "Dubai", "limit": 5})
        assert airports.status_code == 200
        assert any(x["iata"] == "DXB" for x in airports.json()["results"])

        response = client.post("/api/v1/flights/search", json=search_payload())
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["mock_only"] is True
        assert len(result["outbound_flights"]) == 3
        assert len(result["return_flights"]) == 3
        assert result["origin"]["iata"] == "DEL"
        assert result["destination"]["iata"] == "DXB"
        assert all(f["mock_only"] for f in result["outbound_flights"])


def test_one_way_search_does_not_return_inbound_flights():
    with TestClient(app) as client:
        response = client.post("/api/v1/flights/search", json=search_payload("one_way"))
        assert response.status_code == 200, response.text
        assert response.json()["return_flights"] == []


def test_booking_confirmation_registers_synthetic_booking_for_checkin():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload()).json()
        out = search["outbound_flights"][0]
        back = search["return_flights"][0]
        out_fare = next(f for f in out["fare_options"] if f["cabin"] == "Economy")
        back_fare = next(f for f in back["fare_options"] if f["cabin"] == "Economy")
        confirm = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": out["flight_id"],
            "outbound_fare_family": out_fare["code"],
            "return_flight_id": back["flight_id"],
            "return_fare_family": back_fare["code"],
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "+971500000000",
            "accepted_terms": True,
            "extras": ["WIFI"],
            "payment_simulation": "approve",
        })
        assert confirm.status_code == 200, confirm.text
        booking = confirm.json()
        assert booking["mock_only"] is True
        assert len(booking["booking_reference"]) == 6
        assert booking["payment_status"] == "SIMULATED_APPROVED"
        assert len(booking["itinerary"]) == 2

        lookup = client.post("/api/v1/bookings/lookup", json={
            "record_locator": booking["booking_reference"],
            "last_name": "DEMO",
        })
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["session"]["booking"]["record_locator"] == booking["booking_reference"]


def test_declined_synthetic_payment_creates_no_booking():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload("one_way")).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        response = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "1234567890",
            "accepted_terms": True,
            "payment_simulation": "decline",
        })
        assert response.status_code == 402
        assert "No charge was made" in response.json()["detail"]


def test_confirmation_rejects_fare_not_in_selected_search():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload("one_way")).json()
        flight = search["outbound_flights"][0]
        response = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": "NOT_A_REAL_FARE",
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "1234567890",
            "accepted_terms": True,
            "payment_simulation": "approve",
        })
        assert response.status_code == 422
        assert "fare family" in response.json()["detail"].lower()


def test_confirmation_requires_acceptance_of_demo_terms():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload("one_way")).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        response = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "1234567890",
            "accepted_terms": False,
            "payment_simulation": "approve",
        })
        assert response.status_code == 422


def test_site_assistant_can_fill_route_from_language():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "Find flights from Delhi to Dubai", "page": "book"
        })
        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "flight_search"
        action = payload["actions"][0]
        assert action["type"] == "FILL_FLIGHT_SEARCH"
        assert action["origin"] == "DEL"
        assert action["destination"] == "DXB"


def test_pdp_context_is_opt_in_and_does_not_override_faq_intent():
    with TestClient(app) as client:
        faq = client.post("/api/v1/site-assistant/chat", json={
            "message": "Can I add baggage later?",
            "page": "passengers",
            "context": {"pdp": {"passengers": [], "contact": {}}},
        })
        assert faq.status_code == 200
        assert faq.json()["intent"] != "pdp_context"

        guidance = client.post("/api/v1/site-assistant/chat", json={
            "message": "What should I enter next?",
            "page": "passengers",
            "context": {"pdp": {"passengers": [], "contact": {}}},
        })
        assert guidance.status_code == 200
        assert guidance.json()["intent"] == "pdp_context"


def test_faq_question_about_checkin_window_is_not_misrouted_to_navigation():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "What is the online check-in window?",
            "page": "passengers",
            "context": {"pdp": {"passengers": [], "contact": {}}},
        })
        assert response.status_code == 200
        body = response.json()
        assert body["intent"] != "pdp_context"
        assert body["intent"] != "booking_navigation"

        navigate = client.post("/api/v1/site-assistant/chat", json={
            "message": "Open check-in", "page": "help"
        })
        assert navigate.status_code == 200
        assert navigate.json()["intent"] == "booking_navigation"
        assert navigate.json()["actions"][0]["target"] == "checkin"


def test_site_assistant_maps_multiple_labeled_booking_fields_in_one_turn():
    from datetime import date, timedelta
    departure = (date.today() + timedelta(days=30)).isoformat()
    message = (f"origin: Delhi, destination: Dubai, departure date: {departure}, "
               "trip type: one way, adults: 2, children: 0, infants: 0, cabin: Economy")
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": message,
            "page": "book",
            "context": {"assistant_task": "booking", "pending_field": "origin", "workflow": {}},
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["intent"] == "booking_search_ready"
        assert payload["assistant_values"]["origin"] == "DEL"
        assert payload["assistant_values"]["destination"] == "DXB"
        assert payload["assistant_values"]["trip_type"] == "one_way"
        fields = {action.get("field") for action in payload["actions"] if action["type"] == "SET_SEARCH_FORM_FIELD"}
        assert {"origin", "destination", "departure_date", "trip_type", "adults", "children", "infants", "cabin"}.issubset(fields)


def test_site_assistant_maps_multiple_labeled_passenger_fields_atomically():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "first name: Alen, last name: Thomas, email: demo@example.com, phone: 919876543210, terms: yes",
            "page": "passengers",
            "context": {
                "assistant_task": "passenger_details",
                "pending_field": "first_name",
                "pending_passenger_index": 0,
                "pdp": {
                    "passengers": [{"title": "MR", "first_name": "", "middle_name": "", "last_name": "", "passenger_type": "ADT", "date_of_birth": "", "nationality": "IN", "gender": "U"}],
                    "contact": {"email": "", "phone": ""},
                    "accepted_terms": False,
                },
            },
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["intent"] == "slot_filled"
        assert payload["pending_field"] == "date_of_birth"
        types = [action["type"] for action in payload["actions"]]
        assert types.count("UPDATE_PASSENGER_FIELD") == 2
        assert "UPDATE_CONTACT_FIELD" in types
        assert "UPDATE_ACCEPTED_TERMS" in types
        assert "demo@example.com" not in payload["answer"]


def test_labelled_field_extractor_does_not_consume_faq_questions_as_form_values():
    from app.assistant_schema import extract_labeled_field_values
    allowed = {"passport_number", "nationality", "expiry_date", "contact_email"}
    assert extract_labeled_field_values("Can I travel with my passport?", allowed) == {}
    assert extract_labeled_field_values("What is my email address?", allowed) == {}
    assert extract_labeled_field_values("passport number: MOCK12345, nationality is IN, expiry date = 2034-12-31", allowed) == {
        "passport_number": "MOCK12345", "nationality": "IN", "expiry_date": "2034-12-31"
    }


def test_site_assistant_accepts_complete_labelled_booking_command_on_first_turn():
    from datetime import date, timedelta
    departure = (date.today() + timedelta(days=45)).isoformat()
    message = (f"Book a flight by chat: origin: DEL, destination: DXB, departure date: {departure}, "
               "trip type: one way, adults: 1, children: 0, infants: 0, cabin: Economy")
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={"message": message, "page": "book"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["assistant_task"] == "booking"
        assert payload["intent"] == "booking_search_ready"
        assert payload["assistant_values"]["origin"] == "DEL"
        assert payload["assistant_values"]["destination"] == "DXB"
        assert payload["pending_field"] is None
        assert len(payload["actions"]) >= 8


def test_site_assistant_optional_passenger_fields_can_be_filled_after_required_fields():
    from datetime import date
    dob = (date.today().replace(year=date.today().year-30)).isoformat()
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": f"title: Mr, date of birth: {dob}, nationality: IN, gender: M",
            "page": "passengers",
            "context": {
                "assistant_task": "passenger_details", "pending_field": None,
                "pending_passenger_index": 0,
                "pdp": {"passengers": [{"title": "MR", "first_name": "ALEX", "middle_name": "", "last_name": "THOMAS", "passenger_type": "ADT", "date_of_birth": "", "nationality": "IN", "gender": "U"}], "contact": {"email": "demo@example.com", "phone": "919876543210"}, "accepted_terms": True},
            },
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["intent"] == "pdp_complete"
        updates = [x for x in payload["actions"] if x["type"] == "UPDATE_PASSENGER_FIELD"]
        assert {x["field"] for x in updates} >= {"title", "date_of_birth", "nationality", "gender"}


def test_site_assistant_answers_faq_interrupt_and_preserves_pending_booking_field():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "What is the online check-in window?", "page": "book",
            "context": {"assistant_task": "booking", "pending_field": "origin", "workflow": {}},
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pending_field"] == "origin"
        assert payload["assistant_task"] == "booking"
        assert payload["intent"] in {"faq", "faq_abstain"}


def test_booking_confirmation_persists_valid_mock_seat_selection():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload("one_way")).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        response = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "1234567890",
            "accepted_terms": True,
            "selected_seats": {"outbound:0": "40C"},
            "payment_simulation": "approve",
        })
        assert response.status_code == 200, response.text
        assert response.json()["selected_seats"] == {"outbound:0": "40C"}
        booking = client.post("/api/v1/bookings/lookup", json={
            "record_locator": response.json()["booking_reference"],
            "last_name": "DEMO",
        })
        assert booking.status_code == 200, booking.text
        assert booking.json()["session"]["booking"]["seat_assignments"] == {"outbound:0": "40C"}


def test_booking_confirmation_rejects_unavailable_or_wrong_leg_seats():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json=search_payload("one_way")).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        base = {
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [passenger()],
            "contact_email": "demo@example.com",
            "contact_phone": "1234567890",
            "accepted_terms": True,
            "payment_simulation": "approve",
        }
        unavailable = client.post("/api/v1/bookings/confirm", json={**base, "selected_seats": {"outbound:0": "1B"}})
        assert unavailable.status_code == 422
        outside_inventory = client.post("/api/v1/bookings/confirm", json={**base, "selected_seats": {"outbound:0": "41A"}})
        assert outside_inventory.status_code == 422
        wrong_leg = client.post("/api/v1/bookings/confirm", json={**base, "selected_seats": {"return:0": "1A"}})
        assert wrong_leg.status_code == 422
        wrong_passenger = client.post("/api/v1/bookings/confirm", json={**base, "selected_seats": {"outbound:1": "1A"}})
        assert wrong_passenger.status_code == 422


def test_booking_confirmation_rejects_age_category_mismatch():
    with TestClient(app) as client:
        body = search_payload("one_way")
        body["children"] = 1
        search = client.post("/api/v1/flights/search", json=body).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        payload = {
            "search_id": search["search_id"], "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [passenger(), {**passenger(), "passenger_type": "CHD", "date_of_birth": "1990-05-20"}],
            "contact_email": "demo@example.com", "contact_phone": "1234567890",
            "accepted_terms": True, "payment_simulation": "approve",
        }
        response = client.post("/api/v1/bookings/confirm", json=payload)
        assert response.status_code == 422
        assert "age on outbound departure" in response.json()["detail"]


def test_booking_confirmation_rejects_infant_who_turns_two_before_return():
    from datetime import timedelta
    with TestClient(app) as client:
        body = search_payload("round_trip")
        body["infants"] = 1
        search = client.post("/api/v1/flights/search", json=body).json()
        out, back = search["outbound_flights"][0], search["return_flights"][0]
        out_fare = next(f for f in out["fare_options"] if f["cabin"] == "Economy")
        back_fare = next(f for f in back["fare_options"] if f["cabin"] == "Economy")
        return_date = date.fromisoformat(back["departure_date"])
        dob = return_date.replace(year=return_date.year - 2)
        payload = {
            "search_id": search["search_id"], "outbound_flight_id": out["flight_id"],
            "outbound_fare_family": out_fare["code"], "return_flight_id": back["flight_id"],
            "return_fare_family": back_fare["code"],
            "passengers": [passenger(), {**passenger(), "passenger_type": "INF", "date_of_birth": dob.isoformat(), "accompanying_adult_index": 0}],
            "contact_email": "demo@example.com", "contact_phone": "1234567890",
            "accepted_terms": True, "payment_simulation": "approve",
        }
        response = client.post("/api/v1/bookings/confirm", json=payload)
        assert response.status_code == 422
        assert "turns 2 before the return" in response.json()["detail"]


def test_chat_booking_start_does_not_reprompt_for_fields_already_in_form():
    from datetime import date, timedelta
    with TestClient(app) as client:
        departure = date.today() + timedelta(days=30)
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "Book a flight by chat", "page": "search", "context": {
                "workflow": {"origin": "DEL", "destination": "DXB", "departure_date": departure.isoformat(), "return_date": (departure + timedelta(days=7)).isoformat(), "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "cabin": "Economy"},
                "assistant_task": None, "pending_field": None,
            }
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intent"] == "slot_prompt"
        assert body["pending_field"] == "origin"
        assert body["assistant_values"]["_chat_started"] is True
        assert "departing from" in body["answer"].lower()
        # Even though the web form contains demo defaults, the explicit chat flow asks for the route.
        # The standalone confirmation path below still preserves any existing values.
        resumed = client.post("/api/v1/site-assistant/chat", json={
            "message": "no", "page": "search", "context": {
                "workflow": {"origin": "DEL", "destination": "DXB", "departure_date": departure.isoformat(), "return_date": (departure + timedelta(days=7)).isoformat(), "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "cabin": "Economy"},
                "assistant_task": "booking", "pending_field": "confirm_booking_edit",
                "assistant_values": {"origin": "DEL", "destination": "DXB", "departure_date": departure.isoformat(), "return_date": (departure + timedelta(days=7)).isoformat(), "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "cabin": "Economy", "_resume_pending_field": None},
            }
        }).json()
        assert resumed["intent"] == "edit_declined"
        assert resumed["pending_field"] is None
        assert "no changes" in resumed["answer"].lower()


def test_chat_flight_choice_uses_numbered_visible_options():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "Choose flight 1", "page": "outbound", "context": {
                "flight_leg": "outbound", "flight_options": [{"flight_id": "demo-flight-1", "fare_options": [
                    {"code": "ECONOMY_SPECIAL", "label": "Economy Special"},
                    {"code": "ECONOMY_FLEX", "label": "Economy Flex"},
                ]}], "assistant_task": None, "pending_field": None,
            }
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intent"] == "slot_prompt"
        assert body["assistant_task"] == "flight_fare"
        assert body["pending_field"] == "fare_family"
        assert "Economy Special" in body["answer"]


def test_booking_edit_yes_walks_fields_and_skip_preserves_current_route():
    from datetime import date, timedelta
    workflow = {"origin": "DEL", "destination": "DXB", "departure_date": (date.today() + timedelta(days=30)).isoformat(), "return_date": (date.today() + timedelta(days=37)).isoformat(), "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "cabin": "Economy"}
    with TestClient(app) as client:
        first = client.post("/api/v1/site-assistant/chat", json={"message": "modify flight search details", "page": "search", "context": {"workflow": workflow, "assistant_task": "booking", "pending_field": None}}).json()
        assert first["pending_field"] == "confirm_booking_edit"
        context = {"workflow": workflow, "assistant_task": first["assistant_task"], "pending_field": first["pending_field"], "assistant_values": first["assistant_values"]}
        accepted = client.post("/api/v1/site-assistant/chat", json={"message": "yes", "page": "search", "context": context}).json()
        assert accepted["intent"] == "edit_started"
        assert accepted["pending_field"] == "origin"
        skip_context = {"workflow": workflow, "assistant_task": "booking", "pending_field": "origin", "assistant_values": {**workflow, "_edit_mode": True}}
        skipped = client.post("/api/v1/site-assistant/chat", json={"message": "skip", "page": "search", "context": skip_context}).json()
        assert skipped["intent"] == "slot_skipped"
        assert skipped["pending_field"] == "destination"
        assert skipped["card"]["fields"][0]["value"] == "DEL"


def test_passenger_page_overrides_stale_booking_slot_for_short_answer():
    from datetime import date, timedelta
    workflow = {"origin": "DEL", "destination": "DXB", "departure_date": (date.today() + timedelta(days=30)).isoformat(), "return_date": (date.today() + timedelta(days=37)).isoformat(), "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "cabin": "Economy"}
    pdp = {"passengers": [{"title": "MR", "first_name": "", "middle_name": "", "last_name": "", "passenger_type": "ADT", "date_of_birth": "", "nationality": "IN", "gender": "U"}], "current_passenger_index": 0, "contact": {"email": "", "phone": ""}, "accepted_terms": False}
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={"message": "ALEX", "page": "passengers", "context": {"workflow": workflow, "assistant_task": "booking", "pending_field": "departure_date", "pdp": pdp}})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["assistant_task"] == "passenger_details"
        # The short reply is mapped to the first missing field on the visible PDP,
        # not parsed as the stale booking task's departure date.
        assert body["intent"] == "slot_filled"
        assert body["pending_field"] == "last_name"
        assert any(action.get("type") == "UPDATE_PASSENGER_FIELD" and action.get("field") == "first_name" and action.get("value") == "ALEX" for action in body["actions"])
        assert "first name" in body["answer"].lower()



def test_section_edit_asks_confirmation_then_reviews_optional_extras_one_by_one():
    with TestClient(app) as client:
        first = client.post("/api/v1/site-assistant/chat", json={
            "message": "Edit this section", "page": "extras",
            "context": {"page_context": {"selected_extras": ["WIFI"]}},
        })
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["intent"] == "edit_confirmation"
        assert body["pending_field"] == "confirm_section_edit"
        assert body["actions"] == []
        assert "no form values" in body["card"]["note"].lower()

        state = {"assistant_task": body["assistant_task"], "pending_field": body["pending_field"],
                 "assistant_values": body["assistant_values"], "page_context": {"selected_extras": ["WIFI"]}}
        started = client.post("/api/v1/site-assistant/chat", json={"message": "yes", "page": "extras", "context": state}).json()
        assert started["intent"] == "edit_started"
        assert started["pending_field"] == "section_extra_EXTRA_BAG"
        assert started["actions"] == [{"type": "NAVIGATE_FLOW_STEP", "target": "extras"}]

        state.update({"assistant_task": started["assistant_task"], "pending_field": started["pending_field"], "assistant_values": started["assistant_values"]})
        skipped = client.post("/api/v1/site-assistant/chat", json={"message": "skip", "page": "extras", "context": state}).json()
        assert skipped["intent"] == "slot_filled"
        assert skipped["pending_field"] == "section_extra_LOUNGE"
        assert skipped["actions"] == []
        assert skipped["assistant_values"]["_edit_codes"] == ["WIFI"]

        state.update({"assistant_task": skipped["assistant_task"], "pending_field": skipped["pending_field"], "assistant_values": skipped["assistant_values"]})
        added = client.post("/api/v1/site-assistant/chat", json={"message": "add", "page": "extras", "context": state}).json()
        assert added["intent"] == "slot_filled"
        assert added["pending_field"] == "section_extra_WIFI"
        assert {"type": "TOGGLE_EXTRAS", "codes": ["LOUNGE"]} in added["actions"]
        assert set(added["assistant_values"]["_edit_codes"]) == {"WIFI", "LOUNGE"}


def test_question_about_extras_does_not_mutate_selected_extras():
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={
            "message": "Can I add extra baggage later?", "page": "extras",
            "context": {"page_context": {"selected_extras": []}},
        })
        assert response.status_code == 200
        body = response.json()
        assert body["actions"] == []
        assert body["intent"] not in {"extras_selected", "extras_removed"}
        assert body.get("card", {}).get("type") == "faq"


def test_chat_can_change_return_date_directly_without_losing_existing_search_values():
    from datetime import date, timedelta
    departure = (date.today() + timedelta(days=30)).isoformat()
    old_return = (date.today() + timedelta(days=37)).isoformat()
    new_return = (date.today() + timedelta(days=45)).isoformat()
    workflow = {"origin": "DEL", "destination": "DXB", "departure_date": departure, "return_date": old_return, "trip_type": "round_trip", "adults": 1, "children": 0, "infants": 0, "senior_citizens": 1, "cabin": "Economy"}
    context = {"workflow": workflow, "assistant_task": "booking", "pending_field": "confirm_booking_edit", "assistant_values": {**workflow, "_resume_pending_field": None}}
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={"message": f"change return date to {new_return}", "page": "search", "context": context})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["intent"] == "booking_fields_updated"
        assert body["assistant_values"]["return_date"] == new_return
        assert body["assistant_values"]["origin"] == "DEL"
        assert body["assistant_values"]["destination"] == "DXB"
        assert any(action["type"] == "SET_SEARCH_FORM_FIELD" and action["field"] == "return_date" and action["value"] == new_return for action in body["actions"])


def test_chat_booking_multi_value_passenger_counts_are_applied_and_not_asked_again():
    from datetime import date, timedelta
    departure = (date.today() + timedelta(days=30)).isoformat()
    return_date = (date.today() + timedelta(days=37)).isoformat()
    workflow = {"origin": "DEL", "destination": "DXB", "trip_type": "round_trip", "departure_date": departure, "return_date": return_date, "adults": 1, "children": 0, "infants": 0, "senior_citizens": 0, "cabin": "Economy", "_chat_started": True, "_chat_completed_fields": ["origin", "destination", "trip_type", "departure_date", "return_date"]}
    with TestClient(app) as client:
        response = client.post("/api/v1/site-assistant/chat", json={"message": "1 adult, 2 children, 1 infant, 1 senior citizen", "page": "search", "context": {"workflow": workflow, "assistant_values": workflow, "assistant_task": "booking", "pending_field": "adults"}})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["assistant_values"]["adults"] == 1
        assert body["assistant_values"]["children"] == 2
        assert body["assistant_values"]["infants"] == 1
        assert body["assistant_values"]["senior_citizens"] == 1
        assert body["pending_field"] == "cabin"
        assert body["intent"] == "slot_filled"


def test_booking_addons_are_priced_and_stored_per_passenger():
    with TestClient(app) as client:
        search = client.post("/api/v1/flights/search", json={**search_payload("one_way"), "adults": 2}).json()
        flight = search["outbound_flights"][0]
        fare = next(f for f in flight["fare_options"] if f["cabin"] == "Economy")
        response = client.post("/api/v1/bookings/confirm", json={
            "search_id": search["search_id"],
            "outbound_flight_id": flight["flight_id"],
            "outbound_fare_family": fare["code"],
            "passengers": [{**passenger(), "first_name": "ALEN"}, {**passenger(), "first_name": "EMMA"}],
            "contact_email": "demo@example.com", "contact_phone": "1234567890", "accepted_terms": True,
            "extras": ["EXTRA_BAG"], "passenger_extras": {"0": ["EXTRA_BAG"], "1": []},
            "payment_simulation": "approve",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["passenger_extras"]["0"][0]["code"] == "EXTRA_BAG"
        assert body["passenger_extras"]["1"] == []
        assert body["total"] == 1380  # Two 600-AED fares plus one 180-AED add-on.
        assert body["extras"][0]["code"] == "EXTRA_BAG"


def test_country_registry_accepts_alpha3_and_rejects_unknown_iso_country_codes():
    import pytest
    from pydantic import ValidationError
    from app.models import PassportInput
    from app.assistant_schema import parse_field_value

    document = PassportInput.model_validate({
        "passport_number": "MOCK12345", "nationality": "IND", "issuing_country": "ARE",
        "expiry_date": "2035-12-31", "purpose_of_stay": "TOURISM", "duration_of_stay_days": 14,
    })
    assert document.nationality == "IND"
    assert document.issuing_country == "ARE"
    assert parse_field_value("nationality", "India")["nationality"] == "IN"
    assert parse_field_value("issuing_country", "IND")["issuing_country"] == "IND"
    with pytest.raises(ValidationError):
        PassportInput.model_validate({"passport_number": "MOCK12345", "nationality": "ZZZ", "issuing_country": "ARE", "expiry_date": "2035-12-31"})


def test_future_date_of_birth_is_rejected_and_seat_rows_are_bounded_to_inventory():
    import pytest
    from datetime import date, timedelta
    from pydantic import ValidationError
    from app.models import PassportInput, SeatSelectionRequest
    from app.assistant_schema import parse_field_value, FieldParseError

    with pytest.raises(ValidationError, match="future"):
        PassportInput.model_validate({"passport_number": "MOCK12345", "nationality": "IND", "issuing_country": "ARE", "date_of_birth": (date.today() + timedelta(days=1)).isoformat(), "expiry_date": "2035-12-31"})
    with pytest.raises(FieldParseError, match="future"):
        parse_field_value("date_of_birth", (date.today() + timedelta(days=1)).isoformat())
    valid = SeatSelectionRequest(passenger_key="PAX-001", journey_key="outbound", seat_code="40c")
    assert valid.seat_code == "40C"
    with pytest.raises(ValidationError):
        SeatSelectionRequest(passenger_key="PAX-001", journey_key="outbound", seat_code="41A")


def test_booking_passenger_nationality_checks_actual_iso_registry():
    import pytest
    from pydantic import ValidationError
    from app.commerce import PassengerInput
    assert PassengerInput(first_name="ALEN", last_name="THOMAS", nationality="IND").nationality == "IND"
    with pytest.raises(ValidationError, match="valid ISO-3166"):
        PassengerInput(first_name="ALEN", last_name="THOMAS", nationality="ZZZ")


def test_chat_passenger_wizard_requires_under16_companion_after_fields():
    import pytest
    from datetime import date, timedelta
    from app.assistant_schema import next_booking_field, parse_field_value, FieldParseError
    departure = (date.today() + timedelta(days=30)).isoformat()
    adult = {"title":"MR", "first_name":"ALEN", "last_name":"THOMAS", "date_of_birth":"1990-01-01", "nationality":"IND", "gender":"M", "passenger_type":"ADT", "accompanying_adult_index":None}
    infant = {"title":"MSTR", "first_name":"BABY", "last_name":"THOMAS", "date_of_birth":"2025-01-01", "nationality":"IND", "gender":"U", "passenger_type":"INF", "accompanying_adult_index":None}
    field, index = next_booking_field([adult, infant], {"email":"demo@example.com", "phone":"919876543210"}, True, departure)
    assert (field, index) == ("accompanying_adult_index", 1)
    assert parse_field_value("accompanying_adult_index", "Passenger 1 · ALEN THOMAS")["accompanying_adult_index"] == 0
    with pytest.raises(FieldParseError, match="passenger number"):
        parse_field_value("accompanying_adult_index", "No adult")
