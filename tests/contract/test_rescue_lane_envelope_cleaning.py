"""The two RESCUE lanes clean the envelope before persisting it.

Three writers reach ``agent_contract_handoffs.raw_handoff_json``. The CLI one
(``gaia contract set/fill/finalize``) has always sanitized and canonicalized on
the way in. The two RESCUE lanes -- the T9 hook backstop
(``handoff_persister.persist_handoff``) and the T11 truncation salvage
(``ClaudeCodeAdapter._salvage_truncated_draft``) -- serialized whatever they
found, so a fifth of the persisted rows arrived carrying undeclared keys and
uncanonical enum spellings the CLI route would never have written. This file
proves both lanes now clean, and -- the harder half -- that cleaning never costs
the rescue what the rescue exists to save.

Three properties, and the last two matter more than the first:

  * **Clean.** An envelope that would have persisted dirty by either lane now
    persists with undeclared keys dropped and enum spellings canonical.
  * **Still traceable.** ``sanitize_envelope`` gates a declared key on its TYPE,
    never on its value's format, so the malformed-but-string ``agent_id`` values
    actually observed in the persisted population survive byte-identical. The
    one shape that WOULD be dropped -- a non-string ``agent_id``, which the
    sanitizer removes as an unrepairable type -- is preserved in string form
    instead, because losing it converts a bad value into a missing field and
    erases the only handle back to the turn. Separately, the row's own
    ``agent_id`` column is resolved from the dispatch, not from the envelope, so
    it is out of the sanitizer's reach by construction.
  * **Still rescuing.** These lanes run only when something already went wrong,
    on input nobody validated. If cleaning raises, the rescue must persist the
    envelope dirty rather than lose the row: a dirty row is recoverable, an
    absent one is not.

The isolation fixtures mirror ``test_truncation_salvage.py``: drafts and the
default DB under an isolated ``GAIA_DATA_DIR``, with the writer materializing
the real schema on first connect.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.contract.drafts import mint_draft_id, save_draft  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from modules.agents.handoff_persister import (  # noqa: E402
    clean_rescue_envelope,
    persist_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

VALID_AGENT_ID = valid_agent_id("rescue-cleaning")
HARNESS_AGENT_ID = "a00000000000000ff"
WORKSPACE = "me"

# The malformed ``agent_id`` values observed inside real persisted envelopes on
# the rescue lane. Written inline, never minted: an invalid handle is the
# SUBJECT of these assertions, and a fixture that keeps them conforming would
# make the test pass without ever exercising the case.
MALFORMED_AGENT_IDS = [
    "execution-approved",           # a status string where a handle belongs
    "audit-operator",               # an agent NAME where a handle belongs
    "a_placeholder",                # a literal placeholder
    "axb3c9d1e2f4a5b6c7",           # 'x' is not a hex digit
    "a0000000000000000",            # sixteen zeros
    valid_agent_id("some-other-turn"),  # well-formed, but not this row's handle
]


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _dirty_envelope(agent_id: str = VALID_AGENT_ID) -> dict:
    """A draft as the rescue lanes actually find it: valid content, plus the
    two defects the CLI route strips and these lanes used to persist -- an
    undeclared key with nowhere to go, and enum values in a spelling the
    validator only accepts after normalizing."""
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": agent_id,
            "pending_steps": ["finish"],
            "next_action": "continue",
        },
        "work_phase": "  Verifying  ",
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "pytest", "checks": ["x"],
                             "result": "PASS", "details": "ok"},
        },
        "scratch_notes": "an undeclared key no verb can remove",
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path | None) -> dict:
    ti = {"agent_id": HARNESS_AGENT_ID, "agent": "developer", "workspace": WORKSPACE}
    if db_path is not None:
        ti["db_path"] = str(db_path)
    return ti


def _born_and_stamped(draft_id: str, db_path: Path) -> None:
    insert_dispatched_handoff(
        contract_id=draft_id, agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE, db_path=db_path,
    )
    stamp_harness_agent_id(draft_id, HARNESS_AGENT_ID, db_path=db_path)


def _seed_brief(db_path: Path, brief_id: int) -> None:
    """The row's brief_id is a real foreign key, so the brief has to exist for
    the link to be assertable end to end."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (brief_id, WORKSPACE, "rescue-lane-cleaning", "in-progress"),
        )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path):
    if not db_path.is_file():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT contract_id, agent_id, agent_state, brief_id, cut_reason, "
            "raw_handoff_json FROM agent_contract_handoffs ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _only_payload(db_path: Path) -> dict:
    rows = _rows(db_path)
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    return json.loads(rows[0]["raw_handoff_json"])


# ---------------------------------------------------------------------------
# 1. Both lanes persist a CLEAN envelope
# ---------------------------------------------------------------------------

def test_backstop_persists_cleaned_envelope(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope())
    _born_and_stamped(draft_id, db)

    persist_handoff(
        parsed_contract=None, agent_output="cut off",
        task_info=_task_info(db), session_id="sess-backstop-clean",
    )

    payload = _only_payload(db)
    assert "scratch_notes" not in payload, (
        "an undeclared key must not reach the row through the backstop"
    )
    assert payload["work_phase"] == "verifying"
    assert payload["evidence_report"]["verification"]["result"] == "pass"
    # The provenance flags are added AFTER cleaning, so cleaning never eats them.
    assert payload.get("degraded") is True
    assert payload.get("backstop") == "hook_subagent_stop"


def test_salvage_persists_cleaned_envelope(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope())
    _born_and_stamped(draft_id, db)

    out = ClaudeCodeAdapter()._salvage_truncated_draft(
        parsed_contract=None, task_info=_task_info(db),
        session_id="sess-salvage-clean",
    )
    assert out is not None and out["created"] is True

    payload = _only_payload(db)
    assert "scratch_notes" not in payload, (
        "an undeclared key must not reach the row through the salvage"
    )
    assert payload["work_phase"] == "verifying"
    assert payload["evidence_report"]["verification"]["result"] == "pass"
    assert payload.get("salvaged") == "truncation"
    assert payload.get("degraded") is True


def test_cleaning_does_not_promote_an_uncanonical_verdict(db):
    """Cleaning the envelope must not re-decide what the turn is RECORDED AS.

    The boundary of this change, pinned so it cannot drift open by accident.
    A fence spelling its state ``"complete"`` is still recorded IN_PROGRESS: the
    verdict is read from the RAW envelope, so canonicalization reaches the
    persisted JSON but never the ``agent_state`` column. Promoting it would let
    a turn that never ran its own finalize land COMPLETE on a spelling, and
    would widen a population that is being reviewed case by case -- a decision
    that is not envelope hygiene's to make.

    Both halves are asserted together on purpose: the payload IS cleaned
    (``next_action`` canonicalized, the undeclared key gone), and the column is
    NOT promoted. That pairing is what shows the carve-out is scoped to the
    verdict rather than a cleaning that silently stopped working.
    """
    envelope = _dirty_envelope()
    envelope["agent_status"]["agent_state"] = "complete"
    envelope["agent_status"]["next_action"] = "Done"

    persist_handoff(
        parsed_contract=envelope, agent_output="",
        task_info=_task_info(db), session_id="sess-canon-state",
    )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "IN_PROGRESS"

    payload = json.loads(rows[0]["raw_handoff_json"])
    assert payload["agent_status"]["next_action"] == "done"
    assert "scratch_notes" not in payload
    assert payload.get("degraded") is True
    assert rows[0]["cut_reason"] == "backstop_capture"


def test_salvage_does_not_promote_an_uncanonical_verdict(db):
    """The same boundary on the salvage lane, which needs it more.

    The backstop downgrades a COMPLETE claim to IN_PROGRESS when it reconciles
    an orphaned dispatch row; this lane has no such downgrade, so whatever state
    it reads is what the row records. Asserted separately rather than folded
    into the backstop case because the two lanes hold the line in different
    files, and a regression in one would otherwise hide behind the other.
    """
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _dirty_envelope()
    envelope["agent_status"]["agent_state"] = "complete"
    envelope["agent_status"]["next_action"] = "Done"
    save_draft(draft_id, envelope)
    _born_and_stamped(draft_id, db)

    out = ClaudeCodeAdapter()._salvage_truncated_draft(
        parsed_contract=None, task_info=_task_info(db),
        session_id="sess-salvage-verdict",
    )
    assert out is not None and out["created"] is True

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "IN_PROGRESS"

    payload = json.loads(rows[0]["raw_handoff_json"])
    assert payload["agent_status"]["next_action"] == "done"
    assert "scratch_notes" not in payload
    assert rows[0]["cut_reason"] == "salvaged_truncation"


# ---------------------------------------------------------------------------
# 2. Traceability survives the cleaning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("malformed", MALFORMED_AGENT_IDS)
def test_backstop_preserves_malformed_agent_id(db, malformed):
    """The exact population this change begins to sanitize. Each of these is a
    STRING, and the sanitizer judges a declared key by type rather than by
    format, so the value must arrive in the row unchanged -- wrong, and legible
    as wrong. Erasing it would turn the only handle back to the turn into a
    missing field."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope(agent_id=malformed))
    _born_and_stamped(draft_id, db)

    persist_handoff(
        parsed_contract=None, agent_output="",
        task_info=_task_info(db), session_id="sess-trace",
    )

    payload = _only_payload(db)
    assert payload["agent_status"]["agent_id"] == malformed


@pytest.mark.parametrize("malformed", MALFORMED_AGENT_IDS)
def test_salvage_preserves_malformed_agent_id(db, malformed):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope(agent_id=malformed))
    _born_and_stamped(draft_id, db)

    ClaudeCodeAdapter()._salvage_truncated_draft(
        parsed_contract=None, task_info=_task_info(db),
        session_id="sess-trace-salvage",
    )

    payload = _only_payload(db)
    assert payload["agent_status"]["agent_id"] == malformed


@pytest.mark.parametrize("weird", [12345, {"id": "x"}, ["a"]])
def test_non_string_agent_id_is_kept_not_dropped(weird):
    """The one shape the sanitizer WOULD erase. It has no lossless string
    reading, so the sanitizer removes it as an unrepairable type -- trading a
    wrong value for an absent one. On a rescue lane that trade is the whole
    loss, so the value is put back in string form: still not a conforming
    handle, still honest about what the turn wrote, and still findable."""
    envelope = {"agent_status": {"agent_state": "IN_PROGRESS",
                                 "agent_id": weird, "next_action": "continue"}}
    cleaned = clean_rescue_envelope(envelope)
    assert cleaned["agent_status"]["agent_id"] == str(weird)


def test_row_agent_id_column_is_independent_of_the_envelope(db):
    """Row-level traceability does not go through the envelope at all: the
    column is resolved from the dispatch, so no envelope defect can reach it."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope(agent_id="execution-approved"))
    _born_and_stamped(draft_id, db)

    persist_handoff(
        parsed_contract=None, agent_output="",
        task_info=_task_info(db), session_id="sess-column",
    )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == VALID_AGENT_ID
    assert rows[0]["contract_id"] == draft_id


def test_brief_link_survives_cleaning(db):
    """A top-level ``brief_id`` is not a declared envelope key, so cleaning
    legitimately drops it from the JSON -- but it is also how the backstop finds
    the brief the row belongs to. The link is read before cleaning; reading it
    after would silently unlink every rescued row that carried one."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _dirty_envelope()
    envelope["brief_id"] = 7
    save_draft(draft_id, envelope)
    _born_and_stamped(draft_id, db)
    _seed_brief(db, 7)

    persist_handoff(
        parsed_contract=None, agent_output="",
        task_info=_task_info(db), session_id="sess-brief",
    )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["brief_id"] == 7
    assert "brief_id" not in json.loads(rows[0]["raw_handoff_json"])


# ---------------------------------------------------------------------------
# 3. The rescue keeps rescuing when the cleaning cannot be applied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("broken", [None, 42, "half a draft {\"agent_st", ["x"]])
def test_cleaning_a_non_dict_returns_it_unchanged(broken):
    """A rescue lane may be handed something that is not an envelope at all.
    Cleaning must hand it straight back, never raise -- the caller decides what
    an unusable input means."""
    assert clean_rescue_envelope(broken) is broken


def test_backstop_still_persists_when_cleaning_raises(db, monkeypatch):
    """The property that matters most: if the cleaning itself blows up on input
    nobody validated, the row must still land. A dirty row is recoverable; a
    lost one is not."""
    def _explode(*args, **kwargs):
        raise RuntimeError("sanitizer blew up on a half-written draft")

    monkeypatch.setattr(
        "gaia.contract.validator.sanitize_envelope", _explode
    )

    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope())
    _born_and_stamped(draft_id, db)

    persist_handoff(
        parsed_contract=None, agent_output="",
        task_info=_task_info(db), session_id="sess-explode",
    )

    payload = _only_payload(db)
    assert payload["agent_status"]["agent_id"] == VALID_AGENT_ID
    # Uncleaned, because cleaning failed -- and that is the point: the fallback
    # is the envelope as it arrived, not the absence of a row.
    assert payload.get("scratch_notes") == "an undeclared key no verb can remove"
    assert payload.get("degraded") is True


def test_salvage_still_persists_when_cleaning_raises(db, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("sanitizer blew up on a half-written draft")

    monkeypatch.setattr(
        "gaia.contract.validator.sanitize_envelope", _explode
    )

    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _dirty_envelope())
    _born_and_stamped(draft_id, db)

    out = ClaudeCodeAdapter()._salvage_truncated_draft(
        parsed_contract=None, task_info=_task_info(db),
        session_id="sess-explode-salvage",
    )
    assert out is not None and out["created"] is True

    payload = _only_payload(db)
    assert payload.get("scratch_notes") == "an undeclared key no verb can remove"
    assert payload.get("salvaged") == "truncation"


def test_helper_never_raises_on_a_hostile_envelope(monkeypatch):
    """Contained at the helper, not only at the call sites, so a third rescue
    lane added later inherits the guarantee instead of re-deriving it."""
    def _explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "gaia.contract.validator.canonicalize_envelope", _explode
    )
    envelope = {"agent_status": {"agent_state": "IN_PROGRESS"}}
    assert clean_rescue_envelope(envelope) is envelope
