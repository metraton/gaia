"""
Store Populator -- Scanner-side adapter to the Gaia SQLite substrate.

Replaces the legacy "produce JSON sections" path. Each populator function
inspects a repository directory (or a workspace of repos) and emits CRUD
operations through the gaia.store API:

    upsert_repo, upsert_app, bulk_upsert, delete_missing_in

The populators NEVER touch agent-owned columns. They only set scanner-owned
columns; the store API protects agent fields by listing scanner columns
explicitly in its UPSERT statements.

Identity resolution: for each repo path, identity is resolved via
``gaia.project.current(repo_path)`` (B0). This means two clones of the same
remote on different machines collapse to the same workspace identity row.

Public API::

    populate_repo(workspace, repo_path, agent, *, db_path=None) -> dict
    populate_infrastructure(workspace, repo, repo_path, agent, *, db_path=None) -> dict
    populate_orchestration(workspace, repo, repo_path, agent, *, db_path=None) -> dict
    populate_features(workspace, repo, repo_path, agent, *, db_path=None) -> dict
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

def resolve_identity(repo_path: Path) -> str:
    """Resolve workspace identity from the git remote of `repo_path` via B0.

    Returns:
        Canonical identity string (host/owner/repo) or the path basename
        when no remote is detected. Never empty, never raises.
    """
    from gaia.project import current
    return current(cwd=repo_path)


# ---------------------------------------------------------------------------
# Repo-level populator
# ---------------------------------------------------------------------------

def populate_repo(
    workspace: str,
    repo_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
    repo_name: str | None = None,
) -> dict:
    """Detect role + remote of a repo and persist to the `repos` table.

    Args:
        workspace: Workspace identity (projects.name).
        repo_path: Absolute path to the repo root.
        agent: Agent name (used by the permission matrix in the store).
        db_path: Optional explicit DB path (test override).
        repo_name: Override for the repo basename. When None, uses
            repo_path.name.

    Returns:
        Dict with keys ``applied`` (1|0), ``rejected`` (1|0), ``role``,
        ``identity``, and ``name``. Never raises.
    """
    from gaia.store import upsert_repo

    name = repo_name or repo_path.name
    role = detect_role(repo_path)
    identity = resolve_identity(repo_path)
    remote_url = _git_remote_origin(repo_path)
    platform = _platform_from_remote(remote_url)
    primary_language = _detect_primary_language(repo_path)

    res = upsert_repo(
        workspace=workspace,
        name=name,
        fields={
            "role": role,
            "remote_url": remote_url,
            "platform": platform,
            "primary_language": primary_language,
        },
        agent=agent,
        db_path=db_path,
        workspace_path=repo_path,
    )
    applied = 1 if res.get("status") == "applied" else 0
    return {
        "applied": applied,
        "rejected": 1 - applied,
        "role": role,
        "identity": identity,
        "name": name,
    }


# ---------------------------------------------------------------------------
# Infrastructure-side populator (tf_modules, tf_live, clusters)
# ---------------------------------------------------------------------------

def populate_infrastructure(
    workspace: str,
    repo: str,
    repo_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist Terraform / cluster discoveries for a repo into the store.

    Reads `repo_path` for *.tf, terragrunt.hcl, and live/ directories.
    Calls ``bulk_upsert('tf_modules', ...)``, ``bulk_upsert('tf_live', ...)``,
    and ``bulk_upsert('clusters_defined', ...)`` for what was found, then
    calls ``delete_missing_in`` to prune stale rows for this repo.

    Returns:
        Dict with applied/rejected/deleted counts per table.
    """
    from gaia.store import bulk_upsert, delete_missing_in

    tf_modules = _scan_tf_modules(repo_path)
    tf_live = _scan_tf_live(repo_path)
    clusters_defined = _scan_clusters_defined(repo_path)

    out = {"tf_modules": {}, "tf_live": {}, "clusters_defined": {}}

    # tf_modules
    rows_tm = [
        {"repo": repo, "name": m["name"], "source": m.get("source"),
         "version": m.get("version"), "scanner_ts": _now_iso()}
        for m in tf_modules
    ]
    if rows_tm:
        out["tf_modules"]["upsert"] = bulk_upsert(
            "tf_modules", workspace, rows_tm, agent, db_path=db_path
        )
    surviving_tm = [(repo, m["name"]) for m in tf_modules]
    out["tf_modules"]["deleted"] = _safe_delete_missing(
        "tf_modules", workspace, repo, surviving_tm, db_path
    )

    # tf_live
    rows_tl = [
        {"repo": repo, "name": l["name"], "kind": l.get("kind"),
         "attributes": l.get("attributes"), "scanner_ts": _now_iso()}
        for l in tf_live
    ]
    if rows_tl:
        out["tf_live"]["upsert"] = bulk_upsert(
            "tf_live", workspace, rows_tl, agent, db_path=db_path
        )
    surviving_tl = [(repo, l["name"]) for l in tf_live]
    out["tf_live"]["deleted"] = _safe_delete_missing(
        "tf_live", workspace, repo, surviving_tl, db_path
    )

    # clusters_defined
    rows_cd = [
        {"repo": repo, "name": c["name"], "provider": c.get("provider"),
         "region": c.get("region"), "scanner_ts": _now_iso()}
        for c in clusters_defined
    ]
    if rows_cd:
        out["clusters_defined"]["upsert"] = bulk_upsert(
            "clusters_defined", workspace, rows_cd, agent, db_path=db_path
        )
    surviving_cd = [(repo, c["name"]) for c in clusters_defined]
    out["clusters_defined"]["deleted"] = _safe_delete_missing(
        "clusters_defined", workspace, repo, surviving_cd, db_path
    )

    return out


# ---------------------------------------------------------------------------
# Orchestration-side populator (releases, workloads, clusters_defined)
# ---------------------------------------------------------------------------

def populate_orchestration(
    workspace: str,
    repo: str,
    repo_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist GitOps / workload discoveries for a repo into the store."""
    from gaia.store import bulk_upsert

    releases = _scan_releases(repo_path)
    workloads = _scan_workloads(repo_path)

    out = {"releases": {}, "workloads": {}}

    rows_r = [
        {"repo": repo, "name": r["name"], "released_at": r.get("released_at"),
         "scanner_ts": _now_iso()}
        for r in releases
    ]
    if rows_r:
        out["releases"]["upsert"] = bulk_upsert(
            "releases", workspace, rows_r, agent, db_path=db_path
        )
    surviving_r = [(repo, r["name"]) for r in releases]
    out["releases"]["deleted"] = _safe_delete_missing(
        "releases", workspace, repo, surviving_r, db_path
    )

    rows_w = [
        {"repo": repo, "name": w["name"], "kind": w.get("kind"),
         "namespace": w.get("namespace"), "cluster": w.get("cluster"),
         "scanner_ts": _now_iso()}
        for w in workloads
    ]
    if rows_w:
        out["workloads"]["upsert"] = bulk_upsert(
            "workloads", workspace, rows_w, agent, db_path=db_path
        )
    surviving_w = [(repo, w["name"]) for w in workloads]
    out["workloads"]["deleted"] = _safe_delete_missing(
        "workloads", workspace, repo, surviving_w, db_path
    )

    return out


# ---------------------------------------------------------------------------
# Features-side populator
# ---------------------------------------------------------------------------

def populate_features(
    workspace: str,
    repo: str,
    repo_path: Path,
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Persist feature-unit discoveries for a repo into the ``features`` table.

    Feature detection heuristic (in priority order):

    1. **``features/`` directory** -- any subdirectory directly under
       ``{repo_path}/features/`` is treated as a feature unit. This covers the
       canonical ``qxo-monorepo`` layout where each feature lives in its own
       package directory (e.g. ``features/auth-feature``,
       ``features/orders-feature``).
    2. **Feature descriptor files** -- any ``feature.json`` or
       ``feature.yaml``/``feature.yml`` file found anywhere under the repo
       (excluding ``node_modules``, ``.git``, ``__pycache__``) is treated as a
       feature descriptor; its parent directory name becomes the feature name.
    3. **LaunchDarkly / OpenFeature flags** -- ``flags.json`` or
       ``flags.yaml`` at the repo root is parsed for top-level keys, each of
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

    features = _scan_features(repo_path)
    out: dict = {"features": {}}

    rows_f = [
        {"repo": repo, "name": f["name"], "scanner_ts": _now_iso()}
        for f in features
    ]
    if rows_f:
        out["features"]["upsert"] = bulk_upsert(
            "features", workspace, rows_f, agent, db_path=db_path
        )
    surviving_f = [(repo, f["name"]) for f in features]
    out["features"]["deleted"] = _safe_delete_missing(
        "features", workspace, repo, surviving_f, db_path
    )
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

    Returns:
        Dict mapping repo names to per-repo result dicts.
    """
    repos = _list_repos(root)
    results = {}
    for repo_path in repos:
        repo_name = repo_path.name
        repo_res = populate_repo(workspace, repo_path, agent, db_path=db_path)
        infra_res = populate_infrastructure(
            workspace, repo_name, repo_path, agent, db_path=db_path
        )
        orch_res = populate_orchestration(
            workspace, repo_name, repo_path, agent, db_path=db_path
        )
        feat_res = populate_features(
            workspace, repo_name, repo_path, agent, db_path=db_path
        )
        results[repo_name] = {
            "repo": repo_res,
            "infrastructure": infra_res,
            "orchestration": orch_res,
            "features": feat_res,
        }
    return results


# ===========================================================================
# Internal helpers (filesystem + git probing -- pure read-only)
# ===========================================================================

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_remote_origin(repo_path: Path) -> str | None:
    import shutil
    import subprocess
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
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


def _detect_primary_language(repo_path: Path) -> str | None:
    if not repo_path.is_dir():
        return None
    try:
        names = {c.name for c in repo_path.iterdir()}
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


def _list_repos(root: Path) -> list[Path]:
    """Return the list of repo directories under root.

    A repo is a depth-1 subdirectory. If root itself contains marker files
    suggesting it is a single repo (e.g. .git, package.json, *.tf), it is
    returned as the only repo.
    """
    if not root.is_dir():
        return []

    if (root / ".git").exists():
        return [root]

    children = []
    skip = {"node_modules", "__pycache__", "vendor", "dist", "build",
            ".terraform", ".venv", "venv"}
    try:
        for c in sorted(root.iterdir()):
            if not c.is_dir():
                continue
            if c.name.startswith(".") or c.name in skip:
                continue
            children.append(c)
    except OSError:
        return []
    return children


def _scan_tf_modules(repo_path: Path) -> list[dict]:
    """Detect Terraform module references in *.tf files.

    Returns a list of {name, source, version} dicts, one per `module` block.
    """
    import re
    modules = []
    seen = set()
    pattern = re.compile(r'module\s+"([^"]+)"\s*\{', re.MULTILINE)
    source_re = re.compile(r'\bsource\s*=\s*"([^"]+)"')
    version_re = re.compile(r'\bversion\s*=\s*"([^"]+)"')
    if not repo_path.is_dir():
        return modules
    try:
        for tf in repo_path.rglob("*.tf"):
            if any(p in tf.parts for p in (".terraform", "node_modules", ".git")):
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


def _scan_tf_live(repo_path: Path) -> list[dict]:
    """Detect live Terraform resources from `live/` directories."""
    out = []
    seen = set()
    live_dir = repo_path / "live"
    if not live_dir.is_dir():
        return out
    try:
        for tg in live_dir.rglob("terragrunt.hcl"):
            rel = tg.parent.relative_to(repo_path)
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


def _scan_clusters_defined(repo_path: Path) -> list[dict]:
    """Detect cluster definitions in TF files (google_container_cluster etc.)."""
    import re
    out = []
    seen = set()
    cluster_re = re.compile(
        r'resource\s+"(google_container_cluster|aws_eks_cluster|azurerm_kubernetes_cluster)"\s+"([^"]+)"'
    )
    if not repo_path.is_dir():
        return out
    try:
        for tf in repo_path.rglob("*.tf"):
            if any(p in tf.parts for p in (".terraform", "node_modules", ".git")):
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


def _scan_releases(repo_path: Path) -> list[dict]:
    """Detect HelmRelease + Kustomization YAMLs as 'releases' rows."""
    out = []
    seen = set()
    if not repo_path.is_dir():
        return out
    try:
        for yml in repo_path.rglob("*.y*ml"):
            if any(p in yml.parts for p in ("node_modules", ".git", "__pycache__")):
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


def _scan_workloads(repo_path: Path) -> list[dict]:
    """Detect Deployment/StatefulSet/DaemonSet YAMLs."""
    out = []
    seen = set()
    workload_kinds = {"Deployment", "StatefulSet", "DaemonSet"}
    if not repo_path.is_dir():
        return out
    try:
        for yml in repo_path.rglob("*.y*ml"):
            if any(p in yml.parts for p in ("node_modules", ".git", "__pycache__")):
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


def _scan_features(repo_path: Path) -> list[dict]:
    """Detect feature units in a repo using a three-tier heuristic.

    Tier 1: ``features/`` subdirectory -- any child dir of
    ``{repo_path}/features/`` becomes a feature row.

    Tier 2: ``feature.json`` / ``feature.yaml`` / ``feature.yml`` descriptor
    files anywhere in the repo tree (excluding noise dirs). The parent
    directory name is used as the feature name.

    Tier 3: ``flags.json`` / ``flags.yaml`` at repo root -- top-level keys
    become feature rows (LaunchDarkly / OpenFeature style).

    Returns a de-duplicated list of ``{"name": str}`` dicts.
    """
    import json

    out: list[dict] = []
    seen: set[str] = set()
    _SKIP = {"node_modules", "__pycache__", ".git", ".terraform", "dist", "build", ".venv", "venv"}

    def _add(name: str) -> None:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name})

    if not repo_path.is_dir():
        return out

    # Tier 1: features/ directory
    features_dir = repo_path / "features"
    if features_dir.is_dir():
        try:
            for child in sorted(features_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    _add(child.name)
        except OSError:
            pass

    # Tier 2: feature descriptor files
    try:
        for desc in repo_path.rglob("feature.json"):
            if any(p in desc.parts for p in _SKIP):
                continue
            _add(desc.parent.name)
        for desc in repo_path.rglob("feature.y*ml"):
            if any(p in desc.parts for p in _SKIP):
                continue
            _add(desc.parent.name)
    except OSError:
        pass

    # Tier 3: flags.json / flags.yaml at repo root
    for flags_file in (repo_path / "flags.json", repo_path / "flags.yaml", repo_path / "flags.yml"):
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


def _safe_delete_missing(
    table: str,
    workspace: str,
    repo: str,
    surviving: Iterable[tuple],
    db_path: Path | None,
) -> int:
    """Prune rows in `table` for this workspace that no longer survive.

    The store's ``delete_missing_in`` deletes by project + PK fragment. For
    repo-scoped tables (PK = (project, repo, name)), we pass
    ``[(repo, name), ...]`` directly. We also include the rows for OTHER
    repos in the same workspace under the same PK shape so we don't delete
    sibling repos' rows.
    """
    from gaia.store import delete_missing_in
    from gaia.store.writer import _connect

    surviving = list(surviving)

    # Read ALL rows for this workspace and add foreign-repo PKs to the
    # surviving set so we only prune the rows belonging to `repo`.
    con = _connect(db_path)
    try:
        cur = con.execute(
            f"SELECT repo, name FROM {table} WHERE project = ?",
            (workspace,),
        )
        all_rows = [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        con.close()

    surviving_set = set(surviving)
    foreign = [(r, n) for (r, n) in all_rows if r != repo]
    full_surviving = list(surviving_set) + foreign

    return delete_missing_in(table, workspace, full_surviving, db_path=db_path)
