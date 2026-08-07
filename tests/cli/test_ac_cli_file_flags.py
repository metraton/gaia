"""Gap 1 (2026-08-03 CLI-gap fix): `gaia ac add|edit` file-based long-text
input, and `gaia ac edit`'s in-place update (id + list position preserved).

`gaia ac edit` already existed before this fix and already wrapped
gaia.briefs.store.update_ac -- what was missing was a file-input path for
long evidence_shape/description prose (the operator's own workaround:
`<placeholder>` tokens and '; expect ...' clauses inlined as a shell argument
trip the command pre-execution security scan). This file exercises the new
--description-file / --evidence-shape-file flags added to `add` and `edit`,
and confirms `edit` never reorders or reassigns the AC's id.

Matchable by ``pytest tests/ -k ac_cli_file_flags -q``.
"""

from __future__ import annotations

import argparse
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


def _seed_brief_with_three_acs(tmp_db: Path, brief: str = "ac-brief") -> None:
    from gaia.briefs import upsert_brief, add_ac

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    add_ac(
        "me", brief, "AC-1", description="first", evidence_type="test",
        db_path=tmp_db,
    )
    add_ac(
        "me", brief, "AC-2", description="second", evidence_type="test",
        db_path=tmp_db,
    )
    add_ac(
        "me", brief, "AC-3", description="third", evidence_type="test",
        db_path=tmp_db,
    )


def _add_args(**overrides):
    base = dict(
        brief="ac-brief", ac_id="AC-4", description=None, description_file=None,
        evidence_type=None, evidence_shape=None, evidence_shape_file=None,
        artifact_path=None, workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _edit_args(**overrides):
    base = dict(
        brief="ac-brief", ac_id="AC-2", description=None, description_file=None,
        evidence_type=None, evidence_shape=None, evidence_shape_file=None,
        artifact_path=None, workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _get_ac_rows(tmp_db, brief="ac-brief"):
    """Raw row-id-visible read, deliberately bypassing get_brief()'s own AC
    projection (gaia.briefs.store.get_brief) -- that projection selects only
    ac_id/description/evidence_type/evidence_shape/artifact_path and omits
    the acceptance_criteria.id primary key entirely, so it cannot be used to
    assert "the row id did not change" (this file's whole point)."""
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


# ---------------------------------------------------------------------------
# --evidence-shape-file / --description-file on `add`
# ---------------------------------------------------------------------------

def test_add_reads_evidence_shape_from_file(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.ac import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_three_acs(tmp_db)

    shape_file = tmp_path / "shape.txt"
    shape_text = (
        "Run: kubectl get pods -n <namespace>; expect all pods Running. "
        "Placeholder <namespace> must be substituted before use."
    )
    shape_file.write_text(shape_text, encoding="utf-8")

    args = _add_args(
        description="Fourth AC", evidence_shape_file=str(shape_file), json=True,
    )
    assert _cmd_add(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ac_id"] == "AC-4"

    rows = _get_ac_rows(tmp_db)
    row = next(r for r in rows if r["ac_id"] == "AC-4")
    assert row["evidence_shape"] == shape_text


def test_add_reads_description_from_stdin(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.ac import _cmd_add

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("Piped description"))
    _seed_brief_with_three_acs(tmp_db)

    args = _add_args(description_file="-", json=True)
    assert _cmd_add(args) == 0

    rows = _get_ac_rows(tmp_db)
    row = next(r for r in rows if r["ac_id"] == "AC-4")
    assert row["description"] == "Piped description"


def test_add_description_and_description_file_are_mutex_at_argparse_level():
    """The CLI-level mutex group -- verified via the registered parser, not
    just the handler -- since the handler alone cannot see argparse's own
    mutual-exclusion error."""
    from cli.ac import register
    import argparse as _argparse

    parser = _argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    with pytest.raises(SystemExit):
        parser.parse_args([
            "ac", "add", "b", "AC-1",
            "--description", "x", "--description-file", "/tmp/y",
        ])


# ---------------------------------------------------------------------------
# `edit` preserves id AND list position (the actual bug remove+add caused)
# ---------------------------------------------------------------------------

def test_edit_preserves_id_and_position(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.ac import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_three_acs(tmp_db)

    before = _get_ac_rows(tmp_db)
    assert [r["ac_id"] for r in before] == ["AC-1", "AC-2", "AC-3"]
    ac2_row_id_before = before[1]["id"]

    args = _edit_args(description="second, corrected", json=True)
    assert _cmd_edit(args) == 0

    after = _get_ac_rows(tmp_db)
    assert [r["ac_id"] for r in after] == ["AC-1", "AC-2", "AC-3"], (
        "edit must not reorder the AC list"
    )
    ac2_after = after[1]
    assert ac2_after["id"] == ac2_row_id_before, (
        "edit must preserve the AC row's id (unlike remove+add)"
    )
    assert ac2_after["description"] == "second, corrected"


def test_edit_reads_evidence_shape_from_file_with_placeholders(
    tmp_db, tmp_path, monkeypatch,
):
    from cli.ac import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_three_acs(tmp_db)

    shape_file = tmp_path / "shape.txt"
    shape_text = (
        "gcloud compute addresses describe <address-name> --region=<region>; "
        "expect status RESERVED"
    )
    shape_file.write_text(shape_text, encoding="utf-8")

    args = _edit_args(evidence_shape_file=str(shape_file))
    assert _cmd_edit(args) == 0

    rows = _get_ac_rows(tmp_db)
    row = next(r for r in rows if r["ac_id"] == "AC-2")
    assert row["evidence_shape"] == shape_text


def test_edit_requires_at_least_one_field(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.ac import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_three_acs(tmp_db)

    args = _edit_args(json=True)
    assert _cmd_edit(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_edit_missing_file_reports_clean_error(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.ac import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_brief_with_three_acs(tmp_db)

    args = _edit_args(evidence_shape_file=str(tmp_path / "does-not-exist.txt"))
    assert _cmd_edit(args) == 1
    err = capsys.readouterr().err
    assert "does-not-exist.txt" in err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
