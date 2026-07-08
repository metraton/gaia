"""
T7 -- v4 class/status-driven selection for `gaia memory get-relevant`.

The default (no --types) code path selects by class/status:
  * Section 1: class=thread, status=carry_forward (no quota, no trimming)
  * Section 2: class=anchor, updated_at DESC, quota 4
  * Section 3: class=thread, status=open, updated_at DESC, quota 2
  * class=log NEVER appears
  * Rows that are the destination of a supersedes edge are excluded
  * Empty sections drop their header; all-empty -> empty block

These tests use a real temporary SQLite DB (writer._connect materializes
schema on first connect) so they exercise the real query path including
the memory_links subquery.
"""

import json
import sys
import sqlite3
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Spin up a real SQLite DB at tmp_path/gaia.db and route writer._connect."""
    db_path = tmp_path / "gaia.db"

    from gaia.store import writer as _w
    from gaia import paths as _paths

    # Route the writer to our temp DB. _db_path is the indirection layer.
    monkeypatch.setattr(_paths, "db_path", lambda: db_path)
    monkeypatch.setattr(_w, "_db_path", lambda: db_path)

    # Materialize schema by opening once.
    con = _w._connect(db_path)
    # Need a workspaces row because memory FK -> workspaces(name)
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
        "VALUES (?, ?, ?)",
        ("testws", "testws", "2026-05-22T00:00:00Z"),
    )
    con.commit()
    con.close()
    return db_path


def _insert_memory(db_path, name, type_, class_, status_, desc,
                   updated_at, workspace="testws", body=None, project_ref=None):
    body = body or f"body for {name}"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, "
        "                    updated_at, class, status, project_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, desc, body, updated_at, class_, status_,
         project_ref),
    )
    con.commit()
    con.close()


def _insert_project(db_path, name, path, project_identity,
                    workspace="testws", status="active"):
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO projects (workspace, name, path, project_identity, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (workspace, name, path, project_identity, status),
    )
    con.commit()
    con.close()


def _insert_link(db_path, src_name, dst_name, kind, workspace="testws"):
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO memory_links (workspace, src_name, dst_name, kind, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (workspace, src_name, dst_name, kind, "2026-05-22T00:00:00Z"),
    )
    con.commit()
    con.close()


def _args(**overrides):
    base = {
        "workspace": "testws",
        "limit": 8,
        "max_chars": 800,
        "types": None,   # v4 default path
        "json": True,
        "func": memory_mod._cmd_get_relevant,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCarryForwardFirst:
    """carry_forward rows appear in section 1, before anchors."""

    def test_carry_forward_appears_in_section_1(self, tmp_db, capsys):
        _insert_memory(tmp_db, "atom_anchor_1", "atom", "anchor", None,
                       "anchor first", "2026-05-22T10:00:00Z")
        _insert_memory(tmp_db, "atom_carry_1", "atom", "thread", "carry_forward",
                       "carry this", "2026-05-22T09:00:00Z")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        items = payload["items"]
        assert items, "expected non-empty result"
        # First item must be the carry_forward row.
        assert items[0]["name"] == "atom_carry_1"
        assert items[0]["section"] == "carry_forward"

    def test_carry_forward_no_quota(self, tmp_db, capsys):
        """10 carry_forward rows -> all 10 are injected."""
        for i in range(10):
            _insert_memory(
                tmp_db, f"atom_carry_{i}", "atom", "thread", "carry_forward",
                f"d{i}", f"2026-05-22T{i:02d}:00:00Z",
            )

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out

        assert rc == 0
        payload = json.loads(out)
        cf = [i for i in payload["items"] if i["section"] == "carry_forward"]
        assert len(cf) == 10, f"expected 10 carry_forward, got {len(cf)}"


class TestAnchorQuota:
    """anchor section is bounded to quota (4)."""

    def test_anchor_quota_4_max(self, tmp_db, capsys):
        for i in range(7):
            _insert_memory(
                tmp_db, f"atom_anchor_{i}", "atom", "anchor", None,
                f"d{i}", f"2026-05-22T{i:02d}:00:00Z",
            )

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0

        payload = json.loads(out)
        anchors = [i for i in payload["items"] if i["section"] == "anchor"]
        assert len(anchors) == 4

    def test_anchor_orderby_updated_at_desc(self, tmp_db, capsys):
        # Insert in random order, expect newest first.
        _insert_memory(tmp_db, "atom_old", "atom", "anchor", None,
                       "old", "2026-05-20T00:00:00Z")
        _insert_memory(tmp_db, "atom_new", "atom", "anchor", None,
                       "new", "2026-05-22T00:00:00Z")
        _insert_memory(tmp_db, "atom_mid", "atom", "anchor", None,
                       "mid", "2026-05-21T00:00:00Z")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        anchors = [i for i in payload["items"] if i["section"] == "anchor"]
        assert [a["name"] for a in anchors] == ["atom_new", "atom_mid", "atom_old"]


class TestThreadOpenQuota:
    """thread/open section quota of 2."""

    def test_thread_open_quota_2(self, tmp_db, capsys):
        for i in range(5):
            _insert_memory(
                tmp_db, f"atom_open_{i}", "atom", "thread", "open",
                f"d{i}", f"2026-05-22T{i:02d}:00:00Z",
            )

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        opens = [i for i in payload["items"] if i["section"] == "thread_open"]
        assert len(opens) == 2


class TestLogNeverAppears:
    """class=log rows are excluded from every section."""

    def test_log_class_excluded(self, tmp_db, capsys):
        _insert_memory(tmp_db, "atom_log_1", "atom", "log", None,
                       "log entry", "2026-05-22T00:00:00Z")
        _insert_memory(tmp_db, "atom_anchor_1", "atom", "anchor", None,
                       "anchor", "2026-05-22T00:00:00Z")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        names = {i["name"] for i in payload["items"]}
        assert "atom_log_1" not in names
        assert "atom_anchor_1" in names


class TestSupersedesExclusion:
    """Rows that are the dst of a supersedes edge are excluded."""

    def test_dst_of_supersedes_excluded(self, tmp_db, capsys):
        _insert_memory(tmp_db, "atom_old_anchor", "atom", "anchor", None,
                       "old", "2026-05-22T10:00:00Z")
        _insert_memory(tmp_db, "atom_new_anchor", "atom", "anchor", None,
                       "new", "2026-05-22T11:00:00Z")
        # new supersedes old: old must be excluded.
        _insert_link(tmp_db, "atom_new_anchor", "atom_old_anchor", "supersedes")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        names = {i["name"] for i in payload["items"]}
        assert "atom_old_anchor" not in names
        assert "atom_new_anchor" in names

    def test_other_link_kinds_do_not_exclude(self, tmp_db, capsys):
        """relates_to / derived_from do NOT trigger exclusion."""
        _insert_memory(tmp_db, "atom_a", "atom", "anchor", None,
                       "a", "2026-05-22T10:00:00Z")
        _insert_memory(tmp_db, "atom_b", "atom", "anchor", None,
                       "b", "2026-05-22T11:00:00Z")
        _insert_link(tmp_db, "atom_b", "atom_a", "relates_to")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        payload = json.loads(out)
        names = {i["name"] for i in payload["items"]}
        assert "atom_a" in names
        assert "atom_b" in names


class TestCharBudgetTrimOrder:
    """Char budget trims thread_open first, then anchor; never carry_forward."""

    def test_trim_thread_open_before_anchor(self, tmp_db, capsys):
        # Wide rows ensure overflow.
        _insert_memory(tmp_db, "atom_carry", "atom", "thread", "carry_forward",
                       "x" * 60, "2026-05-22T12:00:00Z")
        for i in range(4):
            _insert_memory(tmp_db, f"atom_anch_{i}", "atom", "anchor", None,
                           "y" * 60, f"2026-05-22T{10+i:02d}:00:00Z")
        for i in range(2):
            _insert_memory(tmp_db, f"atom_open_{i}", "atom", "thread", "open",
                           "z" * 60, f"2026-05-22T{8+i:02d}:00:00Z")

        # Budget tight enough to force trimming.
        rc = memory_mod._cmd_get_relevant(_args(max_chars=300))
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        # carry_forward survives.
        names = {i["name"] for i in payload["items"]}
        assert "atom_carry" in names, "carry_forward MUST never be trimmed"
        # thread/open should be the first to go.
        open_count = sum(1 for i in payload["items"]
                         if i["section"] == "thread_open")
        anchor_count = sum(1 for i in payload["items"]
                           if i["section"] == "anchor")
        # In a tight budget thread_open should be empty before anchor is drained.
        assert open_count == 0 or anchor_count > 0, (
            "Expected trim order to drain thread_open before anchor"
        )
        assert len(payload["block"]) <= 300 + 100, (
            "Block still must roughly fit budget (allow footer fudge)"
        )

    def test_carry_forward_alone_exceeds_budget_warning(self, tmp_db, capsys):
        # Pack 10 carry_forwards with long descriptions to ensure overflow.
        for i in range(10):
            _insert_memory(
                tmp_db, f"atom_cf_{i}", "atom", "thread", "carry_forward",
                "x" * 80, f"2026-05-22T{i:02d}:00:00Z",
            )

        rc = memory_mod._cmd_get_relevant(_args(max_chars=200))
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        # All 10 should still be present (we do NOT trim carry_forward).
        cf_count = sum(1 for i in payload["items"]
                       if i["section"] == "carry_forward")
        assert cf_count == 10
        # Block exceeds budget by design.
        assert len(payload["block"]) > 200
        assert payload.get("overflow_warning"), (
            "Expected overflow_warning when carry_forward alone exceeds budget"
        )


class TestEmptySectionsOmitHeader:
    """If a section has zero items, its header is omitted."""

    def test_only_anchors_no_threads(self, tmp_db, capsys):
        _insert_memory(tmp_db, "atom_only_1", "atom", "anchor", None,
                       "alone", "2026-05-22T10:00:00Z")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        block = payload["block"]
        assert "About you" in block
        assert "For this session" not in block
        assert "Open threads" not in block

    def test_all_empty_returns_empty_block(self, tmp_db, capsys):
        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        assert payload["block"] == ""
        assert payload["items"] == []


class TestProjectAwareInjection:
    """Read/injection is project-aware: when the cwd resolves to a project,
    prioritise its anchored rows, include unanchored (project_ref NULL) rows
    as the workspace past, and exclude rows anchored to OTHER projects. When
    the cwd resolves to no project, behaviour is unchanged (all workspace rows).
    """

    def _seed(self, tmp_db, proj_dir):
        _insert_project(tmp_db, "p1", str(proj_dir.resolve()), "id/p1")
        # anchor rows across three anchoring states.
        _insert_memory(tmp_db, "atom_mine", "atom", "anchor", None,
                       "mine", "2026-05-22T10:00:00Z", project_ref="id/p1")
        _insert_memory(tmp_db, "atom_legacy", "atom", "anchor", None,
                       "legacy null", "2026-05-22T09:00:00Z", project_ref=None)
        _insert_memory(tmp_db, "atom_other", "atom", "anchor", None,
                       "other project", "2026-05-22T11:00:00Z",
                       project_ref="id/other")

    def test_active_project_scopes_and_prioritises(self, tmp_db, tmp_path,
                                                   monkeypatch, capsys):
        proj_dir = tmp_path / "p1"
        proj_dir.mkdir()
        self._seed(tmp_db, proj_dir)
        monkeypatch.chdir(proj_dir)

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        items = json.loads(out)["items"]
        names = [i["name"] for i in items]

        # Row anchored to a DIFFERENT project is excluded.
        assert "atom_other" not in names
        # Active-project row and legacy NULL row are both present.
        assert "atom_mine" in names
        assert "atom_legacy" in names
        # Active-project row floats above the NULL row despite older updated_at.
        assert names.index("atom_mine") < names.index("atom_legacy")

    def test_no_active_project_keeps_all_workspace_rows(self, tmp_db, tmp_path,
                                                        monkeypatch, capsys):
        proj_dir = tmp_path / "p1"
        proj_dir.mkdir()
        self._seed(tmp_db, proj_dir)
        # cwd is the workspace root, ABOVE the project dir -> no active project.
        monkeypatch.chdir(tmp_path)

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        assert rc == 0
        names = {i["name"] for i in json.loads(out)["items"]}
        # Unchanged workspace behaviour: every anchoring state is visible.
        assert {"atom_mine", "atom_legacy", "atom_other"} <= names


class TestHeaderStructure:
    """Section headers use the T7-defined naming."""

    def test_headers_render_with_em_dash(self, tmp_db, capsys):
        _insert_memory(tmp_db, "atom_carry_1", "atom", "thread", "carry_forward",
                       "carry", "2026-05-22T10:00:00Z")
        _insert_memory(tmp_db, "atom_anchor_1", "atom", "anchor", None,
                       "anchor", "2026-05-22T09:00:00Z")
        _insert_memory(tmp_db, "atom_open_1", "atom", "thread", "open",
                       "open", "2026-05-22T08:00:00Z")

        rc = memory_mod._cmd_get_relevant(_args())
        out = capsys.readouterr().out
        payload = json.loads(out)
        block = payload["block"]
        assert "## Memory — For this session" in block
        assert "## Memory — About you / What I know" in block
        assert "## Memory — Open threads" in block


class TestSectionsFilter:
    """--sections filters which curated sections render (subagent cut).

    The subagent-dispatch path passes --sections=anchor so a dispatched
    subagent receives only the durable anchors, never the session-scoped
    carry_forward or open-thread state. The orchestrator omits --sections and
    keeps all three sections (covered by TestSectionHeaders above).
    """

    def _seed_all_three(self, tmp_db):
        _insert_memory(tmp_db, "atom_carry_1", "atom", "thread", "carry_forward",
                       "carry", "2026-05-22T10:00:00Z")
        _insert_memory(tmp_db, "atom_anchor_1", "atom", "anchor", None,
                       "anchor", "2026-05-22T09:00:00Z")
        _insert_memory(tmp_db, "atom_open_1", "atom", "thread", "open",
                       "open", "2026-05-22T08:00:00Z")

    def test_sections_anchor_only_renders_anchor(self, tmp_db, capsys):
        self._seed_all_three(tmp_db)
        rc = memory_mod._cmd_get_relevant(_args(sections="anchor"))
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        block = payload["block"]
        assert "## Memory — About you / What I know" in block
        assert "## Memory — For this session" not in block
        assert "## Memory — Open threads" not in block
        # items are anchors only
        assert all(i["section"] == "anchor" for i in payload["items"])

    def test_sections_omitted_renders_all_three(self, tmp_db, capsys):
        self._seed_all_three(tmp_db)
        rc = memory_mod._cmd_get_relevant(_args())  # no sections -> all three
        out = capsys.readouterr().out
        assert rc == 0
        block = json.loads(out)["block"]
        assert "## Memory — For this session" in block
        assert "## Memory — About you / What I know" in block
        assert "## Memory — Open threads" in block

    def test_sections_empty_string_falls_back_to_all(self, tmp_db, capsys):
        """A blank/whitespace --sections is a safe fallback to all sections."""
        self._seed_all_three(tmp_db)
        rc = memory_mod._cmd_get_relevant(_args(sections="   "))
        out = capsys.readouterr().out
        assert rc == 0
        block = json.loads(out)["block"]
        assert "## Memory — For this session" in block
        assert "## Memory — About you / What I know" in block
        assert "## Memory — Open threads" in block

    def test_sections_unknown_token_ignored_falls_back_to_all(self, tmp_db, capsys):
        """Only unknown tokens -> no valid section -> safe fallback to all."""
        self._seed_all_three(tmp_db)
        rc = memory_mod._cmd_get_relevant(_args(sections="bogus"))
        out = capsys.readouterr().out
        assert rc == 0
        block = json.loads(out)["block"]
        assert "## Memory — About you / What I know" in block
        assert "## Memory — For this session" in block

    def test_sections_multi_subset(self, tmp_db, capsys):
        """--sections=anchor,thread_open renders those two, drops carry_forward."""
        self._seed_all_three(tmp_db)
        rc = memory_mod._cmd_get_relevant(_args(sections="anchor,thread_open"))
        out = capsys.readouterr().out
        assert rc == 0
        block = json.loads(out)["block"]
        assert "## Memory — About you / What I know" in block
        assert "## Memory — Open threads" in block
        assert "## Memory — For this session" not in block
