from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from envsync_helpers import assert_no_sentinel
from envsync import dotenv
from envsync.dotenv import ParseError, decode, parse, parse_line


def _compose_works() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


class DecodeTests(unittest.TestCase):
    def test_decode_unquoted(self):
        cases = {
            "value": "value",
            "  a b  ": "a b",
            "a #c": "a",
            "a#b": "a#b",
            "a\t#b": "a\t#b",
            "   # text": "# text",
            "": "",
            "abc==": "abc==",
            "http://h.example.invalid/#frag": "http://h.example.invalid/#frag",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(decode(raw, "dotenv"), want)

    def test_decode_quoted(self):
        self.assertEqual(decode("'a #b'", "dotenv"), "a #b")
        self.assertEqual(decode('"a\\"b"', "dotenv"), 'a"b')
        self.assertEqual(decode("'x' # c", "dotenv"), "x")
        self.assertEqual(decode('"a\\nb\\t\\\\"', "dotenv"), "a\nb\t\\")
        for bad in ("'x", "'x' y", '"x\\"'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    decode(bad, "dotenv")

    def test_decode_make(self):
        self.assertEqual(decode("a   # c", "make"), "a   ")
        self.assertEqual(decode("a#b", "make"), "a")
        self.assertEqual(decode("a\\#b", "make"), "a#b")
        self.assertEqual(decode("  v", "make"), "v")


class ParseTests(unittest.TestCase):
    def test_parse_kinds(self):
        raws = [
            "",
            "# doc",
            "A=1",
            "export B=2",
            "C = 3",
            "# D=4",
            "#E=5",
            "#   F=6",
            "# G is prose",
            "   # indented",
        ]
        env = parse("\n".join(raws))
        self.assertEqual(
            [ln.kind for ln in env.lines],
            "blank comment setting setting setting commented commented "
            "comment comment comment".split(),
        )
        self.assertTrue(env.lines[3].exported)
        self.assertFalse(env.lines[2].exported)
        self.assertEqual(env.lines[5].key, "D")
        self.assertEqual(env.lines[5].value, "4")
        self.assertEqual(env.lines[4].value, "3")

    def test_commented_undecodable_value_is_stripped_raw(self):
        line = parse_line("# K='open  ")
        self.assertEqual(line.kind, "commented")
        self.assertEqual(line.value, "'open")

    def test_parse_refuses(self):
        cases = [
            ("KEY: v", 1),
            ("KEY", 1),
            ("=v", 1),
            ("K.EY=v", 1),
            ("A=1\nK='SENTINEL-open", 2),
            ("\ufeffK=v", 1),
            ("K=v\r", 1),
            ("A=1\nK=SENTINEL-1\nK=SENTINEL-2", 3),
        ]
        for text, lineno in cases:
            with self.subTest(text=text):
                with self.assertRaises(ParseError) as cm:
                    parse(text)
                self.assertEqual(cm.exception.lineno, lineno)
                self.assertEqual(
                    str(cm.exception), f"line {lineno}: {cm.exception.reason}"
                )
                assert_no_sentinel(self, str(cm.exception))

    def test_parse_refuses_reasons(self):
        reasons = {
            "KEY: v": "unsupported syntax",
            "K='x": "unterminated quote",
            "K='x' y": "text after closing quote",
            "\ufeffK=v": "byte-order mark",
            "K=v\r": "CRLF line end",
            "K=1\nK=2": "duplicate key (first defined on line 1)",
        }
        for text, reason in reasons.items():
            with self.subTest(text=text):
                with self.assertRaises(ParseError) as cm:
                    parse(text)
                self.assertEqual(cm.exception.reason, reason)

    def test_missing_final_newline_is_fine(self):
        self.assertEqual(parse("A=1").decoded(), {"A": "1"})
        self.assertEqual(parse("A=1\n").decoded(), {"A": "1"})

    def test_parse_make(self):
        self.assertEqual(parse("X = v", "make").lines[0].value, "v")
        for bad in (
            "X += v",
            "X ?= v",
            "X := v",
            "X ::= v",
            "X != v",
            "export X=v",
            "ifeq (a,b)",
            "define X",
            "override X=v",
            " X=v",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ParseError):
                    parse(bad, "make")

    def test_make_line_continuation_refused(self):
        # make would swallow the next line into the value, and the sync may
        # place any line there.
        for text in ("A=1\nX=SENTINEL-v \\\n", "A=1\n# X=SENTINEL-v\\\n"):
            with self.subTest(text=text):
                with self.assertRaises(ParseError) as cm:
                    parse(text, "make")
                self.assertEqual(cm.exception.lineno, 2)
                self.assertEqual(
                    cm.exception.reason,
                    "line continuation (\\) not supported in federation.env",
                )
                assert_no_sentinel(self, str(cm.exception))
        self.assertEqual(parse("X=a\\b\n", "make").decoded(), {"X": "a\\b"})
        self.assertEqual(parse("X=a\\\n").decoded(), {"X": "a\\"})

    def test_settings_and_commented(self):
        env = parse("A=1\n# D=4\nB='x y'\n# D=5\n")
        self.assertEqual(list(env.settings()), ["A", "B"])
        self.assertEqual(env.settings()["A"].lineno, 1)
        self.assertEqual([ln.lineno for ln in env.commented("D")], [2, 4])
        self.assertEqual(env.commented("A"), [])
        self.assertEqual(env.decoded(), {"A": "1", "B": "x y"})


class HelperTests(unittest.TestCase):
    def test_references(self):
        line = parse_line("A=${B}-$C-$$D-${E:-x}")
        self.assertEqual(dotenv.references(line), ["B", "C", "E"])

    def test_references_first_seen_order_deduplicated(self):
        line = parse_line("A=$X-${Y}-$X")
        self.assertEqual(dotenv.references(line), ["X", "Y"])

    def test_rewrite_key(self):
        self.assertEqual(
            dotenv.rewrite_key("export OLD = v  # c", "OLD", "NEW"),
            "export NEW = v  # c",
        )
        self.assertEqual(dotenv.rewrite_key("OLD=OLD", "OLD", "NEW"), "NEW=OLD")

    def test_uncomment(self):
        self.assertEqual(dotenv.uncomment("# A=1"), "A=1")
        self.assertEqual(dotenv.uncomment("#A=1"), "A=1")
        self.assertEqual(dotenv.uncomment("#  A=1"), " A=1")

    def test_lint(self):
        env = parse(
            "A= # note\nB=x\t#y\nC='x\t#y'\nD=ok # fine\n",
        )
        self.assertEqual(
            dotenv.lint(env),
            [
                (
                    1,
                    "empty value followed by a comment: compose reads the "
                    "comment as the value; put the comment on its own line",
                ),
                (
                    2,
                    "a tab before '#' does not start a comment; compose "
                    "keeps it in the value",
                ),
            ],
        )
        make = parse("X=1\nY=a # c\nZ=a\\# c\n", "make")
        self.assertEqual(
            dotenv.lint(make),
            [
                (
                    2,
                    "inline comment in federation.env: make keeps the "
                    "blanks before it in the value",
                )
            ],
        )

    def test_is_secret_like(self):
        for key in (
            "AUDIT_EDGE_SECRET",
            "GRAFANA_ADMIN_PASSWORD",
            "NEXTEXT_SERVICE_TOKENS",
            "HF_TOKEN",
            "QDRANT_API_KEY",
            "KEY",
        ):
            with self.subTest(key=key):
                self.assertTrue(dotenv.is_secret_like(key))
        for key in (
            "EDGE_HOST",
            "AUDIT_RETENTION",
            "MONKEY",
            "KEYBOARD",
            # A count of tokens is no secret.
            "CHAT_MAX_NUM_BATCHED_TOKENS",
            "MAX_TOKENS",
            "TOKEN_LIMIT",
        ):
            with self.subTest(key=key):
                self.assertFalse(dotenv.is_secret_like(key))


FIXTURE = """\
# synthetic fixture
PLAIN=value
SPACED=  padded value  
INLINE=value # trailing comment
HASH=a#b
FRAGMENT=https://h.example.invalid/p#frag
TABHASH=a\t#b
EMPTY=
EMPTYCOMMENT= # note
EQUALS=abc==
SINGLE='a #b'
DOUBLE="say \\"hi\\""
QUOTED_COMMENT='x' # c
"""


@unittest.skipUnless(_compose_works(), "docker compose is not available")
class ParityTests(unittest.TestCase):
    def test_parity_with_compose(self):
        env = parse(FIXTURE)
        keys = list(env.decoded())
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "fixture.env"
            env_file.write_text(FIXTURE, encoding="utf-8")
            compose = Path(tmp) / "compose.yaml"
            lines = ["services:", "  probe:", "    image: busybox", "    environment:"]
            lines += [f"      {k}: ${{{k}}}" for k in keys]
            compose.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "docker", "compose", "--env-file", str(env_file),
                    "-f", str(compose), "config", "--format", "json",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        got = json.loads(result.stdout)["services"]["probe"]["environment"]
        for key, want in env.decoded().items():
            with self.subTest(key=key):
                self.assertEqual(got[key], want)


if __name__ == "__main__":
    unittest.main()
