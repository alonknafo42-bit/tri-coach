"""Ari's two real 2026 triathlons, and the comparison that must not lie.

The raw numbers are 1:16:18 then 1:13:34. Anyone reading those concludes he
took 2:44 off in four weeks. He did not: the second bike course was 1.38 km
shorter. These tests hold the module to reporting that.
"""

import pytest

from ari_coach import race as R

# Verbatim from Garmin's multisport splits on his account.
RACE1 = R.Race("2026-05-01", "טריאתלון ראשון 26", [
    R.Leg(R.SWIM, 836.0, 800.0),     # 13:56
    R.Leg(R.T1,   207.0, 440.0),     # 3:27
    R.Leg(R.BIKE, 2112.0, 19930.0),  # 35:12
    R.Leg(R.T2,   109.0, 220.0),     # 1:49
    R.Leg(R.RUN,  1313.0, 5090.0),   # 21:53
])
RACE2_PB = R.Race("2026-05-29", "ספורט משולב", [
    R.Leg(R.SWIM, 787.0, 760.0),     # 13:07
    R.Leg(R.T1,   279.0, 770.0),     # 4:39
    R.Leg(R.BIKE, 1951.0, 18550.0),  # 32:31
    R.Leg(R.T2,   77.0, 120.0),      # 1:17
    R.Leg(R.RUN,  1318.0, 5140.0),   # 21:58
])


def test_the_raw_totals_are_what_they_appear_to_be():
    assert R.mmss(RACE1.total_seconds) == "1:16:17"
    assert R.mmss(RACE2_PB.total_seconds) == "1:13:32"


# ── the trap ──────────────────────────────────────────────────────────────

def test_raw_delta_says_he_improved_by_nearly_three_minutes():
    """Establish the misleading number explicitly, so the fix is visible."""
    c = R.compare(RACE1, RACE2_PB)
    assert c["raw_total_delta"] == pytest.approx(-165, abs=2)


def test_normalised_delta_removes_almost_all_of_that_improvement():
    c = R.compare(RACE1, RACE2_PB)
    # Once both are projected onto the same course, 2:44 of "improvement"
    # collapses to noise -- in fact slightly the wrong way, because T1 is
    # carried across and his T1 course was much longer the second time.
    assert abs(c["normalised_total_delta"]) < 30, \
        f"expected the gap to nearly vanish, got {c['normalised_total_delta']:.0f}s"
    assert c["normalised_total_delta"] > c["raw_total_delta"] + 100


def test_the_comparison_always_carries_the_warning():
    assert "מסלול" in R.compare(RACE1, RACE2_PB)["warning"]


def test_bike_is_flagged_as_a_different_course():
    bike = [l for l in R.compare(RACE1, RACE2_PB)["legs"] if l["leg"] == R.BIKE][0]
    assert bike["same_course"] is False
    assert bike["metres_before"] - bike["metres_after"] == pytest.approx(1380, abs=1)


def test_bike_speed_barely_moved():
    """+0.26 km/h, not the 2:41 the raw split suggests."""
    bike = [l for l in R.compare(RACE1, RACE2_PB)["legs"] if l["leg"] == R.BIKE][0]
    assert bike["raw_delta"] == pytest.approx(-161, abs=2)
    assert bike["rate_delta"] == pytest.approx(0.26, abs=0.05)
    assert bike["rate_better"] is True


def test_swim_gained_about_one_second_per_hundred():
    swim = [l for l in R.compare(RACE1, RACE2_PB)["legs"] if l["leg"] == R.SWIM][0]
    assert swim["rate_delta"] == pytest.approx(-1.0, abs=0.6)


def test_run_was_essentially_identical():
    run = [l for l in R.compare(RACE1, RACE2_PB)["legs"] if l["leg"] == R.RUN][0]
    assert abs(run["rate_delta"]) < 4          # seconds per km


# ── projecting the PB onto the course he is actually racing ───────────────

def test_pb_on_a_standard_sprint_is_about_1_15_not_1_13():
    """The finding that sets the whole training plan.

    His PB was set on an 18.55 km bike. Eilat is a standard 20 km. Hold his
    speeds constant and the same performance takes about a minute and three
    quarters longer.
    """
    norm, notes = R.normalise(RACE2_PB, "sprint")
    assert 74 * 60 < norm.total_seconds < 76 * 60, R.mmss(norm.total_seconds)
    assert any("אופניים" in n for n in notes)


def test_transitions_are_not_scaled_by_distance():
    """A transition's length is a property of the venue, not of the athlete."""
    norm, _ = R.normalise(RACE2_PB, "sprint")
    assert norm.leg(R.T1).seconds == RACE2_PB.leg(R.T1).seconds
    assert norm.leg(R.T2).seconds == RACE2_PB.leg(R.T2).seconds


# ── targets ───────────────────────────────────────────────────────────────

def test_breaking_1_13_34_needs_real_time_found():
    t = R.target_splits(RACE2_PB, goal_seconds=73 * 60 + 30, course="sprint")
    assert t["already_achievable"] is False
    assert t["needed_seconds"] > 60


def test_transitions_are_taken_first_because_they_are_free():
    t = R.target_splits(RACE2_PB, goal_seconds=73 * 60 + 30, course="sprint")
    t1 = [l for l in t["legs"] if l["leg"] == R.T1][0]
    assert t1["target_seconds"] == 120
    assert t1["gap_seconds"] == pytest.approx(159, abs=2)   # 4:39 -> 2:00
    assert t1["free"] is True
    assert t["freed_by_transitions"] > 150


def test_every_leg_reports_its_share_of_the_race():
    t = R.target_splits(RACE2_PB, goal_seconds=73 * 60 + 30)
    shares = {l["leg"]: l["share_pct"] for l in t["legs"]}
    assert shares[R.BIKE] > 45, "the bike is the longest leg of a sprint"
    assert shares[R.T1] > 5, "T1 is over 5% of his race -- that is the free time"
    assert sum(shares.values()) == pytest.approx(100, abs=0.6)


def test_a_goal_he_already_beats_is_reported_as_such():
    t = R.target_splits(RACE2_PB, goal_seconds=90 * 60)
    assert t["already_achievable"] is True


# ── sensitivity ───────────────────────────────────────────────────────────

def test_sensitivity_ranks_the_bike_first():
    s = R.sensitivity(RACE2_PB, "sprint")
    assert s[0]["leg"] == R.BIKE
    assert 50 < s[0]["seconds"] < 70, "+1 km/h on a 20 km bike is about a minute"


def test_sensitivity_covers_all_three_disciplines():
    assert {x["leg"] for x in R.sensitivity(RACE2_PB)} == {R.SWIM, R.BIKE, R.RUN}


# ── formatting ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sec,text", [
    (0, "0:00"), (59, "0:59"), (60, "1:00"), (279, "4:39"),
    (4412, "1:13:32"), (None, "—"), (-90, "-1:30"),
])
def test_mmss(sec, text):
    assert R.mmss(sec) == text
