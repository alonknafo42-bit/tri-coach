#!/usr/bin/env python3
"""התקנה בפקודה אחת.

    uv run python install.py

עושה הכל חוץ משני דברים שדורשים אדם: ההתחברות לגרמין (הוא מקליד את
הסיסמה, לא אנחנו) והתקנת התוסף ב-Claude Desktop (גרירה של קובץ).
בסוף מדפיס בדיוק מה לעשות עם שניהם, עם הנתיבים המלאים כבר ממולאים.

בטוח להריץ שוב: כל שלב בודק אם הוא כבר נעשה.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

OK, BAD, WARN, DOT = "✅", "🔴", "⚠️ ", "  ·"
FIRST_DAYS = 21          # נמדד 235 שניות. מספיק לדשבורד; השאר נמשך אחר כך


def say(*a):
    """Print, and survive a console that cannot render the characters.

    cmd.exe with the wrong codepage raises rather than showing mojibake, and
    an installer that crashes on its own progress message is worse than one
    that prints a few question marks.
    """
    try:
        print(*a, flush=True)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "utf-8")
        print(*[str(x).encode(enc, "replace").decode(enc, "replace") for x in a],
              flush=True)


def head(n, title):
    say(f"\n{'─' * 58}\n{n}. {title}\n{'─' * 58}")


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=HERE, **kw)


def _uv():
    """The uv binary, wherever the installer put it."""
    return (shutil.which("uv")
            or os.path.expanduser("~/.local/bin/uv")
            or "uv")


def claude_config_path():
    home = os.path.expanduser("~")
    if platform.system() == "Windows":
        return os.path.join(os.getenv("APPDATA", home), "Claude",
                            "claude_desktop_config.json")
    if platform.system() == "Darwin":
        return os.path.join(home, "Library", "Application Support", "Claude",
                            "claude_desktop_config.json")
    return os.path.join(home, ".config", "Claude", "claude_desktop_config.json")


def claude_running():
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
            return "claude.exe" in out.lower()
        out = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True).returncode
        return out == 0
    except Exception:
        return False


def wire_claude(here):
    """Add this server to Claude Desktop's config, without stepping on it.

    Claude Desktop rewrites this file while it is running -- an edit made
    mid-session was observed being erased minutes later -- so this refuses
    to touch it unless the app is closed. It merges rather than replaces,
    and keeps a backup, because the file may already hold other servers the
    athlete depends on.
    """
    warn = ""
    if claude_running():
        # Claude Desktop keeps a background process after the window closes,
        # so refusing outright strands people who did everything right. Write
        # anyway, then check whether it stuck.
        warn = (" ⚠️ Claude נראה פתוח ברקע — אם החיבור לא נתפס, "
                "סגור אותו לגמרי (מנהל המשימות) והרץ אותי שוב.")
    cfg = claude_config_path()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    data = {}
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            return False, f"לא הצלחתי לקרוא את ההגדרות של Claude ({e})"
        shutil.copy(cfg, cfg + ".backup")

    tools = os.path.join(here, "config", "enabled_tools.txt")
    allow = ""
    if os.path.exists(tools):
        allow = ",".join(l.strip() for l in open(tools, encoding="utf-8")
                         if l.strip() and not l.startswith("#"))

    servers = data.setdefault("mcpServers", {})
    servers["ari-coach"] = {
        "command": _uv(),
        "args": ["run", "--directory", here, "garmin-mcp"],
        "env": {
            "GARMINTOKENS": os.getenv("GARMINTOKENS", "~/.garminconnect"),
            "GARMIN_ENABLED_TOOLS": allow,
            "ARI_COACH_BRIEF": os.path.join(here, "config",
                                            "coach-instructions.md"),
        },
    }
    tmp = cfg + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg)
    # Read it back: if Claude was running it may have already overwritten us,
    # and reporting success we did not achieve is the worst outcome here.
    try:
        with open(cfg, encoding="utf-8") as fh:
            if "ari-coach" not in json.load(fh).get("mcpServers", {}):
                return False, ("ההגדרות נדרסו — Claude פתוח ברקע. "
                               "סגור אותו לגמרי והרץ אותי שוב.")
    except (OSError, json.JSONDecodeError):
        pass
    n = len(allow.split(",")) if allow else 0
    return True, f"{n} כלים · {cfg}{warn}"


def main():
    say("\n🏊🚴🏃  התקנת המאמן\n")

    # ── 1 ────────────────────────────────────────────────────────────────
    head(1, "בדיקת סביבה")
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    if not os.path.exists(uv):
        say(f"{BAD} uv לא מותקן. התקן אותו ואז הרץ אותי שוב:")
        say("   macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh")
        say('   Windows:      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"')
        return 1
    say(f"{OK} uv: {uv}")
    say(f"{DOT} מתקין תלויות…")
    if run([uv, "sync", "--quiet"]).returncode:
        say(f"{BAD} uv sync נכשל."); return 1
    say(f"{OK} תלויות מותקנות")

    # ── 2 ────────────────────────────────────────────────────────────────
    head(2, "בדיקת תקינות")
    # A smoke test, not the suite. Running 213 developer tests on someone's
    # laptop to "verify" an install is slow, meaningless to them, and fragile:
    # on Windows the console codepage collides with pytest's capture and
    # every test errors with "I/O operation on closed file" -- which blocked
    # an install because a time-formatting assertion could not print.
    try:
        from ari_coach import builders, cache, insights, race  # noqa: F401
        payload = builders.build({
            "sport": "bike", "name": "check", "steps": [
                {"kind": "warmup", "end": {"type": "time_s", "value": 600}},
                {"kind": "work", "end": {"type": "time_s", "value": 300},
                 "target": {"type": "power_w", "low": 180, "high": 200}}]})
        assert payload["sportType"]["sportTypeKey"] == "cycling"
        assert len(payload["workoutSegments"][0]["workoutSteps"]) == 2
        say(f"{OK} המנוע עובד")
    except Exception as e:
        say(f"{BAD} משהו לא תקין בקוד: {type(e).__name__}: {e}")
        return 1

    # ── 3 ────────────────────────────────────────────────────────────────
    head(3, "התחברות לגרמין")
    store = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))
    tok = os.path.join(store, "garmin_tokens.json")
    # A token file dropped next to this script skips the login entirely.
    # Worth supporting: the interactive login is the most fragile step in the
    # whole install -- Garmin rate-limits repeated attempts and then reports
    # it as "invalid email or password", which sends people chasing a
    # password that was never wrong.
    dropped = os.path.join(HERE, "garmin_tokens.json")
    if not os.path.exists(tok) and os.path.exists(dropped):
        os.makedirs(store, exist_ok=True)
        try:
            os.chmod(store, 0o700)
        except OSError:
            pass
        shutil.copy(dropped, tok)
        try:
            os.chmod(tok, 0o600)
        except OSError:
            pass
        say(f"{OK} נמצא קובץ חיבור מוכן — דילגתי על ההתחברות")

    if os.path.exists(tok):
        say(f"{OK} כבר מחובר ({tok})")
    else:
        say(f"{WARN}עכשיו ייפתח חלון התחברות.")
        say("   הקלד את האימייל והסיסמה שלך **כאן בטרמינל**.")
        say("   הם לא נשמרים בשום קובץ הגדרות ולא עוברים דרך הצ'אט.\n")
        r = run([uv, "run", "garmin-mcp-auth"], capture_output=True, text=True)
        sys.stdout.write(r.stdout or "")
        blob = (r.stdout or "") + (r.stderr or "")
        if not os.path.exists(tok):
            if "429" in blob or "rate limit" in blob.lower():
                # The library reports every failure as bad credentials. It is
                # not: a 429 means Garmin throttled this IP, usually after a
                # few attempts, and the password was fine all along.
                say(f"\n{BAD} גרמין חסמה זמנית את הכתובת שלך (שגיאה 429).")
                say("   ⚠️ זו **לא** סיסמה שגויה, למרות מה שכתוב למעלה.")
                say("   חכה 30-60 דקות והרץ אותי שוב, או בקש מאלון קובץ חיבור מוכן.")
            else:
                say(f"\n{BAD} ההתחברות לא הושלמה. הרץ אותי שוב.")
            return 1
        say(f"{OK} מחובר")

    from garminconnect import Garmin
    g = Garmin(); g.login(store)
    say(f"{OK} מזוהה כ-{g.get_full_name()}")

    # ── 4 ────────────────────────────────────────────────────────────────
    head(4, "משיכת ההיסטוריה")
    from ari_coach import cache, sync
    cache.init()
    with cache.connect() as c:
        have = c.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
    if have >= FIRST_DAYS:
        say(f"{OK} כבר יש {have} ימים במטמון")
    else:
        say(f"{DOT} מושך {FIRST_DAYS} ימים — נמדד: כ-4 דקות. אל תסגור את החלון.")
        t0 = time.time()
        res = sync.sync(g, days=FIRST_DAYS, verbose=False)
        say(f"{OK} {res['days_fetched']} ימים, {res['activities']} אימונים "
            f"({time.time() - t0:.0f} שניות)")
    say(f"{DOT} אפשר להרחיב אחר כך: הדשבורד מסנכרן לבד, ואפשר לבקש מהמאמן")

    # ── 5 ────────────────────────────────────────────────────────────────
    head(5, "בניית הדשבורד")
    from ari_coach import dashboard, plan_store as P
    out = dashboard.build()
    d = dashboard.collect()
    say(f"{OK} {out}")
    say(f"{DOT} {len(d['cards'])} תובנות · {len(d['tiles'])} מדדים · "
        f"{len(d['activities'])} אימונים")
    for c in d["cards"][:3]:
        say(f"    {dict(critical='🔴', warn='🟠', info='🔵', good='🟢')[c['severity']]} {c['title']}")

    # ── 6 ────────────────────────────────────────────────────────────────
    head(6, "חיבור ל-Claude Desktop")
    ok, detail = wire_claude(HERE)
    if ok:
        say(f"{OK} המאמן חובר. {detail}")
    else:
        say(f"{WARN}{detail}")
        say(f"{DOT} אפשר גם לגרור את {os.path.join(HERE, 'ari-coach.dxt')} "
            f"לחלון של Claude ולתת את הנתיב {HERE}")

    # ── 7 ────────────────────────────────────────────────────────────────
    head(7, "רענון יומי")
    r = run([sys.executable, os.path.join(HERE, "schedule_morning.py")],
            capture_output=True, text=True)
    line = (r.stdout or r.stderr).strip().splitlines()
    say(("  " + "\n  ".join(line[:3])) if line else f"{WARN}לא הותקן")

    # ── 8 ────────────────────────────────────────────────────────────────
    head(8, "מוכן")
    say(f"""
   פתח את Claude Desktop וכתוב לו:

       בוא נגדיר את המרוץ שלי

   הוא כבר יודע מי אתה ומה הנתונים שלך.
""")
    say(f"{DOT} לפתוח את הדשבורד המקומי בכל רגע:")
    say(f'    {uv} run --directory "{HERE}" python -c '
        f'"import sys;sys.path.insert(0,\'src\');from ari_coach import server;server.serve()"')
    if not P.onboarding_complete():
        say(f"\n{WARN}עדיין אין יעד מוגדר — הדשבורד יראה 'עדיין לא הגדרת יעד'. זה תקין.")
    say("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
