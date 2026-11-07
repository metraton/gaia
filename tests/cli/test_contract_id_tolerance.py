"""`gaia contract view --contract-id/--draft-id` opens a row by any id the row carries."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

AGENT_ID = "ad155d4257c07d350"
CONTRACT_ID = f"{AGENT_ID}.0123456789ab"
RESUME_ID = "af21c6d2c265bf715"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    _connect(db_path()).close()
    return db_path()


def _insert(tmp_db, *, agent_id=AGENT_ID, contract_id=CONTRACT_ID, harness_id=RESUME_ID,
            session_id="sess-1", prompt="the goal", continues=None,
            created="2026-10-28T10:00:00Z") -> int:
    con = sqlite3.connect(str(tmp_db))
    try:
        cur = con.execute(
            "INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, "
            "raw_handoff_json, contract_id, dispatch_prompt, harness_agent_id, session_id, "
            "continues_handoff_id, created_at) "
            "VALUES (?, 'me', 'COMPLETE', ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, json.dumps({"conclusion": "row conclusion"}), contract_id, prompt,
             harness_id, session_id, continues, created),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _run(capsys, *argv) -> tuple[int, str, str]:
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", "view", *argv])
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize("form", ["agent_id", "handoff_id", "harness_id"])
def test_row_opens_by_every_id_form_and_says_which_row(tmp_db, capsys, form):
    handoff = _insert(tmp_db)
    value = {"agent_id": AGENT_ID, "handoff_id": str(handoff), "harness_id": RESUME_ID}[form]

    rc, out, err = _run(capsys, "--row", "--contract-id", value)

    assert rc == 0, err
    row = json.loads(out)
    assert (row["contract_id"], row["harness_agent_id"], row["goal"]) == (
        CONTRACT_ID, RESUME_ID, "the goal")
    assert CONTRACT_ID in err and RESUME_ID in err and "contract init" not in err


@pytest.mark.parametrize("form", ["agent_id", "handoff_id"])
def test_envelope_opens_by_every_id_form_and_says_which_row(tmp_db, capsys, form):
    handoff = _insert(tmp_db)
    value = AGENT_ID if form == "agent_id" else str(handoff)

    rc, out, err = _run(capsys, "--contract-id", value)

    assert rc == 0, err
    payload = json.loads(out)
    assert payload["draft_id"] == CONTRACT_ID
    assert payload["envelope"]["conclusion"] == "row conclusion"
    assert CONTRACT_ID in payload["opened_from"] and RESUME_ID in payload["opened_from"]
    assert CONTRACT_ID in err and RESUME_ID in err


def test_field_survives_an_alternate_id_form(tmp_db, capsys):
    _insert(tmp_db)

    rc, out, err = _run(capsys, "--draft-id", AGENT_ID, "--field", "conclusion")

    assert rc == 0, err
    assert json.loads(out) == "row conclusion"


def test_exact_contract_id_is_unchanged_and_announces_nothing(tmp_db, capsys):
    _insert(tmp_db)

    rc, out, err = _run(capsys, "--row", "--contract-id", CONTRACT_ID)

    assert rc == 0, err
    assert "opened_from" not in json.loads(out)
    assert err == ""


def test_contract_id_without_a_dot_still_opens_as_a_contract_id(tmp_db, capsys):
    _insert(tmp_db, contract_id="legacy-contract", harness_id="a7777777777777777")

    rc, out, err = _run(capsys, "--row", "--contract-id", "legacy-contract")

    assert rc == 0, err
    assert "opened_from" not in json.loads(out)
    assert err == ""


def test_unknown_id_names_what_it_is_not_and_does_not_advise_init(tmp_db, capsys):
    _insert(tmp_db)

    rc, _, err = _run(capsys, "--contract-id", "a9999999999999999")

    assert rc == 1
    assert "opens nothing" in err and "contract init" not in err


def test_session_id_is_named_by_the_other_identifier_hint(tmp_db, capsys):
    _insert(tmp_db)

    rc, _, err = _run(capsys, "--row", "--contract-id", "sess-1")

    assert rc == 1
    assert "session_id" in err and RESUME_ID in err


def test_unknown_numeric_id_opens_nothing(tmp_db, capsys):
    _insert(tmp_db)

    rc, _, err = _run(capsys, "--row", "--contract-id", "99999")

    assert rc == 1
    assert "opens nothing" in err


def test_row_without_a_contract_id_opens_through_its_harness_id(tmp_db, capsys):
    handoff = _insert(tmp_db, contract_id=None)

    rc, out, err = _run(capsys, "--row", "--contract-id", str(handoff))

    assert rc == 0, err
    row = json.loads(out)
    assert (row["handoff_id"], row["harness_agent_id"]) == (handoff, RESUME_ID)
    assert str(handoff) in err and RESUME_ID in err


def _chain(tmp_db) -> list:
    links = []
    for suffix, hour in (("aaaaaaaaaaaa", "10"), ("bbbbbbbbbbbb", "11"), ("cccccccccccc", "12")):
        links.append(_insert(
            tmp_db, contract_id=f"{AGENT_ID}.{suffix}", continues=links[-1] if links else None,
            created=f"2026-10-28T{hour}:00:00Z"))
    return links


@pytest.mark.parametrize("mode", [["--row"], []])
@pytest.mark.parametrize("by_harness_id", [False, True])
def test_chain_opens_the_latest_link_and_lists_every_link(tmp_db, capsys, mode, by_harness_id):
    _chain(tmp_db)
    value = RESUME_ID if by_harness_id else AGENT_ID

    rc, out, err = _run(capsys, *mode, "--contract-id", value)

    assert rc == 0, err
    for suffix in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        assert f"{AGENT_ID}.{suffix}" in out + err
    assert f"{AGENT_ID}.cccccccccccc (opened)" in err
    assert f"{AGENT_ID}.aaaaaaaaaaaa (opened)" not in err


def test_chain_opened_by_its_latest_handoff_id_reports_the_same_links(tmp_db, capsys):
    latest = _chain(tmp_db)[-1]

    rc, out, err = _run(capsys, "--row", "--contract-id", str(latest))

    assert rc == 0, err
    assert f"{AGENT_ID}.cccccccccccc" in out
    assert f"{AGENT_ID}.aaaaaaaaaaaa" in err and RESUME_ID in err


def test_unknown_dotted_contract_id_keeps_the_draft_lane_message(tmp_db, capsys):
    rc, _, err = _run(capsys, "--contract-id", "a1111111111111111.zzz")

    assert rc == 1
    assert "No draft found" in err
