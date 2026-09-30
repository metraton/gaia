"""Nothing in Gaia starts an AI session while the user is away.

Recurring work is a reminder or routine row that a read finds due when the user
next uses Gaia (see `gaia notifications`). The scheduler that used to start the
host unsupervised -- `gaia schedule`, the cron backend and its headless wrapper
-- is gone, and these tests hold it gone: the verb does not exist, the package
does not import, no shipped file launches a host CLI headless or writes a
crontab, and SessionStart neither announces schedules nor writes the database.

Every subprocess runs against a temporary HOME, GAIA_DATA_DIR and GAIA_DB.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.conftest import IsolatedRuntimeEnv, copy_bootstrapped_db

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GAIA = _REPO_ROOT / "bin" / "gaia"

_SHIPPED_TREES = ("gaia", "bin", "hooks", "skills", "agents")
_SCANNED_SUFFIXES = {".py", ".sh", ".md", ".json", ".sql", ".template", ".yml", ".yaml", ".js", ".mjs"}

# bin/plugin-dryrun.sh --functional runs `claude --plugin-dir ... -p` as an
# opt-in release probe that a person present starts, which meets the intent of
# AC-18. It is the one exemption; any other match fails the test.
_HEADLESS_PROBE_EXEMPTION = "bin/plugin-dryrun.sh"

# What the retired mechanism did: a host CLI started in print mode, permission
# prompts skipped, a crontab installed. A reading `crontab -l` is not a write.
# The print flag (-p or --print) counts in any position after the binary, with
# other flags in between, and whether the binary is bare, a path, or a list item.
_UNATTENDED_SHAPES = {
    "host-headless-launch": re.compile(
        r"""(?:^|[\s"'`=(/])claude(?:\.exe)?(?![\w.-])[^\n;&|]*?[\s"'](?:-p|--print)(?=[\s"'=]|$)""",
        re.MULTILINE,
    ),
    "permissions-skipped": re.compile(r"--dangerously-skip-permissions"),
    "crontab-shell-write": re.compile(r"\bcrontab\s+-(?:\s|$|e\b|r\b)", re.MULTILINE),
    "crontab-subprocess-write": re.compile(r"""["']crontab["']\s*,\s*["'](?!-l["'])"""),
}


def _run_gaia(env: IsolatedRuntimeEnv, *args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(_GAIA), *args],
        cwd=cwd or _REPO_ROOT, env=dict(env), capture_output=True, text=True, timeout=120,
    )


def _dump(db: Path) -> list:
    con = sqlite3.connect(db)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def test_gaia_schedule_is_an_unknown_subcommand(tmp_path):
    env = IsolatedRuntimeEnv(tmp_path / "runtime")

    verb = _run_gaia(env, "schedule", "list")
    assert verb.returncode != 0, verb.stdout
    assert "invalid choice" in verb.stderr and "schedule" in verb.stderr, verb.stderr

    top_level_help = _run_gaia(env, "--help")
    assert top_level_help.returncode == 0, top_level_help.stderr
    assert not re.search(r"\bschedule\b", top_level_help.stdout, re.IGNORECASE), top_level_help.stdout


def test_the_scheduler_package_does_not_import():
    spec = importlib.util.find_spec("gaia.schedulers")
    # A leftover empty directory is a namespace package with no origin; the
    # planner package had modules.
    assert spec is None or spec.origin is None, spec


def test_no_shipped_file_launches_a_host_headless_or_writes_a_crontab():
    offenders = []
    for tree in _SHIPPED_TREES:
        for path in sorted((_REPO_ROOT / tree).rglob("*")):
            if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
                continue
            if {"__pycache__", "node_modules"} & set(path.parts):
                continue
            relative = path.relative_to(_REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace").replace("\\\n", " ")
            offenders.extend(
                f"{relative}: {label}"
                for label, shape in _UNATTENDED_SHAPES.items()
                if shape.search(text)
                and not (label == "host-headless-launch" and relative == _HEADLESS_PROBE_EXEMPTION)
            )
    assert not offenders, offenders


def test_the_headless_launch_shape_catches_every_position_of_the_print_flag():
    launches = (
        'claude -p "summarize"',
        "claude --print 'summarize'",
        "/usr/local/bin/claude --model opus -p task",
        "claude --plugin-dir . --add-dir x --print 'hi'",
        'claude --plugin-dir "${ROOT}" \\\n  -p "hi"'.replace("\\\n", " "),
        'subprocess.run(["claude", "--output-format", "json", "-p", prompt])',
        "$(command -v claude) -p x",
        "run `claude -p 'triage'` every morning",
    )
    not_launches = (
        "claude plugin validate .",
        "claude --plugin-dir . --resume abc",
        "cat .claude/settings.json -p",
        "claude-code --print",
    )
    shape = _UNATTENDED_SHAPES["host-headless-launch"]
    assert [text for text in launches if not shape.search(text)] == []
    assert [text for text in not_launches if shape.search(text)] == []


def _seed_schedules_and_a_due_reminder(db: Path, workspace_root: Path, project: Path) -> None:
    con = sqlite3.connect(db)
    try:
        for ws in ("_gaia_user", "me"):
            con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)", (ws, ws))
        con.execute("UPDATE workspaces SET root_path = ? WHERE name = 'me'", (str(workspace_root),))
        con.execute("INSERT INTO projects (workspace, name, path) VALUES ('me', 'gaia', ?)",
                    (str(project),))
        task_id = con.execute(
            "INSERT INTO scheduled_tasks (workspace, name, schedule_spec, enabled, machine_scope) "
            "VALUES ('me', 'nightly', '{\"kind\":\"interval\",\"every_seconds\":3600}', 1, 'named')"
        ).lastrowid
        con.execute("INSERT INTO scheduled_task_machines (task_id, machine_name) "
                    "VALUES (?, 'some-other-machine')", (task_id,))
        con.execute("INSERT INTO schedule_suspensions (workspace, until, reason) "
                    "VALUES ('me', '2026-01-01T00:00:00Z', 'lapsed on purpose')")
        con.execute("INSERT INTO task_notifications (workspace, task_name, headline, kind, unread) "
                    "VALUES (NULL, 'reminder', 'Review the release notes', 'reminder', 0)")
        con.commit()
    finally:
        con.close()


def test_session_start_announces_no_schedule_and_writes_nothing(tmp_path, bootstrapped_db_template):
    env = IsolatedRuntimeEnv(tmp_path / "runtime")
    db = copy_bootstrapped_db(bootstrapped_db_template, Path(env["GAIA_DB"]))
    workspace_root = tmp_path / "ws" / "me"
    project = workspace_root / "gaia"
    project.mkdir(parents=True)
    _seed_schedules_and_a_due_reminder(db, workspace_root, project)
    before = _dump(db)

    birth = _run_gaia(env, "session", "preview", cwd=project)

    assert birth.returncode == 0, birth.stderr
    assert "Review the release notes" in birth.stdout, "the due reminder is still announced"
    announced = birth.stdout + birth.stderr
    assert not re.search(r"schedul|suspen|lapsed", announced, re.IGNORECASE), announced
    assert _dump(db) == before, "SessionStart must not write the database"
