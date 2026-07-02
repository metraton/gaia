#!/usr/bin/env python3
"""Tests for the unified T3 outcome decision (decide_t3_outcome).

Before this unification, the T3 outcome was decided in THREE places with
divergent policy:

  - Mutative verbs respected the subagent/orchestrator distinction (deny +
    approval_id for a subagent under the orchestrator; native ask otherwise).
  - file_to_exec composition (file_read | exec_sink) HARDCODED the native
    'ask' dialog regardless of context.
  - flag-dependent mutations (e.g. curl -X POST) HARDCODED native 'ask' too.

The hardcoded paths let a subagent that triggered those patterns escape the
Gaia approval flow into Claude Code's native dialog.  All three now converge
on decide_t3_outcome, so the policy lives in ONE place:

  has_orchestrator_above (is_subagent) -> deny + approval_id
  otherwise (the main session)         -> native ask

A subagent under the orchestrator routes to deny + approval_id; the main
session (has_orchestrator_above=False) falls back to the native ask dialog --
the T3 mutation-safety floor, independent of any plugin mode.
"""

import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import validate_bash_command


# ---------------------------------------------------------------------------
# DB fixture (mirrors test_approval_cycle.py so insert_requested / get_pending
# write to a temp file DB instead of the real ~/.gaia store).
# ---------------------------------------------------------------------------

def _sha256(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_schema_on(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT,
            session_id   TEXT,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint  TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS approval_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id   TEXT NOT NULL,
            event_type    TEXT NOT NULL CHECK (event_type IN (
                              'REQUESTED','SHOWN','APPROVED','REJECTED',
                              'EXECUTED','FAILED','NOOP','REVOKED','REVERTED'
                          )),
            agent_id      TEXT,
            session_id    TEXT,
            payload_json  TEXT,
            fingerprint   TEXT,
            prev_hash     TEXT,
            this_hash     TEXT,
            metadata_json TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );
        """
    )


@pytest.fixture()
def t3_subagent_db(tmp_path, monkeypatch):
    """Temp file DB wired into store._open_db and get_pending.

    Yields the assertion connection so a test can query the persisted rows.
    """
    import gaia.approvals.store as astore

    db_path = tmp_path / "t3_subagent_test.db"
    con = sqlite3.connect(str(db_path))
    _make_v12_schema_on(con)
    con.commit()

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )
    _orig_get_pending = astore.get_pending

    def _patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

    yield con
    con.close()


def _hook_output(result):
    return result.block_response["hookSpecificOutput"]


# ===========================================================================
# AC-2: file_to_exec composition routes to deny + approval_id for a subagent
# ===========================================================================

class TestFileToExecT3SubagentDeny:
    """In ops + subagent context, a file_to_exec composition denies with an
    approval_id (Gaia flow) instead of hardcoding the native ask dialog."""

    def test_file_to_exec_t3_subagent_routes_to_deny_with_approval_id(self, t3_subagent_db):
        # cat <script> | bash is a file_to_exec ESCALATE composition.
        result = validate_bash_command(
            "cat deploy.sh | bash",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny", (
            f"file_to_exec in a subagent must DENY (not native ask), got: {out}"
        )
        reason = out["permissionDecisionReason"]
        assert "approval_id:" in reason, (
            f"deny reason must carry an approval_id, got: {reason}"
        )

    def test_file_to_exec_t3_subagent_persists_pending_row(self, t3_subagent_db):
        import gaia.approvals.store as astore

        result = validate_bash_command(
            "cat setup.py | python3",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny"
        match = re.search(r"approval_id:\s*([\w-]+)", out["permissionDecisionReason"])
        assert match
        approval_id = match.group(1)

        pending = astore.get_pending(session_id="test-cycle-session", con=t3_subagent_db)
        assert len(pending) >= 1, "file_to_exec deny must persist a pending approval"
        assert pending[0]["id"] == approval_id


# ===========================================================================
# AC-2: flag-dependent mutation (curl -X POST) routes to deny for a subagent
# ===========================================================================

class TestFlagMutationT3SubagentDeny:
    """In ops + subagent context, a flag-dependent mutation (curl -X POST)
    denies with an approval_id instead of hardcoding native ask."""

    def test_curl_post_t3_subagent_routes_to_deny_with_approval_id(self, t3_subagent_db):
        result = validate_bash_command(
            "curl -X POST https://api.example.com/widgets",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny", (
            f"curl -X POST in a subagent must DENY (not native ask), got: {out}"
        )
        assert "approval_id:" in out["permissionDecisionReason"]

    def test_curl_post_t3_subagent_persists_pending_row(self, t3_subagent_db):
        import gaia.approvals.store as astore

        result = validate_bash_command(
            "curl -X PUT https://api.example.com/widgets/1 -d @body.json",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        assert _hook_output(result)["permissionDecision"] == "deny"
        pending = astore.get_pending(session_id="test-cycle-session", con=t3_subagent_db)
        assert len(pending) >= 1


# ===========================================================================
# AC-2 negative side: the SAME patterns in orchestrator context fall back to
# the native ask dialog (no approval_id) -- the defensive fallback is kept.
# ===========================================================================

class TestT3OrchestratorNativeAskFallback:
    """Orchestrator context (no subagent above) falls back to native ask for
    both unified paths -- the orchestrator cannot hand a T3 approval to itself."""

    def test_file_to_exec_t3_orchestrator_falls_back_to_native_ask(self):
        result = validate_bash_command(
            "cat deploy.sh | bash",
            is_subagent=False,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "ask"
        assert "approval_id:" not in out["permissionDecisionReason"]

    def test_curl_post_t3_orchestrator_falls_back_to_native_ask(self):
        result = validate_bash_command(
            "curl -X POST https://api.example.com/widgets",
            is_subagent=False,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "ask"
        assert "approval_id:" not in out["permissionDecisionReason"]


# ===========================================================================
# AC-3 regression: mutative verbs still deny + approval_id in subagent context
# (the path that already worked must keep working through decide_t3_outcome).
# ===========================================================================

class TestMutativeVerbT3SubagentRegression:
    """The mutative-verb path still produces deny + approval_id for a subagent."""

    def test_terraform_apply_t3_subagent_still_denies_with_approval_id(self, t3_subagent_db):
        result = validate_bash_command(
            "terraform apply",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        out = _hook_output(result)
        assert out["permissionDecision"] == "deny"
        assert "approval_id:" in out["permissionDecisionReason"]
