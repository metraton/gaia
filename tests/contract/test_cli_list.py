#!/usr/bin/env python3
"""Tests for `gaia contract list` -- the read-only view over persisted handoffs.

`view` reads a DRAFT on disk; before this verb nothing in the CLI exposed the
finalized ``agent_contract_handoffs`` rows, so recovering a ``handoff_id``
required calling the internal writer API through an interpreter. These tests
pin the filters and the T0 (SELECT-only) contract.
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
    """Insert one agent_contract_handoffs row directly and return its id."""
    from gaia.store import writer as store_writer

    row = {
        "contract_id": "c-1",
        "agent_id": "a" + "0" * 16,
        "session_id": "s-1",
        "workspace": "me",
        "agent_state": "COMPLETE",
        "raw_handoff_json": "{}",
        "created_at": "2026-07-20T10:00:00Z",
        "kind": "task_execution",
        "cut_reason": None,
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
            " raw_handoff_json, created_at, kind, cut_reason) "
            "VALUES (:contract_id, :agent_id, :session_id, :workspace, :agent_state, "
            " :raw_handoff_json, :created_at, :kind, :cut_reason)",
            row,
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _seed_born(db_path, agent_name, **overrides):
    """Seed a row the way a DISPATCH births one: name inside the birth envelope.

    Mirrors ``insert_dispatched_handoff`` -- ``agent_id`` holds the minted
    handle and the readable name lives under ``BIRTH_AGENT_NAME_KEY`` in
    ``raw_handoff_json`` -- so the tests exercise the real recorded shape rather
    than a convenient one.
    """
    from gaia.store.writer import BIRTH_AGENT_NAME_KEY

    envelope = {"agent_state": "DISPATCHED", "born_at_dispatch": True}
    if agent_name is not None:
        envelope[BIRTH_AGENT_NAME_KEY] = agent_name
    defaults = {
        "agent_state": "DISPATCHED",
        "cut_reason": "never_finalized",
        "raw_handoff_json": json.dumps(envelope),
    }
    defaults.update(overrides)
    return _seed(db_path, **defaults)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    return data_dir / "gaia.db"


def _run(argv, capsys):
    args = _parser().parse_args(argv)
    code = args.func(args)
    return code, capsys.readouterr().out


class TestListVerbExists:
    def test_list_is_a_registered_subcommand(self):
        args = _parser().parse_args(["contract", "list"])

        assert args.func is contract_cli.cmd_list


class TestListReadsPersistedRows:
    def test_lists_a_finalized_row_with_its_handoff_id(self, db_path, capsys):
        """The gap this closes: recovering a handoff_id without the internal API."""
        handoff_id = _seed(db_path, contract_id="c-listed", agent_id="a" + "1" * 16)

        code, out = _run(["contract", "list", "--json"], capsys)
        payload = json.loads(out)

        assert code == 0
        assert payload["count"] == 1
        assert payload["handoffs"][0]["id"] == handoff_id
        assert payload["handoffs"][0]["contract_id"] == "c-listed"

    def test_table_output_renders_the_row(self, db_path, capsys):
        _seed(db_path, agent_id="a" + "2" * 16)

        code, out = _run(["contract", "list"], capsys)

        assert code == 0
        assert "agent_state" in out
        assert "a" + "2" * 16 in out
        assert "1 handoff(s)." in out

    def test_empty_result_is_a_clean_exit_not_an_error(self, db_path, capsys):
        code, out = _run(["contract", "list"], capsys)

        assert code == 0
        assert "No handoffs matched." in out


class TestListFilters:
    def test_filter_by_agent(self, db_path, capsys):
        _seed(db_path, contract_id="c-a", agent_id="a" + "a" * 16)
        _seed(db_path, contract_id="c-b", agent_id="a" + "b" * 16)

        _, out = _run(
            ["contract", "list", "--agent", "a" + "a" * 16, "--json"], capsys
        )
        payload = json.loads(out)

        assert payload["count"] == 1
        assert payload["handoffs"][0]["contract_id"] == "c-a"

    def test_agent_id_spelling_is_supported_and_literal(self, db_path, capsys):
        minted = "a" + "c" * 16
        _seed(db_path, contract_id="c-agent-id", agent_id=minted)

        _, out = _run(
            ["contract", "list", "--agent-id", minted, "--json"], capsys
        )

        assert json.loads(out)["handoffs"][0]["contract_id"] == "c-agent-id"

    def test_filter_by_state(self, db_path, capsys):
        _seed(db_path, contract_id="c-done", agent_state="COMPLETE")
        _seed(db_path, contract_id="c-open", agent_state="DISPATCHED")

        _, out = _run(["contract", "list", "--state", "DISPATCHED", "--json"], capsys)
        payload = json.loads(out)

        assert payload["count"] == 1
        assert payload["handoffs"][0]["contract_id"] == "c-open"

    def test_filter_by_session(self, db_path, capsys):
        _seed(db_path, contract_id="c-s1", session_id="s-one")
        _seed(db_path, contract_id="c-s2", session_id="s-two")

        _, out = _run(["contract", "list", "--session", "s-two", "--json"], capsys)
        payload = json.loads(out)

        assert payload["count"] == 1
        assert payload["handoffs"][0]["contract_id"] == "c-s2"

    def test_filter_by_date_range(self, db_path, capsys):
        _seed(db_path, contract_id="c-old", created_at="2026-07-01T09:00:00Z")
        _seed(db_path, contract_id="c-new", created_at="2026-07-26T09:00:00Z")

        _, out = _run(
            ["contract", "list", "--since", "2026-07-20", "--json"], capsys
        )
        payload = json.loads(out)

        assert [r["contract_id"] for r in payload["handoffs"]] == ["c-new"]

    def test_until_is_inclusive_of_a_bare_date(self, db_path, capsys):
        """A bare --until date must include the whole of that day."""
        _seed(db_path, contract_id="c-same-day", created_at="2026-07-26T23:59:00Z")

        _, out = _run(
            ["contract", "list", "--until", "2026-07-26", "--json"], capsys
        )
        payload = json.loads(out)

        assert [r["contract_id"] for r in payload["handoffs"]] == ["c-same-day"]

    def test_limit_caps_the_result(self, db_path, capsys):
        for i in range(3):
            _seed(db_path, contract_id=f"c-{i}", created_at=f"2026-07-2{i}T10:00:00Z")

        _, out = _run(["contract", "list", "--limit", "2", "--json"], capsys)

        assert json.loads(out)["count"] == 2


class TestCutReasonIsVisibleWithoutOpeningTheRow:
    """The question a stuck row must answer from the list alone: why it is stuck.

    Before this, ``cut_reason`` was reachable only through ``contract view``, so
    triaging N cut turns meant N round trips.
    """

    def test_cut_reason_is_a_table_column(self, db_path, capsys):
        _seed(db_path, contract_id="c-cut", cut_reason="reaped")

        code, out = _run(["contract", "list"], capsys)

        assert code == 0
        assert "cut_reason" in out
        assert "reaped" in out

    def test_a_cleanly_closed_row_shows_no_reason(self, db_path, capsys):
        _seed(db_path, contract_id="c-clean", cut_reason=None)

        _, out = _run(["contract", "list"], capsys)

        assert "never_finalized" not in out
        assert "reaped" not in out

    def test_cut_reason_is_present_in_json(self, db_path, capsys):
        _seed(db_path, contract_id="c-json-cut", cut_reason="salvaged_truncation")

        _, out = _run(["contract", "list", "--json"], capsys)

        assert json.loads(out)["handoffs"][0]["cut_reason"] == "salvaged_truncation"


class TestCutFilter:
    def test_bare_cut_selects_every_uncleanly_closed_turn(self, db_path, capsys):
        _seed(db_path, contract_id="c-clean", cut_reason=None)
        _seed(db_path, contract_id="c-never", cut_reason="never_finalized")
        _seed(db_path, contract_id="c-reaped", cut_reason="reaped")

        _, out = _run(["contract", "list", "--cut", "--json"], capsys)
        payload = json.loads(out)

        assert payload["count"] == 2
        assert {r["contract_id"] for r in payload["handoffs"]} == {
            "c-never",
            "c-reaped",
        }

    def test_cut_with_a_reason_selects_only_that_reason(self, db_path, capsys):
        _seed(db_path, contract_id="c-never", cut_reason="never_finalized")
        _seed(db_path, contract_id="c-backstop", cut_reason="backstop_capture")

        _, out = _run(
            ["contract", "list", "--cut", "backstop_capture", "--json"], capsys
        )
        payload = json.loads(out)

        assert [r["contract_id"] for r in payload["handoffs"]] == ["c-backstop"]

    def test_without_cut_every_row_is_listed(self, db_path, capsys):
        _seed(db_path, contract_id="c-clean", cut_reason=None)
        _seed(db_path, contract_id="c-reaped", cut_reason="reaped")

        _, out = _run(["contract", "list", "--json"], capsys)

        assert json.loads(out)["count"] == 2

    def test_cut_filter_is_applied_before_the_limit(self, db_path, capsys):
        """A minority filter must not be starved by rows the limit already ate."""
        for i in range(5):
            _seed(
                db_path,
                contract_id=f"c-clean-{i}",
                cut_reason=None,
                created_at=f"2026-07-2{i}T10:00:00Z",
            )
        _seed(
            db_path,
            contract_id="c-the-cut-one",
            cut_reason="reaped",
            created_at="2026-07-01T10:00:00Z",
        )

        _, out = _run(["contract", "list", "--cut", "--limit", "3", "--json"], capsys)
        payload = json.loads(out)

        assert [r["contract_id"] for r in payload["handoffs"]] == ["c-the-cut-one"]

    def test_cut_combines_with_state(self, db_path, capsys):
        _seed(
            db_path,
            contract_id="c-open-cut",
            agent_state="DISPATCHED",
            cut_reason="never_finalized",
        )
        _seed(
            db_path,
            contract_id="c-done-cut",
            agent_state="COMPLETE",
            cut_reason="reaped",
        )

        _, out = _run(
            ["contract", "list", "--cut", "--state", "DISPATCHED", "--json"], capsys
        )
        payload = json.loads(out)

        assert [r["contract_id"] for r in payload["handoffs"]] == ["c-open-cut"]


class TestAgentNameColumn:
    """Mapping a stuck row to the specialist it belonged to.

    The name is projected from the birth envelope and from nowhere else, so the
    column is honestly EMPTY wherever the dispatch never recorded one.
    """

    def test_a_born_row_shows_the_dispatched_agent_name(self, db_path, capsys):
        _seed_born(db_path, "gaia-system", contract_id="c-born")

        code, out = _run(["contract", "list"], capsys)

        assert code == 0
        assert "agent_name" in out
        assert "gaia-system" in out

    def test_the_name_is_also_in_json(self, db_path, capsys):
        _seed_born(db_path, "cloud-troubleshooter", contract_id="c-born-json")

        _, out = _run(["contract", "list", "--json"], capsys)

        assert json.loads(out)["handoffs"][0]["agent_name"] == "cloud-troubleshooter"

    def test_a_row_with_no_recorded_name_is_empty_not_guessed(self, db_path, capsys):
        """A legacy row carrying an agent NAME in agent_id is NOT read as a name.

        Deciding by shape which handles are names would also promote fixture
        handles; an empty cell is the honest answer.
        """
        _seed(db_path, contract_id="c-legacy", agent_id="cloud-troubleshooter")

        _, out = _run(["contract", "list", "--json"], capsys)

        assert json.loads(out)["handoffs"][0]["agent_name"] is None

    def test_a_finalized_envelope_yields_no_name(self, db_path, capsys):
        """finalize replaces raw_handoff_json wholesale; the marker does not survive."""
        _seed(
            db_path,
            contract_id="c-final",
            raw_handoff_json=json.dumps(
                {
                    "agent_status": {
                        "agent_state": "COMPLETE",
                        "agent_id": "a" + "f" * 16,
                    }
                }
            ),
        )

        _, out = _run(["contract", "list", "--json"], capsys)

        assert json.loads(out)["handoffs"][0]["agent_name"] is None

    def test_a_malformed_envelope_does_not_break_the_listing(self, db_path, capsys):
        _seed(db_path, contract_id="c-broken", raw_handoff_json="{not json")

        code, out = _run(["contract", "list", "--json"], capsys)

        assert code == 0
        assert json.loads(out)["handoffs"][0]["agent_name"] is None

    def test_the_stuck_triage_query_answers_who_and_why_in_one_call(
        self, db_path, capsys
    ):
        """The end-to-end shape of today's failure: four turns cut, none legible."""
        _seed_born(db_path, "gaia-system", contract_id="c-1", session_id="s-live")
        _seed_born(db_path, "gaia-planner", contract_id="c-2", session_id="s-live")
        _seed(
            db_path,
            contract_id="c-ok",
            session_id="s-live",
            agent_state="COMPLETE",
            cut_reason=None,
        )

        _, out = _run(
            ["contract", "list", "--session", "s-live", "--cut", "--json"], capsys
        )
        payload = json.loads(out)

        assert payload["count"] == 2
        assert {
            (r["agent_name"], r["cut_reason"]) for r in payload["handoffs"]
        } == {
            ("gaia-system", "never_finalized"),
            ("gaia-planner", "never_finalized"),
        }


class TestListIsReadOnly:
    def test_list_does_not_mutate_the_table(self, db_path, capsys):
        """T0 contract: the verb only SELECTs."""
        from gaia.store import writer as store_writer

        _seed(db_path, contract_id="c-ro")

        def _count():
            con = store_writer._connect(db_path)
            try:
                return con.execute(
                    "SELECT COUNT(*) AS n FROM agent_contract_handoffs"
                ).fetchone()["n"]
            finally:
                con.close()

        before = _count()
        _run(["contract", "list", "--json"], capsys)

        assert _count() == before

    def test_gaia_contract_list_classifies_as_read_only(self):
        """The whole `gaia contract` group is T0; list must not escalate it."""
        sys.path.insert(0, str(REPO_ROOT / "hooks"))
        from modules.security.mutative_verbs import detect_mutative_command

        assert detect_mutative_command("gaia contract list").is_mutative is False
