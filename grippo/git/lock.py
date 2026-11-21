"""Per-repository readers/writer lock shared by threads *and* processes.

Implemented with ``flock`` on a file under ``~/.local/state/grippo/locks`` keyed by the
repository's common git dir (so linked worktrees of the same repository serialize, while
different repositories never block each other). Each acquisition opens its own file
description, so the lock works between threads of one process as well.

Nesting in one thread is re-entrant for the same mode; upgrading shared→exclusive is
refused (it is the classic self-deadlock).
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from grippo.errors import LockTimeoutError
from grippo.git.repo import Repository


class RepoLocks:
    def __init__(self, locks_dir: Path, timeout: float = 30.0) -> None:
        self.locks_dir = locks_dir
        self.timeout = timeout
        self._held = threading.local()

    def _path(self, repo: Repository) -> Path:
        key = hashlib.sha256(str(repo.common_dir).encode()).hexdigest()[:20]
        return self.locks_dir / f"{key}.lock"

    def _held_modes(self) -> dict[Path, list[int]]:
        if not hasattr(self._held, "modes"):
            self._held.modes = {}
        modes: dict[Path, list[int]] = self._held.modes
        return modes

    @contextmanager
    def _acquire(self, repo: Repository, mode: int, timeout: float | None) -> Iterator[None]:
        path = self._path(repo)
        held = self._held_modes().setdefault(path, [])
        if held:
            if mode == fcntl.LOCK_EX and fcntl.LOCK_EX not in held:
                raise RuntimeError("upgrade de lock compartilhado para exclusivo não permitido")
            held.append(mode)
            try:
                yield
            finally:
                held.pop()
            return
        self.locks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        try:
            while True:
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"outra operação GRIPPO está em andamento em {repo.root}"
                        ) from None
                    time.sleep(0.05)
            held.append(mode)
            try:
                yield
            finally:
                held.pop()
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def shared(self, repo: Repository, timeout: float | None = None):  # type: ignore[no-untyped-def]
        return self._acquire(repo, fcntl.LOCK_SH, timeout)

    def exclusive(self, repo: Repository, timeout: float | None = None):  # type: ignore[no-untyped-def]
        return self._acquire(repo, fcntl.LOCK_EX, timeout)
