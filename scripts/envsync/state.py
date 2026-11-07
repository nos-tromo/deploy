"""Private state directory, backups and the only code that writes a .env."""

from __future__ import annotations

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import FrozenSet, Iterable, Iterator, List, Optional, Tuple

BACKUPS_KEPT = 5
_BACKUP_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:-(\d+))?\.env$")


class StateError(Exception):
    """The state directory is unusable."""


class LockHeld(Exception):
    """Another run holds the state lock."""


class WriteRefused(Exception):
    """A .env must not be written; the message is a fixed reason."""


@dataclass
class Snapshot:
    path: Path
    data: Optional[bytes]
    stat: Optional[os.stat_result]


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(str(path))
        except OSError:
            pass
        raise


class StateDir:
    def __init__(self, root: Path, create: bool) -> None:
        self.root = Path(root)
        if self.root.is_symlink():
            raise StateError("state directory is a symlink")
        if create:
            for d in (self.root, self.root / "base", self.root / "backup", self.root / "tmp"):
                if d.is_symlink():
                    raise StateError("state directory is a symlink")
                try:
                    d.mkdir(mode=0o700, exist_ok=True)
                    if stat.S_IMODE(os.lstat(d).st_mode) & 0o077:
                        os.chmod(d, 0o700)
                except OSError as exc:
                    raise StateError(f"cannot create {d}: {exc.strerror}") from None

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            fd = os.open(str(self.root / "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise StateError(f"cannot open lock: {exc.strerror}") from None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise LockHeld("another env-sync run holds the lock") from None
            yield
        finally:
            os.close(fd)

    def _atomic_private(self, dest: Path, data: bytes) -> None:
        tmp = self.root / "tmp" / f"{dest.name}.{os.getpid()}.tmp"
        try:
            _write_private(tmp, data)
            os.replace(str(tmp), str(dest))
        except BaseException:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise

    def read_base(self, file_id: str) -> Optional[str]:
        try:
            return (self.root / "base" / f"{file_id}.example").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_base(self, file_id: str, text: str) -> None:
        self._atomic_private(self.root / "base" / f"{file_id}.example", text.encode("utf-8"))

    def read_placeholders(self, file_id: str) -> FrozenSet[str]:
        try:
            text = (self.root / "base" / f"{file_id}.placeholders").read_text(encoding="utf-8")
        except FileNotFoundError:
            return frozenset()
        return frozenset(line for line in text.split("\n") if line)

    def write_placeholders(self, file_id: str, values: Iterable[str]) -> None:
        keep = sorted({v for v in values if v and "\n" not in v and "\r" not in v})
        text = "".join(v + "\n" for v in keep)
        self._atomic_private(self.root / "base" / f"{file_id}.placeholders", text.encode("utf-8"))

    def backup(self, file_id: str, data: bytes, now: Optional[datetime] = None) -> Path:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        folder = self.root / "backup" / file_id
        folder.mkdir(mode=0o700, exist_ok=True)
        taken = [
            int(m.group(2) or 0)
            for m in map(_BACKUP_RE.match, os.listdir(folder))
            if m and m.group(1) == stamp
        ]
        n = max(taken) + 1 if taken else 0
        while True:
            name = f"{stamp}.env" if n == 0 else f"{stamp}-{n}.env"
            try:
                _write_private(folder / name, data)
                break
            except FileExistsError:
                n += 1
        self._prune(folder)
        return folder / name

    def _prune(self, folder: Path) -> None:
        found: List[Tuple[str, int, str]] = []
        for entry in os.listdir(folder):
            m = _BACKUP_RE.match(entry)
            if m:
                found.append((m.group(1), int(m.group(2) or 0), entry))
        found.sort()
        for _, _, entry in found[:-BACKUPS_KEPT]:
            os.unlink(str(folder / entry))


def read_target(path: Path) -> Snapshot:
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return Snapshot(path, None, None)
    if stat.S_ISLNK(st.st_mode):
        raise WriteRefused(f"{path}: is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise WriteRefused(f"{path}: not a regular file")
    if st.st_nlink > 1:
        raise WriteRefused(f"{path}: has hard links")
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as fh:
            data = fh.read()
    except OSError:
        raise WriteRefused(f"{path}: cannot be read") from None
    return Snapshot(path, data, st)


def world_readable(snap: Snapshot) -> bool:
    return snap.stat is not None and bool(stat.S_IMODE(snap.stat.st_mode) & 0o004)


def atomic_replace(snap: Snapshot, text: str, state: StateDir, file_id: str) -> None:
    path = snap.path
    now = read_target(path)
    if now.data != snap.data:
        raise WriteRefused(f"{path}: changed while syncing")
    euid = os.geteuid()
    if now.stat is not None and euid != 0 and now.stat.st_uid != euid:
        raise WriteRefused(f"{path}: owned by another user")
    tmp_dir = state.root / "tmp"
    if os.stat(tmp_dir).st_dev != os.stat(path.parent).st_dev:
        raise WriteRefused(f"{path}: state directory on another filesystem")
    want_mode = stat.S_IMODE(now.stat.st_mode) if now.stat is not None else 0o600
    tmp = tmp_dir / f"{file_id}.{os.getpid()}.tmp"
    try:
        _write_private(tmp, text.encode("utf-8"))
        os.chmod(tmp, want_mode)
        if euid == 0 and now.stat is not None:
            os.chown(tmp, now.stat.st_uid, now.stat.st_gid)
        os.replace(str(tmp), str(path))
        written = os.lstat(path)
        if (
            stat.S_IMODE(written.st_mode) != want_mode
            or path.read_bytes() != text.encode("utf-8")
        ):
            raise WriteRefused(f"{path}: written file does not match")
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
