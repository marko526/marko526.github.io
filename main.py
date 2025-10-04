import itertools
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import geopy.distance
from flask import Flask, jsonify, render_template, request

from airports_coordinates import airport_lookup, airports_coordinates, airport_suggestions

AIRCRAFT_TYPES = (
    {"key": "citation_mustang", "label": "Cessna Citation Mustang", "speed": 340, "seats": 4},
    {"key": "citation_cj", "label": "Cessna Citation CJ", "speed": 377, "seats": 5},
    {"key": "citation_cj2", "label": "Cessna Citation CJ2", "speed": 405, "seats": 6},
    {"key": "citation_cj3", "label": "Cessna Citation CJ3", "speed": 416, "seats": 7},
    {"key": "citation_xls", "label": "Cessna Citation XLS", "speed": 433, "seats": 8},
    {"key": "phenom_300", "label": "Embraer Phenom 300", "speed": 453, "seats": 7},
    {"key": "airbus_a320", "label": "Airbus A320", "speed": 447, "seats": 180},
    {"key": "boeing_737", "label": "Boeing 737", "speed": 455, "seats": 189},
    {"key": "challenger_350", "label": "Bombardier Challenger 350", "speed": 459, "seats": 9},
    {"key": "challenger_604", "label": "Bombardier Challenger 604", "speed": 459, "seats": 12},
    {"key": "challenger_605", "label": "Bombardier Challenger 605", "speed": 459, "seats": 12},
)

MISSION_TYPES: Tuple[str, ...] = ("Ferry", "Tech", *[f"{i} pax" for i in range(1, 21)])
PIC_OPTIONS: Tuple[str, ...] = ("LUB", "JAG", "VLK", "SID", "MEK")
SIC_OPTIONS: Tuple[str, ...] = ("ZUD", "VEK", "MAJ", "DAS")
CA1_OPTIONS: Tuple[str, ...] = ("AAA", "BBB")

DEFAULT_GOOGLE_MAPS_KEY = "AIzaSyA6LHxtgxLJtKjJcx6Nrt7v96uozCYuSYs"
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", DEFAULT_GOOGLE_MAPS_KEY)

FINDPLANE_SPEED = 404
GROUND_BUFFER = timedelta(hours=1)

_AIRCRAFT_INFO: Dict[str, Dict[str, float]] = {item["key"]: item for item in AIRCRAFT_TYPES}


@dataclass
class FleetAircraft:
    registration: str
    type_key: str
    max_pax: int

    @property
    def info(self) -> Dict[str, float]:
        return _AIRCRAFT_INFO[self.type_key]


@dataclass
class ScheduleEntry:
    entry_id: int
    departure_airport: str
    arrival_airport: str
    departure_time: datetime
    aircraft_type: str
    mission_type: str
    pic: str
    sic: str
    ca1: str
    requested_registration: Optional[str] = None
    assigned_registration: Optional[str] = None
    arrival_time: Optional[datetime] = None
    flight_time_hours: float = 0.0
    flight_time_text: str = ""
    auto_generated: bool = False
    notes: Optional[str] = None


@dataclass
class AircraftState:
    registration: str
    type_key: str
    max_pax: int
    available_from: datetime = datetime.min
    location: Optional[str] = None
    accumulated_ferry: float = 0.0


app = Flask(__name__)

FLEET: Dict[str, FleetAircraft] = {}
SCHEDULE_ENTRIES: Dict[int, ScheduleEntry] = {}
AUTO_SEGMENTS: List[ScheduleEntry] = []
SCHEDULE_ERRORS: List[str] = []
ENTRY_ID_COUNTER = itertools.count(1)
AUTO_ID_COUNTER = itertools.count(1000)


def get_coordinates(airport_code: str) -> Optional[Tuple[float, float]]:
    return airports_coordinates.get(airport_code)


def get_aircraft_info(key: Optional[str]) -> Optional[Dict[str, float]]:
    if key is None:
        return None
    return _AIRCRAFT_INFO.get(key)


def _default_flight_form() -> Dict[str, str]:
    return {"airport1": "", "airport2": "", "aircraft_type": AIRCRAFT_TYPES[0]["key"]}


def _default_findplane_form() -> Dict[str, str]:
    return {"aircraft1_pos": "", "aircraft2_pos": "", "airport1h": ""}


def _parse_datetime_local(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def compute_flight_stats(distance_nm: float, speed_kts: float) -> Dict[str, float]:
    base_time = distance_nm / speed_kts
    buffer = 0.333 if base_time > 0.4 else 0.20
    total_hours = base_time + buffer

    hours = int(total_hours)
    minutes = int((total_hours - hours) * 60)
    return {"total_hours": total_hours, "hours": hours, "minutes": minutes}


def render_index(*, selected_aircraft: Optional[str] = None, flight_form: Optional[Dict[str, str]] = None,
                 findplane_form: Optional[Dict[str, str]] = None, **context):
    if selected_aircraft is None:
        selected_aircraft = AIRCRAFT_TYPES[0]["key"]
    if flight_form is None:
        flight_form = _default_flight_form()
    if findplane_form is None:
        findplane_form = _default_findplane_form()

    context.setdefault("result_summary", None)
    context.setdefault("map_route", None)

    return render_template(
        "index.html",
        airports=airport_suggestions,
        aircraft_types=AIRCRAFT_TYPES,
        mission_types=MISSION_TYPES,
        pic_options=PIC_OPTIONS,
        sic_options=SIC_OPTIONS,
        ca1_options=CA1_OPTIONS,
        selected_aircraft=selected_aircraft,
        flight_form=flight_form,
        findplane_form=findplane_form,
        google_maps_api_key=GOOGLE_MAPS_API_KEY,
        **context,
    )


@app.route("/")
def index():
    return render_index()


@app.route("/calculate", methods=["POST"])
def calculate():
    dep_airport = request.form["airport1"].upper()
    arr_airport = request.form["airport2"].upper()
    aircraft_type = request.form.get("aircraft_type")

    aircraft_info = get_aircraft_info(aircraft_type)
    if aircraft_info is None:
        return render_index(
            flight_error="Please select a supported aircraft type.",
            flight_form={"airport1": dep_airport, "airport2": arr_airport, "aircraft_type": AIRCRAFT_TYPES[0]["key"]},
        )

    speed = float(aircraft_info["speed"])

    flight_form_state = {"airport1": dep_airport, "airport2": arr_airport, "aircraft_type": aircraft_type}

    if dep_airport not in airports_coordinates:
        return render_index(
            flight_error=f"Airport 1 with code {dep_airport} not found in our database.",
            selected_aircraft=aircraft_type,
            flight_form=flight_form_state,
        )
    if arr_airport not in airports_coordinates:
        return render_index(
            flight_error=f"Airport 2 with code {arr_airport} not found in our database.",
            selected_aircraft=aircraft_type,
            flight_form=flight_form_state,
        )

    departure_airport = airport_lookup[dep_airport]
    arrival_airport = airport_lookup[arr_airport]

    distance = geopy.distance.great_circle(
        get_coordinates(dep_airport), get_coordinates(arr_airport)
    ).nm
    flight_stats = compute_flight_stats(distance, speed)
    result_time = f"{flight_stats['hours']} hours {flight_stats['minutes']} minutes"
    summary = (
        f"Flight time between {departure_airport.display_label} and "
        f"{arrival_airport.display_label} using {aircraft_info['label']} is {result_time}."
    )

    map_route = {
        "origin": {
            "lat": departure_airport.coordinates[0],
            "lng": departure_airport.coordinates[1],
            "label": departure_airport.display_label,
        },
        "destination": {
            "lat": arrival_airport.coordinates[0],
            "lng": arrival_airport.coordinates[1],
            "label": arrival_airport.display_label,
        },
    }

    return render_index(
        result_summary=summary,
        map_route=map_route,
        selected_aircraft=aircraft_type,
        flight_form=flight_form_state,
    )


@app.route("/findplane", methods=["POST"])
def findplane():
    aircraft1_pos = request.form["aircraft1_pos"].upper()
    aircraft2_pos = request.form["aircraft2_pos"].upper()
    dep_airport = request.form["airport1h"].upper()

    speed = float(FINDPLANE_SPEED)

    findplane_form_state = {
        "aircraft1_pos": aircraft1_pos,
        "aircraft2_pos": aircraft2_pos,
        "airport1h": dep_airport,
    }

    if aircraft1_pos not in airports_coordinates:
        return render_index(
            findplane_error=f"Aircraft 1 position with code {aircraft1_pos} not found in our database.",
            findplane_form=findplane_form_state,
        )
    if aircraft2_pos not in airports_coordinates:
        return render_index(
            findplane_error=f"Aircraft 2 position with code {aircraft2_pos} not found in our database.",
            findplane_form=findplane_form_state,
        )
    if dep_airport not in airports_coordinates:
        return render_index(
            findplane_error=f"Departure airport with code {dep_airport} not found in our database.",
            findplane_form=findplane_form_state,
        )

    distance_1 = geopy.distance.great_circle(
        get_coordinates(aircraft1_pos), get_coordinates(dep_airport)
    ).nm
    flight_time_1 = compute_flight_stats(distance_1, speed)["total_hours"]

    distance_2 = geopy.distance.great_circle(
        get_coordinates(aircraft2_pos), get_coordinates(dep_airport)
    ).nm
    flight_time_2 = compute_flight_stats(distance_2, speed)["total_hours"]
    minutes_difference = int(abs(flight_time_1 - flight_time_2) * 60)

    if flight_time_1 == flight_time_2:
        findplaneresults = "You may choose either aircraft; both arrive at the same time."
    elif flight_time_1 > flight_time_2:
        findplaneresults = f"Choose aircraft 2, you will save {minutes_difference} minutes."
    else:
        findplaneresults = f"Choose aircraft 1, you will save {minutes_difference} minutes."

    return render_index(
        findplaneresults=findplaneresults,
        map_route=None,
        findplane_form=findplane_form_state,
    )


def _format_datetime_local(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M")


def _format_datetime_display(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _mission_required_pax(mission_type: str) -> int:
    if mission_type and mission_type.endswith(" pax"):
        try:
            return int(mission_type.split()[0])
        except ValueError:
            return 0
    return 0


def _distance_between(code_one: str, code_two: str) -> float:
    return geopy.distance.great_circle(
        get_coordinates(code_one), get_coordinates(code_two)
    ).nm


def _airport_display(code: str) -> str:
    if not code:
        return ""
    record = airport_lookup.get(code)
    if record is None:
        return code
    return record.display_label


def _refresh_entry_metrics(entry: ScheduleEntry) -> None:
    if entry.departure_airport not in airports_coordinates or entry.arrival_airport not in airports_coordinates:
        raise ValueError("Unknown airport code provided in schedule entry.")

    info = get_aircraft_info(entry.aircraft_type)
    if info is None:
        raise ValueError("Unsupported aircraft type in schedule entry.")

    distance = _distance_between(entry.departure_airport, entry.arrival_airport)
    stats = compute_flight_stats(distance, float(info["speed"]))
    entry.flight_time_hours = stats["total_hours"]
    entry.flight_time_text = f"{stats['hours']}h {stats['minutes']}m"
    entry.arrival_time = entry.departure_time + timedelta(hours=entry.flight_time_hours)


def _build_aircraft_states(type_key: str) -> Dict[str, AircraftState]:
    relevant = [aircraft for aircraft in FLEET.values() if aircraft.type_key == type_key]
    return {
        aircraft.registration: AircraftState(
            registration=aircraft.registration,
            type_key=aircraft.type_key,
            max_pax=aircraft.max_pax,
        )
        for aircraft in relevant
    }


def _evaluate_candidate(state: AircraftState, entry: ScheduleEntry, info: Dict[str, float]) -> Optional[Tuple[datetime, datetime, float]]:
    latest_finish = entry.departure_time - GROUND_BUFFER

    if state.location is None:
        if state.available_from > latest_finish:
            return None
        return state.available_from, state.available_from, 0.0

    if state.location == entry.departure_airport:
        if state.available_from > latest_finish:
            return None
        ready_time = max(state.available_from, latest_finish)
        return ready_time, ready_time, 0.0

    distance = _distance_between(state.location, entry.departure_airport)
    stats = compute_flight_stats(distance, float(info["speed"]))
    reposition_duration = timedelta(hours=stats["total_hours"])

    if state.available_from + reposition_duration > latest_finish:
        return None

    reposition_start = max(state.available_from, latest_finish - reposition_duration)
    reposition_end = reposition_start + reposition_duration
    return reposition_start, reposition_end, stats["total_hours"]


def _optimise_assignments() -> List[ScheduleEntry]:
    auto_segments: List[ScheduleEntry] = []
    for entry in SCHEDULE_ENTRIES.values():
        entry.assigned_registration = None

    entries_by_type: Dict[str, List[ScheduleEntry]] = {}
    for entry in SCHEDULE_ENTRIES.values():
        entries_by_type.setdefault(entry.aircraft_type, []).append(entry)

    for type_key, entries in entries_by_type.items():
        info = get_aircraft_info(type_key)
        if info is None:
            continue

        aircraft_states = _build_aircraft_states(type_key)
        if not aircraft_states:
            SCHEDULE_ERRORS.append(
                f"No aircraft of type {info['label']} available in the fleet for scheduled missions."
            )
            continue

        for entry in sorted(entries, key=lambda e: e.departure_time):
            required_pax = _mission_required_pax(entry.mission_type)
            candidate_states: List[AircraftState] = []

            if entry.requested_registration:
                requested = aircraft_states.get(entry.requested_registration)
                if requested is None:
                    SCHEDULE_ERRORS.append(
                        f"Requested registration {entry.requested_registration} is not available for {info['label']}."
                    )
                    continue
                if requested.max_pax < required_pax:
                    SCHEDULE_ERRORS.append(
                        f"Requested aircraft {entry.requested_registration} cannot carry {required_pax} passengers."
                    )
                    continue
                candidate_states = [requested]
            else:
                candidate_states = [state for state in aircraft_states.values() if state.max_pax >= required_pax]
                if not candidate_states:
                    SCHEDULE_ERRORS.append(
                        f"No aircraft with enough seats for {required_pax} passengers in type {info['label']}."
                    )
                    continue

            chosen_state: Optional[AircraftState] = None
            chosen_window: Optional[Tuple[datetime, datetime, float]] = None
            best_metric = None

            for state in candidate_states:
                window = _evaluate_candidate(state, entry, info)
                if window is None:
                    continue
                metric = state.accumulated_ferry + window[2]
                if best_metric is None or metric < best_metric:
                    best_metric = metric
                    chosen_state = state
                    chosen_window = window

            if chosen_state is None or chosen_window is None:
                SCHEDULE_ERRORS.append(
                    f"No feasible assignment for mission departing {entry.departure_airport} at {_format_datetime_display(entry.departure_time)}."
                )
                continue

            reposition_start, reposition_end, reposition_hours = chosen_window
            if reposition_hours > 0:
                auto_id = next(AUTO_ID_COUNTER)
                auto_entry = ScheduleEntry(
                    entry_id=auto_id,
                    departure_airport=chosen_state.location or "",
                    arrival_airport=entry.departure_airport,
                    departure_time=reposition_start,
                    aircraft_type=entry.aircraft_type,
                    mission_type="Auto Ferry",
                    pic="-",
                    sic="-",
                    ca1="-",
                    requested_registration=chosen_state.registration,
                    assigned_registration=chosen_state.registration,
                    auto_generated=True,
                    notes=f"Reposition {_airport_display(chosen_state.location)} → {_airport_display(entry.departure_airport)}",
                )
                auto_entry.arrival_time = reposition_end
                hours = int(reposition_hours)
                minutes = int((reposition_hours - hours) * 60)
                auto_entry.flight_time_hours = reposition_hours
                auto_entry.flight_time_text = f"{hours}h {minutes}m"
                auto_segments.append(auto_entry)

            entry.assigned_registration = chosen_state.registration
            chosen_state.location = entry.arrival_airport
            chosen_state.available_from = entry.arrival_time + GROUND_BUFFER
            chosen_state.accumulated_ferry = (chosen_state.accumulated_ferry + reposition_hours)

    return auto_segments


def recalculate_schedule() -> None:
    global AUTO_SEGMENTS, SCHEDULE_ERRORS
    AUTO_SEGMENTS = []
    SCHEDULE_ERRORS = []
    for entry in SCHEDULE_ENTRIES.values():
        entry.assigned_registration = None
        try:
            _refresh_entry_metrics(entry)
        except ValueError as exc:
            SCHEDULE_ERRORS.append(str(exc))
    if SCHEDULE_ERRORS:
        return
    AUTO_SEGMENTS = _optimise_assignments()


def _entry_to_json(entry: ScheduleEntry) -> Dict[str, object]:
    data = {
        "entry_id": entry.entry_id,
        "auto_generated": entry.auto_generated,
        "aircraft_type": entry.aircraft_type,
        "aircraft_label": _AIRCRAFT_INFO.get(entry.aircraft_type, {}).get("label", entry.aircraft_type),
        "mission_type": entry.mission_type,
        "departure_airport": entry.departure_airport,
        "departure_airport_display": _airport_display(entry.departure_airport),
        "arrival_airport": entry.arrival_airport,
        "arrival_airport_display": _airport_display(entry.arrival_airport),
        "departure_time_value": _format_datetime_local(entry.departure_time),
        "departure_time_display": _format_datetime_display(entry.departure_time),
        "arrival_time_display": _format_datetime_display(entry.arrival_time) if entry.arrival_time else "",
        "flight_time": entry.flight_time_text,
        "requested_registration": entry.requested_registration or "",
        "assigned_registration": entry.assigned_registration or "",
        "pic": entry.pic,
        "sic": entry.sic,
        "ca1": entry.ca1,
        "notes": entry.notes or "",
        "editable": not entry.auto_generated,
    }
    if entry.auto_generated:
        data["entry_id"] = f"auto-{entry.entry_id}"
    return data


def build_state_payload() -> Dict[str, object]:
    timeline = list(SCHEDULE_ENTRIES.values()) + AUTO_SEGMENTS
    timeline.sort(key=lambda e: (e.departure_time, e.auto_generated))

    fleet_payload = [
        {
            "registration": aircraft.registration,
            "type_key": aircraft.type_key,
            "type_label": aircraft.info["label"],
            "max_pax": aircraft.max_pax,
        }
        for aircraft in sorted(FLEET.values(), key=lambda a: a.registration)
    ]

    return {
        "fleet": fleet_payload,
        "schedule": [_entry_to_json(entry) for entry in timeline],
        "errors": SCHEDULE_ERRORS,
    }


@app.route("/api/state")
def api_state():
    return jsonify(build_state_payload())


@app.route("/api/aircraft", methods=["POST"])
def api_add_aircraft():
    payload = request.get_json(force=True)
    registration = payload.get("registration", "").strip().upper()
    type_key = payload.get("type_key")
    max_pax = int(payload.get("max_pax", 0))

    if not registration:
        return jsonify({"error": "Registration is required."}), 400
    if registration in FLEET:
        return jsonify({"error": "Aircraft already exists in the fleet."}), 400
    info = get_aircraft_info(type_key)
    if info is None:
        return jsonify({"error": "Unsupported aircraft type."}), 400
    if max_pax <= 0:
        return jsonify({"error": "Maximum passengers must be greater than zero."}), 400

    FLEET[registration] = FleetAircraft(registration=registration, type_key=type_key, max_pax=max_pax)
    recalculate_schedule()
    return jsonify(build_state_payload())


@app.route("/api/aircraft/<registration>", methods=["DELETE"])
def api_remove_aircraft(registration: str):
    registration = registration.upper()
    if registration in FLEET:
        del FLEET[registration]
        recalculate_schedule()
    return jsonify(build_state_payload())


def _update_entry_from_payload(entry: ScheduleEntry, payload: Dict[str, object]) -> None:
    if "departure_airport" in payload:
        entry.departure_airport = str(payload["departure_airport"]).upper()
    if "arrival_airport" in payload:
        entry.arrival_airport = str(payload["arrival_airport"]).upper()
    if "departure_time" in payload:
        entry.departure_time = _parse_datetime_local(payload["departure_time"])
    if "aircraft_type" in payload:
        entry.aircraft_type = str(payload["aircraft_type"])
    if "mission_type" in payload:
        entry.mission_type = str(payload["mission_type"])
    if "pic" in payload:
        entry.pic = str(payload["pic"])
    if "sic" in payload:
        entry.sic = str(payload["sic"])
    if "ca1" in payload:
        entry.ca1 = str(payload["ca1"])
    if "requested_registration" in payload:
        requested = str(payload["requested_registration"]).strip().upper()
        entry.requested_registration = requested or None


def _create_schedule_entry(payload: Dict[str, object]) -> ScheduleEntry:
    required_fields = ("departure_airport", "arrival_airport", "departure_time", "aircraft_type", "mission_type")
    for field in required_fields:
        if field not in payload:
            raise ValueError(f"Missing required field {field}.")

    entry = ScheduleEntry(
        entry_id=next(ENTRY_ID_COUNTER),
        departure_airport=str(payload["departure_airport"]).upper(),
        arrival_airport=str(payload["arrival_airport"]).upper(),
        departure_time=_parse_datetime_local(payload["departure_time"]),
        aircraft_type=str(payload["aircraft_type"]),
        mission_type=str(payload["mission_type"]),
        pic=str(payload.get("pic", PIC_OPTIONS[0])),
        sic=str(payload.get("sic", SIC_OPTIONS[0])),
        ca1=str(payload.get("ca1", CA1_OPTIONS[0])),
        requested_registration=str(payload.get("requested_registration", "")).strip().upper() or None,
    )
    return entry


@app.route("/api/schedule", methods=["POST"])
def api_add_schedule():
    payload = request.get_json(force=True)
    try:
        entry = _create_schedule_entry(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    SCHEDULE_ENTRIES[entry.entry_id] = entry
    recalculate_schedule()
    return jsonify(build_state_payload())


@app.route("/api/schedule/<int:entry_id>", methods=["PUT"])
def api_update_schedule(entry_id: int):
    entry = SCHEDULE_ENTRIES.get(entry_id)
    if entry is None:
        return jsonify({"error": "Schedule entry not found."}), 404

    payload = request.get_json(force=True)
    try:
        _update_entry_from_payload(entry, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    recalculate_schedule()
    return jsonify(build_state_payload())


@app.route("/api/schedule/<int:entry_id>", methods=["DELETE"])
def api_delete_schedule(entry_id: int):
    if entry_id in SCHEDULE_ENTRIES:
        del SCHEDULE_ENTRIES[entry_id]
        recalculate_schedule()
    return jsonify(build_state_payload())


if __name__ == "__main__":
    app.run()
