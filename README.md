# Triathlon coach — Garmin MCP + a Hebrew dashboard

A local triathlon coach that connects to your own Garmin account. It runs on
your machine, answers from a local cache, and can build structured swim, bike
and run workouts and push them to your watch.

Built on [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp), which
supplies the Garmin API surface. This adds the coaching layer.

## Why a local cache

Garmin's API is slow — a live read took **1,401 ms median** in testing, so a
coach that fetched everything on demand spent about seven seconds waiting
before it could answer one question. Reads come from a local SQLite cache
instead (**0.06 ms**), and only writes go to Garmin (**~400 ms** to upload a
workout, ~450 ms to schedule it).

The two halves also change at different rates, and syncing them at the same
rate is what makes things slow:

| | changes | cost | when |
|---|---|---|---|
| activities | when you train | **1.1 s** — one API call | on demand |
| wellness (sleep, HRV, readiness) | once, overnight | **14 s per day** — 11 calls each | scheduled, 06:00 |

## What it adds

**Structured workouts for all three disciplines.** Swim needs `poolLength`,
per-step `strokeType`, `stepType: main/rest` and `fixed.rest` end conditions —
none of which a run-shaped builder emits. Targets are structured fields
(`pace_per_100m`, `power_w`) rather than display strings, because parsing
`"1:36/100m"` out of a label silently reads `"36s/25m"` as seconds per
kilometre and pushes a nonsense target to the watch.

> ⚠️ **Swimming is `sportTypeId` 4, not 5.** 5 is `strength_training`. Getting
> this wrong creates a strength workout on the watch, silently.

**A deterministic insight engine.** Plain Python over the cache — no model
call, so it is free, instant, and correct even after a week without opening a
chat. Every rule carries a minimum sample and stays silent below it, because a
threshold that fires on two data points is noise wearing a severity colour.

**Heart-rate ceilings derived per athlete, per sport.** One threshold cannot
serve three disciplines: heart rate runs lower on the bike and lower again in
the water. Ceilings are anchored on a running-equivalent maximum and offset,
and a sport's own measured maximum wins when it is credible.

**Race splits with distance normalisation.** Two triathlons four weeks apart
read 1:16:18 then 1:13:34 — nearly three minutes faster, and wrong. The second
bike course was 1.38 km shorter. Normalised, the bike had moved +0.26 km/h.
Every cross-course comparison goes through `race.py` rather than through
someone's judgement.

**Plan ownership.** The plan belongs to the athlete. The coach proposes into a
pending slot, and only an explicit approval moves anything into the plan; a day
the athlete wrote is never overwritten. Consultation is a separate read-only
tool with no write path at all — without that separation a model shown a plan
will correct it, and "I asked for an opinion" becomes "it changed my week".

## Install

```
uv run python install.py
```

Dependencies, Garmin login, history, dashboard, Claude Desktop wiring and the
morning refresh. Then open Claude and say what race you are training for.

## Privacy

Your Garmin tokens, cache and plan live in `~/.garminconnect` and
`~/.ari-coach`, owner-readable only, and never enter this repository. The
coaching brief ships generic; everything about a specific athlete is computed
from their own cache at server startup.

MIT.
