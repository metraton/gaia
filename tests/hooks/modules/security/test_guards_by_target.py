"""The four path guards judge a program by the target its operands resolve to, never by whether a list names the program."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import (  # noqa: E402
    gaia_db_write_guard,
    protected_path_guard,
    sensitive_read_guard,
)
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

HOME = str(Path.home())
STORE = f"{HOME}/.gaia/gaia.db"
CLAUDE = f"{HOME}/.claude/settings.json"
HOOKS = f"{HOME}/.claude/hooks"
WORKTREE = str(Path(__file__).resolve().parents[4])

GUARDS = {
    "protected": protected_path_guard,
    "db": gaia_db_write_guard,
    "secret": sensitive_read_guard,
}

REFUSED = [
    ("protected", f"newtool --in-place {CLAUDE}"),
    ("protected", f"newtool --target={CLAUDE} data.json"),
    ("protected", f"newtool > {CLAUDE}"),
    ("protected", f"sed -n 1p {CLAUDE}"),
    ("protected", f"bsdtar -xf /tmp/a.tar -C {HOOKS}"),
    ("protected", f"7z x /tmp/a.7z -o{HOOKS}"),
    ("protected", f"cpio -idm -D {HOOKS}"),
    ("protected", f"unzip /tmp/a.zip -d {HOME}/.claude"),
    ("protected", f"cp -r /tmp/hooks {HOME}/.claude"),
    ("protected", f"rsync -a /tmp/dotfiles/ {HOME}/.claude/"),
    ("db", f"newtool {STORE}"),
    ("db", f"usql sqlite://{STORE} -c 'DELETE FROM memory'"),
    ("db", f"newtool --db={STORE} vacuum"),
    ("db", f"newtool > {STORE}"),
    ("db", f"cp /tmp/x {STORE}"),
    ("db", f"cp /tmp/gaia.db {HOME}/.gaia/"),
    ("db", f"mv {STORE} /tmp/x"),
    ("db", f"mv {STORE}-wal /tmp/y"),
    ("protected", f"mv {CLAUDE} /tmp/x"),
    ("secret", f"newcat {HOME}/.ssh/id_rsa"),
    ("secret", f"newcat --in={HOME}/.ssh/id_rsa"),
    ("secret", f"cat {HOME}/.ssh/id_rsa"),
]


@pytest.mark.parametrize("guard, command", REFUSED)
def test_operand_on_a_guarded_target_is_refused_whatever_the_program(guard, command):
    allowed, reason = GUARDS[guard].check(command, cwd=WORKTREE)
    assert not allowed, f"{guard} guard let {command!r} run free"
    assert reason


def test_unnamed_program_on_an_account_path_asks_for_a_signature():
    assert detect_mutative_command(f"newtool --in-place {HOME}/.bashrc", cwd=WORKTREE).is_mutative
