"""Retention (GC) and liveness-aware resolution for contract drafts.

Two defects, one root cause -- history was being counted as candidacy:

  * ``--agent-id`` was effectively unusable. Agent handles collide heavily in
    practice (one handle was observed on 64 drafts), and resolution returned
    the most recently modified draft for the handle, which was routinely a
    FINISHED draft from an unrelated turn that merely reused it.
  * Bare resolution raised permanently. Every draft ever created stayed on
    disk, so once two agents had run, the "2+ distinct agents" ambiguity test
    could never be false again -- and the error naming every candidate grew
    with the directory (measured at 12,931 characters on 481 drafts).

The safety property under test throughout: a draft that is NOT spent is never
collectable, whatever its age within the window. That is the draft an agent was
holding when the harness cut it, and recovering it is a real, exercised path.

Isolation: ``GAIA_DATA_DIR`` is redirected to a tmp path so ``drafts_dir()``
and ``db_path()`` both resolve under it -- no test touches the real
``~/.gaia/contract_drafts``.
"""

import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_A = valid_agent_id("a1c2d3")
AGENT_B = valid_agent_id("a4e5f6")
DAY = 86400.0
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


def _write(drafts_mod, draft_id, agent_id, age_seconds=0.0, state="IN_PROGRESS"):
    """Persist a draft and back-date its mtime to simulate elapsed time."""
    drafts_mod.save_draft(draft_id, _envelope(agent_id, state))
    if age_seconds:
        path = drafts_mod.draft_path(draft_id)
        when = time.time() - age_seconds
        os.utime(path, (when, when))
    return draft_id


def _seed_terminal_rows(tmp_path, contract_ids, state="COMPLETE"):
    """Create the minimal agent_contract_handoffs shape the policy reads."""
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
# The negative test: a collided --agent-id resolves usefully instead of
# landing on a stranger's finished draft.
# ---------------------------------------------------------------------------

def test_collided_agent_id_resolves_to_the_live_draft_not_a_spent_one(drafts, tmp_path):
    """The reported failure: one handle, many drafts, most recent is finished.

    The caller's own in-flight draft is OLDER than a finished draft that reused
    the handle. Pure latest-mtime hands back the finished one; liveness-aware
    resolution hands back the caller's.
    """
    mine = _write(drafts, f"{AGENT_A}.mine", AGENT_A, age_seconds=2 * HOUR)
    stranger = _write(drafts, f"{AGENT_A}.stranger", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows(tmp_path, [stranger])

    assert drafts.list_draft_ids(AGENT_A)[0] == stranger, (
        "precondition: the spent draft is the most recently modified"
    )
    assert drafts.resolve_draft_id(None, AGENT_A) == mine


def test_agent_id_falls_back_to_most_recent_when_every_draft_is_spent(drafts, tmp_path):
    """All spent -> still resolve something rather than regressing to None."""
    older = _write(drafts, f"{AGENT_A}.older", AGENT_A, age_seconds=2 * HOUR)
    newer = _write(drafts, f"{AGENT_A}.newer", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows(tmp_path, [older, newer])

    assert drafts.resolve_draft_id(None, AGENT_A) == newer


def test_spent_drafts_do_not_manufacture_cross_agent_ambiguity(drafts, tmp_path):
    """A finished draft from another agent is history, not a rival candidate."""
    live = _write(drafts, f"{AGENT_A}.live", AGENT_A, age_seconds=1 * HOUR)
    other = _write(drafts, f"{AGENT_B}.done", AGENT_B, age_seconds=30)
    _seed_terminal_rows(tmp_path, [other])

    assert drafts.resolve_draft_id() == live


def test_two_live_agents_still_refuse_to_guess(drafts):
    """The real cross-agent case must still raise -- liveness narrows, not disarms."""
    a = _write(drafts, f"{AGENT_A}.live", AGENT_A)
    b = _write(drafts, f"{AGENT_B}.live", AGENT_B)

    with pytest.raises(drafts.AmbiguousDraftError) as excinfo:
        drafts.resolve_draft_id()
    assert set(excinfo.value.candidates) == {a, b}


# ---------------------------------------------------------------------------
# The error is readable and actionable, independent of the ambiguity.
# ---------------------------------------------------------------------------

def test_ambiguity_message_is_bounded_and_actionable(drafts):
    """Many candidates must not produce a wall of text.

    The full enumeration reached ~13 KB in the field. The message now previews
    a few candidates as copy-pasteable ``--draft-id`` arguments and summarizes
    the rest, while ``.candidates`` keeps every id for programmatic callers.
    """
    made = []
    for i in range(40):
        agent = AGENT_A if i % 2 else AGENT_B
        made.append(_write(drafts, f"{agent}.{i:04x}", agent, age_seconds=i))

    with pytest.raises(drafts.AmbiguousDraftError) as excinfo:
        drafts.resolve_draft_id()
    exc = excinfo.value
    message = str(exc)

    assert len(message) < 1000, f"message is {len(message)} chars, still a wall"
    assert message.count("--draft-id ") <= drafts._AMBIGUITY_PREVIEW_LIMIT + 1
    assert "--agent-id" in message
    assert "and 35 more" in message
    assert len(exc.candidates) == 40, "full candidate list must survive bounding"

    # Every previewed line is directly runnable: the id follows the flag
    # verbatim, so the caller can copy the line and re-run.
    previews = [
        line.strip() for line in message.splitlines()
        if line.startswith("  --draft-id ")
    ]
    assert len(previews) == drafts._AMBIGUITY_PREVIEW_LIMIT
    for line in previews:
        assert line.split("--draft-id ", 1)[1].split()[0] in exc.candidates


# ---------------------------------------------------------------------------
# Retention: safe by construction.
# ---------------------------------------------------------------------------

def test_recoverable_draft_is_never_collected(drafts, tmp_path):
    """The property that matters most: a live draft is outside the selection.

    An agent cut mid-turn leaves an unfinalized draft. It has no terminal row,
    so no elapsed time inside the age window can make it collectable.
    """
    cut_off = _write(drafts, f"{AGENT_A}.cut", AGENT_A, age_seconds=6 * DAY)

    selected = drafts.collectable_drafts(max_age_days=7, grace_hours=24)

    assert cut_off not in {r["draft_id"] for r in selected}


def test_spent_draft_past_grace_is_collected(drafts, tmp_path):
    spent = _write(drafts, f"{AGENT_A}.spent", AGENT_A, age_seconds=48 * HOUR)
    _seed_terminal_rows(tmp_path, [spent])

    selected = drafts.collectable_drafts(max_age_days=7, grace_hours=24)

    assert [r["draft_id"] for r in selected] == [spent]
    assert selected[0]["reason"] == "spent"


def test_spent_draft_inside_grace_is_kept(drafts, tmp_path):
    """Finalized is not the same moment as no-longer-read.

    The orchestrator reads a just-closed draft to relay the turn's outcome, so
    the grace window keeps it for that window.
    """
    fresh = _write(drafts, f"{AGENT_A}.fresh", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows(tmp_path, [fresh])

    selected = drafts.collectable_drafts(max_age_days=7, grace_hours=24)

    assert selected == []


def test_non_terminal_row_is_not_spent(drafts, tmp_path):
    """An in-flight row means the turn is still running -- never collectable."""
    running = _write(drafts, f"{AGENT_A}.running", AGENT_A, age_seconds=48 * HOUR)
    _seed_terminal_rows(tmp_path, [running], state="IN_PROGRESS")

    selected = drafts.collectable_drafts(max_age_days=7, grace_hours=24)

    assert selected == []


def test_aged_draft_is_collected_without_any_db_row(drafts):
    """The backstop lane: a draft that never finalized still ages out."""
    old = _write(drafts, f"{AGENT_A}.old", AGENT_A, age_seconds=10 * DAY)

    selected = drafts.collectable_drafts(max_age_days=7, grace_hours=24)

    assert [r["draft_id"] for r in selected] == [old]
    assert selected[0]["reason"] == "aged"


def test_unreadable_db_degrades_to_age_only_and_never_widens(drafts, monkeypatch):
    """No evidence must never become evidence of disposability."""
    monkeypatch.setattr(drafts, "_ro_db_connect", lambda: None)
    recent = _write(drafts, f"{AGENT_A}.recent", AGENT_A, age_seconds=2 * HOUR)
    old = _write(drafts, f"{AGENT_A}.old", AGENT_A, age_seconds=10 * DAY)

    selected = {r["draft_id"] for r in drafts.collectable_drafts(max_age_days=7)}

    assert selected == {old}
    assert recent not in selected
    assert drafts.spent_draft_ids([recent, old]) == set()


# ---------------------------------------------------------------------------
# The GC hook executes the shared policy, and its dry run is truthful.
# ---------------------------------------------------------------------------

def _gc_module():
    hooks_dir = _REPO_ROOT / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    import modules.session.contract_drafts_gc as gc_mod

    return importlib.reload(gc_mod)


def test_dry_run_reports_without_deleting(drafts, tmp_path):
    spent = _write(drafts, f"{AGENT_A}.spent", AGENT_A, age_seconds=48 * HOUR)
    live = _write(drafts, f"{AGENT_A}.live", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows(tmp_path, [spent])
    gc_mod = _gc_module()

    would = gc_mod.gc_contract_drafts(max_days=7, grace_hours=24, dry_run=True)

    assert would == 1
    assert drafts.draft_exists(spent), "dry run must not delete"
    assert drafts.draft_exists(live)


def test_sweep_deletes_exactly_what_the_dry_run_reported(drafts, tmp_path):
    spent = _write(drafts, f"{AGENT_A}.spent", AGENT_A, age_seconds=48 * HOUR)
    live = _write(drafts, f"{AGENT_A}.live", AGENT_A, age_seconds=1 * HOUR)
    _seed_terminal_rows(tmp_path, [spent])
    gc_mod = _gc_module()

    would = gc_mod.gc_contract_drafts(max_days=7, grace_hours=24, dry_run=True)
    deleted = gc_mod.gc_contract_drafts(max_days=7, grace_hours=24)

    assert deleted == would == 1
    assert not drafts.draft_exists(spent)
    assert drafts.draft_exists(live), "the recoverable draft survives the sweep"
