"""What memory each context carries: the birth indexes the user's facts and preferences, the kernel the scope chain's rows and none of the user's.

Runs in-process against the temporary HOME and GAIA_DATA_DIR the suite's
conftest provides.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store.writer import USER_WORKSPACE, _connect, upsert_memory  # noqa: E402
from modules.context.kernel_builder import build_memory_block  # noqa: E402
from modules.context.user_sections import render_scope_section, render_user_sections  # noqa: E402
from modules.session.session_manifest import build_due_section, build_session_context  # noqa: E402


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _seed_project() -> None:
    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name, root_path, status) VALUES ('me', '/tmp/me', 'active')")
    con.execute(
        "INSERT OR IGNORE INTO projects (workspace, name, path, status, group_name, project_identity) "
        "VALUES ('me', 'gaia', '/tmp/me/gaia', 'active', 'tools', 'github.com/metraton/gaia')"
    )
    con.commit()
    con.close()


def _seed_rows() -> None:
    upsert_memory(USER_WORKSPACE, "user_pref_pr_headings", type="user", body="why", kind="preference",
                  description="Preference: PRs: seven headings, English.")
    upsert_memory(USER_WORKSPACE, "user_fact_team", type="user", body="why", kind="fact",
                  description="Fact: Jorge leads an offshore team.")
    upsert_memory(USER_WORKSPACE, "user_pref_expired", type="user", body="why", kind="preference",
                  description="Preference: Delivery: ship on Fridays.", until="2020-01-01")
    upsert_memory(USER_WORKSPACE, "user_pref_log_only", type="user", body="why", kind="preference",
                  description="Preference: an old note.", class_="log")
    upsert_memory("me", "decision_me_ws_rule", type="decision", body="why", kind="preference",
                  description="Preference: Reports: UTC only.", class_="anchor")
    upsert_memory("me", "decision_tools_group", type="decision", body="why", kind="fact",
                  description="Fact: the tools group deploys with Flux.", class_="anchor", initiative="tools")
    upsert_memory("me", "negative_gaia_retries", type="negative", body="why", kind="lesson",
                  description="Lesson: retries hide the fault.", class_="anchor", project_ref="github.com/metraton/gaia")
    upsert_memory("me", "project_gaia_pending", type="project", body="why", kind="pending",
                  description="Pending: close the probe.", class_="anchor", project_ref="github.com/metraton/gaia")


def test_birth_indexes_the_users_live_facts_and_preferences_by_kind():
    _seed_project()
    _seed_rows()
    birth = build_session_context()
    section = birth[birth.index("## User memory"):].split("\n\n")[0]
    assert section.splitlines()[:5] == [
        "## User memory",
        "kind=preference",
        '  "PRs: seven headings, English."  ref=memory:user/user_pref_pr_headings',
        "kind=fact",
        '  "Jorge leads an offshore team."  ref=memory:user/user_fact_team',
    ]
    assert "why" not in section
    assert "ship on Fridays" not in birth
    assert "an old note" not in birth
    assert "UTC only" not in birth
    assert "## The user" not in birth and "## User preferences" not in birth


def test_kernel_carries_the_scope_chain_and_none_of_the_users_rows():
    _seed_project()
    _seed_rows()
    kernel = build_memory_block({"workspace": "me", "dispatch_project": "gaia"})
    assert kernel.splitlines() == [
        "# Scope memory: me > tools > gaia",
        "kind=preference",
        '  "Reports: UTC only."  scope=workspace  ref=memory:me/decision_me_ws_rule',
        "kind=fact",
        '  "the tools group deploys with Flux."  scope=group  ref=memory:me/decision_tools_group',
        "kind=lesson",
        '  "retries hide the fault."  scope=project  ref=memory:me/negative_gaia_retries',
    ]
    assert "memory:user/" not in kernel
    assert "close the probe" not in kernel


def test_kernel_for_another_project_holds_only_the_workspace_rows():
    _seed_project()
    _seed_rows()
    kernel = build_memory_block({"workspace": "me", "dispatch_project": "other"})
    assert "scope=workspace" in kernel
    assert "scope=group" not in kernel and "scope=project" not in kernel


def test_a_preference_held_by_a_defect_leaves_when_the_defect_closes():
    _seed_project()
    upsert_memory("me", "feedback_guard_overblocks", type="feedback", body="why", initiative="gaia_system")
    upsert_memory(USER_WORKSPACE, "user_pref_workaround", type="user", body="why", kind="preference",
                  description="Preference: Tools: pass --no-verify.", until="memory:gaia/feedback_guard_overblocks")
    assert "until=memory:gaia/feedback_guard_overblocks" in build_session_context()

    from gaia.store.writer import delete_memory, HOST_WORKSPACE

    delete_memory(HOST_WORKSPACE, "feedback_guard_overblocks")
    assert "pass --no-verify" not in build_session_context()


def test_renderers_print_values_only():
    rows = [{"workspace": USER_WORKSPACE, "name": "a", "kind": "preference",
             "description": "Preference: PRs: English.", "until": "2030-01-01"}]
    assert render_user_sections(rows) == (
        '## User memory\nkind=preference\n  "PRs: English."  until=2030-01-01  ref=memory:user/a'
    )
    assert render_scope_section("me > gaia", [{**rows[0], "workspace": "me", "scope": "project"}]) == (
        '# Scope memory: me > gaia\nkind=preference\n  "PRs: English."  until=2030-01-01  scope=project  ref=memory:me/a'
    )
    assert render_user_sections([]) == ""


def test_due_section_lists_overdue_items_as_values(monkeypatch):
    from modules.session import session_manifest

    monkeypatch.setattr(session_manifest, "_read_workspace_identity", lambda: "me")
    _seed_project()
    con = _connect()
    cur = con.execute(
        "INSERT INTO task_notifications (workspace, task_name, headline, kind, due_at) "
        "VALUES ('me', 'reminder', 'Call the bank', 'reminder', '2020-01-02T15:00:00Z')"
    )
    con.commit()
    row_id = cur.lastrowid
    con.close()
    section = build_due_section("me")
    lines = section.splitlines()
    assert lines[0:2] == ["## Due", "overdue=1"]
    assert lines[2].startswith("'Call the bank'  due=2020-01-02T15:00Z  late=")
    assert lines[2].endswith(f"id={row_id}")
    birth = build_session_context()
    assert "## Due" in birth and "Recurring work pending" not in birth
