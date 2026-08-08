"""A turn is a contract: resuming does not reopen it, it continues into a new one.

THE DEFECT, measured live. When the orchestrator resumes an agent that already
closed its turn, the agent keeps working and has nowhere to write. Its row is
terminal, so ``mirror_partial_contract_handoff`` answers ``skipped/terminal`` and
``finalize_agent_contract_handoff``'s ``WHERE agent_state NOT IN (COMPLETE)``
guard converges nothing -- while every CLI call still exits 0. A resumption that
produced excellent work left its row frozen at the first close's content.

The obvious repair is unavailable: a resumption emits NO birth event (the nascent
row is written only from the dispatching PreToolUse:Task; a resume arrives as
SendMessage), so there is no moment before the work to prepare a place for it.
The only moment left is the FIRST WRITE.

Two properties are covered here, and each is written so it FAILS against the
pre-change code rather than merely describing the new one:

  P1  CONTINUATION, NOT REOPENING -- a contract write addressed at a CLOSED row
      mints a NEW contract recording where it came from, lands the write there,
      and leaves the closed row untouched in state AND content. Against the old
      code the write vanished and the closed row was the only row.
  P5  THE CLOSE STILL WORKS -- a resumed turn's SubagentStop gate validates ITS
      continuation, not the closed row. The trap is that the harness stamps the
      SAME agent id on a resumption, so the row lookup sees every link of the
      chain at once; read as rival candidates it declines, resolves nothing, and
      rejects a turn whose work is perfectly recorded.

Plus the two supporting properties: the chain is readable from ANY link (P4) and
the mint is not silent (P3).

Everything runs against the REAL writers and the REAL CLI (subprocesses against
the standalone shim, isolated ``GAIA_DATA_DIR``) -- never a mock of the store or
the gate.

The new store symbols are imported INSIDE the tests that need them, not at module
scope. That is deliberate: a module-scope import turns a run against the
pre-change code into one collection error, which proves only that a name is
missing. Kept local, the two headline regressions (the CLI end-to-end P1 and the
CLI end-to-end P5) run to completion against the old code and fail on the
assertion that names the DEFECT -- the resumed turn's work is not on any row, and
the gate validates the first close's record instead of the resumed turn's.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
for _p in (_HOOKS_DIR, str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
AGENT_ID = valid_agent_id("resumed-turn-continuation")
AGENT_NAME = "gaia-system"
SESSION_ID = "sess-continuation"
HARNESS_ID = valid_agent_id("harness-side-continuation")

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """An isolated DB path; the writer materializes the real schema."""
    return tmp_path / "gaia.db"


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR: drafts AND the DB land under it."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    for var in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(var, raising=False)
    return dict(os.environ)


def _cli_db(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _complete_envelope(note: str = "first close") -> dict:
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            # COMPLETE requires next_action == 'done' (validator COMPLETE_SHAPE);
            # the per-turn marker rides in key_outputs instead.
            "next_action": "done",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }
    envelope["evidence_report"]["key_outputs"] = [note]
    envelope["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": note,
    }
    return envelope


def _rows(db_path: Path, **where) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        clause = " AND ".join(f"{col} = ?" for col in where)
        sql = "SELECT * FROM agent_contract_handoffs"
        if clause:
            sql += f" WHERE {clause}"
        return con.execute(sql + " ORDER BY id ASC", tuple(where.values())).fetchall()
    finally:
        con.close()


def _closed_row(db_path: Path, contract_id: str, *, note: str = "first close") -> dict:
    """A turn that ran and CLOSED -- born, then finalized COMPLETE."""
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        kind="task_execution",
        agent_name=AGENT_NAME,
        dispatch_prompt_id="prompt-1",
        db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, HARNESS_ID, db_path=db_path)
    envelope = _complete_envelope(note)
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID,
        db_path=db_path,
    )
    return envelope


# ---------------------------------------------------------------------------
# P1 -- the writer: a closed row is continued, never reopened
# ---------------------------------------------------------------------------

def test_continuation_mints_a_new_contract_and_leaves_the_closed_row_intact(db):
    from gaia.store.writer import open_contract_continuation

    closed_id = f"{AGENT_ID}.turn-one"
    closed_envelope = _closed_row(db, closed_id)
    before = dict(_rows(db, contract_id=closed_id)[0])

    outcome = open_contract_continuation(
        closed_id,
        f"{AGENT_ID}.turn-two",
        raw_handoff_json=json.dumps({"continues_contract_id": closed_id}),
        db_path=db,
    )

    assert outcome["status"] == "opened"
    assert outcome["created"] is True
    assert outcome["contract_id"] == f"{AGENT_ID}.turn-two"
    assert outcome["continues_contract_id"] == closed_id

    after = dict(_rows(db, contract_id=closed_id)[0])
    assert after == before, (
        "the closed row must be byte-identical afterwards -- neither its state "
        "nor its content may ever be modified by a continuation"
    )
    assert json.loads(after["raw_handoff_json"]) == closed_envelope

    link = dict(_rows(db, contract_id=f"{AGENT_ID}.turn-two")[0])
    assert link["continues_handoff_id"] == before["id"], "the link records its origin"
    assert link["agent_state"] == "DISPATCHED", "a fresh link is open, not closed"
    assert link["cut_reason"] == "never_finalized", "cleanliness is earned by finalize"
    assert link["claimed_at"] is not None, (
        "the link must be born CLAIMED: it inherits the dispatch correlation "
        "keys, and claim_dispatch_row's pool is exactly DISPATCHED + unclaimed, "
        "so an unclaimed link could be taken by a later sibling dispatch"
    )
    for inherited in ("agent_id", "session_id", "workspace", "kind",
                      "harness_agent_id", "dispatch_prompt_id"):
        assert link[inherited] == before[inherited], (
            f"{inherited} belongs to the turn, which is the same turn"
        )


def test_continuation_is_refused_on_a_row_that_is_still_open(db):
    """An open row is written in place; a continuation there would fork a turn."""
    from gaia.store.writer import open_contract_continuation

    open_id = f"{AGENT_ID}.still-running"
    insert_dispatched_handoff(
        contract_id=open_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db,
    )

    outcome = open_contract_continuation(
        open_id, f"{AGENT_ID}.premature", raw_handoff_json="{}", db_path=db
    )

    assert outcome == {"status": "skipped", "reason": "not_closed"}
    assert _rows(db, contract_id=f"{AGENT_ID}.premature") == []


def test_continuation_of_an_unknown_contract_creates_nothing(db):
    from gaia.store.writer import open_contract_continuation

    _closed_row(db, f"{AGENT_ID}.schema-seed")
    outcome = open_contract_continuation(
        f"{AGENT_ID}.never-existed",
        f"{AGENT_ID}.orphan-link",
        raw_handoff_json="{}",
        db_path=db,
    )
    assert outcome == {"status": "skipped", "reason": "no_row"}
    assert _rows(db, contract_id=f"{AGENT_ID}.orphan-link") == []


def test_a_closed_row_gets_exactly_one_continuation(db):
    """A second open adopts the first link instead of forking the closed row."""
    from gaia.store.writer import open_contract_continuation

    closed_id = f"{AGENT_ID}.one-link-only"
    _closed_row(db, closed_id)

    first = open_contract_continuation(
        closed_id, f"{AGENT_ID}.link-a", raw_handoff_json="{}", db_path=db
    )
    second = open_contract_continuation(
        closed_id, f"{AGENT_ID}.link-b", raw_handoff_json="{}", db_path=db
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["contract_id"] == f"{AGENT_ID}.link-a"
    assert _rows(db, contract_id=f"{AGENT_ID}.link-b") == []
    assert len(_rows(db, continues_handoff_id=first["continues_handoff_id"])) == 1


# ---------------------------------------------------------------------------
# P1 -- the CLI, end to end: the resumed turn's work lands somewhere
#
# This is the regression proper. Against the pre-change code the `set`/`add`
# calls below exit 0, write only to disk, and the DB still holds ONE row frozen
# at the first close -- the measured defect.
# ---------------------------------------------------------------------------

def test_resumed_turn_write_lands_in_a_continuation_not_on_the_closed_row(cli_env):
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.cli-turn-one"
    closed_envelope = _closed_row(db_path, closed_id)

    # The resumed agent does nothing different: same --draft-id, same verbs. An
    # `add` is used rather than a `set`, on purpose: appending evidence is valid
    # against the COMPLETE envelope the first turn left on disk, so against the
    # PRE-CHANGE code this call SUCCEEDS and the failure lands on the defect
    # itself (the write reached no row) instead of on an unrelated shape
    # rejection that would mask it.
    addc = _run(
        ["add", "--draft-id", closed_id, "--json",
         "evidence_report.key_outputs", "found while resumed"],
        cli_env,
    )
    assert addc.returncode == 0, addc.stderr
    payload = json.loads(addc.stdout)

    assert payload["mirrored"] is True, (
        "the whole point: the resumed turn's write must REACH a row. A closed "
        "row refuses the mirror, so without a continuation this write is lost "
        "while the command still exits 0"
    )
    assert "continuation" in payload, (
        "a write addressed at a closed contract must open a continuation"
    )
    link_id = payload["draft_id"]
    assert link_id != closed_id, "the write must NOT land on the closed contract"
    assert payload["continuation"]["continues_contract_id"] == closed_id

    second = _run(
        ["add", "--draft-id", closed_id, "--json",
         "evidence_report.files_checked", "resumed.py"],
        cli_env,
    )
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["draft_id"] == link_id, (
        "a second write follows the chain to the SAME link -- it does not mint "
        "another one"
    )

    rows = {r["contract_id"]: r for r in _rows(db_path)}
    assert set(rows) == {closed_id, link_id}

    assert json.loads(rows[closed_id]["raw_handoff_json"]) == closed_envelope, (
        "the closed contract is frozen -- that is the property, not the bug"
    )
    assert rows[closed_id]["agent_state"] == "COMPLETE"

    link_envelope = json.loads(rows[link_id]["raw_handoff_json"])
    assert link_envelope["evidence_report"]["key_outputs"] == ["found while resumed"]
    assert link_envelope["evidence_report"]["files_checked"] == ["resumed.py"]
    assert link_envelope["continues_contract_id"] == closed_id


def test_resumed_turn_finalize_closes_the_continuation_not_the_closed_row(cli_env):
    """finalize FOLLOWS the chain: the resumed close lands where its writes did."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.cli-finalize-follows"
    _closed_row(db_path, closed_id)

    setc = _run(
        ["set", "--draft-id", closed_id, "--json",
         "agent_status.next_action", "done"],
        cli_env,
    )
    link_id = json.loads(setc.stdout)["draft_id"]

    assert _run(
        ["fill", "--draft-id", closed_id, "--json", json.dumps({
            "evidence_report": {
                "key_outputs": ["second turn evidence"],
                "verification": {
                    "method": "test", "result": "pass", "details": "resumed",
                },
            },
        })],
        cli_env,
    ).returncode == 0
    assert _run(
        ["set", "--draft-id", closed_id, "agent_status.agent_state", "COMPLETE"],
        cli_env,
    ).returncode == 0

    fin = _run(["finalize", "--draft-id", closed_id, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr
    finalized = json.loads(fin.stdout)
    assert finalized["draft_id"] == link_id, (
        "finalize must converge the LIVE link, not the record already closed"
    )
    assert finalized["created"] is True, (
        "a real convergence, not the idempotent no-op the closed row would give"
    )

    rows = {r["contract_id"]: r for r in _rows(db_path)}
    assert rows[link_id]["agent_state"] == "COMPLETE"
    assert rows[link_id]["cut_reason"] is None, "a clean close clears the birth stamp"
    envelope = json.loads(rows[link_id]["raw_handoff_json"])
    assert envelope["evidence_report"]["key_outputs"] == ["second turn evidence"]


def test_finalize_alone_never_mints_a_link(cli_env):
    """A repeated finalize stays the documented idempotent no-op.

    Minting on finalize would satisfy "a write to a closed row forks" in the
    letter and break the guarantee that a retried close is a no-op reporting the
    same handoff_id -- every retry would leave an empty link behind.
    """
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.cli-refinalize"
    _closed_row(db_path, closed_id)
    # The draft file the first turn left behind, still carrying COMPLETE.
    assert _run(
        ["init", "--agent-id", AGENT_ID, "--draft-id", closed_id], cli_env
    ).returncode == 0
    assert _run(
        ["fill", "--draft-id", closed_id, "--json",
         json.dumps({
             "agent_status": {"agent_state": "COMPLETE", "next_action": "done"},
             "evidence_report": {
                 "verification": {
                     "method": "test", "result": "pass", "details": "again",
                 },
             },
         })],
        cli_env,
    ).returncode == 0

    # The fill above already opened the link; finalize twice must not add more.
    before = len(_rows(db_path))
    assert _run(["finalize", "--draft-id", closed_id, "--json"], cli_env).returncode == 0
    assert _run(["finalize", "--draft-id", closed_id, "--json"], cli_env).returncode == 0
    assert len(_rows(db_path)) == before, "finalize follows the chain; it never mints"


# ---------------------------------------------------------------------------
# P3 -- no ceremony, but not silent
# ---------------------------------------------------------------------------

def test_continuation_announces_itself_where_the_caller_can_see_it(cli_env):
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.cli-not-silent"
    _closed_row(db_path, closed_id)

    setc = _run(
        ["set", "--draft-id", closed_id, "agent_status.next_action", "resumed"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    assert "CONTINUATION" in setc.stderr, (
        "plain output must still say a new contract was minted"
    )
    assert closed_id in setc.stderr

    con = sqlite3.connect(str(db_path))
    try:
        events = con.execute(
            "SELECT result FROM harness_events WHERE type = 'contract.continuation'"
        ).fetchall()
    finally:
        con.close()
    assert len(events) == 1, "the mint must stay queryable after the fact"
    assert closed_id in events[0][0]


# ---------------------------------------------------------------------------
# P4 -- the chain reads back from ANY link
# ---------------------------------------------------------------------------

def test_chain_is_recoverable_from_either_end(db):
    from gaia.store.writer import (
        continuation_chain,
        continuation_tip,
        open_contract_continuation,
    )

    first = f"{AGENT_ID}.chain-1"
    _closed_row(db, first)
    second = open_contract_continuation(
        first, f"{AGENT_ID}.chain-2", raw_handoff_json="{}", db_path=db
    )["contract_id"]
    finalize_agent_contract_handoff(
        contract_id=second,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_complete_envelope("second close")),
        db_path=db,
    )
    third = open_contract_continuation(
        second, f"{AGENT_ID}.chain-3", raw_handoff_json="{}", db_path=db
    )["contract_id"]

    expected = [first, second, third]
    for entry_point in expected:
        chain = continuation_chain(entry_point, db_path=db)
        assert [row["contract_id"] for row in chain] == expected, (
            f"the whole chain must be recoverable from {entry_point}"
        )
    assert continuation_tip(first, db_path=db)["contract_id"] == third


def test_chain_of_an_unresumed_contract_is_itself(db):
    from gaia.store.writer import continuation_chain

    only = f"{AGENT_ID}.never-resumed"
    _closed_row(db, only)
    chain = continuation_chain(only, db_path=db)
    assert [row["contract_id"] for row in chain] == [only]
    assert continuation_chain(f"{AGENT_ID}.no-such-row", db_path=db) == []


def test_chain_cli_prints_every_link_from_any_of_them(cli_env):
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.cli-chain"
    _closed_row(db_path, closed_id)
    setc = _run(
        ["set", "--draft-id", closed_id, "--json",
         "agent_status.next_action", "resumed"],
        cli_env,
    )
    link_id = json.loads(setc.stdout)["draft_id"]

    for entry_point in (closed_id, link_id):
        chained = _run(["chain", "--contract-id", entry_point, "--json"], cli_env)
        assert chained.returncode == 0, chained.stderr
        payload = json.loads(chained.stdout)
        assert payload["links"] == 2
        assert [link["contract_id"] for link in payload["chain"]] == [
            closed_id, link_id,
        ]
        assert payload["chain"][0]["link"] == 1
        assert payload["chain"][1]["continues_handoff_id"] == \
            payload["chain"][0]["id"]

    # The operator's own view: the plain table, which must name both links and
    # say which one is live. Printed so a `pytest -s` run shows the literal
    # output an operator would see.
    human = _run(["chain", "--contract-id", closed_id], cli_env)
    assert human.returncode == 0, human.stderr
    print(human.stdout)
    assert closed_id in human.stdout
    assert link_id in human.stdout
    assert "2 link(s)" in human.stdout
    assert f"the live contract is {link_id}" in human.stdout


# ---------------------------------------------------------------------------
# P5 -- the close still works, and validates the CONTINUATION
#
# The trap: the harness stamps the SAME agent id on a resumption, so the row
# lookup the gate depends on sees every link of the chain. Against the
# pre-change code the bridge reads them as rival candidates, declines, and the
# gate rejects a turn whose work is perfectly recorded.
# ---------------------------------------------------------------------------

def test_collapse_keeps_the_live_link_and_still_declines_a_real_collision():
    from gaia.store.writer import collapse_continuation_chains

    chain = [
        {"id": 2, "continues_handoff_id": 1},
        {"id": 1, "continues_handoff_id": None},
    ]
    assert collapse_continuation_chains(chain) == [{"id": 2, "continues_handoff_id": 1}]

    unrelated = [
        {"id": 7, "continues_handoff_id": None},
        {"id": 9, "continues_handoff_id": None},
    ]
    assert collapse_continuation_chains(unrelated) == unrelated, (
        "two genuinely unrelated rows are still an ambiguity to decline"
    )


def test_subagent_stop_gate_validates_the_resumed_turns_own_record(cli_env):
    """A resumed turn closes, and its gate reads the record IT wrote.

    Driven end to end through the real CLI so it runs to completion against the
    pre-change code too. There it fails on the defect itself: the resumed turn's
    writes reach no row, its finalize no-ops, and the gate happily validates the
    FIRST close's envelope -- a green verdict on the wrong record.
    """
    from adapters.claude_code import GATE_SOURCE_ROW, resolve_subagent_stop_gate
    from modules.agents.handoff_persister import dispatch_row_by_harness_id

    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    closed_id = f"{AGENT_ID}.gate-turn-one"
    _closed_row(db_path, closed_id)

    # The resumed turn: same draft id, no ceremony, ordinary verbs.
    assert _run(
        ["set", "--draft-id", closed_id, "agent_status.next_action", "done"], cli_env
    ).returncode == 0
    assert _run(
        ["fill", "--draft-id", closed_id, "--json", json.dumps({
            "evidence_report": {
                "key_outputs": ["resumed turn evidence"],
                "verification": {
                    "method": "test", "result": "pass", "details": "resumed",
                },
            },
        })],
        cli_env,
    ).returncode == 0
    assert _run(
        ["set", "--draft-id", closed_id, "agent_status.agent_state", "COMPLETE"],
        cli_env,
    ).returncode == 0
    assert _run(["finalize", "--draft-id", closed_id], cli_env).returncode == 0

    # SubagentStop hands the SAME harness agent id it did for the first turn --
    # the harness mints it per run, not per resumption -- so the lookup sees
    # every link of the chain at once.
    row = dispatch_row_by_harness_id(
        {"agent_id": HARNESS_ID, "db_path": str(db_path)},
        session_id=SESSION_ID,
        db_path=db_path,
    )
    assert row is not None, (
        "the bridge must resolve a row: a chain under one harness id is not an "
        "ambiguity, and declining it rejects the close of a recorded turn"
    )
    assert row["contract_id"] != closed_id, (
        "the gate must resolve THIS turn's own record, not the one the previous "
        "turn already closed"
    )

    gate_envelope = json.loads(row["raw_handoff_json"])
    assert gate_envelope["evidence_report"]["key_outputs"] == [
        "resumed turn evidence"
    ], "the record the gate validates must carry the resumed turn's evidence"

    verdict, source = resolve_subagent_stop_gate(
        agent_type=AGENT_NAME,
        bound_dispatch_row=row,
        db_path=str(db_path),
    )
    assert source == GATE_SOURCE_ROW
    assert verdict.rejected is False, verdict.rejection_reason


# ---------------------------------------------------------------------------
# The column has to REACH a live database, not only a fresh install
# ---------------------------------------------------------------------------

def test_migration_puts_the_continuation_column_on_an_existing_db(tmp_path):
    """An already-installed DB gains continues_handoff_id through the migration.

    Everything above runs against a DB the writer materialized from schema.sql,
    which is the FRESH-install shape. A real machine upgrades in place instead,
    so this drives the actual bootstrap over an old DB and checks the column and
    the ledger arrive -- the difference between a schema that is correct and one
    that is also reachable.
    """
    import sqlite3 as _sqlite3

    from tests.cli.test_bootstrap_migrations import (
        _build_v26_pre_contract_id_db,
        _run_bootstrap,
    )

    workspace = tmp_path / "upgrade"
    workspace.mkdir()
    db_path = workspace / "tmp_gaia.db"
    _build_v26_pre_contract_id_db(db_path)

    result = _run_bootstrap(workspace)
    assert result.returncode == 0, result.stderr

    con = _sqlite3.connect(str(db_path))
    try:
        columns = {
            r[1] for r in con.execute(
                "PRAGMA table_info(agent_contract_handoffs)"
            )
        }
        version = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()

    assert "continues_handoff_id" in columns
    assert version >= 46


def test_gate_bridge_still_declines_two_unrelated_rows_under_one_harness_id(db):
    """The collapse narrows the refusal; it must not remove it."""
    from modules.agents.handoff_persister import dispatch_row_by_harness_id

    for suffix in ("collide-a", "collide-b"):
        contract_id = f"{AGENT_ID}.{suffix}"
        insert_dispatched_handoff(
            contract_id=contract_id,
            agent_id=AGENT_ID,
            workspace=WORKSPACE,
            session_id=SESSION_ID,
            db_path=db,
        )
        stamp_harness_agent_id(contract_id, HARNESS_ID, db_path=db)

    row = dispatch_row_by_harness_id(
        {"agent_id": HARNESS_ID, "db_path": str(db)},
        session_id=SESSION_ID,
        db_path=db,
    )
    assert row is None, "two rows that continue nothing are still a real ambiguity"
