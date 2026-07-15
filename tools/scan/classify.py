"""
Deterministic scan classification -- the núcleo of ``gaia scan``.

This module replaces the historical inference layer (workspace-type detection,
nearest-installed-ancestor attribution, install-anchor demotion). Classification
is now DETERMINISTIC and driven by a single required parameter: the workspace
name ``W``. There is no guessing -- a repo's workspace is the ancestor path
segment that matches ``W``, and the project NAME is the repo's own basename.

THE RULESET (per repo -- a folder containing ``.git`` -- found by walking down
from ``root``):

  R1 repo      = basename of the folder holding ``.git``. This is ALSO the
                 project NAME (the ``projects.name`` storage slot).
  R2 container = the path segment immediately before the repo (its parent),
                 when there is one between the workspace and the repo. Recorded
                 in the ``group_name`` column -- NOT used as the project name.
                 ``None`` when the repo sits directly under the workspace.
  R3 workspace = resolved by matching ``W`` against the repo's ancestor
                 segments. If ``W`` matches a segment -> valid. If it matches
                 NO segment -> error-as-text (structured, non-crashing, with a
                 suggestion). ``W`` is resolved per-repo with early-exit on
                 no-match (no git cost is paid for a repo that cannot match).
  R4 collapse  = if NOTHING is between the workspace segment and the repo (the
                 parent of the repo IS the workspace) -> container = None.
                 (The project name is the repo basename either way -- R2 and R4
                 are collapsed into the single rule "name = repo basename".)
  R5 reconcile = upsert keyed by ``project_identity`` (writer identity-collapse
                 UPSERT); soft-delete scoped to the exact ``(workspace,
                 name)`` set discovered this run.
  R6 output    = always structured data (see :class:`ScanReport`). Non-crashing.

Naming history: R2 previously used the *container* segment as the project NAME,
which made every repo under one container (e.g. ``aaxis/bildwiz/<repo>``)
collide on the single name ``bildwiz`` and forced the writer to disambiguate
with ``-2``/``-3`` suffixes -- opaque names (``bildwiz-7``) that lost the real
basename (``newco-pitot``). The name is now ALWAYS the repo basename, and the
container is preserved separately in ``group_name``. A genuine collision only
arises when two DIFFERENT physical repos share the SAME basename under one
workspace; the writer still disambiguates that (rarer) case.

Principle: a workspace is never a project, but a project CAN be a workspace (the
same folder, its role decided by ``W``). Deeper-than-3 nesting -> the container
is the segment just before the repo (its immediate parent), and the extra
levels are returned as ambiguity DATA -- the scan never guesses which of them
"should" have been the container.

Reused primitives (kept from the correct low-level layer):
  * ``_list_repos`` / ``_walk_for_repos`` / ``_REPO_WALK_SKIP`` -- the .git walk.
  * ``resolve_project_identity`` (git-common-dir) -- stable per-repo identity.
  * ``upsert_project`` (writer identity-collapse UPSERT) -- persistence.
  * ``mark_missing_in`` (writer survivor loop) -- reconcile / soft-delete.

Public API::

    ancestor_segments(repo) -> list[str]
    match_workspace_index(segs, W) -> int | None
    classify_repo(repo, W) -> RepoClassification
    scan(root, W, *, agent, db_path, apply) -> ScanReport
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tools.scan.role_detector import detect_role
from tools.scan.store_populator import (
    _git_remote_origin,
    _list_repos,
    _platform_from_remote,
    resolve_project_identity,
)


# ---------------------------------------------------------------------------
# Pure segment algorithm (R1-R4)
# ---------------------------------------------------------------------------

def ancestor_segments(repo: Path) -> list[str]:
    """Return the path segments of ``repo`` ending with the repo basename.

    The returned list is ``[.., grandparent, parent, repo_name]`` -- ordered
    root-to-leaf. ``segs[-1]`` is the repo (R1), ``segs[-2]`` is its parent
    (the R2 project candidate). Everything before ``segs[-1]`` is an ancestor
    segment eligible to match ``W`` (R3).

    Uses the resolved (absolute) parts so matching is done against real path
    segments, never against ``.`` or ``..`` tokens.
    """
    try:
        parts = list(Path(repo).resolve().parts)
    except (OSError, RuntimeError):
        parts = list(Path(repo).parts)
    # Drop the filesystem anchor (e.g. "/") -- it is never a matchable segment.
    if parts and parts[0] in ("/", "\\"):
        parts = parts[1:]
    # On some platforms the anchor is like "C:\\"; strip a trailing separator.
    parts = [p.rstrip("/\\") or p for p in parts]
    return parts


def match_workspace_index(segs: list[str], W: str) -> Optional[int]:
    """Return the index of the LAST ancestor segment matching ``W``, else None.

    Only the ancestor segments (``segs[:-1]``) are eligible -- the repo itself
    (``segs[-1]``) is never a workspace (R3, "a workspace is never a project"
    read from the repo side: the repo's own name cannot be the workspace).

    Matching is a two-tier contains/split test (R3 "contains/split"):
      1. Exact segment equality (``segment == W``) -- the strong signal.
      2. Exact equality after splitting ``W`` on the path separator, so a
         caller may pass a nested workspace token like ``"aaxis/aos"`` and it
         matches when those segments appear consecutively as ancestors.

    The LAST match wins so that when ``W`` appears more than once in the path,
    the deepest occurrence (nearest the repo) is chosen -- that is the most
    specific workspace boundary.
    """
    ancestors = segs[:-1]
    if not ancestors:
        return None

    # Tier 1: exact single-segment match, last occurrence.
    idx: Optional[int] = None
    for i, seg in enumerate(ancestors):
        if seg == W:
            idx = i
    if idx is not None:
        return idx

    # Tier 2: split W on the separator and match a consecutive run of ancestors.
    w_parts = [p for p in W.replace("\\", "/").split("/") if p]
    if len(w_parts) > 1:
        n = len(w_parts)
        last: Optional[int] = None
        for i in range(0, len(ancestors) - n + 1):
            if ancestors[i:i + n] == w_parts:
                last = i + n - 1  # index of the LAST segment of the run
        if last is not None:
            return last

    return None


@dataclass
class RepoClassification:
    """Outcome of classifying a single repo against ``W`` (R1-R4).

    Exactly one of ``project`` / ``error`` is populated:
      * matched   -> ``project`` set, ``error`` None. ``ambiguity`` may be set
                     when the repo nests deeper than 3 levels below ``W``.
      * no-match  -> ``project`` None, ``error`` set (R3 error-as-text).

    ``project`` is now ALWAYS the repo basename (R1) -- the ``projects.name``
    storage slot. ``container`` is the grouping folder between the workspace
    and the repo (R2), persisted in ``group_name``; it is ``None`` when the
    repo sits directly under the workspace (R4 collapse).
    """

    repo: str
    path: str
    workspace: Optional[str] = None
    project: Optional[str] = None
    container: Optional[str] = None
    project_identity: Optional[str] = None
    error: Optional[dict] = None
    ambiguity: Optional[dict] = None

    @property
    def matched(self) -> bool:
        return self.error is None


def _suggestion_for(segs: list[str], W: str) -> str:
    """Build a human suggestion when ``W`` matched no ancestor segment.

    Names the ancestor segments that WERE available so the user can pick a real
    one, and points at the immediate parent as the most likely intended value.
    """
    ancestors = segs[:-1]
    if not ancestors:
        return f"repo has no ancestor segments to match --workspace {W!r}"
    parent = ancestors[-1] if ancestors else None
    available = ", ".join(repr(a) for a in ancestors)
    hint = f"did you mean --workspace {parent!r}?" if parent else ""
    return (
        f"--workspace {W!r} matched no ancestor segment; "
        f"available segments: [{available}]. {hint}".strip()
    )


def classify_repo(repo: Path, W: str) -> RepoClassification:
    """Classify one repo against workspace name ``W`` (R1-R4). Never raises.

    Control flow (matches the ordered algorithm):
      segs = ancestor_segments(repo)
      idx  = match_workspace_index(segs, W)      # ancestors only
      if idx is None: -> no-match error (early exit, no git cost)
      between = segs[idx+1:-1]
      project = repo_name                         # R1: name = repo basename
      if not between:            container = None            # R4 collapse
      else:                      container = between[-1]      # R2 immediate parent
                                 if len(between) > 1:         # deeper-than-3
                                     ambiguity = between[:-1]
      identity = resolve_project_identity(repo)             # reuse
    """
    segs = ancestor_segments(repo)
    repo_name = segs[-1] if segs else Path(repo).name

    idx = match_workspace_index(segs, W)
    if idx is None:
        # R3 no-match: early exit, structured error, no git-common-dir cost.
        return RepoClassification(
            repo=repo_name,
            path=str(repo),
            error={
                "repo": repo_name,
                "W": W,
                "suggestion": _suggestion_for(segs, W),
            },
        )

    between = segs[idx + 1:-1]
    ambiguity: Optional[dict] = None
    # R1 collapse of R2+R4: the project NAME is ALWAYS the repo basename.
    project = repo_name
    if not between:
        # R4 collapse: parent of repo IS the workspace -> no container.
        container = None
    else:
        # R2: the immediate container is the segment right before the repo.
        container = between[-1]
        if len(between) > 1:
            # Deeper-than-3 nesting: return the levels ABOVE the immediate
            # container as ambiguity DATA (do not guess). extra_levels are the
            # segments between the workspace and the container (between[:-1]).
            ambiguity = {
                "repo": repo_name,
                "extra_levels": list(between[:-1]),
            }

    identity = resolve_project_identity(repo)
    return RepoClassification(
        repo=repo_name,
        path=str(repo),
        workspace=W,
        project=project,
        container=container,
        project_identity=identity,
        ambiguity=ambiguity,
    )


# ---------------------------------------------------------------------------
# Structured report (R6)
# ---------------------------------------------------------------------------

@dataclass
class ScanReport:
    """Structured, non-crashing result of a scan (R6).

    Attributes:
        resolved_workspace: The workspace name ``W`` once at least one repo
            matched it, else None (no repo matched -> pure error report).
        repos_found: One ``{"repo", "path"}`` dict per discovered git repo.
        projects: One dict per matched repo:
            ``{repo, project, container, workspace, project_identity, path,
            facets, applied}``. ``container`` is the proyecto level (shared
            across the repos of a multi-repo container; equals ``repo`` on R4
            collapse); ``project`` is the DB storage slot
            (collision-disambiguated); ``path`` is the repo's own absolute path
            (M2-T4/T5). ``facets`` is the repo's stack fingerprint (M3/T8,
            AC-6) -- a list of ``{scope, key, value}`` rows previewed on a
            dry-run and persisted to ``project_facets`` on apply.
        errors: One dict per no-match repo: ``{repo, W, suggestion}`` (R3).
        ambiguities: One dict per deeper-than-3 repo:
            ``{repo, extra_levels}``.
        warnings: One dict per would-be collision (M2-T6, AC-5). A collision
            is any repo whose requested project slot ``(workspace, project)``
            was already occupied by a DIFFERENT physical repo, forcing the
            writer to disambiguate the DB slot name. Under the pre-M1 model
            these repos silently overwrote each other; T1 stopped the data
            loss, and T6 makes the event VISIBLE instead of silent. Shape:
            ``{kind, repo, workspace, requested_project, assigned_project,
            path, message}``.
        marked_missing: Count of project rows soft-marked missing during
            reconcile (R5). Zero in dry-run. Kept for back-compat; the same
            information, per-row, is now also in ``vanished`` (SV2).
        facet_failures: One dict per repo whose facet persistence raised
            (M3/T8). Per-repo isolation: a facet write error is collected here
            and never aborts the scan. Empty on a clean run and always empty in
            dry-run (facets are only persisted on apply). Shape:
            ``{workspace, project, path, error}``.
        vanished: One dict per project row that would be / was marked missing
            during reconcile (R5, SV2). Shape: ``{workspace, project, path,
            project_identity, remote, missing_since}``. ``missing_since`` is
            ``None`` on a dry-run (nothing was written) and the persisted
            timestamp on apply.
        move_candidates: Detected repo moves (SV2) -- a project that vanished
            from this scan's workspace paired 1:1, by normalized git remote,
            with an ACTIVE project row in a DIFFERENT workspace, or a project
            just seen here paired 1:1 with a MISSING row elsewhere carrying the
            same remote. Anti-false-positive: a remote match against MORE than
            one candidate on the other side (e.g. two live clones of the same
            remote) is never emitted -- ambiguous pairings are not guessed.
            Shape: ``{from: {workspace, project, path}, to: {workspace,
            project, path}, signal, remote, confidence, reason}``.
            ``confidence`` is one of ``high`` / ``medium`` / ``low``.
        rename_candidates: Rows (SV2) where the physical folder's basename no
            longer matches the persisted project name -- scoped to R4-collapse
            repos (``container == repo``) so ordinary multi-level container
            layouts (R2, where project deliberately differs from the repo
            basename) never appear here. Typically produced by M1-T1
            collision-disambiguation (the slot was renamed to avoid clobbering
            a different physical repo). Shape: ``{workspace, project, repo,
            path, expected_name, reason}``.
        orphaned_autored: Detection-only (SV2, never mutates) list of vanished
            projects that carried an agent-authored ``description``, together
            with a count of the memory notes and open briefs still held by the
            affected workspace -- content that may now be orphaned and worth a
            human look before any move/archive decision. Shape: ``{workspace,
            project, path, project_identity, description, memory_count,
            brief_count, reason}``.
        diff: Summary counts for this run. Dry-run keys: ``would_create``,
            ``would_update``, ``would_move``, ``would_mark_missing``. Apply
            keys: ``did_create``, ``did_update``, ``did_move``,
            ``did_mark_missing``. ``*_move`` counts detected move_candidates
            (SV2 only ever detects/reports a move -- adjudication and the
            actual ``superseded_by`` write happen in SV4).
        mode: ``"dry-run"`` or ``"apply"`` -- mirrors the ``apply`` flag this
            report was produced with.
        error: A top-level error string when the scan could not run at all
            (e.g. no git repos under root). None on a normal run.
    """

    resolved_workspace: Optional[str] = None
    repos_found: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    ambiguities: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    marked_missing: int = 0
    facet_failures: list[dict] = field(default_factory=list)
    vanished: list[dict] = field(default_factory=list)
    move_candidates: list[dict] = field(default_factory=list)
    rename_candidates: list[dict] = field(default_factory=list)
    orphaned_autored: list[dict] = field(default_factory=list)
    diff: dict = field(default_factory=dict)
    mode: str = "dry-run"
    error: Optional[str] = None
    # Carrier for stage 3 (contract promotion). classify.scan() NEVER writes
    # this -- discovery stays a pure indexer. The CLI (bin/cli/scan.py) fills it
    # after scan by invoking the decoupled promotion stage (tools/scan/promote).
    promotion: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "resolved_workspace": self.resolved_workspace,
            "repos_found": self.repos_found,
            "projects": self.projects,
            "errors": self.errors,
            "ambiguities": self.ambiguities,
            "warnings": self.warnings,
            "marked_missing": self.marked_missing,
            "facet_failures": self.facet_failures,
            "vanished": self.vanished,
            "move_candidates": self.move_candidates,
            "rename_candidates": self.rename_candidates,
            "orphaned_autored": self.orphaned_autored,
            "diff": self.diff,
            "mode": self.mode,
            "error": self.error,
            "promotion": self.promotion,
        }


def error_report(message: str) -> ScanReport:
    """Return a structured error report (non-crashing)."""
    return ScanReport(error=message)


# ---------------------------------------------------------------------------
# The scan driver (R5 reconcile + R6 output)
# ---------------------------------------------------------------------------

# Agent identity used when the scan persists rows. Kept as a module constant so
# tests and callers refer to it by name.
SCAN_AGENT = "gaia-system"

# Tables the scan agent must be able to write for population to succeed.
_SCAN_TABLES = [
    "workspaces", "projects", "apps", "services", "libraries", "features",
    "project_facets",
    "integrations", "gaia_installations",
    "tf_modules", "tf_live", "releases", "workloads", "clusters_defined",
]


def _ensure_scan_permissions(db_path: Path | None) -> None:
    """Idempotently grant ``SCAN_AGENT`` write access on scanner tables.

    Without a grant, ``upsert_project`` returns ``{"status": "rejected"}`` and
    nothing persists. Safe to call repeatedly (INSERT OR REPLACE).
    """
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        for table in _SCAN_TABLES:
            con.execute(
                "INSERT OR REPLACE INTO agent_permissions "
                "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
                (table, SCAN_AGENT),
            )
        con.commit()
    except Exception:  # pragma: no cover -- non-fatal
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        con.close()


def _upsert(
    classification: RepoClassification,
    *,
    agent: str,
    db_path: Path | None,
    remote_url: str | None = None,
    primary_language: str | None = None,
) -> tuple[bool, str]:
    """Persist one matched repo as a projects row (R5 upsert).

    Returns ``(applied, final_name)``. ``final_name`` may differ from
    ``classification.project`` when the writer detected a genuine
    repo-name collision (AC-2, M1-T1): a DIFFERENT physical repo already
    occupying ``(workspace, classification.project)`` never gets silently
    overwritten -- the writer disambiguates instead.

    Reuses the writer's identity-collapse UPSERT (keyed on ``project_identity``
    via the partial unique index) so the SAME physical repo scanned from
    different roots collapses to ONE row. group_name records the container
    directory between the workspace and the project, when there is one.

    Args:
        remote_url: The repo's raw ``git remote get-url origin`` value (SV2),
            or None when unavailable. This is the base signal move-detection
            (SV2 ``move_candidates``) matches on: unlike ``project_identity``
            (git-common-dir), the remote survives a physical directory move.
    """
    from gaia.store.writer import upsert_project

    repo_path = Path(classification.path)
    # group_name: the container directory immediately between the workspace
    # segment and the repo (R2). ``None`` when the repo sits directly under the
    # workspace (R4 collapse -- no grouping folder). This is the repo's own
    # parent-folder name, matching store_populator.scan_workspace_to_store's
    # group_name semantics.
    group_name = classification.container

    res = upsert_project(
        workspace=classification.workspace,
        name=classification.project,
        fields={
            "project_identity": classification.project_identity,
            "path": str(repo_path),
            "group_name": group_name,
            "status": "active",
            "missing_since": None,
            # SV2: base signal for cross-workspace move detection. Written
            # even when None (mirrors store_populator.populate_project) --
            # a repo whose origin was removed reports that honestly rather
            # than leaving a stale value silently in place.
            "remote_url": remote_url,
            # Scan-owned scalars, populated deterministically for PARITY with
            # store_populator.populate_project (the sibling scan path). Both
            # are scan-owned (writer._PROJECTS_AGENT_OWNED == {"description"}),
            # so `gaia scan` must fill them or promotion carries NULLs into the
            # project_identity contract even when the data is on disk.
            #   * primary_language: the DOMINANT language, derived from the
            #     SAME scanner detection that produces the `language` facets
            #     (passed in by the caller so both come from ONE scan) -- so the
            #     scalar column can never drift from the multi-language
            #     `language` facets in project_facets, which enumerate ALL
            #     languages. None for an IaC-only repo (no language detected).
            #   * platform: derived from the git remote host (None when the
            #     repo has no origin remote -- honest NULL, not a bug).
            #   * role: deterministic disk-based classification; without it
            #     promote._apply_scan_owned never seeds the contract entry's
            #     `type` (promote.py seeds type from role when absent).
            "primary_language": primary_language,
            "platform": _platform_from_remote(remote_url),
            "role": detect_role(repo_path),
        },
        agent=agent,
        db_path=db_path,
        workspace_path=repo_path,
        # `gaia scan` IS the scan path -- structurally guarantee it can never
        # write a projects.* agent-owned column (M1-T2/T3).
        strip_agent_owned=True,
    )
    final_name = res.get("name") or classification.project
    return res.get("status") == "applied", final_name


def _reconcile(
    workspace: str,
    surviving_projects: list[str],
    *,
    db_path: Path | None,
) -> int:
    """Soft-delete projects for ``workspace`` not in ``surviving_projects`` (R5).

    Reuses the writer survivor loop (``mark_missing_in``), which is scoped
    strictly per workspace and only touches ``(workspace, project)`` rows whose
    project name is absent from the surviving set. Never destroys -- marks
    ``status='missing'`` so the row survives and is recoverable on reappearance.
    """
    from gaia.store.writer import mark_missing_in
    surviving_keys = [(p,) for p in surviving_projects]
    return mark_missing_in("projects", workspace, surviving_keys, db_path=db_path)


# ---------------------------------------------------------------------------
# SV2: cross-DB detection helpers (move / rename / orphaned-authored)
#
# All read-only. Every helper below is guarded by :func:`_db_file_exists` so a
# dry-run against a workspace that has never been scanned before touches
# nothing on disk -- mirrors the same "dry-run touches-nothing" guarantee
# `preview_project_name` already established (see its docstring): `_connect`
# runs schema.sql and MATERIALIZES the DB file the first time it opens a
# connection, so a read-only helper must never call it against a path that
# does not yet exist.
# ---------------------------------------------------------------------------

def _resolve_db_path(db_path: Path | None) -> Path:
    """Resolve the effective DB path the same way `_connect` would, without
    opening a connection (so existence can be checked first)."""
    if db_path is not None:
        return db_path
    from gaia.paths import db_path as _default_db_path
    return _default_db_path()


def _db_file_exists(db_path: Path | None) -> bool:
    return _resolve_db_path(db_path).exists()


def _identity_exists(project_identity: Optional[str], db_path: Path | None) -> bool:
    """Return True iff a projects row already carries ``project_identity``.

    Read-only. Powers the create-vs-update split in the ``diff`` block: a
    match means this scan's upsert will UPDATE an existing row in place
    (identity-collapse); no match means a brand new row is being created.
    """
    if not project_identity or not _db_file_exists(db_path):
        return False
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM projects WHERE project_identity = ?",
            (project_identity,),
        ).fetchone()
    finally:
        con.close()
    return row is not None


def _compute_vanished(
    workspace: str,
    surviving_projects: list[str],
    *,
    db_path: Path | None,
) -> list[dict]:
    """Return the rows in ``workspace`` that would-be/are newly marked missing.

    Read-only preview of what :func:`_reconcile` (``mark_missing_in``) is
    about to do: any row currently ``status='active'`` whose name is NOT in
    ``surviving_projects``. Rows already ``status='missing'`` are excluded --
    they vanished on a PRIOR scan, not this one. Returns ``[]`` when the DB
    file does not yet exist (nothing can have vanished from a DB that was
    never written).

    Each item carries the fields the SV2 report needs downstream (remote for
    move-candidate matching, description for orphaned-authored detection).
    """
    if not _db_file_exists(db_path):
        return []
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT name, path, project_identity, remote_url, description "
            "FROM projects WHERE workspace = ? AND status = 'active'",
            (workspace,),
        ).fetchall()
    finally:
        con.close()
    surviving = set(surviving_projects)
    return [
        {
            "name": row["name"],
            "path": row["path"],
            "project_identity": row["project_identity"],
            "remote": row["remote_url"],
            "description": row["description"],
        }
        for row in rows
        if row["name"] not in surviving
    ]


def _memory_and_brief_counts(workspace: str, db_path: Path | None) -> tuple[int, int]:
    """Return ``(memory_count, open_brief_count)`` for ``workspace``.

    Read-only, workspace-scoped (``memory.project_ref`` is added in SV1 but
    not yet populated -- see schema.sql -- so per-project attribution is
    deferred to SV3; this is the best-effort workspace-level signal for
    "does this workspace still hold authored context that referenced the
    vanishing project"). ``memory_count`` excludes tombstoned rows
    (``deleted_at IS NOT NULL``, SV3/v26) so it reflects live memory only --
    a soft-deleted note is no longer "authored context" worth flagging.
    Returns ``(0, 0)`` when the DB file does not exist.
    """
    if not _db_file_exists(db_path):
        return 0, 0
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        memory_count = con.execute(
            "SELECT COUNT(*) FROM memory WHERE workspace = ? "
            "AND deleted_at IS NULL", (workspace,)
        ).fetchone()[0]
        brief_count = con.execute(
            "SELECT COUNT(*) FROM briefs WHERE workspace = ? "
            "AND status NOT IN ('closed', 'archived')",
            (workspace,),
        ).fetchone()[0]
    finally:
        con.close()
    return memory_count, brief_count


def _find_remote_matches(
    remote_norm: str,
    *,
    exclude_workspace: str,
    status: str,
    db_path: Path | None,
) -> list[dict]:
    """Return ``{workspace, name, path}`` rows in OTHER workspaces whose
    normalized remote equals ``remote_norm`` and whose ``status`` matches.

    Read-only. Normalization happens in Python (not SQL) because
    ``projects.remote_url`` is stored raw/unnormalized (schema.sql) -- two
    remotes that normalize identically (e.g. ``git@github.com:org/repo.git``
    vs ``https://github.com/org/repo``) must still collapse to one match.
    Returns ``[]`` when the DB file does not yet exist.
    """
    if not remote_norm or not _db_file_exists(db_path):
        return []
    from gaia.project import _normalize_remote
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT workspace, name, path, remote_url FROM projects "
            "WHERE status = ? AND workspace != ? AND remote_url IS NOT NULL",
            (status, exclude_workspace),
        ).fetchall()
    finally:
        con.close()
    return [
        {"workspace": row["workspace"], "name": row["name"], "path": row["path"]}
        for row in rows
        if _normalize_remote(row["remote_url"]) == remote_norm
    ]


def _facet_target(
    project_identity: Optional[str],
    fallback_workspace: str,
    fallback_name: str,
    *,
    db_path: Path | None,
) -> tuple[str, str]:
    """Resolve the (workspace, name) where the canonical projects row lives.

    Facets are FK-bound to ``projects(workspace, name)``. Under M1-T1
    identity-collapse the persisted row for a repo may live under a DIFFERENT
    (workspace, name) than the one this scan run classified -- the same
    physical repo scanned from a second root under another workspace collapses
    onto its first-seen row. Writing facets to the classified (workspace,
    name) would then violate the FK. Look the row up by its stable
    ``project_identity`` and return that row's actual (workspace, name); fall
    back to the classified values when the identity is absent or unmatched
    (e.g. a brand-new row, or a legacy DB without the identity column).
    """
    if not project_identity:
        return fallback_workspace, fallback_name
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT workspace, name FROM projects WHERE project_identity = ?",
            (project_identity,),
        ).fetchone()
    finally:
        con.close()
    if row is not None:
        return row["workspace"], row["name"]
    return fallback_workspace, fallback_name


def scan(
    root: Path,
    W: str,
    *,
    agent: str = SCAN_AGENT,
    db_path: Path | None = None,
    apply: bool = True,
) -> ScanReport:
    """Classify + reconcile every repo under ``root`` against workspace ``W``.

    Implements the ordered algorithm end-to-end and returns a structured,
    non-crashing :class:`ScanReport` (R6).

    Args:
        root:    Directory to walk for git repos (the CLI positional ``root``).
        W:       REQUIRED workspace name to resolve per-repo (R3).
        agent:   Agent identity for the writer permission gate.
        db_path: Optional explicit DB path (tests MUST pass a temp DB).
        apply:   When False, classify only -- no DB writes (dry-run). The report
                 still lists projects/errors/ambiguities, with ``applied=False``.

    Returns:
        A :class:`ScanReport`. Never raises for classification issues -- a repo
        that does not match ``W`` becomes an entry in ``errors``; a repo nested
        deeper than 3 levels becomes an entry in ``ambiguities``.
    """
    repos = _list_repos(root)
    if not repos:
        return error_report(f"no git repos under root: {root}")

    report = ScanReport(mode="apply" if apply else "dry-run")
    for repo in repos:
        report.repos_found.append({"repo": repo.name, "path": str(repo)})

    if apply:
        _ensure_scan_permissions(db_path)

    # Track surviving projects per workspace so reconcile is scoped to the exact
    # (workspace, project) set this run discovered (R5). Populated for BOTH
    # apply and dry-run (SV2) so `_compute_vanished` can preview the R5 diff
    # without writing anything.
    surviving_by_ws: dict[str, list[str]] = {}

    # Per-workspace {name: project_identity} of names already resolved earlier
    # in THIS batch (M1-T1, AC-2). A real apply=True run commits each repo's
    # upsert sequentially, so a later repo in the batch already sees an
    # earlier repo's committed row when the writer checks for collisions --
    # but a dry-run writes nothing, so this in-memory map lets the preview
    # simulate the same sequential-commit visibility without touching the DB.
    claimed_by_ws: dict[str, dict[str, str]] = {}

    # SV2 diff counters (create vs update, both dry-run and apply).
    create_count = 0
    update_count = 0

    for repo in repos:
        c = classify_repo(repo, W)
        if not c.matched:
            report.errors.append(c.error)  # R3 no-match, early-exit already done
            continue

        report.resolved_workspace = c.workspace
        if c.ambiguity:
            report.ambiguities.append(c.ambiguity)

        ws_claims = claimed_by_ws.setdefault(c.workspace, {})

        # SV2: the repo's raw git remote -- the move-stable signal
        # (project_identity/git-common-dir changes when a repo is physically
        # moved; the remote does not). Read-only, cheap (short subprocess
        # timeout, never raises). Computed once here and reused both for the
        # persisted `remote_url` column (_upsert) and for this run's
        # move_candidates matching below.
        remote_url = _git_remote_origin(Path(c.path))

        # SV2: create-vs-update, decided BEFORE the write so dry-run and
        # apply agree on the same answer (read-only; see _identity_exists).
        existed_before = _identity_exists(c.project_identity, db_path)

        # M3/T8 (AC-6): compute the repo's stack fingerprint (languages,
        # frameworks with version, build tools, detected infra/deployment/
        # orchestration) as facet rows. This runs the read-only scanner
        # orchestrator over the repo and maps its output to
        # {scope, key, value} facets. Always full (no --deep mode) and
        # side-effect-free -- persistence happens only on apply, below.
        # Run the scanner orchestrator ONCE per repo and derive BOTH the facet
        # rows and the dominant primary_language from the SAME sections, so the
        # scalar projects.primary_language column can never drift from the
        # multi-language `language` facets (the bug this path historically had:
        # a Gemfile / subdir manifest yielded a language facet but a NULL
        # primary_language, because primary_language used a separate, incomplete
        # top-level-only probe instead of the scanner detection).
        from tools.scan.store_populator import (
            compute_stack_sections,
            primary_language_from_sections,
            stack_output_to_facets,
        )
        sections = compute_stack_sections(Path(c.path))
        facets = stack_output_to_facets(sections)
        primary_language = primary_language_from_sections(sections)

        applied = False
        if apply:
            applied, final_name = _upsert(
                c, agent=agent, db_path=db_path, remote_url=remote_url,
                primary_language=primary_language,
            )
            # Persist the scanner-owned facets for this repo. The facets FK
            # references projects(workspace, name), so they MUST be written to
            # the workspace/name where the canonical projects row ACTUALLY
            # lives -- which, under identity-collapse, may differ from
            # (c.workspace, final_name): the same physical repo scanned from a
            # second root under a different workspace collapses onto its
            # first-seen row (M1-T1 writer identity-collapse). Resolve the row
            # by its stable project_identity and target that (workspace, name).
            facet_ws, facet_name = _facet_target(
                c.project_identity, c.workspace, final_name, db_path=db_path
            )
            # Per-repo isolation (mirrors scan_workspace_to_store): a facet
            # write error must NEVER abort the whole scan -- collect it and
            # continue. The projects row itself is already committed above.
            from tools.scan.store_populator import populate_facets
            try:
                populate_facets(
                    facet_ws, facet_name, Path(c.path), agent,
                    db_path=db_path, facets=facets,
                )
            except Exception as exc:  # pragma: no cover -- non-fatal isolation
                report.facet_failures.append({
                    "workspace": facet_ws,
                    "project": facet_name,
                    "path": c.path,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        else:
            from gaia.store.writer import preview_project_name
            final_name = preview_project_name(
                c.workspace, c.project, c.project_identity,
                db_path=db_path, extra_claimed=ws_claims,
            )
        surviving_by_ws.setdefault(c.workspace, []).append(final_name)
        ws_claims[final_name] = c.project_identity

        if existed_before:
            update_count += 1
        else:
            create_count += 1

        # M2-T6 (AC-5): a would-be collision is now VISIBLE, not silent. When
        # the writer/preview had to disambiguate the DB slot (final_name differs
        # from the requested project), a DIFFERENT physical repo already held
        # ``(workspace, c.project)``. Under the pre-M1 model these repos
        # overwrote each other with no signal; T1 stopped the loss and T6
        # surfaces the event as an explicit warning so no colliding repo is
        # ever merged/renamed silently.
        if final_name != c.project:
            report.warnings.append({
                "kind": "repo_collision",
                "repo": c.repo,
                "workspace": c.workspace,
                "requested_project": c.project,
                "assigned_project": final_name,
                "path": c.path,
                "message": (
                    f"repo {c.repo!r} at {c.path} would collide on project "
                    f"slot ({c.workspace!r}, {c.project!r}) already held by a "
                    f"different repo; assigned distinct slot {final_name!r} "
                    f"instead of overwriting."
                ),
            })

        report.projects.append({
            "repo": c.repo,
            "project": final_name,
            # Vocabulary (workspace -> container -> repo):
            #   * ``project`` (above) is the DB storage slot ``projects.name``,
            #     which is now ALWAYS the repo basename (collision-disambiguated
            #     only when two DIFFERENT repos share a basename under one
            #     workspace).
            #   * ``container`` is the grouping folder between the workspace and
            #     the repo (persisted as ``group_name``). For a multi-repo
            #     container (N>1) every repo shares the same ``container`` (e.g.
            #     three repos under "desing-repos" all carry
            #     container="desing-repos"), so grouping the report by
            #     ``container`` yields "one container with >1 repo". For a repo
            #     directly under the workspace (R4 collapse) ``container`` is
            #     ``None`` -- there is no grouping folder.
            "container": c.container,
            "workspace": c.workspace,
            "project_identity": c.project_identity,
            # M2-T4 (AC-3): each repo carries its own absolute path, distinct
            # from the project/container grouping.
            "path": c.path,
            # SV2: raw git remote (None when unavailable). The move-stable
            # signal -- see ScanReport.move_candidates.
            "remote": remote_url,
            # M3/T8 (AC-6): the stack fingerprint persisted for this repo as
            # rows in project_facets. On a dry-run this is the PREVIEW of what
            # would be written (nothing is persisted); on apply it is exactly
            # what populate_facets wrote. Each item is {scope, key, value}.
            "facets": facets,
            "applied": applied,
        })

        # SV2 rename_candidates: the project name is now ALWAYS the repo
        # basename, so ``persisted name == folder basename`` is a global
        # invariant. A mismatch means M1-T1 collision-disambiguation (two
        # different repos share a basename under one workspace) -- or a
        # legacy/pre-move row -- is masking the physical folder's real name.
        if Path(c.path).name != final_name:
            report.rename_candidates.append({
                "workspace": c.workspace,
                "project": final_name,
                "repo": c.repo,
                "path": c.path,
                "expected_name": Path(c.path).name,
                "reason": (
                    f"folder basename {Path(c.path).name!r} at {c.path} does "
                    f"not match the persisted project name {final_name!r} -- "
                    f"review whether this is a rename or a genuine collision."
                ),
            })

        # SV2 move_candidates (direction: appeared here <- missing elsewhere).
        # Anti-false-positive: only when EXACTLY one missing row elsewhere
        # shares the same normalized remote (see _find_remote_matches).
        if remote_url:
            from gaia.project import _normalize_remote
            remote_norm = _normalize_remote(remote_url)
            if remote_norm:
                missing_matches = _find_remote_matches(
                    remote_norm, exclude_workspace=c.workspace,
                    status="missing", db_path=db_path,
                )
                if len(missing_matches) == 1:
                    src = missing_matches[0]
                    report.move_candidates.append({
                        "from": {
                            "workspace": src["workspace"],
                            "project": src["name"],
                            "path": src["path"],
                        },
                        "to": {
                            "workspace": c.workspace,
                            "project": final_name,
                            "path": c.path,
                        },
                        "signal": "remote",
                        "remote": remote_norm,
                        "confidence": "high",
                        "reason": (
                            f"{final_name!r} in workspace {c.workspace!r} shares "
                            f"remote {remote_norm!r} with {src['name']!r}, "
                            f"currently missing in workspace {src['workspace']!r} "
                            f"-- likely moved here."
                        ),
                    })

    # SV2: vanished / move_candidates (direction: vanished here -> active
    # elsewhere) / orphaned_autored. Computed for every workspace this scan
    # touched (dry-run AND apply), BEFORE _reconcile actually writes anything,
    # so the same code produces the dry-run preview and the apply record.
    for ws, projects in surviving_by_ws.items():
        vanished_rows = _compute_vanished(ws, projects, db_path=db_path)
        for row in vanished_rows:
            report.vanished.append({
                "workspace": ws,
                "project": row["name"],
                "path": row["path"],
                "project_identity": row["project_identity"],
                "remote": row["remote"],
                "missing_since": None,  # filled in below when apply=True
            })

            if row["remote"]:
                from gaia.project import _normalize_remote
                remote_norm = _normalize_remote(row["remote"])
                if remote_norm:
                    active_matches = _find_remote_matches(
                        remote_norm, exclude_workspace=ws,
                        status="active", db_path=db_path,
                    )
                    if len(active_matches) == 1:
                        dst = active_matches[0]
                        report.move_candidates.append({
                            "from": {
                                "workspace": ws,
                                "project": row["name"],
                                "path": row["path"],
                            },
                            "to": {
                                "workspace": dst["workspace"],
                                "project": dst["name"],
                                "path": dst["path"],
                            },
                            "signal": "remote",
                            "remote": remote_norm,
                            "confidence": "high",
                            "reason": (
                                f"{row['name']!r} vanished from workspace "
                                f"{ws!r} and an active project with the same "
                                f"remote ({remote_norm}) exists at "
                                f"{dst['workspace']!r}/{dst['name']!r} -- "
                                f"likely moved there."
                            ),
                        })

            # SV2 orphaned_autored: detect-and-report only, never mutates.
            if row["description"]:
                mem_count, brief_count = _memory_and_brief_counts(ws, db_path)
                report.orphaned_autored.append({
                    "workspace": ws,
                    "project": row["name"],
                    "path": row["path"],
                    "project_identity": row["project_identity"],
                    "description": row["description"],
                    "memory_count": mem_count,
                    "brief_count": brief_count,
                    "reason": (
                        f"project {row['name']!r} vanished with an "
                        f"agent-authored description; workspace {ws!r} still "
                        f"holds {mem_count} memory note(s) and {brief_count} "
                        f"open brief(s) that may reference it -- review "
                        f"before any move/archive decision."
                    ),
                })

        # R5 reconcile: soft-delete missing projects, scoped per (workspace,
        # project). Only performed on apply -- dry-run already recorded the
        # preview above via `vanished`, with `missing_since=None`.
        if apply:
            report.marked_missing += _reconcile(ws, projects, db_path=db_path)

    if apply and report.vanished:
        # Refresh `missing_since` now that _reconcile has actually written it.
        from gaia.store.writer import _connect
        con = _connect(db_path)
        try:
            for v in report.vanished:
                row = con.execute(
                    "SELECT missing_since FROM projects "
                    "WHERE workspace = ? AND name = ?",
                    (v["workspace"], v["project"]),
                ).fetchone()
                if row is not None:
                    v["missing_since"] = row["missing_since"]
        finally:
            con.close()

    if apply:
        from gaia.store.writer import set_workspace_last_scan_at
        if report.resolved_workspace:
            try:
                set_workspace_last_scan_at(
                    report.resolved_workspace, db_path=db_path
                )
            except Exception:  # pragma: no cover -- non-fatal
                pass
        report.diff = {
            "did_create": create_count,
            "did_update": update_count,
            "did_move": len(report.move_candidates),
            "did_mark_missing": report.marked_missing,
        }
    else:
        report.diff = {
            "would_create": create_count,
            "would_update": update_count,
            "would_move": len(report.move_candidates),
            "would_mark_missing": len(report.vanished),
        }

    return report
