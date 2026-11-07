from __future__ import annotations

import copy
import unittest
from typing import Callable, Dict, FrozenSet, List, Optional

from envsync_helpers import ROOT, assert_no_sentinel
from envsync.dotenv import EnvFile, parse, parse_line
from envsync.example import load_example
from envsync.plan import Entry, Event, FilePlan, plan_file
from envsync.render import render, verify
from envsync.shared import (
    SAFE_VALUE,
    Group,
    Row,
    SharedFile,
    TableError,
    apply_shared,
    fill_line,
    load_table,
    placeholder_values,
    shared_keys_for,
)

AUDIT = (
    "audit-edge    edge-plane  AUDIT_EDGE_SECRET    gen\n"
    "audit-edge    obs-plane   AUDIT_EDGE_SECRET    gen\n"
)
GRAFANA = (
    "grafana-edge  edge-plane  GRAFANA_EDGE_SECRET  gen\n"
    "grafana-edge  obs-plane   GRAFANA_EDGE_SECRET  gen\n"
)
QDRANT = (
    "qdrant-key    data-plane  QDRANT_API_KEY       gen\n"
    "qdrant-key    docint      QDRANT_API_KEY       gen\n"
)
HOST = (
    "edge-host  edge-plane          EDGE_HOST  source\n"
    "edge-host  obs-plane           EDGE_HOST\n"
    "edge-host  open-webui-service  EDGE_HOST\n"
)
ROUTER = (
    "router-key  vllm-service        OPENAI_API_KEY  source\n"
    "router-key  docint              OPENAI_API_KEY\n"
    "router-key  Nextext             OPENAI_API_KEY\n"
    "router-key  translator          OPENAI_API_KEY\n"
    "router-key  open-webui-service  OPENAI_API_KEY\n"
)
TOKENS = (
    "nextext-token  docint   NEXTEXT_API_KEY         gen\n"
    "nextext-token  Nextext  NEXTEXT_SERVICE_TOKENS  gen  entry=docint\n"
)
TRAP = "QDRANT_API_KEY=   # required with data-plane\n"
TOKENS_EXAMPLE = "# NEXTEXT_SERVICE_TOKENS=docint=<token>\n"
GROUP_NAMES = [
    "edge-host",
    "docint-edge",
    "nextext-edge",
    "translator-edge",
    "open-webui-edge",
    "grafana-edge",
    "audit-edge",
    "qdrant-key",
    "openwebui-jwt",
    "nextext-token",
    "router-key",
]


def unset_everywhere(item: str) -> Event:
    return Event("unset_everywhere", [item], "warn")


def conflict(*found: str) -> Event:
    return Event("conflict", list(found), "error")


def event(plan: FilePlan, kind: str) -> Optional[Event]:
    for found in plan.events:
        if found.kind == kind:
            return found
    return None


def items(plan: FilePlan, kind: str) -> Optional[List[str]]:
    found = event(plan, kind)
    return None if found is None else found.items


def no_token() -> str:
    raise AssertionError("token() must not be called")


class Tokens:
    """A token stub that hands out the given values in order, each once."""

    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        if not self.values:
            raise AssertionError("token() called more often than expected")
        return self.values.pop(0)


class TableTests(unittest.TestCase):
    def test_table_errors(self) -> None:
        cases = {
            "one row": ("g  alpha  A_KEY\n", 1),
            "one row, after ignored lines": (
                "# header\n\nh  alpha  A_KEY\nh  beta  A_KEY\ng  alpha  B_KEY\n",
                5,
            ),
            "two sources": ("g  alpha  A_KEY  source\ng  beta  A_KEY  source\n", 2),
            "mixed gen": (
                "g  alpha  A_KEY  gen\ng  beta  A_KEY\ng  gamma  A_KEY  gen\n",
                2,
            ),
            "a key in two groups": (
                "g  alpha  A_KEY\ng  beta  A_KEY\nh  alpha  A_KEY\nh  gamma  A_KEY\n",
                3,
            ),
            "flag nope": ("g  alpha  A_KEY  nope\ng  beta  A_KEY\n", 1),
            "key bad-key": ("g  alpha  bad-key\ng  beta  A_KEY\n", 1),
            "bad group name": ("G  alpha  A_KEY\nG  beta  A_KEY\n", 1),
            "no key": ("g  alpha  A_KEY\ng  beta\n", 2),
            "empty entry name": ("g  alpha  A_KEY  entry=\ng  beta  A_KEY\n", 1),
            "repeated flag": ("g  alpha  A_KEY  gen  gen\ng  beta  A_KEY  gen\n", 1),
        }
        for name, (text, lineno) in cases.items():
            with self.subTest(name):
                with self.assertRaises(TableError) as ctx:
                    load_table(text)
                message = str(ctx.exception)
                self.assertTrue(
                    message.startswith(f"env-sync.conf line {lineno}: "), message
                )

    def test_table_rows(self) -> None:
        text = (
            "# header\n"
            "\n"
            "   # indented comment\n"
            "g  alpha  A_KEY  source  gen\n"
            "g\tbeta\tB_KEY\tgen\tentry=docint"
        )
        self.assertEqual(
            load_table(text),
            [
                Group(
                    "g",
                    [
                        Row("g", "alpha", "A_KEY", True, True, None, 4),
                        Row("g", "beta", "B_KEY", False, True, "docint", 5),
                    ],
                )
            ],
        )
        self.assertEqual(load_table("# only a comment\n\n"), [])

    def test_real_table_loads(self) -> None:
        text = (ROOT / "env-sync.conf").read_text()
        groups = load_table(text)
        self.assertEqual([group.name for group in groups], GROUP_NAMES)
        rows = {(row.member, row.key): row for group in groups for row in group.rows}
        self.assertEqual(len(rows), 26)
        self.assertEqual(
            sorted(key for key, row in rows.items() if row.source),
            [("edge-plane", "EDGE_HOST"), ("vllm-service", "OPENAI_API_KEY")],
        )
        self.assertEqual(
            [(row.member, row.entry) for row in rows.values() if row.entry],
            [("Nextext", "docint")],
        )
        self.assertEqual(
            [group.name for group in groups if not group.rows[0].gen],
            ["edge-host", "router-key"],
        )
        first = next(
            lineno
            for lineno, line in enumerate(text.split("\n"), start=1)
            if line.startswith("edge-host")
        )
        self.assertEqual(groups[0].rows[0].lineno, first)

    def test_shared_keys_for(self) -> None:
        groups = load_table((ROOT / "env-sync.conf").read_text())
        self.assertEqual(
            shared_keys_for(groups, "obs-plane"),
            frozenset({"EDGE_HOST", "GRAFANA_EDGE_SECRET", "AUDIT_EDGE_SECRET"}),
        )
        self.assertEqual(
            shared_keys_for(groups, "docint"),
            frozenset(
                {
                    "DOCINT_EDGE_SECRET",
                    "QDRANT_API_KEY",
                    "NEXTEXT_API_KEY",
                    "OPENAI_API_KEY",
                }
            ),
        )
        self.assertEqual(shared_keys_for(groups, "deploy"), frozenset())

    def test_placeholder_values(self) -> None:
        groups = load_table(AUDIT + HOST)
        example = load_example(
            "# renamed: AUDIT_OLDEST_SECRET -> AUDIT_SHARED_SECRET\n"
            "# renamed: AUDIT_SHARED_SECRET -> AUDIT_EDGE_SECRET\n"
            "AUDIT_EDGE_SECRET=change-me\n"
            "# AUDIT_EDGE_SECRET=<generate one>\n"
            "EDGE_HOST=127.0.0.1\n"
            "GRAFANA_ADMIN_PASSWORD=not-shared\n"
        )
        base = load_example(
            "AUDIT_SHARED_SECRET=old-placeholder\n"
            "# AUDIT_OLDEST_SECRET=oldest-placeholder\n"
            "EDGE_HOST=\n"
            "GRAFANA_ADMIN_PASSWORD=old-not-shared\n"
        )
        current = {"change-me", "<generate one>", "127.0.0.1"}
        self.assertEqual(
            placeholder_values(groups, "obs-plane", example, base),
            frozenset(current | {"old-placeholder", "oldest-placeholder"}),
        )
        self.assertEqual(
            placeholder_values(groups, "obs-plane", example, None), frozenset(current)
        )
        self.assertEqual(
            placeholder_values(groups, "deploy", example, base), frozenset()
        )


class FillLineTests(unittest.TestCase):
    def test_fill_line(self) -> None:
        cases = {
            None: "K=v",
            "K=old": "K=v",
            "export K=old": "export K=v",
            "export K=old  # note": "export K=v # note",
            "  K = old": "K=v",
            "K=old  # note": "K=v # note",
            "K=old # note   ": "K=v # note",
            "K=   # required with data-plane": "K=v # required with data-plane",
            "K=#trap": "K=v #trap",
            "K=   # one # two": "K=v # one # two",
            'K="old # inside" # note': "K=v # note",
            "K='o # ld'   # note": "K=v # note",
            'K="a\\"b # c" # note': "K=v # note",
            'K="old"': "K=v",
            "K=a#b": "K=v",
            "K=a\t# b": "K=v",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                existing = None if raw is None else parse_line(raw)
                line = fill_line(existing, "K", "v")
                self.assertEqual(line, expected)
                self.assertEqual(parse_line(line).value, "v")

    def test_safe_value(self) -> None:
        for value in (
            "sk-no-key-required",
            "a" * 64,
            "docint=abc,other=def",
            "gw.example.invalid",
            "http://gw.example.invalid:8080/p",
            "user@host+tag~1",
        ):
            self.assertIsNotNone(SAFE_VALUE.fullmatch(value), value)
        for value in (
            "",
            "a b",
            "a#b",
            "${X}",
            "$X",
            "'q'",
            '"q"',
            "a\tb",
            "a\\b",
            "a\n",
        ):
            self.assertIsNone(SAFE_VALUE.fullmatch(value), value)


class SharedCase(unittest.TestCase):
    """Builds members in memory and checks what every shared pass must satisfy:
    one event per kind on each plan, no sentinel in any event item, and every
    plan still renders and passes verify()."""

    def setUp(self) -> None:
        self.start("")

    def start(self, table: str) -> None:
        self.groups: List[Group] = load_table(table)
        self.files: Dict[str, SharedFile] = {}
        self.originals: Dict[str, Optional[EnvFile]] = {}

    def member(
        self,
        name: str,
        example: str,
        env: Optional[str],
        base: Optional[str] = None,
        remembered: FrozenSet[str] = frozenset(),
    ) -> FilePlan:
        ex = load_example(example)
        base_ex = load_example(base) if base is not None else None
        parsed = parse(env, "dotenv") if env is not None else None
        plan = plan_file(
            name,
            f"{name}/.env",
            ex,
            base_ex,
            parsed,
            shared_keys_for(self.groups, name),
        )
        self.files[name] = SharedFile(plan, ex, base_ex, remembered)
        self.originals[name] = parsed
        return plan

    def apply(
        self, generate: bool = False, token: Callable[[], str] = no_token
    ) -> List[Event]:
        events = apply_shared(self.groups, self.files, generate, token)
        for found in events:
            assert_no_sentinel(self, *found.items)
        for name, shared in self.files.items():
            kinds = [found.kind for found in shared.plan.events]
            self.assertEqual(len(kinds), len(set(kinds)), name)
            for found in shared.plan.events:
                assert_no_sentinel(self, *found.items)
            text = render(shared.example, shared.plan)
            verify(shared.example, shared.plan, self.originals[name], text)
        return events

    def plan(self, name: str) -> FilePlan:
        return self.files[name].plan

    def text(self, name: str) -> str:
        shared = self.files[name]
        return render(shared.example, shared.plan)

    def snapshot(self) -> Dict[str, object]:
        return {
            name: copy.deepcopy((shared.plan.entries, shared.plan.events))
            for name, shared in self.files.items()
        }


class FillTests(SharedCase):
    def test_fill_from_set_side(self) -> None:
        self.start(AUDIT)
        self.member(
            "edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=SENTINEL-s\n"
        )
        self.member(
            "obs-plane",
            "AUDIT_EDGE_SECRET=change-me\n",
            "AUDIT_EDGE_SECRET=change-me\n",
        )
        self.assertEqual(self.apply(), [])
        obs = self.plan("obs-plane")
        self.assertEqual(
            obs.entries["AUDIT_EDGE_SECRET"],
            Entry(
                "AUDIT_EDGE_SECRET",
                "AUDIT_EDGE_SECRET=SENTINEL-s",
                "SENTINEL-s",
                "filled",
            ),
        )
        self.assertEqual(
            obs.events, [Event("filled", ["AUDIT_EDGE_SECRET (from edge-plane)"])]
        )
        self.assertEqual(self.text("obs-plane"), "AUDIT_EDGE_SECRET=SENTINEL-s\n")
        edge = self.plan("edge-plane")
        self.assertEqual(edge.entries["AUDIT_EDGE_SECRET"].origin, "kept")
        self.assertIsNone(event(edge, "filled"))

    def test_placeholder_does_not_spread(self) -> None:
        # (edge example, edge env, obs example, obs base, obs env)
        cases = {
            "its own example": (
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                None,
                "AUDIT_EDGE_SECRET=change-me\n",
            ),
            "another member's example": (
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                None,
                "AUDIT_EDGE_SECRET=\n",
            ),
            "the synced example": (
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                "AUDIT_EDGE_SECRET=old-placeholder\n",
                "AUDIT_EDGE_SECRET=old-placeholder\n",
            ),
            "a commented example value": (
                "# AUDIT_EDGE_SECRET=example-only\nOTHER=1\n",
                "AUDIT_EDGE_SECRET=example-only\nOTHER=1\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                None,
                "AUDIT_EDGE_SECRET=change-me\n",
            ),
            "an angle-bracket placeholder": (
                "AUDIT_EDGE_SECRET=\n",
                "AUDIT_EDGE_SECRET=<secret>\n",
                "AUDIT_EDGE_SECRET=change-me\n",
                None,
                "AUDIT_EDGE_SECRET=\n",
            ),
        }
        for name, (edge_ex, edge_env, obs_ex, obs_base, obs_env) in cases.items():
            with self.subTest(name):
                self.start(AUDIT)
                self.member("edge-plane", edge_ex, edge_env)
                self.member("obs-plane", obs_ex, obs_env, base=obs_base)
                before = self.snapshot()
                self.assertEqual(
                    self.apply(),
                    [unset_everywhere("AUDIT_EDGE_SECRET (edge-plane, obs-plane)")],
                )
                self.assertEqual(self.snapshot(), before)

    def test_renamed_placeholder_does_not_spread(self) -> None:
        self.start(AUDIT)
        self.member("edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=\n")
        obs = self.member(
            "obs-plane",
            "# renamed: AUDIT_SHARED_SECRET -> AUDIT_EDGE_SECRET\n"
            "AUDIT_EDGE_SECRET=change-me\n",
            "AUDIT_SHARED_SECRET=old-placeholder\n",
            base="AUDIT_SHARED_SECRET=old-placeholder\n",
        )
        self.assertEqual(obs.entries["AUDIT_EDGE_SECRET"].origin, "carried")
        before = self.snapshot()
        self.assertEqual(
            self.apply(),
            [unset_everywhere("AUDIT_EDGE_SECRET (edge-plane, obs-plane)")],
        )
        self.assertEqual(self.snapshot(), before)

    def test_trap_counts_unset_and_keeps_comment(self) -> None:
        self.start(QDRANT)
        self.member("data-plane", "QDRANT_API_KEY=\n", "QDRANT_API_KEY=SENTINEL-q\n")
        self.member("docint", TRAP, TRAP)
        self.assertEqual(self.apply(), [])
        docint = self.plan("docint")
        self.assertEqual(
            docint.entries["QDRANT_API_KEY"],
            Entry(
                "QDRANT_API_KEY",
                "QDRANT_API_KEY=SENTINEL-q # required with data-plane",
                "SENTINEL-q",
                "filled",
            ),
        )
        self.assertEqual(items(docint, "filled"), ["QDRANT_API_KEY (from data-plane)"])

    def test_conflict_changes_nothing(self) -> None:
        self.start(AUDIT)
        self.member(
            "edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=SENTINEL-a\n"
        )
        self.member(
            "obs-plane",
            "AUDIT_EDGE_SECRET=change-me\n",
            "AUDIT_EDGE_SECRET=SENTINEL-b\n",
        )
        before = self.snapshot()
        events = self.apply()
        self.assertEqual(
            events, [conflict("AUDIT_EDGE_SECRET: obs-plane differs from edge-plane")]
        )
        self.assertEqual(events[0].level, "error")
        self.assertEqual(self.snapshot(), before)

    def test_conflict_names_every_differing_member(self) -> None:
        table = (
            "shared  alpha  SHARED_SECRET\n"
            "shared  beta   SHARED_SECRET\n"
            "shared  gamma  SHARED_SECRET\n"
            "shared  delta  SHARED_SECRET\n"
        )
        self.start(table)
        self.member("alpha", "SHARED_SECRET=\n", "SHARED_SECRET=SENTINEL-a\n")
        self.member("beta", "SHARED_SECRET=\n", "SHARED_SECRET=SENTINEL-b\n")
        self.member("gamma", "SHARED_SECRET=\n", "SHARED_SECRET=SENTINEL-a\n")
        self.member("delta", "SHARED_SECRET=\n", "SHARED_SECRET=\n")
        before = self.snapshot()
        self.assertEqual(
            self.apply(), [conflict("SHARED_SECRET: beta differs from alpha")]
        )
        self.assertEqual(self.snapshot(), before)
        self.start(table)
        for name in ("alpha", "beta", "gamma"):
            self.member(name, "SHARED_SECRET=\n", f"SHARED_SECRET=SENTINEL-{name}\n")
        self.assertEqual(
            self.apply(),
            [
                conflict(
                    "SHARED_SECRET: beta differs from alpha",
                    "SHARED_SECRET: gamma differs from alpha",
                )
            ],
        )

    def test_source_counts_as_set(self) -> None:
        self.start(HOST)
        self.member("edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=127.0.0.1\n")
        self.member(
            "obs-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=gw.example.invalid\n"
        )
        self.assertEqual(
            self.apply(), [conflict("EDGE_HOST: obs-plane differs from edge-plane")]
        )

        self.start(HOST)
        self.member("edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=127.0.0.1\n")
        self.member("obs-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=127.0.0.1\n")
        before = self.snapshot()
        self.assertEqual(self.apply(), [])
        self.assertEqual(self.snapshot(), before)

    def test_source_is_the_reference_wherever_it_stands(self) -> None:
        self.start(
            "edge-host  obs-plane   EDGE_HOST\n"
            "edge-host  edge-plane  EDGE_HOST  source\n"
        )
        self.member(
            "obs-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=gw.example.invalid\n"
        )
        self.member("edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=127.0.0.1\n")
        self.assertEqual(
            self.apply(), [conflict("EDGE_HOST: obs-plane differs from edge-plane")]
        )

    def test_source_fills_every_other_member(self) -> None:
        self.start(HOST)
        self.member(
            "edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=gw.example.invalid\n"
        )
        self.member("obs-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=127.0.0.1\n")
        self.member("open-webui-service", "EDGE_HOST=127.0.0.1\n", None)
        self.assertEqual(self.apply(), [])
        for name in ("obs-plane", "open-webui-service"):
            with self.subTest(name):
                self.assertEqual(
                    self.plan(name).entries["EDGE_HOST"],
                    Entry(
                        "EDGE_HOST",
                        "EDGE_HOST=gw.example.invalid",
                        "gw.example.invalid",
                        "filled",
                    ),
                )
                self.assertEqual(
                    items(self.plan(name), "filled"), ["EDGE_HOST (from edge-plane)"]
                )

    def test_router_key_reaches_apps(self) -> None:
        self.start(ROUTER)
        key = "OPENAI_API_KEY=sk-no-key-required\n"
        self.member("vllm-service", key, key)
        self.member("docint", key, key)
        self.member(
            "open-webui-service",
            "OPENAI_API_KEY=sk-1234567890\n",
            "OPENAI_API_KEY=sk-1234567890\n",
        )
        self.member(
            "translator",
            "# Inference\n# OPENAI_API_KEY=ollama\nTEXT_MODEL=m\n",
            "TEXT_MODEL=m\n",
        )
        self.assertEqual(self.apply(), [])
        filled = Entry(
            "OPENAI_API_KEY",
            "OPENAI_API_KEY=sk-no-key-required",
            "sk-no-key-required",
            "filled",
        )
        for name in ("open-webui-service", "translator"):
            with self.subTest(name):
                self.assertEqual(self.plan(name).entries["OPENAI_API_KEY"], filled)
                self.assertEqual(
                    items(self.plan(name), "filled"),
                    ["OPENAI_API_KEY (from vllm-service)"],
                )
        self.assertEqual(
            self.text("translator"),
            "# Inference\nOPENAI_API_KEY=sk-no-key-required\nTEXT_MODEL=m\n",
        )
        self.assertEqual(self.plan("docint").entries["OPENAI_API_KEY"].origin, "kept")
        self.assertIsNone(event(self.plan("docint"), "filled"))

    def test_unsafe_not_copied(self) -> None:
        for name, raw in {
            "a blank": 'AUDIT_EDGE_SECRET="SENTINEL-a b"\n',
            "a newline": 'AUDIT_EDGE_SECRET="SENTINEL-n\\n"\n',
        }.items():
            with self.subTest(name):
                self.start(AUDIT)
                self.member("edge-plane", "AUDIT_EDGE_SECRET=\n", raw)
                obs = self.member(
                    "obs-plane",
                    "AUDIT_EDGE_SECRET=change-me\n",
                    "AUDIT_EDGE_SECRET=change-me\n",
                )
                self.assertEqual(self.apply(), [])
                self.assertEqual(
                    obs.entries["AUDIT_EDGE_SECRET"],
                    Entry(
                        "AUDIT_EDGE_SECRET",
                        "AUDIT_EDGE_SECRET=change-me",
                        "change-me",
                        "kept",
                    ),
                )
                self.assertEqual(
                    obs.events, [Event("unsafe", ["AUDIT_EDGE_SECRET"], "warn")]
                )

    def test_missing_member_and_unknown_key(self) -> None:
        self.start(
            "audit-edge    edge-plane          AUDIT_EDGE_SECRET    gen\n"
            "audit-edge    obs-plane           AUDIT_EDGE_SECRET    gen\n"
            "audit-edge    open-webui-service  AUDIT_EDGE_SECRET    gen\n"
            "grafana-edge  edge-plane          GRAFANA_EDGE_SECRET  gen\n"
            "grafana-edge  obs-plane           GRAFANA_EDGE_SECRET  gen\n"
            "grafana-edge  docint              GRAFANA_EDGE_SECRET  gen\n"
        )
        self.member(
            "edge-plane",
            "AUDIT_EDGE_SECRET=\nGRAFANA_EDGE_SECRET=\n",
            "AUDIT_EDGE_SECRET=SENTINEL-s\nGRAFANA_EDGE_SECRET=\n",
        )
        obs = self.member(
            "obs-plane",
            "AUDIT_EDGE_SECRET=change-me\nGRAFANA_EDGE_SECRET=\n",
            "AUDIT_EDGE_SECRET=change-me\nGRAFANA_EDGE_SECRET=\n",
        )
        self.member("docint", "OTHER=1\n", "OTHER=1\n")
        self.assertEqual(
            self.apply(),
            [
                unset_everywhere("GRAFANA_EDGE_SECRET (edge-plane, obs-plane)"),
                Event(
                    "table",
                    ["env-sync.conf: GRAFANA_EDGE_SECRET is not in docint's example"],
                    "warn",
                ),
            ],
        )
        self.assertEqual(obs.entries["AUDIT_EDGE_SECRET"].origin, "filled")
        self.assertEqual(items(obs, "filled"), ["AUDIT_EDGE_SECRET (from edge-plane)"])

    def test_lone_row_is_not_evaluated(self) -> None:
        self.start(TOKENS + AUDIT)
        self.member(
            "Nextext", TOKENS_EXAMPLE, "NEXTEXT_SERVICE_TOKENS=other=SENTINEL-o\n"
        )
        self.member("edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=\n")
        before = self.snapshot()
        self.assertEqual(self.apply(generate=True), [])
        self.assertEqual(self.snapshot(), before)

    def test_unset_on_purpose_not_filled(self) -> None:
        example = "QDRANT_API_KEY=\nOTHER=1\n"
        self.start(QDRANT)
        self.member("data-plane", "QDRANT_API_KEY=\n", "QDRANT_API_KEY=SENTINEL-q\n")
        docint = self.member("docint", example, "OTHER=1\n", base=example)
        self.assertIn("QDRANT_API_KEY", docint.unset)
        self.assertEqual(self.apply(), [])
        self.assertNotIn("QDRANT_API_KEY", docint.entries)
        self.assertEqual(items(docint, "kept_unset"), ["QDRANT_API_KEY"])
        self.assertIsNone(event(docint, "filled"))

    def test_generation_skips_a_group_kept_unset_on_purpose(self) -> None:
        example = "QDRANT_API_KEY=\nOTHER=1\n"
        self.start(QDRANT)
        self.member("data-plane", "QDRANT_API_KEY=\n", "QDRANT_API_KEY=\n")
        docint = self.member("docint", example, "OTHER=1\n", base=example)
        before = self.snapshot()
        self.assertEqual(
            self.apply(generate=True),
            [unset_everywhere("QDRANT_API_KEY (data-plane, docint)")],
        )
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(items(docint, "kept_unset"), ["QDRANT_API_KEY"])


class EntryRowTests(SharedCase):
    def tokens(
        self,
        docint_env: Optional[str],
        nextext_env: Optional[str],
        generate: bool = False,
        token: Callable[[], str] = no_token,
    ) -> List[Event]:
        self.start(TOKENS)
        self.member("docint", "# NEXTEXT_API_KEY=\nOTHER=1\n", docint_env)
        self.member("Nextext", TOKENS_EXAMPLE, nextext_env)
        return self.apply(generate, token)

    def test_entry_row(self) -> None:
        self.assertEqual(self.tokens("NEXTEXT_API_KEY=SENTINEL-t\n", None), [])
        nextext = self.plan("Nextext")
        self.assertEqual(
            nextext.entries["NEXTEXT_SERVICE_TOKENS"],
            Entry(
                "NEXTEXT_SERVICE_TOKENS",
                "NEXTEXT_SERVICE_TOKENS=docint=SENTINEL-t",
                "docint=SENTINEL-t",
                "filled",
            ),
        )
        self.assertEqual(
            items(nextext, "filled"), ["NEXTEXT_SERVICE_TOKENS (from docint)"]
        )
        self.assertEqual(
            self.text("Nextext"), "NEXTEXT_SERVICE_TOKENS=docint=SENTINEL-t\n"
        )

        events = self.tokens(
            "NEXTEXT_API_KEY=SENTINEL-t\n", "NEXTEXT_SERVICE_TOKENS=other=SENTINEL-o\n"
        )
        self.assertEqual(
            events, [conflict("NEXTEXT_SERVICE_TOKENS: Nextext differs from docint")]
        )
        self.assertEqual(
            self.plan("Nextext").entries["NEXTEXT_SERVICE_TOKENS"].origin, "kept"
        )

        events = self.tokens(
            "NEXTEXT_API_KEY=SENTINEL-t\n",
            "NEXTEXT_SERVICE_TOKENS=docint=SENTINEL-t,other=x\n",
        )
        self.assertEqual(events, [])
        for name in ("docint", "Nextext"):
            self.assertIsNone(event(self.plan(name), "filled"), name)

    def test_list_fills_the_single_key(self) -> None:
        events = self.tokens(
            "OTHER=1\n",
            "NEXTEXT_SERVICE_TOKENS=other=SENTINEL-o, docint = SENTINEL-t \n",
        )
        self.assertEqual(events, [])
        docint = self.plan("docint")
        self.assertEqual(
            docint.entries["NEXTEXT_API_KEY"],
            Entry(
                "NEXTEXT_API_KEY", "NEXTEXT_API_KEY=SENTINEL-t", "SENTINEL-t", "filled"
            ),
        )
        self.assertEqual(items(docint, "filled"), ["NEXTEXT_API_KEY (from Nextext)"])

    def test_unset_lists_are_filled(self) -> None:
        filled = "NEXTEXT_SERVICE_TOKENS=docint=SENTINEL-t"
        for name, (raw, expected) in {
            "the example's placeholder": (
                "NEXTEXT_SERVICE_TOKENS=docint=<token>\n",
                filled,
            ),
            "an empty token": ("NEXTEXT_SERVICE_TOKENS=docint=\n", filled),
            "an empty list": ("NEXTEXT_SERVICE_TOKENS=\n", filled),
            "only empty items": ("NEXTEXT_SERVICE_TOKENS= , \n", filled),
            "the trap": (
                "NEXTEXT_SERVICE_TOKENS=   # docint's token goes here\n",
                filled + " # docint's token goes here",
            ),
            "the trap, with name=token in its comment": (
                "NEXTEXT_SERVICE_TOKENS=   # docint=the token docint sends\n",
                filled + " # docint=the token docint sends",
            ),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.tokens("NEXTEXT_API_KEY=SENTINEL-t\n", raw), [])
                self.assertEqual(
                    self.plan("Nextext").entries["NEXTEXT_SERVICE_TOKENS"].raw, expected
                )

    def test_a_bare_placeholder_item_is_unset(self) -> None:
        self.assertEqual(
            self.tokens(
                "NEXTEXT_API_KEY=SENTINEL-t\n", "NEXTEXT_SERVICE_TOKENS=<your-token>\n"
            ),
            [],
        )
        nextext = self.plan("Nextext")
        self.assertEqual(
            nextext.entries["NEXTEXT_SERVICE_TOKENS"],
            Entry(
                "NEXTEXT_SERVICE_TOKENS",
                "NEXTEXT_SERVICE_TOKENS=docint=SENTINEL-t",
                "docint=SENTINEL-t",
                "filled",
            ),
        )
        self.assertEqual(
            items(nextext, "filled"), ["NEXTEXT_SERVICE_TOKENS (from docint)"]
        )

    def test_a_comma_never_enters_a_list(self) -> None:
        # The list separator inside the shared token would add an item to the
        # list, and the next run would read a different token.
        self.assertEqual(self.tokens("NEXTEXT_API_KEY=SENTINEL-a,other=b\n", None), [])
        nextext = self.plan("Nextext")
        self.assertNotIn("NEXTEXT_SERVICE_TOKENS", nextext.entries)
        self.assertEqual(
            nextext.events, [Event("unsafe", ["NEXTEXT_SERVICE_TOKENS"], "warn")]
        )

    def test_list_example_values_are_placeholders(self) -> None:
        # A token in the list's example is a placeholder on both sides, and so
        # is a whole example list, even one that is not name=token.
        cases = {
            "the example's token, as the single key": (
                "# NEXTEXT_SERVICE_TOKENS=docint=change-me\n",
                "NEXTEXT_API_KEY=change-me\n",
                None,
            ),
            "the example's token, in a longer list": (
                "# NEXTEXT_SERVICE_TOKENS=docint=change-me\n",
                "OTHER=1\n",
                "NEXTEXT_SERVICE_TOKENS=docint=change-me,\n",
            ),
            "a whole example list": (
                "# NEXTEXT_SERVICE_TOKENS=none\n",
                "OTHER=1\n",
                "NEXTEXT_SERVICE_TOKENS=none\n",
            ),
        }
        for name, (nextext_ex, docint_env, nextext_env) in cases.items():
            with self.subTest(name):
                self.start(TOKENS)
                self.member("docint", "# NEXTEXT_API_KEY=\nOTHER=1\n", docint_env)
                self.member("Nextext", nextext_ex, nextext_env)
                before = self.snapshot()
                self.assertEqual(
                    self.apply(),
                    [unset_everywhere("NEXTEXT_API_KEY (docint, Nextext)")],
                )
                self.assertEqual(self.snapshot(), before)

    def test_a_set_list_is_never_appended_to(self) -> None:
        # (docint env, Nextext env, conflict item)
        cases = {
            "a placeholder entry beside a real one": (
                "NEXTEXT_API_KEY=SENTINEL-t\n",
                "NEXTEXT_SERVICE_TOKENS=docint=<token>,other=SENTINEL-o\n",
                "NEXTEXT_SERVICE_TOKENS: Nextext differs from docint",
            ),
            "no entry, the single key unset": (
                "OTHER=1\n",
                "NEXTEXT_SERVICE_TOKENS=other=SENTINEL-o\n",
                "NEXTEXT_SERVICE_TOKENS: Nextext has no docint entry",
            ),
            "an item that is not name=token": (
                "OTHER=1\n",
                "NEXTEXT_SERVICE_TOKENS=SENTINEL-x\n",
                "NEXTEXT_SERVICE_TOKENS: Nextext has no docint entry",
            ),
        }
        for name, (docint_env, nextext_env, item) in cases.items():
            with self.subTest(name):
                self.start(TOKENS)
                self.member("docint", "# NEXTEXT_API_KEY=\nOTHER=1\n", docint_env)
                self.member("Nextext", TOKENS_EXAMPLE, nextext_env)
                before = self.snapshot()
                self.assertEqual(self.apply(), [conflict(item)])
                self.assertEqual(self.snapshot(), before)

    def test_generate_fills_list_and_key(self) -> None:
        stub = Tokens("a" * 64)
        self.assertEqual(self.tokens("OTHER=1\n", None, generate=True, token=stub), [])
        self.assertEqual(stub.values, [])
        self.assertEqual(
            self.plan("docint").entries["NEXTEXT_API_KEY"],
            Entry(
                "NEXTEXT_API_KEY", "NEXTEXT_API_KEY=" + "a" * 64, "a" * 64, "generated"
            ),
        )
        self.assertEqual(
            self.plan("Nextext").entries["NEXTEXT_SERVICE_TOKENS"],
            Entry(
                "NEXTEXT_SERVICE_TOKENS",
                "NEXTEXT_SERVICE_TOKENS=docint=" + "a" * 64,
                "docint=" + "a" * 64,
                "generated",
            ),
        )


class GenerateTests(SharedCase):
    def generated(self, generate: bool, token: Callable[[], str]) -> List[Event]:
        self.start(AUDIT + QDRANT + GRAFANA)
        self.member(
            "edge-plane",
            "AUDIT_EDGE_SECRET=\nGRAFANA_EDGE_SECRET=\n",
            "AUDIT_EDGE_SECRET=\nGRAFANA_EDGE_SECRET=SENTINEL-g\n",
        )
        self.member(
            "obs-plane", "AUDIT_EDGE_SECRET=change-me\nGRAFANA_EDGE_SECRET=\n", None
        )
        self.member("data-plane", "QDRANT_API_KEY=\n", "QDRANT_API_KEY=\n")
        self.member("docint", TRAP, TRAP)
        return self.apply(generate, token)

    def origins(self) -> List[str]:
        return [
            entry.origin
            for shared in self.files.values()
            for entry in shared.plan.entries.values()
        ]

    def test_generate(self) -> None:
        self.assertEqual(
            self.generated(False, no_token),
            [
                unset_everywhere("AUDIT_EDGE_SECRET (edge-plane, obs-plane)"),
                unset_everywhere("QDRANT_API_KEY (data-plane, docint)"),
            ],
        )
        self.assertNotIn("generated", self.origins())
        self.assertEqual(
            self.plan("obs-plane").entries["GRAFANA_EDGE_SECRET"].origin, "filled"
        )

        stub = Tokens("a" * 64, "b" * 64)
        self.assertEqual(self.generated(True, stub), [])
        self.assertEqual(stub.values, [])
        for name, key, value in (
            ("edge-plane", "AUDIT_EDGE_SECRET", "a" * 64),
            ("obs-plane", "AUDIT_EDGE_SECRET", "a" * 64),
            ("data-plane", "QDRANT_API_KEY", "b" * 64),
            ("docint", "QDRANT_API_KEY", "b" * 64),
        ):
            with self.subTest(name=name, key=key):
                entry = self.plan(name).entries[key]
                self.assertEqual((entry.value, entry.origin), (value, "generated"))
                self.assertEqual(items(self.plan(name), "generated"), [key])
        self.assertEqual(
            self.plan("docint").entries["QDRANT_API_KEY"].raw,
            "QDRANT_API_KEY=" + "b" * 64 + " # required with data-plane",
        )
        obs = self.plan("obs-plane")
        self.assertEqual(
            (
                obs.entries["GRAFANA_EDGE_SECRET"].value,
                obs.entries["GRAFANA_EDGE_SECRET"].origin,
            ),
            ("SENTINEL-g", "filled"),
        )
        self.assertEqual([found.kind for found in obs.events], ["filled", "generated"])
        for shared in self.files.values():
            for found in shared.plan.events:
                for item in found.items:
                    self.assertNotIn("a" * 64, item)
                    self.assertNotIn("b" * 64, item)


class PlannerNotesTests(SharedCase):
    """A fill replaces the planner's notes about its key, and only those."""

    def test_added_default_gives_way_to_the_fill(self) -> None:
        self.start(HOST)
        self.member(
            "edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=gw.example.invalid\n"
        )
        obs = self.member("obs-plane", "OTHER=1\nEDGE_HOST=127.0.0.1\n", "OTHER=1\n")
        self.assertEqual(items(obs, "added"), ["EDGE_HOST=127.0.0.1"])
        self.assertEqual(self.apply(), [])
        self.assertEqual(obs.events, [Event("filled", ["EDGE_HOST (from edge-plane)"])])

    def test_held_placeholder_gives_way_to_the_fill(self) -> None:
        self.start(AUDIT)
        self.member(
            "edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=SENTINEL-s\n"
        )
        obs = self.member(
            "obs-plane",
            "AUDIT_EDGE_SECRET=change-me\n",
            "AUDIT_EDGE_SECRET=old-placeholder\n",
            base="AUDIT_EDGE_SECRET=old-placeholder\n",
        )
        self.assertEqual(items(obs, "held"), ["AUDIT_EDGE_SECRET"])
        self.assertEqual(self.apply(), [])
        self.assertEqual(obs.entries["AUDIT_EDGE_SECRET"].origin, "filled")
        self.assertEqual(
            obs.events, [Event("filled", ["AUDIT_EDGE_SECRET (from edge-plane)"])]
        )

    def test_other_keys_keep_their_notes(self) -> None:
        self.start(HOST)
        self.member(
            "edge-plane", "EDGE_HOST=127.0.0.1\n", "EDGE_HOST=gw.example.invalid\n"
        )
        obs = self.member(
            "obs-plane",
            "OTHER=1\nEDGE_HOST=127.0.0.1\nEDGE_HOST_2=127.0.0.2\nLOG_LEVEL=info\n",
            "OTHER=1\n",
        )
        self.assertEqual(self.apply(), [])
        self.assertEqual(
            obs.events,
            [
                Event("added", ["EDGE_HOST_2=127.0.0.2", "LOG_LEVEL=info"]),
                Event("filled", ["EDGE_HOST (from edge-plane)"]),
            ],
        )

    def test_held_change_and_differs_give_way_to_the_fill(self) -> None:
        # held: "KEY: old -> new"; differs: "KEY", next to a key it prefixes.
        for name, (example, base, env, kind, others) in {
            "held": (
                "EDGE_HOST=10.0.0.1\n",
                "EDGE_HOST=127.0.0.1\n",
                "EDGE_HOST=127.0.0.1\n",
                "held",
                None,
            ),
            "differs": (
                "EDGE_HOST=127.0.0.1\nEDGE_HOST_2=127.0.0.2\n",
                None,
                "EDGE_HOST=<your host>\nEDGE_HOST_2=gw2.example.invalid\n",
                "differs",
                ["EDGE_HOST_2"],
            ),
        }.items():
            with self.subTest(name):
                self.start(HOST)
                edge = self.member(
                    "edge-plane",
                    "EDGE_HOST=127.0.0.1\n",
                    "EDGE_HOST=gw.example.invalid\n",
                )
                obs = self.member("obs-plane", example, env, base=base)
                self.assertIsNotNone(items(obs, kind))
                self.assertEqual(self.apply(), [])
                self.assertEqual(items(obs, kind), others)
                self.assertEqual(items(obs, "filled"), ["EDGE_HOST (from edge-plane)"])
                self.assertEqual(items(edge, "differs"), ["EDGE_HOST"])

    def test_generation_replaces_the_added_note(self) -> None:
        self.start(AUDIT)
        for name in ("edge-plane", "obs-plane"):
            self.member(name, "OTHER=1\nAUDIT_EDGE_SECRET=\n", "OTHER=1\n")
        self.assertEqual(self.apply(generate=True, token=Tokens("a" * 64)), [])
        for name in ("edge-plane", "obs-plane"):
            with self.subTest(name):
                self.assertEqual(
                    self.plan(name).events,
                    [Event("generated", ["AUDIT_EDGE_SECRET"])],
                )


class RememberedTests(SharedCase):
    """Placeholders of examples older than the synced copy, kept per file."""

    NEW = "AUDIT_EDGE_SECRET=change-me\n"

    def old_placeholder(self, remembered: FrozenSet[str]) -> None:
        # The run after the sync: the synced copy is already the new example,
        # whose placeholder text changed; obs-plane still holds the old one.
        self.start(AUDIT)
        self.member("edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=\n")
        self.member(
            "obs-plane",
            self.NEW,
            "AUDIT_EDGE_SECRET=old-placeholder\n",
            base=self.NEW,
            remembered=remembered,
        )

    def test_remembered_placeholder_does_not_spread(self) -> None:
        self.old_placeholder(frozenset({"old-placeholder"}))
        before = self.snapshot()
        self.assertEqual(
            self.apply(),
            [unset_everywhere("AUDIT_EDGE_SECRET (edge-plane, obs-plane)")],
        )
        self.assertEqual(self.snapshot(), before)
        # Without the memory, the old placeholder reads as a real value and
        # spreads: the one-run-late case the memory exists for.
        self.old_placeholder(frozenset())
        self.assertEqual(self.apply(), [])
        self.assertEqual(
            self.plan("edge-plane").entries["AUDIT_EDGE_SECRET"].origin, "filled"
        )

    def test_remembered_value_is_filled_from_the_other_side(self) -> None:
        remembered = frozenset({"Lk2-old-default"})
        for holder in ("obs-plane", "edge-plane"):
            with self.subTest(remembered_by=holder):
                self.start(AUDIT)
                self.member(
                    "edge-plane",
                    "AUDIT_EDGE_SECRET=\n",
                    "AUDIT_EDGE_SECRET=SENTINEL-s\n",
                    remembered=remembered if holder == "edge-plane" else frozenset(),
                )
                obs = self.member(
                    "obs-plane",
                    self.NEW,
                    "AUDIT_EDGE_SECRET=Lk2-old-default\n",
                    remembered=remembered if holder == "obs-plane" else frozenset(),
                )
                self.assertEqual(self.apply(), [])
                self.assertEqual(
                    obs.entries["AUDIT_EDGE_SECRET"],
                    Entry(
                        "AUDIT_EDGE_SECRET",
                        "AUDIT_EDGE_SECRET=SENTINEL-s",
                        "SENTINEL-s",
                        "filled",
                    ),
                )
                self.assertEqual(
                    items(obs, "filled"), ["AUDIT_EDGE_SECRET (from edge-plane)"]
                )
        # Not remembered, the same value is real: two set values conflict.
        self.start(AUDIT)
        self.member(
            "edge-plane", "AUDIT_EDGE_SECRET=\n", "AUDIT_EDGE_SECRET=SENTINEL-s\n"
        )
        self.member("obs-plane", self.NEW, "AUDIT_EDGE_SECRET=Lk2-old-default\n")
        self.assertEqual(
            self.apply(),
            [conflict("AUDIT_EDGE_SECRET: obs-plane differs from edge-plane")],
        )

    def test_remembered_list_tokens_are_placeholders(self) -> None:
        # An older Nextext example's list keeps its token a placeholder on
        # docint's side too.
        self.start(TOKENS)
        self.member(
            "docint", "# NEXTEXT_API_KEY=\nOTHER=1\n", "NEXTEXT_API_KEY=old-token\n"
        )
        self.member(
            "Nextext",
            TOKENS_EXAMPLE,
            None,
            remembered=frozenset({"docint=old-token"}),
        )
        before = self.snapshot()
        self.assertEqual(
            self.apply(), [unset_everywhere("NEXTEXT_API_KEY (docint, Nextext)")]
        )
        self.assertEqual(self.snapshot(), before)


class RoundTripTests(SharedCase):
    EXAMPLES = {
        "edge-plane": "# Edge\nAUDIT_EDGE_SECRET=\n",
        "obs-plane": "# Obs\nAUDIT_EDGE_SECRET=change-me  # same value as edge-plane\n",
        "data-plane": "QDRANT_API_KEY=\n",
        "docint": TRAP,
    }

    def test_filled_files_render_and_replan_cleanly(self) -> None:
        self.start(AUDIT + QDRANT)
        self.member(
            "edge-plane", self.EXAMPLES["edge-plane"], "AUDIT_EDGE_SECRET=SENTINEL-s\n"
        )
        self.member("obs-plane", self.EXAMPLES["obs-plane"], None)
        self.member(
            "data-plane", self.EXAMPLES["data-plane"], "QDRANT_API_KEY=SENTINEL-q\n"
        )
        self.member("docint", self.EXAMPLES["docint"], self.EXAMPLES["docint"])
        self.assertEqual(self.apply(), [])
        first = {name: self.text(name) for name in self.files}
        self.assertEqual(
            first["obs-plane"],
            "# Obs\nAUDIT_EDGE_SECRET=SENTINEL-s # same value as edge-plane\n",
        )
        self.assertEqual(
            first["docint"], "QDRANT_API_KEY=SENTINEL-q # required with data-plane\n"
        )

        # The next run: each written file, planned against its example as the
        # synced copy, needs nothing more and renders byte for byte the same.
        self.start(AUDIT + QDRANT)
        for name, example in self.EXAMPLES.items():
            self.member(name, example, first[name], base=example)
        self.assertEqual(self.apply(), [])
        for name, shared in self.files.items():
            with self.subTest(name):
                self.assertEqual(self.text(name), first[name])
                self.assertEqual(
                    {entry.origin for entry in shared.plan.entries.values()}, {"kept"}
                )
                self.assertEqual(shared.plan.events, [])


if __name__ == "__main__":
    unittest.main()
