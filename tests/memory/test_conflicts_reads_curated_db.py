"""`gaia memory conflicts` shows candidate contradictions from the curated memory.

What the user feels: two of his own live rows that say opposite things about
the same subject come back as one candidate pair for the orchestrator to judge,
an unrelated row is not dragged into it, `gaia memory stats` counts the same
candidates, and memory files left under ~/.claude are never read. Runs
in-process against the temporary HOME and GAIA_DATA_DIR the suite's conftest
provides.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli.memory as memory_mod  # noqa: E402

DIRECT = "user_gaia_integrates_directly_to_main"
BY_PR = "user_gaia_integrates_through_pull_requests"
UNRELATED = "user_pref_male_narration_voice"


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _run(*argv: str) -> None:
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", *argv])
    assert args.func(args) == 0


def _memory(capsys, *argv: str) -> dict:
    capsys.readouterr()
    _run(*argv, "--json")
    return json.loads(capsys.readouterr().out)


def _add_user_row(name: str, description: str, body: str) -> None:
    _run("add", "--type=user", f"--name={name}",
         f"--description={description}", f"--body={body}")


@pytest.fixture()
def seeded():
    _add_user_row(
        DIRECT,
        "Preference: the gaia repository integrates directly to main.",
        "In the gaia repository every change is integrated directly to main, "
        "without pull requests.",
    )
    _add_user_row(
        BY_PR,
        "Preference: the gaia repository integrates through pull requests.",
        "In the gaia repository every change is integrated through a pull "
        "request, never directly to main.",
    )
    _add_user_row(
        UNRELATED,
        "Preference: video narration uses a male voice.",
        "Video narration uses a male voice, chosen per video.",
    )


@pytest.fixture()
def stale_claude_files(monkeypatch, tmp_path):
    """Contradicting memory files where the old detector looked; they must stay unread."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    folder = Path.home() / ".claude" / "projects" / "-home-jorge-ws-me" / "memory"
    folder.mkdir(parents=True)
    (folder / "stale_always.md").write_text("Always use docker for the build.\n")
    (folder / "stale_never.md").write_text("Never use docker for the build.\n")
    return folder


def _entries(payload: dict) -> list:
    """The one list the conflicts output carries, whatever its key is named."""
    (entries,) = [value for value in payload.values() if isinstance(value, list)]
    return entries


def _pairs(payload: dict) -> list[set]:
    names = (DIRECT, BY_PR, UNRELATED)
    return [{n for n in names if n in json.dumps(e)} for e in _entries(payload)]


def test_two_contradicting_user_rows_come_back_as_one_candidate(seeded, capsys):
    pairs = _pairs(_memory(capsys, "conflicts"))

    assert {DIRECT, BY_PR} in pairs
    assert not any(UNRELATED in pair for pair in pairs)


def test_stats_counts_the_candidates_conflicts_shows(seeded, capsys):
    candidates = _entries(_memory(capsys, "conflicts"))

    assert _memory(capsys, "stats")["conflicts"] == len(candidates) > 0


def test_memory_files_under_the_home_claude_folder_are_never_read(
    stale_claude_files, capsys,
):
    output = json.dumps(_memory(capsys, "conflicts"))

    assert "stale_always" not in output
    assert "stale_never" not in output
