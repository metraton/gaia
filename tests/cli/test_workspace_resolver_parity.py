"""Every CLI that accepts ``--workspace`` resolves the same default workspace.

The coordination CLIs (brief, plan, task, ac, evidence, milestone, ...) and the
memory CLI each carried their own ``_resolve_workspace``; brief and its siblings
named the directory (``gaia.project.current``) and ignored the dispatch env
vars, while memory asked which registered project contains the directory. The
same cwd then read two different workspaces. This matrix pins them to one
answer for the same cwd and environment.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_CLI_MODULES = (
    "cli.brief",
    "cli.plan",
    "cli.task",
    "cli.ac",
    "cli.evidence",
    "cli.milestone",
    "cli.memory",
    "cli.memory_story",
    "cli.notifications",
    "cli.schedule",
    "cli.query",
    "cli.defects",
    "cli.contract",
)


@pytest.fixture()
def layout(tmp_path, monkeypatch):
    """Two registered workspaces, the inner one nested inside the outer root.

    outer/            workspace 'outer_ws', project 'alpha' at outer/alpha
    outer/inner/      workspace 'inner_ws', project 'beta' at outer/inner/beta
    elsewhere/        registered by nobody
    """
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    for key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(key, raising=False)

    outer = tmp_path / "outer"
    alpha = outer / "alpha"
    inner = outer / "inner"
    beta = inner / "beta"
    elsewhere = tmp_path / "elsewhere"
    for d in (alpha, beta / "src", elsewhere):
        d.mkdir(parents=True)

    from gaia.paths import db_path
    from gaia.store.writer import _connect

    con = _connect(db_path())
    try:
        for name, root in (("outer_ws", outer), ("inner_ws", inner)):
            con.execute(
                "INSERT INTO workspaces (name, root_path) VALUES (?, ?)",
                (name, str(root)),
            )
        for ws, name, path in (("outer_ws", "alpha", alpha), ("inner_ws", "beta", beta)):
            con.execute(
                "INSERT INTO projects (workspace, name, path, status) "
                "VALUES (?, ?, ?, 'active')",
                (ws, name, str(path)),
            )
        con.commit()
    finally:
        con.close()

    return {"outer": outer, "alpha": alpha, "beta_src": beta / "src", "elsewhere": elsewhere}


def _resolved_by_every_cli() -> dict[str, str]:
    return {
        name: importlib.import_module(name)._resolve_workspace(None)
        for name in _CLI_MODULES
    }


@pytest.mark.parametrize(
    ("cwd_key", "expected"),
    [
        ("beta_src", "inner_ws"),
        ("alpha", "outer_ws"),
        ("outer", "outer"),
        ("elsewhere", "elsewhere"),
    ],
)
def test_same_cwd_resolves_the_same_workspace(layout, monkeypatch, cwd_key, expected):
    monkeypatch.chdir(layout[cwd_key])
    resolved = _resolved_by_every_cli()
    assert set(resolved.values()) == {expected}, resolved


@pytest.mark.parametrize("env_key", ["GAIA_WORKSPACE", "GAIA_DISPATCH_WORKSPACE"])
def test_env_workspace_wins_over_cwd_for_every_cli(layout, monkeypatch, env_key):
    monkeypatch.chdir(layout["beta_src"])
    monkeypatch.setenv(env_key, "forced_ws")
    resolved = _resolved_by_every_cli()
    assert set(resolved.values()) == {"forced_ws"}, resolved


def test_unresolvable_workspace_lands_on_global_for_every_cli(layout, monkeypatch):
    """No flag, no env and a failing lookup name no installation's workspace."""
    import gaia.project

    def unresolvable(*_args, **_kwargs):
        raise RuntimeError("no workspace resolves here")

    monkeypatch.chdir(layout["elsewhere"])
    monkeypatch.setattr(gaia.project, "containing_workspace", unresolvable)
    monkeypatch.setattr(gaia.project, "current", unresolvable)
    resolved = _resolved_by_every_cli()
    assert set(resolved.values()) == {"global"}, resolved


def test_explicit_flag_wins_over_env_for_every_cli(layout, monkeypatch):
    monkeypatch.chdir(layout["beta_src"])
    monkeypatch.setenv("GAIA_WORKSPACE", "forced_ws")
    resolved = {
        name: importlib.import_module(name)._resolve_workspace("explicit_ws")
        for name in _CLI_MODULES
    }
    assert set(resolved.values()) == {"explicit_ws"}, resolved
