"""Grant cycle with no session env: the grant belongs to its requester (D6).

The block-approve-retry flow crosses sessions legitimately:

  block     : happens under the requesting (subagent) session
  approve   : happens under the approver's session (the orchestrator's answer)
  retry     : happens under the requesting session again

Brief aprobaciones-agnosticas-al-host, D6, binds every grant to the session and
agent that REQUESTED it, never to the one that answered. So the activation runs
under a different session than the block, the requester's retry is allowed, and
a retry from any other session is not. ``CLAUDE_SESSION_ID`` is stripped from the
subprocess environment (by the harness); the only source of the session is the
event JSON -- exactly the production path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

# An install-shaped hooks dir, deliberately NOT HOOKS_DIR (this repo's own
# checkout): protection follows the installation, not the repository
# (decision decision_gaia_proteccion_sigue_a_la_instalacion_no_al_repo), so a
# checkout path no longer drives the protected-path block this file exercises.
INSTALL_HOOKS_DIR = Path.home() / ".claude" / "hooks"

from tests.fixtures.grant_cycle_harness import run_pre_tool_use_event


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cwd(tmp_path: Path) -> Path:
    """Return an isolated project root with a .claude dir for the subprocess."""
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _pending_rows() -> list[dict]:
    """Return all pending approval rows from the isolated DB (any session)."""
    from gaia.approvals.store import get_pending
    return get_pending(all_sessions=True)


def _activate_first_pending(current_session_id: str) -> "ApprovalActivationResult":
    """Activate the first (and expected only) pending row by its approval_id."""
    from modules.security.approval_grants import activate_db_pending_by_id

    rows = _pending_rows()
    assert rows, "Expected at least one pending row in the DB before activation"
    approval_id: str = rows[0]["id"]
    assert approval_id.startswith("P-"), f"Unexpected approval_id format: {approval_id!r}"
    return activate_db_pending_by_id(approval_id, current_session_id=current_session_id)


# ---------------------------------------------------------------------------
# Bash semantic plane
# ---------------------------------------------------------------------------

class TestBashSemanticGrantCycleNoSessionEnv:
    """Grant cycle for Bash T3 commands, bound to the requester (no CLAUDE_SESSION_ID env)."""

    COMMAND = "git push origin feat/brief-71"
    BLOCK_SESSION = "session-subagent-A"
    APPROVER_SESSION = "session-orchestrator-B"
    FOREIGN_SESSION = "session-other-C"

    def test_approver_session_activates_grant_consumed_only_by_the_requester(
        self, tmp_path, monkeypatch
    ):
        """Full bash grant cycle: block → activate from another session → retry.

        The grant is bound to the requesting session, not the approver's: a retry
        from a foreign session is blocked, the requester's retry is allowed.
        """
        cwd = _make_cwd(tmp_path)

        # ── Phase 1: block ──────────────────────────────────────────────────
        # agent_id in the event → hook treats this as a subagent → deny + DB pending.
        block_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.BLOCK_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": self.COMMAND},
            "agent_id": "a12345670f1e2d3c4", "agent_type": "developer",          # marks subagent context
        }
        block_result = run_pre_tool_use_event(block_event, cwd=cwd)

        # The subagent T3 block returns a structured deny (not a hard exit-2).
        assert block_result.exit_code == 0, (
            f"Block phase: expected exit 0 (structured deny), got {block_result.exit_code}.\n"
            f"stderr: {block_result.stderr}\nstdout: {block_result.stdout}"
        )
        assert block_result.permission_decision == "deny", (
            f"Block phase: expected permissionDecision='deny', "
            f"got {block_result.permission_decision!r}.\n"
            f"output: {block_result.output}"
        )

        # A pending approval row must exist in the DB.
        pending = _pending_rows()
        assert len(pending) == 1, (
            f"Expected exactly 1 pending row after block; got {len(pending)}."
        )

        # ── Phase 2: activate (orchestrator side, different session) ────────
        activation = _activate_first_pending(current_session_id=self.APPROVER_SESSION)
        assert activation.success, (
            f"Activation failed: status={activation.status!r}, reason={activation.reason!r}"
        )
        # Pending row must be gone after activation.
        assert not _pending_rows(), "Pending row should be consumed after activation"

        # ── Phase 3: a foreign session cannot use the grant ──────────────────
        retry_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.FOREIGN_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": self.COMMAND},
            "agent_id": "a12345670f1e2d3c4", "agent_type": "developer",
        }
        foreign_result = run_pre_tool_use_event(retry_event, cwd=cwd)
        assert foreign_result.permission_decision == "deny", foreign_result.output

        # ── Phase 4: the requesting session's retry is allowed ───────────────
        retry_event["session_id"] = self.BLOCK_SESSION
        retry_result = run_pre_tool_use_event(retry_event, cwd=cwd)

        assert retry_result.exit_code == 0, (
            f"Retry phase: expected exit 0 (allowed), got {retry_result.exit_code}.\n"
            f"stderr: {retry_result.stderr}\nstdout: {retry_result.stdout}"
        )
        # An allowed passthrough produces no hookSpecificOutput (stdout is empty / None).
        decision = retry_result.permission_decision
        assert decision in (None, "allow"), (
            f"Retry phase: expected allow/None, got {decision!r}.\n"
            f"output: {retry_result.output}"
        )


# ---------------------------------------------------------------------------
# Write/Edit file-path plane
# ---------------------------------------------------------------------------

class TestWriteEditFilePathGrantCycleNoSessionEnv:
    """Grant cycle for protected Write/Edit paths, bound to the requester."""

    BLOCK_SESSION = "session-write-X"
    RETRY_SESSION = BLOCK_SESSION
    APPROVER_SESSION = "session-write-Y"
    FOREIGN_SESSION = "session-write-Z"

    def test_protected_path_write_allowed_after_cross_session_activation(
        self, tmp_path, monkeypatch
    ):
        """Write to a protected hooks path: block → activate from another session → retry.

        The SCOPE_FILE_PATH grant is bound to the requesting session: a foreign
        session is blocked, the requester's retry is allowed.
        """
        cwd = _make_cwd(tmp_path)

        # An install-shaped path so is_protected_hook_path() returns True.
        protected_file = str(INSTALL_HOOKS_DIR / "pre_tool_use.py")

        # ── Phase 1: block ──────────────────────────────────────────────────
        block_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.BLOCK_SESSION,
            "tool_name": "Write",
            "tool_input": {"file_path": protected_file, "content": ""},
            "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",          # subagent context
        }
        block_result = run_pre_tool_use_event(block_event, cwd=cwd)

        # Protected-path block returns a deny (exit 0 + structured response).
        assert block_result.exit_code == 0, (
            f"Block phase: expected exit 0, got {block_result.exit_code}.\n"
            f"stderr: {block_result.stderr}\nstdout: {block_result.stdout}"
        )
        assert block_result.permission_decision == "deny", (
            f"Block phase: expected permissionDecision='deny', "
            f"got {block_result.permission_decision!r}.\n"
            f"output: {block_result.output}"
        )

        pending = _pending_rows()
        assert len(pending) == 1, (
            f"Expected exactly 1 pending row after block; got {len(pending)}."
        )

        # ── Phase 2: activate (approver's session) ──────────────────────────
        activation = _activate_first_pending(current_session_id=self.APPROVER_SESSION)
        assert activation.success, (
            f"Activation failed: status={activation.status!r}, reason={activation.reason!r}"
        )
        assert not _pending_rows(), "Pending row should be consumed after activation"

        # ── Phase 3: a foreign session cannot use the grant ──────────────────
        retry_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.FOREIGN_SESSION,
            "tool_name": "Write",
            "tool_input": {"file_path": protected_file, "content": ""},
            "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",
        }
        foreign_result = run_pre_tool_use_event(retry_event, cwd=cwd)
        assert foreign_result.permission_decision == "deny", foreign_result.output

        # ── Phase 4: the requesting session's retry is allowed ───────────────
        retry_event["session_id"] = self.RETRY_SESSION
        retry_result = run_pre_tool_use_event(retry_event, cwd=cwd)

        assert retry_result.exit_code == 0, (
            f"Retry phase: expected exit 0 (allowed), got {retry_result.exit_code}.\n"
            f"stderr: {retry_result.stderr}\nstdout: {retry_result.stdout}"
        )
        decision = retry_result.permission_decision
        assert decision in (None, "allow"), (
            f"Retry phase: expected allow/None, got {decision!r}.\n"
            f"output: {retry_result.output}"
        )

    def test_file_write_grant_does_not_lift_the_bash_categorical_guard(
        self, tmp_path, monkeypatch
    ):
        """A SCOPE_FILE_PATH grant is scoped to the Write/Edit surface only.

        Companion to ``test_protected_path_write_allowed_after_cross_session_
        activation`` above: that test proves the grant DOES lift the Write/Edit
        block. This test proves the same active grant, for the exact same path,
        does NOT lift ``protected_path_guard``'s Bash guard -- it stays
        categorical (hard exit 2, no ``approval_id``), because
        ``protected_path_guard.check(command)`` takes no grant or approval_id
        argument at all and never consults ``approval_grants``. If that guard
        were ever changed to check a grant (turning it into an approvable T3
        like Write/Edit), this test's ``exit_code == 2`` assertion would fail
        first -- it would observe a structured ``exit_code == 0`` deny (or an
        outright allow) instead of the hard block.
        """
        cwd = _make_cwd(tmp_path)
        protected_file = str(INSTALL_HOOKS_DIR / "pre_tool_use.py")

        # ── Phase 1: block the Write, then activate its grant ────────────────
        block_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.BLOCK_SESSION,
            "tool_name": "Write",
            "tool_input": {"file_path": protected_file, "content": ""},
            "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",
        }
        block_result = run_pre_tool_use_event(block_event, cwd=cwd)
        assert block_result.permission_decision == "deny", (
            f"Block phase: expected permissionDecision='deny', "
            f"got {block_result.permission_decision!r}.\noutput: {block_result.output}"
        )

        activation = _activate_first_pending(current_session_id=self.APPROVER_SESSION)
        assert activation.success, (
            f"Activation failed: status={activation.status!r}, reason={activation.reason!r}"
        )

        # ── Phase 2: confirm the grant DOES lift Write/Edit for this path ────
        write_retry = run_pre_tool_use_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": self.RETRY_SESSION,
                "tool_name": "Write",
                "tool_input": {"file_path": protected_file, "content": ""},
                "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",
            },
            cwd=cwd,
        )
        assert write_retry.is_allowed, (
            f"Write/Edit retry should be allowed by the active file-path grant.\n"
            f"exit_code={write_retry.exit_code}, decision={write_retry.permission_decision!r}"
        )

        # ── Phase 3: the SAME grant must NOT lift the Bash surface ───────────
        bash_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.RETRY_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": f"cp /tmp/payload.py {protected_file}"},
            "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",
        }
        bash_result = run_pre_tool_use_event(bash_event, cwd=cwd)

        assert bash_result.exit_code == 2, (
            "protected_path_guard is categorical: an active FILE_WRITE grant "
            "for this exact path must not turn the Bash write into an "
            f"approvable deny. Got exit_code={bash_result.exit_code}.\n"
            f"stdout={bash_result.stdout!r}\nstderr={bash_result.stderr!r}"
        )
        assert "PROTECTED_PATH" in bash_result.stdout
        assert "approval_id" not in bash_result.stdout

    def test_activation_gives_the_grant_the_approval_window(
        self, tmp_path, monkeypatch
    ):
        """A grant born through activation carries the 30-minute approval window.

        The activation call site used to forward its own ``ttl_minutes``
        parameter into insert_file_path_grant, overriding that function's
        default. The window is therefore only correct end-to-end if the BORN
        row is measured; asserting the writer's default alone passes either way.
        """
        import sqlite3
        from datetime import datetime, timedelta

        from gaia.paths import db_path
        from gaia.store.writer import APPROVAL_WINDOW_MINUTES

        cwd = _make_cwd(tmp_path)
        protected_file = str(INSTALL_HOOKS_DIR / "pre_tool_use.py")

        block_result = run_pre_tool_use_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": self.BLOCK_SESSION,
                "tool_name": "Write",
                "tool_input": {"file_path": protected_file, "content": ""},
                "agent_id": "a76543210f1e2d3c4", "agent_type": "developer",
            },
            cwd=cwd,
        )
        assert block_result.permission_decision == "deny", block_result.output

        activation = _activate_first_pending(current_session_id=self.RETRY_SESSION)
        assert activation.success, activation.reason

        con = sqlite3.connect(str(db_path()))
        try:
            row = con.execute(
                "SELECT created_at, expires_at FROM approval_grants "
                "WHERE scope = 'SCOPE_FILE_PATH' ORDER BY created_at DESC",
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "Activation should have written a SCOPE_FILE_PATH grant"

        span = datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(
            row[0], "%Y-%m-%dT%H:%M:%SZ"
        )
        assert span == timedelta(minutes=APPROVAL_WINDOW_MINUTES), (
            f"Expected the {APPROVAL_WINDOW_MINUTES}-minute approval window, got {span}."
        )
