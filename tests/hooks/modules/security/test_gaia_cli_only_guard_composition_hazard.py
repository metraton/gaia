"""Fd-dup false-positive fix: ``_find_composition_hazard`` must not flag
``>&`` (file-descriptor duplication, e.g. ``2>&1``) as a bare background
operator.

Before this fix, the character-by-character scan in
``_find_composition_hazard`` inspected only the character AFTER an ``&`` to
rule out ``&&``, never the character BEFORE it to rule out ``>&`` -- so
`gaia doctor 2>&1` was denied outright with "GAIA CLI ONLY: bare background
operator (`&`) detected", even though `2>&1` duplicates a file descriptor and
runs nothing in the background. No prior test covered this function at all
(confirmed by search); this file is that coverage.

The negative cases matter more than the positive one: they are the proof
that loosening the fd-dup exclusion did not open a hole for real background
execution or defeat `&&` composition. See the precedents this fix mirrors:
``bash_validator._FD_DUP_RE`` (``r"\\d+>&\\d+"``) and
``cloud_pipe_validator.UNIVERSAL_VIOLATIONS``'s "background" entry
(``r'(?<![>&])&(?!&)'``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard

_GAIA = "/abs/path/bin/gaia"
_ORCHESTRATOR_PAYLOAD: dict = {}


@pytest.fixture(autouse=True)
def _trust_fake_binary(monkeypatch):
    monkeypatch.setattr(
        guard, "is_trusted_gaia_binary", lambda token: token == _GAIA
    )


def _hazard(command: str):
    return guard._find_composition_hazard(command)


def _check(command: str):
    return guard.check(command, _ORCHESTRATOR_PAYLOAD)


# ---------------------------------------------------------------------------
# Positive: fd duplication is not a composition hazard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "gaia doctor 2>&1",
        "gaia doctor 1>&2",
        "gaia doctor >&2",
    ],
)
def test_fd_duplication_is_not_flagged_as_hazard(command):
    assert _hazard(command) is None


def test_fd_duplication_allowed_end_to_end_through_check():
    allowed, reason = _check(f"{_GAIA} doctor 2>&1")
    assert allowed is True
    assert reason is None


# ---------------------------------------------------------------------------
# Negative: everything else that must still be denied stays denied
# ---------------------------------------------------------------------------

def test_trailing_bare_background_still_flagged():
    assert _hazard("gaia doctor &") == "bare background operator (`&`) detected"


def test_mid_command_bare_background_still_flagged():
    assert (
        _hazard("gaia doctor & echo x")
        == "bare background operator (`&`) detected"
    )


def test_trailing_bare_background_still_denied_end_to_end():
    allowed, reason = _check(f"{_GAIA} doctor &")
    assert allowed is False
    assert "bare background operator" in reason


def test_double_ampersand_composition_still_not_a_hazard():
    """`&&` between two trusted gaia invocations is composition, not a
    background hazard -- the fd-dup fix must not disturb this."""
    assert _hazard(f"{_GAIA} doctor && {_GAIA} status") is None


def test_double_ampersand_between_trusted_invocations_still_allowed():
    allowed, reason = _check(f"{_GAIA} doctor && {_GAIA} status")
    assert allowed is True
    assert reason is None


def test_pipe_to_untrusted_binary_still_denied():
    allowed, reason = _check(f"{_GAIA} doctor | rm -rf /tmp/x")
    assert allowed is False
    assert reason is not None


def test_process_substitution_still_flagged_as_hazard():
    assert (
        _hazard("gaia doctor <(cat /etc/passwd)")
        == "process substitution (`<(` / `>(`) detected"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
