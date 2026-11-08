from __future__ import annotations

import unittest
from unittest import mock

from envsync_helpers import assert_no_sentinel
from envsync import render as render_mod
from envsync.dotenv import parse
from envsync.example import load_example
from envsync.plan import MARKER, plan_file
from envsync.render import ORPHAN_HEADER, CheckError, render, verify

EDGE_EXAMPLE = (
    "# Edge\n"
    "KEY=v            # doc\n"
    "                               # continuation one\n"
    "                               # continuation two\n"
    "OTHER=2\n"
)

# name -> (example, base, env)
FIXTURES = {
    "substitute": (
        "# Doc\nHOST=127.0.0.1\nPORT=8080\n",
        None,
        "PORT=9090  # mine\nHOST=gw.example.invalid\n",
    ),
    "commented": ("# A comment\n# MODE=fast\n", None, "MODE=slow\n"),
    "unset": ("A=1\nB=2", "A=1\nB=2", "A=1\n"),
    "orphan": (
        "A=1\n",
        None,
        "A=1\n# lab proxy\nHTTP_PROXY=SENTINEL-p\n",
    ),
    "rename_note": (
        "# renamed: OLD -> NEW\nNEW=1\n",
        None,
        "OLD=SENTINEL-o\n",
    ),
    "edge": (EDGE_EXAMPLE, None, "KEY=custom\nOTHER=2\n"),
}


def plan_of(name: str):
    e, base, env = FIXTURES[name]
    example = load_example(e)
    base_ex = load_example(base) if base is not None else None
    plan = plan_file("member", "member/.env", example, base_ex, parse(env, "dotenv"))
    return example, plan, parse(env, "dotenv")


class RenderTests(unittest.TestCase):
    def test_marker_is_the_plan_marker(self) -> None:
        from envsync import plan

        self.assertIs(render_mod.MARKER, plan.MARKER)
        self.assertEqual(MARKER, "# [env-sync]")

    def test_substitutes_at_anchor(self) -> None:
        example, plan, _ = plan_of("substitute")
        self.assertEqual(
            render(example, plan),
            "# Doc\nHOST=gw.example.invalid\nPORT=9090  # mine\n",
        )

    def test_commented_anchor(self) -> None:
        example, plan, _ = plan_of("commented")
        self.assertEqual(render(example, plan), "# A comment\nMODE=slow\n")

    def test_unset_written_commented(self) -> None:
        example, plan, _ = plan_of("unset")
        self.assertEqual(render(example, plan), "A=1\n# B=2\n")

    def test_orphan_block(self) -> None:
        example, plan, _ = plan_of("orphan")
        self.assertEqual(
            render(example, plan),
            "A=1\n\n" + ORPHAN_HEADER + "\n# lab proxy\nHTTP_PROXY=SENTINEL-p\n",
        )

    def test_orphan_header_text(self) -> None:
        self.assertEqual(
            ORPHAN_HEADER,
            "# [env-sync] Not in .env.example: kept as they were. Delete what is obsolete.",
        )

    def test_orphan_after_blank_adds_no_extra_blank(self) -> None:
        example = load_example("A=1\n\n")
        plan = plan_file(
            "m", "m/.env", example, None, parse("A=1\nZ=SENTINEL-z\n", "dotenv")
        )
        text = render(example, plan)
        self.assertIn("\n\n" + ORPHAN_HEADER + "\n", text)
        self.assertNotIn("\n\n\n", text)

    def test_orphan_note_line(self) -> None:
        example, plan, _ = plan_of("rename_note")
        text = render(example, plan)
        self.assertIn(
            "# [env-sync] renamed to NEW in the example; value not carried\n"
            "OLD=SENTINEL-o\n",
            text,
        )

    def test_rerun_is_identical(self) -> None:
        for name, (e, _base, _env) in FIXTURES.items():
            with self.subTest(name):
                example, plan, _ = plan_of(name)
                text = render(example, plan)
                again = plan_file(
                    "member", "member/.env", example, example, parse(text, "dotenv")
                )
                self.assertEqual(render(example, again), text)
                self.assertLessEqual(text.count(ORPHAN_HEADER), 1)

    def test_edge_style_example(self) -> None:
        example, plan, _ = plan_of("edge")
        text = render(example, plan)
        lines = text.split("\n")
        self.assertEqual(lines[2], " " * 31 + "# continuation one")
        self.assertEqual(lines[3], " " * 31 + "# continuation two")
        self.assertEqual(lines[1], "KEY=custom")


class VerifyTests(unittest.TestCase):
    def test_verify_passes(self) -> None:
        for name in FIXTURES:
            with self.subTest(name):
                example, plan, original = plan_of(name)
                verify(example, plan, original, render(example, plan))

    def test_verify_passes_without_original(self) -> None:
        example = load_example("A=1\nB=${A}\n")
        plan = plan_file("m", "m/.env", example, None, None)
        verify(example, plan, None, render(example, plan))

    def fault(self, example, plan, original, rendered: str) -> str:
        with self.assertRaises(CheckError) as ctx:
            verify(example, plan, original, rendered)
        assert_no_sentinel(self, str(ctx.exception))
        return str(ctx.exception)

    def test_check_a_does_not_parse(self) -> None:
        example, plan, original = plan_of("orphan")
        good = render(example, plan)
        message = self.fault(
            example, plan, original, good.replace("A=1\n", "A=1\nA=1\n", 1)
        )
        self.assertIn("does not parse", message)

    def test_check_b_decoded_values(self) -> None:
        example, plan, original = plan_of("orphan")
        message = self.fault(
            example, plan, original, render(example, plan).replace("A=1", "A=2", 1)
        )
        self.assertIn("decoded values", message)

    def test_check_c_kept_line_exactly_once(self) -> None:
        ex = load_example("A=1\nB=2\n")
        env = parse("A=1  # note\nB=2\n", "dotenv")
        plan = plan_file("m", "m/.env", ex, ex, env)
        good = render(ex, plan)
        self.assertIn("A=1  # note", good)
        message = self.fault(ex, plan, env, good.replace("  # note", ""))
        self.assertIn("kept line", message)

    def test_check_d_reference_order(self) -> None:
        ex = load_example("X=1\nY=${X}\n")
        env = parse("X=1\nY=${X}\n", "dotenv")
        plan = plan_file("m", "m/.env", ex, ex, env)
        message = self.fault(ex, plan, env, "Y=${X}\nX=1\n")
        self.assertIn("order", message)

    def test_check_e_rerender_differs(self) -> None:
        example, plan, original = plan_of("orphan")
        message = self.fault(example, plan, original, render(example, plan) + "\n")
        self.assertIn("again gives a different file", message)

    def test_carried_rename_with_reference_is_checked(self) -> None:
        ex = load_example("# renamed: OLD -> NEW\nX=1\nNEW=2\n")
        base = load_example("X=1\nOLD=2\n")
        env = parse("X=1\nOLD=${X}-s\n", "dotenv")
        plan = plan_file("m", "m/.env", ex, base, env)
        self.assertEqual(plan.entries["NEW"].origin, "carried")
        verify(ex, plan, env, render(ex, plan))

    def test_carried_rename_above_its_reference_refused(self) -> None:
        # The example puts NEW above X: the carried line would read ${X}
        # before X is set.
        ex = load_example("# renamed: OLD -> NEW\nNEW=2\nX=1\n")
        base = load_example("X=1\nOLD=2\n")
        env = parse("X=1\nOLD=${X}-s\n", "dotenv")
        plan = plan_file("m", "m/.env", ex, base, env)
        self.assertEqual(plan.entries["NEW"].origin, "carried")
        message = self.fault(ex, plan, env, render(ex, plan))
        self.assertEqual(message, "OLD: order against X changed")

    def test_renamed_line_not_carried_is_checked_where_it_stays(self) -> None:
        # Without a sync record OLD stays at the end, below X, under its own
        # name; dropped as the old default, it is not in the file at all.
        # Neither is NEW's line, which the example puts above X.
        ex = load_example("# renamed: OLD -> NEW\nNEW=2\nX=1\n")
        env = parse("X=1\nOLD=${X}-s\n", "dotenv")
        plan = plan_file("m", "m/.env", ex, None, env)
        self.assertEqual([o.line.key for o in plan.orphans], ["OLD"])
        verify(ex, plan, env, render(ex, plan))
        inherited = "X=1\nOLD=${X}-s\n"
        env = parse(inherited, "dotenv")
        plan = plan_file("m", "m/.env", ex, load_example(inherited), env)
        self.assertEqual(plan.entries["NEW"].origin, "added")
        verify(ex, plan, env, render(ex, plan))

    def test_rerender_exception_is_a_check_error(self) -> None:
        example, plan, original = plan_of("orphan")
        text = render(example, plan)
        with mock.patch.object(
            render_mod, "plan_file", side_effect=RuntimeError("SENTINEL-boom")
        ):
            with self.assertRaises(CheckError) as ctx:
                verify(example, plan, original, text)
        assert_no_sentinel(self, str(ctx.exception))
        self.assertIn("re-planning", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
