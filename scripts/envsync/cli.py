"""The env_sync.py command. Each directory's file is planned against its
example and the copy it was last synced with; the shared values are then
reconciled across the planned files; then the files are checked (nothing is
written anywhere) or written (backup, atomic replace, the example recorded),
and the report is printed.

Exit codes: 0 ok; 1 a refused or missing file, a file error (its stored
copy not saved), a shared conflict, or (check) a file behind its example or
never synced; 2 a usage, table, lock or state directory error, raised before
any file is touched."""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, List, Optional, Set, Tuple, Union

from .dotenv import EnvFile, ParseError, parse
from .example import Example, ExampleError, load_example
from .plan import Event, FilePlan, PlanError, placeholder_event, plan_file
from .render import CheckError, render, verify
from .report import FileResult, format_report
from .shared import (
    Group,
    SharedFile,
    TableError,
    apply_shared,
    load_table,
    placeholder_values,
    shared_keys_for,
)
from .state import (
    LockHeld,
    Snapshot,
    StateDir,
    StateError,
    WriteRefused,
    atomic_replace,
    read_target,
    world_readable,
)

DEPLOY_ROOT = Path(__file__).resolve().parents[2]

DRIFT_WARNING = (
    "WARNING: ENV_ALLOW_DRIFT=1 — 'make up' continues although .env files "
    "are behind their examples."
)
STORED_UNREADABLE = "stored copy unreadable; treated as never synced"
STORED_NOT_SAVED = (
    "stored copy not saved ({}); fix the state directory, then run 'make env' again"
)


class UsageError(Exception):
    """The command line or the table cannot be used; nothing was touched."""


@dataclass
class Target:
    dir: Path
    file_id: str
    display: str
    syntax: str
    env_path: Path
    example_path: Path


def target_for(dir_arg: str) -> Target:
    """deploy's federation.env (make syntax) in a directory that has a
    federation.env.example, otherwise the member's .env. The name is taken
    from the path as given (made absolute, symlinks kept), so a member
    directory that is a symlink keeps its env-sync.conf name."""
    folder = Path(os.path.abspath(dir_arg))
    if (folder / "federation.env.example").is_file():
        syntax, name = "make", "federation.env"
    else:
        syntax, name = "dotenv", ".env"
    return Target(
        folder,
        folder.name,
        f"{folder.name}/{name}",
        syntax,
        folder / name,
        folder / f"{name}.example",
    )


@dataclass
class _Stored:
    """What the state directory holds for one file."""

    text: Optional[str] = None
    base: Optional[Example] = None
    remembered: FrozenSet[str] = frozenset()
    unreadable: bool = False


@dataclass
class _Planned:
    target: Target
    example_text: str
    example: Example
    stored: _Stored
    snap: Snapshot
    env: Optional[EnvFile]
    plan: FilePlan


def _decode(data: bytes) -> str:
    """UTF-8 text, or a ParseError naming the first line that is not."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(data.count(b"\n", 0, exc.start) + 1, "not UTF-8") from None


def _read_example(target: Target) -> Tuple[str, Example]:
    """The example's text and model; an ExampleError names the example file,
    since the report's header names the .env."""
    name = target.example_path.name
    try:
        text = _decode(target.example_path.read_bytes())
        return text, load_example(text, target.syntax)
    except OSError:
        raise ExampleError(f"{name}: cannot be read") from None
    except (ExampleError, ParseError) as exc:
        raise ExampleError(f"{name}: {exc}") from None


def _read_stored(state: StateDir, target: Target) -> _Stored:
    try:
        text = state.read_base(target.file_id)
        remembered = state.read_placeholders(target.file_id)
        base = None if text is None else load_example(text, target.syntax)
    except (OSError, ValueError, ExampleError):
        return _Stored(unreadable=True)
    return _Stored(text, base, remembered)


def _load(
    target: Target, groups: List[Group], state: StateDir
) -> Union[_Planned, FileResult]:
    """Read and plan one file, or the reason it is missing or refused."""
    display = target.display
    if not os.path.isdir(target.dir):
        return FileResult(display, "missing", error="directory not found")
    if not os.path.isfile(target.example_path):
        return FileResult(display, "missing", error=f"no {target.example_path.name}")
    try:
        example_text, example = _read_example(target)
        stored = _read_stored(state, target)
        snap = read_target(target.env_path)
        env = None if snap.data is None else parse(_decode(snap.data), target.syntax)
        shared_keys = shared_keys_for(groups, target.file_id)
        plan = plan_file(
            target.file_id, display, example, stored.base, env, shared_keys
        )
    except (ExampleError, ParseError, PlanError, WriteRefused) as exc:
        return FileResult(display, "refused", error=str(exc))
    except OSError as exc:
        return FileResult(display, "refused", error=f"cannot be read ({exc.strerror})")
    if stored.unreadable:
        plan.events.insert(0, Event("state", [STORED_UNREADABLE], "warn"))
    return _Planned(target, example_text, example, stored, snap, env, plan)


def _replace(p: _Planned, text: str, state: StateDir) -> Optional[str]:
    """Back the file up when it exists, then replace it. Returns the backup's
    path relative to the state directory's parent."""
    backup = None
    if p.snap.data is not None:
        path = state.backup(p.target.file_id, p.snap.data)
        backup = str(path.relative_to(state.root.parent))
    atomic_replace(p.snap, text, state, p.target.file_id)
    return backup


def _record(p: _Planned, groups: List[Group], state: StateDir) -> None:
    """Remember the placeholders, those of the copy about to be replaced
    included, then store the example the file is now in step with. A failure
    is an error on this file only: its .env is written, the run goes on."""
    file_id = p.target.file_id
    keep = p.stored.remembered | placeholder_values(
        groups, file_id, p.example, p.stored.base
    )
    try:
        if keep != p.stored.remembered:
            state.write_placeholders(file_id, keep)
        if p.example_text != p.stored.text:
            state.write_base(file_id, p.example_text)
    except (OSError, StateError) as exc:
        cause = getattr(exc, "strerror", None) or type(exc).__name__
        p.plan.events.append(Event("state", [STORED_NOT_SAVED.format(cause)], "error"))


def _finish(
    p: _Planned, groups: List[Group], state: StateDir, check: bool
) -> FileResult:
    """Self-check the planned file, then report (check) or write it."""
    display, plan = p.target.display, p.plan
    try:
        text = render(p.example, plan)
        verify(p.example, plan, p.env, text)
    except CheckError as exc:
        return FileResult(display, "refused", error=str(exc))
    found = placeholder_event(plan, p.example, p.stored.base)
    if found is not None:
        plan.events.append(found)
    if p.target.syntax == "dotenv" and world_readable(p.snap):
        plan.events.append(Event("readable", [display], "warn"))
    behind = p.env is None or p.env.decoded() != plan.effective()
    old = p.snap.data
    same = text.encode("utf-8") == old
    if check:
        status = "in sync" if same else "would create" if old is None else "would write"
        never = old is not None and p.stored.base is None
        return FileResult(display, status, plan, behind=behind, never_synced=never)
    backup = None
    if not same:
        try:
            backup = _replace(p, text, state)
        except WriteRefused as exc:
            return FileResult(display, "refused", error=str(exc))
        except OSError as exc:
            return FileResult(
                display, "refused", error=f"cannot be written ({exc.strerror})"
            )
    _record(p, groups, state)
    status = "unchanged" if same else "created" if old is None else "written"
    return FileResult(display, status, plan, backup=backup, behind=behind)


def _run(
    targets: List[Target],
    groups: List[Group],
    state: StateDir,
    check: bool,
    generate: bool,
) -> Tuple[List[FileResult], List[Event]]:
    loaded = [_load(target, groups, state) for target in targets]
    planned = [item for item in loaded if isinstance(item, _Planned)]
    files = {
        p.target.file_id: SharedFile(
            p.plan, p.example, p.stored.base, p.stored.remembered
        )
        for p in planned
    }
    shared = apply_shared(groups, files, generate)
    results = [
        item if isinstance(item, FileResult) else _finish(item, groups, state, check)
        for item in loaded
    ]
    return results, shared


def _targets(dirs: List[str]) -> List[Target]:
    """One target per directory, in argument order; a directory named twice
    (by any spelling) counts once."""
    targets: List[Target] = []
    seen: Set[str] = set()
    names: Set[str] = set()
    for arg in dirs:
        target = target_for(arg)
        real = os.path.realpath(target.dir)
        if real in seen:
            continue
        if target.file_id in names:
            raise UsageError(f"two directories are named {target.file_id}")
        seen.add(real)
        names.add(target.file_id)
        targets.append(target)
    return targets


def _load_table(path: Path) -> List[Group]:
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from None
    except UnicodeDecodeError:
        raise UsageError(f"{path}: not UTF-8") from None
    return load_table(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="env_sync.py",
        description=(
            "Bring each directory's .env (deploy's federation.env) in step "
            "with its example."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail while a file is behind its example",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="with --check: warn and succeed instead of failing",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="generate the shared secrets that every member leaves unset",
    )
    parser.add_argument(
        "--state",
        default=str(DEPLOY_ROOT / ".env-sync"),
        metavar="DIR",
        help="state directory (default: deploy's .env-sync/)",
    )
    parser.add_argument(
        "--table",
        default=str(DEPLOY_ROOT / "env-sync.conf"),
        metavar="FILE",
        help="the shared values (default: deploy's env-sync.conf)",
    )
    parser.add_argument(
        "dirs", nargs="*", metavar="DIR", help="deploy's directory or a member's"
    )
    return parser


def _check_usage(args: argparse.Namespace) -> None:
    if not args.dirs:
        raise UsageError("no directories given")
    if args.generate and args.check:
        raise UsageError("--generate writes values; it cannot be combined with --check")
    if args.allow_drift and not args.check:
        raise UsageError("--allow-drift applies only with --check")


def _exit_code(
    results: List[FileResult], shared: List[Event], check: bool, allow_drift: bool
) -> int:
    failed = any(result.status in ("refused", "missing") for result in results)
    failed = failed or any(
        event.level == "error" for result in results for event in result.events()
    )
    failed = failed or any(event.kind == "conflict" for event in shared)
    if check:
        failed = failed or any(r.behind or r.never_synced for r in results)
        if failed and allow_drift:
            sys.stdout.flush()
            print(DRIFT_WARNING, file=sys.stderr)
            return 0
    return 1 if failed else 0


def main(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        _check_usage(args)
        targets = _targets(args.dirs)
        groups = _load_table(Path(args.table))
        state = StateDir(Path(os.path.abspath(args.state)), create=not args.check)
        with nullcontext() if args.check else state.lock():
            results, shared = _run(targets, groups, state, args.check, args.generate)
    except (UsageError, TableError, LockHeld, StateError) as exc:
        sys.stdout.flush()
        print(f"env_sync.py: error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(format_report(results, shared, args.check))
    return _exit_code(results, shared, args.check, args.allow_drift)
