"""An interpreter fed its program by a heredoc is judged by what that program does.

``python3 - <<'PY'``, ``node -``, ``bash -s`` and ``sh -s`` read their PROGRAM
from stdin, so a heredoc body there is code that runs, not data a CLI stores.
Measured at the public Bash boundary (``BashValidator.validate``): a mutating
body stops (a signature or the permanent floor), a read-only body runs free.
The data form -- the same body handed to ``gaia ... --<name>-file -`` -- is
held free by ``test_quoted_as_data_corpus.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for entry in (str(_REPO_ROOT), str(_HOOKS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402


def _heredoc(opener: str, body: str, delimiter: str = "EOF", quoted: bool = True) -> str:
    word = f"'{delimiter}'" if quoted else delimiter
    return f"{opener} <<{word}\n{body}\n{delimiter}"


_PY_RMTREE = "import shutil\nshutil.rmtree('/srv/data')"
_PY_REMOVE = "import os\nos.remove('/srv/data/ledger.db')"
_PY_PUSH = "import subprocess\nsubprocess.run(['git', 'push', 'origin', 'main'], check=True)"
_JS_RM = "const fs = require('fs');\nfs.rmSync('build', { recursive: true, force: true });"
_SH_RM = "cd /srv/app\nrm -rf build"

MUTATING = [
    ("python-rmtree-quoted", _heredoc("python3 -", _PY_RMTREE, "PY")),
    ("python-rmtree-unquoted", _heredoc("python3 -", _PY_RMTREE, "PY", quoted=False)),
    ("python-os-remove-quoted", _heredoc("python3 -", _PY_REMOVE, "PY")),
    ("python-os-remove-unquoted", _heredoc("python3 -", _PY_REMOVE, "PY", quoted=False)),
    ("python-subprocess-push-quoted", _heredoc("python3 -", _PY_PUSH, "PY")),
    ("python-subprocess-push-unquoted", _heredoc("python3 -", _PY_PUSH, "PY", quoted=False)),
    ("node-rmsync-quoted", _heredoc("node -", _JS_RM, "JS")),
    ("node-rmsync-unquoted", _heredoc("node -", _JS_RM, "JS", quoted=False)),
    ("bash-s-rm-quoted", _heredoc("bash -s", _SH_RM, "SH")),
    ("bash-s-rm-unquoted", _heredoc("bash -s", _SH_RM, "SH", quoted=False)),
    ("sh-s-rm-quoted", _heredoc("sh -s", _SH_RM, "SH")),
    ("sh-s-rm-unquoted", _heredoc("sh -s", _SH_RM, "SH", quoted=False)),
    ("bash-s-push-quoted", _heredoc("bash -s", "git push origin main", "SH")),
]

READ_ONLY = [
    ("python-print", _heredoc("python3 -", "print('hello')", "PY")),
    ("python-read-file",
     _heredoc("python3 -", "with open('package.json') as fh:\n    print(fh.read()[:200])", "PY")),
    ("python-json-parse",
     _heredoc("python3 -", "import json\nprint(json.load(open('package.json'))['name'])", "PY")),
    ("python-print-unquoted", _heredoc("python3 -", "print('hello')", "PY", quoted=False)),
    ("node-json-parse",
     _heredoc("node -", "const p = JSON.parse(require('fs').readFileSync('package.json', 'utf8'));\n"
              "console.log(p.name);", "JS")),
    ("bash-s-read", _heredoc("bash -s", "ls -la\ncat package.json", "SH")),
    ("sh-s-read", _heredoc("sh -s", "grep -n name package.json", "SH")),
]


def _clear_classifier_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    _clear_classifier_caches()
    yield root
    _clear_classifier_caches()


def _validate(command: str):
    return BashValidator().validate(
        command, is_subagent=True, session_id="s-779", agent_type="developer",
    )


@pytest.mark.parametrize("case_id,command", MUTATING, ids=[c[0] for c in MUTATING])
def test_mutating_heredoc_program_stops(case_id, command):
    result = _validate(command)
    assert not result.allowed, f"{case_id}: ran free ({result.reason!r})"


@pytest.mark.parametrize("case_id,command", READ_ONLY, ids=[c[0] for c in READ_ONLY])
def test_read_only_heredoc_program_runs_free(case_id, command):
    result = _validate(command)
    assert result.allowed, f"{case_id}: gated -- {result.reason[:240]}"
