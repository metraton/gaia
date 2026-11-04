"""pytest's own options are not inline code, and what they load is judged against the repository being tested."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

REPO_FILES = {
    "tests/test_a.py": "def test_a():\n    assert True\n",
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["hooks"]\n',
    "points_out.toml": '[tool.pytest.ini_options]\npythonpath = ["../outside"]\n',
    "addopts_out.ini": "[pytest]\naddopts = --confcutdir=/\n",
    "cache_out.ini": "[pytest]\ncache_dir = ../outside/c\n",
    "bt_out.ini": "[pytest]\naddopts = --basetemp=/etc/x\n",
    "bt_rel.ini": "[pytest]\naddopts = --basetemp=../outside/bt\n",
    "bt_in_repo.ini": "[pytest]\naddopts = --basetemp=.bt\n",
    "bt_temp.ini": "[pytest]\naddopts = --basetemp={systmp}/bt\n",
    "plug_out.ini": "[pytest]\naddopts = -p evil\npythonpath = ../outside\n",
    "hooks/helper.py": "VALUE = 1\n",
}
OUTSIDE_FILES = {
    "pytest.ini": "[pytest]\npythonpath = .\n",
    "evil.py": "import shutil\nshutil.rmtree('build')\n",
    "test_x.py": "def test_x():\n    assert True\n",
}

# The verifier's probe set (handoff 36818): (command, runs free).
PROBES = [
    ("pytest -c ../outside/pytest.ini tests/", False),
    ("python3 -m pytest -c ../x tests/", False),
    ("PYTHONPATH={outside} pytest -p evil.sub tests/", False),
    ("python3 -m pytest -o pythonpath={outside} -p evil.sub tests/", False),
    ("python3 -m pytest -o pythonpath=../outside -p evil tests/", False),
    ("pytest -o pythonpath=/ tests/", False),
    ("pytest --rootdir=/ tests/", False),
    ("python3 -m pytest --rootdir=/ tests/", False),
    ("pytest --rootdir=.. tests/", False),
    ("pytest -c bt_out.ini tests/", False),
    ("python3 -m pytest -c bt_out.ini tests/", False),
    ("pytest -c bt_rel.ini tests/", False),
    ("pytest -c bt_in_repo.ini tests/", False),
    ("pytest -c bt_temp.ini tests/", True),
    ("pytest -c plug_out.ini tests/", False),
    ("pytest -o addopts=--basetemp=/etc/x tests/", False),
    ("pytest -o addopts='--basetemp={outside}/b' tests/", False),
    ("python3 -m pytest -o addopts=--junitxml=/etc/r tests/", False),
    ("pytest -o addopts='--basetemp={systmp}/b' tests/", True),
    ("pytest --basetemp=../outside/b tests/", False),
    ("pytest --basetemp={systmp}/../outside/b tests/", False),
    ("pytest --basetemp=/tmp tests/", False),
    ("pytest --basetemp=. tests/", False),
    ("pytest --basetemp={systmp}/ok tests/", True),
    ("pytest -k x --basetemp={outside} tests/", False),
    ("pytest --basetemp {outside} -k x tests/", False),
    ("pytest --confcutdir=.. tests/", False),
    ("pytest --junitxml=../r.xml tests/", False),
    ("pytest -o cache_dir=../c tests/", False),
    ("PYTHONPATH={outside} pytest --pdbcls=evil:C tests/", False),
    ("PYTEST_PLUGINS=evil PYTHONPATH={outside} pytest tests/", False),
    ("PYTEST_PLUGINS=../outside/evil.py pytest tests/", False),
    ("PYTEST_ADDOPTS=--basetemp=/etc/x pytest tests/", False),
    ("PYTEST_ADDOPTS=--junitxml={outside}/r pytest tests/", False),
    ("PYTEST_ADDOPTS='-p no:cacheprovider -q' pytest tests/", True),
    ("export PYTEST_ADDOPTS=--basetemp=/etc/x && pytest tests/", False),
    ("pytest {outside}/test_x.py", False),
    ("pytest ../outside/test_x.py", False),
    ("pytest tests/ --rootdir . -p no:cacheprovider -c pyproject.toml -k a", True),
    ("python3 -m pytest -c pyproject.toml -p no:cacheprovider -k 'a' tests/", True),
    ("PYTHONPATH={repo}/tests pytest tests/", True),
    ("PYTHONPATH={repo}/../outside pytest tests/", False),
    ("PYTHONPATH={repo}/tests:/ pytest tests/", False),
    ("PYTHONPATH=../outside python3 -m pytest tests/", False),
]

FREE = {
    "config-in-repo": "python3 -m pytest -c {repo}/pyproject.toml tests/test_a.py",
    "rootdir-in-repo": "python3 -m pytest -c {repo}/pyproject.toml --rootdir {repo} tests/",
    "plugin-disabled": "python3 -m pytest -c {repo}/pyproject.toml -p no:cacheprovider tests/",
    "keyword-expression": "python3 -m pytest -c {repo}/pyproject.toml -k 'delete or not test_a.py' tests/",
    "all-four": "python3 -m pytest -c {repo}/pyproject.toml --rootdir={repo} -p no:cacheprovider -k 'delete' -q tests/",
    "pythonpath-in-repo": "PYTHONPATH={repo}/hooks python3 -m pytest -q tests/",
    "env-pythonpath-in-repo": "env PYTHONPATH={repo}:{repo}/hooks python3 -m pytest -c pyproject.toml tests/",
    "bare-pytest-same-options": "pytest -c {repo}/pyproject.toml --rootdir {repo} -p no:cacheprovider -k 'delete' tests/",
    "outputs-in-repo": "pytest --junitxml={repo}/r.xml --log-file {repo}/log.txt --cache-clear tests/",
    "outputs-in-temp-dir": "python3 -m pytest --basetemp {systmp}/bt --junit-xml={systmp}/r.xml -o cache_dir={systmp}/c tests/",
    "bare-pytest-pythonpath-in-repo": "PYTHONPATH={repo}/hooks pytest -q tests/",
}

ASKS = {
    "config-outside": "python3 -m pytest -c {outside}/pytest.ini tests/",
    "config-file-outside": "python3 -m pytest --config-file={outside}/pytest.ini tests/",
    "config-in-repo-points-out": "python3 -m pytest -c points_out.toml tests/",
    "config-addopts-points-out": "python3 -m pytest -c addopts_out.ini tests/",
    "bare-pytest-config-outside": "pytest -c {outside}/pytest.ini tests/",
    "plugin-on-outside-path": "python3 -m pytest -o pythonpath={outside} -p evil tests/",
    "plugin-not-a-module-name": "python3 -m pytest -c pyproject.toml -p {outside}/evil.py tests/",
    "rootdir-outside": "python3 -m pytest --rootdir {outside} tests/",
    "confcutdir-outside": "python3 -m pytest --confcutdir / tests/",
    "pythonpath-outside": "PYTHONPATH={outside} python3 -m pytest tests/",
    "pythonpath-partly-outside": "PYTHONPATH={repo}/hooks:{outside} python3 -m pytest tests/",
    "pythonpath-expansion": "PYTHONPATH='{repo}/$X' python3 -m pytest tests/",
    "pythonpath-in-repo-not-pytest": "PYTHONPATH={repo}/hooks python3 -c 'import json'",
    "inline-code-before-module": "python3 -c 'import shutil; shutil.rmtree(\"x\")' -m pytest",
    "basetemp-outside": "python3 -m pytest --basetemp={outside}/bt tests/",
    "bare-basetemp-outside": "pytest --basetemp {outside}/bt tests/",
    "basetemp-is-temp-dir": "pytest --basetemp={systmp} tests/",
    "basetemp-is-repo": "pytest --basetemp={repo} tests/",
    "basetemp-inside-repo": "python3 -m pytest --basetemp={repo}/.bt tests/",
    "bare-basetemp-inside-repo": "pytest --basetemp tests/.bt tests/",
    "junitxml-outside": "pytest --junitxml={outside}/r.xml tests/",
    "junit-xml-outside": "python3 -m pytest --junit-xml {outside}/r.xml tests/",
    "log-file-outside": "pytest --log-file={outside}/log.txt tests/",
    "resultlog-outside": "pytest --resultlog={outside}/r.log tests/",
    "cache-dir-outside": "python3 -m pytest -o cache_dir={outside}/c --cache-clear tests/",
    "config-cache-dir-outside": "pytest -c cache_out.ini --cache-clear tests/",
    "bare-pytest-pythonpath-outside": "PYTHONPATH={outside} pytest tests/",
}


@pytest.fixture
def layout(tmp_path, monkeypatch):
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    for root, files in ((repo, REPO_FILES), (outside, OUTSIDE_FILES)):
        for name, body in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(_fill(body, repo, outside), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (tmp_path / "systmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "systmp"))
    monkeypatch.chdir(repo)
    return repo, outside


def _fill(template: str, repo: Path, outside: Path) -> str:
    places = {"repo": repo, "outside": outside, "systmp": repo.parent / "systmp"}
    return re.sub(r"\{(repo|outside|systmp)\}", lambda m: str(places[m.group(1)]), template)


def _validate(command: str, repo: Path):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(repo), "tool_use_id": "t-1", "session_id": "s-1", "agent_id": "apytest01", "agent_type": "developer"}
    return BashValidator().validate(
        command, is_subagent=True, session_id="s-1", agent_type="developer", hook_payload=payload,
    )


@pytest.mark.parametrize("case", sorted(FREE))
def test_pytest_options_inside_the_repo_run_free(case, layout):
    repo, outside = layout
    result = _validate(_fill(FREE[case], repo, outside), repo)
    assert result.allowed, result.rule


@pytest.mark.parametrize(("command", "free"), PROBES)
def test_verifier_probes(command, free, layout):
    repo, outside = layout
    result = _validate(_fill(command, repo, outside), repo)
    assert bool(result.allowed) is free, result.rule


@pytest.mark.parametrize("case", sorted(ASKS))
def test_pytest_loading_code_outside_the_repo_asks(case, layout):
    repo, outside = layout
    detect_mutative_command.cache_clear()
    assert detect_mutative_command(_fill(ASKS[case], repo, outside), cwd=str(repo)).is_mutative
