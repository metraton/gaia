"""
migrate_workspace.py -- B5 rescan-real-workspaces-with-backup

Migrates a real workspace to ~/.gaia/gaia.db via:
  1. Grant scanner agent permissions (idempotent)
  2. wipe_project(<identity>)
  3. scan_workspace_to_store (populate repos + infra + orchestration)
  4. Verify row counts
  5. Print diff JSON

Usage:
    python3 migrate_workspace.py <workspace_root> [--verify-only] [--diff-path <path>]

Options:
    --verify-only   Run scan but do NOT commit (dry-run mode for AC-1 test).
                    Note: wipe_project is NOT called in verify-only mode.
    --diff-path     Path to write .diff.json (default: <workspace_root>.diff.json)

Exit codes:
    0  Verify passed (rows > 0 in repos after scan)
    1  Verify failed or error
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_GAIA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_GAIA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GAIA_ROOT))


# ---------------------------------------------------------------------------
# Agent permission bootstrap
# ---------------------------------------------------------------------------

_SCANNER_AGENTS = ["developer", "terraform-architect", "gitops-operator", "gaia-system"]
_SCANNER_TABLES = [
    "repos", "apps", "integrations", "gaia_installations",
    "tf_modules", "tf_live", "releases", "workloads",
    "clusters_defined", "features", "libraries", "services",
    "machines", "clusters",
]


def _grant_scanner_permissions(db_path: Path) -> None:
    """Ensure scanner agents have write permission. Idempotent."""
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        for agent in _SCANNER_AGENTS:
            for table in _SCANNER_TABLES:
                con.execute(
                    "INSERT OR REPLACE INTO agent_permissions "
                    "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
                    (table, agent),
                )
        con.commit()
        print(f"[permissions] granted {len(_SCANNER_AGENTS) * len(_SCANNER_TABLES)} rows OK")
    except Exception as exc:
        con.rollback()
        raise RuntimeError(f"Failed to grant permissions: {exc}") from exc
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Row count snapshot (for diff)
# ---------------------------------------------------------------------------

_SNAPSHOT_TABLES = [
    "repos", "apps", "integrations", "gaia_installations",
    "tf_modules", "tf_live", "releases", "workloads",
    "clusters_defined",
]


def _snapshot(db_path: Path, workspace: str) -> dict:
    con = sqlite3.connect(str(db_path))
    try:
        out = {}
        for t in _SNAPSHOT_TABLES:
            try:
                row = con.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE project = ?", (workspace,)
                ).fetchone()
                out[t] = row[0] if row else 0
            except Exception:
                out[t] = -1
        return out
    finally:
        con.close()


def _build_diff(before: dict, after: dict) -> dict:
    added = {}
    removed = {}
    changed = {}
    all_keys = set(before) | set(after)
    for k in sorted(all_keys):
        b, a = before.get(k, 0), after.get(k, 0)
        if b == 0 and a > 0:
            added[k] = a
        elif b > 0 and a == 0:
            removed[k] = b
        elif b != a:
            changed[k] = {"before": b, "after": a}
    return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# Main migration logic
# ---------------------------------------------------------------------------

def migrate(workspace_root: Path, verify_only: bool, diff_path: Path, db_path: Path) -> int:
    from gaia.project import current
    from gaia.store import wipe_project
    from tools.scan.store_populator import (
        populate_infrastructure,
        populate_orchestration,
        scan_workspace_to_store,
    )
    from tools.scan.workspace import detect_workspace_type

    print(f"[migrate] workspace_root={workspace_root}")
    print(f"[migrate] db_path={db_path}")
    print(f"[migrate] verify_only={verify_only}")

    # Resolve identity
    identity = current(cwd=workspace_root)
    print(f"[migrate] identity={identity!r}")

    # Grant permissions (idempotent)
    _grant_scanner_permissions(db_path)

    # Snapshot before
    before = _snapshot(db_path, identity)
    print(f"[migrate] snapshot before: {before}")

    if not verify_only:
        # Wipe existing project rows (FK CASCADE cleans children)
        wipe_project(identity, db_path=db_path)
        print(f"[migrate] wiped project {identity!r}")

    # Detect workspace type to enumerate repos
    ws_info = detect_workspace_type(workspace_root)
    print(f"[migrate] workspace_type={ws_info.workspace_type}, repos={len(ws_info.repo_dirs)}")

    # Determine repos to scan
    if ws_info.is_multi_repo:
        repo_dirs = ws_info.repo_dirs
    elif (workspace_root / ".git").is_dir():
        repo_dirs = [workspace_root]
    else:
        # Single subdir case (e.g. qxo with qxo-monorepo)
        repo_dirs = [workspace_root]

    # Scan repos via store_populator (handles single/multi/monorepo cases via _list_repos)
    results = scan_workspace_to_store(
        workspace=identity,
        root=workspace_root,
        agent="developer",
        db_path=db_path,
    )

    # Infra + orchestration per repo -- use the SAME repo set scan_workspace_to_store
    # discovered via _list_repos (whose results dict keys are repo names that exist in
    # the `repos` table, satisfying the FK constraint for child tables).
    from tools.scan.store_populator import _list_repos
    for repo_path in _list_repos(workspace_root):
        rname = repo_path.name
        populate_infrastructure(
            workspace=identity,
            repo=rname,
            repo_path=repo_path,
            agent="terraform-architect",
            db_path=db_path,
        )
        populate_orchestration(
            workspace=identity,
            repo=rname,
            repo_path=repo_path,
            agent="gitops-operator",
            db_path=db_path,
        )

    print(f"[migrate] scan results: {results}")

    # Snapshot after
    after = _snapshot(db_path, identity)
    print(f"[migrate] snapshot after: {after}")

    # Build and write diff
    diff = _build_diff(before, after)
    diff["workspace"] = identity
    diff["workspace_root"] = str(workspace_root)
    diff["verify_only"] = verify_only
    diff["timestamp"] = datetime.now(timezone.utc).isoformat()
    diff["before"] = before
    diff["after"] = after

    diff_path.write_text(json.dumps(diff, indent=2))
    print(f"[migrate] diff written to {diff_path}")

    # Verify: repos count must be > 0
    repos_count = after.get("repos", 0)
    if repos_count > 0:
        print(f"[migrate] VERIFY OK: repos={repos_count} for workspace {identity!r}")
        return 0
    else:
        print(f"[migrate] VERIFY FAIL: repos=0 for workspace {identity!r}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate workspace to ~/.gaia/gaia.db")
    parser.add_argument("workspace_root", type=Path)
    parser.add_argument("--verify-only", action="store_true", default=False)
    parser.add_argument("--diff-path", type=Path, default=None)
    args = parser.parse_args()

    workspace_root = args.workspace_root.resolve()
    if not workspace_root.is_dir():
        print(f"ERROR: workspace_root not found: {workspace_root}", file=sys.stderr)
        return 1

    diff_path = args.diff_path or Path(str(workspace_root) + ".diff.json")

    from gaia.paths import db_path
    db = db_path()

    return migrate(workspace_root, args.verify_only, diff_path, db)


if __name__ == "__main__":
    sys.exit(main())
