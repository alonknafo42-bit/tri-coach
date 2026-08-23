"""MCP tools for the plan, the cache and the watch.

Read tools answer from the local cache (0.05ms) rather than from Garmin
(1401ms median). Only the push tools talk to Garmin, because only writes have
to. A coach that fetched everything live would take about seven seconds to
answer one question, which is the complaint this whole thing exists to fix.
"""

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone

from . import builders, cache, plan_store as P

_client = None
_startup_refresh_done = False

# Claude Desktop starts this server every time it launches, which makes
# startup the natural moment to catch up on anything the 06:00 job missed
# because the machine was off. It runs on a thread: a blocking sync would
# add six seconds to his first question, which is the "slow" complaint
# arriving through the back door.
STARTUP_REFRESH_AFTER_HOURS = 6.0
STARTUP_REFRESH_DAYS = 3


def configure(client):
    global _client
    _client = client
    _kick_startup_refresh()


def _kick_startup_refresh():
    global _startup_refresh_done
    if _startup_refresh_done or os.getenv("ARI_NO_STARTUP_REFRESH"):
        return
    _startup_refresh_done = True

    def work():
        try:
            with cache.connect() as c:
                last = cache.sync_status(c).get("last_sync")
            if last:
                then = datetime.fromisoformat(last)
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
                if age < STARTUP_REFRESH_AFTER_HOURS:
                    return
            from . import sync
            sync.sync(_client, days=STARTUP_REFRESH_DAYS, verbose=False)
        except Exception:
            pass          # never let a background refresh break the server

    threading.Thread(target=work, daemon=True).start()


def _garmin():
    if _client is None:
        raise RuntimeError("Garmin client not configured")
    return _client


def _ok(**kw):
    return json.dumps({"ok": True, **kw}, ensure_ascii=False, default=str)


def _err(msg, **kw):
    return json.dumps({"ok": False, "error": str(msg), **kw}, ensure_ascii=False)


SPORT_HE = {"swim": "שחייה", "bike": "אופניים", "run": "ריצה",
            "strength": "כוח", "other": "אחר"}


def _mmss(seconds):
    if not seconds:
        return "—"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _rate(sport, speed_mps):
    """The number that means something for this sport, unit included."""
    if not speed_mps:
        return None
    if sport == "run":
        return f"{_mmss(1000 / speed_mps)}/ק\"מ"
    if sport == "swim":
        return f"{_mmss(100 / speed_mps)}/100מ'"
    if sport == "bike":
        return f"{speed_mps * 3.6:.1f} קמ\"ש"
    return None


def _summary(days=84):
    """Everything the coach needs to reason, from cache, in one read."""
    since = (date.today() - timedelta(days=days)).isoformat()
    with cache.connect() as c:
        weekly = [dict(r) for r in c.execute(
            "SELECT strftime('%Y-%W', date) wk, sport,"
            " ROUND(SUM(duration_s)/3600.0, 2) hours,"
            " ROUND(SUM(distance_m)/1000.0, 1) km, COUNT(*) n"
            " FROM activities WHERE date >= ? GROUP BY wk, sport ORDER BY wk", (since,))]
        recent = [dict(r) for r in c.execute(
            "SELECT date, sport, type, name, distance_m, duration_s, avg_hr,"
            " avg_power, norm_power, avg_speed, training_load"
            " FROM activities WHERE date >= ? ORDER BY date DESC LIMIT 40", (since,))]
        recovery = [dict(r) for r in c.execute(
            "SELECT date, hrv_last, hrv_7d, hrv_status, rhr, sleep_score,"
            " readiness_score, readiness_level, vo2max,"
            " load_acute, load_chronic, load_status"
            " FROM daily WHERE date >= ? ORDER BY date DESC LIMIT 30", (since,))]
        totals = [dict(r) for r in c.execute(
            "SELECT sport, COUNT(*) n, ROUND(SUM(duration_s)/3600.0,1) hours"
            " FROM activities WHERE date >= ? GROUP BY sport ORDER BY hours DESC", (since,))]
        rp = cache.get_metric(c, "race_predictions")
        sync = cache.sync_status(c)
    return {
        "window_days": days, "totals_by_sport": totals, "weekly_by_sport": weekly,
        "recent_activities": recent, "recovery": recovery,
        "race_predictions": rp, "cache": sync,
    }


def register_tools(app):

    # ── profile / onboarding ─────────────────────────────────────────────

    @app.tool()
    async def get_athlete_profile() -> str:
        """Read the athlete's profile: race, goal, availability, zones, limits.

        Call this first in any coaching conversation. If onboarding_complete is
        false, run the onboarding conversation before offering a plan.
        """
        p = P.get_profile()
        return _ok(profile=p, onboarding_complete=P.onboarding_complete(),
                   days_to_race=P.days_to_race(),
                   memory=P.get_memory()["notes"])

    @app.tool()
    async def save_athlete_profile(
        athlete: str = None, race: str = None, race_date: str = None,
        race_date_is_approximate: bool = None, distance: str = None,
        goal: str = None, hours_per_week: float = None,
        available_days: str = None, rest_day: str = None,
        pool_length_m: float = None, swims_open_water: bool = None,
        has_trainer: bool = None, has_power_meter: bool = None,
        ftp_watts: float = None, css_sec_per_100m: float = None,
        max_hr: float = None, limiter: str = None,
        injuries: str = None, notes: str = None,
        course_pb_seconds: float = None, course_pb_note: str = None,
        overall_pb_seconds: float = None, overall_pb_note: str = None,
        goal_seconds: float = None,
    ) -> str:
        """Save onboarding answers. Only pass the fields the athlete actually gave.

        race_date may be approximate (e.g. "2026-12-01" with
        race_date_is_approximate=true) and refined later.

        The three times are three different facts and must not be collapsed:
          course_pb_seconds  - his best AT THIS RACE, in seconds
          overall_pb_seconds - his best anywhere, which may be faster
          goal_seconds       - what he is chasing now
        Setting goal_seconds is what makes the race panel appear: without it
        there is nothing to measure his splits against.
        """
        try:
            return _ok(profile=P.save_profile(
                race=race, race_date=race_date,
                race_date_is_approximate=race_date_is_approximate,
                distance=distance, goal=goal, hours_per_week=hours_per_week,
                available_days=available_days, rest_day=rest_day,
                pool_length_m=pool_length_m, swims_open_water=swims_open_water,
                has_trainer=has_trainer, has_power_meter=has_power_meter,
                ftp_watts=ftp_watts, css_sec_per_100m=css_sec_per_100m,
                max_hr=max_hr, limiter=limiter, injuries=injuries, notes=notes,
                athlete=athlete,
                course_pb_seconds=course_pb_seconds, course_pb_note=course_pb_note,
                overall_pb_seconds=overall_pb_seconds, overall_pb_note=overall_pb_note,
                goal_seconds=goal_seconds))
        except ValueError as e:
            return _err(e)

    # ── training data (cache-backed, fast) ───────────────────────────────

    @app.tool()
    async def get_training_summary(days: int = 84) -> str:
        """Volume by sport, weekly trend, recent sessions and recovery.

        Reads the local cache, so it is instant. Volume is reported in HOURS as
        well as km because indoor cycling records zero distance -- this athlete
        logged 56 hours of it against 0 km, so km alone hides a third of his
        training.
        """
        return _ok(**_summary(days))

    @app.tool()
    async def get_training_week(week_of_date: str = None) -> str:
        """The planned week next to what was actually done, Monday-anchored.

        Each day carries `source`: "athlete" if he wrote it, "coach" if it came
        from an approved proposal.
        """
        week = P.week_of(week_of_date)
        dates = [d["date"] for d in week]
        with cache.connect() as c:
            done = {}
            for r in c.execute(
                "SELECT date, sport, name, distance_m, duration_s, avg_hr,"
                " avg_power, training_load FROM activities"
                f" WHERE date IN ({','.join('?' * len(dates))})", dates):
                done.setdefault(r["date"], []).append(dict(r))
        for d in week:
            d["actual"] = done.get(d["date"], [])
        return _ok(week=week, days_to_race=P.days_to_race())

    @app.tool()
    async def analyze_plan_vs_actual(days: int = 28) -> str:
        """Compliance: which planned sessions were done, moved, skipped or missed."""
        plan = P.get_plan()["days"]
        start = (date.today() - timedelta(days=days)).isoformat()
        today = date.today().isoformat()
        with cache.connect() as c:
            actual = {}
            for r in c.execute(
                "SELECT date, sport, duration_s, training_load FROM activities"
                " WHERE date >= ?", (start,)):
                actual.setdefault(r["date"], []).append(dict(r))
        rows, hit, total = [], 0, 0
        for d, entry in sorted(plan.items()):
            if not (start <= d <= today):
                continue
            total += 1
            acts = actual.get(d, [])
            matched = any(a["sport"] == entry.get("sport") for a in acts)
            hit += bool(matched)
            rows.append({"date": d, "planned_sport": entry.get("sport"),
                         "title": entry.get("title"), "status": entry.get("status"),
                         "source": entry.get("source"), "matched": matched,
                         "actual": acts})
        unplanned = [{"date": d, "sessions": a} for d, a in sorted(actual.items())
                     if d not in plan]
        return _ok(window_days=days, planned=total,
                   completed_as_planned=hit,
                   compliance_pct=round(100 * hit / total) if total else None,
                   rows=rows, unplanned_sessions=unplanned)

    # ── review: advice only, no write path ───────────────────────────────

    @app.tool()
    async def get_review_context(week_of_date: str = None) -> str:
        """Read-only bundle for critiquing a week the athlete built himself.

        Use this when he asks "what do you think of this week?". It writes
        nothing. Give an opinion and stop -- do not call any plan-editing tool
        afterwards unless he explicitly asks you to change something.
        """
        return _ok(mode="review_only",
                   reminder="Give an opinion. Do not modify the plan.",
                   week=P.week_of(week_of_date),
                   profile=P.get_profile(),
                   days_to_race=P.days_to_race(),
                   summary=_summary(56),
                   memory=P.get_memory()["notes"])

    # ── the athlete's own edits ──────────────────────────────────────────

    @app.tool()
    async def set_workout_day(
        day: str, sport: str, title: str, workout_json: str = None,
        author: str = "athlete", force: bool = False,
    ) -> str:
        """Write one day of the plan.

        author="athlete" when he dictated it; the coach may not overwrite a day
        he wrote unless he explicitly asks (force=true).
        workout_json is the structured workout (see build_and_push_workout).
        """
        entry = {"sport": sport, "title": title}
        if workout_json:
            try:
                w = json.loads(workout_json)
                builders.build(w)          # validate now, not at push time
                entry["workout"] = w
            except (json.JSONDecodeError, builders.WorkoutError) as e:
                return _err(f"workout rejected: {e}")
        try:
            return _ok(day=P.write_day(day, entry, author, force=force))
        except (PermissionError, ValueError) as e:
            return _err(e)

    @app.tool()
    async def set_day_status(day: str, status: str) -> str:
        """Mark a planned day done, skipped, missed or planned."""
        try:
            return _ok(day=P.set_status(day, status))
        except (KeyError, ValueError) as e:
            return _err(e)

    @app.tool()
    async def move_workout(from_day: str, to_day: str) -> str:
        """Move a planned session to another date."""
        try:
            return _ok(day=P.move_day(from_day, to_day))
        except (KeyError, ValueError) as e:
            return _err(e)

    @app.tool()
    async def remove_workout(day: str) -> str:
        """Delete a planned session from the plan (not from the watch)."""
        return _ok(removed=P.remove_day(day))

    # ── proposals ────────────────────────────────────────────────────────

    @app.tool()
    async def propose_plan(days_json: str, summary: str = "") -> str:
        """Stage a plan for the athlete to approve. Does NOT change the plan.

        days_json maps date -> {sport, title, workout?}. Present the proposal in
        chat and wait; only approve_proposal moves it into the plan.
        """
        try:
            days = json.loads(days_json)
        except json.JSONDecodeError as e:
            return _err(f"days_json is not valid JSON: {e}")
        if not isinstance(days, dict):
            return _err("days_json must be an object keyed by date")
        for d, entry in days.items():
            if entry.get("workout"):
                try:
                    builders.build(entry["workout"])
                except builders.WorkoutError as e:
                    return _err(f"{d}: {e}")
        try:
            P.propose(days, summary)
        except ValueError as e:
            return _err(e)
        return _ok(staged=len(days), summary=summary,
                   note="Nothing is in the plan yet. Ask him to approve.")

    @app.tool()
    async def get_pending_proposal() -> str:
        """Show the staged proposal, if any."""
        return _ok(pending=P.get_pending())

    @app.tool()
    async def approve_proposal(dates: str = None, force: bool = False) -> str:
        """Apply the staged proposal. Call only after he has actually agreed.

        dates: optional comma-separated subset. Days he authored himself are
        refused unless force=true.
        """
        try:
            picked = [d.strip() for d in dates.split(",")] if dates else None
            return _ok(**P.approve(picked, force=force))
        except ValueError as e:
            return _err(e)

    @app.tool()
    async def reject_proposal() -> str:
        """Discard the staged proposal without touching the plan."""
        return _ok(**P.reject())

    # ── memory ───────────────────────────────────────────────────────────

    @app.tool()
    async def remember_preference(note: str, tag: str = None) -> str:
        """Store a durable preference or constraint he stated.

        Examples: "hates the treadmill", "Tuesday is always the bike",
        "pool closed on Fridays". These are loaded into every conversation.
        """
        try:
            P.remember(note, tag)
            return _ok(notes=P.get_memory()["notes"])
        except ValueError as e:
            return _err(e)

    # ── the watch ────────────────────────────────────────────────────────

    @app.tool()
    async def preview_workout(workout_json: str) -> str:
        """Validate a structured workout and describe it back in Hebrew.

        Always call this and show the result before pushing anything, so a
        mis-specified target is visible before it reaches the watch.
        """
        try:
            w = json.loads(workout_json)
            payload = builders.build(w)
        except (json.JSONDecodeError, builders.WorkoutError) as e:
            return _err(e)
        return _ok(sport=SPORT_HE.get(w["sport"], w["sport"]),
                   name=payload["workoutName"],
                   estimated_minutes=round(payload["estimatedDurationInSecs"] / 60),
                   lines=_describe(w), garmin_payload=payload)

    @app.tool()
    async def push_workout_to_watch(workout_json: str, day: str = None) -> str:
        """Upload a workout to Garmin and optionally schedule it on a date.

        Only call this after showing him the preview and getting a yes.
        """
        try:
            w = json.loads(workout_json)
            payload = builders.build(w)
        except (json.JSONDecodeError, builders.WorkoutError) as e:
            return _err(e)
        g = _garmin()
        res = g.upload_workout(payload)
        wid = res.get("workoutId") if isinstance(res, dict) else None
        if not wid:
            return _err("Garmin did not return a workoutId", response=res)
        scheduled = None
        if day:
            scheduled = g.schedule_workout(wid, day)
            if w.get("_plan_day"):
                P.write_day(day, {"garmin_workout_id": wid, "scheduled_at": P.now()},
                            P.ATHLETE, force=True)
        return _ok(workout_id=wid, scheduled_for=day, response=scheduled)

    @app.tool()
    async def push_plan_days_to_watch(from_day: str = None, days: int = 10) -> str:
        """Push the next few planned days to the watch.

        Deliberately a rolling horizon rather than the whole block: a plan that
        is still adapting should not be nailed to the calendar four months out.
        """
        start = date.fromisoformat(from_day) if from_day else date.today()
        plan = P.get_plan()["days"]
        g, pushed, skipped = _garmin(), [], []
        for i in range(days):
            d = (start + timedelta(days=i)).isoformat()
            entry = plan.get(d)
            if not entry or not entry.get("workout"):
                continue
            if entry.get("garmin_workout_id"):
                skipped.append({"date": d, "reason": "already on the watch"})
                continue
            try:
                payload = builders.build(entry["workout"])
            except builders.WorkoutError as e:
                skipped.append({"date": d, "reason": str(e)})
                continue
            res = g.upload_workout(payload)
            wid = res.get("workoutId") if isinstance(res, dict) else None
            if not wid:
                skipped.append({"date": d, "reason": "no workoutId returned"})
                continue
            g.schedule_workout(wid, d)
            P.write_day(d, {"garmin_workout_id": wid, "scheduled_at": P.now()},
                        entry.get("source", P.ATHLETE), force=True)
            pushed.append({"date": d, "workout_id": wid, "title": entry.get("title")})
        return _ok(pushed=pushed, skipped=skipped)

    @app.tool()
    async def remove_from_watch(day: str) -> str:
        """Unschedule whatever is on the Garmin calendar for a date."""
        g = _garmin()
        y, m = int(day[:4]), int(day[5:7])
        cal = g.connectapi(f"/calendar-service/year/{y}/month/{m - 1}") or {}
        removed = []
        for item in cal.get("calendarItems") or []:
            if item.get("itemType") == "workout" and item.get("date") == day and item.get("id"):
                g.client.delete("connectapi", f"/workout-service/schedule/{item['id']}", api=True)
                removed.append(item["id"])
        entry = P.get_day(day)
        if entry and entry.get("garmin_workout_id"):
            P.write_day(day, {"garmin_workout_id": None, "scheduled_at": None},
                        entry.get("source", P.ATHLETE), force=True)
        return _ok(unscheduled=removed)

    @app.tool()
    async def analyze_last_session(which: str = "last", date: str = None) -> str:
        """Everything needed to judge one session, gathered in a single call.

        Assembles the summary, the lap splits, the time in each heart-rate
        zone, the power zones for a ride, and what the plan asked for that day.
        It exists as one tool rather than five so the analysis comes out the
        same way every time -- a coach re-deciding which endpoints to pull is
        a coach whose answers drift.

        which: "last" | "run" | "bike" | "swim"   (or pass an explicit date)
        """
        want = None if which in (None, "last") else which
        with cache.connect() as c:
            if date:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM activities WHERE date = ? ORDER BY start_time DESC", (date,))]
            elif want:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM activities WHERE sport = ?"
                    " ORDER BY start_time DESC LIMIT 1", (want,))]
            else:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM activities ORDER BY start_time DESC LIMIT 1")]
        if not rows:
            return _err("לא נמצא אימון מתאים במטמון. "
                        "אם הוא בדיוק הסתיים — קרא ל-refresh_from_garmin קודם.")
        a = rows[0]
        aid = a["activity_id"]
        g = _garmin()

        # Live, per-activity: none of this is in the cache, and it is the
        # detail that separates "you ran 8k" from "you faded in the last 2k".
        detail = {}
        for label, fn in (
            ("splits", lambda: g.get_activity_splits(aid)),
            ("hr_zones", lambda: g.get_activity_hr_in_timezones(aid)),
        ):
            try:
                detail[label] = fn()
            except Exception as e:
                detail[label] = {"error": f"{type(e).__name__}: {e}"}
        if a["sport"] == "bike":
            try:
                detail["power_zones"] = g.get_activity_power_in_timezones(aid)
            except Exception as e:
                detail["power_zones"] = {"error": f"{type(e).__name__}: {e}"}

        laps = []
        for i, l in enumerate((detail.get("splits") or {}).get("lapDTOs") or [], 1):
            dur, dist = l.get("duration") or 0, l.get("distance") or 0
            sp = l.get("averageSpeed") or 0
            laps.append({
                "lap": i, "km": round(dist / 1000, 2),
                "seconds": round(dur), "time": _mmss(dur),
                "avg_hr": l.get("averageHR"), "max_hr": l.get("maxHR"),
                "avg_power": l.get("averagePower"),
                "rate": _rate(a["sport"], sp),
            })

        planned = P.get_day(a["date"])
        sp = a.get("avg_speed") or 0
        return _ok(
            session={
                "date": a["date"], "sport": SPORT_HE.get(a["sport"], a["sport"]),
                "name": a["name"], "km": round((a["distance_m"] or 0) / 1000, 2),
                "minutes": round((a["duration_s"] or 0) / 60),
                "avg_hr": a["avg_hr"], "max_hr": a["max_hr"],
                "avg_power": a["avg_power"], "norm_power": a["norm_power"],
                "rate": _rate(a["sport"], sp),
                "garmin_load": a["training_load"],
                "aerobic_te": a["aerobic_te"], "anaerobic_te": a["anaerobic_te"],
            },
            laps=laps,
            hr_zones=detail.get("hr_zones"),
            power_zones=detail.get("power_zones"),
            planned=planned,
            matched_plan=(bool(planned) and planned.get("sport") == a["sport"]),
            note=("השווה בין הביצוע למתוכנן, והתייחס לפיזור ההקפות — "
                  "דעיכה בהקפות האחרונות אומרת משהו אחר מקצב אחיד."),
        )

    # ── refresh ──────────────────────────────────────────────────────────

    @app.tool()
    async def refresh_from_garmin(days: int = 14) -> str:
        """Pull recent Garmin data into the local cache.

        Everything else reads the cache, so call this when he says he just
        finished a session and wants it reflected.
        """
        from . import sync
        try:
            return _ok(**sync.sync(_garmin(), days=days, verbose=False))
        except Exception as e:
            return _err(f"{type(e).__name__}: {e}")

    return app


def _describe(w):
    """Render a structured workout as Hebrew lines for the preview."""
    def pace(sec):
        return f"{int(sec) // 60}:{int(sec) % 60:02d}"

    def one(s, depth=0):
        pad = "   " * depth
        e = s.get("end") or {}
        kind = {"warmup": "חימום", "cooldown": "שחרור", "rest": "מנוחה",
                "work": "עבודה", "main": "עבודה", "interval": "עבודה",
                "recovery": "מנוחה"}.get(s.get("kind"), s.get("kind") or "עבודה")
        if e.get("type") == "distance_m":
            amount = f"{e['value']:.0f} מ'"
        elif e.get("type") == "time_s":
            amount = f"{pace(e['value'])} דק'"
        elif e.get("type") == "fixed_rest_s":
            amount = f"{e['value']:.0f} שנ' מנוחה"
        else:
            amount = "עד לחיצת לאפ"
        t = s.get("target") or {}
        tgt = ""
        if t.get("type") == "power_w":
            tgt = f" @ {t['low']:.0f}-{t.get('high', t['low']):.0f}W"
        elif t.get("type") == "pace_per_100m":
            tgt = f" @ {pace(t['low'])}-{pace(t.get('high', t['low']))}/100מ'"
        elif t.get("type") == "pace_per_km":
            tgt = f" @ {pace(t['low'])}-{pace(t.get('high', t['low']))}/ק\"מ"
        elif t.get("type") == "hr_bpm":
            tgt = f" @ {t['low']:.0f}-{t.get('high', t['low']):.0f} דופק"
        if s.get("cadence"):
            tgt += f" · קדנס {s['cadence']['low']:.0f}"
        return f"{pad}{kind}: {amount}{tgt}"

    out = []
    for s in w.get("steps", []):
        if s.get("kind") == "repeat":
            out.append(f"{s['times']}× —")
            for c in s.get("steps", []):
                out.append(one(c, 1))
        else:
            out.append(one(s))
    return out
