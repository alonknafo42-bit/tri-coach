"""Race splits, distance normalisation, and what each leg is worth.

This module exists because of a mistake. Two triathlons four weeks apart read
1:16:18 then 1:13:34, and the obvious conclusion -- nearly three minutes faster
-- was wrong. The second bike course was 1.38 km shorter, which at 34 km/h is
most of that gap. Normalised to speed the bike had moved +0.26 km/h and the
swim one second per 100 m. Almost nothing had improved.

So every cross-course comparison here goes through normalise(), and the raw
elapsed difference is never reported on its own. The rule lives in code rather
than in someone's head precisely because the raw number is so persuasive.
"""

from dataclasses import dataclass, asdict

SWIM, T1, BIKE, T2, RUN = "swim", "t1", "bike", "t2", "run"
LEG_ORDER = (SWIM, T1, BIKE, T2, RUN)
LEG_HE = {SWIM: "שחייה", T1: "מעבר 1", BIKE: "אופניים", T2: "מעבר 2", RUN: "ריצה"}
LEG_ICON = {SWIM: "🏊", T1: "🔄", BIKE: "🚴", T2: "🔄", RUN: "🏃"}

# Distances a leg is measured in. Transitions are timed, not raced: their
# length is a property of the venue's car park, so normalising them by
# distance would compare two unrelated things.
PACED = {SWIM: "per_100m", BIKE: "kmh", RUN: "per_km"}

COURSES = {
    "sprint": {"name": "ספרינט תקני", SWIM: 750.0, BIKE: 20000.0, RUN: 5000.0},
    "olympic": {"name": "אולימפי", SWIM: 1500.0, BIKE: 40000.0, RUN: 10000.0},
    "70.3": {"name": "חצי איש ברזל", SWIM: 1900.0, BIKE: 90000.0, RUN: 21100.0},
}


def mmss(seconds):
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}" if h else f"{sign}{m}:{s:02d}"


@dataclass
class Leg:
    leg: str
    seconds: float
    metres: float = 0.0

    @property
    def speed_kmh(self):
        if not self.metres or not self.seconds:
            return None
        return (self.metres / 1000.0) / (self.seconds / 3600.0)

    @property
    def per_km(self):
        if not self.metres or not self.seconds:
            return None
        return self.seconds / (self.metres / 1000.0)

    @property
    def per_100m(self):
        if not self.metres or not self.seconds:
            return None
        return self.seconds / (self.metres / 100.0)

    def rate(self):
        """The comparable number for this leg, unit-tagged."""
        kind = PACED.get(self.leg)
        if kind == "kmh":
            return {"kind": kind, "value": self.speed_kmh, "text":
                    f"{self.speed_kmh:.1f} קמ\"ש" if self.speed_kmh else "—"}
        if kind == "per_km":
            return {"kind": kind, "value": self.per_km, "text":
                    f"{mmss(self.per_km)}/ק\"מ" if self.per_km else "—"}
        if kind == "per_100m":
            return {"kind": kind, "value": self.per_100m, "text":
                    f"{mmss(self.per_100m)}/100מ'" if self.per_100m else "—"}
        return {"kind": "time", "value": self.seconds, "text": mmss(self.seconds)}


@dataclass
class Race:
    date: str
    name: str
    legs: list

    @property
    def total_seconds(self):
        return sum(l.seconds for l in self.legs)

    def leg(self, which):
        for l in self.legs:
            if l.leg == which:
                return l
        return None


def from_splits(date, name, lap_dtos):
    """Build a Race from Garmin's five multisport laps, in order."""
    legs = []
    for i, lap in enumerate(lap_dtos[:5]):
        legs.append(Leg(leg=LEG_ORDER[i],
                        seconds=float(lap.get("duration") or 0),
                        metres=float(lap.get("distance") or 0)))
    return Race(date=date, name=name, legs=legs)


def normalise(race, course="sprint"):
    """Project a race onto a reference course, holding his speeds constant.

    This is the whole point of the module: it answers "what would this
    performance have been on the course he is actually about to race", which
    is the only comparison that means anything across two different venues.
    Transitions carry over as elapsed time, because they are not raced.
    """
    ref = COURSES[course]
    out, notes = [], []
    for leg in race.legs:
        if leg.leg in (T1, T2):
            out.append(Leg(leg.leg, leg.seconds, leg.metres))
            continue
        target_m = ref[leg.leg]
        if not leg.metres or not leg.seconds:
            out.append(Leg(leg.leg, leg.seconds, target_m))
            continue
        scaled = leg.seconds * (target_m / leg.metres)
        if abs(leg.metres - target_m) / target_m > 0.02:
            notes.append(
                f"{LEG_HE[leg.leg]}: המסלול היה {leg.metres/1000:.2f} ק\"מ "
                f"מול {target_m/1000:.2f} תקני — הזמן הותאם "
                f"({mmss(leg.seconds)} → {mmss(scaled)})")
        out.append(Leg(leg.leg, scaled, target_m))
    return Race(date=race.date, name=race.name, legs=out), notes


def compare(older, newer, course="sprint"):
    """Compare two races honestly: by speed, not by elapsed time.

    Reports the raw delta too, but always beside the normalised one and
    labelled as course-affected, so the misleading number can never travel
    on its own.
    """
    na, _ = normalise(older, course)
    nb, _ = normalise(newer, course)
    rows = []
    for name in LEG_ORDER:
        a, b = older.leg(name), newer.leg(name)
        if not a or not b:
            continue
        ra, rb = a.rate(), b.rate()
        row = {
            "leg": name, "he": LEG_HE[name], "icon": LEG_ICON[name],
            "raw_before": a.seconds, "raw_after": b.seconds,
            "raw_delta": b.seconds - a.seconds,
            "metres_before": a.metres, "metres_after": b.metres,
            "rate_before": ra["text"], "rate_after": rb["text"],
            "same_course": abs(a.metres - b.metres) <= max(20.0, 0.02 * max(a.metres, 1)),
            "normalised_delta": (nb.leg(name).seconds - na.leg(name).seconds),
        }
        # Signed improvement in the leg's own unit, sign fixed so that
        # "better" is always negative regardless of whether the unit counts
        # up (km/h) or down (sec/km).
        if ra["value"] is not None and rb["value"] is not None:
            row["rate_delta"] = rb["value"] - ra["value"]
            row["rate_better"] = (row["rate_delta"] > 0) if ra["kind"] == "kmh" \
                else (row["rate_delta"] < 0)
        rows.append(row)
    return {
        "raw_total_delta": newer.total_seconds - older.total_seconds,
        "normalised_total_delta": nb.total_seconds - na.total_seconds,
        "course": course,
        "legs": rows,
        "warning": (
            "הפרש הזמן הגולמי מושפע מהבדלי מסלול. "
            "ההשוואה שקובעת היא המנורמלת."
        ),
    }


def target_splits(pb, goal_seconds, course="sprint", improvable=None):
    """Distribute the time he has to find across the legs that can give it.

    Not spread evenly. Transitions are the cheapest seconds in triathlon --
    Ari's T1 was 4:39, which is 6.3% of a sprint against a well-drilled
    1:30-2:00 -- so they are taken first, and only the remainder is asked of
    the disciplines, in proportion to how much of the race each one occupies.
    """
    base, notes = normalise(pb, course)
    needed = base.total_seconds - goal_seconds
    improvable = improvable or {T1: 120.0, T2: 75.0}

    targets, freed = {}, 0.0
    for name, floor in improvable.items():
        leg = base.leg(name)
        if leg and leg.seconds > floor:
            gain = leg.seconds - floor
            targets[name] = floor
            freed += gain

    remaining = max(0.0, needed - freed)
    disciplines = [l for l in base.legs if l.leg in PACED]
    total_disc = sum(l.seconds for l in disciplines) or 1.0
    for leg in disciplines:
        share = leg.seconds / total_disc
        targets[leg.leg] = leg.seconds - remaining * share

    rows = []
    for name in LEG_ORDER:
        cur = base.leg(name)
        if not cur:
            continue
        tgt = Leg(name, targets.get(name, cur.seconds), cur.metres)
        rows.append({
            "leg": name, "he": LEG_HE[name], "icon": LEG_ICON[name],
            "pb_seconds": cur.seconds, "pb_text": mmss(cur.seconds),
            "pb_rate": cur.rate()["text"],
            "target_seconds": tgt.seconds, "target_text": mmss(tgt.seconds),
            "target_rate": tgt.rate()["text"],
            "gap_seconds": cur.seconds - tgt.seconds,
            "gap_text": mmss(cur.seconds - tgt.seconds),
            "share_pct": round(100 * cur.seconds / base.total_seconds, 1),
            "free": name in (T1, T2),
        })
    return {
        "course": COURSES[course]["name"],
        "normalised_pb_seconds": base.total_seconds,
        "normalised_pb_text": mmss(base.total_seconds),
        "goal_seconds": goal_seconds, "goal_text": mmss(goal_seconds),
        "needed_seconds": needed, "needed_text": mmss(abs(needed)),
        "already_achievable": needed <= 0,
        "freed_by_transitions": freed,
        "notes": notes, "legs": rows,
    }


def sensitivity(pb, course="sprint"):
    """What one unit of improvement in each leg is worth, in seconds.

    Turns "work on the bike" into "+1 km/h on the bike is 60 seconds", which
    is the difference between advice and a decision.
    """
    base, _ = normalise(pb, course)
    out = []
    for leg in base.legs:
        if leg.leg not in PACED:
            continue
        r = leg.rate()
        if r["value"] is None:
            continue
        if r["kind"] == "kmh":
            faster = Leg(leg.leg, 0, leg.metres)
            faster.seconds = (leg.metres / 1000.0) / ((r["value"] + 1.0) / 3600.0)
            out.append({"leg": leg.leg, "he": LEG_HE[leg.leg], "icon": LEG_ICON[leg.leg],
                        "step": "+1 קמ\"ש", "seconds": leg.seconds - faster.seconds})
        elif r["kind"] == "per_km":
            out.append({"leg": leg.leg, "he": LEG_HE[leg.leg], "icon": LEG_ICON[leg.leg],
                        "step": "‎-10 שנ'/ק\"מ", "seconds": 10.0 * leg.metres / 1000.0})
        elif r["kind"] == "per_100m":
            out.append({"leg": leg.leg, "he": LEG_HE[leg.leg], "icon": LEG_ICON[leg.leg],
                        "step": "‎-5 שנ'/100מ'", "seconds": 5.0 * leg.metres / 100.0})
    out.sort(key=lambda x: -x["seconds"])
    return out


def as_dict(race):
    return {"date": race.date, "name": race.name,
            "total_seconds": race.total_seconds,
            "total_text": mmss(race.total_seconds),
            "legs": [{**asdict(l), "he": LEG_HE[l.leg], "icon": LEG_ICON[l.leg],
                      "text": mmss(l.seconds), "rate": l.rate()["text"]}
                     for l in race.legs]}
