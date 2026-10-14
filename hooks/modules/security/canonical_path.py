"""One canonical form of a path, so every path guard judges a file and not its spelling.

Windows opens one file under many spellings: a Git Bash drive (``/c/Users``),
backslashes, any letter case, an alternate data stream (``.env::$DATA``),
trailing dots and spaces, a long-path or local admin-share prefix
(``\\\\?\\C:\\``, ``\\\\localhost\\C$\\``) and an 8.3 short name (``SSH~1``).
A guard that matches names or shapes compares :func:`canonical_path`; a guard
that resolves a path on disk takes :func:`native_path`, which drops only what
Windows itself drops and leaves a POSIX path alone.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Tuple

ON_WINDOWS = os.name == "nt"

_GIT_BASH_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")
_LONG_PATH_PREFIX = re.compile(r"^//[?.]/(unc/)?", re.IGNORECASE)
_LOCAL_ADMIN_SHARE = re.compile(
    r"^//(?:localhost|127\.0\.0\.1|\[?::1\]?)/([A-Za-z])\$(?=/|$)", re.IGNORECASE,
)
_DRIVE = re.compile(r"^[A-Za-z]:")
_DRIVE_ROOT = re.compile(r"^[A-Za-z]:(?:/|$)")
_SHORT_NAME = re.compile(r"^([^~./]{1,6})~\d{1,6}(?:\.([^.]{1,3}))?$")
_EXECUTABLE_SUFFIXES = (".exe", ".com", ".cmd", ".bat")


def git_bash_to_windows(path: str) -> str:
    """Rewrite a Git Bash drive path (``/c/x``) as the Windows path it names (``C:/x``).

    MSYS mounts each drive at ``/<letter>``; ``ntpath`` does not know that and
    reads ``/c/x`` as a folder ``c`` at the root of the current drive.
    """
    return _GIT_BASH_DRIVE.sub(lambda match: match.group(1).upper() + ":", path, count=1)


def _drop_prefixes(path: str) -> str:
    long_prefix = _LONG_PATH_PREFIX.match(path)
    if long_prefix:
        path = ("//" if long_prefix.group(1) else "") + path[long_prefix.end():]
    return _LOCAL_ADMIN_SHARE.sub(lambda match: match.group(1).upper() + ":", path, count=1)


def _windows_segment(segment: str) -> str:
    """The name Windows opens for one segment: stream suffix, trailing dots and spaces dropped."""
    name = segment.split(":", 1)[0]
    return name.rstrip(". ") or name


def _windows_form(path: str) -> str:
    path = _drop_prefixes(git_bash_to_windows(path.strip()).replace("\\", "/"))
    drive = _DRIVE.match(path)
    head = drive.group(0) if drive else ""
    return head + "/".join(_windows_segment(segment) for segment in path[len(head):].split("/"))


def native_path(path: str, windows: bool = ON_WINDOWS) -> str:
    """The path Windows opens for *path*; off Windows these spellings are distinct files, so unchanged."""
    return _windows_form(path) if windows else path


def _short_alias_parts(long_name: str) -> Tuple[str, str]:
    name = long_name.lstrip(".")
    stem, _, extension = name.rpartition(".") if "." in name else (name, "", "")
    return stem.replace(".", "").replace(" ", "").upper(), extension[:3].upper()


def expand_short_name(segment: str, long_names: Iterable[str]) -> str:
    """The long name an 8.3 alias stands for, matched by shape, or *segment* itself.

    Shape only, never the disk: an alias whose stem Windows replaced with a
    hash (``ID3F2A~1``) is not recognized.
    """
    match = _SHORT_NAME.match(segment)
    if not match:
        return segment
    stem, extension = match.group(1).upper(), (match.group(2) or "").upper()
    for long_name in long_names:
        long_stem, long_extension = _short_alias_parts(long_name)
        if long_stem.startswith(stem) and long_extension == extension:
            return long_name
    return segment


def canonical_path(path: str, short_names: Iterable[str] = ()) -> str:
    """The comparison key for *path*: its Windows form, *short_names* expanded, case folded.

    Applied on every platform, so a guard that refuses a name refuses each of
    its spellings -- and also a case-distinct POSIX file, which is the
    conservative side to err on.
    """
    names = tuple(short_names)
    segments = (expand_short_name(segment, names) for segment in _windows_form(path).split("/"))
    return "/".join(segments).lower()


def is_absolute(canonical: str) -> bool:
    """True when a canonical path is rooted at ``/``, a UNC host or a drive."""
    return canonical.startswith("/") or bool(_DRIVE_ROOT.match(canonical))


def command_name(token: str) -> str:
    """The program a command word runs, as Windows finds it: no directory, any case, no executable suffix."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in _EXECUTABLE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name
