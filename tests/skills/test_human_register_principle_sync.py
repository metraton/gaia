"""The orchestrator identity restates human-register's Iron Law word for word."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _REPO_ROOT / "skills" / "human-register" / "SKILL.md"
_IDENTITY = _REPO_ROOT / "agents" / "gaia-orchestrator.md"


def _first_paragraph_under(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(heading)}\n\n(.+?)\n\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{path.name}: no paragraph under {heading!r}"
    return match.group(1).strip()


def test_identity_carries_the_iron_law_verbatim():
    iron_law = _first_paragraph_under(_SKILL, "## Iron Law")
    identity = _first_paragraph_under(_IDENTITY, "## How I speak to a person")
    assert iron_law.startswith("Everything a person reads from Gaia")
    assert identity == iron_law


def test_a_divergent_copy_is_detected(tmp_path):
    iron_law = _first_paragraph_under(_SKILL, "## Iron Law")
    drifted = tmp_path / "identity.md"
    drifted.write_text(
        "## How I speak to a person\n\n" + iron_law.replace("observed", "seen") + "\n\nrest\n",
        encoding="utf-8",
    )
    assert _first_paragraph_under(drifted, "## How I speak to a person") != iron_law
