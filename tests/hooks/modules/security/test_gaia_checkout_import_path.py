"""A by-path Gaia CLI in a source checkout runs free only when nothing foreign sits on its import path, ignored files included."""

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
COMMAND = "python3 {checkout}/bin/gaia memory list"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hook = tmp_path / "hook"
    (hook / "bin/cli").mkdir(parents=True)
    for source in (_REPO_ROOT / "bin/cli").glob("*.py"):
        (hook / "bin/cli" / source.name).write_text(source.read_text())
    (hook / "bin/gaia").write_text((_REPO_ROOT / "bin/gaia").read_text())
    (hook / ".gitignore").write_text("__pycache__/\n*.so\n")
    _git(hook, "init", "-q")
    _git(hook, "add", "-A")
    _git(hook, "commit", "-q", "-m", "init")
    second = tmp_path / "second"
    _git(hook, "worktree", "add", "-q", "-b", "other", str(second))
    monkeypatch.setattr(mv, "_HOOK_ROOT", hook)
    mv._gaia_source_repositories.cache_clear()
    yield second
    mv._gaia_source_repositories.cache_clear()


def _runs_free(checkout: Path) -> bool:
    result = mv.detect_mutative_command(COMMAND.format(checkout=checkout))
    return "re-dispatched" in (result.reason or "") and not result.is_mutative


def _compile(checkout: Path, relative: str, mode: py_compile.PycInvalidationMode, payload: Path) -> Path:
    cached = Path(importlib.util.cache_from_source(str(checkout / relative)))
    py_compile.compile(str(payload), cfile=str(cached), invalidation_mode=mode)
    return cached


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    source = tmp_path / "payload.py"
    source.write_text(PAYLOAD)
    return source


def test_a_clean_checkout_runs_free(checkout):
    assert _runs_free(checkout)


@pytest.mark.parametrize("name", ["json.pyc", "json.cpython-311-x86_64-linux-gnu.so", "evil.pth"])
def test_a_foreign_file_at_the_checkout_root_asks(name, checkout, payload):
    (checkout / name).write_bytes(payload.read_bytes())
    assert not _runs_free(checkout)


def test_an_ignored_extension_module_beside_a_cli_module_asks(checkout, payload):
    (checkout / "bin/cli/memory.cpython-311-x86_64-linux-gnu.so").write_bytes(payload.read_bytes())
    assert not _runs_free(checkout)


@pytest.mark.parametrize("mode, free", [
    (py_compile.PycInvalidationMode.TIMESTAMP, True),
    (py_compile.PycInvalidationMode.CHECKED_HASH, True),
    (py_compile.PycInvalidationMode.UNCHECKED_HASH, False),
])
def test_a_cached_pyc_of_a_tracked_module_is_trusted_only_when_validated(mode, free, checkout, payload):
    _compile(checkout, "bin/cli/memory.py", mode, payload)
    assert _runs_free(checkout) is free


def test_the_dispatcher_own_cached_pyc_is_trusted(checkout, payload):
    _compile(checkout, "bin/gaia", py_compile.PycInvalidationMode.TIMESTAMP, payload)
    assert _runs_free(checkout)


def test_a_cached_pyc_without_its_tracked_source_asks(checkout, payload):
    _compile(checkout, "bin/cli/json.py", py_compile.PycInvalidationMode.TIMESTAMP, payload)
    assert not _runs_free(checkout)


def test_a_truncated_cached_pyc_asks(checkout, payload):
    cached = _compile(checkout, "bin/cli/memory.py", py_compile.PycInvalidationMode.TIMESTAMP, payload)
    cached.write_bytes(cached.read_bytes()[:10])
    assert not _runs_free(checkout)
