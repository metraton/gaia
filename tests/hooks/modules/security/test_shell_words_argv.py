"""Shell words are judged as bash passes them: comments, a single `&`, redirect targets and option values are not argv.

Each bypass spelling is applied to two mutations and to the subagent's
resume-point write: the mutation must still ask, the write must still be
refused, and a real `--help` must stay free.
"""

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

MUTATIONS = ["gaia task gate remove 12", "git push origin x"]
RESUME_POINT_WRITE = "gaia session resume-point set --session-id s --text t"

# (case_id, how a fake `--help` is attached to a command)
BYPASSES = [
    ("comment", lambda c: f"{c} # --help"),
    ("single-ampersand", lambda c: f"{c} & true --help"),
    ("option-value", lambda c: f"env -u --help {c}"),
    ("redirect-target", lambda c: f"{c} 2> --help"),
]

MUTATION_ROWS = [
    (f"{name}:{mutation.split()[0]}-{mutation.split()[1]}", spell(mutation))
    for name, spell in BYPASSES
    for mutation in MUTATIONS
]


def _validate(command, *, is_subagent):
    return BashValidator().validate(command, is_subagent=is_subagent, agent_type="developer" if is_subagent else "")


@pytest.mark.parametrize("case_id,command", MUTATION_ROWS, ids=[row[0] for row in MUTATION_ROWS])
def test_a_fake_help_does_not_free_a_mutation(case_id, command):
    assert classify_command_tier(command) == SecurityTier.T3_BLOCKED, case_id
    assert not _validate(command, is_subagent=True).allowed, case_id
    assert not _validate(command, is_subagent=False).allowed, case_id


@pytest.mark.parametrize("case_id,spell", BYPASSES, ids=[name for name, _ in BYPASSES])
def test_a_fake_help_does_not_free_a_subagent_resume_point_write(case_id, spell):
    assert not _validate(spell(RESUME_POINT_WRITE), is_subagent=True).allowed, case_id


@pytest.mark.parametrize("command", [
    "gaia task gate remove 12 --help",
    "git push --help",
    "gaia session resume-point set --help",
    "gaia task gate remove 12 --help # trailing note",
    "git push --help 2>/dev/null",
])
def test_a_real_help_stays_free(command):
    assert detect_mutative_command(command).is_mutative is False, command
    assert classify_command_tier(command) == SecurityTier.T0_READ_ONLY, command
    result = _validate(command, is_subagent=True)
    assert result.allowed and result.tier == SecurityTier.T0_READ_ONLY, f"{command}: {result.reason}"


@pytest.mark.parametrize("command", [
    "'bash' -c 'git push origin x'",
    'b""ash -c "git push origin x"',
    "\\bash -c 'git push origin x'",
])
def test_a_quoted_shell_name_is_still_a_shell_wrapper(command):
    result = _validate(command, is_subagent=True)
    assert not result.allowed, command
    assert classify_command_tier(command) == SecurityTier.T3_BLOCKED, command
