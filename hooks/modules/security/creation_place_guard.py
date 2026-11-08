"""Finds a subagent's ``mkdir``/``touch`` that would create an entry nothing reclaims after its turn."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import List, Optional

from .canonical_path import command_name

_CREATORS = frozenset({"mkdir", "touch"})
_TOUCH_VALUE_FLAGS = frozenset({"-d", "-t", "-r", "--date", "--reference"})

GUIDANCE = (
    "It creates {path} outside your scratch entry and every git worktree, where nothing reclaims it "
    "after your turn. A throwaway belongs under ~/.gaia/scratch/<contract_id> and a change in your "
    "worktree; sign only for a deliverable that must live here, and name it in --does."
)


def _operands(tokens: List[str]) -> List[str]:
    operands, skip, options_done = [], False, False
    program = command_name(tokens[0])
    for token in tokens[1:]:
        if skip:
            skip = False
        elif not options_done and token == "--":
            options_done = True
        elif not options_done and token.startswith("-"):
            skip = "=" not in token and (
                (program == "touch" and token in _TOUCH_VALUE_FLAGS) or (program == "mkdir" and token in ("-m", "--mode")))
        else:
            operands.append(token)
    return operands


def _reclaimed(path: Path) -> bool:
    from gaia.paths import data_dir
    from gaia.worktree import managed_root_containing

    if any((ancestor / ".git").exists() for ancestor in (path, *path.parents)):
        return True
    kept = (Path(os.path.realpath(data_dir())), Path(os.path.realpath(os.path.expanduser("~/.gaia"))))
    return any(root == path or root in path.parents for root in kept) or managed_root_containing(path) is not None


def stranded_target(component: str, cwd: Optional[str]) -> Optional[str]:
    """The path a ``mkdir``/``touch`` *component* would create under the home folder where nothing reclaims it."""
    try:
        tokens = shlex.split(component)
    except ValueError:
        return None
    if not tokens or command_name(tokens[0]) not in _CREATORS:
        return None
    home = Path(os.path.realpath(os.path.expanduser("~")))
    for operand in _operands(tokens):
        spelled = os.path.expanduser(operand)
        if any(char in operand for char in "$`*?[{") or not (os.path.isabs(spelled) or (cwd and os.path.isabs(cwd))):
            continue
        path = Path(os.path.realpath(os.path.join(cwd or "", spelled)))
        if home in path.parents and not os.path.lexists(path) \
                and not path.relative_to(home).parts[0].startswith(".") and not _reclaimed(path):
            return operand
    return None


def stranded_creation(command: str, cwd: Optional[str], is_subagent: bool) -> Optional[str]:
    """The entry a subagent line would strand, else None."""
    if not is_subagent:
        return None
    from ..tools.shell_parser import parse_command
    from .mutative_verbs import cwd_after_component

    for component in parse_command(command):
        target = stranded_target(component.strip(), cwd)
        if target:
            return target
        cwd = cwd_after_component(component, cwd)
    return None
