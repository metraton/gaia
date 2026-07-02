"""Characterization tests: grant cycle is session-agnostic (Brief 71 invariant).

CONTEXT — why these tests exist
================================
Brief 71 / Task E migrated the T3 approval grant plane from filesystem files
(which were session-locked via ``AND session_id=?``) to a DB-backed model whose
``check_db_semantic_grant`` and ``check_db_file_path_grant`` intentionally omit
any session_id filter.  The migration proved empirically that the block-approve-
retry flow crosses sessions legitimately:

  block     : happens under the subagent session
  approve   : happens under the orchestrator session (AskUserQuestion answer)
  retry     : happens under the subagent session (resumed after grant activated)

If ``session_id`` were a match criterion the retry would NEVER find the grant,
because the orchestrator-side activation uses a different session_id than the
one recorded in the DB row.

WHAT THESE TESTS DO
===================
They are **characterization** tests: the invariant is already correct.  The
tests SHIELD it against future regressions -- any refactor that re-introduces
a session_id filter into the grant-match path will turn these green tests red.

The tests deliberately run the retry under a DIFFERENT ``session_id`` than the
block, confirming that the grant survives the session boundary.  ``CLAUDE_SESSION_ID``
is stripped from the subprocess environment (by the harness); the only source of
the session is the event JSON -- exactly the production path.

PLANES COVERED
==============
- bash_semantic : T3 command blocked as subagent → grant activated → allowed on
                  retry with a different session_id
- write_edit_file_path : protected-path Write blocked as subagent → grant
                         activated → allowed on retry with a different session_id
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

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
    """Activate the first (and expected only) pending row by its nonce prefix."""
    from modules.security.approval_grants import activate_db_pending_by_prefix

    rows = _pending_rows()
    assert rows, "Expected at least one pending row in the DB before activation"
    # approval_id format: P-{hex} → nonce_prefix is the first 8 chars after "P-"
    approval_id: str = rows[0]["id"]
    assert approval_id.startswith("P-"), f"Unexpected approval_id format: {approval_id!r}"
    nonce_prefix = approval_id[2:10]  # 8-char prefix used by activate_db_pending_by_prefix
    return activate_db_pending_by_prefix(nonce_prefix, current_session_id=current_session_id)


# ---------------------------------------------------------------------------
# Bash semantic plane
# ---------------------------------------------------------------------------

class TestBashSemanticGrantCycleNoSessionEnv:
    """Grant cycle for Bash T3 commands is session-agnostic (no CLAUDE_SESSION_ID env)."""

    COMMAND = "git push origin feat/brief-71"
    BLOCK_SESSION = "session-subagent-A"
    RETRY_SESSION = "session-subagent-B"   # deliberately different from BLOCK_SESSION

    def test_block_activates_grant_consumed_by_cross_session_retry(self, tmp_path, monkeypatch):
        """Full bash grant cycle: block → activate → retry under a different session.

        Invariant: the retry finds the grant WITHOUT a session_id match constraint.
        The test uses a DIFFERENT session for the retry than was used for the block;
        if session_id were a filter criterion, the retry would be blocked again.
        """
        cwd = _make_cwd(tmp_path)

        # ── Phase 1: block ──────────────────────────────────────────────────
        # agent_id in the event → hook treats this as a subagent → deny + DB pending.
        block_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.BLOCK_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": self.COMMAND},
            "agent_id": "a1234567",          # marks subagent context
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
        activation = _activate_first_pending(current_session_id=self.RETRY_SESSION)
        assert activation.success, (
            f"Activation failed: status={activation.status!r}, reason={activation.reason!r}"
        )
        # Pending row must be gone after activation.
        assert not _pending_rows(), "Pending row should be consumed after activation"

        # ── Phase 3: retry (subagent, different session) ─────────────────────
        # The retry uses RETRY_SESSION (≠ BLOCK_SESSION) to prove session-agnosticism.
        retry_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.RETRY_SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": self.COMMAND},
            "agent_id": "a1234567",
        }
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
    """Grant cycle for protected Write/Edit paths is session-agnostic."""

    BLOCK_SESSION = "session-write-X"
    RETRY_SESSION = "session-write-Y"   # deliberately different

    def test_protected_path_write_allowed_after_cross_session_activation(
        self, tmp_path, monkeypatch
    ):
        """Write to a protected hooks path: block → activate → retry under a different session.

        Invariant: the retry finds the SCOPE_FILE_PATH grant without a session_id
        constraint.  Using a different session_id for the retry exercises the
        cross-session boundary that Brief 71 Task E fixed.
        """
        cwd = _make_cwd(tmp_path)

        # Target a real path inside the hooks dir so _is_protected() returns True.
        protected_file = str(HOOKS_DIR / "pre_tool_use.py")

        # ── Phase 1: block ──────────────────────────────────────────────────
        block_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.BLOCK_SESSION,
            "tool_name": "Write",
            "tool_input": {"file_path": protected_file, "content": ""},
            "agent_id": "a7654321",          # subagent context
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

        # ── Phase 2: activate (different session) ───────────────────────────
        activation = _activate_first_pending(current_session_id=self.RETRY_SESSION)
        assert activation.success, (
            f"Activation failed: status={activation.status!r}, reason={activation.reason!r}"
        )
        assert not _pending_rows(), "Pending row should be consumed after activation"

        # ── Phase 3: retry (different session) ──────────────────────────────
        retry_event = {
            "hook_event_name": "PreToolUse",
            "session_id": self.RETRY_SESSION,
            "tool_name": "Write",
            "tool_input": {"file_path": protected_file, "content": ""},
            "agent_id": "a7654321",
        }
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
