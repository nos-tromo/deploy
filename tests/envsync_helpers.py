"""Shared test helpers for the env-sync tests."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict, Optional, Union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

Content = Union[str, bytes]


def assert_no_sentinel(tc: unittest.TestCase, *texts: str) -> None:
    """Fail when any text contains an operator-secret sentinel."""
    for text in texts:
        tc.assertNotIn("SENTINEL-", text)


def _write(path: Path, content: Content, mode: Optional[int] = None) -> None:
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    if mode is not None:
        os.chmod(path, mode)


def make_member(
    infra: Path,
    name: str,
    example: Content,
    env: Optional[Content] = None,
    mode: int = 0o600,
) -> Path:
    """infra/<name>/ with its .env.example and, when given, its .env."""
    folder = Path(infra) / name
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / ".env.example", example)
    if env is not None:
        _write(folder / ".env", env, mode)
    return folder


def make_deploy(infra: Path, example: Content, env: Optional[Content] = None) -> Path:
    """infra/deploy/ with its federation.env.example and, when given, its
    federation.env (0600)."""
    folder = Path(infra) / "deploy"
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "federation.env.example", example)
    if env is not None:
        _write(folder / "federation.env", env, 0o600)
    return folder


def run_sync(*args: object, state: Path, table: Path) -> subprocess.CompletedProcess:
    """Run scripts/env_sync.py with an explicit state directory and table."""
    command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts" / "env_sync.py"),
        "--state",
        str(state),
        "--table",
        str(table),
        *(str(arg) for arg in args),
    ]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=60, check=False
    )


def tree_digest(root: Path) -> Dict[str, str]:
    """Relative path -> sha256 of every file under root. Directories and
    symlinks are listed too, so creating an empty directory changes it."""
    root = Path(root)
    found: Dict[str, str] = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            key = str(path.relative_to(root))
            if path.is_symlink():
                found[key] = "symlink:" + os.readlink(path)
            elif path.is_dir():
                found[key] = "dir"
            else:
                found[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found
