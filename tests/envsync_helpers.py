"""Shared test helpers for the env-sync tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def assert_no_sentinel(tc: unittest.TestCase, *texts: str) -> None:
    """Fail when any text contains an operator-secret sentinel."""
    for text in texts:
        tc.assertNotIn("SENTINEL-", text)
