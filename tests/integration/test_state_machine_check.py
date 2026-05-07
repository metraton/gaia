"""
Integration tests for the four canonical state machines (gaia-state-machines).

Covers all five acceptance criteria of the gaia-state-machines brief:

* AC-1: CHECK constraint enforces enum on plan_status, briefs.status,
        plans.status, tasks.status (4 parametrized sub-tests).
* AC-2: ``gaia brief set-status`` rejects illegal transitions (already
        covered by ``test_brief_cli.test_set_status_illegal_transition``;
        this file adds a redundant smoke test against a closed brief).
* AC-3: Post-migration count of legacy / NULL / <STATUS> rows in
        ``episodes.plan_status`` is zero. Tested via a fresh schema apply
        against an empty DB followed by a representative seed.
* AC-4: state_tracker + brief_cli suites are green (this file adds nothing
        new -- AC-4 is verified by the CI run, not a single test).
* AC-5: Python SSOT and DB CHECK constraints agree. Tested by running the
        diff tool in-process against a fresh tmp DB and asserting the
        artifact is empty.

Tests use ``tmp_path`` to route the DB to an isolated path; nothing here
mutates the user's real ``~/.gaia/gaia.db``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure the gaia package and bin/cli are importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db(tmp_path: Path) -> Path:
    """Apply the canonical schema.sql to an empty DB and return the path."""
    db = tmp_path / "gaia-test.db"
    schema = (_REPO_ROOT / "gaia" / "store" / "schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(str(db))
    try:
        con.executescript(schema)
        con.commit()
    finally:
        con.close()
    return db


def _seed_minimal(con: sqlite3.Connection) -> int:
    """Insert a project + brief + plan so plans / tasks rows can be
    constructed under FK constraints. Returns the plan_id."""
    con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
    con.execute(
        "INSERT INTO briefs (project, name, status) VALUES (?, ?, ?)",
        ("me", "seed-brief", "draft"),
    )
    brief_id = con.execute(
        "SELECT id FROM briefs WHERE name='seed-brief'"
    ).fetchone()[0]
    con.execute(
        "INSERT INTO plans (brief_id, status) VALUES (?, ?)",
        (brief_id, "draft"),
    )
    plan_id = con.execute(
        "SELECT id FROM plans WHERE brief_id=?", (brief_id,)
    ).fetchone()[0]
    con.commit()
    return plan_id


# ---------------------------------------------------------------------------
# AC-1: CHECK rejects invalid values on each of the 4 columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("invalid_status", ["WRONG_VALUE", "review", "complete"])
def test_check_rejects_invalid_episodes_plan_status(tmp_path, invalid_status):
    """episodes.plan_status CHECK rejects values outside VALID_PLAN_STATUSES."""
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        con.commit()
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            con.execute(
                "INSERT INTO episodes (episode_id, project, timestamp, plan_status) "
                "VALUES (?, ?, ?, ?)",
                ("ep_test", "me", "2026-01-01T00:00:00Z", invalid_status),
            )
        assert "CHECK" in str(exc_info.value).upper()
    finally:
        con.close()


def test_episodes_plan_status_allows_null(tmp_path):
    """episodes.plan_status CHECK admits NULL for forward-compat writers."""
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        con.execute(
            "INSERT INTO episodes (episode_id, project, timestamp, plan_status) "
            "VALUES (?, ?, ?, ?)",
            ("ep_null", "me", "2026-01-01T00:00:00Z", None),
        )
        con.commit()
        row = con.execute(
            "SELECT plan_status FROM episodes WHERE episode_id='ep_null'"
        ).fetchone()
        assert row[0] is None
    finally:
        con.close()


@pytest.mark.parametrize("invalid_status", ["WRONG", "deprecated", "DRAFT"])
def test_check_rejects_invalid_briefs_status(tmp_path, invalid_status):
    """briefs.status CHECK rejects values outside VALID_BRIEF_STATUSES."""
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        con.commit()
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            con.execute(
                "INSERT INTO briefs (project, name, status) VALUES (?, ?, ?)",
                ("me", "bad-brief", invalid_status),
            )
        assert "CHECK" in str(exc_info.value).upper()
    finally:
        con.close()


@pytest.mark.parametrize("invalid_status", ["WRONG", "open", "DRAFT"])
def test_check_rejects_invalid_plans_status(tmp_path, invalid_status):
    """plans.status CHECK rejects values outside VALID_PLAN_LIFECYCLE_STATUSES."""
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        con.execute(
            "INSERT INTO briefs (project, name, status) VALUES (?, ?, ?)",
            ("me", "seed-brief", "draft"),
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE name='seed-brief'"
        ).fetchone()[0]
        con.commit()
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            con.execute(
                "INSERT INTO plans (brief_id, status) VALUES (?, ?)",
                (brief_id, invalid_status),
            )
        assert "CHECK" in str(exc_info.value).upper()
    finally:
        con.close()


@pytest.mark.parametrize("invalid_status", ["WRONG", "complete", "Done"])
def test_check_rejects_invalid_tasks_status(tmp_path, invalid_status):
    """tasks.status CHECK rejects values outside VALID_TASK_STATUSES."""
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        plan_id = _seed_minimal(con)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            con.execute(
                "INSERT INTO tasks (plan_id, order_num, goal, status) "
                "VALUES (?, ?, ?, ?)",
                (plan_id, 1, "do the thing", invalid_status),
            )
        assert "CHECK" in str(exc_info.value).upper()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# AC-1 inverse: each canonical value MUST be accepted
# ---------------------------------------------------------------------------

def test_all_canonical_plan_statuses_accepted(tmp_path):
    from gaia.state import VALID_PLAN_STATUSES
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        for i, status in enumerate(VALID_PLAN_STATUSES):
            con.execute(
                "INSERT INTO episodes (episode_id, project, timestamp, plan_status) "
                "VALUES (?, ?, ?, ?)",
                (f"ep_{i}", "me", "2026-01-01T00:00:00Z", status),
            )
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        assert n == len(VALID_PLAN_STATUSES)
    finally:
        con.close()


def test_all_canonical_brief_statuses_accepted(tmp_path):
    from gaia.state import VALID_BRIEF_STATUSES
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT INTO projects (name) VALUES (?)", ("me",))
        for i, status in enumerate(VALID_BRIEF_STATUSES):
            con.execute(
                "INSERT INTO briefs (project, name, status) VALUES (?, ?, ?)",
                ("me", f"brief-{i}", status),
            )
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM briefs").fetchone()[0]
        assert n == len(VALID_BRIEF_STATUSES)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# AC-3: post-migration legacy rows = 0 (using tmp DB seeded with legacy rows)
# ---------------------------------------------------------------------------

def test_migration_eliminates_legacy_episode_rows(tmp_path):
    """Apply the migration script against a tmp DB seeded with legacy rows
    and confirm the post-migration count of REVIEW / <STATUS> / NULL is 0.
    """
    # Seed a DB with the OLD (pre-CHECK) schema -- we mimic the old schema by
    # creating episodes manually without CHECK so we can insert legacy junk.
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db))
    try:
        con.executescript("""
            CREATE TABLE projects (name TEXT PRIMARY KEY);
            CREATE TABLE episodes (
                episode_id            TEXT NOT NULL PRIMARY KEY,
                project               TEXT NOT NULL,
                timestamp             TEXT NOT NULL,
                session_id            TEXT,
                task_id               TEXT,
                agent                 TEXT,
                type                  TEXT,
                title                 TEXT,
                prompt                TEXT,
                enriched_prompt       TEXT,
                wf_prompt             TEXT,
                clarifications        TEXT,
                keywords              TEXT,
                tags                  TEXT,
                commands_executed     TEXT,
                context_metrics       TEXT,
                relevance_score       REAL,
                outcome               TEXT,
                duration_seconds      REAL,
                exit_code             INTEGER,
                plan_status           TEXT,
                output_length         INTEGER,
                output_tokens_approx  INTEGER,
                FOREIGN KEY (project) REFERENCES projects(name)
            );
            CREATE TABLE briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                surface_type TEXT, title TEXT, objective TEXT, context TEXT,
                approach TEXT, out_of_scope TEXT, topic_key TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE (project, name)
            );
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brief_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                order_num INTEGER NOT NULL,
                goal TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                evidence_path TEXT
            );
            CREATE VIRTUAL TABLE episodes_fts USING fts5(
                episode_id UNINDEXED, prompt, enriched_prompt, tags, title,
                content='episodes', content_rowid='rowid'
            );
            CREATE VIRTUAL TABLE briefs_fts USING fts5(
                objective, context, approach,
                content='briefs', content_rowid='id'
            );
        """)
        con.execute("INSERT INTO projects (name) VALUES ('me')")
        # Seed legacy values
        rows = [
            ("ep_review", "REVIEW"),
            ("ep_status_lit", "<STATUS>"),
            ("ep_empty", ""),
            ("ep_null", None),
            ("ep_complete", "COMPLETE"),
        ]
        for ep_id, status in rows:
            con.execute(
                "INSERT INTO episodes (episode_id, project, timestamp, plan_status) "
                "VALUES (?, ?, ?, ?)",
                (ep_id, "me", "2026-01-01T00:00:00Z", status),
            )
        con.commit()
    finally:
        con.close()

    # Run the migration in-process
    from tools.migration.migrate_06_state_machines import _migrate
    con = sqlite3.connect(str(db))
    try:
        result = _migrate(con, dry_run=False)
    finally:
        con.close()

    assert result["status"] == "applied"

    # AC-3 evidence query
    con = sqlite3.connect(str(db))
    try:
        legacy = con.execute(
            "SELECT COUNT(*) FROM episodes WHERE plan_status IS NULL "
            "OR plan_status='REVIEW' OR plan_status='<STATUS>'"
        ).fetchone()[0]
    finally:
        con.close()
    assert legacy == 0, f"expected 0 legacy rows post-migration, got {legacy}"


# ---------------------------------------------------------------------------
# AC-5: Python SSOT vs DB CHECK -- empty diff
# ---------------------------------------------------------------------------

def test_source_of_truth_diff_is_empty(tmp_path):
    """The diff tool emits zero bytes when Python and DB CHECK agree."""
    from tools.state.diff_source_of_truth import _build_diff
    db = _fresh_db(tmp_path)
    con = sqlite3.connect(str(db))
    try:
        diff = _build_diff(con)
    finally:
        con.close()
    assert diff == "", f"expected empty diff, got:\n{diff}"


# ---------------------------------------------------------------------------
# AC-2 redundant smoke test (the canonical test lives in test_brief_cli.py)
# ---------------------------------------------------------------------------

def test_set_status_brief_rejects_illegal_transition(tmp_path, monkeypatch):
    """archived is terminal -> any outgoing transition is rejected."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.briefs import set_status_brief, upsert_brief
    from gaia.paths import db_path as _db_path
    db = _db_path()

    upsert_brief("me", "smoke", {"status": "archived", "title": "S"}, db_path=db)
    with pytest.raises(ValueError) as exc:
        set_status_brief("me", "smoke", "open", db_path=db)
    assert "illegal transition" in str(exc.value).lower()
