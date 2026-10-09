"""The orchestrator can record a session's resume point, in one bounded shape and nothing wider."""

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from gaia.session_snapshot import RESUME_POINT_MAX_CHARS, write_resume_point  # noqa: E402
from modules.security import gaia_cli_only_guard as guard  # noqa: E402

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"
VERB = "session resume-point set"


@pytest.fixture
def run_guard(monkeypatch):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


def test_the_guard_cap_equals_the_writer_cap():
    assert guard._RESUME_POINT_MAX_CHARS == RESUME_POINT_MAX_CHARS == 2000


@pytest.mark.parametrize("argline", [
    f"{VERB} --session-id ses-1 --text 'finish task 21 then run the gate'",
    f"{VERB} --text 'x' --session-id=ses-1",
    f"{VERB} --session-id ses-1 --text '{'x' * 2000}'",
])
def test_the_bounded_resume_point_shape_passes(run_guard, argline):
    allowed, reason = run_guard(argline)
    assert allowed, reason


@pytest.mark.parametrize("argline", [
    f"{VERB} --text 'no session'",
    f"{VERB} --session-id ses-1",
    f"{VERB} --session-id ses-1 --text 'x' --json",
    f"{VERB} --session-id ses-1 --text 'x' --workspace me",
    f"{VERB} --session-id ses-1 --text 'x' --text 'y'",
    f"{VERB} ses-1 --session-id ses-1 --text 'x'",
    f"{VERB} --session-id ses-1 --text ''",
    f"{VERB} --session-id ses-1 --text '{'x' * 2001}'",
])
def test_resume_point_writes_outside_the_shape_are_denied(run_guard, argline):
    allowed, reason = run_guard(argline)
    assert not allowed
    assert reason


def test_the_writer_refuses_text_over_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        write_resume_point("ses-1", "x" * (RESUME_POINT_MAX_CHARS + 1))
    write_resume_point("ses-1", "x" * RESUME_POINT_MAX_CHARS)
