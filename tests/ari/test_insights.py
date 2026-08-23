"""The insight engine, and mostly the brakes on it.

A rule that fires on two data points is noise with a severity colour, and a
card that cries wolf costs more than the insight was worth. Most of what
follows checks that rules stay silent, because silence is the harder
behaviour to get right and the easier one to lose.
"""

import sqlite3
from datetime import date, timedelta

import pytest

from ari_coach import cache, insights as I, race as R


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(cache.SCHEMA)
    yield c
    c.close()


def day(n):
    return (date.today() - timedelta(days=n)).isoformat()


def add_days(conn, n, **vals):
    for i in range(n):
        row = {"date": day(i), **vals}
        cache.upsert_daily(conn, row)


def add_acts(conn, n, sport="run", hours=1.0, hr=140, start=0, every=1):
    for i in range(n):
        cache.upsert_activity(conn, {
            "activity_id": hash((sport, start, i)) & 0x7FFFFFFF,
            "date": day(start + i * every), "sport": sport,
            "duration_s": hours * 3600, "avg_hr": hr, "distance_m": 10000,
        })


def keys(cards):
    return {c.key for c in cards}


# ── brakes ────────────────────────────────────────────────────────────────

def test_an_empty_database_produces_no_cards_at_all(conn):
    assert I.compute(conn) == []


def test_load_rule_stays_silent_below_five_days(conn):
    add_days(conn, 4, load_acute=400, load_chronic=300)
    assert "load" not in keys(I.compute(conn))


def test_load_rule_fires_at_five_days(conn):
    add_days(conn, 5, load_acute=450, load_chronic=300)
    assert "load" in keys(I.compute(conn))


def test_easy_hard_stays_silent_below_eight_sessions(conn):
    add_acts(conn, 7, hr=180)
    assert "easy_hard" not in keys(I.compute(conn))


def test_recovery_stays_silent_on_a_short_history(conn):
    add_days(conn, 10, hrv_last=50, rhr=50)
    assert "recovery" not in keys(I.compute(conn))


def test_vo2_stays_silent_below_three_weeks(conn):
    add_days(conn, 15, vo2max=50.0)
    assert "vo2" not in keys(I.compute(conn))


def test_ramp_stays_silent_without_four_weeks(conn):
    add_acts(conn, 3, every=7, hours=5)
    assert "ramp" not in keys(I.compute(conn))


def test_collapse_needs_something_to_fall_from(conn):
    """Going from 0.2h to 0h is not a collapse worth a critical card."""
    add_acts(conn, 2, sport="bike", hours=0.2, start=60, every=7)
    assert not [k for k in keys(I.compute(conn)) if k.startswith("collapse")]


# ── the rules do fire when they should ────────────────────────────────────

def test_a_spike_in_load_is_critical(conn):
    add_days(conn, 8, load_acute=600, load_chronic=300, load_status="HIGH")
    card = [c for c in I.compute(conn) if c.key == "load"][0]
    assert card.severity == I.CRITICAL
    assert "2.0" in card.evidence
    assert card.action


def test_a_healthy_ratio_reads_as_good(conn):
    add_days(conn, 8, load_acute=340, load_chronic=330, load_status="OPTIMAL")
    card = [c for c in I.compute(conn) if c.key == "load"][0]
    assert card.severity == I.GOOD


def test_a_discipline_that_vanished_is_caught(conn):
    add_acts(conn, 6, sport="bike", hours=3.0, start=45, every=7)   # prior
    add_acts(conn, 6, sport="bike", hours=0.1, start=3, every=7)    # recent
    card = [c for c in I.compute(conn) if c.key == "collapse_bike"][0]
    assert card.severity == I.CRITICAL
    assert "אופניים" in card.title


def test_easy_hard_uses_a_per_sport_ceiling(conn):
    """The same heart rate is easy running and hard on the bike.

    150 bpm is 77% of his running max (194) and 92% of his cycling max (163).
    A single pooled threshold would call both the same thing, which is the
    bug this rule exists to avoid.
    """
    add_acts(conn, 10, sport="run", hr=150)
    run_card = [c for c in I.compute(conn) if c.key == "easy_hard"][0]
    assert run_card.metric["easy_pct"] == 100, "150 bpm running is under the ceiling"

    conn.execute("DELETE FROM activities")
    add_acts(conn, 10, sport="bike", hr=150)
    bike_card = [c for c in I.compute(conn) if c.key == "easy_hard"][0]
    assert bike_card.metric["easy_pct"] == 0, "150 bpm on the bike is over it"
    assert bike_card.severity == I.WARN


def test_all_easy_is_flagged_as_missing_intensity(conn):
    add_acts(conn, 10, sport="run", hr=140)
    card = [c for c in I.compute(conn) if c.key == "easy_hard"][0]
    assert card.severity == I.INFO and "עצימות" in card.evidence


def test_a_healthy_mix_reads_as_good(conn):
    add_acts(conn, 8, sport="run", hr=145, start=0, every=2)    # easy
    add_acts(conn, 2, sport="run", hr=180, start=1, every=9)    # hard
    card = [c for c in I.compute(conn) if c.key == "easy_hard"][0]
    assert 60 <= card.metric["easy_pct"] <= 92
    assert card.severity == I.GOOD


def test_every_card_carries_evidence_and_most_carry_an_action(conn):
    add_days(conn, 30, load_acute=500, load_chronic=300, hrv_last=40,
             rhr=55, vo2max=50.0)
    add_acts(conn, 12, sport="run", hr=185)
    cards = I.compute(conn)
    assert cards
    for c in cards:
        assert c.evidence.strip(), f"{c.key} has no evidence"
        assert c.severity in (I.CRITICAL, I.WARN, I.INFO, I.GOOD)


def test_cards_come_back_worst_first(conn):
    add_days(conn, 30, load_acute=600, load_chronic=300, hrv_last=60,
             rhr=45, vo2max=50.0)
    add_acts(conn, 12, sport="run", hr=140)
    ranks = [I._RANK[c.severity] for c in I.compute(conn)]
    assert ranks == sorted(ranks)


def test_a_rule_that_throws_becomes_a_card_not_a_blank_page(conn, monkeypatch):
    def boom(ctx):
        raise ValueError("kaboom")
    monkeypatch.setattr(I, "RULES", (boom,))
    cards = I.compute(conn)
    assert len(cards) == 1 and "kaboom" in cards[0].evidence


# ── race-aware rules ──────────────────────────────────────────────────────

PB = R.Race("2026-05-29", "PB", [
    R.Leg(R.SWIM, 787.0, 760.0), R.Leg(R.T1, 279.0, 770.0),
    R.Leg(R.BIKE, 1951.0, 18550.0), R.Leg(R.T2, 77.0, 120.0),
    R.Leg(R.RUN, 1318.0, 5140.0)])


def test_transition_card_names_the_free_time(conn):
    tgt = R.target_splits(PB, goal_seconds=73 * 60 + 30)
    card = I.rule_transitions({"race_targets": tgt})
    assert card and "מעבר 1" in card.evidence
    assert card.metric["seconds_available"] > 150


def test_race_rules_are_absent_without_a_goal(conn):
    assert I.rule_transitions({"race_targets": None}) is None
    assert I.rule_race_gap({"race_targets": None}) is None


# ── merge ─────────────────────────────────────────────────────────────────

def test_a_coach_card_never_replaces_a_measured_one(conn):
    add_days(conn, 8, load_acute=340, load_chronic=330)
    engine = I.compute(conn)
    merged = I.merge(engine, [{"key": "load", "severity": "warn",
                               "title": "coach disagrees", "evidence": "x"}])
    loads = [c for c in merged if c["key"].startswith("load")]
    assert len(loads) == 2
    assert any(c["author"] == "engine" for c in loads)
    assert any(c["author"] == "coach" for c in loads)


def test_merge_keeps_worst_first(conn):
    merged = I.merge([], [{"key": "a", "severity": "good", "title": "g", "evidence": ""},
                          {"key": "b", "severity": "critical", "title": "c", "evidence": ""}])
    assert merged[0]["severity"] == "critical"


# ── HR ceilings, derived per athlete ──────────────────────────────────────
# Two real failures, both found by running the engine against a second
# athlete's account rather than by reading the code.

ARI_HR = ([{"sport": "run", "max_hr": 169}] * 35
          + [{"sport": "swim", "max_hr": 173}] * 37
          + [{"sport": "bike", "max_hr": 165}] * 23)
ALON_HR = ([{"sport": "run", "max_hr": 184}] * 7
           + [{"sport": "swim", "max_hr": 114}] * 4)


def test_ceilings_are_not_one_athletes_numbers_applied_to_everyone():
    """The bug: thresholds hard-coded from Ari scored Alon's sessions 45%
    easy where his own numbers say 9%."""
    ari = I.max_hr_by_sport(ARI_HR)
    alon = I.max_hr_by_sport(ALON_HR)
    assert alon["run"] != ari["run"]
    assert alon["run"] == 184, "his own measured running max"
    assert ari["run"] == 169


def test_a_sport_he_never_races_hard_does_not_set_its_own_ceiling():
    """The opposite trap: Alon's highest swim HR is 114 because he only ever
    swims easy. A ceiling built on that rejects nearly every swim as hard."""
    alon = I.max_hr_by_sport(ALON_HR)
    assert alon["swim"] > 160, f"got {alon['swim']} — the 114 trap"
    assert alon["swim"] == 184 + I.SPORT_HR_OFFSET["swim"]


def test_a_genuinely_higher_sport_max_is_believed():
    """Ari really does hit 173 swimming across 37 sessions, higher than his
    169 running max. That is evidence, and it beats the offset -- but it
    must not drag the running ceiling up with it."""
    ari = I.max_hr_by_sport(ARI_HR)
    assert ari["swim"] == 173
    assert ari["run"] == 169, "running keeps its own measured max"


def test_the_anchor_is_a_running_equivalent_not_the_global_maximum():
    """Offsets are defined relative to running, so the anchor must be too."""
    swimmer = [{"sport": "swim", "max_hr": 175}] * 20   # no running at all
    m = I.max_hr_by_sport(swimmer)
    assert m["swim"] == 175
    assert m["run"] == 175 - I.SPORT_HR_OFFSET["swim"], "converted back to running"


def test_a_handful_of_sessions_is_not_enough_to_set_a_max():
    few = [{"sport": "run", "max_hr": 150}] * 3
    assert I.max_hr_by_sport(few)["run"] == I.ANCHOR_FALLBACK


def test_a_stated_max_hr_in_the_profile_wins():
    m = I.max_hr_by_sport(ALON_HR, profile={"max_hr": 200})
    assert m["run"] == 200
    assert m["bike"] == 200 + I.SPORT_HR_OFFSET["bike"]


def test_no_heart_rate_data_at_all_falls_back_rather_than_crashing():
    m = I.max_hr_by_sport([])
    assert set(m) == {"run", "bike", "swim"}
    assert all(v > 100 for v in m.values())


def test_the_same_session_scores_differently_for_two_athletes(conn):
    """160 bpm is a hard run for Alon (max 184) and a moderate one for a
    200-max athlete. The rule must reflect whose body it is."""
    add_acts(conn, 10, sport="run", hr=160)
    for r in conn.execute("SELECT activity_id FROM activities").fetchall():
        conn.execute("UPDATE activities SET max_hr = 184 WHERE activity_id = ?", (r[0],))
    low = [c for c in I.compute(conn) if c.key == "easy_hard"][0]
    high = [c for c in I.compute(conn, profile={"max_hr": 210}) if c.key == "easy_hard"][0]
    assert low.metric["easy_pct"] == 0      # 160/184 = 0.87
    assert high.metric["easy_pct"] == 100   # 160/210 = 0.76
