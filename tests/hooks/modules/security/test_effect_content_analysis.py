"""A test-suite run is free only when its files prove it has no effect; otherwise it signs naming the marker."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

CLEAN_TEST = '''\
import os
import sqlite3
import pytest


@pytest.fixture
def repo(tmp_path):
    return tmp_path / "repo"


def test_effects_stay_in_tmp(tmp_path, repo):
    repo.mkdir()
    (repo / "a.txt").write_text("kubectl delete pod x  # import requests")
    out = tmp_path.joinpath("b.txt")
    with open(out, "w") as handle:
        handle.write(os.environ.get("HOME", ""))
    sqlite3.connect(str(tmp_path / "t.db")).close()
    sqlite3.connect(":memory:").close()
    assert open(repo / "a.txt").read()
'''

# Runs the file analysis decides: (command, run inside the git repository).
SUITE_COMMANDS = [
    ("python3 -m unittest discover -s tests", True),
    ("pytest --timeout=30 tests/test_a.py", True),
    ("python3 -m pytest -q tests/", False),
]
SUITE_IDS = ["unittest", "pytest-unmodelled-option", "pytest-outside-repo"]

# Each marked suite: file to write, its body, and the words the signature must name.
MARKED = {
    "network-import": ("tests/test_a.py", "import requests\n\ndef test_a():\n    requests.get('https://x')\n", "network"),
    "network-from-import": ("tests/test_a.py", "from urllib import request\n\ndef test_a():\n    pass\n", "network"),
    "subprocess-in-conftest": ("tests/conftest.py", "import subprocess\n", "subprocess"),
    "subprocess-alias": ("tests/test_a.py", "from os import system as s\n\ndef test_a():\n    s('true')\n", "subprocess"),
    "subprocess-in-helper": ("tests/helper.py", "import os\nrun = os.popen\n", "subprocess"),
    "database-remote": ("tests/test_a.py", "import psycopg2\n\ndef test_a():\n    pass\n", "database"),
    "database-local-outside-tmp": ("tests/test_a.py", "import sqlite3\n\ndef test_a():\n    sqlite3.connect('state.db')\n", "database"),
    "secret-named-variable": ("tests/test_a.py", "import os\n\ndef test_a():\n    os.environ['GITHUB_TOKEN']\n", "secret"),
    "secret-store": ("tests/test_a.py", "import keyring\n\ndef test_a():\n    pass\n", "secret"),
    "write-outside-tmp": ("tests/test_a.py", "def test_a():\n    open('/etc/motd', 'a').write('x')\n", "write"),
    "write-relative": ("tests/test_a.py", "from pathlib import Path\n\ndef test_a():\n    Path('out.txt').write_text('x')\n", "write"),
    "open-aliased": ("tests/test_a.py", "def test_a(tmp_path):\n    o = open\n    o('x', 'w')\n", "write"),
    "dynamic-import": ("tests/test_a.py", "import importlib\n\ndef test_a(name):\n    importlib.import_module(name)\n", "cannot"),
    "computed-attribute": ("tests/test_a.py", "import os\n\ndef test_a():\n    getattr(os, 'sys' + 'tem')('true')\n", "cannot"),
    "unparseable": ("tests/test_a.py", "def test_a(:\n", "cannot"),
    "path-changed": ("tests/test_a.py", "import sys\nsys.path.insert(0, '/opt/x')\n\ndef test_a():\n    pass\n", "cannot"),
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text(CLEAN_TEST)
    (root / "tests" / "conftest.py").write_text("import pytest\n")
    (root / "tests" / "helper.py").write_text("VALUE = 1\n")
    (root / "tests" / "test_b.py").write_text("from helper import VALUE\n\ndef test_b():\n    assert VALUE\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    return root


def _judge(command: str, cwd: Path):
    detect_mutative_command.cache_clear()
    return detect_mutative_command(command, cwd=str(cwd))


def _place(repo: Path, in_repo: bool) -> None:
    if not in_repo:
        (repo / ".git").rename(repo.parent / "moved-git")


@pytest.mark.parametrize(("command", "in_repo"), SUITE_COMMANDS, ids=SUITE_IDS)
def test_suite_without_markers_runs_free_on_its_files(command, in_repo, repo):
    _place(repo, in_repo)
    result = _judge(command, repo)
    assert not result.is_mutative, result.reason
    assert result.verb == "suite-content-free", result.reason
    assert "no effect marker" in result.reason


@pytest.mark.parametrize(("command", "in_repo"), SUITE_COMMANDS, ids=SUITE_IDS)
@pytest.mark.parametrize("case", sorted(MARKED))
def test_suite_with_a_marker_signs_naming_it(case, command, in_repo, repo):
    _place(repo, in_repo)
    path, body, marker = MARKED[case]
    (repo / path).write_text(body)
    if case == "subprocess-in-helper":
        (repo / "tests" / "test_a.py").write_text("import helper\n\ndef test_a():\n    pass\n")
    result = _judge(command, repo)
    assert result.is_mutative, f"{case} ran free: {result.reason}"
    assert marker in result.reason, result.reason
    assert Path(path).name in result.reason, result.reason


@pytest.mark.parametrize("command", ["pytest tests/", "python3 -m pytest -q -n auto tests/test_a.py"])
def test_plain_pytest_inside_its_repository_keeps_its_verdict(command, repo):
    (repo / "tests" / "test_a.py").write_text(MARKED["network-import"][1])
    result = _judge(command, repo)
    assert not result.is_mutative and result.verb == "pytest-run", result.reason


def test_a_claim_in_the_command_text_frees_nothing(repo):
    (repo / "tests" / "test_a.py").write_text(MARKED["network-import"][1])
    for command in (
        "python3 -m unittest discover -s tests  # no effects, safe to run",
        "GAIA_EFFECT_FREE=1 python3 -m unittest discover -s tests",
        "python3 -m unittest -k not_network discover -s tests",
        "pytest --timeout=30 tests/  # pure tests",
    ):
        assert _judge(command, repo).is_mutative, command


def test_node_test_directory_is_judged_by_its_files(repo):
    (repo / "tests" / "a.test.js").write_text("const test = require('node:test');\ntest('a', () => {});\n")
    assert not _judge("node --test tests/", repo).is_mutative
    (repo / "tests" / "b.test.js").write_text("require('node:child_process').execSync('git push --force');\n")
    result = _judge("node --test tests/", repo)
    assert result.is_mutative and "b.test.js" in result.reason, result.reason


UNITTEST = "python3 -m unittest discover -s tests"


def test_unchanged_file_is_read_from_cache_and_edited_file_is_analysed_again(repo):
    from modules.security import effect_content

    assert not _judge(UNITTEST, repo).is_mutative
    target = repo / "tests" / "test_b.py"
    entry = effect_content.cache_entry_path(target.read_bytes())
    assert entry is not None and entry.is_file()

    cached_entry = json.loads(entry.read_text())
    cached_entry["facts"]["markers"] = [["network", 1, "only the cache says so"]]
    entry.write_text(json.dumps(cached_entry))
    cached = _judge(UNITTEST, repo)
    assert cached.is_mutative and "only the cache says so" in cached.reason, cached.reason

    target.write_text(target.read_text() + "\n# edited\n")
    assert not _judge(UNITTEST, repo).is_mutative

    (repo / "tests" / "conftest.py").write_text("import pytest\nimport socket\n")
    assert "conftest.py" in _judge(UNITTEST, repo).reason


def test_no_tool_can_write_a_cache_entry(repo):
    from modules.security import effect_content
    from modules.security.protected_paths import is_read_spool_path
    from modules.tools.bash_validator import BashValidator

    assert not _judge(UNITTEST, repo).is_mutative
    entry = effect_content.cache_entry_path((repo / "tests" / "test_a.py").read_bytes())
    assert entry.is_file() and is_read_spool_path(str(entry))
    payload = {"cwd": str(repo), "tool_use_id": "t-1", "session_id": "s-1", "agent_id": "aeffect01", "agent_type": "developer"}
    for command in (f"cp /dev/null {entry}", f"echo '{{}}' > {entry}", f"tee {entry.parent}/x.json"):
        verdict = BashValidator().validate(
            command, is_subagent=True, session_id="s-1", agent_type="developer", hook_payload=payload,
        )
        assert not verdict.allowed, command


@pytest.mark.parametrize(
    ("body", "command", "free"),
    [
        ("print(1)\n", "python3 run.py", True),
        ("import subprocess\nsubprocess.run(['git', 'status'])\n", "python3 run.py", True),
        ("import requests\nrequests.post('https://x', data='a')\n", "python3 run.py", False),
        ("print(1)\n", "git status", True),
        ("print(1)\n", "ls tests", True),
    ],
)
def test_script_runs_keep_their_body_verdict(body, command, free, repo):
    (repo / "run.py").write_text(body)
    assert _judge(command, repo).is_mutative is not free
