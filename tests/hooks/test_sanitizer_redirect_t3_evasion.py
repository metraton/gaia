"""
Empirical measurement: does the sanitizer short-circuit in
BashValidator.validate() let a real T3 block be evaded by appending a
trailing redirect?

_try_sanitize_command (phase 3d of validate(), bash_validator.py) strips a
trailing ``> file`` / ``>> file`` and returns allowed=True / tier=T0
IMMEDIATELY -- before phase 5, where mutative-verb detection would otherwise
classify the same command as T3 and route it to the approval ("ask")
dialog. This was VERIFIED by reading the code. This test MEASURES it: it
runs the concrete command ``terraform apply`` through the real pipeline
(``validate_bash_command``, the same entry the PreToolUse hook uses) both
bare and with a trailing redirect appended, and asserts the OBSERVED
result of each -- it does not modify _try_sanitize_command, validate(), or
any other validator code.

Repro, byte for byte:
  Bare:      terraform apply
  Redirect:  terraform apply > /tmp/tf-out.txt
"""

import sys
from pathlib import Path

# Add hooks to path so imports resolve from the test environment (matches
# the convention in tests/hooks/modules/tools/test_bash_pipeline_integration.py).
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import validate_bash_command  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402


BARE_COMMAND = "terraform apply"
REDIRECTED_COMMAND = "terraform apply > /tmp/tf-out.txt"


def test_sanitizer_redirect_baseline_is_blocked_t3():
    """
    OBSERVED: the bare command IS a real T3 block today.

    ``terraform apply`` is a mutative verb (phase 5 of validate()) with no
    active approval grant, so it is routed to decide_t3_outcome() and comes
    back not-allowed, tier T3_BLOCKED, with an "ask" block_response. This is
    the block the redirect form is checked against below.
    """
    result = validate_bash_command(BARE_COMMAND)
    assert result.allowed is False
    assert result.tier == SecurityTier.T3_BLOCKED
    assert result.block_response is not None
    assert (
        result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"
    )


def test_sanitizer_redirect_evades_t3_block():
    """
    OBSERVED: appending a trailing redirect to the SAME command that
    test_sanitizer_redirect_baseline_is_blocked_t3 proves is T3-gated
    reverses the verdict to allowed=True, tier=T0_READ_ONLY.

    validate()'s phase 3d (_try_sanitize_command) matches the trailing
    ``> /tmp/tf-out.txt``, strips it, and returns allowed=True immediately
    -- before phase 5 (mutative-verb detection) ever runs on the stripped
    command. The result carries modified_input with the redirect-free
    command, confirming the sanitize branch (not some other allow path)
    produced the verdict.

    This CONFIRMS the hypothesis under test: the sanitizer short-circuit
    lets a command that is a real T3 block bypass approval by appending a
    trailing redirect.
    """
    result = validate_bash_command(REDIRECTED_COMMAND)
    assert result.allowed is True
    assert result.tier == SecurityTier.T0_READ_ONLY
    assert result.modified_input == {"command": BARE_COMMAND}
