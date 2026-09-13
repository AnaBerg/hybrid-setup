#!/usr/bin/env python3
"""Install portable Claude and Codex guidance without copying account settings."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile
import uuid


START = "<!-- hybrid-setup:start -->"
END = "<!-- hybrid-setup:end -->"
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json"}


@dataclass
class Change:
    target: Path
    root: Path
    content: bytes
    original: bytes | None
    mode: int


def absolute_path(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {value}")
    # Keep symlinks visible for validation instead of resolving through them.
    return Path(os.path.abspath(path))


def reject_symlinks(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlink paths are not supported: {part}")


def overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def render(path: Path, replacements: dict[str, str]) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        text = data.decode("utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        return text.encode("utf-8")
    return data


def managed_document(original: bytes | None, template: bytes) -> bytes:
    guidance = template.decode("utf-8").strip()
    if START in guidance or END in guidance:
        raise ValueError("Source guidance must not contain managed-block markers")
    block = f"{START}\n{guidance}\n{END}"
    if original is None:
        return (block + "\n").encode("utf-8")
    text = original.decode("utf-8")
    starts, ends = text.count(START), text.count(END)
    if starts != ends or starts > 1:
        raise ValueError("Malformed or duplicate hybrid-setup managed-block markers")
    if starts:
        beginning, ending = text.index(START), text.index(END)
        if beginning >= ending:
            raise ValueError("Managed-block end marker precedes its start marker")
        return (text[:beginning] + block + text[ending + len(END):]).encode("utf-8")
    if text.strip() == guidance:
        return (block + "\n").encode("utf-8")
    separator = "" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
    return (text + separator + block + "\n").encode("utf-8")


def check_destination(path: Path) -> None:
    reject_symlinks(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular file at {path}")
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Expected a directory at {parent}")


def plan(args: argparse.Namespace) -> list[Change]:
    """Validate every destination and return changes without writing anything."""
    operating_system = platform.system()
    if operating_system not in {"Linux", "Darwin", "Windows"}:
        raise ValueError(f"Unsupported operating system: {operating_system}")
    home = absolute_path(args.home or Path.home(), "Home")
    source = absolute_path(args.source, "Source")
    codex = absolute_path(args.codex_home or os.environ.get("CODEX_HOME") or home / ".codex", "Codex home")
    claude = absolute_path(args.claude_home or os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude", "Claude home")
    roots = [source, codex, claude]
    for root in roots:
        reject_symlinks(root)
        if root.exists() and not root.is_dir():
            raise ValueError(f"Expected a directory at {root}")
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            if overlaps(root, other):
                raise ValueError(f"Source and target directories must not overlap: {root}, {other}")
    replacements = {"{{HOME}}": home.as_posix(), "{{CODEX_HOME}}": codex.as_posix(), "{{CLAUDE_HOME}}": claude.as_posix()}
    changes: list[Change] = []
    for tool, root, document in [("codex", codex, "AGENTS.md"), ("claude", claude, "CLAUDE.md")]:
        source_root = source / tool
        source_document = source_root / document
        skill_root = source_root / "skills"
        for required in (source_document, skill_root):
            reject_symlinks(required)
        if not source_document.is_file() or not skill_root.is_dir():
            raise ValueError(f"Source requires {source_document} and {skill_root}")
        backup_root = root / ".hybrid-setup-backups"
        reject_symlinks(backup_root)
        if backup_root.exists() and not backup_root.is_dir():
            raise ValueError(f"Expected a backup directory at {backup_root}")
        entries = [source_document]
        for entry in sorted(skill_root.rglob("*")):
            reject_symlinks(entry)
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise ValueError(f"Unsupported source entry: {entry}")
            entries.append(entry)
        for entry in entries:
            target = root / entry.relative_to(source_root)
            check_destination(target)
            original = target.read_bytes() if target.exists() else None
            content = render(entry, replacements)
            if entry == source_document:
                content = managed_document(original, content)
            elif original is not None and original != content and not args.force:
                raise ValueError(f"Existing skill differs: {target}. Use --force to back it up and replace it.")
            if original != content:
                mode = stat.S_IMODE(target.stat().st_mode if original is not None else entry.stat().st_mode)
                changes.append(Change(target, root, content, original, mode))
    return changes


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".hybrid-setup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(changes: list[Change]) -> None:
    # Detect edits since planning before creating any backup or destination.
    for change in changes:
        check_destination(change.target)
        current = change.target.read_bytes() if change.target.exists() else None
        if current != change.original:
            raise ValueError(f"Destination changed during installation: {change.target}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    # Finish every required backup before overwriting any original.
    for change in changes:
        if change.original is not None:
            backup = change.root / ".hybrid-setup-backups" / run_id / change.target.relative_to(change.root)
            reject_symlinks(backup)
            atomic_write(backup, change.original)
            print(f"Backup: {backup}")
    for change in changes:
        atomic_write(change.target, change.content, change.mode)
        print(f"Installed: {change.target}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--home", help="Absolute home directory override (defaults to the current user's home)")
    result.add_argument("--codex-home", help="Absolute Codex directory; overrides CODEX_HOME")
    result.add_argument("--claude-home", help="Absolute Claude directory; overrides CLAUDE_CONFIG_DIR")
    result.add_argument("--source", default=str(Path(__file__).resolve().parents[1] / "config"), help="Absolute configuration source directory")
    result.add_argument("--dry-run", action="store_true", help="Validate and display changes without writing files")
    result.add_argument("--force", action="store_true", help="Back up and replace conflicting skill files")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        changes = plan(args)
        print(f"Operating system: {platform.system()}")
        if args.dry_run:
            for change in changes:
                print(f"Would install: {change.target}")
        else:
            apply(changes)
        print(f"{len(changes)} file(s) {'planned' if args.dry_run else 'changed'}.")
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
