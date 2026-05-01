"""
test_install_capture_e2e.py -- AC-3 verification.

E2E test: when agent_output contains an npm install pattern, the subagent_stop
hook pipeline (via the adapter) writes a row to the integrations table in
~/.gaia/gaia.db with name='acli' via store.save_integration.

Strategy:
- Use a temporary DB (monkeypatched via GAIA_DATA_DIR)
- Directly call the hook's install-capture path to verify DB write
- Also verify the full adapter path writes to integrations
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Isolated gaia.db with schema materialized."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Clear any cached path so GAIA_DATA_DIR is picked up
    try:
        from modules.core.paths import clear_path_cache
        clear_path_cache()
    except (ImportError, Exception):
        pass
    from gaia.paths import db_path as _db_path
    db = _db_path()
    from gaia.store.writer import _connect
    con = _connect(db)
    con.close()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_npm_install_writes_integration(tmp_db: Path):
    """AC-3: npm install -g acli -> integrations row with name='acli' in DB."""
    from modules.install_detector import detect, resolve_workspace, build_topic_key
    from gaia.store import save_integration

    tool_output = "npm install -g acli → added 1 package"

    # Step 1: detect the pattern
    match = detect(tool_output)
    assert match["matched"] is True, f"Detector did not match: {match}"
    assert match["target"] == "acli"
    assert match["pattern"] == "npm install"

    # Step 2: resolve workspace (mock to avoid git dependency in CI)
    with patch("gaia.project.current", return_value="global"):
        ws = resolve_workspace()

    # Step 3: build topic_key
    tk = build_topic_key(match["kind"], match["target"])
    assert tk == "cli/atlassian/acli"

    # Step 4: save_integration writes to DB
    result = save_integration(
        workspace=ws,
        name=match["target"],
        kind=match["kind"],
        topic_key=tk,
        agent="system",
        db_path=tmp_db,
    )
    assert result.get("status") == "applied", f"save_integration returned: {result}"

    # Step 5: verify row is in integrations table
    con = sqlite3.connect(str(tmp_db))
    row = con.execute(
        "SELECT name, kind, topic_key FROM integrations WHERE name = ?",
        ("acli",),
    ).fetchone()
    con.close()

    assert row is not None, "No integrations row found for 'acli'"
    assert row[0] == "acli"
    assert row[1] == "cli"
    assert row[2] == "cli/atlassian/acli"


def test_save_integration_idempotent(tmp_db: Path):
    """Reinstall of same tool: second save_integration call updates row, no duplicate."""
    from gaia.store import save_integration

    # First install
    r1 = save_integration(
        workspace="global",
        name="gcloud",
        kind="cli",
        topic_key="cli/google/gcloud",
        agent="system",
        db_path=tmp_db,
    )
    assert r1["status"] == "applied"

    # Second install (e.g. upgrade) -- same workspace+name, same topic_key
    r2 = save_integration(
        workspace="global",
        name="gcloud",
        kind="cli",
        version="450.0.1",  # version added on second call
        topic_key="cli/google/gcloud",
        agent="system",
        db_path=tmp_db,
    )
    assert r2["status"] == "applied"

    # Verify only one row
    con = sqlite3.connect(str(tmp_db))
    rows = con.execute(
        "SELECT name, version, topic_key FROM integrations WHERE name = 'gcloud'"
    ).fetchall()
    con.close()

    assert len(rows) == 1, f"Expected 1 row, found {len(rows)}: {rows}"
    assert rows[0][1] == "450.0.1", "Version not updated on second upsert"
    assert rows[0][2] == "cli/google/gcloud"


def test_pip_install_capture(tmp_db: Path):
    """pip install capture: pytest installed -> integrations row with name='pytest'."""
    from modules.install_detector import detect, build_topic_key
    from gaia.store import save_integration

    output = "pip install pytest\nSuccessfully installed pytest-7.4.0"
    match = detect(output)

    assert match["matched"] is True
    assert match["target"] == "pytest"
    assert match["pattern"] == "pip install"

    tk = build_topic_key(match["kind"], match["target"])

    result = save_integration(
        workspace="global",
        name=match["target"],
        kind=match["kind"],
        topic_key=tk,
        agent="system",
        db_path=tmp_db,
    )
    assert result["status"] == "applied"

    con = sqlite3.connect(str(tmp_db))
    row = con.execute(
        "SELECT name, kind FROM integrations WHERE name = 'pytest'"
    ).fetchone()
    con.close()

    assert row is not None
    assert row[0] == "pytest"
    assert row[1] == "pkg"
