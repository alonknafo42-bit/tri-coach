"""Build the Hebrew dashboard as one self-contained HTML file.

Volume is charted in HOURS. This athlete's indoor cycling records zero
distance -- 56 hours of it against 0 km -- so a kilometre chart would draw
a third of his training as nothing at all. Kilometres appear as a secondary
label where they exist.
"""

import json
import os
import webbrowser
from datetime import date, datetime, timedelta

from . import cache, insights as I, plan_store as P, race as R

SPORT_HE = {"swim": "שחייה", "bike": "אופניים", "run": "ריצה",
            "strength": "כוח", "other": "אחר"}
SPORT_ICON = {"swim": "🏊", "bike": "🚴", "run": "🏃", "strength": "🏋", "other": "•"}
SPORT_COLOR = {"swim": "#4aa8d8", "bike": "#f0b866", "run": "#7ec98f",
               "strength": "#b48ce0", "other": "#7d8590"}


def sparkline(values, width=104, height=26, pad=3):
    """A tiny inline SVG trend line.

    Drawn in Python rather than by a chart library: the reference puts a
    sparkline under every number, and eight more Chart.js instances to draw
    eight ~100px lines is a lot of runtime for a shape that is one path.
    Also means the tiles render even before the chart script runs.
    """
    pts = [v for v in values if isinstance(v, (int, float))]
    if len(pts) < 3:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(pts)
    step = (width - 2 * pad) / (n - 1)
    coords = [(pad + i * step,
               height - pad - (v - lo) / span * (height - 2 * pad))
              for i, v in enumerate(pts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{coords[0][0]:.1f},{height} " + line + f" {coords[-1][0]:.1f},{height}"
    lx, ly = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" aria-hidden="true">'
            f'<polygon points="{area}" fill="currentColor" opacity=".13"/>'
            f'<polyline points="{line}" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.1" fill="currentColor"/></svg>')


def _series(daily, key):
    return [d.get(key) for d in daily]


def _tile(label, value, detail, series, tone):
    return {"label": label, "value": value, "detail": detail,
            "spark": sparkline(series), "tone": tone}


def _pace(sec):
    if not sec or sec <= 0:
        return None
    return f"{int(sec) // 60}:{int(sec) % 60:02d}"


def collect(weeks=13, live=False):
    since = (date.today() - timedelta(days=weeks * 7)).isoformat()
    with cache.connect() as c:
        weekly = [dict(r) for r in c.execute(
            "SELECT strftime('%Y-%W', date) wk, sport,"
            " ROUND(SUM(duration_s)/3600.0,2) hours, ROUND(SUM(distance_m)/1000.0,1) km,"
            " COUNT(*) n FROM activities WHERE date >= ?"
            " GROUP BY wk, sport ORDER BY wk", (since,))]
        daily = [dict(r) for r in c.execute(
            "SELECT date, hrv_last, hrv_7d, rhr, sleep_score, readiness_score,"
            " vo2max, load_acute, load_chronic, load_status"
            " FROM daily WHERE date >= ? ORDER BY date", (since,))]
        acts = [dict(r) for r in c.execute(
            "SELECT date, sport, type, name, distance_m, duration_s, avg_hr,"
            " avg_power, norm_power, avg_speed, training_load"
            " FROM activities WHERE date >= ? ORDER BY date DESC LIMIT 40", (since,))]
        totals = [dict(r) for r in c.execute(
            "SELECT sport, COUNT(*) n, ROUND(SUM(duration_s)/3600.0,1) hours,"
            " ROUND(SUM(distance_m)/1000.0,1) km FROM activities"
            " WHERE date >= ? GROUP BY sport ORDER BY hours DESC", (since,))]
        rp = cache.get_metric(c, "race_predictions")
        sync = cache.sync_status(c)

    # The plan going forward, not just this week: a training plan that is
    # only visible seven days at a time is a calendar, not a plan.
    plan_days = P.get_plan()["days"]
    horizon = []
    for i in range(0, 21):
        d = (date.today() + timedelta(days=i)).isoformat()
        entry = plan_days.get(d)
        if not entry:
            continue
        horizon.append({
            **entry, "date": d,
            "pushable": bool(entry.get("workout")),
            "on_watch": bool(entry.get("garmin_workout_id")),
            "in_days": i,
        })

    # planned week alongside what was actually done
    week = P.week_of()
    by_date = {}
    for a in acts:
        by_date.setdefault(a["date"], []).append(a)
    for d in week:
        d["actual"] = by_date.get(d["date"], [])
        d["is_today"] = d["date"] == date.today().isoformat()

    for a in acts:
        a["sport_he"] = SPORT_HE.get(a["sport"], a["sport"])
        a["icon"] = SPORT_ICON.get(a["sport"], "•")
        a["mins"] = round((a["duration_s"] or 0) / 60)
        a["km"] = round((a["distance_m"] or 0) / 1000, 2) if a["distance_m"] else None
        sp = a.get("avg_speed") or 0
        if a["sport"] == "run" and sp:
            a["rate"] = f"{_pace(1000 / sp)}/ק\"מ"
        elif a["sport"] == "swim" and sp:
            a["rate"] = f"{_pace(100 / sp)}/100מ'"
        elif a["sport"] == "bike":
            w = a.get("norm_power") or a.get("avg_power")
            a["rate"] = f"{round(w)}W" if w else (f"{round(sp*3.6,1)} קמ\"ש" if sp else None)
        else:
            a["rate"] = None

    profile = P.get_profile()

    # ---- race targets, if he has told us what he is chasing -------------
    targets = sens = pb = None
    pb_race = load_pb_race()
    goal = profile.get("goal_seconds")
    if pb_race and goal:
        course = (profile.get("distance") or "sprint").lower()
        course = course if course in R.COURSES else "sprint"
        targets = R.target_splits(pb_race, float(goal), course)
        sens = R.sensitivity(pb_race, course)
        pb = R.as_dict(pb_race)

    # ---- insight cards --------------------------------------------------
    with cache.connect() as c:
        engine_cards = I.compute(c, race_targets=targets, profile=profile)
    cards = I.merge(engine_cards, P.get_insights())

    # ---- metric tiles with sparklines -----------------------------------
    last = daily[-1] if daily else {}
    prev = daily[-8] if len(daily) > 8 else {}
    tiles = []
    la, lc = last.get("load_acute"), last.get("load_chronic")
    tiles.append(_tile("עומס אקוטי", f"{la:.0f}" if la else "—",
                       "‏7 ימים", _series(daily, "load_acute"), "warn"))
    tiles.append(_tile("עומס כרוני", f"{lc:.0f}" if lc else "—",
                       "‏28 ימים", _series(daily, "load_chronic"), "cool"))
    tiles.append(_tile("יחס", f"{la/lc:.2f}" if (la and lc) else "—",
                       last.get("load_status") or "—",
                       [ (d["load_acute"]/d["load_chronic"])
                         if (d.get("load_acute") and d.get("load_chronic")) else None
                         for d in daily ], "accent"))
    tiles.append(_tile("נפח 7 ימים",
                       f"{sum((a['duration_s'] or 0) for a in acts if a['date'] >= (date.today()-timedelta(days=7)).isoformat())/3600:.1f} ש'",
                       "כל הענפים", [w["hours"] for w in weekly], "run"))
    tiles.append(_tile("HRV לילה", f"{last.get('hrv_last'):.0f}" if last.get("hrv_last") else "—",
                       f"ממוצע 7 ימים {last.get('hrv_7d') or '—'}",
                       _series(daily, "hrv_last"), "cool"))
    tiles.append(_tile("דופק מנוחה", f"{last.get('rhr'):.0f}" if last.get("rhr") else "—",
                       _delta_text(last.get("rhr"), prev.get("rhr")),
                       _series(daily, "rhr"), "warn"))
    tiles.append(_tile("ציון שינה", f"{last.get('sleep_score'):.0f}" if last.get("sleep_score") else "—",
                       _delta_text(last.get("sleep_score"), prev.get("sleep_score")),
                       _series(daily, "sleep_score"), "swim"))
    tiles.append(_tile("VO2max", f"{last.get('vo2max'):.1f}" if last.get("vo2max") else "—",
                       _delta_text(last.get("vo2max"), prev.get("vo2max"), 1),
                       _series(daily, "vo2max"), "good"))

    return {
        "generated": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "profile": profile,
        "days_to_race": P.days_to_race(),
        "onboarded": P.onboarding_complete(),
        "week": week, "weekly": weekly, "daily": daily,
        "activities": acts, "totals": totals,
        "race_predictions": rp, "sync": sync,
        "tiles": tiles, "cards": cards,
        "live": live, "horizon": horizon,
        "race": {"pb": pb, "targets": targets, "sensitivity": sens},
        "sport_he": SPORT_HE, "sport_color": SPORT_COLOR, "sport_icon": SPORT_ICON,
    }


def _delta_text(now, before, digits=0):
    if now is None or before is None:
        return "—"
    d = now - before
    if abs(d) < (0.05 if digits else 0.5):
        return "ללא שינוי"
    arrow = "▲" if d > 0 else "▼"
    return f"{arrow} {abs(d):.{digits}f} מול שבוע שעבר"


def load_pb_race():
    """His fastest multisport race in the cache, with its real leg splits.

    Read from the data rather than from a number he typed, so the splits and
    the total can never disagree.
    """
    best = None
    with cache.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT activity_id, date, name, duration_s, laps FROM activities"
            " WHERE sport = 'other' AND type = 'multi_sport'"
            " AND duration_s IS NOT NULL ORDER BY duration_s")]
    for row in rows:
        if not row.get("laps"):
            continue
        try:
            laps = json.loads(row["laps"])
        except (TypeError, ValueError):
            continue
        if len(laps) < 5:
            continue
        r = R.from_splits(row["date"], row["name"] or "מרוץ", laps)
        if best is None or r.total_seconds < best.total_seconds:
            best = r
    return best


CSS = r"""
:root{
  color-scheme:dark;
  --bg:#0b1017; --panel:#141a23; --raised:#101620; --line:#222b37;
  --ink:#e8eef6; --dim:#8a97a8; --faint:#5d6a7b;
  --accent:#f0b866;
  --good:#6fc98f; --warn:#e8964e; --bad:#e06d6d; --cool:#5aa9d6;
  --swim:#5aa9d6; --bike:#f0b866; --run:#6fc98f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Rubik,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:26px 18px 72px}
bdi,.num,table{font-variant-numeric:tabular-nums}
h2{font-size:11px;margin:0 0 13px;color:var(--faint);font-weight:650;
   letter-spacing:.09em;text-transform:uppercase}
.sub{color:var(--dim);font-size:12.5px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:17px;margin-bottom:15px}
.grid{display:grid;gap:15px}
/* Grid children default to min-width:auto, so a child never shrinks below its
   content. A Chart.js canvas reports an intrinsic width, so a 1fr column blew
   past its track and pushed the page sideways -- visible only in a browser. */
.grid>*{min-width:0}
@media(min-width:880px){.g2{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .g3{grid-template-columns:repeat(3,minmax(0,1fr))}}

.hero{display:flex;align-items:center;gap:20px;flex-wrap:wrap;
  background:linear-gradient(180deg,#161d28,#111721);border:1px solid var(--line);
  border-right:3px solid var(--accent);border-radius:12px;padding:20px 24px;margin-bottom:15px}
.count{font-size:50px;font-weight:700;color:var(--accent);line-height:.95;letter-spacing:-.03em}
.goal{margin-inline-start:auto;text-align:center;padding-inline-start:20px;border-inline-start:1px solid var(--line)}
.goal .v{font-size:26px;font-weight:650;color:var(--ink)}

/* metric tiles, each with its own trend */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(136px,1fr));gap:11px}
.tiles>*{min-width:0}
.tile{background:var(--raised);border:1px solid var(--line);border-radius:10px;padding:11px 13px;
  display:flex;flex-direction:column;gap:2px}
.tile .l{font-size:10.5px;color:var(--faint);letter-spacing:.04em}
.tile .v{font-size:23px;font-weight:650;line-height:1.15}
.tile .d{font-size:11px;color:var(--dim)}
.spark{display:block;width:100%;height:26px;margin-top:5px}
.t-accent{color:var(--accent)} .t-cool{color:var(--cool)} .t-warn{color:var(--warn)}
.t-good{color:var(--good)} .t-swim{color:var(--swim)} .t-run{color:var(--run)}
.tile .v,.tile .l,.tile .d{color:var(--ink)}
.tile .l,.tile .d{color:var(--dim)}

/* insight cards — the interpretation, above the data */
.cards{display:grid;gap:11px}
.cards>*{min-width:0}
@media(min-width:880px){.cards{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.ins{background:var(--raised);border:1px solid var(--line);border-radius:10px;
  padding:13px 15px;border-inline-start:3px solid var(--dim)}
.ins.critical{border-inline-start-color:var(--bad);background:#e06d6d0a}
.ins.warn{border-inline-start-color:var(--warn);background:#e8964e0a}
.ins.info{border-inline-start-color:var(--cool);background:#5aa9d60a}
.ins.good{border-inline-start-color:var(--good);background:#6fc98f0a}
.ins h3{margin:0 0 6px;font-size:14px;font-weight:650;display:flex;align-items:center;gap:7px}
.ins p{margin:0;font-size:12.8px;color:var(--dim);line-height:1.65}
.ins .do{margin-top:8px;font-size:12.5px;color:var(--ink);
  padding-top:8px;border-top:1px solid var(--line)}
.ins .who{font-size:9.5px;border:1px solid var(--line);border-radius:4px;
  padding:1px 5px;color:var(--faint);font-weight:500;margin-inline-start:auto}

/* race panel */
.legs{width:100%;border-collapse:collapse;font-size:13px}
.legs th{text-align:right;color:var(--faint);font-weight:600;font-size:10.5px;
  letter-spacing:.05em;padding:7px 8px;border-bottom:1px solid var(--line);text-transform:uppercase}
.legs td{padding:9px 8px;border-bottom:1px solid #1a212b}
.legs tr:last-child td{border-bottom:0}
.legs .gap{font-weight:650}
.legs .gap.has{color:var(--accent)}
.bar{height:5px;border-radius:3px;background:var(--line);overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;border-radius:3px}
.free{font-size:9.5px;color:var(--good);border:1px solid #6fc98f44;
  border-radius:4px;padding:1px 5px;margin-inline-start:6px}

/* the week */
/* Seven fixed columns cannot hold "שני 22.08" on a phone: at 375px each
   track is 47px. So it is a scrollable strip when narrow -- which is also
   how a plan reads on a phone -- and a full week once there is room. */
.week{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(116px,1fr);
  gap:6px;overflow-x:auto;padding-bottom:4px}
.week>*{min-width:0;overflow-wrap:anywhere}
@media(min-width:780px){
  .week{grid-auto-flow:row;grid-template-columns:repeat(7,minmax(0,1fr));overflow-x:visible}
}
.day{background:var(--raised);border:1px solid var(--line);border-radius:10px;
  padding:9px;min-height:112px;font-size:12px;border-inline-start:3px solid var(--line)}
.day.today{border-color:var(--accent);box-shadow:0 0 0 1px #f0b86633}
.day.easy{border-inline-start-color:var(--good)}
.day.quality{border-inline-start-color:var(--bad)}
.day.long{border-inline-start-color:var(--cool)}
.day.brick{border-inline-start-color:var(--accent)}
.day.rest{border-inline-start-color:var(--faint)}
.day .dow{color:var(--faint);font-size:10.5px;margin-bottom:6px}
.tag{display:inline-block;font-size:9.5px;padding:1px 5px;border-radius:5px;
  border:1px solid var(--line);color:var(--dim);margin-top:5px}
.tag.me{border-color:#3a5878;color:#8ec0e8;background:#5aa9d611}
.tag.coach{border-color:#6a5326;color:var(--accent);background:#f0b8660f}
.st{font-size:11px;margin-top:4px;color:var(--dim)}
.st.done{color:var(--good)} .st.missed{color:var(--warn)}

/* the plan, with its controls */
.plan{display:flex;flex-direction:column;gap:8px}
.prow{display:flex;align-items:center;gap:11px;background:var(--raised);
  border:1px solid var(--line);border-radius:10px;padding:11px 13px;
  border-inline-start:3px solid var(--line);flex-wrap:wrap}
.prow.easy{border-inline-start-color:var(--good)}
.prow.quality{border-inline-start-color:var(--bad)}
.prow.long{border-inline-start-color:var(--cool)}
.prow.brick{border-inline-start-color:var(--accent)}
.prow.rest{border-inline-start-color:var(--faint)}
.prow.is-today{box-shadow:0 0 0 1px #f0b86633;border-color:var(--accent)}
.prow .when{min-width:96px;font-size:11.5px;color:var(--faint)}
.prow .what{flex:1 1 220px;min-width:0}
.prow .what b{font-weight:600}
.prow .meta{font-size:11.5px;color:var(--dim);margin-top:2px}
.acts{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
button.act{font:inherit;font-size:11.5px;font-weight:600;cursor:pointer;
  background:var(--panel);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:5px 11px;transition:border-color .15s,background .15s}
button.act:hover:not(:disabled){border-color:var(--accent)}
button.act:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button.act:disabled{opacity:.45;cursor:default}
button.act.go{border-color:#f0b86655;color:var(--accent);background:#f0b8660f}
button.act.quiet{color:var(--dim)}
.onwatch{font-size:11px;color:var(--good);display:inline-flex;align-items:center;gap:4px}
.toast{position:fixed;inset-block-end:18px;inset-inline-start:50%;transform:translateX(-50%);
  background:var(--panel);border:1px solid var(--line);border-radius:9px;
  padding:10px 16px;font-size:13px;z-index:50;box-shadow:0 6px 24px #0008;max-width:90vw}
.toast.bad{border-color:#e06d6d66;color:var(--bad)}
.toast.good{border-color:#6fc98f66;color:var(--good)}

table.sessions{width:100%;border-collapse:collapse;font-size:13px}
table.sessions th{text-align:right;color:var(--faint);font-weight:600;font-size:10.5px;
  letter-spacing:.05em;padding:7px 8px;border-bottom:1px solid var(--line);text-transform:uppercase}
table.sessions td{padding:8px}
table.sessions tr:nth-child(even) td{background:#0f141c}
.scroll{overflow-x:auto}
.empty{border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center;color:var(--dim)}
.empty b{color:var(--accent);display:block;font-size:16px;margin-bottom:6px}
.chartbox{position:relative;height:232px}
.note{font-size:11.5px;color:var(--faint);margin-top:10px;line-height:1.65}
.foot{color:var(--faint);font-size:11px;text-align:center;margin-top:26px}
@media (prefers-reduced-motion:no-preference){
  .card,.hero{animation:rise .35s ease both}
  @keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
}
"""

JS = r"""
const el = h => { const d=document.createElement('div'); d.innerHTML=h.trim(); return d.firstChild; };
const ltr = s => `<bdi dir="ltr">${s}</bdi>`;
const esc = s => String(s??'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const DOW = ['שני','שלישי','רביעי','חמישי','שישי','שבת','ראשון'];
const SEV = {critical:'🔴', warn:'🟠', info:'🔵', good:'🟢'};
const app = document.getElementById('app');
const S = D.sport_he, C = D.sport_color, I = D.sport_icon;
const add = h => app.appendChild(el(h));

/* ── hero ─────────────────────────────────────────────────────────── */
if (D.onboarded && D.days_to_race !== null) {
  const p = D.profile, t = D.race && D.race.targets;
  add(`<div class="hero">
    <div><div class="sub">${esc(p.race||'המרוץ')}</div>
      <div class="count">${ltr(D.days_to_race)}</div></div>
    <div><div style="font-size:17px">ימים לתחרות</div>
      <div class="sub">${ltr(esc(p.race_date))}${p.race_date_is_approximate?' (משוער)':''}</div></div>
    ${t?`<div class="goal"><div class="sub">יעד</div><div class="v">${ltr(t.goal_text)}</div>
      <div class="sub">שיא ${ltr(t.normalised_pb_text)}</div></div>`:''}
  </div>`);
} else {
  add(`<div class="empty"><b>עדיין לא הגדרת יעד</b>
    פתח את המאמן ואמור "בוא נגדיר את המרוץ" — הספירה, היעד והתוכנית יופיעו כאן.
    <div class="note">כל שאר הלוח מלא מההיסטוריה שלך ועובד כרגיל.</div></div>`);
}

/* ── metric tiles with sparklines ─────────────────────────────────── */
add(`<div class="card"><h2>מצב נוכחי</h2><div class="tiles">${
  D.tiles.map(t => `<div class="tile t-${t.tone}">
    <div class="l">${esc(t.label)}</div>
    <div class="v">${ltr(esc(t.value))}</div>
    <div class="d">${ltr(esc(t.detail))}</div>${t.spark}</div>`).join('')
}</div></div>`);

/* ── insight cards: interpretation before data ────────────────────── */
if (D.cards && D.cards.length) {
  add(`<div class="card"><h2>מה הנתונים אומרים</h2><div class="cards">${
    D.cards.map(c => `<div class="ins ${esc(c.severity)}">
      <h3>${SEV[c.severity]||''} ${esc(c.title)}
        ${c.author==='coach'?'<span class="who">מהמאמן</span>':''}</h3>
      <p>${esc(c.evidence)}</p>
      ${c.action?`<div class="do">← ${esc(c.action)}</div>`:''}</div>`).join('')
  }</div></div>`);
}

/* ── race panel ───────────────────────────────────────────────────── */
if (D.race && D.race.targets) {
  const t = D.race.targets, max = Math.max(...t.legs.map(l=>l.pb_seconds));
  const colour = l => l.free ? 'var(--good)' : (l.leg==='bike'?'var(--bike)':l.leg==='swim'?'var(--swim)':'var(--run)');
  add(`<div class="card"><h2>המרוץ — שיא מול יעד (${esc(t.course)})</h2>
    <div class="scroll"><table class="legs">
      <thead><tr><th>רגל</th><th>שיא</th><th>יעד</th><th>לחסוך</th><th>קצב יעד</th><th>חלק מהמרוץ</th></tr></thead>
      <tbody>${t.legs.map(l=>`<tr>
        <td>${l.icon} ${esc(l.he)}${l.free?'<span class="free">חינם</span>':''}</td>
        <td>${ltr(l.pb_text)}</td><td>${ltr(l.target_text)}</td>
        <td class="gap ${l.gap_seconds>2?'has':''}">${l.gap_seconds>2?ltr(l.gap_text):'—'}</td>
        <td>${ltr(esc(l.target_rate))}</td>
        <td>${ltr(l.share_pct+'%')}
          <div class="bar"><i style="width:${(100*l.pb_seconds/max).toFixed(1)}%;background:${colour(l)}"></i></div></td>
      </tr>`).join('')}</tbody></table></div>
    <div class="note">${t.already_achievable
      ? 'היעד בהישג מהכושר הנוכחי.'
      : `השיא מנורמל למסלול הוא ${ltr(t.normalised_pb_text)}, היעד ${ltr(t.goal_text)} — צריך למצוא ${ltr(t.needed_text)}.
         ${t.notes.length?'<br>⚠️ '+t.notes.map(esc).join('<br>⚠️ '):''}`}</div>
    ${D.race.sensitivity?`<div class="note">כמה שווה שיפור: ${
      D.race.sensitivity.map(s=>`${s.icon} ${esc(s.he)} ${ltr(esc(s.step))} = ${ltr(fmtSec(s.seconds))}`).join(' · ')}</div>`:''}
  </div>`);
}
function fmtSec(s){s=Math.round(s);const m=Math.floor(s/60);return `${m}:${String(s%60).padStart(2,'0')}`;}

/* ── the plan, and the buttons that act on it ─────────────────────── */
/* Controls render only when this page is served by the local server. The
   published copy is a static file with no API behind it, so a push button
   there would be a lie. */
function toast(msg, kind) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const t = el(`<div class="toast ${kind||''}">${esc(msg)}</div>`);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), kind === 'bad' ? 7000 : 3500);
}

async function api(path, body, btn) {
  const label = btn && btn.textContent;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(body||{})});
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'לא הצליח');
    return d;
  } catch (e) {
    toast(e.message, 'bad');
    if (btn) { btn.disabled = false; btn.textContent = label; }
    throw e;
  }
}

const DAYNAME = d => {
  const wd = new Date(d+'T00:00').getDay();
  return DOW[wd===0?6:wd-1];
};

function planRow(p) {
  const when = p.in_days === 0 ? 'היום' : p.in_days === 1 ? 'מחר'
             : `${DAYNAME(p.date)} ${p.date.slice(8,10)}.${p.date.slice(5,7)}`;
  const src = p.source === 'athlete' ? '<span class="tag me">אני</span>'
            : p.source === 'coach' ? '<span class="tag coach">מאמן</span>' : '';
  const done = p.status === 'done' ? '<span class="st done">✓ בוצע</span>'
             : p.status === 'skipped' ? '<span class="st missed">✗ דולג</span>' : '';

  let controls = '';
  if (D.live) {
    const push = p.on_watch
      ? `<span class="onwatch">⌚ על השעון</span>
         <button class="act quiet" data-act="unpush" data-date="${p.date}">הסר</button>`
      : p.pushable
        ? `<button class="act go" data-act="push" data-date="${p.date}">דחוף לשעון</button>`
        : `<button class="act" disabled title="אין אימון מובנה ליום הזה">אין מה לדחוף</button>`;
    controls = `<div class="acts">${push}
      <button class="act quiet" data-act="done" data-date="${p.date}">בוצע</button>
      <button class="act quiet" data-act="skip" data-date="${p.date}">דולג</button>
      <button class="act quiet" data-act="later" data-date="${p.date}" title="דחה ליום הבא">↩ מחר</button>
    </div>`;
  } else if (p.on_watch) {
    controls = '<span class="onwatch">⌚ על השעון</span>';
  }

  return `<div class="prow ${esc(p.kind||'')} ${p.in_days===0?'is-today':''}">
    <div class="when">${esc(when)}</div>
    <div class="what"><b>${I[p.sport]||''} ${esc(p.title||'—')}</b>
      <div class="meta">${src}${done}</div></div>
    ${controls}</div>`;
}

if (D.horizon && D.horizon.length) {
  const pushable = D.horizon.filter(p => p.pushable && !p.on_watch).length;
  add(`<div class="card"><h2>התוכנית — ${D.horizon.length} אימונים ב-3 השבועות הקרובים</h2>
    <div class="plan" id="plan">${D.horizon.map(planRow).join('')}</div>
    ${D.live && pushable ? `<div class="acts" style="margin-top:12px">
      <button class="act go" data-act="pushweek">דחוף את כל השבוע (${pushable})</button>
      <button class="act quiet" data-act="refresh">רענן נתונים מגרמין</button></div>` : ''}
    ${!D.live ? '<div class="note">זו תצוגה בלבד. כדי לדחוף לשעון או לסמן — פתח את הדשבורד המקומי.</div>' : ''}
  </div>`);
} else {
  add(`<div class="card"><h2>התוכנית</h2>
    <div class="empty"><b>עדיין אין אימונים מתוכננים</b>
      פתח את המאמן ובקש "בנה לי את השבוע" — מה שתאשר יופיע כאן עם כפתור דחיפה.</div></div>`);
}

document.addEventListener('click', async ev => {
  const b = ev.target.closest('button.act[data-act]');
  if (!b) return;
  const act = b.dataset.act, date = b.dataset.date;
  try {
    if (act === 'push')       { const d = await api('/api/push', {date}, b);
                                toast(`נדחף לשעון (${d.workout_id})`, 'good'); }
    else if (act === 'unpush'){ await api('/api/unpush', {date}, b); toast('הוסר מהשעון', 'good'); }
    else if (act === 'done')  { await api('/api/status', {date, status:'done'}, b);
                                toast('סומן כבוצע', 'good'); }
    else if (act === 'skip')  { await api('/api/status', {date, status:'skipped'}, b);
                                toast('סומן כדולג', 'good'); }
    else if (act === 'later') {
      const next = new Date(date+'T00:00'); next.setDate(next.getDate()+1);
      const to = next.toISOString().slice(0,10);
      await api('/api/move', {from: date, to}, b);
      toast(`הוזז ל-${to.slice(8,10)}.${to.slice(5,7)}`, 'good');
    }
    else if (act === 'refresh'){ const d = await api('/api/refresh', {days:3}, b);
                                toast(`רועננו ${d.days_fetched} ימים`, 'good'); }
    else if (act === 'pushweek'){
      const days = D.horizon.filter(p => p.pushable && !p.on_watch && p.in_days < 7);
      if (!days.length) return toast('אין מה לדחוף השבוע');
      const d = await api('/api/pushdays', {days: days.map(x => x.date)}, b);
      toast(`נדחפו ${d.pushed.length} אימונים` +
            (d.skipped.length ? ` · ${d.skipped.length} דולגו` : ''), 'good');
    }
    setTimeout(() => location.reload(), 900);
  } catch (e) { /* the toast already said what failed */ }
});

/* ── the week ─────────────────────────────────────────────────────── */
add(`<div class="card"><h2>השבוע — מתוכנן מול בוצע</h2><div class="week">${
  D.week.map(d=>{
    const done = (d.actual||[]).map(a=>`${I[a.sport]||'•'} ${ltr(a.mins)} דק'`).join('<br>');
    const src = d.source==='athlete'?'<span class="tag me">אני</span>'
              : d.source==='coach'?'<span class="tag coach">מאמן</span>':'';
    const st = d.status==='done'?'<div class="st done">✓ בוצע</div>'
             : d.status==='skipped'?'<div class="st missed">✗ דולג</div>':'';
    const wd = new Date(d.date+'T00:00').getDay();
    return `<div class="day ${d.is_today?'today ':''}${esc(d.kind||'')}">
      <div class="dow">${DOW[wd===0?6:wd-1]} ${ltr(d.date.slice(8,10)+'.'+d.date.slice(5,7))}</div>
      ${d.title?`<div>${I[d.sport]||''} ${esc(d.title)}</div>`:'<div class="sub">—</div>'}
      ${src}${st}${done?`<div class="st">${done}</div>`:''}</div>`;
  }).join('')
}</div><div class="note">פס הצבע מסמן את סוג האימון. התג מציין מי כתב אותו — מה שאתה כותב, המאמן לא דורס.</div></div>`);

/* ── charts ───────────────────────────────────────────────────────── */
add(`<div class="grid g2">
  <div class="card"><h2>נפח שבועי לפי ענף — בשעות</h2><div class="chartbox"><canvas id="vol"></canvas></div>
    <div class="note">⚠️ בשעות ולא בקילומטרים: אימוני אופניים מקורים רושמים <bdi dir="ltr">0</bdi> ק"מ,
      אז גרף ק"מ היה מוחק חלק גדול מהאימון שלך.</div></div>
  <div class="card"><h2>עומס — אקוטי מול כרוני (מגרמין)</h2><div class="chartbox"><canvas id="load"></canvas></div>
    <div class="note">חוצה-ענפים ומבוסס EPOC, ולכן שחייה ואופניים נספרים נכון ולא לפי סף ריצה.</div></div>
</div>`);

const tot = D.totals.map(t=>`<div class="tile"><div class="l">${I[t.sport]||''} ${esc(S[t.sport]||t.sport)}</div>
  <div class="v">${ltr(t.hours)}<span style="font-size:12px"> ש'</span></div>
  <div class="d">${ltr(t.n)} אימונים${t.km?` · ${ltr(t.km)} ק"מ`:''}</div></div>`).join('');
add(`<div class="card"><h2>סה"כ ב-13 שבועות</h2><div class="tiles">${tot}</div></div>`);

add(`<div class="card"><h2>אימונים אחרונים</h2><div class="scroll"><table class="sessions">
  <thead><tr><th>תאריך</th><th>ענף</th><th>שם</th><th>ק"מ</th><th>דק'</th>
  <th>קצב / הספק</th><th>דופק</th><th>עומס</th></tr></thead><tbody>${
  D.activities.map(a=>`<tr>
    <td>${ltr(a.date.slice(8,10)+'.'+a.date.slice(5,7))}</td>
    <td>${a.icon} ${esc(a.sport_he)}</td><td>${esc(a.name||'')}</td>
    <td>${a.km?ltr(a.km):'—'}</td><td>${ltr(a.mins)}</td>
    <td>${a.rate?ltr(esc(a.rate)):'—'}</td>
    <td>${a.avg_hr?ltr(Math.round(a.avg_hr)):'—'}</td>
    <td>${a.training_load?ltr(Math.round(a.training_load)):'—'}</td></tr>`).join('')
}</tbody></table></div></div>`);

if (D.race_predictions){
  const r=D.race_predictions, f=s=>{s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
    return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(s%60).padStart(2,'0');};
  add(`<div class="card"><h2>תחזיות גרמין</h2><div class="tiles">
    <div class="tile"><div class="l">5 ק"מ</div><div class="v">${ltr(f(r.time5K))}</div></div>
    <div class="tile"><div class="l">10 ק"מ</div><div class="v">${ltr(f(r.time10K))}</div></div>
    <div class="tile"><div class="l">חצי מרתון</div><div class="v">${ltr(f(r.timeHalfMarathon))}</div></div>
  </div></div>`);
}
document.getElementById('syncinfo').innerHTML =
  D.sync.last_sync ? `סנכרון אחרון ${ltr(D.sync.last_sync.replace('T',' ').slice(0,16))}` : 'טרם סונכרן';

/* ── chart config ─────────────────────────────────────────────────── */
Chart.defaults.color='#8b949e'; Chart.defaults.borderColor='#232a33';
Chart.defaults.font.family='-apple-system,Segoe UI,Rubik,Arial,sans-serif';
const weeks=[...new Set(D.weekly.map(w=>w.wk))].sort();
new Chart(document.getElementById('vol'),{type:'bar',
  data:{labels:weeks.map(w=>w.split('-')[1]),
    datasets:['swim','bike','run'].map(sp=>({label:S[sp],backgroundColor:C[sp],
      data:weeks.map(w=>{const h=D.weekly.find(x=>x.wk===w&&x.sport===sp);return h?h.hours:0;})}))},
  options:{maintainAspectRatio:false,responsive:true,
    scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,title:{display:true,text:'שעות'}}},
    plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:12}}}}});
const dl=D.daily.map(d=>d.date.slice(5).replace('-','.'));
new Chart(document.getElementById('load'),{type:'line',
  data:{labels:dl,datasets:[
    {label:'אקוטי',data:D.daily.map(d=>d.load_acute),borderColor:'#f0b866',
     backgroundColor:'#f0b8661f',fill:true,borderWidth:2,pointRadius:0,tension:.3,spanGaps:true},
    {label:'כרוני',data:D.daily.map(d=>d.load_chronic),borderColor:'#5aa9d6',
     borderWidth:2,borderDash:[5,4],pointRadius:0,tension:.3,spanGaps:true}]},
  options:{maintainAspectRatio:false,responsive:true,
    plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:12}}},
    scales:{x:{ticks:{maxTicksLimit:7},grid:{display:false}}}}});
"""

TEMPLATE = r"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>__CSS__</style>
</head>
<body><div class="wrap"><div id="app"></div>
<div class="foot">עודכן <bdi dir="ltr">__GEN__</bdi> · <span id="syncinfo"></span></div>
</div>
<script>const D = __DATA__;</script>
<script>__JS__</script>
</body></html>"""


def build(out=None, weeks=13, live=False):
    data = collect(weeks, live=live)
    name = (data["profile"].get("athlete") or "המאמן").strip()
    html = (TEMPLATE
            .replace("__CSS__", CSS).replace("__JS__", JS)
            .replace("__TITLE__", f"האימונים של {name}")
            .replace("__GEN__", data["generated"])
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str)))
    out = out or os.path.join(P.home(), "index.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


def main(open_browser=True):
    cache.init()
    path = build()
    print(f"✅ {path}")
    if open_browser:
        webbrowser.open("file://" + path)
    return path


if __name__ == "__main__":
    main()
