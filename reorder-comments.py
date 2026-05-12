#!/usr/bin/env python3
"""
Analyze, sort, and optionally reorder daily-log GitHub issue comments by date.
Cross-checks missing days against Google Calendar when --gcal is passed.

Usage:
  python reorder-comments.py [--apply] [--gcal] [--since YYYY-MM-DD] [--until YYYY-MM-DD]

  --apply   Delete and recreate out-of-order comments in correct date order
  --gcal    Fetch Google Calendar events to show activities on missing days
  --since   Look for gaps starting from this date (default: 60 days ago)
  --until   Look for gaps up to this date (default: today)
"""

from __future__ import annotations

import sys
import json
import subprocess
import re
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# Fix Windows console UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OWNER = "bmw-ece-ntust"
REPO = "progress-plan"
ISSUE = 366

GCAL_CREDENTIALS = "~/.config/dailylog/credentials.json"
GCAL_TOKEN = "~/.config/dailylog/token.json"
GCAL_ID = "primary"

DATE_HEADING_RE = re.compile(r"^###\s+(\d{4}/\d{2}/\d{2})", re.MULTILINE)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def fetch_comments() -> list[dict]:
    raw = gh("issue", "view", str(ISSUE), "-R", f"{OWNER}/{REPO}", "--json", "comments")
    return json.loads(raw).get("comments", [])


def numeric_id_from_url(url: str) -> str:
    """Extract numeric comment ID from a GitHub comment URL."""
    m = re.search(r"issuecomment-(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse numeric ID from URL: {url}")
    return m.group(1)


def delete_comment(comment_url: str) -> bool:
    numeric_id = numeric_id_from_url(comment_url)
    result = subprocess.run(
        ["gh", "api", "--method", "DELETE",
         f"/repos/{OWNER}/{REPO}/issues/comments/{numeric_id}"],
        capture_output=True, encoding="utf-8",
    )
    return result.returncode == 0


def create_comment(body: str) -> str:
    return gh("issue", "comment", str(ISSUE), "-R", f"{OWNER}/{REPO}", "-b", body)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def extract_heading_date(body: str) -> Optional[datetime]:
    m = DATE_HEADING_RE.search(body)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y/%m/%d")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Google Calendar helpers
# ---------------------------------------------------------------------------

def try_fetch_gcal_events(since: date, until: date) -> dict[date, list[str]]:
    """Return {date: [event summaries]} for the given range via gcal.py."""
    src_dir = Path(__file__).parent / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    try:
        from dailylog.gcal import get_calendar_service, fetch_events, event_start_date  # type: ignore
    except ImportError:
        print("  [!] gcal module not importable — skipping Google Calendar.")
        return {}

    try:
        svc = get_calendar_service(GCAL_CREDENTIALS, GCAL_TOKEN)
        events = fetch_events(svc, GCAL_ID, since, until)
    except FileNotFoundError as exc:
        print(f"  [!] {exc}")
        return {}
    except Exception as exc:
        print(f"  [!] Calendar fetch failed: {exc}")
        return {}

    by_day: dict[date, list[str]] = {}
    for ev in events:
        d = event_start_date(ev)
        if d:
            by_day.setdefault(d, []).append(ev.get("summary") or "(no title)")
    return by_day


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def missing_weekdays(a: datetime, b: datetime, since: datetime, until: datetime) -> list[datetime]:
    """Return weekdays in (a, b) that fall within [since, until]."""
    out = []
    check = a + timedelta(days=1)
    while check < b:
        if check.weekday() < 5 and since <= check <= until:
            out.append(check)
        check += timedelta(days=1)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Delete and recreate out-of-order comments in chronological order")
    parser.add_argument("--gcal", action="store_true",
                        help="Cross-check missing days against Google Calendar")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="Gap-check window start (default: 60 days ago)")
    parser.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                        help="Gap-check window end (default: today)")
    args = parser.parse_args()

    since_dt = (
        datetime.strptime(args.since, "%Y-%m-%d") if args.since
        else datetime.now() - timedelta(days=60)
    )
    until_dt = (
        datetime.strptime(args.until, "%Y-%m-%d") if args.until
        else datetime.now()
    )

    print(f"Fetching comments from {OWNER}/{REPO}#{ISSUE} ...")
    comments = fetch_comments()
    print(f"  {len(comments)} total comments\n")

    # Separate dated from undated
    dated = []
    for c in comments:
        dt = extract_heading_date(c["body"])
        if dt:
            dated.append({**c, "date": dt})

    print(f"  {len(dated)} dated daily-log entries")
    print(f"  {len(comments) - len(dated)} undated/administrative entries\n")

    # -----------------------------------------------------------------------
    # Sort by date (reference order)
    # -----------------------------------------------------------------------
    sorted_dated = sorted(dated, key=lambda x: x["date"])

    # -----------------------------------------------------------------------
    # Gap report
    # -----------------------------------------------------------------------
    print("=" * 70)
    print(f"GAPS — missing weekdays between {since_dt.strftime('%Y-%m-%d')} and {until_dt.strftime('%Y-%m-%d')}")
    print("=" * 70)

    cal_events: dict[date, list[str]] = {}
    if args.gcal:
        print("  Fetching Google Calendar events ...")
        cal_events = try_fetch_gcal_events(since_dt.date(), until_dt.date())

    all_missing: list[datetime] = []
    for i in range(len(sorted_dated) - 1):
        gaps = missing_weekdays(sorted_dated[i]["date"], sorted_dated[i + 1]["date"], since_dt, until_dt)
        all_missing.extend(gaps)

    # Also check tail (last entry → until_dt)
    if sorted_dated:
        tail_gaps = missing_weekdays(sorted_dated[-1]["date"], until_dt + timedelta(days=1), since_dt, until_dt)
        all_missing.extend(tail_gaps)

    if all_missing:
        for missing in sorted(set(all_missing)):
            label = missing.strftime("%Y/%m/%d (%A)")
            day_key = missing.date()
            if cal_events and day_key in cal_events:
                cal_str = "; ".join(cal_events[day_key])
                print(f"  MISSING: {label}  <-- Calendar: {cal_str}")
            else:
                print(f"  MISSING: {label}")
    else:
        print("  No gaps detected in the window.")

    # -----------------------------------------------------------------------
    # Out-of-order report
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("CHRONOLOGICAL ORDER CHECK")
    print("=" * 70)

    current_ids = [c["id"] for c in dated]
    sorted_ids = [c["id"] for c in sorted_dated]

    if current_ids == sorted_ids:
        print("  All dated comments are already in chronological order.")
    else:
        print("  Out-of-order entries detected:\n")
        print(f"  {'Pos':>4}  {'Current comment date':<24}  Expected date")
        print(f"  {'-'*4}  {'-'*24}  {'-'*24}")
        for i, (curr_id, exp_id) in enumerate(zip(current_ids, sorted_ids)):
            if curr_id != exp_id:
                curr = next(c for c in dated if c["id"] == curr_id)
                exp = next(c for c in sorted_dated if c["id"] == exp_id)
                curr_ds = curr["date"].strftime("%Y/%m/%d")
                exp_ds = exp["date"].strftime("%Y/%m/%d")
                print(f"  {i:>4}  {curr_ds:<24}  {exp_ds}")

        print()
        if not args.apply:
            print("  Run with --apply to delete and recreate them in chronological order.")
        else:
            print("  --apply: rebuilding all dated comments in sorted order ...\n")
            print("  Step 1 -- deleting all dated comments ...")
            failed = []
            for c in dated:
                ok = delete_comment(c["url"])
                status = "ok" if ok else "FAILED"
                short = c["url"].split("#")[-1]
                print(f"    [{status}] deleted {c['date'].strftime('%Y/%m/%d')} ({short})")
                if not ok:
                    failed.append(c)

            if failed:
                print(f"\n  ERROR: {len(failed)} deletion(s) failed. Aborting recreate step.")
                print("  Manually check the issue -- some comments may have been deleted.")
                sys.exit(1)

            print("\n  Step 2 -- recreating in sorted order ...")
            for c in sorted_dated:
                new_url = create_comment(c["body"])
                print(f"    Recreated {c['date'].strftime('%Y/%m/%d')} -> {new_url}")

            print("\n  Done. All dated comments rebuilt in chronological order.")

    # -----------------------------------------------------------------------
    # Summary table
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("FULL ENTRY LIST (sorted by date)")
    print("=" * 70)
    print(f"  {'Date':<14}  {'Day':<12}  URL anchor")
    print(f"  {'-'*14}  {'-'*12}  {'-'*14}")
    for c in sorted_dated:
        short_url = c["url"].split("#")[-1]
        print(f"  {c['date'].strftime('%Y/%m/%d'):<14}  {c['date'].strftime('%A'):<12}  {short_url}")


if __name__ == "__main__":
    main()
