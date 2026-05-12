from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import load_config
from .dailylog_md import (
    append_unmatched_commits,
    parse_heading_date,
    patch_daily_logs,
    patch_short_term_goal,
    render_new_day_from_commits,
    render_new_day_from_suggestions,
    render_new_day_template,
)
from .github_api import (
    commits_by_day_multi,
    create_issue_comment,
    enrich_commits_with_file_urls,
    fetch_issue,
    fetch_issue_comments,
    patch_issue_comment,
)
from .overleaf import list_commits, render_summary_markdown, summarize_by_day
from .planning import rank_suggestions, suggest_from_paths, suggest_from_text
from .restore import load_backup_comments, max_day


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser(prog="dailylog")
    ap.add_argument("--config", default="env.yaml")
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--until", help="YYYY-MM-DD")
    ap.add_argument("--today", action="store_true", help="Set --since/--until to today's date (Asia/Taipei)")
    ap.add_argument(
        "--resume-from-latest",
        action="store_true",
        help="Continue from the day after your latest existing daily-log comment (sets --since automatically)",
    )
    ap.add_argument(
        "--max-create",
        type=int,
        default=0,
        help="Max number of new daily comments to create in this run (0 = unlimited)",
    )
    ap.add_argument(
        "--restore-from-json",
        help="Path to a JSON backup of issue comments to restore/backfill from",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ensure-comments", action="store_true", help="Create missing ### yyyy/mm/dd comments")
    ap.add_argument("--seed-from-commits", action="store_true", help="Seed new day comment Daily-logs from commits")
    ap.add_argument(
        "--seed-from-plans",
        action="store_true",
        help="Seed new day comment Daily-logs from your milestones/backlog (planning.sources)",
    )
    ap.add_argument(
        "--update-existing-targets",
        action="store_true",
        help="If today's comment exists, update **Short-term Goal** from plans/checklist instead of creating a second comment",
    )
    ap.add_argument(
        "--append-unmatched-commits",
        action="store_true",
        help="Append commits as new bullets under **Daily-logs** when not already referenced",
    )
    ap.add_argument(
        "--skip-if-no-activity",
        action="store_true",
        help="With --ensure-comments, skip creating a new day comment when no commits exist for that day",
    )
    ap.add_argument("--include-overleaf", action="store_true")
    ap.add_argument(
        "--link-to-files",
        action="store_true",
        help="Resolve each commit to a specific file blob URL (7-char hash). Makes one extra API call per commit.",
    )
    ap.add_argument(
        "--generate-reminder",
        metavar="PATH",
        nargs="?",
        const="",
        help="Write reminder.md listing missing dates and activities without evidence. "
        "PATH defaults to reminder.output_path from config.",
    )
    ap.add_argument(
        "--check-calendar",
        action="store_true",
        help="Print upcoming calendar events for the date range (requires google_calendar.enabled: true)",
    )
    args = ap.parse_args()

    if args.dry_run and args.apply:
        raise SystemExit("Use only one of --dry-run or --apply")
    # --generate-reminder and --check-calendar don't require --dry-run/--apply
    if not args.dry_run and not args.apply and args.generate_reminder is None and not args.check_calendar:
        raise SystemExit("Specify --dry-run or --apply (or use --generate-reminder / --check-calendar)")

    cfg = load_config(args.config)

    # Resolve planning.sources relative to the config file.
    config_path = Path(args.config)
    repo_root = config_path.resolve().parent if config_path.exists() else Path.cwd()

    # Use Taiwan day boundary to match commit/day bucketing.
    now_taipei = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()

    # Determine until first (so resume mode can default to today).
    if args.today:
        until = now_taipei
    elif args.until:
        until = date.fromisoformat(args.until)
    else:
        until = now_taipei

    # 1) Issue comments (needed early for resume mode)
    comments = fetch_issue_comments(cfg.github.repo_owner, cfg.github.repo_name, cfg.github.issue_number)
    my_comments = [c for c in comments if (c.get("user") or {}).get("login") == cfg.github.login]

    # map day -> candidate comments (all, not yet filtered)
    day_to_comments: dict[date, list[dict]] = {}
    for c in my_comments:
        d = parse_heading_date(c.get("body") or "")
        if not d:
            continue
        day_to_comments.setdefault(d, []).append(c)

    for d in list(day_to_comments.keys()):
        day_to_comments[d].sort(key=lambda c: c.get("created_at") or "")

    latest_existing = max(day_to_comments.keys()) if day_to_comments else None

    if args.restore_from_json:
        if args.since or args.resume_from_latest:
            raise SystemExit("--restore-from-json automatically resumes from latest; do not use --since/--resume-from-latest")
        if not latest_existing:
            raise SystemExit("No existing daily-log comments found; cannot infer resume point")

        backup = load_backup_comments(args.restore_from_json)
        backup_end = max_day(backup)
        if not backup_end:
            raise SystemExit("Backup JSON contained no daily-log comments")

        since = latest_existing + timedelta(days=1)
        until = date.fromisoformat(args.until) if args.until else backup_end
        if since > until:
            raise SystemExit(
                f"Nothing to restore: since {since.isoformat()} is after until {until.isoformat()}"
            )

        created = 0
        backup_by_day = {c.day: c for c in backup}

        cur = since
        while cur <= until:
            if cur in day_to_comments:
                cur += timedelta(days=1)
                continue
            bc = backup_by_day.get(cur)
            if not bc:
                cur += timedelta(days=1)
                continue

            if args.dry_run:
                print(f"{cur.isoformat()}: would restore daily-log comment from backup")
            else:
                created_c = create_issue_comment(
                    cfg.github.repo_owner, cfg.github.repo_name, cfg.github.issue_number, bc.body
                )
                print(f"{cur.isoformat()}: restored daily-log comment {created_c.get('id')}")
                day_to_comments[cur] = [created_c]

            created += 1
            if args.max_create and created >= args.max_create:
                break
            cur += timedelta(days=1)

        print("Done.")
        return 0

    if args.resume_from_latest:
        if args.since:
            raise SystemExit("Use either --since or --resume-from-latest (not both)")
        if not day_to_comments:
            raise SystemExit("No existing daily-log comments found to resume from")
        latest = max(day_to_comments.keys())
        since = latest + timedelta(days=1)
    elif args.today:
        since = until
    else:
        if not args.since:
            raise SystemExit("Provide --since (or use --today / --resume-from-latest)")
        since = date.fromisoformat(args.since)

    if since > until:
        raise SystemExit(f"Invalid range: since {since.isoformat()} is after until {until.isoformat()}")

    # 2) Calendar events (optional)
    cal_events_by_day: dict[date, list] = {}
    if cfg.google_calendar.enabled or args.check_calendar:
        if not cfg.google_calendar.enabled:
            print("Warning: --check-calendar requested but google_calendar.enabled is false in config.")
        else:
            try:
                from .gcal import events_by_day, fetch_events, get_calendar_service, print_events

                service = get_calendar_service(
                    cfg.google_calendar.credentials_file, cfg.google_calendar.token_file
                )
                events = fetch_events(service, cfg.google_calendar.calendar_id, since, until)
                cal_events_by_day = events_by_day(events)
                if args.check_calendar:
                    print_events(events, since, until)
            except Exception as exc:
                print(f"Warning: could not fetch Google Calendar events: {exc}")

    # 3) Commits
    src = cfg.github.contribution_sources
    day_to_commits = commits_by_day_multi(
        login=cfg.github.login,
        orgs=list(src.orgs),
        repo_owners=list(src.repo_owners),
        repos=list(src.repos),
        since=since,
        until=until,
    )

    # Optionally resolve commit URLs to specific file blob URLs
    if args.link_to_files and day_to_commits:
        print("Fetching commit file details (one API call per commit)…")
        day_to_commits = enrich_commits_with_file_urls(day_to_commits)

    # Filter day_to_comments down to range for subsequent work
    day_to_comments = {d: cs for d, cs in day_to_comments.items() if since <= d <= until}

    # 4) Ensure missing daily comment exists
    if args.ensure_comments:
        created_count = 0
        for d in daterange(since, until):
            if d in day_to_comments:
                continue

            commits = day_to_commits.get(d) or []
            seed_plans = args.seed_from_plans or (
                getattr(cfg, "planning", None) is not None and cfg.planning.enabled
            )
            seed_commits = args.seed_from_commits or cfg.github.seed_new_day_from_commits

            if args.skip_if_no_activity and not commits:
                continue

            if seed_plans:
                planning = getattr(cfg, "planning", None)
                sources = list(getattr(planning, "sources", []) or [])
                max_items = int(getattr(planning, "max_suggestions", 6) or 6)
                include_backlog = bool(getattr(planning, "include_backlog", True))

                collected = []
                seen: set[str] = set()

                if any(s in ("github_issue_body", "github:issue_body", "github_issue") for s in sources):
                    issue = fetch_issue(cfg.github.repo_owner, cfg.github.repo_name, cfg.github.issue_number)
                    issue_body = str(issue.get("body") or "")
                    issue_title = str(issue.get("title") or "")
                    issue_items = suggest_from_text(
                        issue_body,
                        source=f"github:{cfg.github.repo_owner}/{cfg.github.repo_name}#{cfg.github.issue_number}:{issue_title}",
                        max_items=max_items,
                        include_backlog=include_backlog,
                    )
                    for it in issue_items:
                        k = it.text.strip().lower()
                        if k and k not in seen:
                            collected.append(it)
                            seen.add(k)

                file_sources = [
                    s for s in sources if s not in ("github_issue_body", "github:issue_body", "github_issue")
                ]
                if len(collected) < max_items and file_sources:
                    file_items = suggest_from_paths(
                        file_sources,
                        repo_root=repo_root,
                        max_items=max_items - len(collected),
                        include_backlog=include_backlog,
                    )
                    for it in file_items:
                        k = it.text.strip().lower()
                        if k and k not in seen:
                            collected.append(it)
                            seen.add(k)

                ranked = rank_suggestions(collected, base_day=d)
                body = render_new_day_from_suggestions(d, [it.text for it in ranked])
            elif seed_commits:
                body = render_new_day_from_commits(d, commits)
            else:
                body = render_new_day_template(d)

            if args.dry_run:
                print(f"{d.isoformat()}: would create daily-log comment")
                created_count += 1
                if args.max_create and created_count >= args.max_create:
                    break
                continue

            created = create_issue_comment(cfg.github.repo_owner, cfg.github.repo_name, cfg.github.issue_number, body)
            print(f"{d.isoformat()}: created daily-log comment {created.get('id')}")
            day_to_comments[d] = [created]

            created_count += 1
            if args.max_create and created_count >= args.max_create:
                break

    # 5) Patch existing comments (link + end time)
    patched = 0
    for d in daterange(since, until):
        commits = day_to_commits.get(d) or []
        cs = day_to_comments.get(d) or []
        if not cs:
            continue

        c = cs[0]
        body = c.get("body") or ""
        updated = body
        changes = 0

        # Morning mode: update targets even if there are no commits.
        if args.update_existing_targets and (
            args.seed_from_plans or (getattr(cfg, "planning", None) is not None and cfg.planning.enabled)
        ):
            planning = getattr(cfg, "planning", None)
            sources = list(getattr(planning, "sources", []) or [])
            max_items = int(getattr(planning, "max_suggestions", 6) or 6)
            include_backlog = bool(getattr(planning, "include_backlog", True))

            collected = []
            seen: set[str] = set()
            if any(s in ("github_issue_body", "github:issue_body", "github_issue") for s in sources):
                issue = fetch_issue(cfg.github.repo_owner, cfg.github.repo_name, cfg.github.issue_number)
                issue_body = str(issue.get("body") or "")
                issue_title = str(issue.get("title") or "")
                issue_items = suggest_from_text(
                    issue_body,
                    source=f"github:{cfg.github.repo_owner}/{cfg.github.repo_name}#{cfg.github.issue_number}:{issue_title}",
                    max_items=max_items,
                    include_backlog=include_backlog,
                )
                for it in issue_items:
                    k = it.text.strip().lower()
                    if k and k not in seen:
                        collected.append(it)
                        seen.add(k)

            file_sources = [
                s for s in sources if s not in ("github_issue_body", "github:issue_body", "github_issue")
            ]
            if len(collected) < max_items and file_sources:
                file_items = suggest_from_paths(
                    file_sources,
                    repo_root=repo_root,
                    max_items=max_items - len(collected),
                    include_backlog=include_backlog,
                )
                for it in file_items:
                    k = it.text.strip().lower()
                    if k and k not in seen:
                        collected.append(it)
                        seen.add(k)

            ranked = rank_suggestions(collected, base_day=d)

            goal_lines = []
            if ranked:
                for it in ranked:
                    if it.deadline is None:
                        goal_lines.append(f"- {it.text} (no deadline)")
                    else:
                        tag = "overdue" if it.deadline < d else "due"
                        goal_lines.append(f"- {it.text} ({tag} {it.deadline.strftime('%Y/%m/%d')})")
            else:
                goal_lines.append("<Goal>")

            updated2, ch = patch_short_term_goal(updated, goal_lines)
            updated, changes = updated2, (changes + ch)

        # Normal mode: patch commit links/end-times.
        if commits:
            updated2, ch = patch_daily_logs(updated, commits)
            updated, changes = updated2, (changes + ch)

        if commits:
            do_append = args.append_unmatched_commits or cfg.github.append_unmatched_commits
            if do_append:
                updated2, appended = append_unmatched_commits(updated, commits)
                updated, changes = updated2, (changes + appended)

        if changes == 0:
            continue

        comment_id = int(c["id"])
        if args.dry_run:
            print(f"{d.isoformat()}: would patch comment {comment_id} ({changes} line(s))")
        else:
            patch_issue_comment(cfg.github.repo_owner, cfg.github.repo_name, comment_id, updated)
            print(f"{d.isoformat()}: patched comment {comment_id} ({changes} line(s))")

        patched += 1

    # 6) Optional Overleaf summary (append at end of day comment)
    if args.include_overleaf and cfg.overleaf.enabled and cfg.overleaf.projects:
        for proj in cfg.overleaf.projects:
            ov_commits = list_commits(proj.repo_path, since, until)
            by_day = summarize_by_day(ov_commits)
            for d in daterange(since, until):
                cs = day_to_comments.get(d) or []
                if not cs:
                    continue
                summary = render_summary_markdown(proj.name, d, by_day.get(d) or [])
                c = cs[0]
                comment_id = int(c["id"])
                body = c.get("body") or ""
                if summary.strip() in body:
                    continue
                updated = body.rstrip() + "\n\n" + summary
                if args.dry_run:
                    print(f"{d.isoformat()}: would append Overleaf summary to {comment_id} ({proj.name})")
                else:
                    patch_issue_comment(cfg.github.repo_owner, cfg.github.repo_name, comment_id, updated)
                    print(f"{d.isoformat()}: appended Overleaf summary to {comment_id} ({proj.name})")

    # 7) Generate reminder.md
    if args.generate_reminder is not None:
        from .reminder import generate_reminder, get_activities_without_evidence, get_missing_dates

        existing_days = set(day_to_comments.keys())
        missing = get_missing_dates(since, until, existing_days)

        range_comments = [c for d in daterange(since, until) for c in (day_to_comments.get(d) or [])]
        activities_no_evidence = get_activities_without_evidence(
            range_comments, cal_events_by_day, cfg.google_calendar.class_keywords
        )

        output_path = args.generate_reminder or cfg.reminder.output_path
        generate_reminder(missing, activities_no_evidence, output_path)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
