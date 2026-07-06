"""
M3-T9 (AC-7 + the description slice of AC-1): the agent-owned
``projects.description`` column (schema v23) survives any number of scanner
rescans unchanged.

This closes ONLY the description-specific slice of AC-1. The general
coalesce-or-omit + ownership MECHANISM was already proven independently in
M1/T2 against apps.description/status and scanner-owned projects columns
(see tests/cli/test_scan_write_safety.py). T9 adds the new agent-owned
``projects.description`` column (gaia/store/writer.py::_PROJECTS_AGENT_OWNED)
and extends the guarantee to it:

  (a) The scan path (``strip_agent_owned=True`` -- always set by
      populate_project, classify._upsert, bulk_upsert's projects branch) can
      NEVER write ``projects.description``, even if the row dict contains it:
      it is stripped by ``_present_fields`` before the coalesce-or-omit step.
  (b) A scanner rescan that OMITS description leaves an agent-authored value
      intact (coalesce-or-omit), across BOTH the identity-collapse UPDATE path
      and the ON CONFLICT(workspace, name) path of upsert_project.
  (c) Without ``strip_agent_owned`` (a direct agent write), description is
      writable -- the flag gates the SCAN PATH, not the column absolutely.

AC command (plan_id=19, T9):
    pytest tests/cli/test_scan_description_preserved.py -q   # exits 0
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


def _description(con, ws: str, name: str):
    row = con.execute(
        "SELECT description FROM projects WHERE workspace = ? AND name = ?",
        (ws, name),
    ).fetchone()
    return row["description"] if row is not None else None


# ---------------------------------------------------------------------------
# Schema: the agent-owned description column exists (v23)
# ---------------------------------------------------------------------------

def test_projects_has_description_column(tmp_db):
    """The v23 migration / schema.sql gives projects an agent-owned
    description column."""
    from gaia.store.writer import _connect

    con = _connect(tmp_db)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(projects)").fetchall()}
    finally:
        con.close()

    assert "description" in cols, "projects.description column missing (schema v23 / T9)"


# ---------------------------------------------------------------------------
# (a) scan path can never write description, even when it tries
# ---------------------------------------------------------------------------

def test_scan_path_cannot_clobber_agent_description(tmp_db):
    """An agent authors description; a scanner-path rescan that even *tries* to
    pass a new description must leave the agent value intact (agent-owned
    protection via strip_agent_owned)."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-desc-clobber"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects")
    _grant(con, "gaia-system", "projects")
    con.close()

    ident = "/abs/svc/.git"
    # Agent authors the description (direct write, not the scan path).
    upsert_project(
        ws, "svc",
        {"description": "hand-authored purpose", "project_identity": ident},
        "developer", db_path=tmp_db,
    )

    # Scanner-path rescan tries to write a new description -- must be stripped.
    upsert_project(
        ws, "svc",
        {"description": "SCANNER-CLOBBER", "role": "backend", "project_identity": ident},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    con = _connect(tmp_db)
    try:
        assert _description(con, ws, "svc") == "hand-authored purpose", (
            "scan path clobbered agent-owned projects.description"
        )
        # scanner-owned column was still refreshed on the same rescan.
        role = con.execute(
            "SELECT role FROM projects WHERE workspace = ? AND name = ?",
            (ws, "svc"),
        ).fetchone()["role"]
    finally:
        con.close()
    assert role == "backend", "scanner-owned role was not refreshed on rescan"


# ---------------------------------------------------------------------------
# (b) coalesce-or-omit: a rescan that omits description preserves it
# ---------------------------------------------------------------------------

def test_scan_rescan_omitting_description_preserves_it(tmp_db):
    """A scanner rescan that does NOT mention description at all leaves the
    agent value untouched (coalesce-or-omit), across repeated rescans."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-desc-omit"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects")
    _grant(con, "gaia-system", "projects")
    con.close()

    ident = "/abs/api/.git"
    upsert_project(
        ws, "api",
        {"description": "the billing API", "project_identity": ident},
        "developer", db_path=tmp_db,
    )

    # Two scanner rescans that never mention description.
    upsert_project(
        ws, "api",
        {"role": "backend", "path": "/abs/api", "project_identity": ident},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )
    upsert_project(
        ws, "api",
        {"primary_language": "python", "project_identity": ident},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    con = _connect(tmp_db)
    try:
        assert _description(con, ws, "api") == "the billing API", (
            "description was nulled/lost across scanner rescans that omitted it"
        )
    finally:
        con.close()


def test_scan_before_agent_write_then_scan_preserves(tmp_db):
    """Order-independence: a scan runs first (no description), the agent adds a
    description, then another scan runs -- the description survives. This is the
    realistic lifecycle (scanner discovers the repo, agent enriches it later)."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-desc-lifecycle"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects")
    _grant(con, "gaia-system", "projects")
    con.close()

    ident = "/abs/gaia/.git"
    # 1. First scan: no description yet.
    upsert_project(
        ws, "gaia",
        {"role": "application", "project_identity": ident, "path": "/abs/gaia"},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )
    # 2. Agent enriches with a description.
    upsert_project(
        ws, "gaia",
        {"description": "the meta layer builder", "project_identity": ident},
        "developer", db_path=tmp_db,
    )
    # 3. Later scan refreshes scanner columns; description must survive.
    upsert_project(
        ws, "gaia",
        {"role": "monorepo", "project_identity": ident, "path": "/abs/gaia"},
        "gaia-system", db_path=tmp_db, strip_agent_owned=True,
    )

    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT description, role FROM projects WHERE workspace = ? AND name = ?",
            (ws, "gaia"),
        ).fetchone()
    finally:
        con.close()

    assert row["description"] == "the meta layer builder", (
        "agent-added description did not survive a subsequent scan"
    )
    assert row["role"] == "monorepo", "scanner-owned role was not refreshed"


# ---------------------------------------------------------------------------
# (c) direct agent write keeps full access to description
# ---------------------------------------------------------------------------

def test_direct_agent_write_keeps_description_access(tmp_db):
    """Without strip_agent_owned, an agent can write and update description --
    the flag gates the SCAN PATH, not the column in the abstract."""
    from gaia.store.writer import upsert_project, _connect

    ws = "ws-desc-agentwrite"
    con = _connect(tmp_db)
    _grant(con, "developer", "projects")
    con.close()

    ident = "/abs/svc/.git"
    upsert_project(ws, "svc", {"description": "v1", "project_identity": ident},
                   "developer", db_path=tmp_db)
    # A later agent edit updates description.
    upsert_project(ws, "svc", {"description": "v2", "project_identity": ident},
                   "developer", db_path=tmp_db)

    con = _connect(tmp_db)
    try:
        assert _description(con, ws, "svc") == "v2"
    finally:
        con.close()
