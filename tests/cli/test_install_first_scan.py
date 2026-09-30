"""The workspace is the folder Gaia was installed in, scanned on install.

Covers the bootstrap no longer registering the package folder, `gaia install`
registering and scanning the folder it ran in, a nested workspace keeping its
own projects, and a subfolder resolving to the installed root instead of
seeding a `.claude` of its own.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _env(tmp_path: Path, db: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "WORKSPACE"}
    env.update(
        GAIA_DB=str(db),
        GAIA_DATA_DIR=str(tmp_path / "data"),
        HOME=str(tmp_path / "home"),
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    return env


def _bootstrap(tmp_path: Path, db: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "bootstrap_database.py")],
        env=_env(tmp_path, db), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _rows(db: Path, sql: str, *params) -> list:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_bootstrap_registers_no_workspace_for_the_package_folder(tmp_path):
    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)

    assert _rows(db, "SELECT name, identity FROM workspaces") == []


def test_bootstrap_registers_a_workspace_only_when_one_is_named(tmp_path):
    unnamed = tmp_path / "unnamed.db"
    named = tmp_path / "named.db"
    workspace = tmp_path / "acme"
    workspace.mkdir()

    for db, extra in ((unnamed, {}), (named, {"WORKSPACE": str(workspace)})):
        proc = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "bootstrap_database.py")],
            env={**_env(tmp_path, db), **extra}, capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    assert _rows(unnamed, "SELECT name FROM workspaces") == []
    assert _rows(named, "SELECT name FROM workspaces") == [("acme",)]


def test_install_registers_and_scans_the_folder_it_ran_in(tmp_path):
    db = tmp_path / "gaia.db"
    workspace = tmp_path / "acme"
    _git_repo(workspace / "billing")

    proc = subprocess.run(
        [sys.executable, str(_REPO / "bin" / "gaia"), "install", "--channel", "npm",
         "--workspace", str(workspace), "--quiet"],
        env=_env(tmp_path, db), capture_output=True, text=True, timeout=600,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _rows(db, "SELECT name, root_path FROM workspaces") == [
        ("acme", str(workspace.resolve()))
    ]
    assert _rows(db, "SELECT workspace, name FROM projects") == [("acme", "billing")]


def test_first_scan_leaves_a_nested_workspace_with_its_own_projects(tmp_path):
    from gaia.install_root import first_scan

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    outer = tmp_path / "outer"
    inner = outer / "inner"
    _git_repo(inner / "lib")
    assert first_scan(inner, database=db)["action"] == "created"
    _git_repo(inner / "later")
    _git_repo(outer / "app")

    result = first_scan(outer, database=db)

    assert result["action"] == "created"
    assert sorted(_rows(db, "SELECT workspace, name FROM projects")) == [
        ("inner", "lib"),
        ("outer", "app"),
    ]


def test_first_scan_never_rebinds_a_name_recorded_for_another_root(tmp_path):
    from gaia.install_root import first_scan

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    first = tmp_path / "a" / "shop"
    second = tmp_path / "b" / "shop"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert first_scan(first, database=db)["action"] == "created"

    assert first_scan(first, database=db)["action"] == "noop"
    assert first_scan(second, database=db)["action"] == "error"
    assert _rows(db, "SELECT root_path FROM workspaces WHERE name = 'shop'") == [
        (str(first.resolve()),)
    ]


def test_a_subfolder_resolves_to_the_installed_root_and_seeds_no_claude_dir(
    tmp_path, monkeypatch
):
    from gaia.install_root import first_scan, installed_root

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    workspace = tmp_path / "acme"
    subfolder = _git_repo(workspace / "billing") / "src"
    subfolder.mkdir()
    first_scan(workspace, database=db)
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(subfolder)

    assert installed_root(subfolder, database=db) == workspace.resolve()

    package = workspace / "node_modules" / "@jaguilar87" / "gaia"
    (package / "hooks").mkdir(parents=True)
    (package / "package.json").write_text('{"version": "0.0.1"}', encoding="utf-8")
    sys.path.insert(0, str(_REPO / "hooks"))
    from modules.core.workspace_bootstrap import ensure_workspace_hooks_link

    ensure_workspace_hooks_link()

    assert not (subfolder / ".claude").exists()
    assert not (workspace / "billing" / ".claude").exists()
    assert (workspace / ".claude" / "hooks").is_symlink()
    assert (workspace / ".claude" / "hooks").resolve() == (package / "hooks").resolve()
