"""A test-suite run is free only when its files prove it has no effect; otherwise it signs naming the marker."""

from __future__ import annotations

import json
import os
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


@pytest.mark.parametrize(("command", "in_repo"), [SUITE_COMMANDS[0], SUITE_COMMANDS[2]], ids=[SUITE_IDS[0], SUITE_IDS[2]])
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
    assert effect_content.cache_entry_path(target.read_bytes()).is_file()

    parsed = []
    real_parse = effect_content._parse
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(effect_content, "_parse", lambda content: parsed.append(content) or real_parse(content))
        assert not _judge(UNITTEST, repo).is_mutative
        assert parsed == []
        target.write_text(target.read_text() + "\n# edited\n")
        assert not _judge(UNITTEST, repo).is_mutative
        assert parsed == [target.read_bytes()]

    (repo / "tests" / "conftest.py").write_text("import pytest\nimport socket\n")
    assert "conftest.py" in _judge(UNITTEST, repo).reason


@pytest.mark.parametrize("forgery", ["no-mac", "wrong-key", "facts-swapped"])
def test_a_forged_cache_entry_never_frees_a_marked_file(forgery, repo):
    import hashlib
    import hmac

    from modules.security import effect_content

    target = repo / "tests" / "test_a.py"
    target.write_text("import socket\n\ndef test_a():\n    assert True\n")
    assert _judge(UNITTEST, repo).is_mutative
    entry = effect_content.cache_entry_path(target.read_bytes())
    stored = json.loads(entry.read_text())
    empty = {"markers": [], "imports": [], "sites": [], "assigns": {}, "fixtures": {}}
    if forgery == "no-mac":
        stored = {"version": effect_content.MARKER_SET_VERSION, "facts": empty}
    elif forgery == "wrong-key":
        message = json.dumps(empty, sort_keys=True).encode()
        stored = {"version": effect_content.MARKER_SET_VERSION, "facts": empty,
                  "mac": hmac.new(b"0" * 32, message, hashlib.sha256).hexdigest()}
    else:
        stored["facts"] = empty
    entry.write_text(json.dumps(stored))
    result = _judge(UNITTEST, repo)
    assert result.is_mutative and "network" in result.reason, result.reason


def _forge_routes(cache: Path, key: Path, source: Path) -> dict:
    entry = cache / ("0" * 64 + ".json")
    rel_cache = os.path.relpath(cache, Path.home())
    return {
        "cp": f"cp /dev/null {entry}", "redirect": f"echo '{{}}' > {entry}", "tee": f"tee {cache}/x.json",
        "ln-symlink-entry": f"ln -s {source} {entry}", "ln-hardlink-entry": f"ln {source} {entry}",
        "mv-entry": f"mv {source} {entry}", "cp-r-dir": f"cp -r {source.parent} {cache}",
        "cp-into-dir-slash": f"cp {source} {cache}/", "rsync": f"rsync {source} {entry}",
        "install": f"install -m644 {source} {entry}", "dd-of": f"dd if={source} of={entry}",
        "tar-x-C": f"tar -xf {source.parent}/a.tar -C {cache}", "unzip-d": f"unzip {source.parent}/a.zip -d {cache}",
        "unzip-d-attached": f"unzip {source.parent}/a.zip -d{cache}", "unzip-key-dir": f"unzip a.zip -d {key.parent}",
        "tilde": f"cp {source} ~/{rel_cache}/h.json", "dollar-home": f"cp {source} $HOME/{rel_cache}/h.json",
        "sed-w": f"sed -n 'w {entry}' {source}", "key-overwrite": f"cp {source} {key}",
        "read-key": f"cat {key}", "read-key-python": f"python3 -c \"print(open('{key}', 'rb').read())\"",
    }


def test_no_tool_can_write_the_cache_or_read_its_key(repo, tmp_path):
    from modules.security import effect_content
    from modules.security.protected_paths import is_read_spool_path
    from modules.tools.bash_validator import BashValidator

    assert not _judge(UNITTEST, repo).is_mutative
    entry = effect_content.cache_entry_path((repo / "tests" / "test_a.py").read_bytes())
    key = effect_content.key_path()
    assert entry.is_file() and key.is_file() and is_read_spool_path(str(entry)) and is_read_spool_path(str(key))
    source = tmp_path / "x.json"
    source.write_text("{}")
    payload = {"cwd": str(tmp_path), "tool_use_id": "t-1", "session_id": "s-1", "agent_id": "aeffect01", "agent_type": "developer"}
    free = [name for name, command in _forge_routes(effect_content.cache_dir(), key, source).items()
            if BashValidator().validate(command, is_subagent=True, session_id="s-1", agent_type="developer",
                                        hook_payload=payload).allowed]
    assert not free, free


VERIFIER_MARKED = {
    "star-import-os-system": {"tests/test_a.py": "from os import *\n\ndef test_a():\n    system('true')\n"},
    "star-import-shutil-rmtree": {"tests/test_a.py": "from shutil import *\n\ndef test_a():\n    rmtree('/x')\n"},
    "module-alias-attr": {"tests/test_a.py": "import os as o\nf = o.popen\n\ndef test_a():\n    f('true')\n"},
    "class-attr-alias": {"tests/test_a.py": "import os\n\nclass K:\n    go = staticmethod(os.system)\n"},
    "reexport-star": {
        "tests/test_a.py": "from pkg import *\n\ndef test_a():\n    go()\n",
        "tests/pkg/__init__.py": "from .impl import *\n",
        "tests/pkg/impl.py": "import socket\n\ndef go():\n    socket.create_connection(('x', 1))\n",
    },
    "dynamic-importorskip": {"tests/test_a.py": "import pytest\ns = pytest.importorskip('socket')\n"},
    "importorskip-from-import": {"tests/test_a.py": "from pytest import importorskip as skip\ns = skip('socket')\n"},
    "spec-from-file": {"tests/test_a.py": (
        "import importlib.util\n\ndef test_a():\n"
        "    spec = importlib.util.spec_from_file_location('m', '/x/evil.py')\n"
        "    spec.loader.exec_module(importlib.util.module_from_spec(spec))\n")},
    "source-file-loader": {"tests/test_a.py": (
        "from importlib.machinery import SourceFileLoader\n\ndef test_a():\n    SourceFileLoader('m', '/x/e.py').load_module()\n")},
    "loader-through-attribute": {"tests/test_a.py": "def test_a(spec):\n    spec.loader.exec_module(spec)\n"},
    "pythonpath-shadows-installed": {
        "pytest.ini": "[pytest]\npythonpath = src\n",
        "src/pytest_shadowed_name/__init__.py": "import subprocess\n",
        "tests/test_a.py": "import pytest_shadowed_name\n",
    },
}


@pytest.mark.parametrize(("command", "in_repo"), SUITE_COMMANDS, ids=SUITE_IDS)
@pytest.mark.parametrize("case", sorted(VERIFIER_MARKED))
def test_verifier_probe_marked_closure_never_free(case, command, in_repo, repo):
    _place(repo, in_repo)
    for relative, body in VERIFIER_MARKED[case].items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(body)
    result = _judge(command, repo)
    assert result.is_mutative, f"{case} ran free: {result.reason}"


def test_pythonpath_shadowing_an_installed_module_is_resolved_locally(repo):
    _place(repo, False)
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = src\n")
    (repo / "src" / "json").mkdir(parents=True)
    (repo / "src" / "json" / "__init__.py").write_text("import subprocess\n")
    (repo / "tests" / "test_a.py").write_text("import json\n")
    result = _judge("python3 -m pytest -q tests/", repo)
    assert result.is_mutative and "src/json/__init__.py" in result.reason, result.reason


UNMODELLED = {
    "ini-addopts-plugin-outside-repo": (
        {"pytest.ini": "[pytest]\naddopts = -p evilplug\n", "evilplug.py": "import subprocess\n"},
        False, "python3 -m pytest -q -s tests/", ""),
    "ini-addopts-plugin-in-repo": (
        {"pytest.ini": "[pytest]\naddopts = -p evilplug\n", "evilplug.py": "import subprocess\n"},
        True, "python3 -m pytest --assert=plain -q -s tests/", ""),
    "pyargs-outside-repo": (
        {"mypkg/__init__.py": "", "mypkg/tests/__init__.py": "", "mypkg/tests/test_x.py": "import subprocess\n"},
        False, "python3 -m pytest -q -s --pyargs mypkg.tests", ""),
    "pyargs-in-repo": (
        {"mypkg/__init__.py": "", "mypkg/tests/__init__.py": "", "mypkg/tests/test_x.py": "import subprocess\n"},
        True, "python3 -m pytest -q -s --pyargs mypkg.tests", ""),
    "testpaths-outside-cwd": (
        {"proj/pytest.ini": "[pytest]\ntestpaths = ../sibling\n", "proj/tests/test_a.py": "def test_a():\n    pass\n",
         "sibling/test_x.py": "import subprocess\n"}, False, "python3 -m pytest -q -s", "proj"),
    "testpaths-matching-nothing": (
        {"pytest.ini": "[pytest]\ntestpaths = nowhere\n"}, False, "python3 -m pytest -q", ""),
    "ini-addopts-unparsed-option": (
        {"pytest.ini": "[pytest]\naddopts = --some-plugin-flag=x\n"}, False, "python3 -m pytest -q tests/", ""),
    "ini-key-not-modelled": ({"pytest.ini": "[pytest]\nasyncio_mode = auto\n"}, False, "python3 -m pytest -q tests/", ""),
    "confcutdir-argument": ({}, False, "python3 -m pytest --confcutdir=. -q tests/", ""),
    "rootdir-argument": ({}, False, "python3 -m pytest --rootdir=/ -q tests/", ""),
    "missing-argument": ({}, False, "python3 -m pytest -q tests/test_missing.py", ""),
    "no-test-file": ({"tests/test_a.py": None, "tests/test_b.py": None}, False, "python3 -m pytest -q tests/", ""),
    "unittest-no-test-file": ({"tests/test_a.py": None, "tests/test_b.py": None}, True, UNITTEST, ""),
    "unittest-missing-start": ({}, True, "python3 -m unittest discover -s nowhere", ""),
    "plain-option-not-modelled": ({}, True, "pytest --timeout=30 tests/", ""),
}


@pytest.mark.parametrize("case", sorted(UNMODELLED))
def test_what_the_analysis_does_not_model_never_runs_free(case, repo):
    files, in_repo, command, sub = UNMODELLED[case]
    _place(repo, in_repo)
    for relative, body in files.items():
        if body is None:
            (repo / relative).unlink()
            continue
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(body)
    result = _judge(command, repo / sub if sub else repo)
    assert result.is_mutative, f"{case} ran free: {result.reason}"


def test_conftest_above_the_working_directory_is_in_the_closure(repo):
    _place(repo, False)
    (repo / "conftest.py").write_text("import subprocess\n")
    (repo / "a" / "b").mkdir(parents=True)
    (repo / "a" / "b" / "test_x.py").write_text("def test_x():\n    assert True\n")
    result = _judge("python3 -m pytest -q", repo / "a" / "b")
    assert result.is_mutative and "conftest.py" in result.reason, result.reason


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
