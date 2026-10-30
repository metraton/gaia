"""The session birth block every session starts with.

Protects what the user feels when a session opens: the orchestrator knows the
projects and their live-pending counts without reading project memory, knows
where it stands and where the data really lives, and knows the user through an
index of their standing facts and preferences -- each line a title and a ref,
a preference only while its ``until`` holds -- and all of it fits under the
host's cap, so the host never swaps the block for a short preview. Runs
in-process against a temporary HOME, GAIA_DATA_DIR and GAIA_DB; never against a
real workspace.
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

from modules.context.user_sections import omitted_line  # noqa: E402
from modules.session import session_manifest  # noqa: E402

USER_SCOPE = "_gaia_user"

FACT_TITLE = "Jorge Aguilar, DevOps at a consultancy; speaks Chilean Spanish."
PREFERENCE_TITLES = [
    "Versioned artifacts are written in English.",
    "GitHub is operated through ghx per command.",
    "Nothing published in a repository carries traces of the tools used.",
    "Every file the user must look at is handed over as a Windows path.",
    "Video narration uses a male voice, chosen per video.",
    "Comments stay minimal until the review agent stops flagging dense blocks.",
]

BODY = "THE LONG WHY that stays behind the ref and never reaches the birth block."
SUPERSEDED = "an older rule about comment density, since replaced."
LOGGED = "a retired note kept only as history in the log class."
EXPIRED = "ship on Fridays, until a date that has passed."
TEMPORARY = "pass --no-verify, while the 2099 freeze lasts."
PROJECT_ANCHOR = "PROJECT ANCHOR TEXT that belongs to the gaia project only."
THREAD_TEXT = "THREAD TEXT about an open gaia pull request."


def _row(con, workspace, name, *, type_, body, class_="anchor", description=None,
         status=None, initiative=None, kind=None, owner_level=None, until=None,
         updated_at="2026-09-01T00:00:00Z"):
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, "
        "status, initiative, kind, owner_level, until, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, description, body, class_, status, initiative,
         kind, owner_level, until, updated_at),
    )


def _user(con, name, title, *, kind="preference", class_="anchor", until=None,
          updated_at="2026-09-01T00:00:00Z"):
    claim = "Fact" if kind == "fact" else "Preference"
    _row(con, USER_SCOPE, name, type_="user", body=BODY, class_=class_,
         description=f"{claim}: {title}", kind=kind, owner_level="user", until=until,
         updated_at=updated_at)


def _line(name, title, until=None):
    marker = f"  until={until}" if until else ""
    return f'  "{title}"{marker}  ref=memory:user/{name}'


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

        _user(con, "user_jorge", FACT_TITLE, kind="fact")
        for i, title in enumerate(PREFERENCE_TITLES):
            _user(con, f"user_pref_{i}", title)
        _user(con, "user_pref_old_comments", SUPERSEDED)
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind) "
            "VALUES (?, 'user_pref_5', 'user_pref_old_comments', 'supersedes')",
            (USER_SCOPE,),
        )
        _user(con, "user_retired_note", LOGGED, class_="log")
        _user(con, "user_pref_expired", EXPIRED, until="2020-01-01")
        _user(con, "user_pref_temporary", TEMPORARY, until="2099-01-01")

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
    return block[block.index(session_manifest.USER_HEADER):block.index(session_manifest.ENVIRONMENT_HEADER)]


def _bulk(con, count: int, *, prefix: str = "bulk") -> list[str]:
    """``count`` long-titled preferences older than every seeded row; returns their names, stalest first."""
    names = []
    for i in range(count):
        name = f"user_pref_{prefix}_{i:03d}"
        _user(con, name, f"{prefix} standing rule number {i}: " + "keep the habit going " * 6,
              updated_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z")
        names.append(name)
    return names


def test_the_block_has_its_sections_in_order_and_due_only_when_something_is_due(birth):
    block = session_manifest.build_session_context()
    expected = [h for h in session_manifest.BIRTH_SECTION_HEADERS if h != session_manifest.DUE_HEADER]
    assert re.findall(r"(?m)^## .+$", block) == expected
    assert session_manifest.BIRTH_SECTION_HEADERS == (
        "## User memory", "## Due", "## Environment", "## Workspaces I manage",
    )


def test_the_block_fits_the_host_budget_and_the_user_index_its_share(birth):
    from modules.context.user_sections import USER_ROWS_BUDGET

    block = session_manifest.build_session_context()
    assert len(block) <= 9_500
    assert len(_user_part(block)) <= USER_ROWS_BUDGET


def test_every_standing_user_row_arrives_as_a_title_and_a_ref_and_no_retired_one_does(birth):
    """Replaces "arrives whole": the birth carries a title and a ref, the body stays behind the ref."""
    block = session_manifest.build_session_context()
    user_part = _user_part(block)
    assert user_part.splitlines()[:2] == ["## User memory", "kind=preference"]
    assert "kind=fact" in user_part.splitlines()
    assert _line("user_jorge", FACT_TITLE) in user_part
    for i, title in enumerate(PREFERENCE_TITLES[:5]):
        assert _line(f"user_pref_{i}", title) in user_part
    assert BODY not in block
    assert SUPERSEDED not in block
    assert LOGGED not in block


def test_a_preference_is_indexed_only_while_its_until_holds(birth):
    user_part = _user_part(session_manifest.build_session_context())
    assert _line("user_pref_temporary", TEMPORARY, until="2099-01-01") in user_part
    assert EXPIRED not in user_part


def test_project_memory_never_loads_at_birth_only_its_counts(birth):
    block = session_manifest.build_session_context()
    assert PROJECT_ANCHOR not in block
    assert THREAD_TEXT not in block
    projects = _sections(block)[session_manifest.PROJECTS_HEADER]
    assert re.search(r"\bgaia\(3\)", projects), "the count must add the threads of both workspaces"


def test_a_large_workspace_lists_five_projects_then_narrows_and_ends_as_one_counted_line(birth):
    block = session_manifest.build_session_context()
    projects = _sections(block)[session_manifest.PROJECTS_HEADER]
    assert re.search(r"(?m)^  \[repo00, repo01, repo02, repo03, repo04, \+25\]$", projects)

    roomy = session_manifest.build_projects_section(sys.maxsize)
    narrow = session_manifest.build_projects_section(len(roomy) - 1)
    assert re.search(r"(?m)^  \[repo00, repo01, repo02, \+27\]$", narrow), narrow

    floor = session_manifest.build_projects_section(0)
    assert re.search(r"(?m)^workspace=github-repos path=\S+/ws/gh groups=0 projects=30$", floor)
    assert not re.search(r"\brepo\d\d\b", floor)


def test_the_environment_names_the_real_data_home_and_its_database(birth):
    block = session_manifest.build_session_context()
    environment = _sections(block)[session_manifest.ENVIRONMENT_HEADER]
    assert str(birth.data) in environment
    assert str(birth.db) in environment


def test_the_user_index_is_the_same_from_any_folder(birth):
    seen = set()
    for place in ("me", "gaia", "branch", "outside"):
        birth.monkeypatch.chdir(birth.dirs[place])
        seen.add(_user_part(session_manifest.build_session_context()))
    assert len(seen) == 1


def test_an_index_over_the_cap_drops_whole_rows_stalest_first_and_names_them_in_one_line(birth):
    """Replaces "over their share arrive whole with an excess line": the index stays under the cap by leaving rows out."""
    con = sqlite3.connect(birth.db)
    try:
        stalest_first = _bulk(con, 90)
        con.commit()
    finally:
        con.close()

    block = session_manifest.build_session_context()
    user_part = _user_part(block)
    left_out = re.search(r"\(Left out to fit the host's context cap, stalest first: ([^.]*)\. Read one", user_part)

    assert len(block) <= 9_500
    assert left_out, user_part
    names = left_out.group(1).split(", ")
    assert len(names) >= 1 and set(names) == set(stalest_first[:len(names)])
    assert omitted_line(names) in user_part
    assert _line("user_jorge", FACT_TITLE) in user_part
    for name in names:
        assert f"ref=memory:user/{name}\n" not in user_part + "\n"


def test_after_the_index_is_trimmed_the_projects_take_the_richest_level_that_fits(birth):
    con = sqlite3.connect(birth.db)
    try:
        _bulk(con, 90, prefix="trim")
        con.commit()
    finally:
        con.close()

    block = session_manifest.build_session_context()
    projects = block[block.index(session_manifest.PROJECTS_HEADER):]
    room = 9_500 - (len(block) - len(projects))

    assert len(block) <= 9_500
    assert "workspace=" in projects, projects
    assert projects == session_manifest.build_projects_section(room)
