"""OpenCode event adapter.

OpenCode plugins call this adapter through a small JSON bridge rather than
Claude Code's stdin-hook protocol. The bridge supplies an immutable session,
tool call, and dispatch identity for every event. Lifecycle paths that have not
yet been wired to the plugin deny or report incomplete state instead of
silently accepting a governed operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Dict, FrozenSet

from modules.orchestrator.delegate_mode import ORCHESTRATOR_AGENT_TYPES

from .base import HookAdapter
from .types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ConsentRequest,
    ContextResult,
    HookEvent,
    HookEventType,
    HookResponse,
    HostCapability,
    HostDistribution,
    QualityResult,
    ToolResult,
    ValidationRequest,
    ValidationResult,
    VerificationResult,
    RoleCapabilityContext,
)

if TYPE_CHECKING:
    from modules.security.host_attestation import Attestation


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

_EVENT_TYPES = {
    "tool.execute.before": HookEventType.PRE_TOOL_USE,
    "tool.execute.after": HookEventType.POST_TOOL_USE,
    "message.part.updated": HookEventType.SUBAGENT_START,
    "session.idle": HookEventType.STOP,
    "session.error": HookEventType.POST_TOOL_USE_FAILURE,
    "session.deleted": HookEventType.SESSION_END,
    "session.compacted": HookEventType.POST_COMPACT,
    "session.compacting": HookEventType.PRE_COMPACT,
}

_PATCH_PATH_MARKER = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$"
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


def _apply_patch_paths(patch_text: object) -> list[str]:
    """Return every declared patch path, rejecting ambiguous patch envelopes."""
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ValueError("apply_patch requires non-empty patchText")
    paths: list[str] = []
    saw_file_operation = False
    for line in patch_text.splitlines():
        if line.startswith("*** ") and line not in {"*** Begin Patch", "*** End Patch"}:
            match = _PATCH_PATH_MARKER.fullmatch(line)
            if match is None:
                raise ValueError(f"unsupported apply_patch marker: {line}")
            saw_file_operation = True
            path = match.group(1).strip()
            if not path or path in {"/", ".", ".."} or "\x00" in path:
                raise ValueError("apply_patch contains an unsafe or empty path")
            paths.append(path)
    if not saw_file_operation or not paths:
        raise ValueError("apply_patch contains no recognized file operation")
    return paths


def _fail_closed(output: Dict[str, Any], exit_code: int) -> HookResponse:
    """Return a host response whose exit code cannot contradict a denial.

    The plugin reads the bridge's exit code before it reads the envelope, so a
    denial that exits zero would be read as a successful, permitted call.
    """
    return HookResponse(
        output=output,
        exit_code=2 if output.get("action") == "deny" else exit_code,
    )


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
        return BootstrapResult(
            should_scan=True,
            should_refresh=True,
            session_type="startup",
        )

    def format_bootstrap_response(self, result: BootstrapResult) -> HookResponse:
        return HookResponse(
            output={
                "should_scan": result.should_scan,
                "should_refresh": result.should_refresh,
                "session_type": result.session_type,
            }
        )

    def adapt_stop(self, raw: dict) -> QualityResult:
        return QualityResult()

    def adapt_task_completed(self, raw: dict) -> VerificationResult:
        return VerificationResult()

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

    def format_quality_response(self, result: QualityResult) -> HookResponse:
        return HookResponse(
            output={
                "quality_sufficient": result.quality_sufficient,
                "missing_elements": result.missing_elements,
            }
        )

    def format_verification_response(self, result: VerificationResult) -> HookResponse:
        return HookResponse(
            output={
                "criteria_met": result.criteria_met,
                "failed_items": result.failed_items,
                "block_completion": result.block_completion,
            }
        )

    def adapt_pre_tool_use(self, event: HookEvent) -> HookResponse:
        """Run the existing host-neutral policy through an OpenCode boundary.

        Gaia's policy flow still owns validation, grants, and audit state. This
        adapter supplies that flow with normalized OpenCode identities, then
        translates only the final host response back to the plugin protocol.
        """
        from .claude_code import ClaudeCodeAdapter

        payload = dict(event.payload)
        original_tool = str(payload.get("tool_name", "")).lower()
        rejection = self._identity_rejection(event, original_tool)
        if rejection is not None:
            return HookResponse(output={"action": "deny", "reason": rejection}, exit_code=2)
        backstop = self._child_binding_backstop_denial(event)
        if backstop is not None:
            return backstop
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
        policy_adapter = ClaudeCodeAdapter()
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
                checked = self._translate_policy_response(policy_adapter.adapt_pre_tool_use(path_event))
                if isinstance(checked.output, dict) and checked.output.get("action") != "allow":
                    return checked
            return HookResponse(output={"action": "allow"})
        if original_tool == "task":
            return self._adapt_task_with_kernel(policy_adapter, policy_event)
        response = policy_adapter.adapt_pre_tool_use(policy_event)
        return self._translate_policy_response(response)

    def _adapt_task_with_kernel(
        self, policy_adapter: "ClaudeCodeAdapter", policy_event: HookEvent,
    ) -> HookResponse:
        """Run the Task dispatch through the shared policy path, then --
        on allow -- prepend the just-born row's rendered kernel to the
        child's own prompt, in place (plan 65, task 9).

        Claude Code receives its kernel through a SEPARATE start event
        (SubagentStart) that fires before the subagent's first turn.
        OpenCode has no equivalent: its only start-adjacent signal
        (``message.part.updated``) merely reports the callID<->child-
        session binding, sometimes after the child has already acted. The
        one point this host reliably controls before the child's first
        action is THIS call -- the Task dispatch itself -- so the kernel
        is embedded directly into the dispatched prompt via
        ``updated_input``, applied field-by-field by the plugin's
        ``applyUpdatedInput`` (T6): only ``prompt`` changes, every other
        Task argument (``description``, ``subagent_type``, ...) passes
        through untouched.

        The delegated call already births the row with
        ``dispatch_tool_use_id=callID`` (``build_policy_payload`` forwards
        ``event.call_id`` as ``tool_use_id``, and
        ``_maybe_birth_dispatched_row`` stamps it); this method claims
        that SAME row by the SAME callID -- layer 0 of
        ``claim_dispatch_row``'s correlation ladder -- and renders its
        ``# Your Contract`` block with ``build_dispatch_kernel``. No
        second birth: claiming is a state transition on the row the
        delegated call already inserted, never a new insert.

        Degrades to the plain delegated response -- no kernel, prompt
        unmodified -- whenever the dispatch was denied/asked, or the
        claim/render step finds nothing (birth skipped, row already
        claimed, or a rendering error): a subagent dispatch must never be
        blocked by kernel injection.
        """
        response = policy_adapter.adapt_pre_tool_use(policy_event)
        translated = self._translate_policy_response(response)
        output = translated.output
        if not isinstance(output, dict) or output.get("action") != "allow":
            return translated

        try:
            from gaia.store.writer import claim_dispatch_row
            from modules.context.kernel_builder import build_dispatch_kernel
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

        try:
            kernel = build_dispatch_kernel(row)
        except Exception:
            kernel = None
        if not kernel:
            return translated

        tool_input = policy_event.payload.get("tool_input") or {}
        original_prompt = str(tool_input.get("prompt") or "")
        kernel_with_rules = f"{kernel}\n\n{CLOSING_RULES_KERNEL}"
        merged_prompt = (
            f"{kernel_with_rules}\n\n{original_prompt}"
            if original_prompt else kernel_with_rules
        )
        updated_input = dict(output.get("updated_input") or {})
        updated_input["prompt"] = merged_prompt
        output["updated_input"] = updated_input
        return translated

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
                "tool_input": payload.get("tool_input", {}),
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
            "apply_patch": "Edit",
        }
        return names.get(str(tool_name).lower(), str(tool_name))

    @staticmethod
    def _translate_policy_response(response: HookResponse) -> HookResponse:
        """Convert the legacy response envelope without leaking it to OpenCode."""
        output = response.output
        if not isinstance(output, dict):
            return HookResponse(
                output={"action": "deny", "reason": str(output)},
                exit_code=2,
            )

        if "action" in output:
            return _fail_closed(output, response.exit_code)

        specific = output.get("hookSpecificOutput")
        if isinstance(specific, dict) and specific.get("permissionDecision"):
            translated = {
                "action": specific["permissionDecision"],
                "reason": specific.get("permissionDecisionReason", ""),
            }
            if isinstance(specific.get("updatedInput"), dict):
                translated["updated_input"] = specific["updatedInput"]
            approval = re.search(r"approval_id:\s*(P-[A-Za-z0-9-]+)", translated["reason"])
            if approval:
                translated["approval_id"] = approval.group(1)
            return _fail_closed(translated, response.exit_code)

        return HookResponse(output={"action": "allow"}, exit_code=response.exit_code)

    def adapt_post_tool_use(self, event: HookEvent) -> HookResponse:
        """Run post-tool policy with the same immutable call identity.

        The identity normalization is ``build_policy_payload``'s, not a second
        copy of it, so a fix to how a name becomes ``agent_type`` cannot land on
        one path and miss the other. ``_identity_rejection`` is deliberately not
        run here: the tool has already executed, so a denial would gate nothing
        while discarding the audit record of what ran. The fence that matters is
        upstream, and the payload is safe without it only because
        ``_policy_agent_type`` withholds a control-plane name from an unattested
        caller on its own.
        """
        from .claude_code import ClaudeCodeAdapter

        payload = self.build_policy_payload(event)
        payload["tool_response"] = event.payload.get("tool_response", {})
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
        response = ClaudeCodeAdapter().adapt_post_tool_use(policy_event)
        return self._translate_policy_response(response)

    def adapt_subagent_stop(self, event: HookEvent) -> HookResponse:
        """Close the row bound to this session on a real lifecycle signal
        (session.idle/error/deleted -- ``Stop``/``PostToolUseFailure``/
        ``SessionEnd`` after mapping, plan 65, T11).

        Replaces the exit-2 stub: OpenCode never awaits this hook's return
        for these events (they carry no host decision to gate -- see
        ``LIFECYCLE_EVENT_TYPES`` in ``opencode/plugin.ts``), so this is a
        best-effort database write, never a permission verdict. ``resolve_close``
        (``dispatch_lifecycle``) does the actual work: it is keyed on
        ``event.session_id`` because that IS the harness_agent_id a dispatched
        child was bound under (``bind_harness_child_session`` stamps the
        CHILD's own OpenCode session id at ``message.part.updated`` time,
        before any tool call ever runs) -- so a child with ZERO tool calls is
        still found and closed here. The PRIMARY/root session's own
        session.idle resolves no bound row (never stamped by
        ``bind_harness_child_session``) and is a harmless no-op.
        """
        from modules.agents.dispatch_lifecycle import resolve_close

        outcome = resolve_close(
            harness_agent_id=event.session_id, session_id=event.session_id,
        )
        return HookResponse(output={"contract_valid": True, "closed": outcome})

    def adapt_pre_compact(self, event: HookEvent) -> HookResponse:
        """Reinject the claimed dispatch row's kernel before OpenCode's real
        compaction (experimental.session.compacting) discards prior context
        (plan 65, task 12; AC-7).

        OpenCode's compaction summarizes/discards the session's prior messages
        before the child's next turn -- the same row-bound kernel T9 prepends
        at dispatch would otherwise fall out of the child's live context with
        no re-delivery. This session's own id (event.session_id) is the same
        value bind_harness_child_session stamped as harness_agent_id on the
        child's row (T10), so find_dispatch_row_by_harness_agent_id resolves
        the exact row this compaction event belongs to with no correlation
        ladder.

        Degrades to a plain allow -- no context injected -- whenever nothing
        is bound to this session, claimed_at is absent, or the kernel render
        fails: a compaction must never be blocked by kernel injection, the
        same rule _adapt_task_with_kernel applies to a Task dispatch.
        """
        from gaia.store.writer import find_dispatch_row_by_harness_agent_id
        from modules.context.kernel_builder import build_dispatch_kernel

        session_id = event.session_id
        try:
            row = (
                find_dispatch_row_by_harness_agent_id(str(session_id))
                if session_id else None
            )
        except Exception:
            row = None
        if row is None or not row.get("claimed_at"):
            return HookResponse(output={"action": "allow"})

        try:
            kernel = build_dispatch_kernel(row)
        except Exception:
            kernel = None
        if not kernel:
            return HookResponse(output={"action": "allow"})

        return HookResponse(
            output={"action": "allow", "updated_input": {"context": [kernel]}}
        )
