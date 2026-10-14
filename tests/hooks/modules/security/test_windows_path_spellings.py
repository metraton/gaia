"""Every Windows spelling of a guarded path gets the verdict of its POSIX twin.

Windows opens one file under many spellings: a Git Bash drive (``/c/...``),
backslashes, any letter case, an alternate data stream, trailing dots and
spaces, a long-path or local admin-share prefix and an 8.3 short name. Each
probe here is Windows-shaped input judged on Linux, and asserts that the
spelling and its canonical twin are both refused, or both free. HOME is a
Windows profile so Git Bash paths under it reach the home entries.
"""

from __future__ import annotations

import ntpath
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import gaia_db_write_guard, protected_path_guard  # noqa: E402
from modules.security import sensitive_read_guard  # noqa: E402
from modules.security.protected_paths import is_protected_hook_path  # noqa: E402
from modules.security.sensitive_paths import is_account_path  # noqa: E402


@pytest.fixture(autouse=True)
def windows_home(monkeypatch):
    monkeypatch.setenv("HOME", "C:/Users/x")


def bash_read_refused(command: str) -> bool:
    return not sensitive_read_guard.check(command, "/repo")[0]


def read_tool_refused(path: str) -> bool:
    return sensitive_read_guard.check_file_tool("Read", {"file_path": path}, "/repo") is not None


SECRET_SPELLINGS = [
    ("/repo/.ENV", "/repo/.env"),
    ("/repo/.Env.local", "/repo/.env.local"),
    ("/work/ID_RSA", "/work/id_rsa"),
    ("/work/ID_ED25519", "/work/id_ed25519"),
    ("/srv/u/.SSH/config", "/srv/u/.ssh/config"),
    ("/repo/.env::$DATA", "/repo/.env"),
    ("/repo/.env.", "/repo/.env"),
    ("C:/repo/.env. ", "/repo/.env"),
    (r"\\?\C:\repo\.env", "/repo/.env"),
    (r"\\localhost\C$\repo\.env", "/repo/.env"),
    (r"\\127.0.0.1\c$\repo\.env", "/repo/.env"),
    (r"C:\Users\x\SSH~1\config", "/srv/u/.ssh/config"),
    (r"C:\Users\x\.ssh\ID_ED2~1", "/srv/u/.ssh/id_ed25519"),
    ("/srv/u/ENV~1", "/repo/.env"),
    ("/c/Users/x/.config/gh/hosts.yml", "~/.config/gh/hosts.yml"),
    ("/c/Users/x/.ssh/id_rsa", "~/.ssh/id_rsa"),
    (r"C:\Users\x\AppData\Roaming\GitHub CLI\hosts.yml", "~/.config/gh/hosts.yml"),
    (r"%APPDATA%\gcloud\credentials.db", "~/.config/gcloud/credentials.db"),
    ("$env:APPDATA/gnupg/pubring.kbx", "~/.gnupg/pubring.kbx"),
]


@pytest.mark.parametrize("spelling,twin", SECRET_SPELLINGS)
def test_read_tool_refuses_each_spelling_like_its_twin(spelling, twin):
    assert read_tool_refused(spelling) == read_tool_refused(twin) is True


@pytest.mark.parametrize("spelling,twin", SECRET_SPELLINGS)
def test_bash_refuses_each_spelling_like_its_twin(spelling, twin):
    assert bash_read_refused(f"cat '{spelling}'") == bash_read_refused(f"cat '{twin}'") is True


@pytest.mark.parametrize("command", [
    "type .env", "gc .env", "Get-Content -Path .env", "findstr /i token .env",
    "certutil -encode .env out.b64", "TYPE.EXE .env", "Select-String token .env",
    "cmd /c type .env", 'powershell -Command "Get-Content .env"',
])
def test_windows_readers_are_refused_like_cat(command):
    assert bash_read_refused(command) == bash_read_refused("cat .env") is True


@pytest.mark.parametrize("command", [
    "type README.md", "Get-Content -Path src/app.py", "findstr TODO src/main.py",
    "certutil -hashfile setup.exe SHA256", "type .env.example", "gci C:/repo/src",
    "cat /c/Users/x/projects/app/README.md",
])
def test_ordinary_windows_reads_stay_free(command):
    assert bash_read_refused(command) is False


@pytest.mark.parametrize("path", [
    r"C:\repo\.env.example", r"C:\repo\src\app.py", r"C:\PROGRA~1\Git\etc\gitconfig",
    "/c/Users/x/projects/notes.md",
    r"C:\Users\x\Documents\PowerShell\Microsoft.PowerShell_profile.ps1",
])
def test_ordinary_windows_read_tool_paths_stay_free(path):
    assert read_tool_refused(path) is False


@pytest.mark.parametrize("spelling,twin", [
    (r"C:\Users\x\Documents\PowerShell\Microsoft.PowerShell_profile.ps1", "~/.bashrc"),
    (r"%USERPROFILE%\Documents\WindowsPowerShell\profile.ps1", "~/.bashrc"),
    (r"C:\Users\x\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\run.lnk",
     "~/.profile"),
    ("/c/Users/x/.SSH/authorized_keys", "~/.ssh/authorized_keys"),
    (r"%APPDATA%\GitHub CLI\hosts.yml", "~/.config/gh/hosts.yml"),
])
def test_windows_account_paths_ask_a_signature_like_their_twin(spelling, twin):
    assert is_account_path(spelling) == is_account_path(twin) is True


@pytest.mark.parametrize("command,twin", [
    ("cp.exe x .CLAUDE/hooks/pre_tool_use.py", "cp x .claude/hooks/pre_tool_use.py"),
    ("Copy-Item x .claude/Settings.json", "cp x .claude/settings.json"),
    (r"Copy-Item -Path x -Destination 'C:\repo\.claude\hooks\a.py'", "cp x .claude/hooks/a.py"),
    ("cp x '.claude/settings.json::$DATA'", "cp x .claude/settings.json"),
    ("cp x '.claude/settings.json.'", "cp x .claude/settings.json"),
    (r"cp x 'C:\repo\CLAUDE~1\settings.json'", "cp x .claude/settings.json"),
    (r"cp x '\\?\C:\repo\.claude\hooks\a.py'", "cp x .claude/hooks/a.py"),
])
def test_bash_protected_writes_are_refused_like_their_twin(command, twin):
    assert protected_path_guard.check(command)[0] == protected_path_guard.check(twin)[0] is False


@pytest.mark.parametrize("spelling", [
    r"C:\repo\.CLAUDE\hooks\x.py", "/repo/.claude/Settings.JSON",
    "/repo/.claude/settings.json::$DATA", "/repo/.claude/hooks/x.py. ",
])
def test_write_tool_protects_each_spelling_like_its_twin(spelling):
    assert is_protected_hook_path(spelling) == is_protected_hook_path("/repo/.claude/hooks/x.py") is True


@pytest.mark.parametrize("command", ["cp x C:/repo/docs/a.md", "Copy-Item a b"])
def test_ordinary_windows_copies_stay_free_of_the_protected_guard(command):
    assert protected_path_guard.check(command)[0] is True


@pytest.mark.parametrize("path", [r"C:\repo\.claude\hooks\README.md", r"C:\repo\src\hooks\x.py"])
def test_unprotected_windows_paths_stay_writable(path):
    assert is_protected_hook_path(path) is False


@pytest.mark.parametrize("command,twin", [
    ("cp x /srv/.gaia/GAIA.DB", "cp x /srv/.gaia/gaia.db"),
    (r"cp.exe x 'C:\Users\x\.gaia\gaia.db'", "cp x /srv/.gaia/gaia.db"),
    (r"Copy-Item x '\\?\C:\Users\x\.gaia\gaia.db::$DATA'", "cp x /srv/.gaia/gaia.db"),
    ("tee GAIA.DB", "tee gaia.db"),
    ("Set-Content -Path /c/Users/x/.gaia/Gaia.Db -Value x", "truncate -s 0 /srv/.gaia/gaia.db"),
])
def test_db_writes_are_refused_like_their_twin(command, twin):
    assert gaia_db_write_guard.check(command)[0] == gaia_db_write_guard.check(twin)[0] is False


@pytest.mark.parametrize("command", [
    "cp /srv/.gaia/GAIA.DB /tmp/backup.db", "Copy-Item C:/Users/x/.gaia/gaia.db C:/tmp/b.db",
])
def test_db_reads_stay_free(command):
    assert gaia_db_write_guard.check(command)[0] is True


def test_r10_refuted_ntpath_does_not_resolve_git_bash_paths():
    """R10 ("Git Bash paths are resolved by ntpath") is false.

    ntpath reads ``/c/Users`` as a folder named ``c`` at the root of the
    current drive, so a guard resolving it lands on the wrong file.
    """
    from modules.security.canonical_path import git_bash_to_windows

    assert ntpath.splitdrive("/c/Users/x/.ssh/id_rsa")[0] == ""
    assert ntpath.normpath("/c/Users/x/.ssh/id_rsa") == "\\c\\Users\\x\\.ssh\\id_rsa"
    assert ntpath.splitdrive(git_bash_to_windows("/c/Users/x/.ssh/id_rsa"))[0] == "C:"


def test_native_path_drops_what_windows_drops_only_on_windows():
    from modules.security.canonical_path import native_path

    assert native_path("/c/Users/x/repo/a.txt::$DATA", windows=True) == "C:/Users/x/repo/a.txt"
    assert native_path(r"\\?\C:\repo\notes. ", windows=True) == "C:/repo/notes"
    assert native_path("/c/repo/a.txt::$DATA", windows=False) == "/c/repo/a.txt::$DATA"
