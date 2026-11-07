"""v74 -> v75 adds memory.surface, and a database migrated to it refuses what a fresh one refuses."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO / "scripts" / "migrations" / "v74_to_v75.sql"


@pytest.fixture
def migrated():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE memory (workspace TEXT, name TEXT, kind TEXT, PRIMARY KEY (workspace, name))")
    con.execute("INSERT INTO memory VALUES ('_gaia_user', 'user_pref_old', 'preference')")
    con.execute("INSERT INTO memory VALUES ('_gaia_user', 'user_fact_old', 'fact')")
    con.executescript(_MIGRATION.read_text(encoding="utf-8"))
    yield con
    con.close()


def test_an_existing_preference_gains_a_surface_and_nothing_else_can(migrated):
    migrated.execute("UPDATE memory SET surface = 'conversation' WHERE name = 'user_pref_old'")

    assert migrated.execute("SELECT surface FROM memory WHERE name = 'user_pref_old'").fetchone() == ("conversation",)
    with pytest.raises(sqlite3.IntegrityError, match="kind=preference only"):
        migrated.execute("UPDATE memory SET surface = 'document' WHERE name = 'user_fact_old'")
    with pytest.raises(sqlite3.IntegrityError, match="conversation, sent or document"):
        migrated.execute("UPDATE memory SET surface = 'chat' WHERE name = 'user_pref_old'")
    with pytest.raises(sqlite3.IntegrityError, match="kind=preference only"):
        migrated.execute("INSERT INTO memory VALUES ('_gaia_user', 'user_fact_new', 'fact', 'sent')")


def test_the_fresh_schema_carries_the_same_triggers_as_the_migration():
    schema = (_REPO / "gaia" / "store" / "schema.sql").read_text(encoding="utf-8")
    migration = _MIGRATION.read_text(encoding="utf-8")

    for name in ("trg_memory_surface_insert", "trg_memory_surface_update"):
        block = migration[migration.index(f"CREATE TRIGGER IF NOT EXISTS {name}"):]
        block = block[:block.index("END;") + 4]
        assert block in schema
