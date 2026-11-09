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
    build_project_notes_block,
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


def test_kernel_project_context_under_a_huge_prompt_is_one_line_and_the_contract_stays_whole(db):
    _section(db, "stack", {"language": "go"})
    row = _row(DISPLAY, ["stack"], prompt="do it " * 1300)

    kernel = _kernel(db, row)

    cut_lines = len(build_cli_block("platform-architect", workspace="me").splitlines()) - 3
    assert f"1 section: stack — `gaia context get-contract --section <s> {POINTER_SCOPE}`" in kernel
    assert f"# Your CLI\n\n+{cut_lines} more — `gaia --help`" in kernel
    assert kernel.startswith(build_dispatch_kernel(row, db_path=db, agent_name="platform-architect"))
    assert len(kernel) <= KERNEL_BUDGET


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


SCOPE_KINDS = (("preference", 10), ("fact", 20), ("lesson", 48))
SCOPE_TOTAL = sum(count for _, count in SCOPE_KINDS)


def _big_project(db: Path) -> dict:
    """A project holding 78 scope rows, two readable sections and 40 standing notes; its preferences are its stalest rows."""
    minute = 0
    for kind, count in SCOPE_KINDS:
        for i in range(count):
            minute += 1
            _execute(
                db, "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, "
                "project_ref, updated_at) VALUES ('me', ?, 'decision', ?, 'why', 'anchor', ?, 'project', ?, ?)",
                f"decision_me_{kind}_{i:02d}", f"Standing {kind} number {i:02d} " + "x" * 150, kind, IDENTITY,
                f"2026-01-01T{minute // 60:02d}:{minute % 60:02d}:00Z",
            )
    for i in range(40):
        _execute(
            db, "INSERT INTO memory (workspace, name, type, description, body, class, project_ref, updated_at) "
            "VALUES ('me', ?, 'project', ?, 'the full body', 'anchor', ?, '2026-02-01T00:00:00Z')",
            f"atom_alpha_note_{i:02d}", "Fact: " + "word " * 20, IDENTITY,
        )
    _section(db, "stack", {"language": "go", "notes": "s" * 900})
    _section(db, "environment", {"cloud": "gcp", "notes": "e" * 900})
    return _row(DISPLAY, ["stack", "environment"])


def _scope_section(kernel: str) -> list:
    return kernel.split("# Scope memory", 1)[1].split("\n\n# ", 1)[0].splitlines()


def test_a_project_with_more_scope_rows_than_the_budget_births_its_fixed_blocks_whole(db):
    row = _big_project(db)

    kernel = _kernel(db, row)

    notes = build_project_notes_block(row, db_path=db)
    assert build_cli_block("platform-architect", workspace="me") in kernel
    assert "s" * 900 in kernel and "e" * 900 in kernel
    assert _pointer("stack") not in kernel and _pointer("environment") not in kernel
    assert "\n- atom_alpha_note_" in notes and notes in kernel
    assert len(kernel) <= KERNEL_BUDGET


def test_cut_scope_memory_ends_with_its_omitted_count_and_the_verb_that_lists_them(db):
    kernel = _kernel(db, _big_project(db))

    section = _scope_section(kernel)
    shown = {kind: sum(f"Standing {kind} number" in line for line in section) for kind, _ in SCOPE_KINDS}
    tail = re.fullmatch(r"\+(\d+) more — `(gaia memory list [^`]+)`", section[-1])
    assert tail, section[-1]
    assert int(tail[1]) > 0 and int(tail[1]) + sum(shown.values()) == SCOPE_TOTAL
    assert shown["lesson"] == 0 or shown["fact"] == 20, "lessons leave before facts"
    assert shown["fact"] == 0 or shown["preference"] == 10, "facts leave before preferences"

    listed = _paste(db, shlex.split(re.sub(r"<[^>]*lesson[^>]*>", "lesson", tail[2]))[1:])
    assert listed.returncode == 0, listed.stderr
    assert "decision_me_lesson_00" in listed.stdout


def test_only_the_scope_rows_a_kernel_delivers_count_a_kernel_access(db):
    kernel = _kernel(db, _big_project(db))

    con = sqlite3.connect(db)
    try:
        counted = dict(con.execute("SELECT name, kernel_count FROM memory WHERE name LIKE 'decision_me_%'"))
    finally:
        con.close()
    assert len(counted) == SCOPE_TOTAL
    for name, count in counted.items():
        assert count == (1 if f"ref=memory:me/{name}" in kernel else 0), name


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
