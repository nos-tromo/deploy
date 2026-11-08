"""Model of an .env.example: where each key sits and which keys were renamed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from .dotenv import KEY, EnvFile, Line, ParseError, parse, uncomment

_DIRECTIVE_START = re.compile(r"^#\s*renamed:")
_DIRECTIVE = re.compile(rf"^# renamed: ({KEY}) -> ({KEY})$")


class ExampleError(Exception):
    """The example cannot be used; the message never carries line content."""


def is_prose(value: str) -> bool:
    """True when the value reads as a sentence rather than a setting."""
    return any(ch.isspace() for ch in value) or value.endswith((".", ",", ";", ":"))


@dataclass
class Example:
    env: EnvFile
    renames: Dict[str, str]

    def active(self) -> Dict[str, Line]:
        return self.env.settings()

    def documented(self) -> Set[str]:
        keys = set(self.active())
        keys.update(
            ln.key for ln in self.env.lines if ln.kind == "commented" and ln.key
        )
        return keys

    def candidates(self, key: str) -> List[Line]:
        return self.env.commented(key)

    def values(self, key: str) -> Set[str]:
        found = {ln.value or "" for ln in self.candidates(key)}
        line = self.active().get(key)
        if line is not None:
            found.add(line.value or "")
        return found

    def raw_lines(self) -> Set[str]:
        return {ln.raw for ln in self.env.lines}

    def anchor_for(self, key: str, line: Line) -> Optional[Line]:
        active = self.active().get(key)
        if active is not None:
            return active
        cands = self.candidates(key)
        for cand in cands:
            if uncomment(cand.raw).rstrip() == line.raw.rstrip():
                return cand
        for cand in cands:
            if cand.value == line.value:
                return cand
        for cand in cands:
            if not is_prose(cand.value or ""):
                return cand
        return cands[0] if cands else None


def load_example(text: str, syntax: str = "dotenv") -> Example:
    try:
        env = parse(text, syntax)
    except ParseError as err:
        raise ExampleError(str(err)) from None
    direct: Dict[str, str] = {}
    for line in env.lines:
        if line.kind != "comment" or not _DIRECTIVE_START.match(line.raw):
            continue
        match = _DIRECTIVE.match(line.raw)
        if not match:
            raise ExampleError(f"line {line.lineno}: malformed renamed directive")
        old, new = match.group(1), match.group(2)
        if old in direct:
            raise ExampleError(f"renamed: {old} appears twice")
        direct[old] = new
    example = Example(env, {})
    documented = example.documented()
    for old in direct:
        if old in documented:
            raise ExampleError(f"renamed: {old} is still a key of the example")
    renames: Dict[str, str] = {}
    for old in direct:
        seen = {old}
        end = direct[old]
        while end in direct:
            if end in seen:
                raise ExampleError(f"renamed: cycle through {old}")
            seen.add(end)
            end = direct[end]
        if end not in documented:
            raise ExampleError(f"renamed: {end} is not a key of the example")
        renames[old] = end
    example.renames = renames
    return example
