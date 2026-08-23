#!/usr/bin/env python3
"""התקנת עבודת הבוקר — 06:00 כל יום.

    uv run python schedule_morning.py            # התקנה
    uv run python schedule_morning.py --remove   # הסרה

בוינדוס יוצר משימה ב-Task Scheduler, במק סוכן launchd. בשני המקרים
המשימה מריצה סנכרון קצר ובונה מחדש את הדשבורד, ואם הוגדר VERCEL_TOKEN
גם מפרסמת אותו לכתובת.
"""

import argparse
import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = "com.ari-coach.morning"
TASK = "AriCoachMorning"
HOUR, MINUTE = 6, 0


def _uv():
    for c in ("uv", os.path.expanduser("~/.local/bin/uv")):
        if os.path.exists(c) or (c == "uv" and _which(c)):
            return _which(c) or c
    return "uv"


def _which(x):
    from shutil import which
    return which(x)


def install_mac():
    plist = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    with open(plist, "w") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{_uv()}</string><string>run</string>
    <string>--directory</string><string>{HERE}</string>
    <string>ari-morning</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{HOUR}</integer>
        <key>Minute</key><integer>{MINUTE}</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{HERE}/morning.log</string>
  <key>StandardErrorPath</key><string>{HERE}/morning.log</string>
</dict></plist>
""")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", plist],
                       capture_output=True, text=True)
    if r.returncode:
        return False, (r.stderr or r.stdout).strip()
    return True, plist


def remove_mac():
    plist = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)
    if os.path.exists(plist):
        os.remove(plist)
    return True, plist


def install_windows():
    cmd = f'"{_uv()}" run --directory "{HERE}" ari-morning'
    r = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY",
         "/TN", TASK, "/ST", f"{HOUR:02d}:{MINUTE:02d}",
         "/TR", cmd],
        capture_output=True, text=True)
    if r.returncode:
        return False, (r.stderr or r.stdout).strip()
    return True, TASK


def remove_windows():
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK],
                   capture_output=True, text=True)
    return True, TASK


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    win = platform.system() == "Windows"
    if a.remove:
        ok, what = (remove_windows if win else remove_mac)()
        print(f"{'✅' if ok else '🔴'} הוסר: {what}")
        return 0 if ok else 1
    ok, what = (install_windows if win else install_mac)()
    if not ok:
        print(f"🔴 ההתקנה נכשלה: {what}")
        return 1
    print(f"✅ עבודת הבוקר תרוץ כל יום ב-{HOUR:02d}:{MINUTE:02d}")
    print(f"   {what}")
    print(f"   יומן: {HERE}/morning.log")
    if not os.getenv("VERCEL_TOKEN"):
        print("   ℹ️  בלי VERCEL_TOKEN היא מסנכרנת ובונה מקומית, בלי לפרסם.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
