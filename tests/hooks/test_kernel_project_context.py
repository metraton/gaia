"""A specialist dispatched to a project reads that project's context sections in its kernel, within the shared budget."""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from modules.context.kernel_builder import (  # noqa: E402
    KERNEL_BUDGET,
    build_cli_block,
    build_dispatch_kernel,
    build_project_context_block,
    build_kernel_context,
)

HEADING = "# Project context"
PROJECT = "alpha"
IDENTITY = "github.com/me/alpha"
DISPLAY = "alpha (/work/alpha)"
POINTER_SCOPE = f"--workspace me --project {IDENTITY}"


def _pointer(section: str) -> str:
    return f"`gaia context get-contract --section {section} {POINTER_SCOPE}`"


@pytest.fixture()
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.chdir(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute(
            "INSERT INTO projects (workspace, name, path, project_identity) VALUES ('me', ?, '/work/alpha', ?)",
            (PROJECT, IDENTITY),
        )
        con.commit()
    finally:
        con.close()
    return db


def _execute(db: Path, sql: str, *params) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def _section(db: Path, name: str, payload) -> None:
    _execute(
        db, "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
        "VALUES (?, ?, ?, '2026-10-01T00:00:00Z')", IDENTITY, name, json.dumps(payload),
    )


def _row(project, can_read, prompt="fix the build") -> dict:
    return {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": "me",
        "dispatch_prompt": prompt,
        "dispatch_project": project,
        "kernel_sections": json.dumps({"role": "primary", "surface": "iac", "can_read": can_read}),
    }


def _kernel(db: Path, row: dict) -> str:
    return build_kernel_context(row, agent_name="platform-architect", db_path=db)


def test_kernel_project_context_carries_readable_sections_whole(db):
    _section(db, "stack", {"language": "go", "framework": "gin"})
    _section(db, "environment", {"cloud": "gcp"})
    _section(db, "secrets_layout", {"vault": "unreadable here"})

    kernel = _kernel(db, _row(DISPLAY, ["stack", "environment"]))

    assert HEADING in kernel
    assert '"framework": "gin"' in kernel
    assert '"cloud": "gcp"' in kernel
    assert "unreadable here" not in kernel
    assert kernel.index("# Your Contract") < kernel.index(HEADING)
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_project_context_absent_when_the_project_has_no_rows(db):
    kernel = _kernel(db, _row(DISPLAY, ["stack"]))

    assert HEADING not in kernel


def test_kernel_project_context_section_larger_than_the_budget_becomes_a_pointer(db):
    _section(db, "cluster_details", {"nodes": ["n" * 200 for _ in range(970)]})
    _section(db, "stack", {"language": "go"})
    row = _row(DISPLAY, ["cluster_details", "stack"])

    kernel = _kernel(db, row)

    assert _pointer("cluster_details") in kernel
    assert "n" * 200 not in kernel
    assert '"language": "go"' in kernel
    assert len(kernel) <= KERNEL_BUDGET


MORE_PREFERENCES = "more — `gaia memory list --kind preference --workspace me`"


def test_kernel_past_the_budget_shrinks_the_cli_then_the_preferences_and_fits(db):
    _section(db, "stack", {"language": "go"})
    for index in range(12):
        _scope_row(db, "preference", index, project=True)
    row = _row(DISPLAY, ["stack"], prompt="do it " * 1000)

    kernel = _kernel(db, row)

    assert len(kernel) <= KERNEL_BUDGET
    assert kernel.startswith("# Your Contract\n")
    assert build_cli_block(contract_id=row["contract_id"], contract_only=True) in kernel
    assert "gaia memory search" not in kernel.split("# Your CLI")[1]
    assert "# Scope preferences: me > alpha (12)\n" in kernel
    dropped = int(re.search(r"\n\+(\d+) " + re.escape(MORE_PREFERENCES), kernel).group(1))
    assert 0 < dropped < 12
    counted = [name for name, count in sorted(_kernel_counts(db).items()) if count]
    assert len(counted) == 12 - dropped and all(f"memory:me/{name}" in kernel for name in counted)
    listed = _paste(db, ["memory", "list", "--kind", "preference", "--workspace", "me"])
    assert listed.returncode == 0, listed.stderr
    assert "decision_me_preference_00" in listed.stdout


def test_kernel_no_shrink_can_fit_opens_with_what_overflowed_and_how_to_read_the_goal(db):
    _section(db, "stack", {"language": "go"})
    _scope_row(db, "preference", 0, project=True)
    row = _row(DISPLAY, ["stack"], prompt="do it " * 1600)

    kernel = _kernel(db, row)

    contract = build_dispatch_kernel(row, db_path=db, agent_name="platform-architect")
    first, _, rest = kernel.partition("\n\n")
    assert re.fullmatch(
        rf"over the 9,500-char kernel budget at [\d,]+ chars: contract {len(contract):,} \(goal 9,600\), .*\. "
        rf"A host preview cuts what follows; `gaia contract view --contract-id {row['contract_id']} --row` "
        r"prints the goal whole",
        first,
    )
    assert rest.startswith(f"{contract}\n\n")
    assert f"1 section: stack — `gaia context get-contract --section <s> {POINTER_SCOPE}`" in kernel
    assert f"# Scope preferences: me > alpha (1)\n+1 {MORE_PREFERENCES}\n" in kernel
    assert kernel.endswith(build_cli_block(contract_id=row["contract_id"], contract_only=True))
    assert not any(_kernel_counts(db).values())


def test_kernel_project_context_many_sections_fit_or_point(db):
    names = [f"section_{i:02d}" for i in range(40)]
    for name in names:
        _section(db, name, {"value": name + " " + "v" * 600})

    kernel = _kernel(db, _row(DISPLAY, names))

    whole = [n for n in names if f"{n} " + "v" * 600 in kernel]
    pointed = [n for n in names if _pointer(n) in kernel]
    assert whole and names[0] in whole
    assert not set(whole) & set(pointed)
    assert len(whole) + len(pointed) == len(names) or "more" in kernel.split(HEADING)[1]
    assert len(kernel) <= KERNEL_BUDGET


def test_kernel_project_context_absent_for_a_project_matching_no_row(db):
    _section(db, "stack", {"language": "go"})

    kernel = _kernel(db, _row("ghost (/work/ghost)", ["stack"]))

    assert HEADING not in kernel
    assert '"language": "go"' not in kernel


def test_kernel_project_context_reads_a_workspace_scoped_legacy_row(db):
    _execute(
        db, "INSERT INTO project_context_contracts (workspace, contract_name, payload) VALUES ('me', 'stack', ?)",
        json.dumps({"language": "rust"}),
    )

    kernel = _kernel(db, _row(DISPLAY, ["stack"]))

    assert HEADING in kernel
    assert '"language": "rust"' in kernel


def test_kernel_project_context_absent_without_a_dispatch_project(db):
    _section(db, "stack", {"language": "go"})

    kernel = _kernel(db, _row(None, ["stack"]))

    assert HEADING not in kernel


def _scope_row(db: Path, kind: str, index: int, *, project: bool) -> None:
    owner, ref = ("project", IDENTITY) if project else ("workspace", None)
    _execute(
        db, "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, "
        "project_ref, updated_at) VALUES ('me', ?, 'decision', ?, 'why', 'anchor', ?, ?, ?, ?)",
        f"decision_me_{kind}_{index:02d}", f"Standing {kind} number {index:02d} " + "x" * 150, kind, owner, ref,
        f"2026-01-01T00:{index:02d}:00Z",
    )


def _project_with(db: Path, preferences: int, facts: int, lessons: int, notes: int) -> dict:
    """The alpha project holding that many scope rows and other standing notes, and two readable sections."""
    for kind, count in (("preference", preferences), ("fact", facts), ("lesson", lessons)):
        for index in range(count):
            _scope_row(db, kind, index, project=True)
    for index in range(notes):
        _execute(
            db, "INSERT INTO memory (workspace, name, type, description, body, class, project_ref, updated_at) "
            "VALUES ('me', ?, 'project', 'Decision: a standing note', 'the full body', 'anchor', ?, "
            "'2026-02-01T00:00:00Z')", f"decision_alpha_note_{index:02d}", IDENTITY,
        )
    _section(db, "stack", {"language": "go", "notes": "s" * 900})
    _section(db, "environment", {"cloud": "gcp", "notes": "e" * 900})
    return _row(DISPLAY, ["stack", "environment"])


def _kernel_counts(db: Path) -> dict:
    con = sqlite3.connect(db)
    try:
        return dict(con.execute("SELECT name, kernel_count FROM memory WHERE name LIKE 'decision_me_%'"))
    finally:
        con.close()


@pytest.mark.parametrize(
    "preferences, facts, lessons, notes", [(3, 29, 46, 40), (2, 5, 3, 4)], ids=["gaia-size", "mid-size"],
)
def test_a_project_kernel_carries_its_preferences_whole_and_counts_what_it_does_not_preload(
    db, preferences, facts, lessons, notes,
):
    row = _project_with(db, preferences, facts, lessons, notes)

    kernel = _kernel(db, row)

    assert f"# Scope preferences: me > alpha ({preferences})\n" in kernel
    for index in range(preferences):
        assert (
            f'  "Standing preference number {index:02d} {"x" * 150}"  scope=project'
            f"  ref=memory:me/decision_me_preference_{index:02d}"
        ) in kernel
    assert (
        f"# Scope memory: me > alpha\nfacts {facts}, lessons {lessons}, other notes {notes} — not preloaded: "
        "`gaia memory search '<term>' --workspace me`; `gaia memory show <slug> --workspace me`\n"
    ) in kernel
    assert "get-relevant --initiative alpha" not in kernel and "--sections anchor" not in kernel
    assert "Standing fact number" not in kernel and "Standing lesson number" not in kernel
    assert "decision_alpha_note_" not in kernel
    assert build_cli_block("platform-architect", workspace="me", contract_id=row["contract_id"]) in kernel
    assert "s" * 900 in kernel and "e" * 900 in kernel
    assert len(kernel) <= KERNEL_BUDGET
    assert all(count == ("_preference_" in name) for name, count in _kernel_counts(db).items())

    for verb, found in (
        ("memory search Standing --workspace me --limit 200", "decision_me_fact_00"),
        ("memory show decision_alpha_note_00 --workspace me", "the full body"),
        ("open memory:me/decision_me_preference_00", "decision_me_preference_00"),
    ):
        read = _paste(db, verb.split())
        assert read.returncode == 0, read.stderr
        assert found in read.stdout


def test_a_kernel_with_no_project_carries_the_workspace_preferences_and_counts_its_facts(db):
    for index in range(2):
        _scope_row(db, "preference", index, project=False)
    for index in range(4):
        _scope_row(db, "fact", index, project=False)
    row = _row(None, ["stack"])

    kernel = _kernel(db, row)

    assert "# Scope preferences: me (2)\n" in kernel
    assert "ref=memory:me/decision_me_preference_00" in kernel and "ref=memory:me/decision_me_preference_01" in kernel
    assert (
        "# Scope memory: me\nfacts 4 — not preloaded: "
        "`gaia memory search '<term>' --workspace me`; `gaia memory show <slug> --workspace me`\n"
    ) in kernel
    assert "Standing fact number" not in kernel
    assert HEADING not in kernel
    assert build_cli_block("platform-architect", workspace="me", contract_id=row["contract_id"]) in kernel
    assert len(kernel) <= KERNEL_BUDGET


def test_project_context_block_degrades_whole_then_pointers_then_one_line_naming_its_sections():
    sections = [("stack", {"v": "a" * 300}), ("environment", {"v": "b" * 300})]
    whole = build_project_context_block("me", IDENTITY, sections, max_chars=10_000)
    mixed = build_project_context_block("me", IDENTITY, sections, max_chars=len(whole) - 1)
    pointers = build_project_context_block("me", IDENTITY, sections, max_chars=len(mixed) - 150)
    line = build_project_context_block("me", IDENTITY, sections, max_chars=0)

    assert "a" * 300 in whole and "b" * 300 in whole
    assert _pointer("stack") not in whole
    assert "a" * 300 in mixed and "b" * 300 not in mixed
    assert _pointer("environment") in mixed and _pointer("stack") not in mixed
    assert "a" * 300 not in pointers
    assert _pointer("stack") in pointers and _pointer("environment") in pointers
    assert line == (
        f"{HEADING}\n\n2 sections: stack, environment — "
        f"`gaia context get-contract --section <s> {POINTER_SCOPE}`"
    )


def _pasted_pointers(kernel: str) -> list:
    return [
        shlex.split(line)[1:]
        for line in re.findall(r"`(gaia context get-contract --section [^<`][^`]*)`", kernel)
    ]


def _paste(db: Path, args: list):
    env = dict(os.environ, GAIA_DB=str(db), GAIA_DATA_DIR=str(db.parent))
    return subprocess.run(
        [sys.executable, str(_REPO_ROOT / "bin" / "gaia"), *args], capture_output=True, text=True, env=env,
    )


def test_kernel_project_context_pointer_pasted_returns_the_section_when_two_workspaces_share_a_name(db):
    _execute(db, "INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('other', 'other')")
    _execute(
        db, "INSERT INTO projects (workspace, name, path, project_identity) "
        "VALUES ('other', ?, '/other/alpha', 'github.com/other/alpha')", PROJECT,
    )
    _section(db, "stack", {"who": "mine", "pad": "p" * KERNEL_BUDGET})
    _execute(
        db, "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
        "VALUES ('github.com/other/alpha', 'stack', ?, '2026-10-01T00:00:00Z')",
        json.dumps({"who": "theirs", "pad": "p" * KERNEL_BUDGET}),
    )

    pointers = _pasted_pointers(_kernel(db, _row(DISPLAY, ["stack"])))

    assert len(pointers) == 1
    result = _paste(db, pointers[0])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["payload"]["who"] == "mine"


def test_kernel_project_context_pointer_pasted_returns_the_section_when_the_project_path_has_a_space(db):
    spaced = "/work/my proj"
    _execute(
        db, "INSERT INTO projects (workspace, name, path, project_identity) VALUES ('me', 'my proj', ?, NULL)",
        spaced,
    )
    _execute(
        db, "INSERT INTO project_context (project_identity, contract_name, payload, updated_at) "
        "VALUES (?, 'stack', ?, '2026-10-01T00:00:00Z')",
        spaced, json.dumps({"who": "spaced", "pad": "p" * KERNEL_BUDGET}),
    )

    pointers = _pasted_pointers(_kernel(db, _row(f"my proj ({spaced})", ["stack"])))

    assert len(pointers) == 1
    result = _paste(db, pointers[0])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["payload"]["who"] == "spaced"
