"""Values several members must share (env-sync.conf): checked across the planned
files, filled into the unset side, reported as conflicts, generated only on
request. A lead row's value is written into every other row of its group whose
endpoint is the router. Events carry key and member names only, never a value."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from .dotenv import KEY, Line, parse_line
from .example import Example, is_prose
from .plan import Entry, Event, FilePlan

SAFE_VALUE = re.compile(r"^[A-Za-z0-9._~:/@+=,-]+$")

_GROUP = re.compile(r"^[a-z0-9-]+$")
_KEY = re.compile(rf"^{KEY}$")
_ENTRY = re.compile(r"^entry=([A-Za-z0-9._-]+)$")
_VIA = re.compile(r"^via=(.*)$")
_SUBST = re.compile(rf"\$\$|\$\{{({KEY})\}}|\$({KEY})")
_PLACEHOLDER = re.compile(r"<[^>]*>")
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_FILE_KINDS = ("filled", "generated", "linked", "not_linked", "kept_unset", "unsafe")
# What a generated token looks like to the safe-value check: 64 hex digits.
_GENERATED = "0" * 64
_REPLACED_KINDS = ("added", "held", "differs")
# The alias the federation's router answers to on inference-net.
ROUTER_HOST = "vllm-router"


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
    # The source whose value, or its example's default, every row of the group
    # whose endpoint is the router takes, replacing what it holds.
    lead: bool = False
    # The keys naming this row's endpoint, the first one with a value counting.
    via: Tuple[str, ...] = ()


@dataclass
class Group:
    name: str
    rows: List[Row]


@dataclass
class SharedFile:
    plan: FilePlan
    example: Example
    base: Optional[Example]
    # Placeholder values of examples older than the synced copy, kept per file
    # in the state directory, so an old placeholder never spreads.
    remembered: FrozenSet[str] = frozenset()


def _row(lineno: int, fields: List[str]) -> Row:
    if len(fields) < 3:
        raise TableError(lineno, "expected GROUP MEMBER KEY [FLAG ...]")
    name, member, key, flags = fields[0], fields[1], fields[2], fields[3:]
    if not _GROUP.match(name):
        raise TableError(lineno, "a group name uses only a-z, 0-9 and '-'")
    if not _KEY.match(key):
        raise TableError(lineno, "bad key")
    source = gen = lead = False
    entry: Optional[str] = None
    via: Optional[Tuple[str, ...]] = None
    for flag in flags:
        match = _ENTRY.match(flag)
        endpoint = _VIA.match(flag)
        if flag == "source" and not source:
            source = True
        elif flag == "gen" and not gen:
            gen = True
        elif flag == "lead" and not lead:
            lead = True
        elif match and entry is None:
            entry = match.group(1)
        elif endpoint and via is None:
            via = tuple(endpoint.group(1).split(","))
            if not all(_KEY.match(item) for item in via):
                raise TableError(lineno, "via= takes KEY[,KEY ...]")
        else:
            raise TableError(
                lineno,
                "unknown or repeated flag (source, lead, gen, entry=NAME, via=KEYS)",
            )
    if (source or lead) and entry is not None:
        # An entry row owns one item of a list, not the key: it cannot be the
        # authority for it.
        raise TableError(lineno, "source or lead cannot be combined with entry=NAME")
    if lead and (source or gen):
        raise TableError(lineno, "lead is a source of its own, never generated")
    if via is not None and (source or lead):
        raise TableError(lineno, "via= belongs on a row that follows the source")
    return Row(name, member, key, source or lead, gen, entry, lineno, lead, via or ())


def _clash(row: Row, earlier: List[Row]) -> Optional[Row]:
    """The first earlier row of this member and key that this row may not
    share it with. A list key may be on several rows only when each carries
    its own entry= name and stands in its own group; any other key is on one."""
    for other in earlier:
        if (
            row.entry is None
            or other.entry is None
            or other.entry == row.entry
            or other.group == row.group
        ):
            return other
    return None


def load_table(text: str) -> List[Group]:
    """Parse and validate env-sync.conf; raises TableError naming the line."""
    groups: Dict[str, Group] = {}
    seen: Dict[Tuple[str, str], List[Row]] = {}
    for lineno, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = _row(lineno, line.split())
        earlier = _clash(row, seen.setdefault((row.member, row.key), []))
        if earlier is not None:
            raise TableError(
                lineno,
                f"{row.member} {row.key} is already in group {earlier.group} "
                f"(line {earlier.lineno})",
            )
        seen[(row.member, row.key)].append(row)
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
        if not sources:
            stray = next((row for row in rows if row.via), None)
            if stray is not None:
                raise TableError(
                    stray.lineno, f"group {group.name}: via= needs a source or lead"
                )
    return list(groups.values())


def shared_keys_for(groups: List[Group], member: str) -> FrozenSet[str]:
    """The keys of this member that some group shares."""
    return frozenset(
        row.key for group in groups for row in group.rows if row.member == member
    )


def placeholder_values(
    groups: List[Group], member: str, example: Example, base: Optional[Example]
) -> FrozenSet[str]:
    """Every value, active or commented, that the example or the synced copy
    gives this member's shared keys or the names renamed to them; empty
    values excluded."""
    found: Set[str] = set()
    for key in shared_keys_for(groups, member):
        names = [key] + [old for old, new in example.renames.items() if new == key]
        for model in (example, base):
            if model is not None:
                for name in names:
                    found |= model.values(name)
    found.discard("")
    return frozenset(found)


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
    return _placeholder((token if sep else item).strip(), placeholders)


def _forget(plan: FilePlan, key: str) -> None:
    """Drop the planner's added, held and differs items about a key a fill has
    just written, and any of those events left empty."""
    mine = (key + "=", key + ": ")
    kept: List[Event] = []
    for event in plan.events:
        if event.kind in _REPLACED_KINDS:
            event.items = [
                item
                for item in event.items
                if item != key and not item.startswith(mine)
            ]
            if not event.items:
                continue
        kept.append(event)
    plan.events[:] = kept


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


def _unreadable(value: str, placeholders: Set[str]) -> bool:
    """Some item of the list has no '=' and is not a placeholder: the list is
    not a name=token list, and no entry row reads or rewrites it."""
    return any(
        "=" not in item and not _placeholder(item.strip(), placeholders)
        for item in value.split(",")
    )


def _merged(
    current: Optional[str], name: str, token: str, placeholders: Set[str]
) -> str:
    """The list with this row's item set: written anew when nothing in the list
    is real, the row's placeholder item replaced where it stands, else the item
    appended. The other items are kept verbatim, in their order."""
    mine = f"{name}={token}"
    if current is None or _list_unset(current, placeholders):
        return mine
    items = current.split(",")
    for i, item in enumerate(items):
        # Only a placeholder item can be this row's here: a real one is read.
        if item.partition("=")[0].strip() == name:
            items[i] = mine
            return ",".join(items)
    return ",".join(items + [mine])


def _twin(
    current: Optional[str], name: str, token: str, placeholders: Set[str]
) -> Optional[str]:
    """The name of another item of the list that already holds this token."""
    if current is None or _list_unset(current, placeholders):
        return None
    for item in current.split(","):
        other, sep, held = item.partition("=")
        if sep and other.strip() != name and held.strip() == token:
            return other.strip()
    return None


def shown_default(example: Example, key: str) -> str:
    """The value the example shows for a key: its active line, else its one
    commented setting line (the real default, by the example format); empty
    when it shows none."""
    line = example.active().get(key)
    if line is not None:
        return line.value or ""
    for cand in example.candidates(key):
        if cand.value and not is_prose(cand.value):
            return cand.value
    return ""


def _resolve(value: str, plan: FilePlan, depth: int = 0) -> str:
    """The value with each ${NAME} / $NAME the file sets put in, as compose
    reads the .env; an unknown name reads as empty."""

    def put(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2)
        if name is None:
            return "$"
        entry = plan.entries.get(name)
        if entry is None or depth >= 8:
            return ""
        return _resolve(entry.value, plan, depth + 1)

    return _SUBST.sub(put, value)


def _set_value(shared: SharedFile, key: str) -> str:
    """What the member reads for a key: its non-empty line, resolved, else the
    example's default."""
    entry = shared.plan.entries.get(key)
    if entry is not None and entry.value != "":
        return _resolve(entry.value, shared.plan)
    return shown_default(shared.example, key)


def _held_value(shared: SharedFile, key: str) -> str:
    """What a follower holds: its line, resolved, even when empty; without a
    line, the example's default."""
    entry = shared.plan.entries.get(key)
    if entry is not None:
        return _resolve(entry.value, shared.plan)
    return shown_default(shared.example, key)


def _host(url: str) -> Optional[str]:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


@dataclass
class _Side:
    row: Row
    file: SharedFile
    is_set: bool
    value: Optional[str]  # None on a set side: a list that cannot be read


class _Pass:
    def __init__(
        self,
        groups: List[Group],
        files: Dict[str, SharedFile],
        generate: bool,
        token: Callable[[], str],
    ) -> None:
        self.files = files
        self.generate = generate
        self.token = token
        self.known: Dict[str, FrozenSet[str]] = {
            member: placeholder_values(groups, member, shared.example, shared.base)
            | shared.remembered
            for member, shared in files.items()
        }
        self.found: Dict[str, List[Event]] = {
            "conflict": [],
            "unset_everywhere": [],
            "lead_unset": [],
            "table": [],
        }
        self.notes: Dict[str, Dict[str, List[str]]] = {
            member: {kind: [] for kind in _FILE_KINDS} for member in files
        }

    def events(self) -> List[Event]:
        return (
            self.found["conflict"]
            + self.found["unset_everywhere"]
            + self.found["lead_unset"]
            + self.found["table"]
        )

    def group(self, group: Group) -> None:
        rows = self.present(group)
        lead = next((row for row in group.rows if row.lead), None)
        if lead is not None:
            self.led(lead, rows)
            return
        rows = [row for row in rows if self.on_router(row)]
        if len(rows) < 2:
            return
        placeholders = self.placeholders(rows)
        sides = [self.read(row, placeholders) for row in rows]
        held = [side for side in sides if side.is_set]
        if held:
            self.reconcile(sides, held, placeholders)
        elif group.rows[0].gen:
            self.nothing_set(sides, placeholders)

    def present(self, group: Group) -> List[Row]:
        """The rows of members on this host whose example documents the key."""
        rows: List[Row] = []
        for row in group.rows:
            shared = self.files.get(row.member)
            if shared is None:
                continue
            if row.key not in shared.example.documented():
                self.table_warning(
                    f"env-sync.conf: {row.key} is not in {row.member}'s example"
                )
                continue
            rows.append(row)
        return rows

    def on_router(self, row: Row) -> bool:
        """Whether this row's endpoint is the router: always for a row without
        via=; else the first via= key with a value names it. A row off the
        router is left out of its group and noted as not linked."""
        if not row.via:
            return True
        shared = self.files[row.member]
        documented = shared.example.documented()
        endpoint = ""
        for key in row.via:
            if key not in documented:
                self.table_warning(f"env-sync.conf: {key} is not in {row.member}'s example")
                endpoint = ""
                break
            endpoint = _set_value(shared, key)
            if endpoint:
                break
        if endpoint and _host(endpoint) == ROUTER_HOST:
            return True
        self.note(row.member, "not_linked", row.key)
        return False

    def table_warning(self, item: str) -> None:
        if not any(item in found.items for found in self.found["table"]):
            self.found["table"].append(Event("table", [item], "warn"))

    def led(self, lead: Row, rows: List[Row]) -> None:
        """A lead group: the lead's value, or its example's default, replaces
        what each row on the router holds. Nothing flows back to the lead, and
        nothing moves without it on this host."""
        if lead not in rows:
            return
        followers = [row for row in rows if row is not lead and self.on_router(row)]
        if not followers:
            return
        value = _set_value(self.files[lead.member], lead.key)
        if value == "":
            self.found["lead_unset"].append(
                Event("lead_unset", [f"{lead.key} ({lead.member})"], "warn")
            )
            return
        for row in followers:
            shared = self.files[row.member]
            if _held_value(shared, row.key) == value:
                continue
            self.fill(_Side(row, shared, False, None), value, "linked", lead.member, set())

    def placeholders(self, rows: List[Row]) -> Set[str]:
        """The placeholder values of every member with a row here (current,
        synced or remembered); in a group with a list, also the token of each
        name=token item among them."""
        found: Set[str] = set()
        for row in rows:
            found |= self.known[row.member]
        if any(row.entry is not None for row in rows):
            found |= {
                item.partition("=")[2].strip()
                for value in list(found)
                for item in value.split(",")
            }
            found.discard("")
        return found

    def read(self, row: Row, placeholders: Set[str]) -> _Side:
        shared = self.files[row.member]
        entry = shared.plan.entries.get(row.key)
        if entry is None:
            return _Side(row, shared, False, None)
        if row.entry is None:
            if row.source:
                if entry.value == "":
                    return _Side(row, shared, False, None)
                return _Side(row, shared, True, entry.value)
            if not _placeholder(entry.value, placeholders):
                return _Side(row, shared, True, entry.value)
            return _Side(row, shared, False, None)
        if not row.source and _list_unset(entry.value, placeholders):
            return _Side(row, shared, False, None)
        if _unreadable(entry.value, placeholders):
            return _Side(row, shared, True, None)
        # The other entries of the list are not this row's: only its own item
        # says whether it is set.
        token = _entry_token(entry.value, row.entry, placeholders)
        return _Side(row, shared, token is not None, token)

    def reconcile(
        self, sides: List[_Side], held: List[_Side], placeholders: Set[str]
    ) -> None:
        ref = next((side for side in held if side.row.source), held[0])
        value = ref.value
        differs = [side for side in held if side is not ref and side.value != value]
        if differs:
            items = [
                f"{side.row.key}: {side.row.member} differs from {ref.row.member}"
                for side in differs
            ]
            self.found["conflict"].append(Event("conflict", items, "error"))
            return
        if value is None:
            # A list that cannot be read is set for every entry row but holds
            # no token: nothing disagrees, and it is never filled.
            self.unset_everywhere(sides)
            return
        for side in sides:
            if not side.is_set:
                self.fill(side, value, "filled", ref.row.member, placeholders)

    def nothing_set(self, sides: List[_Side], placeholders: Set[str]) -> None:
        kept = [side for side in sides if side.row.key in side.file.plan.unset]
        unsafe = self.unsafe_lists(sides, placeholders) if self.generate else []
        if self.generate and not kept and not unsafe:
            value = self.token()
            for side in sides:
                self.fill(side, value, "generated", None, placeholders)
            return
        if self.generate:
            for side in kept:
                self.note(side.row.member, "kept_unset", side.row.key)
            for side in unsafe:
                self.note(side.row.member, "unsafe", side.row.key)
        self.unset_everywhere(sides)

    def unsafe_lists(self, sides: List[_Side], placeholders: Set[str]) -> List[_Side]:
        """The entry rows whose list cannot take a generated item as it stands
        (another item holds a character outside the safe set). Asked before a
        token is drawn: a group is generated into every member or none, never a
        key whose list does not hold the same token."""
        found: List[_Side] = []
        for side in sides:
            row = side.row
            if row.entry is None:
                continue
            entry = side.file.plan.entries.get(row.key)
            current = entry.value if entry is not None else None
            target = _merged(current, row.entry, _GENERATED, placeholders)
            if not SAFE_VALUE.fullmatch(target):
                found.append(side)
        return found

    def unset_everywhere(self, sides: List[_Side]) -> None:
        members = list(dict.fromkeys(side.row.member for side in sides))
        self.found["unset_everywhere"].append(
            Event(
                "unset_everywhere",
                [f"{sides[0].row.key} ({', '.join(members)})"],
                "warn",
            )
        )

    def fill(
        self,
        side: _Side,
        value: str,
        origin: str,
        source: Optional[str],
        placeholders: Set[str],
    ) -> None:
        row, plan = side.row, side.file.plan
        entry = plan.entries.get(row.key)
        current: Optional[str] = None
        if row.entry is None:
            target = value
        else:
            # Read now, not when the group was read: an earlier group of this
            # run may have put its own item in the list.
            current = entry.value if entry is not None else None
            target = _merged(current, row.entry, value, placeholders)
        if entry is not None and entry.value == target:
            return
        if row.key in plan.unset:
            self.note(row.member, "kept_unset", row.key)
            return
        if row.entry is not None:
            twin = _twin(current, row.entry, value, placeholders)
            if twin is not None:
                # Two callers on one token: Nextext refuses to start with it.
                self.found["conflict"].append(
                    Event(
                        "conflict",
                        [f"{row.key}: {row.entry}'s token equals {twin}'s"],
                        "error",
                    )
                )
                return
        if not SAFE_VALUE.fullmatch(target) or (row.entry is not None and "," in value):
            self.note(row.member, "unsafe", row.key)
            return
        existing = parse_line(entry.raw, plan.syntax) if entry is not None else None
        plan.entries[row.key] = Entry(
            row.key, fill_line(existing, row.key, target), target, origin
        )
        _forget(plan, row.key)
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
    run = _Pass(groups, files, generate, token)
    for group in groups:
        run.group(group)
    run.merge_notes()
    return run.events()
