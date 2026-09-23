"""One list of sensitive paths, read by every lane (plan 76 task 13, D21).

The list starts from the two sets that existed apart -- the exfiltration
patterns of ``composition_rules`` and the account paths of ``mutative_verbs``
-- and adds ``.env``/``.env.*`` (templates excepted), ``~/.azure``, ``~/.npmrc``
and ``~/.pgpass``. Every path here is a stand-in under a temporary HOME; no
real credential file is read, listed or named.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import composition_rules, mutative_verbs, sensitive_paths  # noqa: E402
from modules.security import sensitive_read_guard  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    return fake


# Each entry of the two original sets, and each addition.
FROM_COMPOSITION = [
    "~/.ssh/config", "/srv/other/.ssh/known_hosts", "~/.aws/config",
    "~/.gnupg/pubring.kbx", "/etc/shadow", "/etc/passwd", "/work/id_rsa",
    "/work/id_ed25519", "/work/id_ecdsa", "/work/id_dsa", "/work/server.pem",
    "/work/server.key", "/work/credentials", "~/.netrc", "~/.pgpass",
    "/etc/ssl/private/site.key",
]
FROM_ACCOUNT_PATHS = [
    "~/.ssh/authorized_keys", "~/.gnupg/gpg.conf", "~/.aws/credentials",
    "~/.kube/config", "~/.docker/config.json", "~/.config/gcloud/credentials.db",
    "~/.config/gh/hosts.yml", "~/.config/git/config", "~/.bashrc",
    "~/.bash_profile", "~/.bash_login", "~/.profile", "~/.zshrc", "~/.zprofile",
    "~/.zshenv", "~/.git-credentials", "~/.gitconfig",
]
ADDED = [
    "/repo/.env", "/repo/.env.local", "/repo/config/.env.production",
    "~/.azure/accessTokens.json", "~/.npmrc", "~/.pgpass",
]
NOT_SENSITIVE = [
    "/repo/.env.example", "/repo/.env.sample", "/repo/src/app.py",
    "~/.gaia/scratch/notes.txt", "~/.cache/build.log", "~/ws/other-repo/README.md",
    "/usr/share/doc/readme", "~/.config/nvim/init.lua", "/repo/.envrc",
]


@pytest.mark.parametrize("path", FROM_COMPOSITION + FROM_ACCOUNT_PATHS + ADDED)
def test_sensitive_path_guard_list_holds_both_sets_and_the_additions(home, path):
    assert sensitive_paths.sensitive_path_label(path), path


@pytest.mark.parametrize("path", NOT_SENSITIVE)
def test_sensitive_path_guard_list_leaves_ordinary_paths_free(home, path):
    assert sensitive_paths.sensitive_path_label(path) == "", path


@pytest.mark.parametrize("path", ["~/.azure/config", "~/.npmrc", "~/.pgpass"])
def test_sensitive_path_guard_list_additions_are_account_paths(home, path):
    assert sensitive_paths.is_account_path(path)
    assert mutative_verbs.is_account_sensitive_path(path)


def test_sensitive_path_guard_list_is_the_one_composition_and_mutative_verbs_read(home, monkeypatch):
    """Withdrawing an entry from the one list withdraws it from every lane."""
    assert not hasattr(composition_rules, "_SENSITIVE_PATH_PATTERNS")
    assert not hasattr(mutative_verbs, "ACCOUNT_SENSITIVE_HOME_FILES")
    assert composition_rules._is_sensitive_path("cat /repo/.env")
    assert mutative_verbs.is_account_sensitive_path("~/.npmrc")

    monkeypatch.setattr(sensitive_paths, "ENV_FILE_NAME", ".no-such-name")
    monkeypatch.setattr(sensitive_paths, "ACCOUNT_SENSITIVE_HOME_FILES", frozenset())
    assert not composition_rules._is_sensitive_path("cat /repo/.env")
    assert not mutative_verbs.is_account_sensitive_path("~/.npmrc")
    assert sensitive_read_guard.check("cat /repo/.env") == (True, None)


READS = [
    "cat ~/.ssh/config", "head -n 3 ~/.aws/credentials", "less /repo/.env",
    "tail ~/.npmrc", "ls ~/.ssh", "ls -la ~/.azure/", "grep token ~/.pgpass",
    "sudo cat /etc/shadow", "find ~/.gnupg -type f", "echo $(cat /repo/.env.local)",
    "bash -c 'cat ~/.kube/config'", "cd /repo && cat .env", "base64 /work/id_rsa",
]
FREE = [
    "cat /repo/.env.example", "ls ~", "ls -la /repo", "cat ~/ws/other-repo/README.md",
    "cat ~/.gaia/scratch/notes.txt", "find /repo -name .env", "grep -r TODO /repo/src",
    "echo ~/.ssh/config", "wc -l /repo/src/app.py",
]


@pytest.mark.parametrize("command", READS)
def test_sensitive_path_guard_bash_refuses_reading_or_listing(home, command):
    allowed, reason = sensitive_read_guard.check(command)
    assert allowed is False, command
    assert reason.startswith("[SENSITIVE_PATH]")


@pytest.mark.parametrize("command", FREE)
def test_sensitive_path_guard_bash_leaves_ordinary_reads_free(home, command):
    assert sensitive_read_guard.check(command) == (True, None), command


def test_sensitive_path_guard_sed_in_place_is_left_to_the_write_route(home):
    assert sensitive_read_guard.check("sed -i 's/a/b/' ~/.bashrc") == (True, None)
    assert sensitive_read_guard.check("sed -n 1p ~/.bashrc")[0] is False
