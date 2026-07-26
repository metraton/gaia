"""``--agent-id`` refuses to guess when the handle names several live drafts.

The production incident this pins: two consecutive
``gaia contract fill --agent-id a7f3c1`` calls resolved to two DIFFERENT
drafts -- the recency winner moved between the calls -- and the second wrote a
COMPLETE plus a verification block onto a draft belonging to another agent's
turn. The cause is structural, not accidental: every agent mints its own
``^a[0-9a-f]{16,}$`` handle and nothing enforces uniqueness, so one handle
routinely spans unrelated turns (44 files were observed under a single one).

Excluding spent drafts from candidacy made ``--agent-id`` usable but not safe:
it turned a loud failure into a silent wrong answer. Resolution now refuses,
and the three outcomes are kept as three distinct diagnoses:

  * no draft under the handle      -> None (caller reports "run init")
  * exactly one live draft         -> that draft, behavior unchanged
  * 2+ live drafts                 -> AmbiguousDraftError naming --draft-id

Isolation: ``GAIA_DATA_DIR`` is redirected to a tmp path so ``drafts_dir()``
and ``db_path()`` resolve under it -- no test touches the real
``~/.gaia/contract_drafts``. CLI checks run as real subprocesses against
``bin/cli/contract.py``'s standalone shim, matching the sibling contract tests.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

AGENT_A = valid_agent_id("a7f3c1")
AGENT_B = valid_agent_id("a4e5f6")
HOUR = 3600.0


@pytest.fixture()
def drafts(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate to tmp and return the drafts module."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    import gaia.contract.drafts as drafts_mod

    importlib.reload(drafts_mod)
    return drafts_mod


def _envelope(agent_id, state="IN_PROGRESS"):
    return {
        "agent_status": {
            "agent_state": state,
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "pending",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _write(drafts_mod, draft_id, agent_id, age_seconds=0.0):
    drafts_mod.save_draft(draft_id, _envelope(agent_id))
    if age_seconds:
        path = drafts_mod.draft_path(draft_id)
        when = time.time() - age_seconds
        os.utime(path, (when, when))
    return draft_id


def _seed_terminal_rows(contract_ids, state="COMPLETE"):
    """Create the minimal agent_contract_handoffs shape liveness reads."""
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, agent_state text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, agent_state) values (?, ?)",
        [(cid, state) for cid in contract_ids],
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# SEVERAL live candidates -> refuse. The incident, reproduced.
# ---------------------------------------------------------------------------

def test_agent_id_with_two_live_drafts_refuses_instead_of_picking(drafts):
    mine = _write(drafts, f"{AGENT_A}.mine", AGENT_A, age_seconds=2 * HOUR)
    stranger = _write(drafts, f"{AGENT_A}.stranger", AGENT_A, age_seconds=1 * HOUR)

    with pytest.raises(drafts.AmbiguousDraftError) as excinfo:
        drafts.resolve_draft_id(None, AGENT_A)

    exc = excinfo.value
    assert set(exc.candidates) == {mine, stranger}
    assert exc.agent_id == AGENT_A
    assert exc.code == "ambiguous_agent_draft", (
        "the agent-scoped case needs its own code: unlike the cross-agent one, "
        "it cannot be fixed by adding --agent-id"
    )


def test_refusal_does_not_move_with_recency(drafts):
    """The incident's signature: which draft 'wins' changes between calls.

    Touching the loser makes it the newest, which under recency resolution
    flips the answer. The refusal must be indifferent to that.
    """
    first = _write(drafts, f"{AGENT_A}.first", AGENT_A, age_seconds=2 * HOUR)
    second = _write(drafts, f"{AGENT_A}.second", AGENT_A, age_seconds=1 * HOUR)

    with pytest.raises(drafts.AmbiguousDraftError):
        drafts.resolve_draft_id(None, AGENT_A)

    now = time.time()
    os.utime(drafts.draft_path(first), (now, now))

    with pytest.raises(drafts.AmbiguousDraftError) as excinfo:
        drafts.resolve_draft_id(None, AGENT_A)
    assert set(excinfo.value.candidates) == {first, second}


def test_agent_id_error_names_draft_id_and_stays_bounded(drafts):
    """44 drafts under one handle must not produce a wall of text.

    The measured failure was a 12,931-character error. The message previews at
    most ``_AMBIGUITY_PREVIEW_LIMIT`` candidates as copy-pasteable --draft-id
    arguments, and must not send the caller back through --agent-id, the flag
    that just failed.
    """
    made = [_write(drafts, f"{AGENT_A}.{i:04x}", AGENT_A, age_seconds=i) for i in range(44)]

    with pytest.raises(drafts.AmbiguousDraftError) as excinfo:
        drafts.resolve_draft_id(None, AGENT_A)
    exc = excinfo.value
    message = str(exc)

    preview_lines = [
        line for line in message.splitlines() if line.startswith("  --draft-id ")
    ]
    assert len(message) < 1000, f"message is {len(message)} chars, still a wall"
    assert len(preview_lines) == drafts._AMBIGUITY_PREVIEW_LIMIT
    assert "--draft-id" in message
    assert "not unique" in message.lower(), "must say WHY the handle cannot resolve"
    assert "--agent-id <agent_id>" not in message, (
        "must not recommend the flag the caller already passed"
    )
    assert f"and {len(made) - drafts._AMBIGUITY_PREVIEW_LIMIT} more" in message
    assert len(exc.candidates) == len(made), "full list survives bounding"

    # Every previewed line is directly runnable as written.
    for line in preview_lines:
        assert line.split()[1] in made


def test_spent_drafts_never_manufacture_agent_scoped_ambiguity(drafts):
    """History is not candidacy: finished drafts must not trigger the refusal."""
    mine = _write(drafts, f"{AGENT_A}.mine", AGENT_A, age_seconds=3 * HOUR)
    spent = [
        _write(drafts, f"{AGENT_A}.old{i}", AGENT_A, age_seconds=i * HOUR)
        for i in range(1, 4)
    ]
    _seed_terminal_rows(spent)

    assert drafts.resolve_draft_id(None, AGENT_A) == mine


# ---------------------------------------------------------------------------
# EXACTLY ONE live candidate -> unchanged. An uncolliding agent sees nothing.
# ---------------------------------------------------------------------------

def test_agent_id_with_one_draft_resolves_exactly_as_before(drafts):
    only = _write(drafts, f"{AGENT_A}.only", AGENT_A)
    _write(drafts, f"{AGENT_B}.other", AGENT_B)  # another agent, irrelevant

    assert drafts.resolve_draft_id(None, AGENT_A) == only


def test_explicit_draft_id_still_wins_amid_agent_scoped_ambiguity(drafts):
    mine = _write(drafts, f"{AGENT_A}.mine", AGENT_A, age_seconds=2 * HOUR)
    _write(drafts, f"{AGENT_A}.stranger", AGENT_A, age_seconds=1 * HOUR)

    assert drafts.resolve_draft_id(mine, AGENT_A) == mine


# ---------------------------------------------------------------------------
# ZERO candidates -> None, the pre-existing "no draft" diagnosis, NOT ambiguity.
# ---------------------------------------------------------------------------

def test_agent_id_with_no_drafts_returns_none_and_does_not_raise(drafts):
    _write(drafts, f"{AGENT_B}.other", AGENT_B)

    assert drafts.resolve_draft_id(None, AGENT_A) is None


def test_agent_id_with_only_spent_drafts_still_falls_back(drafts):
    """Deliberate boundary, not an oversight.

    With no live candidate the pool is spent-only, and a spent draft's outcome
    is already a terminal row the writer refuses to amend, so the latest-spent
    fallback is preserved -- read paths that address a FINISHED draft (the
    reconstruction of a lost fence) depend on it. Ambiguity is judged on LIVE
    candidates alone.
    """
    older = _write(drafts, f"{AGENT_A}.older", AGENT_A, age_seconds=2 * HOUR)
    newer = _write(drafts, f"{AGENT_A}.newer", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows([older, newer])

    assert drafts.resolve_draft_id(None, AGENT_A) == newer


# ---------------------------------------------------------------------------
# Through the CLI: the refusal reaches every subcommand from the single seam,
# and no draft is mutated on the way.
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
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _init(agent_id: str, env: dict) -> str:
    proc = _run(["init", "--agent-id", agent_id, "--json"], env)
    assert proc.returncode == 0, f"init({agent_id}) failed: {proc.stderr!r}"
    return json.loads(proc.stdout)["draft_id"]


@pytest.mark.parametrize(
    "argv",
    [
        ["set", "agent_status.next_action", "hijacked"],
        ["add", "agent_status.pending_steps", "sneaky-step"],
        ["view"],
        ["validate"],
        ["finalize"],
    ],
)
def test_cli_subcommands_refuse_a_colliding_agent_id(cli_env, argv):
    draft_1 = _init(AGENT_A, cli_env)
    draft_2 = _init(AGENT_A, cli_env)

    proc = _run([*argv, "--agent-id", AGENT_A], cli_env)
    assert proc.returncode != 0, (
        f"{argv[0]} must refuse a colliding --agent-id; stdout={proc.stdout!r}"
    )

    for draft_id in (draft_1, draft_2):
        view = _run(["view", "--draft-id", draft_id], cli_env)
        assert "hijacked" not in view.stdout
        assert "sneaky-step" not in view.stdout


def test_cli_reports_the_agent_scoped_code_and_lists_candidates(cli_env):
    draft_1 = _init(AGENT_A, cli_env)
    draft_2 = _init(AGENT_A, cli_env)

    proc = _run(
        ["set", "agent_status.next_action", "x", "--agent-id", AGENT_A, "--json"],
        cli_env,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "ambiguous_agent_draft"
    assert set(payload["candidates"]) == {draft_1, draft_2}
    assert "--draft-id" in payload["message"]


def test_cli_agent_id_with_one_draft_still_works(cli_env):
    draft_id = _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    proc = _run(["view", "--agent-id", AGENT_A], cli_env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["draft_id"] == draft_id


def test_cli_agent_id_with_no_drafts_reports_no_draft_not_ambiguity(cli_env):
    _init(AGENT_B, cli_env)

    proc = _run(["set", "agent_status.next_action", "x", "--agent-id", AGENT_A,
                 "--json"], cli_env)
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert "ambiguous" not in json.dumps(payload).lower(), (
        "zero candidates and several candidates are different diagnoses"
    )
    assert "No draft found" in payload["error"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
