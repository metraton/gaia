"""The birth block's "## Workspaces I manage" section, against fixture databases.

Every value is read from the database: workspaces and projects from their
tables, pending counts from the memory threads, dispatch counts from the
contract rows. The section degrades in a fixed order under its budget and the
two pointer lines always land.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.session import session_manifest  # noqa: E402
from tests.conftest import copy_bootstrapped_db  # noqa: E402

ROOMY = 100_000
FOOTER = (
    "A name that is not listed here: gaia map <word>\n"
    "A project's card: gaia context project <name>"
)


def _stamp(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Roster:
    """Writes workspaces, projects, pending threads and dispatched turns into one fixture database."""

    def __init__(self, db: Path, home: Path):
        self.db = db
        self.home = home
        self.roots: dict = {}

    def _run(self, sql: str, params: tuple = ()) -> None:
        con = sqlite3.connect(self.db)
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()

    def workspace(self, name: str, root: Path | None = None) -> None:
        root = root if root is not None else self.home / "ws" / name
        self.roots[name] = root
        self._run("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (name, name))
        self._run("UPDATE workspaces SET root_path = ? WHERE name = ?", (str(root), name))

    def project(self, ws: str, name: str, *, group: str | None = None, pending: int = 0,
                dispatches: int = 0, status: str = "active", dispatch_age_days: float = 1) -> Path:
        path = self.roots[ws] / (group or "") / name
        self._run(
            "INSERT INTO projects (workspace, name, path, group_name, status) VALUES (?, ?, ?, ?, ?)",
            (ws, name, str(path), group, status),
        )
        for i in range(pending):
            self._run(
                "INSERT INTO memory (workspace, name, type, description, body, class, status, "
                "initiative, updated_at) VALUES (?, ?, 'project', 'd', 'b', 'thread', 'open', ?, ?)",
                (ws, f"{name}_thread_{i}", name.replace("-", "_"), _stamp(1)),
            )
        for _ in range(dispatches):
            self.dispatch(ws, f"{name} ({path})", dispatch_age_days)
        return path

    def dispatch(self, ws: str, dispatch_project: str | None, age_days: float = 1) -> None:
        self._run(
            "INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, "
            "dispatch_project, raw_handoff_json, created_at) VALUES (?, ?, 'COMPLETE', ?, '{}', ?)",
            ("a" + "0" * 16, ws, dispatch_project, _stamp(age_days)),
        )

    def contract(self, ws: str, payload: dict) -> None:
        self._run(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
            "VALUES (?, 'project_identity', ?)", (ws, json.dumps(payload)),
        )


@pytest.fixture
def roster(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: None)
    return Roster(db, home)


def _section(max_chars: int = ROOMY) -> str:
    return session_manifest.build_projects_section(max_chars)


def _body(section: str) -> list:
    """The section without its header and footer, as lines."""
    assert section.startswith(session_manifest.PROJECTS_HEADER + "\n")
    assert section.endswith(FOOTER)
    inner = section[len(session_manifest.PROJECTS_HEADER) + 1:-len(FOOTER)]
    return inner.rstrip("\n").splitlines()


def _grouped_workspace(roster) -> None:
    roster.workspace("aaxis")
    for name in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"):
        roster.project("aaxis", name, group="harness")
    roster.project("aaxis", "aos", group="aos", pending=5)
    roster.project("aaxis", "aos-iac", group="aos")
    roster.project("aaxis", "loose")


def test_the_section_renders_the_agreed_shape(roster):
    roster.workspace("aaxis")
    roster.workspace("century-inc")
    roster.project("aaxis", "aos", group="aos", pending=5, dispatches=1)
    roster.project("aaxis", "aos-iac", group="aos")
    roster.project("aaxis", "aos-keycloak", group="aos")
    roster.project("aaxis", "aaxis-io-iac", group="aaxis-io")
    roster.project("century-inc", "factory", pending=11, dispatches=3)
    roster.project("century-inc", "platform", pending=2)

    assert _section() == (
        "## Workspaces I manage\n"
        "Total: 2 workspaces · 2 groups · 6 projects\n"
        "workspace=aaxis path=~/ws/aaxis groups=2 projects=4\n"
        "  aaxis-io=[aaxis-io-iac]\n"
        "  aos=[aos(5), aos-iac, aos-keycloak]\n"
        "workspace=century-inc path=~/ws/century-inc groups=0 projects=2\n"
        "  [factory(11), platform(2)]\n\n" + FOOTER
    )


def test_the_workspace_path_shows_home_as_a_tilde(roster, tmp_path):
    roster.workspace("inside")
    roster.workspace("outside", tmp_path / "elsewhere" / "outside")
    roster.project("inside", "one")
    roster.project("outside", "two")

    lines = _body(_section())

    assert "workspace=inside path=~/ws/inside groups=0 projects=1" in lines
    assert f"workspace=outside path={tmp_path / 'elsewhere' / 'outside'} groups=0 projects=1" in lines


def test_a_group_lists_at_most_five_and_counts_the_rest(roster):
    _grouped_workspace(roster)

    lines = _body(_section())

    assert "  harness=[alpha, beta, delta, epsilon, eta, +3]" in lines


def test_a_project_with_pending_work_is_always_listed_past_the_cap_in_its_place(roster):
    roster.workspace("aaxis")
    for name in ("a1", "a2", "a3", "a4", "a5", "a6"):
        roster.project("aaxis", name, group="g", dispatches=1)
    roster.project("aaxis", "z-quiet", group="g", pending=4)
    roster.project("aaxis", "x-also", group="g", pending=1)
    roster.project("aaxis", "y-other", group="g")

    lines = _body(_section())

    assert "  g=[a1, a2, a3, a4, a5, z-quiet(4), x-also(1), +2]" in lines


def test_order_is_dispatches_then_pending_then_name(roster):
    roster.workspace("aaxis")
    roster.project("aaxis", "p-name-first", group="g")
    roster.project("aaxis", "q-pending", group="g", pending=3)
    roster.project("aaxis", "r-one-turn", group="g", dispatches=1)
    roster.project("aaxis", "s-many-turns", group="g", dispatches=4)
    roster.project("aaxis", "t-turns-and-pending", group="g", dispatches=1, pending=2)

    lines = _body(_section())

    assert "  g=[s-many-turns, t-turns-and-pending(2), r-one-turn, q-pending(3), p-name-first]" in lines


def test_dispatches_older_than_thirty_days_do_not_order_a_project(roster):
    roster.workspace("aaxis")
    roster.project("aaxis", "old-favourite", group="g", dispatches=9, dispatch_age_days=45)
    roster.project("aaxis", "recent", group="g", dispatches=1, dispatch_age_days=29)

    assert "  g=[recent, old-favourite]" in _body(_section())


def test_a_dispatch_names_its_project_by_path_even_when_the_stored_name_differs(roster):
    roster.workspace("aaxis")
    first = roster.project("aaxis", "first", group="g")
    roster.project("aaxis", "second", group="g")
    roster.dispatch("aaxis", f"legacy-slot-name ({first})")
    roster.dispatch("aaxis", "second")
    roster.dispatch("aaxis", "second")
    roster.dispatch("aaxis", "a group name")
    roster.dispatch("aaxis", None)

    assert "  g=[second, first]" in _body(_section())


def test_a_workspace_without_groups_is_one_bracket_line_with_no_group_name(roster):
    roster.workspace("century-inc")
    for name in ("b", "a", "c"):
        roster.project("century-inc", name)

    lines = _body(_section())

    assert lines == [
        "Total: 1 workspaces · 0 groups · 3 projects",
        "workspace=century-inc path=~/ws/century-inc groups=0 projects=3",
        "  [a, b, c]",
    ]


def test_a_mixed_workspace_lists_its_groups_alphabetically_then_the_ungrouped(roster):
    roster.workspace("aaxis")
    roster.project("aaxis", "loose-one")
    roster.project("aaxis", "zz", group="zeta")
    roster.project("aaxis", "mm", group="mu")
    roster.project("aaxis", "aa", group="alpha")
    roster.project("aaxis", "loose-two", pending=1)

    lines = _body(_section())

    assert lines[2:] == ["  alpha=[aa]", "  mu=[mm]", "  zeta=[zz]", "  [loose-two(1), loose-one]"]
    assert lines[1] == "workspace=aaxis path=~/ws/aaxis groups=3 projects=5"


def test_every_workspace_is_listed_in_name_order_with_the_total_on_top(roster):
    for name in ("github-repos", "aaxis", "me"):
        roster.workspace(name)
        roster.project(name, f"{name}-p", group="g")

    lines = _body(_section())

    assert lines[0] == "Total: 3 workspaces · 3 groups · 3 projects"
    assert [line.split()[0] for line in lines if line.startswith("workspace=")] == [
        "workspace=aaxis", "workspace=github-repos", "workspace=me",
    ]


def test_a_vanished_project_is_not_counted_or_listed(roster):
    roster.workspace("aaxis")
    roster.project("aaxis", "here", group="g")
    roster.project("aaxis", "gone", group="g", status="missing")

    section = _section()

    assert "gone" not in section
    assert "projects=1" in section


def test_a_copy_of_another_project_is_not_listed_or_counted(roster):
    roster.workspace("aaxis")
    roster.project("aaxis", "auto-claude-sleep", group="g")
    duplicate = roster.project("aaxis", "auto-claude-sleep-2", group="g")
    roster.contract("aaxis", {
        "auto_claude_sleep": {"name": "auto-claude-sleep",
                              "local_path": str(roster.roots["aaxis"] / "g" / "auto-claude-sleep")},
        "auto_claude_sleep_2": {"name": "auto-claude-sleep-2", "local_path": str(duplicate),
                                "copy_of": "auto_claude_sleep"},
    })

    section = _section()

    assert "auto-claude-sleep-2" not in section
    assert "  g=[auto-claude-sleep]" in section
    assert "projects=1" in section


@pytest.fixture
def wide(roster):
    """Three workspaces whose full section is long enough to pass through every level."""
    for ws in ("alpha-ws", "beta-ws", "grouped-ws"):
        roster.workspace(ws)
    for i in range(12):
        roster.project("alpha-ws", f"alpha-project-{i:02d}")
    for g in ("one", "two", "three"):
        for i in range(8):
            roster.project("grouped-ws", f"{g}-project-{i}", group=g)
    roster.project("grouped-ws", "one-pending", group="one", pending=2)
    roster.project("beta-ws", "beta-solo", group="only", pending=1)
    return roster


def _levels(section_for) -> list:
    """The distinct sections, from the fullest down, as the budget shrinks one character at a time."""
    seen = [section_for(ROOMY)]
    while True:
        smaller = section_for(len(seen[-1]) - 1)
        if smaller == seen[-1]:
            return seen
        seen.append(smaller)


def test_the_section_degrades_in_the_fixed_order(wide):
    levels = _levels(_section)

    assert len(levels) == 5, [len(level) for level in levels]
    full, three, pending_only, group_counts, workspaces = levels

    assert "  one=[one-pending(2), one-project-0, one-project-1, one-project-2, one-project-3, +4]" in full
    assert "  one=[one-pending(2), one-project-0, one-project-1, +6]" in three
    assert "  one=[one-pending(2), +8]" in pending_only
    assert "  two=[+8]" in pending_only
    assert "alpha-project" not in group_counts and "one-pending" not in group_counts
    assert "  one=9, three=8, two=8" in group_counts
    assert "  only=1" in group_counts
    assert _body(workspaces) == [
        "Total: 3 workspaces · 4 groups · 38 projects",
        "workspace=alpha-ws path=~/ws/alpha-ws groups=0 projects=12",
        "workspace=beta-ws path=~/ws/beta-ws groups=1 projects=1",
        "workspace=grouped-ws path=~/ws/grouped-ws groups=3 projects=25",
    ]


def test_each_level_fits_the_room_it_was_chosen_for(wide):
    levels = _levels(_section)

    for level in levels:
        assert _section(len(level)) == level
    for smaller, larger in zip(levels[1:], levels):
        assert len(smaller) < len(larger)


def test_the_pointer_lines_land_whatever_the_room(wide):
    for room in (ROOMY, 1_000, 200, 50, 0):
        section = _section(room)
        assert section.endswith(FOOTER), room
        assert section.startswith("## Workspaces I manage\nTotal: 3 workspaces"), room


def test_the_smallest_level_is_returned_even_when_it_overflows_the_room(wide):
    assert _section(0) == _levels(_section)[-1]


def test_a_database_with_no_workspace_and_no_project_has_no_section(roster):
    assert _section() == ""


def test_the_pointer_names_the_resolved_cli_path(roster, monkeypatch):
    roster.workspace("aaxis")
    roster.project("aaxis", "one")
    monkeypatch.setattr(session_manifest, "_resolve_gaia_cli_path", lambda: "/abs/bin/gaia")

    section = _section()

    assert section.endswith(
        "A name that is not listed here: /abs/bin/gaia map <word>\n"
        "A project's card: /abs/bin/gaia context project <name>"
    )


def test_the_dispatch_reader_is_one_query_over_the_window(roster, monkeypatch):
    from gaia.store import reader

    roster.workspace("aaxis")
    roster.dispatch("aaxis", "p (/x/p)", 1)
    roster.dispatch("aaxis", "p (/x/p)", 29)
    roster.dispatch("aaxis", "p (/x/p)", 31)
    roster.dispatch("aaxis", "q (/x/q)", 2)
    roster.dispatch("aaxis", None, 2)

    queries = []
    real = reader._read_only_rows
    monkeypatch.setattr(reader, "_read_only_rows",
                        lambda *args, **kw: queries.append(args[0]) or real(*args, **kw))

    assert reader.dispatch_counts_by_project(30) == {"p (/x/p)": 2, "q (/x/q)": 1}
    assert len(queries) == 1 and "GROUP BY" in queries[0]


def test_the_dispatch_reader_answers_nothing_on_an_unreadable_database(tmp_path):
    from gaia.store import reader

    assert reader.dispatch_counts_by_project(30, tmp_path) == {}
