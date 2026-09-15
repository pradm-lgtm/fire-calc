#!/usr/bin/env python3
"""
Install the Tuesday job and the always-on approval page as launchd services.

    python3 install_schedule.py --user pradm7            # page and weekly job
    python3 install_schedule.py --user pradm7 --watch   # and the submit button
    python3 install_schedule.py --user pradm7 --day mon --hour 8
    python3 install_schedule.py --status               # what is installed
    python3 install_schedule.py --uninstall            # remove both

Four services, two of them on by default:

  weekly  gathers this week's articles and files proposals, then exits.
          Monday morning by default: the analysts have written by then, and
          waiting for the last game costs a day of review for a picture that
          barely moves.
  web     keeps the approval page up, restarting it if it dies and starting
          it again at login, so the URL simply works when you open it.
  watch   checks the page's "place them now" button every minute and acts
          on it. OFF unless you ask for it with --watch. This is the one to
          use: every submission still begins with you pressing something,
          and the page cannot place claims itself because it runs where
          there is no browser and no Sleeper session.
  submit  places approved claims on a timer instead, Tuesday evening by
          default, with --submit. Use it only if you want them placed
          whether or not you are there.

Both need Chrome running and signed in to Sleeper, and this Mac awake.

launchd will not wake a sleeping Mac on its own. If the machine is asleep at
the scheduled time the job runs when it next wakes, which for a Tuesday
morning is usually fine; --wake installs a power schedule if you would rather
not rely on that.
"""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCH_DIR = Path.home() / "Library" / "LaunchAgents"
WEEKLY_LABEL = "com.fantasyagent.weekly"
WEB_LABEL = "com.fantasyagent.web"
SUBMIT_LABEL = "com.fantasyagent.submit"
WATCH_LABEL = "com.fantasyagent.watch"
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def python_bin():
    return sys.executable or "/usr/bin/python3"


def weekly_plist(username, weekday, hour, minute):
    return {
        "Label": WEEKLY_LABEL,
        "ProgramArguments": [python_bin(), str(HERE / "run_weekly.py"), username],
        "WorkingDirectory": str(HERE),
        "StartCalendarInterval": [{"Weekday": weekday, "Hour": hour,
                                   "Minute": minute}],
        "StandardOutPath": str(HERE / "logs" / "weekly.log"),
        "StandardErrorPath": str(HERE / "logs" / "weekly.err"),
        "RunAtLoad": False,
    }


def watch_plist(username):
    """Check the page's button every minute and act on it.

    Not a timer that submits: a timer that looks for your press. Every
    submission still begins with you pressing something, which is the
    difference between this and a scheduled run.
    """
    return {
        "Label": WATCH_LABEL,
        "ProgramArguments": [python_bin(), str(HERE / "submitter.py"),
                             username, "--watch"],
        "WorkingDirectory": str(HERE),
        "StartInterval": 60,
        "RunAtLoad": False,
        "StandardOutPath": str(HERE / "logs" / "submit.log"),
        "StandardErrorPath": str(HERE / "logs" / "submit.err"),
    }


def submit_plist(weekday, hour, minute):
    """Place approved claims, on a schedule, shortly before waivers process.

    Separate from the weekly job and off by default. Filing proposals is
    reading; placing claims reaches into a league, and starting that on a
    timer is a decision to make once rather than a default to inherit.

    It only ever reads claims already approved on the page, so an unattended
    run can do nothing you have not already agreed to.
    """
    return {
        "Label": SUBMIT_LABEL,
        "ProgramArguments": [python_bin(), str(HERE / "submitter.py")],
        "WorkingDirectory": str(HERE),
        "StartCalendarInterval": [{"Weekday": weekday, "Hour": hour,
                                   "Minute": minute}],
        "StandardOutPath": str(HERE / "logs" / "submit.log"),
        "StandardErrorPath": str(HERE / "logs" / "submit.err"),
        "RunAtLoad": False,
    }


def web_plist(host, port):
    return {
        "Label": WEB_LABEL,
        "ProgramArguments": [python_bin(), str(HERE / "webapp.py"),
                             "--host", host, "--port", str(port)],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(HERE / "logs" / "web.log"),
        "StandardErrorPath": str(HERE / "logs" / "web.err"),
    }


def write_and_load(label, data):
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    (HERE / "logs").mkdir(exist_ok=True)
    path = LAUNCH_DIR / f"{label}.plist"
    path.write_bytes(plistlib.dumps(data))
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                   capture_output=True)
    r = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        # Older macOS, or already loaded: fall back to the legacy verb.
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(path)],
                           capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"  {'installed' if ok else 'FAILED  '} {label}  ({path.name})")
    if not ok and r.stderr.strip():
        print(f"    {r.stderr.strip()}")
    return ok


def unload(label):
    path = LAUNCH_DIR / f"{label}.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                   capture_output=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    if path.exists():
        path.unlink()
        print(f"  removed {label}")
    else:
        print(f"  {label} was not installed")


def reachable_urls(port):
    """Addresses this Mac's approval page can be opened at, phone included."""
    urls = [("this Mac", f"http://127.0.0.1:{port}")]

    def run(cmd):
        """Missing tools are normal - Tailscale may not be installed."""
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    for ip in (run(["tailscale", "ip", "-4"]) or "").split():
        urls.append(("phone, over Tailscale", f"http://{ip}:{port}"))
    for iface in ("en0", "en1"):
        ip = run(["ipconfig", "getifaddr", iface])
        if ip:
            urls.append((f"phone, same wifi ({iface})", f"http://{ip}:{port}"))
    return urls


def status():
    out = subprocess.run(["launchctl", "list"], capture_output=True,
                         text=True).stdout
    for label in (WEEKLY_LABEL, WEB_LABEL, SUBMIT_LABEL,
                  WATCH_LABEL):
        installed = (LAUNCH_DIR / f"{label}.plist").exists()
        running = label in out
        print(f"  {label}: "
              f"{'installed' if installed else 'not installed'}"
              f"{', loaded' if running else ''}")
    urls = reachable_urls(8777)
    print("\n  open the approval page at:")
    for label, url in urls:
        print(f"    {url:<28} {label}")
    if not any("Tailscale" in label for label, _ in urls):
        print("\n  Only reachable from home. A 192.168.x address is your wifi")
        print("  network and means nothing from anywhere else. To reach the")
        print("  page from work, install Tailscale on this Mac and your phone")
        print("  (free, private, nothing exposed to the internet):")
        print("      brew install --cask tailscale")
        print("  then sign both into the same account and re-run --status.")
        print("  Note the Mac has to be awake to answer, whichever route.")

    log = HERE / "logs" / "weekly.log"
    if log.exists():
        tail = log.read_text(errors="replace").strip().splitlines()[-3:]
        if tail:
            print("\n  last weekly run:")
            for line in tail:
                print(f"    {line}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", help="Sleeper username the weekly job runs for")
    ap.add_argument("--day", default="mon", choices=sorted(DAYS))
    ap.add_argument("--hour", type=int, default=8)
    ap.add_argument("--minute", type=int, default=30)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to reach the page from your phone")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--wake", action="store_true",
                    help="also schedule a system wake before the job")
    ap.add_argument("--watch", action="store_true",
                    help="act on the page's 'place them now' button, checking "
                         "every minute (recommended over --submit)")
    ap.add_argument("--submit", action="store_true",
                    help="also place approved claims on a schedule "
                         "(off by default; it reaches into your leagues)")
    ap.add_argument("--submit-day", default="tue", choices=sorted(DAYS))
    ap.add_argument("--submit-hour", type=int, default=21)
    ap.add_argument("--submit-minute", type=int, default=0)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("launchd is macOS-only. On Linux use systemd timers or cron.")
        return 1

    if args.status:
        status()
        return 0
    if args.uninstall:
        unload(WEEKLY_LABEL)
        unload(WEB_LABEL)
        unload(SUBMIT_LABEL)
        unload(WATCH_LABEL)
        return 0
    if not args.user:
        print("Need --user YOUR_SLEEPER_USERNAME (or --status / --uninstall)")
        return 1

    print("Installing:")
    ok = write_and_load(WEEKLY_LABEL, weekly_plist(
        args.user, DAYS[args.day], args.hour, args.minute))
    ok &= write_and_load(WEB_LABEL, web_plist(args.host, args.port))
    if args.watch:
        ok &= write_and_load(WATCH_LABEL, watch_plist(args.user))
    else:
        unload(WATCH_LABEL)
    if args.submit:
        ok &= write_and_load(SUBMIT_LABEL, submit_plist(
            DAYS[args.submit_day], args.submit_hour, args.submit_minute))
    else:
        # Removed rather than left behind, so turning it off is one command.
        unload(SUBMIT_LABEL)

    if args.wake:
        when = f"{args.day.upper()} {args.hour:02d}:{max(0, args.minute - 5):02d}:00"
        r = subprocess.run(["sudo", "pmset", "repeat", "wakeorpoweron", when],
                           capture_output=True, text=True)
        print(f"  {'wake scheduled' if r.returncode == 0 else 'wake FAILED'}"
              f" ({when})")

    print()
    print(f"Approval page: http://127.0.0.1:{args.port}")
    if args.host == "0.0.0.0":
        print("Bound to all interfaces — open it from your phone using this "
              "Mac's Tailscale or LAN address.")
    else:
        print("Bound to localhost. Re-run with --host 0.0.0.0 to reach it "
              "from your phone.")
    print(f"Weekly job: {args.day.title()} {args.hour:02d}:{args.minute:02d}, "
          f"logging to logs/weekly.log")
    if args.watch:
        print("Submitter: waiting for the page's button, checked every "
              "minute, logging to logs/submit.log")
        print("  Nothing is placed until you press it. Chrome has to be "
              "running and signed in to Sleeper.")
    if args.submit:
        print(f"Submitter: {args.submit_day.title()} "
              f"{args.submit_hour:02d}:{args.submit_minute:02d}, placing only "
              "claims you have approved, logging to logs/submit.log")
        print("  It drives Chrome, so that Mac has to be awake and signed in "
              "to Sleeper.")
    elif not args.watch:
        print("Submitter: not running. Approved claims wait for "
              "`python3 submitter.py`.")
        print("  --watch acts on the page's button; --submit runs it on a "
              "timer instead.")
    print("\nCheck anytime with:  python3 install_schedule.py --status")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
