# auto-daily-log

Automated daily-log tool. Posts one GitHub issue comment per working day, seeded from commit history and Google Calendar events. Detects missing entries and activities lacking evidence links.

- **Source**: `src/dailylog/`
- **Entry point**: `main.py`
- **Config**: `env.yaml` (copy from `env.example.yaml`, safe to commit — no secrets)
- **Auth**: `gh` CLI (`gh auth login`) — no secrets stored in files

---

## Quick Claude Prompts

Copy any prompt below and paste it into Claude Code. Replace `<date>` with a real date (`YYYY-MM-DD`).

---

### Recap & full catch-up since a date

> Check my daily-log (bmw-ece-ntust/progress-plan#366) since `<date>` until today.
> For every missing weekday: first look for GitHub commits in the bmw-ece-ntust org (`env.yaml` config), then check my Google Calendar via the iCal URL in `env.local.yaml`. Create a new daily-log comment for each missing day that has activity, seed it with the events found. Then reorder all dated comments chronologically using `reorder-comments.py --apply`. Finally, generate `reminder.md` listing remaining missing days and any bullets without evidence links.

---

### Fill missing entries from GitHub commits only

> Check my daily-log (bmw-ece-ntust/progress-plan#366) since `<date>` until today.
> Run `python main.py --since <date> --ensure-comments --seed-from-commits --skip-if-no-activity --apply` (dry-run first, then apply after confirming). Only create entries for days that actually have commits — skip empty stubs.

---

### Fill missing entries from Google Calendar only

> Check my daily-log (bmw-ece-ntust/progress-plan#366) since `<date>` until today.
> Run `python fill-from-calendar.py --since <date>` (reads iCal URL from `env.local.yaml`) to show missing days with calendar events. Then run with `--create` to post them.

---

### Reorder all comments chronologically

> Reorder all dated comments in bmw-ece-ntust/progress-plan#366 into chronological order.
> Run `python reorder-comments.py --since <date> --until today` to preview, then `--apply` to delete and recreate them in sorted order. Confirm before applying — this posts ~200+ comments.

---

### Log today (or a specific day)

> Add a daily-log entry to bmw-ece-ntust/progress-plan#366 for `<date>`.
> The entry should include: [describe what you did — commits, meetings, tasks].
> Use `### YYYY/MM/DD` heading, `HH.MM` dot-notation time ticks, evidence links where available.

---

### Log a sick leave or holiday

> Add a sick leave entry to bmw-ece-ntust/progress-plan#366 for `<date>`.
> Format: `### YYYY/MM/DD` heading, then under `**Daily-logs**:` a single bullet: `` - `SICK LEAVE` ``.
> For a public holiday use `` `HOLIDAY` `` instead.

---

### Generate reminder (missing days + missing evidence)

> Generate `reminder.md` for bmw-ece-ntust/progress-plan#366 covering `<date>` to today.
> Run `python main.py --generate-reminder reminder.md --since <date>`.
> Then show me the contents — I want to see which days are missing and which bullets have no evidence link.

---

## Install

```bash
git clone https://github.com/bmw-ece-ntust/auto-daily-log.git
cd auto-daily-log

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy and edit the config:

```bash
cp env.example.yaml env.yaml
# Edit env.yaml — set github.login, contribution_sources, etc.
```

Authenticate GitHub CLI (once):

```bash
gh auth login
```

---

## Configure (`env.yaml`)

### Your identity

```yaml
github:
  login: your-github-username
```

### Contribution sources

Which organizations, user repos, and specific repos to scan for your commits:

```yaml
github:
  contribution_sources:
    orgs:
      - bmw-ece-ntust          # your org
    repo_owners:
      - raycg                  # supervisor's repos where your commits count
    repos: []                  # specific repos (owner/name)
```

### Target issue (where daily-log comments are posted)

```yaml
github:
  repo_owner: bmw-ece-ntust
  repo_name: progress-plan
  issue_number: 366
```

### Timeline

```yaml
timeline:
  lookback_days: 14            # default window for missing-date checks
  start_date: "2023-09-01"    # PhD/employment start date for full backfill
```

### Google Calendar (optional)

```yaml
google_calendar:
  enabled: true
  credentials_file: "~/.config/dailylog/credentials.json"
  token_file:        "~/.config/dailylog/token.json"
  calendar_id:       "primary"
  class_keywords:    ["class", "lecture", "seminar", "course"]
```

Download `credentials.json` from [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON.

---

## Daily-Log Comment Format

```markdown
### YYYY/MM/DD

**Short-term Goal**:
<one-line goal>

**Daily-logs**:
- `HH.MM - HH.MM`: [description](https://github.com/org/repo/blob/abc1234/path/to/file.md#section)
- `HH.MM - `: <ongoing activity>
```

Rules:

- Heading: `### YYYY/MM/DD` (Asia/Taipei timezone)
- **Short-term Goal**: one-line goal for the day
- **Daily-logs**: one bullet per activity
  - Time range in backticks: `` `HH.MM - HH.MM` `` (end time from commit timestamp)
  - Evidence link: **specific file URL** with 7-digit commit hash + heading anchor
  - Special entries: `` `SICK LEAVE` ``, `` `HOLIDAY` ``, `` `ABSENT` ``

### Holiday / Time-Replacement Policy

When you worked on a public holiday or weekend:

```markdown
### YYYY/MM/DD

> 🏖️ **HOLIDAY: <Holiday Name>** — Worked on public holiday; time replacement for `YYYY-MM-DD` absence.

**Daily-logs**:
- `HH.MM - HH.MM`: [description](url)
```

For the compensated absence day:

```markdown
### YYYY/MM/DD

**Short-term Goal**:
<absent — compensated by working on YYYY-MM-DD (Holiday Name)>

**Daily-logs**:
- `ABSENT — compensated by 2026-05-01 (Labor Day)`
```

---

## How to Run

> Activate the venv first:
> ```bash
> source .venv/bin/activate
> ```

### Step 0 — Check missing dates (run this first every time)

```bash
python3 main.py \
  --dry-run --since YYYY-MM-DD --until YYYY-MM-DD \
  --ensure-comments --seed-from-commits
```

### Step 1 — Today only

```bash
# Preview
python3 main.py --dry-run --today --ensure-comments --seed-from-commits

# Apply
python3 main.py --apply --today --ensure-comments --seed-from-commits
```

Fetches commits from all configured `contribution_sources`, creates the day's comment if missing, and seeds **Daily-logs** bullets.

### Step 2 — Backfill a date range

```bash
python3 main.py \
  --apply --since YYYY-MM-DD --until YYYY-MM-DD \
  --ensure-comments --seed-from-commits --max-create 20
```

> **Skip weekends** — run one Mon–Fri range at a time to avoid empty weekend entries.

### Step 3 — Generate reminder.md

Lists missing dates and activities lacking evidence links:

```bash
python3 main.py \
  --generate-reminder reminder.md \
  --since YYYY-MM-DD --until YYYY-MM-DD
```

Output sections:

1. **Missing Daily-Log Entries** — weekdays with no comment
2. **Activities Without Evidence** — bullets with no hyperlink (class/lecture events excluded)

Evidence links must point to a **specific file** with a **7-digit commit hash**:

```text
[description](https://github.com/org/repo/blob/abc1234/path/to/file.md#section-heading)
```

### Step 4 — Check Google Calendar (optional)

```bash
python3 main.py --check-calendar --since YYYY-MM-DD --until YYYY-MM-DD
```

Requires `google_calendar.enabled: true` in `env.yaml`.

### Step 5 — File-specific commit links (optional)

```bash
python3 main.py --apply --today --ensure-comments --seed-from-commits --link-to-files
```

Resolves each commit to the primary file changed and generates a blob URL with 7-char hash. Makes one extra API call per commit — use sparingly.

---

## Flag Reference

| Flag | Purpose |
| --- | --- |
| `--today` | Use today's date (Asia/Taipei) as `--since`/`--until` |
| `--since YYYY-MM-DD` | Start of date range |
| `--until YYYY-MM-DD` | End of date range |
| `--ensure-comments` | Create a missing day comment if none exists |
| `--resume-from-latest` | Auto-detect last comment and start from next day |
| `--max-create N` | Cap new comments per run (0 = unlimited) |
| `--dry-run` | Preview only — no writes to GitHub |
| `--apply` | Write changes to GitHub |
| `--seed-from-commits` | Seed new comment bullets from that day's commits |
| `--seed-from-plans` | Seed **Short-term Goal** from milestone/backlog sources |
| `--update-existing-targets` | Update **Short-term Goal** on existing comment |
| `--append-unmatched-commits` | Add unreferenced commits as new bullets |
| `--link-to-files` | Resolve commit URLs to file-specific blob URLs (7-char hash) |
| `--generate-reminder [PATH]` | Write reminder.md (missing dates + no-evidence activities) |
| `--check-calendar` | Print calendar events for the date range |
| `--config PATH` | Path to config file (default: `env.yaml`) |

---

## Notes

- Commits are bucketed by **Asia/Taipei (GMT+8)** timezone.
- Always run `--dry-run` first and confirm output before switching to `--apply`.
- `seed_new_day_from_commits: true` in `env.yaml` pre-seeds new comments automatically.
- No secrets in files — GitHub auth uses `gh auth login`; Google Calendar uses OAuth2.
