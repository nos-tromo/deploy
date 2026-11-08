from __future__ import annotations

import unittest
from typing import Dict, Iterable, List, Optional, Tuple

from envsync_helpers import assert_no_sentinel
from envsync.dotenv import parse
from envsync.example import Example, load_example
from envsync.plan import (
    MARKER,
    Entry,
    Event,
    FilePlan,
    PlanError,
    is_held,
    placeholder_event,
    plan_file,
)
from envsync.render import render

def plan_texts(
    e: str,
    env: Optional[str],
    base: Optional[str] = None,
    shared: Iterable[str] = frozenset(),
    syntax: str = "dotenv",
) -> Tuple[Example, FilePlan]:
    example = load_example(e, syntax)
    base_ex = load_example(base, syntax) if base is not None else None
    env_file = parse(env, syntax) if env is not None else None
    result = plan_file(
        "member", "member/.env", example, base_ex, env_file, frozenset(shared)
    )
    return example, result


def event(plan: FilePlan, kind: str) -> Optional[Event]:
    for found in plan.events:
        if found.kind == kind:
            return found
    return None


def items(plan: FilePlan, kind: str) -> Optional[List[str]]:
    found = event(plan, kind)
    return None if found is None else found.items


class PlanCase(unittest.TestCase):
    def plan(
        self,
        e: str,
        env: Optional[str],
        base: Optional[str] = None,
        shared: Iterable[str] = frozenset(),
        syntax: str = "dotenv",
    ) -> FilePlan:
        """Plan inline texts and check what every plan must satisfy: one event per
        kind, no sentinel in any event item, and a stable re-plan: the rendered
        text, planned again with base = the example, renders the same text."""
        example, result = plan_texts(e, env, base, shared, syntax)
        kinds = [found.kind for found in result.events]
        self.assertEqual(len(kinds), len(set(kinds)))
        for found in result.events:
            assert_no_sentinel(self, *found.items)
        text = render(example, result)
        _, again = plan_texts(e, text, e, shared, syntax)
        self.assertEqual(render(example, again), text, "re-plan of the rendered text")
        return result

    def rendered(self, e: str, plan: FilePlan) -> str:
        return render(load_example(e, plan.syntax), plan)


class CreateAndKeepTests(PlanCase):
    def test_created_from_example(self) -> None:
        p = self.plan("# Doc\nA=1\n# B=2\nC=3\n", None)
        self.assertTrue(p.created)
        self.assertEqual(
            p.entries,
            {
                "A": Entry("A", "A=1", "1", "added"),
                "C": Entry("C", "C=3", "3", "added"),
            },
        )
        self.assertEqual(p.events, [])
        self.assertEqual((p.unset, p.orphans, p.dropped_comments), ({}, [], 0))
        self.assertFalse(p.has_base)
        with_base = self.plan("A=1\n", None, base="A=1\n", shared={"A"})
        self.assertTrue(with_base.has_base)
        self.assertEqual(with_base.shared_keys, frozenset({"A"}))

    def test_kept_byte_for_byte(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=SENTINEL-a  # note\nB=2\n")
        self.assertFalse(p.created)
        self.assertEqual(
            p.entries["A"], Entry("A", "A=SENTINEL-a  # note", "SENTINEL-a", "kept")
        )
        self.assertEqual(p.entries["B"].origin, "kept")
        self.assertEqual(items(p, "differs"), ["A"])

    def test_differs_only_against_active_defaults(self) -> None:
        p = self.plan("# MODE=fast\nA=1\n", "MODE=slow\nA=2\n")
        self.assertEqual(p.entries["MODE"].origin, "kept")
        self.assertEqual(items(p, "differs"), ["A"])
        self.assertIsNone(items(self.plan("A=1\n", "A=1\n", base="A=1\n"), "differs"))

    def test_empty_env_rebuilt_from_example(self) -> None:
        e = "# Doc\nA=1\nX_SECRET=change-me\n"
        for env in ("", "\n \n\t\n"):
            with self.subTest(env=env):
                p = self.plan(e, env, base=e)
                self.assertFalse(p.created)
                self.assertTrue(p.has_base)
                self.assertEqual(
                    p.entries,
                    {
                        "A": Entry("A", "A=1", "1", "added"),
                        "X_SECRET": Entry(
                            "X_SECRET", "X_SECRET=change-me", "change-me", "added"
                        ),
                    },
                )
                self.assertEqual(p.events, [])
                self.assertEqual((p.unset, p.orphans, p.dropped_comments), ({}, [], 0))


class AddAndUnsetTests(PlanCase):
    def test_new_key_added(self) -> None:
        p = self.plan("A=1\nNEW=x\n", "A=1\n", base="A=1\n")
        self.assertEqual(p.entries["NEW"], Entry("NEW", "NEW=x", "x", "added"))
        self.assertEqual(p.entries["A"].origin, "kept")
        self.assertEqual(items(p, "added"), ["NEW=x"])

    def test_added_secret_hides_value(self) -> None:
        p = self.plan("A=1\nX_SECRET=change-me\n", "A=1\n", base="A=1\n")
        self.assertEqual(p.entries["X_SECRET"].origin, "added")
        self.assertEqual(items(p, "added"), ["X_SECRET"])

    def test_first_run_commented_key_stays_unset(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=1\n# B=2\n")
        self.assertEqual(p.unset, {"B": "# B=2"})
        self.assertNotIn("B", p.entries)
        self.assertEqual(items(p, "kept_unset"), ["B"])
        self.assertEqual(p.dropped_comments, 0)

    def test_removed_key_stays_unset(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=1\n", base="A=1\nB=2\n")
        self.assertEqual(p.unset, {"B": "# B=2"})
        self.assertNotIn("B", p.entries)
        self.assertEqual(items(p, "kept_unset"), ["B"])

    def test_promoted_key_added(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=1\n# B=2\n", base="A=1\n# B=2\n")
        self.assertEqual(p.entries["B"], Entry("B", "B=2", "2", "added"))
        self.assertEqual(p.unset, {})
        self.assertEqual(items(p, "added"), ["B=2"])

    DEVICE = "# DEVICE=cuda\nDEVICE=cpu\nA=1\n"

    def test_unset_line_stable_with_base(self) -> None:
        p = self.plan(self.DEVICE, "A=1\n", base="DEVICE=cpu\nA=1\n")
        self.assertEqual(p.unset, {"DEVICE": "# DEVICE=cpu"})
        self.assertEqual(
            self.rendered(self.DEVICE, p), "# DEVICE=cuda\n# DEVICE=cpu\nA=1\n"
        )
        log = "# LOG_LEVEL=debug\nLOG_LEVEL=info\n"
        p = self.plan(log, "# LOG_LEVEL=info\n", base="LOG_LEVEL=info\n")
        self.assertEqual(p.unset, {"LOG_LEVEL": "# LOG_LEVEL=info"})
        self.assertEqual(self.rendered(log, p), "# LOG_LEVEL=debug\n# LOG_LEVEL=info\n")

    def test_unset_line_stable_on_first_run(self) -> None:
        p = self.plan(self.DEVICE, "# DEVICE=cpu\nA=1\n")
        self.assertEqual(p.unset, {"DEVICE": "# DEVICE=cpu"})
        self.assertEqual(items(p, "kept_unset"), ["DEVICE"])
        self.assertEqual(
            self.rendered(self.DEVICE, p), "# DEVICE=cuda\n# DEVICE=cpu\nA=1\n"
        )
        example_line_only = self.plan(self.DEVICE, "# DEVICE=cuda\nA=1\n")
        self.assertEqual(example_line_only.unset, {})
        self.assertEqual(
            example_line_only.entries["DEVICE"],
            Entry("DEVICE", "DEVICE=cpu", "cpu", "added"),
        )


class FollowTests(PlanCase):
    def test_inherited_follows(self) -> None:
        p = self.plan("PORT=8080\n", "PORT=8000\n", base="PORT=8000\n")
        self.assertEqual(
            p.entries["PORT"], Entry("PORT", "PORT=8080", "8080", "followed")
        )
        self.assertEqual(items(p, "followed"), ["PORT: 8000 -> 8080"])

    def test_pinned_line_kept(self) -> None:
        p = self.plan("PORT=8080\n", "PORT=8000  # pinned\n", base="PORT=8000\n")
        self.assertEqual(
            p.entries["PORT"], Entry("PORT", "PORT=8000  # pinned", "8000", "kept")
        )
        self.assertIsNone(items(p, "followed"))
        self.assertEqual(p.events, [])

    def test_trailing_blanks_do_not_count(self) -> None:
        unchanged = self.plan("PORT=8000\n", "PORT=8000  \n", base="PORT=8000\n")
        self.assertEqual(
            unchanged.entries["PORT"], Entry("PORT", "PORT=8000  ", "8000", "kept")
        )
        self.assertEqual(unchanged.events, [])
        moved = self.plan("PORT=8080\n", "PORT=8000  \n", base="PORT=8000\n")
        self.assertEqual(moved.entries["PORT"].origin, "followed")

    def test_inherited_now_commented(self) -> None:
        p = self.plan("# MODE=fast\n", "MODE=fast\n", base="MODE=fast\n")
        self.assertNotIn("MODE", p.entries)
        self.assertEqual(p.unset, {})
        self.assertEqual(p.orphans, [])
        self.assertEqual(items(p, "unset_now"), ["MODE"])

    def test_inherited_dropped_removed(self) -> None:
        p = self.plan("A=1\n", "OLD=1\nA=1\n", base="OLD=1\nA=1\n")
        self.assertNotIn("OLD", p.entries)
        self.assertEqual(p.orphans, [])
        self.assertEqual(items(p, "removed"), ["OLD"])


class HeldTests(PlanCase):
    def test_is_held(self) -> None:
        none = frozenset()
        self.assertTrue(is_held("X_TOKEN", "dotenv", none))
        self.assertTrue(is_held("QDRANT_API_KEY", "dotenv", none))
        self.assertTrue(is_held("LOG_RETENTION_DAYS", "dotenv", none))
        self.assertTrue(is_held("EDGE_HOST", "dotenv", frozenset({"EDGE_HOST"})))
        self.assertTrue(is_held("APP_DIRS", "make", none))
        self.assertFalse(is_held("PORT", "dotenv", none))
        self.assertFalse(is_held("KEYBOARD", "dotenv", none))

    def test_held_never_follow(self) -> None:
        base = "AUDIT_RETENTION=30d\nGRAFANA_ADMIN_PASSWORD=change-me\nSHARED=x\n"
        e = "AUDIT_RETENTION=7d\nGRAFANA_ADMIN_PASSWORD=changeme-new\nSHARED=y\n"
        p = self.plan(e, base, base=base, shared={"SHARED"})
        for line in base.splitlines():
            key = line.split("=", 1)[0]
            self.assertEqual(p.entries[key].raw, line)
            self.assertEqual(p.entries[key].origin, "kept")
        self.assertEqual(
            event(p, "held"),
            Event(
                "held",
                [
                    "AUDIT_RETENTION: 30d -> 7d",
                    "GRAFANA_ADMIN_PASSWORD",
                    "SHARED: x -> y",
                ],
                "warn",
            ),
        )
        self.assertIsNone(items(p, "followed"))

    def test_held_unchanged_default_not_reported(self) -> None:
        p = self.plan(
            "AUDIT_RETENTION=30d\n",
            "AUDIT_RETENTION=30d\n",
            base="AUDIT_RETENTION=30d\n",
        )
        self.assertEqual(p.entries["AUDIT_RETENTION"].origin, "kept")
        self.assertEqual(p.events, [])

    def test_held_now_commented_reported_by_name(self) -> None:
        p = self.plan(
            "# AUDIT_RETENTION=30d\n",
            "AUDIT_RETENTION=30d\n",
            base="AUDIT_RETENTION=30d\n",
        )
        self.assertEqual(p.entries["AUDIT_RETENTION"].origin, "kept")
        self.assertEqual(items(p, "held"), ["AUDIT_RETENTION"])
        self.assertIsNone(items(p, "unset_now"))

    def test_held_dropped_becomes_orphan(self) -> None:
        p = self.plan("A=1\n", "AUDIT_RETENTION=30d\n", base="AUDIT_RETENTION=30d\n")
        self.assertEqual([o.line.key for o in p.orphans], ["AUDIT_RETENTION"])
        self.assertIsNone(p.orphans[0].note)
        self.assertNotIn("AUDIT_RETENTION", p.entries)
        self.assertEqual(items(p, "unknown"), ["AUDIT_RETENTION"])
        self.assertIsNone(items(p, "removed"))

    def test_make_syntax_never_follows(self) -> None:
        p = self.plan(
            "APP_DIRS=a b\nNEW_DIR=x\n",
            "APP_DIRS=a b c\n",
            base="APP_DIRS=a b c\n",
            syntax="make",
        )
        self.assertEqual(p.syntax, "make")
        self.assertEqual(
            p.entries["APP_DIRS"], Entry("APP_DIRS", "APP_DIRS=a b c", "a b c", "kept")
        )
        self.assertEqual(items(p, "held"), ["APP_DIRS: a b c -> a b"])
        self.assertIsNone(items(p, "followed"))
        self.assertEqual(
            p.entries["NEW_DIR"], Entry("NEW_DIR", "NEW_DIR=x", "x", "added")
        )
        self.assertEqual(items(p, "added"), ["NEW_DIR=x"])


class OrphanTests(PlanCase):
    def test_orphan_with_comments(self) -> None:
        p = self.plan("A=1\n", "A=1\n# lab proxy\nHTTP_PROXY=SENTINEL-p\n")
        self.assertEqual(len(p.orphans), 1)
        orphan = p.orphans[0]
        self.assertEqual(orphan.line.raw, "HTTP_PROXY=SENTINEL-p")
        self.assertEqual(orphan.comments, ["# lab proxy"])
        self.assertIsNone(orphan.note)
        self.assertNotIn("HTTP_PROXY", p.entries)
        self.assertEqual(items(p, "unknown"), ["HTTP_PROXY"])
        self.assertEqual(p.dropped_comments, 0)

    def test_orphan_comments_skip_marker_and_example_lines(self) -> None:
        env = (
            "A=1\n"
            "# own note\n"
            "# [env-sync] Not in .env.example: kept as they were. Delete what is obsolete.\n"
            "# Example doc\n"
            "HTTP_PROXY=SENTINEL-p\n"
        )
        p = self.plan("# Example doc\nA=1\n", env)
        self.assertEqual(p.orphans[0].comments, ["# own note"])
        self.assertEqual(p.dropped_comments, 0)
        self.assertIsNone(items(p, "dropped_comments"))

    def test_orphan_comments_stop_at_blank_and_setting(self) -> None:
        env = "# far away\n\n# near\nX=SENTINEL-x\n# KEY_ABOUT_Y=1\nY=SENTINEL-y\n"
        p = self.plan("A=1\n", env)
        self.assertEqual(
            [(o.line.key, o.comments) for o in p.orphans],
            [("X", ["# near"]), ("Y", ["# KEY_ABOUT_Y=1"])],
        )
        self.assertEqual(p.dropped_comments, 1)

    def test_base_comment_lines_not_dropped(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=1\n# B=2\n", base="A=1\n# B=2\n")
        self.assertEqual(p.entries["B"], Entry("B", "B=2", "2", "added"))
        self.assertEqual(p.dropped_comments, 0)
        self.assertIsNone(items(p, "dropped_comments"))
        own = self.plan("A=1\nB=2\n", "A=1\n# B=2\n# mine\n", base="A=1\n# B=2\n")
        self.assertEqual(own.dropped_comments, 1)

    def test_dropped_comment_count(self) -> None:
        p = self.plan("A=1\nB=2\n", "# mine about A\nA=1\n# mine about B\nB=2\n")
        self.assertEqual(p.dropped_comments, 2)
        self.assertEqual(
            items(p, "dropped_comments"),
            ["2 comment lines not in the example dropped (see the backup)"],
        )


class RenameTests(PlanCase):
    E = "# renamed: OLD -> NEW\nNEW=d\n"

    def test_rename_carried(self) -> None:
        p = self.plan(self.E, "OLD=SENTINEL-x  # mine\n", base="OLD=d\n")
        self.assertEqual(
            p.entries["NEW"],
            Entry("NEW", "NEW=SENTINEL-x  # mine", "SENTINEL-x", "carried"),
        )
        self.assertNotIn("OLD", p.entries)
        self.assertEqual(p.orphans, [])
        self.assertEqual(items(p, "renamed"), ["OLD -> NEW (value carried)"])

    def test_rename_inherited_old_dropped(self) -> None:
        p = self.plan(self.E, "OLD=d\n", base="OLD=d\n")
        self.assertEqual(p.entries["NEW"], Entry("NEW", "NEW=d", "d", "added"))
        self.assertEqual(p.orphans, [])
        self.assertEqual(items(p, "renamed"), ["OLD -> NEW (old default dropped)"])

    def test_rename_without_base_not_carried(self) -> None:
        p = self.plan(self.E, "OLD=SENTINEL-x\n")
        self.assertEqual(
            [(o.line.key, o.note) for o in p.orphans],
            [("OLD", "renamed to NEW in the example; value not carried")],
        )
        self.assertEqual(p.entries["NEW"], Entry("NEW", "NEW=d", "d", "added"))
        self.assertEqual(
            event(p, "rename_not_carried"),
            Event("rename_not_carried", ["OLD -> NEW"], "warn"),
        )

    def test_rename_both_set(self) -> None:
        p = self.plan(
            "# renamed: OLD -> NEW\n# NEW=e\n",
            "OLD=SENTINEL-1\nNEW=SENTINEL-2\n",
            base="OLD=d\n# NEW=e\n",
        )
        self.assertEqual(
            p.entries["NEW"], Entry("NEW", "NEW=SENTINEL-2", "SENTINEL-2", "kept")
        )
        self.assertEqual(
            [(o.line.key, o.note) for o in p.orphans],
            [("OLD", "renamed to NEW in the example; value not carried")],
        )
        self.assertEqual(
            event(p, "rename_conflict"),
            Event("rename_conflict", ["OLD -> NEW"], "warn"),
        )

    def test_rename_note_stable_on_replan(self) -> None:
        cases = (
            (
                "# renamed: OLD -> NEW\nNEW=e\n",
                "OLD=d\n",
                "OLD=SENTINEL-1\nNEW=e\n",
                "OLD",
                "NEW",
                ["rename_conflict", "rename_not_carried"],
            ),
            (
                "# renamed: OLD_RETENTION -> NEW_RETENTION\nNEW_RETENTION=7d\n",
                "NEW_RETENTION=30d\n",
                "OLD_RETENTION=SENTINEL-1\nNEW_RETENTION=30d\n",
                "OLD_RETENTION",
                "NEW_RETENTION",
                ["rename_not_carried", "rename_conflict"],
            ),
        )
        for e, base, env, old, new, kinds in cases:
            note = f"renamed to {new} in the example; value not carried"
            with self.subTest(old=old):
                plans = [self.plan(e, env, base=base), self.plan(e, env, base=e)]
                for p, kind in zip(plans, kinds):
                    self.assertEqual(
                        [(o.line.key, o.note) for o in p.orphans], [(old, note)]
                    )
                    self.assertEqual(items(p, kind), [f"{old} -> {new}"])

    def test_held_inherited_old_is_carried(self) -> None:
        for old, new in (
            ("OLD_RETENTION", "NEW_RETENTION"),
            ("LOG_RETENTION", "LOG_DAYS"),
            ("LOG_DAYS", "LOG_RETENTION"),
        ):
            with self.subTest(old=old, new=new):
                p = self.plan(
                    f"# renamed: {old} -> {new}\n{new}=7d\n",
                    f"{old}=30d\n",
                    base=f"{old}=30d\n",
                )
                self.assertEqual(
                    p.entries[new], Entry(new, f"{new}=30d", "30d", "carried")
                )
                self.assertEqual(p.orphans, [])
                self.assertEqual(
                    items(p, "renamed"), [f"{old} -> {new} (value carried)"]
                )

    def test_held_inherited_old_yields_to_set_new(self) -> None:
        both = "OLD_RETENTION=7d\nNEW_RETENTION=30d\n"
        p = self.plan(
            "# renamed: OLD_RETENTION -> NEW_RETENTION\nNEW_RETENTION=30d\n",
            both,
            base=both,
        )
        self.assertEqual(
            p.entries["NEW_RETENTION"],
            Entry("NEW_RETENTION", "NEW_RETENTION=30d", "30d", "kept"),
        )
        self.assertEqual(
            [(o.line.key, o.note) for o in p.orphans],
            [
                (
                    "OLD_RETENTION",
                    "renamed to NEW_RETENTION in the example; value not carried",
                )
            ],
        )
        self.assertEqual(
            event(p, "rename_conflict"),
            Event("rename_conflict", ["OLD_RETENTION -> NEW_RETENTION"], "warn"),
        )
        dirs = "OLD_DIRS=a b c\nAPP_DIRS=a b\n"
        p = self.plan(
            "# renamed: OLD_DIRS -> APP_DIRS\nAPP_DIRS=a b\n",
            dirs,
            base=dirs,
            syntax="make",
        )
        self.assertEqual(
            p.entries["APP_DIRS"], Entry("APP_DIRS", "APP_DIRS=a b", "a b", "kept")
        )
        self.assertEqual([o.line.key for o in p.orphans], ["OLD_DIRS"])
        self.assertEqual(items(p, "rename_conflict"), ["OLD_DIRS -> APP_DIRS"])

    def test_removed_old_keeps_new_unset(self) -> None:
        e = "# renamed: OLD -> NEW\nNEW=on\nA=1\n"
        for env in ("A=1\n", "# OLD=on\nA=1\n"):
            with self.subTest(env=env):
                p = self.plan(e, env, base="OLD=on\nA=1\n")
                self.assertEqual(p.unset, {"NEW": "# NEW=on"})
                self.assertNotIn("NEW", p.entries)
                self.assertEqual(items(p, "kept_unset"), ["NEW"])
                self.assertIsNone(items(p, "added"))
                self.assertEqual(
                    self.rendered(e, p), "# renamed: OLD -> NEW\n# NEW=on\nA=1\n"
                )
        never_active = self.plan(e, "A=1\n", base="# OLD=on\nA=1\n")
        self.assertEqual(never_active.entries["NEW"].origin, "added")

    def test_old_replaces_inherited_new(self) -> None:
        p = self.plan(
            "# renamed: OLD -> NEW\nNEW=e\n",
            "OLD=SENTINEL-1\nNEW=e\n",
            base="OLD=d\nNEW=e\n",
        )
        self.assertEqual(
            p.entries["NEW"], Entry("NEW", "NEW=SENTINEL-1", "SENTINEL-1", "carried")
        )
        self.assertEqual(p.orphans, [])

    def test_two_olds_conflict(self) -> None:
        p = self.plan(
            "# renamed: A -> C\n# renamed: B -> C\nC=1\n",
            "A=SENTINEL-1\nB=SENTINEL-2\nC=1\n",
            base="A=1\nB=1\nC=1\n",
        )
        note = "renamed to C together with another key; value not carried"
        self.assertEqual(
            [(o.line.key, o.note) for o in p.orphans], [("A", note), ("B", note)]
        )
        self.assertEqual(p.entries["C"], Entry("C", "C=1", "1", "kept"))
        self.assertEqual(
            event(p, "rename_conflict"),
            Event("rename_conflict", ["A -> C", "B -> C"], "warn"),
        )

    def test_rename_chain(self) -> None:
        p = self.plan(
            "# renamed: A -> B\n# renamed: B -> C\nC=1\n",
            "A=SENTINEL-1\n",
            base="A=1\n",
        )
        self.assertEqual(
            p.entries["C"], Entry("C", "C=SENTINEL-1", "SENTINEL-1", "carried")
        )
        self.assertEqual(items(p, "renamed"), ["A -> C (value carried)"])

    def test_reference_to_renamed_refused(self) -> None:
        e = "# renamed: OLD -> NEW\nNEW=1\nX=2\n"
        base = "OLD=1\nX=2\n"
        env = "OLD=SENTINEL-o\nX=${OLD}/v1\n"
        with self.assertRaises(PlanError) as cm:
            self.plan(e, env, base=base)
        self.assertEqual(str(cm.exception), "line 2: refers to a renamed key")
        assert_no_sentinel(self, str(cm.exception))
        self.plan(e, "OLD=${OLD}/v1\n", base=base)
        self.plan(e, env, base=base, syntax="make")


class EventOrderTests(PlanCase):
    def test_event_order_and_levels(self) -> None:
        e = (
            "# renamed: OLD -> NEW\n"
            "# renamed: STALE -> NEW4\n"
            "# renamed: LEGACY -> NEW2\n"
            "# renamed: OLDB -> NEW3\n"
            "PORT=8080\n"
            "# MODE=fast\n"
            "AUDIT_RETENTION=7d\n"
            "NEW=d\n"
            "NEW2=d2\n"
            "NEW3=d3\n"
            "NEW4=d4\n"
            "KEEP=1\n"
            "ADDED=x\n"
            "DROPPED=y\n"
        )
        base = (
            "PORT=8000\n"
            "MODE=fast\n"
            "GONE=1\n"
            "AUDIT_RETENTION=30d\n"
            "STALE=s\n"
            "OLD=d\n"
            "OLDB=b\n"
            "NEW3=d3\n"
            "KEEP=1\n"
            "DROPPED=y\n"
        )
        env = (
            "# own comment\n"
            "PORT=8000\n"
            "EXTRA=SENTINEL-e\n"
            "MODE=fast\n"
            "GONE=1\n"
            "AUDIT_RETENTION=30d\n"
            "STALE=s\n"
            "OLD=SENTINEL-o\n"
            "LEGACY=SENTINEL-l\n"
            "OLDB=SENTINEL-b\n"
            "NEW3=SENTINEL-n\n"
            "KEEP=1\n"
            "BAD=   # trap\n"
        )
        p = self.plan(e, env, base=base)
        self.assertEqual(
            [(found.kind, found.level) for found in p.events],
            [
                ("added", "info"),
                ("followed", "info"),
                ("unset_now", "info"),
                ("removed", "info"),
                ("held", "warn"),
                ("renamed", "info"),
                ("rename_not_carried", "warn"),
                ("rename_conflict", "warn"),
                ("unknown", "warn"),
                ("kept_unset", "info"),
                ("dropped_comments", "info"),
                ("lint", "warn"),
            ],
        )
        self.assertEqual(items(p, "added"), ["NEW2=d2", "NEW4=d4", "ADDED=x"])
        self.assertEqual(
            items(p, "renamed"),
            ["STALE -> NEW4 (old default dropped)", "OLD -> NEW (value carried)"],
        )
        self.assertEqual(items(p, "unknown"), ["EXTRA", "BAD"])
        self.assertEqual(
            [o.line.key for o in p.orphans], ["EXTRA", "LEGACY", "OLDB", "BAD"]
        )
        self.assertEqual(p.unset, {"DROPPED": "# DROPPED=y"})

    def test_lint_event(self) -> None:
        p = self.plan("A=1\nB=2\n", "A=1\nB=   # SENTINEL-c\n")
        lint_event = event(p, "lint")
        self.assertEqual(lint_event.level, "warn")
        self.assertEqual(len(lint_event.items), 1)
        self.assertTrue(
            lint_event.items[0].startswith("line 2: empty value followed by a comment")
        )

    def test_reference_now_reads_an_added_line(self) -> None:
        def note(lineno: int, key: str) -> str:
            return f"line {lineno}: now reads ${{{key}}} from a line the sync added"

        trap = (
            "line 3: empty value followed by a comment: compose reads the "
            "comment as the value; put the comment on its own line"
        )
        e = (
            "OPENAI_API_KEY=sk-no-key-required\n"
            "MODEL=m\n"
            "# NER_API_KEY=$OPENAI_API_KEY\n"
            "TAIL=1\n"
        )
        base = "MODEL=m\n# NER_API_KEY=$OPENAI_API_KEY\nTAIL=1\n"
        cases = {
            # docint-style: the operator's NER_API_KEY reads a key the file
            # did not set, which the example now adds above it.
            "kept line": (
                e,
                base,
                "MODEL=m\nNER_API_KEY=$OPENAI_API_KEY\nTAIL=   # SENTINEL-c\n",
                [note(2, "OPENAI_API_KEY"), trap],
            ),
            "line kept at the end": (
                e,
                base,
                "MODEL=m\nTAIL=1\nMY_KEY=${OPENAI_API_KEY}/v1\n",
                [note(3, "OPENAI_API_KEY")],
            ),
            "carried line": (
                "# renamed: OLD -> NEW\nX=1\nNEW=2\n",
                "OLD=2\n",
                "OLD=${X}-s\n",
                [note(1, "X")],
            ),
            "set by the operator": (
                e,
                base,
                "OPENAI_API_KEY=SENTINEL-k\nMODEL=m\nNER_API_KEY=$OPENAI_API_KEY\n",
                None,
            ),
            "added below the reference": (
                "TAIL=1\nLATE=x\n",
                "TAIL=1\n",
                "TAIL=${LATE}\n",
                None,
            ),
        }
        for name, (example, synced, env, found) in cases.items():
            with self.subTest(name):
                p = self.plan(example, env, base=synced)
                self.assertEqual(items(p, "lint"), found)
        # make reads a reference when it is used, wherever the line stands.
        p = self.plan("B=1\nA=x\n", "A=${B}\n", base="A=x\n", syntax="make")
        self.assertEqual(p.entries["B"].origin, "added")
        self.assertIsNone(items(p, "lint"))


class EffectiveAndPlaceholderTests(PlanCase):
    def test_effective(self) -> None:
        p = self.plan(
            'A=1\nB="two words"\nC=3\n',
            'A=\'SENTINEL-a\'  # mine\nB="two words"\nZ="SENTINEL-z"\n',
        )
        self.assertEqual(
            p.effective(),
            {"A": "SENTINEL-a", "B": "two words", "C": "3", "Z": "SENTINEL-z"},
        )

    def placeholders(
        self,
        e: str,
        env: str,
        base: Optional[str] = None,
        shared: Iterable[str] = frozenset(),
    ) -> Optional[List[str]]:
        example = load_example(e)
        base_ex = load_example(base) if base is not None else None
        p = self.plan(e, env, base=base, shared=shared)
        found = placeholder_event(p, example, base_ex)
        if found is None:
            return None
        self.assertEqual((found.kind, found.level), ("placeholder", "warn"))
        assert_no_sentinel(self, *found.items)
        return found.items

    def test_placeholder_event(self) -> None:
        e = "AUTH_SECRET=insecure-dev-1\n"
        self.assertEqual(self.placeholders(e, e), ["AUTH_SECRET"])
        self.assertIsNone(self.placeholders("AUTH_SECRET=\n", "AUTH_SECRET=\n"))

    def test_placeholder_sources(self) -> None:
        self.assertEqual(
            self.placeholders(
                "AUTH_SECRET=new-default\n",
                "AUTH_SECRET=old-default\n",
                base="AUTH_SECRET=old-default\n",
            ),
            ["AUTH_SECRET"],
        )
        self.assertEqual(
            self.placeholders("# AUTH_SECRET=change-me\n", "AUTH_SECRET=change-me\n"),
            ["AUTH_SECRET"],
        )
        self.assertEqual(
            self.placeholders("AUTH_SECRET=\n", "AUTH_SECRET=<generate one>\n"),
            ["AUTH_SECRET"],
        )
        e = "AUTH_SECRET=insecure-dev-1\n"
        self.assertIsNone(self.placeholders(e, e, shared={"AUTH_SECRET"}))
        self.assertIsNone(self.placeholders(e, "AUTH_SECRET=SENTINEL-real\n"))
        self.assertIsNone(self.placeholders("PORT=8080\n", "PORT=8080\n"))

    def test_placeholder_carried_through_rename(self) -> None:
        self.assertEqual(
            self.placeholders(
                "# renamed: OLD_TOKEN -> NEW_TOKEN\nNEW_TOKEN=replace-me\n",
                "OLD_TOKEN=change-me\n",
                base="OLD_TOKEN=change-me\n",
            ),
            ["NEW_TOKEN"],
        )


if __name__ == "__main__":
    unittest.main()
