"""Local SQLite cache of the athlete's Garmin history.

Why this exists at all: measured on Ari's live account, a single Garmin API
call has a 1401ms median (637-2038ms range), so a coach answer that touches
five endpoints spends ~7.5 seconds waiting. The same data read from this
cache takes 0.05ms. Reads come from here; only writes go to Garmin.
"""

import json
import os
import sqlite3
from contextlib import contextmanager

# Bump when a column is added below. Rows written by an older version are
# re-fetched instead of being skipped -- see needs_sync().
#
# This is the fix for a real bug in the predecessor: its resume check was
# `sleep_seconds IS NOT NULL`, so once a day was stored it was never revisited.
# When load_acute/load_chronic were added later, 113 of 120 days silently kept
# a NULL there forever, and Garmin's own cross-sport load looked unavailable
# when it was merely never re-requested.
ROW_VERSION = 3

DEFAULT_DB = os.path.join(
    os.path.expanduser(os.getenv("ARI_COACH_HOME", "~/.ari-coach")), "cache.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY,
    row_version INTEGER DEFAULT 0,
    sleep_seconds INTEGER, deep_seconds INTEGER, rem_seconds INTEGER,
    sleep_score INTEGER,
    hrv_last INTEGER, hrv_7d INTEGER, hrv_status TEXT,
    rhr INTEGER,
    bb_low INTEGER, bb_high INTEGER, stress_avg INTEGER,
    readiness_score INTEGER, readiness_level TEXT, readiness_feedback TEXT,
    recovery_time_min INTEGER,
    training_status TEXT, training_status_code INTEGER,
    vo2max REAL,
    -- Garmin's own EPOC-derived load. Cross-sport, which is exactly why we
    -- use it instead of deriving load from heart rate against a single
    -- running threshold: Ari's max HR differs by sport (run 194, swim 173,
    -- bike 163), so one anchor systematically under-counts his swimming.
    load_acute REAL, load_chronic REAL, load_min REAL, load_max REAL,
    load_status TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY,
    start_time TEXT, date TEXT,
    type TEXT,
    sport TEXT,               -- normalised: swim | bike | run | strength | other
    name TEXT,
    distance_m REAL, duration_s REAL, moving_duration_s REAL,
    avg_hr REAL, max_hr REAL,
    avg_speed REAL,
    avg_power REAL, norm_power REAL, max_power REAL,
    avg_cadence REAL,
    elevation_gain REAL, calories REAL,
    training_load REAL, aerobic_te REAL, anaerobic_te REAL,
    laps TEXT
);
CREATE INDEX IF NOT EXISTS idx_act_date  ON activities(date);
CREATE INDEX IF NOT EXISTS idx_act_sport ON activities(sport, date);

CREATE TABLE IF NOT EXISTS metrics (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_sync TEXT, last_error TEXT, days_synced INTEGER
);
"""

DAILY_COLS = [
    "date", "row_version",
    "sleep_seconds", "deep_seconds", "rem_seconds", "sleep_score",
    "hrv_last", "hrv_7d", "hrv_status", "rhr",
    "bb_low", "bb_high", "stress_avg",
    "readiness_score", "readiness_level", "readiness_feedback",
    "recovery_time_min", "training_status", "training_status_code", "vo2max",
    "load_acute", "load_chronic", "load_min", "load_max", "load_status",
]

ACTIVITY_COLS = [
    "activity_id", "start_time", "date", "type", "sport", "name",
    "distance_m", "duration_s", "moving_duration_s",
    "avg_hr", "max_hr", "avg_speed",
    "avg_power", "norm_power", "max_power", "avg_cadence",
    "elevation_gain", "calories",
    "training_load", "aerobic_te", "anaerobic_te", "laps",
]

# Garmin's activityType.typeKey is open-ended, so match on substrings rather
# than an allowlist -- a new device introducing "virtual_running" should not
# silently drop out of the run bucket.
_SPORT_MATCH = (
    ("swim", ("swim",)),
    ("bike", ("cycl", "bik", "ride")),
    ("run", ("run", "treadmill")),
    ("strength", ("strength", "weight")),
)


def classify_sport(type_key):
    """Map a Garmin typeKey onto swim/bike/run/strength/other."""
    t = (type_key or "").lower()
    for sport, needles in _SPORT_MATCH:
        if any(n in t for n in needles):
            return sport
    return "other"


def db_path():
    return os.path.expanduser(os.getenv("ARI_COACH_DB", DEFAULT_DB))


@contextmanager
def connect(path=None):
    p = path or db_path()
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    # The cache holds his resting heart rate, sleep and HRV. Same protection
    # as the tokens sitting beside it.
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    existed = os.path.exists(p)
    conn = sqlite3.connect(p)
    if not existed:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path=None):
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def _upsert(conn, table, cols, row, key):
    """Write only the keys actually present in `row`.

    A partial Garmin response (one endpoint down, the rest fine) must not
    blank columns that already hold good values, so absent keys are excluded
    from the statement rather than bound as NULL.
    """
    present = [c for c in cols if c in row]
    if not present:
        return
    placeholders = ",".join("?" for _ in present)
    updates = ",".join(f"{c}=excluded.{c}" for c in present if c != key)
    sql = (
        f"INSERT INTO {table} ({','.join(present)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in present])


def upsert_daily(conn, row):
    row = dict(row, row_version=ROW_VERSION)
    _upsert(conn, "daily", DAILY_COLS, row, "date")


def upsert_activity(conn, row):
    _upsert(conn, "activities", ACTIVITY_COLS, row, "activity_id")


def needs_sync(conn, date):
    """True when a day is missing or was written by an older schema version."""
    r = conn.execute(
        "SELECT row_version FROM daily WHERE date = ?", (date,)
    ).fetchone()
    return r is None or (r["row_version"] or 0) < ROW_VERSION


def set_metric(conn, key, value):
    conn.execute(
        "INSERT INTO metrics (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def get_metric(conn, key, default=None):
    r = conn.execute("SELECT value FROM metrics WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def mark_sync(conn, days, error=None):
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO sync_log (id, last_sync, last_error, days_synced) "
        "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "last_sync=excluded.last_sync, last_error=excluded.last_error, "
        "days_synced=excluded.days_synced",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), error, days),
    )


def sync_status(conn):
    r = conn.execute("SELECT * FROM sync_log WHERE id = 1").fetchone()
    return dict(r) if r else {"last_sync": None, "last_error": None, "days_synced": 0}
