"""
SV2 (scan-v2, DETECCIÓN Y REPORT) -- tests for the cross-DB detection layer of
``tools.scan.classify.scan``.

The scan is mono-workspace but, during the run, it CONSULTS the DB (read-only in
dry-run) to detect whether a repo already exists elsewhere and emits ALERTS; a
human adjudicates later. The move-stable signal is the NORMALIZED git remote
(``project_identity`` -- the git-common-dir -- changes when a repo is physically
moved; the remote survives).

Coverage (the 7 SV2 cases):
  1. ``projects.remote_url`` is persisted on the CLI path after apply=True.
  2. ``move_candidate`` 1:1 is emitted when a repo appears whose remote matches
     EXACTLY one row (status) in ANOTHER workspace.
  3. Two live clones of the same remote -> NO ``move_candidate``
     (anti-false-positive: a remote matching >1 candidate is never guessed).
  4. ``rename_candidate`` when the folder basename != the persisted project name
     (an R4-collapse repo whose slot was collision-disambiguated).
  5. ``vanished[]`` populated with
     {workspace, project, path, project_identity, remote, missing_since}.
  6. ``orphaned_autored`` detects a non-empty ``description`` on a vanished row
     and reports the workspace memory/brief counts.
  7. ``diff`` carries ``would_*`` on dry-run vs ``did_*`` on apply, and ``mode``
     is ``"dry-run"`` vs ``"apply"``.

Test isolation:
  * GAIA_DATA_DIR is redirected to a tmp dir per test (tmp_db fixture) and the
    explicit db_path is threaded through every scan call, so the real
    ~/.gaia/gaia.db is never touched.
  * Repos are created with ``git init`` + an ``origin`` remote so the scan's
    ``_git_remote_origin`` sees a real remote (the SV2 base signal). Direct DB
    seeding (raw INSERT) is used to place rows in OTHER workspaces without a
    second physical repo tree.
  * No pipes, no ``python3 -c``, no ``python3 bin/gaia`` -- pytest drives the
    classifier API directly.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tools.scan import classify as classify_mod


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to a temp dir and return the isolated db path.

    The file is NOT created here (db_path() only resolves the path); the first
    write -- or an explicit seed -- materializes it.
    """
    db_dir = tmp_path / "gaia_data"
    db_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(db_dir))
    from gaia.paths import db_path
    return db_path()


def _init_git_repo(path: Path, remote: str | None = None) -> Path:
    """Create a real git repo at ``path`` with an optional ``origin`` remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(path), check=True,
        )
    return path


def _seed_workspace(db_path: Path, workspace: str) -> None:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(name) DO NOTHING",
            (workspace, workspace, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _seed_project(
    db_path: Path,
    workspace: str,
    name: str,
    *,
    remote_url: str | None = None,
    project_identity: str | None = None,
    status: str = "active",
    path: str = "/seeded/path",
    description: str | None = None,
) -> None:
    """Directly insert a projects row (simulating a prior scan elsewhere)."""
    _seed_workspace(db_path, workspace)
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO projects "
            "(workspace, name, remote_url, project_identity, status, path, "
            " description, scanner_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace, name) DO UPDATE SET "
            "remote_url=excluded.remote_url, "
            "project_identity=excluded.project_identity, "
            "status=excluded.status, path=excluded.path, "
            "description=excluded.description",
            (workspace, name, remote_url, project_identity, status, path,
             description, "2026-01-01T00:00:00Z"),
        )
        con.commit()
    finally:
        con.close()


def _get_project_column(db_path: Path, workspace: str, name: str, column: str):
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            f"SELECT {column} FROM projects WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _set_project_description(db_path: Path, workspace: str, name: str, desc: str) -> None:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "UPDATE projects SET description = ? WHERE workspace = ? AND name = ?",
            (desc, workspace, name),
        )
        con.commit()
    finally:
        con.close()


def _seed_memory(db_path: Path, workspace: str, name: str) -> None:
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO memory (workspace, name, type, body, class) "
            "VALUES (?, ?, 'decision', 'seeded body', 'anchor') "
            "ON CONFLICT(workspace, name) DO NOTHING",
            (workspace, name),
        )
        con.commit()
    finally:
        con.close()


def _seed_brief(db_path: Path, workspace: str, name: str) -> None:
    _seed_workspace(db_path, workspace)
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO briefs (workspace, name, status) "
            "VALUES (?, ?, 'open')",
            (workspace, name),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Case 1: remote_url is persisted on the CLI/classify path (apply=True)
# ---------------------------------------------------------------------------

class TestRemoteUrlPersisted:
    def test_remote_url_persisted_after_apply(self, tmp_db, tmp_path):
        remote = "https://github.com/org/proj1.git"
        _init_git_repo(tmp_path / "wsA" / "proj1", remote=remote)

        report = classify_mod.scan(
            tmp_path / "wsA", "wsA", db_path=tmp_db, apply=True,
        )
        assert report.error is None
        assert any(p["applied"] for p in report.projects)

        stored = _get_project_column(tmp_db, "wsA", "proj1", "remote_url")
        assert stored == remote, f"remote_url not persisted (got {stored!r})"

        # It is also surfaced in the per-project report entry.
        entry = next(p for p in report.projects if p["project"] == "proj1")
        assert entry["remote"] == remote


# ---------------------------------------------------------------------------
# Case 2: move_candidate 1:1 (remote matches exactly one row elsewhere)
# ---------------------------------------------------------------------------

class TestMoveCandidate1to1:
    def test_appeared_here_matches_single_missing_elsewhere(self, tmp_db, tmp_path):
        remote = "https://github.com/org/moved.git"
        # Seed a MISSING row for the same remote in a DIFFERENT workspace,
        # with a fake identity so upsert-collapse does not touch it.
        _seed_project(
            tmp_db, "old-ws", "moved-proj",
            remote_url=remote, project_identity="old/location/.git",
            status="missing", path="/old/location/moved-proj",
        )

        # The repo re-appears in a NEW workspace with the same remote.
        _init_git_repo(tmp_path / "new-ws" / "moved-proj", remote=remote)

        report = classify_mod.scan(
            tmp_path / "new-ws", "new-ws", db_path=tmp_db, apply=True,
        )
        assert report.error is None

        assert len(report.move_candidates) == 1, report.move_candidates
        mc = report.move_candidates[0]
        assert mc["from"]["workspace"] == "old-ws"
        assert mc["from"]["project"] == "moved-proj"
        assert mc["to"]["workspace"] == "new-ws"
        assert mc["to"]["project"] == "moved-proj"
        assert mc["signal"] == "remote"
        assert mc["remote"] == "github.com/org/moved"
        assert mc["confidence"] == "high"

    def test_match_survives_remote_url_normalization(self, tmp_db, tmp_path):
        # Seeded SSH form vs scanned HTTPS form -> must normalize to one match.
        _seed_project(
            tmp_db, "old-ws", "moved-proj",
            remote_url="git@github.com:Org/Moved.git",
            project_identity="old/location/.git",
            status="missing", path="/old/location/moved-proj",
        )
        _init_git_repo(
            tmp_path / "new-ws" / "moved-proj",
            remote="https://github.com/org/moved.git",
        )

        report = classify_mod.scan(
            tmp_path / "new-ws", "new-ws", db_path=tmp_db, apply=True,
        )
        assert len(report.move_candidates) == 1, report.move_candidates
        assert report.move_candidates[0]["remote"] == "github.com/org/moved"


# ---------------------------------------------------------------------------
# Case 3: two live clones of the same remote -> NO move_candidate
# ---------------------------------------------------------------------------

class TestTwoClonesNoCandidate:
    def test_two_active_clones_are_ambiguous_not_a_move(self, tmp_db, tmp_path):
        shared_remote = "https://github.com/org/shared.git"

        # Two ACTIVE rows in two OTHER workspaces share the same remote.
        _seed_project(
            tmp_db, "ws-a", "clone-a",
            remote_url=shared_remote, project_identity="a/.git",
            status="active", path="/a/clone-a",
        )
        _seed_project(
            tmp_db, "ws-b", "clone-b",
            remote_url=shared_remote, project_identity="b/.git",
            status="active", path="/b/clone-b",
        )

        # ws-main has a keeper (survives) plus a doomed repo carrying the
        # shared remote. First scan writes both active.
        _init_git_repo(
            tmp_path / "ws-main" / "keeper",
            remote="https://github.com/org/keeper.git",
        )
        doomed = _init_git_repo(
            tmp_path / "ws-main" / "doomed", remote=shared_remote,
        )
        classify_mod.scan(tmp_path / "ws-main", "ws-main", db_path=tmp_db, apply=True)

        # Now the doomed repo vanishes from disk (keeper remains so the scan
        # still finds a repo under the root).
        import shutil
        shutil.rmtree(doomed)

        report = classify_mod.scan(
            tmp_path / "ws-main", "ws-main", db_path=tmp_db, apply=True,
        )
        assert report.error is None

        # 'doomed' vanished, and its remote matches TWO active clones -> the
        # pairing is ambiguous, so NO move_candidate is emitted.
        vanished_names = {v["project"] for v in report.vanished}
        assert "doomed" in vanished_names
        assert report.move_candidates == [], (
            "two live clones of the same remote must NOT produce a move "
            f"candidate; got {report.move_candidates}"
        )


# ---------------------------------------------------------------------------
# Case 4: rename_candidate (folder basename != persisted name)
# ---------------------------------------------------------------------------

class TestRenameCandidate:
    def test_collision_disambiguation_flags_rename(self, tmp_db, tmp_path):
        # A DIFFERENT physical repo already occupies (ws, "app").
        _seed_project(
            tmp_db, "ws", "app",
            remote_url="https://github.com/org/other-app.git",
            project_identity="preexisting/other/.git",
            status="active", path="/somewhere/else/app",
        )

        # A new repo whose folder basename is ALSO "app" is scanned. It
        # collapses (R4: directly under the workspace), so project==repo=="app",
        # but the slot is taken -> the writer disambiguates to "app-2".
        _init_git_repo(
            tmp_path / "ws" / "app",
            remote="https://github.com/org/real-app.git",
        )

        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        assert report.error is None

        assert len(report.rename_candidates) == 1, report.rename_candidates
        rc = report.rename_candidates[0]
        assert rc["workspace"] == "ws"
        assert rc["project"] == "app-2"      # persisted (disambiguated) name
        assert rc["expected_name"] == "app"  # folder basename on disk
        assert rc["repo"] == "app"


# ---------------------------------------------------------------------------
# Case 5: vanished[] populated with the full field set
# ---------------------------------------------------------------------------

class TestVanishedPopulated:
    def test_vanished_row_carries_all_fields(self, tmp_db, tmp_path):
        remote = "https://github.com/org/doomed.git"
        _init_git_repo(
            tmp_path / "ws" / "keeper",
            remote="https://github.com/org/keeper.git",
        )
        doomed = _init_git_repo(tmp_path / "ws" / "doomed", remote=remote)

        classify_mod.scan(tmp_path / "ws", "ws", db_path=tmp_db, apply=True)

        import shutil
        shutil.rmtree(doomed)

        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        assert report.error is None

        vanished = [v for v in report.vanished if v["project"] == "doomed"]
        assert len(vanished) == 1, report.vanished
        v = vanished[0]
        # Full field set required by SV2.
        assert set(v.keys()) >= {
            "workspace", "project", "path", "project_identity",
            "remote", "missing_since",
        }
        assert v["workspace"] == "ws"
        assert v["remote"] == remote
        assert v["project_identity"], "project_identity must be present"
        # On apply, missing_since is the persisted soft-delete timestamp.
        assert v["missing_since"], "missing_since must be set on apply"
        # And the row is soft-deleted, not removed.
        assert _get_project_column(tmp_db, "ws", "doomed", "status") == "missing"

    def test_vanished_missing_since_none_on_dry_run(self, tmp_db, tmp_path):
        _init_git_repo(
            tmp_path / "ws" / "keeper",
            remote="https://github.com/org/keeper.git",
        )
        doomed = _init_git_repo(
            tmp_path / "ws" / "doomed",
            remote="https://github.com/org/doomed.git",
        )
        classify_mod.scan(tmp_path / "ws", "ws", db_path=tmp_db, apply=True)

        import shutil
        shutil.rmtree(doomed)

        # Dry-run: vanished is previewed, but nothing is written -> missing_since
        # is None and the row stays active on disk-of-record.
        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=False,
        )
        v = next(v for v in report.vanished if v["project"] == "doomed")
        assert v["missing_since"] is None
        assert _get_project_column(tmp_db, "ws", "doomed", "status") == "active", (
            "dry-run must not soft-delete the row"
        )


# ---------------------------------------------------------------------------
# Case 6: orphaned_autored (description on a vanished row + memory/brief counts)
# ---------------------------------------------------------------------------

class TestOrphanedAutored:
    def test_detects_description_and_counts_context(self, tmp_db, tmp_path):
        _init_git_repo(
            tmp_path / "ws" / "keeper",
            remote="https://github.com/org/keeper.git",
        )
        doomed = _init_git_repo(
            tmp_path / "ws" / "doomed",
            remote="https://github.com/org/doomed.git",
        )
        classify_mod.scan(tmp_path / "ws", "ws", db_path=tmp_db, apply=True)

        # An agent authored a description on the project (scan never writes it).
        _set_project_description(tmp_db, "ws", "doomed", "the payments engine")
        # The workspace still holds authored context.
        _seed_memory(tmp_db, "ws", "decision_payments")
        _seed_brief(tmp_db, "ws", "payments-refactor")

        import shutil
        shutil.rmtree(doomed)

        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        assert report.error is None

        orphaned = [o for o in report.orphaned_autored if o["project"] == "doomed"]
        assert len(orphaned) == 1, report.orphaned_autored
        o = orphaned[0]
        assert o["workspace"] == "ws"
        assert o["description"] == "the payments engine"
        assert o["memory_count"] >= 1, "workspace memory notes must be counted"
        assert o["brief_count"] >= 1, "workspace open briefs must be counted"

    def test_vanished_without_description_is_not_orphaned(self, tmp_db, tmp_path):
        _init_git_repo(
            tmp_path / "ws" / "keeper",
            remote="https://github.com/org/keeper.git",
        )
        doomed = _init_git_repo(
            tmp_path / "ws" / "doomed",
            remote="https://github.com/org/doomed.git",
        )
        classify_mod.scan(tmp_path / "ws", "ws", db_path=tmp_db, apply=True)

        import shutil
        shutil.rmtree(doomed)

        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        # 'doomed' vanished but carried no authored description.
        assert any(v["project"] == "doomed" for v in report.vanished)
        assert report.orphaned_autored == [], (
            "a vanished project without a description must not be reported as "
            f"orphaned; got {report.orphaned_autored}"
        )


# ---------------------------------------------------------------------------
# Case 7: diff block + mode (dry-run vs apply)
# ---------------------------------------------------------------------------

class TestDiffAndMode:
    def test_dry_run_diff_uses_would_keys(self, tmp_db, tmp_path):
        _init_git_repo(
            tmp_path / "ws" / "proj1",
            remote="https://github.com/org/proj1.git",
        )
        report = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=False,
        )
        assert report.mode == "dry-run"
        assert set(report.diff.keys()) == {
            "would_create", "would_update", "would_move", "would_mark_missing",
        }
        assert report.diff["would_create"] == 1
        assert report.diff["would_update"] == 0

        payload = report.to_dict()
        assert payload["mode"] == "dry-run"
        assert payload["diff"]["would_create"] == 1

    def test_apply_diff_uses_did_keys_and_update_on_rescan(self, tmp_db, tmp_path):
        _init_git_repo(
            tmp_path / "ws" / "proj1",
            remote="https://github.com/org/proj1.git",
        )

        first = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        assert first.mode == "apply"
        assert set(first.diff.keys()) == {
            "did_create", "did_update", "did_move", "did_mark_missing",
        }
        assert first.diff["did_create"] == 1
        assert first.diff["did_update"] == 0

        # Re-scan: the identity now exists -> it is an UPDATE, not a create.
        second = classify_mod.scan(
            tmp_path / "ws", "ws", db_path=tmp_db, apply=True,
        )
        assert second.diff["did_create"] == 0
        assert second.diff["did_update"] == 1
        assert second.diff["did_mark_missing"] == 0
