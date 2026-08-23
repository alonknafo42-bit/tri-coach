"""Serve the dashboard locally so it can act, not just display.

Bound to 127.0.0.1 only. The page needs a server for two reasons: the edit
actions (mark done, move a day, push to the watch) have to reach Python, and
navigator.clipboard requires a secure context -- http://127.0.0.1 counts as
one, file:// does not.
"""

import json
import os
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import builders, cache, dashboard, plan_store as P

_garmin_factory = None
_client = None
_client_lock = threading.Lock()

# Background refresh state. The page must never block on the network: a
# seven-day sync is about 45 seconds of Garmin calls, and a dashboard that
# hangs for half a minute is worse than one showing yesterday's numbers.
REFRESH_AFTER_HOURS = 2.0
REFRESH_DAYS = 3
_refreshing = threading.Event()
_last_attempt = 0.0
_last_error = None


def set_garmin_factory(fn):
    """Lazily supply a logged-in Garmin client; only push actions need one."""
    global _garmin_factory
    _garmin_factory = fn


def _default_client():
    """Log in once from the cached tokens, and reuse that session.

    Login costs about five seconds, so doing it per request would make every
    push feel broken.
    """
    global _client
    with _client_lock:
        if _client is None:
            from garminconnect import Garmin
            store = os.getenv("GARMINTOKENS", "~/.garminconnect")
            g = Garmin()
            g.login(os.path.expanduser(store))
            _client = g
        return _client


def _client_or_none():
    try:
        return (_garmin_factory or _default_client)()
    except Exception as e:                       # no tokens yet, or offline
        globals()["_last_error"] = f"{type(e).__name__}: {e}"
        return None


def _sync_age_hours():
    with cache.connect() as c:
        last = cache.sync_status(c).get("last_sync")
    if not last:
        return None
    try:
        then = datetime.fromisoformat(last)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


def _refresh_in_background():
    """Kick off a short sync if the cache has gone stale.

    Deliberately narrow: the last few days only. The point is to pick up the
    session he finished an hour ago, not to re-walk five months of history.
    """
    global _last_attempt, _last_error
    age = _sync_age_hours()
    if age is not None and age < REFRESH_AFTER_HOURS:
        return "fresh"
    if _refreshing.is_set():
        return "running"
    if time.time() - _last_attempt < 120:        # do not retry a failure in a loop
        return "cooling"
    _last_attempt = time.time()

    def work():
        global _last_error
        _refreshing.set()
        try:
            g = _client_or_none()
            if g is None:
                return
            from . import sync
            sync.sync(g, days=REFRESH_DAYS, verbose=False)
            _last_error = None
        except Exception as e:                   # never take the page down with it
            _last_error = f"{type(e).__name__}: {e}"
        finally:
            _refreshing.clear()

    threading.Thread(target=work, daemon=True).start()
    return "started"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                   # keep the console readable

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            # Serve from cache immediately, refresh behind it. The freshness
            # is stated on the page, so a stale number is never silently stale.
            _refresh_in_background()
            out = dashboard.build(live=True)
            with open(out, encoding="utf-8") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if path == "/api/freshness":
            age = _sync_age_hours()
            return self._json(200, {"ok": True, "age_hours": age,
                                    "refreshing": _refreshing.is_set(),
                                    "error": _last_error})
        if path == "/api/week":
            return self._json(200, {"week": P.week_of(), "days_to_race": P.days_to_race()})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"ok": False, "error": f"bad JSON: {e}"})
        try:
            return self._json(200, {"ok": True, **self._act(path, body)})
        except (KeyError, ValueError, PermissionError, builders.WorkoutError) as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:                  # noqa: BLE001 - surface, never swallow
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _act(self, path, body):
        if path == "/api/status":
            return {"day": P.set_status(body["date"], body["status"])}
        if path == "/api/move":
            return {"day": P.move_day(body["from"], body["to"])}
        if path == "/api/refresh":
            from . import sync
            g = _client_or_none()
            if g is None:
                raise ValueError(f"אין התחברות לגרמין ({_last_error or 'לא ידוע'})")
            return sync.sync(g, days=int(body.get("days", 14)), verbose=False)
        if path == "/api/pushdays":
            # Bulk push, one day at a time so a single bad workout cannot
            # take the rest of the week down with it.
            pushed, skipped = [], []
            for d in body.get("days") or []:
                try:
                    pushed.append({"date": d, **self._push_day(d)})
                except Exception as e:
                    skipped.append({"date": d, "reason": str(e)})
            return {"pushed": pushed, "skipped": skipped}
        if path == "/api/unpush":
            g = _client_or_none()
            if g is None:
                raise ValueError(f"אין התחברות לגרמין ({_last_error or 'לא ידוע'})")
            d = body["date"]
            y, m = int(d[:4]), int(d[5:7])
            cal = g.connectapi(f"/calendar-service/year/{y}/month/{m - 1}") or {}
            removed = []
            for item in cal.get("calendarItems") or []:
                if (item.get("itemType") == "workout" and item.get("date") == d
                        and item.get("id")):
                    g.client.delete("connectapi",
                                    f"/workout-service/schedule/{item['id']}", api=True)
                    removed.append(item["id"])
            entry = P.get_day(d)
            if entry:
                P.write_day(d, {"garmin_workout_id": None, "scheduled_at": None},
                            entry.get("source", P.ATHLETE), force=True)
            return {"unscheduled": removed}
        if path == "/api/push":
            entry = P.get_day(body["date"])
            if not entry or not entry.get("workout"):
                raise ValueError(f"{body['date']} has no structured workout to push")
            return self._push_day(body["date"])
        raise ValueError(f"unknown action {path}")

    def _push_day(self, day):
        entry = P.get_day(day)
        if not entry or not entry.get("workout"):
            raise ValueError(f"{day}: אין אימון מובנה לדחוף")
        payload = builders.build(entry["workout"])
        g = _client_or_none()
        if g is None:
            raise ValueError(f"אין התחברות לגרמין ({_last_error or 'לא ידוע'})")
        res = g.upload_workout(payload)
        wid = res.get("workoutId") if isinstance(res, dict) else None
        if not wid:
            raise ValueError("גרמין לא החזירה workoutId")
        g.schedule_workout(wid, day)
        P.write_day(day, {"garmin_workout_id": wid, "scheduled_at": P.now()},
                    entry.get("source", P.ATHLETE), force=True)
        return {"workout_id": wid}


def serve(port=8770, open_browser=True, block=True):
    cache.init()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"✅ {url}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye")
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def cli(argv=None):
    """Entry point so the dashboard is one command, not a python -c incantation."""
    import argparse
    ap = argparse.ArgumentParser(description="Open the local dashboard")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    serve(port=a.port, open_browser=not a.no_open)
    return 0
