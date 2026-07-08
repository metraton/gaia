"""
gaia.store.writer -- CRUD API for the Gaia SQLite substrate.

The writer is the only authorized path to mutate `~/.gaia/gaia.db`. Every
mutation consults `agent_permissions(table_name, agent_name, allow_write)`
before touching data. If the (table, agent) pair is missing or has
``allow_write=0``, the operation returns ``{"status": "rejected",
"reason": "not_authorized"}`` without modifying the DB.

Vocabulary:
  * ``workspaces`` table -- organizational containers (e.g. "me", "bildwiz").
  * ``projects`` table  -- git-bearing source projects within a workspace.
  * Column ``workspace`` -- FK to workspaces.name.
  * Column ``project``   -- FK to projects(workspace, name).

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram. See NOTICE.md.

Public API::

    upsert_project(workspace, name, fields, agent, topic_key=None) -> dict
    upsert_app(workspace, project, name, fields, agent, topic_key=None) -> dict
    delete_missing_in(table, workspace, surviving_keys) -> int
    bulk_upsert(table, workspace, rows, agent) -> dict
    wipe_workspace(workspace) -> None
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Schema file lives alongside this module
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Tables we recognize (whitelist for delete_missing_in / bulk_upsert)
_KNOWN_TABLES = {
    "workspaces",
    "projects",
    "apps",
    "libraries",
    "services",
    "features",
    "project_facets",
    "tf_modules",
    "tf_live",
    "releases",
    "workloads",
    "clusters_defined",
    "clusters",
    "integrations",
    "gaia_installations",
    "machines",
}


# ---------------------------------------------------------------------------
# Semantic-grant lifetime (approvals redesign, M1)
# ---------------------------------------------------------------------------
#
# APPROVAL_GRANT_TTL_MINUTES is the default lifetime of an ACTIVE semantic grant
# -- the window in which an already-approved command may be retried and consumed.
# It is consumed by insert_semantic_grant() here and by the hooks-layer grant
# default (DEFAULT_GRANT_TTL_MINUTES in modules/security/approval_grants.py).
#
# It is DELIBERATELY a distinct concept from DEFAULT_PENDING_TTL_MINUTES (1440 /
# 24h), which is how long an UNANSWERED approval waits for the user. The two must
# not be conflated: a 24h pending window lets a human come back the next day,
# while the grant window is the short, post-approval execution horizon. Collapsing
# them would either shrink the approval wait to 5m (a regression) or stretch the
# grant lifetime to 24h (a security weakening). See the regression guards in
# tests/hooks/test_pending_scanner_cleanup.py::TestTTLConstants.
#
# The value is 5 minutes (approvals redesign, M1). The grant is consumed AT THE
# MATCH (bash_validator flips the row PENDING->CONSUMED when it authorizes the
# command in PreToolUse, before execution), so this short window only needs to
# cover the block -> approve -> retry round trip; a grant that is never presented
# to a matching retry simply expires. Replay protection comes from consume-at-
# match plus this short TTL, not from a long-lived grant.
#
# It lives HERE, in gaia.store.writer, because writer is the dependency leaf of
# the approval planes: gaia.approvals.store already imports from this module
# (_connect) and the hooks approval_grants module already imports
# insert_semantic_grant from here, while writer imports neither -- so any consumer
# can read this constant without a circular import.
APPROVAL_GRANT_TTL_MINUTES = 5


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    """Resolve the DB path via gaia.paths (B0). Imported lazily to avoid
    side effects at import time."""
    from gaia.paths import db_path
    return db_path()


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, ensuring the schema is materialized.

    Args:
        db_path: Optional explicit DB path (used by tests). When None,
            resolves via ``gaia.paths.db_path()``.

    Returns:
        Open sqlite3.Connection with foreign_keys=ON.
    """
    if db_path is None:
        db_path = _db_path()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not db_path.exists()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    # Register gaia_sha256: scalar function used by the ai_approval_events_hash
    # trigger to compute this_hash = SHA-256(prev_hash || fingerprint).
    # SQLite does not include SHA-256 built-in; we inject it as a Python function
    # at connection time. All connections opened via _connect() get this function,
    # which means the trigger fires correctly on any INSERT into approval_events.
    # The function accepts a single TEXT argument and returns the hex digest.
    def _gaia_sha256(value: str | None) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()

    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)

    if fresh:
        con.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        con.commit()
    return con


def _now_iso() -> str:
    """Return current UTC time as ISO8601 (Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Permission enforcement
# ---------------------------------------------------------------------------

def _is_authorized(con: sqlite3.Connection, table_name: str, agent: str) -> bool:
    """Return True iff (table_name, agent) has allow_write=1."""
    row = con.execute(
        "SELECT allow_write FROM agent_permissions WHERE table_name = ? AND agent_name = ?",
        (table_name, agent),
    ).fetchone()
    if row is None:
        return False
    return bool(row[0])


def _rejected(reason: str = "not_authorized") -> dict:
    return {"status": "rejected", "reason": reason}


def _applied(extra: dict | None = None) -> dict:
    out = {"status": "applied"}
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Identity resolution (workspaces.identity)
# ---------------------------------------------------------------------------

def _resolve_identity(workspace: str, workspace_path: Path | None = None) -> str:
    """Resolve workspace identity -- REMOTE-derived, read directly (M2-T7).

    Rule:
      * If ``workspace_path`` is provided AND ``workspace_path / .git`` exists
        (the workspace root is itself a git project), resolve identity from
        the git remote of that directory, read DIRECTLY via
        ``gaia.project._git_remote_origin`` + ``_normalize_remote``.
      * Otherwise (organizational workspace -- no .git at the root), the
        identity IS the workspace name. We do NOT leak the remote of a child
        project up to the workspace row.

    This deliberately does NOT go through ``gaia.project.current()``. As of
    M2-T7 (AC-9) ``current()`` is PATH-based (it answers "which workspace am I
    in" by disk location, not by remote). The ``workspaces.identity`` column,
    however, must remain the normalized git remote (``host/owner/repo``) so two
    clones of the same remote collapse to the same identity row (the B0 design
    in ``tools/scan/store_populator.py``). Reading the remote directly here
    decouples the identity column from ``current()``'s path-first behavior and
    preserves the remote-derived semantic.

    This also prevents the historical contamination where a workspace like
    ``me`` received the identity of its first scanned child project.

    Falls back to the workspace string itself when path resolution fails or no
    remote is configured.

    Args:
        workspace:      Workspace name used as the fallback / organizational identity.
        workspace_path: Directory whose git remote may supply the identity.
                        Defaults to None (treated as organizational workspace).
    """
    if workspace_path is None:
        return workspace.lower()

    # Only resolve a remote-derived identity when the workspace root is itself
    # a git project. Organizational workspaces (no .git at root) keep their
    # name as identity.
    try:
        if not (workspace_path / ".git").is_dir():
            return workspace.lower()
        from gaia.project import _git_remote_origin, _normalize_remote
        remote = _git_remote_origin(workspace_path)
        if remote:
            ident = _normalize_remote(remote)
            if ident:
                return ident
    except Exception:
        pass
    return workspace.lower()


def _ensure_workspace_row(
    con: sqlite3.Connection,
    workspace: str,
    workspace_path: Path | None = None,
) -> None:
    """Insert (or update) the workspaces row for a workspace.

    Identity is resolved from the git remote of ``workspace_path`` at insertion
    time IFF the workspace root itself is a git project (see
    :func:`_resolve_identity`). On a fresh row the identity is captured; for
    existing rows the identity is left intact (idempotent).

    Args:
        con:            Open SQLite connection.
        workspace:      Workspace name (workspaces.name PK).
        workspace_path: Directory whose git remote may supply the identity.
                        When None, identity defaults to the workspace name.
    """
    existing = con.execute(
        "SELECT name FROM workspaces WHERE name = ?",
        (workspace,),
    ).fetchone()
    if existing is not None:
        return
    identity = _resolve_identity(workspace, workspace_path)
    con.execute(
        "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
        (workspace, identity, _now_iso()),
    )


# ---------------------------------------------------------------------------
# Public API: set_workspace_last_scan_at
# ---------------------------------------------------------------------------

def set_workspace_last_scan_at(
    workspace: str,
    ts: str | None = None,
    *,
    db_path: Path | None = None,
) -> None:
    """Record the ISO8601 timestamp of the most recent successful gaia scan.

    Called by bin/cli/scan.py after a scan run completes without errors.
    The workspaces row is created (via _ensure_workspace_row) if it does
    not yet exist; the update is a no-op when the workspace is unknown.

    Args:
        workspace: Workspace name (workspaces.name PK).
        ts:        ISO8601 UTC timestamp string. Defaults to _now_iso().
        db_path:   Optional explicit DB path (used by tests).
    """
    if ts is None:
        ts = _now_iso()

    con = _connect(db_path)
    try:
        _ensure_workspace_row(con, workspace)
        # A successful scan of an installed workspace means the workspace IS
        # live: stamp last_scan_at AND reactivate it (status='active',
        # missing_since=NULL). This mirrors project reactivation (v16) at the
        # workspace level (v17 DEMOTE) -- a workspace that was previously
        # demoted but is installed again on re-scan recovers cleanly.
        con.execute(
            "UPDATE workspaces SET last_scan_at = ?, status = 'active', "
            "missing_since = NULL WHERE name = ?",
            (ts, workspace),
        )
        con.commit()
    finally:
        con.close()


def mark_workspace_demoted(
    workspace: str,
    *,
    ts: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Soft-delete a workspace whose Gaia install footprint disappeared (DEMOTE).

    Sets ``status='missing'`` and ``missing_since=<now>`` on the workspaces row
    instead of deleting it, mirroring :func:`mark_missing_in` for projects (v16)
    at the workspace level (v17). The row, its projects, and all historical
    context survive; the workspace is simply no longer treated as live.

    Crucially this does NOT touch ``last_scan_at`` -- a demoted workspace must
    not receive a fresh scan timestamp (that is the BUG-3 symptom: persisting a
    demoted workspace as if it were freshly scanned).

    Only a row that is not ALREADY missing is touched -- a row already
    ``status='missing'`` keeps its original ``missing_since`` (first-seen-gone),
    so repeated re-scans of a still-demoted directory do not keep bumping it.

    The workspace row is NOT created if it does not exist: marking a never-seen
    directory demoted is meaningless. Returns True only when an existing,
    previously-active row was transitioned to missing.

    Args:
        workspace: Workspace name (workspaces.name PK).
        ts:        ISO8601 UTC timestamp for missing_since. Defaults to now.
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        True when an existing active row was marked missing; False otherwise
        (no such row, or already missing).
    """
    if ts is None:
        ts = _now_iso()

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            row = con.execute(
                "SELECT status FROM workspaces WHERE name = ?",
                (workspace,),
            ).fetchone()
            if row is None:
                con.commit()
                return False
            if row["status"] == "missing":
                # Already demoted -> keep original missing_since intact.
                con.commit()
                return False
            con.execute(
                "UPDATE workspaces SET status = 'missing', missing_since = ? "
                "WHERE name = ?",
                (ts, workspace),
            )
            con.commit()
            return True
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Column ownership map (coalesce-or-omit + agent-owned protection)
# ---------------------------------------------------------------------------
#
# Ported from tools/scan/orchestrator.py's SCANNER_OWNED_TOP_LEVEL /
# AGENT_ENRICHED_SECTIONS split (the retired project-context.json ownership
# model) down to the DB write path (workspace-identity brief, M1-T2).
#
# Semantics:
#   * Coalesce-or-omit: a column is only written when its key is PRESENT in
#     the caller's `fields` mapping (even when the value is explicitly None,
#     e.g. ``missing_since=None`` to reactivate a project). A key ABSENT from
#     `fields` is left OUT of the INSERT/UPDATE entirely -- the column keeps
#     its current value instead of being forced to NULL just because a given
#     scan run's payload did not mention it. This is the fix for the
#     "columns go NULL when a rescan omits them" clobber.
#   * Agent-owned protection: a column named in the table's `_AGENT_OWNED`
#     set is stripped from `fields` before the coalesce-or-omit step
#     whenever the caller passes ``strip_agent_owned=True`` -- the scan path
#     (bulk_upsert's projects/apps branches, populate_project) always does.
#     A direct caller that does NOT set ``strip_agent_owned`` (tests, or any
#     future agent-driven write) keeps full write access -- the flag gates
#     the SCAN PATH specifically, not the column in the abstract.
#
# M3/T9: `description` is agent-owned (schema v23, scripts/migrations/
# v22_to_v23.sql). The scan path (strip_agent_owned=True) can never write it,
# regardless of what a caller's `fields` dict happens to contain -- it is
# stripped by _present_fields before the coalesce-or-omit step, same
# mechanism already proven for apps.description/status in M1.
_PROJECTS_AGENT_OWNED: frozenset = frozenset({"description"})
# NOTE: `role` is NOT agent-owned here (M1-T3): it is auto-detected by
# tools/scan/role_detector.py and refreshed on every scan, so it belongs to
# the scanner. See schema.sql's `role` column comment for the same note.
_APPS_AGENT_OWNED: frozenset = frozenset({"description", "status"})


def _present_fields(
    fields: Mapping[str, Any],
    recognized: Sequence[str],
    *,
    strip: frozenset = frozenset(),
) -> dict:
    """Return the subset of `recognized` keys actually supplied in `fields`.

    Powers coalesce-or-omit: building the INSERT/UPDATE column list from this
    dict's keys means an omitted scanner-owned column is never forced to NULL,
    and (when `strip` names the table's agent-owned columns) the scan path can
    never write agent-owned data regardless of what its payload happens to
    include.
    """
    return {k: fields[k] for k in recognized if k in fields and k not in strip}


def _find_collision_free_name(
    con: sqlite3.Connection,
    workspace: str,
    name: str,
    project_identity: str | None,
) -> str:
    """Return a `projects.name` guaranteed not to collide with a DIFFERENT
    physical repo already occupying ``(workspace, name)``.

    Two distinct repos (distinct ``project_identity``) can legitimately share
    a basename under the same workspace (e.g. two "foo" repos nested under
    different containers). Without this guard, upserting the second one would
    silently overwrite the first via the ``(workspace, name)`` UNIQUE
    constraint -- the collision-key defect (workspace-identity brief, AC-2).

    Read-only (issues no writes) so it is safe to call from a dry-run preview
    as well as from inside upsert_project's write transaction. When the
    existing occupant shares the SAME identity (or the slot is free, or the
    slot's identity is unset/legacy), the name is returned unchanged --
    disambiguation only fires for a CONFIRMED different physical repo.

    Args:
        con: Open connection (used read-only here).
        workspace: Workspace name.
        name: Candidate project name.
        project_identity: The NEW row's stable identity, or None/empty (in
            which case no collision can be detected and `name` is returned
            unchanged).

    Returns:
        `name` unchanged, or `name` suffixed with `-2`, `-3`, ... until a free
        (or same-identity) slot is found.
    """
    if not project_identity:
        return name

    def _occupied_by_other(candidate: str) -> bool:
        row = con.execute(
            "SELECT project_identity FROM projects WHERE workspace = ? AND name = ?",
            (workspace, candidate),
        ).fetchone()
        existing_identity = row["project_identity"] if row else None
        return bool(existing_identity) and existing_identity != project_identity

    if not _occupied_by_other(name):
        return name

    suffix = 2
    while True:
        candidate = f"{name}-{suffix}"
        if not _occupied_by_other(candidate):
            return candidate
        suffix += 1


def preview_project_name(
    workspace: str,
    name: str,
    project_identity: str | None,
    *,
    db_path: Path | None = None,
    extra_claimed: Mapping[str, str] | None = None,
) -> str:
    """Read-only preview of the name :func:`upsert_project` would actually use.

    Lets a dry-run report the REAL, collision-free name without writing
    anything. ``extra_claimed`` lets a caller iterating a batch of repos in
    one pass (e.g. ``tools/scan/classify.py::scan``) fold in names already
    "claimed" earlier in the SAME batch -- names that a real ``apply=True``
    run would already have committed to the DB by the time a later repo in
    the batch is processed (commits are sequential), but that a dry-run,
    which writes nothing, cannot see via the DB alone.

    Args:
        workspace: Workspace name.
        name: Candidate project name.
        project_identity: The repo's stable identity, or None/empty.
        db_path: Optional explicit DB path (used by tests).
        extra_claimed: Optional ``{name: project_identity}`` map of names
            already claimed earlier in the same in-progress batch.

    Returns:
        The name that would be used, disambiguated if needed.
    """
    if not project_identity:
        return name
    if extra_claimed and name in extra_claimed:
        if extra_claimed[name] == project_identity:
            return name
        # In-memory collision against an earlier repo in this same batch --
        # resolve purely in-memory first (no DB round trip needed to know
        # this slot is taken), then fall through to the DB-aware resolver
        # starting from the first candidate suffix.
        suffix = 2
        while True:
            candidate = f"{name}-{suffix}"
            claimed_identity = extra_claimed.get(candidate)
            if claimed_identity is None:
                break
            if claimed_identity == project_identity:
                return candidate
            suffix += 1
        name = candidate

    # Dry-run touches-nothing guarantee: never let a PREVIEW materialize the
    # DB. `_connect()` runs schema.sql when the file is absent, which would
    # create the data dir during a --dry-run scan (regression caught by
    # tests/cli/test_scan.py::test_dry_run_does_not_touch_db). A DB that does
    # not yet exist has zero rows to collide with, so the in-memory
    # `extra_claimed` resolution above is already the complete answer.
    resolved = db_path if db_path is not None else _db_path()
    if not resolved.exists():
        return name

    con = _connect(resolved)
    try:
        return _find_collision_free_name(con, workspace, name, project_identity)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_project
# ---------------------------------------------------------------------------

_PROJECT_FIELDS = ("role", "remote_url", "platform", "primary_language", "group_name", "path", "status", "missing_since", "project_identity", "description")


def _projects_has_identity_column(con: sqlite3.Connection) -> bool:
    """Return True iff the live ``projects`` table carries ``project_identity``.

    Guards the identity-collapse path against a DB that predates the v18
    migration (column added by scripts/migrations/v17_to_v18.sql). When the
    column is absent, :func:`upsert_project` degrades to the historical
    ``(workspace, name)`` UPSERT so an un-migrated DB keeps working.
    """
    rows = con.execute("PRAGMA table_info(projects)").fetchall()
    return any(r[1] == "project_identity" for r in rows)


def upsert_project(
    workspace: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
    strip_agent_owned: bool = False,
) -> dict:
    """Upsert a projects row, enforcing per-agent write permission.

    Args:
        workspace: Workspace name (matches workspaces.name / projects.workspace).
        name: Project name (basename).
        fields: Dict of column->value pairs. Recognized keys:
            ``role``, ``remote_url``, ``platform``, ``primary_language``,
            ``group_name``, ``path``, ``status``, ``missing_since``,
            ``project_identity``. A key ABSENT from `fields` is coalesce-or-
            omit: the column keeps its current value instead of being forced
            to NULL (see the ownership map above `_PROJECTS_AGENT_OWNED`). A
            key present with value None (e.g. ``missing_since=None``) is an
            explicit write -- this is how the scanner reactivates a
            previously-missing project (pass status='active' and
            missing_since=None together). When ``project_identity`` is
            non-null and the live schema carries the column (v18+), the
            UPSERT collapses on that stable identity: the SAME physical repo
            scanned from different workspaces/roots updates the existing row
            IN PLACE (preserving its original (workspace, name) PK) instead
            of inserting a duplicate. ``status`` defaults to 'active' when
            not provided (or explicitly None).
        agent: Agent name. Must have allow_write=1 for table 'projects' in
            agent_permissions.
        topic_key: Optional dimension key. Coalesced: an explicit value
            overwrites; omitting it (None) preserves the existing value
            instead of nulling it on every rescan.
        db_path: Optional explicit DB path (used by tests).
        workspace_path: Directory whose git remote supplies the workspaces.identity
            value. Pass ``project_path`` from the scanner for correct
            multi-workspace ingestion.
        strip_agent_owned: When True (the scan path -- bulk_upsert's
            projects branch, populate_project), any key in
            ``_PROJECTS_AGENT_OWNED`` is dropped from `fields` before the
            coalesce-or-omit step, regardless of what the caller supplied.
            Direct callers (tests, future agent-driven writes) leave this
            False and keep full write access.

    Returns:
        {"status": "applied", "name": <final name used, disambiguated if a
        genuine repo-name collision was detected -- see
        :func:`_find_collision_free_name`>} on success.
        {"status": "rejected", "reason": "not_authorized"} if the agent lacks
        write permission for the 'projects' table.
    """
    con = _connect(db_path)
    try:
        if not _is_authorized(con, "projects", agent):
            return _rejected()
        has_identity_col = _projects_has_identity_column(con)
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace, workspace_path)

            present = _present_fields(
                fields, _PROJECT_FIELDS,
                strip=_PROJECTS_AGENT_OWNED if strip_agent_owned else frozenset(),
            )
            # Default status to 'active' when not explicitly provided (or
            # explicitly None). Newly-inserted rows and re-upserted live
            # projects always carry an explicit status value -- unchanged
            # historical default.
            if present.get("status") is None:
                present["status"] = "active"
            now = _now_iso()
            project_identity = present.get("project_identity")

            # Identity-collapse path (M1-T2): when a stable project_identity is
            # supplied AND the live schema carries the column, the SAME physical
            # repo must map to ONE row regardless of the workspace/root it was
            # scanned from. We look up any existing row keyed by that identity
            # (the partial unique index idx_projects_identity guarantees at most
            # one) and UPDATE it IN PLACE, preserving its original (workspace,
            # name) PK -- the first-seen vantage wins, later scans only refresh
            # the row's scanner-owned columns. This is what makes the
            # "same repo from two roots -> 0 duplicates" query hold. Only the
            # columns actually PRESENT in `fields` are updated (coalesce-or-
            # omit); scanner-owned columns this call didn't mention keep their
            # current value instead of being nulled.
            if has_identity_col and project_identity:
                existing = con.execute(
                    "SELECT workspace, name FROM projects WHERE project_identity = ?",
                    (project_identity,),
                ).fetchone()
                if existing is not None:
                    set_parts = [f"{c} = ?" for c in present.keys()]
                    set_parts += ["scanner_ts = ?", "topic_key = COALESCE(?, topic_key)"]
                    params = list(present.values()) + [now, topic_key]
                    con.execute(
                        f"UPDATE projects SET {', '.join(set_parts)} "
                        f"WHERE workspace = ? AND name = ?",
                        (*params, existing["workspace"], existing["name"]),
                    )
                    con.commit()
                    return _applied({"name": existing["name"]})

            # No identity match -- this is a NEW row (or a legacy DB with no
            # identity column). Resolve a collision-free name so a DIFFERENT
            # physical repo sharing this basename never silently overwrites
            # an existing, unrelated row (AC-2).
            final_name = name
            if has_identity_col and project_identity:
                final_name = _find_collision_free_name(con, workspace, name, project_identity)

            if has_identity_col:
                insert_cols = ["workspace", "name"] + list(present.keys()) + ["scanner_ts", "topic_key"]
                insert_vals = [workspace, final_name] + list(present.values()) + [now, topic_key]
                update_clause_parts = [f"{c} = excluded.{c}" for c in present.keys()]
                update_clause_parts += [
                    "scanner_ts = excluded.scanner_ts",
                    "topic_key = COALESCE(excluded.topic_key, topic_key)",
                ]
                con.execute(
                    f"INSERT INTO projects ({', '.join(insert_cols)}) "
                    f"VALUES ({', '.join(['?'] * len(insert_cols))}) "
                    f"ON CONFLICT(workspace, name) DO UPDATE SET {', '.join(update_clause_parts)}",
                    insert_vals,
                )
            else:
                # Backward-compat: un-migrated DB without project_identity.
                # No collision-free naming is possible without an identity
                # signal -- degrades to the historical (workspace, name) key.
                # Drop `project_identity` from `present`: the legacy schema
                # does not carry that column at all.
                legacy_present = {k: v for k, v in present.items() if k != "project_identity"}
                insert_cols = ["workspace", "name"] + list(legacy_present.keys()) + ["scanner_ts", "topic_key"]
                insert_vals = [workspace, final_name] + list(legacy_present.values()) + [now, topic_key]
                update_clause_parts = [f"{c} = excluded.{c}" for c in legacy_present.keys()]
                update_clause_parts += [
                    "scanner_ts = excluded.scanner_ts",
                    "topic_key = COALESCE(excluded.topic_key, topic_key)",
                ]
                con.execute(
                    f"INSERT INTO projects ({', '.join(insert_cols)}) "
                    f"VALUES ({', '.join(['?'] * len(insert_cols))}) "
                    f"ON CONFLICT(workspace, name) DO UPDATE SET {', '.join(update_clause_parts)}",
                    insert_vals,
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied({"name": final_name})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_app
# ---------------------------------------------------------------------------

_APP_FIELDS = ("kind", "description", "status")


def upsert_app(
    workspace: str,
    project: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
    strip_agent_owned: bool = False,
) -> dict:
    """Upsert an apps row, enforcing per-agent write permission.

    Args:
        workspace: Workspace name (matches apps.workspace).
        project: Parent project name (must reference a row in the
                 ``projects`` table).
        name: App name.
        fields: Dict with optional keys ``kind``, ``description``, ``status``.
            A key ABSENT from `fields` is coalesce-or-omit: the column keeps
            its current value instead of being forced to NULL. A key present
            (even with value None) is an explicit write.
        agent: Agent name. Requires allow_write=1 for table 'apps'.
        topic_key: Optional dimension key. Coalesced: an explicit value
            overwrites; omitting it (None) preserves the existing value.
        db_path: Optional explicit DB path (used by tests).
        strip_agent_owned: When True (the scan path -- bulk_upsert's apps
            branch), ``description`` and ``status`` (``_APPS_AGENT_OWNED``)
            are dropped from `fields` before the coalesce-or-omit step,
            regardless of what the caller supplied. Direct callers (tests,
            future agent-driven writes) leave this False and keep full
            write access.

    Returns:
        {"status": "applied"} on success.
        {"status": "rejected", "reason": "not_authorized"} otherwise.
    """
    con = _connect(db_path)
    try:
        if not _is_authorized(con, "apps", agent):
            return _rejected()
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            # Ensure parent project row exists -- create a minimal stub if missing
            existing_project = con.execute(
                "SELECT name FROM projects WHERE workspace = ? AND name = ?",
                (workspace, project),
            ).fetchone()
            if existing_project is None:
                con.execute(
                    "INSERT INTO projects (workspace, name, scanner_ts) VALUES (?, ?, ?)",
                    (workspace, project, _now_iso()),
                )
            present = _present_fields(
                fields, _APP_FIELDS,
                strip=_APPS_AGENT_OWNED if strip_agent_owned else frozenset(),
            )
            now = _now_iso()
            insert_cols = ["workspace", "project", "name"] + list(present.keys()) + ["topic_key", "scanner_ts"]
            insert_vals = [workspace, project, name] + list(present.values()) + [topic_key, now]
            update_clause_parts = [f"{c} = excluded.{c}" for c in present.keys()]
            update_clause_parts += [
                "topic_key = COALESCE(excluded.topic_key, topic_key)",
                "scanner_ts = excluded.scanner_ts",
            ]
            con.execute(
                f"INSERT INTO apps ({', '.join(insert_cols)}) "
                f"VALUES ({', '.join(['?'] * len(insert_cols))}) "
                f"ON CONFLICT(workspace, project, name) DO UPDATE SET {', '.join(update_clause_parts)}",
                insert_vals,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: delete_missing_in
# ---------------------------------------------------------------------------

def delete_missing_in(
    table: str,
    workspace: str,
    surviving_keys: Iterable[Sequence[Any]],
    *,
    db_path: Path | None = None,
) -> int:
    """Delete rows from `table` (filtered by workspace) whose primary
    key is NOT in surviving_keys.

    Args:
        table: Target table name (must be in _KNOWN_TABLES).
        workspace: Workspace name (workspace FK value).
        surviving_keys: Iterable of tuples representing the PK fragments to
            keep. For ``projects`` use ``[(name,), ...]``. For ``apps`` use
            ``[(project, name), ...]``.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows deleted.

    Raises:
        ValueError: if `table` is not in the whitelist.
    """
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown table: {table!r}")

    surviving = list(surviving_keys)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            pk_columns = {
                "workspaces": ("name",),
                "projects": ("name",),
                "apps": ("project", "name"),
                "libraries": ("project", "name"),
                "services": ("project", "name"),
                "features": ("project", "name"),
                "project_facets": ("project", "scope", "key"),
                "tf_modules": ("project", "name"),
                "tf_live": ("project", "name"),
                "releases": ("project", "name"),
                "workloads": ("project", "name"),
                "clusters_defined": ("project", "name"),
                "clusters": ("name",),
                "integrations": ("name",),
                "gaia_installations": ("machine",),
                "machines": ("name",),
            }[table]

            cols_sql = ", ".join(pk_columns)
            existing = con.execute(
                f"SELECT {cols_sql} FROM {table} WHERE workspace = ?",
                (workspace,),
            ).fetchall()
            existing_set = {tuple(row) for row in existing}
            surviving_set = {tuple(s) for s in surviving}
            to_delete = existing_set - surviving_set

            count = 0
            for key in to_delete:
                placeholders = " AND ".join(f"{c} = ?" for c in pk_columns)
                con.execute(
                    f"DELETE FROM {table} WHERE workspace = ? AND {placeholders}",
                    (workspace, *key),
                )
                count += 1
            con.commit()
            return count
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: mark_missing_in (soft-delete; mirror of delete_missing_in)
# ---------------------------------------------------------------------------

def mark_missing_in(
    table: str,
    workspace: str,
    surviving_keys: Iterable[Sequence[Any]],
    *,
    db_path: Path | None = None,
) -> int:
    """Soft-delete rows in `table` (filtered by workspace) whose primary key is
    NOT in surviving_keys: set ``status='missing'`` and ``missing_since=<now>``
    instead of DELETEing them.

    This is the mirror of :func:`delete_missing_in` but UPDATEs instead of
    DELETEs. A scan that only partially discovers projects (a partial walk, a
    permissions hiccup, a transient error) must never destroy real rows; it
    marks them missing so the data survives and remains consultable.

    Only rows that are not ALREADY missing are touched -- a row already
    ``status='missing'`` keeps its original ``missing_since`` timestamp (the
    moment it first disappeared), so repeated re-scans do not keep bumping it.

    Args:
        table: Target table name. Must be ``"projects"`` -- it is the only
            table carrying the ``status`` / ``missing_since`` soft-delete
            columns. Any other table raises ValueError because marking it
            missing has no column to write.
        workspace: Workspace name (workspace FK value). Scoping is strict;
            rows in other workspaces are never touched.
        surviving_keys: Iterable of tuples representing the PK fragments to
            keep active. For ``projects`` use ``[(name,), ...]``.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows newly marked missing.

    Raises:
        ValueError: if `table` is not whitelisted or does not carry the
            soft-delete columns.
    """
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown table: {table!r}")
    # Only `projects` carries status/missing_since. Marking any other table
    # missing is a programming error -- fail loudly instead of writing to a
    # column that does not exist.
    if table != "projects":
        raise ValueError(
            f"mark_missing_in only supports the 'projects' table "
            f"(soft-delete columns status/missing_since); got {table!r}"
        )

    surviving = list(surviving_keys)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            existing = con.execute(
                "SELECT name, status FROM projects WHERE workspace = ?",
                (workspace,),
            ).fetchall()
            surviving_set = {tuple(s) for s in surviving}

            now = _now_iso()
            count = 0
            for row in existing:
                key = (row["name"],)
                if key in surviving_set:
                    continue
                # Already missing -> leave missing_since intact (first-seen-gone).
                if row["status"] == "missing":
                    continue
                con.execute(
                    "UPDATE projects SET status = 'missing', missing_since = ? "
                    "WHERE workspace = ? AND name = ?",
                    (now, workspace, row["name"]),
                )
                count += 1
            con.commit()
            return count
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: bulk_upsert
# ---------------------------------------------------------------------------

def bulk_upsert(
    table: str,
    workspace: str,
    rows: Iterable[Mapping[str, Any]],
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Upsert multiple rows in a single transaction.

    Returns:
        {"applied": int, "rejected": int}
    """
    rows_list = list(rows)
    applied = 0
    rejected = 0
    if table == "projects":
        # bulk_upsert is exclusively the scan path's batch writer (see the
        # module docstring: "populators NEVER touch agent-owned columns") --
        # strip_agent_owned=True enforces that structurally, regardless of
        # what a row dict happens to include.
        for r in rows_list:
            res = upsert_project(
                workspace,
                r["name"],
                r,
                agent,
                topic_key=r.get("topic_key"),
                db_path=db_path,
                strip_agent_owned=True,
            )
            if res.get("status") == "applied":
                applied += 1
            else:
                rejected += 1
        return {"applied": applied, "rejected": rejected}

    if table == "apps":
        for r in rows_list:
            res = upsert_app(
                workspace,
                r["project"],
                r["name"],
                r,
                agent,
                topic_key=r.get("topic_key"),
                db_path=db_path,
                strip_agent_owned=True,
            )
            if res.get("status") == "applied":
                applied += 1
            else:
                rejected += 1
        return {"applied": applied, "rejected": rejected}

    # Generic path: enforce permission + ON CONFLICT DO UPDATE that ONLY
    # updates the columns the caller provided.
    pk_columns = {
        "workspaces": ("name",),
        "projects": ("name",),
        "apps": ("project", "name"),
        "libraries": ("project", "name"),
        "services": ("project", "name"),
        "features": ("project", "name"),
        "project_facets": ("project", "scope", "key"),
        "tf_modules": ("project", "name"),
        "tf_live": ("project", "name"),
        "releases": ("project", "name"),
        "workloads": ("project", "name"),
        "clusters_defined": ("project", "name"),
        "clusters": ("name",),
        "integrations": ("name",),
        "gaia_installations": ("machine",),
        "machines": ("name",),
    }
    if table not in pk_columns:
        raise ValueError(f"unknown table for bulk_upsert: {table!r}")
    pk = ("workspace", *pk_columns[table])

    con = _connect(db_path)
    try:
        if not _is_authorized(con, table, agent):
            return {"applied": 0, "rejected": len(rows_list)}
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            for r in rows_list:
                row_data = dict(r)
                cols = ["workspace"] + list(row_data.keys())
                vals = [workspace] + list(row_data.values())
                placeholders = ", ".join(["?"] * len(cols))
                update_cols = [c for c in row_data.keys() if c not in pk]
                pk_sql = ", ".join(pk)
                if update_cols:
                    set_clause = ", ".join(
                        f"{c} = excluded.{c}" for c in update_cols
                    )
                    sql = (
                        f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT({pk_sql}) DO UPDATE SET {set_clause}"
                    )
                else:
                    sql = (
                        f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT({pk_sql}) DO NOTHING"
                    )
                con.execute(sql, vals)
                applied += 1
            con.commit()
        except Exception:
            con.rollback()
            raise
        return {"applied": applied, "rejected": rejected}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: save_integration
# ---------------------------------------------------------------------------

_INTEGRATION_FIELDS = ("kind", "version", "install_path", "topic_key")


def save_integration(
    workspace: str,
    name: str,
    *,
    kind: str | None = None,
    version: str | None = None,
    install_path: str | None = None,
    topic_key: str | None = None,
    agent: str = "system",
    db_path: Path | None = None,
) -> dict:
    """Upsert an integrations row, bypassing per-agent permission enforcement.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            con.execute(
                """
                INSERT INTO integrations (workspace, name, kind, version,
                                          install_path, topic_key, scanner_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    kind         = COALESCE(excluded.kind, kind),
                    version      = COALESCE(excluded.version, version),
                    install_path = COALESCE(excluded.install_path, install_path),
                    topic_key    = COALESCE(excluded.topic_key, topic_key),
                    scanner_ts   = excluded.scanner_ts
                """,
                (workspace, name, kind, version, install_path, topic_key, _now_iso()),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: write_harness_event
# ---------------------------------------------------------------------------
#
# Brief 54 / Task 2.2: the harness event pipeline (every hook firing) writes
# here instead of the legacy events.jsonl file. This is the hot path -- every
# AGENT_DISPATCH / COMMAND_EXECUTED / AGENT_COMPLETE / SESSION_END event flows
# through it -- so the contract is: non-blocking and silent-on-failure at the
# call site (the hook wraps this in try/except: pass), append-only INSERT, no
# permission gate (episodic audit events are not curated memory).
#
# Column mapping (harness_events, schema.sql ~L756):
#   type      <- event_type
#   source    <- source
#   agent     <- agent
#   result    <- result
#   severity  <- severity
#   payload   <- json.dumps(meta)   (NULL when meta is falsy)
#   workspace <- workspace          (None-safe; column is nullable, no FK)
#   ts        <- _now_iso()
#
# No _ensure_workspace_row call: harness_events.workspace is a plain nullable
# TEXT column with no FK to workspaces, so an arbitrary or NULL workspace is
# valid and must not trigger workspace-row creation.
# ---------------------------------------------------------------------------

def write_harness_event(
    *,
    event_type: str,
    source: str | None = None,
    agent: str | None = None,
    result: str | None = None,
    severity: str | None = "info",
    meta: Mapping[str, Any] | None = None,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Append one row to ``harness_events`` and return its id.

    This is the DB cutover of the historical ``EventWriter.write_event`` file
    writer. It is append-only and not permission-gated. Callers in the hook
    pipeline wrap it in ``try/except: pass`` -- this function itself does not
    swallow exceptions, so tests and direct callers see real failures.

    Args:
        event_type: Dotted event category -> ``type`` column (NOT NULL).
        source:     Who emitted the event (e.g. "hook").
        agent:      Agent involved, or empty/None for non-agent events.
        result:     Outcome summary string.
        severity:   info | warning | error.
        meta:       Optional structured data; serialized to JSON into the
                    ``payload`` column. Falsy meta -> NULL payload.
        workspace:  Workspace name or None (column is nullable, no FK).
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.
    """
    payload = json.dumps(meta, separators=(",", ":")) if meta else None
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO harness_events
                (workspace, ts, type, source, agent, result, severity, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace,
                _now_iso(),
                event_type,
                source,
                agent,
                result,
                severity,
                payload,
            ),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_memory
# ---------------------------------------------------------------------------

VALID_MEMORY_TYPES = ("project", "user", "feedback", "atom", "decision", "negative")


# ---------------------------------------------------------------------------
# Structural enforcement: curated memory is owned by the orchestrator-operator
# pair. When a subagent dispatch carries GAIA_DISPATCH_AGENT, only those two
# identities are allowed to mutate the `memory` table. Absence of the env var
# means the caller is a human shell (CLI run directly) -- always permitted.
# See brief: memory-model-refactor-class-status-links-structural-enforcement.
# ---------------------------------------------------------------------------

class MemoryWriteForbidden(PermissionError):
    """Raised when a non-curator subagent attempts to mutate curated memory."""


_MEMORY_CURATOR_AGENTS = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
})


def _assert_dispatch_can_write_memory() -> None:
    """Block memory writes from non-curator subagent dispatches.

    Reads ``GAIA_DISPATCH_AGENT`` from the environment. The contract:

    * Unset -> human caller running the CLI directly. Allowed.
    * Set to an empty string -> treated as unset. Allowed.
    * Set to one of the curator identities -> allowed.
    * Set to anything else -> raises ``MemoryWriteForbidden``.

    Curated memory is the orchestrator-operator pair's substrate. Subagents
    (developer, platform-architect, gitops-operator, ...) record episodic
    events via the audit pipeline; they do not author the curated layer.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    if raw in _MEMORY_CURATOR_AGENTS:
        return
    raise MemoryWriteForbidden(
        f"Curated memory writes are forbidden from subagent dispatches "
        f"(current GAIA_DISPATCH_AGENT={raw!r}). Memory is owned by the "
        f"orchestrator-operator pair."
    )

# Curated slug taxonomy: when the type is one of the new curated types
# (atom / decision / negative), the `name` must start with the MATCHING prefix
# for that specific type, and use snake_case slug discipline. The legacy types
# (project / user / feedback) keep their historical naming freedom, but are
# NOT allowed to use a curated prefix -- that combination is a mismatch that
# must fail loudly.
#
# Single-source-of-truth rule: the slug prefix IS the type. (slug, type) pairs
# that disagree are always an error -- never silently reclassified.
import re as _re_for_slug
_CURATED_SLUG_TYPES = ("atom", "decision", "negative")
_LEGACY_SLUG_TYPES = ("project", "user", "feedback")

# Pre-computed per-type patterns for precise prefix enforcement.
_CURATED_TYPE_PATTERNS = {
    t: _re_for_slug.compile(rf"^{t}_[a-z0-9_]+$")
    for t in _CURATED_SLUG_TYPES
}
# Used to detect when a legacy-type call uses a curated prefix (cross-direction mismatch).
_CURATED_PREFIX_PATTERN = _re_for_slug.compile(
    r"^(atom|decision|negative)_"
)


def _validate_curated_slug(name: str, type: str) -> None:
    """Raise ValueError when the slug and type disagree, in either direction.

    Rules (single source of truth: the slug prefix IS the type):
      * type in (atom, decision, negative): name must match '^{type}_[a-z0-9_]+$'
        exactly -- not just any curated prefix, the SPECIFIC one for this type.
      * type in (project, user, feedback): name must NOT start with any curated
        prefix (atom_, decision_, negative_). If it does, caller is expressing
        an impossible pair; fail loudly instead of reclassifying silently.
    """
    if type in _CURATED_SLUG_TYPES:
        pattern = _CURATED_TYPE_PATTERNS[type]
        if not pattern.match(name):
            raise ValueError(
                f"slug {name!r} does not match type={type!r}: "
                f"expected '^{type}_[a-z0-9_]+$' (e.g. '{type}_my_topic'). "
                f"The slug prefix must match the type -- they are the same thing."
            )
    elif type in _LEGACY_SLUG_TYPES:
        if _CURATED_PREFIX_PATTERN.match(name):
            # Extract the conflicting prefix so the error is actionable.
            conflicting_prefix = name.split("_")[0]
            raise ValueError(
                f"slug {name!r} starts with '{conflicting_prefix}_' but type={type!r}: "
                f"the slug prefix and the type must agree. "
                f"Either use --type={conflicting_prefix} to match the slug prefix, "
                f"or rename the slug to start with '{type}_'."
            )


def resolve_project_ref(
    workspace: str,
    project_name: str,
    *,
    db_path: Path | None = None,
) -> str:
    """Resolve a ``projects.name`` within ``workspace`` to its stable
    ``project_identity`` anchor -- the value ``upsert_memory(project_ref=...)``
    expects (N3 forward-only anchoring).

    Looks up the exact ``(workspace, project_name)`` row -- the same lookup
    documented as the manual convention in ``skills/memory/SKILL.md`` before
    this function existed (``SELECT project_identity FROM projects WHERE
    workspace=? AND name=?``). Never guesses: raises ``ValueError`` with an
    actionable message when the project does not exist, when more than one
    row matches (structurally guarded against by the ``(workspace, name)``
    primary key, but checked defensively), or when the matching row has not
    yet been assigned a ``project_identity`` (e.g. a legacy pre-v18 row, or a
    project scanned before the identity column was populated) -- anchoring to
    an absent identity would be a guess, not a resolution.

    Args:
        workspace: Workspace name (matches ``projects.workspace``).
        project_name: Project basename (matches ``projects.name``).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        The resolved ``project_identity`` string.

    Raises:
        ValueError: project not found, ambiguous, or has no project_identity.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT project_identity FROM projects WHERE workspace = ? AND name = ?",
            (workspace, project_name),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        raise ValueError(
            f"project {project_name!r} not found in workspace {workspace!r}; "
            f"cannot anchor memory to it. Check the name with "
            f"`gaia context query \"SELECT name FROM projects WHERE "
            f"workspace='{workspace}'\"`."
        )
    if len(rows) > 1:
        # Structurally unreachable today ((workspace, name) is the projects
        # PK), kept as a defensive guard against a future schema change that
        # relaxes that constraint -- "never guess" applies here too.
        raise ValueError(
            f"project {project_name!r} is ambiguous in workspace {workspace!r} "
            f"({len(rows)} matching rows); cannot anchor memory to a single "
            f"identity without guessing."
        )
    identity = rows[0]["project_identity"]
    if not identity:
        raise ValueError(
            f"project {project_name!r} in workspace {workspace!r} has no "
            f"project_identity yet (legacy row, or not yet scanned); "
            f"cannot anchor memory to it without guessing. Run `gaia scan` "
            f"first."
        )
    return identity


def project_workspaces(
    project_name: str,
    *,
    db_path: Path | None = None,
) -> list[str]:
    """Return the workspaces that contain a project named ``project_name``.

    Used by ``gaia memory add`` to tell two failure modes apart when
    ``resolve_project_ref(workspace, name)`` cannot resolve: a project that
    does not exist at ALL vs. one that exists under a DIFFERENT workspace (a
    ``--project`` / ``--workspace`` mismatch). Considers rows of any
    ``status`` -- a 'missing' project under another workspace is still a
    mismatch signal, not a "does not exist".

    Never raises: returns ``[]`` on any DB/lookup failure so the caller's
    mismatch heuristic degrades to the plain "not found" path.

    Args:
        project_name: Project basename (matches ``projects.name``).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        A list of distinct workspace names, possibly empty.
    """
    try:
        con = _connect(db_path)
        try:
            rows = con.execute(
                "SELECT DISTINCT workspace FROM projects WHERE name = ?",
                (project_name,),
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 -- best-effort discriminator
        return []
    return [r["workspace"] for r in rows]


def resolve_project_ref_by_cwd(
    workspace: str,
    *,
    cwd: Path | str | None = None,
    db_path: Path | None = None,
) -> str | None:
    """Resolve the *active* project anchor for ``workspace`` from ``cwd``.

    This is the cwd->project resolution used by the READ/injection side only
    (``gaia memory get-relevant``, to scope and re-rank the SessionStart
    block). It is deliberately NOT used by the write side: ``gaia memory add``
    demands explicit scope and refuses to infer a `project_ref` from the cwd,
    because a wrong guess on write would persist bad data, whereas on read it
    only re-ranks what is shown (cheap, reversible). Unlike
    :func:`resolve_project_ref` -- which resolves an *explicit* ``projects.name``
    and RAISES when it cannot -- this one never raises and never guesses: it
    returns the ``project_identity`` of the active project, or ``None`` when
    the cwd does not sit inside exactly one project.

    Resolution rule (matches the design decision): among the workspace's
    active projects, find those whose recorded ``path`` CONTAINS ``cwd``
    (``path`` is an ancestor of, or equal to, ``cwd``). The MOST SPECIFIC
    match wins -- the project with the longest such ``path`` -- so a nested
    project resolves to itself rather than to an ancestor project. When NO
    project path contains ``cwd`` (e.g. sitting at the root of a workspace
    whose N projects all live in subdirectories), the result is ``None``
    and the caller falls back to workspace-only behaviour. A row whose
    ``path`` or ``project_identity`` is NULL, or whose ``status`` is not
    'active', can never be the resolved anchor.

    Fail-safe: any error (unresolvable cwd, DB failure) returns ``None`` --
    the injection path must never break SessionStart merely because the
    active project could not be inferred.

    Args:
        workspace: Workspace name (scopes the ``projects`` lookup).
        cwd: Directory to resolve from. Defaults to ``Path.cwd()``.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        The resolved ``project_identity`` string, or ``None``.
    """
    try:
        target = Path(cwd) if cwd is not None else Path.cwd()
        target = target.resolve()
    except (OSError, RuntimeError):
        return None

    try:
        con = _connect(db_path)
        try:
            rows = con.execute(
                "SELECT path, project_identity FROM projects "
                "WHERE workspace = ? AND status = 'active' "
                "  AND path IS NOT NULL AND project_identity IS NOT NULL",
                (workspace,),
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 -- fail-safe default path
        return None

    best_identity: str | None = None
    best_len = -1
    for r in rows:
        raw_path = r["path"]
        if not raw_path:
            continue
        try:
            proj_path = Path(raw_path).resolve()
        except (OSError, RuntimeError):
            continue
        # `path` CONTAINS `cwd`: proj_path is an ancestor of, or equal to, cwd.
        if target == proj_path or target.is_relative_to(proj_path):
            plen = len(str(proj_path))
            if plen > best_len:
                best_len = plen
                best_identity = r["project_identity"]

    return best_identity


def upsert_memory(
    workspace: str,
    name: str,
    *,
    type: str,
    body: str,
    description: str | None = None,
    origin_session_id: str | None = None,
    project_ref: str | None = None,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Upsert a curated-memory row in the ``memory`` table.

    Archive-on-upsert (scan-v2 SV3): when this overwrites an existing row, the
    ``memory_au``... no -- the ``trg_memory_history`` AFTER UPDATE trigger fires
    on the ON CONFLICT DO UPDATE below and archives the PREVIOUS ``body`` (and
    workspace/type/description/status/deleted_at) into ``memory_history`` before
    the new value lands. The prior version is never lost; no explicit archival
    code is needed here because the guarantee is enforced at the SQL layer for
    every write path, not just this one.

    Resurrection: re-adding a slug that was soft-deleted clears ``deleted_at``
    (the row returns to the live set). The clearing is captured by the same
    history trigger.

    ``project_ref`` -- forward-only remote-stable project anchor (N3, scan-v2
    SV3 follow-up). The v25/v26 columns/migration exist, but the automatic
    backfill in ``scripts/migrations/v25_to_v26.sql`` (guarded on "workspace
    hosts exactly one active project") is a one-time, already-applied
    historical statement that populated 0 rows in practice -- the
    memory-row-to-project mapping is ambiguous whenever a workspace hosts more
    than one project, and NEVER guessed. There is no live code that re-runs or
    depends on that guard; going forward, ``project_ref`` is anchored
    explicitly, at write time, by whoever calls this function knowing which
    project a ``project``-type row is about (see ``gaia memory add --project``
    in ``bin/cli/memory.py``, which resolves a project name to its
    ``projects.project_identity`` via :func:`resolve_project_ref` before
    calling here).

    Coalesce-or-omit (same discipline as ``topic_key`` elsewhere in this
    module): ``project_ref=None`` (the default) never touches an existing
    anchor -- an update that does not mention the project leaves a
    previously-set anchor intact instead of clobbering it back to NULL. Pass
    an explicit identity string to set or overwrite it. There is no "clear"
    sentinel; once anchored, forward-only re-anchoring is the only write path
    (matches the existing ``topic_key`` COALESCE convention -- no precedent in
    this module for an explicit-NULL clear on a coalesced column).
    """
    _assert_dispatch_can_write_memory()

    if type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"invalid memory type {type!r}; must be one of {list(VALID_MEMORY_TYPES)}"
        )
    if not body or not body.strip():
        raise ValueError("memory body cannot be empty")
    if not name or not name.strip():
        raise ValueError("memory name cannot be empty")
    _validate_curated_slug(name, type)

    if origin_session_id is None:
        origin_session_id = os.environ.get("GAIA_SESSION_ID") or None

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace, workspace_path)

            existing = con.execute(
                "SELECT name FROM memory WHERE workspace = ? AND name = ?",
                (workspace, name),
            ).fetchone()
            action = "updated" if existing is not None else "inserted"

            now = _now_iso()
            con.execute(
                """
                INSERT INTO memory (workspace, name, type, description, body,
                                    project_ref, origin_session_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    type              = excluded.type,
                    description       = excluded.description,
                    body              = excluded.body,
                    project_ref       = COALESCE(excluded.project_ref, project_ref),
                    origin_session_id = excluded.origin_session_id,
                    updated_at        = excluded.updated_at,
                    deleted_at        = NULL
                """,
                (workspace, name, type, description, body,
                 project_ref, origin_session_id, now),
            )
            con.commit()
            return {
                "status": "applied",
                "action": action,
                "name": name,
                "updated_at": now,
            }
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: delete_memory / update_memory_field
# ---------------------------------------------------------------------------

_MEMORY_PATCHABLE_FIELDS = ("description", "body")


def delete_memory(
    workspace: str,
    name: str,
    *,
    hard: bool = False,
    db_path: Path | None = None,
) -> bool:
    """Soft-delete (tombstone) a curated memory row -- scan-v2 SV3.

    By default this is a SOFT delete: the row's ``deleted_at`` column is stamped
    with the current UTC timestamp instead of the row being physically removed.
    The row and its ``body`` survive (recoverable, and re-addable via
    :func:`upsert_memory`, which clears the tombstone). The ``trg_memory_history``
    trigger records the tombstone transition (before_deleted_at NULL -> after
    non-NULL). All read paths filter ``deleted_at IS NULL`` so a tombstoned row
    is invisible to normal queries.

    A tombstone is idempotent: calling delete_memory on an already-tombstoned
    row is a no-op (the row is not re-stamped and no new history row is written).

    ``hard=True`` performs the real physical DELETE. This is the ONLY path that
    destroys the row and its body, and it exists exclusively for explicit human
    curation ("never hard-delete curated memory except by explicit human
    curation" -- decision_scan_v2_memory_loss_vectors). The CLI surfaces it via
    ``gaia memory delete --hard`` behind the existing confirmation prompt.

    Returns True when a row was affected (tombstoned or hard-deleted), False
    when no live row matched (already tombstoned, or absent).
    """
    _assert_dispatch_can_write_memory()
    con = _connect(db_path)
    try:
        if hard:
            cur = con.execute(
                "DELETE FROM memory WHERE workspace = ? AND name = ?",
                (workspace, name),
            )
            con.commit()
            return cur.rowcount > 0
        # Soft delete: stamp deleted_at only on a currently-live row. The
        # `deleted_at IS NULL` guard makes a repeated tombstone a no-op (no
        # spurious history row, no timestamp churn).
        now = _now_iso()
        cur = con.execute(
            "UPDATE memory SET deleted_at = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ? AND deleted_at IS NULL",
            (now, now, workspace, name),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def update_memory_field(
    workspace: str,
    name: str,
    field: str,
    content: str,
    *,
    append: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Patch a single column on a curated memory row."""
    _assert_dispatch_can_write_memory()
    if field not in _MEMORY_PATCHABLE_FIELDS:
        raise ValueError(
            f"invalid memory field {field!r}; must be one of "
            f"{list(_MEMORY_PATCHABLE_FIELDS)}"
        )
    if content is None or content == "":
        raise ValueError("content cannot be empty")

    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT {field}, body FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory '{name}' not found in workspace '{workspace}'"
            )

        existing = row[field] or ""
        if append and existing:
            new_value = f"{existing}\n\n{content}"
            action = "appended"
        else:
            new_value = content
            action = "overwritten"

        if field == "body" and not new_value.strip():
            raise ValueError("memory body cannot be empty")

        now = _now_iso()
        con.execute(
            f"UPDATE memory SET {field} = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (new_value, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "field": field,
            "action": action,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: memory_links (v4 graph primitives)
# ---------------------------------------------------------------------------
#
# Brief: memory-model-refactor-class-status-links-structural-enforcement (T4).
#
# Duplicate-edge policy (architectural decision):
#   The writer accepts ``if_exists`` with default ``"skip"``. Re-creating the
#   same edge is idempotent -- no error, returns ``{"status":"applied",
#   "action":"noop"}``. Callers that want strict semantics pass
#   ``if_exists="error"`` to receive a ValueError on duplicates.
#
# Rationale: declarative wiring ("thread X supersedes anchor Y") is the dominant
# CLI use case; idempotent default makes the `gaia memory link` command safely
# re-runnable from scripts and migration tooling. The strict mode is preserved
# for callers that need to detect drift (e.g. reclassify pipelines verifying
# that an edge they expected to be a one-time event did not silently re-fire).
#
# Existence enforcement: both src_name and dst_name MUST already exist in the
# ``memory`` table for the workspace. Links to non-existent slugs would leave
# dangling edges that the injector cannot resolve -- the writer raises ValueError
# instead of accepting them. ON DELETE CASCADE on workspace handles the deeper
# integrity guarantees at the SQLite layer.
# ---------------------------------------------------------------------------

VALID_MEMORY_LINK_KINDS = ("relates_to", "supersedes", "derived_from", "graduated_to")


def insert_memory_link(
    workspace: str,
    src_name: str,
    dst_name: str,
    kind: str,
    *,
    if_exists: str = "skip",
    db_path: Path | None = None,
) -> dict:
    """Insert a row into ``memory_links``. Idempotent by default.

    Both ``src_name`` and ``dst_name`` must already exist in the ``memory``
    table for ``workspace`` -- otherwise the writer refuses to create a
    dangling edge.

    Args:
        workspace:  Workspace name (FK -> workspaces.name).
        src_name:   Source memory slug (must exist in memory).
        dst_name:   Destination memory slug (must exist in memory).
        kind:       One of VALID_MEMORY_LINK_KINDS. The schema enforces this
                    via CHECK; the writer validates first for clearer errors.
        if_exists:  ``"skip"`` (default) -> idempotent re-insert returns
                    ``action="noop"``. ``"error"`` -> raise ValueError when
                    the (workspace, src, dst, kind) row already exists.
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "action": "inserted"|"noop",
         "workspace": ..., "src_name": ..., "dst_name": ..., "kind": ...,
         "created_at": ...}

    Raises:
        ValueError: invalid kind, missing src/dst, or if_exists="error" on dup.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if kind not in VALID_MEMORY_LINK_KINDS:
        raise ValueError(
            f"invalid link kind {kind!r}; must be one of "
            f"{list(VALID_MEMORY_LINK_KINDS)}"
        )
    if if_exists not in ("skip", "error"):
        raise ValueError(
            f"invalid if_exists {if_exists!r}; must be 'skip' or 'error'"
        )
    if not src_name or not src_name.strip():
        raise ValueError("src_name cannot be empty")
    if not dst_name or not dst_name.strip():
        raise ValueError("dst_name cannot be empty")

    con = _connect(db_path)
    try:
        # Validate endpoints exist. Without these checks we silently create
        # edges to slugs that do not (yet) exist -- the injector and graph
        # walkers cannot recover from that.
        src_row = con.execute(
            "SELECT name FROM memory WHERE workspace = ? AND name = ?",
            (workspace, src_name),
        ).fetchone()
        if src_row is None:
            raise ValueError(
                f"src memory {src_name!r} not found in workspace "
                f"{workspace!r}"
            )
        dst_row = con.execute(
            "SELECT name FROM memory WHERE workspace = ? AND name = ?",
            (workspace, dst_name),
        ).fetchone()
        if dst_row is None:
            raise ValueError(
                f"dst memory {dst_name!r} not found in workspace "
                f"{workspace!r}"
            )

        existing = con.execute(
            "SELECT created_at FROM memory_links "
            "WHERE workspace = ? AND src_name = ? AND dst_name = ? AND kind = ?",
            (workspace, src_name, dst_name, kind),
        ).fetchone()
        if existing is not None:
            if if_exists == "error":
                raise ValueError(
                    f"memory_link already exists: ({workspace}, {src_name}, "
                    f"{dst_name}, {kind}) -- created_at={existing['created_at']}"
                )
            return {
                "status": "applied",
                "action": "noop",
                "workspace": workspace,
                "src_name": src_name,
                "dst_name": dst_name,
                "kind": kind,
                "created_at": existing["created_at"],
            }

        now = _now_iso()
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind, "
            "                          created_at) VALUES (?, ?, ?, ?, ?)",
            (workspace, src_name, dst_name, kind, now),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "workspace": workspace,
            "src_name": src_name,
            "dst_name": dst_name,
            "kind": kind,
            "created_at": now,
        }
    finally:
        con.close()


def delete_memory_link(
    workspace: str,
    src_name: str,
    dst_name: str,
    kind: str,
    *,
    if_missing: str = "skip",
    db_path: Path | None = None,
) -> dict:
    """Delete a row from ``memory_links``. Idempotent by default.

    Args:
        workspace, src_name, dst_name, kind: Full PK of the link.
        if_missing: ``"skip"`` (default) -> deleting a non-existent edge
                    returns ``action="noop"``. ``"error"`` -> raise ValueError.
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "action": "deleted"|"noop",
         "workspace": ..., "src_name": ..., "dst_name": ..., "kind": ...}

    Raises:
        ValueError: invalid kind, invalid if_missing, or if_missing="error"
                    when the row does not exist.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if kind not in VALID_MEMORY_LINK_KINDS:
        raise ValueError(
            f"invalid link kind {kind!r}; must be one of "
            f"{list(VALID_MEMORY_LINK_KINDS)}"
        )
    if if_missing not in ("skip", "error"):
        raise ValueError(
            f"invalid if_missing {if_missing!r}; must be 'skip' or 'error'"
        )

    con = _connect(db_path)
    try:
        cur = con.execute(
            "DELETE FROM memory_links "
            "WHERE workspace = ? AND src_name = ? AND dst_name = ? AND kind = ?",
            (workspace, src_name, dst_name, kind),
        )
        con.commit()
        if cur.rowcount == 0:
            if if_missing == "error":
                raise ValueError(
                    f"memory_link not found: ({workspace}, {src_name}, "
                    f"{dst_name}, {kind})"
                )
            return {
                "status": "applied",
                "action": "noop",
                "workspace": workspace,
                "src_name": src_name,
                "dst_name": dst_name,
                "kind": kind,
            }
        return {
            "status": "applied",
            "action": "deleted",
            "workspace": workspace,
            "src_name": src_name,
            "dst_name": dst_name,
            "kind": kind,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: reclassify_memory (v4 class/status fields)
# ---------------------------------------------------------------------------
#
# Brief: memory-model-refactor-class-status-links-structural-enforcement (T5).
#
# The writer is the source of truth for the class/status enums (the schema
# does NOT carry CHECK constraints on these columns -- see schema.sql L572-578
# for the rebuild-avoidance rationale). Validation lives here.
#
# Design decisions captured here so future readers don't have to mine the
# brief:
#
#   1. **Auto-clear status when class moves away from thread.** When the
#      caller changes class from 'thread' to 'anchor' or 'log' (and does NOT
#      pass an explicit status flag), the writer NULLs the status column on
#      its own. Rationale: status is semantically meaningful only for
#      class=thread (schema.sql L576-578). Leaving a stale 'open' status on
#      an anchor row would silently corrupt the lifecycle view. Forcing
#      callers to pass --status=null on every class change is busywork that
#      hides the rule rather than naming it.
#
#   2. **Empty-string sentinel for explicit clear.** The Python signature
#      uses ``status=None`` to mean "don't touch this column". When the CLI
#      caller passes ``--status=null`` (the literal string), we translate it
#      to ``status=""`` in the kwargs -- the writer treats the empty string
#      as "explicitly clear to NULL". This separation is the only way to
#      distinguish "leave alone" from "wipe" when both routes need to coexist
#      on the same function signature.
# ---------------------------------------------------------------------------

VALID_MEMORY_CLASSES = ("anchor", "thread", "log")
VALID_MEMORY_STATUSES = ("open", "carry_forward", "graduated", "closed")


def reclassify_memory(
    workspace: str,
    name: str,
    *,
    class_: str | None = None,
    status: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Update the ``class`` and/or ``status`` columns on a curated memory row.

    Args:
        workspace: Workspace name (FK -> workspaces.name).
        name:      Curated memory slug; the row must already exist.
        class_:    New value for the ``class`` column. ``None`` means
                   "do not touch". Must be one of VALID_MEMORY_CLASSES
                   when set. The trailing underscore avoids the Python
                   reserved word.
        status:    New value for the ``status`` column. ``None`` means
                   "do not touch". Empty string ``""`` is the explicit-
                   clear sentinel: it nulls the column. Otherwise must be
                   one of VALID_MEMORY_STATUSES.
        db_path:   Optional explicit DB path (used by tests).

    Behaviour:
        * If neither ``class_`` nor ``status`` is supplied (both None) the
          writer raises ``ValueError`` -- there is nothing to do.
        * ``status`` may only resolve to a non-NULL value when the resulting
          class is ``"thread"``. If the caller asks for ``status="open"``
          on a row that is (or will be) class=anchor/log, ValueError fires
          with a message explaining the constraint.
        * When the caller changes class FROM 'thread' TO 'anchor' or 'log'
          and does NOT pass a status flag, status is auto-NULLed.

    Returns:
        ``{"status": "applied", "action": "reclassified", "name": name,
           "class": ..., "status": ..., "updated_at": ...}``.

    Raises:
        ValueError: missing row, invalid enum, missing both flags, or
                    status-without-thread.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    # Disambiguate the three input modes for status:
    #   * status is None        -> do not touch the column
    #   * status == ""          -> explicit clear (write NULL)
    #   * status == "<value>"   -> set to value; must be in enum
    status_explicit_clear = (status == "")
    status_touches_column = (status is not None)

    if class_ is None and not status_touches_column:
        raise ValueError(
            "reclassify_memory requires at least one of class_ or status"
        )

    if class_ is not None and class_ not in VALID_MEMORY_CLASSES:
        raise ValueError(
            f"invalid class {class_!r}; must be one of "
            f"{list(VALID_MEMORY_CLASSES)}"
        )

    if (status_touches_column
            and not status_explicit_clear
            and status not in VALID_MEMORY_STATUSES):
        raise ValueError(
            f"invalid status {status!r}; must be one of "
            f"{list(VALID_MEMORY_STATUSES)} (or empty string to clear)"
        )

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT class, status FROM memory "
            "WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory {name!r} not found in workspace {workspace!r}"
            )

        current_class = row["class"]
        current_status = row["status"]

        new_class = class_ if class_ is not None else current_class

        # Decide the new status value:
        #   * Caller passed status explicit-clear -> NULL.
        #   * Caller passed status="<value>"      -> that value (already
        #                                            enum-checked above).
        #   * Caller did NOT pass status, AND class moved from thread to
        #     non-thread -> auto-NULL.
        #   * Otherwise -> leave current_status untouched.
        if status_touches_column:
            new_status = None if status_explicit_clear else status
        elif (current_class == "thread"
              and class_ is not None
              and class_ != "thread"):
            new_status = None  # auto-clear on demotion / promotion
        else:
            new_status = current_status

        if new_status is not None and new_class != "thread":
            raise ValueError(
                "status only applies to class=thread "
                f"(resulting class={new_class!r}, status={new_status!r})"
            )

        now = _now_iso()
        con.execute(
            "UPDATE memory SET class = ?, status = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (new_class, new_status, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "reclassified",
            "workspace": workspace,
            "name": name,
            "class": new_class,
            "memory_status": new_status,  # avoid colliding with envelope 'status'
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: search_memory_curated (FTS5 over the memory table)
# ---------------------------------------------------------------------------

import re as _re_for_fts

_MEMORY_FTS_SAFE = _re_for_fts.compile(r"^[A-Za-z0-9_*\s\"]+$")


def _prepare_memory_fts_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q
    if _MEMORY_FTS_SAFE.match(q):
        return q
    return '"' + q.replace('"', '""') + '"'


def search_memory_curated(
    workspace: str,
    query: str,
    *,
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Run FTS5 MATCH against ``memory_fts`` and join with the ``memory`` table."""
    fts_q = _prepare_memory_fts_query(query)
    con = _connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT m.name, m.type, m.description,
                   snippet(memory_fts, -1, '[', ']', '...', 16) AS snippet,
                   bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memory m ON m.rowid = memory_fts.rowid
            WHERE memory_fts MATCH ?
              AND m.workspace = ?
              AND m.deleted_at IS NULL
            ORDER BY rank
            LIMIT ?
            """,
            (fts_q, workspace, limit),
        ).fetchall()
        return [
            {
                "name": r["name"],
                "type": r["type"],
                "description": r["description"],
                "snippet": r["snippet"],
                "rank": r["rank"],
            }
            for r in rows
        ]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: memory read helpers
# ---------------------------------------------------------------------------

def get_memory(
    workspace: str,
    name: str,
    *,
    include_deleted: bool = False,
    db_path: Path | None = None,
) -> dict | None:
    """Return a curated memory row as a dict, or ``None`` when missing.

    Tombstoned rows (``deleted_at`` non-NULL, scan-v2 SV3) are excluded by
    default so a soft-deleted memory reads as absent. Pass
    ``include_deleted=True`` to reach a tombstoned row (e.g. for an explicit
    hard-delete or a recovery inspection).
    """
    con = _connect(db_path)
    try:
        sql = (
            "SELECT workspace, name, type, description, body, project_ref, "
            "       origin_session_id, updated_at, deleted_at "
            "FROM memory WHERE workspace = ? AND name = ?"
        )
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = con.execute(sql, (workspace, name)).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    finally:
        con.close()


def list_memory(
    workspace: str,
    *,
    type: str | None = None,
    include_deleted: bool = False,
    db_path: Path | None = None,
) -> list[dict]:
    """List curated memory rows, optionally filtered by ``type``.

    Tombstoned rows (``deleted_at`` non-NULL, scan-v2 SV3) are excluded by
    default; pass ``include_deleted=True`` to include them.
    """
    con = _connect(db_path)
    try:
        where = ["workspace = ?"]
        params: list = [workspace]
        if type is not None:
            where.append("type = ?")
            params.append(type)
        if not include_deleted:
            where.append("deleted_at IS NULL")
        sql = (
            "SELECT name, type, description, updated_at "
            "FROM memory WHERE " + " AND ".join(where) + " ORDER BY name"
        )
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: brief field patch
# ---------------------------------------------------------------------------

_BRIEF_PATCHABLE_FIELDS = (
    "objective",
    "context",
    "approach",
    "out_of_scope",
    "description",
    "title",
    # v5 (T5.4): metadata fields added to the whitelist so they are
    # reachable via `gaia brief edit --headless --field=<field>`.
    "surface_type",
    "topic_key",
)


def update_brief_field(
    workspace: str,
    name: str,
    field: str,
    content: str,
    *,
    append: bool = False,
    db_path: Path | None = None,
) -> dict:
    if field not in _BRIEF_PATCHABLE_FIELDS:
        raise ValueError(
            f"invalid brief field {field!r}; must be one of "
            f"{list(_BRIEF_PATCHABLE_FIELDS)}"
        )
    if content is None or content == "":
        raise ValueError("content cannot be empty")

    column = "objective" if field == "description" else field

    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT id, {column} FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"brief '{name}' not found in workspace '{workspace}'"
            )

        existing = row[column] or ""
        if append and existing:
            new_value = f"{existing}\n\n{content}"
            action = "appended"
        else:
            new_value = content
            action = "overwritten"

        now = _now_iso()
        con.execute(
            f"UPDATE briefs SET {column} = ?, updated_at = ? WHERE id = ?",
            (new_value, now, row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "field": field,
            "action": action,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: plan CRUD
# ---------------------------------------------------------------------------

VALID_PLAN_LIFECYCLE_STATUSES = ("draft", "active", "closed")

# Brief statuses that reject plan persistence (D11, D13).
# Saving a plan against a closed or archived brief is always a mistake --
# fail-fast rather than silently creating an orphaned plan.
_PLAN_SAVE_REJECTED_BRIEF_STATUSES = frozenset({"closed", "archived"})


def _resolve_brief_id(
    con: sqlite3.Connection,
    workspace: str,
    brief_name: str,
) -> int | None:
    row = con.execute(
        "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
        (workspace, brief_name),
    ).fetchone()
    return row["id"] if row else None


def upsert_plan(
    workspace: str,
    brief_name: str,
    *,
    content: str | None = None,
    status: str = "draft",
    db_path: Path | None = None,
) -> dict:
    """Upsert the plan attached to ``brief_name``.

    Canonical persistence path for plans
    ------------------------------------
    This function is the ONE canonical writer for plan content. It is invoked
    by ``gaia plan save --brief=<name> --content="..." [--status=...]`` and
    has UPSERT semantics:

    * If no plan row exists for the brief -> INSERT a new row.
    * If a plan row exists -> UPDATE ``status`` and ``content`` (preserving
      the existing ``content`` when ``content=None`` is passed).

    The ``plans`` row id is permanent across re-runs of ``gaia plan save``;
    only the content and status fields are updated. Verify after saving with
    ``gaia plan show <brief>``.

    Note that this writer is NOT full-sync. It only touches
    ``plans.status`` and ``plans.content``. The ``tasks`` table is a
    separate child of ``plans`` (FK CASCADE on delete). To mutate the
    task list, use the granular writers ``add_task_to_plan``,
    ``remove_task_from_plan``, and ``reorder_tasks`` (NOT this function).

    Anti-patterns -- DO NOT use any of these:

    * ``gaia brief edit <name>`` to persist a plan. ``gaia brief edit``
      writes to the ``briefs`` table, not the ``plans`` table. Plans and
      briefs are separate rows in separate tables. Edits applied to the
      brief body do not appear in ``gaia plan show``.

    * ``EDITOR=cp /tmp/plan.md gaia brief edit <name>``. This was a hack
      used during session 2026-05-22 to side-load plan content. It bypasses
      DB semantics, writes to the wrong table, and produces a stale brief
      body that does not appear in ``gaia plan show``. Never repeat this
      pattern. Use ``gaia plan save`` with ``--content="$(cat /tmp/plan.md)"``
      if the content is too large to pass inline.

    Raises ValueError if the brief does not exist, if the brief status is
    ``closed`` or ``archived`` (D11 fail-fast), or if the status enum is
    invalid.
    """
    if status not in VALID_PLAN_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid plan status {status!r}; must be one of "
            f"{list(VALID_PLAN_LIFECYCLE_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        # D11 / D13: fail-fast guard -- reject plan persistence against
        # a brief whose lifecycle has ended.
        brief_status_row = con.execute(
            "SELECT status FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief_status_row is not None:
            brief_status = brief_status_row["status"]
            if brief_status in _PLAN_SAVE_REJECTED_BRIEF_STATUSES:
                raise ValueError(
                    f"cannot save plan for brief '{brief_name}': brief is "
                    f"'{brief_status}'. Only briefs with status in "
                    f"{{draft, open, in-progress}} accept new plans."
                )

        existing = con.execute(
            "SELECT id, status, content FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()

        now = _now_iso()
        if existing is None:
            con.execute(
                "INSERT INTO plans (brief_id, status, content, created_at, "
                "                   updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (brief_id, status, content, now, now),
            )
            plan_id = con.execute(
                "SELECT id FROM plans WHERE brief_id = ?",
                (brief_id,),
            ).fetchone()["id"]
            action = "inserted"
            new_status = status
        else:
            plan_id = existing["id"]
            new_status = status
            new_content = content if content is not None else existing["content"]
            con.execute(
                "UPDATE plans SET status = ?, content = ?, updated_at = ? "
                "WHERE id = ?",
                (new_status, new_content, now, plan_id),
            )
            action = "updated"

        con.commit()
        return {
            "status": "applied",
            "action": action,
            "brief_name": brief_name,
            "plan_id": plan_id,
            "plan_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def get_plan(
    workspace: str,
    brief_name: str,
    *,
    db_path: Path | None = None,
) -> dict | None:
    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            return None
        row = con.execute(
            "SELECT id, brief_id, status, content, created_at, updated_at "
            "FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if row is None:
            return None
        out = {k: row[k] for k in row.keys()}
        out["brief_name"] = brief_name
        return out
    finally:
        con.close()


def list_plans(
    workspace: str,
    *,
    brief_name: str | None = None,
    status: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    con = _connect(db_path)
    try:
        sql = (
            "SELECT p.id, p.brief_id, p.status, p.created_at, p.updated_at, "
            "       b.name AS brief_name "
            "FROM plans p "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.workspace = ? "
        )
        params: list = [workspace]
        if brief_name is not None:
            sql += "AND b.name = ? "
            params.append(brief_name)
        if status is not None:
            sql += "AND p.status = ? "
            params.append(status)
        sql += "ORDER BY b.name"
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_plan(
    workspace: str,
    brief_name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            return False
        cur = con.execute("DELETE FROM plans WHERE brief_id = ?", (brief_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def set_plan_status(
    workspace: str,
    brief_name: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("plans")

    if new_status not in VALID_PLAN_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid plan status {new_status!r}; must be one of "
            f"{list(VALID_PLAN_LIFECYCLE_STATUSES)}"
        )

    from gaia.state.transitions import assert_legal_plan_lifecycle

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        row = con.execute(
            "SELECT id, status FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}' in workspace "
                f"'{workspace}'"
            )

        old_status = row["status"] or "draft"
        if old_status == new_status:
            return {
                "brief_name": brief_name,
                "old_status": old_status,
                "new_status": new_status,
                "action": "noop",
                "warnings": [],
            }

        assert_legal_plan_lifecycle(old_status, new_status)

        con.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now_iso(), row["id"]),
        )
        con.commit()

        # D11 (advisory): when closing a plan, check AC satisfaction.
        # Emit warnings for pending/blocked ACs -- the close is still
        # permitted (ACs may be intentionally deferred), but the caller
        # should surface any unsatisfied criteria to the user.
        warnings: list[str] = []
        if new_status == "closed":
            pending_acs = con.execute(
                "SELECT ac_id, status FROM acceptance_criteria "
                "WHERE brief_id = ? AND status != 'done'",
                (brief_id,),
            ).fetchall()
            warnings = [
                f"AC '{r['ac_id']}' is status='{r['status']}' (not done)"
                for r in pending_acs
            ]

        return {
            "brief_name": brief_name,
            "old_status": old_status,
            "new_status": new_status,
            "action": "updated",
            "warnings": warnings,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: set_task_status, set_ac_status, set_milestone_status (v5)
# ---------------------------------------------------------------------------

def set_task_status(
    workspace: str,
    brief_name: str,
    task_id: int,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition a task's ``status`` after validating the move is legal.

    Navigates workspace -> brief_name -> brief_id -> plan_id -> task row
    by ``(plan_id, order_num)`` where ``task_id`` is the order_num integer.

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")
    from gaia.state import VALID_TASK_STATUSES
    from gaia.state.transitions import assert_legal_task_lifecycle

    if new_status not in VALID_TASK_STATUSES:
        raise ValueError(
            f"invalid task status {new_status!r}; must be one of "
            f"{list(VALID_TASK_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}' in workspace '{workspace}'"
            )
        plan_id = plan_row["id"]

        task_row = con.execute(
            "SELECT id, status FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, task_id),
        ).fetchone()
        if task_row is None:
            raise ValueError(
                f"task with order_num={task_id} not found in plan for brief "
                f"'{brief_name}'"
            )

        old_status = task_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": task_id,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_task_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (new_status, task_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": task_id,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def set_ac_status(
    workspace: str,
    brief_name: str,
    ac_id: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition an acceptance criterion's ``status`` after validating the move.

    Navigates workspace -> brief_name -> brief_id -> AC row by (brief_id, ac_id).

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("acceptance_criteria")
    from gaia.state import VALID_AC_STATUSES
    from gaia.state.transitions import assert_legal_ac_lifecycle

    if new_status not in VALID_AC_STATUSES:
        raise ValueError(
            f"invalid AC status {new_status!r}; must be one of "
            f"{list(VALID_AC_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        ac_row = con.execute(
            "SELECT id, status FROM acceptance_criteria "
            "WHERE brief_id = ? AND ac_id = ?",
            (brief_id, ac_id),
        ).fetchone()
        if ac_row is None:
            raise ValueError(
                f"AC '{ac_id}' not found in brief '{brief_name}'"
            )

        old_status = ac_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": ac_id,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_ac_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE acceptance_criteria SET status = ? WHERE id = ?",
            (new_status, ac_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": ac_id,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def set_milestone_status(
    workspace: str,
    brief_name: str,
    milestone_name: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition a milestone's ``status`` after validating the move.

    Navigates workspace -> brief_name -> brief_id -> milestone row by
    (brief_id, name).

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("milestones")
    from gaia.state import VALID_MILESTONE_STATUSES
    from gaia.state.transitions import assert_legal_milestone_lifecycle

    if new_status not in VALID_MILESTONE_STATUSES:
        raise ValueError(
            f"invalid milestone status {new_status!r}; must be one of "
            f"{list(VALID_MILESTONE_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        ms_row = con.execute(
            "SELECT id, status FROM milestones "
            "WHERE brief_id = ? AND name = ?",
            (brief_id, milestone_name),
        ).fetchone()
        if ms_row is None:
            raise ValueError(
                f"milestone '{milestone_name}' not found in brief '{brief_name}'"
            )

        old_status = ms_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": milestone_name,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_milestone_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE milestones SET status = ? WHERE id = ?",
            (new_status, ms_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": milestone_name,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: granular task mutation in plans (v5 -- T5.3)
# ---------------------------------------------------------------------------
#
# upsert_plan is intentionally NOT full-sync (D7): it only touches
# plans.status and plans.content. The tasks child table needs its own
# granular writers. tasks are curator_only=False (D1) -- subagents allowed.


def add_task_to_plan(
    workspace: str,
    brief_name: str,
    order_num: int,
    goal: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a new task row at ``order_num`` in the plan attached to brief.

    Raises ValueError on duplicate order_num within the plan or missing plan.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    if not goal or not goal.strip():
        raise ValueError("task goal cannot be empty")
    if order_num is None or order_num < 1:
        raise ValueError("order_num must be a positive integer")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        collision = con.execute(
            "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, order_num),
        ).fetchone()
        if collision is not None:
            raise ValueError(
                f"task with order_num={order_num} already exists in plan "
                f"for brief '{brief_name}'"
            )

        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, 'pending')",
            (plan_id, order_num, goal),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "brief_name": brief_name,
            "order_num": order_num,
        }
    finally:
        con.close()


def remove_task_from_plan(
    workspace: str,
    brief_name: str,
    order_num: int,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete a task row by (plan, order_num).

    Raises ValueError if the brief, plan, or task does not exist.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        cur = con.execute(
            "DELETE FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, order_num),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"task with order_num={order_num} not found in plan for "
                f"brief '{brief_name}'"
            )
        con.commit()
        return {
            "status": "applied",
            "action": "deleted",
            "brief_name": brief_name,
            "order_num": order_num,
        }
    finally:
        con.close()


def reorder_tasks(
    workspace: str,
    brief_name: str,
    swaps: list[list[int]],
    *,
    db_path: Path | None = None,
) -> dict:
    """Swap task order_num pairs in a single transaction.

    ``swaps`` is a list of ``[from_order, to_order]`` pairs. Each swap
    exchanges the order_num of the two tasks atomically. If either task
    does not exist, the entire operation is rolled back.

    Raises ValueError on missing brief/plan or task not found.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    if not swaps:
        raise ValueError("swaps cannot be empty")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        applied: list[dict] = []
        try:
            con.execute("BEGIN")
            for pair in swaps:
                if len(pair) != 2:
                    raise ValueError(
                        f"swap entries must be [from, to] pairs, got {pair!r}"
                    )
                from_o, to_o = pair[0], pair[1]
                from_row = con.execute(
                    "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
                    (plan_id, from_o),
                ).fetchone()
                to_row = con.execute(
                    "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
                    (plan_id, to_o),
                ).fetchone()
                if from_row is None:
                    raise ValueError(
                        f"task with order_num={from_o} not found in plan"
                    )
                if to_row is None:
                    raise ValueError(
                        f"task with order_num={to_o} not found in plan"
                    )
                # Three-step swap via temporary negative order_num to avoid
                # the UNIQUE/CHECK conflict if we ever add a unique index.
                # tasks table currently has no unique index on (plan_id,
                # order_num), so two-step would also work. Three-step is
                # defensive.
                con.execute(
                    "UPDATE tasks SET order_num = -1 * order_num WHERE id = ?",
                    (from_row["id"],),
                )
                con.execute(
                    "UPDATE tasks SET order_num = ? WHERE id = ?",
                    (from_o, to_row["id"]),
                )
                con.execute(
                    "UPDATE tasks SET order_num = ? WHERE id = ?",
                    (to_o, from_row["id"]),
                )
                applied.append({"from": from_o, "to": to_o})
            con.commit()
        except Exception:
            con.rollback()
            raise

        return {
            "status": "applied",
            "action": "reordered",
            "brief_name": brief_name,
            "swaps": applied,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: wipe_workspace
# ---------------------------------------------------------------------------

def _reinsert_row(con: sqlite3.Connection, table: str, row: sqlite3.Row) -> None:
    """Re-INSERT a captured ``sqlite3.Row`` back into ``table`` verbatim.

    Column list is derived from the row's own keys, so the helper survives
    schema evolution without hard-coding column names. Used by
    :func:`wipe_workspace` to restore memory / memory_links / the workspaces row
    after a CASCADE wipe.
    """
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    con.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
        tuple(row[c] for c in cols),
    )


def wipe_workspace(
    workspace: str,
    *,
    preserve_memory: bool = True,
    db_path: Path | None = None,
) -> None:
    """Delete the workspaces row for `workspace`. FK CASCADE removes all
    child rows (projects, apps, integrations, etc.) automatically.

    Memory preservation (scan-v2 SV3, Vector 4)
    -------------------------------------------
    ``memory`` and ``memory_links`` are FK'd to ``workspaces`` with ON DELETE
    CASCADE, so a naive workspace delete DESTROYS all curated memory for the
    workspace. That is the loss vector `migrate_workspace.py` triggered on every
    re-scan. This function now DECOUPLES memory from the CASCADE at the app
    layer -- the safer of the two options (the alternative, changing the FK to
    ON DELETE SET NULL / RESTRICT, would require a full ``memory`` table rebuild
    per the v21->v22 precedent).

    With ``preserve_memory=True`` (the DEFAULT): inside a single transaction the
    memory rows, memory_links rows, and the workspaces row itself are captured
    BEFORE the delete; the CASCADE then fires as normal; and the workspaces row
    (with its identity / created_at / status preserved) plus every memory /
    memory_links row is re-inserted. Net effect: projects and all scannable
    children are cleared (what a re-scan wants), while curated memory survives
    untouched. The memory_ai / memory_links insert triggers keep the FTS mirror
    consistent.

    ``preserve_memory=False`` performs the original full CASCADE (memory
    destroyed). This exists ONLY for explicit human curation -- e.g.
    ``gaia context wipe --purge-memory`` behind its confirmation prompt --
    honouring "never hard-delete curated memory except by explicit human
    curation".
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            if preserve_memory:
                ws_row = con.execute(
                    "SELECT * FROM workspaces WHERE name = ?", (workspace,)
                ).fetchone()
                mem_rows = con.execute(
                    "SELECT * FROM memory WHERE workspace = ?", (workspace,)
                ).fetchall()
                link_rows = con.execute(
                    "SELECT * FROM memory_links WHERE workspace = ?", (workspace,)
                ).fetchall()

                con.execute("DELETE FROM workspaces WHERE name = ?", (workspace,))

                # Restore the workspaces row (only when it existed) so the FK
                # target for the re-inserted memory is present again, then the
                # memory + links. If the workspace had no row, there was nothing
                # to preserve and the delete was a no-op.
                if ws_row is not None:
                    _reinsert_row(con, "workspaces", ws_row)
                    for r in mem_rows:
                        _reinsert_row(con, "memory", r)
                    for r in link_rows:
                        _reinsert_row(con, "memory_links", r)
            else:
                con.execute("DELETE FROM workspaces WHERE name = ?", (workspace,))
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Surgical reconciliation helpers (workspace-identity brief, M4/T10)
# ---------------------------------------------------------------------------
#
# `gaia scan` reconciles the `projects` table workspace-by-workspace: it
# upserts the repos it finds under the SCANNED workspace and prunes
# (mark_missing_in / delete_missing_in) only WITHIN that same workspace. Two
# consequences make a plain rescan unable to repair historical drift:
#
#   1. A stale row with project_identity=NULL living under a DIFFERENT
#      workspace than the one being scanned is never collapsed (the identity-
#      collapse path in upsert_project only fires for a non-null identity) and
#      never pruned (prune is scoped to the scanned workspace) -- so a rescan
#      RESURRECTS the repo as a fresh duplicate row and leaves the stale row
#      ORPHANED under its old workspace.
#   2. project_context_contracts is not touched by scan at all, so a contract
#      written under the wrong workspace can only be corrected by moving it.
#
# wipe_workspace is too blunt for a LIVE workspace: it CASCADE-deletes the
# workspaces row and EVERY child (projects, memory, briefs, episodes, PCC).
# The helper below is the surgical, workspace-preserving primitive that
# repairs drift without destroying collateral:
#
#   relocate_contracts -- re-key project_context_contracts rows between
#                         workspaces (the only correction path for mis-keyed PCC).
#
# NOTE: a `delete_projects` sibling (targeted deletion of `projects` rows)
# existed here as a one-time reconciliation tool (workspace-identity brief
# M4/T10) and was removed in scan-v2 SV4 -- agents must never hold the power
# to hard-delete project rows; `mark_missing_in` (soft-delete, scanner-owned)
# and the resolve-move adjudication (re-key + tombstone, see
# `resolve_move_candidate` below) are the only sanctioned paths that touch a
# project row's lifecycle.
# ---------------------------------------------------------------------------


def relocate_contracts(
    from_workspace: str,
    to_workspace: str,
    contracts: Sequence[str],
    *,
    on_conflict: str = "error",
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Re-key ``project_context_contracts`` rows from one workspace to another.

    `gaia scan` itself never writes ``project_context_contracts`` -- it only
    populates the raw ``projects`` index. The decoupled promotion stage
    (``tools/scan/promote.py::promote_workspace``, invoked by ``gaia scan``
    after a successful classify pass) does write into it, but only
    scan-owned fields on entries keyed by physical identity (path / remote),
    never the ``workspace`` PK column itself. So a contract mis-keyed to the
    wrong workspace (e.g. AOS project context mis-keyed to the 'me'
    workspace) still cannot self-correct via scan or promotion -- it can only
    be corrected by moving the row. This re-keys the named
    contracts by UPDATEing the ``workspace`` PK column IN PLACE -- payload,
    metadata and updated_at are preserved, and the ``trg_pcc_history`` AFTER
    UPDATE trigger records the move in project_context_contracts_history.

    ``to_workspace`` must satisfy the FK to workspaces(name); it is created via
    :func:`_ensure_workspace_row` when absent. The PK is
    (workspace, contract_name), so if (to_workspace, contract) ALREADY exists
    ``on_conflict`` decides:

        'error'     -- raise ValueError, move nothing (default; never clobber)
        'skip'      -- leave both rows; report the contract under 'skipped'
        'overwrite' -- delete the target row first, then move the source row

    Idempotent: a contract already absent from ``from_workspace`` is reported
    under 'missing' and is a no-op, so re-running after a partial apply is safe.

    Args:
        from_workspace: Source workspace (current, wrong key).
        to_workspace: Destination workspace (correct key).
        contracts: Contract names to move (project_context_contracts.contract_name).
        on_conflict: 'error' | 'skip' | 'overwrite' (see above).
        dry_run: When True, mutate nothing; report the classification only.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"|"preview", "from": ..., "to": ...,
         "moved": [...], "skipped": [...], "missing": [...], "overwritten": [...]}.

    Raises:
        ValueError: on invalid on_conflict, empty contracts, from==to, or an
            unresolved PK conflict when on_conflict='error'.
    """
    if on_conflict not in ("error", "skip", "overwrite"):
        raise ValueError(
            f"relocate_contracts: invalid on_conflict {on_conflict!r}; "
            f"must be 'error', 'skip', or 'overwrite'"
        )
    contract_list = list(contracts)
    if not contract_list:
        raise ValueError("relocate_contracts: at least one contract is required")
    if from_workspace == to_workspace:
        raise ValueError(
            "relocate_contracts: from_workspace and to_workspace are identical"
        )

    moved: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    overwritten: list[str] = []

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            if not dry_run:
                _ensure_workspace_row(con, to_workspace)

            for name in contract_list:
                src = con.execute(
                    "SELECT 1 FROM project_context_contracts "
                    "WHERE workspace = ? AND contract_name = ?",
                    (from_workspace, name),
                ).fetchone()
                if src is None:
                    missing.append(name)
                    continue

                dst = con.execute(
                    "SELECT 1 FROM project_context_contracts "
                    "WHERE workspace = ? AND contract_name = ?",
                    (to_workspace, name),
                ).fetchone()
                if dst is not None:
                    if on_conflict == "error":
                        raise ValueError(
                            f"relocate_contracts: target already has contract "
                            f"{name!r} under workspace {to_workspace!r} "
                            f"(on_conflict='error')"
                        )
                    if on_conflict == "skip":
                        skipped.append(name)
                        continue
                    # overwrite
                    if not dry_run:
                        con.execute(
                            "DELETE FROM project_context_contracts "
                            "WHERE workspace = ? AND contract_name = ?",
                            (to_workspace, name),
                        )
                    overwritten.append(name)

                if not dry_run:
                    con.execute(
                        "UPDATE project_context_contracts SET workspace = ? "
                        "WHERE workspace = ? AND contract_name = ?",
                        (to_workspace, from_workspace, name),
                    )
                moved.append(name)

            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    return {
        "status": "preview" if dry_run else "applied",
        "from": from_workspace,
        "to": to_workspace,
        "moved": moved,
        "skipped": skipped,
        "missing": missing,
        "overwritten": overwritten,
    }


def relocate_memory(
    from_workspace: str,
    to_workspace: str,
    names: Sequence[str],
    *,
    on_conflict: str = "error",
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Re-key curated ``memory`` rows (and their intra-set ``memory_links``)
    between workspaces -- the mirror of :func:`relocate_contracts` for the
    curated memory table.

    `gaia scan` never touches ``memory``, so a memory row written under the
    wrong workspace (e.g. the 'project_gaia_roadmap' / 'user_blog_articles'
    notes mis-keyed to the 'rnd' workspace but belonging to 'me') can only be
    corrected by moving the row. This re-keys the named rows by UPDATEing the
    ``workspace`` PK column IN PLACE: every other column (type, description,
    body, origin_session_id, updated_at, class, status) is preserved untouched,
    and the ``memory_au`` AFTER UPDATE trigger keeps the ``memory_fts`` mirror
    in sync (workspace is an FTS column, so the mirror row is rewritten).

    memory_links follow the notes: a link under ``from_workspace`` whose BOTH
    endpoints are in the moved set is re-keyed to ``to_workspace`` (the edge
    travels with the pair). A link with only ONE endpoint in the moved set
    cannot stay consistent under the single-workspace link model -- scan-v2 SV3
    DELETES that now-dangling edge (its endpoint left the workspace) and reports
    it under 'partial_links'. The link is derived graph metadata, not curated
    memory: both endpoint rows survive untouched; only the broken edge is
    removed, so nothing is lost silently and no dangling reference is left
    behind.

    Provenance (scan-v2 SV3): the workspace re-key UPDATE fires the
    ``trg_memory_history`` trigger, which records before_workspace ->
    after_workspace for each moved row -- the origin of every move is preserved
    in ``memory_history`` automatically, no explicit trace-write needed.

    ``to_workspace`` must satisfy the FK to workspaces(name); it is created via
    :func:`_ensure_workspace_row` when absent. PK is (workspace, name); on a
    (to_workspace, name) collision ``on_conflict`` decides:

        'error'     -- raise ValueError, move nothing (default; never clobber)
        'skip'      -- leave both rows; report the name under 'skipped'
        'overwrite' -- delete the target row first, then move the source row

    Idempotent: a name already absent from ``from_workspace`` is reported under
    'missing' and is a no-op, so re-running after a partial apply is safe.

    Subject to the curated-memory write guard
    (:func:`_assert_dispatch_can_write_memory`): like every other memory
    mutator, this refuses writes from a NON-curator subagent dispatch. Run it
    from a human shell or the orchestrator/operator context.

    Returns:
        {"status": "applied"|"preview", "from": ..., "to": ...,
         "moved": [...], "skipped": [...], "missing": [...],
         "overwritten": [...],
         "links_moved": [{"src","dst","kind"}...],
         "partial_links": [{"src","dst","kind"}...]}.

    Raises:
        ValueError: invalid on_conflict, empty names, from==to, or an
            unresolved PK conflict when on_conflict='error'.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if on_conflict not in ("error", "skip", "overwrite"):
        raise ValueError(
            f"relocate_memory: invalid on_conflict {on_conflict!r}; "
            f"must be 'error', 'skip', or 'overwrite'"
        )
    name_list = list(names)
    if not name_list:
        raise ValueError("relocate_memory: at least one name is required")
    if from_workspace == to_workspace:
        raise ValueError(
            "relocate_memory: from_workspace and to_workspace are identical"
        )

    moved: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    overwritten: list[str] = []
    links_moved: list[dict] = []
    partial_links: list[dict] = []

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            if not dry_run:
                _ensure_workspace_row(con, to_workspace)

            for name in name_list:
                src = con.execute(
                    "SELECT 1 FROM memory WHERE workspace = ? AND name = ?",
                    (from_workspace, name),
                ).fetchone()
                if src is None:
                    missing.append(name)
                    continue

                dst = con.execute(
                    "SELECT 1 FROM memory WHERE workspace = ? AND name = ?",
                    (to_workspace, name),
                ).fetchone()
                if dst is not None:
                    if on_conflict == "error":
                        raise ValueError(
                            f"relocate_memory: target already has memory "
                            f"{name!r} under workspace {to_workspace!r} "
                            f"(on_conflict='error')"
                        )
                    if on_conflict == "skip":
                        skipped.append(name)
                        continue
                    # overwrite: drop the target row first (memory_ad keeps FTS
                    # in sync); its own links under `to` are left as-is.
                    if not dry_run:
                        con.execute(
                            "DELETE FROM memory WHERE workspace = ? AND name = ?",
                            (to_workspace, name),
                        )
                    overwritten.append(name)

                if not dry_run:
                    con.execute(
                        "UPDATE memory SET workspace = ? "
                        "WHERE workspace = ? AND name = ?",
                        (to_workspace, from_workspace, name),
                    )
                moved.append(name)

            # Re-key links that live entirely within the moved set.
            moved_set = set(moved)
            if moved_set:
                link_rows = con.execute(
                    "SELECT src_name, dst_name, kind FROM memory_links "
                    "WHERE workspace = ?",
                    (from_workspace,),
                ).fetchall()
                for lr in link_rows:
                    src_in = lr["src_name"] in moved_set
                    dst_in = lr["dst_name"] in moved_set
                    if not (src_in or dst_in):
                        continue
                    entry = {
                        "src": lr["src_name"],
                        "dst": lr["dst_name"],
                        "kind": lr["kind"],
                    }
                    if src_in and dst_in:
                        if not dry_run:
                            con.execute(
                                "UPDATE memory_links SET workspace = ? "
                                "WHERE workspace = ? AND src_name = ? "
                                "AND dst_name = ? AND kind = ?",
                                (to_workspace, from_workspace,
                                 lr["src_name"], lr["dst_name"], lr["kind"]),
                            )
                        links_moved.append(entry)
                    else:
                        # Only one endpoint moved. Under the single-workspace
                        # link model this edge is now referentially dangling:
                        # one of its endpoints no longer exists under
                        # ``from_workspace`` and cannot be re-homed to
                        # ``to_workspace`` (the other endpoint stayed). Leaving
                        # it in place is silent corruption -- scan-v2 SV3 removes
                        # the dangling edge and reports it under 'partial_links'
                        # so nothing is lost silently. A link is derived graph
                        # metadata, not curated memory: both endpoint rows (the
                        # data) survive untouched; only the broken edge is
                        # dropped. Never touches memory rows.
                        if not dry_run:
                            con.execute(
                                "DELETE FROM memory_links "
                                "WHERE workspace = ? AND src_name = ? "
                                "AND dst_name = ? AND kind = ?",
                                (from_workspace, lr["src_name"],
                                 lr["dst_name"], lr["kind"]),
                            )
                        partial_links.append(entry)

            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    return {
        "status": "preview" if dry_run else "applied",
        "from": from_workspace,
        "to": to_workspace,
        "moved": moved,
        "skipped": skipped,
        "missing": missing,
        "overwritten": overwritten,
        "links_moved": links_moved,
        "partial_links": partial_links,
    }


# ---------------------------------------------------------------------------
# scan-v2 SV4: move-candidate adjudication (superseded_by write / re-key).
#
# `gaia scan` (SV2) only DETECTS and REPORTS a move -- it pairs a project that
# vanished from one workspace 1:1 (by normalized remote) with a project that
# appeared in another, and emits a `move_candidate`. It never mutates the
# lineage. A human then adjudicates each candidate; `resolve_move_candidate`
# below is the write path that EXECUTES an adjudicated 'movido' decision.
#
# Post-scan, a detected move leaves TWO rows in `projects`:
#   * the OLD row (the `from` side): now status='missing' (soft-deleted by the
#     reconcile pass), still carrying the pre-move project_identity (its
#     git-common-dir at the old location) and any agent-owned `description`.
#   * the NEW row (the `to` side): freshly upserted, status='active', carrying
#     a DIFFERENT project_identity (the git-common-dir changed when the repo
#     physically moved). This is the successor.
#
# The 'movido' adjudication links the two WITHOUT ever hard-deleting either:
#   * When the successor row ALREADY exists (the realistic post-scan state, and
#     the only state a move_candidate is ever emitted from): the old row is
#     tombstoned (status='missing') and its `superseded_by` column is set to the
#     successor's project_identity -- the forward link that records "this row's
#     project moved to the row bearing identity X". Both rows survive; the
#     successor stays the active canonical at the new (workspace, name). A
#     merge/re-key of the old row ONTO the successor slot is impossible without
#     destroying the successor row (a hard delete), which the no-hard-delete
#     principle forbids -- so the link, not a key rewrite, is the mechanism.
#   * When the successor row does NOT exist (defensive path, e.g. adjudicating
#     from a cross-DB or dry-run report where the new location was never
#     scanned into its own row): the OLD row is RE-KEYED in place -- its
#     (workspace, name) is updated to the successor location and status flipped
#     back to 'active'. The row (identity, description, remote) travels intact;
#     the re-key preserves the data.
#
# Agent-authored collateral (curated `memory`, `project_context_contracts`) is
# NEVER auto-moved here -- it is only PROPOSED. The human relocates it
# deliberately via `gaia context move-memory` / `move-contracts` once the move
# is confirmed. This function touches only the `projects` lineage.
#
# 'duplicado' / 'worktree' decisions are a structural no-op: both rows are
# legitimately independent and are left exactly as they are (see the CLI
# `--decision` handling; this writer is only invoked for 'movido').
# ---------------------------------------------------------------------------

def resolve_move_candidate(
    from_workspace: str,
    from_name: str,
    to_workspace: str,
    to_name: str,
    *,
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Execute a 'movido' adjudication of a scan-v2 move_candidate.

    Links the OLD (``from``) project row to its successor (``to``) without ever
    hard-deleting a row. Two branches (see the module comment above):

      * successor row EXISTS  -> tombstone the old row (status='missing') and
        write ``superseded_by`` = successor.project_identity on it. Both rows
        survive; the successor stays the active canonical. action='superseded'.
      * successor row ABSENT  -> re-key the old row in place: update its
        (workspace, name) to the successor location, flip status back to
        'active', clear missing_since. The row's data travels intact.
        action='rekeyed'.

    Curated memory / PCC are NOT moved here -- they are proposed for a separate
    `move-memory` / `move-contracts` step. This function only touches the
    `projects` lineage.

    Args:
        from_workspace: Old row workspace (move_candidate ``from.workspace``).
        from_name: Old row name (move_candidate ``from.project``).
        to_workspace: Successor workspace (move_candidate ``to.workspace``).
        to_name: Successor name (move_candidate ``to.project``).
        dry_run: When True, mutate nothing; report the branch + successor
            identity that WOULD be written.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"|"preview", "action": "superseded"|"rekeyed",
         "from": {"workspace","name"}, "to": {"workspace","name"},
         "superseded_by": <successor project_identity or None>,
         "proposed_relocations": {"memory": <n>, "contracts": <n>}}.

    Raises:
        ValueError: when the old row does not exist, or from == to.
    """
    if not from_workspace or not from_name:
        raise ValueError("resolve_move_candidate: from_workspace and from_name are required")
    if not to_workspace or not to_name:
        raise ValueError("resolve_move_candidate: to_workspace and to_name are required")
    if (from_workspace, from_name) == (to_workspace, to_name):
        raise ValueError(
            "resolve_move_candidate: from and to are identical -- nothing to resolve"
        )

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            old = con.execute(
                "SELECT workspace, name, project_identity, status "
                "FROM projects WHERE workspace = ? AND name = ?",
                (from_workspace, from_name),
            ).fetchone()
            if old is None:
                raise ValueError(
                    f"resolve_move_candidate: old row "
                    f"({from_workspace!r}, {from_name!r}) not found"
                )

            successor = con.execute(
                "SELECT workspace, name, project_identity, status "
                "FROM projects WHERE workspace = ? AND name = ?",
                (to_workspace, to_name),
            ).fetchone()

            # Count agent-authored collateral still keyed to the OLD workspace,
            # so the caller can PROPOSE (never auto-execute) its relocation.
            proposed_memory = con.execute(
                "SELECT COUNT(*) FROM memory WHERE workspace = ? "
                "AND deleted_at IS NULL",
                (from_workspace,),
            ).fetchone()[0]
            proposed_contracts = con.execute(
                "SELECT COUNT(*) FROM project_context_contracts WHERE workspace = ?",
                (from_workspace,),
            ).fetchone()[0]

            now = _now_iso()

            if successor is not None:
                # Realistic post-scan state: two rows. Tombstone the old row and
                # link it forward to the successor identity. Never hard-delete.
                action = "superseded"
                superseded_by = successor["project_identity"]
                if not dry_run:
                    con.execute(
                        "UPDATE projects SET status = 'missing', "
                        "missing_since = COALESCE(missing_since, ?), "
                        "superseded_by = ? "
                        "WHERE workspace = ? AND name = ?",
                        (now, superseded_by, from_workspace, from_name),
                    )
                    # Ensure the successor is the active canonical row.
                    con.execute(
                        "UPDATE projects SET status = 'active', missing_since = NULL "
                        "WHERE workspace = ? AND name = ?",
                        (to_workspace, to_name),
                    )
            else:
                # Successor slot is free: re-key the old row in place. The row's
                # identity + description + remote travel with it (data preserved).
                action = "rekeyed"
                superseded_by = old["project_identity"]
                if not dry_run:
                    con.execute(
                        "UPDATE projects SET workspace = ?, name = ?, "
                        "status = 'active', missing_since = NULL "
                        "WHERE workspace = ? AND name = ?",
                        (to_workspace, to_name, from_workspace, from_name),
                    )

            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    return {
        "status": "preview" if dry_run else "applied",
        "action": action,
        "from": {"workspace": from_workspace, "name": from_name},
        "to": {"workspace": to_workspace, "name": to_name},
        "superseded_by": superseded_by,
        "proposed_relocations": {
            "memory": proposed_memory,
            "contracts": proposed_contracts,
        },
    }


# ---------------------------------------------------------------------------
# Public API: approval_grants (DB-backed command_set grant store, M3)
# ---------------------------------------------------------------------------
# These functions are the authoritative write path for the approval_grants
# table added in v7 (M3).  Filesystem JSON approval files are superseded by
# this DB store per D5.  No permission enforcement is applied here -- the
# approval_grants table is system-internal and not agent-owned per the
# agent_permissions matrix.

import json as _json  # local alias to avoid shadowing top-level json in callers


def insert_approval_grant(
    approval_id: str,
    command_set: list[dict],
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    scope: str = "COMMAND_SET",
    expires_at: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert a new approval grant row (status=PENDING).

    Args:
        approval_id: Unique nonce identifying this grant.
        command_set: List of dicts with keys ``command`` (str) and
            ``rationale`` (str).  Each entry is single-use; the
            ``consumed_indexes_json`` column tracks which have been used.
        agent_id: Requesting agent identifier.
        session_id: CLAUDE_SESSION_ID at grant creation time.
        scope: Grant scope type (default 'COMMAND_SET').
        expires_at: Optional ISO8601 expiry timestamp.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "error", "reason": ...} on failure.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT INTO approval_grants
                    (approval_id, agent_id, session_id, command_set_json,
                     scope, created_at, expires_at, status,
                     consumed_indexes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', '[]')
                """,
                (
                    approval_id,
                    agent_id,
                    session_id,
                    _json.dumps(command_set),
                    scope,
                    _now_iso(),
                    expires_at,
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def update_approval_grant_status(
    approval_id: str,
    status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Update the status column of an existing approval_grants row.

    Args:
        approval_id: The grant to update.
        status: New status value (PENDING|CONSUMED|REVOKED|EXPIRED).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                "UPDATE approval_grants SET status = ? WHERE approval_id = ?",
                (status, approval_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def mark_command_set_item_consumed(
    approval_id: str,
    index: int,
    *,
    db_path: Path | None = None,
) -> dict:
    """Mark a single command_set item (by index) as consumed.

    Appends ``index`` to ``consumed_indexes_json``.  When all items in the
    command_set are consumed the grant status is set to CONSUMED and
    ``consumed_at`` is stamped.

    Args:
        approval_id: The grant whose item was just executed.
        index: Zero-based index of the command_set item that matched.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "all_consumed": bool} on success.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            row = con.execute(
                "SELECT command_set_json, consumed_indexes_json, status "
                "FROM approval_grants WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                con.rollback()
                return {"status": "error", "reason": f"approval_id {approval_id!r} not found"}

            command_set = _json.loads(row[0] or "[]")
            consumed = _json.loads(row[1] or "[]")
            if index not in consumed:
                consumed.append(index)

            all_consumed = len(consumed) >= len(command_set)
            now = _now_iso()
            if all_consumed:
                con.execute(
                    """
                    UPDATE approval_grants
                    SET consumed_indexes_json = ?,
                        status = 'CONSUMED',
                        consumed_at = ?
                    WHERE approval_id = ?
                    """,
                    (_json.dumps(consumed), now, approval_id),
                )
            else:
                con.execute(
                    "UPDATE approval_grants SET consumed_indexes_json = ? WHERE approval_id = ?",
                    (_json.dumps(consumed), approval_id),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied({"all_consumed": all_consumed})
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def revoke_approval_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Revoke an approval grant (status=REVOKED, revoked_at=now).

    After revocation, any command in the command_set that hasn't been
    executed yet will require fresh approval.

    Args:
        approval_id: The grant to revoke.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "not_found"} if no row.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                UPDATE approval_grants
                SET status = 'REVOKED', revoked_at = ?
                WHERE approval_id = ? AND status NOT IN ('REVOKED', 'CONSUMED')
                """,
                (_now_iso(), approval_id),
            )
            if cur.rowcount == 0:
                # Either not found or already in terminal state
                exists = con.execute(
                    "SELECT status FROM approval_grants WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                con.rollback()
                if exists is None:
                    return {"status": "not_found"}
                return {"status": "no_op", "current_status": exists[0]}
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def list_approval_grants(
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict]:
    """Query approval_grants rows with optional filters.

    Args:
        agent_id: Filter by requesting agent.
        session_id: Filter by CLAUDE session ID.
        status: Filter by status (PENDING|CONSUMED|REVOKED|EXPIRED).
        limit: Maximum rows to return (default 100).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        List of dicts keyed by column name, ordered by created_at DESC.
    """
    con = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = con.execute(
            f"SELECT * FROM approval_grants {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


def list_command_set_grants_agnostic(
    *,
    status: str = "PENDING",
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict]:
    """List COMMAND_SET grants WITHOUT a session_id constraint (Brief 71).

    This is the COMMAND_SET analogue of the session-agnostic lookup that
    ``check_db_semantic_grant`` performs for the SINGULAR (semantic-signature)
    grant. The block-approve-retry flow legitimately spans sessions -- a
    command is blocked under the subagent session, the user approves under the
    orchestrator session, and the consuming retry runs under whichever session
    (or none -- CLAUDE_SESSION_ID is not guaranteed to be exported into the bash
    subprocess, where ``get_session_id()`` then falls back to the literal
    ``"default"``). A session_id filter therefore never matches the grant the
    approval created, which is exactly the consumption-bypass bug this function
    fixes.

    The security boundary is preserved WITHOUT a session_id constraint, by the
    same conjunction of session-agnostic facts the singular path relies on
    (mirrors the comment in ``check_db_semantic_grant``):
      * the byte-for-byte command match (applied by the caller against each
        unconsumed command_set item) binds the grant to THIS command's exact
        intent;
      * status='PENDING' plus per-index ``consumed_indexes_json`` is the
        single-use replay guard -- a fully consumed grant flips to CONSUMED and
        no longer matches, and an already-consumed index is skipped;
      * expires_at is the TTL -- a stale grant past its window is skipped.
    None of these depend on which session is asking, so dropping the session_id
    filter widens nothing the other checks do not already gate. It only lets the
    legitimate cross-session (or empty-session) retry succeed.

    Args:
        status: Status to filter on (default 'PENDING').
        limit: Maximum rows to return.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        List of dicts keyed by column name, ordered by created_at DESC.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM approval_grants "
            "WHERE scope = 'COMMAND_SET' AND status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: insert_semantic_grant / check_db_semantic_grant /
#             consume_db_semantic_grant (CHECK-side DB cutover, Brief 71)
# ---------------------------------------------------------------------------
#
# These three functions implement the DB-primary path for SCOPE_SEMANTIC_SIGNATURE
# grants created by activate_db_pending_by_prefix().  They use the same
# approval_grants table (scope='SCOPE_SEMANTIC_SIGNATURE') so all grant lifecycle
# is visible in one place.
#
# Lifecycle:
#   insert_semantic_grant()     -- called by activate_db_pending_by_prefix(); writes
#                                  row with status=PENDING.
#   check_db_semantic_grant()   -- called by check_approval_grant(); returns the
#                                  matching row dict when a valid grant exists.
#   consume_db_semantic_grant() -- called by bash_validator after command executes;
#                                  sets status=CONSUMED + consumed_at.
# ---------------------------------------------------------------------------


def insert_semantic_grant(
    approval_id: str,
    command: str,
    scope_signature: dict,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    ttl_minutes: int = APPROVAL_GRANT_TTL_MINUTES,
    db_path: Path | None = None,
) -> dict:
    """Insert a SCOPE_SEMANTIC_SIGNATURE row into approval_grants (status=PENDING).

    Called by activate_db_pending_by_prefix() after the user approves via
    AskUserQuestion.  The row represents a grant valid for one execution of
    the approved command within the TTL window.

    Args:
        approval_id: The P-{hex} approval id that was activated.  Used as PK.
        command: The exact command string approved by the user.
        scope_signature: Dict from ApprovalSignature.to_dict() -- stored in
            command_set_json so check_db_semantic_grant() can match semantically.
        agent_id: Requesting agent identifier.
        session_id: CLAUDE_SESSION_ID of the subagent that will execute.
            Retained for audit only -- check_db_semantic_grant() matches
            cross-session (Brief 71), so this is NOT used to scope lookup.
        ttl_minutes: Grant lifetime in minutes. Defaults to
            APPROVAL_GRANT_TTL_MINUTES (5). The grant is consumed AT THE MATCH,
            so this short window only needs to cover the block -> approve ->
            retry round trip; a grant never presented to a matching retry simply
            expires. This is the GRANT window, distinct from the 24h pending
            window (DEFAULT_PENDING_TTL_MINUTES).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "error", "reason": ...} otherwise.
    """
    from datetime import datetime, timezone, timedelta

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # command_set_json stores the scope_signature dict so CHECK side can match.
    # We also include the original command for audit trail.
    grant_data = {
        "command": command,
        "scope_signature": scope_signature,
    }

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO approval_grants
                    (approval_id, agent_id, session_id, command_set_json,
                     scope, created_at, expires_at, status,
                     consumed_indexes_json)
                VALUES (?, ?, ?, ?, 'SCOPE_SEMANTIC_SIGNATURE', ?, ?, 'PENDING', '[]')
                """,
                (
                    approval_id,
                    agent_id,
                    session_id,
                    _json.dumps(grant_data),
                    _now_iso(),
                    expires_at,
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def check_db_semantic_grant(
    command: str,
    session_id: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Find an active SCOPE_SEMANTIC_SIGNATURE grant for command in the DB.

    Called by check_approval_grant() as the primary (DB) check path.

    Matching uses the scope_signature stored in command_set_json:
    - Deserializes the ApprovalSignature via ApprovalSignature.from_dict()
    - Delegates to matches_approval_signature() for semantic comparison

    Grant must:
    - Have scope='SCOPE_SEMANTIC_SIGNATURE'
    - Have status='PENDING'
    - Not be past its expires_at timestamp

    session_id is audit metadata only, NOT a match constraint (cross-session
    per Brief 71). The block-approve-retry flow legitimately spans sessions: a
    command is blocked under the subagent session, the user approves under the
    orchestrator session, and the subagent retries under its own session. If
    session_id constrained the match, the retry would never find the grant the
    approval created.

    Args:
        command: The command string to check.
        session_id: CLAUDE_SESSION_ID. Accepted for signature compatibility and
            passed through by callers, but IGNORED for matching -- the lookup is
            session-agnostic (see security-boundary note below).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Dict with grant row data when a matching grant is found, None otherwise.
    """
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    try:
        # Import matching utilities lazily to avoid circular imports at module load.
        # These are in the hooks package, not gaia.store.
        import sys as _sys
        _hooks_root = str(_Path(__file__).resolve().parents[2] / "hooks")
        if _hooks_root not in _sys.path:
            _sys.path.insert(0, _hooks_root)

        from modules.security.approval_scopes import (
            ApprovalSignature,
            matches_approval_signature,
        )
    except ImportError:
        # Hooks package not available (e.g. standalone gaia.store test context).
        # Fall back to None -- callers handle None gracefully.
        return None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = _connect(db_path)
    try:
        # Security boundary is preserved WITHOUT a session_id constraint. The
        # grant is authorized by the conjunction of three session-agnostic
        # facts, each closing one attack surface:
        #   * the semantic signature match (below) binds the grant to THIS
        #     command's byte-level intent (Brief 71 signature binding);
        #   * status='PENDING' is the single-use replay guard -- once consumed
        #     the row flips to CONSUMED and no longer matches;
        #   * expires_at is the TTL -- a stale grant past its window is skipped.
        # None of these depend on which session is asking, so dropping the
        # session_id filter widens nothing the three checks above do not already
        # gate. It only lets the legitimate cross-session retry succeed.
        clauses = [
            "scope = 'SCOPE_SEMANTIC_SIGNATURE'",
            "status = 'PENDING'",
        ]
        params: list = []

        where = " AND ".join(clauses)
        rows = con.execute(
            f"SELECT * FROM approval_grants WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            # TTL check: expires_at column holds ISO8601 string.
            expires_at = row_dict.get("expires_at")
            if expires_at and expires_at < now_iso:
                continue

            command_set_json = row_dict.get("command_set_json") or "{}"
            try:
                grant_data = _json.loads(command_set_json)
            except Exception:
                continue

            sig_dict = grant_data.get("scope_signature")
            if not sig_dict:
                continue

            try:
                signature = ApprovalSignature.from_dict(sig_dict)
                if matches_approval_signature(signature, command):
                    return row_dict
            except Exception:
                continue

        return None
    except Exception:
        return None
    finally:
        con.close()


def _consumed_grant_exists(command: str, con) -> bool:
    """Return True if a CONSUMED semantic grant already matches ``command``.

    Single, session-agnostic replay guard shared by both planes (Brief 71,
    Change 4):
      * check_approval_grant()'s DB path, and
      * its DEPRECATED filesystem fallback,
    which previously each carried their own copy of this query -- and the
    filesystem copy was session-locked (``AND session_id=?``), reintroducing the
    very cross-session bug the CHECK side was fixed for. Consolidating here keeps
    the guard in one place and session-agnostic: once a command's grant is
    CONSUMED, no later retry -- in ANY session -- may slip past via a stale
    filesystem copy.

    Matching mirrors check_db_semantic_grant(): the stored scope_signature is
    rehydrated and compared semantically, so the guard recognizes the same
    byte-bound command that consumed the grant.

    Args:
        command: The command string being re-checked.
        con: An OPEN sqlite3 connection (caller owns its lifecycle). Passed in
            rather than opened here so the caller can reuse its own connection.

    Returns:
        True when a CONSUMED SCOPE_SEMANTIC_SIGNATURE grant matches ``command``.
    """
    from pathlib import Path as _Path

    try:
        # Lazy import of the hooks matching utilities (same approach as
        # check_db_semantic_grant) -- they live in the hooks package, not
        # gaia.store, so importing them at module scope would couple the store
        # to the hooks layer and risk a circular import.
        import sys as _sys
        _hooks_root = str(_Path(__file__).resolve().parents[2] / "hooks")
        if _hooks_root not in _sys.path:
            _sys.path.insert(0, _hooks_root)
        from modules.security.approval_scopes import (
            ApprovalSignature,
            matches_approval_signature,
        )
    except ImportError:
        # Matching utilities unavailable -- cannot evaluate the guard. Treat as
        # "no consumed grant found" (return False) so the caller falls through to
        # its other checks rather than spuriously suppressing a legitimate grant.
        return False

    try:
        rows = con.execute(
            "SELECT command_set_json FROM approval_grants "
            "WHERE scope='SCOPE_SEMANTIC_SIGNATURE' AND status='CONSUMED' "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        for row in rows:
            raw = row[0] if not hasattr(row, "keys") else row["command_set_json"]
            try:
                grant_data = _json.loads(raw or "{}")
            except Exception:
                continue
            sig_dict = grant_data.get("scope_signature")
            if not sig_dict:
                continue
            try:
                signature = ApprovalSignature.from_dict(sig_dict)
                if matches_approval_signature(signature, command):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def consume_db_semantic_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Mark a SCOPE_SEMANTIC_SIGNATURE grant as CONSUMED (replay protection).

    Called by bash_validator immediately after a command is allowed via a DB
    semantic grant.  Setting status=CONSUMED prevents the same grant from
    being reused within the TTL window (Gap B fix).

    Args:
        approval_id: The grant to consume.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        True if the grant was found and consumed, False otherwise.
    """
    now = _now_iso()
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                UPDATE approval_grants
                SET status = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ?
                  AND scope = 'SCOPE_SEMANTIC_SIGNATURE'
                  AND status = 'PENDING'
                """,
                (now, approval_id),
            )
            con.commit()
            return cur.rowcount > 0
        except Exception:
            con.rollback()
            raise
    except Exception:
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: insert_file_path_grant / check_db_file_path_grant /
#             consume_db_file_path_grant (SCOPE_FILE_PATH DB migration)
# ---------------------------------------------------------------------------
#
# Mirrors the SCOPE_SEMANTIC_SIGNATURE grant triplet above but for protected-
# path Write/Edit approvals.  Uses scope='SCOPE_FILE_PATH' in the same
# approval_grants table so all grant lifecycle is visible in one place.
#
# Lifecycle:
#   insert_file_path_grant()       -- called by activate_db_pending_by_prefix()
#                                     SCOPE_FILE_PATH branch; writes status=PENDING.
#   check_db_file_path_grant()     -- called by check_approval_grant_for_file();
#                                     returns the matching row dict.
#   consume_db_file_path_grant()   -- called by _adapt_write_edit after allowing
#                                     the protected-path write; sets CONSUMED.
# ---------------------------------------------------------------------------


def insert_file_path_grant(
    approval_id: str,
    file_path: str,
    scope_signature: dict,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    ttl_minutes: int = APPROVAL_GRANT_TTL_MINUTES,
    db_path: Path | None = None,
) -> dict:
    """Insert a SCOPE_FILE_PATH row into approval_grants (status=PENDING).

    Called by activate_db_pending_by_prefix() when a SCOPE_FILE_PATH pending
    approval is activated (user approved the protected-path write).  The row
    is later found by check_db_file_path_grant() on the subagent retry.

    Args:
        approval_id: The P-{hex} approval id that was activated.  Used as PK.
        file_path: The absolute file path approved for write/edit.
        scope_signature: Dict from ApprovalSignature.to_dict() -- stored in
            command_set_json so check_db_file_path_grant() can match.
        agent_id: Requesting agent identifier (audit only).
        session_id: CLAUDE_SESSION_ID at grant time (audit only -- the check
            side is cross-session, same as SCOPE_SEMANTIC_SIGNATURE).
        ttl_minutes: Grant lifetime in minutes.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "error", "reason": ...} otherwise.
    """
    from datetime import datetime, timezone, timedelta

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    grant_data = {
        "file_path": file_path,
        "scope_signature": scope_signature,
    }

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO approval_grants
                    (approval_id, agent_id, session_id, command_set_json,
                     scope, created_at, expires_at, status,
                     consumed_indexes_json)
                VALUES (?, ?, ?, ?, 'SCOPE_FILE_PATH', ?, ?, 'PENDING', '[]')
                """,
                (
                    approval_id,
                    agent_id,
                    session_id,
                    _json.dumps(grant_data),
                    _now_iso(),
                    expires_at,
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def check_db_file_path_grant(
    file_path: str,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Find an active SCOPE_FILE_PATH grant for file_path in the DB.

    Called by check_approval_grant_for_file() as the primary (DB) check path.
    Matching uses the scope_signature stored in command_set_json via
    matches_file_path_approval().

    Grant must:
    - Have scope='SCOPE_FILE_PATH'
    - Have status='PENDING'
    - Not be past its expires_at timestamp

    The lookup is session-agnostic (same rationale as check_db_semantic_grant):
    the activate-approve-retry flow crosses sessions, so a session_id constraint
    would prevent the subagent from finding the grant the orchestrator created.

    Args:
        file_path: The file path to match.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Dict with grant row data when a matching grant is found, None otherwise.
    """
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    try:
        import sys as _sys
        _hooks_root = str(_Path(__file__).resolve().parents[2] / "hooks")
        if _hooks_root not in _sys.path:
            _sys.path.insert(0, _hooks_root)
        from modules.security.approval_scopes import (
            ApprovalSignature,
            matches_file_path_approval,
        )
    except ImportError:
        return None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM approval_grants "
            "WHERE scope = 'SCOPE_FILE_PATH' AND status = 'PENDING' "
            "ORDER BY created_at DESC",
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            expires_at = row_dict.get("expires_at")
            if expires_at and expires_at < now_iso:
                continue

            command_set_json = row_dict.get("command_set_json") or "{}"
            try:
                grant_data = _json.loads(command_set_json)
            except Exception:
                continue

            sig_dict = grant_data.get("scope_signature")
            if not sig_dict:
                continue

            try:
                signature = ApprovalSignature.from_dict(sig_dict)
                if matches_file_path_approval(signature, file_path):
                    return row_dict
            except Exception:
                continue

        return None
    except Exception:
        return None
    finally:
        con.close()


def consume_db_file_path_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Mark a SCOPE_FILE_PATH grant as CONSUMED (replay protection).

    Called by _adapt_write_edit immediately after a protected-path write is
    allowed via a DB file-path grant.  Setting status=CONSUMED prevents reuse.

    Args:
        approval_id: The grant to consume.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        True if the grant was found and consumed, False otherwise.
    """
    now = _now_iso()
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                UPDATE approval_grants
                SET status = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ?
                  AND scope = 'SCOPE_FILE_PATH'
                  AND status = 'PENDING'
                """,
                (now, approval_id),
            )
            con.commit()
            return cur.rowcount > 0
        except Exception:
            con.rollback()
            raise
    except Exception:
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: confirm_db_grant / cleanup_expired_db_grants (v20 / grant-lifecycle)
# ---------------------------------------------------------------------------
#
# Foundation scaffolding for the grant-lifecycle FS-to-DB migration (v20).
# confirm_db_grant() backs the confirm_grant flow. (The former
# consume_session_grants SubagentStop sweep was removed in the approvals
# redesign M1 -- grants are consumed at the match and expire on their short TTL.)
#
#   confirm_db_grant()          -- sets confirmed=1 on a PENDING grant row;
#                                  used when the user explicitly confirms a
#                                  multi-use grant.
#   cleanup_expired_db_grants() -- marks EXPIRED (or hard-deletes) any grant
#                                  whose expires_at is in the past and whose
#                                  status is still PENDING.  Idempotent.
# ---------------------------------------------------------------------------


def confirm_db_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Set confirmed=1 on a PENDING approval_grants row.

    Called when the user explicitly confirms a multi-use grant.  Only rows
    with status='PENDING' are touched -- a CONSUMED or REVOKED grant cannot
    be retroactively confirmed.

    Args:
        approval_id: The grant to confirm (PK of approval_grants).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} when the row was updated.
        {"status": "not_found"} when no PENDING row with that id exists.
        {"status": "error", "reason": ...} on unexpected failure.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                "UPDATE approval_grants SET confirmed = 1 "
                "WHERE approval_id = ? AND status = 'PENDING'",
                (approval_id,),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        if cur.rowcount == 0:
            return {"status": "not_found"}
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def cleanup_expired_db_grants(
    *,
    db_path: Path | None = None,
) -> int:
    """Mark EXPIRED any PENDING approval_grants rows whose expires_at has passed.

    Idempotent: rows already in a terminal status (CONSUMED, REVOKED, EXPIRED)
    are not touched.  Rows with expires_at=NULL are skipped (no TTL set).

    Args:
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows marked EXPIRED.
    """
    now = _now_iso()
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                "UPDATE approval_grants SET status = 'EXPIRED' "
                "WHERE status = 'PENDING' "
                "  AND expires_at IS NOT NULL "
                "  AND expires_at < ?",
                (now,),
            )
            con.commit()
            return cur.rowcount
        except Exception:
            con.rollback()
            raise
    except Exception:
        return 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: agent_contract_handoffs (v9 / M4)
# ---------------------------------------------------------------------------
#
# Brief: agent-contract-handoff M4 (handoff persistence).
#
# Guard pattern mirrors _assert_dispatch_can_write_memory:
#   Handoff rows are written by the SubagentStop hook, which runs as the
#   orchestrator or operator context.  Subagent dispatches (specialist
#   agents) must NOT write their own handoff rows -- that would allow
#   self-reporting without hook oversight.
# ---------------------------------------------------------------------------

class HandoffWriteForbidden(PermissionError):
    """Raised when a non-curator subagent attempts to write a handoff row."""


_HANDOFF_WRITER_AGENTS = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
    # The SubagentStop hook runs outside any dispatch context (GAIA_DISPATCH_AGENT
    # is unset in the hook process), so the unset case is also allowed below.
})


def _assert_dispatch_can_write_handoff() -> None:
    """Block handoff writes from non-curator subagent dispatches.

    Contract (mirrors _assert_dispatch_can_write_memory):
    * GAIA_DISPATCH_AGENT unset / empty -> hook context or CLI, allowed.
    * Set to a curator identity -> allowed.
    * Set to anything else -> raises HandoffWriteForbidden.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    if raw in _HANDOFF_WRITER_AGENTS:
        return
    raise HandoffWriteForbidden(
        f"agent_contract_handoffs writes are forbidden from subagent dispatches "
        f"(current GAIA_DISPATCH_AGENT={raw!r}). Handoff rows are written by "
        f"the SubagentStop hook only."
    )


def insert_agent_contract_handoff(
    agent_id: str,
    workspace: str,
    task_status: str,
    raw_handoff_json: str,
    *,
    session_id: str | None = None,
    brief_id: int | None = None,
    db_path: "Path | None" = None,
) -> int:
    """Insert a row into agent_contract_handoffs.

    Called by the SubagentStop hook after parsing and resolving the contract
    envelope.  Returns the new row's id (handoff_id).

    Args:
        agent_id:         Agent identity string (e.g. "a1b2c3d4e5").
        workspace:        Workspace name (FK -> workspaces.name).
        task_status:      Resolved plan_status from the contract envelope.
        raw_handoff_json: Full contract envelope serialized as JSON string.
        session_id:       CLAUDE_SESSION_ID at SubagentStop time (optional).
        brief_id:         briefs.id FK (optional -- EXTENSION_POINT for
                          state-machine-completion downstream briefs).
        db_path:          Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.

    Raises:
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_handoff()

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            cur = con.execute(
                """
                INSERT INTO agent_contract_handoffs
                    (agent_id, session_id, workspace, brief_id,
                     task_status, raw_handoff_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    session_id,
                    workspace,
                    brief_id,
                    task_status,
                    raw_handoff_json,
                    _now_iso(),
                ),
            )
            handoff_id = cur.lastrowid
            con.commit()
            return handoff_id
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


def insert_handoff_approval(
    handoff_id: int,
    approval_id: str,
    decision: str,
    decided_at: str,
    *,
    db_path: "Path | None" = None,
) -> int:
    """Insert a row into agent_contract_handoff_approvals.

    Args:
        handoff_id:  FK -> agent_contract_handoffs.id.
        approval_id: FK -> approval_grants.approval_id.
        decision:    One of APPROVED|REJECTED|EXPIRED|REVOKED.
        decided_at:  ISO8601 timestamp of the decision.
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.

    Raises:
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
        ValueError: if decision is not a valid enum value.
    """
    _assert_dispatch_can_write_handoff()

    _VALID_DECISIONS = {"APPROVED", "REJECTED", "EXPIRED", "REVOKED"}
    if decision not in _VALID_DECISIONS:
        raise ValueError(
            f"invalid decision {decision!r}; must be one of "
            f"{sorted(_VALID_DECISIONS)}"
        )

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                INSERT INTO agent_contract_handoff_approvals
                    (handoff_id, approval_id, decision, decided_at)
                VALUES (?, ?, ?, ?)
                """,
                (handoff_id, approval_id, decision, decided_at),
            )
            approval_row_id = cur.lastrowid
            con.commit()
            return approval_row_id
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


def list_agent_contract_handoffs(
    *,
    workspace: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    brief_id: int | None = None,
    task_status: str | None = None,
    limit: int = 100,
    db_path: "Path | None" = None,
) -> list[dict]:
    """Query agent_contract_handoffs with optional filters.

    Args:
        workspace:   Filter by workspace name.
        agent_id:    Filter by agent identity string.
        session_id:  Filter by CLAUDE session ID.
        brief_id:    Filter by briefs.id FK.
        task_status: Filter by resolved plan_status.
        limit:       Maximum rows to return (default 100).
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        List of dicts keyed by column name, ordered by created_at DESC.
    """
    con = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace is not None:
            clauses.append("workspace = ?")
            params.append(workspace)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if brief_id is not None:
            clauses.append("brief_id = ?")
            params.append(brief_id)
        if task_status is not None:
            clauses.append("task_status = ?")
            params.append(task_status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = con.execute(
            f"SELECT * FROM agent_contract_handoffs {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: insert_episode / insert_episode_anomaly
# ---------------------------------------------------------------------------
#
# Brief: episodic-workflow-to-db (T4).
#
# Episodes are the persistence target for SubagentStop telemetry: every agent
# turn produces one row in the ``episodes`` table plus zero or more child rows
# in ``episode_anomalies``. The legacy filesystem writers
# (.claude/project-context/episodic-memory/episodes.jsonl + per-episode JSON
# files; .claude/project-context/workflow-episodic-memory/run-snapshots.jsonl;
# anomalies.jsonl) are superseded by these DB writers.
#
# No agent_permissions enforcement: episodes are system-internal telemetry
# written exclusively by the SubagentStop hook chain (not by named subagents).
# This mirrors the approach used for ``approval_grants`` and
# ``agent_contract_handoffs`` -- they are infrastructure tables, not surfaces
# the per-agent permission matrix governs.
# ---------------------------------------------------------------------------

# Columns the episodes table accepts as direct INSERT targets. The schema
# also exposes ``rowid`` (implicit), but no field outside this whitelist is
# allowed at the writer boundary -- this keeps the contract explicit and
# stops accidental drift from the schema definition.
_EPISODE_COLUMNS = (
    "episode_id",
    "workspace",
    "timestamp",
    "session_id",
    "task_id",
    "agent",
    "tier",
    "type",
    "title",
    "prompt",
    "enriched_prompt",
    "wf_prompt",
    "clarifications",
    "keywords",
    "tags",
    "commands_executed",
    "context_metrics",
    "relevance_score",
    "outcome",
    "duration_seconds",
    "exit_code",
    "plan_status",
    "output_length",
    "output_tokens_approx",
)


def insert_episode(
    workspace: str,
    episode_id: str,
    fields: Mapping[str, Any],
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a row into the ``episodes`` table.

    Called from ``EpisodicMemory.store_episode()`` (T4 of brief
    episodic-workflow-to-db). The caller is the SubagentStop hook chain,
    which has no GAIA_DISPATCH_AGENT set -- there is no per-agent permission
    check.

    JSON-shaped columns (``clarifications``, ``keywords``, ``tags``,
    ``commands_executed``, ``context_metrics``) accept either a Python
    container -- which the writer serializes via ``json.dumps`` -- or a
    pre-serialized string. ``None`` is preserved as SQL NULL.

    Args:
        workspace: Workspace name (FK -> workspaces.name). Required.
        episode_id: PK for the new row. Required.
        fields: Dict of column -> value pairs. Recognized keys are the
            columns in ``_EPISODE_COLUMNS`` minus ``workspace`` and
            ``episode_id``. Unknown keys are silently dropped (callers may
            pass workflow-shaped dicts that contain telemetry fields that
            do not map to columns).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "episode_id": <id>}`` on success.
        ``{"status": "error", "reason": str}`` on failure.
    """
    if not workspace or not workspace.strip():
        return {"status": "error", "reason": "workspace required"}
    if not episode_id or not episode_id.strip():
        return {"status": "error", "reason": "episode_id required"}

    # Normalize: serialize JSON-shaped values, preserve scalars and NULLs.
    json_cols = {
        "clarifications",
        "keywords",
        "tags",
        "commands_executed",
        "context_metrics",
    }
    data: dict[str, Any] = {"workspace": workspace, "episode_id": episode_id}
    for col in _EPISODE_COLUMNS:
        if col in ("workspace", "episode_id"):
            continue
        if col not in fields:
            continue
        val = fields[col]
        if val is None:
            data[col] = None
            continue
        if col in json_cols and not isinstance(val, str):
            data[col] = _json.dumps(val)
        else:
            data[col] = val

    # timestamp defaults to now() when caller did not supply one.
    if "timestamp" not in data or data["timestamp"] is None:
        data["timestamp"] = _now_iso()

    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            con.execute(
                f"INSERT INTO episodes ({col_sql}) VALUES ({placeholders})",
                tuple(data[c] for c in cols),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied({"episode_id": episode_id})
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


_EPISODE_ANOMALY_COLUMNS = (
    "episode_id",
    "workspace",
    "timestamp",
    "type",
    "severity",
    "message",
    "payload",
)


def insert_episode_anomaly(
    workspace: str,
    episode_id: str,
    fields: Mapping[str, Any],
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a row into the ``episode_anomalies`` child table.

    Args:
        workspace: Workspace name (denormalized; matches parent episode).
        episode_id: FK -> episodes.episode_id. The parent row must already
            exist (FK ON DELETE CASCADE).
        fields: Dict with optional keys ``timestamp``, ``type``, ``severity``,
            ``message``, ``payload``. ``payload`` is JSON-serialized when it
            is not already a string.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied"}`` on success or ``{"status": "error",
        "reason": str}`` on failure.
    """
    if not workspace or not workspace.strip():
        return {"status": "error", "reason": "workspace required"}
    if not episode_id or not episode_id.strip():
        return {"status": "error", "reason": "episode_id required"}

    anomaly_type = fields.get("type")
    if not anomaly_type or not str(anomaly_type).strip():
        return {"status": "error", "reason": "type required"}

    payload = fields.get("payload")
    if payload is not None and not isinstance(payload, str):
        payload = _json.dumps(payload)

    ts = fields.get("timestamp") or _now_iso()
    severity = fields.get("severity")
    message = fields.get("message")

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT INTO episode_anomalies
                    (episode_id, workspace, timestamp, type, severity,
                     message, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (episode_id, workspace, ts, anomaly_type, severity,
                 message, payload),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()
