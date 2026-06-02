"""
Tests for gaia.store.provider.get_context.

Verifies that the provider returns the JSON shape that agents expect:
- top-level keys: identity, stack, environment, git, workspace
- workspace.projects populated when rows exist
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def test_get_context_shape(tmp_db):
    """get_context('me') returns keys identity/stack/environment/git/workspace
    and workspace.projects is populated when rows exist."""
    from gaia.store import upsert_project, get_context
    from gaia.store.writer import _connect

    # Insert permission for 'developer' on 'projects' so upsert_project applies.
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('projects', 'developer', 1)"
    )
    con.commit()
    con.close()

    res = upsert_project(
        workspace="me",
        name="gaia",
        fields={
            "role": "infra",
            "remote_url": "git@github.com:metraton/gaia.git",
            "platform": "github",
            "primary_language": "python",
        },
        agent="developer",
        db_path=tmp_db,
    )
    assert res["status"] == "applied"

    ctx = get_context("me", db_path=tmp_db)

    # Top-level keys present
    for key in ("identity", "stack", "environment", "git", "workspace"):
        assert key in ctx, f"missing top-level key: {key}"

    assert isinstance(ctx["workspace"], dict)
    assert "projects" in ctx["workspace"]
    assert len(ctx["workspace"]["projects"]) == 1
    project = ctx["workspace"]["projects"][0]
    assert project["name"] == "gaia"
    assert project["role"] == "infra"
    assert project["primary_language"] == "python"


def test_get_context_nonexistent_workspace_returns_none(tmp_db):
    """A workspace not in workspaces table returns None (Fix #5: caller emits exit 1)."""
    from gaia.store import get_context
    from gaia.store.writer import _connect

    # Materialize schema without inserting any workspace row
    _connect(tmp_db).close()

    ctx = get_context("nonexistent-workspace", db_path=tmp_db)
    assert ctx is None


def test_get_context_missing_workspace_hidden_by_default(tmp_db):
    """A workspace with status='missing' is invisible in the active view (include_missing=False).
    With include_missing=True it is returned normally.
    This mirrors the project-level soft-delete filter at the workspace level (v17)."""
    from gaia.store import get_context
    from gaia.store.writer import _connect, mark_workspace_demoted, _ensure_workspace_row

    # Create a workspace row and then demote it.
    con = _connect(tmp_db)
    _ensure_workspace_row(con, "demoted-ws", workspace_path=None)
    con.commit()
    con.close()

    demoted = mark_workspace_demoted("demoted-ws", db_path=tmp_db)
    assert demoted is True, "mark_workspace_demoted should return True for a previously-active row"

    # Active view (default): demoted workspace must be hidden.
    ctx_default = get_context("demoted-ws", db_path=tmp_db)
    assert ctx_default is None, (
        "get_context should return None for a missing workspace when include_missing=False"
    )

    # include_missing=True: demoted workspace must be returned.
    ctx_all = get_context("demoted-ws", db_path=tmp_db, include_missing=True)
    assert ctx_all is not None, (
        "get_context should return context for a missing workspace when include_missing=True"
    )
    assert ctx_all["identity"] == "demoted-ws"


def test_get_context_identity_is_workspace_name(tmp_db):
    """identity field is workspaces.name, not a git remote URL (Fix #4)."""
    from gaia.store import get_context, upsert_project
    from gaia.store.writer import _connect

    # Set up permissions and create a project via upsert_project
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('projects', 'developer', 1)"
    )
    con.commit()
    con.close()

    upsert_project(
        workspace="my-workspace",
        name="some-repo",
        fields={"remote_url": "https://github.com/org/some-repo.git"},
        agent="developer",
        db_path=tmp_db,
    )

    ctx = get_context("my-workspace", db_path=tmp_db)
    assert ctx is not None
    # identity must be the workspace name, not the remote URL
    assert ctx["identity"] == "my-workspace"
