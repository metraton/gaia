"""Importing a hook entrypoint never re-links a workspace; only the executed SessionStart does.

A doctor run or a test inside a managed worktree used to import
``hooks/session_start.py``, whose module body linked ``<workspace>/.claude/hooks``
to the importing checkout and recorded it in the install manifest, so the
workspace enclosing the worktree lost its hooks when the worktree was released.

Every case builds its registry, data dirs and workspace under tmp_path: the
registered workspace links its hooks to an installed package, and holds a
managed worktree whose checkout declares a newer version than that package.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
HOOK_PATH = _REPO / "hooks" / "session_start.py"
_SCHEMA_SQL = _REPO / "gaia" / "store" / "schema.sql"
for _p in (_REPO / "hooks", _REPO / "bin", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _package(path: Path, version: str) -> Path:
    (path / "hooks").mkdir(parents=True)
    (path / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return path


def _register(database: Path, root: Path) -> None:
    with sqlite3.connect(database) as con:
        con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
        con.execute(
            "INSERT INTO workspaces (name, identity, root_path) VALUES (?, ?, ?)",
            (root.name, root.name, str(root)),
        )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point Gaia's registry, data dir, plugin data and HOME at tmp_path."""
    data = tmp_path / "data"
    data.mkdir()
    env = {
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "HOME": str(tmp_path / "home"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    (tmp_path / "home").mkdir()
    return SimpleNamespace(tmp=tmp_path, database=data / "gaia.db", env=env)


@pytest.fixture
def workspace(isolated):
    root = isolated.tmp / "ws"
    package = _package(root / "node_modules" / "@jaguilar87" / "gaia", "0.0.1")
    claude = root / ".claude"
    claude.mkdir()
    link = claude / "hooks"
    link.symlink_to(package / "hooks", target_is_directory=True)
    manifest = claude / "gaia-manifest.json"
    manifest.write_text('{"fixture": "unchanged"}\n', encoding="utf-8")
    worktree = _package(root / ".project-worktrees" / "gaia" / "0123abcd", "999.0.0")
    (worktree / ".git").write_text("gitdir: /nowhere/.git/worktrees/0123abcd\n", encoding="utf-8")
    _register(isolated.database, root)
    return SimpleNamespace(root=root, package=package, link=link, manifest=manifest, worktree=worktree)


def _state(workspace) -> tuple[str, bytes]:
    return os.readlink(workspace.link), workspace.manifest.read_bytes()


def test_a_importing_session_start_in_a_worktree_writes_nothing(workspace, monkeypatch):
    monkeypatch.chdir(workspace.worktree)
    monkeypatch.delitem(sys.modules, "session_start", raising=False)
    before = _state(workspace)

    spec = importlib.util.spec_from_file_location("session_start", HOOK_PATH)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))

    assert _state(workspace) == before


def test_b_doctor_importing_every_hook_in_a_worktree_writes_nothing(workspace, monkeypatch):
    from cli.doctor import check_hooks_importable

    monkeypatch.chdir(workspace.worktree)
    monkeypatch.delitem(sys.modules, "session_start", raising=False)
    before = _state(workspace)

    check_hooks_importable(workspace.worktree)

    assert _state(workspace) == before


def test_c_fresher_hooks_choice_takes_the_package_over_a_newer_checkout(workspace, monkeypatch):
    from modules.core.workspace_bootstrap import _pick_fresher_hooks_dir

    monkeypatch.chdir(workspace.root)

    chosen = _pick_fresher_hooks_dir(
        workspace.worktree / "hooks", workspace.package, workspace.package / "hooks"
    )

    assert chosen == workspace.package / "hooks"


def test_c_fresher_hooks_choice_with_only_a_checkout_links_nothing(workspace, monkeypatch):
    from modules.core.workspace_bootstrap import _pick_fresher_hooks_dir

    monkeypatch.chdir(workspace.root)
    shutil.rmtree(workspace.package)

    chosen = _pick_fresher_hooks_dir(
        workspace.worktree / "hooks", workspace.package, workspace.package / "hooks"
    )

    assert chosen is None


def test_installed_root_refuses_a_cwd_inside_a_managed_worktree(workspace):
    from gaia.install_root import InsideManagedWorktree, installed_root

    assert installed_root(workspace.root / "node_modules") == workspace.root
    with pytest.raises(InsideManagedWorktree):
        installed_root(workspace.worktree / "hooks")


def test_executed_session_start_from_the_installed_package_links_and_records(isolated):
    root = isolated.tmp / "consumer"
    package = root / "node_modules" / "@jaguilar87" / "gaia"
    ignore = shutil.ignore_patterns("__pycache__")
    for name in ("hooks", "gaia", "bin", "scripts", "opencode"):
        shutil.copytree(_REPO / name, package / name, ignore=ignore)
    shutil.copy2(_REPO / "package.json", package / "package.json")
    _register(isolated.database, root)

    proc = subprocess.run(
        [sys.executable, str(package / "hooks" / "session_start.py")],
        input=json.dumps({"session_id": "no-relink", "hook_event_name": "SessionStart", "cwd": str(root)}),
        capture_output=True,
        text=True,
        cwd=root,
        env={"PATH": os.environ.get("PATH", os.defpath), **isolated.env},
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    link = root / ".claude" / "hooks"
    assert Path(os.readlink(link)).resolve() == (package / "hooks").resolve()
    recorded = json.loads((root / ".claude" / "gaia-manifest.json").read_text(encoding="utf-8"))
    assert ".claude/hooks" in json.dumps(recorded)
