"""An interpreter is recognised by its shape and family, so no spelling of one runs code free by elimination."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

WORKTREE = str(Path(__file__).resolve().parents[4])

ASKS = [
    "python3.14 -c \"import os; os.system('rm -rf /tmp/x')\"",
    "pypy3 -c \"import os; os.system('rm -rf /tmp/x')\"",
    "lua5.4 -e \"os.execute('rm -rf /tmp/x')\"",
    "newinterp -e 'frob prod'",
    "./deploy.frob",
    "/opt/tools/deploy.frob --prod",
    "python3.27 -c \"import shutil; shutil.rmtree('/tmp/x')\"",
    "ruby3.3 -e \"system('rm -rf /tmp/x')\"",
    "php8.3 -r \"unlink('/tmp/x');\"",
    "nodejs -e \"require('fs').rmSync('/tmp/x')\"",
]

FREE = [
    "python3.14 -c \"print(1)\"",
    "pypy3 -c \"print(1)\"",
    "lua5.4 -e \"print('hi')\"",
]


@pytest.mark.parametrize("command", ASKS)
def test_interpreter_spelling_never_runs_code_free(command):
    assert detect_mutative_command(command, cwd=WORKTREE).is_mutative


@pytest.mark.parametrize("command", FREE)
def test_read_only_code_under_a_family_spelling_stays_free(command):
    assert not detect_mutative_command(command, cwd=WORKTREE).is_mutative


@pytest.mark.parametrize("shebang, body, asks", [
    ("#!/usr/bin/env python3.14", "import os\nos.remove('/tmp/x')\n", True),
    ("#!/usr/bin/env python3.14", "print('hi')\n", False),
    ("#!/opt/bin/frobsh", "frob prod\n", True),
])
def test_unknown_extension_script_is_judged_by_its_shebang(tmp_path, shebang, body, asks):
    (tmp_path / "x.frob").write_text(f"{shebang}\n{body}")
    assert detect_mutative_command("./x.frob", cwd=str(tmp_path)).is_mutative is asks
