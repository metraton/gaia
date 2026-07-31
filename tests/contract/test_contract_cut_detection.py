"""AC-3 -- a cut is a QUERY, and a clean closure is something a turn earns.

Until v39 the fact that a turn did not close cleanly lived only INSIDE
``raw_handoff_json`` (``degraded`` / ``reaped`` / ``salvaged``). No SQL predicate
reaches a JSON body without parsing every row, so "which turns were cut" was not
a question the substrate could answer. ``agent_contract_handoffs.cut_reason``
lifts that fact into a column.

The design under test is an INVERSION, and every clause below is a consequence
of it: the mark is stamped at BIRTH (``insert_dispatched_handoff``) and ONLY
``finalize_agent_contract_handoff`` called WITHOUT a ``cut_reason`` clears it.
Cleanliness is earned by finalizing, never inherited by disappearing.

Query clauses -- a cut is findable WITHOUT touching raw_handoff_json:
  (a) a born row nobody ever closed (the hard harness cut: no SubagentStop, no
      closure path, no second write at all) is still marked;
  (b) mirroring partial evidence onto that row does not launder it clean;
  (c) each closure lane -- reap, backstop capture, truncation salvage -- lands
      its own reason;
  (d) the operator query itself never selects raw_handoff_json.

Clean clauses -- a clean closure NEVER carries the mark:
  (e) born -> the agent's own finalize -> NULL (the birth stamp is cleared);
  (f) a legacy finalize with no born row behind it -> NULL;
  (g) a SUPERSEDED scaffold (the turn recorded its own contract elsewhere) ->
      NULL, the same split the degraded/reaped envelope flags already make;
  (h) end to end through the real CLI: adopt, fill, finalize -> NULL.

Migration clauses:
  (i) applying v38_to_v39 to a live v38-shaped DB adds the column and the
      partial index and backfills rows still in 'DISPATCHED';
  (j) replaying it is a no-op (the floor model replays every migration on every
      fresh install);
  (k) no row is lost and no already-decided row is re-marked;
  (l) the literal the migration backfills with IS the Python SSOT value.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.contract.drafts import mint_draft_id, save_draft  # noqa: E402
from gaia.state import (  # noqa: E402
    CUT_REASON_BACKSTOP_CAPTURE,
    CUT_REASON_NEVER_FINALIZED,
    CUT_REASON_REAPED,
    CUT_REASON_SALVAGED_TRUNCATION,
    CUT_REASONS,
)
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    mirror_partial_contract_handoff,
)
from modules.agents.handoff_persister import (  # noqa: E402
    close_born_dispatch_row,
    persist_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v38_to_v39.sql"

_INDEX_NAME = "idx_agent_contract_handoffs_cut"

WORKSPACE = "me"
AGENT_ID = valid_agent_id("cut-detection")
AGENT_NAME = "gaia-system"
PLAN_ID = 47
TASK_ID = 199
SESSION = "sess-cut"

# The operator's question, as an actual query. raw_handoff_json is deliberately
# ABSENT from the projection: if answering "was this turn cut" needed the JSON,
# the column would not have solved anything.
_CUT_QUERY = (
    "SELECT contract_id, agent_id, agent_state, cut_reason "
    "FROM agent_contract_handoffs WHERE cut_reason IS NOT NULL ORDER BY id"
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """An isolated DB path; the writer materializes the real schema."""
    return tmp_path / "gaia.db"


def _terminal_envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "result": "pass",
                             "details": "cut detection"},
        },
        "consolidation_report": None,
        "approval_request": None,
        "failure_report": None,
    }


def _partial_envelope(files=()) -> dict:
    envelope = _terminal_envelope()
    envelope["agent_status"]["agent_state"] = "IN_PROGRESS"
    envelope["agent_status"]["pending_steps"] = ["still working"]
    envelope["agent_status"]["next_action"] = "keep going"
    envelope["evidence_report"]["files_checked"] = list(files)
    envelope["evidence_report"].pop("verification")
    return envelope


def _seed_binding_targets(db_path: Path) -> None:
    """Materialize the schema + the briefs -> plans -> tasks FK chain."""
    finalize_agent_contract_handoff(
        contract_id=f"{AGENT_ID}.seed",
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_terminal_envelope()),
        db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-adoptado-en-dispatch", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 4, "structural cut marker", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _born(db_path: Path, contract_id: str, **overrides) -> None:
    kwargs = dict(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        plan_task_id=TASK_ID,
        plan_id=PLAN_ID,
        kind="task_execution",
        session_id=SESSION,
        agent_name=AGENT_NAME,
        db_path=db_path,
    )
    kwargs.update(overrides)
    insert_dispatched_handoff(**kwargs)


def _cut_reason(db_path: Path, contract_id: str):
    """Read ONLY the marker column -- never the envelope."""
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT cut_reason FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


def _cut_rows(db_path: Path) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(_CUT_QUERY).fetchall()
    finally:
        con.close()


def _task_info(db_path: Path) -> dict:
    return {
        "agent_id": AGENT_ID,
        "agent": AGENT_NAME,
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


# ---------------------------------------------------------------------------
# (a) the hard case: born, never closed by anything at all
# ---------------------------------------------------------------------------

def test_contract_cut_detection_born_and_never_finalized_is_marked(db):
    """The measured harness cut: SubagentStop never fires, nothing else runs.

    No closure path can help here -- there is no second write of any kind. The
    row reads as a cut because the BIRTH stamped it, which is the whole reason
    the column is default-marked instead of default-clean.
    """
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.never-closed"
    _born(db, cid)

    assert _cut_reason(db, cid) == CUT_REASON_NEVER_FINALIZED

    found = _cut_rows(db)
    assert [r["contract_id"] for r in found] == [cid], (
        "the seeded clean row must not appear in the cut population"
    )
    assert found[0]["agent_state"] == "DISPATCHED"


def test_contract_cut_detection_query_never_reads_raw_handoff_json(db):
    """(d) The projection that answers the question carries no JSON column."""
    _seed_binding_targets(db)
    _born(db, f"{AGENT_ID}.query-shape")

    assert "raw_handoff_json" not in _CUT_QUERY
    row = _cut_rows(db)[0]
    assert set(row.keys()) == {
        "contract_id", "agent_id", "agent_state", "cut_reason"
    }
    assert row["cut_reason"] in CUT_REASONS


# ---------------------------------------------------------------------------
# (b) incremental fill does not launder a cut into a clean closure
# ---------------------------------------------------------------------------

def test_contract_cut_detection_mirrored_evidence_stays_marked(db):
    """A turn cut mid-fill keeps BOTH its partial evidence and its cut mark."""
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.mirrored-cut"
    _born(db, cid)

    mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["writer.py"])), db_path=db
    )

    assert _cut_reason(db, cid) == CUT_REASON_NEVER_FINALIZED, (
        "mirroring writes raw_handoff_json only -- it must not clear the mark"
    )
    con = sqlite3.connect(str(db))
    try:
        raw = con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs "
            "WHERE contract_id = ?",
            (cid,),
        ).fetchone()[0]
    finally:
        con.close()
    assert json.loads(raw)["evidence_report"]["files_checked"] == ["writer.py"]


# ---------------------------------------------------------------------------
# (c) each closure lane lands its own reason
# ---------------------------------------------------------------------------

def test_contract_cut_detection_reaped_born_row_carries_the_reaped_reason(db):
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.to-be-reaped"
    _born(db, cid)

    from gaia.store import writer as _writer

    outcome = close_born_dispatch_row(
        _writer,
        session_id=SESSION,
        identity_candidates=[AGENT_ID],
        workspace=WORKSPACE,
        contract_pointer=None,
        turn_recorded_own_contract=False,
        db_path=db,
        agent_name=AGENT_NAME,
    )

    assert outcome is not None, "the born row must have been closed"
    assert _cut_reason(db, cid) == CUT_REASON_REAPED


def test_contract_cut_detection_backstop_capture_is_marked(db):
    """A fence-only turn: no draft, no born row -- the hook writes the row."""
    _seed_binding_targets(db)

    persist_handoff(
        _terminal_envelope(),
        json.dumps(_terminal_envelope()),
        _task_info(db),
        SESSION,
    )

    found = _cut_rows(db)
    assert len(found) == 1
    assert found[0]["cut_reason"] == CUT_REASON_BACKSTOP_CAPTURE
    assert found[0]["contract_id"].startswith("hook-backstop.")


def test_contract_cut_detection_truncation_salvage_is_marked(db):
    _seed_binding_targets(db)
    draft_id = mint_draft_id(AGENT_ID)
    save_draft(draft_id, _partial_envelope(files=["half-done.py"]))

    out = ClaudeCodeAdapter()._salvage_truncated_draft(
        parsed_contract=None,
        task_info=_task_info(db),
        session_id=SESSION,
    )

    assert out is not None and out["contract_id"] == draft_id
    assert _cut_reason(db, draft_id) == CUT_REASON_SALVAGED_TRUNCATION


# ---------------------------------------------------------------------------
# (e)-(g) a clean closure never carries the mark
# ---------------------------------------------------------------------------

def test_contract_cut_detection_finalize_clears_the_birth_stamp(db):
    """The one writer that can promote a born row to a clean closure."""
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.clean"
    _born(db, cid)
    assert _cut_reason(db, cid) == CUT_REASON_NEVER_FINALIZED

    finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_terminal_envelope()),
        session_id=SESSION,
        db_path=db,
    )

    assert _cut_reason(db, cid) is None
    assert [r["contract_id"] for r in _cut_rows(db)] == [], (
        "a cleanly finalized turn must not appear in the cut population"
    )


def test_contract_cut_detection_finalize_without_a_born_row_is_clean(db):
    """(f) The legacy path: finalize INSERTs its own row, already clean."""
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.legacy-clean"

    finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_terminal_envelope()),
        db_path=db,
    )

    assert _cut_reason(db, cid) is None


def test_contract_cut_detection_superseded_scaffold_is_not_marked(db):
    """(g) A healthy turn's leftover scaffold is not a cut.

    Marking it would stamp every healthy bound dispatch and drown the very
    population the mark exists to identify -- the same reason the closure
    withholds the ``degraded`` envelope flag in this mode.
    """
    _seed_binding_targets(db)
    scaffold = f"{AGENT_ID}.scaffold"
    own_row = f"{AGENT_ID}.own-contract"
    _born(db, scaffold)
    finalize_agent_contract_handoff(
        contract_id=own_row,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_terminal_envelope()),
        db_path=db,
    )

    from gaia.store import writer as _writer

    close_born_dispatch_row(
        _writer,
        session_id=SESSION,
        identity_candidates=[AGENT_ID],
        workspace=WORKSPACE,
        contract_pointer=own_row,
        turn_recorded_own_contract=True,
        db_path=db,
        agent_name=AGENT_NAME,
    )

    assert _cut_reason(db, scaffold) is None
    assert _cut_reason(db, own_row) is None
    assert _cut_rows(db) == []


# ---------------------------------------------------------------------------
# (h) end to end through the real CLI
# ---------------------------------------------------------------------------

def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def test_contract_cut_detection_cli_adopted_turn_cut_before_finalize(cli_env):
    """The lived shape of a cut turn, driven through the CLI it really uses."""
    db_path = Path(cli_env["GAIA_DATA_DIR"]) / "gaia.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-cut"
    _born(db_path, cid)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid],
                cli_env).returncode == 0
    assert _run(["add", "--draft-id", cid,
                 "evidence_report.files_checked", "mid-turn.py"],
                cli_env).returncode == 0

    # The turn stops here -- no finalize.
    assert _cut_reason(db_path, cid) == CUT_REASON_NEVER_FINALIZED


def test_contract_cut_detection_cli_finalized_turn_is_clean(cli_env):
    db_path = Path(cli_env["GAIA_DATA_DIR"]) / "gaia.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-clean"
    _born(db_path, cid, plan_task_id=None)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid],
                cli_env).returncode == 0
    assert _run(["fill", "--draft-id", cid, "--json", json.dumps({
        "agent_status": {"pending_steps": [], "next_action": "done"},
        "evidence_report": {"verification": {
            "method": "test", "result": "pass", "details": "clean",
        }},
    })], cli_env).returncode == 0
    assert _run(["set", "--draft-id", cid, "agent_status.agent_state",
                 "COMPLETE"], cli_env).returncode == 0

    fin = _run(["finalize", "--draft-id", cid, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr

    assert _cut_reason(db_path, cid) is None
    assert _cut_rows(db_path) == []


# ---------------------------------------------------------------------------
# (i)-(l) the migration
# ---------------------------------------------------------------------------

# The v38 shape: v37's rebuild plus the plan_task index v38 added, and
# deliberately WITHOUT cut_reason -- which is exactly what v38_to_v39 adds.
_V38_SCHEMA = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE agent_contract_handoffs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id       TEXT,
    agent_id          TEXT NOT NULL,
    session_id        TEXT,
    workspace         TEXT NOT NULL,
    brief_id          INTEGER,
    plan_task_id      INTEGER,
    plan_id           INTEGER,
    parent_handoff_id INTEGER,
    kind              TEXT,
    agent_state       TEXT NOT NULL
                      CHECK (agent_state IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION', 'DISPATCHED')),
    raw_handoff_json  TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
CREATE INDEX idx_agent_contract_handoffs_plan_task ON agent_contract_handoffs(plan_task_id);
"""

_V38_ROWS = [
    ("a1.tok1", "a1", "COMPLETE"),
    ("a2.tok2", "a2", "DISPATCHED"),
    ("a3.tok3", "a3", "IN_PROGRESS"),
    ("a4.tok4", "a4", "DISPATCHED"),
]


def _load_bootstrap_module():
    """Apply the migration through the REAL runner's ADD COLUMN guard."""
    spec = importlib.util.spec_from_file_location(
        "gaia_bootstrap_db_v39", _BOOTSTRAP_PY
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_v38_db(db_path: Path) -> list:
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_V38_SCHEMA)
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (38, '2026-01-01T00:00:00Z', 'synthetic v38 DB')"
        )
        inserted = []
        for contract_id, agent_id, state in _V38_ROWS:
            cur = con.execute(
                "INSERT INTO agent_contract_handoffs "
                "(contract_id, agent_id, workspace, agent_state, raw_handoff_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (contract_id, agent_id, WORKSPACE, state, "{}"),
            )
            inserted.append((cur.lastrowid, contract_id, state))
        con.commit()
        return inserted
    finally:
        con.close()


def _apply_migration(con: sqlite3.Connection, bootstrap) -> None:
    mig_sql = bootstrap._filter_add_column_idempotent(con, _MIGRATION)
    con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")


def _columns(con: sqlite3.Connection) -> set:
    return {
        r[1]
        for r in con.execute(
            "PRAGMA table_info(agent_contract_handoffs)"
        ).fetchall()
    }


def _indexes(con: sqlite3.Connection) -> set:
    return {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='agent_contract_handoffs'"
        ).fetchall()
    }


@pytest.fixture()
def v38_db():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gaia.db"
        rows = _build_v38_db(path)
        yield path, rows, _load_bootstrap_module()


def test_contract_cut_detection_migration_file_exists():
    assert _MIGRATION.is_file(), (
        f"bootstrap cannot advance to v39 without {_MIGRATION}"
    )


def test_contract_cut_detection_migration_adds_column_index_and_backfill(v38_db):
    db_path, _rows, bootstrap = v38_db
    con = sqlite3.connect(str(db_path))
    try:
        assert "cut_reason" not in _columns(con)

        _apply_migration(con, bootstrap)

        assert "cut_reason" in _columns(con)
        assert _INDEX_NAME in _indexes(con)

        marked = dict(
            con.execute(
                "SELECT contract_id, cut_reason FROM agent_contract_handoffs "
                "WHERE cut_reason IS NOT NULL"
            ).fetchall()
        )
        assert marked == {
            "a2.tok2": CUT_REASON_NEVER_FINALIZED,
            "a4.tok4": CUT_REASON_NEVER_FINALIZED,
        }, "only rows still in DISPATCHED are backfilled"
    finally:
        con.close()


def test_contract_cut_detection_migration_replay_is_a_noop(v38_db):
    """The fresh-install path replays every migration after schema.sql."""
    db_path, _rows, bootstrap = v38_db
    con = sqlite3.connect(str(db_path))
    try:
        _apply_migration(con, bootstrap)
        first_cols, first_idx = _columns(con), _indexes(con)
        first_state = con.execute(
            "SELECT id, contract_id, agent_state, cut_reason "
            "FROM agent_contract_handoffs ORDER BY id"
        ).fetchall()

        _apply_migration(con, bootstrap)

        assert _columns(con) == first_cols
        assert _indexes(con) == first_idx
        assert con.execute(
            "SELECT id, contract_id, agent_state, cut_reason "
            "FROM agent_contract_handoffs ORDER BY id"
        ).fetchall() == first_state
    finally:
        con.close()


def test_contract_cut_detection_migration_loses_no_rows(v38_db):
    db_path, expected, bootstrap = v38_db
    con = sqlite3.connect(str(db_path))
    try:
        _apply_migration(con, bootstrap)
        actual = con.execute(
            "SELECT id, contract_id, agent_state FROM agent_contract_handoffs "
            "ORDER BY id"
        ).fetchall()
        assert [tuple(r) for r in actual] == expected
    finally:
        con.close()


def test_contract_cut_detection_migration_on_a_fresh_schema_is_a_noop(db):
    """The other ordering: schema.sql ran first, so the column already exists."""
    _seed_binding_targets(db)
    bootstrap = _load_bootstrap_module()
    con = sqlite3.connect(str(db))
    try:
        assert "cut_reason" in _columns(con)
        before = con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs"
        ).fetchone()[0]

        _apply_migration(con, bootstrap)

        assert _INDEX_NAME in _indexes(con)
        assert con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs"
        ).fetchone()[0] == before
        assert con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs "
            "WHERE cut_reason IS NOT NULL"
        ).fetchone()[0] == 0, "the seeded row finalized cleanly"
    finally:
        con.close()


def test_contract_cut_detection_migration_backfill_uses_the_ssot_literal():
    """(l) The SQL literal and the Python constant cannot drift apart silently."""
    body = _MIGRATION.read_text(encoding="utf-8")
    assert f"'{CUT_REASON_NEVER_FINALIZED}'" in body
