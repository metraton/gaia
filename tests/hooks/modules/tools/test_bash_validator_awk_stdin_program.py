#!/usr/bin/env python3
"""An awk program read from stdin is judged by the content the line proves, or signs."""

import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

SYSTEM = 'BEGIN{system("touch x")}'
REDIRECT = '{ print $1 > "out.txt" }'
PIPE = '{ print $1 | "sh" }'
INPLACE = '{ gsub(/a/, "b") } 1'
READ = "{ print $1 }"


def _heredoc(opener, body):
    return f"{opener} <<'EOF'\n{body}\nEOF"


MUST_SIGN = [
    f"echo '{SYSTEM}' | awk -f -",
    f"echo '{SYSTEM}' | awk -f /dev/stdin data.txt",
    f"echo '{SYSTEM}' | awk -f /dev/fd/0 data.txt",
    f"echo '{SYSTEM}' | awk -f /proc/self/fd/0 data.txt",
    f"echo '{REDIRECT}' | awk -f - data.txt",
    f"echo '{PIPE}' | awk -f - data.txt",
    f"printf '%s' '{SYSTEM}' | gawk -E /dev/stdin data.txt",
    f"echo '{SYSTEM}' | awk --file=- data.txt",
    f"echo '{SYSTEM}' | gawk --fil=- data.txt",
    f"printf '%s%s' 'BEGIN{{sys' 'tem(\"touch x\")}}' | awk -f - data.txt",
    _heredoc("awk -f - data.txt", SYSTEM),
    _heredoc("awk -f - data.txt", REDIRECT),
    _heredoc("awk -f - data.txt", PIPE),
    _heredoc("awk -f /dev/stdin data.txt", SYSTEM),
    _heredoc("gawk -f /proc/self/fd/0 data.txt", REDIRECT),
    _heredoc("gawk -E - data.txt", SYSTEM),
    _heredoc("gawk -i /dev/stdin '{print}' data.txt", SYSTEM),
    _heredoc("gawk -i inplace -f - /tmp/notes.txt", INPLACE),
    f"echo '{INPLACE}' | gawk -i inplace -f - /tmp/notes.txt",
    f"awk -f - data.txt <<< '{SYSTEM}'",
    f"awk <<< '{SYSTEM}' -f - data.txt",
    "awk -f - data.txt < {prog}",
    "cat {prog} | awk -f - data.txt",
    f"gawk -M '{SYSTEM}' data.txt",
    "mawk -W exec /dev/stdin < {prog}",
    f"echo '{SYSTEM}' | {{ awk -f - data.txt # < {{readonly}}\n}}",
    f"echo '{SYSTEM}' | {{ awk -f - data.txt; }}",
    f"echo '{SYSTEM}' | ( awk -f - data.txt )",
    f"{{ awk '{SYSTEM}' data.txt; }}",
    "export AWKPATH={evil} && gawk -f prog.awk data.txt",
    "export AWKPATH={evil}\nawk -f ./prog.awk data.txt",
    f"goawk '{SYSTEM}' data.txt",
    f"original-awk '{SYSTEM}' data.txt",
    f"onetrue-awk '{SYSTEM}' data.txt",
    "gawk 'BEGIN{getline l < \"/inet/tcp/0/example.com/80\"}'",
]

UNPROVABLE = [
    "awk -f - data.txt",
    "awk -f - data.txt # < {readonly}",
    "gawk -f prog.awk data.txt",
    "awk -f - data.txt < {readonly}",
    "ls && awk -f - data.txt",
    "awk -f - data.txt < {missing}",
    'awk -f - data.txt <<< "$PROGRAM"',
    "cat {readonly} | sed 's/x/y/' | awk -f - data.txt",
    "git show HEAD:prog.awk | awk -f - data.txt",
    f"echo \"{READ}\" | awk -f - data.txt",
    f"echo -e '{READ}' | awk -f - data.txt",
    f"printf '%s\\n' '{READ}' | awk -f - data.txt",
    _heredoc("awk -f - data.txt", READ).replace("<<'EOF'", "<<EOF"),
]

PROVEN_READ_ONLY = [
    _heredoc("awk -f - data.txt", READ),
    f"echo '{READ}' | awk -f - data.txt",
    f"printf '%s' '{READ}' | gawk -f /dev/stdin data.txt",
    f"awk -f - data.txt <<< '{READ}'",
    "cat {readonly} | awk -f - data.txt",
    f"echo '{READ}' | {{ awk -f - data.txt; }}",
    "gawk -f ./prog.awk data.txt # system",
    "mawk -f prog.awk data.txt",
]


@pytest.fixture(params=["git", "nogit"])
def files(request, tmp_path, monkeypatch):
    from modules.security import tiers
    from modules.security.mutative_verbs import detect_mutative_command

    root = tmp_path / "work"
    root.mkdir()
    if request.param == "git":
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    (tmp_path / "w.awk").write_text(SYSTEM + "\n")
    (tmp_path / "read.awk").write_text(READ + "\n")
    (root / "prog.awk").write_text(READ + "\n")
    return {
        "prog": tmp_path / "w.awk", "readonly": tmp_path / "read.awk", "missing": tmp_path / "absent.awk",
        "evil": tmp_path, "cwd": root,
    }


def _validate(command, files):
    from modules.tools.bash_validator import BashValidator

    for name, path in files.items():
        command = command.replace("{" + name + "}", str(path))
    return BashValidator().validate(
        command, is_subagent=True, session_id="t", agent_type="gaia-planner", cwd=str(files["cwd"]),
    )


@pytest.mark.parametrize("command", MUST_SIGN + UNPROVABLE)
def test_stdin_program_that_runs_writes_or_is_unprovable_does_not_run_free(command, files):
    assert _validate(command, files).allowed is False


@pytest.mark.parametrize("command", PROVEN_READ_ONLY)
def test_stdin_program_proven_read_only_runs_free(command, files):
    result = _validate(command, files)
    assert result.allowed is True, result.reason
