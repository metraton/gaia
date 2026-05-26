"""
gaia.evidence.fs -- Filesystem blob storage for the evidence layer.

Layout: ~/.gaia/evidence/{workspace}/{brief_slug}/{ac_id}/{uuid4}.{ext}

This module does NOT enforce the permission guard: writes to the filesystem
always accompany an insert_evidence() call that already applied the guard.

Public API::

    blob_path_for(workspace, brief_slug, ac_id, uuid_str, ext) -> Path
    write_blob(workspace, brief_slug, ac_id, data, *, ext=".bin") -> tuple[Path, int]
    read_blob(artifact_path) -> bytes | None
    delete_blob(artifact_path) -> bool
"""

from __future__ import annotations

import uuid
from pathlib import Path


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

def _evidence_root() -> Path:
    """Return the root directory for evidence blobs: ~/.gaia/evidence/."""
    return Path.home() / ".gaia" / "evidence"


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------

def blob_path_for(
    workspace: str,
    brief_slug: str,
    ac_id: str,
    uuid_str: str,
    ext: str,
) -> Path:
    """Build the canonical filesystem path for an evidence blob.

    Creates all parent directories (parents=True, exist_ok=True) so the
    caller can immediately open the path for writing.

    Args:
        workspace:   Workspace slug (e.g. "me").
        brief_slug:  Brief name/slug (e.g. "evidence-three-tier-storage").
        ac_id:       Acceptance-criteria identifier (e.g. "AC-1").
        uuid_str:    UUID4 string without hyphens or with (str(uuid.uuid4())).
        ext:         File extension including the leading dot (e.g. ".txt").

    Returns:
        Absolute Path to ~/.gaia/evidence/{workspace}/{brief_slug}/{ac_id}/{uuid}{ext}.
    """
    # Normalize ext
    if ext and not ext.startswith("."):
        ext = "." + ext

    path = _evidence_root() / workspace / brief_slug / ac_id / f"{uuid_str}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_blob(
    workspace: str,
    brief_slug: str,
    ac_id: str,
    data: bytes,
    *,
    ext: str = ".bin",
) -> tuple[Path, int]:
    """Write ``data`` to a new UUID4-named blob file.

    Args:
        workspace:   Workspace slug.
        brief_slug:  Brief name/slug.
        ac_id:       AC identifier.
        data:        Raw bytes to write.
        ext:         File extension (default ".bin"; use ".txt" for text data).

    Returns:
        ``(path, size_bytes)`` where path is the absolute Path written and
        size_bytes is ``len(data)``.
    """
    uuid_str = str(uuid.uuid4())
    path = blob_path_for(workspace, brief_slug, ac_id, uuid_str, ext)
    path.write_bytes(data)
    return path, len(data)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_blob(artifact_path: str) -> bytes | None:
    """Read a blob file. Returns None when the path does not exist."""
    p = Path(artifact_path)
    if not p.exists():
        return None
    return p.read_bytes()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_blob(artifact_path: str) -> bool:
    """Delete a blob file. Returns True on success, False when not found."""
    p = Path(artifact_path)
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False
