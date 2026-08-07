"""Upgrade coverage for the complete curated-memory history envelope."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "scripts" / "migrations" / "v40_to_v41.sql"


def test_v41_migration_adds_fields_and_replaces_trigger(tmp_path: Path) -> None:
    db = tmp_path / "v40.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(
            """
            CREATE TABLE workspaces (name TEXT PRIMARY KEY);
            CREATE TABLE memory (
              workspace TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
              description TEXT, body TEXT NOT NULL, origin_session_id TEXT,
              updated_at TEXT, class TEXT NOT NULL, status TEXT,
              project_ref TEXT, deleted_at TEXT, initiative TEXT,
              PRIMARY KEY (workspace, name)
            );
            CREATE TABLE memory_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL,
              name TEXT NOT NULL, before_workspace TEXT, after_workspace TEXT,
              before_body TEXT, after_body TEXT, before_type TEXT, after_type TEXT,
              before_description TEXT, after_description TEXT,
              before_status TEXT, after_status TEXT,
              before_deleted_at TEXT, after_deleted_at TEXT,
              changed_at TEXT NOT NULL, changed_by_agent TEXT
            );
            CREATE TRIGGER trg_memory_history AFTER UPDATE ON memory
            BEGIN
              INSERT INTO memory_history (workspace, name, changed_at)
              VALUES (NEW.workspace, NEW.name, 'old');
            END;
            """
        )
        con.executescript(MIGRATION.read_text(encoding="utf-8"))
        columns = {
            row["name"] for row in con.execute("PRAGMA table_info(memory_history)")
        }
        assert {
            "before_name", "after_name", "before_class", "after_class",
            "before_project_ref", "after_project_ref",
            "before_initiative", "after_initiative",
        } <= columns

        con.execute("INSERT INTO workspaces VALUES ('me')")
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, class) "
            "VALUES ('me', 'project_x', 'project', 'b', 'thread')"
        )
        con.execute(
            "UPDATE memory SET class='anchor', initiative='gaia' "
            "WHERE workspace='me' AND name='project_x'"
        )
        row = con.execute("SELECT * FROM memory_history").fetchone()
        assert row["before_class"] == "thread"
        assert row["after_class"] == "anchor"
        assert row["after_initiative"] == "gaia"
    finally:
        con.close()
