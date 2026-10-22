"""Which argv the Gaia CLI receives from one shell component, whatever entry point reaches it.

The CLI code is reachable as the ``gaia`` launcher, a symlink or renamed copy of
it, a ``bin/cli/<group>.py`` script (which runs as ``gaia <group>``), and
``python3 -m cli.<group>``. A guard that names only the ``gaia`` word leaves the
others open, and the CLI cannot tell a subagent from the orchestrator once the
subagent unsets ``GAIA_DISPATCH_AGENT``, so the guard is the only boundary.

Every word of the component is considered, not only the command position:
wrappers (``env -u``, ``timeout``, ``uv run``, ``xargs``) put the entry point
anywhere, and the callers match literal group and verb words after it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

_SHELL_RESOLVED = ("$", "`", "(", ")", "*", "?", "[")
_PYTHON_MODULE_FLAG = re.compile(r"^-[bBdEIiOPqRsSuvx]*m(?P<module>.*)$")
_CLI_MODULE = re.compile(r"^(?:bin\.)?cli\.(?P<group>\w+)$")
_DISPATCHER_SIGNATURE = ("Unified Gaia CLI", "_discover_plugins")
_SIGNATURE_READ_LIMIT = 64 * 1024


def is_shell_resolved(word: str) -> bool:
    """True when only the shell can say what *word* becomes: an expansion, a substitution or a glob."""
    return any(c in word for c in _SHELL_RESOLVED)


def _is_dispatcher_file(path: str) -> bool:
    try:
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(_SIGNATURE_READ_LIMIT)
    except OSError:
        return False
    return all(marker in head for marker in _DISPATCHER_SIGNATURE)


def entry_group(word: str) -> Optional[str]:
    """``""`` when *word* runs the dispatcher, ``"<group>"`` for a ``<group>.py`` script, else None.

    A shell-resolved word counts as the dispatcher, since nothing static can
    show it is not one. Any ``<stem>.py`` maps to ``<stem>``; callers match the
    group word literally, so an unrelated script never forms a Gaia argv.
    """
    if is_shell_resolved(word):
        return ""
    from .cli_aliases import aliased_cli
    from .gaia_cli_only_guard import is_trusted_gaia_binary

    if is_trusted_gaia_binary(word):
        return ""
    for candidate in (word, os.path.realpath(word)) if os.path.islink(word) else (word,):
        name = os.path.basename(candidate)
        if name == "gaia" or aliased_cli(name) == "gaia":
            return ""
        if name.endswith(".py") and len(name) > 3:
            return name[:-3]
    return "" if _is_dispatcher_file(word) else None


def _module_argv(words: Sequence[str], index: int) -> Optional[List[str]]:
    match = _PYTHON_MODULE_FLAG.match(words[index])
    if match is None:
        return None
    if match["module"]:
        module, rest = match["module"], words[index + 1:]
    elif index + 1 < len(words):
        module, rest = words[index + 1], words[index + 2:]
    else:
        return None
    cli = _CLI_MODULE.match(module)
    return [cli["group"], *rest] if cli else None


_UNQUOTED_COMPUTED = set("$`()*?[")
_BRACE_EXPANSION = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
_ARGV_FEEDERS = frozenset({"xargs", "parallel"})
_IDENTITY = "GAIA_DISPATCH_AGENT"
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly", "unset"})
_CLI_MODULES = frozenset(
    path.stem for path in (Path(__file__).resolve().parents[3] / "bin" / "cli").glob("*.py"))


def literal_words(component: str) -> List[Tuple[str, bool]]:
    """``(word, computed)`` per shell word: computed when an expansion, substitution, glob or brace makes it."""
    pairs: List[Tuple[str, bool]] = []
    text, unquoted, computed, started, quote = [], [], False, False, None
    index, size = 0, len(component)
    while index < size:
        char = component[index]
        escaped = char == "\\" and index + 1 < size and quote != "'"
        if escaped:
            index += 1
            text.append(component[index])
        elif quote == "'":
            quote = None if char == "'" else quote
            text.extend(char if quote else "")
        elif quote == '"':
            quote = None if char == '"' else quote
            computed = computed or (quote is not None and char in "$`")
            text.extend(char if quote else "")
        elif char in " \t\n":
            if started:
                pairs.append(("".join(text),
                              computed or bool(_BRACE_EXPANSION.search("".join(unquoted)))))
            text, unquoted, computed, started = [], [], False, False
        elif char in "'\"":
            quote = char
        else:
            computed = computed or char in _UNQUOTED_COMPUTED
            text.append(char)
            unquoted.append(char)
        started = started or char not in " \t\n"
        index += 1
    if started:
        pairs.append(("".join(text), computed or quote is not None
                      or bool(_BRACE_EXPANSION.search("".join(unquoted)))))
    return pairs


def _is_gaia_entry(words: Sequence[str], index: int) -> bool:
    if is_shell_resolved(words[index]):
        return False
    group = entry_group(words[index])
    if group == "":
        return True
    if group is not None:
        return group in _CLI_MODULES or os.path.basename(os.path.dirname(words[index])) == "cli"
    return _module_argv(words, index) is not None


def computes_gaia_words(component: str) -> bool:
    """True when a literally named Gaia CLI entry point in *component* takes computed or xargs-fed words."""
    pairs = literal_words(component)
    words = [word for word, _ in pairs]
    if not any(computed or os.path.basename(word) in _ARGV_FEEDERS for word, computed in pairs):
        return False
    fed = False
    for index, (word, computed) in enumerate(pairs):
        fed = fed or os.path.basename(word) in _ARGV_FEEDERS
        if not computed and _is_gaia_entry(words, index) and (
                fed or any(later for _, later in pairs[index + 1:])):
            return True
    return False


def clears_dispatch_identity(component: str) -> bool:
    """True when *component* unsets, empties or overrides ``GAIA_DISPATCH_AGENT``, or clears the environment."""
    pairs = literal_words(component)
    words = [word for word, _ in pairs]
    leading = 0
    while leading < len(words) and _ASSIGNMENT.match(words[leading]):
        if words[leading].startswith(_IDENTITY + "="):
            return True
        leading += 1
    rest = pairs[leading:]
    if rest and rest[0][0] in _DECLARERS and any(
            computed or word == _IDENTITY or word.startswith(_IDENTITY + "=")
            for word, computed in rest[1:]):
        return True
    if rest and rest[0][0] == "exec" and any(
            word.startswith("-") and "c" in word for word, _ in rest[1:2]):
        return True
    return any(os.path.basename(word) == "env" and _env_clears(pairs[index + 1:])
               for index, (word, _) in enumerate(pairs))


def _env_clears(args: Sequence[Tuple[str, bool]]) -> bool:
    index = 0
    while index < len(args):
        word = args[index][0]
        value = args[index + 1] if index + 1 < len(args) else ("", False)
        if word in ("-", "-i", "--ignore-environment") or word.startswith(_IDENTITY + "="):
            return True
        if word.startswith("--unset"):
            named = word.partition("=")[2] if "=" in word else value[0]
            if named == _IDENTITY or args[index][1] or ("=" not in word and value[1]):
                return True
            index += 1 if "=" in word else 2
            continue
        if word in ("-C", "--chdir"):
            index += 2
            continue
        if word.startswith("-") and not word.startswith("--"):
            before, unset, attached = word[1:].partition("u")
            if "i" in before or (not unset and "i" in word[1:]):
                return True
            if unset and ((attached or value[0]) == _IDENTITY or args[index][1]
                          or (not attached and value[1])):
                return True
            index += 1 if attached or not unset else 2
            continue
        if not _ASSIGNMENT.match(word):
            return False
        index += 1
    return False


def gaia_cli_argvs(words: Sequence[str]) -> List[List[str]]:
    """Every argv the Gaia CLI may receive from *words*, one per word that may be an entry point."""
    argvs = []
    for index, word in enumerate(words):
        module_argv = _module_argv(words, index)
        if module_argv is not None:
            argvs.append(module_argv)
            continue
        group = entry_group(word)
        if group is not None:
            argvs.append(([group] if group else []) + list(words[index + 1:]))
    return argvs
