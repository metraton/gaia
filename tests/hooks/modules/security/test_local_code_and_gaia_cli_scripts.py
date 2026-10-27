"""Local modules are judged by content whether committed or not; only Gaia's own CLI scripts are judged as their gaia command."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

RMTREE = "import shutil\nshutil.rmtree('build')\n"
COMMITTED = {
    "app/main.py": "import helper\nprint(helper)\n",
    "app/helper.py": RMTREE,
    "app/clean.py": "import quiet\nprint(quiet.X)\n",
    "app/quiet.py": "X = 1\n",
    "web/main.js": "require('./lib.js')\nconsole.log(1)\n",
    "web/lib.js": "require('fs').rmSync('build', {recursive: true})\n",
    "bin/cli/contract.py": RMTREE,
    "bin/gaia": '"""gaia -- Unified Gaia CLI"""\nimport shutil\ndef _discover_plugins():\n    shutil.rmtree("build")\n',
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for name, body in COMMITTED.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _asks(command: str, repo: Path) -> bool:
    return detect_mutative_command(command.format(repo=repo), cwd=str(repo)).is_mutative


@pytest.mark.parametrize("command, asks", [
    ("python3 {repo}/app/main.py", True),
    ("node {repo}/web/main.js", True),
    ("python3 {repo}/app/clean.py", False),
])
def test_a_committed_local_module_is_judged_by_its_content(command, asks, repo):
    assert _asks(command, repo) is asks


@pytest.mark.parametrize("shadow", ["__pycache__/quiet.cpython-311.pyc", "quiet.pyc", "quiet.pyd"])
def test_a_compiled_file_beside_a_read_module_asks(shadow, repo):
    path = repo / "app" / shadow
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"\x00")
    assert _asks("python3 {repo}/app/clean.py", repo) is True


@pytest.mark.parametrize("command, asks", [
    ("python3 {repo}/bin/cli/contract.py proposals list --json", False),
    ("python3 {repo}/bin/cli/contract.py proposals accept --help", False),
    ("python3 {repo}/bin/cli/approvals.py approve P-1", True),
    ("python3 {repo}/bin/gaia contract list", False),
    ("python3 {repo}/bin/gaia dev", True),
])
def test_gaia_own_cli_scripts_classify_as_their_gaia_command(command, asks):
    assert _asks(command, _REPO_ROOT) is asks


@pytest.mark.parametrize("command", [
    "python3 {repo}/bin/cli/contract.py proposals list --json",
    "cd {repo} && python3 bin/cli/contract.py list",
    "python3 {repo}/bin/gaia contract list",
])
def test_a_committed_cli_script_outside_gaia_source_is_judged_by_its_content(command, repo):
    assert _asks(command, repo) is True
