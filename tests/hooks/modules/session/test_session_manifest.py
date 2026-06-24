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
    build_workspace_memory_block,
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
    """Build a filesystem-style fake pending dict."""
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


def _make_fake_pending_db(nonce_short: str, session_id: str):
    """Build a DB-style fake pending dict (as returned by scan_pending_db)."""
    nonce_full = nonce_short + ("0" * (32 - len(nonce_short)))
    return {
        "nonce_short": nonce_short,
        "nonce_full": nonce_full,
        "command": f"fake-db-cmd-{nonce_short}",
        "verb": "delete",
        "category": "DESTRUCTIVE",
        "age_human": "1 hora",
        "timestamp": time.time() - 3600,
        "context": {"source": "db", "description": "test op", "risk": "high", "rollback": None},
        "scope_type": "db",
        "cross_session": False,
        "pending_session_id": session_id,
        "_approval_id": f"P-{nonce_full}",
    }


class TestBuildPendingApprovalsBlock:
    def test_returns_empty_when_no_pendings(self, monkeypatch):
        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_db", lambda: [])
        monkeypatch.setattr(ps, "scan_pending_approvals", lambda *a, **kw: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-empty")

        result = build_pending_approvals_block()
        assert result == ""

    def test_returns_actionable_block_with_db_pendings(self, monkeypatch):
        """DB pendings (primary path) must surface in the [ACTIONABLE] block."""
        import modules.session.pending_scanner as ps
        db_pendings = [_make_fake_pending_db("abcd1234", "sess-main")]
        monkeypatch.setattr(ps, "scan_pending_db", lambda: db_pendings)
        monkeypatch.setattr(ps, "scan_pending_approvals", lambda *a, **kw: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-main")

        result = build_pending_approvals_block()
        assert "[ACTIONABLE]" in result
        assert "P-abcd1234" in result

    def test_returns_empty_when_db_empty(self, monkeypatch):
        """When DB returns no pending rows, the block is empty (DB-only since Task E)."""
        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_db", lambda: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-fs")

        result = build_pending_approvals_block()
        assert result == "", (
            "Task E: FS supplement is retired; an empty DB must yield an empty block."
        )

    def test_db_is_sole_source_multiple_pendings(self, monkeypatch):
        """Multiple DB pendings all surface; no deduplication needed since DB is sole source."""
        import modules.session.pending_scanner as ps
        db_pendings = [
            _make_fake_pending_db("abcd1234", "sess-x"),
            _make_fake_pending_db("deadbeef", "sess-x"),
        ]
        monkeypatch.setattr(ps, "scan_pending_db", lambda: db_pendings)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-x")

        result = build_pending_approvals_block()
        assert "[ACTIONABLE]" in result
        assert "P-abcd1234" in result
        assert "P-deadbeef" in result
        # Each approval must appear exactly once.
        assert result.count("P-abcd1234") == 1
        assert result.count("P-deadbeef") == 1

    def test_failsafe_when_db_scanner_raises(self, monkeypatch):
        """When scan_pending_db raises, the block must still be "" (fail-safe)."""
        import modules.session.pending_scanner as ps

        def _boom_db():
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(ps, "scan_pending_db", _boom_db)
        monkeypatch.setattr(ps, "scan_pending_approvals", lambda *a, **kw: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-x")

        # Must not raise.
        assert build_pending_approvals_block() == ""

    def test_failsafe_when_scanner_raises(self, monkeypatch):
        """When scan_pending_db raises, return "" without propagating (DB-only since Task E)."""
        import modules.session.pending_scanner as ps

        def _boom(*a, **kw):
            raise RuntimeError("simulated scanner failure")

        monkeypatch.setattr(ps, "scan_pending_db", _boom)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-x")

        # Must not raise.
        assert build_pending_approvals_block() == ""

    def test_command_set_pending_surfaces_correctly(self, monkeypatch):
        """A COMMAND_SET DB pending (multi-command) must surface with
        the correct P-id and command summary in the [ACTIONABLE] block."""
        import modules.session.pending_scanner as ps
        cs_pending = _make_fake_pending_db("cs001234", "sess-y")
        cs_pending["command"] = "[2 commands] kubectl delete pod foo"
        monkeypatch.setattr(ps, "scan_pending_db", lambda: [cs_pending])
        monkeypatch.setattr(ps, "scan_pending_approvals", lambda *a, **kw: [])
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-y")

        result = build_pending_approvals_block()
        assert "[ACTIONABLE]" in result
        assert "P-cs001234" in result
        assert "[2 commands]" in result


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
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: "MEM"
        )

        assert build_session_context("security") == ""
        assert build_session_context("") == ""

    def test_assembles_all_blocks_with_blank_line_separator(self, monkeypatch):
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: "PROJ BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: "MEM BLOCK"
        )

        result = build_session_context("ops")
        assert result == (
            "ENV BLOCK\n\nPROJ BLOCK\n\nLOOP BLOCK\n\nPEND BLOCK\n\nMEM BLOCK"
        ), (
            "Blocks must be joined with exactly one blank line separator -- "
            "markdown convention; agents render this as paragraph breaks. "
            "Project Context — Projects sits right after Environment."
        )

    def test_skips_empty_blocks_in_join(self, monkeypatch):
        """Empty blocks must not leave dangling blank lines in the output."""
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: "PEND BLOCK"
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: ""
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
            session_manifest, "build_projects_context_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_pending_approvals_block", lambda: ""
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: ""
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
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: ""
        )

        # Either the assembler swallows the exception entirely (returning "")
        # or it catches around the whole pipeline and returns "". Both are
        # acceptable; what is not acceptable is propagating the exception.
        result = build_session_context("ops")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_workspace_memory_block
# ---------------------------------------------------------------------------

class TestBuildWorkspaceMemoryBlock:
    """The block shells out to `gaia memory get-relevant`. Tests stub the
    subprocess result to keep the unit isolated from the substrate DB."""

    def test_returns_block_when_cli_emits_content(self, monkeypatch):
        """CLI succeeds with text -> builder returns it verbatim (stripped)."""
        import subprocess

        sentinel = "## Workspace Memory (qxo)\n\nAtoms:\n- atom_x: y"

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=0,
                stdout=sentinel + "\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # Pin the workspace so the helper doesn't try to read project-context.
        result = build_workspace_memory_block(workspace="qxo")
        assert result == sentinel

    def test_returns_empty_when_no_workspace(self, monkeypatch):
        """No workspace identity -> empty block, no subprocess call."""
        monkeypatch.setattr(
            session_manifest, "_read_workspace_identity", lambda: None
        )
        # If subprocess is touched, the test should still not raise.
        result = build_workspace_memory_block()
        assert result == ""

    def test_returns_empty_when_cli_nonzero_exit(self, monkeypatch):
        """CLI exits non-zero -> empty block (fail-safe)."""
        import subprocess

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=2,
                stdout="",
                stderr="oops",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""

    def test_returns_empty_when_cli_raises(self, monkeypatch):
        """Subprocess raises (timeout, FileNotFoundError) -> empty block."""
        import subprocess

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="gaia", timeout=5)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""

    def test_returns_empty_when_cli_emits_only_whitespace(self, monkeypatch):
        """CLI exits 0 but with empty stdout -> empty block."""
        import subprocess

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=0,
                stdout="   \n  \n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = build_workspace_memory_block(workspace="qxo")
        assert result == ""
