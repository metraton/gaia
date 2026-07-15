"""Regression tests for the v31 -> v32 `memory.initiative` migration.

`memory.initiative` (v32) is the canonical project/initiative grouping key --
clean and vantage-independent -- that unifies BOTH git projects (basename of
the git anchor) and logical (non-repo) initiatives (branchkinect, buildwiz,
...). project_ref is untouched (it stays the git-common-dir path).

Group 1 exercises the REAL schema.sql (via gaia.store.writer._connect, which
materializes it on a fresh DB) so drift between schema.sql and these tests is
impossible by construction. Group 2 applies the standalone migration file to a
synthetic v31-shaped DB to prove the in-place upgrade path INCLUDING the
backfill priority: git -> basename, allow-list -> initiative, gaia-internal
token -> 'gaia', unknown -> NULL (never a first-token guess).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "scripts" / "migrations" / "v31_to_v32.sql"


def _columns(con: sqlite3.Connection, table: str) -> dict[str, None]:
    return {row["name"]: None for row in con.execute(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------------
# Group 1: fresh install via the real schema.sql
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    path = db_path()
    con = _connect(path)
    con.close()
    return path


def test_initiative_column_exists_and_defaults_null(fresh_db: Path) -> None:
    con = sqlite3.connect(str(fresh_db))
    con.row_factory = sqlite3.Row
    try:
        assert "initiative" in _columns(con, "memory")
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body) "
            "VALUES ('me', 'project_x', 'project', 'b')"
        )
        con.commit()
        row = con.execute(
            "SELECT initiative FROM memory WHERE workspace='me' AND name='project_x'"
        ).fetchone()
        assert row["initiative"] is None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Group 2: standalone migration file applied to a synthetic v31-shaped DB
# ---------------------------------------------------------------------------

_V31_MINIMAL_SCHEMA = """
CREATE TABLE workspaces (
    name        TEXT NOT NULL PRIMARY KEY,
    identity    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT NOT NULL DEFAULT 'log',
    status            TEXT,
    project_ref       TEXT,
    deleted_at        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
INSERT INTO schema_version (version, applied_at, description)
VALUES (31, '2026-01-01T00:00:00Z', 'synthetic v31 baseline');
"""


@pytest.fixture()
def v31_db(tmp_path) -> Path:
    db_path = tmp_path / "v31.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_V31_MINIMAL_SCHEMA)
    con.commit()
    con.close()
    return db_path


def _apply_migration_sql(con: sqlite3.Connection) -> None:
    con.executescript(_MIGRATION_PATH.read_text(encoding="utf-8"))


def _seed(con: sqlite3.Connection, name: str, project_ref: str | None = None) -> None:
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, project_ref) "
        "VALUES ('me', ?, 'atom', 'b', ?)",
        (name, project_ref),
    )


def _initiative(con: sqlite3.Connection, name: str):
    return con.execute(
        "SELECT initiative FROM memory WHERE workspace='me' AND name=?", (name,)
    ).fetchone()["initiative"]


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"


def test_migration_applies_cleanly_and_adds_column(v31_db: Path) -> None:
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        assert "initiative" not in _columns(con, "memory")
        _apply_migration_sql(con)
        con.commit()
        assert "initiative" in _columns(con, "memory")
    finally:
        con.close()


def test_backfill_git_project_ref_uses_repo_basename(v31_db: Path) -> None:
    """(a) git project_ref -> repo basename with the trailing '.git' stripped."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _seed(con, "atom_gaia_note", project_ref="/home/jorge/ws/me/gaia/.git")
        _seed(con, "atom_balance_note", project_ref="/home/jorge/ws/me/balance/.git")
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        assert _initiative(con, "atom_gaia_note") == "gaia"
        assert _initiative(con, "atom_balance_note") == "balance"
    finally:
        con.close()


def test_backfill_allow_list_tokens(v31_db: Path) -> None:
    """(b) whole-token allow-list match -> that initiative."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        cases = {
            "decision_branchkinect_new_gcp_infrastructure": "branchkinect",
            "project_buildwiz_bitbucket_github_migration": "buildwiz",
            "bildwiz_suggestion_model_fix": "bildwiz",
            "project_axisio_open_threads": "axisio",
            "atom_aos_gotchas": "aos",
            "nfi_newco_pitot_state": "nfi",
            "project_diagram_builder_v2_redesign": "diagram_builder",
            "project_century_diagram_framework": "century",
        }
        for name in cases:
            _seed(con, name)
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        for name, expected in cases.items():
            assert _initiative(con, name) == expected, name
    finally:
        con.close()


def test_backfill_leftmost_token_wins_on_cooccurrence(v31_db: Path) -> None:
    """Real co-occurrences resolve to the LEFTMOST allow-list token, encoded by
    branch order: branchkinect before century, century before diagram_builder."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _seed(con, "project_branchkinect_ssh_key_century_pending")
        _seed(con, "project_century_skill_hub_diagram_builder_sync")
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        assert _initiative(con, "project_branchkinect_ssh_key_century_pending") == "branchkinect"
        assert _initiative(con, "project_century_skill_hub_diagram_builder_sync") == "century"
    finally:
        con.close()


def test_backfill_gaia_internal_tokens(v31_db: Path) -> None:
    """(c) a gaia-internal token with no allow-list hit -> 'gaia'."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        internal = [
            "decision_contract_first_agent_model",
            "project_scan_v2_execution_plan",
            "feedback_release_learnings",
            "atom_approval_metrics_vocabulary",
            "project_security_hooks_pending",
            "decision_memory_close_verbs",
            "atom_t3_classification_overbroad",
            "project_mutation_inventory_fullcore",
        ]
        for name in internal:
            _seed(con, name)
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        for name in internal:
            assert _initiative(con, name) == "gaia", name
    finally:
        con.close()


def test_backfill_unknown_is_null_never_first_token(v31_db: Path) -> None:
    """(d) no git anchor, no allow-list token, no internal token -> NULL.
    Crucially NOT the first slug token ('wsl', 'repos', 'agent')."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        unknown = [
            "wsl_mirrored_docker_setup",
            "atom_repos_layout",
            "decision_agent_naming_convention",
            "feedback_file_organization",
        ]
        for name in unknown:
            _seed(con, name)
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        for name in unknown:
            assert _initiative(con, name) is None, name
    finally:
        con.close()


def test_backfill_substring_does_not_match_token(v31_db: Path) -> None:
    """Token matching is whole-token, not substring: a slug that merely CONTAINS
    an initiative as a substring inside another word must not match."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        # 'gaian' contains 'gaia', 'chaos' contains 'aos', 'rndm' contains 'rnd'
        # -- none is a whole '_'-delimited token, so all resolve to NULL.
        _seed(con, "atom_gaian_theory")
        _seed(con, "atom_chaos_engineering")
        _seed(con, "atom_rndm_seed")
        con.commit()

        _apply_migration_sql(con)
        con.commit()

        assert _initiative(con, "atom_gaian_theory") is None
        assert _initiative(con, "atom_chaos_engineering") is None
        assert _initiative(con, "atom_rndm_seed") is None
    finally:
        con.close()


def test_backfill_is_idempotent_on_rerun(v31_db: Path) -> None:
    """Re-running the backfill UPDATE (WHERE initiative IS NULL) does not change
    an already-resolved key and does not fabricate one for a NULL row."""
    con = sqlite3.connect(str(v31_db))
    con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO workspaces (name) VALUES ('me')")
        _seed(con, "atom_aos_gotchas")
        _seed(con, "wsl_mirrored_docker_setup")
        con.commit()

        _apply_migration_sql(con)
        con.commit()
        first_aos = _initiative(con, "atom_aos_gotchas")
        first_wsl = _initiative(con, "wsl_mirrored_docker_setup")

        # Re-run only the backfill UPDATE (the ALTER cannot repeat -- runner
        # guards it in production; here the column already exists).
        mig = _MIGRATION_PATH.read_text(encoding="utf-8")
        con.executescript(mig[mig.index("UPDATE memory"):])
        con.commit()

        assert _initiative(con, "atom_aos_gotchas") == first_aos == "aos"
        assert _initiative(con, "wsl_mirrored_docker_setup") == first_wsl is None
    finally:
        con.close()
