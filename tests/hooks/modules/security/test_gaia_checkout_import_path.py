"""A by-path Gaia CLI is judged as its gaia command only inside the installed Gaia the hook runs from; any git checkout is judged by content."""

from __future__ import annotations

import importlib.util
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs as mv  # noqa: E402

PAYLOAD = "import shutil\nshutil.rmtree('/srv/data')\n"
COMMANDS = ("python3 {root}/bin/gaia memory list", "python3 {root}/bin/cli/memory.py list")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


def _copy_cli(root: Path) -> Path:
    (root / "bin/cli").mkdir(parents=True)
    for source in (_REPO_ROOT / "bin/cli").glob("*.py"):
        (root / "bin/cli" / source.name).write_text(source.read_text())
    (root / "bin/gaia").write_text((_REPO_ROOT / "bin/gaia").read_text())
    return root


def _redispatched(root: Path) -> list:
    results = [mv.detect_mutative_command(command.format(root=root)) for command in COMMANDS]
    return ["re-dispatched" in (result.reason or "") and not result.is_mutative for result in results]


@pytest.fixture
def hook_root(monkeypatch: pytest.MonkeyPatch):
    def point_at(root: Path) -> None:
        monkeypatch.setattr(mv, "_HOOK_ROOT", root)
        mv._hook_root_is_install.cache_clear()

    yield point_at
    mv._hook_root_is_install.cache_clear()


def test_the_installed_gaia_the_hook_runs_from_is_re_dispatched(tmp_path, hook_root):
    install = _copy_cli(tmp_path / "install")
    hook_root(install)
    assert _redispatched(install) == [True, True]


def test_an_install_nested_in_a_project_checkout_is_still_the_install(tmp_path, hook_root):
    _git(tmp_path, "init", "-q")
    install = _copy_cli(tmp_path / "node_modules/@jaguilar87/gaia")
    hook_root(install)
    assert _redispatched(install) == [True, True]


def test_another_install_than_the_hook_one_is_judged_by_content(tmp_path, hook_root):
    hook_root(_copy_cli(tmp_path / "install"))
    assert _redispatched(_copy_cli(tmp_path / "elsewhere")) == [False, False]


@pytest.fixture
def checkout(tmp_path: Path, hook_root) -> Path:
    hook = _copy_cli(tmp_path / "hook")
    _git(hook, "init", "-q")
    _git(hook, "add", "-A")
    _git(hook, "commit", "-q", "-m", "init")
    hook_root(hook)
    second = tmp_path / "second"
    _git(hook, "worktree", "add", "-q", "-b", "other", str(second))
    return second


def _forge_timestamp_pyc(root: Path, payload: Path) -> None:
    cached = Path(importlib.util.cache_from_source(str(root / "bin/cli/memory.py")))
    py_compile.compile(str(payload), cfile=str(cached), invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)


def _symlink_excluded_pycache(root: Path, payload: Path) -> None:
    elsewhere = root.parent / "cache"
    elsewhere.mkdir()
    (elsewhere / "memory.pyc").write_bytes(payload.read_bytes())
    (root / "bin/cli/__pycache__").symlink_to(elsewhere)
    exclude = Path(subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
        check=True, capture_output=True, text=True,
    ).stdout.strip())
    exclude = exclude if exclude.is_absolute() else root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("__pycache__\n")


@pytest.mark.parametrize("tamper", [
    None,
    lambda root, payload: (root / "json.py").write_text(payload.read_text()),
    _forge_timestamp_pyc,
    _symlink_excluded_pycache,
], ids=["clean", "foreign-root-module", "forged-timestamp-pyc", "symlinked-excluded-pycache"])
@pytest.mark.parametrize("which", ["hook-checkout", "sibling-worktree"])
def test_a_git_checkout_is_judged_by_content_however_clean(tamper, which, checkout, tmp_path):
    root = checkout if which == "sibling-worktree" else mv._HOOK_ROOT
    payload = tmp_path / "payload.py"
    payload.write_text(PAYLOAD)
    if tamper is not None:
        tamper(root, payload)
    assert _redispatched(root) == [False, False]
    assert mv.detect_mutative_command(COMMANDS[0].format(root=root)).is_mutative is True
