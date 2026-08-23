"""Turn the athlete's numbers into things worth doing.

A dashboard that prints "Form -26" makes the reader do the interpretation.
The one this is modelled on says "back off for a few days -- fatigue (92) is
well ahead of fitness (65), which is deeper than a normal training dip". Same
data, and only the second is useful.

Every rule here is plain Python over the local cache: no model call, so the
cards are free, instant, and correct even if he has not opened a chat in a
week. The coach can add its own cards on top; it cannot replace these.

The hard constraint on every rule is the brake. A threshold that fires on two
data points is noise wearing a severity colour, and a card that cries wolf
costs more credibility than the insight was worth. So each rule states its
minimum sample and returns nothing below it -- silence is a valid output.
"""

from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta

CRITICAL, WARN, INFO, GOOD = "critical", "warn", "info", "good"
_RANK = {CRITICAL: 0, WARN: 1, INFO: 2, GOOD: 3}

EASY_CEILING = 0.80

# Sport offsets from the athlete's anchor max HR, in bpm. Heart rate runs
# lower on the bike (seated, larger muscle mass at lower cadence) and lower
# again in the water (prone, cool, hydrostatic pressure on the chest).
SPORT_HR_OFFSET = {"run": 0, "bike": -7, "swim": -12}

# A max HR needs a session where he actually went hard. Below this many
# HR-bearing sessions the observed maximum is just the hardest of a few easy
# days, so we fall back to the anchor rather than trust it.
MIN_SESSIONS_FOR_MAX = 6
ANCHOR_FALLBACK = 190.0


def max_hr_by_sport(rows, profile=None):
    """Per-sport max HR, derived from this athlete rather than hard-coded.

    Two traps this avoids. Hard-coding one athlete's numbers silently
    mis-scores everyone else: Ari's thresholds called 45% of Alon's sessions
    easy where Alon's own numbers say 9%. And taking each sport's observed
    maximum at face value fails the other way -- Alon's highest swim HR is
    114 because he only ever swims easy, and a ceiling built on that would
    reject almost every swim as hard.

    So: anchor on the highest max seen in the sport he actually races hard
    in (or the profile's stated max HR), then apply physiological offsets.
    """
    by_sport = {}
    for r in rows:
        hr = r.get("max_hr")
        if hr and r.get("sport"):
            b = by_sport.setdefault(r["sport"], {"max": 0.0, "n": 0})
            b["max"] = max(b["max"], float(hr))
            b["n"] += 1

    stated = (profile or {}).get("max_hr")
    if stated:
        anchor = float(stated)
    else:
        # The anchor is a RUNNING-equivalent max, because the offsets below
        # are defined relative to running. Taking the highest number across
        # all sports and calling it the anchor mixes two reference points:
        # for an athlete whose pool max exceeds his running max, it would
        # hand running a ceiling derived from swimming.
        run = by_sport.get("run")
        if run and run["n"] >= MIN_SESSIONS_FOR_MAX:
            anchor = run["max"]
        else:
            # No credible running data: convert another sport back to a
            # running equivalent by removing its offset.
            equivalents = [b["max"] - SPORT_HR_OFFSET[sp]
                           for sp, b in by_sport.items()
                           if sp in SPORT_HR_OFFSET and b["n"] >= MIN_SESSIONS_FOR_MAX]
            anchor = max(equivalents) if equivalents else ANCHOR_FALLBACK

    out = {}
    for sport, offset in SPORT_HR_OFFSET.items():
        derived = anchor + offset
        seen = by_sport.get(sport)
        # If he has genuinely gone harder in this sport than the offset
        # predicts, believe the measurement -- it is evidence, not noise.
        if seen and seen["n"] >= MIN_SESSIONS_FOR_MAX and seen["max"] > derived:
            derived = seen["max"]
        out[sport] = derived
    return out


@dataclass
class Card:
    key: str
    severity: str
    title: str
    evidence: str
    action: str = ""
    author: str = "engine"
    metric: dict = field(default_factory=dict)

    def dict(self):
        return asdict(self)


def _fmt(v, digits=0):
    if v is None:
        return "—"
    return f"{v:.{digits}f}" if digits else f"{round(v):,}"


def _hours(rows, sport=None):
    return sum((r["duration_s"] or 0) for r in rows
               if sport is None or r["sport"] == sport) / 3600.0


# ── rules ─────────────────────────────────────────────────────────────────
# Each takes the prepared context and returns a Card or None.

def rule_load(ctx):
    """Garmin's own acute/chronic ratio. Cross-sport, so swimming counts."""
    d = ctx["recent_daily"]
    pts = [r for r in d if r.get("load_acute") and r.get("load_chronic")]
    if len(pts) < 5:                                   # brake
        return None
    last = pts[-1]
    a, c = last["load_acute"], last["load_chronic"]
    ratio = a / c
    status = last.get("load_status") or ""
    m = {"acute": a, "chronic": c, "ratio": round(ratio, 2), "status": status}
    if ratio >= 1.5:
        return Card("load", CRITICAL, "העומס קפץ מהר מדי",
                    f"עומס אקוטי {_fmt(a)} מול כרוני {_fmt(c)} — יחס {ratio:.2f}. "
                    f"גרמין מסמנת {status or 'עומס גבוה'}.",
                    "שבוע קל: לחתוך את הנפח בשליש ולוותר על אימון האיכות השני.", metric=m)
    if ratio <= 0.75:
        return Card("load", WARN, "העומס נמוך מהכושר שבנית",
                    f"אקוטי {_fmt(a)} מול כרוני {_fmt(c)} — יחס {ratio:.2f}. "
                    "זה קצב של תחזוקה יורדת, לא של בנייה.",
                    "להוסיף אימון אחד בשבוע, או להאריך את הארוך ב-15 דקות.", metric=m)
    return Card("load", GOOD, "העומס במקום הנכון",
                f"אקוטי {_fmt(a)} מול כרוני {_fmt(c)} — יחס {ratio:.2f}"
                f"{', ' + status if status else ''}.",
                "להמשיך. זה החלון שבו בונים.", metric=m)


def rule_sport_collapse(ctx):
    """A discipline that quietly disappeared.

    The failure mode this catches is specific: total volume can look merely
    low while one leg has gone to zero. For a triathlete that is the
    difference between being tired and being unprepared.
    """
    recent, prior = ctx["acts_recent"], ctx["acts_prior"]
    if ctx["weeks_recent"] < 3 or ctx["weeks_prior"] < 3:      # brake
        return None
    worst = None
    for sport in ("bike", "swim", "run"):
        h_now = _hours(recent, sport) / ctx["weeks_recent"]
        h_before = _hours(prior, sport) / ctx["weeks_prior"]
        if h_before < 1.0:                                     # nothing to fall from
            continue
        drop = 1 - (h_now / h_before) if h_before else 0
        if drop > 0.5 and (worst is None or drop > worst[1]):
            worst = (sport, drop, h_now, h_before)
    if not worst:
        return None
    sport, drop, now, before = worst
    # Two forms: Hebrew needs the definite article in the headline
    # ("the bike almost disappeared") and drops it in the prescription
    # ("put a bike session back"), so one string cannot serve both.
    DEF = {"bike": "האופניים", "swim": "השחייה", "run": "הריצה"}
    BARE = {"bike": "אופניים", "swim": "שחייה", "run": "ריצה"}
    VERB = {"bike": "נעלמו", "swim": "נעלמה", "run": "נעלמה"}
    share = ctx.get("leg_share", {}).get(sport)
    tail = (f" והם {share:.0f}% מזמן המרוץ שלך." if share else "")
    return Card(f"collapse_{sport}", CRITICAL,
                f"{DEF[sport]} כמעט {VERB[sport]}",
                f"{now:.1f} שעות בשבוע בששת השבועות האחרונים, מול "
                f"{before:.1f} לפני כן — ירידה של {drop*100:.0f}%.{tail}",
                f"להחזיר אימון {BARE[sport]} קבוע לשבוע לפני שמעלים כל דבר אחר.",
                metric={"sport": sport, "now": round(now, 1),
                        "before": round(before, 1), "drop_pct": round(drop * 100)})


def rule_easy_hard(ctx):
    """The 80/20 check, per sport and against this athlete's own ceilings."""
    ceilings = ctx["max_hr"]
    rows = [r for r in ctx["acts_recent"]
            if r.get("avg_hr") and r["sport"] in ceilings]
    if len(rows) < 8:                                          # brake
        return None
    easy = sum(1 for r in rows
               if r["avg_hr"] <= ceilings[r["sport"]] * EASY_CEILING)
    pct = 100.0 * easy / len(rows)
    m = {"easy_pct": round(pct), "n": len(rows)}
    if pct < 60:
        return Card("easy_hard", WARN, "הימים הקלים לא באמת קלים",
                    f"רק {pct:.0f}% מ-{len(rows)} האימונים האחרונים היו מתחת "
                    f"לתקרה האירובית של הענף שלהם. הרף הוא בערך 80%.",
                    "להוריד את הקצב בימים הקלים עד שהדופק יורד מתחת לתקרה — "
                    "גם אם זה מרגיש אטי מדי.", metric=m)
    if pct > 92:
        return Card("easy_hard", INFO, "כמעט הכל קל",
                    f"{pct:.0f}% מהאימונים מתחת לתקרה האירובית. בסיס זה טוב, "
                    "אבל בלי עצימות אין מהירות מרוץ.",
                    "אימון איכות אחד בשבוע, קצר.", metric=m)
    return Card("easy_hard", GOOD, "חלוקת העצימות בריאה",
                f"{pct:.0f}% מהאימונים קלים — קרוב ל-80/20.",
                "לשמור על זה.", metric=m)


def rule_ramp(ctx):
    """Week-over-week volume change, in hours."""
    w = ctx["weekly_hours"]
    if len(w) < 4:                                             # brake
        return None
    last, prev = w[-1], w[-2]
    if prev < 1.0:
        return None
    change = (last - prev) / prev
    m = {"last_h": round(last, 1), "prev_h": round(prev, 1),
         "change_pct": round(change * 100)}
    if change > 0.30:
        return Card("ramp", WARN, "קפיצת נפח חדה",
                    f"{last:.1f} שעות השבוע מול {prev:.1f} בשבוע שעבר — "
                    f"עלייה של {change*100:.0f}%.",
                    "הכלל הוא עד ~10% לשבוע. לקבע את הנפח הזה שבוע נוסף "
                    "לפני שמעלים שוב.", metric=m)
    return None


def rule_recovery(ctx):
    """HRV and resting HR against his own 28-day baseline."""
    d = [r for r in ctx["recent_daily"] if r.get("hrv_last") or r.get("rhr")]
    if len(d) < 14:                                            # brake
        return None
    hrv = [r["hrv_last"] for r in d if r.get("hrv_last")]
    rhr = [r["rhr"] for r in d if r.get("rhr")]
    if len(hrv) < 10 or len(rhr) < 10:
        return None
    base_hrv = sum(hrv[:-3]) / max(1, len(hrv[:-3]))
    base_rhr = sum(rhr[:-3]) / max(1, len(rhr[:-3]))
    cur_hrv = sum(hrv[-3:]) / len(hrv[-3:])
    cur_rhr = sum(rhr[-3:]) / len(rhr[-3:])
    m = {"hrv": round(cur_hrv), "hrv_base": round(base_hrv),
         "rhr": round(cur_rhr), "rhr_base": round(base_rhr)}
    if cur_hrv < base_hrv * 0.90 and cur_rhr > base_rhr + 3:
        return Card("recovery", CRITICAL, "הגוף מסמן עייפות",
                    f"HRV {cur_hrv:.0f} מול בסיס {base_hrv:.0f}, ודופק מנוחה "
                    f"{cur_rhr:.0f} מול {base_rhr:.0f}. שני הסימנים יחד.",
                    "יומיים קלים באמת. אם זה לא חוזר — לבדוק שינה ולחץ, "
                    "לא להוסיף אימון.", metric=m)
    if cur_hrv < base_hrv * 0.92:
        return Card("recovery", WARN, "HRV מתחת לבסיס",
                    f"{cur_hrv:.0f} מול בסיס של {base_hrv:.0f} ב-28 יום.",
                    "לשמור על היום הקל קל. לא להוסיף עצימות השבוע.", metric=m)
    return Card("recovery", GOOD, "ההתאוששות יציבה",
                f"HRV {cur_hrv:.0f} מול בסיס {base_hrv:.0f}, "
                f"דופק מנוחה {cur_rhr:.0f}.",
                "אפשר לשאת את העומס המתוכנן.", metric=m)


def rule_transitions(ctx):
    """The cheapest seconds in the sport, and his are expensive."""
    tgt = ctx.get("race_targets")
    if not tgt:
        return None
    free = [l for l in tgt["legs"] if l["free"] and l["gap_seconds"] > 30]
    if not free:
        return None
    total = sum(l["gap_seconds"] for l in free)
    worst = max(free, key=lambda l: l["gap_seconds"])
    from . import race as R
    enough = total >= tgt["needed_seconds"] > 0
    return Card("transitions", WARN if not enough else CRITICAL,
                "המעברים הם הזמן הזול ביותר שיש לך",
                f"{worst['he']} לקח {worst['pb_text']} — {worst['share_pct']}% "
                f"מהמרוץ. מעבר מתורגל בספרינט הוא 1:30–2:00. "
                f"סך הכל יש שם {R.mmss(total)}."
                + (f" זה לבדו יותר מה-{tgt['needed_text']} שאתה צריך."
                   if enough else ""),
                "לתרגל מעבר פעם בשבוע: אופניים מוכנים, נעליים פתוחות, "
                "מספר על הכידון. עשר חזרות ואתה שם.",
                metric={"seconds_available": round(total)})


def rule_race_gap(ctx):
    """Where he stands against the goal, on the course he is actually racing."""
    tgt = ctx.get("race_targets")
    if not tgt:
        return None
    if tgt["already_achievable"]:
        return Card("race_gap", GOOD, "היעד בהישג יד מהכושר הנוכחי",
                    f"השיא שלך מנורמל למסלול הוא {tgt['normalised_pb_text']}, "
                    f"מול יעד {tgt['goal_text']}.",
                    "היעד שמרני. שווה לכוון גבוה יותר.",
                    metric={"needed": 0})
    return Card("race_gap", INFO, "איפה אתה עומד מול היעד",
                f"השיא שלך על {tgt['course']} הוא {tgt['normalised_pb_text']} "
                f"(אחרי נרמול מרחק), והיעד {tgt['goal_text']} — "
                f"צריך למצוא {tgt['needed_text']}.",
                "המעברים והאופניים הם המקום. הריצה שלך כבר על התחזית.",
                metric={"needed_seconds": round(tgt["needed_seconds"])})


def rule_vo2(ctx):
    d = [r["vo2max"] for r in ctx["recent_daily"] if r.get("vo2max")]
    if len(d) < 21:                                            # brake
        return None
    first, last = d[0], d[-1]
    delta = last - first
    m = {"now": round(last, 1), "then": round(first, 1), "delta": round(delta, 1)}
    if delta <= -0.7:
        return Card("vo2", WARN, "VO2max יורד",
                    f"{first:.1f} → {last:.1f} בחלון הנוכחי.",
                    "בדרך כלל זה נפח שירד, לא כושר שאבד. להחזיר עקביות.", metric=m)
    if delta >= 0.7:
        return Card("vo2", GOOD, "VO2max עולה",
                    f"{first:.1f} → {last:.1f}.", "מה שאתה עושה עובד.", metric=m)
    return None


RULES = (rule_load, rule_sport_collapse, rule_transitions, rule_race_gap,
         rule_recovery, rule_easy_hard, rule_ramp, rule_vo2)


# ── context ───────────────────────────────────────────────────────────────

def build_context(conn, race_targets=None, recent_weeks=6, prior_weeks=6,
                  profile=None):
    today = date.today()
    r_start = (today - timedelta(weeks=recent_weeks)).isoformat()
    p_start = (today - timedelta(weeks=recent_weeks + prior_weeks)).isoformat()

    acts = [dict(r) for r in conn.execute(
        "SELECT date, sport, duration_s, distance_m, avg_hr, avg_power,"
        " training_load FROM activities WHERE date >= ? ORDER BY date", (p_start,))]
    daily = [dict(r) for r in conn.execute(
        "SELECT date, hrv_last, rhr, sleep_score, readiness_score, vo2max,"
        " load_acute, load_chronic, load_status FROM daily"
        " WHERE date >= ? ORDER BY date", (p_start,))]

    weekly = {}
    for a in acts:
        wk = datetime.fromisoformat(a["date"]).strftime("%Y-%W")
        weekly[wk] = weekly.get(wk, 0) + (a["duration_s"] or 0) / 3600.0

    leg_share = {}
    if race_targets:
        for l in race_targets["legs"]:
            if l["leg"] in ("swim", "bike", "run"):
                leg_share[l["leg"]] = l["share_pct"]

    all_hr = [dict(r) for r in conn.execute(
        "SELECT sport, max_hr FROM activities WHERE max_hr IS NOT NULL")]

    return {
        "max_hr": max_hr_by_sport(all_hr, profile),
        "acts_recent": [a for a in acts if a["date"] >= r_start],
        "acts_prior": [a for a in acts if a["date"] < r_start],
        "weeks_recent": recent_weeks, "weeks_prior": prior_weeks,
        "recent_daily": [d for d in daily if d["date"] >= r_start],
        "all_daily": daily,
        "weekly_hours": [weekly[k] for k in sorted(weekly)],
        "race_targets": race_targets,
        "leg_share": leg_share,
    }


def compute(conn, race_targets=None, profile=None):
    """Run every rule. Returns cards sorted worst-first."""
    ctx = build_context(conn, race_targets, profile=profile)
    cards = []
    for rule in RULES:
        try:
            card = rule(ctx)
        except Exception as e:                     # a broken rule must not blank the page
            card = Card(getattr(rule, "__name__", "rule"), INFO,
                        "כלל תובנה נכשל",
                        f"{type(e).__name__}: {e}",
                        "זה באג — שווה לדווח.")
        if card:
            cards.append(card)
    cards.sort(key=lambda c: _RANK.get(c.severity, 9))
    return cards


def merge(engine_cards, coach_cards):
    """Coach cards sit alongside computed ones; neither silently wins.

    A coach card with the same key annotates rather than replaces, so a
    model's opinion can never quietly delete a measurement.
    """
    out = [c if isinstance(c, dict) else c.dict() for c in engine_cards]
    keys = {c["key"] for c in out}
    for c in coach_cards or []:
        c = dict(c, author="coach")
        if c.get("key") in keys:
            c["key"] = f"{c['key']}_coach"
        out.append(c)
    out.sort(key=lambda c: _RANK.get(c.get("severity"), 9))
    return out
