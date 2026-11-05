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


@pytest.mark.parametrize("name, commands, raw", [
    ("x.frob", ["./x.frob"], b"#!/bin/sh\n\x00\ntouch RAN\n"),
    ("x.frob", ["./x.frob"], b"#!/bin/sh\n\xff\xfe\ntouch RAN\n"),
    ("x.frob", ["./x.frob"], b"#!/bin/sh\n" + b"#" * 2_000_000 + b"\ntouch RAN\n"),
    ("x.sh", ["sh x.sh", "./x.sh"], b"#!/bin/sh\n\x00\ntouch RAN\n"),
    ("x.sh", ["sh x.sh", "./x.sh"], b"#!/bin/sh\n\xff\xfe\ntouch RAN\n"),
    ("x.sh", ["bash x.sh", "./x.sh"], b"#!/bin/bash\n# \xff\ntouch RAN\n"),
    ("x.sh", ["bash x.sh", "./x.sh"], b"#!/bin/bash\ntouch RAN\n\x00"),
    ("x.sh", ["sh x.sh", "./x.sh"], b"#!/bin/sh\n" + b"#" * 2_000_000 + b"\ntouch RAN\n"),
], ids=["frob-nul", "frob-undecodable", "frob-huge", "sh-nul", "sh-undecodable", "bash-undecodable", "bash-nul-end", "sh-huge"])
def test_script_not_read_whole_as_text_asks(tmp_path, name, commands, raw):
    (tmp_path / name).write_bytes(raw)
    for command in commands:
        assert detect_mutative_command(command, cwd=str(tmp_path)).is_mutative, command
