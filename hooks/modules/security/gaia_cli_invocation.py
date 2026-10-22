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
from typing import List, Optional, Sequence

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
