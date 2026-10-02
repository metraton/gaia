#!/usr/bin/env python3
"""The host-neutral session lifecycle runs on the inputs a host hands it.

Every side effect is stubbed at its source module, so these tests pin what the
core decides -- order, arguments, branch -- from explicit inputs alone, with
Claude Code's own environment variables set to contradict them.
"""

import contextlib
import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_lifecycle
from modules.session.session_lifecycle import SessionStart


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Stub every start dependency and return the ordered list of calls made."""
    made = []

    def stub(target, name, result=None):
        def recorder(*args, **kwargs):
            made.append((name, args, kwargs))
            return result
        monkeypatch.setattr(target + "." + name, recorder)

    stub("modules.session.session_registry", "register_session")
    stub("modules.session.session_registry", "cleanup_stale_entries", 0)
    stub("modules.security.approval_grants", "cleanup_expired_grants", 0)
    stub("modules.security.approval_cleanup", "count_stale_db_pendings", 0)
    stub("modules.session.db_backup", "maybe_backup_db")
    stub("modules.session.contract_drafts_gc", "gc_contract_drafts", 0)
    stub("gaia.retention.worktree_collector", "sweep_repo_worktrees", [])
    stub("modules.core.plugin_setup", "run_first_time_setup", "setup done")
    stub("modules.core.plugin_setup", "mark_data_home", "home moved")
    stub("gaia.install_root", "installed_root", tmp_path / "root")
    stub("gaia.install_root", "workspace_status", {"action": "noop", "details": ""})
    stub("modules.session.plugin_upgrade", "reconcile_plugin_install", "")
    monkeypatch.setattr("modules.core.plugin_setup.recorded_in_manifest", contextlib.nullcontext)
    stub("modules.session.session_manifest", "build_session_context", "BIRTH")
    stub("modules.context.compact_context_builder", "build_compact_context", "REFRESH")

    monkeypatch.setenv("CLAUDE_SESSION_ID", "from-the-environment")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
    return made


def _start(tmp_path, **overrides):
    fields = dict(
        session_id="explicit-sid",
        source="startup",
        is_headless=True,
        pinned_build={"hooks_path": "/h", "hooks_hash": "abc"},
        workspace_dir=tmp_path / "ws",
    )
    fields.update(overrides)
    return SessionStart(**fields)


def _names(calls):
    return [name for name, _, _ in calls]


def test_start_maintenance_runs_in_order_on_the_explicit_inputs(calls, tmp_path):
    session_lifecycle.start_session(_start(tmp_path))

    assert _names(calls) == [
        "register_session",
        "cleanup_stale_entries",
        "cleanup_expired_grants",
        "count_stale_db_pendings",
        "maybe_backup_db",
        "gc_contract_drafts",
        "sweep_repo_worktrees",
        "run_first_time_setup",
        "mark_data_home",
        "installed_root",
        "reconcile_plugin_install",
        "workspace_status",
        "build_session_context",
    ]
    by_name = {name: (args, kwargs) for name, args, kwargs in calls}
    assert by_name["cleanup_expired_grants"] == ((), {"force": True})
    assert by_name["sweep_repo_worktrees"] == ((tmp_path / "ws",), {})
    assert by_name["reconcile_plugin_install"] == ((tmp_path / "root",), {})


def test_registration_uses_the_given_session_and_stores_no_identity(calls, tmp_path):
    session_lifecycle.start_session(_start(tmp_path))

    register = [kwargs for name, _, kwargs in calls if name == "register_session"]
    assert register == [{
        "session_id": "explicit-sid",
        "is_headless": True,
        "pinned_build": {"hooks_path": "/h", "hooks_hash": "abc"},
    }]


def test_the_default_session_is_never_registered(calls, tmp_path):
    session_lifecycle.start_session(_start(tmp_path, session_id="default"))

    assert "register_session" not in _names(calls)


def test_birth_builds_the_manifest_with_the_notices_as_alarms(calls, tmp_path):
    outcome = session_lifecycle.start_session(_start(tmp_path))

    birth = [kwargs for name, _, kwargs in calls if name == "build_session_context"]
    assert birth == [{"alarms": ["## Data home\nhome moved"], "record_injection": True}]
    assert outcome.context == "BIRTH"
    assert outcome.notices == {"## Data home": "home moved"}
    assert outcome.setup_message == "setup done"


def test_compaction_takes_the_refresh_branch_instead_of_the_birth_block(calls, tmp_path):
    outcome = session_lifecycle.start_session(_start(tmp_path, source="compact"))

    assert "build_session_context" not in _names(calls)
    assert "build_compact_context" in _names(calls)
    assert outcome.context == "## Data home\nhome moved\n\nREFRESH"


def test_an_undeclared_root_becomes_a_workspace_alarm(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gaia.install_root.workspace_status",
        lambda root: {"action": "skipped", "details": f"{root} is not inside a declared workspace."},
    )

    outcome = session_lifecycle.start_session(_start(tmp_path))

    assert outcome.notices["## Workspace"] == f"{tmp_path / 'root'} is not inside a declared workspace."


def test_a_prompt_beats_the_given_session_then_counts_the_given_workspace(monkeypatch):
    made = []
    monkeypatch.setattr(
        "modules.session.session_registry.touch_session",
        lambda sid: made.append(("touch", sid)),
    )

    def count(ws):
        made.append(("count", ws))
        return 2
    monkeypatch.setattr("gaia.store.reader.count_unread_notifications", count)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "from-the-environment")

    context = session_lifecycle.prompt_context("explicit-sid", "me")

    assert made == [("touch", "explicit-sid"), ("count", "me")]
    assert context == (
        "\U0001F514 2 notifications due (`gaia notifications list --unread` to see them)"
    )


def test_a_prompt_with_nothing_due_adds_nothing(monkeypatch):
    monkeypatch.setattr("modules.session.session_registry.touch_session", lambda sid: None)
    monkeypatch.setattr("gaia.store.reader.count_unread_notifications", lambda ws: 0)

    assert session_lifecycle.prompt_context("explicit-sid", None) == ""


def test_the_end_unregisters_the_given_session_and_survives_a_registry_error(monkeypatch):
    from modules.session import session_registry

    ended = []
    monkeypatch.setattr(
        session_registry, "unregister_session", lambda session_id: ended.append(session_id)
    )
    session_lifecycle.end_session("explicit-sid")
    session_lifecycle.end_session("")
    assert ended == ["explicit-sid"]

    def boom(session_id):
        raise session_registry.SessionRegistryError("simulated I/O")
    monkeypatch.setattr(session_registry, "unregister_session", boom)
    session_lifecycle.end_session("explicit-sid")
