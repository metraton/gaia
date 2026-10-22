"""A subagent's Read, Grep and Glob paths, appended to a per-harness-id file with no database connection and merged into files_checked at finalize."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import List

MAX_SPOOL_BYTES = 256 * 1024
MAX_LINE_BYTES = 4096
STALE_AFTER_SECONDS = 7 * 24 * 3600

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _spool_dir() -> Path:
    from gaia.paths.resolver import state_dir

    return state_dir() / "read_spool"


def spool_path(key: str) -> Path:
    name = key if _SAFE_KEY.match(key) else hashlib.sha256(key.encode()).hexdigest()[:32]
    return _spool_dir() / f"{name}.jsonl"


def append(key: str, tool: str, path: str) -> bool:
    """Write one line with a single O_APPEND write, so parallel tool calls never interleave; False when skipped, never raises."""
    try:
        line = (json.dumps({"tool": tool, "path": path}) + "\n").encode()
        if len(line) > MAX_LINE_BYTES:
            return False
        target = spool_path(key)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        try:
            fd = os.open(target, flags, 0o600)
        except FileNotFoundError:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(target, flags, 0o600)
        try:
            if os.fstat(fd).st_size >= MAX_SPOOL_BYTES:
                return False
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError, TypeError):
        return False


def read(key: str) -> List[str]:
    """The spooled paths in call order, without repeats; a torn or foreign line is skipped."""
    try:
        raw = spool_path(key).read_bytes()
    except OSError:
        return []
    paths: List[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            path = json.loads(line).get("path")
        except (ValueError, AttributeError):
            continue
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def discard(key: str) -> None:
    """Delete the key's spool, and any spool left by a turn that was cut and never rescued."""
    try:
        spool_path(key).unlink()
    except OSError:
        pass
    try:
        cutoff = time.time() - STALE_AFTER_SECONDS
        for entry in _spool_dir().iterdir():
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
    except OSError:
        pass
