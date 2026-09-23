"""Refuse reading or listing a sensitive path, in Bash and in the file tools.

The list of what is sensitive is ``sensitive_paths``; this module only decides
when a call READS or LISTS one. The refusal is categorical and carries no
approval: a credential's contents are not something a signature should put in
a transcript. Writes are not decided here -- an account-path write keeps its
signature route (``mutative_verbs`` for Bash, ``tool_policy`` for Write/Edit).

A read is recognized by the command that performs it: a Bash component whose
base command is a known reader or lister and that names a sensitive path, or a
Read/Glob/Grep/list call whose path or pattern reaches one. What the guard
cannot see is stated rather than claimed: a program that opens the file itself
(``python3 script.py``, ``source .env``, ``git show HEAD:.env``), a copy made
first and read later, and a recursive search started above a sensitive
directory (``grep -r x ~``, Grep over the home directory).
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Any, List, Mapping, Optional, Tuple

from .composition_rules import _strip_transparent_prefixes
from .sensitive_paths import (
    first_sensitive_token,
    glob_selects_sensitive_name,
    pattern_reaches_sensitive_path,
    sensitive_path_label,
    sensitive_read_denial,
)
from .shell_grouping import strip_grouping_wrappers
from .shell_substitution import extract_substitutions

# Commands that print a file's contents, or list a directory's entries.
_READER_COMMANDS = frozenset({
    "cat", "tac", "head", "tail", "less", "more", "bat", "batcat", "nl",
    "od", "xxd", "hexdump", "strings", "base64", "base32",
    "sort", "uniq", "cut", "paste", "fold", "fmt", "rev", "column",
    "awk", "gawk", "mawk", "nawk", "sed",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "diff", "cmp", "comm", "jq", "yq", "openssl",
})
_LISTER_COMMANDS = frozenset({
    "ls", "dir", "vdir", "tree", "find", "du", "exa", "eza", "lsd",
})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

# A sed that edits in place is a write; its verdict belongs to the write
# route, which asks for a signature on an account path.
_SED_IN_PLACE = re.compile(r"^(?:-[a-zA-Z]*i|--in-place)")

# find's name tests take a pattern, not a place: `find . -name .env` lists
# where .env files are, never what they hold.
_FIND_PATTERN_FLAGS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-regex", "-iregex",
})

_OPERATOR_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def _tokenize(component: str) -> List[str]:
    try:
        return shlex.split(component, posix=True)
    except ValueError:
        return component.split()


def _component_reads_sensitive(component: str, cwd: Optional[str]) -> str:
    """The sensitive path a single command component reads or lists, or ""."""
    tokens = _strip_transparent_prefixes(_tokenize(strip_grouping_wrappers(component)))
    if not tokens:
        return ""
    base = os.path.basename(tokens[0])
    arguments = tokens[1:]
    if base in _SHELLS and "-c" in arguments:
        inline = arguments[arguments.index("-c") + 1:]
        return reads_sensitive_path(inline[0], cwd) if inline else ""
    if base not in _READER_COMMANDS and base not in _LISTER_COMMANDS:
        return ""
    if base == "sed" and any(_SED_IN_PLACE.match(arg) for arg in arguments):
        return ""
    if base == "find":
        arguments = [
            arg for index, arg in enumerate(arguments)
            if index == 0 or arguments[index - 1] not in _FIND_PATTERN_FLAGS
        ]
    return first_sensitive_token(arguments, cwd)


def reads_sensitive_path(command: str, cwd: Optional[str] = None) -> str:
    """The sensitive path *command* reads or lists, or "" when it reads none.

    Components are split on shell operators so a read in one is never tied
    to a harmless command in another, and command substitutions are scanned
    because they run before the command that holds them.
    """
    if not command:
        return ""
    pieces = list(_OPERATOR_SPLIT.split(command))
    for inner in extract_substitutions(command):
        pieces.extend(_OPERATOR_SPLIT.split(inner))
    for component in pieces:
        component = component.strip()
        if component:
            hit = _component_reads_sensitive(component, cwd)
            if hit:
                return hit
    return ""


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Bash entry point: ``(True, None)`` or ``(False, reason)``."""
    hit = reads_sensitive_path(command, cwd)
    if not hit:
        return True, None
    return False, sensitive_read_denial(hit, sensitive_path_label(hit, cwd))


def check_file_tool(
    tool_name: str, tool_input: Mapping[str, Any], cwd: Optional[str] = None,
) -> Optional[str]:
    """The refusal for a Read/Glob/Grep/LS call that reaches a sensitive path, or None.

    Tool inputs use Claude Code's names (``file_path``, ``path``, ``pattern``,
    ``glob``); the OpenCode adapter normalizes its own before policy runs.
    """
    tool = tool_name.lower()
    if tool not in ("read", "glob", "grep", "ls"):
        return None
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    label = sensitive_path_label(path, cwd) if path else ""
    target = path
    if not label and tool == "glob":
        pattern = str(tool_input.get("pattern") or "")
        label = pattern_reaches_sensitive_path(pattern, path or (cwd or ""))
        target = pattern
    if not label and tool == "grep":
        include = str(tool_input.get("glob") or "")
        if include and glob_selects_sensitive_name(include):
            label, target = "file-name pattern selecting credentials", include
        elif include:
            label = pattern_reaches_sensitive_path(include, path or (cwd or ""))
            target = include
    if not label:
        return None
    return sensitive_read_denial(target, label)
