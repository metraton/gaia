"""The one list of sensitive paths every lane of both hosts consults.

A path is sensitive when its CONTENTS are credentials or hand out access to
the user's accounts. Reading or listing one is refused with the same reason
through Bash, Read, Glob, Grep and OpenCode's list; writing an ACCOUNT path
asks for a signature through Bash, Write, Edit and apply_patch alike. The
exfiltration pipe rule in ``composition_rules`` reads this list too, so no lane
keeps a private copy that can drift from the others.

Matching is by path shape, never by the file existing: a credential directory
or file name is recognized wherever it sits, and the account entries are the
ones meaningful only under ``$HOME``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from typing import Iterable, List, Optional

# Directories whose whole content is credentials, wherever they sit.
CREDENTIAL_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".azure"})

# File names that are credentials wherever they sit.
CREDENTIAL_FILE_NAMES = frozenset({".netrc", ".pgpass", ".git-credentials"})

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

# Under $HOME: what configures the user's accounts. These are both read- and
# write-sensitive -- they decide who may log in, run on the next shell or git
# invocation, name the program that hands out credentials, or are the
# credentials themselves.
ACCOUNT_SENSITIVE_HOME_PREFIXES = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker",
    ".config/gcloud", ".config/gh", ".config/git",
})
ACCOUNT_SENSITIVE_HOME_FILES = frozenset({
    ".bashrc", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zprofile", ".zshenv",
    ".netrc", ".git-credentials", ".gitconfig", ".npmrc", ".pgpass",
})

# Names a file-name glob is tested against: a glob that would select one of
# them selects a sensitive file.
_SENSITIVE_NAME_SAMPLES = (
    ".env", ".env.local", "id_rsa", "id_ed25519", "server.pem", "server.key",
    "credentials", "credentials.json", ".netrc", ".pgpass", ".git-credentials",
)

_GLOB_CHARS = re.compile(r"[*?\[]")
_TOKEN_SPLIT = re.compile(r"[\s=]+")
_TOKEN_LEADING = "@<>&|(\"'"
_TOKEN_TRAILING = ")\"';,"


def _expand_home(path: str) -> str:
    """Expand the three spellings of $HOME a command may carry unexpanded."""
    home = os.path.expanduser("~")
    if path == "~":
        return home
    for prefix in ("~/", "$HOME/", "${HOME}/"):
        if path.startswith(prefix):
            return os.path.join(home, path[len(prefix):])
    return path


def _home_relative(path: str) -> str:
    """The part of an absolute *path* under $HOME, or "" when it lands elsewhere."""
    if not os.path.isabs(path):
        return ""
    home = os.path.normpath(os.path.expanduser("~"))
    norm = os.path.normpath(path)
    if norm != home and not norm.startswith(home + os.sep):
        return ""
    return os.path.relpath(norm, home)


def is_account_path(path: str) -> bool:
    """True when writing *path* hands out access to the user's own account.

    Sensitive by CONTENTS, not by location: the home directory is otherwise
    free, which is the reasoning that once left ``~/.ssh/authorized_keys``
    open. Relative paths are never account paths.
    """
    relative = _home_relative(_expand_home(path))
    if not relative or relative == ".":
        return False
    if relative in ACCOUNT_SENSITIVE_HOME_FILES:
        return True
    return any(
        relative == prefix or relative.startswith(prefix + "/")
        for prefix in ACCOUNT_SENSITIVE_HOME_PREFIXES
    )


def _file_name_label(name: str) -> str:
    """The rule a single file name falls under, or ""."""
    if name == ENV_FILE_NAME or (
        name.startswith(ENV_FILE_NAME + ".") and name not in READABLE_ENV_TEMPLATES
    ):
        return "environment secrets file"
    if name in CREDENTIAL_FILE_NAMES:
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
    candidate = _expand_home(path.strip())
    if cwd and not os.path.isabs(candidate):
        candidate = os.path.join(cwd, candidate)
    if os.path.isabs(candidate):
        candidate = os.path.normpath(candidate)
        if candidate in SYSTEM_SECRET_FILES:
            return "system credentials file"
        if any(candidate == d or candidate.startswith(d + "/") for d in SYSTEM_SECRET_DIRS):
            return "system private keys"
        if is_account_path(candidate):
            return "account configuration"
    parts = [part for part in candidate.split("/") if part]
    if any(part in CREDENTIAL_DIR_NAMES for part in parts):
        return "credentials directory"
    return _file_name_label(parts[-1]) if parts else ""


def glob_selects_sensitive_name(pattern: str) -> bool:
    """True when a file-name glob would select a sensitive file.

    A bare wildcard (``*``, ``**/*``) selects everything and names nothing, so
    it is left to the directory it runs in rather than refused on its own.
    """
    name = pattern.rstrip("/").rsplit("/", 1)[-1]
    if not name or not re.search(r"[^*?/.]", name):
        return False
    return any(fnmatch.fnmatchcase(sample, name) for sample in _SENSITIVE_NAME_SAMPLES)


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
    """The path-like pieces of one shell token (``--in=x``, ``@x``, ``<x``)."""
    for piece in _TOKEN_SPLIT.split(token):
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
