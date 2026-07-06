"""Tests for the surgical reconciliation writers (workspace-identity brief, M4/T10).

Covers the two workspace-preserving primitives added to gaia.store.writer:

    delete_projects    -- targeted deletion of `projects` rows within ONE
                          workspace, leaving the workspaces row and every
                          non-project child (memory, PCC, briefs, episodes)
                          intact -- the surgical alternative to wipe_workspace
                          for a LIVE workspace.
    relocate_contracts -- re-key project_context_contracts rows between
                          workspaces (the only correction path for a mis-keyed
                          contract, since `gaia scan` never touches that table).

Plus a classification guard: the two CLI verbs that wrap these writers
(`gaia context delete-projects`, `gaia context move-contracts`) must be gated
as T3 by the security hook via their verb tokens, and their --dry-run form
must downgrade to non-mutative (T2/preview).

All tests run against a fresh temp DB (writer._connect materializes schema.sql
on first connect); the real ~/.gaia/gaia.db is never touched.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# hooks/ on sys.path for the classifier import (T3 guard).
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from gaia.store import writer  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    delete_projects,
    relocate_contracts,
    relocate_memory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Return the path to a fresh, schema-materialized temp DB."""
    db_path = tmp_path / "gaia.db"
    # First connect materializes schema.sql (writer._connect fresh path).
    con = writer._connect(db_path)
    con.close()
    return db_path


def _conn(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _seed_workspace(db_path: Path, name: str) -> None:
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z')",
            (name, name),
        )
        con.commit()
    finally:
        con.close()


def _seed_project(db_path: Path, workspace: str, name: str, *,
                  group_name=None, status="active", path=None, pid=None) -> None:
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO projects (workspace, name, group_name, status, path, "
            "project_identity) VALUES (?, ?, ?, ?, ?, ?)",
            (workspace, name, group_name, status, path, pid),
        )
        con.commit()
    finally:
        con.close()


def _seed_memory(db_path: Path, workspace: str, name: str, *,
                 type_="project", body="body", cls="log", status=None,
                 description="desc") -> None:
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z')",
            (workspace, workspace),
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, "
            "status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            (workspace, name, type_, description, body, cls, status),
        )
        con.commit()
    finally:
        con.close()


def _seed_memory_link(db_path: Path, workspace: str, src: str, dst: str,
                      kind: str = "relates_to") -> None:
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind, created_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            (workspace, src, dst, kind),
        )
        con.commit()
    finally:
        con.close()


def _seed_pcc(db_path: Path, workspace: str, contract: str, payload: str) -> None:
    con = _conn(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, '2026-01-01T00:00:00Z')",
            (workspace, workspace),
        )
        con.execute(
            "INSERT INTO project_context_contracts "
            "(workspace, contract_name, payload, updated_at) "
            "VALUES (?, ?, ?, '2026-01-01T00:00:00Z')",
            (workspace, contract, payload),
        )
        con.commit()
    finally:
        con.close()


def _project_names(db_path: Path, workspace: str) -> set:
    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM projects WHERE workspace = ?", (workspace,)
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        con.close()


# ===========================================================================
# delete_projects
# ===========================================================================

class TestDeleteProjectsByGroup:
    """The github-repos case: delete a stale group inside a LIVE workspace."""

    def _seed_me(self, db):
        _seed_workspace(db, "me")
        # Live survivors (no group / not github-repos).
        _seed_project(db, "me", "gaia", status="active")
        _seed_project(db, "me", "metraton.github.io", status="active")
        # Stale github-repos group (dir no longer exists on disk).
        for n in ("engram", "skills", "taskmaster"):
            _seed_project(db, "me", n, group_name="github-repos", status="missing")
        # A live-workspace collateral row that must survive the surgery.
        con = _conn(db)
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, class) "
            "VALUES ('me', 'project_note', 'project', 'keep me', 'log')"
        )
        con.commit()
        con.close()
        _seed_pcc(db, "me", "project_identity", '{"gaia": {}}')

    def test_deletes_only_the_group(self, db):
        self._seed_me(db)
        res = delete_projects("me", group_name="github-repos", db_path=db)
        assert res["status"] == "applied"
        assert res["deleted"] == 3
        # Survivors intact, group gone.
        assert _project_names(db, "me") == {"gaia", "metraton.github.io"}

    def test_workspace_row_survives(self, db):
        self._seed_me(db)
        delete_projects("me", group_name="github-repos", db_path=db)
        con = _conn(db)
        row = con.execute("SELECT name FROM workspaces WHERE name='me'").fetchone()
        con.close()
        assert row is not None  # NOT wiped

    def test_memory_and_pcc_survive(self, db):
        self._seed_me(db)
        delete_projects("me", group_name="github-repos", db_path=db)
        con = _conn(db)
        mem = con.execute("SELECT COUNT(*) c FROM memory WHERE workspace='me'").fetchone()["c"]
        pcc = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts WHERE workspace='me'"
        ).fetchone()["c"]
        con.close()
        assert mem == 1  # collateral memory preserved
        assert pcc == 1  # collateral PCC preserved


class TestDeleteProjectsFilters:
    def test_by_status(self, db):
        _seed_workspace(db, "rnd")
        _seed_project(db, "rnd", "a", status="missing")
        _seed_project(db, "rnd", "b", status="missing")
        _seed_project(db, "rnd", "live", status="active")
        res = delete_projects("rnd", status="missing", db_path=db)
        assert res["deleted"] == 2
        assert _project_names(db, "rnd") == {"live"}

    def test_by_names(self, db):
        _seed_workspace(db, "rnd")
        for n in ("terraform", "ai-project-guidance", "keep"):
            _seed_project(db, "rnd", n, status="missing")
        res = delete_projects("rnd", names=["terraform", "ai-project-guidance"], db_path=db)
        assert res["deleted"] == 2
        assert _project_names(db, "rnd") == {"keep"}

    def test_by_path(self, db):
        _seed_workspace(db, "qxo")
        _seed_project(db, "qxo", "qxo-monorepo", status="missing",
                      path="/home/jorge/ws/aaxis/qxo/qxo-monorepo")
        _seed_project(db, "qxo", "other", status="missing", path="/somewhere/else")
        res = delete_projects("qxo", path="/home/jorge/ws/aaxis/qxo/qxo-monorepo", db_path=db)
        assert res["deleted"] == 1
        assert _project_names(db, "qxo") == {"other"}

    def test_by_identity(self, db):
        _seed_workspace(db, "w")
        _seed_project(db, "w", "p1", pid="git-common-dir:/abc")
        _seed_project(db, "w", "p2", pid="git-common-dir:/xyz")
        res = delete_projects("w", project_identity="git-common-dir:/abc", db_path=db)
        assert res["deleted"] == 1
        assert _project_names(db, "w") == {"p2"}

    def test_filters_are_anded(self, db):
        _seed_workspace(db, "me")
        _seed_project(db, "me", "x", group_name="github-repos", status="missing")
        _seed_project(db, "me", "y", group_name="github-repos", status="active")
        # group + status together: only the missing github-repos row.
        res = delete_projects("me", group_name="github-repos", status="missing", db_path=db)
        assert res["deleted"] == 1
        assert _project_names(db, "me") == {"y"}

    def test_scoped_to_workspace(self, db):
        # Same group name in two workspaces: only the named workspace is touched.
        _seed_workspace(db, "me")
        _seed_workspace(db, "other")
        _seed_project(db, "me", "x", group_name="g", status="missing")
        _seed_project(db, "other", "x", group_name="g", status="missing")
        delete_projects("me", group_name="g", db_path=db)
        assert _project_names(db, "me") == set()
        assert _project_names(db, "other") == {"x"}


class TestDeleteProjectsCascade:
    def test_child_apps_cascade_but_survivors_keep_theirs(self, db):
        _seed_workspace(db, "me")
        _seed_project(db, "me", "doomed", group_name="github-repos", status="missing")
        _seed_project(db, "me", "gaia", status="active")
        con = _conn(db)
        con.execute("INSERT INTO apps (workspace, project, name) VALUES ('me','doomed','svc')")
        con.execute("INSERT INTO apps (workspace, project, name) VALUES ('me','gaia','cli')")
        con.commit()
        con.close()

        delete_projects("me", group_name="github-repos", db_path=db)

        con = _conn(db)
        doomed_apps = con.execute(
            "SELECT COUNT(*) c FROM apps WHERE project='doomed'"
        ).fetchone()["c"]
        gaia_apps = con.execute(
            "SELECT COUNT(*) c FROM apps WHERE project='gaia'"
        ).fetchone()["c"]
        con.close()
        assert doomed_apps == 0  # cascaded away with its project
        assert gaia_apps == 1    # survivor's children untouched


class TestDeleteProjectsSafety:
    def test_no_filter_raises(self, db):
        _seed_workspace(db, "me")
        with pytest.raises(ValueError, match="at least one filter"):
            delete_projects("me", db_path=db)

    def test_empty_workspace_raises(self, db):
        with pytest.raises(ValueError, match="workspace is required"):
            delete_projects("", group_name="g", db_path=db)

    def test_dry_run_deletes_nothing(self, db):
        _seed_workspace(db, "me")
        _seed_project(db, "me", "x", group_name="github-repos", status="missing")
        res = delete_projects("me", group_name="github-repos", dry_run=True, db_path=db)
        assert res["status"] == "preview"
        assert res["deleted"] == 0
        assert len(res["matched"]) == 1
        assert _project_names(db, "me") == {"x"}  # still there

    def test_matched_reports_row_detail(self, db):
        _seed_workspace(db, "me")
        _seed_project(db, "me", "engram", group_name="github-repos",
                      status="missing", path="/gone/engram")
        res = delete_projects("me", group_name="github-repos", dry_run=True, db_path=db)
        m = res["matched"][0]
        assert m["name"] == "engram"
        assert m["group_name"] == "github-repos"
        assert m["status"] == "missing"
        assert m["path"] == "/gone/engram"


# ===========================================================================
# relocate_contracts
# ===========================================================================

class TestRelocateContractsBasic:
    def test_moves_named_contracts(self, db):
        _seed_pcc(db, "me", "project_identity", '{"aos": {}}')
        _seed_pcc(db, "me", "infrastructure", '{"aos_secret": {}}')
        _seed_pcc(db, "me", "operational_guidelines", '{"gaia": {}}')  # NOT moved
        _seed_workspace(db, "aaxis")

        res = relocate_contracts(
            "me", "aaxis", ["project_identity", "infrastructure"], db_path=db
        )
        assert res["status"] == "applied"
        assert set(res["moved"]) == {"project_identity", "infrastructure"}
        assert res["missing"] == []

        con = _conn(db)
        me_left = {r["contract_name"] for r in con.execute(
            "SELECT contract_name FROM project_context_contracts WHERE workspace='me'"
        ).fetchall()}
        aaxis_now = {r["contract_name"] for r in con.execute(
            "SELECT contract_name FROM project_context_contracts WHERE workspace='aaxis'"
        ).fetchall()}
        con.close()
        assert me_left == {"operational_guidelines"}  # unmoved survivor stays
        assert aaxis_now == {"project_identity", "infrastructure"}

    def test_payload_preserved(self, db):
        _seed_pcc(db, "me", "project_identity", '{"aos": {"name": "AOS"}}')
        _seed_workspace(db, "aaxis")
        relocate_contracts("me", "aaxis", ["project_identity"], db_path=db)
        con = _conn(db)
        payload = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='aaxis' AND contract_name='project_identity'"
        ).fetchone()["payload"]
        con.close()
        assert payload == '{"aos": {"name": "AOS"}}'

    def test_target_workspace_autocreated(self, db):
        _seed_pcc(db, "me", "project_identity", "{}")
        # 'aaxis' does NOT exist yet.
        relocate_contracts("me", "aaxis", ["project_identity"], db_path=db)
        con = _conn(db)
        ws = con.execute("SELECT name FROM workspaces WHERE name='aaxis'").fetchone()
        con.close()
        assert ws is not None

    def test_history_trigger_records_move(self, db):
        _seed_pcc(db, "me", "project_identity", '{"aos": {}}')
        _seed_workspace(db, "aaxis")
        relocate_contracts("me", "aaxis", ["project_identity"], db_path=db)
        con = _conn(db)
        n = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts_history "
            "WHERE contract_key='project_identity'"
        ).fetchone()["c"]
        con.close()
        assert n >= 1  # trg_pcc_history fired on the UPDATE


class TestRelocateContractsMissingAndConflict:
    def test_missing_contract_reported_noop(self, db):
        _seed_pcc(db, "me", "project_identity", "{}")
        _seed_workspace(db, "aaxis")
        res = relocate_contracts(
            "me", "aaxis", ["project_identity", "does_not_exist"], db_path=db
        )
        assert res["moved"] == ["project_identity"]
        assert res["missing"] == ["does_not_exist"]

    def test_idempotent_second_run(self, db):
        _seed_pcc(db, "me", "project_identity", "{}")
        _seed_workspace(db, "aaxis")
        relocate_contracts("me", "aaxis", ["project_identity"], db_path=db)
        res2 = relocate_contracts("me", "aaxis", ["project_identity"], db_path=db)
        assert res2["moved"] == []
        assert res2["missing"] == ["project_identity"]

    def test_conflict_error_rolls_back(self, db):
        _seed_pcc(db, "me", "project_identity", '{"src": 1}')
        _seed_pcc(db, "me", "infrastructure", '{"src": 2}')
        _seed_pcc(db, "aaxis", "project_identity", '{"dst": 9}')  # collision
        with pytest.raises(ValueError, match="already has contract"):
            relocate_contracts(
                "me", "aaxis", ["infrastructure", "project_identity"],
                on_conflict="error", db_path=db,
            )
        # Whole transaction rolled back: even the non-conflicting 'infrastructure'
        # is still under 'me'.
        con = _conn(db)
        me_rows = {r["contract_name"] for r in con.execute(
            "SELECT contract_name FROM project_context_contracts WHERE workspace='me'"
        ).fetchall()}
        con.close()
        assert me_rows == {"project_identity", "infrastructure"}

    def test_conflict_skip_leaves_both(self, db):
        _seed_pcc(db, "me", "project_identity", '{"src": 1}')
        _seed_pcc(db, "aaxis", "project_identity", '{"dst": 9}')
        res = relocate_contracts(
            "me", "aaxis", ["project_identity"], on_conflict="skip", db_path=db
        )
        assert res["skipped"] == ["project_identity"]
        assert res["moved"] == []
        con = _conn(db)
        me = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='me' AND contract_name='project_identity'"
        ).fetchone()["payload"]
        dst = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='aaxis' AND contract_name='project_identity'"
        ).fetchone()["payload"]
        con.close()
        assert me == '{"src": 1}'   # source untouched
        assert dst == '{"dst": 9}'  # target untouched

    def test_conflict_overwrite_replaces_target(self, db):
        _seed_pcc(db, "me", "project_identity", '{"src": 1}')
        _seed_pcc(db, "aaxis", "project_identity", '{"dst": 9}')
        res = relocate_contracts(
            "me", "aaxis", ["project_identity"], on_conflict="overwrite", db_path=db
        )
        assert res["moved"] == ["project_identity"]
        assert res["overwritten"] == ["project_identity"]
        con = _conn(db)
        # target now carries the source payload; source is gone.
        dst = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace='aaxis' AND contract_name='project_identity'"
        ).fetchone()["payload"]
        me = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts WHERE workspace='me'"
        ).fetchone()["c"]
        con.close()
        assert dst == '{"src": 1}'
        assert me == 0


class TestRelocateContractsSafetyAndDryRun:
    def test_dry_run_mutates_nothing(self, db):
        _seed_pcc(db, "me", "project_identity", "{}")
        _seed_workspace(db, "aaxis")
        res = relocate_contracts(
            "me", "aaxis", ["project_identity"], dry_run=True, db_path=db
        )
        assert res["status"] == "preview"
        assert res["moved"] == ["project_identity"]
        con = _conn(db)
        me = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts WHERE workspace='me'"
        ).fetchone()["c"]
        aaxis = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts WHERE workspace='aaxis'"
        ).fetchone()["c"]
        con.close()
        assert me == 1     # still under source
        assert aaxis == 0  # nothing written to target

    def test_same_workspace_raises(self, db):
        with pytest.raises(ValueError, match="identical"):
            relocate_contracts("me", "me", ["x"], db_path=db)

    def test_empty_contracts_raises(self, db):
        with pytest.raises(ValueError, match="at least one contract"):
            relocate_contracts("me", "aaxis", [], db_path=db)

    def test_invalid_on_conflict_raises(self, db):
        with pytest.raises(ValueError, match="invalid on_conflict"):
            relocate_contracts("me", "aaxis", ["x"], on_conflict="bogus", db_path=db)


# ===========================================================================
# relocate_memory
# ===========================================================================

@pytest.fixture(autouse=True)
def _no_dispatch_agent(monkeypatch):
    """Curated-memory writers refuse a non-curator subagent dispatch. Ensure the
    guard treats the test as a human shell (GAIA_DISPATCH_AGENT unset)."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


class TestRelocateMemoryBasic:
    def test_moves_named_rows_preserving_columns(self, db):
        # The rnd rescue case: 2 notes belonging to `me` mis-keyed under `rnd`.
        _seed_memory(db, "rnd", "project_gaia_roadmap", type_="project",
                     body="roadmap body", cls="log", description="roadmap")
        _seed_memory(db, "rnd", "user_blog_articles", type_="user",
                     body="blog body", cls="thread", status="open", description="blog")
        _seed_memory(db, "rnd", "rnd_only_note", body="stays")  # NOT moved
        _seed_workspace(db, "me")

        res = relocate_memory(
            "rnd", "me", ["project_gaia_roadmap", "user_blog_articles"], db_path=db
        )
        assert res["status"] == "applied"
        assert set(res["moved"]) == {"project_gaia_roadmap", "user_blog_articles"}
        assert res["missing"] == []

        con = _conn(db)
        rnd_left = {r["name"] for r in con.execute(
            "SELECT name FROM memory WHERE workspace='rnd'").fetchall()}
        me_now = {r["name"] for r in con.execute(
            "SELECT name FROM memory WHERE workspace='me'").fetchall()}
        # Every non-workspace column preserved on the moved thread row.
        row = con.execute(
            "SELECT type, body, class, status, description FROM memory "
            "WHERE workspace='me' AND name='user_blog_articles'").fetchone()
        con.close()
        assert rnd_left == {"rnd_only_note"}
        assert me_now == {"project_gaia_roadmap", "user_blog_articles"}
        assert (row["type"], row["body"], row["class"], row["status"], row["description"]) == \
            ("user", "blog body", "thread", "open", "blog")

    def test_fts_mirror_follows_the_move(self, db):
        _seed_memory(db, "rnd", "user_blog_articles", body="unique_blog_token",
                     description="blog")
        _seed_workspace(db, "me")
        relocate_memory("rnd", "me", ["user_blog_articles"], db_path=db)
        con = _conn(db)
        # FTS row must now report workspace='me' (memory_au trigger rewrote it).
        hit = con.execute(
            "SELECT workspace, name FROM memory_fts WHERE memory_fts MATCH 'unique_blog_token'"
        ).fetchall()
        con.close()
        assert len(hit) == 1
        assert hit[0]["workspace"] == "me"
        assert hit[0]["name"] == "user_blog_articles"

    def test_target_workspace_autocreated(self, db):
        _seed_memory(db, "rnd", "n", body="b")
        relocate_memory("rnd", "aaxis", ["n"], db_path=db)
        con = _conn(db)
        ws = con.execute("SELECT name FROM workspaces WHERE name='aaxis'").fetchone()
        con.close()
        assert ws is not None


class TestRelocateMemoryLinks:
    def test_intra_set_link_follows_the_pair(self, db):
        _seed_memory(db, "rnd", "a", body="a")
        _seed_memory(db, "rnd", "b", body="b")
        _seed_memory_link(db, "rnd", "a", "b", "relates_to")
        _seed_workspace(db, "me")
        res = relocate_memory("rnd", "me", ["a", "b"], db_path=db)
        assert res["links_moved"] == [{"src": "a", "dst": "b", "kind": "relates_to"}]
        assert res["partial_links"] == []
        con = _conn(db)
        rnd_links = con.execute(
            "SELECT COUNT(*) c FROM memory_links WHERE workspace='rnd'").fetchone()["c"]
        me_links = con.execute(
            "SELECT COUNT(*) c FROM memory_links WHERE workspace='me'").fetchone()["c"]
        con.close()
        assert rnd_links == 0
        assert me_links == 1

    def test_partial_link_left_in_place_and_reported(self, db):
        # Link a->c where only 'a' is moved: cannot stay consistent, so it is
        # left under 'rnd' and reported as a partial link.
        _seed_memory(db, "rnd", "a", body="a")
        _seed_memory(db, "rnd", "c", body="c")
        _seed_memory_link(db, "rnd", "a", "c", "relates_to")
        _seed_workspace(db, "me")
        res = relocate_memory("rnd", "me", ["a"], db_path=db)
        assert res["moved"] == ["a"]
        assert res["links_moved"] == []
        assert res["partial_links"] == [{"src": "a", "dst": "c", "kind": "relates_to"}]
        con = _conn(db)
        rnd_links = con.execute(
            "SELECT COUNT(*) c FROM memory_links WHERE workspace='rnd'").fetchone()["c"]
        con.close()
        assert rnd_links == 1  # untouched


class TestRelocateMemoryConflictAndSafety:
    def test_missing_reported_noop(self, db):
        _seed_memory(db, "rnd", "a", body="a")
        _seed_workspace(db, "me")
        res = relocate_memory("rnd", "me", ["a", "ghost"], db_path=db)
        assert res["moved"] == ["a"]
        assert res["missing"] == ["ghost"]

    def test_idempotent_second_run(self, db):
        _seed_memory(db, "rnd", "a", body="a")
        _seed_workspace(db, "me")
        relocate_memory("rnd", "me", ["a"], db_path=db)
        res2 = relocate_memory("rnd", "me", ["a"], db_path=db)
        assert res2["moved"] == []
        assert res2["missing"] == ["a"]

    def test_conflict_error_rolls_back(self, db):
        _seed_memory(db, "rnd", "a", body="src_a")
        _seed_memory(db, "rnd", "b", body="src_b")
        _seed_memory(db, "me", "a", body="dst_a")  # collision on 'a'
        with pytest.raises(ValueError, match="already has memory"):
            relocate_memory("rnd", "me", ["b", "a"], on_conflict="error", db_path=db)
        # Whole transaction rolled back: 'b' still under rnd too.
        con = _conn(db)
        rnd = {r["name"] for r in con.execute(
            "SELECT name FROM memory WHERE workspace='rnd'").fetchall()}
        con.close()
        assert rnd == {"a", "b"}

    def test_conflict_skip_leaves_both(self, db):
        _seed_memory(db, "rnd", "a", body="src_a")
        _seed_memory(db, "me", "a", body="dst_a")
        res = relocate_memory("rnd", "me", ["a"], on_conflict="skip", db_path=db)
        assert res["skipped"] == ["a"]
        assert res["moved"] == []
        con = _conn(db)
        rnd = con.execute(
            "SELECT body FROM memory WHERE workspace='rnd' AND name='a'").fetchone()["body"]
        me = con.execute(
            "SELECT body FROM memory WHERE workspace='me' AND name='a'").fetchone()["body"]
        con.close()
        assert rnd == "src_a"
        assert me == "dst_a"

    def test_conflict_overwrite_replaces_target(self, db):
        _seed_memory(db, "rnd", "a", body="src_a")
        _seed_memory(db, "me", "a", body="dst_a")
        res = relocate_memory("rnd", "me", ["a"], on_conflict="overwrite", db_path=db)
        assert res["moved"] == ["a"]
        assert res["overwritten"] == ["a"]
        con = _conn(db)
        me = con.execute(
            "SELECT body FROM memory WHERE workspace='me' AND name='a'").fetchone()["body"]
        rnd = con.execute(
            "SELECT COUNT(*) c FROM memory WHERE workspace='rnd'").fetchone()["c"]
        con.close()
        assert me == "src_a"
        assert rnd == 0

    def test_dry_run_mutates_nothing(self, db):
        _seed_memory(db, "rnd", "a", body="a")
        _seed_workspace(db, "me")
        res = relocate_memory("rnd", "me", ["a"], dry_run=True, db_path=db)
        assert res["status"] == "preview"
        assert res["moved"] == ["a"]
        con = _conn(db)
        rnd = con.execute(
            "SELECT COUNT(*) c FROM memory WHERE workspace='rnd'").fetchone()["c"]
        me = con.execute(
            "SELECT COUNT(*) c FROM memory WHERE workspace='me'").fetchone()["c"]
        con.close()
        assert rnd == 1
        assert me == 0

    def test_same_workspace_raises(self, db):
        with pytest.raises(ValueError, match="identical"):
            relocate_memory("me", "me", ["a"], db_path=db)

    def test_empty_names_raises(self, db):
        with pytest.raises(ValueError, match="at least one name"):
            relocate_memory("rnd", "me", [], db_path=db)

    def test_invalid_on_conflict_raises(self, db):
        with pytest.raises(ValueError, match="invalid on_conflict"):
            relocate_memory("rnd", "me", ["a"], on_conflict="bogus", db_path=db)

    def test_non_curator_dispatch_forbidden(self, db, monkeypatch):
        from gaia.store.writer import MemoryWriteForbidden
        _seed_memory(db, "rnd", "a", body="a")
        _seed_workspace(db, "me")
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
        with pytest.raises(MemoryWriteForbidden):
            relocate_memory("rnd", "me", ["a"], db_path=db)

    def test_curator_dispatch_allowed(self, db, monkeypatch):
        _seed_memory(db, "rnd", "a", body="a")
        _seed_workspace(db, "me")
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-orchestrator")
        res = relocate_memory("rnd", "me", ["a"], db_path=db)
        assert res["moved"] == ["a"]


# ===========================================================================
# T3 classification guard for the CLI verbs
# ===========================================================================

class TestCliVerbsClassifyAsT3:
    """The verbs wrapping these writers must gate as T3 via the security hook.

    delete-projects -> hyphen-splits to 'delete' (in MUTATIVE_VERBS)
    move-contracts  -> hyphen-splits to 'move'   (in MUTATIVE_VERBS)
    """

    def _detect(self, cmd):
        from modules.security.mutative_verbs import detect_mutative_command
        return detect_mutative_command(cmd)

    def test_delete_projects_is_mutative(self):
        r = self._detect("gaia context delete-projects --workspace me --group github-repos")
        assert r.is_mutative is True

    def test_move_contracts_is_mutative(self):
        r = self._detect(
            "gaia context move-contracts --from me --to aaxis --contract project_identity"
        )
        assert r.is_mutative is True

    def test_delete_projects_dry_run_downgrades(self):
        r = self._detect(
            "gaia context delete-projects --workspace me --group github-repos --dry-run"
        )
        assert r.is_mutative is False  # simulation override

    def test_move_contracts_dry_run_downgrades(self):
        r = self._detect(
            "gaia context move-contracts --from me --to aaxis --contract x --dry-run"
        )
        assert r.is_mutative is False

    def test_move_memory_is_mutative(self):
        r = self._detect(
            "gaia context move-memory --from rnd --to me --name project_gaia_roadmap"
        )
        assert r.is_mutative is True

    def test_move_memory_dry_run_downgrades(self):
        r = self._detect(
            "gaia context move-memory --from rnd --to me --name x --dry-run"
        )
        assert r.is_mutative is False

    def test_read_only_context_query_is_not_mutative(self):
        # Sanity: the read-only sibling stays T0.
        r = self._detect('gaia context query "SELECT 1"')
        assert r.is_mutative is False


# ===========================================================================
# CLI wiring (dry-run path only -- non-mutative, safe to run in-process)
# ===========================================================================

class TestCliWiring:
    """End-to-end wiring of the two new `gaia context` subcommands via the
    in-process dispatcher. Only the --dry-run path is exercised (it mutates
    nothing) so the tests never depend on the approval flow."""

    def _args(self, **kw):
        import argparse
        return argparse.Namespace(**kw)

    def _seed(self, db):
        _seed_workspace(db, "me")
        _seed_project(db, "me", "engram", group_name="github-repos", status="missing")
        _seed_project(db, "me", "gaia", status="active")
        _seed_pcc(db, "me", "project_identity", "{}")

    def _import_cli(self):
        bin_dir = _REPO_ROOT / "bin"
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))
        from cli.context import cmd_context
        return cmd_context

    def test_delete_projects_dry_run_dispatches(self, db, monkeypatch, capsys):
        self._seed(db)
        monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
        cmd_context = self._import_cli()
        rc = cmd_context(self._args(
            context_cmd="delete-projects", workspace="me", group="github-repos",
            status=None, name=None, path=None, identity=None,
            dry_run=True, json=True, yes=False,
        ))
        out = capsys.readouterr().out
        assert rc == 0
        assert "engram" in out
        # nothing deleted
        assert _project_names(db, "me") == {"engram", "gaia"}

    def test_delete_projects_requires_a_filter(self, db, monkeypatch, capsys):
        self._seed(db)
        monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
        cmd_context = self._import_cli()
        rc = cmd_context(self._args(
            context_cmd="delete-projects", workspace="me", group=None,
            status=None, name=None, path=None, identity=None,
            dry_run=True, json=False, yes=False,
        ))
        assert rc == 2  # refuses without a filter beyond --workspace

    def test_move_contracts_dry_run_dispatches(self, db, monkeypatch, capsys):
        self._seed(db)
        monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
        cmd_context = self._import_cli()
        rc = cmd_context(self._args(
            context_cmd="move-contracts", from_workspace="me", to_workspace="aaxis",
            contract=["project_identity"], on_conflict="error",
            dry_run=True, json=True, yes=False,
        ))
        out = capsys.readouterr().out
        assert rc == 0
        assert "project_identity" in out
        # nothing moved
        con = _conn(db)
        me = con.execute(
            "SELECT COUNT(*) c FROM project_context_contracts WHERE workspace='me'"
        ).fetchone()["c"]
        con.close()
        assert me == 1

    def test_move_memory_dry_run_dispatches(self, db, monkeypatch, capsys):
        monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
        _seed_memory(db, "rnd", "project_gaia_roadmap", body="b")
        _seed_workspace(db, "me")
        monkeypatch.setenv("GAIA_DATA_DIR", str(db.parent))
        cmd_context = self._import_cli()
        rc = cmd_context(self._args(
            context_cmd="move-memory", from_workspace="rnd", to_workspace="me",
            name=["project_gaia_roadmap"], on_conflict="error",
            dry_run=True, json=True, yes=False,
        ))
        out = capsys.readouterr().out
        assert rc == 0
        assert "project_gaia_roadmap" in out
        con = _conn(db)
        rnd = con.execute(
            "SELECT COUNT(*) c FROM memory WHERE workspace='rnd'").fetchone()["c"]
        con.close()
        assert rnd == 1  # dry-run mutated nothing
