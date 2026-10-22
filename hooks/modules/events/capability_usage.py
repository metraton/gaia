"""Record gaia CLI calls and skill loads in harness_events; argument values are never stored."""

from __future__ import annotations

import logging
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


def _excerpt(text: str) -> str:
    from gaia.redaction import redact_text

    # Redacting before the cut keeps a secret that straddles it from leaving its prefix.
    redacted = redact_text(text[: EXCERPT_LIMIT * 4])
    return redacted if len(redacted) <= EXCERPT_LIMIT else redacted[:EXCERPT_LIMIT] + "...[truncated]"


def _tool_entry(tool_name: str, tool_input: Mapping[str, Any], output: str, exit_code: Optional[int],
                hook_data: Mapping[str, Any], is_shell: bool) -> Optional[Dict[str, Any]]:
    from gaia.redaction import redact_text

    if is_shell:
        command = str(tool_input.get("command") or "")
        if not command:
            return None
        return {"command": redact_text(command), "exit_code": exit_code, "excerpt": _excerpt(output)}
    if tool_name == "Read":
        path = tool_input.get("file_path")
        return {"path": path} if isinstance(path, str) and path else None
    if tool_name in ("Grep", "Glob"):
        path = tool_input.get("path") or hook_data.get("cwd") or ""
        return {"path": str(path), "pattern": redact_text(str(tool_input.get("pattern") or "")),
                "excerpt": _excerpt(output)}
    return None


def record_tool_call(
    tool_name: str,
    tool_input: object,
    output: str,
    exit_code: Optional[int],
    hook_data: Mapping[str, Any],
    *,
    is_shell: bool,
) -> None:
    """Append one ``tool.called`` event to the calling subagent's tool record; the orchestrator's calls and other tools leave none; never raises."""
    try:
        key = str(hook_data.get("harness_agent_id", hook_data.get("agent_id")) or "")
        if not key or not isinstance(tool_input, dict):
            return
        entry = _tool_entry(tool_name, tool_input, output or "", exit_code, hook_data, is_shell)
        if entry is None:
            return
        from gaia.contract.tool_record import TOOL_CALLED

        _, agent = _caller(hook_data)
        _write(TOOL_CALLED, agent, f"{tool_name} agent_id={key}", "info",
               {"harness_agent_id": key, "tool": tool_name, **entry})
    except Exception as exc:
        logger.debug("tool.called not recorded (non-fatal): %s", exc)
