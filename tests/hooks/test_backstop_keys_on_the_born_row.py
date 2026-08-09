"""The SubagentStop capture keys on the row born at dispatch, not on a file glob.

The capture (``handoff_persister.persist_handoff``, steps 1-4) used to resolve
its target row by globbing the DRAFTS DIRECTORY under an agent id it took from
the fence verbatim, and it refused -- deliberately, in a comment -- to key on the
row the dispatch had already created for the turn. Three consequences, all
measured:

  * The agent id is minted once per dispatch, but a CONTINUATION opens a new
    contract under the SAME handle, so each resumption adds a live draft.
    ``resolve_draft_id`` then raises ``AmbiguousDraftError`` -- and a bare
    ``except`` discarded it without a single log line.
  * The capture fell to a synthetic ``hook-backstop.*`` id which by construction
    can never pre-exist, so it always read "no row" and always wrote one, while
    step 5 went on to find the born row and point it at the synthetic twin. 21
    rows carry exactly that shape.
  * A fence id that could never key a draft (``execution-approved``,
    ``a_placeholder``) was used as the glob key regardless; a wrong key and an
    absent draft produce the identical silence.

These pin the properties of the fix. The accepted, deliberate consequence is
asserted head-on rather than worked around: keying on the born row means the
capture now finds a row in state DISPATCHED, which arms the COMPLETE downgrade
that used to be skipped -- so a fence-only turn that declared COMPLETE without
ever running the contract CLI is recorded IN_PROGRESS. The row must stay
AUDITABLE through that: its provenance marks, its cut reason, and the turn's own
claim all survive on it.

Every test runs against a real sqlite substrate and an isolated drafts dir. The
harness agent id is deliberately DIFFERENT from the minted one throughout --
equating them makes every lane appear to work without ever crossing the two
identifier spaces.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import (  # noqa: E402
    drafts_dir,
    mint_draft_id,
    save_draft,
)
from gaia.store.writer import (  # noqa: E402
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from modules.agents.handoff_persister import persist_handoff  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_NAME = "gaia-system"
MINTED_AGENT_ID = valid_agent_id("born-row")
# The harness's own per-run id. Same shape, different identifier space.
HARNESS_AGENT_ID = "a00000000000000ff"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _task_info(db_path: Path, *, agent_id: str = HARNESS_AGENT_ID) -> dict:
    return {
        "agent": AGENT_NAME,
        "agent_id": agent_id,
        "workspace": WORKSPACE,
        "db_path": str(db_path),
    }


def _born_row(
    db_path: Path,
    session_id: str,
    *,
    contract_id: str | None = None,
    agent_id: str = MINTED_AGENT_ID,
    kind: str | None = "investigation",
    harness_agent_id: str | None = HARNESS_AGENT_ID,
) -> str:
    """The dispatch lifecycle every turn's row goes through: born under the
    MINTED identity with the agent NAME in the birth envelope, then stamped with
    the harness id when SubagentStart claims it."""
    contract_id = contract_id or f"{agent_id}.{'b' * 12}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=agent_id,
        workspace=WORKSPACE,
        session_id=session_id,
        agent_name=AGENT_NAME,
        kind=kind,
        db_path=db_path,
    )
    if harness_agent_id:
        stamp_harness_agent_id(contract_id, harness_agent_id, db_path=db_path)
    return contract_id


def _fence(agent_state: str = "COMPLETE", agent_id: str = MINTED_AGENT_ID) -> dict:
    return {
        "agent_status": {
            "agent_state": agent_state,
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": ["lo que hizo el turno"], "verbatim_outputs": [],
            "cross_layer_impacts": [], "open_gaps": [],
            "verification": {"method": "test", "checks": ["born-row"],
                             "result": "pass", "details": "ok"},
        },
    }


def _rows(db_path: Path) -> list:
    if not db_path.is_file():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM agent_contract_handoffs ORDER BY id"
        )]
    finally:
        con.close()


def _payload(row: dict) -> dict:
    return json.loads(row["raw_handoff_json"])


# ---------------------------------------------------------------------------
# The born row IS the capture's key
# ---------------------------------------------------------------------------

def test_a_turn_that_never_ran_the_cli_converges_its_own_born_row(db):
    """The row exists, so the capture must use it -- not invent a second one.

    A turn whose draft never made it to disk (pre-creation is best-effort, and
    the drafts GC sweeps) left the glob nothing to match, so the capture keyed a
    synthetic id, found no row under it (by construction there can be none), and
    wrote a degraded row BESIDE the one the dispatch had already created.
    """
    session = "sess-born"
    contract_id = _born_row(db, session)
    assert list(drafts_dir().glob("*.json")) == [], (
        "the scenario is a turn with NO draft on disk; a draft would make this "
        "resolve through the glob and prove nothing about the row lane"
    )

    persist_handoff(
        parsed_contract=None,
        agent_output="",
        task_info=_task_info(db),
        session_id=session,
    )

    rows = _rows(db)
    assert len(rows) == 1, (
        "the turn must end with ONE row -- the one it was born with. Extra: "
        + ", ".join(r["contract_id"] for r in rows[1:])
    )
    assert rows[0]["contract_id"] == contract_id
    assert not any(
        r["contract_id"].startswith("hook-backstop.") for r in rows
    ), "no synthetic row may be fabricated for a turn whose row already exists"
    # The row left DISPATCHED with an honest, non-COMPLETE verdict.
    assert rows[0]["agent_state"] == "IN_PROGRESS"
    assert rows[0]["cut_reason"] == "reaped"


def test_the_converged_row_stays_auditable(db):
    """Keying on the born row means overwriting its envelope. Everything that
    says WHERE the row came from and WHY it reads as it does must survive that:
    the birth marks, the capture provenance, and the cut reason."""
    session = "sess-audit"
    contract_id = _born_row(db, session)

    persist_handoff(
        parsed_contract=_fence("COMPLETE"),
        agent_output="",
        task_info=_task_info(db),
        session_id=session,
    )

    row = _rows(db)[0]
    payload = _payload(row)
    assert row["contract_id"] == contract_id
    # Where the row came from: the dispatch's own marks, carried forward.
    assert payload.get("born_at_dispatch") is True
    assert payload.get("agent_name") == AGENT_NAME
    # How it was captured.
    assert payload.get("degraded") is True
    assert payload.get("auto_captured") is True
    assert payload.get("backstop") == "hook_subagent_stop"
    assert payload.get("reaped") is True
    # Why it reads non-clean, without parsing the envelope.
    assert row["cut_reason"] == "reaped"


def test_the_fence_only_distinction_survives_as_a_provenance_mark(db):
    """The refusal to adopt the born row was defended as the only way to tell a
    FENCE-ONLY turn from a normal one -- by its synthetic id.

    The distinction is real; the id was never what carried it. It is a fact
    about the turn (it emitted a fence and left no draft behind it), so it is
    recorded as one. A draft-sourced capture of the same turn says ``draft``.
    """
    session = "sess-fence-only"
    _born_row(db, session)

    persist_handoff(
        parsed_contract=_fence("COMPLETE"),
        agent_output="",
        task_info=_task_info(db),
        session_id=session,
    )

    payload = _payload(_rows(db)[0])
    assert payload.get("capture_source") == "fence_only", (
        "a turn captured from its fence alone must stay distinguishable from "
        "one captured from a draft"
    )
    assert payload.get("capture_key_space") == "dispatch_row"


def test_the_fence_only_complete_is_recorded_in_progress_and_keeps_its_claim(db):
    """The ACCEPTED consequence, asserted rather than avoided.

    Keying on the born row means the capture now meets a row in 'DISPATCHED',
    which arms the COMPLETE downgrade that the synthetic key used to skip. A
    turn that declared COMPLETE in a fence without ever running the contract CLI
    never had that COMPLETE verified, so the row records IN_PROGRESS -- the same
    reading the reap has always applied. The claim is not erased: it stays in
    the envelope, beside the marks that say why the row disagrees with it.
    """
    session = "sess-downgrade"
    _born_row(db, session)

    persist_handoff(
        parsed_contract=_fence("COMPLETE"),
        agent_output="",
        task_info=_task_info(db),
        session_id=session,
    )

    row = _rows(db)[0]
    assert row["agent_state"] == "IN_PROGRESS", (
        "an unverified COMPLETE would falsely satisfy the briefs invariant "
        "'plan closed => a COMPLETE handoff row exists'"
    )
    assert _payload(row)["agent_status"]["agent_state"] == "COMPLETE", (
        "the turn's own claim is evidence; the downgrade records a verdict, it "
        "does not rewrite what the turn said"
    )
    assert row["cut_reason"] == "reaped"


# ---------------------------------------------------------------------------
# Ambiguity is a condition, not an accident to swallow
# ---------------------------------------------------------------------------

def test_ambiguous_drafts_are_reported_and_resolved_by_the_row(db, caplog):
    """Two live drafts under one handle is the RESUMPTION shape, not a fault.

    ``resolve_draft_id`` raises, correctly, because the glob cannot name which
    draft is this turn's. The born row can. What must never happen again is the
    bare ``except`` that dropped the exception with no log, no mark, and a
    synthetic row as the only trace.
    """
    session = "sess-ambig"
    contract_id = _born_row(db, session)
    # This turn's own draft, plus the live draft its earlier link left behind --
    # both under the SAME minted handle, which is what makes the glob ambiguous.
    save_draft(contract_id, {"agent_status": {
        "agent_state": "IN_PROGRESS", "agent_id": MINTED_AGENT_ID,
        "pending_steps": [], "next_action": "seguir",
    }, "evidence_report": {
        "patterns_checked": [], "files_checked": ["el archivo del turno"],
        "commands_run": [], "key_outputs": [], "verbatim_outputs": [],
        "cross_layer_impacts": [], "open_gaps": [],
    }})
    sibling_draft = mint_draft_id(MINTED_AGENT_ID)
    save_draft(sibling_draft, {"agent_status": {
        "agent_state": "IN_PROGRESS", "agent_id": MINTED_AGENT_ID,
        "pending_steps": [], "next_action": "otro link",
    }})

    with caplog.at_level("WARNING"):
        persist_handoff(
            parsed_contract=None,
            agent_output="",
            task_info=_task_info(db),
            session_id=session,
        )

    assert any("AMBIGUOUS" in rec.message for rec in caplog.records), (
        "an ambiguity discarded without a log is information the system had "
        "and threw away"
    )
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["contract_id"] == contract_id, (
        "the born row names which of the ambiguous drafts is this turn's"
    )
    payload = _payload(rows[0])
    assert sorted(payload["draft_ambiguity"]["candidates"]) == sorted(
        [contract_id, sibling_draft]
    ), "the row itself must say which candidates collided"
    # Keyed on the row, the capture reads THAT draft -- so the evidence the turn
    # accumulated survives the convergence instead of being overwritten.
    assert payload.get("capture_source") == "draft"
    assert payload["evidence_report"]["files_checked"] == ["el archivo del turno"]


def test_an_unusable_fence_agent_id_never_becomes_a_key(db, caplog):
    """The fence is agent-written and was trusted verbatim.

    A value that cannot key a draft must not be used as one -- and must not
    reach the synthetic id either, where it silently became part of a row's
    identity (``hook-backstop.execution-approved.<session>``).
    """
    session = "sess-badid"
    contract_id = _born_row(db, session)

    with caplog.at_level("WARNING"):
        persist_handoff(
            parsed_contract=_fence("COMPLETE", agent_id="execution-approved"),
            agent_output="",
            task_info=_task_info(db),
            session_id=session,
        )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["contract_id"] == contract_id
    assert "execution-approved" not in rows[0]["contract_id"]
    assert rows[0]["agent_id"] == MINTED_AGENT_ID, (
        "the row's identity is the handle the dispatch minted, never the string "
        "the fence happened to carry"
    )
    assert any(
        "is not a minted handle" in rec.message for rec in caplog.records
    )


def test_adopting_the_born_row_never_closes_a_concurrent_siblings_dispatch(db):
    """Adoption removes the scaffold the closure's guard used to see.

    Once the capture converges the born row, step 5 finds no orphan and reaches
    its LAST-RESORT lane, which matches on the dispatched agent NAME -- a
    coordinate every concurrent dispatch of that agent shares. The guard that
    stops it is "the turn's own row came from a dispatch", and the structural
    test for that reads the binding columns, which are all NULL on a dispatch
    that carried no plan coordinates. So the capture must ASSERT the adoption it
    just performed rather than leave it to be inferred.
    """
    session = "sess-sibling"
    mine = _born_row(db, session, kind=None)
    sibling = _born_row(
        db, session,
        contract_id=f"{valid_agent_id('sibling')}.{'c' * 12}",
        agent_id=valid_agent_id("sibling"),
        kind=None,
        harness_agent_id=None,   # still running: SubagentStop has not fired
    )

    persist_handoff(
        parsed_contract=None,
        agent_output="",
        task_info=_task_info(db),
        session_id=session,
    )

    by_id = {r["contract_id"]: r for r in _rows(db)}
    assert by_id[mine]["agent_state"] == "IN_PROGRESS"
    assert by_id[sibling]["agent_state"] == "DISPATCHED", (
        "a turn that closed its own row must not reach across and close the "
        "live dispatch of a sibling that merely shares its agent name"
    )


# ---------------------------------------------------------------------------
# The rescue still rescues
# ---------------------------------------------------------------------------

def test_a_turn_with_no_resolvable_row_still_gets_one(db):
    """These lanes exist to RESCUE: they run when something already went wrong.

    With no draft, no fence and no row reachable (the harness id is the
    placeholder the bridge refuses), the capture must still leave a row. Losing
    it entirely is worse than any amount of untidiness.
    """
    persist_handoff(
        parsed_contract=None,
        agent_output="se cortó a la mitad",
        task_info=_task_info(db, agent_id="unknown"),
        session_id="sess-nothing",
    )

    rows = _rows(db)
    assert len(rows) == 1, "a turn with nothing resolvable must still leave a row"
    assert rows[0]["contract_id"] == f"hook-backstop.{AGENT_NAME}.sess-nothing"
    payload = _payload(rows[0])
    assert payload.get("degraded") is True
    assert payload.get("capture_key_space") == "synthetic"
    assert payload.get("capture_source") == "none"
    assert rows[0]["agent_state"] == "IN_PROGRESS"


def test_an_unreadable_drafts_substrate_falls_through_to_the_row(db, monkeypatch, caplog):
    """A broken drafts directory must cost the capture its draft, not its row."""
    session = "sess-broken-drafts"
    contract_id = _born_row(db, session)

    import gaia.contract.drafts as _drafts

    def _boom(*_args, **_kwargs):
        raise OSError("drafts directory is not readable")

    monkeypatch.setattr(_drafts, "resolve_draft_id", _boom)

    with caplog.at_level("WARNING"):
        persist_handoff(
            parsed_contract=_fence("IN_PROGRESS"),
            agent_output="",
            task_info=_task_info(db),
            session_id=session,
        )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["contract_id"] == contract_id, (
        "with the drafts substrate gone, the row is the only key left -- and it "
        "is enough"
    )
    # The born row exists whether or not the capture ran, so its mere presence
    # proves nothing. Having LEFT 'DISPATCHED' is what proves the capture
    # survived the failure instead of being aborted by it.
    assert rows[0]["agent_state"] == "IN_PROGRESS", (
        "a substrate failure must cost the capture its draft, never its row"
    )
    assert rows[0]["cut_reason"] == "reaped"
    assert any("unreadable" in rec.message for rec in caplog.records), (
        "a substrate failure is a different diagnosis from an ambiguity and "
        "must not be reported as one"
    )
