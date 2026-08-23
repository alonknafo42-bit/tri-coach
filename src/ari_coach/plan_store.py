"""The training plan, and the ownership rules that keep it the athlete's.

The plan belongs to the athlete. The coach proposes, reviews and executes; it
never decides. Three things enforce that here rather than in a prompt:

  1. Every day records who wrote it (`source`), and write_day refuses to let
     the coach overwrite an athlete-authored day unless explicitly told to.
  2. Coach-built plans land in a separate pending slot. Only approve() moves
     them into the plan.
  3. The review path has no write function at all -- advice cannot quietly
     become an edit.

A rule stated in a prompt is a request; a rule with no code path behind it is
a guarantee.
"""

import json
import os
from datetime import date, datetime, timedelta, timezone

ATHLETE = "athlete"
COACH = "coach"
STATUSES = ("planned", "done", "skipped", "missed")


def home():
    p = os.path.expanduser(os.getenv("ARI_COACH_HOME", "~/.ari-coach"))
    os.makedirs(p, exist_ok=True)
    _secure_dir(p)
    return p


def _secure_dir(path):
    """Owner-only on the directory and everything in it.

    The Garmin tokens were already 0600 in a 0700 directory, but the training
    data next to them was world-readable -- his plan, his resting heart rate,
    his sleep scores. Protecting the credential and leaving the health record
    open is not a threat model, it is an oversight.
    """
    try:
        os.chmod(path, 0o700)
        for entry in os.scandir(path):
            if entry.is_file():
                os.chmod(entry.path, 0o600)
    except OSError:
        pass                       # Windows ACLs, read-only mounts: not fatal


def _path(name):
    return os.path.join(home(), name)


def _read(name, default):
    try:
        with open(_path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(name, data):
    tmp = _path(name + ".tmp")
    # Create owner-only from the start: writing then chmod-ing leaves a window
    # in which the file is world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(name))       # atomic; a crash mid-write cannot truncate
    try:
        os.chmod(_path(name), 0o600)
    except OSError:
        pass
    return data


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── profile ───────────────────────────────────────────────────────────────

PROFILE_FIELDS = (
    "athlete", "race", "race_date", "race_date_is_approximate", "distance",
    "goal", "hours_per_week", "available_days", "rest_day",
    "pool_length_m", "swims_open_water", "has_trainer", "has_power_meter",
    "ftp_watts", "css_sec_per_100m", "max_hr", "limiter", "injuries", "notes",
    # Three separate bests, because they are three different facts and
    # conflating them sets the wrong target: what he has run at THIS race,
    # what he has run anywhere, and what he is chasing now.
    "course_pb_seconds", "course_pb_note",
    "overall_pb_seconds", "overall_pb_note",
    "goal_seconds",
)


def get_profile():
    return _read("profile.json", {})


def save_profile(**fields):
    unknown = set(fields) - set(PROFILE_FIELDS)
    if unknown:
        raise ValueError(
            f"unknown profile field(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(PROFILE_FIELDS)}"
        )
    p = get_profile()
    p.update({k: v for k, v in fields.items() if v is not None})
    p["updated_at"] = now()
    return _write("profile.json", p)


def onboarding_complete():
    p = get_profile()
    return bool(p.get("race_date") and p.get("hours_per_week"))


def days_to_race():
    p = get_profile()
    if not p.get("race_date"):
        return None
    try:
        return (date.fromisoformat(p["race_date"]) - date.today()).days
    except (ValueError, TypeError):
        return None


# ── plan ──────────────────────────────────────────────────────────────────

def get_plan():
    return _read("plan.json", {"days": {}, "created_at": None})


def get_day(d):
    return get_plan()["days"].get(d)


def week_of(d=None):
    """Monday-anchored week containing `d`, as seven day records or None."""
    ref = date.fromisoformat(d) if d else date.today()
    monday = ref - timedelta(days=ref.weekday())
    plan = get_plan()["days"]
    return [
        {"date": (monday + timedelta(days=i)).isoformat(),
         **(plan.get((monday + timedelta(days=i)).isoformat()) or {})}
        for i in range(7)
    ]


def write_day(d, entry, source, force=False):
    """Write one day. The coach may not silently overwrite the athlete's work.

    `force` exists so the athlete can explicitly ask the coach to replace a day
    they wrote -- an instruction, not an inference.
    """
    if source not in (ATHLETE, COACH):
        raise ValueError(f"source must be {ATHLETE!r} or {COACH!r}, got {source!r}")
    plan = get_plan()
    existing = plan["days"].get(d)
    if existing and source == COACH and existing.get("source") == ATHLETE and not force:
        raise PermissionError(
            f"{d} was written by the athlete. The coach cannot overwrite it. "
            "Ask him, and pass force=True only if he says so."
        )
    record = dict(existing or {})
    record.update(entry)
    record["date"] = d
    record["source"] = source
    record.setdefault("status", "planned")
    record["updated_at"] = now()
    plan["days"][d] = record
    plan.setdefault("created_at", now())
    _write("plan.json", plan)
    return record


def set_status(d, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    plan = get_plan()
    if d not in plan["days"]:
        raise KeyError(f"no plan entry for {d}")
    plan["days"][d]["status"] = status
    plan["days"][d]["updated_at"] = now()
    _write("plan.json", plan)
    return plan["days"][d]


def move_day(src, dst):
    plan = get_plan()
    if src not in plan["days"]:
        raise KeyError(f"no plan entry for {src}")
    if dst in plan["days"]:
        raise ValueError(f"{dst} already has a session; move or clear it first")
    rec = plan["days"].pop(src)
    rec["date"] = dst
    rec["moved_from"] = src
    rec["updated_at"] = now()
    plan["days"][dst] = rec
    _write("plan.json", plan)
    return rec


def remove_day(d):
    plan = get_plan()
    rec = plan["days"].pop(d, None)
    _write("plan.json", plan)
    return rec


# ── proposals ─────────────────────────────────────────────────────────────
# A coach-built plan is a suggestion until the athlete says otherwise, so it
# is stored somewhere the plan readers never look.

def get_pending():
    return _read("pending.json", {})


def propose(days, summary="", replaces=None):
    """Stage a coach proposal. Writes nothing into the plan."""
    if not days:
        raise ValueError("a proposal needs at least one day")
    return _write("pending.json", {
        "created_at": now(), "summary": summary,
        "days": days, "replaces": replaces or [],
    })


def approve(dates=None, force=False):
    """Move a pending proposal into the plan. The only path that does."""
    pending = get_pending()
    if not pending.get("days"):
        raise ValueError("there is no pending proposal to approve")
    applied, refused = [], []
    for d, entry in pending["days"].items():
        if dates and d not in dates:
            continue
        try:
            write_day(d, {**entry, "approved_at": now()}, COACH, force=force)
            applied.append(d)
        except PermissionError as e:
            refused.append({"date": d, "reason": str(e)})
    if dates:
        remaining = {k: v for k, v in pending["days"].items() if k not in applied}
        _write("pending.json", {**pending, "days": remaining} if remaining else {})
    else:
        _write("pending.json", {})
    return {"applied": applied, "refused": refused}


def reject():
    """Discard the proposal, leaving no trace in the plan."""
    had = bool(get_pending().get("days"))
    _write("pending.json", {})
    return {"discarded": had}


# ── memory ────────────────────────────────────────────────────────────────

def get_memory():
    return _read("memory.json", {"notes": []})


def remember(note, tag=None):
    if not (note or "").strip():
        raise ValueError("note cannot be empty")
    m = get_memory()
    m["notes"].append({"note": note.strip(), "tag": tag, "at": now()})
    return _write("memory.json", m)


def forget(index):
    m = get_memory()
    if not 0 <= index < len(m["notes"]):
        raise IndexError(f"no note at index {index}")
    dropped = m["notes"].pop(index)
    _write("memory.json", m)
    return dropped


# ── coach-authored insight cards ──────────────────────────────────────────
# The engine computes cards from thresholds; these are the ones the coach
# wrote in conversation. They are stored separately and merged for display,
# so an opinion can sit beside a measurement without overwriting it.

def get_insights(include_expired=False):
    cards = _read("insights.json", {"cards": []})["cards"]
    if include_expired:
        return cards
    today = date.today().isoformat()
    return [c for c in cards if not c.get("expires") or c["expires"] >= today]


def write_insight(key, severity, title, evidence, action="", expires=None):
    if severity not in ("critical", "warn", "info", "good"):
        raise ValueError("severity must be critical | warn | info | good")
    if not (title or "").strip() or not (evidence or "").strip():
        raise ValueError("an insight needs both a title and evidence")
    data = _read("insights.json", {"cards": []})
    data["cards"] = [c for c in data["cards"] if c.get("key") != key]
    data["cards"].append({
        "key": key, "severity": severity, "title": title.strip(),
        "evidence": evidence.strip(), "action": (action or "").strip(),
        "author": "coach", "written_at": now(), "expires": expires,
    })
    _write("insights.json", data)
    return data["cards"]


def clear_insight(key):
    data = _read("insights.json", {"cards": []})
    before = len(data["cards"])
    data["cards"] = [c for c in data["cards"] if c.get("key") != key]
    _write("insights.json", data)
    return {"removed": before - len(data["cards"])}
