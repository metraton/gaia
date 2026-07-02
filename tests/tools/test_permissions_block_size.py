"""FIX (c) proof: the subagent Permissions block collapses from ~70KB to a
few hundred bytes once readable/writable sections are deduped.

Renders the Permissions block the way context_injector does (writable / readable
/ context_update_required as annotated YAML-KV) from BEFORE (raw duplicated rows,
the field state) vs AFTER (deduped by load_provider_contracts), and asserts the
size collapse plus one-entry-per-section.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "context"))

from context_provider import load_provider_contracts


def _render_permissions_block(readable: list[str], writable: list[str]) -> str:
    """Mirror context_injector's Permissions block payload (the write_perms_dict
    rendered as lines). We only need the section lists to measure the bloat."""
    lines = ["# Permissions", "", "writable:"]
    for s in writable:
        lines.append(f"  - {s}")
    lines.append("readable:")
    for s in readable:
        lines.append(f"  - {s}")
    return "\n".join(lines)


def _make_bloated_db(tmp_path, n_dups: int) -> Path:
    db = tmp_path / "gaia.db"
    con = sqlite3.connect(str(db))
    con.execute(
        """
        CREATE TABLE agent_contract_permissions (
            agent_name    TEXT NOT NULL,
            contract_name TEXT NOT NULL,
            can_read      INTEGER NOT NULL DEFAULT 0,
            can_write     INTEGER NOT NULL DEFAULT 0,
            cloud_scope   TEXT,
            PRIMARY KEY (agent_name, contract_name, cloud_scope)
        )
        """
    )
    # The field state: the same two contracts, each duplicated n_dups times as
    # NULL-scope rows (NULL bypasses the PK).
    for _ in range(n_dups):
        con.execute(
            "INSERT INTO agent_contract_permissions VALUES "
            "('developer', 'project_identity', 1, 0, NULL)"
        )
        con.execute(
            "INSERT INTO agent_contract_permissions VALUES "
            "('developer', 'stack', 1, 1, NULL)"
        )
    con.commit()
    con.close()
    return db


class TestPermissionsBlockSizeCollapse:
    def test_before_after_size(self, tmp_path):
        # 1977 duplicates reproduces the observed field magnitude.
        n_dups = 1977
        db = _make_bloated_db(tmp_path, n_dups)

        # BEFORE: raw rows, no dedupe -- what the old builder rendered.
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT contract_name, can_read, can_write "
            "FROM agent_contract_permissions WHERE agent_name='developer'"
        ).fetchall()
        con.close()
        raw_readable = [r["contract_name"] for r in rows if r["can_read"]]
        raw_writable = [r["contract_name"] for r in rows if r["can_write"]]
        before_block = _render_permissions_block(raw_readable, raw_writable)

        # AFTER: through the fixed loader (deduped).
        contracts = load_provider_contracts("developer", "gcp", db_path=db)
        readable = contracts["agents"]["developer"]["read"]
        writable = contracts["agents"]["developer"]["write"]
        after_block = _render_permissions_block(readable, writable)

        # PROOF (printed on failure; the assertions carry the contract):
        print(f"BEFORE: readable={len(raw_readable)} writable={len(raw_writable)} "
              f"block_bytes={len(before_block)}")
        print(f"AFTER:  readable={len(readable)} writable={len(writable)} "
              f"block_bytes={len(after_block)}")

        # Before: thousands of repeated entries, tens of KB.
        assert len(raw_readable) == n_dups * 2  # project_identity + stack, each x1977
        assert len(before_block) > 30_000

        # After: each section exactly once, block is a few hundred bytes.
        assert readable == ["project_identity", "stack"]
        assert writable == ["stack"]
        assert len(after_block) < 1_000
        # >97% reduction.
        assert len(after_block) < len(before_block) * 0.03
