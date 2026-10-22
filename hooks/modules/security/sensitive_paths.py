"""The one list of sensitive paths every lane of both hosts consults.

A path is sensitive when its CONTENTS are credentials or hand out access to
the user's accounts, and each entry says which of the two it is. Reading or
listing where secrets live is refused with the same reason through Bash, Read,
Glob, Grep and OpenCode's list; writing any ACCOUNT path, readable ones
included, asks for a signature through Bash, Write, Edit and apply_patch. The
exfiltration pipe rule in ``composition_rules`` reads this list too, so no lane
keeps a private copy that can drift from the others.

Matching is by path shape, never by the file existing: a credential directory
or file name is recognized wherever it sits, and the account entries are the
ones meaningful only under ``$HOME``. Every path is matched in its
``canonical_path`` form, so a Windows spelling gets its POSIX twin's verdict.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import re
import shlex
from typing import Iterable, List, Optional

from .canonical_path import canonical_path, is_absolute

# Directories whose whole content is credentials, wherever they sit.
CREDENTIAL_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".azure"})

# File names that are credentials wherever they sit.
CREDENTIAL_FILE_NAMES = frozenset({".netrc", ".pgpass", ".git-credentials", ".credentials.json"})

# Private-key stems; ``id_rsa.pub`` shares the stem and is kept with it.
PRIVATE_KEY_STEMS = frozenset({"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"})

CREDENTIAL_SUFFIXES = (".pem", ".key")

# Project secrets. The two templates stay readable: they are committed on
# purpose to show the shape without the values (the user's choice, matching
# OpenCode's own default).
ENV_FILE_NAME = ".env"
READABLE_ENV_TEMPLATES = frozenset({".env.example", ".env.sample"})

SYSTEM_SECRET_FILES = frozenset({"/etc/shadow", "/etc/passwd"})
SYSTEM_SECRET_DIRS = frozenset({"/etc/ssl/private"})

READ = "read"
WRITE = "write"
_BOTH = frozenset({READ, WRITE})
_WRITE_ONLY = frozenset({WRITE})

# Under $HOME, each entry marked by use (D22). READ: reading or listing it is
# refused, because secrets live there. WRITE: writing it asks a signature,
# because it decides who may log in, runs on the next shell or git invocation,
# or names the program that hands out credentials. Shell start-up files and git
# config carry no secrets and stay readable for diagnosis; ~/.docker is
# write-guarded as a whole but only its config.json holds registry tokens. An
# entry matches itself and everything below it.
HOME_ENTRIES = {
    ".ssh": _BOTH, ".gnupg": _BOTH, ".aws": _BOTH, ".azure": _BOTH,
    ".kube": _BOTH, ".config/gcloud": _BOTH, ".config/gh": _BOTH,
    ".docker": _WRITE_ONLY, ".docker/config.json": _BOTH,
    ".netrc": _BOTH, ".git-credentials": _BOTH, ".npmrc": _BOTH, ".pgpass": _BOTH,
    ".config/git": _WRITE_ONLY, ".gitconfig": _WRITE_ONLY,
    ".bashrc": _WRITE_ONLY, ".bash_profile": _WRITE_ONLY, ".bash_login": _WRITE_ONLY,
    ".profile": _WRITE_ONLY, ".zshrc": _WRITE_ONLY, ".zprofile": _WRITE_ONLY,
    ".zshenv": _WRITE_ONLY,
}

# Coding-agent CLI token and API-key files, each with the source naming the path;
# a CLI that keeps them only in the OS keychain has no file to refuse.
CODING_CLI_CREDENTIAL_STORES = {
    ".claude/.credentials.json": "https://code.claude.com/docs/en/iam#credential-management",
    ".claude.json": "https://code.claude.com/docs/en/settings (MCP server configurations, whose env and headers carry tokens)",
    ".config/anthropic": "https://code.claude.com/docs/en/iam#anthropic-profiles-and-federation-credentials",
    ".codex/auth.json": "https://developers.openai.com/codex/auth",
    ".gemini/oauth_creds.json": "google-gemini/gemini-cli packages/core/src/config/storage.ts::OAUTH_FILE",
    ".gemini/mcp-oauth-tokens.json": "google-gemini/gemini-cli packages/core/src/config/storage.ts::getMcpOAuthTokensPath",
    ".qwen/oauth_creds.json": "QwenLM/qwen-code docs/users/qwen-serve.md",
    ".local/share/opencode/auth.json": "https://opencode.ai/docs/providers/#credentials",
    ".copilot/config.json": "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli",
    ".config/github-copilot/hosts.json": "https://aider.chat/docs/llms/github.html (oauth_token of the Copilot IDE plugins)",
    ".config/github-copilot/apps.json": "https://aider.chat/docs/llms/github.html (oauth_token of the Copilot IDE plugins)",
}
HOME_ENTRIES.update(dict.fromkeys(CODING_CLI_CREDENTIAL_STORES, _BOTH))

# The Windows homes of the same uses, marked like HOME_ENTRIES and matched
# anywhere in an absolute path: a profile may sit on any drive or under
# OneDrive, so $HOME does not locate it. The PowerShell folders and Startup
# run code at the next session start and stay readable.
PROFILE_ENTRIES = {
    "appdata/roaming/github cli": _BOTH, "appdata/roaming/gcloud": _BOTH,
    "appdata/roaming/gnupg": _BOTH,
    "documents/powershell": _WRITE_ONLY, "documents/windowspowershell": _WRITE_ONLY,
    "appdata/roaming/microsoft/windows/start menu/programs/startup": _WRITE_ONLY,
}

# The long names an 8.3 alias in a path may stand for. Env variants are listed
# because an alias keeps only the first three letters of the last extension.
_SHORT_NAMES = tuple(sorted(
    CREDENTIAL_DIR_NAMES | CREDENTIAL_FILE_NAMES | PRIVATE_KEY_STEMS | READABLE_ENV_TEMPLATES
    | {".env", ".env.local", ".env.development", ".env.production", ".env.staging",
       ".env.test", "credentials", "credentials.json", ".config", ".docker", ".kube",
       ".npmrc", "github cli", "start menu", "windowspowershell", "powershell",
       "documents", "microsoft"}
))

# Names a file-name glob is tested against: a glob that would select one of
# them selects a sensitive file. A sample sensitive by its stem alone is paired
# with an ordinary file of the same type; a glob selecting both filters by type
# (*.json) rather than naming the credential.
_SENSITIVE_NAME_SAMPLES = {
    ".env": None, ".env.local": None, "id_rsa": None, "id_ed25519": None,
    "server.pem": None, "server.key": None, "credentials": None,
    "credentials.json": "package.json", ".netrc": None, ".pgpass": None,
    ".git-credentials": None,
}

_GLOB_CHARS = re.compile(r"[*?\[]")
_TOKEN_SPLIT = re.compile(r"[\s=]+")
_TOKEN_LEADING = "@<>&|(\"'"
_TOKEN_TRAILING = ")\"';,"

# Unexpanded spellings of the home and the Windows profile folders, each with
# the folder it names below $HOME.
_HOME_SPELLINGS = (
    (re.compile(r"^(?:~|\$\{?home\}?|%userprofile%|\$env:userprofile)(?=[/\\]|$)", re.I), ""),
    (re.compile(r"^(?:%appdata%|\$env:appdata)(?=[/\\]|$)", re.I), "/AppData/Roaming"),
    (re.compile(r"^(?:%localappdata%|\$env:localappdata)(?=[/\\]|$)", re.I), "/AppData/Local"),
)


def expand_home(path: str) -> str:
    """Expand the spellings of $HOME and the Windows profile folders a command may carry unexpanded."""
    for spelling, below_home in _HOME_SPELLINGS:
        match = spelling.match(path)
        if match:
            home = os.path.expanduser("~").rstrip("/\\")
            return home + below_home + path[match.end():]
    return path


def _canonical(path: str) -> str:
    return canonical_path(expand_home(path.strip()), _SHORT_NAMES)


def _home_relative(candidate: str) -> str:
    """The part of a canonical absolute *candidate* under $HOME, or "" when it lands elsewhere."""
    if not is_absolute(candidate):
        return ""
    home = posixpath.normpath(canonical_path(os.path.expanduser("~")))
    norm = posixpath.normpath(candidate)
    if norm == home:
        return "."
    if not norm.startswith(home.rstrip("/") + "/"):
        return ""
    return norm[len(home.rstrip("/")) + 1:]


def _home_entry_marks(path: str) -> frozenset:
    """The uses the home and profile entries covering *path* guard; empty outside them."""
    candidate = _canonical(path)
    marks: frozenset = frozenset()
    if is_absolute(candidate):
        wrapped = posixpath.normpath(candidate) + "/"
        for entry, uses in PROFILE_ENTRIES.items():
            if "/" + entry + "/" in wrapped:
                marks = marks | uses
    relative = _home_relative(candidate)
    if not relative or relative == ".":
        return marks
    for entry, uses in HOME_ENTRIES.items():
        if relative == entry or relative.startswith(entry + "/"):
            marks = marks | uses
    return marks


def holds_credentials(path: str) -> bool:
    """True when *path* is $HOME or a directory above a read-refused entry, so copying it whole copies secrets."""
    relative = _home_relative(_canonical(path)) if path else ""
    if relative == ".":
        return True
    return bool(relative) and any(
        READ in uses and entry.startswith(relative.rstrip("/") + "/") for entry, uses in HOME_ENTRIES.items()
    )


def is_account_path(path: str) -> bool:
    """True when writing *path* hands out access to the user's own account.

    Sensitive by CONTENTS, not by location: the home directory is otherwise
    free, which is the reasoning that once left ``~/.ssh/authorized_keys``
    open. Relative paths are never account paths.
    """
    return WRITE in _home_entry_marks(path)


def _file_name_label(name: str) -> str:
    """The rule a single file name falls under, or ""."""
    if name == ENV_FILE_NAME or (
        name.startswith(ENV_FILE_NAME + ".") and name not in READABLE_ENV_TEMPLATES
    ):
        return "environment secrets file"
    if any(name == known or name.startswith(known + ".") for known in CREDENTIAL_FILE_NAMES):
        return "credentials file"
    if any(name == stem or name.startswith(stem + ".") for stem in PRIVATE_KEY_STEMS):
        return "private key"
    if name.endswith(CREDENTIAL_SUFFIXES) and len(name) > 4:
        return "key or certificate file"
    if name == "credentials" or name.startswith("credentials."):
        return "credentials file"
    return ""


def sensitive_path_label(path: str, cwd: Optional[str] = None) -> str:
    """Name why *path* is sensitive to read or list, or "" when it is not.

    *cwd* anchors a relative path so an account entry under $HOME is still
    recognized; without it a relative path is judged by its names alone.
    """
    if not path:
        return ""
    candidate = _canonical(path)
    if cwd and not is_absolute(candidate):
        candidate = canonical_path(cwd) + "/" + candidate
    if is_absolute(candidate):
        candidate = posixpath.normpath(candidate)
        if candidate in SYSTEM_SECRET_FILES:
            return "system credentials file"
        if any(candidate == d or candidate.startswith(d + "/") for d in SYSTEM_SECRET_DIRS):
            return "system private keys"
        if READ in _home_entry_marks(candidate):
            return "account credentials"
    parts = [part for part in candidate.split("/") if part]
    if any(part in CREDENTIAL_DIR_NAMES for part in parts):
        return "credentials directory"
    return _file_name_label(parts[-1]) if parts else ""


def glob_selects_sensitive_name(pattern: str) -> bool:
    """True when a file-name glob would select a sensitive file.

    A bare wildcard (``*``, ``**/*``) selects everything and names nothing, so
    it is left to the directory it runs in rather than refused on its own.
    """
    name = pattern.rstrip("/").rsplit("/", 1)[-1].lower()
    if not name or not re.search(r"[^*?/.]", name):
        return False
    return any(
        fnmatch.fnmatchcase(sample, name)
        and not (ordinary and fnmatch.fnmatchcase(ordinary, name))
        for sample, ordinary in _SENSITIVE_NAME_SAMPLES.items()
    )


def pattern_reaches_sensitive_path(pattern: str, base: str = "") -> str:
    """Name the sensitive place a path glob descends into or names, or "".

    Each literal segment of the pattern is judged as a place: ``**/.ssh/*`` and
    ``.aws/**`` descend into a credentials directory, ``config/.env`` names a
    secrets file. The literal prefix joined to *base* is judged too, so an
    account entry under $HOME is caught when the glob runs from there.
    """
    literal_prefix: List[str] = []
    prefix_open = True
    for segment in [part for part in pattern.split("/") if part]:
        if _GLOB_CHARS.search(segment):
            prefix_open = False
            continue
        label = sensitive_path_label(segment)
        if label:
            return label
        if prefix_open:
            literal_prefix.append(segment)
    if base or literal_prefix:
        joined = os.path.join(base, *literal_prefix) if base else "/".join(literal_prefix)
        return sensitive_path_label(joined)
    return ""


def _path_candidates(token: str) -> Iterable[str]:
    """The path-like pieces of one shell token (``--in=x``, ``@x``, ``<x``), the whole token first.

    The whole token is a candidate because a quoted path may hold a space
    (``GitHub CLI``) that the split would cut through.
    """
    for piece in (token, *_TOKEN_SPLIT.split(token)):
        piece = piece.lstrip(_TOKEN_LEADING).rstrip(_TOKEN_TRAILING)
        if piece and not piece.startswith("-"):
            yield piece


def first_sensitive_token(tokens: Iterable[str], cwd: Optional[str] = None) -> str:
    """The first token naming a sensitive path, or ""."""
    for token in tokens:
        for piece in _path_candidates(token):
            if sensitive_path_label(piece, cwd):
                return piece
    return ""


def command_names_sensitive_path(command: str) -> bool:
    """True when any word of *command* names a sensitive path."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return bool(first_sensitive_token(tokens))


def sensitive_read_denial(path: str, label: str) -> str:
    """The one reason every host and tool gives for refusing a sensitive read."""
    return (
        f"[SENSITIVE_PATH] Reading or listing '{path}' is refused ({label}): its "
        f"contents are credentials or account access. This block is not "
        f"approvable and no signature lifts it. Work from what the "
        f"file is for (a template such as .env.example, the tool that uses the "
        f"credential, or a question to the user) instead of its contents."
    )
