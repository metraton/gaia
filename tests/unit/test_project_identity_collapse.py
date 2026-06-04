"""M1-T2 regression tests: stable, vantage-independent project identity.

A physical repository scanned from two different roots -- once from the
workspace root and once from inside the repo's own subdirectory (which resolves
a DIFFERENT workspace identity) -- must collapse into a SINGLE `projects` row
keyed by the stable `project_identity` (git-common-dir realpath > normalized
remote > realpath path), instead of duplicating across (workspace, name).

This is the AC-2 behavior: scanning the same repo from the workspace root and
again from the repo subdir produces exactly one project row for that canonical
path (same-path-multi-workspace -> 0 duplicates).

We also assert the inverse guard: two DIFFERENT physical repos must remain two
distinct rows -- the collapse must not over-merge.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, table, agent):
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) "
        "VALUES (?, ?, 1)",
        (table, agent),
    )
    con.commit()


def _make_repo(parent: Path, name: str, remote_url: str | None = None) -> Path:
    """Create a minimal git repo at parent/name with optional origin remote."""
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(repo), check=True,
        )
    (repo / "package.json").write_text("{}")
    return repo


# ---------------------------------------------------------------------------
# Identity resolution unit: git-common-dir wins, then remote, then realpath.
# ---------------------------------------------------------------------------

class TestResolveProjectIdentity:
    def test_git_common_dir_is_vantage_independent(self, tmp_path):
        """Same repo, resolved from root and from a nested subdir, yields the
        same project_identity (the shared .git common dir, realpath'd)."""
        from tools.scan.store_populator import resolve_project_identity

        repo = _make_repo(tmp_path, "repo", "git@github.com:owner/repo.git")
        subdir = repo / "src" / "deep"
        subdir.mkdir(parents=True)

        id_from_root = resolve_project_identity(repo)
        id_from_subdir = resolve_project_identity(subdir)

        assert id_from_root == id_from_subdir
        # It is the realpath of the repo's .git directory.
        assert id_from_root == str((repo / ".git").resolve())

    def test_distinct_repos_get_distinct_identities(self, tmp_path):
        from tools.scan.store_populator import resolve_project_identity

        repo_a = _make_repo(tmp_path, "repo-a", "git@github.com:owner/repo-a.git")
        repo_b = _make_repo(tmp_path, "repo-b", "git@github.com:owner/repo-b.git")

        assert resolve_project_identity(repo_a) != resolve_project_identity(repo_b)

    def test_falls_back_to_realpath_when_no_git(self, tmp_path):
        """A plain (non-git) directory falls back to its realpath -- never empty."""
        from tools.scan.store_populator import resolve_project_identity

        plain = tmp_path / "plain"
        plain.mkdir()
        ident = resolve_project_identity(plain)
        assert ident == str(plain.resolve())
        assert ident  # non-empty


# ---------------------------------------------------------------------------
# AC-2: same physical repo scanned from two roots collapses to ONE row.
# ---------------------------------------------------------------------------

class TestProjectIdentityCollapse:
    def test_same_repo_two_roots_collapses_to_one_row(self, tmp_db, tmp_path, monkeypatch):
        """Populate the same physical repo from the workspace root and from the
        repo subdir (different workspace identities). Exactly ONE projects row
        survives for that canonical path -- 0 duplicates (AC-2)."""
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_project, resolve_project_identity

        con = _connect(tmp_db)
        _grant(con, "projects", "developer")
        con.close()

        repo = _make_repo(tmp_path, "repo", "git@github.com:owner/repo.git")
        repo_subdir = repo / "packages" / "core"
        repo_subdir.mkdir(parents=True)

        canonical_identity = resolve_project_identity(repo)

        # Scan #1: from the workspace root -> workspace="ws-root", name="repo".
        res1 = populate_project("ws-root", repo, "developer", db_path=tmp_db)
        # Scan #2: same physical repo, but the scan vantage is the subdir, which
        # resolves a different *workspace* identity ("ws-nested").
        res2 = populate_project("ws-nested", repo_subdir, "developer", db_path=tmp_db)

        assert res1["applied"] == 1
        assert res2["applied"] == 1
        # Both scans resolved the SAME stable project identity.
        assert res1["project_identity"] == canonical_identity
        assert res2["project_identity"] == canonical_identity

        con = _connect(tmp_db)
        # AC-2 query: same-path-multi-workspace -> count rows for this identity.
        n = con.execute(
            "SELECT COUNT(*) FROM projects WHERE project_identity = ?",
            (canonical_identity,),
        ).fetchone()[0]
        # And total rows must also be one (no orphan duplicate under another PK).
        total = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        # The surviving row keeps the FIRST-seen (workspace, name) PK.
        survivor = con.execute(
            "SELECT workspace, name FROM projects WHERE project_identity = ?",
            (canonical_identity,),
        ).fetchone()
        con.close()

        assert n == 1, f"expected exactly 1 row for canonical path, got {n}"
        assert total == 1, f"expected exactly 1 project row total, got {total}"
        assert survivor["workspace"] == "ws-root"
        assert survivor["name"] == "repo"

    def test_distinct_repos_remain_distinct_rows(self, tmp_db, tmp_path, monkeypatch):
        """Two DIFFERENT physical repos must NOT collapse -- the identity merge
        must not over-merge into a single row."""
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_project

        con = _connect(tmp_db)
        _grant(con, "projects", "developer")
        con.close()

        repo_a = _make_repo(tmp_path, "repo-a", "git@github.com:owner/repo-a.git")
        repo_b = _make_repo(tmp_path, "repo-b", "git@github.com:owner/repo-b.git")

        populate_project("ws", repo_a, "developer", db_path=tmp_db)
        populate_project("ws", repo_b, "developer", db_path=tmp_db)

        con = _connect(tmp_db)
        total = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        identities = {
            r[0] for r in con.execute(
                "SELECT project_identity FROM projects"
            ).fetchall()
        }
        con.close()

        assert total == 2, f"expected 2 distinct project rows, got {total}"
        assert len(identities) == 2, "distinct repos collapsed -- over-merge bug"

    def test_rescan_same_root_is_idempotent(self, tmp_db, tmp_path, monkeypatch):
        """Re-scanning the same repo from the same root twice stays one row."""
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_project

        con = _connect(tmp_db)
        _grant(con, "projects", "developer")
        con.close()

        repo = _make_repo(tmp_path, "repo", "git@github.com:owner/repo.git")

        populate_project("ws", repo, "developer", db_path=tmp_db)
        populate_project("ws", repo, "developer", db_path=tmp_db)

        con = _connect(tmp_db)
        total = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        con.close()
        assert total == 1
