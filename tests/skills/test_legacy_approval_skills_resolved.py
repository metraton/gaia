"""
T4.4 — Audit: legacy approval skill names are resolved.

Asserts that no ACTIVE skill or agent content references the legacy names
Load Skill('request-approval') or Load Skill('orchestrator-approval').

Gaia 5 decision: the legacy skills were renamed to
'subagent-request-approval' / 'orchestrator-present-approval' and the old
directories were deleted outright -- there are no forward-pointer stubs.
ALLOWED_LEGACY_STUBS is retained only as a (now-empty in practice) skip-set
guarding against a stub being re-introduced; the directories no longer exist.
"""

import re
from pathlib import Path

# Repo-relative paths
REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
AGENTS_DIR = REPO_ROOT / "agents"

# Stub files -- these are the ONLY files allowed to mention the legacy names
ALLOWED_LEGACY_STUBS = {
    SKILLS_DIR / "request-approval" / "SKILL.md",
    SKILLS_DIR / "orchestrator-approval" / "SKILL.md",
}

# Legacy skill names that must not appear in active (non-stub) content
LEGACY_SKILL_NAMES = [
    "request-approval",
    "orchestrator-approval",
]

# Pattern to detect skill invocations like Load Skill('request-approval')
# or Skill("orchestrator-approval") in content
_SKILL_INVOCATION_PATTERN = re.compile(
    r"""[Ll]oad\s+[Ss]kill\(['"](%s)['"]\)|[Ss]kill\(['"](%s)['"]\)"""
    % (
        "|".join(re.escape(n) for n in LEGACY_SKILL_NAMES),
        "|".join(re.escape(n) for n in LEGACY_SKILL_NAMES),
    )
)

# Pattern for frontmatter 'name:' field referencing legacy names (active skills only)
_FRONTMATTER_NAME_PATTERN = re.compile(
    r"^name:\s*(%s)\s*$" % "|".join(re.escape(n) for n in LEGACY_SKILL_NAMES),
    re.MULTILINE,
)


def _collect_md_files(directory: Path) -> list[Path]:
    """Collect all .md files under a directory recursively."""
    if not directory.exists():
        return []
    return list(directory.rglob("*.md"))


def test_no_active_skill_invokes_legacy_names():
    """
    No active skill file (outside the two stubs) contains
    Load Skill('request-approval') or Load Skill('orchestrator-approval').

    Stubs are allowed -- they are the forward-pointer files.
    Other files referencing these names represent unresolved drift.
    """
    skill_files = _collect_md_files(SKILLS_DIR)
    violations = []

    for path in skill_files:
        if path in ALLOWED_LEGACY_STUBS:
            continue  # stubs are the exception
        content = path.read_text(encoding="utf-8")
        matches = _SKILL_INVOCATION_PATTERN.findall(content)
        # findall returns tuples of groups; flatten and filter empty strings
        matched_names = [m for group in matches for m in group if m]
        if matched_names:
            violations.append(
                f"{path.relative_to(REPO_ROOT)}: references {matched_names}"
            )

    assert not violations, (
        "Active skill files reference legacy approval skill names.\n"
        "Update these to use 'subagent-request-approval' or "
        "'orchestrator-present-approval' instead:\n"
        + "\n".join(violations)
    )


def test_no_agent_invokes_legacy_names():
    """
    No agent definition invokes legacy skill names via Load Skill(...)
    in its body (outside frontmatter skills list).
    """
    agent_files = _collect_md_files(AGENTS_DIR)
    violations = []

    for path in agent_files:
        content = path.read_text(encoding="utf-8")
        matches = _SKILL_INVOCATION_PATTERN.findall(content)
        matched_names = [m for group in matches for m in group if m]
        if matched_names:
            violations.append(
                f"{path.relative_to(REPO_ROOT)}: references {matched_names}"
            )

    assert not violations, (
        "Agent definitions reference legacy approval skill names.\n"
        "Update these to use 'subagent-request-approval' or "
        "'orchestrator-present-approval' instead:\n"
        + "\n".join(violations)
    )


def test_legacy_approval_skills_are_fully_removed():
    """
    The legacy approval skills are deleted outright -- not even as stubs.

    User decision (Gaia 5): ``request-approval`` and ``orchestrator-approval``
    were renamed to ``subagent-request-approval`` and
    ``orchestrator-present-approval`` and the old directories were removed
    entirely. There is no forward-pointer stub to maintain; the skill
    resolver and routing now reference only the new names. This test pins
    that the legacy directories do not exist (and must not be re-created as
    stubs).
    """
    legacy_dirs = [
        SKILLS_DIR / "request-approval",
        SKILLS_DIR / "orchestrator-approval",
    ]
    for legacy_dir in legacy_dirs:
        assert not legacy_dir.exists(), (
            f"Legacy approval skill {legacy_dir.relative_to(REPO_ROOT)} must be "
            "fully removed (renamed to the 'subagent-'/'present-' variant); "
            "do not re-create it as a stub."
        )


def test_new_skills_exist():
    """
    The renamed skill directories exist and contain SKILL.md files.
    """
    new_skills = [
        SKILLS_DIR / "subagent-request-approval" / "SKILL.md",
        SKILLS_DIR / "orchestrator-present-approval" / "SKILL.md",
        SKILLS_DIR / "agent-approval-protocol" / "SKILL.md",
    ]
    for skill_path in new_skills:
        assert skill_path.exists(), f"New skill file missing: {skill_path}"
        content = skill_path.read_text(encoding="utf-8")
        # Must have frontmatter (starts with ---)
        assert content.startswith("---"), (
            f"{skill_path} missing frontmatter (must start with ---)"
        )
        # Must NOT be marked deprecated (these are the active skills)
        assert "deprecated: true" not in content, (
            f"{skill_path} is marked deprecated but should be active"
        )


def test_agent_protocol_unchanged():
    """
    The agent-protocol/SKILL.md (the generic response contract) is NOT renamed
    and does not contain approval-only content.

    Confirms D12 Function C constraint: agent-protocol is foundational and must
    not be scoped to approvals.
    """
    agent_protocol_path = SKILLS_DIR / "agent-protocol" / "SKILL.md"
    assert agent_protocol_path.exists(), "agent-protocol/SKILL.md must still exist"

    content = agent_protocol_path.read_text(encoding="utf-8")

    # Must still be named agent-protocol in frontmatter
    assert "name: agent-protocol" in content, (
        "agent-protocol/SKILL.md frontmatter name must remain 'agent-protocol'"
    )

    # Must NOT be marked deprecated
    assert "deprecated: true" not in content, (
        "agent-protocol/SKILL.md must not be deprecated -- it is the universal contract"
    )
