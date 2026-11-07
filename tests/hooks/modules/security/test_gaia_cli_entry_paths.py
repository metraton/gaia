"""A path argument named ``gaia`` is not a Gaia CLI entry point when it cannot run as one (the checkout's ``gaia/`` package dir)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.security.gaia_cli_invocation import computes_gaia_words  # noqa: E402
from modules.security.subagent_memory_write_guard import check  # noqa: E402

_PACKAGE = _REPO / "gaia"


def test_the_reported_grep_over_the_package_directory_is_not_refused():
    command = f"grep -rn 'entry_group' {_PACKAGE} --include=*.py -l"

    assert check(command, is_subagent=True, agent_type="developer") == (True, None)


def test_a_directory_named_gaia_is_never_an_entry_point(tmp_path):
    package = tmp_path / "gaia"
    package.mkdir()

    assert not computes_gaia_words(f"grep -rn foo {package} --include=*.py -l")
    assert check(f"grep -rn foo {package} --include=*.py -l",
                 is_subagent=True, agent_type="developer") == (True, None)


def test_a_plain_file_named_gaia_is_never_an_entry_point(tmp_path):
    notes = tmp_path / "gaia"
    notes.write_text("notes\n")

    assert not computes_gaia_words(f"grep -n foo {notes} *.md")


@pytest.mark.parametrize("command", [
    "/opt/missing/bin/gaia contract proposals $(echo dismiss) 1",
    "timeout 5 /opt/missing/bin/gaia memory $(printf add)",
    "echo accept 1 | xargs /opt/missing/bin/gaia contract proposals",
])
def test_a_path_that_may_be_the_cli_still_counts(command):
    assert computes_gaia_words(command)


def test_an_executable_named_gaia_still_counts(tmp_path):
    launcher = tmp_path / "gaia"
    launcher.write_text("#!/bin/sh\nexec real-gaia \"$@\"\n")
    launcher.chmod(0o755)

    assert computes_gaia_words(f"{launcher} contract proposals $(echo dismiss) 1")
