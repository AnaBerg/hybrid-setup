#!/usr/bin/env python3
"""Deterministic lifecycle primitives for Codex invocations and learning hooks.

Supervises `codex exec` and `codex review` child processes on behalf of the
`learn-to-use-codex` hook: it creates the invocation record before preflight,
updates it atomically, records stdout/stderr/exit/signal/timeout/cancellation
and report validity, terminates and confirms the whole process tree on timeout
or cancellation, and manages exclusive learning-hook ownership markers so a
parent can take over without duplicating analysis. Learning output is kept in
`learning-result.md`, never appended to the Codex report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def exclusive_json(path: Path, value: dict) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def process_identity(pid: int) -> str | None:
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value or None


def process_matches(pid: int, identity: str | None) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return identity is None or process_identity(pid) == identity


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def stop_group(process: subprocess.Popen[bytes], grace_seconds: float) -> str:
    pgid = os.getpgid(process.pid)
    if process.poll() is None:
        os.killpg(pgid, signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            process.wait(timeout=grace_seconds)
    deadline = time.monotonic() + grace_seconds
    while group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return "confirmed" if not group_alive(pgid) else "unresolved"


def supervise(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record_path = artifact_dir / "invocation.json"
    record = load_json(record_path)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("A command is required after --")

    stdin_path = Path(args.stdin).resolve() if args.stdin else None
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    report_path = artifact_dir / "report.md"
    cancelled_signal: list[int] = []

    def handle_signal(signum: int, _frame: object) -> None:
        cancelled_signal.append(signum)
        raise InterruptedError

    old_handlers = {
        sig: signal.signal(sig, handle_signal) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    cleanup_outcome = "not_required"
    timed_out = False
    try:
        with (
            stdin_path.open("rb") if stdin_path else open(os.devnull, "rb")
        ) as stdin_handle, stdout_path.open("wb") as stdout_handle, stderr_path.open(
            "wb"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                stdin=stdin_handle,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
            )
            identity = process_identity(process.pid)
            # start_new_session=True makes the child the process-group leader.
            # Using its PID avoids a race where a very short command exits before getpgid().
            process_group_id = process.pid
            deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=args.timeout)
            record.update(
                {
                    "state": "dispatched",
                    "dispatched_at": utc_now(),
                    "root_pid": process.pid,
                    "process_group_id": process_group_id,
                    "process_start_identity": identity,
                    "timeout_seconds": args.timeout,
                    "timeout_rationale": args.timeout_rationale,
                    "deadline_at": deadline.isoformat(),
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "report_path": str(report_path),
                }
            )
            atomic_json(record_path, record)
            try:
                process.wait(timeout=args.timeout)
                cleanup_outcome = "confirmed" if not group_alive(process.pid) else "unresolved"
            except subprocess.TimeoutExpired:
                timed_out = True
                cleanup_outcome = stop_group(process, args.grace)
            except InterruptedError:
                cleanup_outcome = stop_group(process, args.grace)
    except OSError as error:
        record.update(
            {
                "state": "launch_failed",
                "ended_at": utc_now(),
                "cleanup_outcome": "not_started",
                "report_validity": "missing",
                "launch_error": type(error).__name__,
            }
        )
        atomic_json(record_path, record)
        return 127
    finally:
        for sig, previous in old_handlers.items():
            signal.signal(sig, previous)

    assert process is not None
    return_code = process.returncode
    terminating_signal = -return_code if return_code is not None and return_code < 0 else None
    if cancelled_signal:
        terminating_signal = cancelled_signal[-1]
    stdout_size = stdout_path.stat().st_size if stdout_path.exists() else 0
    if return_code == 0 and stdout_size:
        shutil.copyfile(stdout_path, report_path)  # raw Codex stdout becomes the candidate report
        report_validity = "pending_validation"
    else:
        report_validity = "empty" if stdout_size == 0 else "partial"

    record.update(
        {
            "state": "timed_out" if timed_out else "cancelled" if cancelled_signal else "exited",
            "ended_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "exit_code": return_code if return_code is not None and return_code >= 0 else None,
            "signal": terminating_signal,
            "timed_out": timed_out,
            "cancelled": bool(cancelled_signal),
            "cleanup_outcome": cleanup_outcome,
            "cleanup_confirmed_at": utc_now() if cleanup_outcome == "confirmed" else None,
            "report_validity": report_validity,
        }
    )
    atomic_json(record_path, record)
    if timed_out:
        return 124
    if cancelled_signal:
        return 128 + cancelled_signal[-1]
    return return_code or 0


def marker_paths(artifact_dir: Path, invocation_id: str) -> tuple[Path, Path, Path]:
    prefix = artifact_dir / f"learning-hook.{invocation_id}"
    return Path(f"{prefix}.started"), Path(f"{prefix}.takeover"), Path(f"{prefix}.done")


def hook_claim(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    started, takeover, done = marker_paths(artifact_dir, invocation_id)
    if done.exists():
        value = load_json(done)
        return 0 if value.get("invocation_id") == invocation_id else 4
    owner_pid = os.getpid()
    value = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "owner_pid": owner_pid,
        "owner_start_identity": process_identity(owner_pid),
        "started_at": utc_now(),
        "hook_deadline_at": args.deadline,
    }
    if exclusive_json(started, value):
        return 0
    existing = load_json(started)
    deadline = dt.datetime.fromisoformat(existing["hook_deadline_at"])
    alive = process_matches(existing.get("owner_pid", 0), existing.get("owner_start_identity"))
    if alive and dt.datetime.now(dt.timezone.utc) < deadline:
        return 2
    return 0 if exclusive_json(takeover, value) else 2


def hook_complete(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    _, _, done = marker_paths(artifact_dir, invocation_id)
    result_path = Path(args.result).resolve()
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    value = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "evidence_digest": digest,
        "outcome_class": args.outcome,
        "proposal_ids": args.proposal_id,
        "result_path": str(result_path),
        "completed_at": utc_now(),
    }
    atomic_json(done, value)
    record.update({"state": "hook_complete", "learning_result_path": str(result_path)})
    atomic_json(artifact_dir / "invocation.json", record)
    return 0


def hook_status(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    _, _, done = marker_paths(artifact_dir, invocation_id)
    if not done.exists():
        return 1
    value = load_json(done)
    required = {"invocation_id", "evidence_digest", "outcome_class", "proposal_ids", "completed_at"}
    if value.get("invocation_id") != invocation_id or not required.issubset(value):
        return 4
    print(json.dumps(value, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("supervise")
    run.add_argument("--artifact-dir", required=True)
    run.add_argument("--stdin")
    run.add_argument("--timeout", type=float, required=True)
    run.add_argument("--timeout-rationale", required=True)
    run.add_argument("--grace", type=float, default=5.0)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=supervise)

    claim = commands.add_parser("hook-claim")
    claim.add_argument("--artifact-dir", required=True)
    claim.add_argument("--deadline", required=True)
    claim.set_defaults(func=hook_claim)

    complete = commands.add_parser("hook-complete")
    complete.add_argument("--artifact-dir", required=True)
    complete.add_argument("--result", required=True)
    complete.add_argument("--outcome", required=True)
    complete.add_argument("--proposal-id", action="append", default=[])
    complete.set_defaults(func=hook_complete)

    status = commands.add_parser("hook-status")
    status.add_argument("--artifact-dir", required=True)
    status.set_defaults(func=hook_status)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.func(args)
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"invocation lifecycle error: {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
