"""A Windows spelling of a command gets the verdict of its POSIX twin.

Windows finds a program by its name with any directory, any letter case and any
PATHEXT suffix, and ``py`` launches Python. Each row pairs such a spelling with
the POSIX command it runs and compares the blocked, mutative and tier verdicts.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_commands import is_blocked_command
from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# (windows_spelling, posix_twin, expected_tier)
TWINS = [
    ("rm.exe -rf /", "rm -rf /", T3),
    ("RM.EXE -rf /", "rm -rf /", T3),
    ("GH.EXE repo delete metraton/gaia", "gh repo delete metraton/gaia", T3),
    ("C:/tools/git.exe push --force", "git push --force", T3),
    ("C:\\tools\\git.exe push --force", "git push --force", T3),
    ("kubectl.exe delete ns prod", "kubectl delete ns prod", T3),
    ("py deploy.py", "python3 deploy.py", T3),
    ("py -3 deploy.py", "python3 deploy.py", T3),
    ("python.exe deploy.py", "python3 deploy.py", T3),
    ("C:\\Python312\\python.exe deploy.py", "python3 deploy.py", T3),
    ("py -c \"import shutil; shutil.rmtree('/srv')\"", "python3 -c \"import shutil; shutil.rmtree('/srv')\"", T3),
    ("node.exe -e \"require('fs').rmSync('/srv', {recursive: true})\"",
     "node -e \"require('fs').rmSync('/srv', {recursive: true})\"", T3),
    ("cmd /c del C:\\data\\x.txt", "del C:\\data\\x.txt", T3),
    ("cmd.exe /c rd /s /q C:\\data", "rd /s /q C:\\data", T3),
    ("C:\\Windows\\System32\\cmd.exe /c del x.txt", "del x.txt", T3),
    ("start /b cmd /c del x.txt", "del x.txt", T3),
    ("start \"\" /wait cmd.exe /c del x.txt", "del x.txt", T3),
    ("cmd /c \"del x.txt\"", "del x.txt", T3),
    ("\"C:\\Program Files\\Git\\bin\\git.exe\" push --force", "git push --force", T3),
    ("py -3.12-64 deploy.py", "python3 deploy.py", T3),
]

# Read-only twins: the respelling must not turn a free command into a gated one.
FREE_TWINS = [
    ("git.exe status", "git status", T0),
    ("C:\\tools\\GIT.EXE status", "git status", T0),
    ("py -c \"print(1)\"", "python3 -c \"print(1)\"", T0),
    ("cmd /c dir", "dir", T0),
]


def _verdict(command):
    return (
        is_blocked_command(command).is_blocked,
        detect_mutative_command(command).is_mutative,
        classify_command_tier(command),
    )


@pytest.fixture
def deploy_script(tmp_path, monkeypatch):
    (tmp_path / "deploy.py").write_text("import shutil\nshutil.rmtree('/srv/app')\n")
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("windows, posix, tier", TWINS + FREE_TWINS, ids=[row[0] for row in TWINS + FREE_TWINS])
def test_windows_spelling_matches_posix_twin(deploy_script, windows, posix, tier):
    assert classify_command_tier(posix) == tier, "the POSIX twin moved; the row no longer anchors"
    assert _verdict(windows) == _verdict(posix)
