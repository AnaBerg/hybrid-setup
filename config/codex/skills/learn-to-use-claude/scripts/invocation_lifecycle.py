#!/usr/bin/env python3
"""Deterministic lifecycle primitives for Claude invocations and learning hooks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
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
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_group(process: subprocess.Popen[bytes], grace_seconds: float) -> str:
    # The session/group ID remains the original child PID after its leader exits.
    pgid = process.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            # Some sandboxes permit signalling the owned root but deny killpg.
            # Reap what we own; group cleanup remains unconfirmed.
            try:
                process.send_signal(sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + grace_seconds
        while group_alive(pgid) and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.05)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        return "unresolved"
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

    old_handlers = {
        sig: signal.signal(sig, handle_signal) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    cleanup_outcome = "not_required"
    timed_out = False
    supervisor_error = None
    cleanup_requested = False
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
                while process.poll() is None:
                    if cancelled_signal:
                        raise InterruptedError
                    remaining = args.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, args.timeout)
                    try:
                        process.wait(timeout=min(0.1, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                cleanup_requested = group_alive(process.pid)
                cleanup_outcome = stop_group(process, args.grace) if cleanup_requested else "confirmed"
            except subprocess.TimeoutExpired:
                timed_out = True
                cleanup_requested = True
                cleanup_outcome = stop_group(process, args.grace)
            except InterruptedError:
                cleanup_requested = True
                cleanup_outcome = stop_group(process, args.grace)
    except InterruptedError:
        if process is None:
            record.update({"state": "cancelled", "cleanup_outcome": "not_started", "cancelled": True})
            atomic_json(record_path, record)
            return 130
        cleanup_requested = True
        cleanup_outcome = stop_group(process, args.grace)
        if not cancelled_signal:
            cancelled_signal.append(signal.SIGINT)
    except OSError as error:
        if process is not None:
            cleanup_requested = True
            cleanup_outcome = stop_group(process, args.grace)
            supervisor_error = type(error).__name__
        else:
            record.update(
                {"state": "launch_failed", "ended_at": utc_now(),
                 "cleanup_outcome": "not_started", "report_validity": "missing",
                 "launch_error": type(error).__name__}
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
        shutil.copyfile(stdout_path, report_path)
        report_validity = "pending_validation"
    else:
        report_validity = "empty" if stdout_size == 0 else "partial"

    record.update(
        {
            "state": "timed_out" if timed_out else "cancelled" if cancelled_signal else "supervisor_failed" if supervisor_error else "exited",
            "ended_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "exit_code": return_code if return_code is not None and return_code >= 0 else None,
            "signal": terminating_signal,
            "timed_out": timed_out,
            "cancelled": bool(cancelled_signal),
            "process_group_cleanup_outcome": cleanup_outcome,
            "cleanup_outcome": "unresolved" if cleanup_requested else "not_required",
            "cleanup_scope": "process_group_only",
            "cleanup_limitation": "Detached descendants are not contained; caller must verify full-tree cleanup before retry.",
            "cleanup_confirmed_at": None,
            "supervisor_error": supervisor_error,
            "report_validity": report_validity,
        }
    )
    atomic_json(record_path, record)
    if timed_out:
        return 124
    if cancelled_signal:
        return 128 + cancelled_signal[-1]
    if supervisor_error:
        return 125
    return return_code or 0


def marker_paths(artifact_dir: Path, invocation_id: str) -> tuple[Path, Path, Path]:
    prefix = artifact_dir / f"learning-hook.{invocation_id}"
    return Path(f"{prefix}.started"), Path(f"{prefix}.takeover"), Path(f"{prefix}.done")


@contextmanager
def hook_lock(artifact_dir: Path):
    # OS-owned locks are released when the short-lived helper exits or crashes.
    import fcntl
    with (artifact_dir / ".learning-hook.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def hook_claim(args: argparse.Namespace) -> int:
    with hook_lock(Path(args.artifact_dir).resolve()):
        return claim_locked(args)


def claim_locked(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    started, takeover, done = marker_paths(artifact_dir, invocation_id)
    if done.exists():
        return 3 if valid_done(done, invocation_id) else 4
    owner_pid = args.owner_pid
    identity = process_identity(owner_pid)
    deadline = dt.datetime.fromisoformat(args.deadline)
    if deadline.tzinfo is None or deadline <= dt.datetime.now(dt.timezone.utc):
        raise ValueError("Hook deadline must be timezone-aware and in the future")
    if not identity or not process_matches(owner_pid, identity):
        raise ValueError("Hook owner must be a live, long-lived caller process")
    value = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "owner_pid": owner_pid,
        "owner_start_identity": identity,
        "claim_token": args.claim_token,
        "started_at": utc_now(),
        "hook_deadline_at": args.deadline,
    }
    if exclusive_json(started, value):
        return 0
    existing = load_json(takeover if takeover.exists() else started)
    deadline = dt.datetime.fromisoformat(existing["hook_deadline_at"])
    alive = process_matches(existing.get("owner_pid", 0), existing.get("owner_start_identity"))
    if alive and dt.datetime.now(dt.timezone.utc) < deadline:
        return 2
    atomic_json(takeover, value)
    return 0


def hook_complete(args: argparse.Namespace) -> int:
    with hook_lock(Path(args.artifact_dir).resolve()):
        return complete_locked(args)


def complete_locked(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    started, takeover, done = marker_paths(artifact_dir, invocation_id)
    if done.exists():
        return 3 if valid_done(done, invocation_id) else 4
    active = takeover if takeover.exists() else started
    if not active.exists():
        return 2
    owner = load_json(active)
    deadline = dt.datetime.fromisoformat(owner["hook_deadline_at"])
    if (owner.get("claim_token") != args.claim_token
            or not process_matches(owner.get("owner_pid", 0), owner.get("owner_start_identity"))
            or dt.datetime.now(dt.timezone.utc) >= deadline):
        return 2
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
        "claim_token": args.claim_token,
    }
    atomic_json(done, value)
    record.update({"state": "hook_complete", "learning_result_path": str(result_path)})
    atomic_json(artifact_dir / "invocation.json", record)
    return 0


def valid_done(path: Path, invocation_id: str) -> bool:
    try:
        value = load_json(path)
        required = {"invocation_id", "evidence_digest", "outcome_class", "proposal_ids", "completed_at", "result_path", "claim_token"}
        return (value.get("invocation_id") == invocation_id
                and required.issubset(value)
                and isinstance(value["proposal_ids"], list)
                and hashlib.sha256(Path(value["result_path"]).read_bytes()).hexdigest() == value["evidence_digest"])
    except (OSError, ValueError, TypeError):
        return False


def hook_status(args: argparse.Namespace) -> int:
    artifact_dir = Path(args.artifact_dir).resolve()
    record = load_json(artifact_dir / "invocation.json")
    invocation_id = record["invocation_id"]
    _, _, done = marker_paths(artifact_dir, invocation_id)
    if not done.exists():
        return 1
    value = load_json(done)
    if not valid_done(done, invocation_id):
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
    claim.add_argument("--owner-pid", type=int, required=True, help="PID of the long-lived hook caller, not this helper")
    claim.add_argument("--claim-token", required=True, help="Unique caller-generated token; retain it for completion")
    claim.set_defaults(func=hook_claim)

    complete = commands.add_parser("hook-complete")
    complete.add_argument("--artifact-dir", required=True)
    complete.add_argument("--result", required=True)
    complete.add_argument("--claim-token", required=True)
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
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"invocation lifecycle error: {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
