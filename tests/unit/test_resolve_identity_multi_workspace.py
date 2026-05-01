"""
Fix 1 regression tests: _resolve_identity uses workspace_path arg, not cwd.

Verifies that when multiple workspaces are ingested in the same Python process,
each workspace receives the identity derived from its own git remote -- not from
the process cwd.  This was the root cause of all projects receiving identity='me'
in the B5 initial rescan.
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


class TestResolveIdentityWithWorkspacePath:
    """Unit tests for gaia.store.writer._resolve_identity(workspace, workspace_path)."""

    def test_resolve_identity_uses_workspace_path_not_cwd(self, tmp_path, monkeypatch):
        """Identity must derive from workspace_path's git remote, not cwd."""
        from gaia.store.writer import _resolve_identity

        repo_a = _make_repo(tmp_path, "repo-a", "git@github.com:owner/repo-a.git")
        repo_b = _make_repo(tmp_path, "repo-b", "git@github.com:owner/repo-b.git")

        # cwd is tmp_path (no git remote) -- without the fix both would return "tmp_path.name"
        monkeypatch.chdir(tmp_path)

        identity_a = _resolve_identity("ws-a", workspace_path=repo_a)
        identity_b = _resolve_identity("ws-b", workspace_path=repo_b)

        assert identity_a == "github.com/owner/repo-a"
        assert identity_b == "github.com/owner/repo-b"
        assert identity_a != identity_b

    def test_resolve_identity_fallback_without_path(self, tmp_path, monkeypatch):
        """When workspace_path is None, falls back to workspace.lower() or cwd-based."""
        from gaia.store.writer import _resolve_identity

        # cwd has no git -- fallback should return workspace.lower()
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(str(plain))

        result = _resolve_identity("MyWorkspace", workspace_path=None)
        # Falls back: either cwd basename or workspace.lower()
        assert isinstance(result, str)
        assert result  # non-empty


class TestPopulateRepoMultiWorkspaceIdentity:
    """Integration test: two workspaces scanned in same process get distinct identities."""

    def test_two_workspaces_get_distinct_identities(self, tmp_db, tmp_path, monkeypatch):
        """
        Scanning two repos with different git remotes in the same process must
        produce two distinct projects.identity values, not both collapsing to cwd.
        """
        from gaia.store.writer import _connect
        from tools.scan.store_populator import populate_repo

        con = _connect(tmp_db)
        _grant(con, "repos", "developer")
        con.close()

        repo_me = _make_repo(tmp_path, "me", "git@github.com:metraton/me.git")
        repo_bwiz = _make_repo(tmp_path, "bildwiz", "https://bitbucket.org/aaxisdigital/bildwiz.git")

        # Process cwd is tmp_path -- has no git remote; without the fix both
        # repos would get tmp_path.name as identity.
        monkeypatch.chdir(str(tmp_path))

        res_me = populate_repo("ws-me", repo_me, "developer", db_path=tmp_db)
        res_bwiz = populate_repo("ws-bwiz", repo_bwiz, "developer", db_path=tmp_db)

        assert res_me["applied"] == 1
        assert res_bwiz["applied"] == 1

        con = _connect(tmp_db)
        rows = {
            r["name"]: r["identity"]
            for r in con.execute("SELECT name, identity FROM projects").fetchall()
        }
        con.close()

        assert rows.get("ws-me") == "github.com/metraton/me", (
            f"Expected github.com/metraton/me, got {rows.get('ws-me')!r}"
        )
        assert rows.get("ws-bwiz") == "bitbucket.org/aaxisdigital/bildwiz", (
            f"Expected bitbucket.org/aaxisdigital/bildwiz, got {rows.get('ws-bwiz')!r}"
        )
        assert rows["ws-me"] != rows["ws-bwiz"], (
            "Both workspaces collapsed to the same identity -- fix did not apply"
        )
