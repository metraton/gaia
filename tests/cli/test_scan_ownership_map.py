"""
M1-T3 (AC-10): role and topic_key are scanner-owned -- recomputed and
refreshed on every scan, never treated as frozen agent input.

The ownership map (``gaia/store/writer.py``) is the single source of truth for
which columns the scan path may write:

  * ``_PROJECTS_AGENT_OWNED`` is EMPTY in M1 -- ``role`` is auto-detected by
    ``tools/scan/role_detector.py`` and refreshed every scan, so it is NOT
    agent-owned. (M3/T9 adds ``description`` to this set.)
  * ``_APPS_AGENT_OWNED = {description, status}`` -- these are the agent-owned
    columns the scan path may never write.

``role`` and ``topic_key`` being scanner-owned means: a scan that supplies a
new value overwrites the old one (refresh), and the same repo produces the
same ``topic_key`` regardless of which table records it.

AC command (plan_id=19, T3):
    pytest tests/cli/test_scan_ownership_map.py -q   # exits 0
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, agent: str, *tables: str) -> None:
    for table in tables:
        con.execute(
            "INSERT OR REPLACE INTO agent_permissions "
            "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
            (table, agent),
        )
    con.commit()


# ---------------------------------------------------------------------------
# The ownership map itself
# ---------------------------------------------------------------------------

def test_role_is_not_projects_agent_owned():
    """role must NOT be in the projects agent-owned set -- it is scanner-owned
    (auto-detected, refreshed each scan)."""
    from gaia.store import writer

    assert "role" not in writer._PROJECTS_AGENT_OWNED
    assert "topic_key" not in writer._PROJECTS_AGENT_OWNED


def test_projects_agent_owned_empty_in_m1():
    """M1 ships no agent-owned projects column (description arrives in M3/T9)."""
    from gaia.store import writer

    assert writer._PROJECTS_AGENT_OWNED == frozenset()


def test_apps_agent_owned_is_description_and_status():
    from gaia.store import writer

    assert writer._APPS_AGENT_OWNED == frozenset({"description", "status"})


# ---------------------------------------------------------------------------
# role is refreshed on rescan (scanner-owned, not frozen)
# ---------------------------------------------------------------------------

def test_role_refreshed_on_rescan_via_scan_path(tmp_db):
    """A scan-path rescan supplying a new role OVERWRITES the old value --
    role behaves as scanner-owned, not agent-frozen."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-role-refresh"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()

    # identity kept constant so this is the SAME repo re-detected.
    ident = "/abs/svc/.git"
    upsert_project(ws, "svc", {"role": "library", "project_identity": ident},
                   "gaia-system", db_path=tmp_db, strip_agent_owned=True)
    upsert_project(ws, "svc", {"role": "backend", "project_identity": ident},
                   "gaia-system", db_path=tmp_db, strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT role FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()
    finally:
        con.close()

    assert row["role"] == "backend", (
        "role was not refreshed on rescan -- it must be scanner-owned"
    )


# ---------------------------------------------------------------------------
# topic_key: scanner-owned, refreshed when supplied, preserved when omitted
# ---------------------------------------------------------------------------

def test_topic_key_refreshed_when_supplied(tmp_db):
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-topic-refresh"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()

    ident = "/abs/svc/.git"
    upsert_project(ws, "svc", {"project_identity": ident}, "gaia-system",
                   topic_key="k1", db_path=tmp_db, strip_agent_owned=True)
    upsert_project(ws, "svc", {"project_identity": ident}, "gaia-system",
                   topic_key="k2", db_path=tmp_db, strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT topic_key FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()
    finally:
        con.close()

    assert row["topic_key"] == "k2", "topic_key was not refreshed when supplied"


def test_topic_key_preserved_when_omitted(tmp_db):
    """Omitting topic_key on a rescan (None) preserves the existing value via
    COALESCE rather than nulling it -- the coalesce-or-omit contract."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-topic-preserve"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()

    ident = "/abs/svc/.git"
    upsert_project(ws, "svc", {"project_identity": ident}, "gaia-system",
                   topic_key="k1", db_path=tmp_db, strip_agent_owned=True)
    # Rescan without a topic_key.
    upsert_project(ws, "svc", {"project_identity": ident, "role": "backend"},
                   "gaia-system", db_path=tmp_db, strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT topic_key FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()
    finally:
        con.close()

    assert row["topic_key"] == "k1", (
        "topic_key was nulled when omitted on rescan -- COALESCE contract broken"
    )


def test_topic_key_uniform_same_key_projects_and_apps(tmp_db):
    """The same topic_key value records uniformly on both a projects row and
    an apps row -- one repo, one key, regardless of the table."""
    from gaia.store.writer import upsert_project, upsert_app, _connect

    ws = "ws-topic-uniform"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects", "apps")
    con.close()

    key = "svc-topic"
    upsert_project(ws, "svc", {"project_identity": "/abs/svc/.git"}, "gaia-system",
                   topic_key=key, db_path=tmp_db, strip_agent_owned=True)
    upsert_app(ws, "svc", "api", {"kind": "service"}, "gaia-system",
               topic_key=key, db_path=tmp_db, strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        proj_key = con.execute(
            "SELECT topic_key FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()["topic_key"]
        app_key = con.execute(
            "SELECT topic_key FROM apps WHERE workspace = ? AND project = ? AND name = ?",
            (ws, "svc", "api"),
        ).fetchone()["topic_key"]
    finally:
        con.close()

    assert proj_key == app_key == key
