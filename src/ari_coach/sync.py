"""Pull Garmin history into the local cache.

Everything here is rate-limit aware: Garmin answers a burst with HTTP 429 and
a backfill touches seven endpoints per day, so requests are spaced and retried
with exponential backoff rather than hammered.
"""

import json
import sys
import time
from datetime import date, datetime, timedelta

from . import cache

SLEEP = 0.4          # seconds between calls; empirically enough to stay under the limit
MAX_BACKOFF = 60.0
RETRIES = 6


def safe(fn, label, verbose=False):
    """Run a Garmin call, absorbing 429s with backoff and other errors with None.

    Returns None on failure by design: one dead endpoint should degrade a single
    field, not abort a multi-day backfill.
    """
    backoff = 2.0
    for attempt in range(RETRIES):
        try:
            return fn()
        except Exception as e:  # garminconnect raises several unrelated types
            msg = str(e)
            if "429" in msg or "Too Many Requests" in msg:
                if attempt == RETRIES - 1:
                    break
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
            if verbose:
                print(f"    {label}: {type(e).__name__}", file=sys.stderr)
            return None
    if verbose:
        print(f"    {label}: gave up after {RETRIES} attempts (429)", file=sys.stderr)
    return None


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and len(cur) > k:
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur


def daily_row(g, d, verbose=False):
    """Assemble one day of wellness data. Eight calls, ~0.4s apart."""
    row = {"date": d}

    sleep = safe(lambda: g.get_sleep_data(d), "sleep", verbose) or {}
    dto = sleep.get("dailySleepDTO") or {}
    row["sleep_seconds"] = _num(dto.get("sleepTimeSeconds"))
    row["deep_seconds"] = _num(dto.get("deepSleepSeconds"))
    row["rem_seconds"] = _num(dto.get("remSleepSeconds"))
    row["sleep_score"] = _num(_dig(sleep, "dailySleepDTO", "sleepScores", "overall", "value"))
    time.sleep(SLEEP)

    hrv = safe(lambda: g.get_hrv_data(d), "hrv", verbose) or {}
    row["hrv_last"] = _num(_dig(hrv, "hrvSummary", "lastNightAvg"))
    row["hrv_7d"] = _num(_dig(hrv, "hrvSummary", "weeklyAvg"))
    row["hrv_status"] = _dig(hrv, "hrvSummary", "status")
    time.sleep(SLEEP)

    rhr = safe(lambda: g.get_rhr_day(d), "rhr", verbose) or {}
    row["rhr"] = _num(_dig(rhr, "allMetrics", "metricsMap",
                           "WELLNESS_RESTING_HEART_RATE", 0, "value"))
    time.sleep(SLEEP)

    bb = safe(lambda: g.get_body_battery(d, d), "body_battery", verbose) or []
    vals = [v[1] for v in (_dig(bb, 0, "bodyBatteryValuesArray", default=[]) or [])
            if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float))]
    row["bb_low"], row["bb_high"] = (min(vals), max(vals)) if vals else (None, None)
    time.sleep(SLEEP)

    stress = safe(lambda: g.get_all_day_stress(d), "stress", verbose) or {}
    row["stress_avg"] = _num(stress.get("avgStressLevel") or stress.get("overallStressLevel"))
    time.sleep(SLEEP)

    tr = safe(lambda: g.get_training_readiness(d), "readiness", verbose) or []
    tr0 = tr[0] if isinstance(tr, list) and tr else (tr if isinstance(tr, dict) else {})
    row["readiness_score"] = _num(tr0.get("score"))
    row["readiness_level"] = tr0.get("level")
    row["readiness_feedback"] = tr0.get("feedbackShort")
    row["recovery_time_min"] = _num(tr0.get("recoveryTime"))
    time.sleep(SLEEP)

    ts = safe(lambda: g.get_training_status(d), "training_status", verbose) or {}
    latest = _dig(ts, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}
    # Keyed by device id, and a user with two devices has two entries; take the
    # first that actually carries a status rather than assuming a single key.
    for dev in latest.values():
        if not isinstance(dev, dict):
            continue
        if dev.get("trainingStatusFeedbackPhrase"):
            row["training_status"] = dev.get("trainingStatusFeedbackPhrase")
            row["training_status_code"] = _num(dev.get("trainingStatus"))
        acwr = dev.get("acuteTrainingLoadDTO") or {}
        if acwr:
            row["load_acute"] = _num(acwr.get("dailyTrainingLoadAcute"))
            row["load_chronic"] = _num(acwr.get("dailyTrainingLoadChronic"))
            row["load_min"] = _num(acwr.get("minTrainingLoadChronic"))
            row["load_max"] = _num(acwr.get("maxTrainingLoadChronic"))
            row["load_status"] = acwr.get("acwrStatus")
        if row.get("load_acute") is not None:
            break
    row["vo2max"] = _num(_dig(ts, "mostRecentVO2Max", "generic", "vo2MaxPreciseValue"))
    time.sleep(SLEEP)

    # training_status only carries VO2max on days Garmin recomputed it -- 6 of
    # 120 days in Ari's history -- so the dedicated endpoint is what makes a
    # VO2max *trend* possible at all.
    if row["vo2max"] is None:
        mm = safe(lambda: g.get_max_metrics(d), "max_metrics", verbose) or []
        mm0 = mm[0] if isinstance(mm, list) and mm else (mm if isinstance(mm, dict) else {})
        row["vo2max"] = _num(_dig(mm0, "generic", "vo2MaxPreciseValue")) or \
                        _num(_dig(mm0, "generic", "vo2MaxValue"))
        time.sleep(SLEEP)

    return row


def _activity_row(a):
    type_key = _dig(a, "activityType", "typeKey")
    start = a.get("startTimeLocal") or ""
    return {
        "activity_id": a.get("activityId"),
        "start_time": start,
        "date": start[:10],
        "type": type_key,
        "sport": cache.classify_sport(type_key),
        "name": a.get("activityName"),
        "distance_m": _num(a.get("distance")),
        "duration_s": _num(a.get("duration")),
        "moving_duration_s": _num(a.get("movingDuration")),
        "avg_hr": _num(a.get("averageHR")),
        "max_hr": _num(a.get("maxHR")),
        "avg_speed": _num(a.get("averageSpeed")),
        "avg_power": _num(a.get("avgPower")),
        "norm_power": _num(a.get("normPower")),
        "max_power": _num(a.get("maxPower")),
        "avg_cadence": _num(a.get("averageRunningCadenceInStepsPerMinute")
                            or a.get("averageBikingCadenceInRevPerMinute")),
        "elevation_gain": _num(a.get("elevationGain")),
        "calories": _num(a.get("calories")),
        "training_load": _num(a.get("activityTrainingLoad")),
        "aerobic_te": _num(a.get("aerobicTrainingEffect")),
        "anaerobic_te": _num(a.get("anaerobicTrainingEffect")),
    }


def sync_activities(g, conn, since, page=100, verbose=False):
    """Page through the activity list until we pass `since`.

    Paginated deliberately: the predecessor called get_activities(0, 300) with
    a hard 300 cap regardless of the requested window, so asking for two years
    silently returned only the most recent 300.
    """
    total, start = 0, 0
    while True:
        batch = safe(lambda: g.get_activities(start, page), f"activities[{start}]", verbose)
        if not batch:
            break
        stop = False
        for a in batch:
            row = _activity_row(a)
            if not row["date"]:
                continue
            if row["date"] < since:
                stop = True
                continue
            cache.upsert_activity(conn, row)
            total += 1
        if stop or len(batch) < page:
            break
        start += page
        time.sleep(SLEEP)
    return total


def sync_race_splits(g, conn, verbose=False):
    """Store the leg splits of every multisport activity.

    A triathlon's value is entirely in its five laps -- swim, T1, bike, T2,
    run. The summary row gives a finish time and tells you nothing about where
    the time went, which is the only question worth asking about a race.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT activity_id, date FROM activities"
        " WHERE type = 'multi_sport' AND (laps IS NULL OR laps = '')")]
    stored = 0
    for row in rows:
        sp = safe(lambda: g.get_activity_splits(row["activity_id"]),
                  f"splits[{row['activity_id']}]", verbose)
        laps = (sp or {}).get("lapDTOs") or []
        if not laps:
            continue
        keep = [{"duration": l.get("duration"), "distance": l.get("distance"),
                 "averageHR": l.get("averageHR"), "maxHR": l.get("maxHR")}
                for l in laps]
        conn.execute("UPDATE activities SET laps = ? WHERE activity_id = ?",
                     (json.dumps(keep), row["activity_id"]))
        stored += 1
        time.sleep(SLEEP)
    if verbose and stored:
        print(f"  race splits stored: {stored}")
    return stored


def sync(g, days=120, verbose=True):
    """Full sync: activities plus any day that is missing or schema-stale."""
    cache.init()
    today = date.today()
    since = (today - timedelta(days=days)).isoformat()
    fetched = skipped = 0
    with cache.connect() as conn:
        n_act = sync_activities(g, conn, since, verbose=verbose)
        if verbose:
            print(f"  activities since {since}: {n_act}")

        d = today
        while d >= today - timedelta(days=days):
            ds = d.isoformat()
            if cache.needs_sync(conn, ds):
                cache.upsert_daily(conn, daily_row(g, ds, verbose))
                fetched += 1
                # Commit as we go. A 150-day backfill is ~35 minutes of calls;
                # holding one transaction across all of it means a dropped
                # connection at minute 34 throws away every day fetched.
                if fetched % 5 == 0:
                    cache.mark_sync(conn, fetched)
                    conn.commit()
                    if verbose:
                        print(f"  ...{fetched} days fetched (at {ds})", flush=True)
            else:
                skipped += 1
            d -= timedelta(days=1)

        sync_race_splits(g, conn, verbose)

        rp = safe(lambda: g.get_race_predictions(), "race_predictions", verbose)
        if rp:
            cache.set_metric(conn, "race_predictions", rp)
        cache.mark_sync(conn, fetched)
    if verbose:
        print(f"  days fetched {fetched}, already current {skipped}")
    return {"activities": n_act, "days_fetched": fetched, "days_current": skipped}
