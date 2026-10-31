"""Every context Gaia injects at a session's or a subagent's birth stays under the host cap, against a live-sized corpus."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

HOST_CAP = 10_000
SESSION = "ses-budget"
USER_SCOPE = "_gaia_user"
UPGRADE = "Gaia migrated gaia.db from v60 to v61."
LEFTOVERS = "3 leftover worktrees, 12 merged branches and 40 scratch entries. " * 6
RESUME = ("Resume at the birth-block budget task; the next action is the red test run. " * 30)[:1990].strip()

ROWS = [
    (f"user_row_{i:02d}", "preference" if i % 3 else "fact", f"2026-09-01T10:{59 - i:02d}:00Z")
    for i in range(40)
]


def _title(i: int) -> str:
    return (f"standing rule {i} the user relies on in every session. " * 6).strip()


def _line(name: str, title: str) -> str:
    return f'  "{title}"  ref=memory:user/{name}'


@pytest.fixture
def corpus(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    root = tmp_path / "ws"
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
                    (USER_SCOPE, USER_SCOPE))
        titles = {}
        for i, (name, kind, updated) in enumerate(ROWS):
            titles[name] = _title(i)
            claim = "Preference" if kind == "preference" else "Fact"
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, "
                "updated_at) VALUES (?, ?, 'user', ?, 'the why behind the rule', 'anchor', ?, 'user', ?)",
                (USER_SCOPE, name, f"{claim}: {titles[name]}", kind, updated),
            )
        for ws, count in (("me", 9), ("client", 8), ("lab", 8)):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (ws, ws))
            payload = {}
            for n in range(count):
                name = f"{ws}-project-{n}"
                path = root / ws / name
                path.mkdir(parents=True)
                con.execute("INSERT INTO projects (workspace, name, path) VALUES (?, ?, ?)",
                            (ws, name, str(path)))
                payload[name] = {"name": name, "local_path": str(path),
                                 "description": f"The {name} repository, described in one long line."}
            con.execute(
                "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
                "VALUES (?, 'project_identity', ?)", (ws, json.dumps(payload)),
            )
        con.commit()
    finally:
        con.close()
    assert sum(len(_line(name, title)) for name, title in titles.items()) > 4_000
    monkeypatch.chdir(root / "me" / "me-project-0")

    from gaia import session_snapshot

    session_snapshot.write_resume_point(SESSION, RESUME)
    monkeypatch.setattr(session_snapshot, "_pending_signatures", lambda con, sid: [
        {"id": f"P-{n:032x}", "agent_id": f"a{n:016x}", "created_at": "2026-10-21T10:00:00Z"}
        for n in range(12)
    ])
    monkeypatch.setattr(session_snapshot, "_open_contracts", lambda con, sid: ([
        {"contract_id": f"a{n:016x}.{n:012x}", "agent_id": f"a{n:016x}",
         "agent_state": "NEEDS_INPUT", "kind": None, "plan_task_id": None}
        for n in range(12)
    ], []))
    return titles


@pytest.fixture
def maintenance(monkeypatch, tmp_path):
    """Stub every start side effect but the notices this corpus carries."""
    for target in (
        "modules.session.session_lifecycle.run_start_maintenance",
        "modules.core.plugin_setup.run_first_time_setup",
        "modules.core.plugin_setup.mark_data_home",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    monkeypatch.setattr("modules.session.session_lifecycle._reconcile_install", lambda: (UPGRADE, ""))
    monkeypatch.setattr("modules.session.session_lifecycle._leftovers_notice", lambda d: LEFTOVERS)


def _claude(source: str):
    from modules.session.session_lifecycle import SessionStart, start_session

    return start_session(SessionStart(
        session_id=SESSION, source=source, is_headless=False, pinned_build=None,
        workspace_dir=Path.cwd(),
    ))


def _opencode(source: str) -> str:
    from adapters.opencode import OpenCodeAdapter

    raw = {"session_id": SESSION, "source": source}
    return OpenCodeAdapter().adapt_session_start(raw).additional_context


def _assert_rows_whole_or_named(context: str, titles: dict) -> list[str]:
    dropped = []
    for name, title in titles.items():
        if title[:40] in context:
            assert _line(name, title) in context, f"{name} was cut mid-line"
        else:
            dropped.append(name)
    if dropped:
        assert any(all(name in line for name in dropped) for line in context.splitlines()), (
            f"the dropped rows {dropped} are not named in one line"
        )
    return dropped


BIRTH_ORDER = ("## Database upgrade", "## User memory", "## Resume point",
               "## Environment", "## Workspaces I manage")


@pytest.mark.parametrize("source", ["startup", "resume", "compact"])
def test_claude_code_birth_fits_the_cap_in_order_with_no_row_cut(corpus, maintenance, source):
    outcome = _claude(source)
    context = outcome.context

    assert len(context) < HOST_CAP, len(context)
    positions = [context.find(header) for header in BIRTH_ORDER]
    assert -1 not in positions, dict(zip(BIRTH_ORDER, positions))
    assert positions == sorted(positions)
    assert _assert_rows_whole_or_named(context, corpus)
    assert "## Leftovers" not in context
    assert LEFTOVERS.strip() in outcome.notices["## Leftovers"]


@pytest.mark.parametrize("source", ["startup", "compact"])
def test_opencode_birth_fits_the_cap_with_no_row_cut(corpus, maintenance, source):
    context = _opencode(source)

    assert len(context) < HOST_CAP, len(context)
    assert _assert_rows_whole_or_named(context, corpus)


def _without_source(context: str) -> str:
    return re.sub(r"(?m)^# .*\(source: \w+\)$", "", context)


def test_a_compaction_is_the_same_birth_as_a_start(corpus, maintenance):
    startup = _claude("startup").context
    compact = _claude("compact").context

    assert _without_source(compact) == _without_source(startup)


def test_under_pressure_lists_become_counts_and_the_roster_takes_the_richest_level_that_fits(corpus, maintenance):
    """Replaces "the roster keeps its pointers": the two footer pointers moved to the orchestrator identity."""
    context = _claude("compact").context

    assert "Pending signatures: 12" in context
    assert "Open contracts: 12" in context
    assert "P-" + "0" * 32 not in context
    assert f"gaia session snapshot --session-id {SESSION}" in context
    from modules.session.session_manifest import BIRTH_BUDGET, build_projects_section

    projects = context[context.index("## Workspaces I manage"):]
    room = BIRTH_BUDGET - (len(context) - len(projects))
    assert projects == build_projects_section(room), "the richest index level that fits the room"
    assert "context project <name>" not in projects and "gaia map" not in projects
    assert RESUME in context


def test_a_kernel_fits_the_cap_on_both_hosts_and_carries_no_user_row(corpus):
    """Replaces "a kernel fits the cap with no row cut": the user's rows left the kernel, so none can be cut."""
    from adapters.opencode import OpenCodeAdapter
    from modules.context.kernel_builder import KERNEL_HEADING, build_kernel_context

    row = {
        "contract_id": "a" + "1" * 16 + ".abc", "agent_id": "a" + "1" * 16,
        "dispatch_prompt": "Implement the birth-block budget. " * 100,
        "kernel_sections": json.dumps({"role": "primary", "surface": "gaia_system",
                                       "can_read": ["project_identity", "stack"], "can_write": []}),
        "workspace": "me", "dispatch_project": None, "plan_task_id": None,
    }
    for kernel in (build_kernel_context(row, agent_name="gaia-system"),
                   OpenCodeAdapter._child_kernel(row, "gaia-system")):
        assert kernel.startswith(KERNEL_HEADING)
        assert len(kernel) < HOST_CAP, len(kernel)
        assert "".join(row["dispatch_prompt"].split()) in "".join(kernel.split())
        assert "memory:user/" not in kernel
        assert not any(title[:40] in kernel for title in corpus.values())


def test_only_the_rows_the_block_carried_count_an_injection(corpus, maintenance):
    context = _claude("startup").context

    con = sqlite3.connect(os.environ["GAIA_DB"])
    try:
        counted = dict(con.execute(
            "SELECT name, injection_count FROM memory WHERE workspace = ?", (USER_SCOPE,),
        ).fetchall())
    finally:
        con.close()
    for name, count in counted.items():
        assert (count > 0) == (_line(name, corpus[name]) in context), name
