#!/usr/bin/env python3
"""Tests for session_manifest -- SessionStart additionalContext (Phase 4).

Builders are fail-safe and side-effect-free; the assembler decides what to
include based on plugin mode. These tests use heavy patching to keep each
unit isolated from disk, processes, and external state.
"""

import json
import sys
import time
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_manifest
from modules.session.session_manifest import (
    build_agentic_loop_block,
    build_environment_block,
    build_pending_approvals_block,
    build_session_context,
)


# ---------------------------------------------------------------------------
# build_environment_block
# ---------------------------------------------------------------------------

class TestBuildEnvironmentBlock:
    def test_block_includes_cwd_and_machine_minimum(self, monkeypatch):
        """Even with no workspace identity, the block must carry the basics."""
        # No project-context.json so workspace is None.
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        # Deterministic machine label.
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host (Linux/x86_64)"
        )

        result = build_environment_block()
        assert "## Environment" in result
        assert "cwd:" in result
        assert "host (Linux/x86_64)" in result

    def test_block_includes_workspace_when_available(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: "my-workspace"
        )
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host (Linux/x86_64)"
        )

        result = build_environment_block()
        assert "Workspace: my-workspace" in result

    def test_block_includes_version_when_available(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        monkeypatch.setattr(
            session_manifest, "_machine_label", lambda: "host"
        )
        monkeypatch.setattr(
            session_manifest, "_read_gaia_version", lambda: "5.0.0-rc.3"
        )

        result = build_environment_block()
        assert "Gaia: 5.0.0-rc.3" in result

    def test_block_failsafe_when_workspace_helper_raises(self, monkeypatch):
        """A subcomponent raising must not propagate -- builder returns
        either a partial block or ''. Test enforces the no-raise contract."""
        def _boom():
            raise RuntimeError("simulated context-file error")

        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", _boom
        )

        # Should not raise; result is allowed to be either "" or a
        # partial block built without the workspace line.
        result = build_environment_block()
        assert isinstance(result, str)
        # The catch is at the function boundary; we tolerate either branch
        # but must not see a Workspace line for the failing helper.
        assert "Workspace:" not in result


# ---------------------------------------------------------------------------
# build_agentic_loop_block
# ---------------------------------------------------------------------------

class TestBuildAgenticLoopBlock:
    def test_returns_detector_output_when_present(self, monkeypatch):
        """The block is a thin wrapper -- when the detector returns text,
        the builder must return it verbatim.
        """
        sentinel = "## Active Agentic Loop\nGoal: validate Y"
        import modules.context.agentic_loop_detector as detector
        monkeypatch.setattr(detector, "build_resume_context", lambda: sentinel)

        assert build_agentic_loop_block() == sentinel

    def test_returns_empty_when_detector_returns_empty(self, monkeypatch):
        import modules.context.agentic_loop_detector as detector
        monkeypatch.setattr(detector, "build_resume_context", lambda: "")

        assert build_agentic_loop_block() == ""

    def test_returns_empty_when_detector_raises(self, monkeypatch):
        import modules.context.agentic_loop_detector as detector

        def _boom():
            raise RuntimeError("simulated detector error")

        monkeypatch.setattr(detector, "build_resume_context", _boom)
        assert build_agentic_loop_block() == ""


# ---------------------------------------------------------------------------
# build_pending_approvals_block
# ---------------------------------------------------------------------------

def _make_fake_pending(nonce_short: str, session_id: str):
    return {
        "nonce_short": nonce_short,
        "nonce_full": nonce_short + ("0" * (32 - len(nonce_short))),
        "command": f"fake-cmd-{nonce_short}",
        "verb": "update",
        "category": "MUTATIVE",
        "age_human": "1 hora",
        "timestamp": time.time() - 3600,
        "context": {},
        "scope_type": "semantic_signature",
        "cross_session": False,
        "pending_session_id": session_id,
    }


class TestBuildPendingApprovalsBlock:
    def test_returns_empty_when_no_pendings(self, monkeypatch):
        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", lambda *a, **kw: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-empty")

        result = build_pending_approvals_block()
        assert result == ""

    def test_returns_actionable_block_with_pendings(self, monkeypatch):
        import modules.session.pending_scanner as ps
        pendings = [_make_fake_pending("abcd1234", "sess-test")]
        monkeypatch.setattr(
            ps, "scan_pending_approvals", lambda *a, **kw: pendings
        )
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-test")

        result = build_pending_approvals_block()
        assert "[ACTIONABLE]" in result
        assert "P-abcd1234" in result

    def test_cross_session_fallback_uses_exclude_live_sessions(self, monkeypatch):
        """When current-session scan is empty, the cross-session scan must
        be invoked with exclude_live_sessions=True so live siblings are
        filtered out. This protects the include_headless=False path
        installed inside pending_scanner."""
        import modules.session.pending_scanner as ps

        captured_calls = []

        def fake_scan(
            approvals_dir,
            session_id=None,
            current_session_id=None,
            exclude_live_sessions=False,
        ):
            captured_calls.append(
                {
                    "session_id": session_id,
                    "current_session_id": current_session_id,
                    "exclude_live_sessions": exclude_live_sessions,
                }
            )
            # First call (current-session) returns empty so fallback triggers.
            if session_id is not None:
                return []
            # Second call (cross-session fallback) returns one pending.
            return [_make_fake_pending("xs000001", "sess-other")]

        monkeypatch.setattr(ps, "scan_pending_approvals", fake_scan)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-test")

        result = build_pending_approvals_block()
        assert "[ACTIONABLE]" in result
        assert len(captured_calls) == 2, (
            "Both the current-session and cross-session scans must run "
            "when the first returns empty."
        )
        assert captured_calls[1]["exclude_live_sessions"] is True, (
            "Cross-session fallback must pass exclude_live_sessions=True. "
            "Without it, pendings from parallel live sessions would "
            "double-surface."
        )

    def test_failsafe_when_scanner_raises(self, monkeypatch):
        import modules.session.pending_scanner as ps

        def _boom(*a, **kw):
            raise RuntimeError("simulated scanner failure")

        monkeypatch.setattr(ps, "scan_pending_approvals", _boom)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-x")

        # Must not raise.
        assert build_pending_approvals_block() == ""


# ---------------------------------------------------------------------------
# build_session_context (assembler)
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    def test_returns_empty_in_security_mode(self, monkeypatch):
        """The security plugin has no orchestrator to act on the manifest."""
        # Stub all builders to return non-empty content; assembler must
        # still drop them because mode != ops.
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP"
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND"
        )

        assert build_session_context("security") == ""
        assert build_session_context("") == ""

    def test_assembles_three_blocks_with_blank_line_separator(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND BLOCK"
        )

        result = build_session_context("ops")
        assert result == "ENV BLOCK\n\nLOOP BLOCK\n\nPEND BLOCK", (
            "Blocks must be joined with exactly one blank line separator -- "
            "markdown convention; agents render this as paragraph breaks."
        )

    def test_skips_empty_blocks_in_join(self, monkeypatch):
        """Empty blocks must not leave dangling blank lines in the output."""
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND BLOCK"
        )

        result = build_session_context("ops")
        assert result == "ENV BLOCK\n\nPEND BLOCK"
        assert "\n\n\n" not in result, (
            "Triple-newline indicates an empty block sneaked into the join."
        )

    def test_returns_empty_when_all_blocks_empty(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: ""
        )

        assert build_session_context("ops") == ""

    def test_failsafe_when_a_builder_raises(self, monkeypatch):
        """An exception in a builder must not break the assembler."""
        def _boom():
            raise RuntimeError("simulated builder failure")

        monkeypatch.setattr(
            session_manifest, "build_environment_block", _boom
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP"
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND"
        )

        # Either the assembler swallows the exception entirely (returning "")
        # or it catches around the whole pipeline and returns "". Both are
        # acceptable; what is not acceptable is propagating the exception.
        result = build_session_context("ops")
        assert isinstance(result, str)
