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
     is enforced BEFORE any write. An ``status='active'`` project row missing
     its resolvable identity (``project_identity``) or its on-disk ``path`` is
     rejected -- partial/corrupt rows never promote. This function is the
     extension seam: as scan gains intelligence, new gate rules are added HERE
     (see :data:`_HARD_RULES` / ``REQUIRE_REMOTE``). Rows the scan soft-deleted
     (``status='missing'``) are returned SEPARATELY, in ``missing``: they are
     not promotion candidates (the gate rules ask "is this row complete enough
     to write scan-owned facts FROM?", which a vanished repo cannot be), but
     their physical identity is what lets promotion find and mark the contract
     entry that outlived them.

  2b. VANISHED PROPAGATION. A repo that disappeared from disk is information,
     not noise: its contract entry is MARKED (``missing_since``) and stays
     visible rather than being deleted or hidden. The mark is cleared again the
     moment the repo reappears as promotable, mirroring the reactivation rule
     the store writer applies to the ``projects`` row itself
     (``mark_missing_in`` sets ``status='missing'`` + ``missing_since``; an
     upsert with ``missing_since=None`` reactivates).

  2c. ATOMICITY. The contract row has a SECOND writer -- an agent's
     ``update_contracts`` delta, deep-merged by
     ``hooks/modules/context/context_writer.apply_update`` -- so promotion's
     read-merge-write runs inside one ``BEGIN IMMEDIATE`` transaction
     (:func:`_merge_and_write_atomically`). Without that, an agent merge landing
     between the read and the write is silently overwritten: the ownership
     boundary below is a policy about WHICH keys are rewritten, and it only
     preserves the other writer's keys if the value being merged into is the
     current one.

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
import sqlite3
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
          "missing":    [ {name, path, remote_url, missing_since}, ... ],
          "db_present": bool,
        }

    Only ``status='active'`` rows are promotion candidates. Soft-deleted rows
    (``status='missing'``, written by ``mark_missing_in`` during the scan's own
    reconciliation) are returned in ``missing`` WITHOUT passing through
    :data:`_HARD_RULES` -- they are not being promoted, so completeness rules
    that gate a write do not apply; only their physical identity (path/remote)
    is used, to find the contract entry to mark. Never raises; never creates
    the DB file.
    """
    result: dict = {
        "workspace": workspace,
        "promotable": [],
        "rejected": [],
        "missing": [],
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
        missing_rows = con.execute(
            "SELECT name, path, remote_url, missing_since FROM projects "
            "WHERE workspace = ? AND status = 'missing' ORDER BY name",
            (workspace,),
        ).fetchall()
    finally:
        con.close()

    result["missing"] = [dict(r) for r in missing_rows]

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

    Reserved slugs are skipped: the ``_``-prefixed slot holds workspace-level
    metadata (see :func:`_auto_convert_to_map`), which can legitimately carry a
    ``local_path`` of its own and must never be mistaken for a project entry.
    """
    from gaia.identity_shape import is_reserved_slug

    proj_path = proj.get("path")
    proj_remote = _normalize(proj.get("remote_url"))
    for slug, entry in existing_map.items():
        if is_reserved_slug(slug) or not isinstance(entry, dict):
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
    A promotable project is on disk again, so any ``missing_since`` mark left
    by a previous scan is dropped -- the reappearance side of requirement 2b.
    Returns True when the entry changed.
    """
    from gaia.identity_shape import MISSING_MARK_KEY

    before = copy.deepcopy(entry)
    entry.update(_scan_entry(proj))  # _ENTRY_SCAN_REFRESH (non-null only)
    if not entry.get("name") and proj.get("name"):
        entry["name"] = proj["name"]
    if not entry.get("type") and proj.get("role"):
        entry["type"] = proj["role"]
    entry.pop(MISSING_MARK_KEY, None)
    return entry != before


def _mark_missing(result_map: dict, missing: list) -> int:
    """Stamp :data:`MISSING_MARK_KEY` on the entries whose repo vanished.

    The entry is kept, with every other key untouched -- a vanished repo is
    information, and hiding it would repeat the silence this propagation
    exists to end. Only entries that ACTUALLY change are counted, so a second
    scan over a still-missing repo is a no-op rather than a phantom write.
    """
    from gaia.identity_shape import MISSING_MARK_KEY

    marked = 0
    for row in missing:
        slug = _match_slug(result_map, row)
        if slug is None:
            continue
        entry = result_map[slug]
        stamp = row.get("missing_since") or _now_iso()
        if entry.get(MISSING_MARK_KEY) == stamp:
            continue
        entry[MISSING_MARK_KEY] = stamp
        marked += 1
    return marked


def _merge_map(existing_map: dict, promotable: list, missing: list = ()) -> tuple[dict, dict]:
    """Merge scan-owned facts into a map-shape payload. Returns (payload, stats).

    Three branches, in order: create an entry for a newly-seen project, refresh
    the scan-owned keys of one already there, and mark the entries whose repo
    vanished. Marking runs LAST so a project that both reappeared and is stale
    in ``missing`` resolves to present.
    """
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
    marked = _mark_missing(result, list(missing))
    return result, {
        "added_entries": added,
        "refreshed_entries": refreshed,
        "marked_missing_entries": marked,
    }


def _merge_flat(existing: dict, proj: dict) -> tuple[dict, dict]:
    """Refresh scan-owned TOP-LEVEL keys on a flat single-project payload.

    A flat payload has no per-project entry to mark, so vanished propagation
    does not apply on this branch (it is reached only with exactly one
    promotable project -- i.e. one that is present on disk).
    """
    result = copy.deepcopy(existing)
    refreshed = 1 if _apply_scan_owned(result, proj) else 0
    return result, {
        "added_entries": 0,
        "refreshed_entries": refreshed,
        "marked_missing_entries": 0,
    }


def _auto_convert_to_map(
    existing: Optional[dict], promotable: list, missing: list = ()
) -> tuple[dict, dict]:
    """Convert a non-map (flat multi / scanner) payload into a map, losslessly.

    Builds a fresh map from ``promotable`` via :func:`_merge_map`. The old
    top-level metadata of the source payload is preserved verbatim under the
    reserved :data:`WORKSPACE_META_KEY` slug so hand-authored workspace-level
    data (name, identity, workspace_repos, monorepo, ...) is never lost -- the
    reader treats a ``_``-prefixed slug as a non-project reserved slot.
    """
    from gaia.identity_shape import WORKSPACE_META_KEY

    seed: dict = {}
    if existing:
        seed[WORKSPACE_META_KEY] = copy.deepcopy(existing)
    return _merge_map(seed, promotable, missing)


# ---------------------------------------------------------------------------
# Contract read / write (reuses the store connection + the SQL history trigger)
# ---------------------------------------------------------------------------

def _select_identity_payload(con, workspace: str) -> Optional[dict]:
    """Read the stored payload on an ALREADY-OPEN connection.

    Returns None when there is no row, or when the stored value is not a JSON
    object -- neither carries keys to preserve, so the caller merges into {}.
    """
    row = con.execute(
        "SELECT payload FROM project_context_contracts "
        "WHERE workspace = ? AND contract_name = ?",
        (workspace, CONTRACT_NAME),
    ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["payload"] or "{}")
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _upsert_identity_payload(con, workspace: str, payload: dict) -> None:
    """Upsert the project_identity contract on an ALREADY-OPEN connection.

    The AFTER UPDATE trigger ``trg_pcc_history`` records the before/after
    payload automatically.
    """
    now = _now_iso()
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


def _read_identity_contract(workspace: str, db_path: Optional[Path]) -> Optional[dict]:
    """Read the contract in its own short-lived connection (preview path only).

    A dry-run never writes, so it needs no write lock -- and it must not
    materialize the DB file for a workspace that was never scanned.
    """
    if not _db_file_exists(db_path):
        return None
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        return _select_identity_payload(con, workspace)
    finally:
        con.close()


def _merge_for_shape(
    existing: Optional[dict], promotable: list, missing: list
) -> tuple[str, Optional[dict], Optional[dict]]:
    """Dispatch the merge on the stored payload's shape. Pure -- no I/O.

    Returns ``(shape, payload, stats)``, with payload and stats None when the
    shape dictates that nothing promotes at all.
    """
    from gaia.identity_shape import classify_identity_shape

    shape = classify_identity_shape(existing)

    if shape in ("map", "empty"):
        payload, stats = _merge_map(existing or {}, promotable, missing)
    elif not promotable:
        # Flat / scanner shape with nothing on disk to promote: converting it
        # on the strength of vanished rows alone would rewrite a hand-authored
        # payload with no project entry to show for it.
        return shape, None, None
    elif shape == "flat" and len(promotable) == 1:
        # P2 (parked): a single-project flat / workspace-identity contract keeps
        # its top-level shape -- refresh scan-owned keys in place, no conversion.
        payload, stats = _merge_flat(existing or {}, promotable[0])
    else:
        # Flat with >1 promotable, OR a scanner (workspace_repos) shape: convert
        # to a map so every project promotes cleanly, preserving the old
        # top-level metadata under the reserved workspace key. A scanner shape is
        # routed here (never through _merge_flat) precisely so its structured
        # payload is preserved intact instead of being clobbered with top-level
        # scan-owned keys.
        payload, stats = _auto_convert_to_map(existing, promotable, missing)
    return shape, payload, stats


def _stats_changed(stats: Optional[dict]) -> bool:
    if not stats:
        return False
    return (
        stats["added_entries"] > 0
        or stats["refreshed_entries"] > 0
        or stats["marked_missing_entries"] > 0
    )


def _merge_and_write_atomically(
    workspace: str, promotable: list, missing: list, db_path: Optional[Path]
) -> tuple[str, Optional[dict], Optional[dict], bool]:
    """Read, merge and write the contract inside ONE ``BEGIN IMMEDIATE``.

    Holding the write lock from the SELECT through the UPSERT is what makes the
    ownership boundary hold under concurrency, and it is not optional. This row
    has a second writer -- ``hooks/modules/context/context_writer.apply_update``,
    which deep-merges an agent's ``update_contracts`` delta into the SAME
    section -- and both sides finish with ``payload = excluded.payload``. So a
    read taken outside the transaction lets any agent merge that lands between
    it and the UPSERT be overwritten with a payload computed before that merge
    existed: the agent's contribution disappears with no error on either side.
    Serializing the two read-modify-writes is the only thing that makes the
    result order-independent, containing both the fresh scan-owned keys and the
    agent-owned keys that were present.

    Returns ``(shape, payload, stats, wrote)``. Nothing is written when the
    shape yields no payload or when the merge produced no change, so an
    idempotent re-promotion still touches no row.
    """
    from gaia.store.writer import _connect

    con = _connect(db_path)
    con.isolation_level = None  # explicit transaction control, no implicit BEGIN
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = _select_identity_payload(con, workspace)
        shape, payload, stats = _merge_for_shape(existing, promotable, missing)
        if payload is None or not _stats_changed(stats):
            con.execute("ROLLBACK")
            return shape, payload, stats, False
        _upsert_identity_payload(con, workspace, payload)
        con.execute("COMMIT")
        return shape, payload, stats, True
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
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

    On ``apply``, the read-merge-write goes through
    :func:`_merge_and_write_atomically` so a concurrent agent merge into the same
    row cannot be lost. A preview reads outside any transaction: it writes
    nothing, so it has nothing to serialize against.

    Args:
        workspace: Workspace whose scanned projects to promote.
        db_path:   Optional explicit DB path (tests pass a temp DB).
        apply:     When False, preview only -- validate + compute the merged
                   payload, write nothing, materialize no DB file.

    Returns a structured, non-crashing report dict (see keys below). Never
    raises for a workspace with no promotable projects; returns a no-op report.

    ``outcome`` disambiguates the three distinct situations that all leave
    ``applied=False``, which the flag alone cannot tell apart:

      * ``"applied"``           -- the contract was written.
      * ``"dry-run"``           -- there WAS a change to make; nothing written
                                   because ``apply=False``.
      * ``"nothing-promotable"``-- the gate yielded no candidate and no
                                   vanished entry; the merge never ran.
      * ``"no-op"``             -- the merge ran and the contract already
                                   matched; idempotent, nothing to write.
    """
    report: dict = {
        "workspace": workspace,
        "mode": "apply" if apply else "dry-run",
        "applied": False,
        "outcome": "nothing-promotable",
        "shape": None,
        "added_entries": 0,
        "refreshed_entries": 0,
        "marked_missing_entries": 0,
        "rejected": [],
        "warnings": [],
        "preview": None,
    }

    gate = validate_promotion(workspace, db_path=db_path)
    report["rejected"] = gate["rejected"]
    promotable = gate["promotable"]
    missing = gate["missing"]
    for p in promotable:
        for w in p.get("warnings", []):
            report["warnings"].append({"project": p.get("name"), "warning": w})

    if not promotable and not missing:
        return report

    if apply:
        shape, new_payload, stats, wrote = _merge_and_write_atomically(
            workspace, promotable, missing, db_path
        )
    else:
        shape, new_payload, stats = _merge_for_shape(
            _read_identity_contract(workspace, db_path), promotable, missing
        )
        wrote = False
    report["shape"] = shape

    if new_payload is None:
        return report

    report["added_entries"] = stats["added_entries"]
    report["refreshed_entries"] = stats["refreshed_entries"]
    report["marked_missing_entries"] = stats["marked_missing_entries"]
    report["preview"] = new_payload

    if not _stats_changed(stats):
        report["outcome"] = "no-op"
    elif wrote:
        report["applied"] = True
        report["outcome"] = "applied"
    else:
        report["outcome"] = "dry-run"

    return report
