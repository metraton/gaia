"""Installing never declares a workspace; a declared root anchors the install beneath it.

Covers the bootstrap registering no workspace, `gaia install` leaving the
registry as the user declared it, a nested declared workspace keeping its own
projects when the enclosing one is scanned, a declared name never rebinding,
and a subfolder resolving to the declared root instead of seeding a `.claude`
of its own.
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


def _bootstrap(tmp_path: Path, db: Path, **extra) -> None:
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "bootstrap_database.py")],
        env={**_env(tmp_path, db), **extra}, capture_output=True, text=True, timeout=300,
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


def test_bootstrap_registers_no_workspace_even_when_one_is_named(tmp_path):
    db = tmp_path / "gaia.db"
    workspace = tmp_path / "acme"
    workspace.mkdir()

    _bootstrap(tmp_path, db, WORKSPACE=str(workspace))

    assert _rows(db, "SELECT name FROM workspaces") == []


_SQLITE3_SHIM = '''
import sqlite3, sys
sql = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
con = sqlite3.connect(sys.argv[1])
try:
    rows = con.execute(sql).fetchall()
except sqlite3.ProgrammingError:
    con.executescript(sql)
    rows = []
con.commit()
for row in rows:
    print("|".join("" if v is None else str(v) for v in row))
'''


def _sqlite3_on_path(tmp_path: Path) -> str:
    """A `sqlite3 DB [SQL]` stand-in on PATH; the CLI is not part of the own toolchain."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "sqlite3"
    shim.write_text(f"#!{sys.executable}\n{_SQLITE3_SHIM}", encoding="utf-8")
    shim.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def test_shell_bootstrap_registers_no_workspace_even_when_one_is_named(tmp_path):
    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    workspace = _git_repo(tmp_path / "acme")
    subprocess.run(
        ["git", "-C", str(workspace), "remote", "add", "origin", "git@github.com:o/acme.git"],
        check=True,
    )

    proc = subprocess.run(
        ["bash", str(_REPO / "scripts" / "bootstrap_database.sh")],
        env={**_env(tmp_path, db), "WORKSPACE": str(workspace), "PATH": _sqlite3_on_path(tmp_path)},
        capture_output=True, text=True, timeout=300,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _rows(db, "SELECT name FROM workspaces") == []


def test_install_inside_a_declared_workspace_leaves_the_registry_as_declared(tmp_path):
    from gaia.store.writer import declare_workspace

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    workspace = tmp_path / "acme"
    subfolder = _git_repo(workspace / "billing")
    declare_workspace("acme", workspace, db_path=db)

    proc = subprocess.run(
        [sys.executable, str(_REPO / "bin" / "gaia"), "install", "--channel", "npm",
         "--workspace", str(subfolder), "--quiet"],
        env=_env(tmp_path, db), capture_output=True, text=True, timeout=600,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _rows(db, "SELECT name, root_path FROM workspaces") == [
        ("acme", str(workspace.resolve()))
    ]
    assert _rows(db, "SELECT workspace, name FROM projects") == []


def test_scanning_an_enclosing_workspace_leaves_a_nested_one_its_projects(tmp_path):
    from gaia.store.writer import declare_workspace
    from tools.scan.classify import scan

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    outer = tmp_path / "outer"
    inner = outer / "inner"
    _git_repo(inner / "lib")
    declare_workspace("inner", inner, db_path=db)
    scan(inner, "inner", db_path=db)
    _git_repo(inner / "later")
    _git_repo(outer / "app")
    declare_workspace("outer", outer, db_path=db)

    report = scan(outer, "outer", db_path=db)

    assert sorted(repo["repo"] for repo in report.foreign_repos) == ["later", "lib"]
    assert sorted(_rows(db, "SELECT workspace, name FROM projects")) == [
        ("inner", "lib"),
        ("outer", "app"),
    ]


def test_a_declared_name_is_never_rebound_to_another_root(tmp_path):
    from gaia.store.writer import WorkspaceDeclarationError, declare_workspace

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    first = tmp_path / "a" / "shop"
    second = tmp_path / "b" / "shop"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert declare_workspace("shop", first, db_path=db) == "created"

    assert declare_workspace("shop", first, db_path=db) == "noop"
    with pytest.raises(WorkspaceDeclarationError):
        declare_workspace("shop", second, db_path=db)
    assert _rows(db, "SELECT root_path FROM workspaces WHERE name = 'shop'") == [
        (str(first.resolve()),)
    ]


def test_a_subfolder_resolves_to_the_declared_root_and_seeds_no_claude_dir(
    tmp_path, monkeypatch
):
    from gaia.install_root import installed_root
    from gaia.store.writer import declare_workspace

    db = tmp_path / "gaia.db"
    _bootstrap(tmp_path, db)
    workspace = tmp_path / "acme"
    subfolder = _git_repo(workspace / "billing") / "src"
    subfolder.mkdir()
    declare_workspace("acme", workspace, db_path=db)
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
