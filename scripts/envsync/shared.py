"""Values several members must share (env-sync.conf): checked across the planned
files, filled into the unset side, reported as conflicts, generated only on
request. Events carry key and member names only, never a value."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .dotenv import KEY, Line, parse_line
from .example import Example
from .plan import Entry, Event, FilePlan

SAFE_VALUE = re.compile(r"^[A-Za-z0-9._~:/@+=,-]+$")

_GROUP = re.compile(r"^[a-z0-9-]+$")
_KEY = re.compile(rf"^{KEY}$")
_ENTRY = re.compile(r"^entry=([A-Za-z0-9._-]+)$")
_PLACEHOLDER = re.compile(r"<[^>]*>")
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_FILE_KINDS = ("filled", "generated", "kept_unset", "unsafe")


class TableError(Exception):
    """env-sync.conf cannot be used; the message names the line, never a value."""

    def __init__(self, lineno: int, reason: str) -> None:
        super().__init__(f"env-sync.conf line {lineno}: {reason}")
        self.lineno = lineno
        self.reason = reason


@dataclass(frozen=True)
class Row:
    group: str
    member: str
    key: str
    source: bool
    gen: bool
    entry: Optional[str]
    lineno: int


@dataclass
class Group:
    name: str
    rows: List[Row]


@dataclass
class SharedFile:
    plan: FilePlan
    example: Example
    base: Optional[Example]


def _row(lineno: int, fields: List[str]) -> Row:
    if len(fields) < 3:
        raise TableError(lineno, "expected GROUP MEMBER KEY [FLAG ...]")
    name, member, key, flags = fields[0], fields[1], fields[2], fields[3:]
    if not _GROUP.match(name):
        raise TableError(lineno, "a group name uses only a-z, 0-9 and '-'")
    if not _KEY.match(key):
        raise TableError(lineno, "bad key")
    source = gen = False
    entry: Optional[str] = None
    for flag in flags:
        match = _ENTRY.match(flag)
        if flag == "source" and not source:
            source = True
        elif flag == "gen" and not gen:
            gen = True
        elif match and entry is None:
            entry = match.group(1)
        else:
            raise TableError(
                lineno, "unknown or repeated flag (source, gen, entry=NAME)"
            )
    return Row(name, member, key, source, gen, entry, lineno)


def load_table(text: str) -> List[Group]:
    """Parse and validate env-sync.conf; raises TableError naming the line."""
    groups: Dict[str, Group] = {}
    seen: Dict[Tuple[str, str], Row] = {}
    for lineno, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = _row(lineno, line.split())
        earlier = seen.get((row.member, row.key))
        if earlier is not None:
            raise TableError(
                lineno,
                f"{row.member} {row.key} is already in group {earlier.group} "
                f"(line {earlier.lineno})",
            )
        seen[(row.member, row.key)] = row
        groups.setdefault(row.group, Group(row.group, [])).rows.append(row)
    for group in groups.values():
        rows = group.rows
        if len(rows) < 2:
            raise TableError(rows[0].lineno, f"group {group.name} has only one row")
        sources = [row for row in rows if row.source]
        if len(sources) > 1:
            raise TableError(
                sources[1].lineno, f"group {group.name} has more than one source"
            )
        odd = next((row for row in rows if row.gen != rows[0].gen), None)
        if odd is not None:
            raise TableError(
                odd.lineno, f"group {group.name}: gen must be on every row or none"
            )
    return list(groups.values())


def shared_keys_for(groups: List[Group], member: str) -> FrozenSet[str]:
    """The keys of this member that some group shares."""
    return frozenset(
        row.key for group in groups for row in group.rows if row.member == member
    )


def _comment(line: Line) -> Optional[str]:
    """The inline comment a setting carries, as compose reads the line."""
    if line.kind != "setting":
        return None
    text = line.raw.split("=", 1)[1].lstrip(" \t")
    if text[:1] in ("'", '"'):
        quoted = _QUOTED.match(text)
        tail = text[quoted.end() :].strip() if quoted else ""
        return tail if tail.startswith("#") else None
    if text.startswith("#"):
        return text.rstrip()  # the trap: compose reads the comment as the value
    cut = text.find(" #")
    return text[cut + 1 :].rstrip() if cut >= 0 else None


def fill_line(existing: Optional[Line], key: str, value: str) -> str:
    """The line a shared value is written as, keeping the replaced line's comment."""
    prefix = "export " if existing is not None and existing.exported else ""
    line = f"{prefix}{key}={value}"
    comment = _comment(existing) if existing is not None else None
    return f"{line} {comment}" if comment else line


def _placeholder(value: str, placeholders: Set[str]) -> bool:
    return (
        value == ""
        or value.startswith("#")
        or _PLACEHOLDER.search(value) is not None
        or value in placeholders
    )


def _item_unset(item: str, placeholders: Set[str]) -> bool:
    _, sep, token = item.partition("=")
    return not item.strip() or (bool(sep) and _placeholder(token.strip(), placeholders))


def _list_unset(value: str, placeholders: Set[str]) -> bool:
    if value == "" or value.startswith("#") or value in placeholders:
        return True
    return all(_item_unset(item, placeholders) for item in value.split(","))


def _entry_token(value: str, name: str, placeholders: Set[str]) -> Optional[str]:
    for item in value.split(","):
        key, sep, token = item.partition("=")
        if sep and key.strip() == name:
            token = token.strip()
            return None if _placeholder(token, placeholders) else token
    return None


@dataclass
class _Side:
    row: Row
    file: SharedFile
    is_set: bool
    value: Optional[str]  # None on a set side: a list without a real entry item


class _Pass:
    def __init__(
        self, files: Dict[str, SharedFile], generate: bool, token: Callable[[], str]
    ) -> None:
        self.files = files
        self.generate = generate
        self.token = token
        self.found: Dict[str, List[Event]] = {
            "conflict": [],
            "unset_everywhere": [],
            "table": [],
        }
        self.notes: Dict[str, Dict[str, List[str]]] = {
            member: {kind: [] for kind in _FILE_KINDS} for member in files
        }

    def events(self) -> List[Event]:
        return (
            self.found["conflict"]
            + self.found["unset_everywhere"]
            + self.found["table"]
        )

    def group(self, group: Group) -> None:
        rows = self.present(group)
        if len(rows) < 2:
            return
        placeholders = self.placeholders(rows)
        sides = [self.read(row, placeholders) for row in rows]
        held = [side for side in sides if side.is_set]
        if held:
            self.reconcile(sides, held)
        elif group.rows[0].gen:
            self.nothing_set(sides)

    def present(self, group: Group) -> List[Row]:
        """The rows of members on this host whose example documents the key."""
        rows: List[Row] = []
        for row in group.rows:
            shared = self.files.get(row.member)
            if shared is None:
                continue
            if row.key not in shared.example.documented():
                self.found["table"].append(
                    Event(
                        "table",
                        [f"env-sync.conf: {row.key} is not in {row.member}'s example"],
                        "warn",
                    )
                )
                continue
            rows.append(row)
        return rows

    def placeholders(self, rows: List[Row]) -> Set[str]:
        """Every example value of every row, current or synced, active or
        commented, under the key or a name renamed to it."""
        found: Set[str] = set()
        for row in rows:
            shared = self.files[row.member]
            names = [row.key] + [
                old for old, new in shared.example.renames.items() if new == row.key
            ]
            values: Set[str] = set()
            for model in (shared.example, shared.base):
                if model is not None:
                    for name in names:
                        values |= model.values(name)
            found |= values
            if row.entry is not None:
                found |= {
                    item.partition("=")[2].strip()
                    for value in values
                    for item in value.split(",")
                }
        return found

    def read(self, row: Row, placeholders: Set[str]) -> _Side:
        shared = self.files[row.member]
        entry = shared.plan.entries.get(row.key)
        if entry is None:
            return _Side(row, shared, False, None)
        if row.entry is None:
            if row.source or not _placeholder(entry.value, placeholders):
                return _Side(row, shared, True, entry.value)
            return _Side(row, shared, False, None)
        if not row.source and _list_unset(entry.value, placeholders):
            return _Side(row, shared, False, None)
        return _Side(
            row, shared, True, _entry_token(entry.value, row.entry, placeholders)
        )

    def reconcile(self, sides: List[_Side], held: List[_Side]) -> None:
        ref = next((side for side in held if side.row.source), held[0])
        value = ref.value
        differs = [side for side in held if side is not ref and side.value != value]
        if differs or value is None:
            items = [
                f"{side.row.key}: {side.row.member} differs from {ref.row.member}"
                for side in differs
            ] or [f"{ref.row.key}: {ref.row.member} has no {ref.row.entry} entry"]
            self.found["conflict"].append(Event("conflict", items, "error"))
            return
        for side in sides:
            if not side.is_set:
                self.fill(side, value, "filled", ref.row.member)

    def nothing_set(self, sides: List[_Side]) -> None:
        kept = [side for side in sides if side.row.key in side.file.plan.unset]
        if self.generate and not kept:
            value = self.token()
            for side in sides:
                self.fill(side, value, "generated", None)
            return
        if self.generate:
            for side in kept:
                self.note(side.row.member, "kept_unset", side.row.key)
        members = list(dict.fromkeys(side.row.member for side in sides))
        self.found["unset_everywhere"].append(
            Event(
                "unset_everywhere",
                [f"{sides[0].row.key} ({', '.join(members)})"],
                "warn",
            )
        )

    def fill(self, side: _Side, value: str, origin: str, source: Optional[str]) -> None:
        row, plan = side.row, side.file.plan
        target = f"{row.entry}={value}" if row.entry is not None else value
        entry = plan.entries.get(row.key)
        if entry is not None and entry.value == target:
            return
        if row.key in plan.unset:
            self.note(row.member, "kept_unset", row.key)
            return
        if not SAFE_VALUE.fullmatch(target) or (row.entry is not None and "," in value):
            self.note(row.member, "unsafe", row.key)
            return
        existing = parse_line(entry.raw, plan.syntax) if entry is not None else None
        plan.entries[row.key] = Entry(
            row.key, fill_line(existing, row.key, target), target, origin
        )
        self.note(
            row.member, origin, f"{row.key} (from {source})" if source else row.key
        )

    def note(self, member: str, kind: str, item: str) -> None:
        self.notes[member][kind].append(item)

    def merge_notes(self) -> None:
        """Per-file events onto each plan: one event per kind, new kinds last."""
        for member, shared in self.files.items():
            for kind in _FILE_KINDS:
                found = self.notes[member][kind]
                if not found:
                    continue
                event = next((e for e in shared.plan.events if e.kind == kind), None)
                if event is None:
                    level = "warn" if kind == "unsafe" else "info"
                    event = Event(kind, [], level)
                    shared.plan.events.append(event)
                for item in found:
                    if item not in event.items:
                        event.items.append(item)


def apply_shared(
    groups: List[Group],
    files: Dict[str, SharedFile],
    generate: bool,
    token: Callable[[], str] = lambda: secrets.token_hex(32),
) -> List[Event]:
    """Reconcile every group across the planned files (keyed by member name).

    Fills unset rows into plan.entries and puts the per-file events (filled,
    generated, kept_unset, unsafe) on each plan; returns the cross-member ones
    (conflict, unset_everywhere, table)."""
    run = _Pass(files, generate, token)
    for group in groups:
        run.group(group)
    run.merge_notes()
    return run.events()
