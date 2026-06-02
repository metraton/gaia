"""
Store Populator -- Scanner-side adapter to the Gaia SQLite substrate.

Replaces the legacy "produce JSON sections" path. Each populator function
inspects a repository directory (or a workspace of repos) and emits CRUD
operations through the gaia.store API:

    upsert_project, upsert_app, bulk_upsert, delete_missing_in

The populators NEVER touch agent-owned columns. They only set scanner-owned
columns; the store API protects agent fields by listing scanner columns
explicitly in its UPSERT statements.

Identity resolution: for each project path, identity is resolved via
``gaia.project.current(project_path)`` (B0). This means two clones of the same
remote on different machines collapse to the same workspace identity row.

Public API::

    populate_project(workspace, project_path, agent, *, db_path=None) -> dict
    populate_infrastructure(workspace, project, project_path, agent, *, db_path=None) -> dict
    populate_orchestration(workspace, project, project_path, agent, *, db_path=None) -> dict
    populate_features(workspace, project, project_path, agent, *, db_path=None) -> dict
    scan_workspace_to_store(workspace, root, agent, *, db_path=None) -> dict

Each function returns ``{"applied": int, "rejected": int, "deleted": int,
"identity": str}`` so callers can audit the effect.

This module produces NO JSON output. The legacy scanners keep working for
back-compat test suites, but the canonical scan loop calls these functions
to mutate the SQLite store directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from tools.scan.role_detector import detect_role


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

def resolve_identity(project_path: Path) -> str:
    """Resolve workspace identity from the git remote of `project_path` via B0.

    Returns:
        Canonical identity string (host/owner/repo) or the path basename
        when no remote is detected. Never empty, never raises.
    """
    from gaia.project import current
    return current(cwd=project_path)


# ---------------------------------------------------------------------------
# Repo-level populator
# ---------------------------------------------------------------------------

def populate_project(
    workspace: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
    project_name: str | None = None,
    group_name: str | None = None,
) -> dict:
    """Detect role + remote of a project and persist to the `projects` table.

    Args:
        workspace: Workspace identity (workspaces.name).
        project_path: Absolute path to the project root.
        agent: Agent name (used by the permission matrix in the store).
        db_path: Optional explicit DB path (test override).
        project_name: Override for the project basename. When None, uses
            project_path.name.
        group_name: Container directory name when the repo is nested under a
            grouping directory (e.g. ``"github-repos"``, ``"bildwiz"``).
            Pass ``None`` when the repo sits directly at the workspace root.

    Returns:
        Dict with keys ``applied`` (1|0), ``rejected`` (1|0), ``role``,
        ``identity``, ``name``, and ``group_name``. Never raises.
    """
    from gaia.store import upsert_project

    name = project_name or project_path.name
    role = detect_role(project_path)
    identity = resolve_identity(project_path)
    remote_url = _git_remote_origin(project_path)
    platform = _platform_from_remote(remote_url)
    primary_language = _detect_primary_language(project_path)

    res = upsert_project(
        workspace=workspace,
        name=name,
        fields={
            "role": role,
            "remote_url": remote_url,
            "platform": platform,
            "primary_language": primary_language,
            "group_name": group_name,
            # Persist the on-disk path so the project is directly findable
            # (project -> path + workspace). The value is the project root
            # itself; ``upsert_project`` accepts it via ``fields["path"]``.
            "path": str(project_path),
            # Reactivation (soft-delete, AC-4): a project discovered on disk is
            # LIVE. Set status='active' and missing_since=NULL explicitly so a
            # previously-missing project is reactivated on reappearance, rather
            # than relying on the writer's defaults.
            "status": "active",
            "missing_since": None,
        },
        agent=agent,
        db_path=db_path,
        workspace_path=project_path,
    )
    applied = 1 if res.get("status") == "applied" else 0
    return {
        "applied": applied,
        "rejected": 1 - applied,
        "role": role,
        "identity": identity,
        "name": name,
        "group_name": group_name,
    }


# ---------------------------------------------------------------------------
# Infrastructure-side populator (tf_modules, tf_live, clusters)
# ---------------------------------------------------------------------------

def populate_infrastructure(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist Terraform / cluster discoveries for a project into the store.

    Reads `project_path` for *.tf, terragrunt.hcl, and live/ directories.
    Calls ``bulk_upsert('tf_modules', ...)``, ``bulk_upsert('tf_live', ...)``,
    and ``bulk_upsert('clusters_defined', ...)`` for what was found, then
    calls ``delete_missing_in`` to prune stale rows for this project.

    Returns:
        Dict with applied/rejected/deleted counts per table.
    """
    from gaia.store import bulk_upsert, delete_missing_in

    tf_modules = _scan_tf_modules(project_path)
    tf_live = _scan_tf_live(project_path)
    clusters_defined = _scan_clusters_defined(project_path)

    out = {"tf_modules": {}, "tf_live": {}, "clusters_defined": {}}

    # tf_modules
    rows_tm = [
        {"project": project, "name": m["name"], "source": m.get("source"),
         "version": m.get("version"), "scanner_ts": _now_iso()}
        for m in tf_modules
    ]
    if rows_tm:
        out["tf_modules"]["upsert"] = bulk_upsert(
            "tf_modules", workspace, rows_tm, agent, db_path=db_path
        )
    surviving_tm = [(project, m["name"]) for m in tf_modules]
    out["tf_modules"]["deleted"] = _safe_delete_missing(
        "tf_modules", workspace, project, surviving_tm, db_path
    )

    # tf_live
    rows_tl = [
        {"project": project, "name": l["name"], "kind": l.get("kind"),
         "attributes": l.get("attributes"), "scanner_ts": _now_iso()}
        for l in tf_live
    ]
    if rows_tl:
        out["tf_live"]["upsert"] = bulk_upsert(
            "tf_live", workspace, rows_tl, agent, db_path=db_path
        )
    surviving_tl = [(project, l["name"]) for l in tf_live]
    out["tf_live"]["deleted"] = _safe_delete_missing(
        "tf_live", workspace, project, surviving_tl, db_path
    )

    # clusters_defined
    rows_cd = [
        {"project": project, "name": c["name"], "provider": c.get("provider"),
         "region": c.get("region"), "scanner_ts": _now_iso()}
        for c in clusters_defined
    ]
    if rows_cd:
        out["clusters_defined"]["upsert"] = bulk_upsert(
            "clusters_defined", workspace, rows_cd, agent, db_path=db_path
        )
    surviving_cd = [(project, c["name"]) for c in clusters_defined]
    out["clusters_defined"]["deleted"] = _safe_delete_missing(
        "clusters_defined", workspace, project, surviving_cd, db_path
    )

    return out


# ---------------------------------------------------------------------------
# Orchestration-side populator (releases, workloads, clusters_defined)
# ---------------------------------------------------------------------------

def populate_orchestration(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist GitOps / workload discoveries for a project into the store."""
    from gaia.store import bulk_upsert

    releases = _scan_releases(project_path)
    workloads = _scan_workloads(project_path)

    out = {"releases": {}, "workloads": {}}

    rows_r = [
        {"project": project, "name": r["name"], "released_at": r.get("released_at"),
         "scanner_ts": _now_iso()}
        for r in releases
    ]
    if rows_r:
        out["releases"]["upsert"] = bulk_upsert(
            "releases", workspace, rows_r, agent, db_path=db_path
        )
    surviving_r = [(project, r["name"]) for r in releases]
    out["releases"]["deleted"] = _safe_delete_missing(
        "releases", workspace, project, surviving_r, db_path
    )

    rows_w = [
        {"project": project, "name": w["name"], "kind": w.get("kind"),
         "namespace": w.get("namespace"), "cluster": w.get("cluster"),
         "scanner_ts": _now_iso()}
        for w in workloads
    ]
    if rows_w:
        out["workloads"]["upsert"] = bulk_upsert(
            "workloads", workspace, rows_w, agent, db_path=db_path
        )
    surviving_w = [(project, w["name"]) for w in workloads]
    out["workloads"]["deleted"] = _safe_delete_missing(
        "workloads", workspace, project, surviving_w, db_path
    )

    return out


# ---------------------------------------------------------------------------
# Features-side populator
# ---------------------------------------------------------------------------

def populate_features(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist feature-unit discoveries for a repo into the ``features`` table.

    Feature detection heuristic (in priority order):

    1. **``features/`` directory** -- any subdirectory directly under
       ``{project_path}/features/`` is treated as a feature unit. This covers the
       canonical ``qxo-monorepo`` layout where each feature lives in its own
       package directory (e.g. ``features/auth-feature``,
       ``features/orders-feature``).
    2. **Feature descriptor files** -- any ``feature.json`` or
       ``feature.yaml``/``feature.yml`` file found anywhere under the project
       (excluding ``node_modules``, ``.git``, ``__pycache__``) is treated as a
       feature descriptor; its parent directory name becomes the feature name.
    3. **LaunchDarkly / OpenFeature flags** -- ``flags.json`` or
       ``flags.yaml`` at the project root is parsed for top-level keys, each of
       which becomes a feature row.

    Decision rationale: these three heuristics cover the most common
    feature-organisation patterns observed in the real workspaces (qxo uses
    pattern 1; the others serve generic projects). Scanner-owned columns only
    (``name``, ``scanner_ts``); agent-owned columns (``status``,
    ``description``) are never touched.

    Returns:
        Dict with ``features`` sub-key containing ``upsert`` and ``deleted``
        counts.
    """
    from gaia.store import bulk_upsert, delete_missing_in

    features = _scan_features(project_path)
    out: dict = {"features": {}}

    rows_f = [
        {"project": project, "name": f["name"], "scanner_ts": _now_iso()}
        for f in features
    ]
    if rows_f:
        out["features"]["upsert"] = bulk_upsert(
            "features", workspace, rows_f, agent, db_path=db_path
        )
    surviving_f = [(project, f["name"]) for f in features]
    out["features"]["deleted"] = _safe_delete_missing(
        "features", workspace, project, surviving_f, db_path
    )
    return out


# ---------------------------------------------------------------------------
# Apps populator (B2 gap fix: scanners-populate-workspace-model)
# ---------------------------------------------------------------------------

def populate_apps(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist app-unit discoveries for a project into the ``apps`` table.

    App detection heuristic (in priority order):

    1. **``apps/`` directory** -- any subdirectory directly under
       ``{project_path}/apps/`` is treated as a deployable app. This covers the
       canonical monorepo layout (qxo-monorepo, bildwiz-platform-style).
       Scanner-owned ``kind`` is inferred from marker files inside the
       subdirectory (``Dockerfile`` / ``docker-compose*.yml`` -> ``"service"``;
       fallback ``"app"``).
    2. **Single-repo deployable** -- if the project root has a ``package.json``
       AND the project role is ``"application"`` AND no ``apps/`` directory was
       found, the project itself becomes one app row keyed by the package name
       (or project basename as fallback).

    Scanner-owned columns only (``name``, ``kind``, ``scanner_ts``);
    agent-owned columns (``description``, ``status``) are never touched.

    Returns:
        Dict with ``apps`` sub-key containing ``upsert`` and ``deleted``
        counts.
    """
    from gaia.store import bulk_upsert

    apps = _scan_apps(project_path)
    out: dict = {"apps": {}}

    rows_a = [
        {
            "project": project,
            "name": a["name"],
            "kind": a.get("kind"),
            "scanner_ts": _now_iso(),
        }
        for a in apps
    ]
    if rows_a:
        out["apps"]["upsert"] = bulk_upsert(
            "apps", workspace, rows_a, agent, db_path=db_path
        )
    surviving_a = [(project, a["name"]) for a in apps]
    out["apps"]["deleted"] = _safe_delete_missing(
        "apps", workspace, project, surviving_a, db_path
    )
    return out


# ---------------------------------------------------------------------------
# Services populator (B2 gap fix: scanners-populate-workspace-model)
# ---------------------------------------------------------------------------

def populate_services(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist infrastructure-service discoveries for a project into ``services``.

    Service detection heuristic (in priority order):

    1. **``services/`` directory** -- any subdirectory directly under
       ``{project_path}/services/`` is treated as a service unit.
    2. **docker-compose top-level services** -- ``docker-compose.yml`` /
       ``docker-compose.yaml`` / ``docker-compose-*.yml`` at the project root:
       parse the top-level ``services:`` mapping and emit one row per service
       key. ``kind`` is inferred from the image name (``postgres``/``mysql``
       -> ``"database"``; ``redis``/``memcached`` -> ``"cache"``;
       ``rabbitmq``/``kafka`` -> ``"queue"``; otherwise ``"api"``).

    Scanner-owned columns only (``name``, ``kind``, ``scanner_ts``);
    agent-owned columns (``description``, ``status``) are never touched.

    Returns:
        Dict with ``services`` sub-key containing ``upsert`` and ``deleted``
        counts.
    """
    from gaia.store import bulk_upsert

    services = _scan_services(project_path)
    out: dict = {"services": {}}

    rows_s = [
        {
            "project": project,
            "name": s["name"],
            "kind": s.get("kind"),
            "scanner_ts": _now_iso(),
        }
        for s in services
    ]
    if rows_s:
        out["services"]["upsert"] = bulk_upsert(
            "services", workspace, rows_s, agent, db_path=db_path
        )
    surviving_s = [(project, s["name"]) for s in services]
    out["services"]["deleted"] = _safe_delete_missing(
        "services", workspace, project, surviving_s, db_path
    )
    return out


# ---------------------------------------------------------------------------
# Libraries populator (B2 gap fix: scanners-populate-workspace-model)
# ---------------------------------------------------------------------------

def populate_libraries(
    workspace: str,
    project: str,
    project_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist library/package discoveries for a project into ``libraries``.

    Scope decision: ``libraries`` is interpreted as **workspace-internal
    shared packages** (NOT external npm/pypi dependencies). This matches the
    schema -- ``libraries`` shares (project, name) PK with ``apps`` /
    ``services`` and carries ``version`` + ``language``, which only makes
    sense for packages owned by the workspace.

    Library detection heuristic (in priority order):

    1. **``packages/`` directory** -- any subdirectory directly under
       ``{project_path}/packages/`` is treated as a workspace package
       (pnpm/yarn/npm workspace convention used by bildwiz-platform).
       Reads ``package.json`` inside each subdir for ``name`` and ``version``.
    2. **``libs/`` or ``libraries/`` directory** -- alternative monorepo
       conventions. Same per-subdir pattern.
    3. **package.json with workspaces** -- if the project root has
       ``package.json`` with a ``"workspaces"`` field, glob each pattern
       (e.g. ``packages/*``) and emit one row per matched directory's
       ``package.json`` ``name``.

    Each row records ``name`` (from package.json or dir basename),
    ``version`` (from package.json), ``language`` (currently always
    ``"javascript"`` since the heuristic targets JS/TS monorepos --
    Python/Rust/etc. equivalents can be added in a follow-up).

    Scanner-owned columns only (``name``, ``version``, ``language``,
    ``scanner_ts``).

    Returns:
        Dict with ``libraries`` sub-key containing ``upsert`` and ``deleted``
        counts.
    """
    from gaia.store import bulk_upsert

    libraries = _scan_libraries(project_path)
    out: dict = {"libraries": {}}

    rows_l = [
        {
            "project": project,
            "name": l["name"],
            "version": l.get("version"),
            "language": l.get("language"),
            "scanner_ts": _now_iso(),
        }
        for l in libraries
    ]
    if rows_l:
        out["libraries"]["upsert"] = bulk_upsert(
            "libraries", workspace, rows_l, agent, db_path=db_path
        )
    surviving_l = [(project, l["name"]) for l in libraries]
    out["libraries"]["deleted"] = _safe_delete_missing(
        "libraries", workspace, project, surviving_l, db_path
    )
    return out


# ---------------------------------------------------------------------------
# Gaia installations populator (B5 gap fix: rescan-real-workspaces-with-backup)
# ---------------------------------------------------------------------------

def populate_gaia_installations(
    workspace: str,
    workspace_root: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Detect Gaia installations in the workspace and persist them.

    PK is (project, machine), so each detection produces ONE row keyed by
    the local hostname.

    Detection heuristic (in priority order):

    1. **``node_modules/@jaguilar87/gaia/package.json``** -- canonical npm
       install. Reads the version field. ``install_mode = "npm"``.
    2. **``.claude/skills/`` + ``.claude/agents/`` present** -- Gaia
       footprint without a node_modules entry (e.g. dev symlink). Version
       comes from ``.claude/.gaia-version`` if present, otherwise ``None``.
       ``install_mode = "dev"`` when a symlink is detected, else
       ``"unknown"``.

    Note: this populator runs once per workspace (NOT per repo). It is
    invoked from ``scan_workspace_to_store`` after the repo loop.

    Returns:
        Dict with ``gaia_installations`` sub-key containing ``upsert`` and
        ``deleted`` counts (deleted is 0 -- this populator does not prune
        cross-machine rows).
    """
    from gaia.store import bulk_upsert

    installations = _scan_gaia_installations(workspace_root)
    out: dict = {"gaia_installations": {}}

    rows_g = [
        {
            "machine": g["machine"],
            "version": g.get("version"),
            "install_mode": g.get("install_mode"),
            "scanner_ts": _now_iso(),
        }
        for g in installations
    ]
    if rows_g:
        out["gaia_installations"]["upsert"] = bulk_upsert(
            "gaia_installations", workspace, rows_g, agent, db_path=db_path
        )
    # Intentional: do NOT prune other machines' rows (PK is per-machine).
    out["gaia_installations"]["deleted"] = 0
    return out


# ---------------------------------------------------------------------------
# Workspace scan loop
# ---------------------------------------------------------------------------

def scan_workspace_to_store(
    workspace: str,
    root: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Walk a workspace root, populate repo + infra + orchestration rows.

    Args:
        workspace: Workspace identity (projects.name).
        root: Workspace root containing one or more repo subdirectories.
            When `root` itself is a single repo, it is treated as the only
            repo.
        agent: Agent name for permission enforcement.
        db_path: Optional explicit DB path (test override).

    Canonical classification rule (gaia-scan-overhaul):

    * A **project** is a dir with ``.git`` (discovered by :func:`_list_repos`).
    * A **workspace** is a dir with a Gaia installation, detected by the
      mode-agnostic ``.claude/plugin-registry.json`` signal
      (:func:`_is_installed_gaia_workspace`). The CLI ``root`` is always a
      workspace too (the caller resolved its identity).
    * Intermediate folders (no ``.git``, no install) are NOT entities; their
      projects are attributed to the **nearest installed-ancestor workspace**
      within the scanned tree, with ``group_name`` = the container directory
      between that workspace and the project. Depth is variable.
    * A dir may be BOTH a project and a workspace -> it produces a ``projects``
      row (under its nearest installed ANCESTOR, or ``root`` as fallback) AND
      a ``workspaces`` / ``gaia_installations`` row of its own.
    * When no installed ancestor exists in the tree, the project falls back to
      the CLI-``root`` workspace (previous behaviour).

    Returns:
        Dict mapping ``"<workspace>/<project>"`` keys to per-repo result dicts,
        plus a ``__workspace__`` key for workspace-scoped populators
        (``gaia_installations``) of every detected installed workspace, plus a
        ``__failures__`` key holding a list of per-project failure dicts
        (``{"workspace", "project", "path", "error"}``) for repos whose
        population raised -- the scan isolates each repo so one failure does not
        abort the whole scan (AC-4, soft-delete).
    """
    project_dirs = _list_repos(root)

    # Detect installed sub-workspaces in the tree (root included). Each becomes
    # an attribution anchor: a project is owned by the nearest one above it.
    installed_workspaces = _list_installed_workspaces(root)
    installed_set: set[Path] = set(installed_workspaces)
    # The CLI root is always a workspace anchor even if it lacks the registry
    # signal (the caller already resolved/owns its identity).
    installed_set.add(root)

    # Map each installed-workspace path -> its workspace identity. The root
    # uses the caller-provided ``workspace`` name verbatim; detected children
    # resolve identity from their own path (git remote or basename).
    ws_name_by_path: dict[Path, str] = {root: workspace}
    for ws_path in installed_workspaces:
        if ws_path == root:
            continue
        ws_name_by_path[ws_path] = resolve_identity(ws_path)

    results: dict = {}
    # Per-project isolation (AC-4): a single problematic repo (bad column,
    # permissions, malformed content) must NOT abort the whole scan. Each
    # project is populated inside its own try/except; failures are collected
    # here and reported at the end via the reserved ``__failures__`` key, while
    # the loop continues with the remaining projects.
    failures: list[dict] = []
    for project_path in project_dirs:
        project_name = project_path.name

        # Attribution: nearest installed STRICT ancestor wins; else the root.
        anchor = _nearest_installed_ancestor(project_path, installed_set, root)
        if anchor is None:
            anchor = root
        target_workspace = ws_name_by_path.get(anchor, workspace)

        # group_name = container directory between the anchor workspace and the
        # project. When the project sits directly under the anchor, there is no
        # intermediate container -> group_name stays None.
        container = project_path.parent
        group_name: str | None = container.name if container != anchor else None

        try:
            project_res = populate_project(
                target_workspace, project_path, agent, db_path=db_path,
                group_name=group_name,
            )
            infra_res = populate_infrastructure(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
            orch_res = populate_orchestration(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
            feat_res = populate_features(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
            apps_res = populate_apps(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
            services_res = populate_services(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
            libs_res = populate_libraries(
                target_workspace, project_name, project_path, agent, db_path=db_path
            )
        except Exception as exc:  # isolate the bad repo; keep scanning the rest
            failures.append({
                "workspace": target_workspace,
                "project": project_name,
                "path": str(project_path),
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        # Key by workspace/project so two workspaces with same-named projects
        # do not collide, and the CLI soft-delete can scope per workspace.
        results[f"{target_workspace}/{project_name}"] = {
            "workspace": target_workspace,
            "project": project_res,
            "infrastructure": infra_res,
            "orchestration": orch_res,
            "features": feat_res,
            "apps": apps_res,
            "services": services_res,
            "libraries": libs_res,
        }

    # Workspace-scoped populator: gaia_installations runs once per detected
    # installed workspace (and the root). Registering installations through
    # ``bulk_upsert`` also ensures the ``workspaces`` row exists for each
    # detected child workspace (canonical rule 2 + 4).
    from gaia.store.writer import set_workspace_last_scan_at

    workspace_results: dict = {}
    for ws_path in sorted(installed_set):
        ws_name = ws_name_by_path.get(ws_path)
        if ws_name is None:
            ws_name = resolve_identity(ws_path)
            ws_name_by_path[ws_path] = ws_name
        # Ensure the workspaces row exists for every detected workspace,
        # independent of whether an installation row is emitted (canonical
        # rule 2 + 4: a registry-detected dir is a workspace regardless of the
        # node_modules / skills+agents footprint). The CLI root is also marked
        # here for child anchors; the CLI separately records its own root.
        try:
            set_workspace_last_scan_at(ws_name, db_path=db_path)
        except Exception:  # pragma: no cover -- non-fatal
            pass
        gaia_inst_res = populate_gaia_installations(
            ws_name, ws_path, agent, db_path=db_path
        )
        workspace_results[ws_name] = {
            "path": str(ws_path),
            "gaia_installations": gaia_inst_res,
        }
    results["__workspace__"] = workspace_results
    # Per-project isolation (AC-4): collected failures, empty when all repos
    # populated cleanly. Callers (bin/cli/scan.py:_populate_store) consume this
    # to keep failed-but-on-disk projects out of the soft-delete pass and to
    # report the failures.
    results["__failures__"] = failures
    return results


# ===========================================================================
# Internal helpers (filesystem + git probing -- pure read-only)
# ===========================================================================

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_remote_origin(project_path: Path) -> str | None:
    import shutil
    import subprocess
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    url = (result.stdout or "").strip()
    return url or None


def _platform_from_remote(url: str | None) -> str | None:
    if not url:
        return None
    u = url.lower()
    if "github.com" in u:
        return "github"
    if "bitbucket.org" in u:
        return "bitbucket"
    if "gitlab.com" in u or "gitlab" in u:
        return "gitlab"
    return None


def _detect_primary_language(project_path: Path) -> str | None:
    if not project_path.is_dir():
        return None
    try:
        names = {c.name for c in project_path.iterdir()}
    except OSError:
        return None
    if "package.json" in names:
        return "javascript"
    if "pyproject.toml" in names or "setup.py" in names or "requirements.txt" in names:
        return "python"
    if "go.mod" in names:
        return "go"
    if "Cargo.toml" in names:
        return "rust"
    if "pom.xml" in names or "build.gradle" in names:
        return "java"
    if any(n.endswith(".tf") for n in names):
        return "hcl"
    return None


def _list_repos(root: Path, max_depth: int = 4) -> list[Path]:
    """Return git repositories discovered under root via bounded recursive walk.

    A directory is a repo if it contains a ``.git`` entry (directory or
    file -- the file form covers git worktrees).  Container directories that
    are not themselves repos are descended into so that layouts like::

        ~/ws/github-repos/        <- container, no .git
            repo-a/               <- repo, has .git
            repo-b/               <- repo, has .git
        ~/ws/aaxis/               <- container, no .git
            bildwiz/              <- sub-container, no .git
                platform-repo/    <- repo, has .git

    are handled correctly.  Plain directories without ``.git`` anywhere in
    their subtree are **not** returned as repos (AC-3: ``briefs/``,
    ``plans/``, and similar sidecar folders are excluded).

    The walk is bounded at ``max_depth`` levels below ``root`` to avoid
    runaway traversal on deep trees.  Directories whose basename appears in
    ``_REPO_WALK_SKIP`` are never descended into.

    The returned paths are sorted for deterministic output.  Their
    ``.parent`` attribute gives the immediate container directory, which
    T2.2 can consume to infer ``group_name``.

    Args:
        root: Workspace root to search from.
        max_depth: Maximum directory depth to descend (default 4).

    Returns:
        Sorted list of absolute ``Path`` objects, each pointing to the root
        of a git repository.
    """
    if not root.is_dir():
        return []

    # Root itself is a repo -- return it directly without recursing into it.
    if (root / ".git").exists():
        return [root]

    repos: list[Path] = []
    _walk_for_repos(root, root, current_depth=0, max_depth=max_depth, repos=repos)
    return sorted(repos)


# Directories that are never git repos and that the walk must not descend into.
_REPO_WALK_SKIP: frozenset[str] = frozenset({
    "node_modules",
    "__pycache__",
    "vendor",
    "dist",
    "build",
    ".terraform",
    ".terragrunt-cache",
    ".venv",
    "venv",
    ".cache",
    ".npm",
    ".next",
    ".nuxt",
    "target",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    # Gaia sidecar directories -- not projects (AC-3)
    ".git",
    ".claude",
    "briefs",
    "plans",
})


def _is_installed_gaia_workspace(directory: Path) -> bool:
    """Return True if `directory` carries a Gaia installation footprint.

    Canonical, MODE-AGNOSTIC signal (see hooks/modules/core/plugin_mode.py):
    a ``.claude/plugin-registry.json`` whose ``installed[*].name`` list
    includes ``"gaia-ops"`` OR ``"gaia-security"``.

    This deliberately does NOT use ``.plugin-initialized`` and does NOT treat a
    bare ``.claude`` directory as a Gaia install. A repo that was cloned with
    its own third-party ``.claude`` (no Gaia plugin registry, or a registry
    that lists other plugins) therefore returns False -- avoiding the false
    positive called out in the canonical rule.

    Read-only; never raises.
    """
    import json

    registry = directory / ".claude" / "plugin-registry.json"
    if not registry.is_file():
        return False
    try:
        data = json.loads(registry.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    installed = data.get("installed")
    if not isinstance(installed, list):
        return False
    names = {
        p.get("name")
        for p in installed
        if isinstance(p, dict) and isinstance(p.get("name"), str)
    }
    return "gaia-ops" in names or "gaia-security" in names


def _list_installed_workspaces(root: Path, max_depth: int = 4) -> list[Path]:
    """Return directories under `root` (inclusive) that carry a Gaia install.

    A directory qualifies when :func:`_is_installed_gaia_workspace` returns
    True. The walk uses the SAME bounding (``max_depth``) and the SAME
    skip-dir set (:data:`_REPO_WALK_SKIP`, which includes ``.claude`` and
    ``node_modules``) as :func:`_list_repos`, so we never descend into a child
    workspace's installation tree (canonical rule 5).

    Unlike :func:`_list_repos`, this walk does NOT stop at git repos -- a dir
    can be both a project and a workspace, and a workspace may contain nested
    installed workspaces. The signal is tested on the directory itself; we
    never descend into ``.claude`` to find it (we read ``dir/.claude/...``).

    Returns:
        Sorted list of absolute ``Path`` objects, each an installed workspace.
    """
    if not root.is_dir():
        return []

    installed: list[Path] = []
    if _is_installed_gaia_workspace(root):
        installed.append(root)
    _walk_for_installs(root, current_depth=0, max_depth=max_depth, installed=installed)
    return sorted(installed)


def _walk_for_installs(
    current: Path,
    current_depth: int,
    max_depth: int,
    installed: list[Path],
) -> None:
    """Recursive helper for :func:`_list_installed_workspaces`.

    Descends into every non-skipped child directory (depth-bounded), testing
    each for the Gaia installation signal. Does not stop at git repos.
    """
    if current_depth >= max_depth:
        return
    try:
        entries = sorted(current.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or name in _REPO_WALK_SKIP:
            continue
        if _is_installed_gaia_workspace(entry):
            installed.append(entry)
        _walk_for_installs(entry, current_depth + 1, max_depth, installed)


def _nearest_installed_ancestor(
    project_path: Path,
    installed: set[Path],
    scan_root: Path,
) -> Path | None:
    """Return the deepest installed workspace that is a STRICT ancestor.

    Walks ``project_path``'s parents from nearest to farthest, stopping at
    ``scan_root`` (inclusive), and returns the first parent present in
    ``installed``. Excludes ``project_path`` itself: a dir that is both a
    project and a workspace is attributed to the workspace ABOVE it, not to
    itself (its own workspace row is registered separately).

    Returns:
        The nearest installed-ancestor path, or ``None`` when no installed
        workspace sits between the project and ``scan_root``.
    """
    try:
        parents = list(project_path.parents)
    except (OSError, ValueError):
        return None
    for parent in parents:
        if parent in installed:
            return parent
        if parent == scan_root:
            break
    return None


def _walk_for_repos(
    root: Path,
    current: Path,
    current_depth: int,
    max_depth: int,
    repos: list[Path],
) -> None:
    """Recursive helper for :func:`_list_repos`.

    Descends into ``current`` looking for git repos.  Stops at
    ``max_depth`` levels below ``root``.  Any directory that has a
    ``.git`` entry is treated as a repo leaf and is NOT descended into
    (nested repos inside a repo are the repo's own concern).

    Args:
        root: The original workspace root (used only for depth reference).
        current: Directory to examine at this recursion level.
        current_depth: How many levels below ``root`` we are now.
        max_depth: Maximum depth; when reached, stop descending.
        repos: Accumulator list of repo paths found so far.
    """
    if current_depth > max_depth:
        return

    try:
        entries = sorted(current.iterdir())
    except OSError:
        return

    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        # Skip hidden dirs and explicitly excluded names.
        if name.startswith(".") or name in _REPO_WALK_SKIP:
            continue
        if (entry / ".git").exists():
            # This directory is a git repo -- collect it, do not recurse.
            repos.append(entry)
        else:
            # Container directory: recurse to find repos inside it.
            _walk_for_repos(root, entry, current_depth + 1, max_depth, repos)


def _scan_tf_modules(project_path: Path) -> list[dict]:
    """Detect Terraform module references in *.tf files.

    Returns a list of {name, source, version} dicts, one per `module` block.
    """
    import re
    modules = []
    seen = set()
    pattern = re.compile(r'module\s+"([^"]+)"\s*\{', re.MULTILINE)
    source_re = re.compile(r'\bsource\s*=\s*"([^"]+)"')
    version_re = re.compile(r'\bversion\s*=\s*"([^"]+)"')
    if not project_path.is_dir():
        return modules
    try:
        for tf in project_path.rglob("*.tf"):
            if any(p in tf.parts for p in (".terraform", "node_modules", ".git", "tests", "fixtures", "templates", "examples")):
                continue
            try:
                content = tf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in pattern.finditer(content):
                name = m.group(1)
                if name in seen:
                    continue
                seen.add(name)
                # Find source/version near the match
                tail = content[m.end(): m.end() + 400]
                src = source_re.search(tail)
                ver = version_re.search(tail)
                modules.append({
                    "name": name,
                    "source": src.group(1) if src else None,
                    "version": ver.group(1) if ver else None,
                })
    except OSError:
        pass
    return modules


def _scan_tf_live(project_path: Path) -> list[dict]:
    """Detect live Terraform resources from `live/` directories."""
    out = []
    seen = set()
    live_dir = project_path / "live"
    if not live_dir.is_dir():
        return out
    try:
        for tg in live_dir.rglob("terragrunt.hcl"):
            rel = tg.parent.relative_to(project_path)
            name = str(rel).replace("/", "-")
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "kind": "terragrunt",
                "attributes": None,
            })
    except OSError:
        pass
    return out


def _scan_clusters_defined(project_path: Path) -> list[dict]:
    """Detect cluster definitions in TF files (google_container_cluster etc.)."""
    import re
    out = []
    seen = set()
    cluster_re = re.compile(
        r'resource\s+"(google_container_cluster|aws_eks_cluster|azurerm_kubernetes_cluster)"\s+"([^"]+)"'
    )
    if not project_path.is_dir():
        return out
    try:
        for tf in project_path.rglob("*.tf"):
            if any(p in tf.parts for p in (".terraform", "node_modules", ".git", "tests", "fixtures", "templates", "examples")):
                continue
            try:
                content = tf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in cluster_re.finditer(content):
                kind = m.group(1)
                name = m.group(2)
                if name in seen:
                    continue
                seen.add(name)
                provider = {
                    "google_container_cluster": "gke",
                    "aws_eks_cluster": "eks",
                    "azurerm_kubernetes_cluster": "aks",
                }.get(kind)
                out.append({
                    "name": name,
                    "provider": provider,
                    "region": None,
                })
    except OSError:
        pass
    return out


def _scan_releases(project_path: Path) -> list[dict]:
    """Detect HelmRelease + Kustomization YAMLs as 'releases' rows."""
    out = []
    seen = set()
    if not project_path.is_dir():
        return out
    try:
        for yml in project_path.rglob("*.y*ml"):
            if any(p in yml.parts for p in ("node_modules", ".git", "__pycache__", "tests", "fixtures", "templates", "examples")):
                continue
            try:
                content = yml.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kind = _yaml_kind(content)
            if kind in ("HelmRelease", "Kustomization"):
                name = _yaml_metadata_name(content)
                if not name:
                    continue
                key = f"{kind}:{name}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({"name": name})
    except OSError:
        pass
    return out


def _scan_workloads(project_path: Path) -> list[dict]:
    """Detect Deployment/StatefulSet/DaemonSet YAMLs."""
    out = []
    seen = set()
    workload_kinds = {"Deployment", "StatefulSet", "DaemonSet"}
    if not project_path.is_dir():
        return out
    try:
        for yml in project_path.rglob("*.y*ml"):
            if any(p in yml.parts for p in ("node_modules", ".git", "__pycache__", "tests", "fixtures", "templates", "examples")):
                continue
            try:
                content = yml.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kind = _yaml_kind(content)
            if kind in workload_kinds:
                name = _yaml_metadata_name(content)
                ns = _yaml_metadata_namespace(content)
                if not name:
                    continue
                key = f"{kind}:{name}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "name": name,
                    "kind": kind,
                    "namespace": ns,
                    "cluster": None,
                })
    except OSError:
        pass
    return out


def _yaml_kind(content: str) -> str | None:
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("kind:"):
            return s[5:].strip().strip("'\"")
    return None


def _yaml_metadata_name(content: str) -> str | None:
    in_meta = False
    for line in content.splitlines():
        if line.startswith("metadata:"):
            in_meta = True
            continue
        if in_meta:
            if line.startswith(" ") or line.startswith("\t"):
                s = line.strip()
                if s.startswith("name:"):
                    return s[5:].strip().strip("'\"")
            else:
                in_meta = False
    return None


def _yaml_metadata_namespace(content: str) -> str | None:
    in_meta = False
    for line in content.splitlines():
        if line.startswith("metadata:"):
            in_meta = True
            continue
        if in_meta:
            if line.startswith(" ") or line.startswith("\t"):
                s = line.strip()
                if s.startswith("namespace:"):
                    return s[10:].strip().strip("'\"")
            else:
                in_meta = False
    return None


def _scan_features(project_path: Path) -> list[dict]:
    """Detect feature units in a project using a three-tier heuristic.

    Tier 1: ``features/`` subdirectory -- any child dir of
    ``{project_path}/features/`` becomes a feature row.

    Tier 2: ``feature.json`` / ``feature.yaml`` / ``feature.yml`` descriptor
    files anywhere in the project tree (excluding noise dirs). The parent
    directory name is used as the feature name.

    Tier 3: ``flags.json`` / ``flags.yaml`` at project root -- top-level keys
    become feature rows (LaunchDarkly / OpenFeature style).

    Returns a de-duplicated list of ``{"name": str}`` dicts.
    """
    import json

    out: list[dict] = []
    seen: set[str] = set()
    _SKIP = {"node_modules", "__pycache__", ".git", ".terraform", "dist", "build", ".venv", "venv", "tests", "fixtures", "templates", "examples"}

    def _add(name: str) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name})

    if not project_path.is_dir():
        return out

    # Tier 1: features/ directory
    features_dir = project_path / "features"
    if features_dir.is_dir():
        try:
            for child in sorted(features_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    _add(child.name)
        except OSError:
            pass

    # Tier 2: feature descriptor files
    try:
        for desc in project_path.rglob("feature.json"):
            if any(p in desc.parts for p in _SKIP):
                continue
            _add(desc.parent.name)
        for desc in project_path.rglob("feature.y*ml"):
            if any(p in desc.parts for p in _SKIP):
                continue
            _add(desc.parent.name)
    except OSError:
        pass

    # Tier 3: flags.json / flags.yaml at project root
    for flags_file in (project_path / "flags.json", project_path / "flags.yaml", project_path / "flags.yml"):
        if not flags_file.is_file():
            continue
        try:
            if flags_file.suffix == ".json":
                data = json.loads(flags_file.read_text(encoding="utf-8", errors="replace"))
            else:
                # Minimal YAML top-key extraction without requiring PyYAML
                data = {}
                for line in flags_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and ":" in s and not line.startswith(" "):
                        key = s.split(":")[0].strip().strip("'\"")
                        if key:
                            data[key] = True
            if isinstance(data, dict):
                for key in data:
                    _add(str(key))
        except (OSError, ValueError):
            pass

    return out


def _scan_apps(project_path: Path) -> list[dict]:
    """Detect deployable apps in a project.

    Tier 1: ``apps/`` subdirectory -- each child directory becomes an app
    row. ``kind`` is inferred from marker files inside (``Dockerfile`` or
    ``docker-compose*.yml`` -> ``"service"``; otherwise ``"app"``).

    Tier 2: single-project deployable -- if the project has ``package.json``
    at root AND no ``apps/`` directory was found, the project itself becomes
    one app row keyed by package.json ``name`` (or project basename fallback).

    Returns a de-duplicated list of ``{"name": str, "kind": str|None}`` dicts.
    """
    import json

    out: list[dict] = []
    seen: set[str] = set()

    def _add(name: str, kind: str | None) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name, "kind": kind})

    if not project_path.is_dir():
        return out

    # Tier 1: apps/ directory
    apps_dir = project_path / "apps"
    if apps_dir.is_dir():
        try:
            for child in sorted(apps_dir.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                kind = "app"
                try:
                    names = {p.name for p in child.iterdir()}
                except OSError:
                    names = set()
                if "Dockerfile" in names or any(
                    n.startswith("docker-compose") for n in names
                ):
                    kind = "service"
                _add(child.name, kind)
        except OSError:
            pass
        # If we found anything in apps/, do not also emit the single-project row.
        if out:
            return out

    # Tier 2: single-project deployable
    pkg = project_path / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = {}
        # Skip if this is a workspace root (workspaces field present) -- those
        # are aggregators, not deployable apps. Their packages/apps are picked
        # up by populate_libraries / Tier 1 of populate_apps.
        if isinstance(data, dict) and "workspaces" not in data:
            name = (data.get("name") if isinstance(data.get("name"), str) else None) or project_path.name
            kind = "service" if (project_path / "Dockerfile").is_file() else "app"
            _add(name, kind)

    return out


def _scan_services(project_path: Path) -> list[dict]:
    """Detect infrastructure-level services in a project.

    Tier 1: ``services/`` subdirectory -- each child directory becomes a
    service row (kind=``"api"`` by default).

    Tier 2: docker-compose top-level services -- ``docker-compose.yml`` /
    ``docker-compose.yaml`` / ``docker-compose-*.yml`` at the project root.
    Parses the top-level ``services:`` mapping. ``kind`` is inferred from
    the image name when present (``postgres``/``mysql`` -> ``"database"``;
    ``redis``/``memcached`` -> ``"cache"``; ``rabbitmq``/``kafka`` ->
    ``"queue"``; otherwise ``"api"``).

    Returns a de-duplicated list of ``{"name": str, "kind": str|None}`` dicts.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(name: str, kind: str | None) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name, "kind": kind})

    if not project_path.is_dir():
        return out

    # Tier 1: services/ directory
    services_dir = project_path / "services"
    if services_dir.is_dir():
        try:
            for child in sorted(services_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    _add(child.name, "api")
        except OSError:
            pass

    # Tier 2: docker-compose top-level services
    compose_files: list[Path] = []
    try:
        for child in project_path.iterdir():
            if not child.is_file():
                continue
            if child.name in ("docker-compose.yml", "docker-compose.yaml"):
                compose_files.append(child)
            elif child.name.startswith("docker-compose-") and (
                child.name.endswith(".yml") or child.name.endswith(".yaml")
            ):
                compose_files.append(child)
    except OSError:
        pass

    for cf in compose_files:
        try:
            content = cf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for svc_name, image in _parse_compose_services(content):
            _add(svc_name, _infer_service_kind(image))

    return out


def _parse_compose_services(content: str) -> list[tuple[str, str | None]]:
    """Parse a docker-compose YAML for top-level service names + image strings.

    Returns a list of ``(service_name, image_or_None)`` tuples. Tolerates
    the absence of PyYAML by doing line-based parsing of the top-level
    ``services:`` block.
    """
    out: list[tuple[str, str | None]] = []
    lines = content.splitlines()

    in_services = False
    services_indent = -1
    current_service: str | None = None
    current_indent = -1
    current_image: str | None = None

    def _emit() -> None:
        nonlocal current_service, current_image, current_indent
        if current_service:
            out.append((current_service, current_image))
        current_service = None
        current_image = None
        current_indent = -1

    for raw in lines:
        # Strip comment-only lines but keep indentation
        if raw.strip().startswith("#") or not raw.strip():
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        # Detect entering services: block
        if not in_services:
            if stripped == "services:" and indent == 0:
                in_services = True
                services_indent = 0
            continue

        # Inside services: block. Top-level key (one indent step in) = service name.
        # Stop when indentation returns to <= services_indent and is a different key.
        if indent <= services_indent and stripped.endswith(":") and stripped != "services:":
            _emit()
            in_services = False
            continue

        # Service name line: indent > services_indent and ends with ":"
        if current_service is None and indent > services_indent and stripped.endswith(":"):
            current_service = stripped[:-1].strip().strip("'\"")
            current_indent = indent
            current_image = None
            continue

        # Image line under current service
        if current_service is not None and indent > current_indent and stripped.startswith("image:"):
            val = stripped[len("image:"):].strip().strip("'\"")
            current_image = val or None
            continue

        # Next sibling service starts (same indent as current service)
        if current_service is not None and indent == current_indent and stripped.endswith(":"):
            _emit()
            current_service = stripped[:-1].strip().strip("'\"")
            current_indent = indent
            current_image = None
            continue

    _emit()
    return out


def _infer_service_kind(image: str | None) -> str:
    """Map a docker image name to a service kind."""
    if not image:
        return "api"
    img = image.lower()
    if any(db in img for db in ("postgres", "mysql", "mariadb", "mongodb", "mongo:")):
        return "database"
    if any(c in img for c in ("redis", "memcached")):
        return "cache"
    if any(q in img for q in ("rabbitmq", "kafka", "nats")):
        return "queue"
    if any(s in img for s in ("minio", "s3")):
        return "storage"
    return "api"


def _scan_libraries(project_path: Path) -> list[dict]:
    """Detect workspace-internal libraries (shared packages) in a project.

    Tier 1: ``packages/`` subdirectory -- each child dir with a
    ``package.json`` becomes a library row. ``name`` and ``version`` come
    from the package.json.

    Tier 2: ``libs/`` and ``libraries/`` subdirectories -- alternative
    monorepo conventions.

    Tier 3: package.json with ``workspaces`` field -- glob each pattern
    (e.g. ``packages/*``) and emit one row per matched directory's
    ``package.json``. Extends Tier 1 to cover non-default workspace layouts.

    Returns a de-duplicated list of
    ``{"name": str, "version": str|None, "language": str}`` dicts.
    """
    import json

    out: list[dict] = []
    seen: set[str] = set()

    def _add(name: str, version: str | None) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name, "version": version, "language": "javascript"})

    def _read_pkg(pkg_path: Path) -> tuple[str | None, str | None]:
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        n = data.get("name") if isinstance(data.get("name"), str) else None
        v = data.get("version") if isinstance(data.get("version"), str) else None
        return n, v

    if not project_path.is_dir():
        return out

    # Tier 1+2: packages/, libs/, libraries/ directories
    for dir_name in ("packages", "libs", "libraries"):
        d = project_path / dir_name
        if not d.is_dir():
            continue
        try:
            for child in sorted(d.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                pkg = child / "package.json"
                if pkg.is_file():
                    n, v = _read_pkg(pkg)
                    _add(n or child.name, v)
                else:
                    # Even without package.json, treat as library by dirname
                    _add(child.name, None)
        except OSError:
            pass

    # Tier 3: package.json with workspaces field
    root_pkg = project_path / "package.json"
    if root_pkg.is_file():
        try:
            data = json.loads(root_pkg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = {}
        workspaces = data.get("workspaces") if isinstance(data, dict) else None
        # workspaces can be a list or a dict {"packages": [...]}
        patterns: list[str] = []
        if isinstance(workspaces, list):
            patterns = [p for p in workspaces if isinstance(p, str)]
        elif isinstance(workspaces, dict):
            inner = workspaces.get("packages")
            if isinstance(inner, list):
                patterns = [p for p in inner if isinstance(p, str)]

        for pattern in patterns:
            try:
                # Translate trailing /* into a glob over a single directory level
                for match in project_path.glob(pattern):
                    if not match.is_dir():
                        continue
                    pkg = match / "package.json"
                    if pkg.is_file():
                        n, v = _read_pkg(pkg)
                        _add(n or match.name, v)
            except (OSError, ValueError):
                pass

    return out


def _scan_gaia_installations(workspace_root: Path) -> list[dict]:
    """Detect Gaia installations rooted at a workspace.

    Returns at most one row per machine (current hostname). Looks for:

    * ``node_modules/@jaguilar87/gaia/package.json`` -- canonical npm install.
    * ``.claude/skills/`` AND ``.claude/agents/`` -- Gaia footprint without
      a node_modules entry (dev symlink scenario).

    When neither marker is found, returns an empty list.
    """
    import json
    import socket

    out: list[dict] = []
    if not workspace_root.is_dir():
        return out

    machine = socket.gethostname() or "unknown"

    # Marker 1: node_modules/@jaguilar87/gaia/package.json
    npm_pkg = workspace_root / "node_modules" / "@jaguilar87" / "gaia" / "package.json"
    if npm_pkg.is_file():
        version: str | None = None
        try:
            data = json.loads(npm_pkg.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                version = data["version"]
        except (OSError, ValueError):
            version = None
        # Detect symlinked dev install: node_modules/@jaguilar87/gaia is a symlink
        gaia_dir = npm_pkg.parent
        install_mode = "dev" if gaia_dir.is_symlink() else "npm"
        out.append({
            "machine": machine,
            "version": version,
            "install_mode": install_mode,
        })
        return out

    # Marker 2: .claude/ footprint (skills/ + agents/) without node_modules
    claude_dir = workspace_root / ".claude"
    if claude_dir.is_dir():
        skills_dir = claude_dir / "skills"
        agents_dir = claude_dir / "agents"
        if skills_dir.is_dir() and agents_dir.is_dir():
            version_file = claude_dir / ".gaia-version"
            version = None
            if version_file.is_file():
                try:
                    version = version_file.read_text(encoding="utf-8", errors="replace").strip() or None
                except OSError:
                    version = None
            install_mode = "dev" if skills_dir.is_symlink() or agents_dir.is_symlink() else "unknown"
            out.append({
                "machine": machine,
                "version": version,
                "install_mode": install_mode,
            })
            return out

    # Marker 3: canonical plugin-registry.json signal (mode-agnostic). This is
    # the signal the canonical classification rule uses to call a dir a
    # workspace; record the installation so the workspaces row is anchored even
    # when the npm / skills+agents markers are absent (e.g. plugin-managed
    # installs where the footprint lives in the plugin data dir, not under
    # ./.claude/). Version comes from the registry entry when present.
    if _is_installed_gaia_workspace(workspace_root):
        version = None
        try:
            registry = json.loads(
                (claude_dir / "plugin-registry.json").read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            for p in registry.get("installed", []):
                if isinstance(p, dict) and p.get("name") in ("gaia-ops", "gaia-security"):
                    v = p.get("version")
                    if isinstance(v, str):
                        version = v
                        break
        except (OSError, ValueError):
            version = None
        out.append({
            "machine": machine,
            "version": version,
            "install_mode": "plugin",
        })

    return out


def _safe_delete_missing(
    table: str,
    workspace: str,
    project: str,
    surviving: Iterable[tuple],
    db_path: Path | None,
) -> int:
    """Prune rows in `table` for this workspace that no longer survive.

    The store's ``delete_missing_in`` deletes by project + PK fragment. For
    project-scoped tables (PK = (project, name)), we pass
    ``[(project, name), ...]`` directly. We also include the rows for OTHER
    projects in the same workspace under the same PK shape so we don't delete
    sibling projects' rows.
    """
    from gaia.store import delete_missing_in
    from gaia.store.writer import _connect

    surviving = list(surviving)

    # Read ALL rows for this workspace and add foreign-project PKs to the
    # surviving set so we only prune the rows belonging to `project`.
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"SELECT project, name FROM {table} WHERE workspace = ?",
            (workspace,),
        )
        all_rows = [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        con.close()

    surviving_set = set(surviving)
    foreign = [(p, n) for (p, n) in all_rows if p != project]
    full_surviving = list(surviving_set) + foreign

    return delete_missing_in(table, workspace, full_surviving, db_path=db_path)
