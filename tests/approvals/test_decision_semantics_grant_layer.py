"""Decision semantics observed on the GRANT: once, reject, always, provenance.

Every claim here is read off ``approval_grants`` after the reply has been
applied, never off the decision object: a decision that maps a reply string to
the right enum and still produces the wrong grant is the failure this module
exists to catch. The affirmative claims (once, always) are driven by a reply the
real ``opencode/plugin.ts`` normalized from a real ``permission.replied`` event,
so nothing here asserts over a shape production never emits. The negatives
(reject, a reply outside the vocabulary, a decision with no presentation) are
synthetic on purpose -- an arbitrary caller really can emit those.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT / "hooks", _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cli.approvals import cmd_opencode_decide, cmd_opencode_present, register  # noqa: E402

from gaia.approvals import store  # noqa: E402
from gaia.approvals.command_set import command_fingerprint, request_fingerprint  # noqa: E402
from gaia.store import writer  # noqa: E402

_PLUGIN = _REPO_ROOT / "opencode" / "plugin.ts"
_SESSION = "ses-1"
_AGENT = "agent-1"
_CALL = "call-1"
_TOKEN = "presentation-token"
_COMMAND = "git status --short"
_OTHER_COMMAND = "git status --porcelain"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _reply_from_the_real_plugin(raw_reply: str) -> tuple[str, str]:
    """Normalize a real permission event through the real plugin edge.

    Deliberately unguarded: an absent bun must fail this test rather than let
    an affirmative capability claim pass on a reply the test wrote itself.
    """
    event = {
        "type": "permission.replied",
        "properties": {"requestID": "req-1", "reply": raw_reply},
    }
    script = (
        "import { normalizePermissionReply, permissionDecisionLane } from "
        f"{json.dumps(str(_PLUGIN))};"
        f"const event = {json.dumps(event)};"
        "console.log(JSON.stringify({"
        " reply: normalizePermissionReply(event.properties.reply),"
        " lane: permissionDecisionLane(event.type)}));"
    )
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)
    return observed["reply"], observed["lane"]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command"))
    return parser.parse_args(argv)


def _present_args(approval_id: str, *, token: str = _TOKEN) -> argparse.Namespace:
    return _parse([
        "approvals", "opencode-present", approval_id,
        "--session-id", _SESSION, "--call-id", _CALL, "--token", token, "--json",
    ])


def _decide_args(
    approval_id: str, *, reply: str, lane: str, token: str = _TOKEN
) -> argparse.Namespace:
    return _parse([
        "approvals", "opencode-decide", approval_id,
        "--session-id", _SESSION, "--call-id", _CALL, "--token", token,
        "--reply", reply, "--decision-lane", lane, "--json",
    ])


def _seed_presented_command_set(command: str = _COMMAND) -> str:
    items = [{"command": command, "fingerprint": command_fingerprint(command), "rationale": ""}]
    payload = {
        "request_type": "COMMAND_SET",
        "command_set": items,
        "request_fingerprint": request_fingerprint([command]),
        "scope": "COMMAND_SET",
        "operation": "inspect",
        "exact_content": command,
    }
    approval_id = store.insert_requested(payload, agent_id=_AGENT, session_id=_SESSION)
    assert cmd_opencode_present(_present_args(approval_id)) == 0
    return approval_id


def _grant_shape(db_path: Path, approval_id: str) -> dict | None:
    """The grant a decision produced, or None when it produced none."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT scope, status, next_index, consumed_indexes_json, command_set_json "
            "FROM approval_grants WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {
        "scope": row["scope"],
        "status": row["status"],
        "next_index": int(row["next_index"] or 0),
        "consumed": json.loads(row["consumed_indexes_json"] or "[]"),
        "consumable": len(json.loads(row["command_set_json"])) - int(row["next_index"] or 0),
    }


def _grant_count(db_path: Path) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM approval_grants").fetchone()[0]
    finally:
        con.close()


def _approval_status(db_path: Path, approval_id: str) -> str:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0]
    finally:
        con.close()


def test_a_once_reply_grants_one_index_and_the_grant_refuses_the_second_attempt(isolated_db):
    approval_id = _seed_presented_command_set()
    reply, lane = _reply_from_the_real_plugin("once")
    assert (reply, lane) == ("once", "preferred")

    assert cmd_opencode_decide(_decide_args(approval_id, reply=reply, lane=lane)) == 0

    assert _grant_shape(isolated_db, approval_id) == {
        "scope": "COMMAND_SET", "status": "PENDING", "next_index": 0,
        "consumed": [], "consumable": 1,
    }

    assert writer.reserve_plan_command(
        _COMMAND, session_id=_SESSION, tool_use_id="tool-1", db_path=isolated_db
    ) == {"approval_id": approval_id, "index": 0}
    assert writer.settle_plan_command(
        approval_id, session_id=_SESSION, tool_use_id="tool-1", success=True, db_path=isolated_db
    ) is True

    settled = _grant_shape(isolated_db, approval_id)
    assert settled == {
        "scope": "COMMAND_SET", "status": "CONSUMED", "next_index": 1,
        "consumed": [0], "consumable": 0,
    }

    # The refusal is observed on the grant, not inferred from the decision: the
    # second attempt reserves nothing and leaves the settled state untouched.
    assert writer.reserve_plan_command(
        _COMMAND, session_id=_SESSION, tool_use_id="tool-2", db_path=isolated_db
    ) is None
    assert _grant_shape(isolated_db, approval_id) == settled


def test_a_reject_reply_leaves_no_grant_and_no_reservable_index(isolated_db):
    approval_id = _seed_presented_command_set()

    assert cmd_opencode_decide(_decide_args(approval_id, reply="reject", lane="preferred")) == 0

    assert _approval_status(isolated_db, approval_id) == "rejected"
    assert _grant_shape(isolated_db, approval_id) is None
    assert _grant_count(isolated_db) == 0
    assert writer.reserve_plan_command(
        _COMMAND, session_id=_SESSION, tool_use_id="tool-1", db_path=isolated_db
    ) is None


def test_an_always_reply_never_produces_the_grant_shape_a_once_reply_produces(
    isolated_db, capsys
):
    once_approval = _seed_presented_command_set(_COMMAND)
    once_reply, once_lane = _reply_from_the_real_plugin("once")
    assert cmd_opencode_decide(
        _decide_args(once_approval, reply=once_reply, lane=once_lane)
    ) == 0
    once_shape = _grant_shape(isolated_db, once_approval)
    capsys.readouterr()

    always_reply, always_lane = _reply_from_the_real_plugin("always")
    assert always_reply == "always"
    always_approval = _seed_presented_command_set(_OTHER_COMMAND)

    assert cmd_opencode_decide(
        _decide_args(always_approval, reply=always_reply, lane=always_lane)
    ) == 1
    assert "always" in capsys.readouterr().out

    always_shape = _grant_shape(isolated_db, always_approval)
    # Equality of the two shapes IS the silent-conversion defect, so the
    # distinguishability is asserted rather than left to the reply spelling.
    assert once_shape == {
        "scope": "COMMAND_SET", "status": "PENDING", "next_index": 0,
        "consumed": [], "consumable": 1,
    }
    assert always_shape is None
    assert always_shape != once_shape
    assert _approval_status(isolated_db, always_approval) == "pending"
    assert _grant_count(isolated_db) == 1
    assert writer.reserve_plan_command(
        _OTHER_COMMAND, session_id=_SESSION, tool_use_id="tool-1", db_path=isolated_db
    ) is None


def test_a_reply_outside_the_neutral_vocabulary_grants_nothing(isolated_db, capsys):
    approval_id = _seed_presented_command_set()

    # The structured surface refuses the spelling outright, so only a caller
    # bypassing it can hand this reply to the handler.
    with pytest.raises(SystemExit):
        _decide_args(approval_id, reply="allow", lane="preferred")

    args = _decide_args(approval_id, reply="once", lane="preferred")
    args.reply = "allow"
    assert cmd_opencode_decide(args) == 1
    assert "unrecognized consent reply" in capsys.readouterr().out

    assert _grant_shape(isolated_db, approval_id) is None
    assert _grant_count(isolated_db) == 0
    assert _approval_status(isolated_db, approval_id) == "pending"


def test_a_decision_without_a_recorded_presentation_grants_nothing(isolated_db, capsys):
    approval_id = _seed_presented_command_set()
    reply, lane = _reply_from_the_real_plugin("once")

    # Single-field control: this call differs from the granting one below in the
    # token alone, so the refusal is attributable to the missing presentation
    # provenance and to nothing else the call carries.
    assert cmd_opencode_decide(
        _decide_args(approval_id, reply=reply, lane=lane, token="never-presented")
    ) == 1
    assert "No matching OpenCode permission presentation exists" in capsys.readouterr().out
    assert _grant_shape(isolated_db, approval_id) is None
    assert _grant_count(isolated_db) == 0

    assert cmd_opencode_decide(_decide_args(approval_id, reply=reply, lane=lane)) == 0
    assert _grant_shape(isolated_db, approval_id)["consumable"] == 1
