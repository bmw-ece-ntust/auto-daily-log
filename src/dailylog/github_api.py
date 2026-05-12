from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

TAIPEI_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Commit:
    sha: str
    short: str
    url: str
    title: str
    end_hhmm: str
    day: date
    repo_full_name: str
    # When populated, points to a specific file in the commit (blob URL with 7-char hash).
    # Falls back to url (full commit URL) when None.
    file_url: str | None = None


def _run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=input_text, capture_output=True, encoding="utf-8", errors="replace")


def gh_api_json(args: list[str], *, input_json: dict[str, Any] | None = None) -> Any:
    input_text = json.dumps(input_json) if input_json is not None else None
    proc = _run(["gh", "api", *args], input_text=input_text)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "gh api failed")
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def parse_iso_datetime(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def fetch_issue_comments(owner: str, repo: str, issue: int) -> list[dict[str, Any]]:
    pages = gh_api_json(
        [
            "-X",
            "GET",
            f"repos/{owner}/{repo}/issues/{issue}/comments?per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    comments: list[dict[str, Any]] = []
    for page in pages:
        comments.extend(page)
    return comments


def fetch_issue(owner: str, repo: str, issue: int) -> dict[str, Any]:
    endpoint = f"repos/{owner}/{repo}/issues/{issue}"
    return gh_api_json(["-X", "GET", endpoint])


def create_issue_comment(owner: str, repo: str, issue: int, body: str) -> dict[str, Any]:
    endpoint = f"repos/{owner}/{repo}/issues/{issue}/comments"
    return gh_api_json(["-X", "POST", endpoint, "-f", f"body={body}"])


def patch_issue_comment(owner: str, repo: str, comment_id: int, body: str) -> None:
    endpoint = f"repos/{owner}/{repo}/issues/comments/{comment_id}"
    proc = _run(["gh", "api", "-X", "PATCH", endpoint, "--input", "-"], input_text=json.dumps({"body": body}))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "PATCH failed")


def search_commits(query: str) -> list[dict[str, Any]]:
    pages = gh_api_json(
        [
            "-X",
            "GET",
            "search/commits",
            "-H",
            "Accept: application/vnd.github.cloak-preview+json",
            "-f",
            f"q={query}",
            "-f",
            "per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    items: list[dict[str, Any]] = []
    for page in pages:
        items.extend(page.get("items") or [])
    return items


def to_commit(item: dict[str, Any]) -> Commit | None:
    sha = (item.get("sha") or "").strip()
    if not sha:
        return None

    url = (item.get("html_url") or "").strip()
    repo_full = ((item.get("repository") or {}).get("full_name") or "").strip()

    commit_obj = item.get("commit") or {}
    title = ((commit_obj.get("message") or "").splitlines() or [""])[0].strip()

    committer = (commit_obj.get("committer") or {}).get("date")
    author = (commit_obj.get("author") or {}).get("date")
    ts = committer or author
    if not ts:
        return None

    dt = parse_iso_datetime(ts).astimezone(TAIPEI_TZ)

    return Commit(
        sha=sha,
        short=sha[:7],
        url=url,
        title=title,
        end_hhmm=dt.strftime("%H:%M"),
        day=dt.date(),
        repo_full_name=repo_full,
    )


def _dedup_commits(commits: list[Commit]) -> list[Commit]:
    seen: set[str] = set()
    uniq: list[Commit] = []
    for c in sorted(commits, key=lambda c: (c.end_hhmm, c.repo_full_name, c.short)):
        if c.sha in seen:
            continue
        seen.add(c.sha)
        uniq.append(c)
    return uniq


def commits_by_day(org: str, login: str, since: date, until: date) -> dict[date, list[Commit]]:
    """Single-org variant kept for backward compatibility."""
    return commits_by_day_multi(login=login, orgs=[org], repo_owners=[], repos=[], since=since, until=until)


def commits_by_day_multi(
    login: str,
    orgs: list[str] | tuple[str, ...],
    repo_owners: list[str] | tuple[str, ...],
    repos: list[str] | tuple[str, ...],
    since: date,
    until: date,
) -> dict[date, list[Commit]]:
    """Fetch commits from multiple sources: orgs, user-owned repos, and specific repos."""
    q_start = since - timedelta(days=1)
    q_end = until + timedelta(days=1)
    date_range = f"committer-date:{q_start.isoformat()}..{q_end.isoformat()}"

    queries: list[str] = []
    for org in orgs or []:
        queries.append(f"org:{org} author:{login} {date_range}")
    for owner in repo_owners or []:
        queries.append(f"user:{owner} author:{login} {date_range}")
    for repo in repos or []:
        queries.append(f"repo:{repo} author:{login} {date_range}")

    if not queries:
        queries.append(f"author:{login} {date_range}")

    all_items: list[dict[str, Any]] = []
    seen_shas: set[str] = set()
    for q in queries:
        for item in search_commits(q):
            sha = (item.get("sha") or "").strip()
            if sha and sha not in seen_shas:
                seen_shas.add(sha)
                all_items.append(item)

    out: dict[date, list[Commit]] = {}
    for it in all_items:
        c = to_commit(it)
        if not c:
            continue
        if c.day < since or c.day > until:
            continue
        out.setdefault(c.day, []).append(c)

    return {d: _dedup_commits(cs) for d, cs in out.items()}


def fetch_commit_files(repo_full_name: str, sha: str) -> list[dict[str, Any]]:
    """Return the list of files changed in a commit (requires one extra API call per commit)."""
    try:
        result = gh_api_json(["-X", "GET", f"repos/{repo_full_name}/commits/{sha}"])
        return list((result or {}).get("files") or [])
    except RuntimeError:
        return []


def best_file_url(commit: Commit, files: list[dict[str, Any]]) -> str:
    """Return a file-specific blob URL (7-char hash) if possible, otherwise the commit URL."""
    if not files or not commit.repo_full_name:
        return commit.url

    # Prefer markdown files (they're the most readable with anchor links)
    md_files = [f for f in files if (f.get("filename") or "").endswith(".md")]
    target = (md_files + list(files))[0]

    filename = (target.get("filename") or "").strip()
    if not filename:
        return commit.url

    return f"https://github.com/{commit.repo_full_name}/blob/{commit.short}/{filename}"


def enrich_commits_with_file_urls(day_to_commits: dict[date, list[Commit]]) -> dict[date, list[Commit]]:
    """Replace each commit's URL with a file-specific blob URL when possible.

    Makes one extra GitHub API call per commit — use only when --link-to-files is set.
    """
    enriched: dict[date, list[Commit]] = {}
    for d, commits in day_to_commits.items():
        new_commits: list[Commit] = []
        for c in commits:
            files = fetch_commit_files(c.repo_full_name, c.sha)
            file_url = best_file_url(c, files)
            if file_url != c.url:
                c = Commit(
                    sha=c.sha,
                    short=c.short,
                    url=c.url,
                    title=c.title,
                    end_hhmm=c.end_hhmm,
                    day=c.day,
                    repo_full_name=c.repo_full_name,
                    file_url=file_url,
                )
            new_commits.append(c)
        enriched[d] = new_commits
    return enriched
