"""The plugin's own compaction tool crosses tool.execute.before like any tool; the policy decides what that costs.

The bridge runs in process against a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

MAIN_SESSION = "ses-main-compact-tool"


@pytest.fixture
def isolated(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.setenv("GAIA_HOST", "opencode")
    monkeypatch.chdir(tmp_path)
    return db


def _call(tool: str) -> dict:
    import bridge

    return bridge.handle({
        "event": "tool.execute.before",
        "sessionID": MAIN_SESSION,
        "callID": "call-compact",
        "tool": tool,
        "args": {},
    })


def test_the_plugins_compaction_tool_is_allowed_for_the_main_session(isolated):
    response = _call("gaia_compact_when_idle")

    assert response["action"] == "allow", response


def test_the_plugin_reads_an_unseen_sessions_agent_back_with_the_hosts_v1_request_shape():
    result = subprocess.run(
        ["bun", "test", str(_ROOT / "tests/opencode/host_agent_lookup.test.ts")],
        cwd=_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_allowing_the_compaction_tool_does_not_open_other_unknown_tools(isolated):
    assert _call("some_foreign_tool")["action"] == "deny"
    assert _call("gaia_compact_when_idle_clone")["action"] == "deny"
