"""End-to-end T2 test: live DB snapshot survives v3->v4 migration byte-identical.

Where the synthetic v3 fixture test in `test_schema_v4_memory_refactor.py`
proves the migration logic on a hand-rolled v3 DDL stub with 36 dummy rows,
this test goes one step further: it snapshots the *actual* live Gaia DB
(`~/.gaia/gaia.db`) and runs the same bootstrap path against the snapshot.
The point is to defend AC-2 ("real production data survives") with a
fixture that carries the real production payload -- arbitrary body sizes,
varied origin_session_id values, NULL vs '' description distinctions,
mixed `type` distribution -- not a synthetic worst-case approximation.

Contract guarded:
  * row count unchanged (live_count -> live_count post-migration)
  * for every (workspace, name) row: workspace, name, type, description,
    body, origin_session_id, updated_at are byte-identical pre vs post
    (sha256 digest over the 7-tuple matches)
  * memory.class IS NULL on every row (new column lands NULL on legacy)
  * memory.status IS NULL on every row
  * memory_links table exists post-migration with expected indexes
  * schema_version advances to 4

The test is skipped (not failed) if:
  * ~/.gaia/gaia.db does not exist on this machine (CI sandbox, fresh
    checkout where the user never bootstrapped Gaia)
  * the live DB is not at v3 (already migrated, or on a future version)

A skip is the correct signal in those cases because the test exists to
guard the live preservation contract -- it has no contract to guard when
there is no v3 live DB to snapshot.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_LIVE_DB = Path.home() / ".gaia" / "gaia.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _digest_legacy_columns(rows: list[tuple]) -> str:
    """SHA-256 over the 7 v3 columns of every row, in their cursor order.

    Using repr() on each row captures NULL vs '' vs 0 distinctions that
    plain string concatenation would erase.
    """
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _read_live_memory_rows(db_path: Path) -> list[tuple]:
    """Read the 7 v3 columns from a DB, ordered by (workspace, name).

    Returns rows from ALL workspaces, not just `me`. The preservation
    contract is global: no workspace's rows may be mutated by the v3->v4
    migration. We then filter to 'me' for the row-count assertion.
    """
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT workspace, name, type, description, body, "
            "origin_session_id, updated_at "
            "FROM memory ORDER BY workspace, name"
        ).fetchall()
    finally:
        con.close()


def _run_bootstrap_against(db_path: Path, workspace: Path) -> subprocess.CompletedProcess:
    """Invoke bootstrap_database.sh against an arbitrary GAIA_DB path.

    The script accepts GAIA_DB and WORKSPACE via env; it picks up the
    migration files relative to its own location, so we don't have to
    stage anything else.
    """
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(workspace)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchemaV4LivePreservation:

    def setup_method(self):
        if not _BOOTSTRAP_SH.is_file():
            pytest.skip(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _LIVE_DB.is_file():
            pytest.skip(
                f"live Gaia DB not present at {_LIVE_DB} -- "
                "this test guards live preservation; skipping in environments "
                "without a bootstrapped install."
            )
        # Only meaningful when the live DB is still at v3 (pre-T9).
        con = sqlite3.connect(str(_LIVE_DB))
        try:
            ver = con.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version"
            ).fetchone()[0]
        finally:
            con.close()
        if ver != 3:
            pytest.skip(
                f"live Gaia DB is at schema_version={ver}, not v3; "
                "this T2 preservation test is meaningful only on v3 fixtures."
            )

    def test_live_snapshot_v3_to_v4_preserves_every_row(self):
        """Snapshot ~/.gaia/gaia.db, run bootstrap, assert every row survives."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            snapshot = workspace / "gaia_live_snapshot.db"

            # shutil.copy never touches the live DB beyond reading it.
            shutil.copy2(str(_LIVE_DB), str(snapshot))

            # Pre-migration capture.
            pre_rows = _read_live_memory_rows(snapshot)
            pre_count_global = len(pre_rows)
            pre_count_me = sum(1 for r in pre_rows if r[0] == "me")
            pre_digest = _digest_legacy_columns(pre_rows)

            con = sqlite3.connect(str(snapshot))
            try:
                pre_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(memory)")
                }
                pre_version = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
            finally:
                con.close()

            assert pre_version == 3
            assert "class" not in pre_cols
            assert "status" not in pre_cols
            assert pre_count_me >= 1, (
                "live DB has zero rows in workspace=me; nothing to preserve"
            )

            # Apply bootstrap (which runs migrations/v3_to_v4.sql).
            res = _run_bootstrap_against(snapshot, workspace)
            assert res.returncode == 0, (
                f"bootstrap failed against snapshot:\n"
                f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
            )

            # Post-migration capture.
            post_rows = _read_live_memory_rows(snapshot)
            post_count_global = len(post_rows)
            post_count_me = sum(1 for r in post_rows if r[0] == "me")
            post_digest = _digest_legacy_columns(post_rows)

            con = sqlite3.connect(str(snapshot))
            try:
                post_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(memory)")
                }
                post_version = con.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()[0]
                null_klass = con.execute(
                    "SELECT COUNT(*) FROM memory WHERE class IS NOT NULL"
                ).fetchone()[0]
                null_status = con.execute(
                    "SELECT COUNT(*) FROM memory WHERE status IS NOT NULL"
                ).fetchone()[0]
                memory_links_present = con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='memory_links'"
                ).fetchone()
                indexes = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
            finally:
                con.close()

            # Schema advanced.
            assert post_version == 4, (
                f"expected schema_version=4 post-bootstrap, got {post_version}"
            )
            # New columns added; old columns intact.
            assert {"workspace", "name", "type", "description", "body",
                    "origin_session_id", "updated_at", "class", "status"} \
                <= post_cols
            # Row count unchanged globally AND for workspace=me.
            assert post_count_global == pre_count_global, (
                f"global row count changed: {pre_count_global} -> {post_count_global}"
            )
            assert post_count_me == pre_count_me, (
                f"workspace=me row count changed: {pre_count_me} -> {post_count_me}"
            )
            # Legacy 7-column payload byte-identical across migration.
            assert post_digest == pre_digest, (
                "legacy memory column digest changed across v3->v4 migration; "
                "AC-2 preservation contract violated"
            )
            # New columns are NULL on every legacy row.
            assert null_klass == 0, (
                f"expected memory.class NULL on every legacy row, "
                f"got {null_klass} rows with non-NULL class"
            )
            assert null_status == 0, (
                f"expected memory.status NULL on every legacy row, "
                f"got {null_status} rows with non-NULL status"
            )
            # memory_links infrastructure in place.
            assert memory_links_present is not None, (
                "memory_links table missing after v3->v4 migration"
            )
            assert "idx_memory_class_status" in indexes
            assert "memory_links_src" in indexes
            assert "idx_memory_links_dst_kind" in indexes

    def test_live_db_was_not_mutated(self):
        """Sanity: the live DB on disk is untouched by this test run.

        We capture a sha256 of the live DB at the start of the test method
        and again at the end. They must match -- if shutil.copy2 or any
        downstream call leaks a write back to ~/.gaia/gaia.db, this fails.
        """
        def _file_digest(p: Path) -> str:
            h = hashlib.sha256()
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        before = _file_digest(_LIVE_DB)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            snapshot = workspace / "gaia_live_snapshot.db"
            shutil.copy2(str(_LIVE_DB), str(snapshot))
            res = _run_bootstrap_against(snapshot, workspace)
            assert res.returncode == 0, res.stderr

        after = _file_digest(_LIVE_DB)
        assert before == after, (
            "live ~/.gaia/gaia.db was mutated during the test; "
            "expected the snapshot path to be the only write target"
        )
