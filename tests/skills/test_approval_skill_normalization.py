"""Keep approval skills aligned with the current typed consent runtime."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"

FINDING_MAP = (
    (
        1, "orchestrator-present-approval/SKILL.md", "Select the host modality",
        "opencode/plugin.ts", "function activationNotice",
    ),
    (
        2, "orchestrator-present-approval/SKILL.md",
        "Concrete event and tool spellings live in `reference.md`",
        "skills/orchestrator-present-approval/reference.md", "question.asked",
    ),
    (
        3, "orchestrator-present-approval/template.md", "OpenCode may carry the same",
        "opencode/plugin.ts", "function signatureRequest",
    ),
    (
        4, "orchestrator-present-approval/template.md", "Eight visible fields",
        "hooks/adapters/consent_presentation.py", '("window", "WINDOW"',
    ),
    (
        5, "orchestrator-present-approval/reference.md",
        "complete canonical `[P-<32 lowercase hex>]`",
        "hooks/modules/security/approval_grants.py",
        "def extract_approval_id_from_label",
    ),
    (
        6, "subagent-request-approval/SKILL.md", "persisted row is the delivery",
        "hooks/adapters/claude_code.py", "def _resolve_subagent_stop_gate_full",
    ),
    (
        7, "agent-protocol/examples.md", "one approved atomic command per tool call",
        "hooks/modules/tools/bash_validator.py", "def _validate_compound_command",
    ),
    (
        8, "security-tiers/SKILL.md", "pre-execution policy gate",
        "hooks/adapters/opencode.py", "def adapt_pre_tool_use",
    ),
    (
        9, "subagent-request-approval/SKILL.md", "persisted but not returned",
        "bin/cli/approvals.py", "def cmd_request_set",
    ),
    (
        10, "subagent-request-approval/reference.md",
        "Reactive singular command and protected-path FILE_WRITE retries are supported",
        "hooks/adapters/opencode.py",
        '"COMMAND_SET", "SCOPE_SEMANTIC_SIGNATURE", "SCOPE_FILE_PATH"',
    ),
    (
        11, "orchestrator-present-approval/SKILL.md",
        "activation creates the matching grant", "bin/cli/approvals.py",
        "def cmd_approve",
    ),
    (
        12, "agent-approval-protocol/SKILL.md", "legacy\n  compatibility parser",
        "hooks/modules/security/approval_constants.py",
        "The APPROVE: prefix is a legacy path",
    ),
    (
        13, "agent-protocol/SKILL.md", "owns request construction",
        "skills/command-execution/SKILL.md", "One command, one result, one exit code",
    ),
)


def _text(relative: str) -> str:
    return (SKILLS / relative).read_text(encoding="utf-8")


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "finding,skill_path,changed_literal,runtime_path,runtime_symbol",
    FINDING_MAP,
    ids=lambda value: f"finding-{value}" if isinstance(value, int) else None,
)
def test_each_audit_finding_maps_to_changed_teaching_and_runtime(
    finding, skill_path, changed_literal, runtime_path, runtime_symbol
):
    source_path = (
        skill_path if skill_path.startswith("skills/") else f"skills/{skill_path}"
    )
    assert changed_literal in _source(source_path), finding
    assert runtime_symbol in _source(runtime_path), finding


def test_presenter_selects_modality_before_any_ask_imperative():
    skill = _text("orchestrator-present-approval/SKILL.md")
    selection = skill.index("Select the host modality")
    assert selection < skill.index("On Claude Code, presentation")
    assert "attempt provokes the host-managed question" in skill
    assert "resuming that same specialist session by `task_id`" in skill


def test_template_is_host_qualified_and_lists_every_visible_field():
    template = _text("orchestrator-present-approval/template.md")
    assert "OpenCode may carry the same\nrendered `visible_text`" in template
    assert "Eight visible fields" in template
    for label in (
        "OPERATION", "COMMANDS", "SCOPE", "IMPACT", "RISK", "ROLLBACK",
        "VERIFICATION", "WINDOW",
    ):
        assert label in template


def test_current_identity_is_full_canonical_and_legacy_forms_are_legacy():
    protocol = _text("agent-approval-protocol/SKILL.md")
    presenter = _text("orchestrator-present-approval/reference.md")
    assert "current refusal surfaces emit" in protocol
    assert "legacy\n  compatibility parser" in protocol
    assert "complete canonical `[P-<32 lowercase hex>]`" in presenter
    assert "8-character nonce tag" not in presenter


def test_retired_fence_and_compound_minting_cannot_return():
    producer = _text("subagent-request-approval/SKILL.md")
    protocol = _text("agent-approval-protocol/SKILL.md")
    examples = _text("agent-protocol/examples.md")
    assert "compatibility fence" not in producer
    assert "fenced contract" not in protocol
    assert "hook-minted" not in examples
    assert "one approved atomic command per tool call" in examples


def test_common_skills_use_host_neutral_gate_vocabulary():
    for relative in (
        "security-tiers/SKILL.md",
        "subagent-request-approval/SKILL.md",
        "agent-approval-protocol/SKILL.md",
    ):
        content = _text(relative)
        assert "PreToolUse returns `[T3_BLOCKED]`" not in content
        assert "pre-execution policy gate" in content


def test_request_set_output_readback_and_informed_consent_fields_are_taught():
    producer = _text("subagent-request-approval/SKILL.md")
    reference = _text("subagent-request-approval/reference.md")
    assert "returns `status`, the\ncanonical `approval_id`, and `command_set`" in producer
    assert "gaia approvals list\n--json" in producer
    assert "--verification '<desired-state check after execution>'" in reference
    assert "--rollback '<how to undo the effect>'" in reference


def test_reactive_typed_support_and_proactive_preference_are_explicit():
    reference = _text("subagent-request-approval/reference.md")
    assert "Reactive singular command and protected-path FILE_WRITE retries are supported" in reference
    assert "`SCOPE_SEMANTIC_SIGNATURE`" in reference
    assert "`SCOPE_FILE_PATH`" in reference
    assert "Request-set-of-one remains the preferred proactive path" in reference


def test_admin_approve_grants_but_orchestrator_authority_remains_denied():
    skill = _text("orchestrator-present-approval/SKILL.md")
    reference = _text("orchestrator-present-approval/reference.md")
    assert "shared typed\nactivation creates the matching grant" in skill
    assert "categorically denies that authority boundary" in reference
    assert "does **not** create a hook-side grant" not in skill + reference


def test_policy_ownership_is_cross_linked_instead_of_repeated():
    command = _text("command-execution/SKILL.md")
    protocol = _text("agent-protocol/SKILL.md")
    assert "T3 routes to\n   `subagent-request-approval`" in command
    assert "`subagent-request-approval` owns request construction" in protocol
    assert "`execution` owns the\npost-grant lifecycle" in protocol
    assert "first non-zero or mismatched result ends execution" in command
    assert "terminal/frozen `FAILED`" in command
    assert "completed indexes, the failed index, and untouched indexes" in protocol
    assert "never evidence that execution\nhappened" in protocol


def test_teaching_is_anchored_to_current_runtime_symbols():
    approvals = _source("bin/cli/approvals.py")
    opencode = _source("hooks/adapters/opencode.py")
    presentation = _source("hooks/adapters/consent_presentation.py")
    constants = _source("hooks/modules/security/approval_constants.py")
    guard = _source("hooks/modules/security/gaia_cli_only_guard.py")

    assert "def cmd_request_set(" in approvals
    result_line = next(line for line in approvals.splitlines() if "result = {" in line)
    assert '"command_set": items' in result_line
    assert '"request_fingerprint"' not in result_line
    assert "def cmd_approve(" in approvals
    assert "activate_approval_atomically(" in approvals
    assert '"COMMAND_SET", "SCOPE_SEMANTIC_SIGNATURE", "SCOPE_FILE_PATH"' in opencode
    assert '("window", "WINDOW"' in presentation
    assert "The APPROVE: prefix is a legacy path" in constants
    assert '("approvals", "approve")' in guard
