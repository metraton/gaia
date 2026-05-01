"""
Tests for gaia.store.writer permission matrix.

Verifies that upsert_app respects agent_permissions:
- developer (allow_write=1 for 'apps') -> status=applied
- gaia-operator (no row in agent_permissions for 'apps') -> status=rejected reason=not_authorized
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gaia.store import upsert_app


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Provide a temp DB for the writer.

    Routes gaia.paths.db_path() at this temp location via GAIA_DATA_DIR.
    Returns the resolved DB path.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def test_upsert_app_permission_matrix(tmp_db):
    """developer -> applied; gaia-operator -> rejected/not_authorized."""
    # Trigger schema creation by issuing a no-op upsert (fails closed because
    # 'gaia-operator' has no row -> rejected). But first we need the schema.
    # Easiest path: connect once to materialize the schema.
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    # Schema bootstrap inserts 1 row (apps, developer, 1) by default.
    rows = con.execute(
        "SELECT table_name, agent_name, allow_write FROM agent_permissions"
    ).fetchall()
    con.close()
    assert ("apps", "developer", 1) in [(r[0], r[1], r[2]) for r in rows]

    # developer -> applied
    res_dev = upsert_app(
        workspace="me",
        repo="gaia",
        name="hello",
        fields={"kind": "service", "description": "test", "status": "active"},
        agent="developer",
        db_path=tmp_db,
    )
    assert res_dev == {"status": "applied"}, res_dev

    # gaia-operator -> rejected, not_authorized
    res_op = upsert_app(
        workspace="me",
        repo="gaia",
        name="hello",
        fields={"kind": "service"},
        agent="gaia-operator",
        db_path=tmp_db,
    )
    assert res_op == {"status": "rejected", "reason": "not_authorized"}, res_op


def test_upsert_app_unknown_agent_rejected(tmp_db):
    """An agent with no agent_permissions row at all is rejected."""
    res = upsert_app(
        workspace="me",
        repo="gaia",
        name="any",
        fields={},
        agent="nonexistent-agent",
        db_path=tmp_db,
    )
    assert res["status"] == "rejected"
    assert res["reason"] == "not_authorized"
