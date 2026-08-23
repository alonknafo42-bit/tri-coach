"""The training data gets the same protection as the credential.

The tokens were always 0600 in a 0700 directory. The plan, the profile and
the cache -- his resting heart rate, his sleep, his HRV -- were 0644 in a
0755 directory. Guarding the key and leaving the record open is not a
threat model, so these tests pin both.
"""

import os
import stat

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt",
                                reason="POSIX modes; Windows uses ACLs")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARI_COACH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ARI_COACH_DB", str(tmp_path / "home" / "cache.db"))
    import importlib
    from ari_coach import plan_store, cache
    importlib.reload(plan_store)
    importlib.reload(cache)
    return plan_store, cache


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_the_home_directory_is_owner_only(store):
    ps, _ = store
    assert mode(ps.home()) == 0o700


def test_every_json_the_plan_writes_is_owner_only(store):
    ps, _ = store
    ps.save_profile(athlete="ארי", race_date="2026-12-05", hours_per_week=6)
    ps.write_day("2026-09-01", {"sport": "bike", "title": "x"}, ps.ATHLETE)
    ps.propose({"2026-09-02": {"title": "y"}})
    ps.remember("שונא הליכון")
    ps.write_insight("k", "warn", "t", "e")
    written = [f for f in os.listdir(ps.home()) if f.endswith(".json")]
    assert len(written) >= 5, written
    for f in written:
        assert mode(os.path.join(ps.home(), f)) == 0o600, f


def test_the_cache_database_is_owner_only(store):
    _, cache = store
    cache.init()
    assert mode(cache.db_path()) == 0o600
    assert mode(os.path.dirname(cache.db_path())) == 0o700


def test_no_world_readable_window_during_a_write(store):
    """The temp file must be created 0600, not created then chmod-ed."""
    ps, _ = store
    ps.save_profile(athlete="ארי")
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(mode(src))          # inspect the temp file mid-write
        return real_replace(src, dst)

    os.replace = spy
    try:
        ps.save_profile(athlete="ארי", notes="x")
    finally:
        os.replace = real_replace
    assert seen and all(m == 0o600 for m in seen), seen


def test_a_pre_existing_loose_file_gets_tightened(store):
    """Someone upgrading from the earlier build should not stay exposed."""
    ps, _ = store
    ps.save_profile(athlete="ארי")
    loose = os.path.join(ps.home(), "profile.json")
    os.chmod(loose, 0o644)
    os.chmod(ps.home(), 0o755)
    ps.home()                            # the next call re-secures
    assert mode(ps.home()) == 0o700
    assert mode(loose) == 0o600
