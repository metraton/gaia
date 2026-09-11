"""Layer O: every OpenCode identity/control-plane refusal leaves one durable row.

Driven through ``bridge.handle`` -- the boundary the plugin actually spawns --
never through the adapter identity check in isolation: a hand-built context
handed to the adapter would skip the parse boundary these cases exist to
exercise. The ledger and the database are both scratch. Issuance and
resolution derive the same namespace because ``host_run_id`` reads this
process's own parent, so a token minted here is the one the adapter resolves.

Each vector runs under its own session: the ledger binds one attested role
per session, so the V3 token (gaia-orchestrator) and the V4 token
(orchestrator) cannot be issued for the same session. Scoping is therefore by
the run's session SET plus its time window -- never by a query over the whole
surface.

Six-field record (``opencode.identity.refused`` in ``harness_events``):
session_id, tool, reason (verbatim, untruncated), agent_presented,
role_context_present, attested -- plus the attestation-presence/resolution
pair that distinguishes token-absent from token-present-unresolved from
token-present-and-resolved. The token VALUE is never persisted anywhere.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.security.host_attestation import host_run_id, issue  # noqa: E402

ROLE_ISSUER = "opencode-runtime"
SESSION_V1 = "ses-refusal-audit-v1"
SESSION_V2 = "ses-refusal-audit-v2"
SESSION_V3 = "ses-refusal-audit-v3"
SESSION_V4 = "ses-refusal-audit-v4"
SESSION_ALLOW = "ses-refusal-audit-allow"
RUN_SESSIONS = frozenset(
    {SESSION_V1, SESSION_V2, SESSION_V3, SESSION_V4, SESSION_ALLOW}
)
CALL = "call-audit-1"
EVENT_TYPE = "opencode.identity.refused"

REASON_V1 = "ordinary OpenCode agents cannot issue control-plane dispatches"
REASON_V2 = (
    "OpenCode control-plane role was declared without an attested runtime context"
)
REASON_V4 = "OpenCode control-plane role is not attested by the runtime"


@pytest.fixture
def scratch(tmp_path, monkeypatch, bootstrapped_db_template):
    """Ledger and database both under this test's own scratch directory."""
    db_path = tmp_path / "gaia.db"
    shutil.copy(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DB", str(db_path))
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("GAIA_HOST", "opencode")
    return tmp_path


def _task_dispatch(session_id, **overrides):
    event = {
        "event": "tool.execute.before",
        "sessionID": session_id,
        "callID": CALL,
        "tool": "task",
        "args": {"subagent_type": "developer", "prompt": "do the thing"},
        "cwd": str(_ROOT),
    }
    event.update(overrides)
    return event


def _role_context(role, token):
    return {
        "role": role,
        "capabilities": [],
        "issuer": ROLE_ISSUER,
        "attestation": token,
        "verified": True,
    }


def _handle(event):
    import bridge

    return bridge.handle(event)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT type, source, agent, result, severity, payload, ts"
            " FROM harness_events WHERE type = ? ORDER BY id",
            (EVENT_TYPE,),
        ).fetchall()
    finally:
        con.close()


def _drive_all_four():
    """One process run: the four identity vectors through the real handler."""
    issued_v3 = issue(
        host_run=host_run_id(),
        session_id=SESSION_V3,
        role="gaia-orchestrator",
        issuer=ROLE_ISSUER,
    )
    issued_v4 = issue(
        host_run=host_run_id(),
        session_id=SESSION_V4,
        role="orchestrator",
        issuer=ROLE_ISSUER,
    )
    started = _now()
    r1 = _handle(_task_dispatch(SESSION_V1))
    r2 = _handle(_task_dispatch(SESSION_V2, agent="gaia-orchestrator"))
    r3 = _handle(
        _task_dispatch(
            SESSION_V3,
            agent="gaia-orchestrator",
            roleContext=_role_context("gaia-orchestrator", issued_v3.token),
        )
    )
    r4 = _handle(
        _task_dispatch(
            SESSION_V4,
            agent="orchestrator",
            roleContext=_role_context("orchestrator", issued_v4.token),
        )
    )
    ended = _now()
    return (r1, r2, r3, r4), (issued_v3.token, issued_v4.token), (started, ended)


def _scoped_metas(scratch, window):
    """Payloads for this run only: the run's sessions within its window."""
    started, ended = window
    metas = []
    for row in _rows(scratch / "gaia.db"):
        if not (started <= row["ts"] <= ended):
            continue
        meta = json.loads(row["payload"])
        if meta.get("session_id") in RUN_SESSIONS:
            metas.append(meta)
    return metas


class TestSeamGate:
    def test_three_denials_leave_three_rows_and_allow_leaves_none(self, scratch):
        (r1, r2, r3, r4), _, window = _drive_all_four()

        assert (r1["action"], r1["reason"]) == ("deny", REASON_V1)
        assert (r2["action"], r2["reason"]) == ("deny", REASON_V2)
        assert r3["action"] == "allow", r3
        assert (r4["action"], r4["reason"]) == ("deny", REASON_V4)

        metas = _scoped_metas(scratch, window)
        assert len(metas) == 3, (
            "expected exactly one durable row per denial and none for the"
            f" allow, got {len(metas)}"
        )
        by_reason = {meta["reason"]: meta for meta in metas}
        assert set(by_reason) == {REASON_V1, REASON_V2, REASON_V4}
        assert by_reason[REASON_V1]["reason"] == r1["reason"]
        assert by_reason[REASON_V2]["reason"] == r2["reason"]
        assert by_reason[REASON_V4]["reason"] == r4["reason"]
        for meta in metas:
            assert meta["tool"] == "task"
            assert meta["session_id"] in RUN_SESSIONS
            assert isinstance(meta["agent_presented"], bool)
            assert isinstance(meta["role_context_present"], bool)
            assert isinstance(meta["attested"], bool)
        assert by_reason[REASON_V1]["agent_presented"] is False
        assert by_reason[REASON_V1]["role_context_present"] is False
        assert by_reason[REASON_V1]["attested"] is False
        assert by_reason[REASON_V2]["agent_presented"] is True
        assert by_reason[REASON_V2]["role_context_present"] is False
        assert by_reason[REASON_V2]["attested"] is False
        assert by_reason[REASON_V4]["agent_presented"] is True
        assert by_reason[REASON_V4]["role_context_present"] is True
        assert by_reason[REASON_V4]["attested"] is False


class TestNonBypassAndNonLeak:
    def test_verdicts_and_reasons_are_unchanged(self, scratch):
        (r1, r2, r3, r4), _, _ = _drive_all_four()
        assert [r["action"] for r in (r1, r2, r3, r4)] == [
            "deny", "deny", "allow", "deny",
        ]
        assert r1["reason"] == REASON_V1
        assert r2["reason"] == REASON_V2
        assert r4["reason"] == REASON_V4

    def test_no_approval_path_is_introduced(self, scratch):
        (r1, r2, r3, r4), _, window = _drive_all_four()
        for response in (r1, r2, r4):
            assert "approval_id" not in response
            assert "approval_id" not in json.dumps(response)
        metas = _scoped_metas(scratch, window)
        assert len(metas) == 3
        assert "approval_id" not in json.dumps(metas)

    def test_token_value_appears_nowhere_while_states_stay_distinct(self, scratch):
        (_, _, _, _), (token_v3, token_v4), window = _drive_all_four()
        blob = json.dumps(
            [
                (row["result"], row["payload"])
                for row in _rows(scratch / "gaia.db")
            ],
            sort_keys=True,
        )
        assert token_v3 not in blob
        assert token_v4 not in blob
        metas = _scoped_metas(scratch, window)
        by_reason = {meta["reason"]: meta for meta in metas}
        assert by_reason[REASON_V1]["attestation_present"] is False
        assert by_reason[REASON_V1]["attestation_resolved"] is False
        assert by_reason[REASON_V2]["attestation_present"] is False
        assert by_reason[REASON_V2]["attestation_resolved"] is False
        assert by_reason[REASON_V4]["attestation_present"] is True
        assert by_reason[REASON_V4]["attestation_resolved"] is True

    def test_allow_path_is_unharmed(self, scratch):
        issued = issue(
            host_run=host_run_id(),
            session_id=SESSION_ALLOW,
            role="developer",
            issuer=ROLE_ISSUER,
        )
        started = _now()
        response = _handle(
            {
                "event": "tool.execute.before",
                "sessionID": SESSION_ALLOW,
                "callID": "call-ordinary",
                "tool": "read",
                "args": {"file_path": str(_ROOT / "README.md")},
                "cwd": str(_ROOT),
                "roleContext": _role_context("developer", issued.token),
            }
        )
        ended = _now()
        assert response["action"] == "allow", response
        assert _scoped_metas(scratch, (started, ended)) == []
