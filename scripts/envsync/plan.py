"""Plan one file: which operator lines are kept, which example keys are added,
which defaults follow, which renamed keys carry their value, which keys go to
the end block."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set

from .dotenv import EnvFile, Line, is_secret_like, lint, references, rewrite_key
from .example import Example

MARKER = "# [env-sync]"

_PLACEHOLDER = re.compile(r"<[^>]*>")
_COMMENT_KINDS = ("comment", "commented")
_KINDS = (
    "added",
    "followed",
    "unset_now",
    "removed",
    "held",
    "renamed",
    "rename_not_carried",
    "rename_conflict",
    "unknown",
    "kept_unset",
    "differs",
    "dropped_comments",
    "lint",
)
_WARN = frozenset({"unknown", "held", "rename_not_carried", "rename_conflict", "lint"})


class PlanError(Exception):
    """The file cannot be planned; the message never carries line content."""


@dataclass
class Entry:
    key: str
    raw: str
    value: str
    origin: str  # kept | added | followed | carried | filled | generated


@dataclass
class Orphan:
    line: Line
    comments: List[str]
    note: Optional[str]


@dataclass
class Event:
    kind: str
    items: List[str]
    level: str = "info"


@dataclass
class FilePlan:
    file_id: str
    display: str
    syntax: str
    created: bool
    entries: Dict[str, Entry]
    unset: Dict[str, str]
    orphans: List[Orphan]
    events: List[Event]
    dropped_comments: int
    shared_keys: FrozenSet[str]
    has_base: bool

    def effective(self) -> Dict[str, str]:
        """Key -> decoded value of every line the planned file sets."""
        values = {key: entry.value for key, entry in self.entries.items()}
        for orphan in self.orphans:
            if orphan.line.key:
                values[orphan.line.key] = orphan.line.value or ""
        return values


def is_held(key: str, syntax: str, shared_keys: FrozenSet[str]) -> bool:
    """True when the key never follows a changed example default."""
    return (
        syntax == "make"
        or is_secret_like(key)
        or "RETENTION" in key
        or key in shared_keys
    )


def _value(line: Line) -> str:
    return line.value or ""


def _same(a: Line, b: Line) -> bool:
    return a.raw.rstrip() == b.raw.rstrip()


class _Planner:
    """Steps 1-7 for a file that exists."""

    def __init__(
        self,
        example: Example,
        base: Optional[Example],
        env: EnvFile,
        shared_keys: FrozenSet[str],
    ) -> None:
        self.example = example
        self.base = base
        self.env = env
        self.syntax = example.env.syntax
        self.shared_keys = shared_keys
        self.ops = env.settings()
        self.e_active = example.active()
        self.e_documented = example.documented()
        self.e_raw = example.raw_lines()
        self.opc: Dict[str, Line] = {}
        for line in env.lines:
            if (
                line.kind == "commented"
                and line.key
                and line.key not in self.opc
                and line.raw not in self.e_raw
            ):
                self.opc[line.key] = line
        self.b_active = base.active() if base is not None else {}
        self.b_documented = base.documented() if base is not None else set()
        self.b_raw = base.raw_lines() if base is not None else set()
        self.entries: Dict[str, Entry] = {}
        self.unset: Dict[str, str] = {}
        self.orphans: List[Orphan] = []
        self.items: Dict[str, List[str]] = {kind: [] for kind in _KINDS}
        self.reused: Set[int] = set()

    def inherited(self, key: str) -> bool:
        line = self.ops.get(key)
        default = self.b_active.get(key)
        return line is not None and default is not None and _same(line, default)

    def follows(self, key: str) -> bool:
        return self.inherited(key) and not is_held(key, self.syntax, self.shared_keys)

    def drops(self, old: str) -> bool:
        """An inherited OLD whose default is dropped instead of carried."""
        new = self.example.renames[old]
        return (
            self.inherited(old)
            and not is_held(old, self.syntax, self.shared_keys)
            and not is_held(new, self.syntax, self.shared_keys)
        )

    def run(self, file_id: str, display: str) -> FilePlan:
        self.refuse_renamed_references()
        self.plan_renames()
        self.plan_settings()
        self.plan_example_keys()
        self.report_differs()
        dropped = self.count_dropped()
        if dropped:
            self.items["dropped_comments"].append(
                f"{dropped} comment lines not in the example dropped (see the backup)"
            )
        for lineno, message in lint(self.env):
            self.items["lint"].append(f"line {lineno}: {message}")
        events = [
            Event(kind, found, "warn" if kind in _WARN else "info")
            for kind, found in self.items.items()
            if found
        ]
        self.orphans.sort(key=lambda orphan: orphan.line.lineno)
        return FilePlan(
            file_id,
            display,
            self.syntax,
            False,
            self.entries,
            self.unset,
            self.orphans,
            events,
            dropped,
            self.shared_keys,
            self.base is not None,
        )

    def refuse_renamed_references(self) -> None:
        if self.syntax == "make":
            return
        for line in self.ops.values():
            for name in references(line):
                if (
                    name != line.key
                    and name in self.example.renames
                    and name in self.ops
                ):
                    raise PlanError(f"line {line.lineno}: refers to a renamed key")

    def plan_renames(self) -> None:
        renames = self.example.renames
        olds = [key for key in self.ops if key in renames]
        live: Dict[str, List[str]] = {}
        for old in olds:
            if not self.drops(old):
                live.setdefault(renames[old], []).append(old)
        for old in olds:
            new = renames[old]
            not_carried = f"renamed to {new} in the example; value not carried"
            if self.drops(old):
                self.items["renamed"].append(f"{old} -> {new} (old default dropped)")
            elif len(live[new]) > 1:
                self.orphan(
                    old,
                    f"renamed to {new} together with another key; value not carried",
                )
                self.items["rename_conflict"].append(f"{old} -> {new}")
            elif new in self.ops and (not self.inherited(new) or self.inherited(old)):
                self.orphan(old, not_carried)
                self.items["rename_conflict"].append(f"{old} -> {new}")
            elif self.base is None or old not in self.b_documented:
                self.orphan(old, not_carried)
                self.items["rename_not_carried"].append(f"{old} -> {new}")
            else:
                line = self.ops[old]
                raw = rewrite_key(line.raw, old, new)
                self.entries[new] = Entry(new, raw, _value(line), "carried")
                self.items["renamed"].append(f"{old} -> {new} (value carried)")

    def plan_settings(self) -> None:
        for key, line in self.ops.items():
            if key in self.example.renames or key in self.entries:
                continue
            follows = self.follows(key)
            if key not in self.e_documented:
                if follows:
                    self.items["removed"].append(key)
                else:
                    self.orphan(key, None)
                    self.items["unknown"].append(key)
                continue
            target = self.e_active.get(key)
            if follows:
                if target is None:
                    self.items["unset_now"].append(key)
                    continue
                if not _same(line, target):
                    self.entries[key] = Entry(
                        key, target.raw, _value(target), "followed"
                    )
                    self.items["followed"].append(
                        f"{key}: {_value(self.b_active[key])} -> {_value(target)}"
                    )
                    continue
            self.entries[key] = Entry(key, line.raw, _value(line), "kept")
            if self.inherited(key) and is_held(key, self.syntax, self.shared_keys):
                self.report_held(key, target)

    def report_held(self, key: str, target: Optional[Line]) -> None:
        default = self.b_active[key]
        if target is not None and _same(target, default):
            return
        if target is None or is_secret_like(key):
            self.items["held"].append(key)
        else:
            self.items["held"].append(f"{key}: {_value(default)} -> {_value(target)}")

    def plan_example_keys(self) -> None:
        for key, line in self.e_active.items():
            if key in self.entries:
                continue
            commented = self.opc.get(key)
            if (
                key in self.b_active
                or self.removed_before_rename(key)
                or (self.base is None and commented is not None)
            ):
                if commented is not None:
                    self.unset[key] = commented.raw
                    self.reused.add(commented.lineno)
                else:
                    self.unset[key] = "# " + line.raw
                self.items["kept_unset"].append(key)
            else:
                self.entries[key] = Entry(key, line.raw, _value(line), "added")
                self.items["added"].append(
                    key if is_secret_like(key) else f"{key}={_value(line)}"
                )

    def removed_before_rename(self, key: str) -> bool:
        """B had an OLD of this key active, and the operator has neither line."""
        return key not in self.ops and any(
            new == key and old in self.b_active and old not in self.ops
            for old, new in self.example.renames.items()
        )

    def report_differs(self) -> None:
        if self.base is not None:
            return
        for key in self.ops:
            entry = self.entries.get(key)
            default = self.e_active.get(key)
            if (
                entry is not None
                and entry.origin == "kept"
                and default is not None
                and entry.value != _value(default)
            ):
                self.items["differs"].append(key)

    def orphan(self, key: str, note: Optional[str]) -> None:
        line = self.ops[key]
        self.orphans.append(Orphan(line, self.attached(line), note))

    def attached(self, line: Line) -> List[str]:
        """The comment lines directly above an orphan that travel with it."""
        comments: List[str] = []
        index = line.lineno - 2
        while index >= 0 and self.env.lines[index].kind in _COMMENT_KINDS:
            above = self.env.lines[index]
            if not above.raw.startswith(MARKER) and above.raw not in self.e_raw:
                comments.append(above.raw)
                self.reused.add(above.lineno)
            index -= 1
        comments.reverse()
        return comments

    def count_dropped(self) -> int:
        return sum(
            1
            for line in self.env.lines
            if line.kind in _COMMENT_KINDS
            and not line.raw.startswith(MARKER)
            and line.raw not in self.e_raw
            and line.raw not in self.b_raw
            and line.lineno not in self.reused
        )


def plan_file(
    file_id: str,
    display: str,
    example: Example,
    base: Optional[Example],
    env: Optional[EnvFile],
    shared_keys: FrozenSet[str] = frozenset(),
) -> FilePlan:
    """Plan one file against its example and the copy it was last synced with."""
    if env is None or all(line.kind == "blank" for line in env.lines):
        entries = {
            key: Entry(key, line.raw, _value(line), "added")
            for key, line in example.active().items()
        }
        return FilePlan(
            file_id,
            display,
            example.env.syntax,
            env is None,
            entries,
            {},
            [],
            [],
            0,
            shared_keys,
            base is not None,
        )
    return _Planner(example, base, env, shared_keys).run(file_id, display)


def placeholder_event(
    plan: FilePlan, example: Example, base: Optional[Example]
) -> Optional[Event]:
    """Secret-like keys still holding an example placeholder, by name only."""
    found: List[str] = []
    for key, value in plan.effective().items():
        if not value or key in plan.shared_keys or not is_secret_like(key):
            continue
        names = [key] + [old for old, new in example.renames.items() if new == key]
        known: Set[str] = set()
        for name in names:
            known |= example.values(name)
            if base is not None:
                known |= base.values(name)
        if value in known or _PLACEHOLDER.search(value):
            found.append(key)
    return Event("placeholder", found, "warn") if found else None
