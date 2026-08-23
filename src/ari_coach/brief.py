"""Build the athlete's half of the coaching brief from his own data.

The generic coaching principles ship in the package. Everything specific to
one person -- his race, his heart rate ceilings, what his training actually
looks like right now -- is computed here at server startup and appended.

Two reasons it works this way rather than being written into a file. The
repository stays free of anyone's health record, which is what makes it
publishable and therefore updatable without touching his machine. And a
computed brief cannot drift: a hardcoded "bike is his biggest discipline"
was true in June and false by August, and nobody would have noticed.
"""

from datetime import date, timedelta

from . import cache, insights as I, plan_store as P, race as R


def _fmt_hours(h):
    return f"{h:.1f}"


def athlete_section():
    """The whole personal block, or a short note when there is nothing yet."""
    try:
        return _build()
    except Exception as e:                    # never take the server down for this
        return f"\n\n<!-- athlete brief unavailable: {type(e).__name__}: {e} -->\n"


def _build():
    profile = P.get_profile()
    name = (profile.get("athlete") or "").strip()
    lines = ["", "---", "", "# מי האתלט הזה — מחושב מהנתונים שלו, לא כתוב מראש", ""]

    if not profile:
        lines += [
            "‏**עדיין לא נעשה אונבורדינג.** אין יעד, אין מרוץ, אין זמינות.",
            "**זה הצעד הראשון:** שאל אותו על המרוץ, התאריך, הזמן שהוא רוצה",
            "לשבור, וכמה שעות ואילו ימים יש לו — ואז `save_athlete_profile`.",
            "🔴 **בלי `goal_seconds` אין פאנל מרוץ ואין מול מה למדוד פיצולים.**",
        ]

    with cache.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT sport, max_hr FROM activities WHERE max_hr IS NOT NULL")]
        since6 = (date.today() - timedelta(weeks=6)).isoformat()
        since12 = (date.today() - timedelta(weeks=12)).isoformat()
        recent = [dict(r) for r in c.execute(
            "SELECT sport, ROUND(SUM(duration_s)/3600.0,1) h, COUNT(*) n"
            " FROM activities WHERE date >= ? GROUP BY sport ORDER BY h DESC", (since6,))]
        prior = {r["sport"]: r["h"] for r in c.execute(
            "SELECT sport, ROUND(SUM(duration_s)/3600.0,1) h FROM activities"
            " WHERE date >= ? AND date < ? GROUP BY sport", (since12, since6))}
        last = c.execute(
            "SELECT vo2max, load_acute, load_chronic, load_status FROM daily"
            " WHERE vo2max IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
        rp = cache.get_metric(c, "race_predictions")
        sync = cache.sync_status(c)

    # ── the goal ─────────────────────────────────────────────────────────
    if profile.get("race"):
        d = P.days_to_race()
        lines += [f"‏**היעד:** {profile['race']}"
                  + (f" · **בעוד {d} ימים**" if d is not None else "")
                  + (f" · {profile.get('goal')}" if profile.get("goal") else ""), ""]

    # ── heart rate, per sport, because one ceiling cannot serve three ────
    if rows:
        m = I.max_hr_by_sport(rows, profile)
        he = {"run": "ריצה", "bike": "אופניים", "swim": "שחייה"}
        lines += ["‏**תקרת דופק לפי ענף** (‏80% ממנה = הגבול של 'קל'):",
                  "  " + " · ".join(f"{he[k]} **{m[k]:.0f}**" for k in ("run", "bike", "swim")),
                  "⚠️ **אל תשווה דופק בין ענפים.** אותו מספר הוא קל בענף אחד וקשה באחר.", ""]

    # ── what his training actually looks like now ────────────────────────
    if recent:
        tot = sum(r["h"] or 0 for r in recent)
        he = {"run": "🏃 ריצה", "bike": "🚴 אופניים", "swim": "🏊 שחייה",
              "strength": "🏋 כוח", "other": "• אחר"}
        lines += [f"‏**ששת השבועות האחרונים — {_fmt_hours(tot)} שעות סה\"כ:**"]
        for r in recent:
            was = prior.get(r["sport"])
            trend = ""
            if was and was >= 1.0 and r["h"] is not None:
                pct = (r["h"] - was) / was * 100
                if abs(pct) >= 30:
                    trend = f"  ← {'עלייה' if pct > 0 else '**ירידה**'} של {abs(pct):.0f}% מהתקופה שלפני"
            lines.append(f"  {he.get(r['sport'], r['sport'])}: "
                         f"{_fmt_hours(r['h'] or 0)}ש' ב-{r['n']} אימונים{trend}")
        lines += ["⚠️ **נפח בשעות ולא בק\"מ** — אימוני אופניים מקורים רושמים "
                  "‏0 ק\"מ, וגרף ק\"מ מוחק אותם.", ""]

    # ── anchors ──────────────────────────────────────────────────────────
    anchors = []
    if profile.get("ftp_watts"):
        anchors.append(f"FTP {profile['ftp_watts']:.0f}W")
    if profile.get("css_sec_per_100m"):
        anchors.append(f"CSS {R.mmss(profile['css_sec_per_100m'])}/100מ'")
    if last and last["vo2max"]:
        anchors.append(f"VO2max {last['vo2max']:.1f}")
    if last and last["load_acute"] and last["load_chronic"]:
        anchors.append(f"עומס {last['load_acute']/last['load_chronic']:.2f}"
                       + (f" ({last['load_status']})" if last["load_status"] else ""))
    if rp:
        anchors.append("תחזיות גרמין: "
                       + " · ".join(f"{k[4:]} {R.mmss(v)}" for k, v in
                                    (("time5K", rp.get("time5K")),
                                     ("time10K", rp.get("time10K"))) if v))
    if anchors:
        lines += ["‏**עוגנים:** " + " · ".join(anchors), ""]

    # ── his races, with the normalisation that stops the lie ─────────────
    pb = _pb_race()
    if pb:
        lines += [f"‏**השיא שלו:** {R.mmss(pb.total_seconds)} ({pb.date})"]
        for leg in pb.legs:
            lines.append(f"  {R.LEG_ICON[leg.leg]} {R.LEG_HE[leg.leg]}: "
                         f"{R.mmss(leg.seconds)} · {leg.rate()['text']}")
        course = (profile.get("distance") or "sprint").lower()
        if course in R.COURSES:
            norm, notes = R.normalise(pb, course)
            if notes:
                lines += [f"⚠️ **מנורמל ל{R.COURSES[course]['name']}: "
                          f"{R.mmss(norm.total_seconds)}** — המסלול שבו נקבע השיא היה שונה."]
        lines += ["🔴 **לעולם אל תשווה מרוצים בזמן גולמי.** הפרש של דקות בין שני",
                  "מרוצים הוא לרוב הבדל מסלול ולא שיפור כושר. יש `race.py` עם נרמול.", ""]

    # ── what the engine is flagging right now ────────────────────────────
    try:
        with cache.connect() as c:
            cards = I.compute(c, profile=profile)[:3]
        if cards:
            icon = {"critical": "🔴", "warn": "🟠", "info": "🔵", "good": "🟢"}
            lines += ["‏**מה שהמנוע מסמן עכשיו:**"]
            lines += [f"  {icon[k.severity]} **{k.title}** — {k.evidence}" for k in cards]
            lines += [""]
    except Exception:
        pass

    if sync.get("last_sync"):
        lines += [f"<!-- מטמון עודכן לאחרונה: {sync['last_sync']} -->"]
    return "\n".join(lines)


def _pb_race():
    from . import dashboard
    try:
        return dashboard.load_pb_race()
    except Exception:
        return None
