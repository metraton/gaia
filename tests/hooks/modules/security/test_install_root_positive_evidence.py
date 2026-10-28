"""The hook root counts as an installed Gaia only on positive evidence: a known install location git proves is no checkout's top level."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs as mv  # noqa: E402

COMMANDS = ("python3 {root}/bin/gaia memory list", "python3 {root}/bin/cli/memory.py list")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.com",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _copy_cli(root: Path) -> Path:
    (root / "bin/cli").mkdir(parents=True)
    for source in (_REPO_ROOT / "bin/cli").glob("*.py"):
        (root / "bin/cli" / source.name).write_text(source.read_text())
    (root / "bin/gaia").write_text((_REPO_ROOT / "bin/gaia").read_text())
    return root


def _npm_location(tmp_path: Path) -> Path:
    return tmp_path / "store" / "node_modules" / "@jaguilar87" / "gaia"


def _install(tmp_path: Path) -> Path:
    return _copy_cli(_npm_location(tmp_path))


def _checkout(root: Path) -> Path:
    _copy_cli(root)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")
    return root


def _redispatched(root: Path) -> list:
    results = [mv.detect_mutative_command(c.format(root=root)) for c in COMMANDS]
    return ["re-dispatched" in (r.reason or "") and not r.is_mutative for r in results]


def _fake_git(tmp_path: Path, body: str) -> str:
    folder = tmp_path / "fakebin"
    folder.mkdir()
    git = folder / "git"
    git.write_text("#!/bin/sh\n" + body + "\n")
    git.chmod(0o755)
    return str(folder)


def _cached() -> int:
    return mv._root_is_install.cache_info().currsize


@pytest.fixture(autouse=True)
def _fresh():
    mv._detect_mutative_command.cache_clear()
    mv._root_is_install.cache_clear()


def test_a_missing_git_counts_as_a_checkout_and_is_not_remembered(tmp_path, monkeypatch):
    install = _install(tmp_path)
    monkeypatch.setattr(mv, "_HOOK_ROOT", install)
    empty = tmp_path / "empty"
    empty.mkdir()
    with monkeypatch.context() as without_git:
        without_git.setenv("PATH", str(empty))
        assert mv._hook_root_is_install() is False
        assert _redispatched(install) == [False, False]
        assert _cached() == 0
    mv._detect_mutative_command.cache_clear()
    assert mv._hook_root_is_install() is True
    assert _redispatched(install) == [True, True]


def test_a_git_past_its_timeout_counts_as_a_checkout_and_is_not_remembered(tmp_path, monkeypatch):
    install = _install(tmp_path)
    monkeypatch.setattr(mv, "_HOOK_ROOT", install)
    with monkeypatch.context() as slow:
        slow.setenv("PATH", _fake_git(tmp_path, "/bin/sleep 10"))
        started = time.monotonic()
        assert mv._hook_root_is_install() is False
        assert 2.5 < time.monotonic() - started < 6
        assert _cached() == 0
    assert mv._hook_root_is_install() is True


@pytest.mark.parametrize("body", [
    "echo garbage; exit 0",
    "exit 0",
    "echo 'fatal: something' >&2; exit 1",
    "echo 'fatal: detected dubious ownership in repository' >&2; exit 128",
    "exit 129",
], ids=["exit0-garbage", "exit0-empty", "exit1", "exit128-dubious", "exit129"])
@pytest.mark.parametrize("where", ["install-location", "elsewhere"])
def test_a_git_failure_never_proves_an_install(tmp_path, monkeypatch, body, where):
    checkout = _checkout(_npm_location(tmp_path) if where == "install-location" else tmp_path / "checkout")
    monkeypatch.setattr(mv, "_HOOK_ROOT", checkout)
    with monkeypatch.context() as fake:
        fake.setenv("PATH", _fake_git(tmp_path, body))
        assert mv._hook_root_is_install() is False
        assert _redispatched(checkout) == [False, False]


def test_git_saying_not_a_repository_outside_an_install_location_is_not_an_install(tmp_path, monkeypatch):
    root = _copy_cli(tmp_path / "plain")
    monkeypatch.setattr(mv, "_HOOK_ROOT", root)
    assert mv._hook_root_is_install() is False
    assert _redispatched(root) == [False, False]


@pytest.mark.parametrize("variable, value", [
    ("GIT_DIR", "/nonexistent-gitdir"),
    ("GIT_WORK_TREE", "/nonexistent-wt"),
    ("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1"),
    ("GIT_CEILING_DIRECTORIES", "/"),
])
def test_the_callers_git_environment_cannot_turn_a_checkout_into_an_install(tmp_path, monkeypatch, variable, value):
    checkout = _checkout(_npm_location(tmp_path))
    monkeypatch.setattr(mv, "_HOOK_ROOT", checkout)
    with monkeypatch.context() as environment:
        environment.setenv(variable, value)
        assert mv._hook_root_is_install() is False
        assert _redispatched(checkout) == [False, False]


def test_an_install_stays_an_install_whatever_git_dir_the_caller_exports(tmp_path, monkeypatch):
    install = _install(tmp_path)
    elsewhere = _checkout(tmp_path / "elsewhere")
    monkeypatch.setattr(mv, "_HOOK_ROOT", install)
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert mv._hook_root_is_install() is True


def test_a_dev_plugin_cache_entry_is_an_install_and_a_lookalike_is_not(tmp_path, monkeypatch):
    from gaia.paths import cache_dir

    entry = _copy_cli(cache_dir() / "dev-plugin" / "ws")
    lookalike = _copy_cli(tmp_path / "dev-plugin" / "ws")
    assert mv._root_is_install(entry) is True
    assert mv._root_is_install(lookalike) is False


def test_a_symlink_to_a_checkout_is_a_checkout(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "checkout")
    link = _npm_location(tmp_path)
    link.parent.mkdir(parents=True)
    link.symlink_to(checkout)
    monkeypatch.setattr(mv, "_HOOK_ROOT", link)
    assert mv._hook_root_is_install() is False
    assert _redispatched(link) == [False, False]
    assert _redispatched(checkout) == [False, False]


def test_install_scripts_symlinked_into_a_checkout_are_not_shipped(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "checkout")
    install = _npm_location(tmp_path)
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "cli").symlink_to(checkout / "bin" / "cli")
    (install / "bin" / "gaia").symlink_to(checkout / "bin" / "gaia")
    monkeypatch.setattr(mv, "_HOOK_ROOT", install)
    assert mv._hook_root_is_install() is True
    assert _redispatched(install) == [False, False]


def test_two_roots_in_one_process_keep_their_own_verdicts(tmp_path, monkeypatch):
    install = _install(tmp_path)
    checkout = _checkout(tmp_path / "checkout")
    assert mv._root_is_install(install) is True
    assert mv._root_is_install(checkout) is False
    for root, expected in ((install, [True, True]), (checkout, [False, False]), (install, [True, True])):
        monkeypatch.setattr(mv, "_HOOK_ROOT", root)
        mv._detect_mutative_command.cache_clear()
        assert _redispatched(root) == expected


def test_more_roots_than_cache_slots_stay_correct(tmp_path):
    roots = [
        _checkout(tmp_path / f"c{i}") if i % 2 else _install(tmp_path / f"i{i}")
        for i in range(10)
    ]
    for _ in range(2):
        assert [mv._root_is_install(r) for r in roots] == [i % 2 == 0 for i in range(10)]


@pytest.mark.parametrize("form", ["{root}/bin/gaia memory list", "{root}/bin/cli/memory.py list"])
def test_a_checkouts_cli_run_directly_asks(form):
    assert mv.detect_mutative_command(form.format(root=_REPO_ROOT)).is_mutative is True


def test_a_checkouts_cli_run_by_relative_path_asks():
    assert mv.detect_mutative_command("./bin/gaia memory list", cwd=str(_REPO_ROOT)).is_mutative is True


def test_the_installed_cli_run_directly_stays_free(tmp_path, monkeypatch):
    install = _install(tmp_path)
    (install / "bin" / "gaia").chmod(0o755)
    monkeypatch.setattr(mv, "_HOOK_ROOT", install)
    assert mv.detect_mutative_command(f"{install}/bin/gaia memory list").is_mutative is False
    assert mv.detect_mutative_command("gaia memory list").is_mutative is False
