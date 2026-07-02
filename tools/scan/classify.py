"""
Deterministic scan classification -- the núcleo of ``gaia scan``.

This module replaces the historical inference layer (workspace-type detection,
nearest-installed-ancestor attribution, install-anchor demotion). Classification
is now DETERMINISTIC and driven by a single required parameter: the workspace
name ``W``. There is no guessing -- a repo's workspace is the ancestor path
segment that matches ``W``, and its project is the path segment immediately
before the repo.

THE RULESET (per repo -- a folder containing ``.git`` -- found by walking down
from ``root``):

  R1 repo      = basename of the folder holding ``.git``.
  R2 project   = the path segment immediately before the repo (its parent).
  R3 workspace = resolved by matching ``W`` against the repo's ancestor
                 segments. If ``W`` matches a segment -> valid. If it matches
                 NO segment -> error-as-text (structured, non-crashing, with a
                 suggestion). ``W`` is resolved per-repo with early-exit on
                 no-match (no git cost is paid for a repo that cannot match).
  R4 collapse  = if NOTHING is between the workspace segment and the repo (the
                 parent of the repo IS the workspace) -> project = repo name.
  R5 reconcile = upsert keyed by ``project_identity`` (writer identity-collapse
                 UPSERT); soft-delete scoped to the exact ``(workspace,
                 project)`` set discovered this run.
  R6 output    = always structured data (see :class:`ScanReport`). Non-crashing.

Principle: a workspace is never a project, but a project CAN be a workspace (the
same folder, its role decided by ``W``). Deeper-than-3 nesting -> project is the
segment just before the repo, and the extra levels are returned as ambiguity
DATA -- the scan never guesses which of them "should" have been the project.

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

from tools.scan.store_populator import (
    _list_repos,
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
    """

    repo: str
    path: str
    workspace: Optional[str] = None
    project: Optional[str] = None
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
      if not between:            project = repo_name        # R4 collapse
      else:                      project = between[-1]       # R2
                                 if len(between) > 1:        # deeper-than-3
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
    if not between:
        # R4 collapse: parent of repo IS the workspace -> project = repo name.
        project = repo_name
    else:
        project = between[-1]  # R2: segment immediately before the repo
        if len(between) > 1:
            # Deeper-than-3 nesting: return the extra levels as ambiguity DATA
            # (do not guess). extra_levels are the segments between the
            # workspace and the project (i.e. between[:-1]).
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
            ``{repo, project, workspace, project_identity, applied}``.
        errors: One dict per no-match repo: ``{repo, W, suggestion}`` (R3).
        ambiguities: One dict per deeper-than-3 repo:
            ``{repo, extra_levels}``.
        marked_missing: Count of project rows soft-marked missing during
            reconcile (R5). Zero in dry-run.
        error: A top-level error string when the scan could not run at all
            (e.g. no git repos under root). None on a normal run.
    """

    resolved_workspace: Optional[str] = None
    repos_found: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    ambiguities: list[dict] = field(default_factory=list)
    marked_missing: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "resolved_workspace": self.resolved_workspace,
            "repos_found": self.repos_found,
            "projects": self.projects,
            "errors": self.errors,
            "ambiguities": self.ambiguities,
            "marked_missing": self.marked_missing,
            "error": self.error,
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
) -> bool:
    """Persist one matched repo as a projects row (R5 upsert). Returns applied.

    Reuses the writer's identity-collapse UPSERT (keyed on ``project_identity``
    via the partial unique index) so the SAME physical repo scanned from
    different roots collapses to ONE row. group_name records the container
    directory between the workspace and the project, when there is one.
    """
    from gaia.store.writer import upsert_project

    repo_path = Path(classification.path)
    # group_name: the container directory between the workspace segment and the
    # project. When project == parent-of-repo (no collapse and no deeper
    # nesting) there is no container -> None. When collapsed (project == repo)
    # there is likewise no container.
    group_name = None
    if classification.ambiguity:
        # Deeper-than-3: the nearest extra level is the immediate container.
        extra = classification.ambiguity.get("extra_levels") or []
        group_name = extra[-1] if extra else None

    res = upsert_project(
        workspace=classification.workspace,
        name=classification.project,
        fields={
            "project_identity": classification.project_identity,
            "path": str(repo_path),
            "group_name": group_name,
            "status": "active",
            "missing_since": None,
        },
        agent=agent,
        db_path=db_path,
        workspace_path=repo_path,
    )
    return res.get("status") == "applied"


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

    report = ScanReport()
    for repo in repos:
        report.repos_found.append({"repo": repo.name, "path": str(repo)})

    if apply:
        _ensure_scan_permissions(db_path)

    # Track surviving projects per workspace so reconcile is scoped to the exact
    # (workspace, project) set this run discovered (R5).
    surviving_by_ws: dict[str, list[str]] = {}

    for repo in repos:
        c = classify_repo(repo, W)
        if not c.matched:
            report.errors.append(c.error)  # R3 no-match, early-exit already done
            continue

        report.resolved_workspace = c.workspace
        if c.ambiguity:
            report.ambiguities.append(c.ambiguity)

        applied = False
        if apply:
            applied = _upsert(c, agent=agent, db_path=db_path)
            surviving_by_ws.setdefault(c.workspace, []).append(c.project)

        report.projects.append({
            "repo": c.repo,
            "project": c.project,
            "workspace": c.workspace,
            "project_identity": c.project_identity,
            "applied": applied,
        })

    # R5 reconcile: soft-delete missing projects, scoped per (workspace,
    # project). Only workspaces we actually wrote to are reconciled -- we never
    # reach into a workspace this scan did not touch.
    if apply:
        for ws, projects in surviving_by_ws.items():
            report.marked_missing += _reconcile(ws, projects, db_path=db_path)
        from gaia.store.writer import set_workspace_last_scan_at
        if report.resolved_workspace:
            try:
                set_workspace_last_scan_at(
                    report.resolved_workspace, db_path=db_path
                )
            except Exception:  # pragma: no cover -- non-fatal
                pass

    return report
