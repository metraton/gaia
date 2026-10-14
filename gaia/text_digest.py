"""Content digest of a text file that is independent of its line endings."""

from __future__ import annotations

import hashlib
from pathlib import Path


def text_sha256(path: Path) -> str:
    """SHA-256 hex of *path* with CRLF folded to LF, so a Windows checkout hashes like its LF package."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
