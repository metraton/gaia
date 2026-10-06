"""Only workspaces the user declares exist, and one resolver answers which one a folder is in.

A workspace is a row with a root_path recorded by ``gaia workspace declare``.
Install, session start and scan never create one, every resolver of the
current workspace answers with the declared root nearest above the folder, and
outside every declared root the CLI names the folder and the declare command.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO, _REPO / "bin", _REPO / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DECLARE_HINT = "gaia workspace declare <name> <path>"


@pytest.fixture()
def gaia_home(tmp_path, monkeypatch):
    """An isolated Gaia data home with a schema'd, empty database."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    data.mkdir()
    db = data / "gaia.db"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE", "GAIA_DISPATCH_AGENT", "WORKSPACE"):
        monkeypatch.delenv(key, raising=False)

    from gaia.store.writer import _connect

    _connect(db).close()
    return db


def _gaia(*argv, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_REPO / "bin" / "gaia"), *argv],
        cwd=str(cwd), env=dict(os.environ), capture_output=True, text=True, timeout=600,
    )


def _rows(db: Path, sql: str) -> list:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _resolvers(cwd: Path) -> dict:
    from gaia.project import cli_workspace, current, resolve_workspace

    return {
        "current": current(cwd),
        "resolve_workspace": resolve_workspace(cwd),
        "cli_workspace": cli_workspace(cwd=cwd),
    }


def test_declare_records_the_workspace_with_its_root(gaia_home, tmp_path):
    root = tmp_path / "acme"
    root.mkdir()

    proc = _gaia("workspace", "declare", "acme", str(root), cwd=tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _rows(gaia_home, "SELECT name, root_path FROM workspaces") == [
        ("acme", str(root.resolve()))
    ]
    assert _gaia("workspace", "list", cwd=tmp_path).stdout.split() == ["acme", str(root.resolve())]


def test_declare_never_rebinds_a_declared_name_or_root(gaia_home, tmp_path):
    first, second = tmp_path / "a" / "shop", tmp_path / "b" / "shop"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert _gaia("workspace", "declare", "shop", str(first), cwd=tmp_path).returncode == 0

    assert _gaia("workspace", "declare", "shop", str(first), cwd=tmp_path).returncode == 0
    assert _gaia("workspace", "declare", "shop", str(second), cwd=tmp_path).returncode != 0
    assert _gaia("workspace", "declare", "other", str(first), cwd=tmp_path).returncode != 0
    assert _rows(gaia_home, "SELECT name, root_path FROM workspaces") == [
        ("shop", str(first.resolve()))
    ]


def test_inside_a_repo_every_resolver_answers_the_declared_root_not_the_repo(gaia_home, tmp_path):
    root = tmp_path / "acme"
    src = _git_repo(root / "billing") / "src"
    src.mkdir()
    assert _gaia("workspace", "declare", "acme", str(root), cwd=tmp_path).returncode == 0

    assert set(_resolvers(src).values()) == {"acme"}, _resolvers(src)


def test_the_nearest_declared_root_wins_over_an_enclosing_one_and_over_projects(
    gaia_home, tmp_path
):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    beta = _git_repo(inner / "beta")
    alpha = _git_repo(outer / "alpha")
    for name, root in (("outer_ws", outer), ("inner_ws", inner)):
        assert _gaia("workspace", "declare", name, str(root), cwd=tmp_path).returncode == 0
    con = sqlite3.connect(gaia_home)
    con.execute(
        "INSERT INTO projects (workspace, name, path, status) VALUES ('outer_ws', 'beta', ?, 'active')",
        (str(beta),),
    )
    con.commit()
    con.close()

    assert set(_resolvers(beta).values()) == {"inner_ws"}, _resolvers(beta)
    assert set(_resolvers(alpha).values()) == {"outer_ws"}, _resolvers(alpha)


def test_a_row_without_root_is_not_a_workspace(gaia_home, tmp_path):
    repo = _git_repo(tmp_path / "billing")
    con = sqlite3.connect(gaia_home)
    con.execute("INSERT INTO workspaces (name) VALUES ('billing')")
    con.execute(
        "INSERT INTO projects (workspace, name, path, status) VALUES ('billing', 'billing', ?, 'active')",
        (str(repo),),
    )
    con.commit()
    con.close()

    assert set(_resolvers(repo).values()) == {"global"}, _resolvers(repo)
    assert _gaia("workspace", "list", cwd=tmp_path).stdout.split() == []


def test_outside_every_declared_workspace_the_cli_explains_how_to_declare(gaia_home, tmp_path):
    folder = _git_repo(tmp_path / "loose")

    proc = _gaia("workspace", "current", cwd=folder)

    assert proc.returncode != 0
    message = proc.stdout + proc.stderr
    assert str(folder.resolve()) in message
    assert "is not inside a declared workspace" in message
    assert DECLARE_HINT in message
    assert "loose" not in proc.stdout.split()


def test_install_in_an_undeclared_folder_creates_no_workspace(gaia_home, tmp_path):
    folder = tmp_path / "acme"
    _git_repo(folder / "billing")

    proc = _gaia("install", "--channel", "npm", "--workspace", str(folder), cwd=folder)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _rows(gaia_home, "SELECT name FROM workspaces") == []
    assert DECLARE_HINT in proc.stdout + proc.stderr


def test_session_start_in_an_undeclared_folder_creates_no_workspace(
    gaia_home, tmp_path, monkeypatch
):
    from modules.session import session_lifecycle

    folder = tmp_path / "acme"
    _git_repo(folder / "billing")
    monkeypatch.chdir(folder)
    monkeypatch.setattr(
        "modules.session.plugin_upgrade.reconcile_plugin_install", lambda _root: ""
    )

    upgrade_notice, workspace_notice = session_lifecycle._reconcile_install()

    assert upgrade_notice == ""
    assert str(folder.resolve()) in workspace_notice
    assert DECLARE_HINT in workspace_notice
    assert _rows(gaia_home, "SELECT name FROM workspaces") == []


def test_scan_of_an_undeclared_workspace_is_refused_and_writes_nothing(gaia_home, tmp_path):
    folder = tmp_path / "acme"
    _git_repo(folder / "billing")

    refused = _gaia("scan", "--workspace", "acme", str(folder), cwd=folder)

    assert refused.returncode != 0
    assert DECLARE_HINT in refused.stdout + refused.stderr
    assert _rows(gaia_home, "SELECT name FROM workspaces") == []
    assert _rows(gaia_home, "SELECT name FROM projects") == []

    assert _gaia("workspace", "declare", "acme", str(folder), cwd=tmp_path).returncode == 0
    scanned = _gaia("scan", "--workspace", "acme", str(folder), cwd=folder)

    assert scanned.returncode == 0, scanned.stdout + scanned.stderr
    assert _rows(gaia_home, "SELECT workspace, name FROM projects") == [("acme", "billing")]
    assert _rows(gaia_home, "SELECT name, root_path FROM workspaces") == [
        ("acme", str(folder.resolve()))
    ]
