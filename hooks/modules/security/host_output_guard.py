"""Refuses a subagent shell command that copies the host's persisted tool output or feeds it to a program."""

from __future__ import annotations

import os
import re
from typing import List, Optional

from .canonical_path import command_name
from .sensitive_paths import expand_home

RULE = "floor.host-tool-output"
_SPOOL = re.compile(r"(?:^|/)\.claude/projects/(?:[^/\s'\"]+/)+tool-results(?:/|$)")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
_OUTPUT_REDIRECT = re.compile(r"^(?:1?|&)>>?(.*)$")
_SED_ADDRESS = r"(?:\d+|\$|/[^/]*/)"
_SED_PRINT = re.compile(rf"\s*(?:{_SED_ADDRESS}(?:\s*,\s*{_SED_ADDRESS})?)?\s*p\s*")
_INSPECTORS = frozenset({
    "grep", "egrep", "fgrep", "rg", "head", "tail", "cat", "wc", "less", "more", "ls", "stat", "file", "cd",
    "cut", "sort", "uniq", "nl", "tr", "column", "fold",
})

REJECTION = (
    "[HOST_TOOL_OUTPUT] Refusing a command that copies Claude Code's persisted tool output "
    "(~/.claude/projects/*/tool-results) or hands it to a program as input: {path}. That file is a "
    "transcript of an earlier call, not a source. Inspect it with grep, rg, head, tail, cat, wc or the "
    "Read tool; for data, regenerate it from its source into your scratch entry "
    "(~/.gaia/scratch/<contract_id>). Not approvable."
)


def _names_spool(word: str) -> bool:
    path = expand_home(word)
    return bool(_SPOOL.search(os.path.expanduser(path) if path.startswith("~") else path))


def _words(component: str) -> List[str]:
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(component)
    except ValueError:
        words = component.split()
    while words and _ASSIGNMENT.match(words[0]):
        words = words[1:]
    return words


def _writes_stdout(words: List[str]) -> bool:
    for index, word in enumerate(words[1:], start=1):
        match = _OUTPUT_REDIRECT.match(word)
        if match:
            target = match.group(1) or (words[index + 1] if index + 1 < len(words) else "")
            if target != "/dev/null":
                return True
    return False


def _sed_only_prints(words: List[str]) -> bool:
    """True for ``sed -n`` whose every script command prints an address or range, and nothing else."""
    quiet, scripts, operands = False, [], []
    arguments = iter(words[1:])
    for word in arguments:
        if word in ("-e", "--expression"):
            scripts.append(next(arguments, ""))
        elif word.startswith("--expression="):
            scripts.append(word.split("=", 1)[1])
        elif word in ("--quiet", "--silent"):
            quiet = True
        elif re.fullmatch(r"-[nEr]+", word):
            quiet = quiet or "n" in word
        elif word == "--regexp-extended":
            continue
        elif word.startswith("-"):
            return False
        else:
            operands.append(word)
    if not scripts and operands:
        scripts.append(operands.pop(0))
    parts = [part for script in scripts for part in re.split(r"[;\n]", script) if part.strip()]
    return quiet and bool(parts) and all(_SED_PRINT.fullmatch(part) for part in parts)


def misused_spool(command: str, cwd: Optional[str] = None, depth: int = 0) -> Optional[str]:
    """The tool-output path *command* copies or feeds to a program; inspecting it with a reader is not a use."""
    from ..tools.shell_parser import parse_command

    named = cwd if cwd and _SPOOL.search(os.path.abspath(cwd) + "/") else None
    inspections, redirected = [], False
    for component in parse_command(command or ""):
        words = _words(component.strip())
        if not words:
            continue
        program = command_name(words[0])
        inspects = program in _INSPECTORS or (program == "sed" and _sed_only_prints(words))
        inspections.append(inspects)
        redirected = redirected or _writes_stdout(words)
        for word in words[1:]:
            if not _names_spool(word):
                continue
            if " " in word.strip() and depth < 2:
                hit = misused_spool(word, cwd, depth + 1)
                if hit:
                    return hit
                continue
            if not inspects:
                return word
            named = named or word
    if named and (redirected or not all(inspections)):
        return named
    return None


def check(command: str, cwd: Optional[str], is_subagent: bool) -> Optional[str]:
    """The refusal for a subagent command that copies host tool output or feeds it to a program, else None."""
    from .protected_path_guard import _stores_its_words

    if not is_subagent or _stores_its_words(command):
        return None
    path = misused_spool(command, cwd)
    return REJECTION.format(path=path) if path else None
