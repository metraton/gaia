"""Registry tests for the STAGED (not-yet-live) gaia-verifier agent definition.

Brief: B3 (plan_id=33), Task T4, AC-4.

M1 DORMANCY CONSTRAINT: this brief authors the gaia-verifier agent DEFINITION
and proves it is well-formed and recognized by the registry mechanism, WITHOUT
arming the live verifier registry. The canonical content lives at
``tests/fixtures/agents_staging/gaia-verifier.md`` -- a staging path
``gaia.state.permissions._agents_dir()`` never scans (that function resolves
only ``<repo_root>/agents``, see ``permissions.py``) -- so
``verifier_fleet()`` against the LIVE ``agents/`` directory stays an empty
frozenset after this module runs. Landing ``agents/gaia-verifier.md`` in the
live tree is T6/M2, not this task.

Coverage mirrors ``tests/test_verifier_registry.py``'s
``TestSyntheticSeededFleet`` precedent in MECHANISM (an isolated, monkeypatched
``agents/`` fixture directory, never the real tree) but exercises the REAL
staged file content instead of an inline literal, plus asserts the specific
frontmatter shape this brief requires (tools, disallowedTools, no routing:
block) and re-confirms the live registry is untouched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import permissions as _permissions  # noqa: E402
from gaia.state.permissions import (  # noqa: E402
    handoff_writer_fleet,
    is_handoff_writer,
    is_verifier,
    verifier_fleet,
)

_STAGED_AGENT = _REPO_ROOT / "tests" / "fixtures" / "agents_staging" / "gaia-verifier.md"
_LIVE_AGENTS_DIR = _REPO_ROOT / "agents"


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both fleets are lru_cache'd; each test starts from a clean slate."""
    verifier_fleet.cache_clear()
    handoff_writer_fleet.cache_clear()
    yield
    verifier_fleet.cache_clear()
    handoff_writer_fleet.cache_clear()


@pytest.fixture()
def isolated_agents_dir(tmp_path, monkeypatch):
    """Build a synthetic ``agents/`` dir from the STAGED gaia-verifier.md
    content plus one decoy agent, and point ``permissions._agents_dir()`` at
    it -- never the live ``agents/`` directory. This is the isolated-fixture
    pattern ``TestSyntheticSeededFleet`` established, applied to the real
    staged file instead of an inline literal.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    staged_text = _STAGED_AGENT.read_text(encoding="utf-8")
    (agents_dir / "gaia-verifier.md").write_text(staged_text, encoding="utf-8")
    (agents_dir / "developer.md").write_text(
        "---\nname: developer\ncontract_handoff_writer: true\n---\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_permissions, "_agents_dir", lambda: agents_dir)
    return agents_dir


# ---------------------------------------------------------------------------
# Registry recognition -- against the ISOLATED fixture, never the live tree
# ---------------------------------------------------------------------------

class TestVerifierAgentRegistryIsolatedFixture:
    def test_staged_file_exists(self):
        assert _STAGED_AGENT.is_file(), (
            f"expected staged agent definition at {_STAGED_AGENT}"
        )

    def test_isolated_verifier_fleet_recognizes_staged_gaia_verifier(
        self, isolated_agents_dir
    ):
        fleet = verifier_fleet()
        assert fleet == frozenset({"gaia-verifier"})
        assert is_verifier("gaia-verifier") is True
        assert is_verifier("developer") is False
        assert is_verifier("rogue-agent") is False

    def test_isolated_handoff_writer_fleet_includes_gaia_verifier(
        self, isolated_agents_dir
    ):
        fleet = handoff_writer_fleet()
        assert "gaia-verifier" in fleet
        assert is_handoff_writer("gaia-verifier") is True


# ---------------------------------------------------------------------------
# Semantic self-check of the staged frontmatter shape (AC-4, point 2)
# ---------------------------------------------------------------------------

class TestStagedFrontmatterShape:
    def _frontmatter(self) -> str:
        text = _STAGED_AGENT.read_text(encoding="utf-8")
        # Frontmatter is the block between the first two '---' delimiters.
        return text.split("---", 2)[1]

    def test_declares_name_gaia_verifier(self):
        assert re.search(r"^name:\s*gaia-verifier\s*$", self._frontmatter(), re.MULTILINE)

    def test_declares_verifier_true_marker(self):
        assert re.search(r"^verifier:\s*true\s*$", self._frontmatter(), re.MULTILINE)

    def test_declares_contract_handoff_writer_true_marker(self):
        assert re.search(
            r"^contract_handoff_writer:\s*true\s*$", self._frontmatter(), re.MULTILINE
        )

    def test_tools_are_read_bash_skill_only(self):
        m = re.search(r"^tools:\s*(.+)$", self._frontmatter(), re.MULTILINE)
        assert m is not None, "no top-level tools: line found"
        tools = [t.strip() for t in m.group(1).split(",")]
        assert tools == ["Read", "Bash", "Skill"]

    def test_disallowed_tools_are_write_edit_notebookedit(self):
        m = re.search(
            r"^disallowedTools:\s*\[(.+)\]\s*$", self._frontmatter(), re.MULTILINE
        )
        assert m is not None, "no top-level disallowedTools: line found"
        disallowed = [t.strip() for t in m.group(1).split(",")]
        assert disallowed == ["Write", "Edit", "NotebookEdit"]

    def test_no_routing_frontmatter_block(self):
        assert not re.search(r"^routing:\s*$", self._frontmatter(), re.MULTILINE), (
            "gaia-verifier must NOT carry a routing: block -- it is dispatched "
            "on NEEDS_VERIFICATION, not by the surface router"
        )


# ---------------------------------------------------------------------------
# Dormancy re-confirmation -- the LIVE agents/ dir is untouched by this task
# ---------------------------------------------------------------------------

class TestLiveRegistryStaysDormant:
    def test_live_agents_dir_yields_empty_verifier_fleet(self):
        # No monkeypatch active here: this resolves the REAL agents/ dir.
        assert verifier_fleet() == frozenset()

    def test_live_agents_dir_has_no_gaia_verifier_file(self):
        assert not (_LIVE_AGENTS_DIR / "gaia-verifier.md").exists()
