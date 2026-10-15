"""Text-mode subprocess runs whose codec does not depend on the locale."""

import os
import shlex
import subprocess
import sys

_ON_WINDOWS = sys.platform == "win32"

# Git for Windows refuses paths longer than MAX_PATH (260) unless core.longpaths
# is on. Gaia passes it per call and never writes the user's git config.
LONG_PATHS_CONFIG = ("-c", "core.longpaths=true")
ENABLE_LONG_PATHS_COMMAND = "git config --global core.longpaths true"


def _is_git(program) -> bool:
    return os.path.basename(str(program)).lower() in ("git", "git.exe")


def with_long_paths(args):
    """*args* with core.longpaths enabled when it is a git argv on Windows, unchanged otherwise."""
    if not _ON_WINDOWS or isinstance(args, (str, bytes)) or not args or not _is_git(args[0]):
        return args
    args = list(args)
    return [args[0], *LONG_PATHS_CONFIG, *args[1:]]


def run_text(args, **kwargs) -> subprocess.CompletedProcess:
    """Run *args* as ``subprocess.run`` does, exchanging stdin/stdout/stderr as UTF-8 text.

    git and the other tools Gaia reads emit UTF-8; ``text=True`` alone decodes
    with the locale codec, which is cp1252 on Windows and ASCII under ``LC_ALL=C``.
    A git argv on Windows also gets core.longpaths enabled (``with_long_paths``).
    """
    return subprocess.run(with_long_paths(args), encoding="utf-8", **kwargs)


def command_arg(value) -> str:
    """*value* quoted as one argument for the shell a user of this platform types a printed command into.

    cmd.exe treats single quotes as literal characters, so on Windows an
    argument is double-quoted the way CreateProcess parses it; elsewhere POSIX
    quoting applies.
    """
    if sys.platform == "win32":
        return subprocess.list2cmdline([str(value)])
    return shlex.quote(str(value))


def long_paths_disabled():
    """On Windows, the problem line when the user's own git config leaves core.longpaths off; None otherwise."""
    if not _ON_WINDOWS:
        return None
    try:
        # Not run_text: its -c would answer for the user's config.
        value = subprocess.run(
            ["git", "config", "--type=bool", "--get", "core.longpaths"],
            capture_output=True, encoding="utf-8",
        ).stdout.strip()
    except OSError:
        return None
    if value == "true":
        return None
    return "core.longpaths is off: git run outside Gaia cannot check out paths longer than 260 characters"
