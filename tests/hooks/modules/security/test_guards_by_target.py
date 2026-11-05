"""The four path guards judge a program by the target its operands resolve to, never by whether a list names the program."""

from __future__ import annotations

import getpass
import os
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
USER = getpass.getuser()
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
    ("protected", f'newtool --out="{CLAUDE}"'),
    ("protected", f"newtool /tmp/a /tmp/b {CLAUDE}"),
    ("protected", "newtool ~/.claude/settings.json"),
    ("protected", "newtool ${HOME}/.claude/hooks/x.py"),
    ("protected", f"newtool ~{USER}/.claude/settings.json"),
    ("protected", f"newtool file://{CLAUDE}"),
    ("protected", f"newtool --out=file://{HOOKS}/x.py"),
    ("protected", f"newtool -o{HOOKS}/x.py"),
    ("protected", f"unar -o {HOOKS} /tmp/a.rar"),
    ("protected", f"lha xw={HOOKS} /tmp/a.lzh"),
    ("protected", f"ar x --output={HOOKS} /tmp/a.a"),
    ("protected", f"newtool --in-place {HOME}/.claude"),
    ("db", f'newtool "--db={STORE}" vacuum'),
    ("db", "newtool $HOME/.gaia/gaia.db"),
    ("db", f"newtool file:{STORE}?mode=rw"),
    ("db", f"newtool --url=sqlite:///{STORE}"),
    ("db", f"newtool {STORE}-wal"),
    ("db", f"usql file://{STORE} -c 'DELETE FROM memory'"),
    ("secret", "newcat $HOME/.ssh/id_rsa"),
    ("secret", f'newcat "--key={HOME}/.ssh/id_rsa"'),
    ("secret", f"newcat --in {HOME}/.ssh/id_rsa"),
    ("secret", f"newcat file://{HOME}/.ssh/id_rsa"),
    ("secret", f"newcat ~{USER}/.ssh/id_rsa"),
]


@pytest.mark.parametrize("guard, command", REFUSED)
def test_operand_on_a_guarded_target_is_refused_whatever_the_program(guard, command):
    allowed, reason = GUARDS[guard].check(command, cwd=WORKTREE)
    assert not allowed, f"{guard} guard let {command!r} run free"
    assert reason


@pytest.fixture
def links(tmp_path):
    for target, name in ((f"{HOME}/.claude", "c"), (STORE, "s"), (f"{HOME}/.ssh/id_rsa", "k")):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        (tmp_path / name).symlink_to(target)
    return tmp_path


@pytest.mark.parametrize("guard, command", [
    ("protected", "newtool {links}/c/settings.json"),
    ("db", "newtool {links}/s"),
    ("db", "newtool --db={links}/s vacuum"),
    ("secret", "newcat {links}/k"),
])
def test_a_link_is_judged_by_the_file_it_reaches(links, guard, command):
    assert not GUARDS[guard].check(command.format(links=links), cwd=WORKTREE)[0]


@pytest.mark.parametrize("command", [
    f"newtool --in-place {HOME}/.bashrc",
    f"newtool --file={HOME}/.bashrc",
    "newtool ~/.gitconfig",
    "newtool $HOME/.bashrc",
    f'newtool "--rc={HOME}/.zshrc"',
    f"newtool /tmp/a {HOME}/.profile",
])
def test_unnamed_program_on_an_account_path_asks_for_a_signature(command):
    assert detect_mutative_command(command, cwd=WORKTREE).is_mutative
