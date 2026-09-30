"""Curated memory is append-only: nothing edits a row in place unsigned.

A changed agreement is a new row that supersedes the old one, so the `edit`
verb is retired and names that replacement. The one in-place rewrite left,
`add` over an existing name to correct an error, needs `--replace`, is signed
(T3) for every caller, and keeps the prior value in memory_history.

Uses the real CLI parser and writer against a temporary GAIA_DATA_DIR.
"""

from __future__ import annotations

import argparse
import json as _json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cli import memory as memory_mod  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

SLUG = "decision_bildwiz_release_cadence"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path -- never the real ~/.gaia/gaia.db."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    for key in ("GAIA_DISPATCH_AGENT", "GAIA_SESSION_ID",
                "GAIA_DISPATCH_WORKSPACE", "GAIA_DB"):
        monkeypatch.delenv(key, raising=False)
    from gaia.paths import db_path
    return db_path()


def _run(capsys, *argv):
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="subcommand"))
    rc = memory_mod.cmd_memory(parser.parse_args(["memory", *argv, "--json"]))
    return rc, _json.loads(capsys.readouterr().out)


def _add(capsys, body, *extra):
    return _run(
        capsys, "add", f"--name={SLUG}", "--type=decision", "--initiative=bildwiz",
        "--workspace=me",
        "--description=release cadence", f"--body={body}", *extra,
    )


def _seed(capsys):
    rc, out = _add(capsys, "weekly")
    assert rc == 0, out


def _history_bodies(db_path: Path) -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in con.execute(
            "SELECT before_body FROM memory_history WHERE name = ? ORDER BY id",
            (SLUG,),
        )]
    finally:
        con.close()


def test_edit_is_retired_and_names_its_replacement(tmp_db, capsys):
    _seed(capsys)

    rc, out = _run(capsys, "edit", f"--name={SLUG}", "--field=body", "--content=daily")

    assert rc == 1
    assert out["code"] == "verb_retired"
    assert "--kind=supersedes" in out["error"]
    assert "--replace" in out["error"]
    assert _run(capsys, "show", SLUG)[1]["body"] == "weekly"


def test_add_over_an_existing_name_is_refused_without_replace(tmp_db, capsys):
    _seed(capsys)

    rc, out = _add(capsys, "daily")

    assert rc == 1
    assert out["code"] == "name_exists"
    assert _run(capsys, "show", SLUG)[1]["body"] == "weekly"


def test_add_replace_rewrites_and_keeps_the_prior_value_in_history(tmp_db, capsys):
    _seed(capsys)

    rc, out = _add(capsys, "daily", "--replace")

    assert rc == 0, out
    assert _run(capsys, "show", SLUG)[1]["body"] == "daily"
    assert "weekly" in _history_bodies(tmp_db)


def test_an_abbreviation_of_replace_is_refused_by_the_parser(tmp_db, capsys):
    """The signature matches the literal flag; `--repl` must not reach it."""
    _seed(capsys)

    with pytest.raises(SystemExit):
        _add(capsys, "daily", "--repl")

    assert _run(capsys, "show", SLUG)[1]["body"] == "weekly"


def test_add_replace_re_anchors_a_row_written_under_the_wrong_project(tmp_db, capsys):
    assert _add(capsys, "weekly", "--project-ref=github.com/me/wrong")[0] == 0

    rc, out = _add(capsys, "weekly", "--project-ref=github.com/me/right", "--replace")

    assert rc == 0, out
    assert _run(capsys, "show", SLUG)[1]["project_ref"] == "github.com/me/right"


@pytest.mark.parametrize("command,signed", [
    (f"gaia memory add --name={SLUG} --type=decision --body=daily --replace", True),
    (f"gaia memory add --name {SLUG} --replace --body daily", True),
    (f"gaia memory delete {SLUG} --yes", True),
    (f"gaia memory add --name={SLUG} --type=decision --body=daily", False),
    (f"gaia memory append {SLUG} --body=more", False),
    (f"gaia memory reclassify {SLUG} --class=anchor", False),
    (f"gaia memory link new_row {SLUG} --kind=supersedes", False),
])
def test_only_the_in_place_rewrite_and_delete_are_signed(command, signed):
    assert detect_mutative_command(command).is_mutative is signed
