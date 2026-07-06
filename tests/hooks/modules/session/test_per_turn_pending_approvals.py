#!/usr/bin/env python3
"""Tests for the removal of the per-turn VERIFIED pending-approvals injection.

M2 eliminates ALL cross-session surfacing of pendings. The two builders that
previously derived and rendered pending approvals for per-turn injection --

  build_verified_pending_approvals()
  build_per_turn_pending_approvals_block()

-- have been DELETED from session_manifest. This test file used to verify their
behaviour; it now verifies the opposite contract: the builders no longer exist,
and nothing surfaces pendings into the session context.

The DB remains the canonical pending store (read on demand via `gaia
approvals`), TTL hygiene keeps it clean, and session-agnostic matching
(check_db_semantic_grant) still authorizes retried commands -- none of which
lives here.
"""

import sys
from pathlib import Path

import pytest

# hooks/ on path so `from modules.session...` resolves like production.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
# repo root so `import gaia...` resolves if ever needed by the module import.
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.session import session_manifest


class TestPerTurnBuildersRemoved:
    """The per-turn pending-approval builders must no longer exist."""

    def test_build_verified_pending_approvals_is_gone(self):
        assert not hasattr(session_manifest, "build_verified_pending_approvals"), (
            "build_verified_pending_approvals surfaced pendings for per-turn "
            "injection; it must be removed (M2 no cross-session surfacing)."
        )

    def test_build_per_turn_pending_approvals_block_is_gone(self):
        assert not hasattr(
            session_manifest, "build_per_turn_pending_approvals_block"
        ), (
            "build_per_turn_pending_approvals_block rendered pendings for "
            "per-turn injection; it must be removed (M2 no cross-session "
            "surfacing)."
        )

    def test_builders_cannot_be_imported(self):
        with pytest.raises(ImportError):
            from modules.session.session_manifest import (  # noqa: F401
                build_verified_pending_approvals,
            )
        with pytest.raises(ImportError):
            from modules.session.session_manifest import (  # noqa: F401
                build_per_turn_pending_approvals_block,
            )


class TestNoPendingSurfacingBuilderRemains:
    """No builder in session_manifest surfaces pending approvals anymore."""

    def test_session_start_pending_block_builder_is_gone(self):
        assert not hasattr(session_manifest, "build_pending_approvals_block"), (
            "The SessionStart [ACTIONABLE] pending-approvals builder must be "
            "removed as well -- no builder surfaces pendings."
        )

    def test_session_context_contains_no_pending_block(self, monkeypatch):
        """build_session_context must assemble without any pending block.

        Stub the remaining builders so the assembler runs deterministically and
        offline; the result must contain none of the pending-surfacing markers.
        """
        monkeypatch.setattr(
            session_manifest, "build_environment_block", lambda: "ENV"
        )
        monkeypatch.setattr(
            session_manifest, "build_projects_context_block", lambda: "PROJ"
        )
        monkeypatch.setattr(
            session_manifest, "build_agentic_loop_block", lambda: "LOOP"
        )
        monkeypatch.setattr(
            session_manifest, "build_workspace_memory_block", lambda: "MEM"
        )

        result = session_manifest.build_session_context()
        assert result == "ENV\n\nPROJ\n\nLOOP\n\nMEM"
        assert "[ACTIONABLE]" not in result
        assert "PENDING-APPROVALS-VERIFIED" not in result
