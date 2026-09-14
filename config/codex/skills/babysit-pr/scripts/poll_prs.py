#!/usr/bin/env python3
"""Poll GitHub pull requests and emit deduplicated JSON events."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, Callable


PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$")
PR_REF = re.compile(r"^([^/]+)/([^#!]+)[#!](\d+)$")
FAILED = {"FAILURE", "ERROR", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
CANCELLED = {"CANCELLED"}
TIMED_OUT = {"TIMED_OUT"}
GH_TIMEOUT_SECONDS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_target(value: str, default_repo: str | None) -> tuple[str, int]:
    match = PR_URL.match(value)
    if match:
        return f"{match.group(1)}/{match.group(2)}", int(match.group(3))
    match = PR_REF.match(value)
    if match:
        return f"{match.group(1)}/{match.group(2)}", int(match.group(3))
    if value.isdigit() and default_repo and re.match(r"^[^/]+/[^/]+$", default_repo):
        return default_repo, int(value)
    raise ValueError(f"Cannot resolve PR target {value!r}; use a GitHub PR URL, owner/repo#number, or --repo with a number")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command timed out after {GH_TIMEOUT_SECONDS}s: {' '.join(command[:3])}"
        ) from exc


def run_json(command: list[str]) -> Any:
    result = run_command(command)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(command[:3])}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Command returned invalid JSON: {' '.join(command[:3])}") from exc


def gh_executable() -> str:
    found = shutil.which("gh")
    if found:
        return found
    fallback = Path("/usr/local/bin/gh")
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("GitHub CLI (gh) is not installed or not in PATH")


def ensure_gh_auth(gh: str) -> None:
    result = run_command([gh, "auth", "status"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"GitHub authentication failed: {detail}")


def fetch_pages(gh: str, endpoint: str) -> list[dict[str, Any]]:
    """Read all feedback with a separate timeout for each 100-item request."""
    items: list[dict[str, Any]] = []
    for page in count(1):
        batch = run_json([gh, "api", f"{endpoint}?per_page=100&page={page}"])
        if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
            raise RuntimeError(f"Expected an array of objects from {endpoint}, page {page}")
        items.extend(batch)
        if len(batch) < 100:
            return items


def fetch_live(repo: str, number: int) -> dict[str, Any]:
    gh = gh_executable()
    fields = ",".join(
        [
            "number", "url", "title", "state", "isDraft", "mergeable",
            "mergeStateStatus", "headRefName", "headRefOid", "headRepository",
            "headRepositoryOwner", "isCrossRepository", "maintainerCanModify",
            "baseRefName", "reviewDecision", "statusCheckRollup", "updatedAt",
        ]
    )
    pr = run_json([gh, "pr", "view", str(number), "--repo", repo, "--json", fields])

    return {
        "pr": pr,
        "issue_comments": fetch_pages(gh, f"repos/{repo}/issues/{number}/comments"),
        "review_comments": fetch_pages(gh, f"repos/{repo}/pulls/{number}/comments"),
        "reviews": fetch_pages(gh, f"repos/{repo}/pulls/{number}/reviews"),
    }


def author_name(item: dict[str, Any]) -> str | None:
    author = item.get("user") or item.get("author") or {}
    if isinstance(author, dict):
        return author.get("login") or author.get("name")
    return str(author) if author else None


def check_event(check: dict[str, Any]) -> tuple[str, str] | None:
    conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
    if conclusion in FAILED:
        return "check_failed", conclusion
    if conclusion in CANCELLED:
        return "check_cancelled", conclusion
    if conclusion in TIMED_OUT:
        return "check_timed_out", conclusion
    return None


def event_id(kind: str, pr_key: str, item: dict[str, Any], suffix: str = "") -> str:
    identity = item.get("id") or item.get("databaseId") or item.get("node_id") or item.get("url")
    stamp = item.get("updated_at") or item.get("updatedAt") or item.get("submitted_at") or item.get("submittedAt") or ""
    return f"{kind}:{pr_key}:{identity}:{stamp}:{suffix}"


def base_event(kind: str, repo: str, number: int, pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": kind,
        "repo": repo,
        "pr_number": number,
        "pr_url": pr.get("url") or f"https://github.com/{repo}/pull/{number}",
        "head_ref": pr.get("headRefName"),
        "head_sha": pr.get("headRefOid"),
        "base_ref": pr.get("baseRefName"),
        "observed_at": utc_now(),
    }


def snapshot_fields(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        key: pr.get(key)
        for key in ("state", "isDraft", "mergeable", "mergeStateStatus", "headRefName", "headRefOid", "baseRefName")
    }


def collect_events(repo: str, number: int, data: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    pr = data.get("pr") or {}
    pr_key = f"{repo}#{number}"
    seen = set(state.setdefault("seen", []))
    prior = state.setdefault("prs", {}).get(pr_key)
    events: list[dict[str, Any]] = []

    def add(kind: str, item: dict[str, Any], payload: dict[str, Any], suffix: str = "") -> None:
        key = event_id(kind, pr_key, item, suffix)
        if key in seen:
            return
        event = base_event(kind, repo, number, pr)
        event.update(payload)
        event["event_id"] = key
        events.append(event)
        seen.add(key)

    for comment in data.get("issue_comments") or []:
        add("issue_comment", comment, {
            "comment_id": comment.get("id"), "author": author_name(comment),
            "body": comment.get("body"), "url": comment.get("html_url"),
            "created_at": comment.get("created_at"), "updated_at": comment.get("updated_at"),
        })

    for comment in data.get("review_comments") or []:
        add("inline_review_comment", comment, {
            "comment_id": comment.get("id"), "author": author_name(comment),
            "body": comment.get("body"), "url": comment.get("html_url"),
            "path": comment.get("path"), "line": comment.get("line") or comment.get("original_line"),
            "commit_id": comment.get("commit_id"), "created_at": comment.get("created_at"),
            "updated_at": comment.get("updated_at"),
        })

    for review in data.get("reviews") or []:
        review_state = str(review.get("state") or "").upper()
        kind = "change_requested" if review_state == "CHANGES_REQUESTED" else "review_submitted"
        add(kind, review, {
            "review_id": review.get("id"), "review_state": review_state,
            "author": author_name(review), "body": review.get("body"),
            "url": review.get("html_url"), "commit_id": review.get("commit_id"),
            "submitted_at": review.get("submitted_at"),
        }, review_state)

    for check in pr.get("statusCheckRollup") or []:
        classified = check_event(check)
        if not classified:
            continue
        kind, conclusion = classified
        name = check.get("name") or check.get("context") or check.get("workflowName") or "unknown"
        add(kind, check, {
            "check_name": name, "conclusion": conclusion,
            "status": check.get("status") or check.get("state"),
            "workflow": check.get("workflowName"),
            "details_url": check.get("detailsUrl") or check.get("targetUrl"),
            "started_at": check.get("startedAt"), "completed_at": check.get("completedAt"),
        }, f"{name}:{conclusion}:{check.get('startedAt')}:{check.get('completedAt')}")

    current = snapshot_fields(pr)
    conflict = str(current.get("mergeable") or "").upper() == "CONFLICTING" or str(current.get("mergeStateStatus") or "").upper() == "DIRTY"
    prior_conflict = bool(prior and (
        str(prior.get("mergeable") or "").upper() == "CONFLICTING"
        or str(prior.get("mergeStateStatus") or "").upper() == "DIRTY"
    ))
    if conflict and not prior_conflict:
        event = base_event("merge_conflict", repo, number, pr)
        event.update({"mergeable": current.get("mergeable"), "merge_state_status": current.get("mergeStateStatus")})
        event["event_id"] = f"merge_conflict:{pr_key}:{current.get('headRefOid')}:{current.get('mergeStateStatus')}"
        events.append(event)

    if prior is not None:
        changes = {
            key: {"from": prior.get(key), "to": value}
            for key, value in current.items()
            if prior.get(key) != value
        }
        if changes:
            event = base_event("state_changed", repo, number, pr)
            event.update({"changes": changes, "event_id": f"state_changed:{pr_key}:{utc_now()}"})
            events.append(event)

    state["seen"] = sorted(seen)
    state["prs"][pr_key] = current
    state["updated_at"] = utc_now()
    return events


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "seen": [], "prs": {}}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError(f"Unsupported state file: {path}")
    value.setdefault("seen", [])
    value.setdefault("prs", {})
    return value


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fixture_fetcher(path: Path) -> Callable[[str, int, int], dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        fixture = json.load(handle)

    def fetch(repo: str, number: int, cycle: int) -> dict[str, Any]:
        source = fixture.get("cycles", [fixture])
        selected = source[min(cycle, len(source) - 1)]
        targets = selected.get("targets", selected)
        key = f"{repo}#{number}"
        if key not in targets:
            raise KeyError(f"Fixture has no target {key}")
        return targets[key]

    return fetch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="PR URLs, owner/repo#numbers, or numbers with --repo")
    parser.add_argument("--repo", help="Default owner/repo for numeric targets")
    parser.add_argument("--state-file", type=Path, required=True, help="Persistent deduplication state")
    parser.add_argument("--watch", action="store_true", help="Poll continuously until interrupted")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between watch cycles")
    parser.add_argument("--max-cycles", type=int, help="Optionally bound the number of watch cycles")
    parser.add_argument("--fixture", type=Path, help="Read deterministic snapshots instead of calling gh")
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error("--interval must be finite and non-negative")
    if args.watch and not args.fixture and args.interval == 0:
        parser.error("--interval must be positive for live watch polling")
    if args.max_cycles is not None and args.max_cycles < 1:
        parser.error("--max-cycles must be positive")
    if not args.watch:
        args.max_cycles = 1
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        targets = [parse_target(value, args.repo) for value in args.targets]
        if len(set(targets)) != len(targets):
            raise ValueError("Duplicate PR targets are not allowed")
        state = load_state(args.state_file)
        fixture = fixture_fetcher(args.fixture) if args.fixture else None
        if not fixture:
            ensure_gh_auth(gh_executable())
        had_errors = False
        cycles = count() if args.max_cycles is None else range(args.max_cycles)
        for cycle in cycles:
            event_count = 0
            error_count = 0
            for repo, number in targets:
                try:
                    data = fixture(repo, number, cycle) if fixture else fetch_live(repo, number)
                    events = collect_events(repo, number, data, state)
                except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
                    had_errors = True
                    error_count += 1
                    print(json.dumps({
                        "type": "poll_error", "repo": repo, "pr_number": number,
                        "message": str(exc), "observed_at": utc_now(),
                    }, sort_keys=True), file=sys.stderr, flush=True)
                    continue
                for event in events:
                    print(json.dumps(event, sort_keys=True), flush=True)
                event_count += len(events)
            save_state(args.state_file, state)
            print(json.dumps({
                "type": "poll_summary", "cycle": cycle + 1,
                "events": event_count, "errors": error_count,
                "targets": len(targets), "observed_at": utc_now(),
            }, sort_keys=True), flush=True)
            if args.max_cycles is None or cycle + 1 < args.max_cycles:
                time.sleep(args.interval)
        return 1 if had_errors else 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"type": "poll_error", "message": str(exc), "observed_at": utc_now()}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
