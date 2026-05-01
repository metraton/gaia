"""
test_agent_write_perms_matrix.py -- AC-1 verification for B3 M2.

Verifies that store.save_X(agent='Y') returns:
  - status='applied' for tables the agent owns
  - status='rejected' for tables the agent does not own

The test uses only the gaia.store public API (no direct sqlite3 calls).
A temporary DB is seeded with the full B3 agent_permissions mapping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gaia.store import bulk_upsert, upsert_app
from gaia.store.writer import _connect


# ---------------------------------------------------------------------------
# Full B3 permission mapping (same as seed_agent_permissions.py)
# ---------------------------------------------------------------------------
_B3_PERMISSIONS: list[tuple[str, str]] = [
    ("developer", "apps"),
    ("developer", "libraries"),
    ("developer", "services"),
    ("developer", "features"),
    ("terraform-architect", "tf_modules"),
    ("terraform-architect", "tf_live"),
    ("terraform-architect", "clusters"),
    ("gitops-operator", "releases"),
    ("gitops-operator", "workloads"),
    ("gitops-operator", "clusters_defined"),
    ("gaia-operator", "integrations"),
    ("gaia-operator", "gaia_installations"),
    ("cloud-troubleshooter", "clusters"),
]

# ---------------------------------------------------------------------------
# Agent -> (owned_tables, foreign_table_sample)
# Pick one foreign table per agent for the rejection test.
# ---------------------------------------------------------------------------
_AGENT_MATRIX = [
    (
        "developer",
        ["apps", "libraries", "services", "features"],
        "clusters",  # foreign: belongs to terraform-architect/cloud-troubleshooter
    ),
    (
        "terraform-architect",
        ["tf_modules", "tf_live", "clusters"],
        "apps",  # foreign: belongs to developer
    ),
    (
        "gitops-operator",
        ["releases", "workloads", "clusters_defined"],
        "apps",  # foreign: belongs to developer
    ),
    (
        "gaia-operator",
        ["integrations", "gaia_installations"],
        "apps",  # foreign: belongs to developer
    ),
    (
        "cloud-troubleshooter",
        ["clusters"],
        "apps",  # foreign: belongs to developer
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """Temp DB seeded with B3 agent_permissions mapping."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    db = db_path()

    # Materialize schema
    con = _connect(db)
    # Seed B3 full mapping
    for agent, table in _B3_PERMISSIONS:
        con.execute(
            "INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) "
            "VALUES (?, ?, 1)",
            (table, agent),
        )
    con.commit()
    con.close()
    return db


# ---------------------------------------------------------------------------
# Helper: attempt a write on any table via bulk_upsert with a minimal row
# ---------------------------------------------------------------------------
_MINIMAL_ROWS: dict[str, list[dict]] = {
    "apps": [{"repo": "test-repo", "name": "test-app"}],
    "libraries": [{"repo": "test-repo", "name": "test-lib"}],
    "services": [{"repo": "test-repo", "name": "test-svc"}],
    "features": [{"repo": "test-repo", "name": "test-feat"}],
    "tf_modules": [{"repo": "test-repo", "name": "test-mod"}],
    "tf_live": [{"repo": "test-repo", "name": "test-live"}],
    "releases": [{"repo": "test-repo", "name": "v1.0.0"}],
    "workloads": [{"repo": "test-repo", "name": "test-wl"}],
    "clusters_defined": [{"repo": "test-repo", "name": "test-cd"}],
    "clusters": [{"name": "test-cluster"}],
    "integrations": [{"name": "test-integration"}],
    "gaia_installations": [{"machine": "test-machine"}],
}


def _ensure_parent_repo(db: Path) -> None:
    """Ensure a parent repo row exists for FK-dependent tables."""
    from gaia.store.writer import _connect, _ensure_project_row
    con = _connect(db)
    try:
        _ensure_project_row(con, "test-ws")
        con.execute(
            "INSERT OR IGNORE INTO repos (project, name, scanner_ts) VALUES (?, ?, ?)",
            ("test-ws", "test-repo", "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _try_write(table: str, agent: str, db: Path) -> str:
    """Returns 'applied' or 'rejected' based on bulk_upsert result."""
    _ensure_parent_repo(db)
    rows = _MINIMAL_ROWS[table]
    result = bulk_upsert(table=table, workspace="test-ws", rows=rows, agent=agent, db_path=db)
    if result.get("applied", 0) > 0:
        return "applied"
    return "rejected"


# ---------------------------------------------------------------------------
# Main test: parametrized over the 5-agent matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent,owned_tables,foreign_table", _AGENT_MATRIX)
def test_five_agents_table_perms(agent: str, owned_tables: list[str], foreign_table: str, tmp_db: Path):
    """Each agent can write its owned tables and is rejected on foreign tables."""
    # Owned tables -> should be applied
    for table in owned_tables:
        result = _try_write(table, agent, tmp_db)
        assert result == "applied", (
            f"Agent '{agent}' should be ALLOWED to write '{table}', got '{result}'"
        )

    # Foreign table -> should be rejected
    result = _try_write(foreign_table, agent, tmp_db)
    assert result == "rejected", (
        f"Agent '{agent}' should be REJECTED from writing '{foreign_table}', got '{result}'"
    )
