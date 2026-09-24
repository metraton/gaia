"""Keep each approval-skill teaching anchored to the runtime symbol that makes it true."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"

# (skill file, literal the skill teaches, runtime file, symbol behind it)
TEACHING_MAP = (
    (
        "subagent-request-approval/SKILL.md", "Every phrase is required",
        "gaia/approvals/core.py", "def missing_phrases",
    ),
    (
        "subagent-request-approval/SKILL.md", "the denial carries",
        "hooks/modules/security/approval_messages.py", "def _phrases_request",
    ),
    (
        "subagent-request-approval/SKILL.md", "Pass no `--session-id` or `--agent-id`",
        "bin/cli/approvals.py", 'os.environ.get("GAIA_HOST_SESSION_ID")',
    ),
    (
        "subagent-request-approval/SKILL.md", "read from the dispatch environment on both hosts",
        "opencode/plugin.ts", "output.env.GAIA_HOST_SESSION_ID = call.sessionID",
    ),
    (
        "subagent-request-approval/SKILL.md", "`--expect-exit POSITION=CODES`",
        "bin/cli/approvals.py", '"--expect-exit"',
    ),
    (
        "orchestrator-present-approval/SKILL.md", "gaia approvals question",
        "bin/cli/approvals.py", "def cmd_question",
    ),
    (
        "orchestrator-present-approval/SKILL.md", "The hook shows each signature's text",
        "hooks/adapters/claude_code.py", "def _adapt_ask_user_question",
    ),
    (
        "orchestrator-present-approval/SKILL.md", "Gaia posts a notice in your session",
        "opencode/plugin.ts", "export function activationNotice",
    ),
    (
        "execution/SKILL.md", "recorded as no result",
        "gaia/approvals/reading.py", 'NO_RESULT = "no_result"',
    ),
    (
        "pending-approvals/SKILL.md", "creates the grant exactly as Approve",
        "bin/cli/approvals.py", "activate_approval_atomically(",
    ),
    (
        "pending-approvals/SKILL.md", "The orchestrator withdraws but never approves",
        "hooks/modules/security/gaia_cli_only_guard.py", '("approvals", "revoke")',
    ),
    (
        "agent-protocol/examples.md", "one approved atomic command per tool call",
        "hooks/modules/tools/bash_validator.py", "def _validate_compound_command",
    ),
    (
        "security-tiers/SKILL.md", "pre-execution policy gate",
        "hooks/adapters/opencode.py", "def adapt_pre_tool_use",
    ),
    (
        "agent-protocol/SKILL.md", "owns request construction",
        "skills/command-execution/SKILL.md", "One command, one result, one exit code",
    ),
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "skill_path,taught,runtime_path,runtime_symbol",
    TEACHING_MAP,
    ids=[f"{row[0]}::{row[3]}" for row in TEACHING_MAP],
)
def test_each_teaching_maps_to_its_runtime_symbol(
    skill_path, taught, runtime_path, runtime_symbol
):
    assert taught in _source(f"skills/{skill_path}")
    assert runtime_symbol in _source(runtime_path)


def test_policy_ownership_is_cross_linked_instead_of_repeated():
    command = (SKILLS / "command-execution/SKILL.md").read_text(encoding="utf-8")
    protocol = (SKILLS / "agent-protocol/SKILL.md").read_text(encoding="utf-8")
    assert "T3 routes to\n   `subagent-request-approval`" in command
    assert "`subagent-request-approval` owns request construction" in protocol
    assert "`execution` owns the\npost-grant lifecycle" in protocol
