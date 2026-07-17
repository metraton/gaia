"""
Cross-platform exclusive file locking for gaia hooks.

Provides a single context manager, ``exclusive_file_lock``, that serializes
writers across processes by holding an OS-level exclusive lock on a dedicated
lock file for the duration of the ``with`` block.

Why this module exists:
    ``fcntl`` is a POSIX-only stdlib module. Importing it at module scope on
    Windows raises ``ModuleNotFoundError`` at import time, which previously
    crashed any hook that (even transitively) imported the session context
    writer. The platform-specific backend is selected here, once, with the
    POSIX/Windows import guarded INSIDE the branch so that ``import fcntl``
    never executes on Windows and ``import msvcrt`` never executes on POSIX.

Backends:
    - POSIX:   ``fcntl.flock(fd, LOCK_EX)`` / ``LOCK_UN`` -- advisory,
               whole-file, blocking exclusive lock.
    - Windows: ``msvcrt.locking(fd, LK_LOCK, n)`` / ``LK_UNLCK`` -- mandatory,
               byte-range lock over a fixed region at offset 0. ``LK_LOCK``
               retries for ~10s then raises; we loop so the call blocks until
               the lock is acquired, matching the POSIX blocking semantics.

Public API:
    - exclusive_file_lock(lock_path)  (context manager)
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

# Size of the byte region locked on Windows. The lock file is dedicated to
# locking and never read, so a single byte at offset 0 is sufficient: mutual
# exclusion holds as long as every locker contends for the SAME region, which
# this module guarantees by always locking [0, _WINDOWS_LOCK_BYTES).
_WINDOWS_LOCK_BYTES = 1


if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI
    import msvcrt

    def _acquire(lock_file) -> None:
        lock_file.seek(0)
        # LK_LOCK blocks by retrying ~10 times over ~10s, then raises OSError.
        # Loop to preserve the POSIX "block until acquired" semantics.
        while True:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, _WINDOWS_LOCK_BYTES)
                return
            except OSError:
                continue

    def _release(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, _WINDOWS_LOCK_BYTES)

else:  # POSIX
    import fcntl

    def _acquire(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    def _release(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(lock_path: Union[str, Path]) -> Iterator[None]:
    """Hold an exclusive cross-process lock for the body of the ``with`` block.

    Opens (creating if absent) a dedicated lock file at ``lock_path`` and
    acquires a blocking exclusive lock on it. The lock is released and the file
    handle closed on exit, including on exception.

    The lock file is opened in append mode ("a") rather than truncating write
    mode: on Windows ``msvcrt`` locks are mandatory, so truncating a region a
    peer holds locked would raise. Append never touches the locked region, and
    the file's contents are irrelevant to exclusion, so this preserves the
    mutual-exclusion semantics on both platforms.

    Args:
        lock_path: Filesystem path used as the lock. Its contents are never
            read; only the OS lock on the handle matters.
    """
    lock_path = Path(lock_path)
    with open(lock_path, "a") as lock_file:
        _acquire(lock_file)
        try:
            yield
        finally:
            _release(lock_file)
