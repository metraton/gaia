"""AC-2 -- incremental fill is mirrored to the DB row, not only to disk.

The property under test is the one a harness cut exercises for real: a turn that
builds its contract with ``gaia contract set/add/fill`` and is INTERRUPTED before
``finalize`` must still leave its partial evidence on the row, recoverable by
query. Before the mirror, that evidence lived exclusively in
``data_dir()/contract_drafts/`` and the row still held the birth envelope.

Two layers are covered, because the guarantee is split across them: the writer
owns what the mirror is ALLOWED to do (the invariants), and the CLI owns WHEN it
is offered (the wiring).

Writer clauses:
  (a) mirrors a partial envelope onto a non-terminal (DISPATCHED) row, leaving
      agent_state, the born-at-dispatch binding and the birth created_at intact.
  (b) NEVER creates a row -- an unknown contract_id is a silent skip.
  (c) NEVER touches a terminal row -- a COMPLETE row's envelope is immutable.
  (d) the birth-envelope agent-name marker survives the mirror, so the closure's
      last-resort name lane still has the coordinate it matches on.

CLI clauses (real subprocesses against the standalone shim, isolated
``GAIA_DATA_DIR``, real schema materialized by the writer's own ``_connect``):
  (e) set/add/fill mirror onto an adopted born row; after a turn that stops
      BEFORE finalize the row carries the partial evidence.
  (f) that partial row is readable through the CLI itself
      (``gaia contract list --contract-id ... --json``).
  (g) a draft with NO row (no adoption) still writes to disk and mirrors to
      nothing -- no row is conjured.
  (h) finalize after mirroring still converges the SAME single row with its
      binding preserved -- the mirror does not fork a second row.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.store.writer import (
    BIRTH_AGENT_NAME_KEY,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    mirror_partial_contract_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
AGENT_ID = valid_agent_id("incremental-fill-mirror")
PLAN_ID = 47
TASK_ID = 198
AGENT_NAME = "gaia-system"


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


def _partial_envelope(*, files=(), key_outputs=()) -> dict:
    """A mid-turn, NON-terminal envelope -- what set/add/fill build toward."""
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": AGENT_ID,
            "pending_steps": ["still working"],
            "next_action": "keep going",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": list(files),
            "commands_run": [],
            "key_outputs": list(key_outputs),
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
        "failure_report": None,
    }


def _terminal_envelope() -> dict:
    envelope = _partial_envelope()
    envelope["agent_status"]["agent_state"] = "COMPLETE"
    envelope["agent_status"]["pending_steps"] = []
    envelope["agent_status"]["next_action"] = "done"
    envelope["evidence_report"]["verification"] = {
        "method": "test",
        "result": "pass",
        "details": "mirror",
    }
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
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 3, "incremental fill mirrored to the row", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path, contract_id: str) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_id, agent_state, plan_task_id, plan_id, "
            "parent_handoff_id, kind, session_id, created_at, raw_handoff_json "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchall()
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
        session_id="sess-mirror",
        agent_name=AGENT_NAME,
        db_path=db_path,
    )
    kwargs.update(overrides)
    insert_dispatched_handoff(**kwargs)


# ---------------------------------------------------------------------------
# (a) the mirror lands on a non-terminal row without moving anything else
# ---------------------------------------------------------------------------

def test_contract_incremental_fill_mirrors_partial_onto_dispatched_row(db):
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.mirror-a"
    _born(db, cid)
    before = _rows(db, cid)[0]

    outcome = mirror_partial_contract_handoff(
        cid,
        json.dumps(_partial_envelope(files=["writer.py"], key_outputs=["half done"])),
        db_path=db,
    )

    assert outcome["status"] == "applied"
    rows = _rows(db, cid)
    assert len(rows) == 1, "the mirror must never fork a second row"
    row = rows[0]

    envelope = json.loads(row["raw_handoff_json"])
    assert envelope["evidence_report"]["files_checked"] == ["writer.py"]
    assert envelope["evidence_report"]["key_outputs"] == ["half done"]

    # Everything that is NOT the envelope is untouched -- state above all: a row
    # mirrored out of 'DISPATCHED' would drop out of the reaper's orphan query
    # and out of the blind-verification binding read.
    assert row["agent_state"] == "DISPATCHED"
    assert row["plan_task_id"] == TASK_ID
    assert row["plan_id"] == PLAN_ID
    assert row["kind"] == "task_execution"
    assert row["session_id"] == "sess-mirror"
    assert row["created_at"] == before["created_at"]
    assert row["id"] == before["id"]


def test_contract_incremental_fill_mirror_is_repeatable(db):
    """Successive mirrors converge the SAME row to the newest partial state."""
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.mirror-repeat"
    _born(db, cid)

    mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["one"])), db_path=db
    )
    mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["one", "two"])), db_path=db
    )

    rows = _rows(db, cid)
    assert len(rows) == 1
    envelope = json.loads(rows[0]["raw_handoff_json"])
    assert envelope["evidence_report"]["files_checked"] == ["one", "two"]


# ---------------------------------------------------------------------------
# (b) never creates a row
# ---------------------------------------------------------------------------

def test_contract_incremental_fill_mirror_never_creates_a_row(db):
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.never-born"

    outcome = mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["orphan"])), db_path=db
    )

    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "no_row"
    assert _rows(db, cid) == [], "a draft with no born row must mirror to nothing"


def test_contract_incremental_fill_mirror_without_contract_id_is_a_no_op(db):
    outcome = mirror_partial_contract_handoff(
        "", json.dumps(_partial_envelope()), db_path=db
    )
    assert outcome == {"status": "skipped", "reason": "no_contract_id"}


# ---------------------------------------------------------------------------
# (c) never touches a terminal row
# ---------------------------------------------------------------------------

def test_contract_incremental_fill_mirror_never_touches_a_terminal_row(db):
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.already-complete"
    terminal = _terminal_envelope()
    finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(terminal),
        db_path=db,
    )

    outcome = mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["late write"])), db_path=db
    )

    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "closed"
    rows = _rows(db, cid)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "COMPLETE"
    assert json.loads(rows[0]["raw_handoff_json"]) == terminal


def test_contract_incremental_fill_mirror_never_touches_a_row_awaiting_verification(db):
    """The guard is the TURN ending, not the verdict freezing.

    A producer that closed ``NEEDS_VERIFICATION`` is not terminal, so the older
    ``TERMINAL_PLAN_STATUSES`` guard let a later write MERGE its evidence into
    the record an independent verifier was about to read.
    """
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.awaiting-verification"
    closed = _terminal_envelope()
    closed["agent_status"]["agent_state"] = "NEEDS_VERIFICATION"
    finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="NEEDS_VERIFICATION",
        raw_handoff_json=json.dumps(closed),
        db_path=db,
    )

    outcome = mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["next assignment"])), db_path=db
    )

    assert outcome == {"status": "skipped", "reason": "closed"}
    rows = _rows(db, cid)
    assert rows[0]["agent_state"] == "NEEDS_VERIFICATION"
    assert json.loads(rows[0]["raw_handoff_json"]) == closed


def test_contract_incremental_fill_mirror_guards_terminality_in_the_statement():
    """The UPDATE itself carries the guard -- not only the SELECT before it.

    Two layers enforce "never touch a terminal row": the SELECT pre-check that
    returns ``skipped/terminal``, and the ``agent_state NOT IN (...)`` clause on
    the UPDATE. Every behavioural test above is satisfied by the pre-check
    ALONE, so deleting the statement clause leaves them all green while the
    invariant quietly loses its last line of defence -- and it cannot be
    distinguished by behaviour, because ``BEGIN IMMEDIATE`` holds the write lock
    across the SELECT and the UPDATE, so no concurrent writer can turn the row
    terminal in between. The clause is a statement-level restatement of the
    invariant, and a structural assertion is what can hold it in place.
    """
    source = inspect.getsource(mirror_partial_contract_handoff)
    update = re.search(
        r"UPDATE agent_contract_handoffs.*?RETURNING id", source, re.S
    )
    assert update is not None, "the mirror must write through a single UPDATE"
    body = update.group(0)

    def guarded(statement: str) -> bool:
        return "agent_state NOT IN" in statement

    assert guarded(body), (
        "the mirror's UPDATE lost its terminality guard -- a terminal row must "
        "be unreachable by the statement, not only by the pre-check above it"
    )
    # And the check is only worth its line if it FAILS on the regression it
    # names, so the mutation is exercised here rather than assumed.
    assert not guarded(
        re.sub(r"\n\s*AND agent_state NOT IN[^\n]*", "", body)
    ), "the assertion above would pass even with the guard deleted"

    assert "SET raw_handoff_json" in body
    for protected in ("agent_state =", "plan_task_id =", "plan_id =",
                      "parent_handoff_id =", "kind =", "session_id =",
                      "created_at =", "cut_reason ="):
        assert protected not in body, (
            f"the mirror must never write {protected.strip(' =')}"
        )


# ---------------------------------------------------------------------------
# (d) the birth marker the closure's name lane matches on survives
# ---------------------------------------------------------------------------

def test_contract_incremental_fill_mirror_preserves_birth_agent_name(db):
    _seed_binding_targets(db)
    cid = f"{AGENT_ID}.marker"
    _born(db, cid)

    mirror_partial_contract_handoff(
        cid, json.dumps(_partial_envelope(files=["x"])), db_path=db
    )

    envelope = json.loads(_rows(db, cid)[0]["raw_handoff_json"])
    assert envelope[BIRTH_AGENT_NAME_KEY] == AGENT_NAME
    assert envelope["born_at_dispatch"] is True
    assert envelope["evidence_report"]["files_checked"] == ["x"]


# ---------------------------------------------------------------------------
# CLI wiring -- real subprocesses, isolated data dir
# ---------------------------------------------------------------------------

def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR: drafts AND the DB land under it."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _cli_db(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def test_contract_incremental_fill_cli_interrupted_turn_leaves_partial_row(cli_env):
    """A turn that set/add/fills and is CUT before finalize: the row has evidence.

    This is the failure the mirror exists for. No ``finalize`` is ever run --
    the turn simply stops, exactly as a harness cut leaves it.
    """
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-cut"
    _born(db_path, cid)

    init = _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid, "--json"], cli_env)
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["draft_id"] == cid

    setc = _run(
        ["set", "--draft-id", cid, "--json", "agent_status.next_action", "keep going"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    assert json.loads(setc.stdout)["mirrored"] is True

    addc = _run(
        ["add", "--draft-id", cid, "--json",
         "evidence_report.files_checked", "gaia/store/writer.py"],
        cli_env,
    )
    assert addc.returncode == 0, addc.stderr

    fillc = _run(
        ["fill", "--draft-id", cid, "--json",
         json.dumps({"evidence_report": {"key_outputs": ["mirror wired"]}})],
        cli_env,
    )
    assert fillc.returncode == 0, fillc.stderr
    assert json.loads(fillc.stdout)["mirrored"] is True

    # The turn stops here. No finalize.
    rows = _rows(db_path, cid)
    assert len(rows) == 1, "still exactly one row -- the born one"
    row = rows[0]
    assert row["agent_state"] == "DISPATCHED", "a partial fill is not a verdict"
    envelope = json.loads(row["raw_handoff_json"])
    assert envelope["evidence_report"]["files_checked"] == ["gaia/store/writer.py"]
    assert envelope["evidence_report"]["key_outputs"] == ["mirror wired"]
    assert envelope["agent_status"]["next_action"] == "keep going"
    assert row["plan_task_id"] == TASK_ID, "the binding survives the mirror"


def test_contract_incremental_fill_partial_row_is_readable_by_cli(cli_env):
    """The mirrored evidence is recoverable through the CLI, not just by SQL."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-read"
    _born(db_path, cid)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid], cli_env).returncode == 0
    assert _run(
        ["add", "--draft-id", cid, "evidence_report.key_outputs", "recoverable"],
        cli_env,
    ).returncode == 0

    listed = _run(["list", "--contract-id", cid, "--json"], cli_env)
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["count"] == 1
    row = payload["handoffs"][0]
    assert row["agent_state"] == "DISPATCHED"
    envelope = json.loads(row["raw_handoff_json"])
    assert envelope["evidence_report"]["key_outputs"] == ["recoverable"]


def test_contract_incremental_fill_cli_without_born_row_creates_nothing(cli_env):
    """No adoption -> disk write succeeds, and the DB gains no row."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)

    init = _run(["init", "--agent-id", AGENT_ID, "--json"], cli_env)
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]

    setc = _run(
        ["set", "--draft-id", draft_id, "--json", "agent_status.next_action", "solo"],
        cli_env,
    )
    assert setc.returncode == 0, setc.stderr
    assert json.loads(setc.stdout)["mirrored"] is False

    assert _rows(db_path, draft_id) == []
    view = _run(["view", "--draft-id", draft_id], cli_env)
    assert view.returncode == 0
    disk = json.loads(view.stdout)["envelope"]
    assert disk["agent_status"]["next_action"] == "solo", "disk write still stands"


def test_contract_incremental_fill_then_finalize_converges_one_row(cli_env):
    """Mirroring first does not fork a row: finalize still converges the born one."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-converge"
    _born(db_path, cid, plan_task_id=None)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid], cli_env).returncode == 0
    assert _run(
        ["add", "--draft-id", cid, "evidence_report.files_checked", "mid-turn"],
        cli_env,
    ).returncode == 0
    assert _run(
        ["fill", "--draft-id", cid, "--json",
         json.dumps({
             "agent_status": {"pending_steps": [], "next_action": "done"},
             "evidence_report": {
                 "verification": {
                     "method": "test", "result": "pass", "details": "mirror",
                 },
             },
         })],
        cli_env,
    ).returncode == 0
    assert _run(
        ["set", "--draft-id", cid, "agent_status.agent_state", "COMPLETE"], cli_env
    ).returncode == 0

    fin = _run(["finalize", "--draft-id", cid, "--json"], cli_env)
    assert fin.returncode == 0, fin.stderr

    rows = _rows(db_path, cid)
    assert len(rows) == 1, "one row per turn, mirror or no mirror"
    assert rows[0]["agent_state"] == "COMPLETE"
    assert rows[0]["plan_id"] == PLAN_ID, "the binding survives mirror + finalize"
    assert rows[0]["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# fill --json-file -- the patch read from disk instead of a shell argument
#
# A `--json` patch built from report prose (open_gaps, key_outputs,
# verification notes) routinely carries apostrophes and embedded quotes. An
# unescaped apostrophe inside a single-quoted --json value closes the shell's
# quoting early; everything after the break is re-tokenized as bare words, no
# longer recognizable as part of the `gaia contract fill` invocation it was
# written as. --json-file removes the hazard at its source: the patch never
# has to survive shell quoting at all.
# ---------------------------------------------------------------------------

def test_contract_fill_json_file_reads_patch_from_disk(cli_env, tmp_path):
    """--json-file merges a patch read from PATH, identically to --json."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-json-file"
    _born(db_path, cid)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid], cli_env).returncode == 0

    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps({
        "evidence_report": {"key_outputs": ["from a file, not a shell argument"]},
    }))

    fillc = _run(["fill", "--draft-id", cid, "--json-file", str(patch_path)], cli_env)
    assert fillc.returncode == 0, fillc.stderr
    assert json.loads(fillc.stdout)["mirrored"] is True

    view = _run(["view", "--draft-id", cid], cli_env)
    envelope = json.loads(view.stdout)["envelope"]
    assert envelope["evidence_report"]["key_outputs"] == [
        "from a file, not a shell argument"
    ]


def test_contract_fill_json_file_survives_report_prose_with_apostrophes(cli_env, tmp_path):
    """The exact hazard --json-file exists to avoid: prose with apostrophes and
    the words 'resume'/'grant' embedded in report text, not as commands."""
    db_path = _cli_db(cli_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_binding_targets(db_path)
    cid = f"{AGENT_ID}.cli-json-file-prose"
    _born(db_path, cid)

    assert _run(["init", "--agent-id", AGENT_ID, "--draft-id", cid], cli_env).returncode == 0

    prose = (
        "the controller's missing resources.requests was not investigated "
        "further under the 5-minute grant window; the operator will resume "
        "broadcasting once the review is done"
    )
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps({
        "evidence_report": {"open_gaps": [prose]},
    }))

    fillc = _run(["fill", "--draft-id", cid, "--json-file", str(patch_path)], cli_env)
    assert fillc.returncode == 0, fillc.stderr

    view = _run(["view", "--draft-id", cid], cli_env)
    envelope = json.loads(view.stdout)["envelope"]
    assert envelope["evidence_report"]["open_gaps"] == [prose]


def test_contract_fill_json_and_json_file_are_mutually_exclusive(cli_env):
    """Supplying both --json and --json-file is a usage error, not a silent pick."""
    result = _run(
        ["fill", "--json", "{}", "--json-file", "/nonexistent/patch.json"],
        cli_env,
    )
    assert result.returncode != 0
    assert "not allowed with argument" in result.stderr


def test_contract_fill_requires_json_or_json_file(cli_env):
    """Omitting both --json and --json-file is a usage error, not an empty merge."""
    result = _run(["fill", "--draft-id", "whatever"], cli_env)
    assert result.returncode != 0
    assert "required" in result.stderr.lower()
