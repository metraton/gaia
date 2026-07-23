"""
AC-7 -- store write-guard + permissions + fleet seed (M3, task T8).

The handoff write-guard is INVERTED here (brief contract-as-managed-data): the
prior gate was curator-only (every subagent dispatch FORBIDDEN, only the
SubagentStop hook / a curator could write). Under contract-as-managed-data the
terminal ``agent_contract_handoffs`` row is finalized BY the agent itself via
``gaia contract finalize``, so the gate flips to finalize-by-any-SEEDED-agent.

This module enumerates AC-7's four clauses:

  1. raw sqlite blocked        -- a raw INSERT that bypasses the validated
                                  writer cannot forge a terminal row: SQLite's
                                  own integrity constraints (the task_status
                                  CHECK enum, NOT NULL columns) reject it.
  2. authorized finalize       -- GAIA_DISPATCH_AGENT set to a SEEDED fleet
                                  agent -> a row lands with the correct
                                  task_status.
  3. unauthorized -> rejected  -- GAIA_DISPATCH_AGENT set to an identity NOT in
                                  the fleet seed -> HandoffWriteForbidden, no
                                  row written.
  4. POSITIVE fleet            -- every agent under ``agents/`` is present in
                                  the loaded seed AND can finalize.

The guard reads the fleet from ``gaia.state.permissions.handoff_writer_fleet``,
which is seeded from ``agents/*.md`` frontmatter (marker
``contract_handoff_writer: true``). This is the SAME writer + guard T9 reuses
for its conditional hook backstop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from gaia.state.permissions import (
    _parse_agent_frontmatter,
    handoff_writer_fleet,
    is_handoff_writer,
)
from gaia.store.writer import (
    HandoffWriteForbidden,
    finalize_agent_contract_handoff,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _REPO_ROOT / "agents"

WORKSPACE = "me"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """An isolated DB path. The writer's ``_connect`` materializes the real
    schema from ``gaia/store/schema.sql`` on first use, so this exercises the
    live v28 table (contract_id UNIQUE + task_status CHECK), not a fixture."""
    return tmp_path / "gaia.db"


@pytest.fixture(autouse=True)
def _clean_dispatch_and_cache(monkeypatch):
    """Each test starts with no dispatch identity and a fresh fleet cache.

    The fleet is lru_cache'd (it is a static property of the source tree); a
    test that reads it must not inherit another test's cached value."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    handoff_writer_fleet.cache_clear()
    yield
    handoff_writer_fleet.cache_clear()


def _agent_names_from_dir() -> list[str]:
    """The ground-truth fleet: every agent .md under agents/ (skip README)."""
    names: list[str] = []
    for md in sorted(_AGENTS_DIR.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        name, _ = _parse_agent_frontmatter(md.read_text(encoding="utf-8"))
        if name:
            names.append(name)
    return names


def _envelope(plan_status: str = "COMPLETE") -> str:
    return json.dumps({
        "agent_status": {
            "agent_state": plan_status,
            "agent_id": "a1234abcd",
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["ac7"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    })


def _count_rows(db_path: Path, contract_id: str | None = None) -> int:
    if not db_path.is_file():
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        if contract_id is None:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs"
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
                (contract_id,),
            ).fetchone()
        return int(row[0])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Clause 2 -- authorized finalize writes the correct task_status
# ---------------------------------------------------------------------------

def test_seeded_agent_finalize_writes_correct_task_status(db, monkeypatch):
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-system")
    cid = "a1234abcd.tok-authz"
    res = finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id="a1234abcd",
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=_envelope("COMPLETE"),
        db_path=db,
    )
    assert res["status"] == "applied"
    assert res["created"] is True

    con = sqlite3.connect(str(db))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT agent_id, agent_state FROM agent_contract_handoffs WHERE contract_id = ?",
            (cid,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row["agent_state"] == "COMPLETE"
    assert row["agent_id"] == "a1234abcd"


def test_unset_dispatch_is_allowed(db):
    """The CLI / human / hook path (no dispatch identity) must stay permitted --
    the harness-agnostic ``gaia contract finalize`` runs without the env var."""
    assert "GAIA_DISPATCH_AGENT" not in __import__("os").environ
    res = finalize_agent_contract_handoff(
        contract_id="a1234abcd.tok-unset",
        agent_id="a1234abcd",
        workspace=WORKSPACE,
        agent_state="IN_PROGRESS",
        raw_handoff_json=_envelope("IN_PROGRESS"),
        db_path=db,
    )
    assert res["created"] is True
    assert _count_rows(db, "a1234abcd.tok-unset") == 1


# ---------------------------------------------------------------------------
# Clause 3 -- an unseeded / rogue dispatch identity is rejected
# ---------------------------------------------------------------------------

def test_unseeded_agent_is_rejected(db, monkeypatch):
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "rogue-agent")
    assert is_handoff_writer("rogue-agent") is False
    with pytest.raises(HandoffWriteForbidden) as exc:
        finalize_agent_contract_handoff(
            contract_id="a1234abcd.tok-rogue",
            agent_id="a1234abcd",
            workspace=WORKSPACE,
            agent_state="COMPLETE",
            raw_handoff_json=_envelope("COMPLETE"),
            db_path=db,
        )
    msg = str(exc.value)
    assert "rogue-agent" in msg
    assert "not a seeded fleet agent" in msg
    # The guard fires BEFORE any write: no row (and no DB even created).
    assert _count_rows(db, "a1234abcd.tok-rogue") == 0


# ---------------------------------------------------------------------------
# Clause 1 -- a raw sqlite write cannot forge a valid terminal row
# ---------------------------------------------------------------------------

def test_raw_sqlite_forged_row_blocked_by_schema(db):
    """A raw INSERT that bypasses the validated writer is rejected by SQLite's
    own integrity constraints -- the terminal row cannot be forged out-of-band
    with an out-of-enum status or without its required fields."""
    # First materialize the schema + a workspace row via a legitimate finalize.
    finalize_agent_contract_handoff(
        contract_id="a1234abcd.tok-seed",
        agent_id="a1234abcd",
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=_envelope("COMPLETE"),
        db_path=db,
    )

    con = sqlite3.connect(str(db))
    con.execute("PRAGMA foreign_keys = ON")
    try:
        # (a) out-of-enum agent_state -> CHECK constraint rejects it.
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, agent_id, workspace, agent_state, raw_handoff_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("raw.bogus", "aRAW", WORKSPACE, "BOGUS", "{}", "2026-07-08T00:00:00Z"),
            )
        con.rollback()

        # (b) missing NOT NULL agent_id -> NOT NULL constraint rejects it.
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, workspace, agent_state, raw_handoff_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("raw.nokey", WORKSPACE, "COMPLETE", "{}", "2026-07-08T00:00:00Z"),
            )
        con.rollback()
    finally:
        con.close()

    # No forged row survived; only the one legitimate finalize remains.
    assert _count_rows(db) == 1


# ---------------------------------------------------------------------------
# Clause 4 -- POSITIVE: every agent under agents/ is seeded and can finalize
# ---------------------------------------------------------------------------

def test_every_agent_under_agents_dir_is_seeded():
    names = _agent_names_from_dir()
    # sanity: the fleet directory is non-trivial
    assert len(names) >= 8, names
    fleet = handoff_writer_fleet()
    missing = [n for n in names if n not in fleet]
    assert missing == [], f"agents present under agents/ but absent from seed: {missing}"


def test_every_seeded_agent_can_finalize(db, monkeypatch):
    names = _agent_names_from_dir()
    for i, name in enumerate(names):
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", name)
        cid = f"a1234abcd.tok-{i}"
        res = finalize_agent_contract_handoff(
            contract_id=cid,
            agent_id="a1234abcd",
            workspace=WORKSPACE,
            agent_state="COMPLETE",
            raw_handoff_json=_envelope("COMPLETE"),
            db_path=db,
        )
        assert res["created"] is True, f"{name} could not finalize"
        assert _count_rows(db, cid) == 1, f"{name} row missing"

    # One row per seeded agent, all landed.
    assert _count_rows(db) == len(names)
