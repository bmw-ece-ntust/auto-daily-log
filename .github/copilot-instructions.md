# Copilot Instructions — auto-daily-log

## Project

Automated daily-log tool for BMW Lab students at NTUST. Posts GitHub issue comments to bmw-ece-ntust/progress-plan#366, seeded from commits and Google Calendar. See `CLAUDE.md` for full layout.

## Long-Term Memory (MySQL)

See global instructions in `~/.copilot/instructions.md`. The `mysql-memory` MCP tool is available in this workspace.

**When working on this repo**, log the working repo as `bmw-ece-ntust/auto-daily-log` in the `sessions.repo` field.

## Conventions

- Time notation: `HH.MM` (dots, not colons) in daily-log bullets
- Timezone: Asia/Taipei (GMT+8) for all date/time bucketing
- Evidence links: file blob URL with 7-char commit hash + section anchor
- Always `--dry-run` before `--apply`
- Config: `env.yaml` (copy from `env.example.yaml`, never commit secrets)
