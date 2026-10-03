"""
One data home: logs, session state and the init marker resolve under the Gaia
home (GAIA_DATA_DIR) whatever CLAUDE_PLUGIN_DATA a channel launches with, so a
channel switch keeps the session and no first-install welcome is emitted.

HOME, GAIA_DATA_DIR, GAIA_DB and every CLAUDE_PLUGIN_DATA live under tmp_path;
the only hook run is UserPromptSubmit, as a subprocess against a temporary
workspace.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "hooks"))

from modules.core import paths, plugin_setup  # noqa: E402
from modules.session.session_context_writer import SessionContextWriter  # noqa: E402

USER_PROMPT_SUBMIT = REPO / "hooks" / "user_prompt_submit.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    user_home.mkdir()
    data = tmp_path / "gaia-data"
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(data / "gaia.db"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.chdir(workspace)
    paths.clear_path_cache()
    yield {"tmp": tmp_path, "data": data.resolve(), "home": user_home, "ws": workspace}
    paths.clear_path_cache()


def _launch(monkeypatch, channel_dir: Path) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(channel_dir))
    paths.clear_path_cache()


def _data_paths() -> dict:
    return {
        "logs": paths.get_logs_dir().resolve(),
        "session": paths.get_session_dir().resolve(),
        "marker": plugin_setup.marker_path().resolve(),
    }


def test_two_channels_resolve_the_same_home(home, monkeypatch):
    _launch(monkeypatch, home["tmp"] / "A")
    under_a = _data_paths()
    _launch(monkeypatch, home["tmp"] / "B")
    under_b = _data_paths()

    assert under_a == under_b
    for path in under_b.values():
        assert home["data"] in path.parents


def test_session_state_written_under_one_channel_is_read_under_another(home, monkeypatch):
    _launch(monkeypatch, home["tmp"] / "A")
    SessionContextWriter().update_context({"event_type": "probe", "detail": "written under A"})

    _launch(monkeypatch, home["tmp"] / "B")
    context = json.loads((paths.get_session_dir() / "context.json").read_text())

    assert [e["detail"] for e in context["critical_events"]] == ["written under A"]


def test_first_prompt_under_a_new_channel_has_no_welcome(home):
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_DATA": str(home["tmp"] / "B"),
        "PYTHONPATH": str(REPO),
    }
    for name in ("CLAUDE_PLUGIN_ROOT", "INIT_CWD", "CLAUDE_PROJECT_DIR"):
        env.pop(name, None)
    payload = {"session_id": "single-home", "hook_event_name": "UserPromptSubmit", "prompt": "hello"}

    proc = subprocess.run(
        [sys.executable, str(USER_PROMPT_SUBMIT)], input=json.dumps(payload), env=env,
        cwd=home["ws"], capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "installed for the first time" not in context
    assert not (home["tmp"] / "B" / ".plugin-initialized").exists()


def test_session_start_marks_the_home_and_reports_old_channel_data_once(home, monkeypatch):
    old_channel = home["home"] / ".claude" / "plugins" / "data" / "gaia-gaia-marketplace"
    (old_channel / "logs").mkdir(parents=True)
    (old_channel / "logs" / "audit.jsonl").write_text("{}\n")
    _launch(monkeypatch, home["tmp"] / "B")

    first = plugin_setup.mark_data_home()
    second = plugin_setup.mark_data_home()

    assert plugin_setup.marker_path().is_file()
    assert str(old_channel) in first
    assert (old_channel / "logs" / "audit.jsonl").read_text() == "{}\n"
    assert second == ""
