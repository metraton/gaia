"""AC-9: a specialist dispatched in OpenCode is born naming the skills its definition preloads.

OpenCode 1.18.32 preloads no skill: a child sees only the per-request
``<available_skills>`` list and gets a body only by calling the ``skill`` tool.
So the Task prompt the adapter rewrites must carry each skill the agent's
``skills:`` frontmatter names, within the kernel budget, while Claude Code's
kernel, whose host preloads the bodies itself, stays as it was.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hooks"))

from adapters.opencode import CLOSING_RULES_KERNEL, OpenCodeAdapter
from adapters.opencode_parity import CAPABILITIES, PENDING_GAPS
from adapters.tool_policy import PolicyVerdict, ToolPolicy
from modules.context import kernel_builder
from tools.scan.seed_contract_permissions import _parse_frontmatter

AGENTS_DIR = REPO_ROOT / "agents"
SKILLS_DIR = REPO_ROOT / "skills"


def _frontmatter(path: Path) -> dict:
    return _parse_frontmatter(path.read_text(encoding="utf-8"))


def _preloading_agents() -> list[str]:
    return sorted(
        path.stem for path in AGENTS_DIR.glob("*.md")
        if _frontmatter(path).get("skills")
    )


def _declared_skills(agent: str) -> list[str]:
    return list(_frontmatter(AGENTS_DIR / f"{agent}.md")["skills"])


def _description(skill: str) -> str:
    return " ".join(str(_frontmatter(SKILLS_DIR / skill / "SKILL.md")["description"]).split())


def _row(prompt: str) -> dict:
    return {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "dispatch_prompt": prompt,
        "kernel_sections": json.dumps({"role": "primary", "surface": "gaia_system"}),
    }


def _dispatched_prompt(monkeypatch, agent: str, prompt: str = "do the thing") -> str:
    monkeypatch.setattr(
        OpenCodeAdapter, "_is_attested_control_plane", classmethod(lambda cls, event: True),
    )
    monkeypatch.setattr(ToolPolicy, "pre_tool_verdict", lambda _self, event, **_kw: PolicyVerdict())
    monkeypatch.setattr("gaia.store.writer.claim_dispatch_row", lambda **kwargs: _row(prompt))
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-parent",
        "callID": "call-task-1",
        "tool": "task",
        "args": {"description": f"dispatch {agent}", "prompt": prompt, "subagent_type": agent},
    }))
    return OpenCodeAdapter().adapt_pre_tool_use(event).output["updated_input"]["prompt"]


def test_specialist_skills_shipped_agents_preload_skills():
    assert "gaia-system" in _preloading_agents()
    assert "developer" in _preloading_agents()


@pytest.mark.parametrize("agent", _preloading_agents())
def test_specialist_skills_every_declared_skill_rides_the_dispatch_kernel(monkeypatch, agent):
    prompt = _dispatched_prompt(monkeypatch, agent)
    block = prompt[prompt.index(kernel_builder.SKILLS_HEADING):prompt.index(CLOSING_RULES_KERNEL)]

    for skill in _declared_skills(agent):
        assert f"- {skill}: {_description(skill)}" in block
    assert prompt.index("# Your Contract") < prompt.index(kernel_builder.SKILLS_HEADING)


@pytest.mark.parametrize("agent", _preloading_agents())
def test_specialist_skills_block_fits_the_kernel_budget_inlined_bodies_do_not(agent):
    block = kernel_builder.build_skills_block(agent)
    bodies = sum(
        len((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
        for skill in _declared_skills(agent)
    )

    assert 0 < len(block) <= kernel_builder.SKILLS_BLOCK_BUDGET
    assert bodies > kernel_builder.SKILLS_BLOCK_BUDGET


def test_specialist_skills_over_budget_keeps_every_name(tmp_path, monkeypatch):
    agents_dir, skills_dir = tmp_path / "agents", tmp_path / "skills"
    agents_dir.mkdir()
    names = [f"skill-{index}" for index in range(5)]
    (agents_dir / "wide.md").write_text(
        "---\nname: wide\nskills:\n" + "".join(f"  - {n}\n" for n in names) + "---\n",
        encoding="utf-8",
    )
    for name in names:
        (skills_dir / name).mkdir(parents=True)
        (skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {'long ' * 400}\n---\n", encoding="utf-8",
        )
    monkeypatch.setattr(kernel_builder, "SKILLS_BLOCK_BUDGET", 1_000)

    block = kernel_builder.build_skills_block("wide", agents_dir=agents_dir, skills_dir=skills_dir)

    assert len(block) <= 1_000
    for name in names:
        assert f"- {name}\n" in block + "\n"


def test_specialist_skills_absent_for_an_agent_that_preloads_none(monkeypatch):
    prompt = _dispatched_prompt(monkeypatch, "no-such-agent")

    assert kernel_builder.SKILLS_HEADING not in prompt
    assert prompt.endswith(CLOSING_RULES_KERNEL)


def test_specialist_skills_claude_code_kernel_is_unchanged(monkeypatch):
    monkeypatch.setattr(kernel_builder, "build_memory_block", lambda **_kw: "")
    kernel = kernel_builder.build_kernel_context(_row("do the thing"), agent_name="gaia-system")

    assert kernel_builder.SKILLS_HEADING not in kernel


def _headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("# ")]


def test_specialist_skills_task_prompt_carries_the_claude_code_kernel(monkeypatch):
    monkeypatch.setattr(
        kernel_builder, "build_memory_block",
        lambda **_kw: f"{kernel_builder.MEMORY_HEADING}\n\n- a standing rule",
    )
    prompt = _dispatched_prompt(monkeypatch, "gaia-system")

    assert kernel_builder.build_kernel_context(_row("do the thing"), agent_name="gaia-system") in prompt
    assert _headers(prompt) == [
        kernel_builder.KERNEL_HEADING,
        kernel_builder.CLI_HEADING,
        kernel_builder.MEMORY_HEADING,
        kernel_builder.SKILLS_HEADING,
        "# Closing this turn",
    ]


def test_specialist_skills_survive_child_compaction(monkeypatch):
    monkeypatch.setattr(
        "gaia.store.writer.find_dispatch_row_by_harness_agent_id",
        lambda session_id, **_kw: {**_row("do the thing"), "claimed_at": "2026-10-01T00:00:00Z"},
    )
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "session.compacting", "sessionID": "ses-child", "agent": "gaia-system",
    }))

    context = "\n\n".join(OpenCodeAdapter().adapt_pre_compact(event).output["updated_input"]["context"])

    assert kernel_builder.build_skills_block("gaia-system") in context
    assert _headers(context)[:2] == [kernel_builder.KERNEL_HEADING, kernel_builder.CLI_HEADING]


def test_specialist_skills_parity_alarm_no_longer_holds_the_gap():
    capability = next(c for c in CAPABILITIES if c.name == "skills at birth")

    assert "skills at birth" not in PENDING_GAPS
    assert capability.opencode_event == "tool.execute.before"
