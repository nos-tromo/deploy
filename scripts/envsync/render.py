"""Write a planned file from the example, and check the text before it is used."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from .dotenv import EnvFile, ParseError, parse, parse_line, references
from .example import Example
from .plan import MARKER, FilePlan, plan_file

__all__ = ["MARKER", "ORPHAN_HEADER", "CheckError", "render", "verify"]

ORPHAN_HEADER = (
    f"{MARKER} Not in .env.example: kept as they were. Delete what is obsolete."
)


class CheckError(Exception):
    """A self-check failed; the reason never carries a value or line content."""


def render(example: Example, plan: FilePlan) -> str:
    """The planned file: the example's lines with the plan substituted in place."""
    at: Dict[int, str] = {}
    for key, entry in plan.entries.items():
        anchor = example.anchor_for(key, parse_line(entry.raw, plan.syntax))
        if anchor is None:
            raise CheckError(f"{key}: no place for it in the example")
        at[anchor.lineno] = entry.raw
    active = example.active()
    for key, raw in plan.unset.items():
        at[active[key].lineno] = raw
    lines: List[str] = [at.get(line.lineno, line.raw) for line in example.env.lines]
    if plan.orphans:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(ORPHAN_HEADER)
        for orphan in plan.orphans:
            lines.extend(orphan.comments)
            if orphan.note:
                lines.append(f"{MARKER} {orphan.note}")
            lines.append(orphan.line.raw)
    return "\n".join(lines) + "\n"


def _positions(env: EnvFile) -> Dict[str, int]:
    return {key: line.lineno for key, line in env.settings().items()}


def _check_order(original: EnvFile, rendered: EnvFile) -> None:
    before = _positions(original)
    after = _positions(rendered)
    for line in original.settings().values():
        for name in references(line):
            if name == line.key or name not in before or name not in after:
                continue
            assert line.key is not None
            if (before[name] < line.lineno) != (after[name] < after[line.key]):
                raise CheckError(f"{line.key}: order against {name} changed")


def verify(
    example: Example,
    plan: FilePlan,
    original: Optional[EnvFile],
    rendered: str,
) -> None:
    """Raise CheckError unless the rendered text is safe to write."""
    try:
        parsed = parse(rendered, plan.syntax)
    except ParseError as err:
        raise CheckError(f"does not parse again: {err}") from None
    if parsed.decoded() != plan.effective():
        raise CheckError("the decoded values differ from the plan")
    counts = Counter(parsed_line.raw for parsed_line in parsed.lines)
    wanted = [e.raw for e in plan.entries.values() if e.origin == "kept"]
    wanted += [o.line.raw for o in plan.orphans]
    for raw in wanted:
        if counts[raw] != 1:
            line = next(
                (ln.lineno for ln in parsed.lines if ln.raw == raw), "missing"
            )
            raise CheckError(f"a kept line is not present exactly once ({line})")
    if plan.syntax == "dotenv" and original is not None:
        _check_order(original, parsed)
    try:
        again = plan_file(
            plan.file_id, plan.display, example, example, parsed, plan.shared_keys
        )
        second = render(example, again)
    except Exception as err:  # noqa: BLE001 - reason only, never the message
        raise CheckError(f"re-planning failed ({type(err).__name__})") from None
    if second != rendered:
        raise CheckError("planning the result again gives a different file")
