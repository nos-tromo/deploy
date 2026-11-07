"""The report of make env and make env-check: a block per file, the shared
values, a summary; only one line (plus any warnings) when nothing needs
doing. It prints keys, member names and example values, never an operator
value: the event items are built that way by the planner and the shared pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .plan import Event, FilePlan

LABEL: Dict[str, str] = {
    "added": "added",
    "filled": "filled",
    "generated": "generated",
    "followed": "followed",
    "unset_now": "unset",
    "removed": "removed",
    "held": "held",
    "renamed": "renamed",
    "rename_not_carried": "renamed",
    "rename_conflict": "renamed",
    "unknown": "unknown",
    "kept_unset": "kept unset",
    "differs": "differs",
    "lint": "lint",
    "placeholder": "placeholder",
    "dropped_comments": "comments",
    "readable": "readable",
    "state": "state",
    "conflict": "conflict",
    "unset_everywhere": "unset",
    "unsafe": "not copied",
    "table": "table",
}
SUFFIX: Dict[str, str] = {
    "unset_now": " (the example no longer sets it; the default applies)",
    "removed": " (gone from the example; your line was its old default)",
    "held": " (example default changed; yours kept)",
    "rename_not_carried": " (value not carried; see the end of the file)",
    "rename_conflict": " (both set; the new name applies)",
    "unknown": " (not in the example; kept at the end)",
    "kept_unset": " (commented out on purpose)",
    "differs": " (differ from the example's default; no sync record yet)",
    "placeholder": " (example placeholder; set a real value)",
    "readable": " (readable by every user; chmod 600)",
    "unset_everywhere": " (unset in every member; ENV_GENERATE=1 generates one)",
    "unsafe": " (characters outside the safe set; set it by hand)",
}

SHARED_HEADER = "== shared"
NEVER_SYNCED = "   never synced: run 'make env' once"

_FAILED = ("refused", "missing")
_QUIET = ("in sync", "unchanged")


@dataclass
class FileResult:
    display: str
    status: str
    plan: Optional[FilePlan] = None
    backup: Optional[str] = None
    error: Optional[str] = None
    behind: bool = False
    never_synced: bool = False

    def events(self) -> List[Event]:
        return self.plan.events if self.plan is not None else []


def event_line(event: Event) -> str:
    # One blank at least: "placeholder" fills the whole label column.
    items = ", ".join(event.items)
    return f"   {LABEL[event.kind]:<10} {items}{SUFFIX.get(event.kind, '')}"


def _header(result: FileResult) -> str:
    line = f"== {result.display:<24} {result.status}"
    return f"{line} (backup: {result.backup})" if result.backup else line


def _block(header: str, events: List[Event]) -> List[str]:
    return [header] + [event_line(event) for event in events] if events else []


def _quiet(results: List[FileResult], shared: List[Event]) -> bool:
    """Nothing to do: every file in step and on record, no error anywhere."""
    events = [event for result in results for event in result.events()] + shared
    return all(
        result.status in _QUIET and not result.never_synced for result in results
    ) and all(event.level != "error" for event in events)


def _short(results: List[FileResult], shared: List[Event], check: bool) -> List[str]:
    lines = [f"env: {len(results)} files {'in sync' if check else 'unchanged'}"]
    for result in results:
        warns = [event for event in result.events() if event.level == "warn"]
        lines += _block(_header(result), warns)
    lines += _block(SHARED_HEADER, [event for event in shared if event.level == "warn"])
    return lines


def _summary(results: List[FileResult], shared: List[Event], check: bool) -> str:
    def count(*statuses: str) -> int:
        return sum(1 for result in results if result.status in statuses)

    refused = count(*_FAILED)
    conflicts = sum(1 for event in shared if event.kind == "conflict")
    if check:
        return (
            f"summary: {count('would write', 'would create')} would change, "
            f"{count('in sync')} in sync, {refused} refused, "
            f"{conflicts} conflict(s). Run 'make env' to apply."
        )
    written = count("written", "created")
    line = (
        f"summary: {written} written, {count('unchanged')} unchanged, "
        f"{refused} refused, {conflicts} conflict(s)."
    )
    return line + " Changes apply with the next 'make up'." if written else line


def _full(results: List[FileResult], shared: List[Event], check: bool) -> List[str]:
    lines: List[str] = []
    for result in results:
        lines.append(_header(result))
        if result.error is not None:
            lines.append(f"   {result.status:<10} {result.error}")
        lines.extend(event_line(event) for event in result.events())
        if result.never_synced:
            lines.append(NEVER_SYNCED)
    lines += _block(SHARED_HEADER, shared)
    lines.append(_summary(results, shared, check))
    return lines


def format_report(results: List[FileResult], shared: List[Event], check: bool) -> str:
    """The whole report, one line per entry, newline-terminated."""
    if _quiet(results, shared):
        lines = _short(results, shared, check)
    else:
        lines = _full(results, shared, check)
    return "\n".join(lines) + "\n"
