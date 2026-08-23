"""Build Garmin workout JSON for swim, bike and run.

Every enum below was read off Ari's own existing workouts
(reference/garmin-schemas/*.json), not from documentation. That matters:
the predecessor mapped swimming to sportTypeId 5, which is strength_training,
so every swim it pushed would have appeared on the watch as a gym session.
Swimming is 4.

Input is a structured dict, never a display string. The predecessor decided
whether "1:36/100m" meant swim pace or run pace by testing whether the text
contained "100", and fell back to a silent 600-second step when it could not
parse a label at all. Here an unparseable input raises.
"""

SPORTS = {
    "run":  {"sportTypeId": 1, "sportTypeKey": "running",  "displayOrder": 1},
    "bike": {"sportTypeId": 2, "sportTypeKey": "cycling",  "displayOrder": 2},
    "swim": {"sportTypeId": 4, "sportTypeKey": "swimming", "displayOrder": 3},
}

STEP_TYPES = {
    "warmup":   {"stepTypeId": 1, "stepTypeKey": "warmup",   "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest":     {"stepTypeId": 5, "stepTypeKey": "rest",     "displayOrder": 5},
    "repeat":   {"stepTypeId": 6, "stepTypeKey": "repeat",   "displayOrder": 6},
    "main":     {"stepTypeId": 8, "stepTypeKey": "main",     "displayOrder": 8},
}

END_CONDITIONS = {
    "lap_button":  {"conditionTypeId": 1, "conditionTypeKey": "lap.button",  "displayOrder": 1, "displayable": True},
    "time_s":      {"conditionTypeId": 2, "conditionTypeKey": "time",        "displayOrder": 2, "displayable": True},
    "distance_m":  {"conditionTypeId": 3, "conditionTypeKey": "distance",    "displayOrder": 3, "displayable": True},
    "iterations":  {"conditionTypeId": 7, "conditionTypeKey": "iterations",  "displayOrder": 7, "displayable": False},
    "fixed_rest_s": {"conditionTypeId": 8, "conditionTypeKey": "fixed.rest", "displayOrder": 8, "displayable": True},
}

NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
POWER_ZONE = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 2}
CADENCE = {"workoutTargetTypeId": 3, "workoutTargetTypeKey": "cadence", "displayOrder": 3}
PACE_ZONE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}

STROKES = {
    "any":    {"strokeTypeId": 1, "strokeTypeKey": "any_stroke", "displayOrder": 1},
    "back":   {"strokeTypeId": 2, "strokeTypeKey": "backstroke", "displayOrder": 2},
    "breast": {"strokeTypeId": 3, "strokeTypeKey": "breaststroke", "displayOrder": 3},
    "drill":  {"strokeTypeId": 4, "strokeTypeKey": "drill", "displayOrder": 4},
    "fly":    {"strokeTypeId": 5, "strokeTypeKey": "butterfly", "displayOrder": 5},
    "free":   {"strokeTypeId": 6, "strokeTypeKey": "free", "displayOrder": 6},
    "mixed":  {"strokeTypeId": 7, "strokeTypeKey": "individual_medley", "displayOrder": 7},
}
NO_STROKE = {"strokeTypeId": 0, "strokeTypeKey": None, "displayOrder": 0}
NO_EQUIPMENT = {"equipmentTypeId": 0, "equipmentTypeKey": None, "displayOrder": 0}
METER = {"unitId": 1, "unitKey": "meter", "factor": 100.0}

# Swimming names its work and rest steps differently from running and cycling.
# Ari's own pool workouts use main/rest; his runs use interval/recovery.
_DEFAULT_WORK = {"swim": "main", "bike": "interval", "run": "interval"}
_DEFAULT_REST = {"swim": "rest", "bike": "recovery", "run": "recovery"}


class WorkoutError(ValueError):
    """Raised when input cannot be turned into a valid Garmin workout."""


def _mps_from_pace(seconds, per_metres, where):
    if not seconds or seconds <= 0:
        raise WorkoutError(f"{where}: pace must be a positive number of seconds, got {seconds!r}")
    return round(per_metres / float(seconds), 6)


def _target(step, sport, where):
    """Convert a structured target into Garmin's target fields.

    Pace reaches the watch as metres per second, so the unit the athlete meant
    has to be stated rather than guessed: 1:36 means one thing per 100m and a
    very different thing per kilometre.
    """
    t = step.get("target")
    if not t or t.get("type") in (None, "none"):
        return NO_TARGET, None, None
    kind = t.get("type")
    lo, hi = t.get("low"), t.get("high")
    if lo is None:
        raise WorkoutError(f"{where}: target {kind!r} needs 'low'")
    if hi is None:
        hi = lo

    if kind == "power_w":
        if sport != "bike":
            raise WorkoutError(f"{where}: power targets are only valid for bike, not {sport}")
        return POWER_ZONE, float(lo), float(hi)
    if kind == "hr_bpm":
        return HR_ZONE, float(lo), float(hi)
    if kind == "pace_per_km":
        if sport == "swim":
            raise WorkoutError(f"{where}: swim pace must be pace_per_100m, not pace_per_km")
        fast, slow = _mps_from_pace(lo, 1000.0, where), _mps_from_pace(hi, 1000.0, where)
        return PACE_ZONE, min(fast, slow), max(fast, slow)
    if kind == "pace_per_100m":
        if sport != "swim":
            raise WorkoutError(f"{where}: pace_per_100m is only valid for swim, not {sport}")
        fast, slow = _mps_from_pace(lo, 100.0, where), _mps_from_pace(hi, 100.0, where)
        return PACE_ZONE, min(fast, slow), max(fast, slow)
    raise WorkoutError(
        f"{where}: unknown target type {kind!r}. "
        "Use none | pace_per_km | pace_per_100m | power_w | hr_bpm"
    )


def _end(step, where):
    e = step.get("end")
    if not e:
        raise WorkoutError(f"{where}: every step needs an 'end' "
                           "({'type': 'distance_m'|'time_s'|'lap_button'|'fixed_rest_s', 'value': N})")
    kind = e.get("type")
    if kind not in END_CONDITIONS or kind == "iterations":
        raise WorkoutError(
            f"{where}: unknown end type {kind!r}. "
            "Use distance_m | time_s | lap_button | fixed_rest_s"
        )
    value = e.get("value")
    if kind == "lap_button":
        return END_CONDITIONS[kind], float(value or 0)
    if value is None or float(value) <= 0:
        raise WorkoutError(f"{where}: end {kind!r} needs a positive 'value', got {value!r}")
    return END_CONDITIONS[kind], float(value)


class _Order:
    """Garmin numbers parents and children in one flat sequence."""

    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def _executable(step, sport, order, where):
    kind = step.get("kind")
    if kind in ("work", None):
        kind = _DEFAULT_WORK[sport]
    elif kind == "rest":
        kind = _DEFAULT_REST[sport]
    if kind not in STEP_TYPES or kind == "repeat":
        raise WorkoutError(
            f"{where}: unknown step kind {step.get('kind')!r}. "
            "Use warmup | work | rest | cooldown | repeat"
        )

    cond, value = _end(step, where)
    ttype, v1, v2 = _target(step, sport, where)

    out = {
        "type": "ExecutableStepDTO",
        "stepId": None,
        "stepOrder": order,
        "stepType": STEP_TYPES[kind],
        "childStepId": None,
        "description": step.get("note"),
        "endCondition": cond,
        "endConditionValue": value,
        "preferredEndConditionUnit": METER if cond["conditionTypeKey"] == "distance" else None,
        "targetType": ttype,
        "targetValueOne": v1,
        "targetValueTwo": v2,
        "zoneNumber": None,
        "strokeType": NO_STROKE,
        "equipmentType": NO_EQUIPMENT,
        "secondaryTargetType": None,
        "secondaryTargetValueOne": None,
        "secondaryTargetValueTwo": None,
    }

    if sport == "swim" and step.get("stroke"):
        s = step["stroke"]
        if s not in STROKES:
            raise WorkoutError(f"{where}: unknown stroke {s!r}. Use {' | '.join(STROKES)}")
        out["strokeType"] = STROKES[s]

    # Ari's own bike workouts pair a power target with a cadence target, so
    # cadence is exposed as the secondary rather than dropped.
    cad = step.get("cadence")
    if cad:
        if sport != "bike":
            raise WorkoutError(f"{where}: cadence targets are only wired for bike, not {sport}")
        out["secondaryTargetType"] = CADENCE
        out["secondaryTargetValueOne"] = float(cad["low"])
        out["secondaryTargetValueTwo"] = float(cad.get("high", cad["low"]))
    return out


def _build_steps(steps, sport, order, path="steps"):
    built = []
    for i, step in enumerate(steps):
        where = f"{path}[{i}]"
        if step.get("kind") == "repeat":
            times = step.get("times")
            if not times or int(times) < 1:
                raise WorkoutError(f"{where}: repeat needs 'times' >= 1, got {times!r}")
            if not step.get("steps"):
                raise WorkoutError(f"{where}: repeat needs a non-empty 'steps' list")
            group_order = order.next()
            children = _build_steps(step["steps"], sport, order, f"{where}.steps")
            built.append({
                "type": "RepeatGroupDTO",
                "stepId": None,
                "stepOrder": group_order,
                "stepType": STEP_TYPES["repeat"],
                "childStepId": 1,
                "numberOfIterations": int(times),
                "smartRepeat": False,
                "endCondition": END_CONDITIONS["iterations"],
                "endConditionValue": float(times),
                "workoutSteps": children,
            })
        else:
            built.append(_executable(step, sport, order.next(), where))
    return built


def estimate_seconds(steps):
    """Rough duration, used only for Garmin's display estimate."""
    total = 0.0
    for s in steps:
        if s.get("kind") == "repeat":
            total += int(s.get("times", 1)) * estimate_seconds(s.get("steps") or [])
            continue
        e = s.get("end") or {}
        if e.get("type") in ("time_s", "fixed_rest_s"):
            total += float(e.get("value") or 0)
        elif e.get("type") == "distance_m":
            t = s.get("target") or {}
            if t.get("type") == "pace_per_100m":
                total += float(e["value"]) / 100.0 * float(t.get("low") or 100)
            elif t.get("type") == "pace_per_km":
                total += float(e["value"]) / 1000.0 * float(t.get("low") or 330)
            else:
                total += float(e["value"]) / 1000.0 * 300  # ~5:00/km fallback
        else:
            total += 300
    return round(total)


def build(workout):
    """Turn a structured workout dict into Garmin's workout payload."""
    sport = workout.get("sport")
    if sport not in SPORTS:
        raise WorkoutError(f"sport must be one of {' | '.join(SPORTS)}, got {sport!r}")
    name = (workout.get("name") or "").strip()
    if not name:
        raise WorkoutError("workout needs a 'name'")
    steps = workout.get("steps")
    if not steps:
        raise WorkoutError("workout needs a non-empty 'steps' list")

    built = _build_steps(steps, sport, _Order())
    sport_type = SPORTS[sport]

    payload = {
        "workoutName": name[:80],
        "description": (workout.get("description") or None),
        "sportType": sport_type,
        "subSportType": None,
        "estimatedDurationInSecs": estimate_seconds(steps),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport_type,
            "workoutSteps": built,
        }],
    }

    if sport == "swim":
        # Without a pool length Garmin cannot convert distance steps into
        # lengths, and the workout lands on the watch unusable.
        pool = workout.get("pool_length_m", 25)
        if not pool or float(pool) <= 0:
            raise WorkoutError("swim workouts need 'pool_length_m' (e.g. 25 or 50)")
        payload["poolLength"] = float(pool)
        payload["poolLengthUnit"] = METER
        payload["estimateType"] = "TIME_ESTIMATED"
    return payload
