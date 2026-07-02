"""Tests for seed_contract_permissions idempotency (root cause of FIX c).

INSERT OR REPLACE with cloud_scope=NULL does NOT dedupe, because SQLite treats
NULL as distinct in the composite PRIMARY KEY (agent_name, contract_name,
cloud_scope) -- NULL never equals NULL, so no ON CONFLICT fires and every
re-run APPENDS a fresh duplicate row. Left unchecked this accumulated ~1977
duplicate rows per (agent, contract) in the field. grant_contract_permissions
now clears each agent's NULL-scope rows before inserting, so a re-seed replaces
rather than appends.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.fixtures.db_helpers import bootstrap_gaia_schema
from tools.scan.seed_contract_permissions import grant_contract_permissions

_AGENT_MD = """---
name: fixture-agent
project_context_contracts:
  read:
    - project_identity
    - stack
    - git
  write:
    - stack
---
# Fixture Agent
Body.
"""


def _write_agent(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "fixture-agent.md").write_text(_AGENT_MD, encoding="utf-8")


def _count_rows(db_path: Path) -> dict[tuple[str, str], int]:
    con = sqlite3.connect(str(db_path))
    rows = con.execute(
        "SELECT agent_name, contract_name, COUNT(*) "
        "FROM agent_contract_permissions GROUP BY agent_name, contract_name"
    ).fetchall()
    con.close()
    return {(r[0], r[1]): r[2] for r in rows}


class TestSeedIdempotency:
    def test_repeated_seed_does_not_duplicate_rows(self, tmp_path):
        db = tmp_path / "gaia.db"
        bootstrap_gaia_schema(db)
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir)

        # Seed several times -- the field bug appended a fresh NULL-scope row
        # per (agent, contract) on every run.
        for _ in range(5):
            grant_contract_permissions(db, agents_dir=agents_dir)

        counts = _count_rows(db)
        # Exactly one row per (agent, contract) after any number of re-seeds.
        assert counts == {
            ("fixture-agent", "project_identity"): 1,
            ("fixture-agent", "stack"): 1,
            ("fixture-agent", "git"): 1,
        }, f"re-seed duplicated rows: {counts}"

    def test_provider_scoped_overlay_survives_reseed(self, tmp_path):
        db = tmp_path / "gaia.db"
        bootstrap_gaia_schema(db)
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir)

        # Insert a provider-scoped overlay row that the seed must NOT touch.
        con = sqlite3.connect(str(db))
        con.execute(
            "INSERT INTO agent_contract_permissions "
            "(agent_name, contract_name, can_read, can_write, cloud_scope) "
            "VALUES ('fixture-agent', 'infrastructure', 1, 0, 'gcp')"
        )
        con.commit()
        con.close()

        grant_contract_permissions(db, agents_dir=agents_dir)
        grant_contract_permissions(db, agents_dir=agents_dir)

        con = sqlite3.connect(str(db))
        overlay = con.execute(
            "SELECT COUNT(*) FROM agent_contract_permissions "
            "WHERE cloud_scope = 'gcp'"
        ).fetchone()[0]
        con.close()
        assert overlay == 1, "provider-scoped overlay was clobbered by re-seed"
