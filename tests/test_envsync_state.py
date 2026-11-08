from __future__ import annotations

import errno
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest import mock

from envsync_helpers import assert_no_sentinel
from envsync.state import (
    BACKUPS_KEPT,
    LockHeld,
    StateDir,
    StateError,
    WriteRefused,
    atomic_replace,
    read_target,
    world_readable,
)


def mode(p: Path) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "state"
        self.member = self.tmp / "member"
        self.member.mkdir()
        self.env = self.member / ".env"

    def state(self) -> StateDir:
        return StateDir(self.root, True)


class StateDirTests(Base):
    def test_create_modes_and_readonly(self) -> None:
        StateDir(self.root, True)
        for p in (self.root, self.root / "base", self.root / "backup", self.root / "tmp"):
            self.assertEqual(mode(p), 0o700)
        r2 = self.tmp / "state2"
        StateDir(r2, False)
        self.assertFalse(r2.exists())

    def test_wider_modes_reset(self) -> None:
        StateDir(self.root, True)
        os.chmod(self.root, 0o755)
        os.chmod(self.root / "base", 0o750)
        StateDir(self.root, True)
        self.assertEqual(mode(self.root), 0o700)
        self.assertEqual(mode(self.root / "base"), 0o700)

    def test_symlinked_root(self) -> None:
        real = self.tmp / "real"
        real.mkdir()
        os.symlink(real, self.root)
        for create in (True, False):
            with self.assertRaises(StateError):
                StateDir(self.root, create)

    def test_base_roundtrip(self) -> None:
        s = self.state()
        self.assertIsNone(s.read_base("a"))
        s.write_base("a", "K=v\n")
        self.assertEqual(s.read_base("a"), "K=v\n")
        self.assertEqual(mode(self.root / "base" / "a.example"), 0o600)
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    def test_placeholders_roundtrip(self) -> None:
        s = self.state()
        self.assertEqual(s.read_placeholders("a"), frozenset())
        s.write_placeholders("a", ["zeta", "alpha", "", "bad\nline", "bad\rline"])
        self.assertEqual(s.read_placeholders("a"), frozenset({"zeta", "alpha"}))
        p = self.root / "base" / "a.placeholders"
        self.assertEqual(p.read_text(), "alpha\nzeta\n")
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    def test_backup_rotation(self) -> None:
        s = self.state()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        paths = [s.backup("a", b"x%d" % i, t0 + timedelta(seconds=i)) for i in range(7)]
        files = sorted(os.listdir(self.root / "backup" / "a"))
        self.assertEqual(len(files), BACKUPS_KEPT)
        self.assertEqual(files[-1], paths[-1].name)
        self.assertFalse(paths[0].exists())
        for f in files:
            self.assertEqual(mode(self.root / "backup" / "a" / f), 0o600)

    def test_same_second_backups_pruned_in_order(self) -> None:
        s = self.state()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        paths = [s.backup("a", b"%d" % i, now) for i in range(7)]
        self.assertEqual(paths[0].name, "20260101T000000Z.env")
        self.assertEqual(paths[1].name, "20260101T000000Z-1.env")
        kept = {p.name for p in (self.root / "backup" / "a").iterdir()}
        self.assertEqual(kept, {p.name for p in paths[2:]})
        self.assertEqual(paths[-1].read_bytes(), b"6")

    def test_lock_held(self) -> None:
        a, b = self.state(), self.state()
        with a.lock():
            with self.assertRaises(LockHeld):
                with b.lock():
                    pass
        with b.lock():
            pass


class ReplaceTests(Base):
    def replace(self, text: str, file_id: str = "m") -> None:
        s = self.state()
        atomic_replace(read_target(self.env), text, s, file_id)

    def test_replace_keeps_mode(self) -> None:
        self.env.write_text("A=1\n")
        os.chmod(self.env, 0o640)
        self.replace("A=2\n")
        self.assertEqual(self.env.read_text(), "A=2\n")
        self.assertEqual(mode(self.env), 0o640)

    def test_strip_other_removes_other_access(self) -> None:
        self.env.write_text("A=1\n")
        os.chmod(self.env, 0o644)
        atomic_replace(
            read_target(self.env), "A=2\n", self.state(), "m", strip_other=True
        )
        self.assertEqual(self.env.read_text(), "A=2\n")
        self.assertEqual(mode(self.env), 0o640)

    def test_without_strip_other_0644_stays(self) -> None:
        self.env.write_text("A=1\n")
        os.chmod(self.env, 0o644)
        self.replace("A=2\n")
        self.assertEqual(mode(self.env), 0o644)

    def test_replace_new_file_0600(self) -> None:
        self.assertIsNone(read_target(self.env).data)
        self.replace("A=1\n")
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.assertEqual(mode(self.env), 0o600)

    def test_refuses_symlink(self) -> None:
        secret = self.member / "secrets.env"
        secret.write_text("SENTINEL-1\n")
        os.symlink(secret, self.env)
        with self.assertRaises(WriteRefused) as cm:
            read_target(self.env)
        assert_no_sentinel(self, str(cm.exception))
        self.assertEqual(secret.read_text(), "SENTINEL-1\n")

    def test_refuses_hardlink(self) -> None:
        self.env.write_text("A=1\n")
        os.link(self.env, self.member / "other")
        with self.assertRaises(WriteRefused):
            read_target(self.env)

    def test_refuses_non_regular(self) -> None:
        self.env.mkdir()
        with self.assertRaises(WriteRefused):
            read_target(self.env)

    def test_refuses_changed_since_read(self) -> None:
        self.env.write_text("A=1\n")
        snap = read_target(self.env)
        self.env.write_text("A=newer\n")
        with self.assertRaises(WriteRefused):
            atomic_replace(snap, "A=2\n", self.state(), "m")
        self.assertEqual(self.env.read_text(), "A=newer\n")

    def test_refuses_created_since_read(self) -> None:
        snap = read_target(self.env)
        self.env.write_text("A=newer\n")
        with self.assertRaises(WriteRefused):
            atomic_replace(snap, "A=2\n", self.state(), "m")
        self.assertEqual(self.env.read_text(), "A=newer\n")

    def test_backup_hook_runs_after_the_checks(self) -> None:
        self.env.write_text("A=1\n")
        s = self.state()
        seen: List[str] = []

        def backup() -> Path:
            seen.append(self.env.read_text())
            return self.root / "taken"

        snap = read_target(self.env)
        self.env.write_text("A=newer\n")
        with self.assertRaises(WriteRefused):
            atomic_replace(snap, "A=2\n", s, "m", backup)
        self.assertEqual(seen, [])
        snap = read_target(self.env)
        taken = atomic_replace(snap, "A=2\n", s, "m", backup)
        self.assertEqual(taken, self.root / "taken")
        self.assertEqual(seen, ["A=newer\n"])
        self.assertEqual(self.env.read_text(), "A=2\n")
        self.assertIsNone(atomic_replace(read_target(self.env), "A=3\n", s, "m"))

    def test_no_leftovers_on_failure(self) -> None:
        self.env.write_text("A=1\n")
        s = self.state()
        snap = read_target(self.env)
        with mock.patch("envsync.state.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic_replace(snap, "A=2\n", s, "m")
        self.assertEqual(os.listdir(self.member), [".env"])
        self.assertEqual(self.env.read_text(), "A=1\n")
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    def test_other_filesystem(self) -> None:
        self.env.write_text("A=1\n")
        s = self.state()
        snap = read_target(self.env)
        real = os.stat

        def fake(p, *a, **k):  # type: ignore[no-untyped-def]
            r = real(p, *a, **k)
            if str(p) == str(self.root / "tmp"):
                vals = list(r)
                vals[2] = r.st_dev + 1
                return os.stat_result(vals)
            return r

        with mock.patch("envsync.state.os.stat", side_effect=fake):
            with self.assertRaises(WriteRefused) as cm:
                atomic_replace(snap, "A=2\n", s, "m")
        self.assertIn("another filesystem", str(cm.exception))
        self.assertEqual(self.env.read_text(), "A=1\n")

    def test_world_readable(self) -> None:
        self.env.write_text("A=1\n")
        os.chmod(self.env, 0o644)
        self.assertTrue(world_readable(read_target(self.env)))
        os.chmod(self.env, 0o600)
        self.assertFalse(world_readable(read_target(self.env)))
        self.env.unlink()
        self.assertFalse(world_readable(read_target(self.env)))

    @unittest.skipUnless(os.geteuid() == 0, "needs root")
    def test_owner_check(self) -> None:
        self.env.write_text("A=1\n")
        os.chown(self.env, 12345, 12345)
        self.replace("A=2\n")
        self.assertEqual(os.lstat(self.env).st_uid, 12345)

    @unittest.skipUnless(os.geteuid() == 0, "needs root")
    def test_new_file_as_root_owned_like_its_directory(self) -> None:
        os.chown(self.member, 12345, 12345)
        self.replace("A=1\n")
        st = os.lstat(self.env)
        self.assertEqual((st.st_uid, st.st_gid), (12345, 12345))

    def test_new_file_as_root_takes_the_directory_owner(self) -> None:
        # The branch above, run by any user: root is mocked, and so is chown.
        folder = os.stat(self.member)
        with mock.patch("envsync.state.os.geteuid", return_value=0):
            with mock.patch("envsync.state.os.chown") as chown:
                self.replace("A=1\n")
        chown.assert_called_once_with(mock.ANY, folder.st_uid, folder.st_gid)
        self.assertEqual(self.env.read_text(), "A=1\n")

    @unittest.skipIf(os.geteuid() == 0, "root carries the owner")
    def test_group_carried(self) -> None:
        self.env.write_text("A=1\n")
        s = self.state()
        probe = self.root / "tmp" / "probe"
        probe.write_text("")
        fresh = os.lstat(probe).st_gid
        probe.unlink()
        for gid in os.getgroups():
            if gid == fresh:
                continue
            try:
                os.chown(self.env, -1, gid)
                break
            except PermissionError:
                continue
        group = os.lstat(self.env).st_gid
        atomic_replace(read_target(self.env), "A=2\n", s, "m")
        self.assertEqual(os.lstat(self.env).st_gid, group)

    @unittest.skipIf(os.geteuid() == 0, "root carries the owner")
    def test_group_refused_does_not_fail_the_write(self) -> None:
        self.env.write_text("A=1\n")
        snap = read_target(self.env)
        refused = PermissionError(errno.EPERM, os.strerror(errno.EPERM))
        with mock.patch("envsync.state.os.chown", side_effect=refused) as chown:
            atomic_replace(snap, "A=2\n", self.state(), "m")
        chown.assert_called_once_with(mock.ANY, -1, snap.stat.st_gid)
        self.assertEqual(self.env.read_text(), "A=2\n")
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    @unittest.skipIf(os.geteuid() == 0, "root may write any owner")
    def test_owner_refused(self) -> None:
        self.env.write_text("A=1\n")
        snap = read_target(self.env)
        fake = os.stat_result((snap.stat.st_mode, 0, 0, 1, os.geteuid() + 1, 0, 0, 0, 0, 0))
        real = os.lstat

        def lst(p, *a, **k):  # type: ignore[no-untyped-def]
            return fake if str(p) == str(self.env) else real(p, *a, **k)

        with mock.patch("envsync.state.os.lstat", side_effect=lst):
            with self.assertRaises(WriteRefused) as cm:
                atomic_replace(snap, "A=2\n", self.state(), "m")
        self.assertIn("another user", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
