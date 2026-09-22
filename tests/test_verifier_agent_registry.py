"""Registry and frontmatter-shape tests for the gaia-verifier agent definition.

The staged copy under ``tests/fixtures/agents_staging/`` is exercised through
an isolated ``agents/`` directory, never the live tree.
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
)

_STAGED_AGENT = _REPO_ROOT / "tests" / "fixtures" / "agents_staging" / "gaia-verifier.md"
_LIVE_AGENTS_DIR = _REPO_ROOT / "agents"


@pytest.fixture(autouse=True)
def _clean_caches():
    """The handoff-writer fleet is lru_cache'd; each test starts clean."""
    handoff_writer_fleet.cache_clear()
    yield
    handoff_writer_fleet.cache_clear()


@pytest.fixture()
def isolated_agents_dir(tmp_path, monkeypatch):
    """A synthetic ``agents/`` dir holding the staged gaia-verifier.md plus a decoy."""
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


class TestVerifierAgentRegistryIsolatedFixture:
    def test_staged_file_exists(self):
        assert _STAGED_AGENT.is_file(), (
            f"expected staged agent definition at {_STAGED_AGENT}"
        )

    def test_live_agent_file_exists(self):
        assert (_LIVE_AGENTS_DIR / "gaia-verifier.md").exists()

    def test_isolated_handoff_writer_fleet_includes_gaia_verifier(
        self, isolated_agents_dir
    ):
        fleet = handoff_writer_fleet()
        assert "gaia-verifier" in fleet
        assert is_handoff_writer("gaia-verifier") is True


class TestStagedFrontmatterShape:
    def _frontmatter(self) -> str:
        text = _STAGED_AGENT.read_text(encoding="utf-8")
        return text.split("---", 2)[1]

    def test_declares_name_gaia_verifier(self):
        assert re.search(r"^name:\s*gaia-verifier\s*$", self._frontmatter(), re.MULTILINE)

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
