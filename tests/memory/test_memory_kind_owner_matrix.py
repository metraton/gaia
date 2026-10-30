"""Memory rows carry a kind and an owner level, and only the pairs the matrix allows are written.

Runs in-process against the temporary HOME and GAIA_DATA_DIR the suite's
conftest provides; the writer derives the owner from the placement flags and
refuses a forbidden pair with the home that would take it.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import memory_placement as placement  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    USER_WORKSPACE, _connect, check_supersedes_link, get_memory, insert_memory_link, upsert_memory,
)

_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v70_to_v71.sql"


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _row(workspace: str, name: str) -> dict:
    return get_memory(workspace, name)


@pytest.mark.parametrize("owner_flags, kind, expected_owner", [
    ({"type": "user"}, "preference", "user"),
    ({"type": "user"}, "fact", "user"),
    ({"type": "atom", "workspace": "me"}, "decision", "workspace"),
    ({"type": "project", "workspace": "me", "initiative": "branchkinect"}, "lesson", "group"),
    ({"type": "project", "workspace": "me", "project_ref": "github.com/x/gaia"}, "pending", "project"),
    ({"type": "feedback", "workspace": "me", "initiative": "gaia_system"}, "defect", "gaia"),
    ({"type": "negative", "workspace": "me", "initiative": "gaia_system"}, "lesson", "gaia"),
])
def test_allowed_pairs_are_written_with_their_owner(owner_flags, kind, expected_owner):
    flags = dict(owner_flags)
    mem_type = flags.pop("type")
    workspace = flags.pop("workspace", "me")
    prefix = {"atom": "atom_", "negative": "negative_", "decision": "decision_"}.get(mem_type, "")
    name = f"{prefix}{kind}_{expected_owner}_row"
    res = upsert_memory(workspace, name, type=mem_type, body="body", kind=kind, **flags)
    assert (res["kind"], res["owner_level"]) == (kind, expected_owner)
    stored = _row(res["workspace"], name)
    assert (stored["kind"], stored["owner_level"]) == (kind, expected_owner)


@pytest.mark.parametrize("owner_flags, kind, home_hint", [
    ({"type": "user"}, "decision", "workspace, group or project"),
    ({"type": "user"}, "defect", "defect to gaia"),
    ({"type": "project", "workspace": "me"}, "pending", "group or project"),
    ({"type": "project", "workspace": "me", "project_ref": "github.com/x/gaia"}, "defect", "project's pending"),
    ({"type": "feedback", "workspace": "me", "initiative": "gaia_system"}, "fact", "gaia project"),
    ({"type": "feedback", "workspace": "me", "initiative": "gaia_system"}, "pending", "gaia project"),
    ({"type": "project", "workspace": "me", "project_ref": "github.com/x/gaia"}, "tool", "belongs to the user"),
    ({"type": "project", "workspace": "me", "initiative": "branchkinect"}, "tool", "belongs to the user"),
])
def test_forbidden_pairs_are_refused_naming_the_right_home(owner_flags, kind, home_hint):
    flags = dict(owner_flags)
    mem_type = flags.pop("type")
    workspace = flags.pop("workspace", "me")
    with pytest.raises(ValueError, match=home_hint.replace("'", "'")):
        upsert_memory(workspace, f"refused_{kind}", type=mem_type, body="body", kind=kind, **flags)


def test_the_trigger_refuses_what_the_writer_refuses():
    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
    with pytest.raises(sqlite3.IntegrityError, match="gaia owns lessons and defects only"):
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, kind, owner_level) "
            "VALUES ('me', 'raw_row', 'atom', 'b', 'fact', 'gaia')"
        )
    with pytest.raises(sqlite3.IntegrityError, match="a tool belongs to the user"):
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, kind, owner_level, binary) "
            "VALUES ('me', 'raw_tool', 'atom', 'b', 'tool', 'project', 'x')"
        )
    with pytest.raises(sqlite3.IntegrityError, match="binary is required"):
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, kind, owner_level) "
            "VALUES ('me', 'raw_tool', 'user', 'b', 'tool', 'user')"
        )
    con.close()


def test_kind_defaults_to_the_claim_word_then_the_type():
    assert placement.default_kind("user", "Preference: PRs in English.", "anchor") == "preference"
    assert placement.default_kind("user", "Regla: hora en UTC.", "anchor") == "preference"
    assert placement.default_kind("user", "Jorge dicta en español.", "anchor") == "fact"
    assert placement.default_kind("feedback", "Bug: the guard blocks reads.", "log") == "defect"
    assert placement.default_kind("project", "Bug: the catalog loses rows.", "thread") == "pending"
    assert placement.default_kind("project", "Open question on the release.", "thread") == "pending"
    assert placement.default_kind("project", "The repo deploys with Flux.", "anchor") == "fact"
    assert placement.default_kind("negative", "Dead end: retries hide the fault.", "anchor") == "lesson"
    assert placement.default_kind("decision", "We ship RC first.", "anchor") == "decision"


def test_until_is_a_preference_only_and_expires():
    upsert_memory(
        USER_WORKSPACE, "user_pref_until_date", type="user", body="b", kind="preference",
        description="PRs: one block per PR.", until="2026-01-01",
    )
    upsert_memory(
        USER_WORKSPACE, "user_pref_until_ref", type="user", body="b", kind="preference",
        description="Tools: pass --no-verify.", until="memory:gaia/feedback_hook_blocks",
    )
    with pytest.raises(ValueError, match="until belongs to a preference only"):
        upsert_memory(USER_WORKSPACE, "user_fact_until", type="user", body="b", kind="fact", until="2027-01-01")
    with pytest.raises(ValueError, match="neither a date"):
        upsert_memory(USER_WORKSPACE, "user_pref_bad_until", type="user", body="b",
                      kind="preference", until="next week")

    con = _connect()
    assert placement.until_holds("2026-01-01", con, today=date(2025, 12, 31))
    assert not placement.until_holds("2026-01-01", con, today=date(2026, 1, 2))
    assert not placement.until_holds("memory:gaia/feedback_hook_blocks", con)
    upsert_memory("me", "feedback_hook_blocks", type="feedback", body="b", initiative="gaia_system")
    assert placement.until_holds("memory:gaia/feedback_hook_blocks", con)
    con.close()


def test_supersedes_needs_the_same_owner_and_kind():
    upsert_memory(USER_WORKSPACE, "user_pref_old", type="user", body="b", kind="preference")
    upsert_memory(USER_WORKSPACE, "user_pref_new", type="user", body="b", kind="preference")
    upsert_memory(USER_WORKSPACE, "user_fact_new", type="user", body="b", kind="fact")
    upsert_memory("me", "decision_me_rule", type="decision", body="b", kind="preference")

    check_supersedes_link(USER_WORKSPACE, "user_pref_new", "user_pref_old")
    with pytest.raises(ValueError, match="own owner and kind"):
        check_supersedes_link(USER_WORKSPACE, "user_fact_new", "user_pref_old")
    with pytest.raises(ValueError, match="own owner and kind"):
        insert_memory_link("me", "decision_me_rule", "user_pref_old", "supersedes",
                           dst_workspace=USER_WORKSPACE)


def _statements(sql: str, starting_with: str) -> list[str]:
    return [s.strip() for s in sql.split(";\n") if s.strip().upper().startswith(starting_with)]


def test_backfill_maps_legacy_rows_by_word_then_type_and_places_their_owner():
    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('_gaia_user')")
    con.execute("INSERT OR IGNORE INTO projects (workspace, name, path, status) "
                "VALUES ('me', 'bildwiz-iac', '/tmp/bildwiz-iac', 'active')")
    legacy = [
        ("_gaia_user", "u_pref", "user", "Preferencia: PRs en bloques.", "anchor", None, None),
        ("_gaia_user", "u_fact", "user", "Jorge works offshore.", "anchor", None, None),
        ("me", "fb_bug", "feedback", "Bug: the guard over-blocks.", "log", "gaia_system", None),
        ("me", "fb_pending", "feedback", "Pending: close the probe.", "log", "gaia_system", None),
        ("me", "p_thread", "project", "El estado de la release.", "thread", "gaia", "github.com/metraton/gaia"),
        ("me", "p_anchor", "project", "El repo usa Flux.", "anchor", "bildwiz_iac", None),
        ("me", "p_group", "project", "Decision: PRs por bloques.", "anchor", "branchkinect", None),
        ("me", "neg", "negative", "Dead end.", "anchor", "gaia_system", None),
        ("me", "legacy_ws", "atom", "Medido: 12 rows.", "log", None, None),
    ]
    con.executemany(
        "INSERT INTO memory (workspace, name, type, description, body, class, initiative, project_ref) "
        "VALUES (?, ?, ?, ?, 'b', ?, ?, ?)", legacy,
    )
    migration = _MIGRATION.read_text(encoding="utf-8")
    assert migration.index("UPDATE memory SET kind") < migration.index("CREATE TRIGGER")
    con.execute("DROP TRIGGER trg_memory_placement_insert")
    con.execute("DROP TRIGGER trg_memory_placement_update")
    for statement in _statements(migration, "UPDATE"):
        con.execute(statement)
    con.commit()
    placed = {
        name: (kind, owner) for name, kind, owner in
        con.execute("SELECT name, kind, owner_level FROM memory WHERE name IN "
                    "('u_pref','u_fact','fb_bug','fb_pending','p_thread','p_anchor','p_group','neg','legacy_ws')")
    }
    con.close()
    assert placed == {
        "u_pref": ("preference", "user"),
        "u_fact": ("fact", "user"),
        "fb_bug": ("defect", "gaia"),
        "fb_pending": ("pending", "gaia"),
        "p_thread": ("pending", "project"),
        "p_anchor": ("fact", "project"),
        "p_group": ("decision", "group"),
        "neg": ("lesson", "gaia"),
        "legacy_ws": ("fact", None),
    }
