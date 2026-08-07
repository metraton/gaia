"""Gap 1 (2026-08-03 CLI-gap fix): `gaia brief ac edit` -- the nested surface
under `gaia brief ac` that the operator actually reached for tonight (per
memory anchor negative_brief_ac_update_no_cli_verb) gains an `edit` action
alongside its existing `add`/`remove`, wired to gaia.briefs.store.update_ac
via the same file-based long-text convention as `gaia ac edit` and `gaia
brief edit --content-file`.

Matchable by ``pytest tests/ -k brief_ac_edit_cli -q``.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_brief_with_two_acs(tmp_db: Path, brief: str = "nested-ac-brief") -> None:
    from gaia.briefs import upsert_brief, add_ac

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    add_ac("me", brief, "AC-1", description="first", db_path=tmp_db)
    add_ac("me", brief, "AC-2", description="second", db_path=tmp_db)


def _raw_ac_rows(tmp_db, brief="nested-ac-brief"):
    import sqlite3

    con = sqlite3.connect(str(tmp_db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT ac.id, ac.ac_id, ac.description, ac.evidence_shape "
            "FROM acceptance_criteria ac "
            "JOIN briefs b ON b.id = ac.brief_id "
            "WHERE b.workspace = 'me' AND b.name = ? "
            "ORDER BY ac.id",
            (brief,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _edit_args(**overrides):
    base = dict(
        ac_action="edit", brief="nested-ac-brief", id="AC-1",
        description=None, description_file=None,
        evidence_type=None, evidence_shape=None, evidence_shape_file=None,
        artifact=None, workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_brief_ac_edit_registered_in_parser():
    """`edit` must appear alongside add/remove in the registered CLI, not
    just be reachable by hand-building a Namespace."""
    from cli.brief import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    args = parser.parse_args([
        "brief", "ac", "edit", "some-brief", "--id=AC-1",
        "--description", "x",
    ])
    assert args.ac_action == "edit"
    assert args.id == "AC-1"


def test_brief_ac_edit_preserves_id_and_position(tmp_db, tmp_path, monkeypatch):
    from cli.brief import _cmd_ac

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs(tmp_db)

    before = _raw_ac_rows(tmp_db)
    ac1_id_before = before[0]["id"]

    args = _edit_args(description="first, corrected")
    assert _cmd_ac(args) == 0

    after = _raw_ac_rows(tmp_db)
    assert [r["ac_id"] for r in after] == ["AC-1", "AC-2"]
    assert after[0]["id"] == ac1_id_before
    assert after[0]["description"] == "first, corrected"


def test_brief_ac_edit_evidence_shape_from_file(tmp_db, tmp_path, monkeypatch):
    from cli.brief import _cmd_ac

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs(tmp_db)

    shape_file = tmp_path / "shape.txt"
    shape_text = "kubectl get pods -n <ns>; expect Running"
    shape_file.write_text(shape_text, encoding="utf-8")

    args = _edit_args(evidence_shape_file=str(shape_file))
    assert _cmd_ac(args) == 0

    rows = _raw_ac_rows(tmp_db)
    row = next(r for r in rows if r["ac_id"] == "AC-1")
    assert row["evidence_shape"] == shape_text


def test_brief_ac_edit_description_from_stdin(tmp_db, tmp_path, monkeypatch):
    from cli.brief import _cmd_ac

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped desc"))
    _seed_brief_with_two_acs(tmp_db)

    args = _edit_args(description_file="-")
    assert _cmd_ac(args) == 0

    rows = _raw_ac_rows(tmp_db)
    row = next(r for r in rows if r["ac_id"] == "AC-1")
    assert row["description"] == "piped desc"


def test_brief_ac_edit_requires_at_least_one_field(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.brief import _cmd_ac

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs(tmp_db)

    args = _edit_args(json=True)
    assert _cmd_ac(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_brief_ac_add_still_works_unaffected(tmp_db, tmp_path, monkeypatch):
    """Regression guard: adding the edit action must not disturb add/remove."""
    from cli.brief import _cmd_ac

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_two_acs(tmp_db)

    add_args = argparse.Namespace(
        ac_action="add", brief="nested-ac-brief", id="AC-3",
        description="third", description_file=None,
        evidence_type=None, evidence_shape=None, evidence_shape_file=None,
        artifact=None, workspace="me", json=False,
    )
    assert _cmd_ac(add_args) == 0

    rows = _raw_ac_rows(tmp_db)
    assert [r["ac_id"] for r in rows] == ["AC-1", "AC-2", "AC-3"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
