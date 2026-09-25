"""Regression checks for the orchestrator's dispatch-input boundary."""

from pathlib import Path


ORCHESTRATOR = (
    Path(__file__).resolve().parents[2] / "agents" / "gaia-orchestrator.md"
)


def test_dispatch_guidance_requires_raw_goal_without_kernel_blocks():
    content = ORCHESTRATOR.read_text(encoding="utf-8")
    dispatch = content.split("## Dispatch", 1)[1].split(
        "## How I close the work", 1
    )[0]

    assert "Sends only raw goal and context." in dispatch
    assert "never embeds `# Your Contract` or contract-closing rules" in dispatch
    assert "fresh and resumed turns" in dispatch
    assert "no kernel is re-injected" not in dispatch


def test_cli_guidance_names_the_missing_path_fallback_beside_the_published_path():
    content = ORCHESTRATOR.read_text(encoding="utf-8")
    paragraph = next(
        p for p in content.split("\n\n")
        if "the absolute path the session's own `## What I can run here` block publishes" in p
    )

    assert "publishes no `gaia CLI:` line" in paragraph
    assert "rather than guess a bare `gaia`" in paragraph
