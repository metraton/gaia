"""A secret reached through a symlink is refused as the secret itself; a link to an ordinary file stays free."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import sensitive_paths, sensitive_read_guard  # noqa: E402


@pytest.fixture()
def links(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = tmp_path / "vault"
    (vault / ".ssh").mkdir(parents=True)
    (vault / "docs").mkdir()
    (vault / ".env").write_text("TOKEN=stand-in\n")
    (vault / ".ssh" / "config").write_text("Host stand-in\n")
    (vault / "README.md").write_text("Fix.\n")
    (vault / "docs" / "notes.md").write_text("Fix.\n")
    found = tmp_path / "links"
    found.mkdir()
    (found / "a.md").symlink_to(vault / ".env")
    (found / "b.md").symlink_to(found / "a.md")
    (found / "keys").symlink_to(vault / ".ssh")
    (found / "gone.md").symlink_to(tmp_path / "missing" / ".env")
    (found / "readme.md").symlink_to(vault / "README.md")
    (found / "readme2.md").symlink_to(found / "readme.md")
    (found / "docs").symlink_to(vault / "docs")
    return found


REFUSED = [
    "cat {d}/a.md",
    "cat {d}/b.md",
    "cat {d}/keys/config",
    "ls {d}/keys",
    "cat {d}/gone.md",
    "cat a.md",
    "foocli pr create --body-file {d}/a.md",
    "ghx pr create --body-file {d}/b.md",
    "gh pr create --body-file=a.md",
    "gh pr create --body-file {d}/keys/config",
    "gh issue create --body-file {d}/a.md",
    "git commit -F {d}/a.md",
    "foocli pr create -F {d}/gone.md",
]
FREE = [
    "cat {d}/readme.md",
    "cat {d}/readme2.md",
    "cat {d}/docs/notes.md",
    "ls {d}/docs",
    "cat readme2.md",
    "gh pr create --body-file {d}/readme2.md",
    "foocli pr create --body-file readme.md",
    "git commit -F {d}/docs/notes.md",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_secret_behind_a_symlink_is_refused(links, command):
    allowed, reason = sensitive_read_guard.check(command.format(d=links), str(links))
    assert allowed is False, command
    assert reason.startswith("[SENSITIVE_PATH]")


@pytest.mark.parametrize("command", FREE)
def test_a_symlink_to_an_ordinary_file_stays_free(links, command):
    assert sensitive_read_guard.check(command.format(d=links), str(links)) == (True, None), command


@pytest.mark.parametrize("name", ["a.md", "b.md", "keys/config", "gone.md"])
def test_the_file_tools_judge_the_link_target(links, name):
    assert sensitive_paths.sensitive_path_label(str(links / name))
    assert sensitive_read_guard.check_file_tool("Read", {"file_path": str(links / name)})


@pytest.mark.parametrize("name", ["readme.md", "readme2.md", "docs/notes.md"])
def test_the_file_tools_leave_a_link_to_an_ordinary_file_free(links, name):
    assert sensitive_read_guard.check_file_tool("Read", {"file_path": str(links / name)}) is None


def test_a_relative_link_is_resolved_only_against_the_commands_directory(links, monkeypatch):
    monkeypatch.chdir(links)
    assert sensitive_paths.sensitive_path_label("a.md") == ""
    assert sensitive_paths.sensitive_path_label("a.md", str(links))
