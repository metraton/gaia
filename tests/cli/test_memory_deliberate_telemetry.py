"""
P1 deliberate-read telemetry, wired into `gaia memory show` and
`gaia memory get-relevant --initiative` (telemetria-de-uso-en-memoria-curada,
task 5).

Property under test, not a command list: a DELIBERATE surface renders a
row's full body on someone's explicit request and must bump
``deliberate_count`` exactly once per row rendered, with ``last_deliberate_at``
set -- and must NEVER touch ``injection_count`` or ``updated_at``, and must
NEVER land a ``memory_history`` row (the narrow-UPDATE isolation this whole
entry is built around; see test_memory_telemetry_audit_isolation.py for the
trigger-level net). A PROJECTION -- search, list -- renders a name/description/
snippet, never a body, and must move neither counter.

Uses a real temporary SQLite DB (writer._connect materialises the schema on
first connect), mirroring the fixture pattern already used by
test_memory_get_relevant_v4.py / test_memory_initiative_digest.py.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli.memory as memory_mod  # noqa: E402

_WORKSPACE = "testws"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Real SQLite DB at tmp_path/gaia.db, routed through writer._connect."""
    db_path = tmp_path / "gaia.db"

    from gaia.store import writer as _w
    from gaia import paths as _paths

    monkeypatch.setattr(_paths, "db_path", lambda: db_path)
    monkeypatch.setattr(_w, "_db_path", lambda: db_path)

    con = _w._connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
        "VALUES (?, ?, ?)",
        (_WORKSPACE, _WORKSPACE, "2026-08-12T00:00:00Z"),
    )
    con.commit()
    con.close()
    return db_path


def _insert(db_path, name, *, body="body", initiative=None,
            updated_at="2026-08-12T00:00:00Z", type_="project",
            class_="thread", status="open", workspace=_WORKSPACE):
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, updated_at, "
        "                    initiative, class, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, body, updated_at, initiative, class_, status),
    )
    con.commit()
    con.close()


def _row(db_path, name, workspace=_WORKSPACE):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT injection_count, deliberate_count, last_injected_at, "
            "       last_deliberate_at, updated_at "
            "FROM memory WHERE workspace=? AND name=?",
            (workspace, name),
        ).fetchone())
    finally:
        con.close()


def _history_count(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM memory_history").fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# `gaia memory show` -- deliberate, single row
# ---------------------------------------------------------------------------

class TestShowIsDeliberate:
    def test_show_bumps_deliberate_only(self, tmp_db, capsys):
        _insert(tmp_db, "probe_show", body="the full body")
        before = _row(tmp_db, "probe_show")
        before_history = _history_count(tmp_db)

        args = SimpleNamespace(name="probe_show", workspace=_WORKSPACE,
                               links=False, history=False, json=True)
        rc = memory_mod._cmd_curated_show(args)
        payload = json.loads(capsys.readouterr().out)

        after = _row(tmp_db, "probe_show")
        after_history = _history_count(tmp_db)

        assert rc == 0
        assert payload["body"] == "the full body"
        assert after["deliberate_count"] == before["deliberate_count"] + 1
        assert after["injection_count"] == before["injection_count"]
        assert after["last_deliberate_at"] is not None
        assert after["updated_at"] == before["updated_at"]
        assert after_history == before_history

    def test_show_missing_slug_does_not_touch_counters(self, tmp_db, capsys):
        args = SimpleNamespace(name="ghost", workspace=_WORKSPACE,
                               links=False, history=False, json=False)
        rc = memory_mod._cmd_curated_show(args)
        assert rc == 1  # not found -- no row to bump, no crash either


# ---------------------------------------------------------------------------
# `gaia memory get-relevant --initiative=X` -- deliberate, whole corpus
# ---------------------------------------------------------------------------

class TestGetRelevantInitiativeIsDeliberate:
    def _args(self, **overrides):
        base = {
            "workspace": _WORKSPACE, "limit": 8, "max_chars": 1500,
            "types": None, "sections": None, "initiative": "demoproj",
            "json": True, "func": memory_mod._cmd_get_relevant,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_every_rendered_row_bumps_deliberate_once(self, tmp_db, capsys):
        _insert(tmp_db, "demo_a", initiative="demoproj")
        _insert(tmp_db, "demo_b", initiative="demoproj")
        # A different initiative must not be touched at all.
        _insert(tmp_db, "other_x", initiative="otherproj")

        before_a = _row(tmp_db, "demo_a")
        before_b = _row(tmp_db, "demo_b")
        before_x = _row(tmp_db, "other_x")
        before_history = _history_count(tmp_db)

        rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)

        after_a = _row(tmp_db, "demo_a")
        after_b = _row(tmp_db, "demo_b")
        after_x = _row(tmp_db, "other_x")
        after_history = _history_count(tmp_db)

        assert rc == 0
        assert {i["name"] for i in payload["items"]} == {"demo_a", "demo_b"}

        for before, after in ((before_a, after_a), (before_b, after_b)):
            assert after["deliberate_count"] == before["deliberate_count"] + 1
            assert after["injection_count"] == before["injection_count"]
            assert after["last_deliberate_at"] is not None
            assert after["updated_at"] == before["updated_at"]

        # Untouched initiative: byte-identical, nothing moved.
        assert after_x == before_x
        assert after_history == before_history

    def test_initiative_never_bumps_injection(self, tmp_db, capsys):
        _insert(tmp_db, "demo_only", initiative="demoproj")
        rc = memory_mod._cmd_get_relevant(self._args())
        assert rc == 0
        after = _row(tmp_db, "demo_only")
        assert after["injection_count"] == 0
        assert after["deliberate_count"] == 1


# ---------------------------------------------------------------------------
# Projections -- search / list -- must move nothing
# ---------------------------------------------------------------------------

class TestProjectionsMoveNothing:
    def test_search_does_not_move_either_counter(self, tmp_db, capsys):
        _insert(tmp_db, "searchable", body="a zzqvortex marker in the body")
        before = _row(tmp_db, "searchable")
        before_history = _history_count(tmp_db)

        args = SimpleNamespace(scope="memory", json=True, query="zzqvortex",
                               limit=10, workspace=_WORKSPACE)
        rc = memory_mod._cmd_search_scoped(args)
        capsys.readouterr()

        after = _row(tmp_db, "searchable")
        after_history = _history_count(tmp_db)

        assert rc == 0
        assert after == before
        assert after_history == before_history

    def test_list_does_not_move_either_counter(self, tmp_db, capsys):
        _insert(tmp_db, "listable")
        before = _row(tmp_db, "listable")

        args = SimpleNamespace(json=True, workspace=_WORKSPACE, type=None,
                               audience=None, format=None, limit=None)
        rc = memory_mod._cmd_list(args)
        capsys.readouterr()

        after = _row(tmp_db, "listable")

        assert rc == 0
        assert after == before

    def test_digest_mode_bumps_injection_only(self, tmp_db, capsys):
        """Regression guard: the no-flag digest is an INJECTION surface,
        wired in task 6 (telemetria-de-uso-en-memoria-curada) -- it must bump
        ONLY injection_count, never deliberate_count. This is the
        shared-file boundary the plan calls out between tasks 5 and 6; see
        test_memory_injection_telemetry.py for the full P1 injection
        coverage (over-select-vs-emit, --initiative exclusion, kernel)."""
        _insert(tmp_db, "digest_row", initiative="demoproj", class_="thread",
                status="open")
        before = _row(tmp_db, "digest_row")

        args = SimpleNamespace(workspace=_WORKSPACE, limit=8, max_chars=1500,
                               types=None, sections=None, initiative=None,
                               json=True, func=memory_mod._cmd_get_relevant)
        rc = memory_mod._cmd_get_relevant(args)
        capsys.readouterr()

        after = _row(tmp_db, "digest_row")

        assert rc == 0
        assert after["injection_count"] == before["injection_count"] + 1
        assert after["deliberate_count"] == before["deliberate_count"]
        assert after["updated_at"] == before["updated_at"]
