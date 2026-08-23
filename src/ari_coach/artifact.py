"""Publish the dashboard as one hosted page.

Two differences from the local build. Chart.js is inlined rather than pulled
from a CDN, because the artifact CSP blocks every external host and a linked
script fails silently -- the charts simply never appear. And the document
wrapper is omitted: the host supplies doctype, html, head and body.

Single dark theme on purpose. It follows the athlete's existing palette
(night ground, amber accent) and matches every instrument he already reads.
Every colour is painted explicitly, and :root re-declares color-scheme:dark
over the host's light default, so the page holds on either ground.
"""

import json
import os

from . import dashboard

VENDOR = os.path.join(os.path.dirname(__file__), "..", "..", "vendor")


def _title(data):
    """Name the page after the race, so the repo carries no one's goal."""
    race = (data.get("profile") or {}).get("race")
    return f"הדרך אל {race}" if race else "לוח האימונים"


def build(out=None, weeks=13):
    data = dashboard.collect(weeks)
    with open(os.path.join(VENDOR, "chart.umd.min.js"), encoding="utf-8") as fh:
        chartjs = fh.read()
    name = (data["profile"].get("athlete") or "האתלט").strip()

    html = (
        f"<title>{_title(data)}</title>\n"
        f"<style>{dashboard.CSS}</style>\n"
        f'<div class="wrap"><div id="app"></div>\n'
        f'<div class="foot">עודכן <bdi dir="ltr">{data["generated"]}</bdi> · '
        f'<span id="syncinfo"></span></div></div>\n'
        f"<script>{chartjs}</script>\n"
        f"<script>const D = {json.dumps(data, ensure_ascii=False, default=str)};</script>\n"
        f"<script>{dashboard.JS}</script>\n"
    )
    out = out or "/tmp/ari-artifact.html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out, len(html)
