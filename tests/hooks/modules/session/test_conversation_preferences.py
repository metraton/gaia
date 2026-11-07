"""A preference marked for the conversation leads the birth, survives its cut, and rides beside every prompt with its title in full."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO = Path(__file__).resolve().parents[4]
for _path in (_REPO, _REPO / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store.memory_placement import PREFERENCE_DESCRIPTION_MAX  # noqa: E402
from modules.context import user_sections  # noqa: E402
from modules.session import answer_form, register_skill  # noqa: E402

SLUG = "user_pref_conversacion_frases_simples_20261106"
TITLE = ("Conversation with Jorge: a few plain sentences, like a colleague; "
         "no bold, headers, tables, menus or recaps.")
PR_TITLE = "PRs: show Jorge a PR only when it is his to merge; put PR links in one closing block, never repeated."
FILES_TITLE = r"Files for Jorge: give the Windows Explorer path \\wsl.localhost\sandbox--aaxis-env\home\jorge\<path>."
MARK = ("memory", "reclassify", SLUG, "--surface=conversation")


@pytest.fixture
def store(tmp_path, monkeypatch, bootstrapped_db_template):
    data = tmp_path / "data"
    db = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    home = tmp_path / "home"
    home.mkdir()
    for name, value in (("HOME", home), ("GAIA_DATA_DIR", data), ("GAIA_DB", db)):
        monkeypatch.setenv(name, str(value))
    con = sqlite3.connect(db)
    con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('_gaia_user', '_gaia_user')")
    _user(con, SLUG, TITLE, "2026-01-01T00:00:00Z")
    con.commit()
    con.close()
    return db


def _user(con, name, title, updated_at, surface=None):
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, class, kind, owner_level, "
        "updated_at, surface) VALUES ('_gaia_user', ?, 'user', ?, 'why', 'anchor', 'preference', "
        "'user', ?, ?)",
        (name, title, updated_at, surface),
    )


def _run(argv, cwd, stdin=None):
    return subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True,
                          env={**os.environ}, check=False)


def _gaia(tmp_path, *argv):
    return _run([sys.executable, str(_REPO / "bin" / "gaia"), *argv], tmp_path)


def _prompt_line(tmp_path, session_id="s-pref"):
    result = _run([sys.executable, str(_REPO / "hooks" / "user_prompt_submit.py")], tmp_path,
                  stdin=json.dumps({"session_id": session_id, "prompt": "hola"}))
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    return next((line for line in context.splitlines() if "conversation preferences" in line), "")


def test_the_marked_row_leads_the_birth_preview_and_survives_the_cut(store, tmp_path):
    con = sqlite3.connect(store)
    for i in range(90):
        _user(con, f"user_pref_bulk_{i:03d}", f"bulk standing rule number {i}: " + "keep the habit " * 6,
              f"2026-09-01T00:00:{i % 60:02d}Z")
    con.commit()
    con.close()

    marked = _gaia(tmp_path, *MARK)
    preview = _gaia(tmp_path, "session", "preview")

    assert marked.returncode == 0, marked.stderr
    user_part = preview.stdout[preview.stdout.index("## User memory"):]
    assert user_part.splitlines()[1:3] == [
        "kind=preference", f'  "{TITLE}"  surface=conversation  ref=memory:user/{SLUG}',
    ]
    assert "Left out to fit" in user_part and SLUG not in user_part.split("Left out")[1]


def test_a_simulated_prompt_carries_the_three_live_titles_verbatim_until_human_register_loads(store, tmp_path):
    assert _prompt_line(tmp_path) == ""
    assert _gaia(tmp_path, *MARK).returncode == 0
    con = sqlite3.connect(store)
    _user(con, "user_pref_pr_links", PR_TITLE, "2026-01-02T00:00:00Z", surface="conversation")
    _user(con, "user_pref_files_path", FILES_TITLE, "2026-01-03T00:00:00Z", surface="conversation")
    con.commit()
    con.close()

    first = _prompt_line(tmp_path)
    register_skill.record_loaded("s-pref")
    loaded = _prompt_line(tmp_path)
    register_skill.record_dropped("s-pref")

    assert len(first) <= user_sections.PREFERENCES_LINE_MAX
    for title in (PR_TITLE, FILES_TITLE, TITLE):
        assert title in first and title in loaded
    assert "memory:user/" not in first
    assert user_sections.PREFERENCES_CLAUSE in first
    assert user_sections.REGISTER_REMINDER in first and user_sections.REGISTER_REMINDER not in loaded
    assert user_sections.REGISTER_REMINDER in _prompt_line(tmp_path)


def test_three_titles_at_the_write_cap_fit_the_line_budget_with_the_reminder():
    rows = [{"name": f"user_pref_conv_{i}", "workspace": "_gaia_user", "kind": "preference",
             "surface": "conversation", "description": "x" * PREFERENCE_DESCRIPTION_MAX} for i in range(3)]

    line = user_sections.preferences_line(rows, remind_register=True)

    assert line.count("x" * PREFERENCE_DESCRIPTION_MAX) == 3
    assert len(line) <= user_sections.PREFERENCES_LINE_MAX


def test_past_three_rows_the_first_three_keep_their_titles_and_the_rest_are_counted_with_coordinates():
    rows = [{"name": f"user_pref_conv_{i}", "workspace": "_gaia_user", "kind": "preference",
             "surface": "conversation", "description": f"Conversation: rule {i}."} for i in range(5)]

    line = user_sections.preferences_line(rows, remind_register=False)

    assert all(f"Conversation: rule {i}." in line for i in range(3))
    assert "Conversation: rule 3." not in line and "Conversation: rule 4." not in line
    assert "2 more, each read with `gaia memory show <coordinate>`: " \
           "memory:user/user_pref_conv_3, memory:user/user_pref_conv_4" in line
    assert "memory:user/user_pref_conv_0" not in line
    assert user_sections.preferences_line(rows[:1] + [{**rows[1], "surface": "document"}],
                                          remind_register=False).count("Conversation: rule") == 1


def test_a_forbidden_form_in_the_final_answer_is_counted_and_recorded(store):
    rows = [{"name": SLUG, "workspace": "_gaia_user", "kind": "preference",
             "surface": "conversation", "description": TITLE}]
    answer = "## Result\n\n**Done.**\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\n# not a header\n```\n"

    meta = answer_form.measure_final_answer("s-form", answer, rows)

    assert meta["counts"] == {"bold": 1, "headers": 1, "tables": 1}
    assert meta["rows"] == [f"memory:user/{SLUG}"]
    con = sqlite3.connect(store)
    recorded = con.execute("SELECT payload FROM harness_events WHERE type = ?",
                           (answer_form.FORM_MEASURED_EVENT,)).fetchall()
    con.close()
    assert len(recorded) == 1 and json.loads(recorded[0][0])["violations"] == 3
    assert answer_form.measure_final_answer("s-form", answer, [{**rows[0], "surface": None}]) is None
