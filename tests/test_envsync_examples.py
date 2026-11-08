"""The shipped examples follow the format: deploy's own, and every sibling
member that has already moved to it."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import List

from envsync_helpers import ROOT

from envsync.conventions import check, is_migrated, main, makefile_defaults


def _members() -> List[str]:
    names: List[str] = []
    for raw in (ROOT / "env-sync.conf").read_text(encoding="utf-8").splitlines():
        parts = raw.split()
        if parts and not raw.lstrip().startswith("#") and parts[1] not in names:
            names.append(parts[1])
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for var in ("VLLM_DIR", "DATA_DIR", "OBS_DIR", "EDGE_DIR", "OPENWEBUI_DIR"):
        found = re.search(rf"^{var}\s*\?=\s*(\S+)", makefile, re.M)
        if found and found.group(1) not in names:
            names.append(found.group(1))
    apps = re.search(r"^APP_DIRS\s*\?=\s*(.+)$", makefile, re.M)
    for name in apps.group(1).split() if apps else []:
        if name not in names:
            names.append(name)
    return names


class ExamplesTest(unittest.TestCase):
    def test_deploy_example_follows_the_format(self) -> None:
        text = (ROOT / "federation.env.example").read_text(encoding="utf-8")
        defaults = makefile_defaults((ROOT / "Makefile").read_text(encoding="utf-8"))
        self.assertEqual(check(text, "deploy", "make", make_defaults=defaults), [])

    def test_migrated_siblings_follow_the_format(self) -> None:
        migrated: List[Path] = []
        for member in _members():
            example = ROOT.parent / member / ".env.example"
            if example.is_file() and is_migrated(
                example.read_text(encoding="utf-8"), member
            ):
                migrated.append(example.parent)
        if not migrated:
            self.skipTest("no sibling member has moved to the format yet")
        self.assertEqual(main([str(folder) for folder in migrated]), 0)


if __name__ == "__main__":
    unittest.main()
