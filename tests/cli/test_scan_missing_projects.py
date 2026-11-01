"""``gaia scan`` of a whole workspace marks a vanished repo missing, even its last one."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tools.scan import classify as classify_mod  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


def _status(db: Path, workspace: str) -> dict:
    con = sqlite3.connect(str(db))
    try:
        return dict(con.execute(
            "SELECT name, status FROM projects WHERE workspace = ?", (workspace,)))
    finally:
        con.close()


def test_scan_marks_the_workspaces_last_vanished_repo_missing(tmp_db, tmp_path):
    from gaia.store.writer import declare_workspace
    root = tmp_path / "century"
    for repo in ("kinetlab01-iac", "branchkinect-iac"):
        (root / repo / ".git").mkdir(parents=True)
    declare_workspace("century", root, db_path=tmp_db)
    assert classify_mod.scan(root, "century", db_path=tmp_db, apply=True).error is None

    shutil.rmtree(root / "kinetlab01-iac")
    assert classify_mod.scan(root, "century", db_path=tmp_db, apply=True).marked_missing == 1
    again = classify_mod.scan(root, "century", db_path=tmp_db, apply=False)
    assert (again.diff["would_mark_missing"], again.already_missing) == (0, 1)

    shutil.rmtree(root / "branchkinect-iac")
    preview = classify_mod.scan(root, "century", db_path=tmp_db, apply=False)
    assert preview.error is None
    assert preview.diff["would_mark_missing"] == 1
    applied = classify_mod.scan(root, "century", db_path=tmp_db, apply=True)
    assert applied.marked_missing == 1
    assert _status(tmp_db, "century") == {"kinetlab01-iac": "missing",
                                          "branchkinect-iac": "missing"}

    shutil.rmtree(root)
    root.mkdir()
    assert "no git repos" in classify_mod.scan(root, "century", db_path=tmp_db, apply=False).error
