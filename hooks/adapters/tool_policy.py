"""Tool-call policy every host adapter runs, answered as a host-neutral verdict.

``ToolPolicy`` decides what Gaia does with a Bash, Task/Agent or Write/Edit call
before it runs and records what happened after it ran. It never builds a host
response: it returns a ``PolicyVerdict`` and each host adapter translates that
verdict into its own protocol. Tools only one host has (Claude Code's
SendMessage and AskUserQuestion) stay in that host's adapter.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .types import ConsentRequest, HookEvent, ToolResult

logger = logging.getLogger(__name__)


# Environment variable the DB-side dispatch guards read to identify the writing
# agent (gaia.store.writer, gaia.state.permissions, gaia.evidence.store,
# gaia.briefs.store). Defined here as the single injection-side constant.
GAIA_DISPATCH_AGENT_ENV = "GAIA_DISPATCH_AGENT"

# Dotted event category written to harness_events when a nascent-row birth is
# rejected for an ANOMALOUS reason (see _is_anomalous_dispatch_binding_rejection).
# Severity is warning, not error: a rejected birth never blocks the dispatch, so
# the row only makes an otherwise-silent misdispatch visible to triage.
DISPATCH_BINDING_REJECTED_EVENT = "dispatch.binding_rejected"

# DispatchBindingError.reason codes that are ALWAYS anomalous: each names a
# binding that carried SOME plan/verifier coordinate and failed to resolve it.
# Mirrors the reason list in modules.agents.dispatch_binding.DispatchBindingError.
_ALWAYS_ANOMALOUS_BINDING_REASONS = frozenset({
    "plan_task_id_unresolved",
    "plan_task_id_not_dispatchable",
    "verifier_requires_parent_handoff_id",
    "parent_handoff_id_unresolved",
})

# Anomalous only when the dispatch named a plan: with no plan_id= either, a
# task_execution dispatch without task_id= is a legitimate free-standing turn.
_CONDITIONAL_BINDING_REASON = "task_execution_requires_plan_task_id"


@dataclass(frozen=True)
class PolicyVerdict:
    """What Gaia decided about one tool call, in no host's vocabulary.

    A verdict with nothing set is no opinion: the host's own default proceeds.
    ``refusal`` blocks the call outright with that text; ``consent`` asks the
    host to obtain the user's consent through its own mechanism; otherwise
    ``decision`` (allow/deny/ask) carries ``reason``, ``updated_input`` and the
    ``approval_id`` a denial asks consent for. ``context`` is text for the model.
    """

    decision: Optional[str] = None
    reason: str = ""
    updated_input: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
    refusal: Optional[str] = None
    consent: Optional[ConsentRequest] = None
    context: str = ""

    @property
    def is_silent(self) -> bool:
        """Whether the policy left this call to the host's default."""
        return (
            self.decision is None
            and self.refusal is None
            and self.consent is None
            and not self.context
        )


def dispatch_tmpdir(agent_id: str) -> str:
    """Create and return the subagent's own TMPDIR, or "" when it cannot exist.

    Without it, pytest and every other tool in the agent's shell write to the
    host's ``/tmp`` (a small tmpfs on the hosts this runs on), which a full
    suite run fills. The directory must exist before the command runs: Python's
    ``tempfile`` silently falls back to ``/tmp`` for a missing TMPDIR.
    """
    from gaia.paths import dispatch_tmp_dir

    path = dispatch_tmp_dir(agent_id)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("dispatch TMPDIR %s unavailable: %s", path, exc)
        return ""
    return str(path)


def build_dispatch_identity_command(command: str, agent_type: str, tmpdir: str = "") -> str:
    """Prefix a subagent Bash command so ``GAIA_DISPATCH_AGENT`` reaches the CLI.

    ``tmpdir``, when given, is exported as ``TMPDIR`` in the same statement, so
    the agent's tool temporaries follow the same scope as its identity.

    The DB guards fail OPEN when ``GAIA_DISPATCH_AGENT`` is unset (that is the
    human-CLI / orchestrator-main-session path). To make them fail CLOSED for a
    dispatched subagent, the agent's identity must reach the ``gaia`` CLI
    subprocess -- and the only lever a PreToolUse hook has over a subprocess's
    environment is the command string it executes. So we export the identity at
    the front of the command line:

        ``export GAIA_DISPATCH_AGENT=<agent>; <original command>``

    An ``export ...;`` statement is used deliberately rather than a bare
    ``VAR=x <cmd>`` word prefix: a bare prefix binds the variable to ONLY the
    first stage of a compound command, so ``GAIA_DISPATCH_AGENT=x cd /repo &&
    gaia ...`` would leave ``gaia`` without the variable. ``export`` makes it
    visible to every stage and every subprocess of that single Bash invocation
    (each tool call is a fresh shell, so the scope is exactly this one command).

    ``agent_type`` is the HARNESS-provided dispatch identity, never a value the
    agent supplies; the caller only invokes this for a real subagent. Returns
    the command unchanged when ``agent_type`` is empty (no identity to assert --
    the guards then stay fail-open for that command, the conservative fallback).

    Note (accepted limitation): a deliberately adversarial agent could append an
    inline ``GAIA_DISPATCH_AGENT=orchestrator`` assignment to a later stage of
    its own command and shadow the exported value for that stage. This injection
    closes the ACCIDENTAL over-authority gap (subagents writing as human by
    default); a forged inline override is a distinct, adversarial threat still
    covered by the T3 approval layer on the underlying mutation.
    """
    agent = (agent_type or "").strip()
    if not agent:
        return command
    exports = f"{GAIA_DISPATCH_AGENT_ENV}={shlex.quote(agent)}"
    if tmpdir:
        exports += f" TMPDIR={shlex.quote(tmpdir)}"
    return f"export {exports}; {command}"


def _is_anomalous_dispatch_binding_rejection(
    reason: str, binding: Dict[str, Any],
) -> bool:
    """Whether a rejected nascent-row birth is worth surfacing as an event.

    A rejected birth is the correct, silent outcome for a free-standing turn
    (investigation, memory) dispatched with no plan coordinate at all --
    ``extract_dispatch_binding`` labels every non-verifier dispatch
    ``task_execution`` by default, so the ABSENCE of a ``task_id=`` token on
    such a turn is not a mistake, it is the shape of a legitimate free
    dispatch. Emitting an event for that case would train the triage reader
    to ignore the channel, which defeats the reason it exists.

    Four reasons are anomalous unconditionally: each one means SOME plan or
    verifier coordinate was supplied and failed to resolve, which is never the
    free-turn shape. The fifth, ``task_execution_requires_plan_task_id``, is
    anomalous only when ``plan_id`` WAS extracted from the prompt -- that
    combination means the dispatcher intended a plan-bound turn (it named the
    plan) and simply dropped the ``task_id=`` token, the exact misdispatch the
    convention in ``agents/gaia-orchestrator.md`` exists to prevent.
    """
    if reason in _ALWAYS_ANOMALOUS_BINDING_REASONS:
        return True
    if reason == _CONDITIONAL_BINDING_REASON:
        return binding.get("plan_id") is not None
    return False


def _record_dispatch_binding_rejection(
    exc: Any, *, agent_name: str, binding: Dict[str, Any],
) -> None:
    """Record an ANOMALOUS nascent-row birth rejection as a harness_events row.

    Writes through ``EventWriter().write_event`` at severity ``warning`` and is
    strictly best-effort -- every failure is swallowed so a birth rejection,
    itself already non-blocking, can never become a reason the dispatch fails.

    Silent by design for the legitimate free-turn shape; see
    :func:`_is_anomalous_dispatch_binding_rejection` for the discriminator.
    """
    reason = getattr(exc, "reason", "")
    if not _is_anomalous_dispatch_binding_rejection(reason, binding):
        return
    try:
        from modules.events.event_writer import EventWriter

        meta: Dict[str, Any] = {
            "agent": agent_name,
            "reason": reason,
            "binding": {
                "kind": binding.get("kind"),
                "turn_role": binding.get("turn_role"),
                "plan_id": binding.get("plan_id"),
                "plan_task_id": binding.get("plan_task_id"),
                "parent_handoff_id": binding.get("parent_handoff_id"),
            },
        }
        EventWriter().write_event(
            DISPATCH_BINDING_REJECTED_EVENT,
            "hook",
            agent_name,
            f"nascent-row birth rejected for {agent_name} ({reason})",
            severity="warning",
            meta=meta,
        )
    except Exception as write_exc:  # pragma: no cover - telemetry must never block
        logger.debug(
            "dispatch binding rejection event write failed (non-fatal): %s",
            write_exc,
        )


def failure_verdict(error: Exception) -> PolicyVerdict:
    """Refuse a call whose validation raised, naming storage exhaustion apart.

    A refusal keeps the failure FAIL-CLOSED on every host: the call never runs
    unvalidated. The distinct storage message tells the operator the refusal is
    an exhausted disk, not a security-policy denial.
    """
    logger.error("Unexpected error in adapt_pre_tool_use: %s", error, exc_info=True)
    from modules.core.operational_errors import storage_exhaustion_message

    operational_message = storage_exhaustion_message(error)
    if operational_message is not None:
        return PolicyVerdict(refusal=operational_message)
    return PolicyVerdict(refusal=f"Error during security validation: {str(error)}")


class ToolPolicy:
    """Gaia's decisions about Bash, Task/Agent and Write/Edit calls.

    ``PRIMARY_AGENT_TYPE`` names the requester of a Bash call whose event
    carries no agent; the approval core never defaults an identity, so a host
    whose main session sends none declares its own name here.
    """

    PRIMARY_AGENT_TYPE = ""

    # ------------------------------------------------------------------ #
    # Before the call
    # ------------------------------------------------------------------ #

    def pre_tool_verdict(
        self, event: HookEvent, *, dispatch_identity_in_env: bool = False,
    ) -> PolicyVerdict:
        """Decide one tool call before it runs.

        ``dispatch_identity_in_env`` is set by a host that exports the
        subagent's identity through the shell environment itself, so the
        command is not rewritten to carry it.
        """
        from modules.security.approval_grants import cleanup_expired_grants

        hook_data = event.payload
        tool_name = hook_data.get("tool_name") or ""
        tool_input = hook_data.get("tool_input", {})

        logger.info("Hook invoked: tool=%s, params=%s", tool_name, json.dumps(tool_input)[:200])

        try:
            # The orchestrator (main session) is restricted to dispatch tools
            # plus Read before any other logic runs; subagents are unaffected.
            from modules.orchestrator.delegate_mode import check_delegate_mode

            dm_result = check_delegate_mode(tool_name, hook_data)
            if dm_result.blocked:
                logger.warning(
                    "DELEGATE_MODE denied %s for orchestrator", tool_name,
                )
                return PolicyVerdict(decision="deny", reason=dm_result.reason)

            cleanup_expired_grants()

            if not isinstance(tool_name, str):
                return PolicyVerdict(refusal="Error: Invalid tool name")
            if not isinstance(tool_input, dict):
                return PolicyVerdict(refusal="Error: Invalid parameters")

            lowered = tool_name.lower()
            if lowered == "bash":
                return self._bash_verdict(
                    tool_name, tool_input, hook_data=hook_data,
                    dispatch_identity_in_env=dispatch_identity_in_env,
                )
            if lowered in ("task", "agent"):
                return self._task_verdict(
                    tool_name, tool_input,
                    session_id=event.session_id,
                    hook_data=hook_data,
                )
            if lowered in ("write", "edit"):
                agent_id = hook_data.get("agent_id", "")
                return self._write_edit_verdict(
                    tool_name, tool_input,
                    session_id=hook_data.get("session_id", ""),
                    is_subagent=bool(agent_id),
                    agent_id=agent_id,
                    agent_type=hook_data.get("agent_type", ""),
                )
            if lowered in ("read", "glob", "grep", "ls"):
                from modules.security.sensitive_read_guard import check_file_tool

                refusal = check_file_tool(
                    tool_name, tool_input, cwd=hook_data.get("cwd") or None,
                )
                if refusal:
                    return PolicyVerdict(decision="deny", reason=refusal)
            return PolicyVerdict()
        except Exception as e:
            return failure_verdict(e)

    def _bash_verdict(
        self,
        tool_name: str,
        parameters: dict,
        hook_data: dict | None = None,
        *,
        dispatch_identity_in_env: bool = False,
    ) -> PolicyVerdict:
        """Validate a Bash command, keep its correlation state, and name its identity.

        ``hook_data`` is the full event payload: its ``agent_id`` marks a
        subagent call, and its ``session_id``/``tool_use_id`` key the state the
        call's terminal event reads back.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.bash_validator import BashValidator

        command = parameters.get("command", "")
        if not command:
            return PolicyVerdict(refusal="Error: Bash tool requires a command")

        is_subagent = bool(hook_data and hook_data.get("agent_id"))
        session_id = (hook_data or {}).get("session_id", "")
        agent_type = (hook_data or {}).get("agent_type", "") or (
            "" if is_subagent else self.PRIMARY_AGENT_TYPE
        )
        # The host sends the SAME tool_use_id at PreToolUse and at the call's
        # terminal event. Keying hook state by (session_id, tool_use_id) is what
        # ends the concurrent-subagent race that clobbered the old single global
        # state file and lost EXECUTED terminal events.
        tool_use_id = (hook_data or {}).get("tool_use_id", "")

        validator = BashValidator()
        result = validator.validate(
            command, is_subagent=is_subagent, session_id=session_id,
            agent_type=agent_type, hook_payload=hook_data,
        )

        if not result.allowed:
            logger.warning("BLOCKED: %s - %s", command[:100], result.reason)
            # The T3 deny-vs-native-ask decision was already made in the
            # validator (decide_t3_outcome): a subagent under the orchestrator
            # gets a deny naming its approval_id; the main session an ask.
            if result.block_response is not None:
                return self._verdict_from_block_response(result)
            return PolicyVerdict(refusal=self._format_blocked_message(result))

        # The grant is consumed here, so the call's terminal event can only
        # find the approval through this state.
        effective_command = result.modified_input.get("command", command) if result.modified_input else command
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=effective_command,
            tier=str(result.tier),
            session_id=session_id,
            tool_use_id=tool_use_id,
            allowed=True,
            consumed_approval_id=result.consumed_approval_id,
            command_set_reservation=result.command_set_reservation,
        )
        state_saved = save_hook_state(state)
        if result.command_set_reservation and not state_saved:
            # Deny fail-closed: without the correlation state, the terminal
            # event could never settle the reserved COMMAND_SET item. The settle
            # here is best-effort -- the denial stands even if it fails.
            try:
                from gaia.store.writer import settle_plan_command
                settle_plan_command(
                    result.command_set_reservation["approval_id"],
                    session_id=session_id, tool_use_id=tool_use_id, success=False,
                    failure_reason="PreToolUse correlation state could not be persisted",
                )
            except Exception as exc:
                logger.debug("reservation settle on state failure errored: %s", exc)
            return PolicyVerdict(
                refusal="COMMAND_SET denied: host correlation state unavailable",
            )

        # A subagent's DB writes are gated by guards that read
        # GAIA_DISPATCH_AGENT, which is never set at the process level; the
        # host-provided agent_type is exported at the front of the command so
        # the guards enforce the per-agent model (build_dispatch_identity_command).
        # The orchestrator and a human CLI call are never injected.
        final_command = effective_command
        if is_subagent and not dispatch_identity_in_env:
            final_command = build_dispatch_identity_command(
                effective_command, agent_type,
                tmpdir=dispatch_tmpdir(hook_data["agent_id"]),
            )

        if final_command != command:
            reason = (
                result.reason
                if result.modified_input
                else "dispatch-identity injected (GAIA_DISPATCH_AGENT)"
            )
            logger.info(
                "MODIFIED: %s -> tier=%s (footer_stripped=%s, dispatch_id=%s)",
                command[:80], result.tier,
                bool(result.modified_input), final_command != effective_command,
            )
            return PolicyVerdict(
                decision="allow", reason=reason,
                updated_input={"command": final_command},
            )

        logger.info("ALLOWED: %s - tier=%s", command[:100], result.tier)
        return PolicyVerdict()

    @staticmethod
    def _verdict_from_block_response(result) -> PolicyVerdict:
        """Read a validator's structured block back into a verdict.

        The validator formats its block through the active host adapter, so it
        is read through that adapter's accessors, never by its keys. The only
        rewritten input a block carries is the footer-stripped command the
        validator attaches to an ask, which is exactly ``modified_input``.
        """
        from modules.tools.hook_response import (
            read_permission_decision,
            read_permission_reason,
        )

        decision = read_permission_decision(result.block_response) or "deny"
        return PolicyVerdict(
            decision=decision,
            reason=read_permission_reason(result.block_response),
            updated_input=result.modified_input if decision == "ask" else None,
            approval_id=result.approval_id,
        )

    @staticmethod
    def _format_blocked_message(result) -> str:
        """Format blocked command message. Delegates to blocked_message_formatter."""
        from modules.security.blocked_message_formatter import format_blocked_message
        return format_blocked_message(result)

    def _task_verdict(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        hook_data: Optional[dict] = None,
    ) -> PolicyVerdict:
        """Validate a Task/Agent dispatch and birth the row its turn will claim.

        The subagent's payload is the dispatch KERNEL rendered from the born
        row, not a preloaded project-context snapshot; the turn pulls project
        context on demand. What happens here: task validation, the
        session-events digest handed to the host, and the birth of the nascent
        row. ``hook_data`` is the raw payload: the birth mines its top-level
        ``prompt_id`` / ``tool_use_id`` / ``cwd``, which ``parameters`` (the
        tool_input) does not carry.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.task_validator import TaskValidator
        from modules.session.session_event_injector import build_session_events

        events_text = build_session_events(parameters)

        validator = TaskValidator()
        result = validator.validate(parameters)

        if not result.allowed:
            logger.warning("BLOCKED Task: %s - %s", result.agent_name, result.reason)
            return PolicyVerdict(refusal=result.reason)

        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"Task:{result.agent_name}",
            tier=str(result.tier),
            allowed=True,
            is_t3=result.is_t3_operation,
        )
        save_hook_state(state)

        logger.info("ALLOWED Task: %s", result.agent_name)

        # Best-effort and strictly non-blocking: a dispatch is never blocked by
        # a binding that fails to resolve or a birth that errors.
        self._maybe_birth_dispatched_row(
            parameters, result.agent_name, session_id,
            hook_data=hook_data,
        )

        if events_text:
            self._deliver_subagent_context(
                session_id or "unknown",
                result.agent_name or "unknown",
                events_text,
                parameters.get("description", ""),
            )

        try:
            from modules.events.event_writer import EventWriter, AGENT_DISPATCH
            prompt = parameters.get("prompt", "")
            EventWriter().write_event(
                AGENT_DISPATCH, "hook", result.agent_name or "unknown",
                f"dispatched for: {prompt[:100]}",
            )
        except Exception:
            pass  # Events are non-critical

        return PolicyVerdict()

    def _deliver_subagent_context(
        self, session_id: str, agent_type: str, context: str, task_description: str,
    ) -> None:
        """Hand the dispatched subagent's session-events digest to the host.

        A host with no start event to carry it drops it.
        """

    @staticmethod
    def _resolve_dispatch_workspace(
        parameters: dict, hook_data: Optional[dict],
    ) -> str:
        """Resolve the REAL workspace for a dispatch birth.

        The explicit overrides (``parameters['workspace']``, GAIA_WORKSPACE)
        keep their priority; otherwise the canonical path-based resolver is
        anchored at the payload's ``cwd``, the same resolver every other
        workspace consumer uses. Its own last resort is ``"global"``: the Task
        tool_input never carries a workspace key and the harness does not set
        GAIA_WORKSPACE, so a literal fallback used to land every birth there.
        """
        explicit = (
            parameters.get("workspace")
            or os.environ.get("GAIA_WORKSPACE")
        )
        if explicit:
            return explicit
        try:
            from modules.install_detector import resolve_workspace

            return resolve_workspace((hook_data or {}).get("cwd") or None)
        except Exception:
            return "global"

    @staticmethod
    def _kernel_dispatch_facts(
        agent_name: str,
        workspace: str,
        *,
        turn_role: Optional[str] = None,
        cwd: Optional[str] = None,
        project_token: Optional[str] = None,
    ) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
        """Derive (kernel_sections, dispatch_project) for a birth -- no routing.

        The kernel payload is derived from the agent's OWN declarations --
        ``agent_contract_permissions`` for can_read/can_write, the agent's own
        ``surface_routing`` row for its surface, the dispatch binding for its
        role. The project is dispatch data first: a ``project=<name>`` token in
        the prompt resolves by name; only a dispatch that named none falls back
        to the cwd, because the orchestrator dispatches from the workspace root.

        Fail-safe: any error degrades to ``(None, None)`` -- a birth without a
        kernel payload, never a blocked dispatch.
        """
        try:
            from modules.core.paths import ensure_package_root_importable

            ensure_package_root_importable()
            from tools.context.context_provider import (
                build_kernel_sections,
                resolve_dispatch_project,
                resolve_project_by_name,
            )

            sections = build_kernel_sections(
                agent_name, workspace, turn_role=turn_role,
            )
            project = resolve_project_by_name(workspace, project_token)
            if not project:
                project = resolve_dispatch_project(workspace, cwd)
            return sections, project
        except Exception:
            logger.debug(
                "kernel dispatch facts derivation failed (non-fatal)",
                exc_info=True,
            )
            return None, None

    @staticmethod
    def _maybe_birth_dispatched_row(
        parameters, agent_name, session_id,
        hook_data: Optional[dict] = None,
    ) -> Optional[Dict[str, str]]:
        """Best-effort born-at-dispatch row birth (degrade-not-drop).

        Mints a REAL, adoptable identity for the turn (see
        ``modules.agents.dispatch_identity``) and births the nascent row under
        it, returning ``{"agent_id": ..., "contract_id": ...}`` on success, or
        None when no row was born at all (a writer error, an extraction gap).
        The return value is a birth signal for callers and tests: the turn
        recovers its identity by claiming the row itself.

        BIRTH IS TOTAL: EVERY ``DispatchBindingError`` degrades rather than
        dropping the row. The coordinate that failed to resolve is stamped NULL
        and the rejection reason plus the failed token are recorded inside the
        birth envelope (``birth_degraded_row``). An unborn row is not a weaker
        binding but no contract at all, and it is unrecoverable, since
        ``harness_agent_id`` is stamped only when the row is claimed.

        ``hook_data`` enriches the birth with the dispatch coordinates
        (prompt_id/tool_use_id/description/prompt, kernel_sections, project)
        and anchors workspace resolution at its cwd. ``session_id`` is stamped
        as-is when present and NULL when absent -- never a placeholder string,
        which would bake a lie into the column that nothing could correct.

        Never raises: every failure path is swallowed so a dispatch is never
        blocked.
        """
        try:
            from modules.agents.dispatch_binding import (
                DispatchBindingError,
                birth_degraded_row,
                birth_dispatched_row,
            )
            from modules.agents.dispatch_identity import mint_dispatch_identity

            meta = {
                "prompt": parameters.get("prompt", ""),
                "subagent_type": agent_name,
            }
            from modules.agents.dispatch_binding import extract_dispatch_binding
            binding = extract_dispatch_binding(meta)

            workspace = ToolPolicy._resolve_dispatch_workspace(
                parameters, hook_data,
            )
            payload = hook_data or {}
            kernel_sections, dispatch_project = (
                ToolPolicy._kernel_dispatch_facts(
                    agent_name or "",
                    workspace,
                    turn_role=binding.get("turn_role"),
                    cwd=payload.get("cwd") or None,
                    project_token=binding.get("project"),
                )
            )
            dispatch_fields = {
                "dispatch_prompt_id": payload.get("prompt_id") or None,
                "dispatch_tool_use_id": payload.get("tool_use_id") or None,
                "dispatch_description": parameters.get("description") or None,
                "dispatch_prompt": parameters.get("prompt") or None,
                "kernel_sections": kernel_sections,
                "dispatch_project": dispatch_project,
            }
            sid = session_id or None
            agent = agent_name or "unknown"
            ptid = binding.get("plan_task_id")
            # The identity is MINTED, not derived from (session, agent, task):
            # a derived key collapses two concurrent dispatches of the same
            # agent type onto one row. The writer's ON CONFLICT keeps a single
            # dispatch's birth idempotent on retry.
            identity = mint_dispatch_identity()
            contract_id = identity["contract_id"]

            try:
                birth_dispatched_row(
                    contract_id=contract_id,
                    agent_id=identity["agent_id"],
                    workspace=workspace,
                    kind=binding.get("kind"),
                    turn_role=binding.get("turn_role"),
                    plan_task_id=ptid,
                    plan_id=binding.get("plan_id"),
                    parent_handoff_id=binding.get("parent_handoff_id"),
                    session_id=sid,
                    # The NAME goes in the birth envelope, not in agent_id: it
                    # is the only coordinate a turn that never adopts still
                    # shares with its own row.
                    agent_name=agent,
                    **dispatch_fields,
                )
                logger.info(
                    "Born-at-dispatch: nascent row stamped (agent=%s, task=%s, "
                    "contract_id=%s)",
                    agent, ptid, contract_id,
                )
                return identity
            except DispatchBindingError as exc:
                logger.info(
                    "Born-at-dispatch: binding not born (agent=%s): %s",
                    agent, exc,
                )
                _record_dispatch_binding_rejection(
                    exc, agent_name=agent, binding=binding,
                )
                try:
                    birth_degraded_row(
                        contract_id=contract_id,
                        agent_id=identity["agent_id"],
                        workspace=workspace,
                        kind=binding.get("kind"),
                        rejection_reason=exc.reason,
                        failed_plan_task_id=ptid,
                        failed_parent_handoff_id=binding.get("parent_handoff_id"),
                        plan_id=binding.get("plan_id"),
                        session_id=sid,
                        agent_name=agent,
                        **dispatch_fields,
                    )
                    logger.info(
                        "Born-at-dispatch: DEGRADED row stamped (agent=%s, "
                        "failed_task=%s, failed_parent=%s, reason=%s, "
                        "contract_id=%s)",
                        agent, ptid, binding.get("parent_handoff_id"),
                        exc.reason, contract_id,
                    )
                    return identity
                except Exception:
                    logger.debug(
                        "Born-at-dispatch degraded birth failed (non-fatal)",
                        exc_info=True,
                    )
        except Exception:
            logger.debug("Born-at-dispatch birth failed (non-fatal)", exc_info=True)
        return None

    def _write_edit_verdict(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        is_subagent: bool = False,
        agent_id: str = "",
        agent_type: str = "",
    ) -> PolicyVerdict:
        """Ask a signature for Write/Edit on Gaia's hook tree or an account path; remind a subagent of its skill.

        The protected set comes from
        ``modules.security.protected_paths.is_protected_hook_path``, the one
        predicate the Bash command-string guard consumes too, so widening this
        surface cannot leave the shell route open against the same tree. An
        account path (``sensitive_paths.is_account_path``) gets the signature a
        Bash write onto it gets.

        - Main session: consent is asked inline through the host's own
          mechanism (``consent`` without an approval id).
        - Subagent: the host-neutral ``core.protected_write_verdict`` decides,
          bound to the session and ``agent_type`` of the event; an event
          missing either is denied without sealing a request (``agent_id`` is
          never used in its place). An active grant allows; otherwise consent
          is keyed to the requester's pending approval, and a pending sealed
          without phrases is denied naming the ``request-file-write`` line.

        A non-protected subagent write whose extension maps to a governing
        skill not yet reminded this turn is allowed with that reminder as
        ``context`` -- text the model reads; it never blocks.
        """
        from modules.agents.artifact_skill_map import expected_skill_for_path
        from modules.agents.artifact_skill_reminder import (
            build_reminder_context,
            should_remind,
        )
        from modules.security.protected_paths import (
            is_protected_hook_path,
            resolved_write_target,
        )
        from modules.security.sensitive_paths import is_account_path

        file_path = parameters.get("file_path", "")
        if not file_path:
            return PolicyVerdict()

        account_path = is_account_path(file_path)
        if not account_path and not is_protected_hook_path(file_path):
            if is_subagent and agent_id:
                expected_skill = expected_skill_for_path(file_path)
                if expected_skill and should_remind(session_id, agent_id, expected_skill):
                    return PolicyVerdict(
                        decision="allow",
                        reason=f"artifact-skill reminder logged for '{expected_skill}'",
                        context=build_reminder_context(file_path, expected_skill),
                    )
            return PolicyVerdict()

        logger.warning(
            "PROTECTED_PATH: %s attempted to modify %s (subagent=%s)",
            tool_name, file_path, is_subagent,
        )

        # Resolved once, so the grant lookup, the pending lookup, the pending
        # write and the surface the user reads all name the same object. The
        # protection check above keeps the path AS WRITTEN instead: only the
        # literal form carries the `.claude` component a symlinked install
        # destroys on resolution.
        consent_path = resolved_write_target(file_path)
        ask_reason = (
            f"[T3_APPROVAL_REQUIRED] This {tool_name} writes '{file_path}', which "
            f"grants access to your own account."
            if account_path else
            "[PROTECTED_PATH] Modifications to Gaia hooks and security config "
            "require approval."
        )

        if not is_subagent:
            return PolicyVerdict(consent=ConsentRequest(
                operation=consent_path,
                kind="file",
                reason=ask_reason,
                tier="T3_BLOCKED",
            ))

        from gaia.approvals import core
        try:
            core.resolve_requester(session_id, agent_type)
        except core.RequesterError as exc:
            return PolicyVerdict(
                decision="deny",
                reason=f"[PROTECTED_PATH] Write denied without a signature: {exc}",
            )
        try:
            verdict = core.protected_write_verdict(
                file_path, session_id=session_id, agent_id=agent_type,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist pending file-path approval for subagent; "
                "falling back to ask: %s (%s)", consent_path, exc,
            )
            return PolicyVerdict(consent=ConsentRequest(
                operation=consent_path,
                kind="file",
                reason=(
                    f"{ask_reason} (Pending approval persistence failed; "
                    "native dialog fallback.)"
                ),
                tier="T3_BLOCKED",
            ))
        if verdict["decision"] == "allow":
            logger.info("File-path grant active, allowing %s through: %s", tool_name, consent_path)
            return PolicyVerdict()
        from modules.security.approval_messages import build_protected_write_denial_message

        reason = build_protected_write_denial_message(
            verdict["approval_id"], consent_path, tool_name, verdict["window_minutes"],
            request_line=verdict.get("request_line") or "",
        )
        return PolicyVerdict(
            consent=ConsentRequest(
                operation=consent_path,
                kind="file",
                reason=reason,
                tier="T3_BLOCKED",
                approval_id=verdict["approval_id"][2:],
            ),
            approval_id=verdict["approval_id"],
        )

    # ------------------------------------------------------------------ #
    # After the call
    # ------------------------------------------------------------------ #

    def post_tool_verdict(self, event: HookEvent, tool_result: ToolResult) -> PolicyVerdict:
        """Record what one tool call did, closing the approval it consumed.

        ``tool_result`` is the host's own reading of the call's outcome. Audit,
        grant confirmation, the call's close against its approval, critical
        events and state cleanup never block: an error is logged, not raised.
        A Task/Agent result may carry a contract summary line as ``context``.
        """
        from modules.core.state import get_hook_state, clear_hook_state
        from modules.audit.logger import log_execution
        from modules.audit.event_detector import detect_critical_event
        from modules.session.session_context_writer import SessionContextWriter
        from modules.security.approval_grants import check_approval_grant, confirm_grant

        hook_data = event.payload
        logger.info("Post-hook event: %s", hook_data.get("hook_event_name"))

        raw_tool_response = hook_data.get("tool_response", {})
        tool_name = tool_result.tool_name
        parameters = hook_data.get("tool_input", {})
        post_session_id = hook_data.get("session_id", "")
        tool_use_id = hook_data.get("tool_use_id", "")
        output = tool_result.output
        # A failed call's tool_response may be a bare string; guard the dict
        # access so the FAILED event is still recorded.
        duration = (
            raw_tool_response.get("duration_ms", 0) / 1000.0
            if isinstance(raw_tool_response, dict)
            else 0.0
        )
        success = tool_result.exit_code == 0

        # The dispatch result is the only place a harness-truncated subagent is
        # observable, and where the orchestrator gets its look at the row. The
        # tool reports itself as "Agent"; "Task" is its former name.
        from modules.agents.task_result_observer import TASK_TOOL_NAMES

        if tool_name in TASK_TOOL_NAMES:
            self._observe_task_result(hook_data)
            return PolicyVerdict(context=self._build_contract_summary_context(hook_data))

        try:
            pre_state = get_hook_state(
                session_id=post_session_id, tool_use_id=tool_use_id
            )
            tier = pre_state.tier if pre_state else "unknown"

            computed_duration = duration
            if pre_state and pre_state.start_time_epoch > 0:
                computed_duration = time.time() - pre_state.start_time_epoch

            log_execution(
                tool_name=tool_name,
                parameters=parameters,
                result=output,
                duration=computed_duration,
                exit_code=0 if success else 1,
                tier=tier,
            )

            # The grant was already CONSUMED at the match in PreToolUse;
            # confirming marks it so later retries in the same session are
            # recognized.
            if tool_name == "Bash" and success:
                command = parameters.get("command", "")
                session_id = hook_data.get("session_id", "")
                if command:
                    grant = check_approval_grant(command, session_id=session_id)
                    if grant is not None and not grant.confirmed:
                        confirm_grant(command, session_id=session_id)
                        logger.info(
                            "T3 grant confirmed (consumed at match in PreToolUse): %s", command[:80],
                        )

            # This is the call's terminal event; the pre_state keyed by its
            # tool_use_id says which approval the call consumed. An interrupted
            # call closes nothing: its outcome is unknown, so it stays without
            # result and its reservation waits to be reclaimed.
            consumed_approval_id = (
                pre_state.metadata.get("consumed_approval_id") if pre_state else None
            )
            if tool_name == "Bash" and consumed_approval_id:
                if hook_data.get("is_interrupt") is True:
                    logger.info(
                        "Interrupted call left without result: approval_id=%s tool_use_id=%s",
                        consumed_approval_id[:16], tool_use_id[:16],
                    )
                else:
                    from gaia.approvals.core import close_call

                    outcome = close_call(
                        consumed_approval_id,
                        command=pre_state.command,
                        session_id=post_session_id,
                        tool_use_id=tool_use_id,
                        exit_code=tool_result.exit_code,
                        reserved=bool(pre_state.metadata.get("command_set_reservation")),
                        terminal_event=str(hook_data.get("hook_event_name") or ""),
                        error="" if success else str(output),
                    )
                    if outcome == "unmatched":
                        raise RuntimeError("COMMAND_SET reservation correlation failed")

            events = detect_critical_event(tool_name, parameters, output, success)
            if events:
                writer = SessionContextWriter()
                for evt in events:
                    writer.update_context(evt.to_dict())

            if tool_name == "Bash" and tier in ("T2", "T3"):
                try:
                    from modules.events.event_writer import EventWriter, COMMAND_EXECUTED
                    cmd = parameters.get("command", "")
                    EventWriter().write_event(
                        COMMAND_EXECUTED, "hook", "",
                        f"{'ok' if success else 'error'}: {cmd[:120]}",
                        severity="info" if success else "warning",
                        meta={"tier": tier},
                    )
                except Exception:
                    pass  # Events are non-critical

            clear_hook_state(
                session_id=post_session_id, tool_use_id=tool_use_id
            )
            logger.debug("Post-hook completed for %s", tool_name)

        except Exception as e:
            logger.error("Error in adapt_post_tool_use: %s", e, exc_info=True)

        return PolicyVerdict()

    @staticmethod
    def _observe_task_result(hook_data) -> None:
        """Record a harness-cut subagent turn seen from the Task result.

        Best-effort and strictly non-blocking: detection or persistence
        failing must never disturb the orchestrator's own turn.
        """
        try:
            from modules.agents.task_result_observer import observe_task_result

            cut = observe_task_result(hook_data)
            if cut is not None:
                logger.warning(
                    "Subagent cut detected: agent=%s reason=%s metrics=%s",
                    cut.agent, cut.reason, cut.metrics,
                )
        except Exception as exc:
            logger.debug("Task result observation failed (non-fatal): %s", exc)

    @staticmethod
    def _build_contract_summary_context(hook_data) -> str:
        """Best-effort contract-row summary line for the Agent/Task result.

        Carries whichever mode ``build_contract_summary_line`` produced: a
        finalized row's state (a synchronous dispatch) or a short "launched,
        not finalized yet" pointer (a background dispatch, whose result
        arrives at launch). Never raises: any failure, including an
        unresolvable row, degrades to "".
        """
        try:
            from modules.agents.task_result_observer import build_contract_summary_line

            line = build_contract_summary_line(
                hook_data.get("tool_response", {}),
                session_id=str(hook_data.get("session_id", "") or ""),
            )
            return line or ""
        except Exception as exc:
            logger.debug("Contract summary line build failed (non-fatal): %s", exc)
            return ""
