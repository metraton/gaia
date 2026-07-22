"""Unit tests for the verifier fleet registry (gaia.state.permissions).

Brief: harness-r2-needs-verification-y-complete-restringido-por-rol-verificador
(plan_id=32, task order_num=3, AC-4).

Coverage mirrors the existing ``handoff_writer_fleet`` / ``is_handoff_writer``
suite (tests/contract/test_finalize_store.py) in MECHANISM, plus the ONE
deliberate difference this brief specifies: the fleet is EMPTY today (no
shipped agent carries the ``verifier: true`` marker), unlike the
handoff-writer fleet's non-empty fallback floor.

  * parser reads the top-level ``verifier:`` marker and ignores nested blocks.
  * ``verifier_fleet()`` is empty against the REAL ``agents/`` directory today
    (positive proof no agent has opted in yet -- the B3 premise).
  * ``verifier_fleet()`` also falls back to the (empty) floor when ``agents/``
    is unresolvable -- same fallback-floor MECHANISM as the handoff-writer
    fleet, just an empty constant.
  * ``is_verifier`` never fails open: absent from an empty fleet -> False;
    absent from a populated synthetic fleet -> False; present -> True.
  * caching: ``verifier_fleet.cache_clear()`` lets a test observe a synthetic
    ``agents/`` directory instead of the real one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import permissions as _permissions  # noqa: E402
from gaia.state.permissions import (  # noqa: E402
    _parse_agent_verifier_frontmatter,
    is_verifier,
    verifier_fleet,
)

_AGENTS_DIR = _REPO_ROOT / "agents"


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with a fresh fleet cache (it is lru_cache'd)."""
    verifier_fleet.cache_clear()
    yield
    verifier_fleet.cache_clear()


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

class TestParseAgentVerifierFrontmatter:
    def test_true_marker_is_recognized(self):
        text = "---\nname: some-verifier\nverifier: true\n---\nBody.\n"
        name, is_v = _parse_agent_verifier_frontmatter(text)
        assert name == "some-verifier"
        assert is_v is True

    def test_absent_marker_defaults_false(self):
        text = "---\nname: developer\ndescription: builds things\n---\nBody.\n"
        name, is_v = _parse_agent_verifier_frontmatter(text)
        assert name == "developer"
        assert is_v is False

    def test_false_marker_is_false(self):
        text = "---\nname: some-agent\nverifier: false\n---\nBody.\n"
        _, is_v = _parse_agent_verifier_frontmatter(text)
        assert is_v is False

    def test_nested_marker_under_another_key_is_ignored(self):
        """A `verifier:` key nested under another block (indented) must NOT
        be read as the top-level marker -- mirrors the routing:/nested-key
        exclusion the handoff-writer parser already guards against."""
        text = (
            "---\n"
            "name: some-agent\n"
            "routing:\n"
            "  verifier: true\n"
            "---\n"
            "Body.\n"
        )
        _, is_v = _parse_agent_verifier_frontmatter(text)
        assert is_v is False

    def test_no_frontmatter_block_returns_none_false(self):
        name, is_v = _parse_agent_verifier_frontmatter("No frontmatter here.\n")
        assert name is None
        assert is_v is False


# ---------------------------------------------------------------------------
# verifier_fleet() against the REAL agents/ dir -- empty today (B3 premise)
# ---------------------------------------------------------------------------

class TestVerifierFleetEmptyToday:
    def test_real_agents_dir_yields_empty_fleet(self):
        """No agent under agents/*.md carries `verifier: true` as of this
        brief -- the registry mechanism ships now; population is B3's job."""
        fleet = verifier_fleet()
        assert fleet == frozenset()

    def test_no_shipped_agent_is_a_verifier(self):
        for md in sorted(_AGENTS_DIR.glob("*.md")):
            if md.name.lower() == "readme.md":
                continue
            name, _ = _parse_agent_verifier_frontmatter(md.read_text(encoding="utf-8"))
            if name:
                assert is_verifier(name) is False, f"{name} unexpectedly verified"


# ---------------------------------------------------------------------------
# Fallback-floor mechanism (unresolvable agents/ dir) -- mirrors the
# handoff-writer precedent structurally; the floor constant itself is empty.
# ---------------------------------------------------------------------------

class TestFallbackFloor:
    def test_unresolvable_agents_dir_falls_back_to_empty_floor(self, monkeypatch):
        monkeypatch.setattr(_permissions, "_agents_dir", lambda: None)
        assert verifier_fleet() == _permissions._FALLBACK_VERIFIER_FLEET
        assert verifier_fleet() == frozenset()

    def test_never_fails_open_on_unresolvable_dir(self, monkeypatch):
        monkeypatch.setattr(_permissions, "_agents_dir", lambda: None)
        assert is_verifier("any-agent-name") is False


# ---------------------------------------------------------------------------
# Synthetic fleet -- proves the mechanism DOES seed correctly once an agent
# opts in (exercised against a temp agents/ dir, not the real shipped set).
# ---------------------------------------------------------------------------

class TestSyntheticSeededFleet:
    def test_synthetic_verifier_agent_is_seeded_and_recognized(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "gaia-verifier.md").write_text(
            "---\nname: gaia-verifier\nverifier: true\n---\nBody.\n",
            encoding="utf-8",
        )
        (agents_dir / "developer.md").write_text(
            "---\nname: developer\n---\nBody.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_permissions, "_agents_dir", lambda: agents_dir)
        fleet = verifier_fleet()
        assert fleet == frozenset({"gaia-verifier"})
        assert is_verifier("gaia-verifier") is True
        assert is_verifier("developer") is False
        assert is_verifier("rogue-agent") is False

    def test_readme_md_is_skipped(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "README.md").write_text(
            "---\nname: not-a-real-agent\nverifier: true\n---\nBody.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_permissions, "_agents_dir", lambda: agents_dir)
        assert verifier_fleet() == frozenset()


# ---------------------------------------------------------------------------
# is_verifier() never-fails-open contract
# ---------------------------------------------------------------------------

class TestIsVerifierNeverFailsOpen:
    def test_empty_string_agent_is_false(self):
        assert is_verifier("") is False

    def test_none_like_falsy_agent_is_false(self):
        assert is_verifier(None) is False  # type: ignore[arg-type]

    def test_unseeded_agent_against_real_fleet_is_false(self):
        assert is_verifier("gaia-system") is False
        assert is_verifier("developer") is False
