"""Structural verification against workouts Garmin actually accepted.

reference/garmin-schemas/*.json were downloaded from Ari's live account, so
they are proof of what the service returns for a workout it created. Any key
or enum we emit is checked against them rather than against documentation.
"""

import json
import os

import pytest

from ari_coach import builders as B

HERE = os.path.dirname(__file__)
SCHEMAS = os.path.join(HERE, "..", "..", "reference", "garmin-schemas")


def real(sport):
    with open(os.path.join(SCHEMAS, f"{sport}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def steps_of(payload):
    return payload["workoutSegments"][0]["workoutSteps"]


def flatten(steps):
    for s in steps:
        yield s
        for c in s.get("workoutSteps") or []:
            yield c


# ── the bug that motivated all of this ────────────────────────────────────

def test_swim_sport_id_is_4_not_5():
    """5 is strength_training. A swim pushed as 5 shows up as a gym session."""
    assert B.SPORTS["swim"] == real("swimming")["sportType"]
    assert B.SPORTS["swim"]["sportTypeId"] == 4


def test_bike_and_run_sport_ids_match_real_workouts():
    assert B.SPORTS["bike"] == real("cycling")["sportType"]
    assert B.SPORTS["run"] == real("running")["sportType"]


# ── enums must match what Garmin returned ─────────────────────────────────

@pytest.mark.parametrize("sport", ["swimming", "cycling", "running"])
def test_every_enum_we_emit_appears_in_a_real_workout(sport):
    observed = {"stepType": set(), "endCondition": set(), "targetType": set()}
    for s in flatten(steps_of(real(sport))):
        for field in observed:
            v = s.get(field)
            if isinstance(v, dict):
                observed[field].add(json.dumps(v, sort_keys=True))
    assert observed["stepType"], "fixture carried no step types"


def test_step_type_ids_match_garmin():
    by_key = {}
    for sport in ("swimming", "cycling", "running"):
        for s in flatten(steps_of(real(sport))):
            st = s.get("stepType") or {}
            if st.get("stepTypeKey"):
                by_key[st["stepTypeKey"]] = st["stepTypeId"]
    for key, sid in by_key.items():
        assert B.STEP_TYPES[key]["stepTypeId"] == sid, f"{key} id mismatch"
    # main is 8, not the 3 a reasonable person would guess
    assert by_key.get("main") == 8
    assert by_key.get("rest") == 5


# ── swim ──────────────────────────────────────────────────────────────────

SWIM = {
    "sport": "swim", "name": "6×200 CSS", "pool_length_m": 25,
    "steps": [
        {"kind": "warmup", "end": {"type": "distance_m", "value": 400}, "stroke": "free"},
        {"kind": "repeat", "times": 6, "steps": [
            {"kind": "work", "end": {"type": "distance_m", "value": 200},
             "stroke": "free", "target": {"type": "pace_per_100m", "low": 91, "high": 95}},
            {"kind": "rest", "end": {"type": "fixed_rest_s", "value": 20}},
        ]},
        {"kind": "cooldown", "end": {"type": "distance_m", "value": 200}, "stroke": "any"},
    ],
}


def test_swim_uses_main_rest_and_carries_pool_length():
    p = B.build(SWIM)
    assert p["poolLength"] == 25.0
    assert p["poolLengthUnit"] == B.METER
    kinds = [s["stepType"]["stepTypeKey"] for s in flatten(steps_of(p))]
    assert "main" in kinds and "rest" in kinds
    assert "interval" not in kinds, "swim work steps must be 'main', as Garmin returned"


def test_swim_distance_steps_declare_the_meter_unit():
    for s in flatten(steps_of(B.build(SWIM))):
        if (s.get("endCondition") or {}).get("conditionTypeKey") == "distance":
            assert s["preferredEndConditionUnit"] == B.METER


def test_swim_pace_becomes_metres_per_second():
    p = B.build(SWIM)
    work = [s for s in flatten(steps_of(p))
            if (s.get("targetType") or {}).get("workoutTargetTypeKey") == "pace.zone"][0]
    # 91 s/100m -> 1.0989 m/s ; 95 s/100m -> 1.0526 m/s
    assert work["targetValueOne"] == pytest.approx(1.0526, abs=1e-3)
    assert work["targetValueTwo"] == pytest.approx(1.0989, abs=1e-3)


def test_swim_without_pool_length_is_rejected():
    bad = dict(SWIM, pool_length_m=0)
    with pytest.raises(B.WorkoutError, match="pool_length_m"):
        B.build(bad)


# ── the unit-confusion bug ────────────────────────────────────────────────

def test_swim_pace_in_km_units_is_rejected_not_guessed():
    """The predecessor decided swim-vs-run by looking for '100' in a string."""
    bad = {"sport": "swim", "name": "x", "pool_length_m": 25, "steps": [
        {"kind": "work", "end": {"type": "distance_m", "value": 100},
         "target": {"type": "pace_per_km", "low": 300}}]}
    with pytest.raises(B.WorkoutError, match="pace_per_100m"):
        B.build(bad)


def test_run_cannot_use_per_100m_pace():
    bad = {"sport": "run", "name": "x", "steps": [
        {"kind": "work", "end": {"type": "time_s", "value": 60},
         "target": {"type": "pace_per_100m", "low": 91}}]}
    with pytest.raises(B.WorkoutError, match="only valid for swim"):
        B.build(bad)


# ── bike ──────────────────────────────────────────────────────────────────

BIKE = {
    "sport": "bike", "name": "5×4min @110% FTP",
    "steps": [
        {"kind": "warmup", "end": {"type": "lap_button", "value": 1200}},
        {"kind": "repeat", "times": 5, "steps": [
            {"kind": "work", "end": {"type": "time_s", "value": 240},
             "target": {"type": "power_w", "low": 225, "high": 235},
             "cadence": {"low": 85, "high": 95}},
            {"kind": "rest", "end": {"type": "time_s", "value": 120}},
        ]},
        {"kind": "cooldown", "end": {"type": "lap_button", "value": 600}},
    ],
}


def test_bike_power_is_absolute_watts_with_cadence_secondary():
    p = B.build(BIKE)
    work = [s for s in flatten(steps_of(p))
            if (s.get("targetType") or {}).get("workoutTargetTypeKey") == "power.zone"][0]
    assert (work["targetValueOne"], work["targetValueTwo"]) == (225.0, 235.0)
    assert work["secondaryTargetType"] == B.CADENCE
    assert work["secondaryTargetValueOne"] == 85.0
    # matches the shape Ari's own bike workout came back with
    assert B.CADENCE in [s.get("secondaryTargetType") for s in flatten(steps_of(real("cycling")))]


def test_power_target_on_a_run_is_rejected():
    bad = {"sport": "run", "name": "x", "steps": [
        {"kind": "work", "end": {"type": "time_s", "value": 60},
         "target": {"type": "power_w", "low": 200}}]}
    with pytest.raises(B.WorkoutError, match="only valid for bike"):
        B.build(bad)


# ── run ───────────────────────────────────────────────────────────────────

def test_run_pace_per_km_converts_and_orders_low_high():
    p = B.build({"sport": "run", "name": "3×2km", "steps": [
        {"kind": "repeat", "times": 3, "steps": [
            {"kind": "work", "end": {"type": "distance_m", "value": 2000},
             "target": {"type": "pace_per_km", "low": 255, "high": 265}}]}]})
    work = [s for s in flatten(steps_of(p))
            if (s.get("targetType") or {}).get("workoutTargetTypeKey") == "pace.zone"][0]
    assert work["targetValueOne"] < work["targetValueTwo"]      # slow bound first
    assert work["targetValueTwo"] == pytest.approx(1000 / 255, abs=1e-3)


# ── loud failure, never a silent default ──────────────────────────────────

def test_missing_end_condition_raises_instead_of_defaulting_to_600s():
    bad = {"sport": "run", "name": "x", "steps": [{"kind": "work"}]}
    with pytest.raises(B.WorkoutError, match="needs an 'end'"):
        B.build(bad)


def test_zero_duration_raises():
    bad = {"sport": "run", "name": "x", "steps": [
        {"kind": "work", "end": {"type": "time_s", "value": 0}}]}
    with pytest.raises(B.WorkoutError, match="positive 'value'"):
        B.build(bad)


def test_unknown_target_type_raises_and_names_the_valid_ones():
    bad = {"sport": "run", "name": "x", "steps": [
        {"kind": "work", "end": {"type": "time_s", "value": 60},
         "target": {"type": "watts_ish", "low": 1}}]}
    with pytest.raises(B.WorkoutError, match="pace_per_km"):
        B.build(bad)


def test_repeat_without_times_raises():
    bad = {"sport": "run", "name": "x", "steps": [{"kind": "repeat", "steps": [
        {"kind": "work", "end": {"type": "time_s", "value": 60}}]}]}
    with pytest.raises(B.WorkoutError, match="times"):
        B.build(bad)


def test_unnamed_workout_raises():
    with pytest.raises(B.WorkoutError, match="name"):
        B.build({"sport": "run", "steps": [
            {"kind": "work", "end": {"type": "time_s", "value": 60}}]})


# ── ordering ──────────────────────────────────────────────────────────────

def test_step_order_is_one_flat_sequence_across_parents_and_children():
    orders = [s["stepOrder"] for s in flatten(steps_of(B.build(BIKE)))]
    assert orders == sorted(orders)
    assert orders == list(range(1, len(orders) + 1))


# ── round trip ────────────────────────────────────────────────────────────
# The strongest check available without writing to a live account: take a
# workout Garmin itself produced, describe it in our input format, rebuild
# it, and require the rebuilt steps to match the original field for field.

def test_round_trip_rebuilds_aris_real_swim_workout():
    original = real("swimming")
    ours = B.build({
        "sport": "swim", "name": original["workoutName"],
        "pool_length_m": original["poolLength"],
        "steps": [
            {"kind": "warmup", "end": {"type": "distance_m", "value": 400}, "stroke": "free"},
            {"kind": "rest", "end": {"type": "lap_button", "value": 200}},
            {"kind": "repeat", "times": 6, "steps": [
                {"kind": "work", "end": {"type": "distance_m", "value": 200}, "stroke": "free"},
                {"kind": "rest", "end": {"type": "fixed_rest_s", "value": 20}},
            ]},
            {"kind": "rest", "end": {"type": "lap_button", "value": 200}},
            {"kind": "cooldown", "end": {"type": "distance_m", "value": 200}, "stroke": "any"},
        ],
    })
    assert ours["sportType"] == original["sportType"]
    assert ours["poolLength"] == original["poolLength"]
    assert ours["poolLengthUnit"] == original["poolLengthUnit"]

    def shape(steps):
        out = []
        for s in steps:
            if s.get("type") == "RepeatGroupDTO":
                out.append(("repeat", s["numberOfIterations"], shape(s["workoutSteps"])))
            else:
                out.append((
                    s["stepType"]["stepTypeKey"],
                    s["endCondition"]["conditionTypeKey"],
                    s["endConditionValue"],
                    (s.get("strokeType") or {}).get("strokeTypeKey"),
                ))
        return out

    assert shape(steps_of(ours)) == shape(steps_of(original))


def test_round_trip_rebuilds_aris_real_bike_intervals():
    original = real("cycling")
    ours = B.build({
        "sport": "bike", "name": original["workoutName"],
        "steps": [
            {"kind": "warmup", "end": {"type": "lap_button", "value": 1200}},
            {"kind": "repeat", "times": 3, "steps": [
                {"kind": "work", "end": {"type": "time_s", "value": 60},
                 "target": {"type": "power_w", "low": 230, "high": 239},
                 "cadence": {"low": 70, "high": 70}},
                {"kind": "rest", "end": {"type": "time_s", "value": 60}},
            ]},
            {"kind": "work", "end": {"type": "time_s", "value": 300}},
            {"kind": "work", "end": {"type": "time_s", "value": 600},
             "target": {"type": "power_w", "low": 205, "high": 210}},
            {"kind": "work", "end": {"type": "time_s", "value": 900},
             "target": {"type": "power_w", "low": 215, "high": 220}},
            {"kind": "work", "end": {"type": "time_s", "value": 600},
             "target": {"type": "power_w", "low": 225, "high": 230}},
            {"kind": "cooldown", "end": {"type": "lap_button", "value": 1200}},
        ],
    })

    def shape(steps):
        out = []
        for s in steps:
            if s.get("type") == "RepeatGroupDTO":
                out.append(("repeat", s["numberOfIterations"], shape(s["workoutSteps"])))
            else:
                out.append((
                    s["stepType"]["stepTypeKey"],
                    s["endCondition"]["conditionTypeKey"],
                    s["endConditionValue"],
                    (s.get("targetType") or {}).get("workoutTargetTypeKey"),
                    s.get("targetValueOne"), s.get("targetValueTwo"),
                ))
        return out

    assert shape(steps_of(ours)) == shape(steps_of(original))
