"""`gaia contract view` must never write, and must recover real evidence.

Regression coverage for a confirmed defect: `cmd_view`'s `--draft-id`
addressing used to resolve through `_load_target_draft`'s DEFAULT
`allow_adopt=True`. When a historical/cut row had no on-disk draft file left
(the normal case once a session ends), that path reached `_maybe_adopt_draft`,
which materialized a genuinely BLANK `_initial_envelope` and PERSISTED it to
`contract_drafts/<id>.json` -- a write to disk performed by a READ verb,
discarding whatever real evidence the row's own `raw_handoff_json` still
carried (from that turn's own `set`/`add`/`fill` calls before it was cut).
Every later `view` of the SAME id then loaded the poisoned blank file, never
looking at `raw_handoff_json` again -- permanent, silent damage.

These tests pin the fix: `--draft-id` addressing now recovers from
`raw_handoff_json` exactly like `--harness-id` addressing already did, never
writes a draft file in the recovery path, and reports an explicit error --
never a blank envelope -- when nothing is recoverable at all.

Style mirrors `test_cli_list.py`: in-process via the registered argparse
subcommands, `capsys` for stdout, and a `db_path` fixture that isolates
`GAIA_DATA_DIR` to a fresh temp directory per test (no real `~/.gaia/gaia.db`
is ever touched).
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


def _parser():
    parser = argparse.ArgumentParser()
    contract_cli.register(parser.add_subparsers(dest="command"))
    return parser


def _seed(db_path, **overrides):
    """Insert one agent_contract_handoffs row directly and return its id.

    Mirrors `test_cli_list.py`'s helper of the same name/shape -- duplicated
    here (not imported) to keep this file's fixtures self-contained, the same
    convention every other file under tests/contract/ already follows.
    """
    from gaia.store import writer as store_writer

    row = {
        "contract_id": "c-1",
        "agent_id": "a" + "0" * 16,
        "session_id": "s-1",
        "workspace": "me",
        "agent_state": "IN_PROGRESS",
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
        cur = con.execute(
            "INSERT INTO agent_contract_handoffs "
            "(contract_id, agent_id, session_id, workspace, agent_state, "
            " raw_handoff_json, created_at, kind, cut_reason, harness_agent_id) "
            "VALUES (:contract_id, :agent_id, :session_id, :workspace, :agent_state, "
            " :raw_handoff_json, :created_at, :kind, :cut_reason, :harness_agent_id)",
            row,
        )
        con.commit()
        return cur.lastrowid
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


# A real, non-empty accumulated envelope -- the shape `mirror_partial_contract_handoff`
# leaves behind for a turn that ran `set`/`add`/`fill` before being cut.
_REAL_EVIDENCE_ENVELOPE = {
    "agent_status": {
        "agent_state": "IN_PROGRESS",
        "agent_id": "a" + "1" * 16,
        "pending_steps": ["finish the audit"],
        "next_action": "keep going",
    },
    "evidence_report": {
        "patterns_checked": ["security-tiers"],
        "files_checked": ["bin/cli/contract.py"],
        "commands_run": ["gaia contract view --draft-id x"],
        "key_outputs": ["found the write-on-read defect"],
        "verbatim_outputs": [],
        "cross_layer_impacts": [],
        "open_gaps": ["confirm fix does not break --harness-id"],
    },
    "consolidation_report": None,
    "approval_request": None,
    "failure_report": None,
    "memory_delta": None,
    "work_phase": "investigating",
    # Birth markers a real mirrored row carries alongside the contract fields
    # (see gaia.store.writer._merge_birth_markers) -- included so the fixture
    # matches what a real cut row actually looks like, not a convenient subset.
    "born_at_dispatch": True,
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    return data_dir / "gaia.db"


def _draft_file_path(db_path, draft_id):
    return db_path.parent / "contract_drafts" / f"{draft_id}.json"


def _run(argv, capsys):
    args = _parser().parse_args(argv)
    code = args.func(args)
    out, err = capsys.readouterr()
    return code, out, err


# ---------------------------------------------------------------------------
# The core regression: --draft-id recovers REAL evidence, no draft file,
# never writes.
# ---------------------------------------------------------------------------
class TestViewRecoversRealEvidenceWithoutADraftFile:
    def test_view_returns_the_real_accumulated_evidence(self, db_path, capsys):
        contract_id = "c-cut-with-evidence"
        _seed(
            db_path,
            contract_id=contract_id,
            raw_handoff_json=json.dumps(_REAL_EVIDENCE_ENVELOPE),
        )
        assert not _draft_file_path(db_path, contract_id).exists()

        code, out, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )

        assert code == 0, f"stderr={err!r}"
        payload = json.loads(out)
        assert payload["draft_id"] == contract_id
        envelope = payload["envelope"]
        # The REAL evidence, not a blank template.
        assert envelope["evidence_report"]["files_checked"] == [
            "bin/cli/contract.py"
        ]
        assert envelope["evidence_report"]["key_outputs"] == [
            "found the write-on-read defect"
        ]
        assert envelope["agent_status"]["pending_steps"] == ["finish the audit"]
        # Recovery is labelled, distinguishing "the live draft" from
        # "reconstructed from the row" -- same labelling --harness-id uses.
        assert payload["envelope_source"] == "db_row"

    def test_view_creates_no_file_on_disk(self, db_path, capsys, tmp_path):
        """The write-on-read defect, pinned directly against the filesystem.

        Checks the DISK, not just the CLI's own report -- the whole point of
        the bug was that the write happened silently underneath a clean-
        looking read.
        """
        contract_id = "c-no-file-written"
        _seed(
            db_path,
            contract_id=contract_id,
            raw_handoff_json=json.dumps(_REAL_EVIDENCE_ENVELOPE),
        )
        drafts_dir = db_path.parent / "contract_drafts"
        before = set(drafts_dir.glob("*.json")) if drafts_dir.exists() else set()

        code, _, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert code == 0, f"stderr={err!r}"

        after = set(drafts_dir.glob("*.json")) if drafts_dir.exists() else set()
        assert after == before, (
            "contract view must never materialize a draft file, but the "
            f"drafts directory changed: before={before} after={after}"
        )
        assert not _draft_file_path(db_path, contract_id).exists()

    def test_view_does_not_alter_the_row_s_raw_handoff_json(self, db_path, capsys):
        """A read-only recovery must not touch the DB either."""
        contract_id = "c-row-untouched"
        original = json.dumps(_REAL_EVIDENCE_ENVELOPE)
        _seed(db_path, contract_id=contract_id, raw_handoff_json=original)

        code, _, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert code == 0, f"stderr={err!r}"
        assert _raw_handoff_json(db_path, contract_id) == original

    def test_repeated_views_keep_returning_the_same_real_evidence(
        self, db_path, capsys
    ):
        """Before the fix, the FIRST view poisoned the row for every later one.

        Two consecutive views of the same cut row must return identical real
        evidence -- neither call may degrade what the next one sees.
        """
        contract_id = "c-repeated-view"
        _seed(
            db_path,
            contract_id=contract_id,
            raw_handoff_json=json.dumps(_REAL_EVIDENCE_ENVELOPE),
        )

        code1, out1, _ = _run(["contract", "view", "--draft-id", contract_id], capsys)
        code2, out2, _ = _run(["contract", "view", "--draft-id", contract_id], capsys)

        assert code1 == 0 and code2 == 0
        env1 = json.loads(out1)["envelope"]
        env2 = json.loads(out2)["envelope"]
        assert env1["evidence_report"]["files_checked"] == ["bin/cli/contract.py"]
        assert env2["evidence_report"]["files_checked"] == ["bin/cli/contract.py"]


# ---------------------------------------------------------------------------
# "Nothing recoverable" must be an explicit, distinguishable outcome -- never
# a silent blank envelope indistinguishable from an honestly empty turn.
# ---------------------------------------------------------------------------
class TestNothingRecoverableIsExplicit:
    def test_row_with_unparseable_raw_json_reports_explicitly(self, db_path, capsys):
        contract_id = "c-corrupt-row"
        _seed(db_path, contract_id=contract_id, raw_handoff_json="{not json")

        code, out, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )

        assert code != 0
        assert out.strip() == ""  # never a fabricated blank envelope on stdout
        assert "nothing is recoverable" in err
        assert "NOT the same as a turn that recorded no evidence" in err

    def test_row_with_empty_raw_json_reports_explicitly(self, db_path, capsys):
        """The column is NOT NULL, so a genuinely empty value is "" -- the
        real shape a row with nothing to mirror can carry, not Python None."""
        contract_id = "c-empty-row"
        _seed(db_path, contract_id=contract_id, raw_handoff_json="")

        code, out, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )

        assert code != 0
        assert out.strip() == ""
        assert "nothing is recoverable" in err

    def test_unknown_draft_id_is_the_ordinary_no_draft_error(self, db_path, capsys):
        """No row at all is the pre-existing 'run init' error -- unchanged."""
        code, out, err = _run(
            ["contract", "view", "--draft-id", "a" + "9" * 16 + ".neverborn"], capsys
        )

        assert code != 0
        assert out.strip() == ""
        assert "No draft found" in err

    def test_a_genuinely_untouched_birth_marker_is_not_confused_with_blank(
        self, db_path, capsys
    ):
        """A row that was born but never mirrored (agent never called set/add/
        fill before being cut) recovers its honest birth marker -- distinct in
        SHAPE from the old poisoned blank envelope, which always carried a
        full (empty-lists) evidence_report. The birth marker carries no
        evidence_report key at all, so the two are never mistakable."""
        contract_id = "c-birth-only"
        birth_envelope = {"agent_state": "DISPATCHED", "born_at_dispatch": True}
        _seed(
            db_path,
            contract_id=contract_id,
            raw_handoff_json=json.dumps(birth_envelope),
        )

        code, out, err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )

        assert code == 0, f"stderr={err!r}"
        envelope = json.loads(out)["envelope"]
        assert "evidence_report" not in envelope
        assert envelope["born_at_dispatch"] is True


# ---------------------------------------------------------------------------
# --harness-id addressing must keep working exactly as before.
# ---------------------------------------------------------------------------
class TestHarnessIdAddressingUnchanged:
    def test_harness_id_recovers_the_same_real_evidence(self, db_path, capsys):
        contract_id = "c-harness-recovery"
        _seed(
            db_path,
            contract_id=contract_id,
            harness_agent_id="harness-run-42",
            raw_handoff_json=json.dumps(_REAL_EVIDENCE_ENVELOPE),
        )

        code, out, err = _run(
            ["contract", "view", "--harness-id", "harness-run-42"], capsys
        )

        assert code == 0, f"stderr={err!r}"
        payload = json.loads(out)
        assert payload["contract_id"] == contract_id
        assert payload["envelope_source"] == "db_row"
        assert payload["envelope"]["evidence_report"]["files_checked"] == [
            "bin/cli/contract.py"
        ]

    def test_harness_id_creates_no_draft_file(self, db_path, capsys):
        contract_id = "c-harness-no-file"
        _seed(
            db_path,
            contract_id=contract_id,
            harness_agent_id="harness-run-99",
            raw_handoff_json=json.dumps(_REAL_EVIDENCE_ENVELOPE),
        )

        code, _, err = _run(
            ["contract", "view", "--harness-id", "harness-run-99"], capsys
        )
        assert code == 0, f"stderr={err!r}"
        assert not _draft_file_path(db_path, contract_id).exists()

    def test_unknown_harness_id_is_a_clean_error(self, db_path, capsys):
        code, out, err = _run(
            ["contract", "view", "--harness-id", "no-such-run"], capsys
        )

        assert code != 0
        assert out.strip() == ""
        assert "no contract row carries harness_agent_id" in err


# ---------------------------------------------------------------------------
# The legitimate consumer of implicit adoption (set/add/fill) is unaffected:
# a turn's OWN first mutating call against its just-born row still adopts and
# materializes the blank starting envelope, exactly as before this fix.
# ---------------------------------------------------------------------------
class TestSetStillAdoptsItsOwnFreshlyBornRow:
    def test_set_adopts_and_writes_the_draft_file(self, db_path, capsys):
        from tests.fixtures.agent_ids import valid_agent_id

        agent_id = valid_agent_id("aabc1230")
        contract_id = f"{agent_id}.freshtoken"
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

        code, out, err = _run(
            [
                "contract",
                "add",
                "evidence_report.files_checked",
                "some/file.py",
                "--draft-id",
                contract_id,
            ],
            capsys,
        )

        assert code == 0, f"stderr={err!r}"
        assert _draft_file_path(db_path, contract_id).exists()

        view_code, view_out, view_err = _run(
            ["contract", "view", "--draft-id", contract_id], capsys
        )
        assert view_code == 0, f"stderr={view_err!r}"
        envelope = json.loads(view_out)["envelope"]
        assert envelope["evidence_report"]["files_checked"] == ["some/file.py"]
