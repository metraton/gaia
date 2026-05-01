"""
Tests for gaia.store.provider.get_context.

Verifies that the provider returns the JSON shape that agents expect:
- top-level keys: identity, stack, environment, git, workspace
- workspace.repos populated when rows exist
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
    and workspace.repos is populated when rows exist."""
    from gaia.store import upsert_repo, get_context
    from gaia.store.writer import _connect

    # Insert permission for 'developer' on 'repos' so upsert_repo applies.
    con = _connect(tmp_db)
    con.execute(
        "INSERT OR REPLACE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('repos', 'developer', 1)"
    )
    con.commit()
    con.close()

    res = upsert_repo(
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
    assert "repos" in ctx["workspace"]
    assert len(ctx["workspace"]["repos"]) == 1
    repo = ctx["workspace"]["repos"][0]
    assert repo["name"] == "gaia"
    assert repo["role"] == "infra"
    assert repo["primary_language"] == "python"


def test_get_context_empty_workspace_returns_safe_dict(tmp_db):
    """A workspace with no rows returns identity == workspace and empty lists."""
    from gaia.store import get_context
    from gaia.store.writer import _connect

    # Materialize schema
    _connect(tmp_db).close()

    ctx = get_context("nonexistent-workspace", db_path=tmp_db)
    assert ctx["identity"] == "nonexistent-workspace"
    assert ctx["workspace"]["repos"] == []
    assert ctx["workspace"]["apps"] == []
