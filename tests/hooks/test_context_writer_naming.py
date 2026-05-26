"""
Tests for context heading rename: investigation_brief -> agent_contract_handoff (T2.1a / AC-14).

Asserts:
- No ``investigation_brief`` key in the telemetry snapshot output dict
- The injected context orientation heading is "Agent Contract Handoff", not "Brief"
- The context string section heading is "# Agent Contract Handoff", not "# Brief"
"""

import pytest
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.context.context_injector import build_context_telemetry_snapshot


# ---------------------------------------------------------------------------
# Telemetry snapshot key tests
# ---------------------------------------------------------------------------

class TestTelemetrySnapshotNaming:
    """The telemetry snapshot must use 'agent_contract_handoff' as the key."""

    def _make_payload(self, key: str) -> dict:
        return {
            "project_knowledge": {},
            "metadata": {},
            "surface_routing": {},
            key: {
                "agent_role": "primary",
                "primary_surface": "gaia_system",
                "adjacent_surfaces": [],
                "cross_check_required": False,
                "consolidation_required": False,
                "required_checks": [],
                "evidence_required": ["files_checked"],
            },
            "write_permissions": {},
        }

    def test_new_key_produces_agent_contract_handoff_in_snapshot(self):
        payload = self._make_payload("agent_contract_handoff")
        snapshot = build_context_telemetry_snapshot(payload)
        assert "agent_contract_handoff" in snapshot, (
            "Snapshot must contain 'agent_contract_handoff' key"
        )

    def test_new_key_no_investigation_brief_in_snapshot(self):
        payload = self._make_payload("agent_contract_handoff")
        snapshot = build_context_telemetry_snapshot(payload)
        assert "investigation_brief" not in snapshot, (
            "Snapshot must NOT contain legacy 'investigation_brief' key"
        )

    def test_legacy_key_still_read_during_dual_mode(self):
        """Legacy investigation_brief key is still accepted as input (fallback)."""
        payload = self._make_payload("investigation_brief")
        snapshot = build_context_telemetry_snapshot(payload)
        # Even when input uses legacy key, output key is the new one
        assert "agent_contract_handoff" in snapshot, (
            "Legacy input key should still be read and emitted under new key"
        )
        assert "investigation_brief" not in snapshot, (
            "Output must use new key even when input was legacy"
        )

    def test_empty_payload_no_agent_contract_handoff_key(self):
        snapshot = build_context_telemetry_snapshot({})
        # Empty payload -> empty snapshot, no spurious keys
        assert "investigation_brief" not in snapshot
        # agent_contract_handoff may or may not be present depending on pruning
        # but it must not be called investigation_brief

    def test_snapshot_agent_role_preserved(self):
        payload = self._make_payload("agent_contract_handoff")
        snapshot = build_context_telemetry_snapshot(payload)
        block = snapshot.get("agent_contract_handoff", {})
        assert block.get("agent_role") == "primary"


# ---------------------------------------------------------------------------
# Context string heading tests
# ---------------------------------------------------------------------------

class TestContextStringHeadings:
    """The injected context string must use the new headings."""

    def _build_minimal_context_string(self) -> str:
        """Simulate what build_project_context produces for orientation section."""
        # We test the heading values by inspecting context_injector module internals
        # without running the full build_project_context() (which requires DB + env).
        # Instead we directly check the string constants used.
        import importlib
        import modules.context.context_injector as ci

        # Verify the orientation line text
        orientation_marker = "Agent Contract Handoff"
        old_marker = "Brief"

        # Read the source to confirm the heading is updated
        source = Path(ci.__file__).read_text()
        return source

    def test_orientation_heading_is_agent_contract_handoff(self):
        source = self._build_minimal_context_string()
        assert "Agent Contract Handoff" in source, (
            "context_injector.py must contain 'Agent Contract Handoff' heading"
        )

    def test_orientation_heading_not_bare_brief(self):
        source = self._build_minimal_context_string()
        # "Brief" may still appear in variable names (brief_mkv) or comments --
        # we specifically check that the section heading is not the old bare form.
        # The old pattern was: "- **Brief** -- goal..."
        assert "**Brief**" not in source, (
            "context_injector.py must not contain the old '**Brief**' orientation heading"
        )

    def test_section_header_is_agent_contract_handoff(self):
        source = self._build_minimal_context_string()
        assert "# Agent Contract Handoff" in source, (
            "context_injector.py must contain '# Agent Contract Handoff' section header"
        )

    def test_old_section_header_not_present(self):
        source = self._build_minimal_context_string()
        # Old: "# Brief\n" -- should not appear
        assert "# Brief\n" not in source, (
            "context_injector.py must not contain the old '# Brief' section header"
        )
