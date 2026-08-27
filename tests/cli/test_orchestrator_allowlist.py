"""Verified control-plane identity does not widen Gaia's CLI allowlist."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "hooks"))

from modules.orchestrator.delegate_mode import SessionRole, classify_session_role
from modules.security import gaia_cli_only_guard


def _context():
    return {
        "role_context": {
            "role": "gaia-orchestrator",
            "capabilities": ["brief.read", "plan.manage"],
            "issuer": "gaia-runtime",
            "attestation": "signed-binding",
            "verified": True,
        }
    }


def test_verified_context_is_control_plane_but_prompt_identity_is_not():
    assert classify_session_role(_context()) is SessionRole.ORCHESTRATOR
    assert classify_session_role({"agent_type": "gaia-orchestrator"}) is SessionRole.ORCHESTRATOR
    assert classify_session_role({"agent_type": "general-purpose", "role": "gaia-orchestrator", "verified": True}) is SessionRole.NAMED_SPECIALIST
    assert classify_session_role({"agent_id": "ordinary-agent", **_context()}) is SessionRole.SUBAGENT


def test_control_plane_keeps_existing_allowlist_and_denies_t3(monkeypatch):
    monkeypatch.setattr(gaia_cli_only_guard, "is_trusted_gaia_binary", lambda _: True)
    allowed, reason = gaia_cli_only_guard.check("/trusted/gaia brief show demo", _context())
    assert allowed and reason is None

    for command in (
        "/trusted/gaia approvals approve P-test",
        "/trusted/gaia push origin main",
        "/trusted/gaia deploy production",
        "git push origin main",
    ):
        allowed, reason = gaia_cli_only_guard.check(command, _context())
        assert not allowed, f"must deny {command!r}: {reason}"
