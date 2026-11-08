from __future__ import annotations

import unittest

import envsync_helpers  # noqa: F401  (puts scripts/ on sys.path)
from envsync.dotenv import parse_line
from envsync.example import ExampleError, load_example

ANCHORS = """\
# PROVIDER=ollama
# BASE=http://ollama.example.invalid:11434/v1
# PROVIDER=vllm
# BASE=http://vllm-router:4000/v1
# LANG=de for a German-speaking audience; defaults to "en".
# LANG=de
ACTIVE=1
"""


class AnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ex = load_example(ANCHORS)

    def anchor(self, raw: str):
        line = parse_line(raw)
        return self.ex.anchor_for(line.key, line)

    def test_anchor_active_first(self) -> None:
        self.assertEqual(self.anchor("ACTIVE=2").lineno, 7)

    def test_anchor_same_text(self) -> None:
        self.assertEqual(self.anchor("BASE=http://vllm-router:4000/v1").lineno, 4)

    def test_anchor_same_value(self) -> None:
        self.assertEqual(self.anchor("PROVIDER=vllm  # mine").lineno, 3)

    def test_anchor_skips_prose(self) -> None:
        self.assertEqual(self.anchor("LANG=en").lineno, 6)

    def test_anchor_first_non_prose(self) -> None:
        self.assertEqual(self.anchor("BASE=http://other.example.invalid/v1").lineno, 2)

    def test_anchor_unknown(self) -> None:
        self.assertIsNone(self.anchor("NOPE=1"))

    def test_anchor_first_candidate_when_all_prose(self) -> None:
        ex = load_example("# K=two words\n# K=more words\n")
        line = parse_line("K=x")
        self.assertEqual(ex.anchor_for("K", line).lineno, 1)

    def test_values_and_documented(self) -> None:
        self.assertEqual(self.ex.values("PROVIDER"), {"ollama", "vllm"})
        self.assertEqual(self.ex.documented(), {"PROVIDER", "BASE", "LANG", "ACTIVE"})

    def test_raw_lines_and_candidates(self) -> None:
        self.assertIn("ACTIVE=1", self.ex.raw_lines())
        self.assertEqual([c.lineno for c in self.ex.candidates("BASE")], [2, 4])


class IsProseTests(unittest.TestCase):
    def test_is_prose(self) -> None:
        from envsync.example import is_prose

        self.assertTrue(is_prose("a b"))
        self.assertTrue(is_prose("end."))
        self.assertTrue(is_prose("x:"))
        self.assertFalse(is_prose("http://h:1/v1"))
        self.assertFalse(is_prose(""))


class RenameTests(unittest.TestCase):
    def test_renames_resolve_chain(self) -> None:
        ex = load_example("# renamed: A -> B\n# renamed: B -> C\n# C=1\n")
        self.assertEqual(ex.renames, {"A": "C", "B": "C"})

    def check(self, text: str, message: str) -> None:
        with self.assertRaises(ExampleError) as cm:
            load_example(text)
        self.assertEqual(str(cm.exception), message)

    def test_rename_errors(self) -> None:
        self.check("# renamed: A->B\n", "line 1: malformed renamed directive")
        self.check(
            "# renamed: A -> B\nA=1\nB=2\n", "renamed: A is still a key of the example"
        )
        self.check(
            "# renamed: A -> B\n# renamed: A -> C\nB=1\nC=1\n", "renamed: A appears twice"
        )
        self.check("# renamed: A -> B\n# renamed: B -> A\n", "renamed: cycle through A")
        self.check("# renamed: A -> B\n", "renamed: B is not a key of the example")

    def test_duplicate_active_key_refused(self) -> None:
        with self.assertRaises(ExampleError) as cm:
            load_example("A=1\nA=2\n")
        self.assertIn("duplicate key", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
