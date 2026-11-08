"""The one format of every .env.example (docs/superpowers/specs/
2026-11-08-env-example-format-design.md): a fixed header, fixed sections in a
fixed order, one rule for active and commented lines, fixed tags.

check() names the line, the rule and the key of every departure, never a
value; scripts/check_env_examples.py runs it for each directory given.

Exit codes of the command: 0 no finding; 1 findings; 2 a usage or table
error, or a directory without its example."""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from .dotenv import (
    KEY,
    EnvFile,
    Line,
    ParseError,
    is_secret_like,
    lint,
    parse_line,
    references,
)
from .example import ExampleError, is_prose, load_example
from .shared import Group, TableError, load_table

DEPLOY_ROOT = Path(__file__).resolve().parents[2]

RULE = "# " + "=" * 77
SECTIONS: Tuple[str, ...] = (
    "Required",
    "Auth & identity",
    "Network & proxy",
    "Inference",
    "<member>",
    "Audit & retention",
    "Development only",
)

_TEMPLATE = (
    "# <member> — example configuration",
    "#",
    "# deploy's `make env` writes <env> from this file and keeps it in step",
    "# (by hand: cp <example> <env>).",
    "#",
    "#   KEY=value     set per host",
    "#   # KEY=value   optional; the value shown is the default",
    "#",
    "# Tags, on the line above a setting:",
    "#   [required]          must be set on every host",
    "#   [secret]            never printed; your value never follows the example",
    "#   [shared: M [KEY]]   same value as member M (its KEY); make env fills it",
    "#   [dev]               read only by the dev overlay (make up-dev)",
)

MESSAGES: Dict[str, str] = {
    "F1": "the line must parse, and compose or make must read its value as written",
    "F2": "a line starts with '#' or is KEY=... at column 0; no tab, no trailing blank",
    "F3": "longer than 79 columns; only a value with no blank and no comment may",
    "F4": "a key is written [A-Z][A-Z0-9_]*",
    "H1": "the file must start with the standard header",
    "H2": "the header holds no setting",
    "H3": "a legend line in the header is indented with two or more blanks",
    "S1": "a banner is a rule, a known section title and a rule",
    "S2": "each section appears once, in the standard order",
    "S3": "'# ==' appears only in a banner",
    "S4": "a subsection is written '# --- topic ---'",
    "S5": "a setting belongs to the section of the nearest banner above it",
    "T1": "a tag line is '# [tag] [tag] ...', directly above its setting",
    "T2": "tags are [required] [secret] [shared: ...] [dev]: in this order, once each",
    "T3": "[required], an active line and the Required section go together",
    "T4": "[secret] marks exactly the secret-like keys",
    "T5": "[shared: ...] must name the partners env-sync.conf gives this key",
    "T6": "[dev] marks exactly the settings in Development only",
    "V1": "an active line is KEY= with nothing after it",
    "V2": "a key has at most one commented line, and none when it is active",
    "V3": "a commented setting is written '# KEY=', with one blank",
    "V4": "a value holds no <placeholder>",
    "V5": "a value never refers to its own key",
    "V6": "a secret-like key shows no value",
    "V7": "an empty value carries no inline comment",
    "V8": "an inline comment follows a value and a blank, never a tab",
    "V9": "a value that reads as prose is quoted",
    "V10": "a key a value refers to is documented above that line",
    "V11": "a key the Makefile reads with grep carries no inline comment",
    "M1": "federation.env carries no inline comment",
    "M2": "a setting is KEY=value, with no blank around '='",
    "M3": "federation.env.example has no active line",
    "M4": "the value shown equals the Makefile's ?= default (empty when it has none)",
    "M5": "federation.env.example has one section, deploy",
    "X2": "rows of one env-sync.conf group agree on [required]",
    "X3": "a compose default equals the value shown; ${K:?} means [required]",
    "X5": "a variable compose interpolates is documented here, *_VERSION aside",
}
_ORDER = {rule: index for index, rule in enumerate(MESSAGES)}

_SETTINGS = ("setting", "commented")
_TAG_RANK = {"required": 0, "secret": 1, "shared": 2, "dev": 3}
# Defaults set outside the Makefile: WAIT_TIMEOUT in scripts/wait-healthy.sh.
_OTHER_DEFAULTS = {"WAIT_TIMEOUT": "180"}

_KEY_FORM = re.compile(r"^[A-Z][A-Z0-9_]*$")
_COLUMN0 = re.compile(rf"^{KEY}=")
_PROSE_EXAMPLE = re.compile(rf"^# {{2,}}{KEY}=(.*)$")
_TITLE = re.compile(r"^# [^\s=]")
_RULE_LIKE = re.compile(r"^#\s*==")
_SUBSECTION_LIKE = re.compile(r"^# ?-{2,}(\s|$)")
_SUBSECTION = re.compile(r"^# --- \S.* ---+$")
_LEGEND_LIKE = re.compile(rf"^# ?(?:\[|# ?{KEY}=)")
_TAG_LIKE = re.compile(r"^# ?\[")
_TAG_LINE = re.compile(r"^# \[[^\[\]]+\](?: \[[^\[\]]+\])*$")
_TAG = re.compile(r"\[([^\[\]]*)\]")
_SHARED_ITEM = re.compile(r"^[A-Za-z0-9._-]+(?: [A-Z][A-Z0-9_]*)?$")
_PLACEHOLDER = re.compile(r"<[^>]*>")
_UNESCAPED_HASH = re.compile(r"(?<!\\)#")
_MAKE_SPACED = re.compile(rf"^# ?({KEY})\s+=")
_LINE_IN_ERROR = re.compile(r"^line (\d+):")
_GREP_KEY = re.compile(
    r"""\bgrep(?:\s+-[A-Za-z]+)*\s+['"]\^([A-Za-z_][A-Za-z0-9_]*)="""
)
_DEFAULT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\?=(.*)$")


@dataclass(frozen=True)
class Finding:
    lineno: int
    rule: str
    key: Optional[str]


def header(member: str, env_name: str, example_name: str) -> List[str]:
    """The template every example starts with."""
    return [
        line.replace("<member>", member)
        .replace("<example>", example_name)
        .replace("<env>", env_name)
        for line in _TEMPLATE
    ]


def is_migrated(text: str, member: str) -> bool:
    """True once the example starts with the standard header (its title and
    the line naming `make env`; a title alone also fits a hand-written file)."""
    want = header(member, ".env", ".env.example")[:3]
    return text.split("\n")[:3] == want


def makefile_grep_keys(makefile_text: str) -> FrozenSet[str]:
    """The keys a Makefile reads from .env with grep '^KEY=' (any flags)."""
    return frozenset(m.group(1) for m in _GREP_KEY.finditer(makefile_text))


def makefile_defaults(makefile_text: str) -> Dict[str, str]:
    """Every `KEY ?= value` of a Makefile, the value stripped; the first wins,
    as in make."""
    found: Dict[str, str] = {}
    for raw in makefile_text.split("\n"):
        match = _DEFAULT.match(raw)
        if match:
            found.setdefault(match.group(1), match.group(2).strip())
    return found


def _quote_end(body: str) -> Optional[int]:
    """The index of the quote closing the one body starts with."""
    if body[0] == "'":
        end = body.find("'", 1)
        return end if end > 0 else None
    i = 1
    while i < len(body):
        if body[i] == "\\":
            i += 2
        elif body[i] == '"':
            return i
        else:
            i += 1
    return None


def _split(rest: str, syntax: str) -> Tuple[str, bool, bool]:
    """The text after `=` as written: the value before any inline comment,
    whether a comment follows, and whether a tab precedes that comment."""
    if syntax == "make":
        match = _UNESCAPED_HASH.search(rest)
        if match is None:
            return rest.strip(" \t"), False, False
        before = rest[: match.start()]
        return before.strip(" \t"), True, before.endswith("\t")
    body = rest.lstrip(" \t")
    lead = rest[: len(rest) - len(body)]
    if body[:1] in ("'", '"'):
        end = _quote_end(body)
        if end is None:
            return body, False, False
        tail = body[end + 1 :]
        at = tail.find("#")
        if at < 0:
            return body[1:end], False, False
        return body[1:end], True, tail[:at].endswith("\t")
    if body.startswith("#"):
        return "", True, lead.endswith("\t")
    for i in range(1, len(body)):
        if body[i] == "#" and body[i - 1] in " \t":
            return body[:i].rstrip(" \t"), True, body[i - 1] == "\t"
    return body.rstrip(" \t"), False, False


def _blankless(value: str) -> bool:
    return " " not in value and "\t" not in value


@dataclass
class _Tags:
    names: List[str]
    shared: Optional[str]


class _Check:
    def __init__(
        self,
        text: str,
        member: str,
        syntax: str,
        groups: Sequence[Group],
        grep_keys: FrozenSet[str],
        make_defaults: Optional[Dict[str, str]],
    ) -> None:
        self.text = text
        self.member = member
        self.syntax = syntax
        self.groups = groups
        self.grep_keys = grep_keys
        self.make_defaults = make_defaults
        raws = text.split("\n")
        if raws and raws[-1] == "":
            raws.pop()
        self.raws = raws
        self.lines: List[Optional[Line]] = [
            self._classify(n, raw) for n, raw in enumerate(raws, 1)
        ]
        self.titles = [title.replace("<member>", member) for title in SECTIONS]
        self.section: List[Optional[str]] = [None] * len(raws)
        self.banner: Set[int] = set()
        self.header_end = len(raws)
        self.tag_lines: Set[int] = set()
        self.tags: Dict[int, _Tags] = {}
        self.found: Set[Finding] = set()

    def _classify(self, lineno: int, raw: str) -> Optional[Line]:
        try:
            return dataclasses.replace(parse_line(raw, self.syntax), lineno=lineno)
        except ParseError:
            return None

    def add(self, index: int, rule: str, key: Optional[str] = None) -> None:
        self.found.add(Finding(index + 1, rule, key))

    def key_at(self, index: int) -> Optional[str]:
        line = self.lines[index] if 0 <= index < len(self.lines) else None
        return line.key if line is not None and line.kind in _SETTINGS else None

    def settings(self) -> List[Tuple[int, Line]]:
        return [
            (i, line)
            for i, line in enumerate(self.lines)
            if line is not None and line.kind in _SETTINGS
        ]

    def run(self) -> List[Finding]:
        self.file_rules()
        self.section_rules()
        self.header_rules()
        self.tag_rules()
        self.value_rules()
        if self.syntax == "make":
            self.make_rules()
        return sorted(self.found, key=lambda f: (f.lineno, _ORDER[f.rule], f.key or ""))

    # F: the file as a whole.
    def file_rules(self) -> None:
        parse_failed = False
        first: Dict[str, int] = {}
        for i, raw in enumerate(self.raws):
            line = self.lines[i]
            key = self.key_at(i)
            if line is None or "\r" in raw or (i == 0 and raw.startswith("\ufeff")):
                self.add(i, "F1", key)
                parse_failed = True
            elif line.kind == "setting" and line.key:
                if line.key in first:
                    self.add(i, "F1", key)
                    parse_failed = True
                first.setdefault(line.key, i)
            if raw and (
                "\t" in raw
                or raw != raw.rstrip()
                or not (raw.startswith("#") or _COLUMN0.match(raw))
            ):
                self.add(i, "F2", key)
            if len(raw) > 79 and not self.long_allowed(raw, line):
                self.add(i, "F3", key)
            if key is not None and not _KEY_FORM.match(key):
                self.add(i, "F4", key)
        env = EnvFile([line for line in self.lines if line is not None], self.syntax)
        for lineno, _ in lint(env):
            self.add(lineno - 1, "F1", self.key_at(lineno - 1))
        if not parse_failed:
            try:
                load_example(self.text, self.syntax)
            except ExampleError as err:
                self.add(self.error_index(str(err)), "F1")

    def long_allowed(self, raw: str, line: Optional[Line]) -> bool:
        """A setting, commented setting or indented prose example may run
        past 79 columns only for a value without a blank and with no inline
        comment; a long value's comment goes on the line above."""
        if line is not None and line.kind in _SETTINGS:
            rest = raw.split("=", 1)[1]
        else:
            match = _PROSE_EXAMPLE.match(raw)
            if match is None:
                return False
            rest = match.group(1)
        value, comment, _ = _split(rest, self.syntax)
        return not comment and _blankless(value)

    def error_index(self, message: str) -> int:
        match = _LINE_IN_ERROR.match(message)
        if match:
            return int(match.group(1)) - 1
        for i, raw in enumerate(self.raws):
            if raw.startswith("# renamed:"):
                return i
        return 0

    # S: banners and subsections; also where the header ends.
    def section_rules(self) -> None:
        raws, count = self.raws, len(self.raws)
        current: Optional[str] = None
        last_rank = -1
        i = 0
        while i < count:
            raw = raws[i]
            if raw == RULE and i + 1 < count and _TITLE.match(raws[i + 1]):
                title = raws[i + 1][2:]
                closed = i + 2 < count and raws[i + 2] == RULE
                if self.header_end == count:
                    self.header_end = i
                if not closed:
                    self.add(i + 1, "S1")
                if title in self.titles:
                    rank = self.titles.index(title)
                    if rank <= last_rank:
                        self.add(i + 1, "S2")
                    last_rank = max(last_rank, rank)
                else:
                    self.add(i + 1, "S1")
                current = title
                span = 3 if closed else 2
                for j in range(i, i + span):
                    self.banner.add(j)
                    self.section[j] = current
                i += span
                continue
            if _RULE_LIKE.match(raw):
                self.add(i, "S3")
            elif _SUBSECTION_LIKE.match(raw) and not _SUBSECTION.match(raw):
                self.add(i, "S4")
            self.section[i] = current
            i += 1

    # H: the header, the lines before the first banner.
    def header_rules(self) -> None:
        if self.syntax == "make":
            names = ("federation.env", "federation.env.example")
        else:
            names = (".env", ".env.example")
        template = header(self.member, *names)
        head = self.raws[: self.header_end]
        for k, expected in enumerate(template):
            if k >= len(head) or head[k] != expected:
                self.add(min(k, max(len(self.raws) - 1, 0)), "H1")
                break
        for i in range(self.header_end):
            line = self.lines[i]
            if line is None:
                continue
            if line.kind in _SETTINGS:
                self.add(i, "H2", line.key)
            elif line.kind == "comment" and _LEGEND_LIKE.match(self.raws[i]):
                self.add(i, "H3")

    # T: the tag line directly above each setting.
    def tag_rules(self) -> None:
        for i, line in self.settings():
            above = self.lines[i - 1] if i > 0 else None
            if (
                above is not None
                and above.kind == "comment"
                and _TAG_LIKE.match(self.raws[i - 1])
            ):
                self.tag_lines.add(i - 1)
                self.tags[i] = self.parse_tags(i - 1, line.key)
            else:
                self.tags[i] = _Tags([], None)
        for i in range(self.header_end, len(self.raws)):
            if (
                i not in self.tag_lines
                and i not in self.banner
                and _TAG_LIKE.match(self.raws[i])
            ):
                self.add(i, "T1")
        required, dev = self.titles[0], self.titles[-1]
        for i, line in self.settings():
            key, tags, section = line.key, self.tags[i], self.section[i]
            active = line.kind == "setting"
            tagged = "required" in tags.names
            in_required = section == required
            if tagged != active or in_required != active:
                self.add(i, "T3", key)
            if ("secret" in tags.names) != is_secret_like(key or ""):
                self.add(i, "T4", key)
            if tags.shared != self.expected_shared(key or ""):
                self.add(i, "T5", key)
            if ("dev" in tags.names) != (section == dev):
                self.add(i, "T6", key)

    def parse_tags(self, index: int, key: Optional[str]) -> _Tags:
        raw = self.raws[index]
        if not _TAG_LINE.match(raw):
            self.add(index, "T1", key)
        names: List[str] = []
        shared: Optional[str] = None
        for content in _TAG.findall(raw):
            if content == "shared" or content.startswith("shared:"):
                items = content[len("shared:") :]
                names.append("shared")
                shared = items.strip()
                if not (
                    items.startswith(" ")
                    and all(_SHARED_ITEM.match(item) for item in items[1:].split(", "))
                ):
                    self.add(index, "T1", key)
            else:
                names.append(content)
        last = -1
        for position, name in enumerate(names):
            rank = _TAG_RANK.get(name)
            if rank is None or name in names[:position] or rank < last:
                self.add(index, "T2", key)
                break
            last = rank
        return _Tags(names, shared)

    def expected_shared(self, key: str) -> Optional[str]:
        """The [shared: ...] items env-sync.conf gives this member's key: a
        non-source names the source; the source, or any row of a group
        without one, names every other row in table order."""
        for group in self.groups:
            mine = [r for r in group.rows if r.member == self.member and r.key == key]
            if not mine:
                continue
            source = next((r for r in group.rows if r.source), None)
            if source is not None and source is not mine[0]:
                partners = [source]
            else:
                partners = [r for r in group.rows if r is not mine[0]]
            return ", ".join(
                r.member if r.key == key else f"{r.member} {r.key}" for r in partners
            )
        return None

    # V: values, each line on its own and against the others.
    def value_rules(self) -> None:
        first: Dict[str, int] = {}
        for i, line in self.settings():
            first.setdefault(line.key or "", i)
        seen: Set[str] = set()
        for i, line in self.settings():
            key = line.key or ""
            raw = self.raws[i]
            active = line.kind == "setting"
            rest = raw.split("=", 1)[1]
            value, comment, after_tab = _split(rest, self.syntax)
            if active and raw != key + "=":
                self.add(i, "V1", key)
            if key in seen:
                self.add(i, "V2", key)
            seen.add(key)
            if not active and not raw.startswith("# "):
                self.add(i, "V3", key)
            if _PLACEHOLDER.search(value):
                self.add(i, "V4", key)
            refs = references(line)
            if key in refs:
                self.add(i, "V5", key)
            if is_secret_like(key) and value:
                self.add(i, "V6", key)
            if comment and not value:
                self.add(i, "V7", key)
            if comment and after_tab:
                self.add(i, "V8", key)
            if (
                self.syntax == "dotenv"
                and not active
                and is_prose(line.value or "")
                and rest.lstrip(" \t")[:1] not in ("'", '"')
            ):
                self.add(i, "V9", key)
            if any(ref != key and first.get(ref, -1) > i for ref in refs):
                self.add(i, "V10", key)
            if comment and key in self.grep_keys:
                self.add(i, "V11", key)

    # M: federation.env.example (make syntax).
    def make_rules(self) -> None:
        for i in sorted(self.banner):
            if self.raws[i] != RULE and self.raws[i] != "# deploy":
                self.add(i, "M5")
        for i, line in enumerate(self.lines):
            if line is not None and line.kind == "comment":
                match = _MAKE_SPACED.match(self.raws[i])
                if match:
                    self.add(i, "M2", match.group(1))
        for i, line in self.settings():
            key = line.key or ""
            raw = self.raws[i]
            rest = raw.split("=", 1)[1]
            active = line.kind == "setting"
            if _UNESCAPED_HASH.search(rest):
                self.add(i, "M1", key)
            if rest[:1] in (" ", "\t") or (active and not raw.startswith(key + "=")):
                self.add(i, "M2", key)
            if active:
                self.add(i, "M3", key)
            elif self.make_defaults is not None:
                expected = self.make_defaults.get(key, _OTHER_DEFAULTS.get(key, ""))
                if (line.value or "") != expected:
                    self.add(i, "M4", key)


def check(
    text: str,
    member: str,
    syntax: str = "dotenv",
    groups: Sequence[Group] = (),
    grep_keys: FrozenSet[str] = frozenset(),
    make_defaults: Optional[Dict[str, str]] = None,
) -> List[Finding]:
    """Every departure of an example from the format, sorted by line. The M
    rules apply to make syntax only; M4 only when make_defaults is given."""
    return _Check(text, member, syntax, groups, grep_keys, make_defaults).run()


# X: an example against its compose files and its env-sync.conf rows.

_COMPOSE_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?-)([^${}]*)\}")
_COMPOSE_REQUIRED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
_COMPOSE_REF = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)"
)


_PROSE_KEY = re.compile(r"^# {2,}([A-Z][A-Z0-9_]*)=")


def _compose_body(text: str) -> str:
    """The compose text without comments and without `$$` escapes."""
    kept = []
    for raw in text.split("\n"):
        if raw.lstrip().startswith("#"):
            continue
        at = raw.find(" #")
        if at >= 0 and "'" not in raw[:at] and '"' not in raw[:at]:
            raw = raw[:at]
        kept.append(raw)
    return "\n".join(kept).replace("$$", "")


def _example_settings(text: str) -> Dict[str, List[Tuple[int, Line, FrozenSet[str]]]]:
    """Every setting and commented setting by key: line number, line and the
    tags on the line above."""
    raws = text.split("\n")
    found: Dict[str, List[Tuple[int, Line, FrozenSet[str]]]] = {}
    for index, raw in enumerate(raws):
        try:
            line = parse_line(raw, "dotenv")
        except ParseError:
            continue
        if line.kind not in _SETTINGS or not line.key:
            continue
        names: FrozenSet[str] = frozenset()
        if index > 0 and _TAG_LINE.match(raws[index - 1]):
            names = frozenset(
                tag.split(":")[0] for tag in _TAG.findall(raws[index - 1])
            )
        found.setdefault(line.key, []).append((index + 1, line, names))
    return found


def check_compose(
    example_text: str, compose_text: str, override_text: Optional[str]
) -> List[Finding]:
    """X3 and X5 for a migrated example: the simple compose defaults against
    the commented values, `${K:?}` against [required], and every interpolated
    variable (*_VERSION aside) against what the example documents."""
    settings = _example_settings(example_text)
    base = _compose_body(compose_text)
    over = _compose_body(override_text) if override_text is not None else ""
    prose = {
        m.group(1)
        for m in (_PROSE_KEY.match(raw) for raw in example_text.split("\n"))
        if m
    }
    found: Set[Finding] = set()

    def is_dev(key: str) -> bool:
        return any("dev" in names for _, _, names in settings.get(key, []))

    for source, is_override in ((base, False), (over, True)):
        for match in _COMPOSE_DEFAULT.finditer(source):
            key, default = match.group(1), match.group(3)
            rows = settings.get(key)
            if not rows or is_dev(key) != is_override:
                continue
            for lineno, line, _ in rows:
                if line.kind == "commented" and (line.value or "") != default:
                    found.add(Finding(lineno, "X3", key))
        for match in _COMPOSE_REQUIRED.finditer(source):
            key = match.group(1)
            rows = settings.get(key)
            if rows and not is_dev(key):
                if not any("required" in names for _, _, names in rows):
                    found.add(Finding(rows[0][0], "X3", key))
    for source in (base, over):
        for match in _COMPOSE_REF.finditer(source):
            key = match.group(1) or match.group(2)
            if not key.endswith("_VERSION") and key not in settings and key not in prose:
                found.add(Finding(1, "X5", key))
    return sorted(found, key=lambda f: (f.lineno, _ORDER[f.rule], f.key or ""))


def _group_required(
    examples: Dict[str, str], groups: Sequence[Group]
) -> List[Tuple[str, Finding]]:
    settings = {
        member: _example_settings(text)
        for member, text in examples.items()
        if is_migrated(text, member)
    }
    found: List[Tuple[str, Finding]] = []
    for group in groups:
        rows = []
        for row in group.rows:
            entries = settings.get(row.member, {}).get(row.key)
            if entries:
                required = any("required" in names for _, _, names in entries)
                rows.append((row, entries[0][0], required))
        if not rows:
            continue
        reference = next((r for r in rows if r[0].source), rows[0])
        for row, lineno, required in rows:
            if required != reference[2]:
                found.append((row.member, Finding(lineno, "X2", row.key)))
    return found


def check_group_required(
    examples: Dict[str, str], groups: Sequence[Group]
) -> List[Finding]:
    """X2: the migrated examples in one group agree on [required]; a row
    that differs from the source (else the first row) is reported."""
    return [f for _, f in _group_required(examples, groups)]


# The command: scripts/check_env_examples.py [--table FILE] DIR...


class UsageError(Exception):
    """The command line, the table or a directory cannot be used."""


@dataclass
class _Target:
    folder: Path
    member: str
    example: Path
    syntax: str


def _target(arg: str) -> _Target:
    """deploy's federation.env.example (make syntax) in a directory that has
    one, otherwise the member's .env.example; the member is the directory's
    name as given (made absolute, symlinks kept)."""
    folder = Path(os.path.abspath(arg))
    if (folder / "federation.env.example").is_file():
        return _Target(folder, folder.name, folder / "federation.env.example", "make")
    example = folder / ".env.example"
    if not example.is_file():
        raise UsageError(f"{folder.name}: no .env.example")
    return _Target(folder, folder.name, example, "dotenv")


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from None


def _load_table(path: Path) -> List[Group]:
    try:
        return load_table(_read(path).decode("utf-8"))
    except UnicodeDecodeError:
        raise UsageError(f"{path}: not UTF-8") from None
    except TableError as exc:
        raise UsageError(str(exc)) from None


def _findings(target: _Target, groups: List[Group]) -> List[Finding]:
    data = _read(target.example)
    found: List[Finding] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        found.append(Finding(data.count(b"\n", 0, exc.start) + 1, "F1", None))
        text = data.decode("utf-8", errors="replace")
    if target.syntax == "dotenv" and is_migrated(text, target.member):
        compose = target.folder / "docker" / "compose.yaml"
        override = target.folder / "docker" / "compose.override.yaml"
        if compose.is_file():
            found += check_compose(
                text,
                _read(compose).decode("utf-8", errors="replace"),
                _read(override).decode("utf-8", errors="replace")
                if override.is_file()
                else None,
            )
    makefile = target.folder / "Makefile"
    rules = (
        _read(makefile).decode("utf-8", errors="replace")
        if makefile.is_file()
        else None
    )
    if target.syntax == "make":
        defaults = makefile_defaults(rules) if rules is not None else {}
        found += check(text, target.member, "make", groups, make_defaults=defaults)
    else:
        greps = makefile_grep_keys(rules) if rules is not None else frozenset()
        found += check(text, target.member, "dotenv", groups, grep_keys=greps)
    return sorted(set(found), key=lambda f: (f.lineno, _ORDER[f.rule], f.key or ""))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_env_examples.py",
        description=(
            "Check each directory's .env.example (deploy's "
            "federation.env.example) against the one format."
        ),
    )
    parser.add_argument(
        "--table",
        default=str(DEPLOY_ROOT / "env-sync.conf"),
        metavar="FILE",
        help="the shared values (default: deploy's env-sync.conf)",
    )
    parser.add_argument(
        "dirs", nargs="+", metavar="DIR", help="a member's directory or deploy's"
    )
    return parser


def main(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        groups = _load_table(Path(args.table))
        targets = [_target(arg) for arg in args.dirs]
        results = [(target, _findings(target, groups)) for target in targets]
    except UsageError as exc:
        print(f"check_env_examples.py: error: {exc}", file=sys.stderr)
        return 2
    texts = {
        target.member: _read(target.example).decode("utf-8", errors="replace")
        for target in targets
        if target.syntax == "dotenv"
    }
    cross: Dict[str, List[Finding]] = {}
    for member, finding in _group_required(texts, groups):
        cross.setdefault(member, []).append(finding)
    failed = False
    for target, findings in results:
        findings = sorted(
            set(findings) | set(cross.get(target.member, [])),
            key=lambda f: (f.lineno, _ORDER[f.rule], f.key or ""),
        )
        name = f"{target.member}/{target.example.name}"
        for f in findings:
            failed = True
            print(f"{name}:{f.lineno}: {f.rule} {f.key or '-'} {MESSAGES[f.rule]}")
    return 1 if failed else 0
