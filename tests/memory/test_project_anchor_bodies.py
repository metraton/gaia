"""A project's standing notes can be read whole, whichever workspace wrote them.

What the user feels: an agent working a project asks the CLI for that
project's anchors and receives their bodies -- the decisions written from this
workspace and from any other -- while a note that a newer one replaced does not
come back, and another project's notes stay out. Runs in-process against a
temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

LIVE_HERE = "Decision: alpha releases are cut from the release train branch."
LIVE_ELSEWHERE = "Decision: alpha keeps its API schema under version control."
REPLACED = "Decision: alpha releases are cut from main."
OTHER_PROJECT = "Decision: beta ships weekly."

ROWS = (
    ("me", "alpha_release_branch", "alpha", LIVE_HERE),
    ("other", "alpha_schema_versioned", "alpha", LIVE_ELSEWHERE),
    ("me", "alpha_release_from_main", "alpha", REPLACED),
    ("me", "beta_weekly", "beta", OTHER_PROJECT),
)


def _seed(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        for workspace in ("me", "other"):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                        (workspace, workspace))
        for workspace, name, initiative, body in ROWS:
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, "
                "initiative, updated_at) "
                "VALUES (?, ?, 'project', ?, ?, 'anchor', ?, '2026-09-01T00:00:00Z')",
                (workspace, name, body, body, initiative),
            )
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
            "VALUES ('me', 'alpha_release_branch', 'alpha_release_from_main', 'supersedes')"
        )
        con.commit()
    finally:
        con.close()


def test_a_projects_live_anchor_bodies_come_back_from_every_workspace(
    tmp_path, monkeypatch, capsys, bootstrapped_db_template,
):
    import cli.memory as memory_mod

    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)
    _seed(db)

    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args([
        "memory", "get-relevant", "--workspace", "me",
        "--initiative", "alpha", "--sections", "anchor", "--json",
    ])
    capsys.readouterr()
    assert args.func(args) == 0
    bodies = [item.get("body") for item in json.loads(capsys.readouterr().out)["items"]]

    assert LIVE_HERE in bodies
    assert LIVE_ELSEWHERE in bodies
    assert REPLACED not in bodies
    assert OTHER_PROJECT not in bodies
