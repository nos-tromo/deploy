"""Parse .env files (and federation.env) the way compose and make read them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

KEY = r"[A-Za-z_][A-Za-z0-9_]*"

_DOTENV_SETTING = re.compile(rf"^\s*(export\s+)?({KEY})\s*=(.*)$")
_MAKE_SETTING = re.compile(rf"^({KEY})\s*=(.*)$")
_COMMENTED = re.compile(rf"^# ?({KEY})=(.*)$")
_REFERENCE = re.compile(rf"\$\$|\$\{{({KEY})|\$({KEY})")
_UNESCAPED_HASH = re.compile(r"(?<!\\)#")
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}


class ParseError(Exception):
    """A line that cannot be read; the reason never carries line content."""

    def __init__(self, lineno: int, reason: str) -> None:
        super().__init__(f"line {lineno}: {reason}")
        self.lineno = lineno
        self.reason = reason


@dataclass(frozen=True)
class Line:
    lineno: int
    raw: str
    kind: str  # "blank" | "comment" | "setting" | "commented"
    key: Optional[str]
    value: Optional[str]
    exported: bool


@dataclass
class EnvFile:
    lines: List[Line]
    syntax: str

    def settings(self) -> Dict[str, Line]:
        return {ln.key: ln for ln in self.lines if ln.kind == "setting" and ln.key}

    def commented(self, key: str) -> List[Line]:
        return [ln for ln in self.lines if ln.kind == "commented" and ln.key == key]

    def decoded(self) -> Dict[str, str]:
        return {k: ln.value or "" for k, ln in self.settings().items()}


def _decode_double(body: str) -> Tuple[str, str]:
    """Return the unescaped text of a double-quoted value and what follows it."""
    out: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] in _ESCAPES:
            out.append(_ESCAPES[body[i + 1]])
            i += 2
        elif ch == '"':
            return "".join(out), body[i + 1 :]
        else:
            out.append(ch)
            i += 1
    raise ValueError("unterminated quote")


def decode(rest: str, syntax: str) -> str:
    """Decode the text after `=` into the value compose (or make) would read."""
    text = rest.lstrip(" \t") if syntax == "dotenv" else rest.lstrip(" ")
    if syntax == "make":
        out: List[str] = []
        i = 0
        while i < len(text):
            if text.startswith("\\#", i):
                out.append("#")
                i += 2
            elif text[i] == "#":
                break
            else:
                out.append(text[i])
                i += 1
        return "".join(out)
    if text[:1] in ("'", '"'):
        quote = text[0]
        if quote == "'":
            end = text.find("'", 1)
            if end < 0:
                raise ValueError("unterminated quote")
            value, after = text[1:end], text[end + 1 :]
        else:
            value, after = _decode_double(text[1:])
        tail = after.lstrip(" \t")
        if tail and not (tail.startswith("#") and after[:1] in (" ", "\t")):
            raise ValueError("text after closing quote")
        return value
    cut = text.find(" #")
    if cut >= 0:
        text = text[:cut]
    return text.rstrip(" \t")


def parse_line(raw: str, syntax: str = "dotenv") -> Line:
    """Classify one line (lineno 1); raises ParseError when it is unreadable."""
    return _classify(1, raw, syntax)


def _classify(lineno: int, raw: str, syntax: str) -> Line:
    if raw.strip() == "":
        return Line(lineno, raw, "blank", None, None, False)
    if raw.lstrip().startswith("#"):
        match = _COMMENTED.match(raw)
        if not match:
            return Line(lineno, raw, "comment", None, None, False)
        key, rest = match.group(1), match.group(2)
        try:
            value = decode(rest, syntax)
        except ValueError:
            value = rest.strip()
        return Line(lineno, raw, "commented", key, value, False)
    if syntax == "make":
        m = _MAKE_SETTING.match(raw)
        exported, key, rest = False, m.group(1) if m else None, m.group(2) if m else ""
    else:
        m = _DOTENV_SETTING.match(raw)
        exported = bool(m and m.group(1))
        key, rest = (m.group(2), m.group(3)) if m else (None, "")
    if key is None:
        raise ParseError(lineno, "unsupported syntax")
    try:
        value = decode(rest, syntax)
    except ValueError as err:
        raise ParseError(lineno, str(err)) from None
    return Line(lineno, raw, "setting", key, value, exported)


def parse(text: str, syntax: str = "dotenv") -> EnvFile:
    """Parse a whole file; raises ParseError on the first unreadable line."""
    if text.startswith("﻿"):
        raise ParseError(1, "byte-order mark")
    raws = text.split("\n")
    if raws and raws[-1] == "":
        raws.pop()
    lines: List[Line] = []
    first: Dict[str, int] = {}
    for lineno, raw in enumerate(raws, start=1):
        if "\r" in raw:
            raise ParseError(lineno, "CRLF line end")
        line = _classify(lineno, raw, syntax)
        if line.kind == "setting" and line.key:
            if line.key in first:
                raise ParseError(
                    lineno, f"duplicate key (first defined on line {first[line.key]})"
                )
            first[line.key] = lineno
        lines.append(line)
    return EnvFile(lines, syntax)


def uncomment(raw: str) -> str:
    """Strip the leading `#` and at most one blank."""
    return re.sub(r"^# ?", "", raw)


def references(line: Line) -> List[str]:
    """Names referenced as ${X} or $X in the value, `$$` skipped, first seen first."""
    names: List[str] = []
    for match in _REFERENCE.finditer(line.value or ""):
        name = match.group(1) or match.group(2)
        if name and name not in names:
            names.append(name)
    return names


def rewrite_key(raw: str, old: str, new: str) -> str:
    """Replace only the key token of a setting or commented line."""
    pattern = rf"^(\s*(?:#\s?)?(?:export\s+)?){re.escape(old)}(?=\s*=)"
    return re.sub(pattern, lambda m: m.group(1) + new, raw, count=1)


def _rest(raw: str, syntax: str) -> str:
    match = (_MAKE_SETTING if syntax == "make" else _DOTENV_SETTING).match(raw)
    return match.group(match.lastindex) if match else ""


def lint(env: EnvFile) -> List[Tuple[int, str]]:
    """Report values whose compose/make reading would surprise the operator."""
    found: List[Tuple[int, str]] = []
    for line in env.lines:
        if line.kind != "setting":
            continue
        rest = _rest(line.raw, env.syntax)
        if env.syntax == "make":
            if _UNESCAPED_HASH.search(rest):
                found.append(
                    (
                        line.lineno,
                        "inline comment in federation.env: make keeps the "
                        "blanks before it in the value",
                    )
                )
        elif re.match(r"^[ \t]*#", rest):
            found.append(
                (
                    line.lineno,
                    "empty value followed by a comment: compose reads the "
                    "comment as the value; put the comment on its own line",
                )
            )
        elif rest.lstrip(" \t")[:1] not in ("'", '"') and "\t#" in (line.value or ""):
            found.append(
                (
                    line.lineno,
                    "a tab before '#' does not start a comment; compose "
                    "keeps it in the value",
                )
            )
    return found


def is_secret_like(key: str) -> bool:
    return bool(
        re.search(r"SECRET|PASSWORD|PASSPHRASE|TOKEN", key)
        or re.search(r"(^|_)KEY$", key)
    )
