"""
gaia.evidence.store -- DB operations for the evidence three-tier storage model.

Layered atop gaia.store.writer._connect. Stores evidence rows in the
``evidence`` table introduced in v5 (extended by Plan B).

Storage modes (decision: EVIDENCE_INLINE_MAX_BYTES = 4096):
  * inline: text IS NOT NULL, artifact_path IS NULL -- payload fits in DB page.
  * blob:   artifact_path IS NOT NULL, text IS NULL -- payload lives in FS at
            <data_dir>/evidence/{workspace}/{brief_slug}/{ac_id}/{uuid}.{ext}
            (data_dir() is ~/.gaia unless overridden by GAIA_DATA_DIR).

Permission guard:
  * Curator identities (orchestrator / operator) may insert AND delete evidence.
  * Producer identities -- every declared agent that is not a curator, derived
    from the fleet by _evidence_producer_agents() -- may insert evidence for
    their own acceptance criteria but may never delete a row; see
    _assert_dispatch_can_write_evidence's allow_producers arg.
  * Read helpers (get_evidence, list_evidence_for_ac) are unrestricted.
  * Absence of GAIA_DISPATCH_AGENT (CLI caller) is always allowed.

Public API::

    insert_evidence(workspace, brief_id, ac_id, *, type, text=None,
                    artifact_path=None, size_bytes=None, task_id=None,
                    created_by_agent=None, db_path=None,
                    bypass_dispatch_guard=False) -> dict
    get_evidence(evidence_id, *, db_path=None) -> dict | None
    list_evidence_for_ac(brief_id, ac_id, *, db_path=None) -> list[dict]
    delete_evidence(evidence_id, *, db_path=None) -> bool
"""

from __future__ import annotations

import os
from pathlib import Path

from gaia.state.permissions import agent_fleet
from gaia.store.writer import _connect

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum payload size (bytes) stored inline in the DB. Payloads above this
# threshold are written to the filesystem and only the path is stored in DB.
# Chosen for SQLite B-tree page locality: rows <4KB stay within one page and
# avoid spilling to overflow pages, keeping DB scans fast.
EVIDENCE_INLINE_MAX_BYTES = 4096

# ---------------------------------------------------------------------------
# Permission guard
# ---------------------------------------------------------------------------

_EVIDENCE_CURATOR_AGENTS = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
})


def _evidence_producer_agents() -> frozenset[str]:
    """Specialist identities admitted to DEPOSIT (insert) evidence.

    DERIVED, never enumerated: the whole agent fleet (``agents/*.md``, via
    ``gaia.state.permissions.agent_fleet``) minus the curators. A producer
    deposits evidence for its own acceptance criteria without joining the
    curator set, and may never delete a row (see ``allow_producers`` below).

    The derivation is the guarantee. A hand-written membership list would
    admit whichever specialist happened to motivate the lane and silently
    exclude every specialist added afterwards -- so a new agent .md would
    reopen the same hole on the day it lands, with nothing to signal it. By
    subtracting the curators from the fleet instead, enrolling an agent is
    enrolling it: no edit here.

    This does NOT make the guard a no-op. Membership is bounded by the fleet,
    so an identity that is not a declared agent -- a typo, a stale name, an
    unregistered caller -- is still rejected, and the insert/delete asymmetry
    is untouched: no producer, derived or not, can delete.
    """
    return frozenset(agent_fleet() - _EVIDENCE_CURATOR_AGENTS)


class EvidenceWriteForbidden(PermissionError):
    """Raised when a subagent dispatch without authority for the requested
    operation attempts to write or delete evidence."""


def _assert_dispatch_can_write_evidence(*, allow_producers: bool = True) -> None:
    """Block evidence writes from subagent dispatches without authority.

    Reads ``GAIA_DISPATCH_AGENT`` from the environment:

    * Unset / empty string -> human CLI caller. Allowed.
    * Set to a curator identity -> allowed, for both insert and delete.
    * Set to a producer identity (``_evidence_producer_agents()``) -> allowed
      only when ``allow_producers`` is True. ``insert_evidence`` calls with
      the default (True); ``delete_evidence`` calls with ``allow_producers=
      False`` so a producer can deposit evidence it made but never delete
      any row.
    * Set to anything else -- including an identity that is not a declared
      agent at all -> raises ``EvidenceWriteForbidden``.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    allowed = _EVIDENCE_CURATOR_AGENTS
    if allow_producers:
        allowed = allowed | _evidence_producer_agents()
    if raw in allowed:
        return
    raise EvidenceWriteForbidden(
        f"Evidence writes are forbidden from this subagent dispatch "
        f"(current GAIA_DISPATCH_AGENT={raw!r}). "
        + (
            "Insert is open to any curator or to any declared agent under "
            "agents/; this identity is neither, so it is most likely a "
            "misspelled or unregistered agent name."
            if allow_producers
            else "Deletion is curator-only (orchestrator / operator): a "
                 "producer identity may insert its own evidence but never "
                 "delete a row."
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# insert_evidence
# ---------------------------------------------------------------------------

_VALID_EVIDENCE_TYPES = frozenset({
    "text", "file", "command_output", "url", "screenshot",
})


def insert_evidence(
    workspace: str,
    brief_id: int,
    ac_id: str,
    *,
    type: str,
    text: str | None = None,
    artifact_path: str | None = None,
    size_bytes: int | None = None,
    task_id: str | None = None,
    created_by_agent: str | None = None,
    db_path: Path | None = None,
    bypass_dispatch_guard: bool = False,
) -> dict:
    """Insert an evidence row. Returns the new row as a dict.

    Args:
        workspace:        Workspace slug (used for FK lookup; not stored in
                          evidence table directly).
        brief_id:         FK to briefs.id (must exist; CASCADE on delete).
        ac_id:            Acceptance-criteria identifier (TEXT, no FK).
        type:             Evidence type; one of VALID_EVIDENCE_TYPES.
        text:             Inline payload. Mutually exclusive with artifact_path.
        artifact_path:    Filesystem path to a blob. Mutually exclusive with text.
        size_bytes:       Payload size in bytes (caller-supplied; optional).
        task_id:          Opaque task reference (TEXT, no FK).
        created_by_agent: Agent slug that produced this evidence.
        db_path:          Optional explicit DB path (tests).
        bypass_dispatch_guard: When True, skip the GAIA_DISPATCH_AGENT permission
                          check. Use only from trusted hook-layer callers (e.g.
                          process_update_contracts in context_writer.py) where the
                          call site itself is the security boundary.

    Returns:
        dict with all columns of the inserted evidence row.

    Raises:
        EvidenceWriteForbidden: when dispatched from a subagent that is
                                neither a curator nor a producer identity
                                (only when bypass_dispatch_guard is False).
        ValueError: on type mismatch, missing payload, or mutex violation.
    """
    if not bypass_dispatch_guard:
        _assert_dispatch_can_write_evidence()

    if type not in _VALID_EVIDENCE_TYPES:
        raise ValueError(
            f"invalid evidence type {type!r}; must be one of "
            f"{sorted(_VALID_EVIDENCE_TYPES)}"
        )
    if text is not None and artifact_path is not None:
        raise ValueError(
            "text and artifact_path are mutually exclusive; supply at most one."
        )
    if text is None and artifact_path is None:
        raise ValueError(
            "one of text or artifact_path must be provided."
        )
    if not ac_id or not ac_id.strip():
        raise ValueError("ac_id cannot be empty")

    con = _connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO evidence
                (brief_id, ac_id, task_id, type, text, artifact_path,
                 size_bytes, created_by_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (brief_id, ac_id, task_id, type, text, artifact_path,
             size_bytes, created_by_agent),
        )
        con.commit()
        evidence_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = con.execute(
            "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# get_evidence
# ---------------------------------------------------------------------------

def get_evidence(
    evidence_id: int,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Return the evidence row for evidence_id, or None when not found."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# list_evidence_for_ac
# ---------------------------------------------------------------------------

def list_evidence_for_ac(
    brief_id: int,
    ac_id: str,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Return all evidence rows for a (brief_id, ac_id) pair, oldest first."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM evidence WHERE brief_id = ? AND ac_id = ? "
            "ORDER BY created_at ASC, id ASC",
            (brief_id, ac_id),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# delete_evidence
# ---------------------------------------------------------------------------

def delete_evidence(
    evidence_id: int,
    *,
    db_path: Path | None = None,
) -> bool:
    """Delete an evidence row and its associated filesystem blob (if any).

    Filesystem cleanup is performed in Python before the DB DELETE so that a
    failed blob removal does not silently leave an orphaned FS file.

    Returns True if a row was deleted, False if the id was not found.

    Raises:
        EvidenceWriteForbidden: when dispatched from a non-curator subagent
                                (a producer identity is not enough -- deletion
                                stays curator-only, see allow_producers).
    """
    _assert_dispatch_can_write_evidence(allow_producers=False)

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT artifact_path FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            return False

        # Clean up the FS blob before the DB row disappears.
        artifact_path = row["artifact_path"]
        if artifact_path:
            from gaia.evidence.fs import delete_blob
            delete_blob(artifact_path)

        cur = con.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()
