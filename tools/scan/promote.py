"""
Scan promotion -- stage 3 of the scan pipeline (discover -> VALIDATE -> promote).

``gaia scan`` (``tools/scan/classify.py``) discovers repos and writes the raw
``projects`` index. It NEVER touches ``project_context_contracts``. This module
is the DECOUPLED third stage: it reads what scan already persisted in the
``projects`` table and PROMOTES the scan-owned facts up into the
``project_identity`` project-context contract, so the SessionStart projects
block (``hooks/modules/session/session_manifest.py::build_projects_context_block``,
which reads ``project_context_contracts WHERE contract_name='project_identity'``)
reflects what was scanned -- without ever clobbering agent-authored enrichment.

Three properties this module guarantees:

  1. DECOUPLE. Promotion reads the ``projects`` table (the source of truth scan
     already wrote), NOT an in-memory ScanReport. So it is independently
     invocable -- ``promote_workspace(workspace)`` promotes whatever is already
     scanned, with or without a fresh scan. Discovery (classify.scan) stays a
     pure indexer; it does not import or call this module.

  2. GATE. :func:`validate_promotion` is the point where completeness/coherence
     is enforced BEFORE any write. A project row missing its resolvable
     identity (``project_identity``) or its on-disk ``path``, or not currently
     ``status='active'``, is rejected -- partial/corrupt rows never promote.
     This function is the extension seam: as scan gains intelligence, new gate
     rules are added HERE (see :data:`_HARD_RULES` / ``REQUIRE_REMOTE``).

  3. OWNERSHIP BOUNDARY. Promotion writes ONLY scan-owned keys
     (:data:`_ENTRY_SCAN_REFRESH`: local_path, remote_url, platform, language)
     and SEEDS name/type only when absent (:data:`_ENTRY_SCAN_SEED`). Every
     other key in an existing entry -- ``description`` and any curated
     structure (apps, package_manager, workspace_roots, ...) -- is agent-owned
     and preserved untouched. Scan-owned refresh is coalesce-or-omit (a NULL
     scan value never overwrites a curated value), mirroring the same rule the
     store writer enforces for the ``projects`` table
     (``gaia/store/writer.py::_present_fields`` + ``_PROJECTS_AGENT_OWNED``).

Reconciliation (requirement 4): a re-scan re-reads the current ``projects``
state and re-runs promotion. Because the merge is keyed on physical identity
(``local_path`` / normalized ``remote_url``), a rescanned project updates its
existing contract entry in place instead of duplicating, and agent-owned keys
survive across any number of rescans.

Public API::

    validate_promotion(workspace, *, db_path=None) -> dict
    promote_workspace(workspace, *, db_path=None, apply=True) -> dict
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CONTRACT_NAME = "project_identity"

# ---------------------------------------------------------------------------
# Ownership boundary for a project_identity contract ENTRY.
#
# Anchored to the projects-table ownership split in gaia/store/writer.py
# (_PROJECTS_AGENT_OWNED = {"description"}). Within a contract entry the
# agent-owned surface is broader (curated display name, type, description, and
# any structural keys the agent authored), so promotion touches only the small
# explicit scan-owned set below and preserves everything else.
# ---------------------------------------------------------------------------

# Always refreshed from the scan (coalesce-or-omit: only when the scan value is
# non-null, so a NULL never clobbers a curated value).
_ENTRY_SCAN_REFRESH = ("local_path", "remote_url", "platform", "language")

# Seeded from the scan ONLY when absent from an existing entry (an agent value,
# once present, is never overwritten).
_ENTRY_SCAN_SEED = ("name", "type")


# ---------------------------------------------------------------------------
# Gate policy (the extension seam -- add rules here as scan gains intelligence)
# ---------------------------------------------------------------------------

# HARD rules: a project row failing ANY of these is rejected (never promoted).
# Each entry is (reason_label, predicate(row) -> bool_is_ok).
_HARD_RULES = (
    ("missing project_identity", lambda r: bool(r.get("project_identity"))),
    ("missing path", lambda r: bool(r.get("path"))),
    ("path not absolute", lambda r: (not r.get("path")) or os.path.isabs(r["path"])),
)

# ADVISORY: promotion still proceeds but records the warning. Flip to True to
# make a present git remote a HARD requirement once scan reliably captures it
# (today many valid local repos legitimately have no origin remote, so keeping
# this advisory avoids blocking real data). This is the documented escalation
# point for requirement 2's "ruta y remote presentes".
REQUIRE_REMOTE = False


# ---------------------------------------------------------------------------
# DB helpers (mirror tools/scan/classify.py: never materialize the DB file on a
# read; a dry-run against a never-scanned workspace must touch nothing).
# ---------------------------------------------------------------------------

def _resolve_db_path(db_path: Optional[Path]) -> Path:
    if db_path is not None:
        return db_path
    from gaia.paths import db_path as _default_db_path
    return _default_db_path()


def _db_file_exists(db_path: Optional[Path]) -> bool:
    return _resolve_db_path(db_path).exists()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Stage 2: the validation gate
# ---------------------------------------------------------------------------

def validate_promotion(workspace: str, *, db_path: Optional[Path] = None) -> dict:
    """Gate the projects rows of ``workspace`` for promotion. Read-only.

    Returns a dict::

        {
          "workspace": str,
          "promotable": [ {name, path, remote_url, platform, primary_language,
                           role, project_identity, warnings: [...]}, ... ],
          "rejected":   [ {name, path, reasons: [...]}, ... ],
          "db_present": bool,
        }

    Only ``status='active'`` rows are considered -- a soft-deleted (missing)
    project is not re-promoted. Never raises; never creates the DB file.
    """
    result: dict = {
        "workspace": workspace,
        "promotable": [],
        "rejected": [],
        "db_present": False,
    }
    if not workspace or not _db_file_exists(db_path):
        return result
    result["db_present"] = True

    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT name, path, remote_url, platform, primary_language, role, "
            "project_identity FROM projects "
            "WHERE workspace = ? AND status = 'active' ORDER BY name",
            (workspace,),
        ).fetchall()
    finally:
        con.close()

    for row in rows:
        r = dict(row)
        reasons = [label for label, ok in _HARD_RULES if not ok(r)]
        if not r.get("remote_url"):
            if REQUIRE_REMOTE:
                reasons.append("missing remote_url")
        if reasons:
            result["rejected"].append(
                {"name": r.get("name"), "path": r.get("path"), "reasons": reasons}
            )
            continue
        warnings = [] if r.get("remote_url") else ["no remote_url (advisory)"]
        result["promotable"].append({**r, "warnings": warnings})

    return result


# ---------------------------------------------------------------------------
# Payload shape + merge helpers
# ---------------------------------------------------------------------------

def _classify_shape(payload: Optional[dict]) -> str:
    """Classify a project_identity payload as 'empty' | 'map' | 'flat'.

    Mirrors ``session_manifest._extract_projects_from_identity``'s inline
    ``is_map_shape`` test so promotion and the reader agree on the shape.
    """
    if not isinstance(payload, dict) or not payload:
        return "empty"
    is_map = (
        "name" not in payload
        and all(isinstance(v, dict) for v in payload.values())
        and any(
            ("local_path" in v or "name" in v)
            for v in payload.values()
            if isinstance(v, dict)
        )
    )
    return "map" if is_map else "flat"


def _scan_entry(proj: dict) -> dict:
    """Return the non-null scan-owned refresh keys for a project row.

    Coalesce-or-omit: a key is included ONLY when the scan produced a value,
    so refreshing an existing entry never overwrites a curated value with NULL.
    """
    out: dict = {}
    if proj.get("path"):
        out["local_path"] = proj["path"]
    if proj.get("remote_url"):
        out["remote_url"] = proj["remote_url"]
    if proj.get("platform"):
        out["platform"] = proj["platform"]
    if proj.get("primary_language"):
        out["language"] = proj["primary_language"]
    return out


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    return s or "project"


def _new_slug(name: str, used: set) -> str:
    base = _slugify(name)
    slug = base
    i = 2
    while slug in used:
        slug = f"{base}_{i}"
        i += 1
    return slug


def _normalize(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        from gaia.project import _normalize_remote
        return _normalize_remote(url) or None
    except Exception:
        return None


def _match_slug(existing_map: dict, proj: dict) -> Optional[str]:
    """Find the existing slug whose entry is the SAME physical repo as ``proj``.

    Match by absolute ``local_path`` first (strongest on-disk signal), then by
    normalized git remote. Returns None when no entry corresponds -- the caller
    then creates a new slug rather than risk merging two distinct repos.
    """
    proj_path = proj.get("path")
    proj_remote = _normalize(proj.get("remote_url"))
    for slug, entry in existing_map.items():
        if not isinstance(entry, dict):
            continue
        e_path = entry.get("local_path")
        if e_path and proj_path and os.path.normpath(e_path) == os.path.normpath(proj_path):
            return slug
        e_remote = _normalize(entry.get("remote_url"))
        if e_remote and proj_remote and e_remote == proj_remote:
            return slug
    return None


def _apply_scan_owned(entry: dict, proj: dict) -> bool:
    """Refresh scan-owned keys on ``entry`` in place; seed name/type if absent.

    Preserves every agent-owned key (description and any curated structure).
    Returns True when the entry changed.
    """
    before = copy.deepcopy(entry)
    entry.update(_scan_entry(proj))  # _ENTRY_SCAN_REFRESH (non-null only)
    if not entry.get("name") and proj.get("name"):
        entry["name"] = proj["name"]
    if not entry.get("type") and proj.get("role"):
        entry["type"] = proj["role"]
    return entry != before


def _merge_map(existing_map: dict, promotable: list) -> tuple[dict, dict]:
    """Merge scan-owned facts into a map-shape payload. Returns (payload, stats)."""
    result = copy.deepcopy(existing_map)
    used = set(result.keys())
    added = refreshed = 0
    for proj in promotable:
        slug = _match_slug(result, proj)
        if slug is None:
            slug = _new_slug(proj.get("name") or "", used)
            used.add(slug)
            entry: dict = {}
            _apply_scan_owned(entry, proj)  # seeds name/type + scan keys
            result[slug] = entry
            added += 1
        else:
            if _apply_scan_owned(result[slug], proj):
                refreshed += 1
    return result, {"added_entries": added, "refreshed_entries": refreshed}


def _merge_flat(existing: dict, proj: dict) -> tuple[dict, dict]:
    """Refresh scan-owned TOP-LEVEL keys on a flat single-project payload."""
    result = copy.deepcopy(existing)
    refreshed = 1 if _apply_scan_owned(result, proj) else 0
    return result, {"added_entries": 0, "refreshed_entries": refreshed}


# ---------------------------------------------------------------------------
# Contract read / write (reuses the store connection + the SQL history trigger)
# ---------------------------------------------------------------------------

def _read_identity_contract(workspace: str, db_path: Optional[Path]) -> Optional[dict]:
    if not _db_file_exists(db_path):
        return None
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace = ? AND contract_name = ?",
            (workspace, CONTRACT_NAME),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    try:
        data = json.loads(row["payload"] or "{}")
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _write_identity_contract(workspace: str, payload: dict, db_path: Optional[Path]) -> None:
    """Upsert the project_identity contract. The AFTER UPDATE trigger
    ``trg_pcc_history`` records the before/after payload automatically."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    now = _now_iso()
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, ?)",
            (workspace, workspace, now),
        )
        con.execute(
            "INSERT INTO project_context_contracts "
            "(workspace, contract_name, payload, metadata, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace, contract_name) DO UPDATE SET "
            "payload = excluded.payload, updated_at = excluded.updated_at",
            (workspace, CONTRACT_NAME, json.dumps(payload), None, now),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Stage 3: the promotion driver (validate gate -> merge -> write)
# ---------------------------------------------------------------------------

def promote_workspace(
    workspace: str,
    *,
    db_path: Optional[Path] = None,
    apply: bool = True,
) -> dict:
    """Promote scanned ``projects`` rows of ``workspace`` into the
    ``project_identity`` contract, merging scan-owned fields without clobbering
    agent-owned enrichment.

    Args:
        workspace: Workspace whose scanned projects to promote.
        db_path:   Optional explicit DB path (tests pass a temp DB).
        apply:     When False, preview only -- validate + compute the merged
                   payload, write nothing, materialize no DB file.

    Returns a structured, non-crashing report dict (see keys below). Never
    raises for a workspace with no promotable projects; returns a no-op report.
    """
    report: dict = {
        "workspace": workspace,
        "mode": "apply" if apply else "dry-run",
        "applied": False,
        "shape": None,
        "added_entries": 0,
        "refreshed_entries": 0,
        "rejected": [],
        "deferred": [],
        "warnings": [],
        "preview": None,
    }

    gate = validate_promotion(workspace, db_path=db_path)
    report["rejected"] = gate["rejected"]
    promotable = gate["promotable"]
    for p in promotable:
        for w in p.get("warnings", []):
            report["warnings"].append({"project": p.get("name"), "warning": w})

    if not promotable:
        return report

    existing = _read_identity_contract(workspace, db_path)
    shape = _classify_shape(existing)
    report["shape"] = shape

    if shape in ("map", "empty"):
        new_payload, stats = _merge_map(existing or {}, promotable)
    else:  # flat
        if len(promotable) == 1:
            new_payload, stats = _merge_flat(existing or {}, promotable[0])
        else:
            # Conservative: do NOT auto-convert a hand-authored flat
            # (single-project / workspace-identity) contract into a map. Defer
            # for human review rather than risk corrupting curated context.
            report["deferred"] = [
                {
                    "project": p.get("name"),
                    "path": p.get("path"),
                    "reason": (
                        "existing project_identity contract is flat "
                        "(single-project/workspace shape) but scan found "
                        f"{len(promotable)} promotable projects; map conversion "
                        "needs a human decision -- not auto-applied."
                    ),
                }
                for p in promotable
            ]
            return report

    report["added_entries"] = stats["added_entries"]
    report["refreshed_entries"] = stats["refreshed_entries"]
    report["preview"] = new_payload

    changed = stats["added_entries"] > 0 or stats["refreshed_entries"] > 0
    if apply and changed:
        _write_identity_contract(workspace, new_payload, db_path)
        report["applied"] = True

    return report
