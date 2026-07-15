"""
`gaia context prune-workspaces` -- delete PHANTOM workspaces (0 projects) that
hold NO curated collateral, while HOLDING (never deleting) any zero-project
workspace that still carries curated memory / PCC / briefs.

Two layers are covered:
  * the writer primitive ``prune_empty_workspaces`` (governance + delete), and
  * the CLI handler ``_cmd_prune_workspaces`` (dry-run, confirmation, DB backup).

Temp DB only (GAIA_DATA_DIR redirected), so nothing touches ~/.gaia/gaia.db.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.store import writer as writer_mod
from tools.scan import classify as classify_mod


def _mk_repo(base: Path, *segments: str) -> Path:
    """Create ``base/segments.../.git`` and return the repo dir."""
    repo = base.joinpath(*segments)
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    dbp = db_path()
    # Materialize the schema.
    from gaia.store.writer import _connect
    _connect(dbp).close()
    return dbp


def _ws(dbp, name):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
            (name, name, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _project(dbp, workspace, name):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        con.execute(
            "INSERT INTO projects (workspace, name, status, scanner_ts) "
            "VALUES (?, ?, 'active', ?)",
            (workspace, name, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _memory(dbp, workspace, name, *, deleted=False):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, deleted_at) "
            "VALUES (?, ?, 'project', 'b', ?)",
            (workspace, name, "2026-01-02T00:00:00Z" if deleted else None),
        )
        con.commit()
    finally:
        con.close()


def _pcc(dbp, workspace, contract_name):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        con.execute(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
            "VALUES (?, ?, '{}')",
            (workspace, contract_name),
        )
        con.commit()
    finally:
        con.close()


def _brief(dbp, workspace, name):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES (?, ?, 'open')",
            (workspace, name),
        )
        con.commit()
    finally:
        con.close()


def _workspace_names(dbp):
    from gaia.store.writer import _connect
    con = _connect(dbp)
    try:
        return {r[0] for r in con.execute("SELECT name FROM workspaces").fetchall()}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# writer primitive: prune_empty_workspaces
# ---------------------------------------------------------------------------

def test_workspace_with_projects_is_never_pruned(tmp_db):
    _ws(tmp_db, "live")
    _project(tmp_db, "live", "repo-a")

    plan = writer_mod.prune_empty_workspaces(apply=False, db_path=tmp_db)
    assert plan["pruned"] == []
    assert plan["held"] == []


def test_phantom_without_collateral_is_prunable(tmp_db):
    _ws(tmp_db, "phantom")  # 0 projects, 0 collateral

    plan = writer_mod.prune_empty_workspaces(apply=False, db_path=tmp_db)
    assert plan["pruned"] == ["phantom"]
    assert plan["held"] == []
    # dry-run mutated nothing.
    assert "phantom" in _workspace_names(tmp_db)


def test_apply_deletes_only_confirmed_phantoms(tmp_db):
    _ws(tmp_db, "live")
    _project(tmp_db, "live", "repo-a")
    _ws(tmp_db, "phantom-a")
    _ws(tmp_db, "phantom-b")

    result = writer_mod.prune_empty_workspaces(apply=True, db_path=tmp_db)
    assert result["mode"] == "apply"
    assert sorted(result["pruned"]) == ["phantom-a", "phantom-b"]
    # The live workspace survives; the phantoms are gone.
    assert _workspace_names(tmp_db) == {"live"}


def test_phantom_with_live_memory_is_held_not_deleted(tmp_db):
    _ws(tmp_db, "has-mem")  # 0 projects but holds curated memory
    _memory(tmp_db, "has-mem", "note-1")

    result = writer_mod.prune_empty_workspaces(apply=True, db_path=tmp_db)
    assert result["pruned"] == []
    assert len(result["held"]) == 1
    held = result["held"][0]
    assert held["workspace"] == "has-mem"
    assert held["memory"] == 1
    # NOT deleted -- curated content preserved.
    assert "has-mem" in _workspace_names(tmp_db)


def test_tombstoned_memory_does_not_hold_a_phantom(tmp_db):
    """A soft-deleted (tombstoned) memory row is not live curated content, so it
    must NOT hold the phantom back from pruning."""
    _ws(tmp_db, "only-dead-mem")
    _memory(tmp_db, "only-dead-mem", "dead-note", deleted=True)

    result = writer_mod.prune_empty_workspaces(apply=True, db_path=tmp_db)
    assert result["pruned"] == ["only-dead-mem"]
    assert "only-dead-mem" not in _workspace_names(tmp_db)


def test_phantom_with_pcc_is_held(tmp_db):
    _ws(tmp_db, "has-pcc")
    _pcc(tmp_db, "has-pcc", "project_identity")

    result = writer_mod.prune_empty_workspaces(apply=True, db_path=tmp_db)
    assert result["pruned"] == []
    assert result["held"][0]["workspace"] == "has-pcc"
    assert result["held"][0]["pcc"] == 1
    assert "has-pcc" in _workspace_names(tmp_db)


def test_phantom_with_briefs_is_held(tmp_db):
    _ws(tmp_db, "has-brief")
    _brief(tmp_db, "has-brief", "some-brief")

    result = writer_mod.prune_empty_workspaces(apply=True, db_path=tmp_db)
    assert result["pruned"] == []
    assert result["held"][0]["workspace"] == "has-brief"
    assert result["held"][0]["briefs"] == 1
    assert "has-brief" in _workspace_names(tmp_db)


def test_mixed_population_partitions_correctly(tmp_db):
    _ws(tmp_db, "live"); _project(tmp_db, "live", "r")
    _ws(tmp_db, "phantom")                       # prunable
    _ws(tmp_db, "held-mem"); _memory(tmp_db, "held-mem", "n")   # held

    plan = writer_mod.prune_empty_workspaces(apply=False, db_path=tmp_db)
    assert plan["scanned"] == 3
    assert plan["pruned"] == ["phantom"]
    assert [h["workspace"] for h in plan["held"]] == ["held-mem"]


# ---------------------------------------------------------------------------
# CLI handler: dry-run / confirmation / DB backup
# ---------------------------------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_cli_dry_run_does_not_delete(tmp_db, capsys):
    from bin.cli.context import _cmd_prune_workspaces
    _ws(tmp_db, "phantom")

    rc = _cmd_prune_workspaces(_Args(dry_run=True, yes=False, json=False))
    assert rc == 0
    # Nothing deleted.
    assert "phantom" in _workspace_names(tmp_db)
    out = capsys.readouterr().out
    assert "would prune" in out


def test_cli_apply_backs_up_db_and_deletes(tmp_db, capsys):
    from bin.cli.context import _cmd_prune_workspaces
    _ws(tmp_db, "phantom")
    _ws(tmp_db, "keep-mem"); _memory(tmp_db, "keep-mem", "n")

    rc = _cmd_prune_workspaces(_Args(dry_run=False, yes=True, json=True))
    assert rc == 0

    # Phantom deleted; held workspace preserved.
    remaining = _workspace_names(tmp_db)
    assert "phantom" not in remaining
    assert "keep-mem" in remaining

    # A backup file was written next to the DB before the delete.
    backups = list(Path(tmp_db).parent.glob("*.prune.bak"))
    assert len(backups) == 1, backups
    assert backups[0].stat().st_size > 0


def test_cli_nothing_to_prune_is_clean_exit(tmp_db, capsys):
    from bin.cli.context import _cmd_prune_workspaces
    _ws(tmp_db, "live"); _project(tmp_db, "live", "r")

    rc = _cmd_prune_workspaces(_Args(dry_run=False, yes=True, json=False))
    assert rc == 0
    assert "live" in _workspace_names(tmp_db)
    # No backup created when there was nothing to delete.
    assert list(Path(tmp_db).parent.glob("*.prune.bak")) == []


# ---------------------------------------------------------------------------
# 2a regression: `gaia scan` must NOT create a phantom workspace row when its
# --workspace matches NO repo. This is the forward-looking half of the prune
# concern -- it prevents new phantoms while prune_empty_workspaces cleans the
# historical debris.
# ---------------------------------------------------------------------------

def test_scan_zero_match_creates_no_workspace_row(tmp_db, tmp_path):
    """A real (apply=True) scan whose W matches NO repo must NOT create a
    phantom workspaces row -- workspace rows are only ever created downstream
    of a matched repo."""
    _mk_repo(tmp_path, "aaxis", "aos", "aos-iac")

    report = classify_mod.scan(tmp_path / "aaxis", "acme", db_path=tmp_db, apply=True)
    assert report.resolved_workspace is None
    assert report.projects == []

    # No 'acme' workspace row was created on the 0-match scan.
    assert "acme" not in _workspace_names(tmp_db)
