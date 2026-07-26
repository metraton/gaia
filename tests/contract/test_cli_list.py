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
            " raw_handoff_json, created_at, kind) "
            "VALUES (:contract_id, :agent_id, :session_id, :workspace, :agent_state, "
            " :raw_handoff_json, :created_at, :kind)",
            row,
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


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
