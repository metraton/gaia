"""Record gaia CLI calls and skill loads in harness_events; argument values are never stored."""

from __future__ import annotations

import logging
import re
import shlex
from pathlib import PurePath, PureWindowsPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

CLI_INVOKED = "cli.invoked"
SKILL_LOADED = "skill.loaded"

ORCHESTRATOR_AGENT = "gaia-orchestrator"

# Must equal bin/gaia's argparse tree, or a verb is cut where a child is missing;
# test_capability_usage.py walks the real parser and fails on any difference.
_SUBCOMMANDS = {
    "": (
        "ac approvals brief cleanup context contract defects dev doctor evidence history "
        "install lint login memory metrics migrate milestone notifications now paths plan "
        "project query release scan session signals status task uninstall update usage "
        "workspace worktree"
    ),
    "ac": "add edit remove set-status",
    "approvals": (
        "approve audit-secrets clean history list opencode-decide opencode-present pending "
        "question reject reject-all replay request-file-write request-set revoke show stats"
    ),
    "brief": (
        "ac close decision delete deps edit history list milestone new search set-project "
        "set-status show verify"
    ),
    "brief ac": "add edit remove",
    "brief decision": "add list",
    "brief milestone": "add remove",
    "context": (
        "accept dump get get-contract move-contracts move-memory move-project project "
        "prune-workspaces query scan show wipe"
    ),
    "contract": "add chain fill finalize init list reap reconcile set validate view",
    "evidence": "add list orphans show",
    "lint": "comments",
    "login": "start status",
    "memory": (
        "add append checkpoint conflicts delete edit episode-show get-relevant link list "
        "reclassify search show stats story"
    ),
    "migrate": "apply plan",
    "milestone": "add edit remove set-status",
    "notifications": "ack add cancel list show snooze",
    "paths": "data db",
    "plan": "change delete history list pause resume save set-status show",
    "plan change": "apply approve list propose request",
    "project": "move",
    "release": "check publish",
    "session": "preview resume-point snapshot",
    "session resume-point": "set",
    "task": "add cover depend edit gate list remove reorder set-status show",
    "task gate": "add edit list remove reverify set-status",
    "usage": "ingest show",
    "workspace": "curate current declare info list merge retire",
    "worktree": "create list prune-branches release show show-capture",
}
SUBCOMMAND_TREE: Dict[str, frozenset] = {
    parent: frozenset(children.split()) for parent, children in _SUBCOMMANDS.items()
}

_FLAG_NAME = re.compile(r"^--?[A-Za-z][A-Za-z0-9_-]*$")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_OPERATOR_PREFIXES = ("<", ">", "|", "&", ";")


def _program_name(token: str) -> str:
    name = PureWindowsPath(PurePath(token).name).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _gaia_arguments(tokens: List[str]) -> Optional[List[str]]:
    index = 0
    while index < len(tokens) and _ASSIGNMENT.match(tokens[index]):
        index += 1
    if index >= len(tokens):
        return None
    program = _program_name(tokens[index])
    if program == "gaia":
        return tokens[index + 1:]
    if program.startswith("python") and index + 1 < len(tokens):
        if _program_name(tokens[index + 1]) == "gaia":
            return tokens[index + 2:]
    return None


def _verb_and_flags(arguments: List[str]) -> Tuple[str, Tuple[str, ...]]:
    path: Tuple[str, ...] = ()
    walking = True
    flags: List[str] = []
    for token in arguments:
        if token == "--" or token.startswith(_SHELL_OPERATOR_PREFIXES):
            break
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            if _FLAG_NAME.match(name) and name not in flags:
                flags.append(name)
            continue
        if walking and token in SUBCOMMAND_TREE.get(" ".join(path), ()):
            path += (token,)
        else:
            walking = False
    return " ".join(path), tuple(flags)


def gaia_invocations(command: str) -> List[Tuple[str, Tuple[str, ...]]]:
    """Each gaia call in ``command`` as (subcommand path, flag names); the path is empty when unknown."""
    from ..tools.shell_parser import parse_command

    found = []
    for component in parse_command(command or ""):
        try:
            tokens = shlex.split(component)
        except ValueError:
            tokens = component.split()
        arguments = _gaia_arguments(tokens)
        if arguments is not None:
            found.append(_verb_and_flags(arguments))
    return found


def _caller(hook_data: Mapping[str, Any]) -> Tuple[str, str]:
    from ..orchestrator.delegate_mode import SessionRole, classify_session_role

    role = classify_session_role(dict(hook_data))
    if role is SessionRole.ORCHESTRATOR:
        return role.value, ORCHESTRATOR_AGENT
    return role.value, str(hook_data.get("agent_type") or "")


def _write(event_type: str, agent: str, result: str, severity: str, meta: Dict[str, Any]) -> None:
    from .event_writer import EventWriter

    EventWriter().write_event(event_type, "hook", agent, result, severity=severity, meta=meta)


def record_cli_invocations(
    command: str,
    hook_data: Mapping[str, Any],
    *,
    outcome: str,
    exit_code: Optional[int] = None,
) -> None:
    """Append one ``cli.invoked`` event per gaia call (outcome allowed|denied|refused|interrupted); never raises."""
    try:
        invocations = gaia_invocations(command)
        if not invocations:
            return
        role, agent = _caller(hook_data)
        session_id = str(hook_data.get("session_id") or "")
        failed = outcome != "allowed" or (exit_code or 0) != 0
        status = f" exit={exit_code}" if exit_code is not None else ""
        for verb, flags in invocations:
            _write(
                CLI_INVOKED,
                agent,
                f"gaia {verb}".rstrip() + f": {outcome}{status} session={session_id}",
                "warning" if failed else "info",
                {
                    "session_id": session_id,
                    "agent_id": str(hook_data.get("agent_id") or ""),
                    "role": role,
                    "verb": verb,
                    "flags": list(flags),
                    "outcome": outcome,
                    "exit_code": exit_code,
                    "tool": str(hook_data.get("tool_name") or ""),
                },
            )
    except Exception as exc:
        logger.debug("cli.invoked not recorded (non-fatal): %s", exc)


def record_skill_load(tool_name: str, tool_input: object, hook_data: Mapping[str, Any]) -> None:
    """Append one ``skill.loaded`` event, without the skill's arguments, when the call loads a skill; never raises."""
    try:
        from ..security.memory_skill_guard import SKILL_TOOL_NAMES

        if tool_name not in SKILL_TOOL_NAMES or not isinstance(tool_input, dict):
            return
        skill = str(tool_input.get("skill") or tool_input.get("name") or "")
        if not skill:
            return
        role, agent = _caller(hook_data)
        session_id = str(hook_data.get("session_id") or "")
        _write(
            SKILL_LOADED,
            agent,
            f"{skill} session={session_id}",
            "info",
            {
                "session_id": session_id,
                "agent_id": str(hook_data.get("agent_id") or ""),
                "role": role,
                "skill": skill,
            },
        )
    except Exception as exc:
        logger.debug("skill.loaded not recorded (non-fatal): %s", exc)
