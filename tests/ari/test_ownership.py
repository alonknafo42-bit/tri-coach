"""The plan belongs to the athlete. These tests are that sentence, executable.

The failure they exist to prevent is quiet: a coach that edits while being
asked for an opinion, or a rebuild that erases a session the athlete wrote.
Both look like helpfulness and read as "someone changed my plan".
"""

import json
import os

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARI_COACH_HOME", str(tmp_path))
    import importlib
    from ari_coach import plan_store
    importlib.reload(plan_store)
    return plan_store


SESSION = {"sport": "bike", "title": "3×10 @ threshold",
           "workout": {"sport": "bike", "name": "3×10", "steps": [
               {"kind": "work", "end": {"type": "time_s", "value": 600},
                "target": {"type": "power_w", "low": 200, "high": 210}}]}}


# ── mode 1: he builds it himself ──────────────────────────────────────────

def test_athlete_written_day_is_stored_exactly_as_given(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    got = store.get_day("2026-09-01")
    assert got["source"] == "athlete"
    assert got["workout"] == SESSION["workout"]
    assert got["status"] == "planned"


# ── mode 2: he asks for an opinion ────────────────────────────────────────

def test_review_path_cannot_write_anything(store):
    """There must be no reviewing function that also mutates."""
    writers = {"write_day", "set_status", "move_day", "remove_day", "approve",
               "reject", "propose", "save_profile", "remember", "forget"}
    exported = {n for n in dir(store) if not n.startswith("_")}
    review_fns = {n for n in exported if "review" in n}
    assert not (review_fns & writers), "a review function must not be a writer"


def test_consulting_leaves_the_plan_byte_identical(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    before = open(os.path.join(store.home(), "plan.json"), "rb").read()

    # everything a "review" turn is allowed to touch: reads only
    store.get_plan(); store.week_of("2026-09-01"); store.get_day("2026-09-01")
    store.get_profile(); store.get_memory(); store.days_to_race()

    after = open(os.path.join(store.home(), "plan.json"), "rb").read()
    assert before == after, "reading the plan must not rewrite it"


# ── mode 3: he asks the coach to build ────────────────────────────────────

def test_proposal_does_not_touch_the_plan_until_approved(store):
    store.propose({"2026-09-02": {"sport": "run", "title": "easy 40min"}},
                  summary="build week 1")
    assert store.get_plan()["days"] == {}, "a proposal must not enter the plan"
    assert store.get_pending()["days"], "but it must be retrievable"

    store.approve()
    assert store.get_day("2026-09-02")["title"] == "easy 40min"
    assert store.get_day("2026-09-02")["source"] == "coach"
    assert store.get_day("2026-09-02")["approved_at"]
    assert store.get_pending() == {}, "approving consumes the proposal"


def test_reject_leaves_no_residue(store):
    store.propose({"2026-09-02": {"sport": "run", "title": "easy"}})
    store.reject()
    assert store.get_plan()["days"] == {}
    assert store.get_pending() == {}


def test_partial_approval_keeps_the_rest_pending(store):
    store.propose({"2026-09-02": {"title": "a"}, "2026-09-03": {"title": "b"}})
    res = store.approve(dates=["2026-09-02"])
    assert res["applied"] == ["2026-09-02"]
    assert store.get_day("2026-09-03") is None
    assert "2026-09-03" in store.get_pending()["days"]


# ── the invariant that protects his work ──────────────────────────────────

def test_coach_cannot_overwrite_an_athlete_authored_day(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    with pytest.raises(PermissionError, match="written by the athlete"):
        store.write_day("2026-09-01", {"title": "coach idea"}, store.COACH)
    assert store.get_day("2026-09-01")["title"] == "3×10 @ threshold"


def test_athlete_day_survives_a_coach_plan_rebuild(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    store.propose({
        "2026-09-01": {"sport": "run", "title": "coach replacement"},
        "2026-09-02": {"sport": "swim", "title": "coach new day"},
    })
    res = store.approve()
    assert res["applied"] == ["2026-09-02"]
    assert len(res["refused"]) == 1 and res["refused"][0]["date"] == "2026-09-01"
    assert store.get_day("2026-09-01")["title"] == "3×10 @ threshold"
    assert store.get_day("2026-09-01")["source"] == "athlete"


def test_athlete_can_explicitly_hand_a_day_to_the_coach(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    store.write_day("2026-09-01", {"title": "he asked for this"},
                    store.COACH, force=True)
    assert store.get_day("2026-09-01")["title"] == "he asked for this"


def test_athlete_can_always_overwrite_a_coach_day(store):
    store.write_day("2026-09-01", {"title": "coach"}, store.COACH)
    store.write_day("2026-09-01", {"title": "mine"}, store.ATHLETE)
    assert store.get_day("2026-09-01")["source"] == "athlete"


# ── everyday edits ────────────────────────────────────────────────────────

def test_status_and_move(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    assert store.set_status("2026-09-01", "done")["status"] == "done"
    store.move_day("2026-09-01", "2026-09-03")
    assert store.get_day("2026-09-01") is None
    assert store.get_day("2026-09-03")["moved_from"] == "2026-09-01"


def test_move_onto_an_occupied_day_is_refused(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    store.write_day("2026-09-02", SESSION, store.ATHLETE)
    with pytest.raises(ValueError, match="already has a session"):
        store.move_day("2026-09-01", "2026-09-02")


def test_bad_status_is_rejected(store):
    store.write_day("2026-09-01", SESSION, store.ATHLETE)
    with pytest.raises(ValueError, match="status must be"):
        store.set_status("2026-09-01", "sort-of-done")


def test_week_of_returns_seven_monday_anchored_days(store):
    w = store.week_of("2026-09-03")          # a Thursday
    assert len(w) == 7
    assert w[0]["date"] == "2026-08-31"      # Monday
    assert w[6]["date"] == "2026-09-06"


# ── profile / onboarding ──────────────────────────────────────────────────

def test_countdown_is_none_until_he_sets_a_race(store):
    assert store.days_to_race() is None
    assert store.onboarding_complete() is False
    store.save_profile(race_date="2026-12-05", hours_per_week=6)
    assert store.onboarding_complete() is True
    assert isinstance(store.days_to_race(), int)


def test_a_typo_in_a_profile_field_is_rejected_not_silently_stored(store):
    with pytest.raises(ValueError, match="unknown profile field"):
        store.save_profile(race_dat="2026-12-05")


def test_memory_round_trips(store):
    store.remember("שונא הליכון", tag="preference")
    assert store.get_memory()["notes"][0]["note"] == "שונא הליכון"
    store.forget(0)
    assert store.get_memory()["notes"] == []


# ── every stored field must have a way in ─────────────────────────────────

def test_every_profile_field_can_be_set_through_the_tool():
    """A field with no writer is the failure this project keeps repeating.

    The race-target fields were added to the store and to the dashboard, and
    for a while the coach had no parameter to set them -- so the race panel
    could never appear through a conversation, which is the only way it was
    ever going to be filled.
    """
    import inspect, re
    from ari_coach import plan_store, tools

    src = inspect.getsource(tools)
    sig = src.split("async def save_athlete_profile", 1)[1].split(") -> str:", 1)[0]
    params = set(re.findall(r"[\s(]([a-z_][a-z0-9_]*)\s*:\s*(?:str|float|bool|int)", sig))

    missing = [f for f in plan_store.PROFILE_FIELDS if f not in params]
    assert not missing, f"stored but unsettable by the coach: {missing}"
