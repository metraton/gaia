"""OpenCode event adapter.

OpenCode plugins call this adapter through a small JSON bridge rather than
Claude Code's stdin-hook protocol. The bridge supplies an immutable session,
tool call, and dispatch identity for every event. Lifecycle paths that have not
yet been wired to the plugin deny or report incomplete state instead of
silently accepting a governed operation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, FrozenSet

from modules.orchestrator.delegate_mode import ORCHESTRATOR_AGENT_TYPES

from .base import HookAdapter
from .tool_policy import PolicyVerdict, ToolPolicy
from .types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ConsentBinding,
    ConsentRequest,
    ContextResult,
    HookEvent,
    HookEventType,
    HookResponse,
    HostCapability,
    HostDistribution,
    ToolResult,
    ValidationRequest,
    ValidationResult,
    RoleCapabilityContext,
)

if TYPE_CHECKING:
    from modules.security.host_attestation import Attestation

    from .subagent_stop_core import SubagentStopOutcome


# The two contract-closing rules agent-protocol/SKILL.md's principles 2 and 10
# already state, appended to every OpenCode dispatch kernel because this host
# never preloads that skill (bin/cli/_install_helpers.py: `skills:` is access
# control here, not a preload guarantee, unlike Claude Code's own SubagentStart
# preload). kernel_builder.py's `# Your Contract` stays DATA ONLY for every
# host; this constant lives at the adapter seat so the append is OpenCode-only
# and importable by any other adapter that turns out to share the same gap.
CLOSING_RULES_KERNEL = (
    "# Closing this turn\n"
    "\n"
    "- Pass --draft-id <contract_id> on EVERY `gaia contract` call, from the "
    "first one. Omitted, a concurrent draft from another agent can raise "
    "AmbiguousDraftError instead of resolving to yours.\n"
    "- `gaia contract finalize --draft-id <contract_id>` is a separate, "
    "mandatory LAST step -- call it only once every other field is set."
)


class _DispatchPolicy(ToolPolicy):
    """The shared policy, keeping the session-events digest a Task dispatch hands to its host."""

    session_events = ""

    def _deliver_subagent_context(
        self, session_id: str, agent_type: str, context: str, task_description: str,
    ) -> None:
        self.session_events = context


_EVENT_TYPES = {
    "tool.execute.before": HookEventType.PRE_TOOL_USE,
    "tool.execute.after": HookEventType.POST_TOOL_USE,
    "chat.message": HookEventType.SESSION_START,
    "chat.prompt": HookEventType.USER_PROMPT_SUBMIT,
    "message.part.updated": HookEventType.SUBAGENT_START,
    "session.idle": HookEventType.SUBAGENT_STOP,
    "session.error": HookEventType.SUBAGENT_STOP,
    "session.deleted": HookEventType.SESSION_END,
    "session.compacted": HookEventType.POST_COMPACT,
    "session.compacting": HookEventType.PRE_COMPACT,
}

_PATCH_PATH_MARKER = re.compile(
    r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$"
)

# Only the OpenCode runtime may issue an identity claim; a claim carrying any
# other issuer reached Gaia through something other than the host itself.
_TRUSTED_ROLE_ISSUER = "opencode-runtime"

# Read from the classifier that acts on these names rather than restated here:
# a local literal fenced one spelling while ``classify_session_role`` accepted
# two, so ``{"agent": "orchestrator"}`` reached the control-plane lane as bare
# prompt text. Any spelling added to the classifier is fenced by construction.
_CONTROL_PLANE_ROLES = frozenset(
    role.strip().lower() for role in ORCHESTRATOR_AGENT_TYPES
)

# Neutral policy treats an empty agent_type as the control plane, so a caller
# with no attested claim is given a name that cannot be mistaken for one.
_UNATTESTED_AGENT_TYPE = "opencode-unattested"

# Named literally so a fail-closed pre-bind deny is never mistaken for one
# from any other policy lane (delegate_mode, an unattested control-plane
# claim, the tier classifier) -- plan 65, T10, rule 3.
_CHILD_BINDING_BACKSTOP_EMITTER = "opencode-adapter:child-session-binding-backstop"

# Dotted event category for the Layer O refusal-audit row (task 509): every
# identity/control-plane refusal denied by _identity_rejection lands exactly
# one harness_events row of this type. Severity warning (not info) keeps it
# visible to the triage reader without claiming error-severity defect status.
_IDENTITY_REFUSAL_EVENT = "opencode.identity.refused"

logger = logging.getLogger(__name__)


def _classify_stop_event(stop_event: str | None) -> str:
    """Map the lifecycle event that ended a child's turn onto a STOP_REASON_* class.

    session.error is the host ending the turn, which the core treats as a
    truncation to salvage rather than a contract to send back for repair.
    """
    from .subagent_stop_core import STOP_REASON_TRUNCATION, STOP_REASON_UNKNOWN

    return STOP_REASON_TRUNCATION if stop_event == "session.error" else STOP_REASON_UNKNOWN


def _apply_patch_paths(patch_text: object) -> list[str]:
    """Return every path a patch adds or updates, rejecting ambiguous envelopes.

    A deletion or a move is refused with the ``rm``/``mv`` command that carries
    it, because each path here is judged as an Edit, which sees neither effect.
    """
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ValueError("apply_patch requires non-empty patchText")
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("*** ") and line not in {"*** Begin Patch", "*** End Patch"}:
            match = _PATCH_PATH_MARKER.fullmatch(line)
            if match is None:
                raise ValueError(f"unsupported apply_patch marker: {line}")
            marker, path = match.group(1), match.group(2).strip()
            if not path or path in {"/", ".", ".."} or "\x00" in path:
                raise ValueError("apply_patch contains an unsafe or empty path")
            if marker == "Delete File":
                raise ValueError(_bash_route_refusal(f"rm -- {shlex.quote(path)}"))
            if marker == "Move to":
                if not paths:
                    raise ValueError("apply_patch Move to must follow Update File")
                source, destination = shlex.quote(paths[-1]), shlex.quote(path)
                raise ValueError(_bash_route_refusal(f"mv -- {source} {destination}"))
            paths.append(path)
    if not paths:
        raise ValueError("apply_patch contains no recognized file operation")
    return paths


def _bash_route_refusal(command: str) -> str:
    """Name the Bash command that must carry a deletion or move a patch attempted."""
    return (
        f"apply_patch cannot delete or move a file. Run `{command}` through bash: "
        "it gets the verdict, and the approval when one is needed, of any rm or mv."
    )


def _fail_closed(output: Dict[str, Any], exit_code: int) -> HookResponse:
    """Return a host response whose exit code cannot contradict a denial.

    The plugin reads the bridge's exit code before it reads the envelope, so a
    denial that exits zero would be read as a successful, permitted call.
    """
    return HookResponse(
        output=output,
        exit_code=2 if output.get("action") == "deny" else exit_code,
    )


def _carries_requester_phrases(approval_id: str) -> bool:
    """Whether ``approval_id`` passes the check `gaia approvals opencode-present` runs before showing it.

    An approval that cannot be read counts as phraseless: the plugin then
    delivers the denial itself instead of a presentation Gaia would refuse.
    """
    from gaia.approvals.core import missing_phrases
    from gaia.approvals.store import get_by_id

    try:
        row = get_by_id(approval_id) or {}
        return not missing_phrases(json.loads(row.get("payload_json") or "{}"))
    except Exception as exc:
        logger.warning("approval %s unreadable for presentation: %s", approval_id, exc)
        return False


class OpenCodeAdapter(HookAdapter):
    """Translate OpenCode plugin events into Gaia's normalized hook contract."""

    _CAPABILITIES: FrozenSet[HostCapability] = frozenset({
        HostCapability.INTERACTIVE_CONSENT,
        HostCapability.OUT_OF_BAND_APPROVAL,
        HostCapability.STRUCTURED_PERMISSION_DECISION,
        HostCapability.UPDATED_INPUT,
    })

    def parse_event(self, stdin_data: str) -> HookEvent:
        if not stdin_data or not stdin_data.strip():
            raise ValueError("Empty stdin data")
        try:
            raw = json.loads(stdin_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON from stdin: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Expected JSON object, got {type(raw).__name__}")

        event_name = raw.get("event") or raw.get("event_type")
        if not event_name:
            raise ValueError("Missing required field: event")
        try:
            event_type = _EVENT_TYPES[event_name]
        except KeyError as exc:
            raise ValueError(f"Unknown OpenCode event type: {event_name}") from exc

        session_id = raw.get("sessionID") or raw.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Missing required field: sessionID")

        payload = dict(raw)
        payload.setdefault("hook_event_name", event_type.value)
        payload.setdefault("session_id", session_id)
        payload.setdefault("tool_name", raw.get("tool", ""))
        payload.setdefault("tool_input", raw.get("args", {}))
        payload.setdefault("tool_response", raw.get("result", {}))

        return HookEvent(
            event_type=event_type,
            session_id=session_id,
            payload=payload,
            distribution=self.detect_distribution(),
            host_agent_id=raw.get("agentID") or raw.get("agent_id"),
            dispatch_id=raw.get("dispatchID") or raw.get("dispatch_id"),
            parent_dispatch_id=raw.get("parentDispatchID")
            or raw.get("parent_dispatch_id"),
            call_id=raw.get("callID") or raw.get("call_id"),
            role_context=self._parse_role_context(raw),
        )

    @staticmethod
    def _parse_role_context(raw: dict) -> RoleCapabilityContext | None:
        """Read only the host's structured identity envelope.

        The prompt and tool arguments are deliberately not identity sources. A
        malformed envelope is rejected at the adapter boundary, before Gaia's
        policy receives a claim that could be mistaken for authority.
        """
        value = raw.get("roleContext", raw.get("role_context"))
        if value is None:
            return None
        return RoleCapabilityContext.from_mapping(value)

    def format_validation_response(self, result: ValidationResult) -> HookResponse:
        return HookResponse(
            output={
                "action": "allow" if result.allowed else "deny",
                "reason": result.reason,
                "updated_input": result.modified_input,
                "tier": result.tier,
            },
            exit_code=0 if result.allowed else 2,
        )

    def format_ask_response(
        self, reason: str, updated_input: dict | None = None
    ) -> HookResponse:
        return HookResponse(
            output={
                "action": "ask",
                "reason": reason,
                "updated_input": updated_input,
            }
        )

    def read_permission_decision(self, output: Dict[str, object]) -> str | None:
        action = output.get("action")
        return action if action in {"allow", "deny", "ask"} else None

    def read_permission_reason(self, output: Dict[str, object]) -> str:
        reason = output.get("reason")
        return reason if isinstance(reason, str) else ""

    def inject_updated_input(
        self, output: Dict[str, object], updated_input: Dict[str, object]
    ) -> Dict[str, object]:
        output["updated_input"] = updated_input
        return output

    def request_consent(self, request: ConsentRequest) -> HookResponse:
        return HookResponse(
            output={
                "action": "approval_required",
                "approval_id": request.approval_id,
                "operation": request.operation,
                "kind": request.kind,
                "reason": request.reason,
                "tier": request.tier,
                "updated_input": request.updated_input,
            }
        )

    def capabilities(self) -> FrozenSet[HostCapability]:
        return self._CAPABILITIES

    def detect_distribution(self) -> HostDistribution:
        return HostDistribution(channel="opencode-plugin")

    def parse_pre_tool_use(self, raw: Dict[str, Any]) -> ValidationRequest:
        tool_input = raw.get("tool_input") or raw.get("args") or {}
        tool_name = raw.get("tool_name") or raw.get("tool") or ""
        command = tool_input.get("command", "") or tool_input.get("prompt", "")
        return ValidationRequest(
            tool_name=tool_name,
            command=command,
            tool_input=tool_input,
            session_id=raw.get("session_id") or raw.get("sessionID") or "",
        )

    def parse_post_tool_use(self, raw: Dict[str, Any]) -> ToolResult:
        tool_input = raw.get("tool_input") or raw.get("args") or {}
        result = raw.get("tool_response") or raw.get("result") or {}
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        raw_exit = result.get("exit_code", result.get("exitCode", 0)) if isinstance(result, dict) else 1
        try:
            exit_code = int(raw_exit)
        except (TypeError, ValueError):
            exit_code = 1
        if isinstance(result, dict) and (result.get("is_error") or result.get("isError")) and exit_code == 0:
            exit_code = 1
        return ToolResult(
            tool_name=raw.get("tool_name") or raw.get("tool") or "",
            command=tool_input.get("command", ""),
            output=output,
            exit_code=exit_code,
            session_id=raw.get("session_id") or raw.get("sessionID") or "",
            call_id=raw.get("call_id") or raw.get("callID"),
        )

    def parse_agent_completion(self, raw: Dict[str, Any]) -> AgentCompletion:
        metadata = raw.get("metadata") or {}
        return AgentCompletion(
            agent_type=raw.get("agent_type") or raw.get("subagent_type") or "",
            agent_id=metadata.get("sessionId") or raw.get("agentID") or "",
            transcript_path="",
            last_message=raw.get("output") or "",
            session_id=raw.get("session_id") or raw.get("sessionID") or "",
            dispatch_id=raw.get("dispatch_id") or raw.get("callID"),
            parent_session_id=metadata.get("parentSessionId"),
        )

    def format_completion_response(self, result: CompletionResult) -> HookResponse:
        return HookResponse(
            output={
                "contract_valid": result.contract_valid,
                "repair_needed": result.repair_needed,
                "anomalies": result.anomalies,
            },
            exit_code=0 if result.contract_valid else 2,
        )

    def format_context_response(self, result: ContextResult) -> HookResponse:
        return HookResponse(
            output={
                "additional_context": result.additional_context,
                "sections_provided": result.sections_provided,
            }
        )

    def adapt_session_start(self, raw: dict) -> BootstrapResult:
        """Run Claude Code's start maintenance for a main session, then build its birth block.

        The plugin forwards ``chat.message`` once per session it or the host
        records as parentless, so every call here is a start or a resume. The
        bridge runs from the session's workspace directory, which is the
        directory Claude Code's start sweeps too.
        """
        from modules.session import session_lifecycle
        from modules.session.session_lifecycle import SessionStart, start_context

        session_lifecycle.run_start_maintenance(SessionStart(
            session_id=str(raw.get("session_id") or ""),
            source="startup",
            is_headless=False,
            pinned_build=None,
            workspace_dir=Path.cwd(),
        ))
        return BootstrapResult(
            session_type="startup",
            additional_context=start_context("startup", []),
        )

    def adapt_user_prompt_submit(self, event: HookEvent) -> HookResponse:
        """Run Claude Code's per-prompt core for one main-session message: heartbeat, then the due-notifications line.

        The plugin forwards ``chat.prompt`` only for a main session's own
        messages, and the bridge runs from that session's workspace directory,
        the workspace Claude Code's UserPromptSubmit counts notifications for.
        """
        from gaia.project import current
        from modules.session.session_lifecycle import prompt_context

        try:
            workspace = current() or None
        except Exception:
            workspace = None
        return HookResponse(output={
            "action": "allow",
            "additional_context": prompt_context(event.session_id, workspace),
        })

    def format_bootstrap_response(self, result: BootstrapResult) -> HookResponse:
        return HookResponse(
            output={"action": "allow", "additional_context": result.additional_context or ""}
        )

    def adapt_subagent_start(self, raw: dict) -> ContextResult:
        """Bind the callID<->child-session pair this event reports, then
        pass the event through unchanged (plan 65, T10).

        OpenCode's only start-adjacent signal is ``message.part.updated`` on
        the PARENT's own tool part (part.type=tool, tool=task): it names the
        exact callID the Task PreToolUse call was born and claimed under (T9)
        together with the child's own session id, at
        ``state.metadata.sessionId``. ``session.created`` -- which fires on
        the CHILD's own session -- carries no callID at all and never reaches
        this method (``_EVENT_TYPES`` maps no such event to SUBAGENT_START);
        the bind's ONLY correlation key is this event's callID (enmienda E1:
        no fallback ladder -- see
        ``gaia.store.writer.bind_harness_child_session``).

        Best-effort and silent on any failure: a start event must never turn
        into an error over binding logic. This host injects no other
        kernel/context contribution from this event today, so the return
        value is unaffected by whether the bind landed.
        """
        call_id = raw.get("call_id") or raw.get("callID")
        state = raw.get("state")
        metadata = state.get("metadata") if isinstance(state, dict) else None
        child_session_id = (
            metadata.get("sessionId") if isinstance(metadata, dict) else None
        )
        if call_id and child_session_id:
            try:
                from gaia.store.writer import bind_harness_child_session

                bind_harness_child_session(
                    dispatch_tool_use_id=call_id,
                    harness_agent_id=child_session_id,
                )
            except Exception:
                pass
        return ContextResult(
            context_injected=bool(raw.get("additional_context")),
            additional_context=raw.get("additional_context"),
        )

    def _adapt_pre_tool_use_with_shell_env(self, event: HookEvent) -> HookResponse:
        """Select the host process transport, with attestation checked before policy."""
        return self.adapt_pre_tool_use(event, _shell_env_transport=True)

    def adapt_pre_tool_use(
        self, event: HookEvent, *, _shell_env_transport: bool = False,
    ) -> HookResponse:
        """Run the shared tool policy through an OpenCode boundary.

        ``ToolPolicy`` owns validation, grants, and audit state. This adapter
        supplies it with normalized OpenCode identities, then formats its
        verdict in the plugin protocol.
        """
        payload = dict(event.payload)
        original_tool = str(payload.get("tool_name", "")).lower()
        lookalike = self._signature_lookalike_refusal(original_tool, payload.get("tool_input"))
        if lookalike is not None:
            return HookResponse(output={"action": "deny", "reason": lookalike}, exit_code=2)
        rejection = self._identity_rejection(event, original_tool)
        if rejection is not None:
            rejection = self._with_identity_gap(rejection, payload)
            self._record_identity_refusal(event, original_tool, rejection)
            return HookResponse(output={"action": "deny", "reason": rejection}, exit_code=2)
        backstop = self._child_binding_backstop_denial(event)
        if backstop is not None:
            return backstop
        env_identity = None
        if _shell_env_transport and self._policy_tool_name(original_tool) == "Bash":
            env_identity = self._resolved_attestation(event)
            if env_identity is None or not event.session_id or not event.call_id:
                shell_reason = "Shell environment transport requires attested call correlation"
                self._record_identity_refusal(event, original_tool, shell_reason)
                return HookResponse(
                    output={"action": "deny", "reason": shell_reason},
                    exit_code=2,
                )
        retry_rejection = self._consent_retry_rejection(event, original_tool)
        if retry_rejection is not None:
            self._record_retry_denial(event, original_tool, retry_rejection)
            return HookResponse(
                output={"action": "deny", "reason": retry_rejection},
                exit_code=2,
            )
        payload = self.build_policy_payload(event)
        policy_event = HookEvent(
            event_type=event.event_type,
            session_id=event.session_id,
            payload=payload,
            distribution=event.distribution,
            host_agent_id=event.host_agent_id,
            dispatch_id=event.dispatch_id,
            parent_dispatch_id=event.parent_dispatch_id,
            call_id=event.call_id,
            role_context=event.role_context,
        )
        policy = ToolPolicy()
        if original_tool == "apply_patch":
            try:
                paths = _apply_patch_paths(payload.get("tool_input", {}).get("patchText"))
            except ValueError as exc:
                return HookResponse(output={"action": "deny", "reason": str(exc)}, exit_code=2)
            for path in paths:
                path_payload = dict(payload)
                path_payload["tool_input"] = {"file_path": path}
                path_event = HookEvent(
                    event_type=event.event_type, session_id=event.session_id,
                    payload=path_payload, distribution=event.distribution,
                    host_agent_id=event.host_agent_id, dispatch_id=event.dispatch_id,
                    parent_dispatch_id=event.parent_dispatch_id, call_id=event.call_id,
                    role_context=event.role_context,
                )
                checked = self._format_policy_verdict(policy.pre_tool_verdict(path_event))
                if checked.output.get("action") != "allow":
                    return checked
            return HookResponse(output={"action": "allow"})
        if original_tool == "task":
            return self._adapt_task_with_kernel(_DispatchPolicy(), policy_event)
        translated = self._format_policy_verdict(policy.pre_tool_verdict(
            policy_event, dispatch_identity_in_env=env_identity is not None,
        ))
        if env_identity is not None and isinstance(translated.output, dict) and translated.output.get("action") == "allow":
            translated.output["shell_env"] = {
                "session_id": event.session_id,
                "call_id": event.call_id,
                "agent_type": env_identity.role,
            }
        return translated

    @staticmethod
    def _signature_lookalike_refusal(tool_name: str, tool_input: object) -> str | None:
        """Refuse a question that reads as a Gaia signature: the plugin's own never reaches here.

        The plugin writes every signature question itself -- in the requester's
        control session, or in the orchestrator's call that carries only the
        approval id -- and returns before the bridge, so any signature-shaped
        question arriving is a model's copy, whose answer would decide nothing.
        """
        if tool_name not in {"askuserquestion", "question"} or not isinstance(tool_input, dict):
            return None
        from gaia.approvals.surface import looks_like_signature

        questions = tool_input.get("questions")
        if not isinstance(questions, list) or not any(
            isinstance(question, dict) and looks_like_signature(question) for question in questions
        ):
            return None
        return (
            "Gaia did not open this question: it copies an approval signature, and its "
            "answer would decide nothing. Do not type or copy a signature: run "
            "`gaia approvals question <approval_id>` and call the question tool with its "
            "output unchanged; Gaia writes the signature into that call."
        )

    @classmethod
    def _bash_command(cls, event: HookEvent, tool_name: str) -> str | None:
        """Return the Bash command string this event carries, if it carries one."""
        if cls._policy_tool_name(tool_name) != "Bash":
            return None
        tool_input = event.payload.get("tool_input") or event.payload.get("args") or {}
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command:
            return None
        return command

    @classmethod
    def _record_retry_denial(
        cls, event: HookEvent, tool_name: str, reason: str,
    ) -> None:
        """Leave a durable trace of a refused retry proof, and why.

        Never raises and never affects the verdict: the caller has already
        decided the denial. The ``harness_events`` record is written for every
        refusal, naming the grant the call should have matched against what
        the call actually presented; the ``approval_events`` denial needs an
        approval row to hang off, so it is written only when a live plan-first
        grant matches the command.
        """
        try:
            from gaia.approvals.decision_audit import (
                LANE_OPENCODE_POLICY_GATE,
                RETRY_REFUSED_PROOF_REJECTED,
                record_consent_retry_refused,
            )
            from gaia.approvals.store import get_by_id, record_execution_denial
            from gaia.store.writer import find_pending_plan_command

            proof = event.payload.get("consentRetry")
            claimed_id = proof.get("approval_id") if isinstance(proof, dict) else None
            command = cls._bash_command(event, tool_name)
            match = find_pending_plan_command(command) if command is not None else None
            observed_fingerprint = (
                hashlib.sha256(command.encode("utf-8")).hexdigest()
                if command is not None else None
            )
            received = (
                f"{event.session_id}/{event.call_id} as {cls._policy_agent_type(event)}"
                + (f" fingerprint {observed_fingerprint}" if observed_fingerprint else f" tool {tool_name}")
            )
            record_consent_retry_refused(
                approval_id=match["approval_id"] if match else str(claimed_id or ""),
                session_id=event.session_id or "",
                call_id=event.call_id or "",
                reason=RETRY_REFUSED_PROOF_REJECTED,
                expected=(
                    f"grant {match['approval_id']}[{match['index']}] fingerprint {match['fingerprint']}"
                    if match else "the active typed grant named by this proof"
                ),
                received=received,
                lane=LANE_OPENCODE_POLICY_GATE,
                detail=reason,
            )
            denial_approval_id = match["approval_id"] if match else claimed_id
            if not isinstance(denial_approval_id, str):
                return
            if match is None and get_by_id(denial_approval_id) is None:
                return
            record_execution_denial(
                denial_approval_id,
                "consent_retry_proof_rejected",
                host="opencode",
                session_id=event.session_id,
                call_id=event.call_id,
                index=match["index"] if match else None,
                command_fingerprint=match["fingerprint"] if match else observed_fingerprint,
                agent_id=cls._policy_agent_type(event),
                detail=reason,
            )
        except Exception:
            return

    @classmethod
    def _consent_retry_rejection(
        cls, event: HookEvent, tool_name: str,
    ) -> str | None:
        """Verify a supplied consent proof against the grant it names.

        A call carrying no proof is left to the host-neutral policy: the
        question lane cannot produce one, and requiring it here gated that lane
        on evidence only the plugin's native lane can mint.

        ``agent_id`` in the proof is the approval's own ``agent_id`` -- the Gaia
        contract identity the requester passed to ``request-set`` (a hash such
        as ``a69d869dc02031f54``), which ``opencode-present`` bound and minted
        the correlation from. It lives in a different namespace from the host
        role ``_policy_agent_type`` yields (``gaia-operator``), so it is checked
        against the grant it names and the correlation it minted, never against
        the role. The executing role is bound by the attestation and the
        grant's session instead (measured 2026-09-16: a byte-identical retry
        was refused because the two namespaces were compared as one).
        """
        proof = event.payload.get("consentRetry")
        if proof is None:
            return None
        if not isinstance(proof, dict):
            return "OpenCode consent retry proof is malformed"
        try:
            attestation = cls._resolved_attestation(event)
        except Exception:
            attestation = None
        if attestation is None:
            return "OpenCode consent retry proof is not host-attested"

        approval_id = proof.get("approval_id")
        agent_id = proof.get("agent_id")
        original_call_id = proof.get("original_call_id")
        retry_call_id = proof.get("retry_call_id")
        if (
            proof.get("version") != 1
            or proof.get("kind") not in {
                "COMMAND_SET", "SCOPE_SEMANTIC_SIGNATURE", "SCOPE_FILE_PATH",
            }
            or not isinstance(approval_id, str)
            or re.fullmatch(r"P-[0-9a-f]{32}", approval_id) is None
            or not isinstance(agent_id, str)
            or not agent_id
            or proof.get("session_id") != event.session_id
            or retry_call_id != event.call_id
            or not isinstance(original_call_id, str)
            or not original_call_id
            or original_call_id == retry_call_id
            or proof.get("role") != cls._policy_agent_type(event)
        ):
            return "OpenCode consent retry proof does not match this fresh tool call"

        try:
            from gaia.store.writer import list_approval_grants
            from .consent_events import mint_correlation_id

            expected_correlation = mint_correlation_id(
                approval_id,
                ConsentBinding(
                    agent_id=agent_id,
                    session_id=event.session_id,
                    call_id=original_call_id,
                ),
            )
            grants = list_approval_grants(status="PENDING", limit=1000)
            grant = next(
                (row for row in grants if row.get("approval_id") == approval_id),
                None,
            )
            grant_payload = json.loads(grant.get("command_set_json") or "null") if grant else None
        except Exception:
            return "OpenCode consent retry proof could not be verified"

        if (
            proof.get("correlation_id") != expected_correlation
            or grant is None
            or grant.get("agent_id") != agent_id
            or grant.get("session_id") != event.session_id
        ):
            return "OpenCode consent retry proof drifted from its bound grant"

        kind = proof["kind"]
        if kind in {"COMMAND_SET", "SCOPE_SEMANTIC_SIGNATURE"}:
            command = cls._bash_command(event, tool_name)
            fingerprint = proof.get("command_fingerprint")
            if (
                command is None
                or proof.get("command") != command
                or fingerprint != hashlib.sha256(command.encode("utf-8")).hexdigest()
            ):
                return "OpenCode consent retry proof command drifted from this call"
            if kind == "SCOPE_SEMANTIC_SIGNATURE":
                if (
                    grant.get("scope") != kind
                    or not isinstance(grant_payload, dict)
                    or grant_payload.get("command") != command
                ):
                    return "OpenCode semantic retry proof drifted from its active grant"
                return None

            index = proof.get("expected_index")
            grant_index = int(grant.get("next_index") or 0)
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or grant.get("scope") != "COMMAND_SET"
                or grant.get("source") != "plan-first"
                or grant.get("reservation_tool_use_id")
                or not isinstance(grant_payload, list)
                or grant_index != index
                or index >= len(grant_payload)
                or not isinstance(grant_payload[index], dict)
                or grant_payload[index].get("command") != command
                or grant_payload[index].get("fingerprint") != fingerprint
                or grant.get("request_fingerprint") != proof.get("request_fingerprint")
            ):
                return "OpenCode COMMAND_SET retry proof drifted from its bound grant"
            return None

        tool_input = event.payload.get("tool_input") or event.payload.get("args") or {}
        policy_tool = cls._policy_tool_name(tool_name)
        if policy_tool not in {"Write", "Edit"} or not isinstance(tool_input, dict):
            return "OpenCode file retry proof names the wrong tool family"
        if str(tool_name).lower() == "apply_patch":
            paths = tool_input.get("file_paths")
            actual_paths = paths if isinstance(paths, list) else []
        else:
            actual_path = tool_input.get("file_path")
            actual_paths = [actual_path] if isinstance(actual_path, str) else []
        canonical_path = proof.get("canonical_path")
        if (
            proof.get("tool_family") != policy_tool
            or not isinstance(canonical_path, str)
            or actual_paths != [canonical_path]
            or proof.get("path_fingerprint")
            != hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
        ):
            return "OpenCode file retry proof path or tool family drifted from this call"
        if (
            grant.get("scope") != "SCOPE_FILE_PATH"
            or not isinstance(grant_payload, dict)
            or grant_payload.get("file_path") != canonical_path
        ):
            return "OpenCode file retry proof drifted from its active grant"
        return None

    def _adapt_task_with_kernel(
        self, policy: _DispatchPolicy, policy_event: HookEvent,
    ) -> HookResponse:
        """Run the Task dispatch through the shared policy and, on allow, rewrite its prompt.

        The new prompt is the session-events digest, the born row's kernel as
        Claude Code's SubagentStart renders it (contract, CLI, the user's rows),
        the skills the dispatched agent's definition preloads (OpenCode
        preloads none; a body arrives only through its ``skill`` tool) and the
        closing rules. OpenCode has no start event that reliably precedes the child's first
        action (``message.part.updated`` can arrive after it), so what Claude
        Code's SubagentStart delivers rides this call's prompt instead, the
        digest ahead of the kernel as there. The kernel's goal already holds
        the original prompt, so it is not appended again. The row is claimed by
        the callID the shared policy birthed it under, never born a second
        time. Any denial or claim/render miss returns the plain verdict: kernel
        injection must never block a dispatch.

        A prompt that already carries a kernel this adapter rendered (a call
        evaluated again, or an orchestrator that echoed one back) is reduced to
        its instruction before the row is born, so the dispatched prompt holds
        exactly one contract block. A call that resumes a child session by
        ``task_id`` links the row it births to the one that child last held.
        """
        self._reduce_prompt_to_instruction(policy_event)
        translated = self._format_policy_verdict(policy.pre_tool_verdict(policy_event))
        output = translated.output
        if output.get("action") != "allow":
            return translated

        try:
            from gaia.store.writer import claim_dispatch_row
        except Exception:
            return translated

        try:
            row = claim_dispatch_row(
                dispatch_tool_use_id=policy_event.call_id or None,
            )
        except Exception:
            return translated
        if row is None:
            return translated

        tool_input = policy_event.payload.get("tool_input") or {}
        self._link_resumed_row(row, tool_input.get("task_id"))

        try:
            kernel = self._child_kernel(row, str(tool_input.get("subagent_type") or ""))
        except Exception:
            kernel = None
        if not kernel:
            return translated

        sections = (policy.session_events, kernel, CLOSING_RULES_KERNEL)
        updated_input = dict(output.get("updated_input") or {})
        updated_input["prompt"] = "\n\n".join(section for section in sections if section)
        output["updated_input"] = updated_input
        return translated

    @staticmethod
    def _reduce_prompt_to_instruction(policy_event: HookEvent) -> None:
        from modules.context.kernel_builder import unwrap_injected_prompt

        tool_input = policy_event.payload.get("tool_input")
        prompt = tool_input.get("prompt") if isinstance(tool_input, dict) else None
        if not isinstance(prompt, str):
            return
        instruction = unwrap_injected_prompt(prompt)
        if instruction != prompt:
            policy_event.payload["tool_input"] = {**tool_input, "prompt": instruction}

    @staticmethod
    def _link_resumed_row(row: dict, resumed_session_id: object) -> None:
        """Make the row born for a ``task_id`` resume continue the row that child session last held.

        Best-effort like the kernel injection: a miss leaves the row unlinked,
        never blocks the dispatch.
        """
        if not isinstance(resumed_session_id, str) or not resumed_session_id:
            return
        try:
            from gaia.store.writer import (
                find_dispatch_row_by_harness_agent_id,
                link_dispatch_continuation,
            )

            previous = find_dispatch_row_by_harness_agent_id(resumed_session_id)
            if previous is not None and previous.get("contract_id") != row.get("contract_id"):
                link_dispatch_continuation(
                    row.get("contract_id"),
                    continues_handoff_id=previous["id"],
                    harness_agent_id=resumed_session_id,
                )
        except Exception:
            return

    @staticmethod
    def _resolved_attestation(event: HookEvent) -> "Attestation | None":
        """Resolve the presented claim against the issuing host's own record.

        This is the provenance check the lane was missing: the plugin can only
        present a token some Gaia-side process minted and wrote down, and every
        field of the claim must equal that record. A claim the caller composed
        resolves to nothing however well formed it is.

        The record is looked up in the host run this process belongs to, never
        in one the event names. A claim carrying its own ledger namespace would
        only ever be checked for agreement with itself -- the token would be
        verified against a store the claimant chose -- so the namespace comes
        from ``host_run_id`` and the event's own ``hostRun`` is not read at all.
        """
        # Imported inside the call, not at module scope: modules.security's
        # package init imports back through adapters, so a top-level import of
        # anything inside it fails every hook entry point on a circular import.
        from modules.security.host_attestation import host_run_id, resolve

        context = event.role_context
        if context is None:
            return None
        return resolve(
            host_run=host_run_id(),
            token=context.attestation,
            session_id=event.session_id,
            role=context.role,
            issuer=context.issuer,
        )

    @classmethod
    def _is_attested_control_plane(cls, event: HookEvent) -> bool:
        """Whether this event carries a control-plane claim with provenance."""
        context = event.role_context
        if context is None or not context.claims_control_plane_shape:
            return False
        record = cls._resolved_attestation(event)
        return record is not None and record.depth == 0 and not record.granted_by

    @classmethod
    def _identity_rejection(cls, event: HookEvent, tool_name: str) -> str | None:
        """Reject a forged, unattested, or unauthorized identity claim.

        Every route by which a mere string could confer the control-plane lane is
        closed here, before Gaia's policy sees the claim: a declared agent name
        disagreeing with the attested context, a claim from any issuer other than
        the runtime, an unattested control-plane role, a control-plane name
        declared with no attested context, and a dispatch carrying no claim.
        """
        payload = event.payload
        context = event.role_context
        declared = str(payload.get("agent") or payload.get("agent_type") or "").strip()
        if context is None:
            if declared.lower() in _CONTROL_PLANE_ROLES:
                return "OpenCode control-plane role was declared without an attested runtime context"
        else:
            if declared and declared != context.role:
                return "OpenCode role identity does not match the structured runtime context"
            if context.issuer != _TRUSTED_ROLE_ISSUER:
                return "OpenCode role context has an untrusted issuer"
            if (
                context.role.strip().lower() in _CONTROL_PLANE_ROLES
                and not cls._is_attested_control_plane(event)
            ):
                return "OpenCode control-plane role is not attested by the runtime"
        if tool_name == "task" and not cls._is_attested_control_plane(event):
            return "ordinary OpenCode agents cannot issue control-plane dispatches"
        return None

    @staticmethod
    def _identity_gap(payload: dict) -> str:
        """The plugin's account of why the session presents no claim, or empty."""
        return str(payload.get("identityGap") or "").strip()

    @classmethod
    def _with_identity_gap(cls, reason: str, payload: dict) -> str:
        """Name the plugin's gap after the verdict so a refused root and a refused child read differently.

        The gap is a diagnosis the plugin composed and is never trusted for
        the verdict itself; it only makes the refusal the user sees, and the
        row it leaves, say which session state produced it.
        """
        gap = cls._identity_gap(payload)
        return f"{reason} ({gap})" if gap else reason

    @classmethod
    def _record_identity_refusal(
        cls, event: HookEvent, tool_name: str, reason: str,
    ) -> None:
        """Leave one durable row for an identity/control-plane refusal.

        Never raises and never affects the verdict: the caller has already
        decided the denial and ignores this method's outcome entirely.

        The attestation token value is never recorded -- in no field, no
        encoded form of it, not even a hash.

        Accepted gap: EventWriter.write_event swallows every exception by
        design, so a failed write loses the audit row, never the denial.
        """
        try:
            from modules.events.event_writer import EventWriter

            payload = event.payload if isinstance(event.payload, dict) else {}
            declared = str(
                payload.get("agent") or payload.get("agent_type") or ""
            ).strip()
            context = event.role_context
            attested = False
            resolved = False
            try:
                attested = bool(cls._is_attested_control_plane(event))
            except Exception:
                attested = False
            try:
                resolved = cls._resolved_attestation(event) is not None
            except Exception:
                resolved = False
            agent_name = declared
            if not agent_name and context is not None:
                agent_name = context.role
            meta: Dict[str, Any] = {
                "session_id": event.session_id,
                "tool": tool_name,
                "reason": reason,
                "agent_presented": bool(declared),
                "declared_agent": declared,
                "role_context_present": context is not None,
                "role": context.role if context is not None else "",
                "attested": attested,
                "attestation_present": bool(
                    getattr(context, "attestation", "") or ""
                ),
                "attestation_resolved": resolved,
                "identity_gap": cls._identity_gap(payload),
            }
            EventWriter().write_event(
                _IDENTITY_REFUSAL_EVENT,
                "opencode-adapter",
                agent_name,
                reason,
                severity="warning",
                meta=meta,
            )
        except Exception as exc:  # pragma: no cover - telemetry must never block
            logger.debug("identity refusal audit write failed (non-fatal): %s", exc)

    @classmethod
    def _child_binding_backstop_denial(cls, event: HookEvent) -> "HookResponse | None":
        """Fail-closed backstop for a dispatched child's tool calls before its
        own binding has landed (plan 65, T10, rule 3).

        ``event.host_agent_id`` is the plugin's own ``dispatchHandle``: truthy
        for exactly a non-primary (dispatched) session, undefined/absent for
        the root session (plugin.ts's own docstring on that predicate). A
        dispatched child is therefore identifiable from its very FIRST tool
        call -- well before ``message.part.updated`` can report the
        callID<->child-session pair that binds it
        (``bind_harness_child_session``, ``adapt_subagent_start``). Until that
        bind lands ``harness_agent_id`` for this exact session, the call is
        denied here, and the reason NAMES this backstop as the emitter so a
        deny from any other policy lane is never mistaken for it.

        A resolution failure denies too: an importable-but-erroring binding
        check must not silently open a fail-closed gate.
        """
        if not event.host_agent_id:
            return None
        try:
            from gaia.store.writer import is_harness_session_bound

            bound = is_harness_session_bound(event.session_id)
        except Exception:
            bound = False
        if bound:
            return None
        return HookResponse(
            output={
                "action": "deny",
                "reason": (
                    f"{_CHILD_BINDING_BACKSTOP_EMITTER}: dispatched session "
                    f"{event.session_id!r} is not yet bound to its Task "
                    "call -- denying its tool call fail-closed until "
                    "message.part.updated reports the callID<->child-session "
                    "pair"
                ),
            },
            exit_code=2,
        )

    def build_policy_payload(self, event: HookEvent) -> Dict[str, Any]:
        """Normalize one OpenCode event into the payload Gaia's policy reads.

        The attested context crosses as a plain mapping because neutral policy
        gates the control-plane lane on a mapping; an adapter dataclass would
        fail that gate silently while still looking present in the payload.
        """
        payload = dict(event.payload)
        payload.update(
            {
                "tool_name": self._policy_tool_name(payload.get("tool_name", "")),
                "tool_input": self._policy_tool_input(
                    payload.get("tool_name", ""), payload.get("tool_input", {}),
                ),
                "session_id": event.session_id,
                "tool_use_id": event.call_id or "",
                "agent_id": event.host_agent_id or "",
                "agent_type": self._policy_agent_type(event),
                "role_context": self._forward_role_context(event),
            }
        )
        return payload

    @classmethod
    def _forward_role_context(cls, event: HookEvent) -> Dict[str, Any] | None:
        """Forward the claim as a plain mapping only once provenance resolves.

        This is the single boundary where the attested claim becomes the mapping
        neutral policy reads, and the classifier downstream confers the
        control-plane role on the mere presence of ``verified``, ``issuer`` and
        ``attestation``. A claim whose token does not resolve against the
        issuing host's ledger therefore crosses stripped of those three fields
        and renamed, so no presence-only predicate downstream can be handed a
        lane this side did not verify. The mapping records who granted the
        claim and at what delegation depth: unbounded minting is refused at
        issuance, and the record of the grant travels with the claim.
        """
        context = event.role_context
        if context is None:
            return None
        record = cls._resolved_attestation(event)
        if record is None:
            return {
                "role": _UNATTESTED_AGENT_TYPE
                if context.role.strip().lower() in _CONTROL_PLANE_ROLES
                else context.role,
                "capabilities": [],
                "issuer": "",
                "attestation": "",
                "verified": False,
                "provenance": "unresolved",
            }
        forwarded = asdict(context)
        forwarded.update(
            {
                "provenance": "host-issued",
                "granted_by": record.granted_by,
                "delegation_depth": record.depth,
            }
        )
        return forwarded

    @classmethod
    def _policy_agent_type(cls, event: HookEvent) -> str:
        """Name the caller for policy without letting a name confer authority.

        Neutral policy reads an absent agent_type as the control plane, so an
        unattested OpenCode caller is named explicitly rather than left blank.
        A control-plane spelling is withheld unless the runtime attested the
        claim: the string route into that lane is closed at the one site that
        produces ``agent_type``, so it stays closed for a caller that reaches
        policy without passing ``_identity_rejection`` first.
        """
        context = event.role_context
        if context is not None:
            if (
                context.role.strip().lower() in _CONTROL_PLANE_ROLES
                and not cls._is_attested_control_plane(event)
            ):
                return _UNATTESTED_AGENT_TYPE
            return context.role
        payload = event.payload
        declared = str(payload.get("agent") or payload.get("agent_type") or "").strip()
        if not declared or declared.lower() in _CONTROL_PLANE_ROLES:
            return _UNATTESTED_AGENT_TYPE
        return declared

    @staticmethod
    def _policy_tool_name(tool_name: object) -> str:
        """Map OpenCode's lowercase built-ins to Gaia's policy tool names."""
        names = {
            "bash": "Bash", "task": "Task", "write": "Write", "edit": "Edit",
            "apply_patch": "Edit", "read": "Read", "glob": "Glob", "grep": "Grep",
            "list": "LS",
        }
        return names.get(str(tool_name).lower(), str(tool_name))

    @staticmethod
    def _policy_tool_input(tool_name: object, tool_input: object) -> object:
        """Rename OpenCode's read and grep arguments to the names Gaia's policy reads.

        OpenCode's read takes ``filePath`` and its grep ``include``; the shared
        policy reads ``file_path`` and ``glob``, as Claude Code sends them.
        """
        if not isinstance(tool_input, dict):
            return tool_input
        renames = {"read": {"filePath": "file_path"}, "grep": {"include": "glob"}}
        mapping = renames.get(str(tool_name).lower(), {})
        normalized = dict(tool_input)
        for source, target in mapping.items():
            if source in normalized and target not in normalized:
                normalized[target] = normalized[source]
        return normalized

    @staticmethod
    def _format_policy_verdict(verdict: PolicyVerdict) -> HookResponse:
        """Format a shared-policy verdict in the plugin's ``action`` protocol.

        A consent request with a pending approval becomes a denial naming it,
        since that signature is asked out of band; one without becomes an ask
        of the host's own prompt. The approval id travels as a field, so the
        plugin never reads it back out of the reason. ``presentable`` marks a
        named approval that already carries its requester's phrases: only that
        one is presented, while a phraseless reactive placeholder reaches the
        specialist as the denial's request line (PD10).
        """
        if verdict.refusal is not None:
            return HookResponse(
                output={"action": "deny", "reason": verdict.refusal}, exit_code=2,
            )
        if verdict.consent is not None:
            decision = "deny" if verdict.consent.approval_id is not None else "ask"
            reason = verdict.consent.reason
            updated_input = None if decision == "deny" else verdict.consent.updated_input
        else:
            decision, reason, updated_input = verdict.decision, verdict.reason, verdict.updated_input
        if decision is None:
            return HookResponse(output={"action": "allow"}, exit_code=0)
        output: Dict[str, Any] = {"action": decision, "reason": reason}
        if isinstance(updated_input, dict) and updated_input:
            output["updated_input"] = updated_input
        if verdict.approval_id:
            output["approval_id"] = verdict.approval_id
            if decision == "deny" and _carries_requester_phrases(verdict.approval_id):
                output["presentable"] = True
        return _fail_closed(output, 0)

    @classmethod
    def _without_unverified_decision(cls, event: HookEvent, container: Any) -> Any:
        """Withhold a structured decision whose provenance Gaia did not verify.

        ``answers`` in the tool result and in the tool arguments both cross the
        bridge as caller-supplied JSON, so a forged ``tool.execute.after``
        carrying them must never read as the user's decision to anything
        downstream. The discriminator that decides which results carry answers
        lives in ``plugin.ts``, on the untrusted side of that stdin, so it
        fences nothing.

        The key is withheld rather than the event denied: the tool has already
        run, so a denial would gate nothing while discarding the audit record.
        A signature is decided only by ``gaia approvals opencode-decide``, which
        the plugin runs from the question's own requestID reply.
        """
        if not isinstance(container, dict) or "answers" not in container:
            return container
        if cls._is_attested_control_plane(event) and event.call_id:
            return container
        return {key: value for key, value in container.items() if key != "answers"}

    def adapt_post_tool_use(self, event: HookEvent) -> HookResponse:
        """Run post-tool policy with the same immutable call identity.

        The identity normalization is ``build_policy_payload``'s, not a second
        copy of it, so a fix to how a name becomes ``agent_type`` cannot land on
        one path and miss the other. ``_identity_rejection`` is deliberately not
        run here: the tool has already executed, so a denial would gate nothing
        while discarding the audit record of what ran. What this path does
        withhold is a structured decision, from both containers it could ride
        in. The call's outcome is read by this adapter's own
        ``parse_post_tool_use``. A Task result's contract summary line returns
        as ``additional_context``, which the plugin appends to the tool output.
        """
        from modules.agents.task_result_observer import TASK_TOOL_NAMES

        payload = self.build_policy_payload(event)
        payload["tool_input"] = self._without_unverified_decision(
            event, payload.get("tool_input", {})
        )
        payload["tool_response"] = self._without_unverified_decision(
            event, event.payload.get("tool_response", {})
        )
        if payload.get("tool_name") in TASK_TOOL_NAMES:
            payload["tool_response"] = self._as_shared_task_result(payload["tool_response"])
        policy_event = HookEvent(
            event_type=event.event_type,
            session_id=event.session_id,
            payload=payload,
            distribution=event.distribution,
            host_agent_id=event.host_agent_id,
            dispatch_id=event.dispatch_id,
            parent_dispatch_id=event.parent_dispatch_id,
            call_id=event.call_id,
            role_context=event.role_context,
        )
        if payload.get("tool_name") == "AskUserQuestion":
            # A signature is decided only by `gaia approvals opencode-decide`,
            # which the plugin runs from the question's own requestID reply; an
            # answer in a tool result is never a decision here.
            return HookResponse(output={"action": "allow"}, exit_code=0)
        verdict = ToolPolicy().post_tool_verdict(
            policy_event, self.parse_post_tool_use(payload),
        )
        response = self._format_policy_verdict(verdict)
        if verdict.context and response.output.get("action") == "allow":
            response.output["additional_context"] = verdict.context
        return response

    @staticmethod
    def _as_shared_task_result(tool_response: Any) -> Any:
        """Translate an OpenCode Task result into the fields the shared Task observer reads.

        OpenCode names the child only as ``metadata.sessionId``, the id its row
        is bound to, and marks a background launch with ``metadata.background``
        and no status; without Claude Code's ``async_launched`` the observer
        would read that launch as a completed turn and record a false cut.
        """
        if not isinstance(tool_response, dict):
            return tool_response
        metadata = tool_response.get("metadata")
        if not isinstance(metadata, dict):
            return tool_response
        translated = dict(tool_response)
        child_session = metadata.get("sessionId")
        if not translated.get("agentId") and isinstance(child_session, str) and child_session:
            translated["agentId"] = child_session
        if metadata.get("background") is True and not translated.get("status"):
            translated["status"] = "async_launched"
        return translated

    def adapt_subagent_stop(self, event: HookEvent) -> HookResponse:
        """Gate a dispatched child's turn on session.idle or session.error, then close its row; for the main session, beat its heartbeat.

        OpenCode does not await these events, so a rejection cannot hold the
        turn open. It returns ``repair_prompt`` for the plugin to send the
        child as its next turn and leaves the row unclosed for the child's own
        finalize. ``user_message`` and ``orchestrator_notice`` are appended by
        the plugin to the parent's Task result, which the orchestrator reads
        under both the TUI and ``opencode run``.

        The session id is the harness_agent_id ``bind_harness_child_session``
        stamped at ``message.part.updated``, so a child with no tool call is
        still found. A session bound to no row is the main one ending a turn:
        that heartbeat is what keeps its worktrees and pending approvals live,
        and what lets a killed OpenCode process, which emits no close event, go
        stale.
        """
        row = self._bound_child_row(event.session_id)
        outcome = self._gate_child_turn(event, row) if row is not None else None
        if outcome is not None and outcome.rejected:
            return HookResponse(output={
                "contract_valid": False,
                "repair_prompt": outcome.result.get("contract_rejection_reason", ""),
                "orchestrator_notice": self._repair_notice(event, outcome.result),
                "closed": {"status": "repair_requested", "contract_id": row.get("contract_id")},
            })
        closed = self._close_bound_row(event)
        if closed.get("status") == "no_row":
            from modules.session.session_lifecycle import refresh_heartbeat

            refresh_heartbeat(event.session_id)
        output: Dict[str, Any] = {"contract_valid": True, "closed": closed}
        if outcome is not None:
            output.update({
                key: outcome.result[key]
                for key in ("contract_circuit_open", "contract_rejection_count", "episode_id")
                if key in outcome.result
            })
            if outcome.user_message:
                output["user_message"] = outcome.user_message
        return HookResponse(output=output)

    @staticmethod
    def _gate_child_turn(event: HookEvent, row: dict) -> "SubagentStopOutcome":
        """Run the host-neutral SubagentStop close for the child session bound to ``row``.

        OpenCode's stop event names only the child session and the agent the
        plugin bound it to, so the dispatching session comes from the row the
        dispatch birthed and the child's output is left to the core's row
        reconstruction.
        """
        from . import subagent_stop_core

        parent_session_id = row.get("session_id") or ""
        agent_type = str(event.payload.get("agent") or "")
        stop_event = str(event.payload.get("event") or "")
        hook_data = {
            "hook_event_name": "SubagentStop",
            "session_id": parent_session_id,
            "agent_type": agent_type,
            "agent_id": event.session_id,
            "stop_reason": stop_event,
        }
        host = subagent_stop_core.SubagentStopHost(
            agent_roster=subagent_stop_core.gaia_agent_roster,
            classify_stop_reason=_classify_stop_event,
            resume_map_dir=None,
        )
        completion = AgentCompletion(
            agent_type=agent_type,
            agent_id=event.session_id,
            transcript_path="",
            last_message="",
            session_id=parent_session_id,
        )
        return subagent_stop_core.run_subagent_stop(
            host, hook_data, completion, event_session_id=parent_session_id or None,
        )

    @staticmethod
    def _repair_notice(event: HookEvent, result: dict) -> str:
        from modules.agents.rejection_circuit import max_rejections

        agent = event.payload.get("agent") or "The specialist"
        return (
            f"{agent} ended its turn without a finalized contract, so Gaia returned "
            f"the turn to it for repair (rejection {result.get('contract_attempts', 1)} "
            f"of {max_rejections()}). This task result is not a valid close: continue "
            f"task_id {event.session_id} to collect the repaired one."
        )

    @staticmethod
    def _bound_child_row(session_id: str) -> dict | None:
        from gaia.store.writer import find_dispatch_row_by_harness_agent_id

        try:
            return find_dispatch_row_by_harness_agent_id(str(session_id)) if session_id else None
        except Exception:
            logger.warning("Child row lookup failed for session %s", session_id, exc_info=True)
            return None

    def adapt_session_end(self, event: HookEvent) -> HookResponse:
        """On session.deleted, close the dispatched child's row, or unregister the main session.

        Rows a main session dispatched are bound to their children's session
        ids, so unregistering it leaves them for their own close events.
        """
        outcome = self._close_bound_row(event)
        if outcome.get("status") == "no_row":
            from modules.session.session_lifecycle import end_session

            end_session(event.session_id)
        return HookResponse(output={"contract_valid": True, "closed": outcome})

    @staticmethod
    def _close_bound_row(event: HookEvent) -> dict:
        from modules.agents import dispatch_lifecycle

        return dispatch_lifecycle.resolve_close(
            harness_agent_id=event.session_id, session_id=event.session_id,
        ) or {"status": "error"}

    def adapt_pre_compact(self, event: HookEvent) -> HookResponse:
        """Return what OpenCode's compaction must carry forward: a bound child's kernel, or a main session's compaction context.

        Answers the experimental ``experimental.session.compacting`` hook,
        whose context survives the summary that discards the session's prior
        messages. A dispatched child gets back the kernel its row was born
        with (plan 65, task 12; AC-7). A session the plugin marks ``main`` gets
        what Claude Code's SessionStart(compact) delivers, from the same
        ``start_context``, followed by the instructions that steer the summary.
        Both travel as ``context`` entries, which the host appends to its own
        summary prompt; replacing the prompt (``output.prompt``) would discard
        the host's summary template, which Gaia has no stake in owning.

        Degrades to a plain allow whenever there is nothing to inject or
        building it fails: a compaction must never be blocked by injection,
        the rule _adapt_task_with_kernel applies to a Task dispatch.
        """
        kernel = self._bound_child_kernel(event.session_id, str(event.payload.get("agent") or ""))
        if kernel:
            return HookResponse(
                output={"action": "allow", "updated_input": {"context": [kernel]}}
            )
        if event.payload.get("main") is True:
            from modules.context.compact_context_builder import build_summary_instructions
            from modules.session.session_lifecycle import start_context

            context = start_context("compact", [], event.session_id)
            if context:
                return HookResponse(
                    output={
                        "action": "allow",
                        "updated_input": {"context": [context, build_summary_instructions()]},
                    }
                )
        return HookResponse(output={"action": "allow"})

    @staticmethod
    def _child_kernel(row: dict, agent_name: str) -> str | None:
        """Claude Code's SubagentStart kernel for *row* plus the skills block, or None without a contract.

        The skills block stands in for the preload OpenCode does not perform.
        """
        from modules.context.kernel_builder import build_kernel_context, build_skills_block

        kernel = build_kernel_context(row, agent_name=agent_name)
        if not kernel:
            return None
        return "\n\n".join(block for block in (kernel, build_skills_block(agent_name)) if block)

    @classmethod
    def _bound_child_kernel(cls, session_id: str, agent_name: str) -> str | None:
        """The kernel of the claimed dispatch row bound to this child session, or None.

        The session id is the harness_agent_id bind_harness_child_session
        stamped on the child's row (T10), so the lookup needs no correlation
        ladder.
        """
        from gaia.store.writer import find_dispatch_row_by_harness_agent_id

        try:
            row = find_dispatch_row_by_harness_agent_id(str(session_id)) if session_id else None
            if row is None or not row.get("claimed_at"):
                return None
            return cls._child_kernel(row, agent_name)
        except Exception:
            return None
