#!/usr/bin/env python3
"""
Fill in missing daily-log entries from Google Calendar ICS data.

Reads events from a local .ics export or the private iCal URL configured in
env.local.yaml, then cross-references with missing dates on
bmw-ece-ntust/progress-plan#366.

Usage:
  # Auto-reads ical_url from env.local.yaml (recommended)
  python fill-from-calendar.py

  # Pass an ICS file or URL explicitly
  python fill-from-calendar.py calendar.ics
  python fill-from-calendar.py "https://calendar.google.com/calendar/ical/.../basic.ics"

  # OAuth browser flow (needs credentials.json from Google Cloud Console)
  python fill-from-calendar.py --oauth

Flags:
  --since YYYY-MM-DD   Start of gap-check window (default: 2023-09-01)
  --until YYYY-MM-DD   End of gap-check window   (default: today)
  --create             Create daily-log comments for missing days with events
  --dry-run            Show what would be created without posting to GitHub
  --all                Also audit days that already have a daily-log entry
  --show-excluded      Print events that were filtered out as personal
"""
from __future__ import annotations

import sys
import json
import re
import argparse
import subprocess
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

OWNER = "bmw-ece-ntust"
REPO = "progress-plan"
ISSUE = 366
TAIPEI_TZ = timezone(timedelta(hours=8))
DATE_HEADING_RE = re.compile(r"^###\s+(\d{4}/\d{2}/\d{2})", re.MULTILINE)
DEFAULT_SINCE = "2023-09-01"
LOCAL_CONFIG = Path(__file__).parent / "env.local.yaml"

DEFAULT_EXCLUDE_KEYWORDS = [
    "worship", "ifgf", "icare", "take transit", "take arc",
    "birthday", "anniversary",
]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_local_config() -> dict:
    if not LOCAL_CONFIG.exists():
        return {}
    if yaml is None:
        raise ImportError("Run: pip install pyyaml")
    with open(LOCAL_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def gcal_cfg() -> dict:
    return load_local_config().get("google_calendar") or {}


def get_ical_url() -> str | None:
    return gcal_cfg().get("ical_url") or None


def get_calendar_id() -> str:
    return gcal_cfg().get("calendar_id") or "primary"


def get_exclude_keywords() -> list[str]:
    kw = gcal_cfg().get("exclude_keywords")
    if kw is None:
        return DEFAULT_EXCLUDE_KEYWORDS
    return [str(k).lower() for k in kw]


# ---------------------------------------------------------------------------
# ICS loading & parsing
# ---------------------------------------------------------------------------

def load_ics_bytes(source: str) -> bytes:
    if source.startswith("http://") or source.startswith("https://"):
        print(f"  Fetching iCal feed ...")
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"ICS file not found: {path}")
    print(f"  Reading {path.name}")
    return path.read_bytes()


def _to_taipei_datetime(val) -> datetime | None:
    """Convert a vDDDTypes value to a Taipei-localised datetime, or None for all-day."""
    if isinstance(val, datetime):
        if val.tzinfo:
            return val.astimezone(TAIPEI_TZ)
        return val.replace(tzinfo=TAIPEI_TZ)
    return None  # date (all-day event) — skip for time-based bullets


def parse_events(raw: bytes) -> list[dict]:
    """Parse ICS bytes → list of normalised event dicts."""
    try:
        import icalendar
    except ImportError:
        raise ImportError("Run: pip install icalendar")

    cal = icalendar.Calendar.from_ical(raw)
    seen_keys: set[tuple] = set()
    events: list[dict] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        start_raw = component.get("DTSTART")
        end_raw = component.get("DTEND")
        if start_raw is None:
            continue

        start_dt = _to_taipei_datetime(start_raw.dt)
        if start_dt is None:
            continue  # all-day event — no time info to put in daily-log

        end_dt = _to_taipei_datetime(end_raw.dt) if end_raw else start_dt
        summary = str(component.get("SUMMARY") or "").strip()

        # Deduplicate on (start, end, summary) — catches recurring invites
        # that appear twice with different UIDs or RECURRENCE-IDs
        dedup_key = (start_dt, end_dt, summary.lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        events.append({
            "summary": summary,
            "start": start_dt,
            "end": end_dt,
            "date": start_dt.date(),
            "location": str(component.get("LOCATION") or "").strip(),
        })

    return events


def filter_work_events(events: list[dict], exclude_kw: list[str]) -> tuple[list[dict], list[dict]]:
    """Split events into (work, excluded) based on exclude_keywords."""
    work, excluded = [], []
    for ev in events:
        title_lower = ev["summary"].lower()
        if any(kw in title_lower for kw in exclude_kw):
            excluded.append(ev)
        else:
            work.append(ev)
    return work, excluded


def group_by_day(events: list[dict]) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for ev in events:
        out.setdefault(ev["date"], []).append(ev)
    # Sort each day's events by start time
    for d in out:
        out[d].sort(key=lambda e: e["start"])
    return out


# ---------------------------------------------------------------------------
# Daily-log formatting
# ---------------------------------------------------------------------------

def hhmm(dt: datetime) -> str:
    """Format datetime as HH.MM (dot notation used in daily-log)."""
    return dt.strftime("%H.%M")


def render_bullet(ev: dict) -> str:
    """Format one calendar event as a daily-log bullet line."""
    start = hhmm(ev["start"])
    end = hhmm(ev["end"])
    summary = ev["summary"]
    loc = f" @{ev['location']}" if ev["location"] else ""
    return f"- `{start} - {end}`: {summary}{loc}"


def render_day_comment(d: date, events: list[dict]) -> str:
    ds = d.strftime("%Y/%m/%d")
    lines = [
        f"### {ds}",
        "",
        "**Short-term Goal**:",
        "<Goal>",
        "",
        "**Daily-logs**:",
    ]
    for ev in events:
        lines.append(render_bullet(ev))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OAuth fallback (existing gcal.py)
# ---------------------------------------------------------------------------

def fetch_via_oauth(calendar_id: str, since: date, until: date) -> list[dict]:
    src_dir = Path(__file__).parent / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from dailylog.gcal import get_calendar_service, fetch_events, event_start_date

    svc = get_calendar_service(
        "~/.config/dailylog/credentials.json",
        "~/.config/dailylog/token.json",
    )
    raw = fetch_events(svc, calendar_id, since, until)
    out = []
    for ev in raw:
        d = event_start_date(ev)
        if d:
            out.append({
                "uid": ev.get("id") or "",
                "summary": (ev.get("summary") or "").strip(),
                "start": datetime.combine(d, datetime.min.time(), tzinfo=TAIPEI_TZ),
                "end": datetime.combine(d, datetime.min.time(), tzinfo=TAIPEI_TZ),
                "date": d,
                "location": (ev.get("location") or "").strip(),
            })
    return out


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def fetch_logged_dates() -> set[date]:
    raw = gh("issue", "view", str(ISSUE), "-R", f"{OWNER}/{REPO}", "--json", "comments")
    comments = json.loads(raw).get("comments", [])
    dates: set[date] = set()
    for c in comments:
        m = DATE_HEADING_RE.search(c.get("body") or "")
        if m:
            try:
                dates.add(datetime.strptime(m.group(1), "%Y/%m/%d").date())
            except ValueError:
                pass
    return dates


def post_comment(body: str) -> str:
    return gh("issue", "comment", str(ISSUE), "-R", f"{OWNER}/{REPO}", "-b", body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("source", nargs="?",
                    help="ICS file path or URL (default: ical_url from env.local.yaml)")
    ap.add_argument("--oauth", action="store_true",
                    help="Use OAuth2 browser flow instead of iCal URL")
    ap.add_argument("--since", default=DEFAULT_SINCE, metavar="YYYY-MM-DD")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--create", action="store_true",
                    help="Post daily-log comments for missing days that have events")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --create: print what would be posted without posting")
    ap.add_argument("--all", action="store_true",
                    help="Also show days that already have a daily-log (for auditing)")
    ap.add_argument("--show-excluded", action="store_true",
                    help="Print events that were filtered out as personal")
    args = ap.parse_args()

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else date.today()
    exclude_kw = get_exclude_keywords()

    # -- Resolve source -------------------------------------------------------
    source = args.source
    if not source and not args.oauth:
        url = get_ical_url()
        if url:
            source = url
        else:
            ap.error(
                "No iCal source found. Either pass a file/URL, use --oauth, "
                "or set google_calendar.ical_url in env.local.yaml"
            )

    # -- Load events ----------------------------------------------------------
    print(f"\nLoading calendar events ({since} → {until}) ...")
    if args.oauth:
        all_events = fetch_via_oauth(get_calendar_id(), since, until)
    else:
        raw_bytes = load_ics_bytes(source)
        all_events = parse_events(raw_bytes)
        all_events = [e for e in all_events if since <= e["date"] <= until]

    work_events, excluded_events = filter_work_events(all_events, exclude_kw)
    print(f"  {len(all_events)} total timed events  |  "
          f"{len(work_events)} work  |  {len(excluded_events)} excluded as personal")

    if args.show_excluded:
        print("\n  Excluded events:")
        for ev in excluded_events:
            print(f"    {ev['date']}  {ev['summary']}")

    by_day = group_by_day(work_events)

    # -- Fetch logged dates ---------------------------------------------------
    print(f"\nFetching existing daily-log entries ...")
    logged = fetch_logged_dates()
    print(f"  {len(logged)} existing dated entries\n")

    # -- Missing days with events ---------------------------------------------
    missing_with = sorted(
        [(d, evs) for d, evs in by_day.items() if d not in logged and d.weekday() < 5],
        key=lambda x: x[0],
    )
    missing_without = sorted(
        d for d in _weekdays(since, until) if d not in logged and d not in by_day
    )

    print("=" * 70)
    print(f"MISSING DAYS WITH CALENDAR EVENTS  ({len(missing_with)} days)")
    print("=" * 70)

    created_count = 0
    for d, evs in missing_with:
        print(f"\n  {d.strftime('%Y/%m/%d (%A)')}:")
        for ev in evs:
            print(f"    {render_bullet(ev)}")

        if args.create:
            body = render_day_comment(d, evs)
            if args.dry_run:
                print(f"    [dry-run] would post daily-log comment")
            else:
                url_out = post_comment(body)
                print(f"    [created]")
                created_count += 1

    if args.create and not args.dry_run:
        print(f"\n  {created_count} comment(s) created.")

    # -- Missing days without any event ---------------------------------------
    print()
    print("=" * 70)
    print(f"MISSING DAYS WITH NO CALENDAR EVENTS  ({len(missing_without)} days)")
    print("=" * 70)
    if missing_without:
        months: dict[str, list[str]] = {}
        for d in missing_without:
            months.setdefault(d.strftime("%Y-%m"), []).append(d.strftime("%d"))
        for m, days in months.items():
            print(f"  {m}: {', '.join(days)}")
    else:
        print("  None — every missing weekday has a calendar event!")

    # -- Audit: logged days with events (--all) --------------------------------
    if args.all:
        logged_with = sorted(
            [(d, by_day[d]) for d in logged if since <= d <= until and d in by_day]
        )
        print()
        print("=" * 70)
        print(f"ALREADY-LOGGED DAYS WITH CALENDAR EVENTS  ({len(logged_with)} days)")
        print("(these may need evidence links added manually)")
        print("=" * 70)
        for d, evs in logged_with:
            print(f"\n  {d.strftime('%Y/%m/%d (%A)')}:")
            for ev in evs:
                print(f"    {render_bullet(ev)}")


def _weekdays(since: date, until: date):
    d = since
    while d <= until:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


if __name__ == "__main__":
    main()
