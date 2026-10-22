"""Record gaia CLI calls and skill loads in harness_events; argument values are never stored."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import gaia_cli_grammar

logger = logging.getLogger(__name__)

CLI_INVOKED = "cli.invoked"
SKILL_LOADED = "skill.loaded"
EXCERPT_LIMIT = 500

ORCHESTRATOR_AGENT = "gaia-orchestrator"

SUBCOMMAND_TREE: Dict[str, frozenset] = {
    path: frozenset(names.split()) for path, names in gaia_cli_grammar.SUBCOMMANDS.items()
}
DECLARED_OPTIONS: Dict[str, frozenset] = {
    path: frozenset(names.split()) for path, names in gaia_cli_grammar.OPTIONS.items()
}


def _verb_and_flags(arguments: List[str]) -> Tuple[str, Tuple[str, ...]]:
    path: Tuple[str, ...] = ()
    walking = True
    flags: List[str] = []
    for token in arguments:
        if token == "--":
            break
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            if name in DECLARED_OPTIONS.get(" ".join(path), ()) and name not in flags:
                flags.append(name)
            continue
        if walking and token in SUBCOMMAND_TREE.get(" ".join(path), ()):
            path += (token,)
        else:
            walking = False
    return " ".join(path), tuple(flags)


def gaia_invocations(command: str) -> List[Tuple[str, Tuple[str, ...]]]:
    """Each gaia call in ``command`` as (subcommand path, declared flag names); the path is empty when unknown."""
    from ..security.host_consent_verb_guard import gaia_calls

    return [_verb_and_flags(arguments) for arguments in gaia_calls(command or "")]


def _caller(hook_data: Mapping[str, Any]) -> Tuple[str, str]:
    from ..orchestrator.delegate_mode import SessionRole, classify_session_role

    role = classify_session_role(dict(hook_data))
    if role is SessionRole.ORCHESTRATOR:
        return role.value, ORCHESTRATOR_AGENT
    return role.value, str(hook_data.get("agent_type") or "")


def _write(
    event_type: str, agent: str, result: str, severity: str, meta: Dict[str, Any], *, lean: bool = False,
) -> None:
    from .event_writer import EventWriter

    EventWriter().write_event(event_type, "hook", agent, result, severity=severity, meta=meta, lean=lean)


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


def _excerpt(text: str) -> str:
    from gaia.redaction import redact_text

    # Redaction runs on a bounded window, and a token the window cuts no longer matches its
    # shape, so the window drops its trailing word before redacting.
    window = text[: EXCERPT_LIMIT * 4]
    cut = len(window) < len(text)
    redacted = redact_text(re.sub(r"\S{1,256}\Z", "", window) if cut else window)
    if not cut and len(redacted) <= EXCERPT_LIMIT:
        return redacted
    return redacted[:EXCERPT_LIMIT] + "...[truncated]"


def _append_tool_record(tool_name: str, hook_data: Mapping[str, Any], entry: Dict[str, Any]) -> None:
    from gaia.contract.tool_record import TOOL_CALLED

    # Only a subagent's call carries a harness id, so the orchestrator's calls leave no record.
    key = str(hook_data.get("harness_agent_id", hook_data.get("agent_id")) or "")
    if key:
        _write(TOOL_CALLED, str(hook_data.get("agent_type") or ""), f"{tool_name} agent_id={key}",
               "info", {"harness_agent_id": key, "tool": tool_name, **entry}, lean=True)


def record_command_run(
    tool_input: object, output: str, exit_code: Optional[int], hook_data: Mapping[str, Any],
) -> None:
    """Append a shell call's redacted command, exit code and output excerpt to the subagent's tool record; never raises."""
    try:
        from gaia.redaction import redact_text

        command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
        if command:
            _append_tool_record(str(hook_data.get("tool_name") or ""), hook_data, {
                "command": redact_text(command), "exit_code": exit_code, "excerpt": _excerpt(output or ""),
            })
    except Exception as exc:
        logger.debug("tool.called not recorded (non-fatal): %s", exc)


def record_file_checked(tool_name: str, tool_input: object, hook_data: Mapping[str, Any]) -> None:
    """Spool the path a Read, Grep or Glob call is about to open for the subagent's finalize, with no database write; never raises."""
    try:
        if not isinstance(tool_input, dict):
            return
        if tool_name == "Read":
            path = tool_input.get("file_path")
        elif tool_name in ("Grep", "Glob"):
            path = tool_input.get("path") or hook_data.get("cwd")
        else:
            return
        if isinstance(path, str) and path:
            from gaia.contract import read_spool
            from gaia.redaction import redact_text

            key = str(hook_data.get("harness_agent_id", hook_data.get("agent_id")) or "")
            if key:
                read_spool.append(key, tool_name, redact_text(path))
    except Exception as exc:
        logger.debug("tool.called not recorded (non-fatal): %s", exc)
