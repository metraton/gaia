"""Reproduction/regression for the pendiente-1 risk flagged after the `view`
fix: does `set`/`add`/`fill` re-fabricate a BLANK envelope, discarding real
evidence already mirrored to `raw_handoff_json`, when the on-disk draft file
is lost mid-turn?

The scenario: a turn runs `set`/`add`/`fill` normally (real evidence lands on
disk AND is mirrored onto the row via
`gaia.store.writer.mirror_partial_contract_handoff`), then the draft FILE is
lost (deleted -- e.g. `contract_drafts/` cleared, disk hiccup) while the row
still carries the real mirrored evidence. The next `set`/`add`/`fill` call
against the SAME `--draft-id` resolves via `_load_target_draft`'s DEFAULT
`allow_adopt=True`: `_draft_exists()` is False (file gone), so it falls into
`_maybe_adopt_draft`, which ONLY checks `agent_contract_handoff_exists` (any
row, nascent or evidence-bearing) -- not whether the row is freshly-born or
already carries real evidence. If it fabricates `_initial_envelope` here, the
next write both persists a blank draft AND mirrors that blank-plus-one-field
envelope back onto the row, permanently destroying the real evidence -- same
defect family the `view` fix closed, through the write door instead of the
read door.

Style mirrors `test_cli_view_recovers_cut_row.py`: in-process via the
registered argparse subcommands, `capsys` for stdout, isolated `GAIA_DATA_DIR`
per test, real schema materialized through `gaia.store.writer`.
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "bin"))

from cli import contract as contract_cli  # noqa: E402

from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402


def _parser():
    parser = argparse.ArgumentParser()
    contract_cli.register(parser.add_subparsers(dest="command"))
    return parser


def _seed(db_path, **overrides):
    """Insert one agent_contract_handoffs row directly and return its id.

    Same shape/convention as `test_cli_view_recovers_cut_row.py`'s helper.
    """
    from gaia.store import writer as store_writer

    row = {
        "contract_id": "c-1",
        "agent_id": "a" + "0" * 16,
        "session_id": "s-1",
        "workspace": "me",
        "agent_state": "DISPATCHED",
        "raw_handoff_json": "{}",
        "created_at": "2026-07-20T10:00:00Z",
        "kind": "task_execution",
        "cut_reason": "never_finalized",
        "harness_agent_id": None,
    }
    row.update(overrides)

    con = store_writer._connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (row["workspace"],)
        )
        con.execute(
            "INSERT INTO agent_contract_handoffs "
            "(contract_id, agent_id, session_id, workspace, agent_state, "
            " raw_handoff_json, created_at, kind, cut_reason, harness_agent_id) "
            "VALUES (:contract_id, :agent_id, :session_id, :workspace, :agent_state, "
            " :raw_handoff_json, :created_at, :kind, :cut_reason, :harness_agent_id)",
            row,
        )
        con.commit()
    finally:
        con.close()


def _raw_handoff_json(db_path, contract_id):
    from gaia.store import writer as store_writer

    con = store_writer._connect(db_path)
    try:
        row = con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return row["raw_handoff_json"] if row is not None else None
    finally:
        con.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return data_dir / "gaia.db"


def _draft_file_path(db_path, draft_id):
    return db_path.parent / "contract_drafts" / f"{draft_id}.json"


def _run(argv, capsys):
    args = _parser().parse_args(argv)
    code = args.func(args)
    out, err = capsys.readouterr()
    return code, out, err


# A real, non-empty accumulated envelope -- the shape
# `mirror_partial_contract_handoff` leaves on the row for a turn that ran
# `set`/`add`/`fill` before its draft FILE was subsequently lost.
_REAL_EVIDENCE_ENVELOPE = {
    "agent_status": {
        "agent_state": "IN_PROGRESS",
        "agent_id": None,  # filled per-test with the real agent_id
        "pending_steps": ["finish closing the pendientes"],
        "next_action": "keep going",
    },
    "evidence_report": {
        "patterns_checked": ["security-tiers"],
        "files_checked": ["bin/cli/contract.py"],
        "commands_run": ["gaia contract add ... --draft-id x"],
        "key_outputs": ["mid-turn evidence mirrored before the draft file vanished"],
        "verbatim_outputs": [],
        "cross_layer_impacts": [],
        "open_gaps": [],
    },
    "consolidation_report": None,
    "approval_request": None,
    "failure_report": None,
    "memory_delta": None,
    "work_phase": "investigating",
    "born_at_dispatch": True,
}


class TestSetAddFillDoNotDiscardMirroredEvidenceWhenDraftFileIsLost:
    def test_add_after_draft_file_loss_does_not_blank_the_row(self, db_path, capsys):
        agent_id = valid_agent_id("set-add-fill-lost-file")
        contract_id = f"{agent_id}.midturn"
        envelope = json.loads(json.dumps(_REAL_EVIDENCE_ENVELOPE))
        envelope["agent_status"]["agent_id"] = agent_id
        _seed(
            db_path,
            contract_id=contract_id,
            agent_id=agent_id,
            raw_handoff_json=json.dumps(envelope),
        )
        # The draft FILE is lost mid-turn: never written in this test at all
        # (equivalent to "was written, then disappeared" for _draft_exists'
        # purposes -- it only tests presence, not history).
        assert not _draft_file_path(db_path, contract_id).exists()

        code, out, err = _run(
            [
                "contract",
                "add",
                "evidence_report.files_checked",
                "gaia/store/writer.py",
                "--draft-id",
                contract_id,
            ],
            capsys,
        )
        assert code == 0, f"stderr={err!r}"

        # The pre-existing real evidence must still be there, not replaced by
        # a blank envelope that only carries the newly-added field.
        view_code, view_out, view_err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert view_code == 0, f"stderr={view_err!r}"
        envelope_after = json.loads(view_out)["envelope"]
        assert envelope_after["evidence_report"]["key_outputs"] == [
            "mid-turn evidence mirrored before the draft file vanished"
        ], (
            "real evidence_report.key_outputs was discarded by a re-fabricated "
            "blank envelope -- pendiente 1 reproduced"
        )
        assert envelope_after["agent_status"]["pending_steps"] == [
            "finish closing the pendientes"
        ]
        assert "gaia/store/writer.py" in envelope_after["evidence_report"]["files_checked"]
        assert "bin/cli/contract.py" in envelope_after["evidence_report"]["files_checked"]

        # And the SAME must hold for the DB row's raw_handoff_json, since
        # set/add/fill mirror onto it.
        raw = _raw_handoff_json(db_path, contract_id)
        row_envelope = json.loads(raw)
        assert row_envelope["evidence_report"]["key_outputs"] == [
            "mid-turn evidence mirrored before the draft file vanished"
        ], (
            "the DB row's raw_handoff_json was blanked by the re-adoption "
            "path -- pendiente 1 reproduced at the mirror layer"
        )

        # The recovered evidence is also what got persisted to disk, not just
        # what the CLI happens to report back.
        assert _draft_file_path(db_path, contract_id).exists()
        disk_envelope = json.loads(
            _draft_file_path(db_path, contract_id).read_text()
        )
        assert disk_envelope["evidence_report"]["key_outputs"] == [
            "mid-turn evidence mirrored before the draft file vanished"
        ]

    def test_set_after_draft_file_loss_also_preserves_evidence(self, db_path, capsys):
        """The same recovery applies to `set`, not only `add`."""
        agent_id = valid_agent_id("set-lost-file")
        contract_id = f"{agent_id}.midturn-set"
        envelope = json.loads(json.dumps(_REAL_EVIDENCE_ENVELOPE))
        envelope["agent_status"]["agent_id"] = agent_id
        _seed(
            db_path,
            contract_id=contract_id,
            agent_id=agent_id,
            raw_handoff_json=json.dumps(envelope),
        )

        code, _out, err = _run(
            [
                "contract",
                "set",
                "agent_status.next_action",
                "continue after recovery",
                "--draft-id",
                contract_id,
            ],
            capsys,
        )
        assert code == 0, f"stderr={err!r}"

        view_code, view_out, view_err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert view_code == 0, f"stderr={view_err!r}"
        envelope_after = json.loads(view_out)["envelope"]
        assert envelope_after["evidence_report"]["key_outputs"] == [
            "mid-turn evidence mirrored before the draft file vanished"
        ]
        assert envelope_after["agent_status"]["next_action"] == (
            "continue after recovery"
        )

    def test_fill_after_draft_file_loss_also_preserves_evidence(self, db_path, capsys):
        """The same recovery applies to `fill`, not only `add`/`set`."""
        agent_id = valid_agent_id("fill-lost-file")
        contract_id = f"{agent_id}.midturn-fill"
        envelope = json.loads(json.dumps(_REAL_EVIDENCE_ENVELOPE))
        envelope["agent_status"]["agent_id"] = agent_id
        _seed(
            db_path,
            contract_id=contract_id,
            agent_id=agent_id,
            raw_handoff_json=json.dumps(envelope),
        )

        code, _out, err = _run(
            [
                "contract",
                "fill",
                "--draft-id",
                contract_id,
                "--json",
                json.dumps({"evidence_report": {"open_gaps": ["one new gap"]}}),
            ],
            capsys,
        )
        assert code == 0, f"stderr={err!r}"

        view_code, view_out, view_err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert view_code == 0, f"stderr={view_err!r}"
        envelope_after = json.loads(view_out)["envelope"]
        assert envelope_after["evidence_report"]["key_outputs"] == [
            "mid-turn evidence mirrored before the draft file vanished"
        ]
        assert envelope_after["evidence_report"]["open_gaps"] == ["one new gap"]


class TestFreshBirthOnlyRowStillAdoptsBlank:
    """The legitimate consumer of `_maybe_adopt_draft`, held unchanged: a row
    that carries only the birth marker (no `set`/`add`/`fill` ever ran yet)
    must still adopt the blank starting envelope -- this pins the same case
    `test_cli_view_recovers_cut_row.py::TestSetStillAdoptsItsOwnFreshlyBornRow`
    already covers via `add`, here also through `set` and `fill`."""

    def test_set_on_a_freshly_born_row_adopts_the_blank_envelope(
        self, db_path, capsys
    ):
        agent_id = valid_agent_id("fresh-birth-set")
        contract_id = f"{agent_id}.freshtoken-set"
        _seed(
            db_path,
            contract_id=contract_id,
            agent_id=agent_id,
            agent_state="DISPATCHED",
            raw_handoff_json=json.dumps(
                {"agent_state": "DISPATCHED", "born_at_dispatch": True}
            ),
        )
        assert not _draft_file_path(db_path, contract_id).exists()

        code, _out, err = _run(
            [
                "contract",
                "set",
                "agent_status.next_action",
                "starting",
                "--draft-id",
                contract_id,
            ],
            capsys,
        )
        assert code == 0, f"stderr={err!r}"

        view_code, view_out, view_err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert view_code == 0, f"stderr={view_err!r}"
        envelope_after = json.loads(view_out)["envelope"]
        assert envelope_after["evidence_report"]["files_checked"] == []
        assert envelope_after["agent_status"]["next_action"] == "starting"
