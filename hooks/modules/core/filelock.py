"""
Cross-platform exclusive file locking for gaia hooks.

``exclusive_file_lock`` serializes writers across processes by holding an
OS-level exclusive lock on a dedicated lock file for the ``with`` block.

``fcntl`` does not exist on Windows, so each backend's import stays inside its
platform branch; importing it at module scope crashed every hook that reached
the session context writer.

Backends, both polled without blocking until the wait bound expires:
    - POSIX:   ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` -- advisory, whole-file.
    - Windows: ``msvcrt.locking(fd, LK_NBLCK, 1)`` -- mandatory, byte-range lock
               at offset 0; mutual exclusion holds because every locker
               contends for that same byte.
"""

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

# A peer that dies or hangs while holding the lock must not hang every hook
# behind it; hooks run under the host's own timeout, well above this bound.
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.05


class LockTimeout(TimeoutError):
    """The lock stayed held by another process for the whole wait bound."""


if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI
    import msvcrt

    def _try_acquire(lock_file) -> bool:
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _release(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

else:  # POSIX
    import fcntl

    def _try_acquire(lock_file) -> bool:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _release(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    lock_path: Union[str, Path], timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Hold an exclusive cross-process lock on *lock_path* for the ``with`` block.

    Raises ``LockTimeout`` without running the body when the lock is not
    acquired within *timeout* seconds.

    The lock file is opened in append mode: on Windows the lock is mandatory,
    so truncating a region a peer holds would raise.
    """
    deadline = time.monotonic() + timeout
    with open(Path(lock_path), "a") as lock_file:
        while not _try_acquire(lock_file):
            if time.monotonic() >= deadline:
                raise LockTimeout(f"{lock_path} still locked after {timeout:g}s")
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            _release(lock_file)
