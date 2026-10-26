"""Reading a secret through code an interpreter is handed is refused at the Bash boundary, like ``cat`` of it."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

PY_SECRET_READS = {
    "expanduser": "import os; print(open(os.path.expanduser('~/.ssh/id_rsa')).read())",
    "path-home-fragments": "from pathlib import Path; print((Path.home() / '.ssh' / 'id_rsa').read_text())",
    "system-file": "print(open('/etc/shadow').read())",
    "env-file": "print(open('.env').read())",
    "aws-credentials": "print(open('/root/.aws/credentials').read())",
}
JS_SECRET_READ = "console.log(require('fs').readFileSync(require('os').homedir() + '/.ssh/id_rsa', 'utf8'))"


@pytest.fixture
def judge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    monkeypatch.chdir(tmp_path)

    def run(command: str) -> tuple:
        detect_mutative_command.cache_clear()
        tiers_module._classify_command_tier_cached.cache_clear()
        payload = {"cwd": str(tmp_path), "tool_use_id": "t-secret", "session_id": "s-secret",
                   "agent_id": "asecretread01", "agent_type": "developer"}
        result = BashValidator().validate(command, is_subagent=True, session_id="s-secret",
                                          agent_type="developer", hook_payload=payload)
        if result.allowed:
            return "FREE", result.rule
        return ("SIGNED" if result.approval_id else "BLOCKED"), result.rule

    return run


def _refused(verdict: tuple) -> bool:
    return verdict == ("BLOCKED", "floor.secret-read")


@pytest.mark.parametrize("name", list(PY_SECRET_READS))
def test_python_reading_a_secret_is_refused_in_every_spelling(name, judge, tmp_path):
    code = PY_SECRET_READS[name]
    (tmp_path / "peek.py").write_text(code.replace("; ", "\n") + "\n", encoding="utf-8")
    spellings = {
        "python -c": f"python3 -c {shlex.quote(code)}",
        "behind a cd": f"cd {tmp_path} && python3 -c {shlex.quote(code)}",
        "heredoc": f"python3 - <<'PY'\n{code.replace('; ', chr(10))}\nPY",
        "script": "python3 peek.py",
        "script after a value flag": "python3 -W ignore peek.py",
    }
    for spelling, command in spellings.items():
        assert _refused(judge(command)), (spelling, command)


def test_javascript_and_other_interpreters_reading_a_secret_are_refused(judge, tmp_path):
    (tmp_path / "peek.js").write_text(JS_SECRET_READ + "\n", encoding="utf-8")
    for command in (
        f"node -e {shlex.quote(JS_SECRET_READ)}",
        "node peek.js",
        "ruby -e 'puts File.read(Dir.home + \"/.ssh/id_rsa\")'",
        "perl -e 'open(F, \"<\", \"$ENV{HOME}/.ssh/id_rsa\"); print <F>'",
    ):
        assert _refused(judge(command)), command


def test_code_that_names_no_secret_path_is_not_refused(judge, tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    for command in (
        "python3 -c \"print(open('package.json').read())\"",
        "python3 -c \"d = {'credentials': 1}; print(d['credentials'])\"",
        "node -e \"console.log(require('fs').readFileSync('package.json', 'utf8'))\"",
    ):
        assert judge(command)[0] == "FREE", command


@pytest.mark.parametrize("flags", ["-W ignore", "-X utf8", "-O", "-W ignore -X dev -B"])
def test_an_interpreter_value_flag_is_not_read_as_the_script(flags, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "read.py").write_text("print(open('package.json').read())\n", encoding="utf-8")
    (tmp_path / "deploy.py").write_text("import shutil\nshutil.rmtree('build')\n", encoding="utf-8")
    detect_mutative_command.cache_clear()
    assert detect_mutative_command(f"python3 {flags} read.py", cwd=str(tmp_path)).is_mutative is False
    assert detect_mutative_command(f"python3 {flags} deploy.py", cwd=str(tmp_path)).is_mutative is True
