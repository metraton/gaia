"""The approval-cycle skills teach the mechanism the code runs, once, for both hosts.

Each check pins a teaching to the code that makes it true -- a limit to the
renderer constant, a state to the reader's token, a verb to the CLI or the
orchestrator guard -- so a change on either side fails here instead of leaving
an agent to follow a mechanism that no longer exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gaia.approvals import reading, surface

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
AGENTS = ROOT / "agents"

DELETED = (
    "agent-approval-protocol",
    "claude-code-consent-adapter",
    "orchestrator-present-approval/template.md",
)

# The retired mechanism: the approval id inside the answer label, the Stop
# sweep that marked running calls failed, and the 5 and 60 minute windows.
RETIRED = {
    "id in the label": re.compile(
        r"\[P-<|approve_label|extract_approval_id_from_label|render_approve_label"
    ),
    "Stop sweep": re.compile(r"(?i)reconcil\w* (at|on) stop|stop (sweep|reconcil)"),
    "5 or 60 minute window": re.compile(r"(?i)\b(5|60|five|sixty)[- ]?min(ute)?s?\b"),
    "printed consent surface": re.compile(r"--consent-surface|visible_text"),
}

# Where a deleted name may still appear: the history, a stored plan fixture
# quoting the plan, and this file.
NAME_SCAN_EXEMPT = {
    Path("CHANGELOG.md"),
    Path("tests/hooks/modules/tools/fixtures/real_plan_body.md"),
    Path("tests/skills/test_approval_cycle_skills.py"),
}
NAME_SCAN_ROOTS = ("agents", "bin", "build", "gaia", "hooks", "opencode", "skills", "tests")

PRODUCER = SKILLS / "subagent-request-approval" / "SKILL.md"
PRESENTER = SKILLS / "orchestrator-present-approval" / "SKILL.md"
EXECUTION = SKILLS / "execution" / "SKILL.md"
PENDING = SKILLS / "pending-approvals" / "SKILL.md"
CYCLE_SKILLS = (
    "subagent-request-approval", "orchestrator-present-approval", "execution", "pending-approvals",
)

# What D23 retired from the signature itself: systemMessage as the way it
# reaches the user, and command paths abbreviated with '...'.
RETIRED_FROM_THE_SIGNATURE = {
    "systemMessage as the signature route": re.compile(r"systemMessage"),
    "abbreviated path": re.compile(r"\.\.\./|/\.\.\.|\w\.\.\.(/|\s)"),
}


def _skill_docs() -> list[Path]:
    return sorted(SKILLS.rglob("*.md"))


def _is_deleted(path: Path) -> bool:
    relative = path.relative_to(SKILLS).as_posix()
    return any(relative == name or relative.startswith(f"{name}/") for name in DELETED)


@pytest.mark.parametrize("name", DELETED)
def test_approval_cycle_skills_deleted_by_the_design_are_gone(name):
    assert not (SKILLS / name).exists(), f"skills/{name} still exists"


def test_approval_cycle_skills_nothing_names_a_deleted_skill():
    names = ("agent-approval-protocol", "claude-code-consent-adapter", "template.md")
    offenders = []
    for root in NAME_SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*")):
            relative = path.relative_to(ROOT)
            if (
                not path.is_file()
                or path.suffix not in {".md", ".py", ".ts", ".json"}
                or relative in NAME_SCAN_EXEMPT
                or "__pycache__" in path.parts
                or (root == "skills" and _is_deleted(path))
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders += [f"{relative}: {name}" for name in names if name in text]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("mechanism", sorted(RETIRED))
def test_approval_cycle_skills_teach_no_retired_mechanism(mechanism):
    pattern = RETIRED[mechanism]
    offenders = [
        f"{doc.relative_to(ROOT)}: {match.group(0)}"
        for doc in [*_skill_docs(), *sorted(AGENTS.glob("*.md"))]
        for match in [pattern.search(doc.read_text(encoding="utf-8"))]
        if match
    ]
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "flag,limit",
    [
        ("--what", surface.TITLE_MAX),
        ("--question", surface.QUESTION_MAX),
        ("--does", surface.LINE_MAX),
        ("--impact", surface.LINE_MAX),
    ],
)
def test_approval_cycle_skills_producer_states_each_phrase_limit(flag, limit):
    rows = [line for line in PRODUCER.read_text(encoding="utf-8").splitlines()
            if line.startswith("|") and f"`{flag}`" in line]
    assert rows, f"no table row teaches {flag}"
    assert any(re.search(rf"\b{limit}\b", row) for row in rows), rows


@pytest.mark.parametrize("retired", sorted(RETIRED_FROM_THE_SIGNATURE))
def test_approval_cycle_skills_teach_no_signature_mechanism_d23_retired(retired):
    pattern = RETIRED_FROM_THE_SIGNATURE[retired]
    offenders = [
        f"{doc.relative_to(ROOT)}: {match.group(0)!r}"
        for name in CYCLE_SKILLS
        for doc in sorted((SKILLS / name).glob("*.md"))
        for match in [pattern.search(doc.read_text(encoding="utf-8"))]
        if match
    ]
    assert not offenders, "\n".join(offenders)


def test_approval_cycle_skills_producer_carries_the_d12_example_with_exact_commands():
    from gaia.approvals import surface

    producer = PRODUCER.read_text(encoding="utf-8")
    command = (
        "python3 /home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia"
        " dev --workspace /home/jorge/ws/me --ref bbc2f09 --host all"
    )
    for phrase in (
        "Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos.",
        "¿Reinstalo Gaia?",
        "gaia dev: instala en tu espacio de trabajo la versión nueva de main.",
        "actualiza tu base de datos; ese cambio no se deshace.",
    ):
        assert phrase in producer, phrase
    shown = surface.render({
        "what": "Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos.",
        "question": "¿Reinstalo Gaia?",
        "requested_by": {"agent_id": "gaia-system"},
        "items": [{"command": command}],
    }, "P-" + "0" * 32).block
    assert shown in producer, shown


def test_approval_cycle_skills_producer_carries_the_d27_example_with_its_folder():
    producer = PRODUCER.read_text(encoding="utf-8")
    shown = surface.render({
        "what": "Publicar la rama de prueba y abrir su pull request.",
        "question": "¿Publico la rama y abro el PR?",
        "requested_by": {"agent_id": "developer"},
        "requested_from": "/home/jorge/ws/me",
        "items": [
            {"command": "git push origin feature/demo-login", "cwd": "/home/jorge/ws/me/demo-repo"},
            {
                "command": (
                    "gh pr create --repo metraton/demo --base main --head feature/demo-login"
                    ' --title "Login de prueba" --body-file /home/jorge/.gaia/scratch/demo-1234/pr.md'
                ),
                "cwd": "/home/jorge/ws/me",
            },
        ],
    }, "P-" + "0" * 32).block
    assert shown in producer, shown


# D29: the block is Gaia's and shown outside the question, so no skill may
# teach that the signature travels inside the question text.
SIGNATURE_INSIDE_THE_QUESTION = re.compile(
    r"(?i)question text[^.|]*(carries|holds|contains)|carries its (signature|details)"
    r"|(signature|block)[^.|]*inside the question"
)


def test_approval_cycle_skills_no_skill_puts_the_signature_inside_the_question():
    offenders = [
        f"{doc.relative_to(ROOT)}: {match.group(0)!r}"
        for doc in [*_skill_docs(), *sorted(AGENTS.glob("*.md"))]
        for match in [SIGNATURE_INSIDE_THE_QUESTION.search(doc.read_text(encoding="utf-8"))]
        if match
    ]
    assert not offenders, "\n".join(offenders)


def test_approval_cycle_skills_presenter_teaches_the_d29_surface():
    """The orchestrator only runs the question verb; Gaia shows the block on each host."""
    presenter = PRESENTER.read_text(encoding="utf-8")
    for teaching in (
        "hook shows each\n   signature's block",
        "plugin posts the signature's block",
        "`gaia approvals question --details <approval_id> ...`",
        "never print, copy, summarise",
        "run it again at any time",
    ):
        assert teaching in presenter, teaching


def test_approval_cycle_skills_producer_and_execution_teach_the_sealed_directory_form():
    from gaia.approvals.core import sealed_invocation

    form = sealed_invocation("<sealed directory>", "<sealed command>").replace("'", "")
    assert form == "cd <sealed directory> && <sealed command>"
    for doc in (PRODUCER, EXECUTION):
        assert f"`{form}`" in doc.read_text(encoding="utf-8"), doc.name


def test_approval_cycle_skills_producer_never_passes_an_identity():
    """Session and agent come from the dispatch environment on both hosts."""
    for doc in _skill_docs():
        if _is_deleted(doc):
            continue
        for line in doc.read_text(encoding="utf-8").splitlines():
            if "gaia approvals request-" in line:
                assert "--session-id" not in line and "--agent-id" not in line, (
                    f"{doc.relative_to(ROOT)}: {line}"
                )


def test_approval_cycle_skills_producer_has_the_three_grouping_cases():
    producer = PRODUCER.read_text(encoding="utf-8")
    for heading in ("### One T3 alone", "### A known batch", "### Batches in sequence"):
        assert heading in producer, heading


def test_approval_cycle_skills_presenter_passes_the_question_object():
    presenter = PRESENTER.read_text(encoding="utf-8")
    assert "gaia approvals question" in presenter
    assert "AskUserQuestion" in presenter
    assert len(presenter.split()) <= 450, len(presenter.split())
    # The batch limit the presenter teaches is the renderer's.
    assert f"up to {surface.BATCH_MAX}" in presenter


def test_approval_cycle_skills_execution_teaches_declared_exits_and_no_result():
    execution = EXECUTION.read_text(encoding="utf-8")
    assert "--expect-exit" in execution
    assert "no result" in execution


def test_approval_cycle_skills_pending_names_every_derived_state():
    skill = PENDING.read_text(encoding="utf-8") + (
        SKILLS / "pending-approvals" / "reference.md"
    ).read_text(encoding="utf-8")
    for state in (
        reading.PENDING, reading.ORPHANED, reading.EXPIRED, reading.REPLACED,
        reading.REJECTED, reading.REVOKED, reading.APPROVED,
        reading.EXECUTED, reading.FAILED, reading.NO_RESULT, reading.IN_FLIGHT,
        reading.UNUSED, reading.LEGACY_EXECUTED, reading.LEGACY_FAILED,
    ):
        assert f"`{state}`" in skill, state


def test_approval_cycle_skills_pending_matches_the_orchestrator_guard():
    """The orchestrator withdraws but never approves; approve creates a grant."""
    guard = (ROOT / "hooks/modules/security/gaia_cli_only_guard.py").read_text(encoding="utf-8")
    approvals = (ROOT / "bin/cli/approvals.py").read_text(encoding="utf-8")
    skill = PENDING.read_text(encoding="utf-8")
    assert '("approvals", "approve")' in guard and '("approvals", "reject")' in guard
    assert "activate_approval_atomically(" in approvals
    assert "creates the grant" in skill
    assert "never approves" in skill


def test_approval_cycle_skills_withdrawal_reason_matches_the_cli_and_the_guard():
    from modules.security.gaia_cli_only_guard import _WITHDRAW_FLAGS

    skill = PENDING.read_text(encoding="utf-8")
    assert "--reason <text>" in skill
    for verb in ("reject", "revoke"):
        assert "--reason" in _WITHDRAW_FLAGS[("approvals", verb)], verb
