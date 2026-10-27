"""A local module committed unmodified in the script's repository is vouched for; untracked or modified code still asks."""

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

COMMITTED = {
    "app/main.py": "import helper\nprint(helper)\n",
    "app/helper.py": "import shutil\nshutil.rmtree('build')\n",
    "app/tool.py": (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "import pkg\nprint(pkg)\n"
    ),
    "pkg/__init__.py": "import shutil\nshutil.rmtree('build')\n",
    "web/main.js": "require('./lib.js')\nconsole.log(1)\n",
    "web/lib.js": "require('fs').rmSync('build', {recursive: true})\n",
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


@pytest.mark.parametrize("command", [
    "python3 {repo}/app/main.py",
    "python3 {repo}/app/tool.py",
    "node {repo}/web/main.js",
])
def test_committed_unmodified_local_code_is_not_chased(command, repo):
    assert _asks(command, repo) is False


@pytest.mark.parametrize("modified, command", [
    ("app/helper.py", "python3 {repo}/app/main.py"),
    ("pkg/__init__.py", "python3 {repo}/app/tool.py"),
    ("web/lib.js", "node {repo}/web/main.js"),
])
def test_a_modified_tracked_module_asks(modified, command, repo):
    (repo / modified).write_text((repo / modified).read_text() + "print(2)\n")
    assert _asks(command, repo) is True


def test_a_staged_modification_asks(repo):
    (repo / "app/helper.py").write_text("import shutil\nshutil.rmtree('other')\n")
    _git(repo, "add", "app/helper.py")
    assert _asks("python3 {repo}/app/main.py", repo) is True


@pytest.mark.parametrize("untracked, body, command", [
    ("app/evil.py", "import shutil\nshutil.rmtree('build')\n", "python3 {repo}/app/uses_evil.py"),
    ("app/evil.py", "import shutil\nshutil.rmtree('build')\n", "python3 {repo}/app/main.py"),
    ("pkg/evil.py", "import shutil\nshutil.rmtree('build')\n", "python3 {repo}/app/tool.py"),
    ("web/evil.js", "require('fs').rmSync('build', {recursive: true})\n", "node {repo}/web/uses_evil.js"),
])
def test_untracked_local_code_asks(untracked, body, command, repo):
    (repo / untracked).write_text(body)
    (repo / "app/uses_evil.py").write_text("import evil\n")
    (repo / "web/uses_evil.js").write_text("require('./evil.js')\n")
    assert _asks(command, repo) is True


@pytest.mark.parametrize("command, asks", [
    ("python3 {repo}/bin/cli/contract.py proposals list --json", False),
    ("python3 {repo}/bin/cli/contract.py proposals accept --help", False),
    ("python3 {repo}/bin/cli/approvals.py approve P-1", True),
])
def test_a_committed_gaia_cli_group_script_classifies_as_its_gaia_command(command, asks):
    assert _asks(command, _REPO_ROOT) is asks


def test_an_uncommitted_cli_group_script_is_judged_by_its_content(repo):
    script = repo / "bin/cli/contract.py"
    script.parent.mkdir(parents=True)
    script.write_text("import shutil\nshutil.rmtree('build')\n")
    assert _asks("python3 {repo}/bin/cli/contract.py proposals list --json", repo) is True


def test_outside_any_repository_keeps_reading_the_module(tmp_path):
    (tmp_path / "helper.py").write_text("import shutil\nshutil.rmtree('build')\n")
    (tmp_path / "main.py").write_text("import helper\n")
    assert detect_mutative_command(f"python3 {tmp_path}/main.py", cwd=str(tmp_path)).is_mutative is True
