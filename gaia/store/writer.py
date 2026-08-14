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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # annotation-only; runtime imports of gaia.state stay lazy
    from gaia.state.task_closure import GateVerdict

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
# Plan-first COMMAND_SET grant lifetime
# ---------------------------------------------------------------------------
#
# PLAN_COMMAND_SET_TTL_MINUTES bounds a plan-first COMMAND_SET grant: the window
# in which an approved, ordered batch may still reserve its remaining commands.
#
# It is a THIRD window, distinct from both constants above, because a plan-first
# set is consumed differently from either. APPROVAL_GRANT_TTL_MINUTES (5) is
# calibrated for a SINGLE command consumed at the match -- it only has to cover
# block -> approve -> retry. A set is N commands executed one per tool call with
# real work between them (a build, an apply, a verification read), so a 5-minute
# window would expire a legitimately-running set mid-batch.
# DEFAULT_PENDING_TTL_MINUTES (1440) is the opposite error: it is how long an
# UNANSWERED approval waits for a human, and reusing it here would let an
# approved-but-never-used key stay armed for a full day.
#
# 60 minutes is the point where both pressures are satisfied: it is longer than
# any batch a user watches through in one sitting, and short enough that a key
# nobody presented to a matching command is dead while the person who approved it
# is still at the same desk.
#
# The window is measured from grant creation and is NOT extended by consuming an
# item. What must be bounded is the grant's total authority, not the idle gap
# between items; a sliding window would let a long enough set carry a live key
# indefinitely, which is the property this constant exists to deny.
PLAN_COMMAND_SET_TTL_MINUTES = 60


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    """Resolve the DB path via gaia.paths (B0). Imported lazily to avoid
    side effects at import time."""
    from gaia.paths import db_path
    return db_path()


# Busy-timeout (ms) applied to EVERY connection: when a connection cannot
# immediately acquire the lock it needs (another writer holds RESERVED, or a
# BEGIN IMMEDIATE is waiting on the write lock), SQLite retries internally for
# up to this long before returning SQLITE_BUSY. This is the first line of
# defense against "database is locked" under genuine concurrency -- it turns
# an instant failure into a bounded wait for the common contention case. The
# retry wrapper (`_retry_on_locked`) covers the residual case where even this
# window is exhausted.
_BUSY_TIMEOUT_MS = 5000

# Materialization sentinel: `schema_version` is the LAST object created by
# schema.sql, so its presence proves the ENTIRE schema has been applied and
# committed (executescript runs the statements in file order). Checking a
# late-created sentinel -- rather than a table created early like `workspaces`
# -- is what makes the fresh-DB check safe under concurrency: a connection
# never proceeds on a half-built schema where an early table exists but the
# `agent_contract_handoffs` table or its `contract_id` UNIQUE index does not.
_SCHEMA_SENTINEL = "schema_version"

# Retry policy for the residual "database is locked" case (BEGIN IMMEDIATE +
# busy_timeout handle the common contention; this covers the rest).
_MAX_WRITE_RETRIES = 8
_WRITE_RETRY_BASE_SLEEP = 0.05


def _has_object(con: sqlite3.Connection, name: str) -> bool:
    """Return True iff ``name`` exists in sqlite_master (any object type)."""
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _has_application_tables(con: sqlite3.Connection) -> bool:
    """Return True iff the DB already carries any non-internal user table.

    Used to recognize a DB that was initialized OUTSIDE this materializer -- a
    test fixture built from a minimal inline schema, or any externally-managed
    DB -- so schema.sql is not force-reapplied on top of it. Excludes SQLite's
    own internal objects (``sqlite_%``, e.g. ``sqlite_sequence``, autoindexes).
    """
    row = con.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _ensure_schema_materialized(con: sqlite3.Connection, db_path: Path) -> None:
    """Materialize the schema exactly once, safe under concurrent first-write.

    Replaces the historical ``fresh = not db_path.exists()`` TOCTOU: that check
    raced ``sqlite3.connect()`` (which itself creates the empty file), so a
    second process could observe the file present but schema-less and fail with
    ``no such table: workspaces``. Here the decision is driven by the ACTUAL
    presence of the schema (a committed sentinel object), never by file
    existence, and the materialization itself is serialized across processes by
    an ``O_EXCL`` lock file so only one process runs ``executescript`` while any
    others WAIT for it to finish and release the lock -- then re-check and skip.
    The schema is fully idempotent (every ``CREATE ... IF NOT EXISTS``), so the
    take-over-a-stale-lock fallback is harmless.
    """
    # Fast path: schema fully present and committed.
    if _has_object(con, _SCHEMA_SENTINEL):
        return

    lock_path = Path(str(db_path) + ".init-lock")
    deadline = time.monotonic() + 30.0
    fd: int | None = None
    while fd is None:
        try:
            # Whoever wins O_EXCL owns materialization; everyone else waits.
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            # Another process is materializing. Do NOT read the schema yet --
            # it may be half-built. Wait for the lock to be released, then take
            # it and re-check under the lock (the winner will have committed the
            # full schema, sentinel included, before releasing).
            if time.monotonic() > deadline:
                # Assume an orphaned lock (a crashed materializer); take over.
                fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
                break
            time.sleep(0.02)
    try:
        if not _has_object(con, _SCHEMA_SENTINEL):
            # A DB that already carries application tables but lacks the sentinel
            # was initialized OUTSIDE this materializer (a test fixture built from
            # a minimal inline schema, or any externally-managed DB). Applying
            # schema.sql on top is destructive: each CREATE TABLE IF NOT EXISTS is
            # a no-op against the pre-existing table, but the trailing CREATE INDEX
            # /TRIGGER then references a column the inline table never declared
            # (e.g. idx_briefs_topic_key ON briefs(topic_key)) -> OperationalError.
            # So materialize ONLY a genuinely-empty DB; never clobber a populated
            # one. This check is deliberately made HERE, under the O_EXCL lock, not
            # in the unlocked fast path above: for a real concurrent first-write the
            # winner commits the sentinel (the schema's LAST object) before releasing
            # the lock, so any waiter re-checking here always sees the sentinel and
            # returns at the line above -- it never reaches this emptiness test on a
            # half-built schema. The test therefore only distinguishes a truly fresh
            # DB (materialize) from an externally-initialized one (skip), preserving
            # T18's fresh-DB TOCTOU fix and its O_EXCL serialization of empty
            # concurrent first-writes.
            if _has_application_tables(con):
                return
            con.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            con.commit()
    finally:
        os.close(fd)
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, ensuring the schema is materialized.

    Args:
        db_path: Optional explicit DB path (used by tests). When None,
            resolves via ``gaia.paths.db_path()``.

    Returns:
        Open sqlite3.Connection with foreign_keys=ON and a busy_timeout set.
    """
    if db_path is None:
        db_path = _db_path()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # Wait (bounded) for a contended lock instead of failing instantly -- the
    # first defense against "database is locked" under concurrent writers.
    con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")

    # Register gaia_sha256: scalar function used by the ai_approval_events_hash
    # trigger to compute this_hash = SHA-256(prev_hash || fingerprint).
    # SQLite does not include SHA-256 built-in; we inject it as a Python function
    # at connection time. All connections opened via _connect() get this function,
    # which means the trigger fires correctly on any INSERT into approval_events.
    # The function accepts a single TEXT argument and returns the hex digest.
    def _gaia_sha256(value: str | None) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()

    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)

    # Materialize the schema based on ACTUAL presence (not file existence),
    # serialized so a concurrent first-write can never observe a missing table.
    _ensure_schema_materialized(con, db_path)
    return con


def _is_locked_error(exc: BaseException) -> bool:
    """True iff ``exc`` is a SQLite 'database is locked'/'busy' OperationalError."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _retry_on_locked(work):
    """Run ``work()`` with bounded backoff retry on 'database is locked'.

    ``work`` must be self-contained (open its own connection, run its
    transaction, close). Each retry gets a fresh attempt from scratch, so it is
    safe only for operations that are idempotent under re-execution -- which the
    contract/handoff writers are (``ON CONFLICT(contract_id) DO NOTHING`` plus a
    rolled-back-on-failure transaction leave no partial state to re-apply). Any
    OperationalError that is NOT a lock/busy error propagates immediately.
    """
    last_exc: BaseException | None = None
    for attempt in range(_MAX_WRITE_RETRIES):
        try:
            return work()
        except sqlite3.OperationalError as exc:
            if not _is_locked_error(exc):
                raise
            last_exc = exc
            time.sleep(_WRITE_RETRY_BASE_SLEEP * (attempt + 1))
    assert last_exc is not None  # loop ran at least once
    raise last_exc


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
        # Identity-collapse fidelity: upsert_project (identity-collapse path)
        # looks up any existing row keyed by project_identity and UPDATEs it
        # IN PLACE, returning that row's PERSISTED name -- which can differ
        # from `name` when the row was first seen under a different basename.
        # A dry-run preview must return that SAME survivor name; otherwise a
        # still-present project (survivor under the persisted name) is counted
        # as vanished and would_mark_missing over-counts vs. did_mark_missing.
        if _projects_has_identity_column(con):
            existing = con.execute(
                "SELECT name FROM projects WHERE project_identity = ?",
                (project_identity,),
            ).fetchone()
            if existing is not None:
                return existing["name"]
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
        row_id = cur.lastrowid
    finally:
        con.close()
    # Automatic retention (mirrors episodes): occasionally prune events older
    # than the 90-day window. Runs on its own connection AFTER the insert is
    # committed and this one is closed, behind a 1/N gate, and swallows any
    # failure so it can never mask the successful append.
    _maybe_prune_harness_events(db_path=db_path)
    return row_id


# ---------------------------------------------------------------------------
# Public API: task_notifications (headless scheduled-task reports)
# ---------------------------------------------------------------------------
#
# These mirror the write_harness_event contract: episodic, NOT curated memory,
# so no agent_permissions gate. The difference is a MUTABLE `unread` flag that
# `ack` clears -- this table is a lightweight unread inbox, not an append-only
# audit mirror. Reads live in gaia.store.reader (list/get/count). The `gaia
# notifications add|ack` CLI is classified T0 (local bookkeeping, reversible)
# via COMMAND_SUBCOMMAND_TIER_EXCEPTIONS in mutative_verbs.py.

def add_task_notification(
    *,
    task_name: str,
    headline: str,
    body: str | None = None,
    session_id: str | None = None,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Insert one unread task-notification row and return its id.

    Called by a headless scheduled task (or the `gaia notifications add` CLI)
    when it finishes, to leave the user a generic PII-free report plus any
    accumulated approval_ids. The row starts ``unread=1``; `ack` clears it.

    Args:
        task_name: Name of the scheduled task that produced the report.
        headline: Short one-line summary (the title).
        body: Full detail message (generic; no PII / proper nouns).
        session_id: Resumable Claude session id (``claude --resume``).
        workspace: Workspace name, or None for a global notification.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.
    """
    if not task_name or not headline:
        raise ValueError("task_name and headline are required")
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO task_notifications
                (workspace, task_name, headline, body, session_id, created_at, unread)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (workspace, task_name, headline, body, session_id, _now_iso()),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def ack_task_notification(
    notification_id: int,
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Mark one notification as seen (unread=0). Idempotent.

    Returns ``{"status": "ok", "id": N, "action": "acked"|"noop"}``. ``noop``
    when the row was already read; ``{"status": "not_found"}`` when no such id.
    """
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT unread FROM task_notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        if row is None:
            return {"status": "not_found", "id": notification_id}
        if int(row["unread"]) == 0:
            return {"status": "ok", "id": notification_id, "action": "noop"}
        con.execute(
            "UPDATE task_notifications SET unread = 0, acked_at = ? WHERE id = ?",
            (_now_iso(), notification_id),
        )
        con.commit()
        return {"status": "ok", "id": notification_id, "action": "acked"}
    finally:
        con.close()


def ack_all_task_notifications(
    *,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Mark every unread notification seen; return the count cleared.

    When ``workspace`` is given, only that workspace's rows are cleared;
    otherwise ALL unread rows across workspaces are cleared.
    """
    con = _connect(db_path)
    try:
        if workspace is None:
            cur = con.execute(
                "UPDATE task_notifications SET unread = 0, acked_at = ? WHERE unread = 1",
                (_now_iso(),),
            )
        else:
            cur = con.execute(
                "UPDATE task_notifications SET unread = 0, acked_at = ? "
                "WHERE unread = 1 AND workspace = ?",
                (_now_iso(), workspace),
            )
        con.commit()
        return cur.rowcount
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: scheduled_tasks (OS-agnostic desired state for recurring tasks)
# ---------------------------------------------------------------------------
#
# The desired-state registry (see the scheduled_tasks table and the
# `scheduled-task` skill). Writing desired state (upsert / enable / disable /
# suspend / resume) is reversible local bookkeeping -- NOT a machine mutation --
# so, like briefs / plans / task_notifications, it carries no agent_permissions
# gate and the `gaia schedule register|list|show|status|enable|disable|suspend|
# resume` CLI classifies T0 via COMMAND_SUBCOMMAND_TIER_EXCEPTIONS. Only `gaia
# schedule sync` (materialize into the OS scheduler) and `gaia schedule remove`
# (irreversible deletion) are T3.
#
# Two DISTINCT ways a task is switched off, deliberately not collapsed:
#   enabled = 0                -- permanent, no deadline (enable/disable).
#   a schedule_suspensions row -- with a deadline that reactivates on its own,
#                                 or indefinite (suspend/resume). Expiry is
#                                 evaluated when read, never by a daemon.
# Reads live in gaia.store.reader.

def upsert_scheduled_task(
    *,
    name: str,
    schedule_spec: Mapping[str, Any] | str,
    schedule_hint: str | None = None,
    prompt_body: str | None = None,
    prompt_path: str | None = None,
    project_dir: str | None = None,
    wrapper_kind: str = "headless-claude",
    machine_scope: str = "all",
    machines: Sequence[str] | None = None,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Insert or update one desired-state task row; return its id.

    ``schedule_spec`` is the NEUTRAL schedule -- either a dict (serialized to
    JSON here) or an already-serialized JSON string. Matching is by
    (workspace, name): an existing row is UPDATED in place (preserving
    created_at, refreshing updated_at); otherwise a new row is inserted.

    When ``machine_scope == 'named'`` the ``machines`` list replaces the task's
    scheduled_task_machines rows. This does NOT touch any OS scheduler -- it only
    records the desired state; `gaia schedule sync` materializes it (T3).
    """
    if not name:
        raise ValueError("name is required")
    if isinstance(schedule_spec, str):
        spec_json = schedule_spec
        # Validate it parses, so a malformed spec fails at write time, not at
        # sync time on the machine.
        try:
            json.loads(spec_json)
        except Exception as exc:
            raise ValueError(f"schedule_spec is not valid JSON: {exc}") from exc
    else:
        spec_json = json.dumps(schedule_spec, separators=(",", ":"))
    if machine_scope not in ("all", "named"):
        raise ValueError("machine_scope must be 'all' or 'named'")

    con = _connect(db_path)
    try:
        now = _now_iso()
        existing = con.execute(
            "SELECT id FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
            (name, workspace),
        ).fetchone()
        if existing is not None:
            task_id = int(existing["id"])
            con.execute(
                """
                UPDATE scheduled_tasks
                   SET schedule_spec = ?, schedule_hint = ?, prompt_body = ?,
                       prompt_path = ?, project_dir = ?, wrapper_kind = ?,
                       machine_scope = ?, updated_at = ?
                 WHERE id = ?
                """,
                (spec_json, schedule_hint, prompt_body, prompt_path, project_dir,
                 wrapper_kind, machine_scope, now, task_id),
            )
        else:
            cur = con.execute(
                """
                INSERT INTO scheduled_tasks
                    (workspace, name, schedule_spec, schedule_hint, prompt_body,
                     prompt_path, project_dir, wrapper_kind, enabled,
                     machine_scope, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (workspace, name, spec_json, schedule_hint, prompt_body,
                 prompt_path, project_dir, wrapper_kind, machine_scope, now, now),
            )
            task_id = cur.lastrowid

        if machine_scope == "named":
            con.execute(
                "DELETE FROM scheduled_task_machines WHERE task_id = ?",
                (task_id,),
            )
            for m in (machines or []):
                con.execute(
                    "INSERT OR IGNORE INTO scheduled_task_machines (task_id, machine_name) "
                    "VALUES (?, ?)",
                    (task_id, m),
                )
        con.commit()
        return task_id
    finally:
        con.close()


def set_scheduled_task_enabled(
    name: str,
    enabled: bool,
    *,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Flip a task's enabled flag. Returns {"status": ok|not_found, ...}.

    The reversible counterpart to `remove`: a disabled task stays in the
    registry (so it can be re-enabled) but is not installed on next sync, and
    its already-installed entry is removed on next sync.
    """
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
            (name, workspace),
        ).fetchone()
        if row is None:
            return {"status": "not_found", "name": name}
        con.execute(
            "UPDATE scheduled_tasks SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, _now_iso(), int(row["id"])),
        )
        con.commit()
        return {"status": "ok", "name": name, "enabled": bool(enabled)}
    finally:
        con.close()


def delete_scheduled_task(
    name: str,
    *,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Delete a desired-state task row (T3). Cascades to machines/state/suspension rows.

    Irreversible in the registry -- the reversible path is
    ``set_scheduled_task_enabled(name, False)``. Does NOT remove the entry from
    any OS scheduler; a subsequent `gaia schedule sync` reconciles the now-orphan
    managed entry out of the crontab.
    """
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
            (name, workspace),
        ).fetchone()
        if row is None:
            return {"status": "not_found", "name": name}
        con.execute("DELETE FROM scheduled_tasks WHERE id = ?", (int(row["id"]),))
        con.commit()
        return {"status": "ok", "name": name, "id": int(row["id"])}
    finally:
        con.close()


def suspend_scheduled_tasks(
    *,
    name: str | None = None,
    until: str | None = None,
    reason: str | None = None,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Suspend one task (``name``) or the whole workspace (``name=None``).

    ``until`` is an ISO8601 UTC instant (build it with
    ``reader.parse_deadline``); None means INDEFINITE -- suspended with no
    deadline, which never lapses on its own. A suspension REPLACES any existing
    one at the same scope, so re-suspending extends or shortens the deadline
    instead of stacking rows.

    Reversible desired-state bookkeeping (T0), exactly like
    ``set_scheduled_task_enabled``: it records that the task should not run and
    touches no OS scheduler. The machine changes only when the user consents to
    `gaia schedule sync`.

    Returns {"status": ok|not_found, "scope": global|task, ...}.
    """
    con = _connect(db_path)
    try:
        task_id = None
        if name is not None:
            row = con.execute(
                "SELECT id FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
                (name, workspace),
            ).fetchone()
            if row is None:
                return {"status": "not_found", "name": name}
            task_id = int(row["id"])

        if task_id is None:
            con.execute(
                "DELETE FROM schedule_suspensions WHERE workspace IS ? AND task_id IS NULL",
                (workspace,),
            )
        else:
            con.execute(
                "DELETE FROM schedule_suspensions WHERE task_id = ?", (task_id,)
            )
        now = _now_iso()
        con.execute(
            "INSERT INTO schedule_suspensions "
            "(workspace, task_id, suspended_at, until, reason) VALUES (?, ?, ?, ?, ?)",
            (workspace, task_id, now, until, reason),
        )
        con.commit()
        return {
            "status": "ok",
            "scope": "task" if task_id is not None else "global",
            "name": name,
            "workspace": workspace,
            "suspended_at": now,
            "until": until,
            "indefinite": until is None,
            "reason": reason,
        }
    finally:
        con.close()


def resume_scheduled_tasks(
    *,
    name: str | None = None,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Clear the suspension on one task (``name``) or the workspace switch.

    Serves both endings a suspension can have, because the stored effect is the
    same either way: lifting a LIVE suspension early, and acknowledging a LAPSED
    one whose deadline already passed (the tasks are running again; this is the
    user confirming they saw it, which is what stops the SessionStart notice).
    The returned ``was_expired`` says which of the two happened.

    Returns {"status": ok|not_found|not_suspended, ...}. ``not_suspended``
    distinguishes "no such suspension to clear" from "no such task".
    """
    con = _connect(db_path)
    try:
        task_id = None
        if name is not None:
            row = con.execute(
                "SELECT id FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
                (name, workspace),
            ).fetchone()
            if row is None:
                return {"status": "not_found", "name": name}
            task_id = int(row["id"])

        if task_id is None:
            existing = con.execute(
                "SELECT * FROM schedule_suspensions "
                "WHERE workspace IS ? AND task_id IS NULL",
                (workspace,),
            ).fetchone()
        else:
            existing = con.execute(
                "SELECT * FROM schedule_suspensions WHERE task_id = ?", (task_id,)
            ).fetchone()
        if existing is None:
            return {
                "status": "not_suspended",
                "scope": "task" if task_id is not None else "global",
                "name": name,
            }

        until = existing["until"]
        was_expired = bool(until) and until <= _now_iso()
        con.execute("DELETE FROM schedule_suspensions WHERE id = ?", (int(existing["id"]),))
        con.commit()
        return {
            "status": "ok",
            "scope": "task" if task_id is not None else "global",
            "name": name,
            "workspace": workspace,
            "until": until,
            "was_expired": was_expired,
        }
    finally:
        con.close()


def mark_scheduled_task_state(
    task_id: int,
    machine_name: str,
    *,
    backend: str | None = None,
    installed: bool = True,
    db_path: Path | None = None,
) -> None:
    """Record per-machine materialization state after a sync install/remove.

    Upserts the (task_id, machine_name) row with the backend used and whether
    the task is currently installed on this machine, stamping last_synced_at.
    """
    con = _connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO scheduled_task_state
                (task_id, machine_name, backend, installed, last_synced_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id, machine_name) DO UPDATE SET
                backend = excluded.backend,
                installed = excluded.installed,
                last_synced_at = excluded.last_synced_at
            """,
            (task_id, machine_name, backend, 1 if installed else 0, _now_iso()),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_memory
# ---------------------------------------------------------------------------

VALID_MEMORY_TYPES = ("project", "user", "feedback", "atom", "decision", "negative")

# v45: which agent role a curated memory row's content is FOR. Orthogonal to
# type/class/status/project_ref/initiative -- see the schema.sql comment on
# memory.audience for the full rationale. 'any' is the default and the value
# every pre-v45 row keeps; kernel injection (a separate, later change) selects
# 'executor' rows for a subagent's kernel without leaking 'orchestrator' ones.
VALID_MEMORY_AUDIENCES = ("orchestrator", "executor", "any")


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


# ---------------------------------------------------------------------------
# initiative -- the canonical project/initiative grouping key (v32).
#
# `initiative` (memory.initiative) is the clean, vantage-independent key that
# unifies BOTH git projects and logical (non-repo) initiatives. It is DISTINCT
# from `project_ref` (the git-common-dir path): project_ref stays the git
# anchor; initiative is the human-facing grouping key that downstream reads
# (memory injection / get-relevant) group by. Populated at write time, never
# guessed -- resolves to None rather than fabricate a key.
# ---------------------------------------------------------------------------

_INITIATIVE_NORMALIZE_RE = _re_for_slug.compile(r"[^a-z0-9]+")


def normalize_initiative(raw: str | None) -> str | None:
    """Normalize a raw project/initiative label into the canonical
    ``memory.initiative`` key: lowercase, every run of non-alphanumeric chars
    collapsed to a single ``_``, leading/trailing ``_`` stripped. Returns
    ``None`` for empty / all-separator input (never an empty-string key).

    Examples: ``"Diagram Builder"`` -> ``"diagram_builder"``; ``"gaia"`` ->
    ``"gaia"``; ``"  "`` -> ``None``.
    """
    if raw is None:
        return None
    key = _INITIATIVE_NORMALIZE_RE.sub("_", str(raw).strip().lower()).strip("_")
    return key or None


def initiative_from_project_ref(project_ref: str | None) -> str | None:
    """Derive the canonical initiative key from a git ``project_ref``.

    ``project_ref`` is the git-common-dir path stored on a project-anchored
    memory row (e.g. ``/home/jorge/ws/me/gaia/.git``). The initiative is the
    repository basename with the trailing ``.git`` removed and then normalized
    -- ``/home/jorge/ws/me/gaia/.git`` -> ``"gaia"``. A ref that is not a
    ``.git`` path (e.g. a bare identity like ``github.com/me/x``) still yields
    its last path segment normalized (``"x"``). Returns ``None`` for an empty
    ref.
    """
    if not project_ref:
        return None
    ref = str(project_ref).strip().rstrip("/")
    if ref.endswith("/.git"):
        ref = ref[:-len("/.git")]
    elif ref.endswith(".git"):
        ref = ref[:-len(".git")].rstrip("/")
    base = ref.rsplit("/", 1)[-1]
    return normalize_initiative(base)


def upsert_memory(
    workspace: str,
    name: str,
    *,
    type: str,
    body: str,
    description: str | None = None,
    origin_session_id: str | None = None,
    project_ref: str | None = None,
    initiative: str | None = None,
    audience: str | None = None,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Upsert a curated-memory row in the ``memory`` table.

    Archive-on-upsert (scan-v2 SV3): when this overwrites an existing row, the
    ``memory_au``... no -- the ``trg_memory_history`` AFTER UPDATE trigger fires
    on the ON CONFLICT DO UPDATE below and archives the tracked before/after
    fields (name, body, workspace, type, description, class, status,
    project_ref, initiative, and deleted_at) into ``memory_history`` before the
    new value lands. No explicit archival code is needed here because ordinary
    updates share the SQL-layer trigger; hard deletion and workspace cascade
    remain outside that recovery guarantee.

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

    ``initiative`` -- canonical project/initiative grouping key (v32). Same
    coalesce-or-omit discipline as ``project_ref``. When ``initiative`` is not
    passed but ``project_ref`` is, it is auto-derived via
    :func:`initiative_from_project_ref` so every project-anchored write gets a
    key for free; pass an explicit ``initiative`` (already-normalized or raw --
    it is normalized here) to set a logical-initiative key with no git anchor.

    ``audience`` -- v45, orthogonal to type/class/status. Same coalesce-or-
    omit discipline as ``project_ref``/``initiative``: ``None`` (the default)
    never touches an existing row's audience on update, so a plain correction
    upsert cannot silently reset a row that was explicitly tagged
    'executor'/'orchestrator' back to 'any'. On INSERT of a brand-new row,
    ``None`` resolves to the schema's own default ('any') rather than NULL.
    Must be one of :data:`VALID_MEMORY_AUDIENCES` when set.
    """
    _assert_dispatch_can_write_memory()

    # initiative: explicit value wins (normalized); otherwise derive from the
    # git anchor. None when neither is available -- never guessed.
    if initiative is not None:
        initiative = normalize_initiative(initiative)
    elif project_ref is not None:
        initiative = initiative_from_project_ref(project_ref)

    if type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"invalid memory type {type!r}; must be one of {list(VALID_MEMORY_TYPES)}"
        )
    if audience is not None and audience not in VALID_MEMORY_AUDIENCES:
        raise ValueError(
            f"invalid memory audience {audience!r}; must be one of "
            f"{list(VALID_MEMORY_AUDIENCES)}"
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
                                    project_ref, initiative, origin_session_id,
                                    updated_at, audience, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'any'), ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    type              = excluded.type,
                    description       = excluded.description,
                    body              = excluded.body,
                    project_ref       = COALESCE(excluded.project_ref, project_ref),
                    initiative        = COALESCE(excluded.initiative, initiative),
                    origin_session_id = excluded.origin_session_id,
                    updated_at        = excluded.updated_at,
                    deleted_at        = NULL,
                    audience          = COALESCE(?, audience)
                """,
                # `audience` is bound twice deliberately: once for the INSERT
                # branch (COALESCE(?, 'any') -- a brand-new row with no
                # explicit audience gets the schema default) and once for the
                # UPDATE branch (COALESCE(?, audience) -- referencing the raw
                # parameter, NOT `excluded.audience`, so an upsert that does
                # not mention audience preserves the row's EXISTING value
                # instead of resetting it to 'any').
                #
                # `created_at` is bound ONCE, for the INSERT branch only, and
                # deliberately absent from the DO UPDATE SET list -- v50's row
                # age is forward-only BY DECISION. A brand-new row is born
                # with `now`; an existing row's `created_at` (NULL for every
                # pre-v50 row, since backfill is refused) is never touched by
                # this statement's UPDATE branch, so editing a row is never
                # mistaken for it being born.
                (workspace, name, type, description, body,
                 project_ref, initiative, origin_session_id, now, audience,
                 now, audience),
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


def reanchor_memory_project_ref(
    workspace: str,
    name: str,
    project_ref: str | None,
    *,
    db_path: Path | None = None,
) -> dict:
    """RE-ANCHOR an existing curated memory row's ``project_ref``.

    This is the correction path that ``upsert_memory`` deliberately does NOT
    provide: ``upsert_memory`` is COALESCE-preserving (it never clobbers an
    existing ``project_ref`` when the field is omitted, so callers cannot
    accidentally null it out), which means there is no way to CHANGE an already
    set anchor through the normal write path. This function is the explicit
    re-anchor: it OVERWRITES ``memory.project_ref`` to ``project_ref``
    unconditionally -- the value the ``gaia memory edit --project`` /
    ``--project-ref`` CLI path resolves and passes in.

    Passing ``project_ref=None`` explicitly CLEARS the anchor (back to the
    forward-only-unattributed state); the CLI never does this (it always
    resolves to a concrete identity), but the writer allows it so a caller can
    correct a wrongly-anchored row.

    Schema v41 includes ``project_ref`` in ``trg_memory_history``. A real
    re-anchor records its before/after anchor automatically; a no-op update is
    skipped here and creates no history row.

    Raises:
        ValueError: the ``(workspace, name)`` row does not exist.
    """
    _assert_dispatch_can_write_memory()

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT project_ref FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory '{name}' not found in workspace '{workspace}'"
            )

        before = row["project_ref"]
        now = _now_iso()
        con.execute(
            "UPDATE memory SET project_ref = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (project_ref, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "before_project_ref": before,
            "after_project_ref": project_ref,
            "updated_at": now,
        }
    finally:
        con.close()


def set_memory_audience(
    workspace: str,
    name: str,
    audience: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """PATCH the ``audience`` column of an existing curated memory row (v45).

    This is the dedicated correction path for ``audience`` -- mirroring
    :func:`reanchor_memory_project_ref` rather than
    :func:`update_memory_field`: ``audience`` is an enum-constrained
    classification, not free text, so it does not belong in
    ``_MEMORY_PATCHABLE_FIELDS`` (which applies text append/overwrite
    semantics that make no sense for an enum). ``gaia memory edit
    --audience=<value>`` calls this unconditionally -- unlike
    :func:`upsert_memory`'s coalesce-preserving ``audience`` parameter, this
    function always sets the value the caller passed.

    Args:
        workspace: Workspace name (FK -> workspaces.name).
        name:      Curated memory slug; the row must already exist.
        audience:  New value. Must be one of :data:`VALID_MEMORY_AUDIENCES`.
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "name": name, "before_audience": ...,
           "after_audience": audience, "updated_at": ...}``.

    Raises:
        ValueError: the ``(workspace, name)`` row does not exist, or
                    ``audience`` is not a valid enum value.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if audience not in VALID_MEMORY_AUDIENCES:
        raise ValueError(
            f"invalid memory audience {audience!r}; must be one of "
            f"{list(VALID_MEMORY_AUDIENCES)}"
        )

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT audience FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory '{name}' not found in workspace '{workspace}'"
            )

        before = row["audience"]
        now = _now_iso()
        con.execute(
            "UPDATE memory SET audience = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (audience, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "before_audience": before,
            "after_audience": audience,
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
# Public API: close_session_memory (transactional session-close checkpoint)
# ---------------------------------------------------------------------------
#
# Brief: session-close-checkpoint-verb.
#
# WHY a dedicated writer instead of composing upsert_memory + reclassify_memory
# + insert_memory_link at the call site: each of those opens its OWN connection
# and commits its OWN transaction, so an N+1-call sequence has N+1 independent
# commit points -- a failure on the 3rd call leaves the first two already
# durable. A session checkpoint is a SINGLE semantic unit (one record anchor +
# N carry-forward threads + N derived_from links); it must be all-or-nothing.
# This is the only memory writer that spans multiple rows under ONE
# BEGIN/COMMIT, mirroring the bulk_upsert pattern earlier in this module.
#
# Ordering is obligatory: anchor -> threads -> links. A derived_from edge needs
# both endpoints to exist, so the anchor and every thread are inserted before
# any link is written.
#
# Validation split (matches the codebase's existing layering):
#   * SHAPE of the payload (container structure + required name/body keys) is
#     validated UPFRONT, before any connection is opened -- a malformed payload
#     never reaches the DB. It raises MemorySessionPayloadError(code="bad_shape").
#   * SEMANTIC validation of each row (type in VALID_MEMORY_TYPES, slug<->type
#     via _validate_curated_slug, non-empty body) runs INSIDE the transaction,
#     immediately before each INSERT. A failure there raises ValueError and the
#     surrounding except rolls the whole checkpoint back to zero rows -- this is
#     the guarantee the dedicated function exists to provide.
# ---------------------------------------------------------------------------

class MemorySessionPayloadError(ValueError):
    """Raised when a session-checkpoint payload is malformed.

    Carries a stable ``code`` (default ``"bad_shape"``) so the CLI can map it
    to a structured error the orchestrator branches on, exactly like the
    ``missing_scope`` / ``project_unresolved`` codes ``_cmd_add`` emits.
    """

    def __init__(self, message: str, *, code: str = "bad_shape") -> None:
        super().__init__(message)
        self.code = code


# Markers that betray an unfiled pending sitting inside the RECORD body -- used
# only for the non-blocking warning heuristic (never a reject). Matched
# case-insensitively, per line for the checkbox form.
_PENDING_MARKER_RE = _re_for_slug.compile(
    r"(?im)(^\s*-\s*\[ \]|\btodo\b|\bpendiente|\bpending\b|pr[oó]ximo\s+paso|next\s+step)"
)


def _require_payload_keys(obj: Mapping, keys: tuple, where: str) -> None:
    """Raise bad_shape when any of ``keys`` is missing/empty on ``obj``."""
    for k in keys:
        v = obj.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise MemorySessionPayloadError(
                f"{where} must carry a non-empty {k!r}"
            )


def _upsert_checkpoint_row(
    con,
    workspace: str,
    *,
    name: str,
    mem_type: str,
    description: str | None,
    body: str,
    class_: str,
    status: str | None,
    project_ref: str | None,
    origin_session_id: str | None,
    now: str,
) -> dict:
    """Upsert one memory row on the CALLER's connection (no BEGIN/COMMIT here).

    Semantic validation runs first, INSIDE the caller's open transaction, so a
    bad row aborts the whole checkpoint via the caller's rollback. Combines the
    body/type/slug rules of ``upsert_memory`` with the class/status write of
    ``reclassify_memory`` in a single INSERT ... ON CONFLICT DO UPDATE.
    """
    if not body or not body.strip():
        raise ValueError(f"memory body cannot be empty (slug {name!r})")
    if mem_type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"invalid memory type {mem_type!r} for slug {name!r}; must be one "
            f"of {list(VALID_MEMORY_TYPES)}"
        )
    _validate_curated_slug(name, mem_type)

    existing = con.execute(
        "SELECT name FROM memory WHERE workspace = ? AND name = ?",
        (workspace, name),
    ).fetchone()
    action = "updated" if existing is not None else "inserted"
    # v32: derive the initiative grouping key from the git anchor (same
    # coalesce-or-omit discipline as project_ref). None when unanchored.
    initiative = initiative_from_project_ref(project_ref)
    con.execute(
        """
        INSERT INTO memory (workspace, name, type, description, body,
                            project_ref, initiative, origin_session_id,
                            updated_at, class, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace, name) DO UPDATE SET
            type              = excluded.type,
            description       = excluded.description,
            body              = excluded.body,
            project_ref       = COALESCE(excluded.project_ref, project_ref),
            initiative        = COALESCE(excluded.initiative, initiative),
            origin_session_id = excluded.origin_session_id,
            updated_at        = excluded.updated_at,
            class             = excluded.class,
            status            = excluded.status,
            deleted_at        = NULL
        """,
        # `created_at` (v50, forward-only): bound for the INSERT branch only,
        # absent from DO UPDATE SET, same discipline as upsert_memory above --
        # a brand-new checkpoint row is born with `now`; an existing row's
        # `created_at` is never touched by this UPDATE branch.
        (workspace, name, mem_type, description, body,
         project_ref, initiative, origin_session_id, now, class_, status, now),
    )
    return {
        "name": name,
        "action": action,
        "class": class_,
        "memory_status": status,
    }


def _insert_checkpoint_link(
    con, workspace: str, src_name: str, dst_name: str, kind: str, now: str
) -> str:
    """Insert one memory_links edge on the CALLER's connection. Idempotent.

    Both endpoints are guaranteed to exist -- the caller inserts the anchor and
    every thread before any link -- so this skips the endpoint-existence probes
    ``insert_memory_link`` does and only guards against a duplicate edge (making
    the whole checkpoint safely re-runnable). Returns ``"inserted"`` or
    ``"noop"``.
    """
    existing = con.execute(
        "SELECT 1 FROM memory_links "
        "WHERE workspace = ? AND src_name = ? AND dst_name = ? AND kind = ?",
        (workspace, src_name, dst_name, kind),
    ).fetchone()
    if existing is not None:
        return "noop"
    con.execute(
        "INSERT INTO memory_links (workspace, src_name, dst_name, kind, "
        "                          created_at) VALUES (?, ?, ?, ?, ?)",
        (workspace, src_name, dst_name, kind, now),
    )
    return "inserted"


def _checkpoint_warnings(record_body: str, pendientes: list) -> list:
    """Heuristic (non-blocking): warn when the record body reads like it hides
    a pending but no carry-forward threads were provided."""
    warnings: list[str] = []
    if not pendientes and _PENDING_MARKER_RE.search(record_body or ""):
        warnings.append(
            "record body contains pending markers (TODO / pendiente / next "
            "step / '- [ ]') but no carry-forward threads were provided. A "
            "pending buried in the record body is NEVER re-injected at "
            "SessionStart (only class=thread status=carry_forward rows "
            "resurface); split it into a 'pendientes' entry so it survives."
        )
    return warnings


def close_session_memory(
    workspace: str,
    payload: Mapping[str, Any],
    *,
    project_ref: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Persist a whole session-close reflection atomically.

    ``payload`` shape::

        {
          "resumen":   {"name", "type", "description", "body"},
          "pendientes": [{"name", "description", "body"}, ...]   # may be empty
        }

    Semantics, all in ONE transaction (rollback to zero rows on any failure):
      1. ``resumen`` -> upsert as a ``class=anchor`` record row.
      2. each ``pendientes[i]`` -> upsert as a ``class=thread
         status=carry_forward`` row (type inherited from ``resumen`` -- the
         payload carries no per-pending type, matching the session-reflection
         convention where record and threads share ``--type``).
      3. a ``derived_from`` edge from each thread back to the anchor.

    Idempotent: re-running the same payload UPSERTs the same rows and re-uses
    the same edges (the fecha-stamped slug convention avoids collisions).

    Returns::

        {"status": "applied", "anchor": {...}, "threads": [...],
         "links": [...], "warnings": [...], "updated_at": ...}

    Raises:
        MemorySessionPayloadError: malformed payload (``code="bad_shape"``);
            raised before any connection is opened.
        ValueError: a row failed semantic validation (invalid type, slug<->type
            mismatch, empty body) -- the whole checkpoint is rolled back.
        MemoryWriteForbidden: GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    # -- SHAPE validation (bad_shape), upfront, before touching the DB --------
    if not isinstance(payload, Mapping):
        raise MemorySessionPayloadError("payload must be a JSON object")
    resumen = payload.get("resumen")
    if not isinstance(resumen, Mapping):
        raise MemorySessionPayloadError("payload.resumen must be an object")
    _require_payload_keys(resumen, ("name", "type", "body"), "payload.resumen")

    pendientes = payload.get("pendientes")
    if pendientes is None:
        pendientes = []
    if not isinstance(pendientes, list):
        raise MemorySessionPayloadError("payload.pendientes must be a list")
    for i, p in enumerate(pendientes):
        if not isinstance(p, Mapping):
            raise MemorySessionPayloadError(
                f"payload.pendientes[{i}] must be an object with name+body"
            )
        _require_payload_keys(p, ("name", "body"), f"payload.pendientes[{i}]")

    record_type = resumen["type"]
    record_name = resumen["name"]
    origin_session_id = os.environ.get("GAIA_SESSION_ID") or None
    now = _now_iso()

    # -- one connection, one BEGIN, one commit/rollback -----------------------
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)

            # (1) record anchor
            anchor = _upsert_checkpoint_row(
                con, workspace,
                name=record_name, mem_type=record_type,
                description=resumen.get("description"), body=resumen["body"],
                class_="anchor", status=None,
                project_ref=project_ref, origin_session_id=origin_session_id,
                now=now,
            )

            # (2) carry-forward threads, then (3) derived_from edges
            threads: list[dict] = []
            links: list[dict] = []
            for p in pendientes:
                threads.append(_upsert_checkpoint_row(
                    con, workspace,
                    name=p["name"], mem_type=record_type,
                    description=p.get("description"), body=p["body"],
                    class_="thread", status="carry_forward",
                    project_ref=project_ref,
                    origin_session_id=origin_session_id, now=now,
                ))
                link_action = _insert_checkpoint_link(
                    con, workspace, p["name"], record_name, "derived_from", now,
                )
                links.append({
                    "src_name": p["name"],
                    "dst_name": record_name,
                    "kind": "derived_from",
                    "action": link_action,
                })

            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    return {
        "status": "applied",
        "anchor": anchor,
        "threads": threads,
        "links": links,
        "warnings": _checkpoint_warnings(resumen["body"], pendientes),
        "updated_at": now,
    }


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

    Projects the v48 telemetry pair (``injection_count``/``last_injected_at``,
    ``deliberate_count``/``last_deliberate_at``) alongside the row's content.
    Reading never bumps either counter; only ``record_memory_access`` does.
    """
    con = _connect(db_path)
    try:
        sql = (
            "SELECT workspace, name, type, description, body, project_ref, "
            "       initiative, origin_session_id, updated_at, deleted_at, "
            "       audience, injection_count, deliberate_count, "
            "       last_injected_at, last_deliberate_at "
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


_MEMORY_LIST_ORDERS: dict[str, str] = {
    "name": "name",
    "injection": "injection_count",
    "deliberate": "deliberate_count",
}

# The direction a key is asked for when the caller names none: a name is
# looked up alphabetically, a counter to see the head of its ranking.
_MEMORY_LIST_DEFAULT_DIRECTIONS: dict[str, str] = {
    "name": "asc",
    "injection": "desc",
    "deliberate": "desc",
}

_MEMORY_LIST_DIRECTIONS = ("asc", "desc")


def list_memory(
    workspace: str,
    *,
    type: str | None = None,
    audience: str | None = None,
    class_: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    order_by: str = "name",
    direction: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """List curated memory rows, optionally filtered by ``type``/``audience``/
    ``class_``/``status``.

    Tombstoned rows (``deleted_at`` non-NULL, scan-v2 SV3) are excluded by
    default; pass ``include_deleted=True`` to include them. ``audience``
    (v45) filters to rows tagged with exactly that value -- it must be one of
    :data:`VALID_MEMORY_AUDIENCES` when set; ``None`` (the default) applies no
    audience filter. ``class_``/``status`` (memory.class/memory.status, same
    trailing-underscore convention as ``reclassify_memory``) filter the same
    way when set; ``None`` applies no filter on either.

    Projects ``class``, ``status`` and the v48 telemetry pair
    (``injection_count``/``last_injected_at``, ``deliberate_count``/
    ``last_deliberate_at``) alongside the content columns.

    ``order_by`` selects the sort key: ``"name"`` (the default) or one of
    ``"injection"``/``"deliberate"``. The two counters are never combined into
    one key -- a single blended score would let automatic injections pass for
    deliberate reads. ``direction`` is ``"asc"``/``"desc"``, defaulting per key
    to :data:`_MEMORY_LIST_DEFAULT_DIRECTIONS`; ties always break by name
    ascending, so the least-used tail is as readable as the head.
    """
    if audience is not None and audience not in VALID_MEMORY_AUDIENCES:
        raise ValueError(
            f"invalid memory audience {audience!r}; must be one of "
            f"{list(VALID_MEMORY_AUDIENCES)}"
        )
    if order_by not in _MEMORY_LIST_ORDERS:
        raise ValueError(
            f"invalid memory list order {order_by!r}; must be one of "
            f"{list(_MEMORY_LIST_ORDERS)}"
        )
    direction = direction or _MEMORY_LIST_DEFAULT_DIRECTIONS[order_by]
    if direction not in _MEMORY_LIST_DIRECTIONS:
        raise ValueError(
            f"invalid memory list direction {direction!r}; must be one of "
            f"{list(_MEMORY_LIST_DIRECTIONS)}"
        )
    sort_column = _MEMORY_LIST_ORDERS[order_by]
    order_clause = f"{sort_column} {direction.upper()}"
    if sort_column != "name":
        order_clause += ", name ASC"
    con = _connect(db_path)
    try:
        where = ["workspace = ?"]
        params: list = [workspace]
        if type is not None:
            where.append("type = ?")
            params.append(type)
        if audience is not None:
            where.append("audience = ?")
            params.append(audience)
        if class_ is not None:
            where.append("class = ?")
            params.append(class_)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if not include_deleted:
            where.append("deleted_at IS NULL")
        sql = (
            "SELECT name, type, description, updated_at, audience, class, "
            "       status, injection_count, deliberate_count, "
            "       last_injected_at, last_deliberate_at "
            "FROM memory WHERE " + " AND ".join(where) +
            " ORDER BY " + order_clause
        )
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: curated-memory usage telemetry (v48, best-effort)
# ---------------------------------------------------------------------------
#
# Two hard constraints, both load-bearing for the rest of the entry:
#   * NARROW UPDATE ONLY. This never goes through upsert_memory/
#     update_memory_field (the normal save path): those rewrite `body` and
#     `updated_at` and trip trg_memory_history. The UPDATE below touches
#     exactly one counter and its own timestamp column -- both are outside
#     trg_memory_history's WHEN clause and memory_au's WHEN clause by design
#     (see the v48 notes on both triggers in schema.sql), so a telemetry write
#     lands zero memory_history rows, zero FTS re-index, and never moves
#     `updated_at` -- the sort key the injected block's ordering depends on.
#   * BEST-EFFORT. This runs on every deliberate read and every automatic
#     injection, so a failure here (a busy DB, a locked file, anything) must
#     never surface to the caller: measuring usage can never cost the read it
#     is measuring. Every failure mode -- connect, execute, commit -- is
#     swallowed and reported back only as `False`.
#
# Deliberately does NOT call _assert_dispatch_can_write_memory(): that gate
# gags subagents from authoring curated memory, but reading a row (show,
# get-relevant --initiative) is not authoring it, and any dispatched agent
# must be able to trigger this write when it reads.
#
# v50: "kernel" is a third, separate axis (kernel_count/last_kernel_at) for
# the dispatch kernel's own "How the user works" block, which fires on EVERY
# subagent dispatch over type=user AND audience=executor rows. It used to
# share "injection"'s columns; that mixed a fixed, high-frequency dispatch
# signal into the same counter as context-injection surfaces (get-relevant
# digest/sections/types), letting the kernel rows dominate any ranking by
# construction. Kept apart for the same reason injection and deliberate were
# kept apart in v48: mixing signals of different natures freezes the
# ranking. Forward-only -- what the kernel already added to injection_count
# before this split is NOT retroactively moved or subtracted.
_MEMORY_TELEMETRY_COLUMNS: dict[str, tuple[str, str]] = {
    "injection": ("injection_count", "last_injected_at"),
    "deliberate": ("deliberate_count", "last_deliberate_at"),
    "kernel": ("kernel_count", "last_kernel_at"),
}


def record_memory_access(
    workspace: str,
    name: str,
    kind: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Best-effort telemetry bump for one curated-memory row access.

    ``kind`` selects which counter/timestamp pair to bump -- ``"deliberate"``
    for a row the caller identified (by slug, or by naming the initiative that
    holds it), ``"injection"`` for a row rendered inside an automatic context
    block for someone who asked for neither (get-relevant digest/sections/
    types), ``"kernel"`` for a row rendered inside the dispatch kernel's own
    "How the user works" block, which fires on every subagent dispatch and is
    kept off the ``"injection"`` axis for the same reason injection and
    deliberate are kept apart. Raises ``ValueError``
    for any other ``kind`` (a programming error, not a runtime condition worth
    degrading for). Every other failure -- DB locked, connect/execute/commit
    raising for any reason -- is caught and reported as ``False``; this
    function never raises past the ``kind`` check and never blocks or breaks
    the read it is instrumenting.

    Returns ``True`` iff the UPDATE committed (whether or not a row matched --
    a miss is not treated as a failure, since the caller already resolved the
    row before calling this).
    """
    if kind not in _MEMORY_TELEMETRY_COLUMNS:
        raise ValueError(
            f"invalid telemetry kind {kind!r}; must be one of "
            f"{list(_MEMORY_TELEMETRY_COLUMNS)}"
        )
    count_col, ts_col = _MEMORY_TELEMETRY_COLUMNS[kind]
    try:
        con = _connect(db_path)
    except Exception:
        return False
    try:
        con.execute(
            f"UPDATE memory SET {count_col} = {count_col} + 1, "
            f"{ts_col} = ? WHERE workspace = ? AND name = ?",
            (_now_iso(), workspace, name),
        )
        con.commit()
        return True
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            con.close()
        except Exception:
            pass


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

    Raises ContentWriteForbidden when GAIA_DISPATCH_AGENT names a dispatched
    agent not authorized to author plan content (plan content is authored by
    the planner). A human CLI call / orchestrator main session (no dispatch
    identity) is always allowed.
    """
    from gaia.state.permissions import _assert_dispatch_can_write_content
    _assert_dispatch_can_write_content("plans")

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
    override_reason: object | None = None,
    db_path: Path | None = None,
) -> dict:
    """Transition a task's ``status`` after validating the move is legal.

    Navigates workspace -> brief_name -> brief_id -> plan_id -> task row
    by ``(plan_id, order_num)`` where ``task_id`` is the order_num integer.

    CLOSING A TASK IS CONDITIONED; the other transitions are not. A move to
    ``'done'`` is permitted only when the task's persisted gates amount to an
    approving verdict OR the caller states why it is being closed anyway -- the
    disjunction owned by ``gaia.state.task_closure_condition``. A move to
    ``'skipped'``, and the reopen to ``'pending'``, carry no gate condition
    (``UNCONDITIONED_STATUSES``); neither asserts that the work was verified.

    A PRODUCER MAY NOT CLOSE ITS OWN TASK, AND NO OVERRIDE LIFTS THAT. Above the
    disjunction sits an identity refusal: when a handoff row binds an agent to
    this task by ``plan_task_id`` and the caller carries that agent's name, the
    close is refused with or without an override
    (``gaia.state.task_closure_identity``). When NOTHING names a producer, the
    absence grants nothing -- the disjunction above decides exactly as it
    otherwise would, so a close with no approving verdict still needs the same
    override and no second path exists to reach one. Only ``'done'`` is guarded;
    a producer marking its own task ``'skipped'`` or reopening it stays exempt,
    because neither asserts the work was verified.

    THE CONDITION LIVES HERE, NOT ONLY IN THE CLI. This is the single writer of
    ``tasks.status``, so a condition placed here holds for every future caller --
    a hook, a convergence step, a verifier seam -- whereas one placed at the
    command-line flag holds only for the one path that goes through it.

    AN OVERRIDE'S RECORD PRECEDES ITS MUTATION. When the override carries the
    close, ``write_task_close_override_event`` is appended BEFORE the UPDATE and
    is deliberately not wrapped: a failure to record the justification aborts
    the close rather than completing it silently. The two possible failures are
    not symmetric -- a record of a close that did not happen is a visible
    inconsistency, while a close with no record is exactly the silent escape
    hatch the channel exists to prevent.

    A NO-OP IS NOT A CLOSURE. When the task already holds ``new_status`` the
    call returns before the condition is consulted: nothing is being closed, so
    there is neither anything to justify nor anything to record. That keeps a
    re-issued close idempotent instead of demanding a fresh override for a
    transition that will not happen.

    Args:
        workspace:       Workspace owning the brief.
        brief_name:      Brief whose plan holds the task.
        task_id:         The task's ``order_num`` within that plan.
        new_status:      Target status (``VALID_TASK_STATUSES``).
        override_reason: WHY this task is being closed without an approving gate
                         verdict. ``None`` (the default) requests no override.
                         Anything else is a request and must state something.
        db_path:         Optional explicit DB path (used by tests).

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises:
        ValueError: on illegal transition, missing entity, an override reason
            that states nothing, or an override stated for a transition that is
            not a closure.
        TaskClosureBlocked: (a ``ValueError``) when a close is backed by neither
            an approving verdict nor a stated reason, or when the caller is the
            agent the task was dispatched to.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")
    from gaia.state import VALID_TASK_STATUSES
    from gaia.state.task_closure import derive_gate_verdict
    from gaia.state.task_closure_condition import (
        TaskClosureBlocked,
        closure_is_conditioned,
        override_not_applicable_message,
    )
    from gaia.state.task_closure_event import normalize_reason, resolve_actor
    from gaia.state.task_closure_identity import (
        classify_producer_standing,
        decide_closure_under_identity,
        producer_agent_names,
    )
    from gaia.state.transitions import assert_legal_task_lifecycle

    if new_status not in VALID_TASK_STATUSES:
        raise ValueError(
            f"invalid task status {new_status!r}; must be one of "
            f"{list(VALID_TASK_STATUSES)}"
        )

    # An override is an argument about closing, so its shape is checked here,
    # ahead of any DB work: it is rejected identically whatever the row turns
    # out to hold, including when the transition would have been a no-op.
    if override_reason is not None:
        override_reason = normalize_reason(override_reason)
        if not closure_is_conditioned(new_status):
            raise ValueError(override_not_applicable_message(new_status))

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

        if closure_is_conditioned(new_status):
            # The two impure reads the condition needs, both performed here
            # rather than inside the predicate: WHO is asking (the one identity
            # coordinate a CLI invocation carries -- an agent NAME, resolved by
            # the same resolver the audit record uses, so a human caller is a
            # known identity and not a blank), and WHO the task was dispatched
            # to. Everything downstream of these two values is pure.
            caller_agent = resolve_actor(os.environ.get("GAIA_DISPATCH_AGENT"))
            decision = decide_closure_under_identity(
                verdict=derive_gate_verdict(
                    _read_task_gate_rows(con, task_row["id"])
                ),
                brief_name=brief_name,
                task_order_num=task_id,
                standing=classify_producer_standing(
                    caller_agent=caller_agent,
                    producer_agents=producer_agent_names(
                        _read_task_binding_rows(con, task_row["id"])
                    ),
                ),
                caller_agent=caller_agent,
                override_reason=override_reason,
            )
            if not decision.permitted:
                raise TaskClosureBlocked(decision.denial_message)
            if decision.override_used:
                write_task_close_override_event(
                    workspace,
                    brief_name,
                    task_order_num=task_id,
                    reason=decision.reason,
                    task_id=task_row["id"],
                    details={
                        "gate_count": decision.verdict.gate_count,
                        "gate_status_counts": dict(decision.verdict.status_counts),
                        "verdict_reasons": list(decision.verdict.reasons),
                    },
                    db_path=db_path,
                )

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


def update_task(
    workspace: str,
    brief_name: str,
    order_num: int,
    *,
    goal: str,
    db_path: Path | None = None,
) -> dict:
    """Update ``goal`` IN PLACE for the task at ``order_num`` in the plan
    attached to ``brief_name``.

    Preserves ``tasks.id`` and, critically, every child ``task_gates`` row:
    ``task_gates.task_id`` carries ``ON DELETE CASCADE`` from ``tasks.id``
    (schema.sql), so the only prior way to edit a goal -- ``remove_task_from_plan``
    followed by ``add_task_to_plan`` -- deletes the task row and cascades away
    every gate attached to it. Adjusting a task's scope is not the same
    operation as replacing the task, and this writer is what lets a caller do
    the former without paying for the latter.

    ``status`` is untouched here; the state machine stays the exclusive
    province of :func:`set_task_status`. ``order_num`` and ``id`` are the
    resolution keys, never rewritten by this call.

    Raises ValueError on missing brief/plan/task or an empty ``goal``.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    if not goal or not goal.strip():
        raise ValueError("task goal cannot be empty")

    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, order_num
        )
        con.execute(
            "UPDATE tasks SET goal = ? WHERE id = ?",
            (goal, task_id),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "order_num": order_num,
            "task_id": task_id,
            "fields": ["goal"],
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


def list_plan_tasks(
    workspace: str,
    brief_name: str,
    *,
    status: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return the task rows for the plan attached to ``brief_name``.

    Read surface for ``gaia task list`` -- scoped to the single plan attached
    to the brief (plans.brief_id is UNIQUE), ordered by order_num. Optionally
    filtered by ``status`` (one of pending/done/skipped). Raises ValueError on
    a missing brief or a brief with no plan attached (mirroring
    add_task_to_plan's "no plan attached" contract), so a caller can tell an
    empty plan (returns []) apart from an unplanned brief (raises).
    """
    if status is not None and status not in ("pending", "done", "skipped"):
        raise ValueError(
            f"status must be one of pending/done/skipped, got {status!r}"
        )

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

        if status is None:
            rows = con.execute(
                "SELECT id, plan_id, order_num, goal, status, evidence_path "
                "FROM tasks WHERE plan_id = ? ORDER BY order_num",
                (plan_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, plan_id, order_num, goal, status, evidence_path "
                "FROM tasks WHERE plan_id = ? AND status = ? ORDER BY order_num",
                (plan_id, status),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_task_by_order(
    workspace: str,
    brief_name: str,
    order_num: int,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Return the single task row at ``order_num`` in the plan attached to
    ``brief_name``, or None if no task sits at that order_num.

    Read surface for ``gaia task show``. Same row shape as
    :func:`list_plan_tasks` (id, plan_id, order_num, goal, status,
    evidence_path) -- ``id`` is ``tasks.id``, the row id the dispatch contract's
    ``task_id=<N>`` token requires, which is NEVER the same value as
    ``order_num`` (the plan-position ordinal a human reads/types). Raises
    ValueError on a missing brief or a brief with no plan attached (mirroring
    list_plan_tasks's contract), so a caller can tell "no task at this
    order_num" (returns None) apart from "no plan to look in" (raises).
    """
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

        row = con.execute(
            "SELECT id, plan_id, order_num, goal, status, evidence_path "
            "FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, order_num),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# task_gates: planner-authored typed verification gate slot (v34, harness R1-A)
#
# A gate is a one-to-many child of a task, addressed by its parent task's
# order_num within the plan attached to a brief -- consistent with how
# add_task_to_plan / remove_task_from_plan address tasks. These writers persist
# a gate AS GIVEN: structural validation (gaia.state.gate_validation) is a
# separate, independently-invokable pure function; whether `add` blocks a
# malformed gate at write time is the advisory-vs-blocking decision, which is
# out of scope for R1-A.
# ---------------------------------------------------------------------------

def _resolve_task_id_by_order(
    con: sqlite3.Connection,
    workspace: str,
    brief_name: str,
    order_num: int,
) -> int:
    """Resolve the tasks.id for (brief plan, order_num). Raises ValueError."""
    brief_id = _resolve_brief_id(con, workspace, brief_name)
    if brief_id is None:
        raise ValueError(
            f"brief '{brief_name}' not found in workspace '{workspace}'"
        )
    plan_row = con.execute(
        "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
    ).fetchone()
    if plan_row is None:
        raise ValueError(f"no plan attached to brief '{brief_name}'")
    task_row = con.execute(
        "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
        (plan_row["id"], order_num),
    ).fetchone()
    if task_row is None:
        raise ValueError(
            f"task with order_num={order_num} not found in plan for "
            f"brief '{brief_name}'"
        )
    return task_row["id"]


def _assert_valid_gate_status(status: str) -> None:
    """Raise ValueError when ``status`` is outside VALID_GATE_STATUSES.

    Code-level guard for task_gates.status (harness B3/T3). As of v36 the
    column also carries a DB CHECK (scripts/migrations/v35_to_v36.sql; see
    gaia.state.VALID_GATE_STATUSES), but this guard remains the first
    enforcement point: it raises a clean ValueError at the call site instead
    of letting an out-of-vocabulary value reach sqlite3 and surface as a raw
    IntegrityError. Shared by every write path that touches the column --
    add_gate_to_task (initial status) and set_gate_status (transition) -- so
    neither can slip an out-of-vocabulary value past the other.
    """
    from gaia.state import VALID_GATE_STATUSES
    if status not in VALID_GATE_STATUSES:
        raise ValueError(
            f"invalid gate status {status!r}: must be one of {VALID_GATE_STATUSES}"
        )


def add_gate_to_task(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    verification_type: str,
    *,
    evidence_type: str | None = None,
    evidence_shape: str | None = None,
    artifact_path: str | None = None,
    status: str = "pending",
    db_path: Path | None = None,
) -> dict:
    """Insert a task_gates row for the task at ``task_order_num``.

    Persists the gate AS GIVEN, except for ``status``: it is validated
    up front against ``gaia.state.VALID_GATE_STATUSES`` (code-level guard --
    see ``_assert_valid_gate_status``) so an out-of-vocabulary value raises a
    clean ValueError here rather than surfacing as a raw sqlite3
    IntegrityError from the DB CHECK the column also carries as of v36.
    Structural completeness of the rest of the gate is validated separately by
    gaia.state.gate_validation.validate_gate.

    Raises ValueError on missing brief/plan/task, an out-of-enum
    verification_type, or an out-of-vocabulary status.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")
    _assert_valid_gate_status(status)

    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, task_order_num
        )
        try:
            cur = con.execute(
                "INSERT INTO task_gates "
                "(task_id, verification_type, evidence_type, evidence_shape, "
                " artifact_path, status) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, verification_type, evidence_type, evidence_shape,
                 artifact_path, status),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"could not insert gate (verification_type={verification_type!r}): {exc}"
            ) from exc
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "brief_name": brief_name,
            "task_order_num": task_order_num,
            "gate_id": cur.lastrowid,
            "verification_type": verification_type,
        }
    finally:
        con.close()


def _read_task_binding_rows(con: sqlite3.Connection, task_id: int) -> list[dict]:
    """Return the handoff rows bound to one plan task, on an OPEN connection.

    The counterpart of :func:`_read_task_gate_rows` for the identity axis of the
    closure condition: every ``agent_contract_handoffs`` row that references this
    ``tasks.id`` through ``plan_task_id``, which is the only column that ties an
    agent to a task it was dispatched to execute.

    Deliberately UNFILTERED by ``agent_state`` or ``kind``. A row born at
    dispatch, a row that later converged to a terminal verdict, and a row written
    by some future path all name an actor, and treating each of them as naming a
    producer is the fail-closed direction: a new writer of the binding widens the
    refusal instead of slipping past it. Which of those actors is a COMPARABLE
    name is not decided here -- that is
    ``gaia.state.task_closure_identity.producer_agent_names``, so the impure read
    stays a read.

    Takes the connection rather than a path so the closure condition can ask
    mid-transaction without opening a second one.
    """
    rows = con.execute(
        "SELECT id, agent_id, plan_task_id, kind, agent_state "
        "FROM agent_contract_handoffs WHERE plan_task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _read_task_gate_rows(con: sqlite3.Connection, task_id: int) -> list[dict]:
    """Return one task's gate rows on an ALREADY OPEN connection.

    The single SELECT behind every gate read, so ``list_task_gates`` and the
    closure condition inside ``set_task_status`` see the identical row shape --
    which is what lets ``gaia.state.task_closure.derive_gate_verdict`` consume
    either without a translation step. ``task_id`` is the persisted
    ``tasks.id``, not an ``order_num``.

    Takes the connection rather than a path so a caller mid-transaction can ask
    without opening a second one.
    """
    rows = con.execute(
        "SELECT id, task_id, verification_type, evidence_type, "
        "       evidence_shape, artifact_path, status "
        "FROM task_gates WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_task_gates(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Return the gate rows for the task at ``task_order_num`` (ordered by id).

    Read surface for `gaia task gate list`. Raises ValueError on missing
    brief/plan/task.
    """
    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, task_order_num
        )
        return _read_task_gate_rows(con, task_id)
    finally:
        con.close()


def read_task_gate_verdict(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    *,
    db_path: Path | None = None,
) -> GateVerdict:
    """Return the derived gate verdict for the task at ``task_order_num``.

    The read half of the derivation: fetch the task's persisted gates and hand
    them to ``gaia.state.task_closure.derive_gate_verdict``, which owns the
    semantics (fail closed; zero gates is NOT an approving verdict). Returns a
    ``GateVerdict``.

    Read-only by construction: one SELECT, no UPDATE, no INSERT, no commit --
    it cannot advance ``tasks.status`` or ``task_gates.status``, and so it does
    NOT call ``_assert_dispatch_can_advance_state``. That omission is
    deliberate, not an oversight: the guard exists to gate state ADVANCEMENT on
    a dispatch identity, and making a pure read answer differently depending on
    who is asking would both misuse the guard and couple the verdict to a
    dispatch coordinate the derivation is specified to ignore.

    Raises ValueError on missing brief/plan/task -- an unresolvable task yields
    no verdict at all rather than a non-approving one, since "this task does
    not exist" and "this task is not approved" are different answers and only
    the second is a verdict. Consistent with ``list_task_gates``, whose
    resolution it reuses.
    """
    from gaia.state.task_closure import derive_gate_verdict

    gates = list_task_gates(
        workspace, brief_name, task_order_num, db_path=db_path
    )
    return derive_gate_verdict(gates)


def write_task_close_override_event(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    *,
    reason: str,
    actor: str | None = None,
    task_id: int | None = None,
    details: Mapping[str, Any] | None = None,
    db_path: Path | None = None,
) -> int:
    """Append the auditable record of a manual task-close override.

    The impure half of ``gaia.state.task_closure_event``, which owns the record's
    shape and the reasoning behind it: append-only into ``harness_events``, graded
    above ``info`` so ``gaia defects`` surfaces it, actor in the ``agent`` column
    so it is filterable.

    Three properties of this seam are worth stating because a caller could
    reasonably assume the opposite of each:

    NOT A SECOND WRITER OF ``tasks.status``. It appends one row to a different
    table and touches no state machine, which is why it does not call
    ``_assert_dispatch_can_advance_state``: there is no transition to authorize.
    Whoever closes the task still goes through ``set_task_status``, the single
    writer.

    NOT BEST-EFFORT. The hook pipeline wraps ``write_harness_event`` in
    ``try/except: pass`` because telemetry must never break a hook. This record is
    not telemetry -- it is the stated justification for a state change -- so a
    failure to write it must surface. Exceptions propagate; do not swallow them at
    the call site.

    NO TASK LOOKUP. ``brief_name`` and ``task_order_num`` are recorded as given
    and ``task_id`` is whatever the caller already resolved. ``harness_events``
    holds no foreign key to ``tasks`` by design, and making the append depend on a
    resolution would let a lookup failure suppress the record of a mutation that
    already happened.

    Args:
        workspace:      Workspace the record is attributed to -> ``workspace``
                        column, which is how ``gaia defects --workspace=W`` finds
                        it.
        brief_name:     Brief owning the task's plan.
        task_order_num: The task's ``order_num`` within that plan.
        reason:         WHY the task is closed without an approving verdict.
        actor:          Explicit actor. When ``None`` (the default) the dispatch
                        identity is read from ``GAIA_DISPATCH_AGENT`` -- the one
                        environment read in this channel, and the only identity
                        coordinate a CLI invocation carries.
        task_id:        Persisted ``tasks.id``, when the caller has it.
        details:        Optional structured context, nested in the payload so it
                        cannot shadow the actor or the reason.
        db_path:        Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the appended ``harness_events`` row.

    Raises:
        ValueError: when ``reason`` states nothing (see
            ``task_closure_event.MISSING_REASON_MESSAGE``).
    """
    from gaia.state.task_closure_event import build_override_event

    actor_source = actor if actor is not None else os.environ.get("GAIA_DISPATCH_AGENT")
    event = build_override_event(
        brief_name=brief_name,
        task_order_num=task_order_num,
        reason=reason,
        actor=actor_source,
        task_id=task_id,
        details=details,
    )
    return write_harness_event(
        workspace=workspace,
        db_path=db_path,
        **event.as_write_kwargs(),
    )


def remove_gate_from_task(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    gate_id: int,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete the task_gates row ``gate_id`` belonging to the task at
    ``task_order_num``.

    Raises ValueError on missing brief/plan/task, or when ``gate_id`` does not
    belong to that task.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, task_order_num
        )
        cur = con.execute(
            "DELETE FROM task_gates WHERE id = ? AND task_id = ?",
            (gate_id, task_id),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"gate id={gate_id} not found on task order_num={task_order_num} "
                f"in plan for brief '{brief_name}'"
            )
        con.commit()
        return {
            "status": "applied",
            "action": "deleted",
            "brief_name": brief_name,
            "task_order_num": task_order_num,
            "gate_id": gate_id,
        }
    finally:
        con.close()


def update_gate(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    gate_id: int,
    *,
    verification_type: str | None = None,
    evidence_type: str | None = None,
    evidence_shape: str | None = None,
    artifact_path: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Update fields of an existing task_gates row IN PLACE, preserving its id.

    Partial update, mirroring :func:`gaia.briefs.store.update_ac`: only the
    keyword fields passed as non-None are written; the rest of the row is left
    untouched. ``task_gates.id`` is the resolution key, never rewritten, and
    ``.status`` is deliberately not a parameter here -- gate status transitions
    stay the exclusive job of :func:`set_gate_status` (and, through it, the
    verifier-only closure path), never a side effect of a content edit.

    Persists the edited fields AS GIVEN: like :func:`add_gate_to_task`, this
    writer does NOT invoke ``gaia.state.gate_validation.validate_gate`` --
    structural validation is a separate, independently-invokable pure
    function, out of scope for what a writer blocks at persist time. Holding
    edit to that same (lack of) check is deliberate: an edited gate must never
    be rejectable by a rule that ``add`` would not also have enforced.

    Raises ValueError when no field is given, on missing brief/plan/task, or
    when ``gate_id`` does not belong to that task.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    updates: dict[str, Any] = {}
    if verification_type is not None:
        updates["verification_type"] = verification_type
    if evidence_type is not None:
        updates["evidence_type"] = evidence_type
    if evidence_shape is not None:
        updates["evidence_shape"] = evidence_shape
    if artifact_path is not None:
        updates["artifact_path"] = artifact_path

    if not updates:
        raise ValueError("at least one field must be specified for update")

    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, task_order_num
        )
        existing = con.execute(
            "SELECT id FROM task_gates WHERE id = ? AND task_id = ?",
            (gate_id, task_id),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"gate id={gate_id} not found on task order_num={task_order_num} "
                f"in plan for brief '{brief_name}'"
            )

        set_clauses = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [gate_id]
        try:
            con.execute(
                f"UPDATE task_gates SET {set_clauses} WHERE id = ?",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"could not update gate id={gate_id}: {exc}"
            ) from exc
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "task_order_num": task_order_num,
            "gate_id": gate_id,
            "fields": list(updates.keys()),
        }
    finally:
        con.close()


# Key under which :func:`set_gate_status` reports what the recorded verdict
# implied for the task itself. Always present, so a caller distinguishes "the
# derivation ran and did nothing" from "the derivation did not run" without
# testing for a missing key.
DERIVED_CLOSURE_RESULT_KEY = "derived_closure"

# Reported in place of the decided action when the derived transition itself
# failed. Not a member of ``DerivedClosureAction``: the derivation reached a
# decision, and what failed is the write that followed it.
DERIVED_CLOSURE_ERROR_ACTION = "error"


def _read_task_status(con: sqlite3.Connection, task_id: int) -> str | None:
    """Return one task's current ``status`` on an ALREADY OPEN connection.

    Companion to :func:`_read_task_gate_rows` and
    :func:`_read_task_binding_rows`: the third impure input the derived-closure
    decision needs, read the same way and for the same reason -- so a caller
    mid-transaction can ask without opening a second connection. ``task_id`` is
    the persisted ``tasks.id``, not an ``order_num``. None means no such row.
    """
    row = con.execute(
        "SELECT status FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return row["status"] or "pending"


def _apply_derived_task_closure(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    *,
    gate_rows: list[dict],
    binding_rows: list[dict],
    task_status: str | None,
    db_path: Path | None,
) -> dict:
    """Perform whatever a freshly recorded gate verdict implies for the task.

    The impure half of ``gaia.state.task_closure_derivation``: it resolves the
    caller's dispatch identity, hands the pure decision its three inputs, and --
    when an act follows -- performs it through :func:`set_task_status`.

    THROUGH THE SINGLE WRITER, WITH NO PRIVILEGE. The transition goes through the
    same function and therefore the same guard a manual close goes through; no
    override travels with it. A derived close satisfies that guard the way it is
    meant to be satisfied -- by evidence -- because the only cell that produces
    one is the cell where every gate passed, which is the condition's first
    disjunct. A derived reopen is exempt from the condition for the same reason a
    manual reopen is: it withdraws an assertion rather than making one.

    BEST EFFORT, AND REPORTED RATHER THAN RAISED. By the time this runs the gate
    verdict is committed, and the verdict is what the caller asked to record. So
    a failure is returned in the result (action ``DERIVED_CLOSURE_ERROR_ACTION``,
    with the exception text) instead of propagating: raising here would report a
    recorded verdict as a failed command and invite the operator to re-issue a
    write that already landed. Nothing is lost by not raising -- the evidence is
    on the gates, so the task remains closable by its own approving verdict at
    any later moment.

    THE GUARANTEE IS ABOUT THE MOMENT, NOT ABOUT ONE STATEMENT, so the guard
    spans the WHOLE body: the deferred imports, the identity resolution, the pure
    decision, and the transition alike. Everything here runs after a commit the
    caller already owns, and the reason none of it may propagate is that
    position -- not which of the steps happens to be fallible today. Guarding
    only the write would leave the promise wider than the protection: the pure
    half documents "never raises" and an ``ImportError`` needs no impurity at
    all, so a reader trusting the docstring would be trusting coverage the
    ``try`` did not give. ``outcome`` is therefore built incrementally and starts
    at the error action, so a failure at any point still returns the key set the
    success path returns.

    Note the deliberate asymmetry with ``write_task_close_override_event``, which
    is NOT best-effort and precedes its mutation: that record is the
    JUSTIFICATION for a state change and must not be missing from one that
    happened. This is a CONSEQUENCE of a state change that already happened, and
    a missing consequence leaves the substrate consistent, merely un-advanced.
    """
    outcome: dict = {
        "action": DERIVED_CLOSURE_ERROR_ACTION,
        "why": "the derivation did not reach a decision",
        "gate_count": None,
        "verdict_approving": None,
    }

    try:
        from gaia.state.task_closure import derive_gate_verdict
        from gaia.state.task_closure_derivation import decide_derived_closure
        from gaia.state.task_closure_event import resolve_actor
        from gaia.state.task_closure_identity import (
            classify_producer_standing,
            producer_agent_names,
        )

        caller_agent = resolve_actor(os.environ.get("GAIA_DISPATCH_AGENT"))
        verdict = derive_gate_verdict(gate_rows)
        outcome["gate_count"] = verdict.gate_count
        outcome["verdict_approving"] = verdict.approving

        decision = decide_derived_closure(
            verdict=verdict,
            task_status=task_status,
            standing=classify_producer_standing(
                caller_agent=caller_agent,
                producer_agents=producer_agent_names(binding_rows),
            ),
        )
        outcome["action"] = decision.action.value
        outcome["why"] = decision.why
        if decision.target_status is None:
            return outcome

        transition = set_task_status(
            workspace,
            brief_name,
            task_order_num,
            decision.target_status,
            db_path=db_path,
        )
    except Exception as exc:
        # Whatever the derivation had already decided is what it intended; a
        # failure landing before any decision has only the derivation itself to
        # name, and says so rather than reporting an intent it never formed.
        decided = outcome["action"]
        outcome["intended_action"] = (
            "derivation" if decided == DERIVED_CLOSURE_ERROR_ACTION else decided
        )
        outcome["action"] = DERIVED_CLOSURE_ERROR_ACTION
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome

    outcome["task_action"] = transition.get("action")
    outcome["old_status"] = transition.get("old_status")
    outcome["new_status"] = transition.get("new_status")
    return outcome


def set_gate_status(
    workspace: str,
    brief_name: str,
    task_order_num: int,
    gate_id: int,
    status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Set the ``status`` of the task_gates row ``gate_id`` on the task at
    ``task_order_num``, then apply whatever that verdict implies for the task.

    Write surface for `gaia task gate set-status` (harness B3/T3): the ONLY
    way, prior to this, to move task_gates.status off its INSERT-time value
    was to re-run add_gate_to_task. ``status`` is enforced against
    ``gaia.state.VALID_GATE_STATUSES`` ('pending' / 'pass' / 'fail') by
    ``_assert_valid_gate_status`` -- a code-level guard that raises a clean
    ValueError ahead of the DB CHECK the column also carries as of v36 (see
    gaia.state.VALID_GATE_STATUSES docstring).

    THE VERDICT CARRIES THE TASK WITH IT. Once the gate row is committed, the
    task's own status either still follows from its gates or no longer does, and
    :func:`_apply_derived_task_closure` applies the difference: a pending task
    whose every gate now passes is closed with no manual step, and a closed task
    whose gates no longer all pass is reopened. Both go through
    :func:`set_task_status`, the single writer, with the same guard in front of
    them -- there is no second writer and no privileged path. What was decided
    is reported under :data:`DERIVED_CLOSURE_RESULT_KEY`, always present, so the
    caller can say what happened to the task as well as to the gate.

    THIS SEAM CARRIES THE TASK'S IDENTITY IN ITS OWN ARGUMENTS, which is why the
    derivation hangs here rather than off the verifier's own turn-closing step:
    the task is named by ``brief_name`` + ``task_order_num`` before any lookup,
    and the sibling gates are readable on the connection already open.

    WHY THE DERIVATION IS PLACED AFTER THE COMMIT. The verdict is what the caller
    asked to record, and it must survive whatever the derivation does or fails to
    do. The gate write commits first and the derivation runs against a closed
    transaction, so no outcome of it can roll back, alter, or withhold the verdict
    that has already landed.

    Raises ValueError on missing brief/plan/task, when ``gate_id`` does not
    belong to that task, or when ``status`` is out of vocabulary. A failure of
    the derivation -- anywhere in it, from resolving who is calling to the
    transition it implies -- does NOT raise, it is reported under
    :data:`DERIVED_CLOSURE_RESULT_KEY`; see :func:`_apply_derived_task_closure`.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")
    _assert_valid_gate_status(status)

    con = _connect(db_path)
    try:
        task_id = _resolve_task_id_by_order(
            con, workspace, brief_name, task_order_num
        )
        gate_row = con.execute(
            "SELECT status FROM task_gates WHERE id = ? AND task_id = ?",
            (gate_id, task_id),
        ).fetchone()
        if gate_row is None:
            raise ValueError(
                f"gate id={gate_id} not found on task order_num={task_order_num} "
                f"in plan for brief '{brief_name}'"
            )
        old_status = gate_row["status"]

        con.execute(
            "UPDATE task_gates SET status = ? WHERE id = ? AND task_id = ?",
            (status, gate_id, task_id),
        )
        con.commit()

        result = {
            "status": "applied",
            "action": "status_updated",
            "brief_name": brief_name,
            "task_order_num": task_order_num,
            "gate_id": gate_id,
            "old_status": old_status,
            "new_status": status,
        }
        # Read the derivation's three inputs while the connection is open, so the
        # transition below opens the only other one -- rather than nesting a
        # writer's connection inside this function's still-open read.
        gate_rows = _read_task_gate_rows(con, task_id)
        binding_rows = _read_task_binding_rows(con, task_id)
        task_status = _read_task_status(con, task_id)
    finally:
        con.close()

    result[DERIVED_CLOSURE_RESULT_KEY] = _apply_derived_task_closure(
        workspace,
        brief_name,
        task_order_num,
        gate_rows=gate_rows,
        binding_rows=binding_rows,
        task_status=task_status,
        db_path=db_path,
    )
    return result


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


def prune_empty_workspaces(
    *,
    apply: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Identify -- and optionally delete -- PHANTOM workspaces: rows in
    ``workspaces`` that carry ZERO ``projects`` rows (of any status).

    Phantom workspaces are debris from historical scans run with different
    ``--workspace`` values (e.g. a scan keyed on a container folder that later
    became a ``group_name`` under a parent workspace). A workspace whose only
    content is an empty shell adds noise to every context read.

    Governance (never delete curated content blindly)
    --------------------------------------------------
    A zero-project workspace is deleted ONLY when it also holds NO curated
    collateral: no live ``memory`` (``deleted_at IS NULL``), no
    ``project_context_contracts`` rows, and no ``briefs`` rows. When ANY of
    those is present the workspace is HELD -- reported, never deleted -- because
    the ``workspaces`` FK cascades ON DELETE and would destroy that curated
    content. Such a workspace needs a human decision (relocate the memory/PCC
    to its canonical workspace first, via ``gaia context move-memory`` /
    ``move-contracts``), which is out of scope for an automatic prune.

    This is a READ on ``apply=False`` (the default): it computes and returns the
    plan without mutating. On ``apply=True`` it deletes the confirmed-empty
    rows inside one transaction (CASCADE removes any stray non-curated children)
    and returns what it did.

    Returns a dict::

        {
          "mode": "apply" | "dry-run",
          "pruned":  [<workspace>, ...],          # deleted (apply) / would-delete
          "held":    [{"workspace", "projects", "memory", "pcc", "briefs",
                       "reason"}, ...],            # zero-project but curated
          "scanned": <int>,                        # workspaces examined
        }
    """
    con = _connect(db_path)
    try:
        ws_names = [
            r["name"]
            for r in con.execute("SELECT name FROM workspaces ORDER BY name").fetchall()
        ]

        prunable: list[str] = []
        held: list[dict] = []
        for ws in ws_names:
            proj = con.execute(
                "SELECT COUNT(*) FROM projects WHERE workspace = ?", (ws,)
            ).fetchone()[0]
            if proj != 0:
                continue  # not a phantom -- it has projects
            mem = con.execute(
                "SELECT COUNT(*) FROM memory WHERE workspace = ? AND deleted_at IS NULL",
                (ws,),
            ).fetchone()[0]
            pcc = con.execute(
                "SELECT COUNT(*) FROM project_context_contracts WHERE workspace = ?",
                (ws,),
            ).fetchone()[0]
            briefs = con.execute(
                "SELECT COUNT(*) FROM briefs WHERE workspace = ?", (ws,)
            ).fetchone()[0]

            if mem or pcc or briefs:
                held.append({
                    "workspace": ws,
                    "projects": 0,
                    "memory": mem,
                    "pcc": pcc,
                    "briefs": briefs,
                    "reason": (
                        f"workspace {ws!r} has 0 projects but holds curated "
                        f"collateral (memory={mem}, pcc={pcc}, briefs={briefs}); "
                        f"NOT pruned -- relocate its curated content first "
                        f"(gaia context move-memory / move-contracts) or wipe "
                        f"it explicitly."
                    ),
                })
            else:
                prunable.append(ws)

        if apply and prunable:
            con.execute("BEGIN")
            try:
                for ws in prunable:
                    con.execute("DELETE FROM workspaces WHERE name = ?", (ws,))
                con.commit()
            except Exception:
                con.rollback()
                raise

        return {
            "mode": "apply" if apply else "dry-run",
            "pruned": prunable,
            "held": held,
            "scanned": len(ws_names),
        }
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


def _plan_grant_deadline(created_at: str) -> str | None:
    """Return when a plan-first grant born at ``created_at`` lapses, None if unreadable."""
    from datetime import datetime, timedelta, timezone

    try:
        born = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (born + timedelta(minutes=PLAN_COMMAND_SET_TTL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")


# The sweep's rule, written once: the UPDATE that expires rows and the COUNT that
# previews it bind these same two parameters, so a preview cannot disagree with
# the sweep it previews. The per-row question -- may THIS grant authorize now --
# is _plan_grant_is_live; the two agree because both measure from
# PLAN_COMMAND_SET_TTL_MINUTES.
_LAPSED_GRANT_PREDICATE = (
    "status = 'PENDING' "
    "AND ((expires_at IS NOT NULL AND expires_at < ?) "
    "     OR (expires_at IS NULL AND source = 'plan-first' AND created_at < ?))"
)


def _lapsed_grant_params() -> tuple[str, str]:
    """Bind ``_LAPSED_GRANT_PREDICATE``: now, and the TTL-less plan-first cutoff."""
    now = _now_iso()
    return now, _plan_grant_born_before(now)


def _plan_grant_born_before(now_iso: str) -> str:
    """Return the created_at below which a TTL-less plan-first grant has lapsed."""
    from datetime import datetime, timedelta, timezone

    try:
        now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        now = datetime.now(timezone.utc)
    return (now - timedelta(minutes=PLAN_COMMAND_SET_TTL_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plan_grant_is_live(grant: dict, now_iso: str) -> bool:
    """Return whether a plan-first grant row is still inside its authority window.

    A row written before grants carried a TTL has expires_at NULL, and its deadline
    is DERIVED from created_at rather than backfilled. Deriving gives an already-
    issued grant exactly the window a new one gets, measured from when it really
    began: a set approved minutes ago stays usable, one approved yesterday is
    already lapsed. It also rewrites no row, so the user's recorded decision and
    the grant's own history stay as they were.
    """
    deadline = grant.get("expires_at") or _plan_grant_deadline(grant.get("created_at") or "")
    # No readable stamp means no window can be measured; refuse the grant rather
    # than let an unmeasurable one authorize without bound.
    return bool(deadline) and deadline > now_iso


def insert_plan_command_set(
    approval_id: str,
    command_set: list[dict],
    *,
    request_fingerprint: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    db_path: Path | None = None,
    con: sqlite3.Connection | None = None,
) -> dict:
    """Persist an approved plan-first COMMAND_SET, failing closed on any error."""
    connection = con if con is not None else _connect(db_path)
    owned = con is None
    created_at = _now_iso()
    try:
        if owned:
            connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO approval_grants
                (approval_id, agent_id, session_id, command_set_json, scope,
                 created_at, expires_at, status, consumed_indexes_json, confirmed,
                 request_fingerprint, next_index, source)
            VALUES (?, ?, ?, ?, 'COMMAND_SET', ?, ?, 'PENDING', '[]', 1, ?, 0,
                    'plan-first')
            """,
            (
                approval_id,
                agent_id,
                session_id,
                _json.dumps(command_set),
                created_at,
                _plan_grant_deadline(created_at),
                request_fingerprint,
            ),
        )
        if owned:
            connection.commit()
        return _applied()
    except Exception as exc:
        if owned:
            connection.rollback()
        return {"status": "error", "reason": str(exc)}
    finally:
        if owned:
            connection.close()


def reserve_plan_command(
    command: str,
    *,
    session_id: str,
    tool_use_id: str,
    db_path: Path | None = None,
) -> dict | None:
    """Reserve the exact next command for one correlated Bash tool call."""
    if not session_id or not tool_use_id:
        return None
    from gaia.approvals.command_set import command_fingerprint

    con = _connect(db_path)
    now_iso = _now_iso()
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT * FROM approval_grants WHERE scope='COMMAND_SET' "
            "AND source='plan-first' AND status='PENDING' ORDER BY created_at, approval_id"
        ).fetchall()
        for row in rows:
            grant = dict(row)
            # The deadline is enforced HERE, at the point that authorizes, and not
            # left to cleanup_expired_db_grants alone: that sweep runs
            # opportunistically, so a lapsed grant would keep reserving commands in
            # every gap between two sweeps.
            if not _plan_grant_is_live(grant, now_iso):
                continue
            items = _json.loads(grant["command_set_json"])
            index = int(grant.get("next_index") or 0)
            if index >= len(items):
                continue
            item = items[index]
            if item.get("command") != command or item.get("fingerprint") != command_fingerprint(command):
                continue
            if grant.get("reservation_tool_use_id"):
                con.rollback()
                return None
            changed = con.execute(
                "UPDATE approval_grants SET reservation_index=?, reservation_session_id=?, "
                "reservation_tool_use_id=?, reservation_at=? WHERE approval_id=? "
                "AND reservation_tool_use_id IS NULL AND next_index=?",
                (index, session_id, tool_use_id, _now_iso(), grant["approval_id"], index),
            ).rowcount
            if changed != 1:
                con.rollback()
                return None
            con.commit()
            return {"approval_id": grant["approval_id"], "index": index}
        con.rollback()
        return None
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def pending_plan_command_exists(command: str, *, db_path: Path | None = None) -> bool:
    """Return whether ``command`` is the exact next item of an active request-set."""
    from gaia.approvals.command_set import command_fingerprint

    con = _connect(db_path)
    now_iso = _now_iso()
    try:
        try:
            rows = con.execute(
                "SELECT command_set_json, next_index, created_at, expires_at FROM approval_grants "
                "WHERE scope='COMMAND_SET' AND source='plan-first' AND status='PENDING'"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc):
                return False
            raise
        for row in rows:
            grant = {"created_at": row[2], "expires_at": row[3]}
            if not _plan_grant_is_live(grant, now_iso):
                continue
            items = _json.loads(row[0])
            index = int(row[1] or 0)
            if index < len(items):
                item = items[index]
                if item.get("command") == command and item.get("fingerprint") == command_fingerprint(command):
                    return True
        return False
    finally:
        con.close()


def settle_plan_command(
    approval_id: str,
    *,
    session_id: str,
    tool_use_id: str,
    success: bool,
    failure_reason: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Commit or release an exact reservation; a failure leaves the remainder."""
    if not session_id or not tool_use_id:
        return False
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            con.rollback()
            return False
        grant = dict(row)
        if (grant.get("reservation_session_id"), grant.get("reservation_tool_use_id")) != (session_id, tool_use_id):
            con.rollback()
            return False
        index = int(grant["reservation_index"])
        if success:
            items = _json.loads(grant["command_set_json"])
            consumed = _json.loads(grant.get("consumed_indexes_json") or "[]")
            consumed.append(index)
            next_index = index + 1
            status = "CONSUMED" if next_index == len(items) else "PENDING"
            con.execute(
                "UPDATE approval_grants SET next_index=?, consumed_indexes_json=?, status=?, "
                "consumed_at=CASE WHEN ?='CONSUMED' THEN ? ELSE consumed_at END, "
                "reservation_index=NULL, reservation_session_id=NULL, "
                "reservation_tool_use_id=NULL, reservation_at=NULL WHERE approval_id=?",
                (next_index, _json.dumps(consumed), status, status, _now_iso(), approval_id),
            )
        else:
            con.execute(
                "UPDATE approval_grants SET status='FAILED', failed_index=?, failure_reason=?, "
                "reservation_index=NULL, reservation_session_id=NULL, "
                "reservation_tool_use_id=NULL, reservation_at=NULL WHERE approval_id=?",
                (index, failure_reason, approval_id),
            )
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
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
    """Mark EXPIRED any PENDING approval_grants rows whose deadline has passed.

    Idempotent: rows already in a terminal status (CONSUMED, REVOKED, EXPIRED)
    are not touched.

    A row with expires_at=NULL carries no TTL and is skipped, EXCEPT on the
    plan-first source, where NULL means the row predates plan-first grants
    carrying a TTL at all rather than a deliberate no-expiry grant. Those rows
    are swept on the deadline derived from created_at, so the sweep reaches the
    already-issued keys instead of leaving them PENDING forever. The exception is
    anchored to source='plan-first' so no other scope's no-TTL semantics change.

    Args:
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows marked EXPIRED.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                f"UPDATE approval_grants SET status = 'EXPIRED' WHERE {_LAPSED_GRANT_PREDICATE}",
                _lapsed_grant_params(),
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


def count_expired_db_grants(
    *,
    db_path: Path | None = None,
) -> int:
    """Count the rows :func:`cleanup_expired_db_grants` would mark EXPIRED."""
    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT COUNT(*) FROM approval_grants WHERE {_LAPSED_GRANT_PREDICATE}",
            _lapsed_grant_params(),
        ).fetchone()
        return int(row[0])
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
# Guard model (T8 -- brief contract-as-managed-data): INVERTED from curator-only
# to finalize-by-any-seeded-agent. Formerly, handoff rows were written by the
# SubagentStop hook (curator context) and every subagent dispatch was FORBIDDEN.
# Under contract-as-managed-data the terminal row is finalized BY the agent
# itself via `gaia contract finalize`, so any agent in the fleet seed (every
# agent declared under agents/ with `contract_handoff_writer: true`) is
# authorized. Only an identity ABSENT from the seed (a rogue/unseeded dispatch)
# is rejected. The fleet seed and its loader live in gaia.state.permissions
# (`handoff_writer_fleet` / `is_handoff_writer`).
# ---------------------------------------------------------------------------

class HandoffWriteForbidden(PermissionError):
    """Raised when an unseeded dispatch identity attempts to write a handoff row."""


def _assert_dispatch_can_write_handoff() -> None:
    """Allow handoff finalize from any SEEDED fleet agent; block unseeded identities.

    Contract (INVERTED from the prior curator-only gate):
    * GAIA_DISPATCH_AGENT unset / empty -> CLI / human / hook context, allowed
      (the harness-agnostic CLI path and the SubagentStop hook both run without
      a dispatch identity set).
    * Set to a seeded fleet identity (any agent under agents/ carrying
      `contract_handoff_writer: true`, plus curator aliases) -> allowed. This is
      the inversion: a subagent finalizing its own handoff is now permitted.
    * Set to an identity NOT in the fleet seed -> raises HandoffWriteForbidden
      (a rogue / unseeded dispatch -- the write AC-7 keeps blocked).
    """
    from gaia.state.permissions import handoff_writer_fleet, is_handoff_writer

    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    agent = raw.strip()
    if not agent:
        return
    if is_handoff_writer(agent):
        return
    raise HandoffWriteForbidden(
        f"agent_contract_handoffs writes are forbidden from '{agent}': it is not "
        f"a seeded fleet agent (GAIA_DISPATCH_AGENT={raw!r}). Only agents declared "
        f"under agents/ with `contract_handoff_writer: true` may finalize a "
        f"handoff row. Seeded fleet: {sorted(handoff_writer_fleet())}."
    )


def insert_agent_contract_handoff(
    agent_id: str,
    workspace: str,
    agent_state: str,
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
        agent_id:         Agent identity string, "a" + 16+ hex (see
                          gaia.contract.validator.AGENT_ID_PATTERN_TEXT).
        workspace:        Workspace name (FK -> workspaces.name).
        agent_state:      Resolved agent_state (turn status) from the contract
                          envelope; maps to the agent_contract_handoffs.agent_state
                          column.
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

    def _work() -> int:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE: write-lock-first, so this SELECT-then-INSERT body
            # (_ensure_workspace_row's SELECT, then the INSERT) cannot deadlock
            # against a concurrent writer upgrading a SHARED lock to RESERVED.
            con.execute("BEGIN IMMEDIATE")
            try:
                _ensure_workspace_row(con, workspace)
                cur = con.execute(
                    """
                    -- v37: the persisted column is `agent_state` (renamed from
                    -- task_status; born-at-dispatch redesign, plan 34). The
                    -- Python parameter is now also `agent_state` (plan 34 task 4
                    -- completed the envelope-field rename plan_status ->
                    -- agent_state); it maps directly to the agent_state column.
                    INSERT INTO agent_contract_handoffs
                        (agent_id, session_id, workspace, brief_id,
                         agent_state, raw_handoff_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agent_id,
                        session_id,
                        workspace,
                        brief_id,
                        agent_state,
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

    return _retry_on_locked(_work)


# ---------------------------------------------------------------------------
# Public API: finalize_agent_contract_handoff (v28 / T7 -- sole idempotent
# writer of the terminal agent_contract_handoffs row)
# ---------------------------------------------------------------------------
#
# Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli,
# task T7 (M3: "finalize is the sole idempotent writer").
#
# THIS is the primitive T8 (write-guard + fleet-seed permissions) and T9
# (SubagentStop hook conditional backstop) build on -- both are expected to
# call this same function rather than reinvent the UPSERT, so document the
# contract precisely instead of letting them reverse-engineer it:
#
#   Idempotency key: `contract_id` -- the CLI-minted draft/contract id from
#   `gaia.contract.drafts.mint_draft_id` (shape `"{agent_id}.{token}"`; NEVER
#   derived from CLAUDE_SESSION_ID or any harness value -- decisions #1/#3).
#   `agent_contract_handoffs.contract_id` carries a UNIQUE index (schema v28,
#   scripts/migrations/v27_to_v28.sql). v37 (born-at-dispatch, plan 34 task 5)
#   this function CONVERGES rather than only DO NOTHING:
#
#       INSERT INTO agent_contract_handoffs (...) VALUES (...)
#       ON CONFLICT(contract_id) DO UPDATE SET <terminal fields>
#       WHERE agent_contract_handoffs.agent_state NOT IN <TERMINAL_PLAN_STATUSES>
#       RETURNING id
#
#   Two shapes of row can pre-exist the finalize: (a) NONE (legacy / no
#   born-at-dispatch) -- the INSERT lands the terminal row directly; (b) a row
#   already exists for this contract_id, either the NASCENT
#   agent_state='DISPATCHED' birth row (see insert_dispatched_handoff) or a
#   PRIOR non-terminal verdict from an earlier finalize call on the SAME draft
#   (IN_PROGRESS, APPROVAL_REQUEST, BLOCKED, NEEDS_INPUT, NEEDS_VERIFICATION --
#   see gaia.state.TERMINAL_PLAN_STATUSES for which values are excluded from
#   this set) -- the DO UPDATE CONVERGES that single row to the newest verdict,
#   so there is still exactly ONE row per turn and no duplicate INSERT. The
#   born-at-dispatch binding columns (plan_task_id, plan_id,
#   parent_handoff_id, kind) and the birth created_at are NOT in the SET list, so
#   convergence preserves them.
#
#   BUG FIXED (was: `WHERE agent_state = 'DISPATCHED'`): that guard treated ANY
#   non-DISPATCHED state as write-blocking, so a row that legitimately
#   auto-finalized as IN_PROGRESS (a paused checkpoint, not a verdict) could
#   never converge to its true COMPLETE outcome on a later finalize call for
#   the SAME contract_id (e.g. after a resume) -- the row lied forever about
#   work that had genuinely completed. The guard now blocks convergence ONLY
#   when the row's CURRENT state is already in TERMINAL_PLAN_STATUSES
#   (COMPLETE) -- the one verdict that must never be overwritten or regressed.
#
#   The `WHERE agent_state NOT IN <TERMINAL_PLAN_STATUSES>` guard is what
#   preserves the write-once-for-COMPLETE + exactly-once-per-verdict invariant
#   under a race: the first writer to COMMIT for a contract_id wins (it
#   inserts, or converges the existing non-terminal row); every subsequent
#   write for the SAME contract_id (a retried `gaia contract finalize`, or --
#   T9 -- a racing SubagentStop hook backstop) is itself free to converge
#   again UNLESS the row is already COMPLETE, in which case the WHERE is
#   false, the UPDATE is skipped, RETURNING yields no row, and the call is a
#   genuine no-op: no duplicate row, no exception, no mutation of the terminal
#   row. finalize+hook-backstop therefore converge to EXACTLY ONE row
#   (never-lost because SOME writer always succeeds first; write-once-COMPLETE
#   because a COMPLETE row is never edited in place). Whichever writer loses
#   the race against an already-COMPLETE row accepts the winner's verdict as-is.
#
#   `contract_id` may be omitted (None/empty) by a caller that has no draft
#   concept (legacy/back-compat path) -- SQLite's UNIQUE index permits any
#   number of NULL values, so such rows never collide with each other or with
#   a real contract_id and simply are not deduplicated (there is nothing to
#   deduplicate against). Only a NON-EMPTY contract_id participates in the
#   idempotent-UPSERT guarantee.
#
# Public signature (stable for T8/T9):
#
#   finalize_agent_contract_handoff(
#       contract_id: str,
#       agent_id: str,
#       workspace: str,
#       agent_state: str,
#       raw_handoff_json: str,
#       *,
#       session_id: str | None = None,
#       brief_id: int | None = None,
#       cut_reason: str | None = None,
#       db_path: Path | None = None,
#   ) -> dict
#
#   v39 adds `cut_reason`, and with it the property that only THIS function can
#   produce a clean closure: the born row carries 'never_finalized' from birth
#   (insert_dispatched_handoff), the DO UPDATE writes cut_reason last-write-wins,
#   and the default None is what clears it. An agent finalizing its own contract
#   passes nothing; the closure paths (reap / backstop capture / truncation
#   salvage) pass the reason they are closing under, so the row stays marked and
#   `WHERE cut_reason IS NOT NULL` finds it without parsing raw_handoff_json.
#
#   Returns {"status": "applied", "created": bool, "handoff_id": int | None,
#   "contract_id": str}. `created` is True only for the call that actually
#   inserted the row; every later call for the same contract_id returns
#   `created=False` with the SAME `handoff_id` -- this is how a caller (the
#   CLI, T9's backstop) tells "I just wrote this" apart from "this was
#   already finalized" without a second round trip.
#
#   Same permission gate as insert_agent_contract_handoff
#   (`_assert_dispatch_can_write_handoff`) -- T8 owns evolving that gate to
#   the fleet-seed model; T7 deliberately reuses it unchanged.
# ---------------------------------------------------------------------------

def finalize_agent_contract_handoff(
    contract_id: str,
    agent_id: str,
    workspace: str,
    agent_state: str,
    raw_handoff_json: str,
    *,
    session_id: str | None = None,
    plan_task_id: int | None = None,
    brief_id: int | None = None,
    cut_reason: str | None = None,
    db_path: "Path | None" = None,
) -> dict:
    """Idempotently write the terminal agent_contract_handoffs row.

    See the module comment immediately above for the full idempotency-key
    contract. In short: INSERT ... ON CONFLICT(contract_id) DO NOTHING, so a
    second call for the same ``contract_id`` never creates a second row --
    it is a genuine no-op that returns the already-existing row's id.

    Args:
        contract_id:      The CLI-minted draft/contract id (the idempotency
                          key). Required (raises ValueError if empty).
        agent_id:         Agent identity string, "a" + 16+ hex (see
                          gaia.contract.validator.AGENT_ID_PATTERN_TEXT).
        workspace:        Workspace name (FK -> workspaces.name).
        agent_state:      Resolved agent_state (turn status) from the contract
                          envelope; maps to the agent_contract_handoffs.agent_state
                          column.
        raw_handoff_json: Full contract envelope serialized as JSON string.
        session_id:       Session identifier to attribute the row to (optional).
                          The core never READS a harness variable for this; the
                          value is always SUPPLIED by the caller -- the hook
                          adapter from the event payload, or the CLI from its
                          explicit ``--session-id`` flag.
        plan_task_id:     tasks.id this turn executes (optional), so a finalized
                          contract is attributable to its plan task by query.
                          Supplied the same way as session_id -- by the caller,
                          never read from the environment. A None here NEVER
                          clears a binding already stamped on the row at birth
                          (see the COALESCE in the UPSERT below).
        brief_id:         briefs.id FK (optional -- EXTENSION_POINT).
        cut_reason:       v39 structural cut marker (gaia.state.CUT_REASONS).
                          DEFAULT None, and that default is the point: this
                          function is the ONLY writer that can produce a CLEAN
                          closure, and it does so precisely by leaving this
                          argument alone. An agent's own `gaia contract
                          finalize` passes nothing here and the row's birth
                          stamp is cleared; a CLOSURE path (reap, backstop
                          capture, truncation salvage) passes the reason it is
                          closing under and the row stays marked. Unlike
                          session_id/plan_task_id this is NOT COALESCEd -- the
                          writer that lands the verdict owns the mark, so a
                          clean finalize cannot leave a stale cut marker behind
                          and a cut cannot silently inherit cleanliness.
        db_path:          Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "created": True, "handoff_id": <new id>,
         "contract_id": contract_id} on the FIRST write for this contract_id.
        {"status": "applied", "created": False, "handoff_id": <existing id>,
         "contract_id": contract_id} on every SUBSEQUENT call (the no-op).

    Raises:
        ValueError: contract_id is empty/None.
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator
            (see _assert_dispatch_can_write_handoff; T8 owns the fleet-seed
            inversion of this gate).
    """
    if not contract_id:
        raise ValueError(
            "finalize_agent_contract_handoff requires a non-empty contract_id "
            "-- it is the idempotency key the UNIQUE constraint UPSERTs on."
        )

    # A placeholder session id is NO session id. The UPSERT below merges
    # session_id with COALESCE(excluded, existing) so "a caller that DOES carry
    # a value still wins" -- which turned an agent's invented
    # `gaia contract finalize --session-id unknown` (the subagent is never told
    # its harness session id) into a write that CLOBBERED the real attribution
    # stamped on the row at birth (measured: handoff 10915). Normalizing the
    # placeholder to None keeps the COALESCE semantics intact for every caller
    # that carries a real value while making the placeholder unable to erase one.
    if session_id is not None and session_id.strip().lower() in ("", "unknown"):
        session_id = None

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE (write-lock-first): acquire the RESERVED write lock
            # up front so two concurrent finalize cycles SERIALIZE at the lock
            # instead of both taking a SHARED read lock and then racing to
            # upgrade to RESERVED -- the classic SQLite two-writers deadlock that
            # a plain deferred BEGIN produces when the body is SELECT-then-INSERT
            # (here: _ensure_workspace_row's SELECT, then the INSERT below). With
            # IMMEDIATE, the second cycle waits (busy_timeout) for the first to
            # commit rather than deadlocking. The idempotency contract is
            # unchanged: contract_id UNIQUE + ON CONFLICT DO NOTHING still makes
            # the second writer for the SAME contract_id a genuine no-op.
            con.execute("BEGIN IMMEDIATE")
            try:
                _ensure_workspace_row(con, workspace)
                # SSOT for which agent_state values block further convergence
                # (gaia.state.TERMINAL_PLAN_STATUSES -- currently just COMPLETE).
                # Built as a NOT IN (...) placeholder list rather than a literal
                # so this stays correct if the terminal set ever grows.
                from gaia.state import TERMINAL_PLAN_STATUSES
                _terminal_placeholders = ", ".join("?" for _ in TERMINAL_PLAN_STATUSES)
                cur = con.execute(
                    f"""
                    -- v37: the persisted column is `agent_state` (renamed from
                    -- task_status). The Python parameter is now also `agent_state`
                    -- (plan 34 task 4 completed the envelope-field rename
                    -- plan_status -> agent_state); it maps directly to the
                    -- agent_state column here.
                    --
                    -- v37 born-at-dispatch (plan 34 task 5): finalize CONVERGES
                    -- onto an existing row instead of only DO NOTHING. A row may
                    -- already exist for this contract_id because it was BORN at
                    -- dispatch with agent_state='DISPATCHED' (see
                    -- insert_dispatched_handoff), or because an EARLIER finalize
                    -- call for the SAME draft already recorded a non-terminal
                    -- verdict (IN_PROGRESS, APPROVAL_REQUEST, BLOCKED,
                    -- NEEDS_INPUT, NEEDS_VERIFICATION) on a resume. The UPSERT
                    -- therefore:
                    --   * INSERTs a fresh row when none exists (the legacy /
                    --     no-born-at-dispatch path), OR
                    --   * CONVERGES an existing DISPATCHED-or-non-terminal row to
                    --     the newest verdict via DO UPDATE ... WHERE agent_state
                    --     NOT IN <TERMINAL_PLAN_STATUSES>, leaving the
                    --     born-at-dispatch binding columns (plan_task_id,
                    --     plan_id, parent_handoff_id, kind) AND the birth
                    --     created_at untouched -- one row per turn, no duplicate
                    --     INSERT.
                    -- BUG FIXED: this guard used to read
                    -- `WHERE agent_state = 'DISPATCHED'`, which treated ANY
                    -- non-DISPATCHED state as write-blocking -- so a row that
                    -- auto-finalized as IN_PROGRESS mid-loop could never
                    -- converge to its true COMPLETE outcome on a later finalize
                    -- for the SAME contract_id, and the DB lied forever about
                    -- work that had genuinely completed. The guard now blocks
                    -- convergence ONLY when the row's CURRENT state is already
                    -- in TERMINAL_PLAN_STATUSES (COMPLETE): a row that is
                    -- ALREADY COMPLETE (a prior finalize, or -- T9 -- a racing
                    -- hook backstop that got there first) does NOT match the
                    -- WHERE, so the second writer's UPDATE is skipped and
                    -- `RETURNING id` yields no row -> a genuine no-op. This is
                    -- what makes finalize+finalize and finalize+backstop
                    -- converge to EXACTLY ONE row under a race, in either
                    -- arrival order, while a COMPLETE row is still never edited
                    -- in place.
                    --
                    -- ATTRIBUTION COLUMNS ARE MERGED, NOT OVERWRITTEN: both
                    -- session_id and plan_task_id go through COALESCE(excluded,
                    -- existing) so a caller that does not carry a coordinate
                    -- (the CLI finalize of an unbound turn, a backstop capture)
                    -- can never CLEAR one that is already on the row -- most
                    -- importantly the plan_task_id/session_id a born-at-dispatch
                    -- row was stamped with. A caller that DOES carry a value
                    -- still wins, so a later, truer attribution converges
                    -- normally. The other columns keep last-write-wins: they
                    -- describe the turn's outcome, which the newest verdict owns.
                    --
                    -- cut_reason (v39) is deliberately in the LAST-WRITE-WINS
                    -- group, NOT the COALESCE group. Whoever lands the verdict
                    -- owns the mark: an agent's own clean finalize passes NULL
                    -- and thereby CLEARS the birth stamp (this is the only
                    -- statement in the system that can), while a closure path
                    -- passes its reason and the mark stands. COALESCEing it
                    -- would make a clean COMPLETE inherit the birth stamp
                    -- forever, so every healthy turn would read as a cut.
                    INSERT INTO agent_contract_handoffs
                        (contract_id, agent_id, session_id, workspace, brief_id,
                         plan_task_id, agent_state, cut_reason, raw_handoff_json,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contract_id) DO UPDATE SET
                        agent_id         = excluded.agent_id,
                        session_id       = COALESCE(excluded.session_id,
                                                    agent_contract_handoffs.session_id),
                        brief_id         = excluded.brief_id,
                        plan_task_id     = COALESCE(excluded.plan_task_id,
                                                    agent_contract_handoffs.plan_task_id),
                        agent_state      = excluded.agent_state,
                        cut_reason       = excluded.cut_reason,
                        raw_handoff_json = excluded.raw_handoff_json
                    WHERE agent_contract_handoffs.agent_state NOT IN ({_terminal_placeholders})
                    RETURNING id
                    """,
                    (
                        contract_id,
                        agent_id,
                        session_id,
                        workspace,
                        brief_id,
                        plan_task_id,
                        agent_state,
                        cut_reason,
                        raw_handoff_json,
                        _now_iso(),
                        *TERMINAL_PLAN_STATUSES,
                    ),
                )
                # RETURNING yields exactly one row iff THIS call inserted a fresh
                # row OR converged an existing non-terminal row (the WHERE
                # matched); it yields NO row when the UPSERT hit a conflict
                # against an already-COMPLETE row (WHERE false -> DO UPDATE
                # skipped) -- the idempotent no-op. This is deterministic and
                # avoids the ambiguous cur.rowcount of an
                # ON CONFLICT DO UPDATE ... WHERE upsert.
                returned = cur.fetchone()
                if returned is not None:
                    handoff_id = returned["id"]
                    con.commit()
                    # Automatic retention (mirrors episodes): occasionally prune
                    # handoffs older than the 90-day window. Runs on its own
                    # connection after commit, behind a 1/N gate, and swallows
                    # any failure so it can never mask this successful finalize.
                    _maybe_prune_handoffs(db_path=db_path)
                    return {
                        "status": "applied",
                        "created": True,
                        "handoff_id": handoff_id,
                        "contract_id": contract_id,
                    }
                # No returned row -- a terminal row for this contract_id already
                # existed (a prior finalize call, or -- T9 -- a racing hook backstop
                # that got there first). Look the winner's row up explicitly and
                # report the no-op with its id.
                existing = con.execute(
                    "SELECT id FROM agent_contract_handoffs WHERE contract_id = ?",
                    (contract_id,),
                ).fetchone()
                con.commit()
                return {
                    "status": "applied",
                    "created": False,
                    "handoff_id": existing["id"] if existing is not None else None,
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


# ---------------------------------------------------------------------------
# Public API: insert_dispatched_handoff (v37 -- born-at-dispatch nascent row)
# ---------------------------------------------------------------------------
#
# Brief: contrato-binding-y-verificacion-por-task-id, plan 34 task 5
# ("born-at-dispatch writer lifecycle").
#
# The FIRST half of the born-at-dispatch lifecycle: a handoff row is BORN at
# dispatch time -- before the agent produces any contract envelope -- carrying
# agent_state='DISPATCHED' and the four binding coordinates (plan_task_id,
# plan_id, parent_handoff_id, kind) that bind the turn to the plan/task it
# executes.
#
# WHO CLOSES THIS ROW. Two paths reach it, and which one runs is decided by
# whether the turn ADOPTED the identity this row was born under:
#
#   * ADOPTION -- the healthy path. Birth mints a REAL, adoptable identity
#     (hooks/modules/agents/dispatch_identity.py: an ``a``+hex agent_id and a
#     contract_id with the ``{agent_id}.{token}`` draft shape) and injects both
#     halves into the subagent's context. An agent that adopts them
#     (``gaia contract init --agent-id ... --draft-id ...``) finalizes under the
#     SAME contract_id this row carries, so finalize_agent_contract_handoff
#     CONVERGES this very row: one row per turn, binding preserved, nothing left
#     behind and nothing to supersede.
#   * NO ADOPTION -- the degraded path. A turn that never adopts (no identity
#     block reached it, or it minted a rival id anyway) writes its verdict to a
#     DIFFERENT row and leaves this one behind. Closing it then falls to the
#     SubagentStop hook (hooks/modules/agents/handoff_persister.py), the only
#     layer holding both identities: superseded (the turn did produce its own
#     terminal row; this scaffold points at it) or reaped (it never finalized;
#     an honest degraded non-COMPLETE verdict).
#
# A THIRD writer touches this row, before either closure: the incremental mirror
# (mirror_partial_contract_handoff, wired into ``gaia contract set/add/fill``)
# reflects the adopted draft's partial envelope onto it WHILE the turn runs,
# writing raw_handoff_json and nothing else -- never agent_state, never the
# binding. That is what leaves a turn cut before finalize with recoverable
# evidence on the row instead of only a birth envelope.
#
# 'DISPATCHED' is thus a transient ROW state, and a row still in it means the
# turn neither converged it by finalize nor reached the stop hook.
#
# Idempotent by construction (INSERT ... ON CONFLICT(contract_id) DO NOTHING):
# a re-dispatch / re-fire for the SAME contract_id never births a second row
# and never clobbers a row that has already converged to a terminal verdict.
# 'DISPATCHED' is a ROW state only -- never an envelope agent_state value (plan
# 34 F9); it exists solely to mark "born, not yet closed" so the stop hook can
# tell a turn that produced its own contract from one that never finalized, and
# close each accordingly without ever producing a false COMPLETE.
#
# Same permission gate as finalize (_assert_dispatch_can_write_handoff): only a
# seeded fleet agent (or the gate-less CLI/hook context) may birth a row.
# ---------------------------------------------------------------------------

def insert_dispatched_handoff(
    contract_id: str,
    agent_id: str,
    workspace: str,
    *,
    plan_task_id: int | None = None,
    plan_id: int | None = None,
    parent_handoff_id: int | None = None,
    kind: str | None = None,
    raw_handoff_json: str | None = None,
    session_id: str | None = None,
    brief_id: int | None = None,
    agent_name: str | None = None,
    dispatch_prompt_id: str | None = None,
    dispatch_tool_use_id: str | None = None,
    dispatch_description: str | None = None,
    dispatch_prompt: str | None = None,
    context_anchors: "list | None" = None,
    kernel_sections: "dict | None" = None,
    dispatch_project: str | None = None,
    db_path: "Path | None" = None,
) -> dict:
    """Birth a nascent ``agent_contract_handoffs`` row (agent_state='DISPATCHED').

    See the module comment above for the born-at-dispatch lifecycle. Called at
    dispatch time to stamp the binding before the agent runs; the row later
    CONVERGES to a terminal verdict via
    :func:`finalize_agent_contract_handoff`.

    Args:
        contract_id:       The CLI-minted draft/contract id (idempotency key).
                           Required (raises ValueError if empty).
        agent_id:          Agent identity string, "a" + 16+ hex (see
                           gaia.contract.validator.AGENT_ID_PATTERN_TEXT).
        workspace:         Workspace name (FK -> workspaces.name).
        plan_task_id:      NULLABLE FK -> tasks.id (the plan task this turn runs).
        plan_id:           NULLABLE FK -> plans.id.
        parent_handoff_id: NULLABLE FK -> agent_contract_handoffs.id.
        kind:              Dispatch label (task_execution / verifier / ...).
        raw_handoff_json:  Optional serialized placeholder envelope. Defaults to
                           a minimal born-at-dispatch marker (the column is NOT
                           NULL, but no contract envelope exists yet at birth).
        session_id:        CLAUDE_SESSION_ID at dispatch time (optional).
        brief_id:          briefs.id FK (optional).
        agent_name:        The dispatched agent's NAME (``gaia-system``,
                           ``gaia-verifier``). Recorded INSIDE the birth envelope,
                           never in the ``agent_id`` column, which holds the
                           minted handle the turn can adopt. The name is what
                           :func:`find_dispatched_row_by_agent_name` matches on so
                           a turn that never adopts its identity can still have
                           its born row found and closed. Ignored when an explicit
                           ``raw_handoff_json`` is supplied (the caller then owns
                           the envelope).
        dispatch_prompt_id:   Host prompt_id of the dispatching PreToolUse event
                              (v43). The primary correlation key
                              :func:`claim_dispatch_row` matches on.
        dispatch_tool_use_id: Host tool_use_id of the Task tool call (v43).
        dispatch_description: Task tool ``description`` parameter (v43); the
                              secondary claim correlation key.
        dispatch_prompt:      Task tool ``prompt`` parameter (v43) -- the turn's
                              goal, rendered into the dispatch kernel.
        context_anchors:      List of context anchors computed at dispatch
                              (v43); JSON-serialized into the column.
        kernel_sections:      Dict with the kernel render payload (role /
                              surface / can_read / can_write, v43);
                              JSON-serialized into the column.
        dispatch_project:     The project the dispatch ran from, as the display
                              string ``"name (/abs/path)"`` (v44), resolved at
                              birth against the workspace's project_identity
                              section; rendered into the kernel's ``project:``
                              field at claim time.
        db_path:           Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "created": True, "handoff_id": <new id>,
         "contract_id": contract_id} when this call birthed the row.
        {"status": "applied", "created": False, "handoff_id": <existing id>,
         "contract_id": contract_id} when a row for this contract_id already
        existed (idempotent no-op -- nascent re-birth or an already-converged row).

    Raises:
        ValueError: contract_id is empty/None.
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names an unseeded agent.
    """
    if not contract_id:
        raise ValueError(
            "insert_dispatched_handoff requires a non-empty contract_id -- it "
            "is the idempotency key the nascent row is born under."
        )

    _assert_dispatch_can_write_handoff()

    from gaia.state import CUT_REASON_NEVER_FINALIZED

    if raw_handoff_json is None:
        birth_envelope = {"agent_state": "DISPATCHED", "born_at_dispatch": True}
        if agent_name:
            birth_envelope[BIRTH_AGENT_NAME_KEY] = str(agent_name)
        raw_handoff_json = json.dumps(birth_envelope)

    context_anchors_json = (
        json.dumps(list(context_anchors)) if context_anchors else None
    )
    kernel_sections_json = (
        json.dumps(dict(kernel_sections)) if kernel_sections else None
    )

    def _work() -> dict:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE (write-lock-first): same rationale as finalize --
            # serialize concurrent births at the RESERVED lock instead of racing
            # a SHARED->RESERVED upgrade.
            con.execute("BEGIN IMMEDIATE")
            try:
                _ensure_workspace_row(con, workspace)
                cur = con.execute(
                    """
                    -- v39: the row is BORN carrying the cut mark
                    -- ('never_finalized'). Only a finalize that supplies no
                    -- cut_reason clears it, so a turn that never finalizes --
                    -- including one cut so hard that SubagentStop never fires
                    -- and no closure path ever touches this row -- stays
                    -- structurally marked with no further write required.
                    INSERT INTO agent_contract_handoffs
                        (contract_id, agent_id, session_id, workspace, brief_id,
                         plan_task_id, plan_id, parent_handoff_id, kind,
                         dispatch_prompt_id, dispatch_tool_use_id,
                         dispatch_description, dispatch_prompt,
                         context_anchors, kernel_sections, dispatch_project,
                         agent_state, cut_reason, raw_handoff_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISPATCHED', ?, ?, ?)
                    ON CONFLICT(contract_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        contract_id,
                        agent_id,
                        session_id,
                        workspace,
                        brief_id,
                        plan_task_id,
                        plan_id,
                        parent_handoff_id,
                        kind,
                        dispatch_prompt_id,
                        dispatch_tool_use_id,
                        dispatch_description,
                        dispatch_prompt,
                        context_anchors_json,
                        kernel_sections_json,
                        dispatch_project,
                        CUT_REASON_NEVER_FINALIZED,
                        raw_handoff_json,
                        _now_iso(),
                    ),
                )
                returned = cur.fetchone()
                if returned is not None:
                    handoff_id = returned["id"]
                    con.commit()
                    return {
                        "status": "applied",
                        "created": True,
                        "handoff_id": handoff_id,
                        "contract_id": contract_id,
                    }
                # A row already exists for this contract_id (idempotent no-op):
                # a re-dispatch, or the row already converged to a terminal
                # verdict. Never clobber it -- birth is write-once.
                existing = con.execute(
                    "SELECT id FROM agent_contract_handoffs WHERE contract_id = ?",
                    (contract_id,),
                ).fetchone()
                con.commit()
                return {
                    "status": "applied",
                    "created": False,
                    "handoff_id": existing["id"] if existing is not None else None,
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def agent_contract_handoff_exists(
    contract_id: str,
    *,
    db_path: "Path | None" = None,
) -> bool:
    """Return True iff ANY row exists for ``contract_id``.

    Read-only helper exposing the existence check T9's conditional hook
    backstop was originally designed around. Kept here (rather than inlined in
    the hook) so callers share the SAME notion of existence that
    :func:`finalize_agent_contract_handoff` relies on (the `contract_id` UNIQUE
    index) -- no separate query to keep in sync.

    NOTE (v37 born-at-dispatch): this returns True for a NASCENT row too -- one
    born at dispatch with ``agent_state='DISPATCHED'`` that has not yet
    converged to a terminal verdict. A caller that means "did the agent
    FINALIZE a terminal row" must use :func:`agent_contract_handoff_finalized`
    instead; "any row exists" and "a terminal row exists" only coincide when no
    born-at-dispatch nascent row is in play.

    Args:
        contract_id: The draft/contract id to check. A falsy value always
            returns False (there is nothing to look up).
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        True iff a row with this contract_id exists (nascent OR terminal);
        False otherwise (including when contract_id is empty/None).
    """
    if not contract_id:
        return False
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM agent_contract_handoffs WHERE contract_id = ? LIMIT 1",
            (contract_id,),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def agent_contract_handoff_state(
    contract_id: str,
    *,
    db_path: "Path | None" = None,
) -> "str | None":
    """Return the current ``agent_state`` of the row for ``contract_id``, or None.

    v37 born-at-dispatch (plan 34 task 5). The three states a caller cares about:

      * None            -- no row exists at all for this contract_id.
      * 'DISPATCHED'    -- a NASCENT row was born at dispatch but has not yet
                           converged to a terminal verdict (the agent is still
                           running, or crashed before finalize -- an ORPHAN that
                           the backstop/reaper must reconcile).
      * anything else   -- a TERMINAL verdict is already recorded (the agent's
                           own finalize, or a prior backstop) -- the row is done.

    The backstop uses this to tell "no row yet" (write a degraded row) from
    "orphaned DISPATCHED" (reap: converge to a degraded NON-COMPLETE verdict)
    from "already terminal" (stay passive).

    Args:
        contract_id: The draft/contract id to inspect. A falsy value returns None.
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        The ``agent_state`` string of the row, or None when no row exists.
    """
    if not contract_id:
        return None
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT agent_state FROM agent_contract_handoffs "
            "WHERE contract_id = ? LIMIT 1",
            (contract_id,),
        ).fetchone()
        return row["agent_state"] if row is not None else None
    finally:
        con.close()


def agent_contract_handoff_finalized(
    contract_id: str,
    *,
    db_path: "Path | None" = None,
) -> bool:
    """Return True iff a TERMINAL (non-DISPATCHED) row exists for ``contract_id``.

    v37 born-at-dispatch (plan 34 task 5). This is the "did the agent FINALIZE"
    question, distinct from :func:`agent_contract_handoff_exists` ("does ANY row
    exist"): a nascent row born at dispatch (``agent_state='DISPATCHED'``) exists
    but is NOT finalized. The conditional hook backstop and the M4 fence-missing
    reconstruction both key on THIS notion -- a nascent row must not be mistaken
    for a completed one.

    Args:
        contract_id: The draft/contract id to check. A falsy value returns False.
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        True iff a row exists whose ``agent_state`` is a terminal verdict (any
        value other than 'DISPATCHED'); False otherwise.
    """
    state = agent_contract_handoff_state(contract_id, db_path=db_path)
    return state is not None and state != "DISPATCHED"


def find_orphaned_dispatched_handoff(
    session_id: "str | None",
    agent_id: "str | Iterable[str] | None",
    *,
    db_path: "Path | None" = None,
) -> "dict | None":
    """Locate a nascent DISPATCHED row for ``(session_id, agent_id)``, or None.

    v37 born-at-dispatch (plan 34 task 5) -- the reaper's orphan-discovery
    query. A row born at dispatch is keyed by its ``contract_id``, which a caller
    that did not observe the dispatch cannot reconstruct, so the orphan is found
    instead by the coordinate pair the birth DOES stamp: ``session_id`` +
    ``agent_id``.

    WHICH IDENTITY ``agent_id`` HOLDS -- pass every candidate you have. Since the
    dispatch began minting a real, adoptable identity
    (``modules.agents.dispatch_identity.mint_dispatch_identity``), this column
    carries a MINTED handle (``gaia.contract.validator.AGENT_ID_PATTERN_TEXT``),
    the same space every other writer on this table uses; the agent's NAME is no
    longer here at all -- it lives in the birth ENVELOPE under
    :data:`BIRTH_AGENT_NAME_KEY`, which is what
    :func:`find_dispatched_row_by_agent_name` matches on. Rows born BEFORE that
    change still hold the agent NAME in this column, and a caller searching by
    one shape while the row carries the other matches NOTHING -- the defect that
    left every born row stuck in 'DISPATCHED' (zero reaps ever recorded) while
    the reaper's own unit tests passed against fixtures born under the other
    shape. So ``agent_id`` accepts EITHER a single identity OR an iterable of
    candidate identities, matched with IN (...): a caller that does not know
    which shape the row carries passes both.

    Only rows still in the 'DISPATCHED' ROW state are returned: a row that has
    already converged to a terminal verdict is not an orphan and is skipped. The
    most-recent (highest id) match is returned when several exist -- so a stale
    orphan from an earlier session is never reaped in place of the live turn's.

    Args:
        session_id: The dispatch session id (falsy -> None: nothing to match).
        agent_id:   The agent identity stamped at dispatch, or an iterable of
                    candidate identities to match against (falsy -> None).
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        ``{"id": int, "contract_id": str, "agent_id": str,
        "plan_task_id": int | None}`` of the orphaned nascent row, or None when
        no DISPATCHED row exists for that (session, agent) pair. ``agent_id`` is
        the identity the row was BORN under: a closer must preserve it rather
        than restamp the row with whichever candidate it searched by, or a row
        born under a minted handle would be rewritten to an agent NAME that no
        contract validator accepts.
    """
    if not session_id or not agent_id:
        return None
    if isinstance(agent_id, str):
        candidates = [agent_id]
    else:
        candidates = [str(a) for a in agent_id if a]
    if not candidates:
        return None
    con = _connect(db_path)
    try:
        placeholders = ", ".join("?" for _ in candidates)
        row = con.execute(
            f"SELECT id, contract_id, agent_id, plan_task_id "
            f"FROM agent_contract_handoffs "
            f"WHERE agent_state = 'DISPATCHED' AND session_id = ? "
            f"AND agent_id IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT 1",
            (session_id, *candidates),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "agent_id": row["agent_id"],
            "plan_task_id": row["plan_task_id"],
        }
    finally:
        con.close()


# The key under which :func:`insert_dispatched_handoff` records the dispatched
# agent's NAME inside the birth envelope. It is a JSON field rather than a column
# because the name is dispatch metadata, not row identity: the identity columns
# hold the minted handle the turn adopts, and adding a column would need a
# migration for a fact the birth envelope can already carry.
BIRTH_AGENT_NAME_KEY = "agent_name"

# The birth-envelope name lane reads the marker only from rows whose
# raw_handoff_json is valid JSON: the column is free text as far as SQLite is
# concerned, and a bare json_extract over a malformed value aborts the WHOLE
# query rather than skipping that row.
_BIRTH_AGENT_NAME_SQL = (
    "CASE WHEN json_valid(raw_handoff_json) "
    "THEN json_extract(raw_handoff_json, '$." + BIRTH_AGENT_NAME_KEY + "') END"
)


def find_dispatched_row_by_agent_name(
    session_id: "str | None",
    agent_name: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "dict | None":
    """Locate the SOLE nascent DISPATCHED row born for ``agent_name`` in a session.

    The fallback lane for a turn that never ADOPTED the identity minted for it at
    dispatch. Since the born row's ``agent_id`` column now holds that minted
    handle (not the agent's name), an unadopting turn shares no identifier with
    its own row: the agent minted an unrelated handle of its own, so neither the
    row's ``contract_id`` nor its ``agent_id`` can be reached from anything the
    turn produced. The dispatched NAME, recorded in the birth envelope
    (:data:`BIRTH_AGENT_NAME_KEY`), is the one coordinate both sides still share.

    REFUSES TO GUESS, and that is the whole design of this lane. A name is not
    unique per dispatch: two concurrent dispatches of the same agent type in one
    session each birth a row carrying the SAME name. Returning the most recent of
    them would close a row that belongs to a sibling turn that is still running --
    reaping a live dispatch, the exact corruption the minted identity exists to
    prevent. So this returns a row ONLY when the match is unambiguous: exactly one
    DISPATCHED row for that (session, name). Two or more -> None, and the rows are
    left in 'DISPATCHED' for the closure paths that can tell them apart.

    Args:
        session_id: The dispatch session id (falsy -> None: nothing to match).
        agent_name: The dispatched agent's name (falsy -> None).
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        ``{"id": int, "contract_id": str, "agent_id": str,
        "plan_task_id": int | None}`` when exactly one row matches; None when
        none or several do.
    """
    if not session_id or not agent_name:
        return None
    con = _connect(db_path)
    try:
        rows = con.execute(
            f"SELECT id, contract_id, agent_id, plan_task_id "
            f"FROM agent_contract_handoffs "
            f"WHERE agent_state = 'DISPATCHED' AND session_id = ? "
            f"AND {_BIRTH_AGENT_NAME_SQL} = ? "
            f"ORDER BY id DESC LIMIT 2",
            (session_id, str(agent_name)),
        ).fetchall()
        if len(rows) != 1:
            return None
        row = rows[0]
        return {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "agent_id": row["agent_id"],
            "plan_task_id": row["plan_task_id"],
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: claim_dispatch_row (v43 -- atomic dispatch->start correlation)
# ---------------------------------------------------------------------------
#
# SubagentStart holds the host's own coordinates (prompt_id, task_description,
# agent_type) but not the contract_id the row was born under. This claim is the
# DB-backed bridge that replaces guessing: it correlates the starting subagent
# to its born row by the dispatch coordinates persisted at birth and CLAIMS it
# atomically (claimed_at, UPDATE ... WHERE claimed_at IS NULL), so two
# concurrent starts can never both take the same row.
#
# Correlation is layered, and the layers only ever NARROW -- never invent:
#   (a) EXACT -- dispatch_prompt_id and/or dispatch_description equality
#       (always additionally scoped by the birth-envelope agent name when the
#       caller provides one);
#   (b) FIFO -- several candidates that are INDISTINGUISHABLE by (a) are claimed
#       oldest-first (ORDER BY created_at ASC, id ASC), but ONLY when their
#       material signatures all agree (identical dispatches: any assignment is
#       equivalent);
#   (c) GUARD -- several candidates whose material signatures DIVERGE mean the
#       correlation would be a guess between genuinely different turns. Nothing
#       is claimed; a critical `dispatch_correlation_ambiguous` anomaly and a
#       warning harness_event are written so the miss is loud, and the turn
#       starts without a kernel (the protocol's bare-init fallback).
#
# A signature is (kind, plan_task_id, plan_id, parent_handoff_id, brief_id,
# sha256(dispatch_prompt)) -- the coordinates that make two dispatches
# materially different even when their correlation keys collide.
# ---------------------------------------------------------------------------

# The anomaly/event identifiers the (c) guard writes under. Named here so tests
# and readers key on the constant, not on a prose string.
DISPATCH_CORRELATION_AMBIGUOUS_ANOMALY = "dispatch_correlation_ambiguous"
DISPATCH_CORRELATION_AMBIGUOUS_EVENT = "dispatch.correlation_ambiguous"

_DISPATCH_SIGNATURE_COLUMNS = (
    "kind",
    "plan_task_id",
    "plan_id",
    "parent_handoff_id",
    "brief_id",
)

# Columns returned to the claimer -- everything the dispatch kernel renders
# from, plus the identity/binding coordinates the caller stamps or logs.
_CLAIM_ROW_COLUMNS = (
    "id",
    "contract_id",
    "agent_id",
    "session_id",
    "workspace",
    "brief_id",
    "plan_task_id",
    "plan_id",
    "parent_handoff_id",
    "kind",
    "dispatch_prompt_id",
    "dispatch_tool_use_id",
    "dispatch_description",
    "dispatch_prompt",
    "claimed_at",
    "context_anchors",
    "kernel_sections",
    "dispatch_project",
    "created_at",
)


def _dispatch_signature(row: sqlite3.Row) -> tuple:
    """Material signature of a born row -- see the module comment above."""
    prompt = row["dispatch_prompt"] or ""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return tuple(row[col] for col in _DISPATCH_SIGNATURE_COLUMNS) + (digest,)


def _record_claim_ambiguity(
    candidates: "list[sqlite3.Row]",
    *,
    agent_name: "str | None",
    dispatch_prompt_id: "str | None",
    dispatch_description: "str | None",
    db_path: "Path | None",
) -> None:
    """Write the critical anomaly + warning event for a divergent-signature
    claim. Best-effort: the guard's REFUSAL is the load-bearing behavior; a
    failed telemetry write must not turn it into an exception."""
    workspace = candidates[0]["workspace"] if candidates else "global"
    contract_ids = [c["contract_id"] for c in candidates]
    payload = {
        "agent_name": agent_name,
        "dispatch_prompt_id": dispatch_prompt_id,
        "dispatch_description": dispatch_description,
        "candidate_contract_ids": contract_ids,
    }
    message = (
        f"claim_dispatch_row: {len(candidates)} unclaimed DISPATCHED rows match "
        f"the correlation keys but their material signatures diverge -- "
        f"refusing to claim (candidates: {', '.join(contract_ids)})"
    )
    try:
        # episode_anomalies is a child of episodes (FK), so the anomaly needs a
        # minimal parent episode to hang off; the guard mints one per refusal.
        episode_id = f"claim-ambiguous-{os.urandom(8).hex()}"
        insert_episode(
            workspace,
            episode_id,
            {"type": "dispatch_correlation", "agent": agent_name,
             "title": "ambiguous dispatch claim"},
            db_path=db_path,
        )
        insert_episode_anomaly(
            workspace,
            episode_id,
            {
                "type": DISPATCH_CORRELATION_AMBIGUOUS_ANOMALY,
                "severity": "critical",
                "message": message,
                "payload": payload,
            },
            db_path=db_path,
        )
    except Exception:
        pass  # telemetry must never turn the guard's refusal into an error
    try:
        write_harness_event(
            event_type=DISPATCH_CORRELATION_AMBIGUOUS_EVENT,
            source="store",
            agent=agent_name,
            result=message,
            severity="warning",
            meta=payload,
            workspace=workspace,
            db_path=db_path,
        )
    except Exception:
        pass  # same contract as the anomaly write above


def claim_dispatch_row(
    *,
    agent_name: "str | None" = None,
    dispatch_prompt_id: "str | None" = None,
    dispatch_description: "str | None" = None,
    db_path: "Path | None" = None,
) -> "dict | None":
    """Atomically claim the born row this subagent start correlates to.

    See the module comment above for the (a)/(b)/(c) correlation ladder.
    Requires at least one correlation key (``dispatch_prompt_id`` or
    ``dispatch_description``): with neither there is nothing exact to match and
    a claim would be a guess, so the function returns None and the turn
    starts without a kernel (the protocol's bare-init fallback).

    Args:
        agent_name:           The dispatched agent's NAME (birth-envelope
                              coordinate); scopes every layer when provided.
        dispatch_prompt_id:   Host prompt_id observed at SubagentStart.
        dispatch_description: Host task_description observed at SubagentStart.
        db_path:              Optional explicit DB path (used by tests).

    Returns:
        The claimed row as a dict (``_CLAIM_ROW_COLUMNS``, with ``claimed_at``
        set and ``context_anchors``/``kernel_sections`` still JSON strings), or
        None when nothing was claimed -- no candidate, an ambiguity refusal, or
        a lost race. None always means "fall back", never an error.
    """
    if not dispatch_prompt_id and not dispatch_description:
        return None

    def _candidates(con: sqlite3.Connection) -> "list[sqlite3.Row]":
        select = (
            f"SELECT {', '.join(_CLAIM_ROW_COLUMNS)} "
            "FROM agent_contract_handoffs "
            "WHERE agent_state = 'DISPATCHED' AND claimed_at IS NULL"
        )
        name_clause = f" AND {_BIRTH_AGENT_NAME_SQL} = ?" if agent_name else ""
        order = " ORDER BY created_at ASC, id ASC"

        def _query(clause: str, params: tuple) -> "list[sqlite3.Row]":
            full_params = params + ((str(agent_name),) if agent_name else ())
            return con.execute(
                select + clause + name_clause + order, full_params
            ).fetchall()

        # Layer (a), most exact first: prompt_id + description together, then
        # each alone. The first non-empty result set wins -- narrower keys are
        # never diluted by a broader retry.
        if dispatch_prompt_id and dispatch_description:
            rows = _query(
                " AND dispatch_prompt_id = ? AND dispatch_description = ?",
                (dispatch_prompt_id, dispatch_description),
            )
            if rows:
                return rows
        if dispatch_prompt_id:
            rows = _query(
                " AND dispatch_prompt_id = ?", (dispatch_prompt_id,)
            )
            if rows:
                return rows
        if dispatch_description:
            rows = _query(
                " AND dispatch_description = ?", (dispatch_description,)
            )
            if rows:
                return rows
        return []

    def _work() -> "dict | None":
        con = _connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                rows = _candidates(con)
                if not rows:
                    con.commit()
                    return None
                if len(rows) > 1:
                    signatures = {_dispatch_signature(r) for r in rows}
                    if len(signatures) > 1:
                        # Layer (c): divergent turns behind one key -- refuse.
                        con.commit()
                        _record_claim_ambiguity(
                            rows,
                            agent_name=agent_name,
                            dispatch_prompt_id=dispatch_prompt_id,
                            dispatch_description=dispatch_description,
                            db_path=db_path,
                        )
                        return None
                # Layer (b): single candidate, or identical siblings -- FIFO.
                chosen = rows[0]
                claimed_at = _now_iso()
                cur = con.execute(
                    "UPDATE agent_contract_handoffs SET claimed_at = ? "
                    "WHERE id = ? AND claimed_at IS NULL",
                    (claimed_at, chosen["id"]),
                )
                if cur.rowcount != 1:
                    # Lost a race with a concurrent claimer -- fall back rather
                    # than retry into someone else's row.
                    con.commit()
                    return None
                con.commit()
                claimed = {col: chosen[col] for col in _CLAIM_ROW_COLUMNS}
                claimed["claimed_at"] = claimed_at
                return claimed
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def is_born_at_dispatch_row(
    contract_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> bool:
    """Return True iff the row for ``contract_id`` CAME from a dispatch.

    Structural, not textual: ``kind``, ``plan_id`` and ``parent_handoff_id`` are
    written only by :func:`insert_dispatched_handoff` and by
    :func:`open_contract_continuation`, which copies them onto a link
    (``_CONTINUATION_CONSTRAINT_COLUMNS``). A finalize neither sets them on
    insert nor touches them on convergence, so any of the three being present
    means this row started as a dispatch or continues one that did. Reading the
    birth envelope instead would not survive adoption -- finalize replaces
    ``raw_handoff_json`` wholesale, so the birth marker inside it is gone
    precisely in the case a caller most needs to recognize.

    A LINK ANSWERS TRUE, and must: a resumption is the same turn, still holding
    the dispatch identity it adopted. Answering by the row alone made a resumed
    turn look like one that never adopted -- see the constraint-tuple comment for
    what the caller below then did to a concurrent sibling's live dispatch.

    The caller that needs this is the born-row closure: when the turn's OWN
    contract row is itself a born row, the turn adopted its dispatch identity and
    there is no separate scaffold left to close. Skipping the closure's
    last-resort name lane in that case is what stops a finished turn from closing
    the row of a concurrent sibling that is still running.
    """
    if not contract_id:
        return False
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT kind, plan_id, parent_handoff_id "
            "FROM agent_contract_handoffs WHERE contract_id = ? LIMIT 1",
            (contract_id,),
        ).fetchone()
        if row is None:
            return False
        return any(
            row[column] is not None
            for column in ("kind", "plan_id", "parent_handoff_id")
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: mirror_partial_contract_handoff (incremental fill -> the row)
# ---------------------------------------------------------------------------
#
# Brief: contrato-adoptado-en-dispatch-con-llenado-incremental-y-finalize (AC-2).
#
# Incremental contract building persisted to DISK only: `gaia contract
# set/add/fill` wrote the draft under ``data_dir()/contract_drafts/`` and nothing
# reached the row. A turn cut before `finalize` therefore left a row holding the
# birth envelope and none of the evidence the turn had already accumulated -- the
# work existed on disk but was invisible to every DB reader. This is the
# capability that closes that gap: the partial envelope is MIRRORED onto the
# turn's own already-born row as it is built, so the row carries recoverable
# partial evidence at every point of the turn, not only after finalize.
#
# It is a strictly WEAKER writer than finalize, by construction -- three
# properties, each enforced by the shape of the statement rather than by a
# caller's discipline:
#
#   * NEVER CREATES. The statement is an UPDATE, not an UPSERT. A draft with no
#     row (a turn that minted its own identity instead of adopting the one born
#     for it, or a plain CLI use with no dispatch behind it) mirrors to nothing:
#     the disk write still happened and this is a silent no-op. Birth stays the
#     sole business of insert_dispatched_handoff and finalize.
#   * NEVER TOUCHES A ROW WHOSE TURN ALREADY CLOSED. The guard is
#     ``agent_state NOT IN <CLOSED_TURN_PLAN_STATUSES>``, NOT the narrower
#     TERMINAL_PLAN_STATUSES finalize converges under, and the difference is the
#     measured defect: a producer that closed NEEDS_VERIFICATION is not terminal,
#     so a mirror used to MERGE the same agent's next assignment into the record
#     an independent verifier was about to read. Evidence is per-turn; a turn
#     that ended accepts none. This is the second layer -- `gaia contract
#     set/add/fill` already diverts such a write into a continuation before it
#     gets here (bin/cli/contract.py::_plan_continuation) -- and it is what makes
#     the property hold for any caller, not only the disciplined one. It does NOT
#     restrict the verifier: a truer verdict arrives through finalize, which is a
#     different writer with its own, deliberately narrower, guard.
#   * NEVER MOVES THE ROW'S STATE OR ITS BINDING. ``raw_handoff_json`` is the
#     ONLY column in the SET list. agent_state stays exactly where it was --
#     which is load-bearing, not incidental: a row mirrored out of 'DISPATCHED'
#     would vanish from find_orphaned_dispatched_handoff (no reap) and from
#     dispatched_binding_plan_task_id (the blind-verification gate would read the
#     turn as UNBOUND and let a plan-task-bound producer self-COMPLETE). The
#     binding columns (plan_task_id, plan_id, parent_handoff_id, kind), the
#     attribution (session_id) and the birth created_at are untouched for the
#     same reason finalize's DO UPDATE leaves them alone.
#   * NEVER CLEARS THE CUT MARK (v39). cut_reason is not in the SET list either,
#     so mirroring evidence onto a row does NOT make it look cleanly closed. This
#     is what keeps the two AC-2/AC-3 halves consistent: a turn cut after several
#     `set`/`add`/`fill` calls leaves a row that BOTH carries its partial
#     evidence AND still reads as a cut. Only finalize earns the clean state.
#
# Birth markers survive the mirror. The birth envelope carries the dispatched
# agent's NAME (BIRTH_AGENT_NAME_KEY) and the born_at_dispatch flag, and the
# closure's last-resort lane (find_dispatched_row_by_agent_name) matches on that
# name INSIDE a still-DISPATCHED row. Overwriting the envelope wholesale while
# the row stays DISPATCHED would erase the only coordinate that lane has, so the
# marker keys are carried forward onto the mirrored envelope. finalize still
# replaces the envelope wholesale -- by then the row has left 'DISPATCHED' and
# the name lane no longer looks at it.
# ---------------------------------------------------------------------------

# Top-level keys of the birth envelope that a mirror carries forward (see the
# module comment above). They are dispatch metadata, not contract fields, so a
# partial envelope never produces them on its own.
_BIRTH_MARKER_KEYS = ("born_at_dispatch", BIRTH_AGENT_NAME_KEY)


def merge_birth_markers(existing_raw: "str | None", envelope_raw: str) -> str:
    """Carry the birth-marker keys from ``existing_raw`` into ``envelope_raw``.

    Returns ``envelope_raw`` unchanged when either side is not a JSON object or
    the existing row carries no marker -- the mirror must degrade to "write the
    partial envelope as-is", never to a failed write.

    Public because the mirror is no longer the only writer that replaces a born
    row's envelope: the SubagentStop capture converges that same row when the
    turn never finalized, and it must preserve the dispatch's own marks for the
    same reason. One definition of "which keys are birth marks", used by both.
    """
    try:
        existing = json.loads(existing_raw) if existing_raw else None
        envelope = json.loads(envelope_raw)
    except (TypeError, ValueError):
        return envelope_raw
    if not isinstance(existing, dict) or not isinstance(envelope, dict):
        return envelope_raw
    carried = {
        key: existing[key]
        for key in _BIRTH_MARKER_KEYS
        if key in existing and key not in envelope
    }
    if not carried:
        return envelope_raw
    envelope.update(carried)
    return json.dumps(envelope)


def mirror_partial_contract_handoff(
    contract_id: "str | None",
    raw_handoff_json: str,
    *,
    db_path: "Path | None" = None,
) -> dict:
    """Mirror a PARTIAL contract envelope onto a row whose turn is still open.

    See the module comment above for the invariants this writer is shaped around
    (never creates, never touches a row whose turn already closed, never moves
    the row's state or binding). Called best-effort from ``gaia contract
    set/add/fill`` after the draft has been validated and persisted to disk, so
    the row tracks the draft while the turn is still running.

    Args:
        contract_id:      The draft/contract id the turn is building under. A
                          falsy value is a no-op (nothing to key on) rather than
                          an error -- this runs on a best-effort seam.
        raw_handoff_json: The partial envelope, serialized. Validated by the
                          caller BEFORE it gets here; this writer does not
                          re-validate, it only persists what the draft holds.
        db_path:          Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "handoff_id": int, "contract_id": str}`` when a
        still-open row was mirrored.
        ``{"status": "skipped", "reason": ...}`` otherwise, where reason is
        ``no_contract_id`` (nothing to key on), ``no_row`` (nothing born to
        mirror -- the silent no-op) or ``closed`` (the turn already declared an
        end; its evidence is complete and a later turn's belongs elsewhere).

    Raises:
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names an unseeded agent
            (the same gate finalize and birth are held to).
    """
    if not contract_id:
        return {"status": "skipped", "reason": "no_contract_id"}

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE (write-lock-first): same rationale as finalize --
            # the body is SELECT-then-UPDATE, so take the RESERVED lock up front
            # instead of racing a SHARED->RESERVED upgrade against a concurrent
            # finalize of the same row.
            con.execute("BEGIN IMMEDIATE")
            try:
                from gaia.state import CLOSED_TURN_PLAN_STATUSES
                existing = con.execute(
                    "SELECT id, agent_state, raw_handoff_json "
                    "FROM agent_contract_handoffs WHERE contract_id = ? LIMIT 1",
                    (contract_id,),
                ).fetchone()
                if existing is None:
                    con.commit()
                    return {"status": "skipped", "reason": "no_row"}
                if existing["agent_state"] in CLOSED_TURN_PLAN_STATUSES:
                    con.commit()
                    return {"status": "skipped", "reason": "closed"}

                merged = merge_birth_markers(
                    existing["raw_handoff_json"], raw_handoff_json
                )
                placeholders = ", ".join("?" for _ in CLOSED_TURN_PLAN_STATUSES)
                cur = con.execute(
                    f"""
                    -- raw_handoff_json is the ONLY column written: agent_state,
                    -- the born-at-dispatch binding (plan_task_id, plan_id,
                    -- parent_handoff_id, kind), session_id, the v39 cut_reason
                    -- and the birth created_at all stay exactly as they are.
                    -- Leaving cut_reason alone is load-bearing: a mirrored row
                    -- is a turn still in flight, and letting evidence-filling
                    -- clear the birth stamp would launder a cut into a clean
                    -- closure. The NOT IN guard
                    -- is redundant with the SELECT above under this lock and is
                    -- kept as the statement-level restatement of the invariant:
                    -- a turn that already closed is never edited in place, by
                    -- any writer.
                    UPDATE agent_contract_handoffs
                       SET raw_handoff_json = ?
                     WHERE contract_id = ?
                       AND agent_state NOT IN ({placeholders})
                    RETURNING id
                    """,
                    (merged, contract_id, *CLOSED_TURN_PLAN_STATUSES),
                )
                returned = cur.fetchone()
                con.commit()
                if returned is None:
                    return {"status": "skipped", "reason": "closed"}
                return {
                    "status": "applied",
                    "handoff_id": returned["id"],
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


# ---------------------------------------------------------------------------
# Public API: the CONTINUATION chain (v46)
# ---------------------------------------------------------------------------
#
# A TURN IS A CONTRACT. RESUMING DOES NOT REOPEN: IT CONTINUES.
#
# When the orchestrator resumes an agent that already closed its turn, the agent
# keeps working -- and, before this, had nowhere to write. Its row was terminal,
# so mirror_partial_contract_handoff answered `skipped/terminal` and
# finalize_agent_contract_handoff's `WHERE agent_state NOT IN (COMPLETE)` guard
# converged nothing while still reporting success. Every finding the resumed turn
# produced was dropped, and the row stayed frozen at the first close's content.
#
# The obvious repair -- prepare a fresh row when the resumption starts -- is not
# available: a resumption emits NO birth. The nascent row is written only from
# the dispatching PreToolUse:Task event; a resume arrives as SendMessage and
# births nothing. (Measured: since 2026-08-01 every specialist shows more
# agent.complete than agent.dispatch events -- platform-architect 426/256,
# gitops-operator 301/232, gaia-system 265/240.) So the ONLY moment left is the
# FIRST WRITE, which is where the CLI opens a continuation.
#
# What is reused across a resumption is the agent's CONTEXT, not its RECORD. A
# closed row is never modified -- not its state, not its content. The new work is
# born into a NEW row that declares which one it continues, through
# `continues_handoff_id`. Three consequences shape the writer below:
#
#   * CLOSED MEANS THE TURN ENDED, NOT THAT THE VERDICT IS FROZEN. The trigger
#     is `agent_state IN gaia.state.CLOSED_TURN_PLAN_STATUSES` -- every state an
#     agent can finalize under. It is deliberately NOT TERMINAL_PLAN_STATUSES,
#     which answers a different question (may a later, truer verdict replace this
#     one?) and is a strict subset. Both frontiers document each other at their
#     definitions; the gap between them is where the measured defect lived. A
#     turn that closed declaring NEEDS_VERIFICATION had ENDED, yet its row was
#     not terminal, so the same agent's next assignment merged its evidence into
#     the record it had already closed and its close replaced its own earlier
#     verdict -- every call exiting 0. Widening the trigger does not touch the
#     convergence the narrower set guards: finalize_agent_contract_handoff still
#     refuses only an already-COMPLETE row, so every lane that exists to correct
#     a record still reaches it (gaia.state, section 1b, records what those lanes
#     are and what merging the two sets was measured to break).
#   * ONE LINK PER CLOSED ROW. The insert runs under BEGIN IMMEDIATE and first
#     looks for an existing child, so two concurrent first-writes cannot fork a
#     closed row into two continuations; the loser adopts the winner's link.
#   * THE LINK IS ALREADY CLAIMED. `claimed_at` is stamped at mint time even
#     though no SubagentStart will ever claim it. claim_dispatch_row's candidate
#     pool is exactly `agent_state='DISPATCHED' AND claimed_at IS NULL`, so the
#     stamp is what keeps a link out of it. The link also carries none of the
#     correlation keys that pool matches on, which makes the exclusion true
#     twice over rather than by a single stamp.
# ---------------------------------------------------------------------------

# A LINK'S BIRTH SORTS THE PARENT'S COLUMNS BY WHAT THEY DO, NOT BY WHERE THEY
# CAME FROM. Two categories are inherited and one is not, and the two rules point
# in opposite directions because the ways they fail are opposite:
#
#   * A column that DESCRIBES THE ASSIGNMENT is never inherited. Inheriting it
#     LIES. The assignment (dispatch_prompt), its description, the prompt and
#     tool-use correlation keys, the project, the brief and the parent binding
#     all belong to the turn that ENDED; the link exists precisely because a new
#     turn began, so no new value is available at mint time and the old one is
#     false. Inheriting them produced a link whose content described renaming a
#     module while its recorded assignment said "audit the pipeline": not empty,
#     but populated with something untrue, which is worse, because an empty
#     column is visible to every reader and a filled one is not. They stay NULL,
#     and the dispatch coordinates of the turn that ended remain readable where
#     they already are -- on the parent, which `gaia contract chain` walks to.
#   * A column that CONSTRAINS THE AGENT is never dropped. Dropping it makes the
#     resumption an ESCAPE HATCH. MEASURED: minting the link clean took
#     ``plan_task_id`` with it, so a producer bound to a plan task -- forbidden
#     from signing its own COMPLETE by both finalize seams
#     (bin/cli/contract.py::cmd_finalize and
#     hooks/adapters/claude_code.py::_blind_verification_required) -- self-signed
#     simply by writing once more after its close: the producer's contract id
#     answered "blocked, task 910" and the link's answered "not blocked, None".
#     The SubagentStop gate is reached the same way, since it resolves the row by
#     harness id and that lookup collapses the chain to this very link
#     (collapse_continuation_chains).
#
# The two categories cannot be merged because a constraint is not a description:
# ``dispatch_prompt`` says what the ended turn was ASKED, which the new turn does
# not know; ``plan_task_id`` says what the agent MAY NOT DO, which the new turn
# has not stopped being subject to. Its truth does not expire with the turn --
# the same agent is still working the same plan task -- so carrying it forward
# asserts nothing the record does not already hold.
#
# ``kind``, ``plan_id`` and ``parent_handoff_id`` belong here for the same reason
# as ``plan_task_id``, and were once left out of it by reading them as labels for
# WHICH assignment this is rather than as restrictions on it. That reading is
# wrong, and one code path is enough to make it wrong: ``is_born_at_dispatch_row``
# answers "did this turn adopt an identity minted at dispatch?" by asking whether
# ANY of the three is present, and its only consumer -- the SubagentStop closure,
# hooks/modules/agents/handoff_persister.py::close_born_dispatch_row -- turns a
# YES into a REFUSAL: the last-resort lane that finds a born row by the dispatched
# agent's NAME is then skipped entirely. That lane must be refused for an adopting
# turn, because a NAME is shared by every dispatch of that agent while an identity
# is not. MEASURED: a link minted without the three answered "not born at
# dispatch" for a turn that was; the lane switched back on; and since the turn had
# by then closed both its own rows, the only row still DISPATCHED under that name
# was a CONCURRENT SIBLING'S live dispatch -- which the resuming turn closed and
# stamped as superseded by its own link.
#
# They pass the DESCRIPTIVE test too, which is why carrying them forward asserts
# nothing false: each names something a resumption does not change -- the same
# agent, still running the same kind of turn, on the same plan, answering to the
# same producer's row. ``dispatch_prompt`` is the contrast: it is the text of an
# assignment that ENDED, and no new value for it exists at mint time.
#
# The admission rule stays narrow: some code path must REFUSE an action because
# of the column. One that merely identifies the work stays out, or it reopens the
# lie the first category exists to prevent.
_CONTINUATION_CONSTRAINT_COLUMNS = (
    "plan_task_id",
    "kind",
    "plan_id",
    "parent_handoff_id",
)

# Columns naming WHO the agent is, WHERE it runs and WHICH harness run it belongs
# to -- unchanged by a resumption, so inherited verbatim.
#
# harness_agent_id is not an exception to the first rule above: the harness mints
# it per RUN, not per turn, so a resumption genuinely carries the same one. It is
# also load-bearing -- the SubagentStop bridge resolves the closing turn's row by
# it (see collapse_continuation_chains).
_CONTINUATION_IDENTITY_COLUMNS = (
    "agent_id",
    "session_id",
    "workspace",
    "harness_agent_id",
)

# Everything NOT in these two tuples is either set explicitly by
# open_contract_continuation (the new contract_id, a fresh DISPATCHED state and
# birth cut mark, the link itself, the seed envelope, claimed_at, created_at) or
# left NULL on purpose.
_CONTINUATION_INHERITED_COLUMNS = (
    *_CONTINUATION_IDENTITY_COLUMNS,
    *_CONTINUATION_CONSTRAINT_COLUMNS,
)

# Hard bound on a chain walk. A chain is one turn's resumptions, which is a
# handful in practice; the bound exists so a corrupt self-referential edge
# degrades to a truncated answer instead of spinning inside a stop hook.
_MAX_CONTINUATION_LINKS = 64


def _row_by_contract_id(con: sqlite3.Connection, contract_id: str):
    return con.execute(
        "SELECT * FROM agent_contract_handoffs WHERE contract_id = ? LIMIT 1",
        (contract_id,),
    ).fetchone()


def _continuation_edge(row: sqlite3.Row) -> "int | None":
    """The parent link of ``row``, as a diagnosis rather than a key error.

    A ``SELECT *`` against a database predating schema v46 returns a row with no
    such key, and sqlite3.Row reports that as ``IndexError: No item with that
    key`` -- a message that names neither the column nor the reason, and would
    have been the whole content of the recorded incident.
    """
    try:
        return row["continues_handoff_id"]
    except (IndexError, KeyError) as exc:
        raise sqlite3.OperationalError(
            "no such column: continues_handoff_id -- this database predates "
            "schema v46; apply scripts/migrations/v45_to_v46.sql"
        ) from exc


def _continuation_chain_rows(
    con: sqlite3.Connection, contract_id: str
) -> "list[sqlite3.Row]":
    """The full chain containing ``contract_id``, oldest link first.

    Walks BACK to the root through ``continues_handoff_id`` and then FORWARD
    through the children, so any member recovers the whole chain -- which is the
    property an operator needs: they hold whatever id they happened to find, not
    necessarily the first one.
    """
    row = _row_by_contract_id(con, contract_id)
    if row is None:
        return []

    # The two walks keep SEPARATE visited sets on purpose. Sharing one would
    # make the forward walk refuse to re-emit the links the backward walk
    # already passed through, so entering the chain anywhere but at its root
    # would return only the root -- which is precisely the case this function
    # exists for (an operator holds whichever id they happened to find).
    walked_back = {row["id"]}
    root = row
    for _ in range(_MAX_CONTINUATION_LINKS):
        previous_id = _continuation_edge(root)
        if previous_id is None:
            break
        previous = con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE id = ? LIMIT 1",
            (previous_id,),
        ).fetchone()
        if previous is None or previous["id"] in walked_back:
            break
        walked_back.add(previous["id"])
        root = previous

    chain = [root]
    seen = {root["id"]}
    cursor = root
    for _ in range(_MAX_CONTINUATION_LINKS):
        child = con.execute(
            "SELECT * FROM agent_contract_handoffs "
            "WHERE continues_handoff_id = ? ORDER BY id ASC LIMIT 1",
            (cursor["id"],),
        ).fetchone()
        if child is None or child["id"] in seen:
            break
        seen.add(child["id"])
        chain.append(child)
        cursor = child
    return chain


# The event type the unreadable-chain trace is written under. It is a
# harness_event and not a log line because this module has no logger and an
# operator cannot grep a process that already exited: the event is queryable
# after the fact with `gaia query --surface harness_events`.
CONTINUATION_CHAIN_UNREADABLE_EVENT = "contract.chain_unreadable"


class ContinuationChainUnreadable(RuntimeError):
    """The chain of a contract id could not be READ -- distinct from "no row".

    Raised only after the substrate itself refused the walk (most concretely: a
    database predating ``continues_handoff_id``, schema < v46, where selecting
    the edge raises). An id that simply names no row is not this: that answer is
    known, and it is the empty chain.
    """

    def __init__(self, contract_id: str, cause: BaseException) -> None:
        super().__init__(
            f"the continuation chain of {contract_id!r} could not be read "
            f"({type(cause).__name__}: {cause}). This is NOT 'no such contract' "
            f"-- whether that row exists is unknown from here."
        )
        self.contract_id = contract_id
        self.cause = cause


def _chain_unreadable_recorded(error: str, db_path: "Path | None") -> bool:
    """True when this exact failure is already on record for this database."""
    con = _connect(db_path)
    try:
        return con.execute(
            "SELECT 1 FROM harness_events WHERE type = ? AND "
            "CASE WHEN json_valid(payload) "
            "THEN json_extract(payload, '$.error') END = ? LIMIT 1",
            (CONTINUATION_CHAIN_UNREADABLE_EVENT, error),
        ).fetchone() is not None
    finally:
        con.close()


def _chain_unreadable(
    contract_id: str, cause: BaseException, db_path: "Path | None"
) -> "ContinuationChainUnreadable":
    """Record the failure where an operator can find it, and build the error.

    ONE INCIDENT PER DISTINCT FAILURE, not per contract id. The failure this
    exists for describes the SUBSTRATE ("no such column: continues_handoff_id"),
    which is true of every row at once, and every contract write resolves a chain
    -- so an event per occurrence would bury the signal it exists to raise, and
    would be graded by ``gaia defects`` as a flood of separate defects. The
    ``contract_id`` in the payload is therefore the first id that met the
    failure, an example rather than its subject.
    """
    error = f"{type(cause).__name__}: {cause}"
    try:
        if not _chain_unreadable_recorded(error, db_path):
            write_harness_event(
                event_type=CONTINUATION_CHAIN_UNREADABLE_EVENT,
                source="store",
                result=(
                    f"continuation chain unreadable ({error}); first seen "
                    f"resolving {contract_id}"
                ),
                severity="warning",
                meta={"contract_id": contract_id, "error": error},
                db_path=db_path,
            )
    except Exception:
        # The trace is the second surface; the RAISE is the load-bearing half,
        # and a substrate too broken to hold an event must not swallow it.
        pass
    return ContinuationChainUnreadable(contract_id, cause)


def continuation_chain(
    contract_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "list[dict]":
    """Every link of the continuation chain ``contract_id`` belongs to.

    Ordered oldest-first, so ``[0]`` is the turn's original contract and ``[-1]``
    is the link currently open. A contract that was never resumed returns a
    single-element list (itself); an unknown contract id returns ``[]``.

    AN EMPTY LIST MEANS "NO SUCH ROW" AND NOTHING ELSE. This used to catch every
    exception and return ``[]``, which merged that answer with "the walk failed"
    -- and the merge was not hypothetical: against a database still on schema
    < v46 the edge column does not exist, so every existing contract read as
    absent and ``gaia contract chain`` told the operator no row existed for an id
    they were holding. A failure now RAISES
    :class:`ContinuationChainUnreadable` and leaves a
    ``contract.chain_unreadable`` harness_event behind, so the distinction
    survives the process that made it.

    This is the read behind ``gaia contract chain``: given ANY link, an operator
    recovers the whole chain with one call rather than following ids by hand.

    Raises:
        ContinuationChainUnreadable: the substrate refused the walk.
    """
    if not contract_id:
        return []
    try:
        con = _connect(db_path)
    except Exception as exc:
        raise _chain_unreadable(contract_id, exc, db_path) from exc
    try:
        return [dict(r) for r in _continuation_chain_rows(con, contract_id)]
    except Exception as exc:
        raise _chain_unreadable(contract_id, exc, db_path) from exc
    finally:
        con.close()


def continuation_tip(
    contract_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "dict | None":
    """The LIVE link of ``contract_id``'s chain -- the row a write lands on.

    Returns the row itself when it was never resumed, the newest link when it
    was, and None when no row exists for the id at all. Callers that must not
    write to a superseded record resolve through this first.

    Raises:
        ContinuationChainUnreadable: propagated from :func:`continuation_chain`.
            None here means "no such row", never "the walk failed" -- a caller
            that wants to degrade must catch the error and say so.
    """
    chain = continuation_chain(contract_id, db_path=db_path)
    return chain[-1] if chain else None


def collapse_continuation_chains(rows: "list[dict]") -> "list[dict]":
    """Drop every row in ``rows`` that another row in ``rows`` continues.

    A PURE function over an already-fetched result set, so a caller that queried
    by some other coordinate can reduce a chain to its live link without a second
    round trip. The case it exists for is the SubagentStop bridge: a resumption
    carries the SAME harness agent id as the turn it continues, so a lookup by
    that id returns every link of the chain and would otherwise read as an
    ambiguous match and be declined -- rejecting the close of a turn whose work
    is perfectly recorded. Rows unrelated to each other are all preserved, so a
    genuine ambiguity is still reported as one.
    """
    superseded = {
        row.get("continues_handoff_id")
        for row in rows
        if row.get("continues_handoff_id") is not None
    }
    return [row for row in rows if row.get("id") not in superseded]


def open_contract_continuation(
    parent_contract_id: "str | None",
    new_contract_id: "str | None",
    *,
    raw_handoff_json: str,
    db_path: "Path | None" = None,
) -> dict:
    """Mint the contract a resumed turn continues into, or return the existing one.

    See the module comment above for why this exists and why the first write is
    the only moment available. The closed row is READ and never written: this
    function only INSERTs, so neither the state nor the content of the record it
    continues can change.

    CLOSED is ``agent_state IN gaia.state.CLOSED_TURN_PLAN_STATUSES`` -- the turn
    declared an end, in ANY of the five states an agent may finalize under. A row
    still DISPATCHED, or reaped to IN_PROGRESS by the backstop, names a turn
    nobody closed and is written in place (``not_closed``).

    Args:
        parent_contract_id: The CLOSED contract the write was addressed to.
        new_contract_id:    The contract id to mint the link under. Supplied by
                            the caller (the CLI owns id minting -- see
                            gaia.contract.drafts.mint_draft_id) so the store
                            stays free of the draft-addressing scheme.
        raw_handoff_json:   The link's seed envelope, serialized. The caller
                            builds it; this writer never invents contract
                            content.
        db_path:            Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "opened", "created": bool, "contract_id": <link's id>,
        "handoff_id": int, "continues_contract_id": str,
        "continues_handoff_id": int}`` -- ``created`` False when a link already
        existed for this closed row and was adopted instead of minted.
        ``{"status": "skipped", "reason": ...}`` otherwise, where reason is
        ``no_contract_id``, ``no_row`` (nothing to continue) or ``not_closed``
        (the row is still writable in place, so no continuation is warranted).

    Raises:
        HandoffWriteForbidden: the same gate birth / finalize / mirror are held to.
    """
    if not parent_contract_id or not new_contract_id:
        return {"status": "skipped", "reason": "no_contract_id"}

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE (write-lock-first): the body is SELECT-then-INSERT
            # and the SELECT is what makes "one link per closed row" true, so the
            # write lock has to be held across both.
            con.execute("BEGIN IMMEDIATE")
            try:
                from gaia.state import (
                    CLOSED_TURN_PLAN_STATUSES,
                    CUT_REASON_NEVER_FINALIZED,
                )

                parent = _row_by_contract_id(con, parent_contract_id)
                if parent is None:
                    con.commit()
                    return {"status": "skipped", "reason": "no_row"}
                if parent["agent_state"] not in CLOSED_TURN_PLAN_STATUSES:
                    con.commit()
                    return {"status": "skipped", "reason": "not_closed"}

                existing = con.execute(
                    "SELECT id, contract_id FROM agent_contract_handoffs "
                    "WHERE continues_handoff_id = ? ORDER BY id ASC LIMIT 1",
                    (parent["id"],),
                ).fetchone()
                if existing is not None:
                    con.commit()
                    return {
                        "status": "opened",
                        "created": False,
                        "contract_id": existing["contract_id"],
                        "handoff_id": existing["id"],
                        "continues_contract_id": parent_contract_id,
                        "continues_handoff_id": parent["id"],
                    }

                columns = (
                    "contract_id",
                    *_CONTINUATION_INHERITED_COLUMNS,
                    "continues_handoff_id",
                    "agent_state",
                    "cut_reason",
                    "claimed_at",
                    "raw_handoff_json",
                    "created_at",
                )
                now = _now_iso()
                values = (
                    new_contract_id,
                    *(parent[column] for column in _CONTINUATION_INHERITED_COLUMNS),
                    parent["id"],
                    "DISPATCHED",
                    CUT_REASON_NEVER_FINALIZED,
                    now,
                    raw_handoff_json,
                    now,
                )
                cur = con.execute(
                    f"""
                    -- ON CONFLICT DO NOTHING keeps the contract_id UNIQUE index
                    -- authoritative: an id that somehow already names a row is
                    -- never clobbered, exactly as at birth.
                    INSERT INTO agent_contract_handoffs ({', '.join(columns)})
                    VALUES ({', '.join('?' for _ in columns)})
                    ON CONFLICT(contract_id) DO NOTHING
                    RETURNING id
                    """,
                    values,
                )
                returned = cur.fetchone()
                if returned is None:
                    con.commit()
                    return {"status": "skipped", "reason": "contract_id_taken"}
                con.commit()
                return {
                    "status": "opened",
                    "created": True,
                    "contract_id": new_contract_id,
                    "handoff_id": returned["id"],
                    "continues_contract_id": parent_contract_id,
                    "continues_handoff_id": parent["id"],
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def stamp_harness_agent_id(
    contract_id: "str | None",
    harness_agent_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> dict:
    """Record the harness's own per-run agent id on the turn's contract row.

    The row lives in the CLI-minted identifier space; the harness reports a
    DIFFERENT id for the same run (``agentId`` on the Task result, ``agent_id``
    on SubagentStart/SubagentStop payloads), and nothing joined the two. This
    stamp is that join, and its call site is deliberately SubagentStart: the
    one point in the dispatch lifecycle where the caller holds both identities
    BEFORE the turn can be cut. SubagentStop cannot be the stamping seam --
    it never fires on a harness cut, which is exactly the turn this join
    exists to recover (``SELECT ... WHERE harness_agent_id = ?`` instead of
    searching by date and content).

    Only ``harness_agent_id`` is written: the verdict, the binding, the birth
    ``created_at``, ``cut_reason`` and ``raw_handoff_json`` all stay exactly
    as they are. A terminal row is refused, restating the writer-wide
    invariant that a final verdict is never edited in place; at the stamping
    seam the row is still 'DISPATCHED', so the refusal only guards the odd
    late caller.

    Returns:
        ``{"status": "applied", "handoff_id": int, "contract_id": str}`` when
        the stamp landed; ``{"status": "skipped", "reason": ...}`` otherwise
        (``no_contract_id`` / ``no_harness_agent_id`` / ``no_row`` /
        ``terminal``).
    """
    if not contract_id:
        return {"status": "skipped", "reason": "no_contract_id"}
    if not harness_agent_id:
        return {"status": "skipped", "reason": "no_harness_agent_id"}

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                from gaia.state import TERMINAL_PLAN_STATUSES

                placeholders = ", ".join("?" for _ in TERMINAL_PLAN_STATUSES)
                cur = con.execute(
                    f"""
                    UPDATE agent_contract_handoffs
                       SET harness_agent_id = ?
                     WHERE contract_id = ?
                       AND agent_state NOT IN ({placeholders})
                    RETURNING id
                    """,
                    (str(harness_agent_id), contract_id, *TERMINAL_PLAN_STATUSES),
                )
                returned = cur.fetchone()
                con.commit()
                if returned is None:
                    exists = con.execute(
                        "SELECT 1 FROM agent_contract_handoffs "
                        "WHERE contract_id = ? LIMIT 1",
                        (contract_id,),
                    ).fetchone()
                    reason = "terminal" if exists is not None else "no_row"
                    return {"status": "skipped", "reason": reason}
                return {
                    "status": "applied",
                    "handoff_id": returned["id"],
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def reconcile_cut_row(
    contract_id: "str | None",
    *,
    raw_handoff_json: "str | None" = None,
    db_path: "Path | None" = None,
) -> dict:
    """Clear the cut mark on a row whose turn is accounted for elsewhere.

    The narrow counterpart of :func:`stamp_harness_agent_id`: it writes
    ``cut_reason`` (always to NULL) and optionally ``raw_handoff_json``, and
    NOTHING else -- never ``agent_state``, never the binding, never the
    attribution. It therefore cannot promote a turn to COMPLETE, cannot alter
    what any gate reads, and cannot change which plan task a row answers for.

    WHY IT MAY TOUCH A TERMINAL ROW WHERE :func:`finalize_agent_contract_handoff`
    MAY NOT. That guard protects the VERDICT -- a COMPLETE row's outcome is
    never rewritten. ``cut_reason`` is not the verdict; it is the structural
    mark of HOW the row was closed, and the SubagentStop backstop can stamp it
    onto a row that carries a perfectly good COMPLETE envelope
    (``CUT_REASON_BACKSTOP_CAPTURE`` on a fence-only turn). Routing this through
    finalize would silently no-op on exactly those rows and leave them
    permanently cut, so the operation gets its own statement with its own,
    narrower scope.

    Refuses a row that is NOT marked cut (``cut_reason IS NULL``): there is
    nothing to reconcile, and the refusal keeps this from becoming a general
    "edit any row" door.

    Returns ``{"status": "applied", "handoff_id": int, "contract_id": str}`` or
    ``{"status": "skipped", "reason": ...}`` (``no_contract_id`` / ``no_row`` /
    ``not_cut``).
    """
    if not contract_id:
        return {"status": "skipped", "reason": "no_contract_id"}

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                if raw_handoff_json is None:
                    cur = con.execute(
                        "UPDATE agent_contract_handoffs SET cut_reason = NULL "
                        "WHERE contract_id = ? AND cut_reason IS NOT NULL "
                        "RETURNING id",
                        (contract_id,),
                    )
                else:
                    cur = con.execute(
                        "UPDATE agent_contract_handoffs "
                        "   SET cut_reason = NULL, raw_handoff_json = ? "
                        " WHERE contract_id = ? AND cut_reason IS NOT NULL "
                        "RETURNING id",
                        (raw_handoff_json, contract_id),
                    )
                returned = cur.fetchone()
                con.commit()
                if returned is None:
                    exists = con.execute(
                        "SELECT 1 FROM agent_contract_handoffs "
                        "WHERE contract_id = ? LIMIT 1",
                        (contract_id,),
                    ).fetchone()
                    reason = "not_cut" if exists is not None else "no_row"
                    return {"status": "skipped", "reason": reason}
                return {
                    "status": "applied",
                    "handoff_id": returned["id"],
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def demote_uncertified_completion(
    contract_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> dict:
    """Take a COMPLETE verdict off a row no agent ever certified.

    The mirror image of :func:`reconcile_cut_row`: that one clears the cut mark
    and never touches the verdict; this one corrects the verdict and never
    touches the cut mark. Both are narrow on purpose, and the narrowness is the
    safety property -- neither can become a general "edit any row" door.

    WHAT MAKES THIS SAFE IS THE PREDICATE, not the caller. It fires only on a
    row that is BOTH ``agent_state = 'COMPLETE'`` AND ``cut_reason IS NOT NULL``
    -- a row closed by a CLOSURE path (backstop capture, reap, truncation
    salvage) that nonetheless carries a COMPLETE verdict. A row the agent
    finalized itself has ``cut_reason IS NULL`` (see
    :func:`finalize_agent_contract_handoff`, where leaving that argument alone
    is what produces a clean closure) and is therefore unreachable from here.
    A genuine completion can never be demoted by this statement.

    WHY THE CASE EXISTS AT ALL. The SubagentStop backstop captures a turn that
    emitted a valid fenced envelope but never ran ``gaia contract finalize``,
    and it records that fence's own ``agent_state`` -- COMPLETE included. The
    existing downgrade in ``handoff_persister`` covers only the REAPING branch
    (an orphaned nascent row being converged), so a fence-only turn, which has
    no nascent row to converge, keeps its self-declared COMPLETE. That row then
    satisfies the briefs invariant "a closed plan must have a COMPLETE handoff"
    (``gaia/briefs/store.py``) for a turn nobody completed.

    IN_PROGRESS is the honest replacement, matching the reaping branch: the turn
    ran and did not close, which is exactly what happened.

    Returns ``{"status": "applied", "handoff_id": int, "contract_id": str}`` or
    ``{"status": "skipped", "reason": ...}`` (``no_contract_id`` / ``no_row`` /
    ``not_demotable``).
    """
    if not contract_id:
        return {"status": "skipped", "reason": "no_contract_id"}

    _assert_dispatch_can_write_handoff()

    def _work() -> dict:
        con = _connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute(
                    "UPDATE agent_contract_handoffs "
                    "   SET agent_state = 'IN_PROGRESS' "
                    " WHERE contract_id = ? "
                    "   AND agent_state = 'COMPLETE' "
                    "   AND cut_reason IS NOT NULL "
                    "RETURNING id",
                    (contract_id,),
                )
                returned = cur.fetchone()
                con.commit()
                if returned is None:
                    exists = con.execute(
                        "SELECT 1 FROM agent_contract_handoffs "
                        "WHERE contract_id = ? LIMIT 1",
                        (contract_id,),
                    ).fetchone()
                    reason = "not_demotable" if exists is not None else "no_row"
                    return {"status": "skipped", "reason": reason}
                return {
                    "status": "applied",
                    "handoff_id": returned["id"],
                    "contract_id": contract_id,
                }
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    return _retry_on_locked(_work)


def dispatch_row_for_identity(
    session_id: "str | None",
    agent_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "dict | None":
    """Return the row for ``(session_id, agent_id)`` in ANY state, or None.

    The state-agnostic sibling of :func:`find_orphaned_dispatched_handoff`. Both
    key on the coordinate pair a birth stamps, but they answer different
    questions: the orphan finder asks "is a born row still open?", this asks
    "which row belongs to this turn?".

    ADOPTION is why the distinction now matters. When a turn adopts the identity
    minted for it, its ``gaia contract finalize`` CONVERGES the born row itself,
    so by the time SubagentStop runs, the row is no longer 'DISPATCHED' and the
    orphan finder correctly reports nothing. The binding stamped at birth
    (``plan_task_id``, ``parent_handoff_id``) survives that convergence untouched,
    and a reader that needs it -- the blind-verification gate above all -- must
    still be able to reach it. Restricting to 'DISPATCHED' would make an adopted
    turn look unbound and silently drop the gate that forbids a plan-task-bound
    producer from self-COMPLETEing.

    Pass a MINTED handle, not an agent name: the pair is unique per dispatch only
    in the minted space (see :func:`find_dispatched_row_by_agent_name` for why a
    name is not).

    Args:
        session_id: The dispatch session id (falsy -> None).
        agent_id:   The minted identity the row was born under (falsy -> None).
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        ``{"id": int, "contract_id": str, "agent_id": str, "agent_state": str,
        "plan_task_id": int | None}`` of the most-recent match, or None.
    """
    if not session_id or not agent_id:
        return None
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id, contract_id, agent_id, agent_state, plan_task_id "
            "FROM agent_contract_handoffs "
            "WHERE session_id = ? AND agent_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, str(agent_id)),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "agent_id": row["agent_id"],
            "agent_state": row["agent_state"],
            "plan_task_id": row["plan_task_id"],
        }
    finally:
        con.close()


def dispatched_binding_plan_task_id(
    session_id: "str | None",
    agent_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "int | None":
    """Return the ``plan_task_id`` bound to the nascent DISPATCHED row for
    ``(session_id, agent_id)``, or None.

    The finalize gate (plan 34 task 7) keys blind verification on whether the
    turn's dispatch binding carries a ``plan_task_id``. A row born at dispatch
    stamps that binding alongside ``session_id`` + ``agent_id`` (the same
    coordinate pair :func:`find_orphaned_dispatched_handoff` reaps by), so the
    SubagentStop gate can recover the binding for the turn that is ending. Only
    a row still in the 'DISPATCHED' ROW state is consulted -- the binding of the
    turn as dispatched, before it converges to a terminal verdict; the
    most-recent (highest id) match wins. Returns None when no DISPATCHED row
    exists for that pair or its ``plan_task_id`` is NULL (an unbound turn --
    investigation / memory / a free-standing verifier turn).
    """
    if not session_id or not agent_id:
        return None
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT plan_task_id FROM agent_contract_handoffs "
            "WHERE agent_state = 'DISPATCHED' AND session_id = ? AND agent_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, agent_id),
        ).fetchone()
        if row is None:
            return None
        return row["plan_task_id"]
    finally:
        con.close()


def dispatched_binding_plan_task_id_by_contract(
    contract_id: "str | None",
    *,
    db_path: "Path | None" = None,
) -> "int | None":
    """Return the ``plan_task_id`` bound to the row for ``contract_id``, or None.

    The CONTRACT-KEYED sibling of :func:`dispatched_binding_plan_task_id` (which
    keys by the (session, agent) coordinate pair the live SubagentStop gate has).
    The ``gaia contract finalize`` CLI, by contrast, holds the draft's
    ``contract_id`` (== ``draft_id``) and NOT the harness session id, so it needs
    to recover the binding by that key instead.

    This is what lets the CLI finalize path (plan 34 task 8) close the
    role-blind self-COMPLETE leak: a turn born at dispatch stamps its
    ``plan_task_id`` onto the row (task 6), so before the CLI converges a draft to
    a terminal COMPLETE it can ask "is this turn bound to a plan task?" and refuse
    the self-COMPLETE when the answer is yes -- the SAME binding-keyed decision the
    SubagentStop gate already enforces, now applied at the CLI seam too so neither
    persistence path is a bypass.

    Unlike the session/agent reader, this does NOT restrict to the 'DISPATCHED'
    ROW state: the binding columns are preserved verbatim when finalize converges
    the nascent row (the DO UPDATE never touches plan_task_id), so the binding is
    readable both before convergence (the normal CLI-finalize case) and after (an
    idempotent re-finalize). Returns None when no row exists for ``contract_id``
    or its ``plan_task_id`` is NULL (an unbound turn -- investigation / memory / a
    free-standing verifier turn, all free to self-COMPLETE).

    THE BINDING IS A PROPERTY OF THE CHAIN, NOT OF ONE ROW, so an empty column
    is answered by walking BACK through ``continues_handoff_id`` to the nearest
    ancestor that carries one. The mint already copies the constraint onto every
    new link (``_CONTINUATION_CONSTRAINT_COLUMNS``), so this walk is what keeps
    the answer true for a link that was minted WITHOUT it -- by an older build,
    or by a mint that raced. It matters because a flat read here is the exact
    shape of the measured leak: the same agent, still on the same plan task,
    self-signed COMPLETE through a link whose own column was NULL. The walk is
    BACKWARD only -- a link is subject to what the turn it continues was bound
    to; a DESCENDANT's binding never reaches back to constrain its ancestor.

    Degrades to the flat read on any chain error, which is what a database
    predating the ``continues_handoff_id`` column (schema < v46) raises. Such a
    database holds no links at all, so the fallback loses nothing that exists;
    the walk only ever ADDS a constraint the flat read could not see.
    """
    if not contract_id:
        return None
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT plan_task_id FROM agent_contract_handoffs "
            "WHERE contract_id = ? LIMIT 1",
            (contract_id,),
        ).fetchone()
        if row is None:
            return None
        if row["plan_task_id"] is not None:
            return row["plan_task_id"]
        try:
            return _inherited_plan_task_id(con, contract_id)
        except Exception:
            return None
    finally:
        con.close()


def _inherited_plan_task_id(
    con: sqlite3.Connection, contract_id: str
) -> "int | None":
    """The nearest ANCESTOR binding of ``contract_id``'s continuation chain.

    Walks parent-ward from the row itself and returns the first non-NULL
    ``plan_task_id`` it meets, or None when the whole chain is unbound. Bounded
    by ``_MAX_CONTINUATION_LINKS`` and by a visited set, so a corrupt
    self-referential edge degrades to None instead of spinning inside a hook.
    """
    row = con.execute(
        "SELECT id, plan_task_id, continues_handoff_id "
        "FROM agent_contract_handoffs WHERE contract_id = ? LIMIT 1",
        (contract_id,),
    ).fetchone()
    seen: "set[int]" = set()
    for _ in range(_MAX_CONTINUATION_LINKS):
        if row is None:
            return None
        if row["plan_task_id"] is not None:
            return row["plan_task_id"]
        parent_id = row["continues_handoff_id"]
        if parent_id is None or parent_id in seen:
            return None
        seen.add(parent_id)
        row = con.execute(
            "SELECT id, plan_task_id, continues_handoff_id "
            "FROM agent_contract_handoffs WHERE id = ? LIMIT 1",
            (parent_id,),
        ).fetchone()
    return None


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

    def _work() -> int:
        con = _connect(db_path)
        try:
            # BEGIN IMMEDIATE: this write shares the contract/handoff path and
            # may run concurrently with a racing backstop finalize; take the
            # write lock up front so it waits (busy_timeout) rather than
            # contending for a lock upgrade.
            con.execute("BEGIN IMMEDIATE")
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

    return _retry_on_locked(_work)


def list_agent_contract_handoffs(
    *,
    workspace: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    brief_id: int | None = None,
    agent_state: str | None = None,
    contract_id: str | None = None,
    harness_agent_id: str | None = None,
    cut_reason: str | None = None,
    any_cut: bool = False,
    limit: int = 100,
    db_path: "Path | None" = None,
) -> list[dict]:
    """Query agent_contract_handoffs with optional filters.

    Args:
        workspace:   Filter by workspace name.
        agent_id:    Filter by agent identity string.
        session_id:  Filter by CLAUDE session ID.
        brief_id:    Filter by briefs.id FK.
        agent_state: Filter by resolved agent_state (turn status).
        contract_id: Filter by the T7 idempotency key (the CLI-minted
            draft/contract id) -- see finalize_agent_contract_handoff.
        harness_agent_id: Filter by the harness's per-run agent id (v40,
            stamped at SubagentStart -- see stamp_harness_agent_id). This is
            the recovery coordinate for a cut turn: the parent's Task result
            carries this id even when the subagent never finalized.
        cut_reason:  Filter by the exact v39 cut marker (never_finalized /
            reaped / backstop_capture / salvaged_truncation -- gaia.state.CUT_REASONS).
        any_cut:     Filter to every row that did NOT close cleanly, whatever the
            reason (``cut_reason IS NOT NULL``). Ignored when ``cut_reason`` names
            a specific reason. Both forms are SQL-side on purpose: the cut
            population is a minority of the table, so filtering after ``limit``
            would silently return a handful of rows and read as "almost nothing
            was cut". Served by idx_agent_contract_handoffs_cut, the partial
            index over exactly this population.
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
        if agent_state is not None:
            # v37: column renamed task_status -> agent_state (plan 34 task 3);
            # the keyword arg is now agent_state to match (plan 34 task 4).
            clauses.append("agent_state = ?")
            params.append(agent_state)
        if contract_id is not None:
            clauses.append("contract_id = ?")
            params.append(contract_id)
        if harness_agent_id is not None:
            clauses.append("harness_agent_id = ?")
            params.append(harness_agent_id)
        if cut_reason is not None:
            clauses.append("cut_reason = ?")
            params.append(cut_reason)
        elif any_cut:
            clauses.append("cut_reason IS NOT NULL")

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
        # Automatic retention (episodic-workflow-to-db growth control): once
        # the row is safely committed, occasionally prune episodes older than
        # the retention window. Gated to ~1/N inserts so it costs nothing on
        # the common turn; wrapped so a prune failure NEVER masks the
        # successful insert. See _maybe_prune_episodes / prune_episodes.
        _maybe_prune_episodes(db_path=db_path)
        return _applied({"episode_id": episode_id})
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Episode retention (automatic, DB-side)
# ---------------------------------------------------------------------------
#
# The ``episodes`` table had NO DB-side retention: the only retention that
# existed (bin/cli/cleanup.py, tools/memory/episodic.py.cleanup_old_episodes)
# operates on the LEGACY filesystem layout and explicitly does not touch
# gaia.db. Left unbounded, ``episodes`` grows without limit (13.7k rows /
# ~13MB observed in workspace 'me'), which also slows every full-table read.
#
# Policy: 90 days (user decision). ``prune_episodes`` is the testable unit --
# a single ``DELETE FROM episodes WHERE timestamp < cutoff``. The schema's
# ``episodes_ad`` AFTER DELETE trigger keeps ``episodes_fts`` synchronized, so
# no separate FTS cleanup is needed. Timestamps are stored as
# ``datetime.now(timezone.utc).isoformat()`` (fixed ``+00:00`` offset), so a
# lexicographic string comparison against a cutoff built the same way is
# correct.
#
# Trigger point: inside ``insert_episode`` (the sole SubagentStop write path),
# behind a probabilistic 1/N gate (_maybe_prune_episodes). This keeps the cost
# off the hot path -- a turn pays for the prune only ~1/N of the time -- while
# staying fully automatic with no new scheduler, hook, or cron. A closing hook
# was considered but rejected: SessionEnd fires just as often and would add a
# second write path to reason about.

EPISODE_RETENTION_DAYS = 90

# How often (1 in N) a successful insert triggers a prune sweep. Overridable
# via env for tests / tuning. A value <= 1 makes every insert prune (used by
# the regression test to force deterministic behavior).
_PRUNE_SAMPLE_RATE_DEFAULT = 50


def _prune_sample_rate(env_var: str = "GAIA_EPISODE_PRUNE_SAMPLE_RATE") -> int:
    """Resolve the 1-in-N prune sampling rate from ``env_var``.

    Shared by all DB-side retention gates (episodes, harness_events,
    agent_contract_handoffs); each passes its own env var so a test can force
    one table's gate deterministically without affecting the others. A value
    <= 1 makes every insert prune; a missing/malformed value falls back to
    ``_PRUNE_SAMPLE_RATE_DEFAULT``.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return _PRUNE_SAMPLE_RATE_DEFAULT
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return _PRUNE_SAMPLE_RATE_DEFAULT
    return val if val >= 1 else 1


def prune_episodes(
    cutoff_days: int = EPISODE_RETENTION_DAYS,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete ``episodes`` rows older than ``cutoff_days`` (default 90).

    Runs a single ``DELETE FROM episodes WHERE timestamp < ?``. The schema's
    ``episodes_ad`` AFTER DELETE trigger keeps ``episodes_fts`` consistent, so
    the FTS index is maintained automatically -- no manual FTS delete here.

    Returns ``{"status": "applied", "deleted": <n>, "cutoff": <iso>}`` on
    success, or ``{"status": "error", "reason": str}`` on failure.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).isoformat()
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                "DELETE FROM episodes WHERE timestamp < ?",
                (cutoff,),
            )
            deleted = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
        return {"status": "applied", "deleted": deleted, "cutoff": cutoff}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def _maybe_prune_episodes(db_path: Path | None = None) -> None:
    """Probabilistically run ``prune_episodes`` after a successful insert.

    Best-effort: any failure is swallowed so it can never mask the insert that
    just succeeded. Fires with probability ``1/_prune_sample_rate()``.
    """
    import random

    try:
        rate = _prune_sample_rate()
        if rate <= 1 or random.randint(1, rate) == 1:
            prune_episodes(db_path=db_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# harness_events retention (automatic, DB-side)
# ---------------------------------------------------------------------------
#
# ``harness_events`` is the append-only audit mirror written on every hook
# firing (write_harness_event); it had NO retention and grew unbounded (~11k
# rows already past the window / ~39% of the table observed in workspace 'me').
# Left unbounded it slows every full-table read and inflates the DB file.
#
# Policy: 90 days (user decision -- parity with episodes). ``prune_harness_events``
# is the testable unit: a single ``DELETE FROM harness_events WHERE ts < cutoff``.
# The ``ts`` column is written by _now_iso() (fixed ``...Z`` UTC format), so the
# cutoff MUST be built with the SAME strftime format -- NOT datetime.isoformat()
# (which episodes uses) -- for the lexicographic string comparison to be correct.
# harness_events has no FK and no child tables, so the DELETE needs no cascade.
#
# Trigger point: inside write_harness_event (the sole write path) behind the
# shared 1/N probabilistic gate, mirroring episodes -- automatic, no scheduler.

HARNESS_EVENT_RETENTION_DAYS = 90
_HARNESS_EVENT_PRUNE_ENV = "GAIA_HARNESS_EVENT_PRUNE_SAMPLE_RATE"


def _retention_cutoff_iso(cutoff_days: int) -> str:
    """Cutoff timestamp in the ``_now_iso()`` (``%Y-%m-%dT%H:%M:%SZ``) format.

    harness_events.ts and agent_contract_handoffs.created_at are both written by
    _now_iso(), so their retention cutoff must use the identical fixed-width UTC
    format for a lexicographic comparison to be valid. (episodes uses
    datetime.isoformat() with a ``+00:00`` offset and therefore builds its cutoff
    differently -- do not unify the two.)
    """
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=cutoff_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def prune_harness_events(
    cutoff_days: int = HARNESS_EVENT_RETENTION_DAYS,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete ``harness_events`` rows older than ``cutoff_days`` (default 90).

    Returns ``{"status": "applied", "deleted": <n>, "cutoff": <iso>}`` on
    success, or ``{"status": "error", "reason": str}`` on failure.
    """
    cutoff = _retention_cutoff_iso(cutoff_days)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                "DELETE FROM harness_events WHERE ts < ?",
                (cutoff,),
            )
            deleted = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
        return {"status": "applied", "deleted": deleted, "cutoff": cutoff}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def _maybe_prune_harness_events(db_path: Path | None = None) -> None:
    """Probabilistically prune harness_events after a successful insert.

    Best-effort: any failure is swallowed so it can never mask the insert that
    just succeeded. Fires with probability ``1/_prune_sample_rate(...)``.
    """
    import random

    try:
        rate = _prune_sample_rate(_HARNESS_EVENT_PRUNE_ENV)
        if rate <= 1 or random.randint(1, rate) == 1:
            prune_harness_events(db_path=db_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# agent_contract_handoffs retention (automatic, DB-side)
# ---------------------------------------------------------------------------
#
# ``agent_contract_handoffs`` persists one terminal contract row per finalized
# agent turn; it had NO retention and grew unbounded (~5.4k rows / ~51 days
# observed in workspace 'me'). Policy: 90 days (user decision -- parity with
# episodes).
#
# FK/cascade: agent_contract_handoff_approvals.handoff_id REFERENCES
# agent_contract_handoffs(id) ON DELETE CASCADE (schema.sql). _connect() sets
# ``PRAGMA foreign_keys = ON``, so deleting an expired handoff cascade-deletes
# its join rows in agent_contract_handoff_approvals -- exactly the desired
# behavior (the approval_grants rows themselves are NOT touched; only the join
# rows linking them to the pruned handoff are removed). No orphan rows are left.
#
# The ``created_at`` column is written by _now_iso() (``...Z`` UTC format), so
# the cutoff is built with the same strftime format via _retention_cutoff_iso.
#
# Trigger point: inside finalize_agent_contract_handoff -- the SOLE runtime
# write path (bin/cli/contract.py + hooks/modules/agents/handoff_persister.py);
# insert_agent_contract_handoff is legacy and has no non-test caller -- on the
# winner (created) branch after commit, behind the shared 1/N gate.

HANDOFF_RETENTION_DAYS = 90
_HANDOFF_PRUNE_ENV = "GAIA_HANDOFF_PRUNE_SAMPLE_RATE"


def prune_handoffs(
    cutoff_days: int = HANDOFF_RETENTION_DAYS,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete ``agent_contract_handoffs`` rows older than ``cutoff_days`` (90).

    Runs a single ``DELETE FROM agent_contract_handoffs WHERE created_at < ?``.
    With foreign_keys ON (set by _connect), the ON DELETE CASCADE on
    agent_contract_handoff_approvals.handoff_id removes the matching join rows
    automatically -- no separate cleanup needed.

    Returns ``{"status": "applied", "deleted": <n>, "cutoff": <iso>}`` on
    success, or ``{"status": "error", "reason": str}`` on failure.
    """
    cutoff = _retention_cutoff_iso(cutoff_days)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                "DELETE FROM agent_contract_handoffs WHERE created_at < ?",
                (cutoff,),
            )
            deleted = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
        return {"status": "applied", "deleted": deleted, "cutoff": cutoff}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def _maybe_prune_handoffs(db_path: Path | None = None) -> None:
    """Probabilistically prune agent_contract_handoffs after a successful write.

    Best-effort: any failure is swallowed so it can never mask the finalize that
    just succeeded. Fires with probability ``1/_prune_sample_rate(...)``.
    """
    import random

    try:
        rate = _prune_sample_rate(_HANDOFF_PRUNE_ENV)
        if rate <= 1 or random.randint(1, rate) == 1:
            prune_handoffs(db_path=db_path)
    except Exception:
        pass


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
