"""The planner skill teaches a plan-body route the planner agent can take.

``agents/gaia-planner.md`` disallows Write and Edit, so a skill that tells it
to write the plan into a scratch file first teaches a step the agent cannot
perform. Every plan-body example in ``skills/gaia-planner`` must read the body
from stdin (``--content-file=-``) instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT = _REPO_ROOT / "agents" / "gaia-planner.md"
_SKILL_DIR = _REPO_ROOT / "skills" / "gaia-planner"


def _disallowed_tools() -> set[str]:
    frontmatter = _AGENT.read_text(encoding="utf-8").split("---")[1]
    return set(yaml.safe_load(frontmatter).get("disallowedTools") or [])


def _skill_texts() -> dict[str, str]:
    return {name: (_SKILL_DIR / name).read_text(encoding="utf-8")
            for name in ("SKILL.md", "reference.md")}


def test_the_planner_agent_still_cannot_write_files():
    assert {"Write", "Edit"} <= _disallowed_tools()


def test_every_plan_body_example_reads_stdin():
    examples = [
        (name, line) for name, text in _skill_texts().items()
        for line in re.findall(r"gaia plan (?:save|change apply)[^\n`]*", text)
        if "--content-file" in line
    ]
    assert examples, "the skill documents no plan-body example at all"
    for name, line in examples:
        assert "--content-file=-" in line, f"{name}: {line}"


def test_the_skill_never_routes_the_plan_body_through_a_file_tool():
    for name, text in _skill_texts().items():
        assert "Write tool" not in text, name
        assert "scratch/<contract_id>.md" not in text, name
