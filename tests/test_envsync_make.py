"""The Makefile wiring of the env sync: targets, the up gate, defaults, table."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from envsync_helpers import ROOT

from envsync import example

MAKEFILE = ROOT / "Makefile"


def _table_rows():
    rows = []
    for raw in (ROOT / "env-sync.conf").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            parts = line.split()
            rows.append((parts[1], parts[2]))
    return rows


class MakeTargetsTest(unittest.TestCase):
    def test_dry_run_executes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deploy = Path(tmp) / "deploy"
            deploy.mkdir()
            shutil.copy(MAKEFILE, deploy / "Makefile")
            shutil.copytree(
                ROOT / "scripts",
                deploy / "scripts",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for target in ("env", "env-check"):
                done = subprocess.run(
                    ["make", "-n", target],
                    cwd=deploy,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("env_sync.py", done.stdout)
            self.assertFalse((deploy / ".env-sync").exists())

    def test_up_runs_env_check_first(self) -> None:
        done = subprocess.run(
            ["make", "-pq", "help"], cwd=ROOT, capture_output=True, text=True
        )
        lines = done.stdout.splitlines()
        self.assertIn("up: env-check setup", lines)
        self.assertIn("up-dev: env-check setup", lines)

    def test_table_keys_in_member_examples(self) -> None:
        rows = _table_rows()
        members = {member for member, _ in rows}
        missing = [
            m for m in members if not (ROOT.parent / m / ".env.example").is_file()
        ]
        if missing:
            self.skipTest("sibling members not present: " + ", ".join(sorted(missing)))
        documented = {}
        for member in members:
            text = (ROOT.parent / member / ".env.example").read_text(encoding="utf-8")
            documented[member] = example.load_example(text).documented()
        for member, key in rows:
            self.assertIn(key, documented[member], f"{member} {key}")


if __name__ == "__main__":
    unittest.main()
