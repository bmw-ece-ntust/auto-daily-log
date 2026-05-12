# CLAUDE.md — auto-daily-log

Static knowledge snapshot for LLMs. Describes repo layout, conventions, and key behaviours.

---

## Purpose

Automated daily-log tool for BMW Lab students at NTUST. Posts one GitHub issue comment per working day to a designated progress-plan issue, seeded from:
- Git commit history across configured organisations / repos
- Google Calendar events (optional)
- Planning sources (issue body, local markdown files)

Spec: [SOP daily-log.md § Auto Daily-log](https://github.com/bmw-ece-ntust/SOP/blob/master/daily-log.md#auto-daily-log)

---

## Repository Layout

```
auto-daily-log/
├── main.py                        # Entrypoint: adds src/ to path, auto-loads env.yaml
├── env.example.yaml               # Config template — copy to env.yaml (safe to commit)
├── env.local.example.yaml         # Template for gitignored private overrides → env.local.yaml
├── reorder-comments.py            # Standalone: gap analysis + chronological reorder of issue comments
├── fill-from-calendar.py          # Standalone: fill missing entries from ICS/iCal feed or OAuth
├── requirements.txt               # Runtime deps: PyYAML; optional Google Calendar libs
├── .sop-hash                      # SHA-256[:16] of the SOP ## Auto Daily-log section
├── .claude/
│   └── settings.local.json        # Project-local Claude Code permissions (bypassPermissions)
├── .github/
│   ├── copilot-instructions.md    # Per-repo Copilot context (project purpose, conventions)
│   └── workflows/
│       └── sop-check.yml          # Midnight weekday check against SOP (needs SOP_READ_TOKEN secret)
├── lab-automation/
│   ├── setup-memory.sh            # Bootstrap: writes ~/.claude/settings.json, mcp.json, ~/CLAUDE.md, ~/.copilot/instructions.md
│   ├── global-claude.md           # Source for ~/CLAUDE.md (global Claude memory instructions)
│   └── global-copilot.md          # Source for ~/.copilot/instructions.md (global Copilot instructions)
└── src/dailylog/
    ├── cli.py                     # Argument parser and main orchestration
    ├── config.py                  # YAML → frozen dataclasses (AppConfig and sub-configs)
    ├── github_api.py              # GitHub commit search + issue comment CRUD via gh CLI (utf-8 safe)
    ├── dailylog_md.py             # Markdown parse/patch: bullet regex, time-range update, commit linking
    ├── gcal.py                    # Google Calendar OAuth2 integration
    ├── planning.py                # Suggestion ranking from issue body or local markdown files
    ├── overleaf.py                # Overleaf local-git integration (summarise by day)
    ├── llm.py                     # Optional OpenAI-compatible LLM provider
    ├── reminder.py                # Generate reminder.md (missing dates + no-evidence activities)
    ├── restore.py                 # Restore daily-log comments from a JSON backup
    ├── __init__.py
    └── __main__.py                # python -m dailylog entrypoint
```

---

## Config

### `env.yaml` (committed, no secrets)

Copy `env.example.yaml` → `env.yaml`.

| Field | Purpose |
|---|---|
| `github.login` | Your GitHub username (used to attribute commits) |
| `github.contribution_sources.orgs` | Orgs to scan for your commits |
| `github.contribution_sources.repo_owners` | Additional users whose repos to include |
| `github.contribution_sources.repos` | Specific `owner/repo` entries |
| `github.repo_owner / repo_name / issue_number` | Target issue for daily-log comments |
| `github.seed_new_day_from_commits` | Auto-seed new day bullets from commits |
| `github.append_unmatched_commits` | Append unreferenced commits as new bullets |
| `timeline.lookback_days` | Default window for missing-date checks |
| `timeline.start_date` | Historical start for full backfill |
| `google_calendar.*` | Optional OAuth2 calendar integration |
| `planning.*` | Optional task suggestion seeding |
| `overleaf.*` | Optional Overleaf local-git summary |
| `llm.*` | Optional OpenAI-compatible LLM analysis |

### `env.local.yaml` (gitignored, secrets)

Copy `env.local.example.yaml` → `env.local.yaml`. Used by `fill-from-calendar.py`.

| Field | Purpose |
| --- | --- |
| `google_calendar.ical_url` | Private iCal URL (Google Calendar → Settings → "Secret address in iCal format") |
| `google_calendar.calendar_id` | Calendar ID for OAuth path (e.g. `user@gapps.ntust.edu.tw`) |
| `google_calendar.exclude_keywords` | Substrings that mark an event as personal (case-insensitive); matched events are skipped |

---

## Daily-Log Comment Format

```markdown
### YYYY/MM/DD

**Short-term Goal**:
<one-line goal>

**Daily-logs**:
- `HH.MM - HH.MM`: [description](https://github.com/org/repo/blob/abc1234/path/file.md#section)
- `HH.MM - `: <ongoing activity>
```

Rules:
- Date heading: `### YYYY/MM/DD` (Asia/Taipei timezone)
- Time ticks use **dot notation**: `HH.MM` (not colons)
- Evidence link must point to a **specific file blob URL** with 7-char commit hash + anchor
- Special time-tick values: `` `SICK LEAVE` ``, `` `HOLIDAY` ``, `` `ABSENT` ``

> **Note:** The SOP example uses `HH:MM` colon notation. This tool normalises to `HH.MM` dots.
> The `.sop-hash` workflow will alert if the SOP section changes.

---

## Authentication

| Service | Method |
| --- | --- |
| GitHub | `gh auth login` — no tokens in files |
| Google Calendar (OAuth2) | `credentials.json` from Google Cloud Console, cached `token.json`; browser redirect via `InstalledAppFlow.run_local_server` |
| Google Calendar (iCal) | Private iCal URL in `env.local.yaml` — no OAuth needed; acts as a bearer token in the URL |

---

## Key Behaviours

### Markdown parsing (`dailylog_md.py`)

- `parse_heading_date` — extracts `YYYY/MM/DD` from `### YYYY/MM/DD` heading (also handles blockquoted and fallback bubble-header form)
- `patch_daily_logs` — matches bullet activities to commits by title similarity, fills end time and adds evidence link
- `append_unmatched_commits` — adds commits not yet referenced anywhere in the comment
- `patch_short_term_goal` — replaces content under `**Short-term Goal**:` with provided lines
- `render_new_day_*` — templates for seeding a brand-new day comment

### Commit search (`github_api.py`)

- Uses `gh api search/commits` with `committer-date` range (±1 day buffer)
- Bucketed by Asia/Taipei timezone
- Deduplicates across multiple query sources
- `enrich_commits_with_file_urls` — optional, resolves each commit to its primary `.md` file (one extra API call per commit)

### CLI orchestration (`cli.py`)

Order of operations per run:
1. Fetch all existing daily-log comments for the date range
2. Optionally fetch Google Calendar events
3. Fetch commits from all configured sources
4. `--ensure-comments` → create missing day comments (seeded from commits or plans)
   - `--skip-if-no-activity` — skip creating a stub when no commits exist for that day
5. Patch existing comments: fill end times, add evidence links, append unmatched commits
6. `--include-overleaf` → append Overleaf summary
7. `--generate-reminder` → write reminder.md

### Gap analysis and reorder (`reorder-comments.py`)

Standalone script — does not use `env.yaml`.

- Fetches all comments from the configured issue via `gh issue view --json comments`
- Separates dated entries (matching `### YYYY/MM/DD`) from undated/administrative ones
- Reports missing weekdays in a configurable `--since` / `--until` window
- Reports out-of-order dated entries
- `--apply` — deletes all dated comments and recreates them in sorted order (destructive; dry-run by default)
- `--gcal` — cross-checks missing days against Google Calendar (uses `gcal.py` OAuth flow)

### Calendar-seeded backfill (`fill-from-calendar.py`)

Standalone script — reads private config from `env.local.yaml`.

- Source priority: CLI argument (file path or URL) → `ical_url` from `env.local.yaml` → `--oauth` flag
- Parses ICS bytes via the `icalendar` library; converts all datetimes to Asia/Taipei
- Deduplicates recurring meeting invites on `(start, end, summary)` tuple
- Filters personal events via `google_calendar.exclude_keywords` from `env.local.yaml`
- Reports missing weekdays that have calendar events, formatted as ready-to-paste daily-log bullets (`HH.MM` dot notation)
- `--create` — posts a new daily-log comment for each missing day with events
- `--all` — also audits days that already have a log entry (for cross-referencing)

---

## SOP Sync Workflow (`.github/workflows/sop-check.yml`)

Runs every weekday at midnight Taiwan time (16:00 UTC). Requires a repository secret:

| Secret | Value |
|---|---|
| `SOP_READ_TOKEN` | Fine-grained PAT with **Contents: Read-only** on `bmw-ece-ntust/SOP` |

If the `## Auto Daily-log` section hash changes from `.sop-hash`, the workflow opens a GitHub issue labelled `sop-sync`. To acknowledge a change: review the SOP diff, update README/implementation, then update `.sop-hash` and commit.

---

## Long-Term Memory (MySQL)

The `mysql-memory` MCP server is configured globally in `~/.claude/settings.json` and connects directly to `llm_memory` on the BMW Lab VM at `140.118.122.119:3306`.

**Session start** — load last 5 sessions:
```sql
SELECT s.id, s.repo, s.start_at, s.end_at, s.commit_title, ss.summary
FROM sessions s LEFT JOIN session_summaries ss ON ss.session_id = s.id
ORDER BY s.start_at DESC LIMIT 5;
```

**Session end** — insert before committing: `sessions` → `prompts` → `session_summaries`, then `UPDATE sessions SET result_commit` after push. Full procedure: [SOP lab-automation/llm-memory.md](https://github.com/bmw-ece-ntust/SOP/blob/master/lab-automation/llm-memory.md).

Bootstrap a new machine: `bash lab-automation/setup-memory.sh`

---

## Conventions

- Always run `--dry-run` first; switch to `--apply` only after confirming output
- Skip weekends when backfilling — run Mon–Fri ranges only
- Asia/Taipei (GMT+8) is the single authoritative timezone throughout
- Evidence links must be file-specific with a 7-char commit hash anchor (not branch-head links)
