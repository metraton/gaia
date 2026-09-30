"""The session birth block every session starts with.

Protects what the user feels when a session opens: the orchestrator knows the
projects and their live-pending counts without reading project memory, knows
where it stands and where the data really lives, and knows the user by every
one of their standing rows, whole -- and all of it fits under the host's cap,
so the host never swaps the block for a short preview. Runs in-process against
a temporary HOME, GAIA_DATA_DIR and GAIA_DB; never against a real workspace.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.session import session_manifest  # noqa: E402

USER_SCOPE = "_gaia_user"

FACT = (
    "Hecho: Jorge Aguilar, DevOps at a consultancy; speaks Chilean Spanish "
    "and works across a personal workspace and two client workspaces. "
) * 4

PREFERENCES = [
    "Preferencia: versioned artifacts are written in English; the Spanish "
    "that reaches the user stays in conversation only. " * 3,
    "Preferencia: GitHub is operated through ghx per command, leaving the "
    "global gh account untouched for other tools. " * 3,
    "Preferencia: nothing published in a repository carries traces of the "
    "tools used to produce it, attribution trailers included. " * 3,
    "Preferencia: every file the user must look at is handed over as a "
    "Windows path he can open from the host. " * 3,
    "Preferencia: video narration uses a male voice, chosen per video. " * 3,
    "Preferencia temporal: comments stay minimal until the review agent "
    "stops flagging dense blocks (bug: reviewer counts docstrings). " * 3,
]

SUPERSEDED = "Preferencia: an older rule about comment density, since replaced."
LOGGED = "Preferencia: a retired note kept only as history in the log class."
PROJECT_ANCHOR = "PROJECT ANCHOR TEXT that belongs to the gaia project only."
THREAD_TEXT = "THREAD TEXT about an open gaia pull request."


def _row(con, workspace, name, *, type_, body, class_="anchor", description=None,
         status=None, initiative=None, updated_at="2026-09-01T00:00:00Z"):
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, "
        "status, initiative, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, description, body, class_, status, initiative, updated_at),
    )


def _user(con, name, body, *, class_="anchor", updated_at="2026-09-01T00:00:00Z"):
    description = body.split(". ", 1)[0]
    _row(con, USER_SCOPE, name, type_="user", body=body, class_=class_,
         description=description, updated_at=updated_at)


def _seed(db: Path, root: Path) -> dict:
    """Seed users, projects in three workspaces and a 30-project group; return the dirs."""
    dirs = {
        "me": root / "me",
        "gaia": root / "me" / "gaia",
        "balance": root / "me" / "balance",
        "century": root / "century",
        "branch": root / "century" / "branch",
        "outside": root.parent / "outside",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    groups = {
        "me": (dirs["me"], {"gaia": dirs["gaia"], "balance": dirs["balance"]}),
        "century-inc": (dirs["century"], {"branch": dirs["branch"]}),
        "github-repos": (root / "gh", {f"repo{i:02d}": root / "gh" / f"repo{i:02d}" for i in range(30)}),
    }
    con = sqlite3.connect(db)
    try:
        for ws in (USER_SCOPE, "ws", *groups):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (ws, ws))
        for ws, (ws_root, projects) in groups.items():
            con.execute("UPDATE workspaces SET root_path = ? WHERE name = ?", (str(ws_root), ws))
            payload = {}
            for name, path in projects.items():
                con.execute(
                    "INSERT INTO projects (workspace, name, path) VALUES (?, ?, ?)",
                    (ws, name, str(path)),
                )
                payload[name] = {"name": name, "local_path": str(path),
                                 "description": f"{name} project"}
            con.execute(
                "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
                "VALUES (?, 'project_identity', ?)",
                (ws, json.dumps(payload)),
            )

        _user(con, "user_jorge", FACT)
        for i, body in enumerate(PREFERENCES):
            _user(con, f"user_pref_{i}", body)
        _user(con, "user_pref_old_comments", SUPERSEDED)
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
            "VALUES (?, 'user_pref_5', 'user_pref_old_comments', 'supersedes')",
            (USER_SCOPE,),
        )
        _user(con, "user_retired_note", LOGGED, class_="log")

        _row(con, "me", "project_gaia_anchor", type_="project", body=PROJECT_ANCHOR,
             initiative="gaia", description=PROJECT_ANCHOR)
        for ws, name in (("me", "thread_a"), ("me", "thread_b"), ("ws", "thread_c")):
            _row(con, ws, name, type_="project", body=THREAD_TEXT, class_="thread",
                 status="open", initiative="gaia", description=THREAD_TEXT)
        con.commit()
    finally:
        con.close()
    return dirs


@pytest.fixture
def birth(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    dirs = _seed(db, tmp_path / "ws")
    monkeypatch.chdir(dirs["gaia"])
    return SimpleNamespace(db=db, data=data, dirs=dirs, monkeypatch=monkeypatch)


def _sections(block: str) -> dict:
    """Split the block at its top-level headers, keyed by header."""
    parts = re.split(r"(?m)^(## .+)$", block)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _user_part(block: str) -> str:
    headers = session_manifest.BIRTH_SECTION_HEADERS
    return block[block.index(headers[2]):]


def test_the_block_has_exactly_the_four_sections_in_order(birth):
    block = session_manifest.build_session_context()
    assert re.findall(r"(?m)^## .+$", block) == list(session_manifest.BIRTH_SECTION_HEADERS)


def test_the_block_fits_the_host_budget_and_the_user_rows_their_share(birth):
    block = session_manifest.build_session_context()
    assert len(block) <= 9_500
    assert len(_user_part(block)) <= 4_000


def test_every_standing_user_row_arrives_whole_and_no_retired_one_does(birth):
    block = session_manifest.build_session_context()
    for body in [FACT, *PREFERENCES]:
        assert body.strip() in block
    assert SUPERSEDED not in block
    assert LOGGED not in block


def test_project_memory_never_loads_at_birth_only_its_counts(birth):
    block = session_manifest.build_session_context()
    assert PROJECT_ANCHOR not in block
    assert THREAD_TEXT not in block
    projects = _sections(block)[session_manifest.BIRTH_SECTION_HEADERS[0]]
    gaia_line = next(line for line in projects.splitlines() if line.startswith("- gaia"))
    assert re.search(r"\b3\b", gaia_line), "the count must add the threads of both workspaces"


def test_a_large_group_is_one_line_with_its_count(birth):
    block = session_manifest.build_session_context()
    projects = _sections(block)[session_manifest.BIRTH_SECTION_HEADERS[0]]
    lines = [line for line in projects.splitlines() if "github-repos" in line]
    assert len(lines) == 1
    assert re.search(r"\b30\b", lines[0].rsplit(":", 1)[-1])
    assert not re.search(r"\brepo\d\d\b", block)


def test_the_environment_names_the_real_data_home_and_its_database(birth):
    block = session_manifest.build_session_context()
    environment = _sections(block)[session_manifest.BIRTH_SECTION_HEADERS[1]]
    assert str(birth.data) in environment
    assert str(birth.db) in environment


def test_the_user_rows_are_the_same_from_any_folder(birth):
    seen = set()
    for place in ("me", "gaia", "branch", "outside"):
        birth.monkeypatch.chdir(birth.dirs[place])
        seen.add(_user_part(session_manifest.build_session_context()))
    assert len(seen) == 1


def test_user_rows_over_their_share_still_arrive_whole_with_a_visible_excess_line(birth):
    extra = [f"Preferencia: an additional long standing rule number {i}. " * 16 for i in range(3)]
    con = sqlite3.connect(birth.db)
    try:
        for i, body in enumerate(extra):
            _user(con, f"user_pref_extra_{i}", body)
        con.commit()
    finally:
        con.close()
    bodies = [b.strip() for b in [FACT, *PREFERENCES, *extra]]
    assert sum(map(len, bodies)) > 5_000

    block = session_manifest.build_session_context()
    user_part = _user_part(block)
    for body in bodies:
        assert body in user_part
    from modules.context.user_sections import overflow_line
    assert overflow_line(sum(map(len, bodies))) in user_part
    assert len(block) <= 9_500
