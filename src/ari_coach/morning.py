"""The 06:00 job: pull what is new, rebuild the page, optionally publish it.

Deliberately small. The chat reads a local cache, so nothing here has to be
clever -- it just has to have run before he wakes up, and to fail quietly
when the machine was asleep or offline rather than leaving a half-written
state behind.

Publishing is optional and off unless a target is configured. It pushes the
rendered HTML only: the Garmin tokens are six months of full account access
and have no business leaving the machine to make a web page visible.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

from . import cache, dashboard, plan_store as P


def _log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _garmin():
    from garminconnect import Garmin
    store = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))
    g = Garmin()
    g.login(store)
    return g


def publish(html_path, token=None, project=None):
    """Deploy the rendered page as a static site.

    Uses a Vercel token rather than an interactive login so the athlete's
    machine never needs a Vercel account of its own -- the token belongs to
    whoever wants to watch, and only ever carries a finished HTML file.
    """
    token = token or os.getenv("VERCEL_TOKEN")
    project = project or os.getenv("ARI_PUBLISH_PROJECT", "ari-coach-dashboard")
    if not shutil.which("npx"):
        return {"published": False, "reason": "npx not found"}
    if not token and not os.getenv("ARI_PUBLISH_USE_CLI_AUTH"):
        # Silent by default: publishing is for whoever wants to watch from a
        # phone, and an athlete who never asked for it should not see a
        # failure every morning.
        return {"published": False, "reason": "no VERCEL_TOKEN configured"}

    # Vercel names the project after the directory, and --name is deprecated,
    # so the staging directory carries the name.
    parent = tempfile.mkdtemp(prefix="ari-publish-")
    staging = os.path.join(parent, project)
    os.makedirs(staging, exist_ok=True)
    try:
        shutil.copy(html_path, os.path.join(staging, "index.html"))
        cmd = ["npx", "--yes", "vercel", "deploy", "--prod", "--yes"]
        if token:
            cmd += ["--token", token]
        r = subprocess.run(cmd, cwd=staging, capture_output=True,
                           text=True, timeout=300)
        url = ""
        for m in re.finditer(r"https://[\w.-]+\.vercel\.app",
                             r.stdout + r.stderr):
            url = m.group(0)
        if r.returncode:
            return {"published": False,
                    "reason": (r.stderr or r.stdout).strip()[-300:]}
        return {"published": True, "url": url or f"https://{project}.vercel.app"}
    except subprocess.TimeoutExpired:
        return {"published": False, "reason": "deploy timed out"}
    except Exception as e:
        return {"published": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def run(days=3, do_publish=True, quiet=False):
    started = time.time()
    out = {"synced": None, "built": None, "publish": None, "error": None}
    cache.init()

    try:
        from . import sync
        g = _garmin()
        out["synced"] = sync.sync(g, days=days, verbose=False)
        _log(f"synced {out['synced']['days_fetched']} days, "
             f"{out['synced']['activities']} activities")
    except Exception as e:
        # Offline, asleep, expired tokens: log and carry on. A failed pull
        # should still leave yesterday's page standing rather than nothing.
        out["error"] = f"{type(e).__name__}: {e}"
        _log(f"sync failed ({out['error']}) — rebuilding from what is cached")

    try:
        path = dashboard.build()
        out["built"] = path
        _log(f"built {path}")
    except Exception as e:
        out["error"] = f"build failed: {type(e).__name__}: {e}"
        _log(out["error"])
        return out

    if do_publish:
        out["publish"] = publish(path)
        _log("published " + (out["publish"].get("url") or "")
             if out["publish"]["published"]
             else f"not published: {out['publish']['reason']}")

    _log(f"done in {time.time() - started:.0f}s")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Morning refresh")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args(argv)
    r = run(days=a.days, do_publish=not a.no_publish)
    return 1 if r.get("error") and not r.get("built") else 0


if __name__ == "__main__":
    sys.exit(main())
