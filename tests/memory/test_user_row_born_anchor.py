"""A preference the user saves is standing from the moment it is written.

What the user feels: a `gaia memory add --type=user` with no `--class` reaches
the next session's birth block and every dispatched subagent's kernel, and a
row saved with an explicit `--class=log` stays out of both. Runs in-process
against the temporary HOME and GAIA_DATA_DIR the suite's conftest provides.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context.kernel_builder import build_memory_block  # noqa: E402
from modules.session.session_manifest import build_session_context  # noqa: E402

STANDING = "Preference: pull requests are opened as drafts until CI is green."
LOGGED = "Preference: an old note the user keeps only as history."


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _memory_add(*flags: str) -> None:
    import cli.memory as memory_mod

    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", "add", "--type=user", *flags])
    assert args.func(args) == 0


def test_a_user_row_saved_without_a_class_reaches_birth_and_kernel():
    _memory_add(
        "--name=user_pref_draft_prs",
        "--description=Preference: pull requests start as drafts.",
        f"--body={STANDING}",
    )

    assert STANDING in build_session_context()
    assert STANDING in build_memory_block()


def test_an_explicit_log_class_keeps_the_row_out_of_birth_and_kernel():
    _memory_add(
        "--name=user_pref_old_note",
        "--description=Preference: an old note kept as history.",
        f"--body={LOGGED}",
        "--class=log",
    )

    assert LOGGED not in build_session_context()
    assert LOGGED not in build_memory_block()
