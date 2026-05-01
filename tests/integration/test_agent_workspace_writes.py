"""
test_agent_workspace_writes.py -- AC-3 integration test.

When developer calls upsert on `apps`, table `clusters` remains byte-identical
(SELECT diff = 0). Validates per-table write isolation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gaia.store import upsert_app
from gaia.store.writer import _connect


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    db = db_path()
    con = _connect(db)
    # Permissions
    con.execute(
        "INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('apps', 'developer', 1)"
    )
    # Project + repo + apps + clusters seed
    con.execute(
        "INSERT OR IGNORE INTO projects (name, identity, created_at) VALUES ('iso-ws', 'iso-ws', '2026-01-01T00:00:00Z')"
    )
    con.execute(
        "INSERT OR IGNORE INTO repos (project, name, scanner_ts) VALUES ('iso-ws', 'r1', '2026-01-01T00:00:00Z')"
    )
    con.execute(
        "INSERT OR IGNORE INTO apps (project, repo, name, kind, scanner_ts) VALUES ('iso-ws', 'r1', 'pre-existing', 'service', '2026-01-01T00:00:00Z')"
    )
    con.execute(
        "INSERT OR IGNORE INTO clusters (project, name, provider, region, attributes, scanner_ts) VALUES ('iso-ws', 'gke-prod', 'gke', 'us-central1', '{\"node_count\": 3}', '2026-01-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    return db


def _snapshot_clusters(db: Path) -> list[tuple]:
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT project, name, provider, region, attributes, scanner_ts FROM clusters ORDER BY project, name"
    ).fetchall()
    con.close()
    return rows


def test_developer_write_isolates_clusters(tmp_db: Path):
    """developer's upsert_app on 'apps' must not modify 'clusters'."""
    snapshot_before = _snapshot_clusters(tmp_db)
    assert len(snapshot_before) == 1, "fixture seed expected 1 row in clusters"

    result = upsert_app(
        workspace="iso-ws",
        repo="r1",
        name="new-app-from-dev",
        fields={"kind": "service", "description": "new app", "status": "active"},
        agent="developer",
        db_path=tmp_db,
    )
    assert result == {"status": "applied"}

    snapshot_after = _snapshot_clusters(tmp_db)
    assert snapshot_before == snapshot_after, (
        f"clusters table was modified by developer's upsert_app:\n"
        f"  before: {snapshot_before}\n"
        f"  after:  {snapshot_after}"
    )
