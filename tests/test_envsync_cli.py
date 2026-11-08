"""End to end: the real scripts/env_sync.py runs as a subprocess against a
temporary infra/ with two synthetic members and a two-group table. One test
runs main() in-process, to make the state directory fail on cue."""

from __future__ import annotations

import errno
import fcntl
import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional, Tuple
from unittest import mock

from envsync_helpers import (
    Content,
    assert_no_sentinel,
    make_deploy,
    make_member,
    run_sync,
    tree_digest,
)
from envsync.cli import main
from envsync.plan import _KINDS, Event
from envsync.report import LABEL, event_line
from envsync.shared import _FILE_KINDS
from envsync.state import StateDir

HOST_ROWS = (
    "edge-host   edge-plane  EDGE_HOST          source\n"
    "edge-host   obs-plane   EDGE_HOST\n"
)
TABLE = HOST_ROWS + (
    "audit-edge  edge-plane  AUDIT_EDGE_SECRET  gen\n"
    "audit-edge  obs-plane   AUDIT_EDGE_SECRET  gen\n"
)

EDGE_EXAMPLE = (
    "# Edge gateway (synthetic test member).\n"
    "EDGE_HOST=edge.example.invalid\n"
    "\n"
    "# Shared with obs-plane.\n"
    "AUDIT_EDGE_SECRET=change-me\n"
    "SESSION_TTL=1h\n"
)
EDGE_ENV = (
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "AUDIT_EDGE_SECRET=SENTINEL-audit\n"
    "SESSION_TTL=1h\n"
)
EDGE_SYNCED = (
    "# Edge gateway (synthetic test member).\n"
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "\n"
    "# Shared with obs-plane.\n"
    "AUDIT_EDGE_SECRET=SENTINEL-audit\n"
    "SESSION_TTL=1h\n"
)
EDGE_ENV_UNSET = (
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "AUDIT_EDGE_SECRET=change-me\n"
    "SESSION_TTL=1h\n"
)

OBS_EXAMPLE = (
    "# Observability (synthetic test member).\n"
    "EDGE_HOST=edge.example.invalid\n"
    "AUDIT_EDGE_SECRET=change-me\n"
    "GRAFANA_ADMIN_PASSWORD=change-me\n"
    "SCRAPE_INTERVAL=15s\n"
)
OBS_ENV = (
    "EDGE_HOST=edge.example.invalid\n"
    "GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\n"
    "SCRAPE_INTERVAL=15s\n"
)
OBS_SYNCED = (
    "# Observability (synthetic test member).\n"
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "AUDIT_EDGE_SECRET=SENTINEL-audit\n"
    "GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\n"
    "SCRAPE_INTERVAL=15s\n"
)
OBS_ENV_UNSET = (
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "AUDIT_EDGE_SECRET=change-me\n"
    "GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\n"
    "SCRAPE_INTERVAL=15s\n"
)

# The upgrade: obs-plane's old example, and the new one with a new key, a
# rename, a changed default, a dropped key and the shared audit secret.
OBS_EXAMPLE_OLD = (
    "# Observability (synthetic test member).\n"
    "EDGE_HOST=edge.example.invalid\n"
    "GRAFANA_ADMIN_PASSWORD=change-me\n"
    "SCRAPE_INTERVAL=15s\n"
    "QUERY_TIMEOUT=30s\n"
    "LEGACY_EXPORTER=on\n"
)
OBS_ENV_OLD = (
    "EDGE_HOST=edge.example.invalid\n"
    "GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\n"
    "SCRAPE_INTERVAL=15s\n"
    "QUERY_TIMEOUT=SENTINEL-45s\n"
    "LEGACY_EXPORTER=on\n"
)
OBS_EXAMPLE_NEW = (
    "# Observability (synthetic test member).\n"
    "# renamed: QUERY_TIMEOUT -> PROBE_TIMEOUT\n"
    "EDGE_HOST=edge.example.invalid\n"
    "AUDIT_EDGE_SECRET=change-me\n"
    "GRAFANA_ADMIN_PASSWORD=change-me\n"
    "SCRAPE_INTERVAL=30s\n"
    "PROBE_TIMEOUT=30s\n"
    "AUDIT_VERIFY_INTERVAL=300\n"
)
OBS_UPGRADED = (
    "# Observability (synthetic test member).\n"
    "# renamed: QUERY_TIMEOUT -> PROBE_TIMEOUT\n"
    "EDGE_HOST=SENTINEL-gw.example.invalid\n"
    "AUDIT_EDGE_SECRET=SENTINEL-audit\n"
    "GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\n"
    "SCRAPE_INTERVAL=30s\n"
    "PROBE_TIMEOUT=SENTINEL-45s\n"
    "AUDIT_VERIFY_INTERVAL=300\n"
)

FED_EXAMPLE_OLD = (
    "# Host profile (synthetic test fixture).\n"
    "INFRA_ROOT=..\n"
    "APP_DIRS=alpha beta\n"
    "DATA_PROFILE=cpu\n"
)
FED_ENV = "INFRA_ROOT=..\nAPP_DIRS=alpha # this host\n"
FED_SYNCED = (
    "# Host profile (synthetic test fixture).\n"
    "INFRA_ROOT=..\n"
    "APP_DIRS=alpha # this host\n"
    "DATA_PROFILE=cpu\n"
)
FED_EXAMPLE_NEW = (
    "# Host profile (synthetic test fixture).\n"
    "INFRA_ROOT=../members\n"
    "APP_DIRS=alpha beta gamma\n"
    "DATA_PROFILE=cpu\n"
    "WAIT_TIMEOUT=180\n"
)
FED_UPGRADED = FED_SYNCED + "WAIT_TIMEOUT=180\n"

SUMMARY_WRITTEN = (
    "summary: 2 written, 0 unchanged, 0 refused, 0 conflict(s). "
    "Changes apply with the next 'make up'.\n"
)
UNSET_AUDIT = (
    "   unset      AUDIT_EDGE_SECRET (edge-plane, obs-plane) "
    "(unset in every member; ENV_GENERATE=1 generates one)\n"
)


def header(display: str, status: str) -> str:
    return f"== {display:<24} {status}"


def not_saved(code: int) -> str:
    return (
        f"   state      stored copy not saved ({os.strerror(code)}); "
        "fix the state directory, then run 'make env' again\n"
    )


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def value(path: Path, key: str) -> str:
    for line in read(path).splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} is not set")


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.infra = self.tmp / "infra"
        self.state = self.tmp / "state"
        self.table = self.tmp / "env-sync.conf"
        self.table.write_text(TABLE, encoding="utf-8")

    def sync(self, *args: object) -> subprocess.CompletedProcess:
        """Every run goes through here: no operator value in the output."""
        proc = run_sync(*args, state=self.state, table=self.table)
        assert_no_sentinel(self, proc.stdout, proc.stderr)
        return proc

    def members(
        self,
        edge_env: Optional[Content] = EDGE_ENV,
        obs_env: Optional[Content] = OBS_ENV,
        obs_example: Content = OBS_EXAMPLE,
    ) -> Tuple[Path, Path]:
        return (
            make_member(self.infra, "edge-plane", EDGE_EXAMPLE, edge_env),
            make_member(self.infra, "obs-plane", obs_example, obs_env),
        )

    def assertExit(self, proc: subprocess.CompletedProcess, code: int) -> None:
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)

    def placeholder_history(self, first_table: str) -> Tuple[Path, Path]:
        """Run 1 against obs-plane's placeholder `change-me` (edge-plane
        leaves the key empty); then the example changes it to `change-me-2`
        and the table is the full one."""
        self.table.write_text(first_table, encoding="utf-8")
        edge = make_member(
            self.infra,
            "edge-plane",
            "EDGE_HOST=edge.example.invalid\nAUDIT_EDGE_SECRET=\n",
            "EDGE_HOST=SENTINEL-gw.example.invalid\nAUDIT_EDGE_SECRET=\n",
        )
        obs = make_member(
            self.infra,
            "obs-plane",
            "EDGE_HOST=edge.example.invalid\nAUDIT_EDGE_SECRET=change-me\n",
            "EDGE_HOST=SENTINEL-gw.example.invalid\nAUDIT_EDGE_SECRET=change-me\n",
        )
        self.assertExit(self.sync(edge, obs), 0)
        self.table.write_text(TABLE, encoding="utf-8")
        make_member(
            self.infra,
            "obs-plane",
            "EDGE_HOST=edge.example.invalid\nAUDIT_EDGE_SECRET=change-me-2\n",
        )
        return edge, obs


class WriteTests(CliCase):
    def test_first_run_writes_and_records_base(self) -> None:
        edge, obs = self.members()
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
        self.assertEqual(read(obs / ".env"), OBS_SYNCED)
        for name, example, original in (
            ("edge-plane", EDGE_EXAMPLE, EDGE_ENV),
            ("obs-plane", OBS_EXAMPLE, OBS_ENV),
        ):
            with self.subTest(name):
                base = self.state / "base" / f"{name}.example"
                self.assertEqual(read(base), example)
                backups = list((self.state / "backup" / name).iterdir())
                self.assertEqual([read(path) for path in backups], [original])
                self.assertIn(
                    header(f"{name}/.env", "written")
                    + f" (backup: state/backup/{name}/{backups[0].name})\n",
                    proc.stdout,
                )
        self.assertIn(
            "   filled     EDGE_HOST (from edge-plane), "
            "AUDIT_EDGE_SECRET (from edge-plane)\n",
            proc.stdout,
        )
        self.assertTrue(proc.stdout.endswith(SUMMARY_WRITTEN), proc.stdout)
        self.assertEqual(os.listdir(self.state / "tmp"), [])
        for folder, _, files in os.walk(self.state):
            self.assertEqual(mode(Path(folder)), 0o700, folder)
            for name in files:
                self.assertEqual(mode(Path(folder) / name), 0o600, name)

    def test_second_run_unchanged(self) -> None:
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        before = tree_digest(self.tmp)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files unchanged\n")
        self.assertEqual(proc.stderr, "")
        self.assertEqual(tree_digest(self.tmp), before)

    def test_upgrade_simulation(self) -> None:
        edge, obs = self.members(obs_env=OBS_ENV_OLD, obs_example=OBS_EXAMPLE_OLD)
        self.assertExit(self.sync(edge, obs), 0)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE_NEW)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        for line in (
            header("edge-plane/.env", "in sync"),
            header("obs-plane/.env", "would write"),
            "   added      AUDIT_VERIFY_INTERVAL=300",
            "   followed   SCRAPE_INTERVAL: 15s -> 30s",
            (
                "   removed    LEGACY_EXPORTER "
                "(gone from the example; your line was its old default)"
            ),
            "   renamed    QUERY_TIMEOUT -> PROBE_TIMEOUT (value carried)",
            "   filled     AUDIT_EDGE_SECRET (from edge-plane)",
            (
                "summary: 1 would change, 1 in sync, 0 refused, 0 conflict(s). "
                "Run 'make env' to apply."
            ),
        ):
            self.assertIn(line + "\n", proc.stdout)
        self.assertExit(self.sync(edge, obs), 0)
        self.assertEqual(read(obs / ".env"), OBS_UPGRADED)
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files in sync\n")
        before = tree_digest(self.tmp)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files unchanged\n")
        self.assertEqual(tree_digest(self.tmp), before)

    def test_created_and_empty_env(self) -> None:
        edge, obs = self.members(edge_env=None, obs_env="")
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertIn(header("edge-plane/.env", "created") + "\n", proc.stdout)
        self.assertEqual(read(edge / ".env"), EDGE_EXAMPLE)
        self.assertEqual(mode(edge / ".env"), 0o600)
        self.assertFalse((self.state / "backup" / "edge-plane").exists())
        self.assertEqual(read(obs / ".env"), OBS_EXAMPLE)
        backups = list((self.state / "backup" / "obs-plane").iterdir())
        self.assertEqual([path.read_bytes() for path in backups], [b""])
        self.assertIn(
            header("obs-plane/.env", "written")
            + f" (backup: state/backup/obs-plane/{backups[0].name})\n",
            proc.stdout,
        )
        self.assertIn(
            "   placeholder GRAFANA_ADMIN_PASSWORD "
            "(example placeholder; set a real value)\n",
            proc.stdout,
        )
        self.assertIn("== shared\n" + UNSET_AUDIT, proc.stdout)

    def test_missing_member_dir(self) -> None:
        edge, obs = self.members()
        bare = make_member(self.infra, "docint", "", "QDRANT_URL=SENTINEL-url\n")
        (bare / ".env.example").unlink()
        absent = self.infra / "translator"
        proc = self.sync(edge, absent, obs, bare)
        self.assertExit(proc, 1)
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
        self.assertEqual(read(obs / ".env"), OBS_SYNCED)
        self.assertIn(
            header("translator/.env", "missing")
            + "\n   missing    directory not found\n",
            proc.stdout,
        )
        self.assertIn(
            header("docint/.env", "missing") + "\n   missing    no .env.example\n",
            proc.stdout,
        )
        self.assertIn(
            "summary: 2 written, 0 unchanged, 2 refused, 0 conflict(s).", proc.stdout
        )
        self.assertFalse(absent.exists())
        self.assertEqual(read(bare / ".env"), "QDRANT_URL=SENTINEL-url\n")
        self.assertEqual(
            sorted(os.listdir(self.state / "base")),
            [
                "edge-plane.example",
                "edge-plane.placeholders",
                "obs-plane.example",
                "obs-plane.placeholders",
            ],
        )

    def test_refused_file_does_not_block_others(self) -> None:
        cases = {
            "line 1: CRLF line end": (
                b"EDGE_HOST=edge.example.invalid\r\n"
                b"GRAFANA_ADMIN_PASSWORD=SENTINEL-grafana\r\n"
            ),
            "line 1: byte-order mark": b"\xef\xbb\xbfEDGE_HOST=edge.example.invalid\n",
            "line 2: not UTF-8": (
                b"EDGE_HOST=edge.example.invalid\nGRAFANA_ADMIN_PASSWORD=SENTINEL-\xe4\n"
            ),
        }
        for reason, data in cases.items():
            with self.subTest(reason):
                shutil.rmtree(self.infra, ignore_errors=True)
                shutil.rmtree(self.state, ignore_errors=True)
                edge, obs = self.members(obs_env=data)
                proc = self.sync(edge, obs)
                self.assertExit(proc, 1)
                self.assertIn(
                    header("obs-plane/.env", "refused") + f"\n   refused    {reason}\n",
                    proc.stdout,
                )
                self.assertEqual((obs / ".env").read_bytes(), data)
                self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
                self.assertFalse((self.state / "backup" / "obs-plane").exists())
                self.assertFalse((self.state / "base" / "obs-plane.example").exists())

    def test_symlinked_env_refused(self) -> None:
        edge, obs = self.members(obs_env=None)
        secrets = self.tmp / "secrets.env"
        secrets.write_bytes(b"GRAFANA_ADMIN_PASSWORD=SENTINEL-shared\n")
        os.symlink(secrets, obs / ".env")
        proc = self.sync(edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(header("obs-plane/.env", "refused") + "\n", proc.stdout)
        self.assertIn(": is a symlink\n", proc.stdout)
        self.assertTrue((obs / ".env").is_symlink())
        self.assertEqual(
            secrets.read_bytes(), b"GRAFANA_ADMIN_PASSWORD=SENTINEL-shared\n"
        )
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)

    def test_conflict_keeps_the_group_and_writes_the_rest(self) -> None:
        edge, obs = self.members(obs_env=OBS_ENV + "AUDIT_EDGE_SECRET=SENTINEL-other\n")
        proc = self.sync(edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(
            "== shared\n"
            "   conflict   AUDIT_EDGE_SECRET: obs-plane differs from edge-plane\n",
            proc.stdout,
        )
        self.assertIn(
            "summary: 2 written, 0 unchanged, 0 refused, 1 conflict(s).", proc.stdout
        )
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
        self.assertEqual(value(obs / ".env", "AUDIT_EDGE_SECRET"), "SENTINEL-other")
        self.assertEqual(
            value(obs / ".env", "EDGE_HOST"), "SENTINEL-gw.example.invalid"
        )
        self.assertExit(self.sync("--check", edge, obs), 1)

    def test_deploy_dir(self) -> None:
        deploy = make_deploy(self.infra, FED_EXAMPLE_OLD, FED_ENV)
        proc = self.sync(deploy)
        self.assertExit(proc, 0)
        self.assertIn(header("deploy/federation.env", "written"), proc.stdout)
        self.assertIn("   added      DATA_PROFILE=cpu\n", proc.stdout)
        self.assertIn(
            "   lint       line 2: inline comment in federation.env: make keeps "
            "the blanks before it in the value\n",
            proc.stdout,
        )
        self.assertEqual(read(deploy / "federation.env"), FED_SYNCED)
        make_deploy(self.infra, FED_EXAMPLE_NEW)
        proc = self.sync(deploy)
        self.assertExit(proc, 0)
        self.assertIn(
            "   held       INFRA_ROOT: .. -> ../members "
            "(example default changed; yours kept)\n",
            proc.stdout,
        )
        self.assertIn("   added      WAIT_TIMEOUT=180\n", proc.stdout)
        self.assertEqual(read(deploy / "federation.env"), FED_UPGRADED)
        self.assertEqual(
            sorted(os.listdir(deploy)), ["federation.env", "federation.env.example"]
        )
        self.assertEqual(read(self.state / "base" / "deploy.example"), FED_EXAMPLE_NEW)

    def test_generate(self) -> None:
        edge, obs = self.members(edge_env=EDGE_ENV_UNSET, obs_env=OBS_ENV_UNSET)
        proc = self.sync("--generate", edge, obs)
        self.assertExit(proc, 0)
        token = value(edge / ".env", "AUDIT_EDGE_SECRET")
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertEqual(value(obs / ".env", "AUDIT_EDGE_SECRET"), token)
        self.assertNotIn(token, proc.stdout + proc.stderr)
        self.assertIn("   generated  AUDIT_EDGE_SECRET\n", proc.stdout)
        proc = self.sync("--generate", edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files unchanged\n")
        self.assertEqual(value(obs / ".env", "AUDIT_EDGE_SECRET"), token)

    def placeholder_runs(self, first_table: str) -> None:
        """Runs 2 and 3 after the history must not spread the old
        placeholder to edge-plane."""
        edge, obs = self.placeholder_history(first_table)
        for run in (2, 3):
            with self.subTest(run=run):
                self.assertExit(self.sync(edge, obs), 0)
                self.assertNotIn("change-me", read(edge / ".env"))
                self.assertEqual(value(obs / ".env", "AUDIT_EDGE_SECRET"), "change-me")

    def test_placeholders_remembered_across_runs(self) -> None:
        self.placeholder_runs(TABLE)

    def test_placeholders_of_the_replaced_copy_remembered(self) -> None:
        # The key becomes shared in the release that changes its
        # placeholder: only the replaced stored copy still knows `change-me`.
        self.placeholder_runs(HOST_ROWS)

    def test_duplicate_dirs(self) -> None:
        edge, obs = self.members()
        link = self.tmp / "edge-link"
        os.symlink(edge, link)
        proc = self.sync(edge, obs, f"{edge}/", obs / ".." / "obs-plane", link)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout.count("== edge-plane/.env"), 1)
        self.assertEqual(proc.stdout.count("== obs-plane/.env"), 1)
        self.assertNotIn("edge-link", proc.stdout)
        self.assertTrue(proc.stdout.endswith(SUMMARY_WRITTEN), proc.stdout)

    def test_readable_warning_for_member_env_only(self) -> None:
        # federation.env holds no secrets and is usually 0644: no warning.
        deploy = make_deploy(self.infra, FED_EXAMPLE_OLD, "INFRA_ROOT=..\n")
        os.chmod(deploy / "federation.env", 0o644)
        edge = make_member(self.infra, "edge-plane", EDGE_EXAMPLE, EDGE_ENV, 0o644)
        obs = make_member(self.infra, "obs-plane", OBS_EXAMPLE, OBS_ENV)
        readable = "   readable   edge-plane/.env (readable by every user; chmod 600)\n"
        proc = self.sync(deploy, edge, obs)
        self.assertExit(proc, 0)
        self.assertIn(readable, proc.stdout)
        self.assertNotIn("deploy/federation.env (readable", proc.stdout)
        self.assertEqual(mode(edge / ".env"), 0o644)
        self.assertEqual(mode(deploy / "federation.env"), 0o644)
        proc = self.sync("--check", deploy, edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(
            proc.stdout,
            "env: 3 files in sync\n"
            + header("edge-plane/.env", "in sync")
            + "\n"
            + readable,
        )

    MODE_LINE = (
        "   mode       obs-plane/.env (other users' access removed when a "
        "shared secret is written)\n"
    )
    OBS_READABLE = "   readable   obs-plane/.env (readable by every user; chmod 600)\n"

    def fill_members(self) -> Tuple[Path, Path]:
        """edge-plane holds the audit secret; obs-plane's 0644 .env lacks it."""
        edge = make_member(self.infra, "edge-plane", EDGE_EXAMPLE, EDGE_ENV)
        obs = make_member(self.infra, "obs-plane", OBS_EXAMPLE, OBS_ENV, 0o644)
        return edge, obs

    def test_filled_secret_removes_other_access(self) -> None:
        edge, obs = self.fill_members()
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(mode(obs / ".env"), 0o640)
        self.assertIn(self.MODE_LINE, proc.stdout)
        self.assertNotIn(self.OBS_READABLE, proc.stdout)

    def test_readable_file_without_fill_keeps_mode(self) -> None:
        edge = make_member(self.infra, "edge-plane", EDGE_EXAMPLE, EDGE_ENV)
        obs = make_member(self.infra, "obs-plane", OBS_EXAMPLE, OBS_SYNCED, 0o644)
        self.assertExit(self.sync(edge, obs), 0)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(mode(obs / ".env"), 0o644)
        self.assertIn(self.OBS_READABLE, proc.stdout)
        self.assertNotIn("   mode ", proc.stdout)

    def test_generate_into_readable_file(self) -> None:
        edge = make_member(
            self.infra, "edge-plane", EDGE_EXAMPLE, EDGE_ENV_UNSET, 0o644
        )
        obs = make_member(self.infra, "obs-plane", OBS_EXAMPLE, OBS_ENV_UNSET, 0o644)
        self.assertExit(self.sync("--generate", edge, obs), 0)
        self.assertEqual(mode(edge / ".env"), 0o640)
        self.assertEqual(mode(obs / ".env"), 0o640)

    def test_check_previews_mode_without_writing(self) -> None:
        edge, obs = self.fill_members()
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        self.assertEqual(mode(obs / ".env"), 0o644)
        self.assertEqual(read(obs / ".env"), OBS_ENV)
        self.assertIn(self.OBS_READABLE, proc.stdout)
        self.assertIn(self.MODE_LINE, proc.stdout)

    def test_member_dirs_stay_clean(self) -> None:
        edge, obs = self.members(obs_env=OBS_ENV_OLD, obs_example=OBS_EXAMPLE_OLD)
        self.assertExit(self.sync("--check", edge, obs), 1)
        self.assertExit(self.sync(edge, obs), 0)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE_NEW)
        self.assertExit(self.sync("--check", edge, obs), 1)
        self.assertExit(self.sync("--generate", edge, obs), 0)
        self.assertExit(self.sync(edge, obs), 0)
        self.assertEqual(read(obs / ".env"), OBS_UPGRADED)
        for member in (edge, obs):
            self.assertEqual(sorted(os.listdir(member)), [".env", ".env.example"])

    def test_no_sentinel_leaks(self) -> None:
        edge, obs = self.members(obs_env=OBS_ENV_OLD, obs_example=OBS_EXAMPLE_OLD)
        deploy = make_deploy(self.infra, FED_EXAMPLE_OLD, "INFRA_ROOT=SENTINEL-root\n")
        crlf = make_member(
            self.infra, "translator", "TEXT_MODEL=m\n", b"TEXT_MODEL=SENTINEL-t\r\n"
        )
        dirs = (deploy, edge, obs, crlf, self.infra / "absent")
        self.assertExit(self.sync("--check", *dirs), 1)
        self.assertExit(self.sync(*dirs), 1)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE_NEW)
        make_deploy(self.infra, FED_EXAMPLE_NEW)
        self.assertExit(self.sync("--check", *dirs), 1)
        self.assertExit(self.sync("--check", "--allow-drift", *dirs), 0)
        self.assertExit(self.sync(*dirs), 1)
        self.assertExit(self.sync("--generate", *dirs), 1)
        self.assertExit(self.sync("--check", *dirs), 1)
        self.assertEqual(read(obs / ".env"), OBS_UPGRADED)
        self.assertIn("SENTINEL-root", read(deploy / "federation.env"))
        self.assertEqual((crlf / ".env").read_bytes(), b"TEXT_MODEL=SENTINEL-t\r\n")
        found = [
            path
            for path in self.tmp.rglob("*")
            if path.is_file() and b"SENTINEL-" in path.read_bytes()
        ]
        self.assertTrue(any(self.state in path.parents for path in found))
        leaked = [
            path
            for path in found
            if path.name not in (".env", "federation.env")
            and self.state not in path.parents
        ]
        self.assertEqual(leaked, [])


class CheckTests(CliCase):
    def test_check_writes_nothing(self) -> None:
        edge, obs = self.members()
        before = tree_digest(self.tmp)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(header("obs-plane/.env", "would write") + "\n", proc.stdout)
        self.assertEqual(tree_digest(self.tmp), before)
        self.assertFalse(self.state.exists())
        self.assertExit(self.sync(edge, obs), 0)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE + "RETRY_LIMIT=3\n")
        before = tree_digest(self.tmp)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(header("obs-plane/.env", "would write") + "\n", proc.stdout)
        self.assertIn("   added      RETRY_LIMIT=3\n", proc.stdout)
        self.assertEqual(tree_digest(self.tmp), before)

    def test_check_never_synced(self) -> None:
        edge, obs = self.members(edge_env=EDGE_SYNCED, obs_env=OBS_SYNCED)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(header("edge-plane/.env", "in sync") + "\n", proc.stdout)
        self.assertEqual(proc.stdout.count("   never synced: run 'make env' once\n"), 2)
        self.assertIn(
            "summary: 0 would change, 2 in sync, 0 refused, 0 conflict(s).",
            proc.stdout,
        )
        self.assertFalse(self.state.exists())
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files unchanged\n")
        self.assertTrue((self.state / "base" / "obs-plane.example").is_file())
        self.assertExit(self.sync("--check", edge, obs), 0)

    def test_check_after_env_passes(self) -> None:
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files in sync\n")
        self.assertEqual(proc.stderr, "")

    def test_allow_drift(self) -> None:
        warning = (
            "WARNING: ENV_ALLOW_DRIFT=1 — 'make up' continues although "
            "env-check failed (see above).\n"
        )
        edge, obs = self.members()
        proc = self.sync("--check", "--allow-drift", edge, obs)
        self.assertExit(proc, 0)
        self.assertIn(header("obs-plane/.env", "would write") + "\n", proc.stdout)
        self.assertIn(warning, proc.stderr)
        self.assertFalse(self.state.exists())
        self.assertExit(self.sync(edge, obs), 0)
        proc = self.sync("--check", "--allow-drift", edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files in sync\n")
        self.assertEqual(proc.stderr, "")
        make_member(self.infra, "obs-plane", OBS_EXAMPLE + "RETRY_LIMIT=3\n")
        self.assertExit(self.sync("--check", edge, obs), 1)
        proc = self.sync("--check", "--allow-drift", edge, obs)
        self.assertExit(proc, 0)
        self.assertIn("   added      RETRY_LIMIT=3\n", proc.stdout)
        self.assertEqual(proc.stderr, warning)

    def test_comment_change_is_not_behind(self) -> None:
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE + "# A new note.\n")
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 0)
        self.assertIn(header("obs-plane/.env", "would write") + "\n", proc.stdout)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertTrue(read(obs / ".env").endswith("# A new note.\n"))

    def test_short_form_shows_warnings(self) -> None:
        edge, obs = self.members(edge_env=EDGE_ENV_UNSET, obs_env=OBS_ENV_UNSET)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 0)
        self.assertIn(UNSET_AUDIT, proc.stdout)
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 2 files in sync\n== shared\n" + UNSET_AUDIT)


class ErrorTests(CliCase):
    def assertUntouched(self, edge: Path, obs: Path) -> None:
        self.assertEqual(read(edge / ".env"), EDGE_ENV)
        self.assertEqual(read(obs / ".env"), OBS_ENV)

    def test_usage_errors(self) -> None:
        edge, obs = self.members()
        cases = (
            (("--generate", "--check", edge, obs), "--generate"),
            ((), "no directories given"),
            (("--allow-drift", edge, obs), "--allow-drift"),
            (("--frobnicate", edge, obs), "unrecognized arguments"),
            ((edge, self.tmp / "elsewhere" / "edge-plane"), "two directories"),
        )
        for args, message in cases:
            with self.subTest(message):
                proc = self.sync(*args)
                self.assertExit(proc, 2)
                self.assertIn(message, proc.stderr)
                self.assertEqual(proc.stdout, "")
        self.table.write_text("audit-edge  edge-plane  AUDIT_EDGE_SECRET  gen\n")
        proc = self.sync(edge, obs)
        self.assertExit(proc, 2)
        self.assertIn("env-sync.conf line 1: ", proc.stderr)
        self.table.unlink()
        for args in ((edge, obs), ("--check", edge, obs)):
            proc = self.sync(*args)
            self.assertExit(proc, 2)
            self.assertIn("cannot read", proc.stderr)
        self.assertFalse(self.state.exists())
        self.assertUntouched(edge, obs)

    def test_lock_held(self) -> None:
        edge, obs = self.members()
        self.state.mkdir(mode=0o700)
        fd = os.open(str(self.state / "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 2)
        self.assertIn("lock", proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertUntouched(edge, obs)
        self.assertEqual(os.listdir(self.state / "backup"), [])
        self.assertEqual(os.listdir(self.state / "base"), [])
        self.assertExit(self.sync("--check", edge, obs), 1)

    def test_unreadable_stored_copy(self) -> None:
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        (self.state / "base" / "obs-plane.example").write_bytes(b"A=\xff\n")
        proc = self.sync("--check", edge, obs)
        self.assertExit(proc, 1)
        self.assertIn(
            header("obs-plane/.env", "in sync") + "\n"
            "   state      stored copy unreadable; treated as never synced\n",
            proc.stdout,
        )
        self.assertIn("   never synced: run 'make env' once\n", proc.stdout)
        self.assertExit(self.sync(edge, obs), 0)
        self.assertEqual(read(self.state / "base" / "obs-plane.example"), OBS_EXAMPLE)
        self.assertExit(self.sync("--check", edge, obs), 0)

    def test_damaged_stored_copy_keeps_the_placeholders(self) -> None:
        # Only the stored copy is damaged (not UTF-8, or rejected by a
        # stricter load_example): the remembered placeholders still apply.
        damages = {
            "not UTF-8": b"A=\xff\n",
            "rejected": b"EDGE_HOST=edge.example.invalid\n# renamed: broken\n",
        }
        for name, damage in damages.items():
            with self.subTest(name):
                shutil.rmtree(self.infra, ignore_errors=True)
                shutil.rmtree(self.state, ignore_errors=True)
                edge, obs = self.placeholder_history(TABLE)
                self.assertExit(self.sync(edge, obs), 0)
                placeholders = self.state / "base" / "obs-plane.placeholders"
                self.assertIn("change-me", read(placeholders).split())
                (self.state / "base" / "obs-plane.example").write_bytes(damage)
                proc = self.sync(edge, obs)
                self.assertExit(proc, 0)
                self.assertIn(
                    "   state      stored copy unreadable; treated as never synced\n",
                    proc.stdout,
                )
                self.assertNotIn("filled", proc.stdout)
                self.assertNotIn("change-me", read(edge / ".env"))
                self.assertIn("change-me", read(placeholders).split())
                self.assertExit(self.sync("--check", edge, obs), 0)

    def test_damaged_placeholders_are_repaired(self) -> None:
        # A member without shared keys: a damaged placeholders file is a
        # warning, not "never synced", and one make env rewrites it.
        member = make_member(
            self.infra, "translator", "TEXT_MODEL=m\n", "TEXT_MODEL=m\n"
        )
        self.assertExit(self.sync(member), 0)
        placeholders = self.state / "base" / "translator.placeholders"
        placeholders.write_bytes(b"\xff\n")
        proc = self.sync("--check", member)
        self.assertExit(proc, 0)
        self.assertEqual(
            proc.stdout,
            "env: 1 files in sync\n"
            + header("translator/.env", "in sync")
            + "\n   state      stored placeholders unreadable; "
            "'make env' rewrites them\n",
        )
        self.assertExit(self.sync(member), 0)
        self.assertEqual(placeholders.read_bytes(), b"")
        proc = self.sync("--check", member)
        self.assertExit(proc, 0)
        self.assertEqual(proc.stdout, "env: 1 files in sync\n")

    @unittest.skipIf(os.geteuid() == 0, "root writes into a read-only directory")
    def test_state_write_failure_is_reported(self) -> None:
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE + "RETRY_LIMIT=3\n")
        base = self.state / "base"
        os.chmod(base, 0o500)
        self.addCleanup(os.chmod, base, 0o700)
        proc = self.sync(edge, obs)
        self.assertExit(proc, 1)
        self.assertEqual(proc.stderr, "")
        self.assertIn(header("edge-plane/.env", "unchanged") + "\n", proc.stdout)
        self.assertIn(
            header("obs-plane/.env", "written") + " (backup: state/backup/obs-plane/",
            proc.stdout,
        )
        self.assertIn(
            "   added      RETRY_LIMIT=3\n" + not_saved(errno.EACCES), proc.stdout
        )
        self.assertTrue(
            proc.stdout.endswith(
                "summary: 1 written, 1 unchanged, 0 refused, 0 conflict(s). "
                "Changes apply with the next 'make up'.\n"
            ),
            proc.stdout,
        )
        self.assertIn("RETRY_LIMIT=3\n", read(obs / ".env"))
        self.assertEqual(len(os.listdir(self.state / "backup" / "obs-plane")), 2)
        self.assertEqual(os.listdir(self.state / "tmp"), [])

    def test_stored_copy_failure_is_a_file_error(self) -> None:
        # In-process: StateDir.write_base fails for the first file only.
        edge, obs = self.members()
        write_base = StateDir.write_base

        def failing(state: StateDir, file_id: str, text: str) -> None:
            if file_id == "edge-plane":
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            write_base(state, file_id, text)

        argv = ["--state", str(self.state), "--table", str(self.table)]
        out, err = io.StringIO(), io.StringIO()
        patch = mock.patch.object(StateDir, "write_base", failing)
        with patch, redirect_stdout(out), redirect_stderr(err):
            code = main(argv + [str(edge), str(obs)])
        report = out.getvalue()
        assert_no_sentinel(self, report, err.getvalue())
        self.assertEqual(code, 1, report)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(read(edge / ".env"), EDGE_SYNCED)
        self.assertEqual(read(obs / ".env"), OBS_SYNCED)
        start, end = report.index("== edge-plane/.env"), report.index("== obs-plane/")
        block = report[start:end]
        self.assertTrue(block.startswith(header("edge-plane/.env", "written")))
        self.assertTrue(block.endswith(not_saved(errno.ENOSPC)), block)
        self.assertIn(header("obs-plane/.env", "written"), report)
        self.assertNotIn("   state      ", report[end:])
        self.assertTrue(report.endswith(SUMMARY_WRITTEN), report)
        self.assertFalse((self.state / "base" / "edge-plane.example").exists())
        self.assertEqual(read(self.state / "base" / "obs-plane.example"), OBS_EXAMPLE)

    def test_refused_writes_keep_the_backups(self) -> None:
        # In-process: the state directory reads as another filesystem, so the
        # replace is refused after the file was read. A refusal takes no
        # backup: six in a row rotate none of the earlier ones away.
        edge, obs = self.members()
        self.assertExit(self.sync(edge, obs), 0)
        folder = self.state / "backup" / "obs-plane"
        earlier = sorted(os.listdir(folder))
        self.assertEqual(len(earlier), 1)
        make_member(self.infra, "obs-plane", OBS_EXAMPLE + "RETRY_LIMIT=3\n")
        tmp, real = str(self.state / "tmp"), os.stat

        def elsewhere(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            found = real(path, *args, **kwargs)
            if str(path) != tmp:
                return found
            fields = list(found)
            fields[2] = found.st_dev + 1
            return os.stat_result(fields)

        argv = ["--state", str(self.state), "--table", str(self.table)]
        for _ in range(6):
            out, err = io.StringIO(), io.StringIO()
            patch = mock.patch("envsync.state.os.stat", side_effect=elsewhere)
            with patch, redirect_stdout(out), redirect_stderr(err):
                code = main(argv + [str(edge), str(obs)])
            report = out.getvalue()
            assert_no_sentinel(self, report, err.getvalue())
            self.assertEqual(code, 1, report)
            self.assertIn(header("obs-plane/.env", "refused") + "\n", report)
            self.assertIn("state directory on another filesystem", report)
        self.assertEqual(sorted(os.listdir(folder)), earlier)
        self.assertEqual(read(obs / ".env"), OBS_SYNCED)

    def test_unusable_state_dir(self) -> None:
        edge, obs = self.members()
        real = self.tmp / "real-state"
        real.mkdir()
        os.symlink(real, self.state)
        for args in ((edge, obs), ("--check", edge, obs)):
            with self.subTest(check="--check" in args):
                proc = self.sync(*args)
                self.assertExit(proc, 2)
                self.assertIn("symlink", proc.stderr)
        self.assertEqual(os.listdir(real), [])
        self.assertUntouched(edge, obs)


class ReportTests(unittest.TestCase):
    def test_every_event_kind_has_a_label(self) -> None:
        kinds = set(_KINDS) | set(_FILE_KINDS)
        kinds |= {"conflict", "unset_everywhere", "table"}
        kinds |= {"placeholder", "readable", "state", "mode"}
        self.assertEqual(kinds - set(LABEL), set())

    def test_event_line(self) -> None:
        self.assertEqual(
            event_line(Event("added", ["A=1", "B_KEY"])), "   added      A=1, B_KEY"
        )
        self.assertEqual(
            event_line(Event("kept_unset", ["MODE"])),
            "   kept unset MODE (commented out on purpose)",
        )
        self.assertEqual(
            event_line(Event("placeholder", ["X_SECRET"], "warn")),
            "   placeholder X_SECRET (example placeholder; set a real value)",
        )


if __name__ == "__main__":
    unittest.main()
