"""
M1-T2 (AC-1): coalesce-or-omit + agent-owned protection on the DB write path.

Two clobber families are closed here, across BOTH ``projects`` and ``apps``:

  (a) Coalesce-or-omit -- a rescan that OMITS a scanner-owned column must NOT
      force it to NULL. The writer builds its INSERT/UPDATE column list from
      only the keys PRESENT in ``fields`` (see
      ``gaia/store/writer.py::_present_fields`` and the rewritten
      ``upsert_project`` / ``upsert_app``), so an unmentioned column keeps its
      current value.

  (b) Agent-owned protection -- the scan path (``strip_agent_owned=True``,
      always set by ``bulk_upsert`` for projects/apps, by
      ``tools/scan/store_populator.py::populate_project``, and by
      ``tools/scan/classify.py::_upsert``) can NEVER write a column in the
      table's agent-owned set (``_APPS_AGENT_OWNED = {description, status}``),
      regardless of what the row dict happens to contain.

This proves the MECHANISM against ALREADY-EXISTING agent-owned columns
(``apps.description`` / ``apps.status``) and against scanner-owned columns via
coalesce-or-omit. It carries NO dependency on the not-yet-added
``projects.description`` column -- that survival assertion is deferred to
M3/T9 -- so M1 closes on its own.

AC command (plan_id=19, T2):
    pytest tests/cli/test_scan_write_safety.py -q   # exits 0
"""

from __future__ import annotations

from pathlib import Path

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
# apps: agent-owned columns (description, status) survive the scan path
# ---------------------------------------------------------------------------

def test_apps_agent_owned_columns_survive_scanner_rescan(tmp_db):
    """description + status are written by an agent, then a scanner-path
    rescan (strip_agent_owned) must leave BOTH untouched."""
    from gaia.store.writer import upsert_project, upsert_app, _connect

    ws = "ws-apps-safety"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects", "apps")
    _grant(con, "gaia-system", "projects", "apps")
    con.close()

    upsert_project(ws, "proj", {"role": "backend"}, "developer", db_path=tmp_db)
    # Agent writes the agent-owned columns (NOT the scan path).
    upsert_app(
        ws, "proj", "api",
        {"kind": "service", "description": "hand-authored", "status": "active"},
        "developer", db_path=tmp_db,
    )

    # Scanner-path rescan: strip_agent_owned=True, and it even *tries* to pass
    # description/status -- they must be dropped, not written.
    upsert_app(
        ws, "proj", "api",
        {"kind": "service", "description": "SCANNER-CLOBBER", "status": "deprecated"},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT kind, description, status FROM apps "
            "WHERE workspace = ? AND project = ? AND name = ?",
            (ws, "proj", "api"),
        ).fetchone()
    finally:
        con.close()

    assert row["description"] == "hand-authored", "scan path clobbered apps.description"
    assert row["status"] == "active", "scan path clobbered apps.status"
    assert row["kind"] == "service"


def test_apps_coalesce_or_omit_preserves_scanner_column(tmp_db):
    """A scanner rescan that OMITS a scanner-owned column (kind) must leave it
    intact rather than nulling it."""
    from gaia.store.writer import upsert_project, upsert_app, _connect

    ws = "ws-apps-coalesce"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects", "apps")
    con.close()

    upsert_project(ws, "proj", {"role": "backend"}, "gaia-system", db_path=tmp_db,
                   strip_agent_owned=True)
    upsert_app(ws, "proj", "api", {"kind": "service"}, "gaia-system",
               db_path=tmp_db, strip_agent_owned=True)

    # Rescan omits `kind` entirely.
    upsert_app(ws, "proj", "api", {}, "gaia-system", db_path=tmp_db,
               strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT kind FROM apps WHERE workspace = ? AND project = ? AND name = ?",
            (ws, "proj", "api"),
        ).fetchone()
    finally:
        con.close()

    assert row["kind"] == "service", (
        "coalesce-or-omit failed: omitting `kind` on rescan nulled it"
    )


def test_apps_direct_agent_write_keeps_full_access(tmp_db):
    """Without strip_agent_owned (a direct agent write), description/status
    are writable -- the flag gates the SCAN PATH, not the column absolutely."""
    from gaia.store.writer import upsert_project, upsert_app, _connect

    ws = "ws-apps-agentwrite"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects", "apps")
    con.close()

    upsert_project(ws, "proj", {"role": "backend"}, "developer", db_path=tmp_db)
    upsert_app(ws, "proj", "api",
               {"kind": "service", "description": "v1", "status": "planned"},
               "developer", db_path=tmp_db)
    # A later agent edit updates the agent-owned columns.
    upsert_app(ws, "proj", "api", {"description": "v2", "status": "active"},
               "developer", db_path=tmp_db)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT kind, description, status FROM apps "
            "WHERE workspace = ? AND project = ? AND name = ?",
            (ws, "proj", "api"),
        ).fetchone()
    finally:
        con.close()

    assert row["description"] == "v2"
    assert row["status"] == "active"
    # coalesce-or-omit: kind was NOT supplied in the second call -> preserved.
    assert row["kind"] == "service"


# ---------------------------------------------------------------------------
# projects: coalesce-or-omit on scanner-owned columns
# ---------------------------------------------------------------------------

def test_projects_coalesce_or_omit_preserves_scanner_columns(tmp_db):
    """A scanner rescan that supplies only a subset of scanner-owned columns
    must not null the columns it omitted (remote_url, platform, primary_language)."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-proj-coalesce"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()

    # First scan captures a fuller picture.
    upsert_project(
        ws, "svc",
        {
            "role": "backend",
            "remote_url": "git@github.com:me/svc.git",
            "platform": "github",
            "primary_language": "python",
            "path": "/abs/svc",
        },
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    # A later, partial rescan only refreshes `path` (e.g. moved on disk) and
    # omits remote_url/platform/primary_language entirely.
    upsert_project(
        ws, "svc",
        {"path": "/abs/moved/svc"},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT role, remote_url, platform, primary_language, path "
            "FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()
    finally:
        con.close()

    assert row["path"] == "/abs/moved/svc", "the supplied column was not updated"
    assert row["remote_url"] == "git@github.com:me/svc.git", (
        "coalesce-or-omit failed: omitted remote_url was nulled on rescan"
    )
    assert row["platform"] == "github", "omitted platform was nulled"
    assert row["primary_language"] == "python", "omitted primary_language was nulled"
    assert row["role"] == "backend", "omitted role was nulled"


def test_projects_status_defaults_active_when_absent(tmp_db):
    """Omitting status still defaults to 'active' (unchanged historical rule),
    proven not to regress under the coalesce-or-omit rewrite."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-proj-status"
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()

    upsert_project(ws, "svc", {"role": "backend"}, "gaia-system",
                   db_path=tmp_db, strip_agent_owned=True)

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT status FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()
    finally:
        con.close()

    assert row["status"] == "active"
