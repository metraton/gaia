"""
Regression guard for a fixed sanitizer evasion of the T3 approval gate.

HISTORICAL RECORD (kept intentionally -- do not delete this file or its
narrative): this module was originally committed to document a LIVE
vulnerability, verified by reading the code and then CONFIRMED by running
the concrete command through the real pipeline (``validate_bash_command``,
the same entry the PreToolUse hook uses). Phase 3d of ``validate()``
(``_try_sanitize_command`` in ``bash_validator.py``) matched a trailing
``> file`` / ``>> file`` redirect, stripped it, and returned allowed=True /
tier=T0 IMMEDIATELY -- before phase 5, where mutative-verb detection would
otherwise classify the same command as T3 and route it to the approval
("ask") dialog. The observed evasion, byte for byte:

    Bare:      terraform apply                    -> blocked, T3_BLOCKED
    Redirect:  terraform apply > /tmp/tf-out.txt   -> allowed, T0_READ_ONLY

FIX: the sanitizer no longer classifies the command it cleans. It strips the
decorator (nohup prefix / trailing background `&` / trailing redirect) and
lets the CLEANED command re-enter the full classification pipeline (unwrap
-> decompose -> classify -> composition -> aggregate) exactly like any other
command, so a redirect can no longer hide a genuine T3 verb from the
mutative-verb detector. The sanitization step now runs in EARLY
NORMALIZATION, before phase 1, rather than short-circuiting from inside
phase 3 -- see ``bash_validator.py`` for the current pipeline shape.

The two tests below now assert the FIXED behavior: the bare and the
redirected form of the same command classify IDENTICALLY (T3_BLOCKED). If
either assertion below ever reverts to allowed=True for the redirected
form, the evasion this file was written to catch has reopened.
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
    The bare command is a real T3 block: ``terraform apply`` is a mutative
    verb (phase 5 of validate()) with no active approval grant, so it is
    routed to decide_t3_outcome() and comes back not-allowed, tier
    T3_BLOCKED, with an "ask" block_response. This is the block the
    redirected form is checked against below.
    """
    result = validate_bash_command(BARE_COMMAND)
    assert result.allowed is False
    assert result.tier == SecurityTier.T3_BLOCKED
    assert result.block_response is not None
    assert (
        result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"
    )


def test_sanitizer_redirect_no_longer_evades_t3_block():
    """
    REGRESSION GUARD: appending a trailing redirect to the SAME command that
    test_sanitizer_redirect_baseline_is_blocked_t3 proves is T3-gated must
    classify IDENTICALLY -- still blocked, still T3_BLOCKED, still an "ask".
    The sanitizer strips the trailing ``> /tmp/tf-out.txt`` and re-enters
    classification on the cleaned command, which is exactly where the bare
    form is caught -- the decorator changes nothing about the verdict.

    Before the fix this test asserted the OPPOSITE (allowed=True, tier=T0)
    under the name ``test_sanitizer_redirect_evades_t3_block`` -- see the
    module docstring above for the vulnerability that assertion documented.
    """
    result = validate_bash_command(REDIRECTED_COMMAND)
    assert result.allowed is False
    assert result.tier == SecurityTier.T3_BLOCKED
    assert result.block_response is not None
    assert (
        result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"
    )
    # The classified command inside the block carries the CLEANED form --
    # confirming the redirect was stripped and re-classified, not ignored.
    assert result.modified_input == {"command": BARE_COMMAND}
