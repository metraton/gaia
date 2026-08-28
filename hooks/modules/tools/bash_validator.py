"""
Bash command validator.

Primary security gate for all Bash tool invocations. With Bash(*) in the
settings.json allow list, ALL commands reach this hook -- it is the sole
enforcement layer for dangerous command detection.

5-Phase Pipeline:
  1. UNWRAP      -- ShellUnwrapper strips wrapper shells (bash -c, sh -c, ...);
                    depth >= _OBFUSCATION_DEPTH_LIMIT = permanent block.
                    Existing _detect_indirect_execution() runs as fallback for
                    eval, python -c, node -e, etc.
  2. DECOMPOSE   -- StageDecomposer splits into operator-linked stages.
  3. CLASSIFY    -- blocked_commands + cloud_pipe_validator + mutative_verbs
                    per stage (existing logic, unchanged).
  4. COMPOSITION -- cross-stage composition rules (exfiltration, RCE,
                    obfuscated exec via pipe analysis).
  5. AGGREGATE   -- combine stage results into final BashValidationResult.

Earlier flat-pipeline order preserved within phases for backward compat:
  - Footer stripping runs before phase 1 (EARLY NORMALIZATION)
  - Smart sanitization (strip nohup / trailing & / trailing redirect) also
    runs in EARLY NORMALIZATION, after footer stripping and after the
    gaia.db / subagent-memory / protected-path write guards, but before
    phase 1. It strips the decorator and lets the CLEANED command flow
    into phase 1 like any other command -- it never classifies the
    command itself, so a decorator can never move a command's tier by
    hiding it from phases 1-5 (see _try_sanitize_command's docstring).
  - Indirect execution detection is the first check in phase 1
  - Blocked commands run before cloud_pipe and mutative_verbs in phase 3

Phase 0 -- GAIA CLI ONLY GUARD (gaia_cli_only_guard.check): runs before
everything above, including footer stripping and smart sanitization. It is a
closed-by-default restriction for the ORCHESTRATOR role only (see that
module's docstring) and is a no-op for every other caller today, because
delegate_mode does not yet grant the orchestrator a Bash lane at all. It is
wired in ahead of any command-text transformation on purpose: it evaluates
the RAW string the harness sent, never a version something upstream already
normalized. Only invoked when a caller supplies `hook_payload` -- see
validate()'s own docstring for why "not supplied" and "supplied with an
empty role" are kept distinct rather than collapsed by a default.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from ..security.tiers import SecurityTier
from ..security.blocked_commands import is_blocked_command
from ..security.mutative_verbs import (
    detect_mutative_command,
    build_t3_block_response,
    cwd_after_component,
)
from ..security.flag_classifiers import (
    classify_by_flags,
    OUTCOME_BLOCKED as FLAG_BLOCKED,
    OUTCOME_MUTATIVE as FLAG_MUTATIVE,
)
from ..security.approval_grants import (
    check_approval_grant,
    confirm_grant,
    last_check_found_expired,
    match_command_set_grant,
    # DEPRECATED (T2.1 cutover): generate_nonce, write_pending_approval,
    # find_pending_for_command are no longer used in the T3 subagent intercept
    # path. They remain in approval_grants.py for M3/M4 consumers (e.g.,
    # filesystem-based activation in pre_tool_use) until those layers are
    # migrated. Do not re-introduce these calls in _validate_atomic_command.
)
from ..security.approval_messages import (
    build_t3_approval_instructions,
    build_t3_blocked_denial_message,
    build_t3_degraded_block_message,
)
from ..security.fail_open import clear_classification, note_mutative_classification
from ..security.shell_unwrapper import ShellUnwrapper
from ..security.gaia_db_write_guard import check as check_gaia_db_write
from ..security.subagent_memory_write_guard import (
    check as check_subagent_memory_write,
)
from ..security.protected_path_guard import (
    check as check_protected_path_write,
)
from ..security.shell_write_guard import check as check_shell_write
# gaia_cli_only_guard is NOT imported at module scope: it itself imports
# `..tools.stage_decomposer`, which (via this package's own __init__.py
# eagerly importing bash_validator) closes a circular-import loop back to
# this exact module. Importing it lazily, inside validate() at the point of
# use, is the same convention gaia_cli_only_guard.py already applies to its
# own delegate_mode import, for the identical reason: it avoids assuming
# either package finishes initializing before the other.
from ..security.composition_rules import (
    build_composition_stages,
    check_composition,
    CompositionDecision,
)
from .shell_parser import get_shell_parser
from .cloud_pipe_validator import validate_cloud_pipe
from .hook_response import (
    build_hook_permission_response,
    inject_updated_input,
    read_permission_decision,
    read_permission_reason,
)
from .stage_decomposer import StageDecomposer, DecomposedCommand

logger = logging.getLogger(__name__)

# Maximum wrapper depth before treating as obfuscation (permanent block).
_OBFUSCATION_DEPTH_LIMIT = 5


@dataclass
class BashValidationResult:
    """Result of Bash command validation."""
    allowed: bool
    tier: SecurityTier
    reason: str
    suggestions: List[str] = None
    modified_input: Optional[Dict[str, Any]] = None
    # When set, the caller should return this dict (exit 0) instead of a
    # plain error string (exit 2).  Used for structured block responses that
    # should correct the agent rather than terminate execution.
    block_response: Optional[Dict[str, Any]] = None
    # When a T3 command is allowed because it matched (and consumed) an active
    # grant, this carries the approval_id of that grant. The adapter stashes it
    # in HookState so the terminal event is appended to the approval_events
    # chain for this approval -- EXECUTED by PostToolUse on a clean exit, or
    # FAILED by the Stop-hook reconciliation on a non-zero exit (the host does
    # not fire PostToolUse then). None for non-T3 / no-grant paths.
    consumed_approval_id: Optional[str] = None
    command_set_reservation: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []


# Patterns for AI tool attribution footers (auto-stripped from commits).
# Covers Claude Code, GitHub Copilot, Aider, Windsurf, Codex, Gemini, the
# Anthropic model family (Opus/Sonnet/Haiku), and any future tool using the
# Co-authored-by git trailer convention.
#
# IMPORTANT: this list is the DETECTOR (`_detect_claude_footers`). It MUST stay
# aligned with the line patterns in `_strip_claude_footers` -- if the stripper
# can remove a footer the detector cannot see, the strip never fires (the
# early-normalization guard only strips when the detector returns True). Every
# footer shape the stripper removes has a corresponding detector entry here.
#
# None of these patterns anchor on a newline, so they also catch footers that
# arrive in a SECOND `-m "..."` argument (no preceding newline) -- the detector
# fires, and the stripper's `-m`-aware branch removes them.
FORBIDDEN_FOOTER_PATTERNS = [
    r"Generated with\s+Claude Code",
    r"Generated with\s+\[?Claude Code\]?",
    # Bare robot-emoji "Generated with ..." line (e.g. "🤖 Generated with ...")
    # WITHOUT requiring the literal "Claude Code" after it -- the stripper has
    # always removed this shape; the detector now sees it too.
    r"🤖\s*Generated with",
    # Robot emoji on its own is a strong AI-attribution signal.
    r"🤖",
    r"Co-Authored-By:\s+Claude\b",
    # Claude Code session trailer: "Claude-Session: https://claude.ai/code/..."
    # The trailer key and the session URL are independent signals -- either
    # alone identifies the footer.
    r"Claude-Session:",
    r"claude\.ai/code/session",
    # Anthropic model family attributed via Co-Authored-By / Co-authored-with.
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bOpus\b",
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bSonnet\b",
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bHaiku\b",
    # "Approved-by:" attribution trailer.
    r"Approved-by:",
    r"Co-authored-by:\s+GitHub Copilot\b",
    r"Co-authored-by:\s+aider\b",
    r"Co-authored-by:\s+Windsurf\b",
    r"Co-authored-by:\s+Cursor\b",
    r"Co-authored-by:\s+Codex\b",
    r"Co-authored-by:\s+Gemini\b",
]

# ---------------------------------------------------------------------------
# Indirect execution wrappers — commands that execute arbitrary strings.
# These bypass regex-based command blocking because the real command is
# hidden inside a string argument.  Classified as T2 (requires approval)
# so the user sees what will actually run.
# ---------------------------------------------------------------------------
# Optional prefix commands that can wrap any shell invocation.
# nohup, sudo, env, nice, etc. — the regex allows zero or more of these
# before the real interpreter token so "nohup bash -c ..." is still caught.
_WRAPPER_PREFIX = r"(?:(?:nohup|sudo|env|nice|ionice|setsid|strace|ltrace|time)\s+)*"

INDIRECT_EXEC_PATTERNS = [
    re.compile(r"^" + _WRAPPER_PREFIX + r"bash\s+-c\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"sh\s+-c\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"zsh\s+-c\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"dash\s+-c\s+", re.IGNORECASE),
    re.compile(r"^\s*eval\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"python3?\s+-c\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"node\s+-e\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"perl\s+-e\s+", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"ruby\s+-e\s+", re.IGNORECASE),
    # Process substitution and heredoc piped to shell
    re.compile(r"^" + _WRAPPER_PREFIX + r"bash\s+<\(", re.IGNORECASE),
    re.compile(r"^" + _WRAPPER_PREFIX + r"sh\s+<\(", re.IGNORECASE),
]

class BashValidator:
    """Validator for Bash tool invocations.

    Implements a 5-phase pipeline: unwrap -> decompose -> classify ->
    composition -> aggregate.  See module docstring for phase details.
    """

    def __init__(self):
        """Initialize validator with parser, unwrapper, and decomposer."""
        self.shell_parser = get_shell_parser()
        self._unwrapper = ShellUnwrapper()
        self._decomposer = StageDecomposer()

    def _resolve_bare_gaia_token_for_guard(self, command: str) -> str:
        """Rewrite a bare `gaia` binary token to its $PATH-resolved absolute path.

        For gaia_cli_only_guard.check() ONLY -- the string this returns is
        never executed, only handed to the guard's identity check.

        The guard itself deliberately requires the binary token to already be
        an absolute path in the command text (gaia_cli_only_guard.py, Design
        decision 2): it does not trust the ambient $PATH at analysis time,
        because that environment need not match the one the command actually
        runs under. That is the right call for the guard's own comparison,
        but left alone it would also deny a `gaia contract view` typed the
        normal way, since a bare word never satisfies `os.path.isabs`. This
        method is the wiring-side fix: it resolves the bare token through
        $PATH here, at the call site, and hands the guard an absolute-path
        candidate to run its package-provenance identity check against
        (is_trusted_gaia_binary) -- so a poisoned $PATH resolving to a
        binary no trusted package declares still fails and is denied
        exactly as before. Resolving does not
        widen what the guard accepts; it only lets a normally-typed
        invocation reach the same identity check an absolute path already
        would.

        Returns *command* unchanged when there is no bare `gaia` token in
        binary position of any stage, or when $PATH has no `gaia` to find --
        the guard's own absolute-path check then denies as it always did.
        """
        decomposed = self._decomposer.decompose(command)
        if not decomposed.stages:
            return command

        resolved_path: Optional[str] = None
        rewrote_any = False
        pieces: List[str] = []

        for stage in decomposed.stages:
            text = stage.command
            is_bare_gaia_token = (
                stage.args
                and stage.args[0] == "gaia"
                and text[:4] == "gaia"
                and (len(text) == 4 or text[4] in (" ", "\t"))
            )
            if is_bare_gaia_token:
                if resolved_path is None:
                    which_result = shutil.which("gaia") or ""
                    resolved_path = which_result if os.path.isabs(which_result) else ""
                if resolved_path:
                    text = resolved_path + text[4:]
                    rewrote_any = True
            pieces.append(text)
            if stage.operator is not None:
                pieces.append(f" {stage.operator} ")

        if not rewrote_any:
            return command
        return "".join(pieces)

    def _detect_indirect_execution(self, command: str) -> Optional[BashValidationResult]:
        """Detect indirect execution wrappers that can bypass regex blocking.

        Commands like 'bash -c "az group delete"' hide the real command inside
        a string.  We classify these as T2 (mutative) so they require user
        approval via the nonce workflow, giving the human a chance to inspect
        what will actually run.

        Returns BashValidationResult if indirect execution detected, else None.
        """
        for pattern in INDIRECT_EXEC_PATTERNS:
            if pattern.search(command):
                # Also check if the inner payload contains a blocked command.
                # Extract the string argument after the wrapper.
                inner = self._extract_inner_command(command)
                if inner:
                    blocked = is_blocked_command(inner)
                    if blocked.is_blocked:
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason=(
                                f"Indirect execution of blocked command detected: "
                                f"{blocked.category} (via wrapper)"
                            ),
                            suggestions=[
                                blocked.suggestion or "Run the command directly instead of via a shell wrapper.",
                            ],
                        )

                # Not blocked but still indirect — route through approval
                logger.info("Indirect execution detected: %s", command[:80])
                result = detect_mutative_command(command)
                if result.is_mutative:
                    return None  # Already mutative, will be caught by mutative_verbs

                # For interpreters with inline code analysis (python3 -c),
                # mutative_verbs.py has dedicated pattern scanning that
                # distinguishes safe code (json.dumps, sys.version) from
                # dangerous code (os.system, subprocess.run). If it classified
                # the inline code as safe, trust that analysis and allow it
                # through without forcing an "ask" dialog.
                from ..security.mutative_verbs import _INLINE_CODE_CLIS
                base_cmd = command.strip().split()[0].rsplit("/", 1)[-1].lower()
                if base_cmd in _INLINE_CODE_CLIS:
                    logger.info(
                        "Inline code classified as safe by pattern scanner: %s",
                        command[:80],
                    )
                    return None  # Safe inline code, proceed to normal validation

                # Shell wrappers (bash -c, eval, etc.) hide the real command
                # in a string — no dedicated scanner exists. Force "ask" so
                # the user can inspect what will actually run.
                #
                # Inspect the inner command to identify the mutative verb so
                # the user sees a more informative message
                # (e.g. "inner mutative verb 'mv'"). Falls back to generic
                # message when inner has no mutative verb.
                reason_msg = "Indirect execution wrapper detected — requires confirmation"
                if inner:
                    inner_result = detect_mutative_command(inner)
                    if inner_result.is_mutative and inner_result.verb:
                        reason_msg = (
                            f"Indirect execution detected: inner mutative verb "
                            f"'{inner_result.verb}' — requires confirmation"
                        )
                dialog_msg = (
                    "Indirect execution detected. The command uses a shell "
                    "wrapper (bash -c, eval, etc.) that can bypass "
                    "security checks. Please confirm you want to run this, "
                    "or use discrete commands or a script file / python3 "
                    "<file> instead of bash -c/eval."
                )
                hook_block = build_hook_permission_response("ask", dialog_msg)
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T2_DRY_RUN,
                    reason=reason_msg,
                    block_response=hook_block,
                )
        return None

    def _extract_inner_command(self, command: str) -> Optional[str]:
        """Extract the inner command from an indirect execution wrapper.

        E.g., 'bash -c "az group delete --name foo"' → 'az group delete --name foo'
        """
        # Match: shell -c "..." or shell -c '...'
        match = re.search(r"""-[ce]\s+(['"])(.*?)\1""", command, re.DOTALL)
        if match:
            return match.group(2).strip()
        # Match: shell -c ... (unquoted, take rest of line)
        match = re.search(r"-[ce]\s+(\S+.*)", command)
        if match:
            return match.group(1).strip()
        return None

    def _has_operators(self, command: str) -> bool:
        """Quick check if command has operators (before parsing).

        Detects pipes, logical operators, semicolons, redirects, and
        background operators.  This is a fast pre-filter — the full
        shell parser handles quote-aware splitting downstream.

        Note: '>' and '&' are included so a command still carrying one of
        these tokens after EARLY NORMALIZATION's sanitization pass (e.g. an
        embedded ``2>&1``, or a redirect that is not the trailing one
        sanitization strips) is still routed through the compound-parsing
        path below instead of being mis-treated as a simple command.
        """
        # Fast check for common operators outside quotes
        # This avoids expensive parsing for 70% of commands
        if not any(op in command for op in ['|', '&&', '||', ';', '\n', '>', '&']):
            return False
        return True

    # Regex patterns for operators that can be safely stripped from commands.
    # Applied after quote-masking to avoid false positives.
    _NOHUP_PREFIX_RE = re.compile(r"^\s*nohup\s+")
    _TRAILING_BG_RE = re.compile(r"\s*&\s*$")
    _REDIRECT_RE = re.compile(r"\s*>{1,2}\s*\S+\s*$")
    # Fd duplication (2>&1) is harmless and should NOT be stripped.
    _FD_DUP_RE = re.compile(r"\d+>&\d+")

    def _try_sanitize_command(self, command: str) -> Optional[tuple[str, List[str]]]:
        """Strip a decorator that changes nothing about WHAT will run.

        Sanitizable patterns (stripped without changing semantics):
        - nohup prefix:  ``nohup cmd args`` -> ``cmd args``
        - trailing &:    ``cmd args &``      -> ``cmd args``
        - trailing redirect: ``cmd args > file`` -> ``cmd args``

        This method ONLY strips; it does NOT decide the command's tier. The
        caller (``validate()``, EARLY NORMALIZATION section) re-enters the
        FULL classification pipeline on the cleaned command -- unwrap,
        decompose, blocked-command checks, cloud-pipe checks, composition,
        and mutative-verb detection all run on the cleaned string exactly as
        they would on any other command. A decorator must never be able to
        move a command from T3 to T0 by hiding it from that pipeline; see
        ``tests/hooks/test_sanitizer_redirect_t3_evasion.py`` for the
        vulnerability this fixes and the regression test that guards it.

        Pipes and chaining operators (&&, ||, ;) are NOT touched here --
        they change data flow / composition and are handled by the
        cloud-pipe and composition checks downstream, not by this stripper.

        Returns:
            (cleaned_command, stripped_parts) if a decorator was stripped,
            else None (nothing to sanitize).
        """
        original = command
        cleaned = command
        stripped_parts = []

        # Strip nohup prefix
        if self._NOHUP_PREFIX_RE.match(cleaned):
            cleaned = self._NOHUP_PREFIX_RE.sub("", cleaned).strip()
            stripped_parts.append("nohup")

        # Strip trailing & (background) but not && or >&
        # Mask fd duplications first to avoid false matching
        test_str = self._FD_DUP_RE.sub("", cleaned)
        if self._TRAILING_BG_RE.search(test_str):
            cleaned = self._TRAILING_BG_RE.sub("", cleaned).strip()
            stripped_parts.append("&")

        # Strip trailing redirect (> file or >> file)
        # Only strip if it's at the end of the command
        test_str = self._FD_DUP_RE.sub("", cleaned)
        redirect_match = self._REDIRECT_RE.search(test_str)
        if redirect_match:
            # Find the position in the original cleaned string
            # We need to remove from the redirect operator onward
            pos = cleaned.rfind(">")
            if pos > 0:
                before_redirect = cleaned[:pos].rstrip()
                # Only strip if the > is not inside a flag value like --output=>
                if before_redirect and not before_redirect.endswith("="):
                    cleaned = before_redirect
                    stripped_parts.append("> redirect")

        if not stripped_parts:
            return None  # Nothing to sanitize

        if cleaned == original:
            return None  # Sanitization didn't change anything

        return cleaned, stripped_parts

    def validate(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        hook_payload: Optional[Dict[str, Any]] = None,
    ) -> BashValidationResult:
        """
        Validate a Bash command through the 5-phase pipeline.

        Phases:
            1. UNWRAP      - strip shell wrappers, detect obfuscation
            2. DECOMPOSE   - split into operator-linked stages
            3. CLASSIFY    - blocked_commands + cloud_pipe + mutative_verbs
            4. COMPOSITION - cross-stage pattern checks (stub for T4)
            5. AGGREGATE   - combine results into final verdict

        A phase 0, ahead of all of the above, is described where it runs
        below (GAIA CLI ONLY GUARD).

        Args:
            command: Command string to validate
            is_subagent: True when running in subagent context
            session_id: Session ID for approval scoping
            agent_type: Name of the originating agent (for pending approval context)
            hook_payload: The full stdin JSON dict from the harness, when the
                caller has one. The live adapter path (claude_code.py's
                _adapt_bash) always supplies it; back-compat call sites that
                predate it (validate_bash_command(), pre_tool_use.py's
                _handle_bash, and direct-construction tests) do not, and
                leaving it unset -- `None`, never a substituted `{}` -- is
                what keeps the guard below correctly inert for them instead
                of misreading their absence as an empty orchestrator role.

        Returns:
            BashValidationResult with validation details
        """
        # The fail-open breadcrumb describes THE COMMAND BEING GATED, so it is
        # dropped here rather than left to expire with the process. Entering
        # this method is the only moment at which a new command becomes the
        # subject of the gate; anything noted before it belongs to a previous
        # command, and a later gate failure reading it would deny this one on a
        # classification that was never about it. Ahead of Phase 0 because that
        # guard can return early, and an early return must not be a way for the
        # stale mark to survive. See modules.security.fail_open.
        clear_classification()

        # ================================================================
        # PHASE 0: GAIA CLI ONLY GUARD (orchestrator role closure)
        # First guard in the pipeline -- ahead of the three write guards
        # below and ahead of EARLY NORMALIZATION -- because it is a
        # closed-by-default restriction keyed on the CALLER'S ROLE, and
        # nothing may transform `command` (footer stripping, nohup/&/
        # redirect sanitization) before that role-based closure evaluates
        # the string exactly as the harness sent it.
        #
        # `hook_payload` is passed through to the guard UNCHANGED, never
        # flattened into a pre-extracted role flag here -- see
        # gaia_cli_only_guard.py's own docstring (Design decision 1) for
        # why an intermediate flattening is itself a place the "empty
        # field reads as false" failure mode could be reintroduced.
        #
        # `hook_payload is None` (this parameter's own absence) and "a
        # payload whose agent_type happens to be empty" are two distinct
        # cases, deliberately not collapsed by a shared default:
        #   - None: no caller supplied a payload at all (a legacy call
        #     site -- see this method's docstring) -- the guard does not
        #     apply, and validate() proceeds exactly as it always has.
        #   - a dict, even an empty one or one with agent_type=="": this
        #     IS the harness payload, so the guard runs. An empty
        #     agent_type classifies as the orchestrator role inside
        #     classify_session_role (Gaia's installer writes
        #     `agent: gaia-orchestrator` into settings, and a session that
        #     never got that field populated is still the orchestrator's
        #     own main thread) -- so it is evaluated, never silently
        #     skipped for looking blank.
        #
        # A bare `gaia contract view`, typed normally, resolves through
        # $PATH here (see _resolve_bare_gaia_token_for_guard) into an
        # absolute-path candidate BEFORE the guard's own exact realpath
        # comparison runs -- the guard's identity check itself is
        # unchanged and still denies a poisoned $PATH resolving elsewhere.
        # ================================================================
        if hook_payload is not None:
            from ..security.gaia_cli_only_guard import check as check_gaia_cli_only

            guard_command = self._resolve_bare_gaia_token_for_guard(command)
            guard_allowed, guard_reason = check_gaia_cli_only(guard_command, hook_payload)
            if not guard_allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=guard_reason,
                )

        if not command or not command.strip():
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason="Empty command not allowed",
            )

        command = command.strip()

        # ================================================================
        # EARLY NORMALIZATION: Strip AI attribution footers before any
        # other processing.  This ensures the same normalized command
        # string is used for blocked-command checks, compound parsing,
        # mutative verb detection, pending approval writes, AND pending
        # approval lookups.  Without this, write_pending_approval() and
        # find_pending_for_command() could see different strings on the
        # first attempt vs. retry, causing nonce mismatch loops.
        # ================================================================
        command_was_modified = False
        if self._detect_claude_footers(command):
            command = self._strip_claude_footers(command)
            command_was_modified = True
            logger.info("Auto-stripped Claude Code footer from commit command")

        # ================================================================
        # GAIA DB WRITE GUARD
        # Reject direct sqlite3 writes to ~/.gaia/gaia.db that bypass the
        # store API / agent_permissions enforcement.  Runs on the full
        # (footer-normalized) command BEFORE unwrap/decompose so heredocs
        # and bash -c wrappers are inspected intact -- decomposition would
        # otherwise split the sqlite3 invocation from its write verb.
        # ================================================================
        db_write_allowed, db_write_reason = check_gaia_db_write(command)
        if not db_write_allowed:
            logger.warning("BLOCKED gaia.db direct write: %s", command[:100])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=db_write_reason,
                suggestions=[
                    "Use the `gaia context` CLI or emit update_contracts.",
                ],
            )

        # ================================================================
        # SUBAGENT MEMORY-WRITE GUARD
        # Reject direct curated-memory mutations (`gaia memory add|edit|
        # append|reclassify|delete|link`) attempted from a subagent dispatch
        # context, EXCEPT for the sanctioned writers (gaia-operator). The
        # orchestrator (is_subagent False) is never blocked here. Categorical
        # deny, NOT approvable: the correct subagent path is to PROPOSE via a
        # `memorialize_suggestions` block, not to escalate. Runs on the full
        # (footer-normalized) command BEFORE unwrap/decompose so compound
        # chains and wrappers are inspected intact. See the memory skill's
        # "Who writes" section for the contract this enforces.
        # ================================================================
        mem_write_allowed, mem_write_reason = check_subagent_memory_write(
            command, is_subagent=is_subagent, agent_type=agent_type,
        )
        if not mem_write_allowed:
            logger.warning(
                "BLOCKED subagent memory write (agent=%s): %s",
                agent_type or "?", command[:100],
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=mem_write_reason,
                suggestions=[
                    "Write a `memorialize_suggestions` block into your "
                    "contract row with `gaia contract fill --json` instead.",
                ],
            )

        # ================================================================
        # PROTECTED-PATH WRITE GUARD
        # Reject any command that writes into the protected .claude/ tree
        # (the Gaia hooks directory or .claude settings files). The
        # Write/Edit sensitive-path backstop only inspects file_path params
        # and never sees a Bash command string, so a working-tree writer such
        # as `git mv payload.py .claude/hooks/pre_tool_use.py` (short-circuited
        # to non-mutative via GIT_LOCAL_SAFE_SUBCOMMANDS) would otherwise
        # overwrite hook code with no consent. Categorical deny, NOT approvable
        # -- the .claude/ tree is a hard security boundary. Runs on the full
        # (footer-normalized) command BEFORE unwrap/decompose so compound
        # chains and quoted forms are inspected intact.
        # ================================================================
        pp_write_allowed, pp_write_reason = check_protected_path_write(command)
        if not pp_write_allowed:
            logger.warning(
                "BLOCKED protected-path write via Bash: %s", command[:120]
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=pp_write_reason,
                suggestions=[
                    "Edit the SOURCE under gaia/ and run `gaia install` to "
                    "propagate; never modify .claude/ directly.",
                ],
            )

        # ================================================================
        # SHELL-AUTHORED WRITE GUARD
        # Reject a file write into a git working tree when the SHELL is the
        # author (redirect, `tee`, `sed -i`, `dd of=`). A grant is scoped to a
        # tool and a path, so routing an edit through a command string reaches
        # the bytes with the boundary evaluated against the wrong object and no
        # tool call naming the file. `tee` was the measured hole: it classified
        # T0 with the write executing. Categorical deny but NOT a withholding
        # -- the same edit via Write/Edit is permitted, so there is nothing to
        # approve; only the channel is refused. Runs BEFORE the smart sanitizer
        # below, which strips a trailing redirect and would otherwise delete
        # the destination before this guard could see it.
        # ================================================================
        shell_write_allowed, shell_write_reason = check_shell_write(
            command, cwd=(hook_payload or {}).get("cwd") or None,
        )
        if not shell_write_allowed:
            logger.warning(
                "BLOCKED shell-authored working-tree write: %s", command[:120]
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=shell_write_reason,
                suggestions=[
                    "Use the Write or Edit tool on the file instead; a "
                    "throwaway dump belongs under ~/.gaia/scratch.",
                ],
            )

        # ================================================================
        # EARLY NORMALIZATION (cont'd): SMART SANITIZATION
        # Strip a decorator that changes nothing about WHAT will run -- a
        # leading `nohup`, a trailing background `&`, or a trailing
        # `> file` / `>> file` redirect -- and RE-ENTER the full
        # classification pipeline on the cleaned command by simply letting
        # it flow into phase 1 below, rather than returning a verdict here.
        #
        # This runs AFTER the three write guards above (gaia.db, subagent
        # memory, protected-path) so a redirect INTO a guarded target
        # (e.g. ``echo x > .claude/hooks/y``) is still caught by those
        # guards on the undecorated command -- sanitizing first would let
        # the redirect vanish before a guard ever saw it.
        #
        # HISTORICAL NOTE: an earlier version of this step classified the
        # cleaned command itself, returning allowed=True / tier=T0
        # immediately upon stripping a decorator. That let a genuine T3
        # command evade its block by appending a trailing redirect --
        # ``terraform apply`` was blocked T3, but
        # ``terraform apply > /tmp/tf-out.txt`` was allowed T0, because the
        # stripped command never reached the mutative-verb detection in
        # phase 5. Measured and fixed; the vulnerability and the regression
        # test that now guards it are documented in
        # tests/hooks/test_sanitizer_redirect_t3_evasion.py.
        # ================================================================
        sanitize_result = self._try_sanitize_command(command)
        if sanitize_result is not None:
            cleaned_command, stripped_parts = sanitize_result
            logger.info(
                "Command sanitized: stripped [%s] from: %s -- re-entering "
                "classification on cleaned command: %s",
                ", ".join(stripped_parts), command[:80], cleaned_command[:80],
            )
            command = cleaned_command
            command_was_modified = True

        # ================================================================
        # PHASE 1: UNWRAP
        # Use ShellUnwrapper to detect and strip shell wrapper layers
        # (bash -c, sh -c, env bash -c, etc.).  If the wrapper nesting
        # depth exceeds _OBFUSCATION_DEPTH_LIMIT, permanently block
        # the command as obfuscated.
        #
        # After the unwrapper, _detect_indirect_execution() runs as a
        # fallback for patterns the unwrapper does not cover: eval,
        # python -c, node -e, perl -e, ruby -e, process substitution.
        # ================================================================
        unwrap_result = self._unwrapper.unwrap(command)
        if unwrap_result.depth >= _OBFUSCATION_DEPTH_LIMIT:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=(
                    f"Obfuscated shell nesting detected: {unwrap_result.depth} "
                    f"wrapper layers exceeds limit of {_OBFUSCATION_DEPTH_LIMIT}"
                ),
            )

        indirect_result = self._detect_indirect_execution(command)
        if indirect_result is not None:
            return indirect_result

        # ================================================================
        # PHASE 2: DECOMPOSE
        # Split the command into operator-linked stages using
        # StageDecomposer.  The decomposed result is available for
        # phase 4 (composition rules, T4) and provides operator context
        # that ShellCommandParser.parse() discards.
        # ================================================================
        decomposed = self._decomposer.decompose(command)

        # ================================================================
        # PHASE 3: CLASSIFY STAGES
        # Apply existing classification logic in priority order:
        #   3a. blocked_commands on full command (exit 2)
        #   3b. blocked_commands on each compound component (exit 2)
        #   3c. Git commit message validation
        #   3d. Cloud pipe/redirect/chain check (corrective deny)
        #   3e. Dispatch to single/compound classification
        #        (mutative_verbs, safe-by-elimination)
        #
        # Smart sanitization (strip nohup / trailing & / trailing redirect)
        # runs in EARLY NORMALIZATION, before phase 1 -- not here -- so the
        # cleaned command re-enters this whole pipeline instead of
        # short-circuiting past it.
        # ================================================================

        # 3a. Blocked commands check on FULL command (exit 2).
        # This MUST run before any other classifier to ensure permanently
        # blocked commands (kubectl delete namespace, etc.) are caught
        # with a reliable exit 2.
        blocked_result = is_blocked_command(command)
        if blocked_result.is_blocked:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Command blocked by security policy: {blocked_result.category}",
                suggestions=[blocked_result.suggestion] if blocked_result.suggestion else [],
            )

        # 3b. Parse compound commands and check each component against the
        # deny list.  Uses ShellCommandParser (not StageDecomposer) for
        # backward compat — the decomposed stages are used in phase 4.
        has_operators = self._has_operators(command)
        parsed_components = None
        if has_operators:
            parsed_components = self.shell_parser.parse(command)
            # Check each component against the deny list.
            # This catches "ls && kubectl delete namespace prod" early.
            for component in parsed_components:
                comp_blocked = is_blocked_command(component.strip())
                if comp_blocked.is_blocked:
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason=f"Command blocked by security policy: {comp_blocked.category}",
                        suggestions=[comp_blocked.suggestion] if comp_blocked.suggestion else [],
                    )

        # 3c. Validate git commit messages (on the potentially cleaned command).
        if "git commit" in command and "-m" in command:
            commit_validation = self._validate_commit_message(command)
            if not commit_validation.allowed:
                return commit_validation

        # 3d. Cloud pipe/redirect/chaining check.
        pipe_block = validate_cloud_pipe(command)
        if pipe_block is not None:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=read_permission_reason(pipe_block),
                suggestions=[],
                modified_input=None,
                block_response=pipe_block,
            )

        # ================================================================
        # PHASE 4: CHECK COMPOSITION
        # Cross-stage composition rules detect dangerous pipe patterns:
        #   - Exfiltration: sensitive_read | network_write
        #   - RCE:          network_read   | exec_sink
        #   - Obfuscated:   decode         | exec_sink
        #   - File-to-exec: file_read      | exec_sink  (escalate)
        # Only pipe-connected stages are checked; &&/; are independent.
        # ================================================================
        _composition_result = self._phase4_check_composition(
            decomposed,
            is_subagent=is_subagent,
            session_id=session_id,
            agent_type=agent_type,
        )
        if _composition_result is not None:
            return _composition_result

        # ================================================================
        # PHASE 5: AGGREGATE
        # 3e. Dispatch to per-stage classifiers (single or compound)
        # and combine into the final BashValidationResult.
        #
        # ``payload_cwd`` seeds the directory fold with the REAL invocation
        # directory the harness already sent in ``hook_payload["cwd"]``,
        # instead of leaving the fold to start at ``None`` -- which resolves,
        # deep inside ``detect_mutative_command`` / ``_resolve_dir_against_cwd``,
        # to the HOOK PROCESS's own cwd via ``os.getcwd()``, not the caller's.
        # A relative script path with no leading ``cd`` was resolving against
        # that wrong directory, missing, and falling to the conservative
        # ``script-file-unreadable`` T3 verdict -- 66 measured approvals for
        # scripts that were always readable, just looked up in the wrong place.
        # A later explicit ``cd`` in the chain still overrides this seed
        # exactly as before (``_peel_leading_cd`` / ``cwd_after_component``
        # replace an absolute target and join a relative one onto whatever
        # cwd is already in effect), so the already-covered "behind a cd"
        # case is unaffected.
        # ================================================================
        payload_cwd = (hook_payload or {}).get("cwd") or None
        consent_retry = (hook_payload or {}).get("consent_retry")
        if not isinstance(consent_retry, dict):
            consent_retry = None
        elif (
            consent_retry.get("session_id") != session_id
            or consent_retry.get("call_id") != (hook_payload or {}).get("tool_use_id")
            or consent_retry.get("agent_id") != agent_type
            or consent_retry.get("command") != command
        ):
            consent_retry = None
        requires_bound_retry = bool(
            (hook_payload or {}).get("requires_bound_consent_retry")
        )
        if not has_operators:
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type,
                tool_use_id=str((hook_payload or {}).get("tool_use_id", "")),
                consent_retry=consent_retry,
                requires_bound_retry=requires_bound_retry,
                cwd=payload_cwd,
            )
        elif parsed_components is not None and len(parsed_components) > 1:
            result = self._validate_compound_command(
                parsed_components, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, cwd=payload_cwd,
            )
        else:
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, cwd=payload_cwd,
            )

        # Attach cleaned command for hook to emit via updatedInput.
        # Set regardless of result.allowed so the ask path can include it too.
        if command_was_modified:
            result.modified_input = {"command": command}
            # If the result is an "ask" block_response, inject updatedInput
            # so the modification survives the native permission dialog. The
            # host shape is read/augmented via adapter accessors, never indexed
            # directly here.
            if (
                result.block_response is not None
                and read_permission_decision(result.block_response) == "ask"
            ):
                inject_updated_input(result.block_response, {"command": command})

        return result

    def _validate_single_command(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        cwd: Optional[str] = None,
        tool_use_id: str = "",
        consent_retry: Optional[Dict[str, Any]] = None,
        requires_bound_retry: bool = False,
    ) -> BashValidationResult:
        """Validate a single command (no operators).

        Simplified pipeline:
        0. Indirect execution detection (for compound command components)
        1. Mutative verb detection -> block with nonce or allow with grant
        2. GitOps policy validation (for kubectl/helm/flux)
        3. Everything else -> SAFE by elimination

        Args:
            command: The command to validate.
            is_subagent: True when running in subagent context (generates
                approval_id + deny). False for orchestrator (returns ask).
            session_id: Session ID for pending approval scoping.
            agent_type: Name of the originating agent (for pending approval context).

        Note: is_blocked_command() is NOT called here because validate()
        already checks the full command AND each compound component against
        the deny list before dispatching to this method.
        """

        # Indirect execution check for compound command components.
        # When validate() splits "cd /tmp && python3 -c '...'" into parts,
        # the python3 -c component needs the same indirect execution gate
        # that the full command gets in validate().
        indirect_result = self._detect_indirect_execution(command)
        if indirect_result is not None:
            return indirect_result

        # Mutative verb detection.  ``cwd`` carries the effective directory
        # folded from a preceding ``cd`` component of the chain (see
        # _validate_compound_command), so a relative script path resolves
        # against the workspace the chain navigated to, not the hook's own cwd.
        result = detect_mutative_command(command, cwd=cwd)
        if result.is_mutative:
            from gaia.store.writer import pending_plan_command_exists
            if not session_id or not tool_use_id:
                try:
                    pending_set = pending_plan_command_exists(command)
                except Exception as exc:
                    return BashValidationResult(
                        allowed=False, tier=SecurityTier.T3_BLOCKED,
                        reason=f"COMMAND_SET persistence failed closed: {exc}",
                    )
                if pending_set:
                    return BashValidationResult(
                        allowed=False, tier=SecurityTier.T3_BLOCKED,
                        reason="COMMAND_SET denied: adapter lacks stable tool-call correlation",
                    )
            try:
                from gaia.store.writer import reserve_plan_command
                retry = consent_retry or {}
                cs_match = None
                if not requires_bound_retry or retry:
                    cs_match = reserve_plan_command(
                        command,
                        session_id=session_id,
                        tool_use_id=tool_use_id,
                        approval_id=retry.get("approval_id"),
                        agent_id=retry.get("agent_id"),
                        command_fingerprint_value=retry.get("command_fingerprint"),
                        expected_index=retry.get("expected_index"),
                    )
            except Exception as exc:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"COMMAND_SET persistence failed closed: {exc}",
                )
            if cs_match is not None:
                return BashValidationResult(
                    allowed=True, tier=SecurityTier.T3_BLOCKED,
                    reason="Ordered COMMAND_SET command reserved",
                    consumed_approval_id=cs_match["approval_id"],
                    command_set_reservation=cs_match,
                )

            # DB-primary + filesystem-fallback grant check.
            # check_approval_grant() now returns a DB row first (Brief 71 CHECK-
            # side cutover), falling back to filesystem when no DB row exists.
            grant = check_approval_grant(command, session_id=session_id)
            if grant is not None:
                # Consume the DB semantic grant immediately (replay protection,
                # Gap B fix).  Single-use: a consumed grant will not match on a
                # second attempt within the TTL window.
                db_approval_id = getattr(grant, "_db_approval_id", None)
                if db_approval_id:
                    try:
                        from gaia.store.writer import consume_db_semantic_grant
                        consumed = consume_db_semantic_grant(db_approval_id)
                        if consumed:
                            logger.info(
                                "DB semantic grant consumed (replay protection): "
                                "approval_id=%s, command='%s'",
                                db_approval_id[:16], command[:80],
                            )
                        else:
                            logger.warning(
                                "DB semantic grant consume returned False "
                                "(may already be consumed): approval_id=%s",
                                db_approval_id[:16],
                            )
                    except Exception as _cg_err:
                        logger.warning(
                            "DB semantic grant consume failed (non-fatal): %s", _cg_err
                        )
                    # Also mark the companion filesystem grant as used so the
                    # filesystem fallback path cannot replay the same command.
                    try:
                        from ..security.approval_grants import consume_grant as _consume_fs_grant
                        _consume_fs_grant(command, session_id=session_id)
                    except Exception as _fs_cg_err:
                        logger.debug(
                            "Filesystem grant consume (companion cleanup) failed "
                            "(non-fatal): %s", _fs_cg_err
                        )

                if grant.confirmed:
                    # DB grants are always confirmed=True (user approved via AskUserQuestion).
                    # Filesystem grants may be confirmed or unconfirmed.
                    logger.info(
                        "T3 command allowed via confirmed grant: %s (scope='%s')",
                        command[:80], grant.approved_scope,
                    )
                    return BashValidationResult(
                        allowed=True,
                        tier=SecurityTier.T3_BLOCKED,
                        reason="Grant confirmed",
                        consumed_approval_id=db_approval_id,
                    )
                else:
                    # Filesystem grant exists, not yet confirmed -- GAIA approved,
                    # let it through. PostToolUse will confirm and consume
                    # the grant after successful execution.
                    logger.info(
                        "T3 command passthrough via active grant: %s (scope='%s')",
                        command[:80], grant.approved_scope,
                    )
                    return BashValidationResult(
                        allowed=True,
                        tier=SecurityTier.T3_BLOCKED,
                        reason="Grant active, pending confirmation",
                        consumed_approval_id=db_approval_id,
                    )
            else:
                # Converge on the single T3 decision point.  When there is an
                # orchestrator above (subagent context), it denies with a
                # persisted approval_id; otherwise (the main session) it falls
                # back to the native ask dialog.
                native_ask_reason = (
                    f"[T3_APPROVAL_REQUIRED] {result.category} operation detected.\n"
                    f"Command: {command}\n"
                    f"Verb: '{result.verb}' ({result.category})\n"
                    f"Reason: {result.reason}"
                )
                return decide_t3_outcome(
                    command,
                    verb=result.verb,
                    category=result.category,
                    has_orchestrator_above=is_subagent,
                    native_ask_reason=native_ask_reason,
                    session_id=session_id,
                    agent_type=agent_type,
                )

        # Flag-dependent classification (sed -i, find -exec, tar -x, etc.)
        # This supplements mutative_verbs -- it catches flag-dependent mutations
        # that verb-based detection misses (e.g. "sed" has no mutative verb, but
        # "sed -i" is mutative).  Runs after blocked_commands and mutative_verbs
        # to avoid double-classification.
        #
        # Git commands are EXCLUDED from the MUTATIVE path here because
        # detect_mutative_command() already has deliberate git handling.  If it
        # chose not to block a git command, that decision should be respected.
        # Git BLOCKED results still fire as a safety net (force push, etc.).
        flag_result = classify_by_flags(command)
        if flag_result is not None:
            if flag_result.outcome == FLAG_BLOCKED:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Command blocked by flag classifier: {flag_result.reason}",
                    suggestions=[],
                )
            if flag_result.outcome == FLAG_MUTATIVE:
                # Skip git commands -- mutative_verbs already handles them.
                if flag_result.command_family.startswith("git_"):
                    pass  # Fall through to safe-by-elimination
                else:
                    # Check for an approved grant BEFORE deciding T3 -- mirroring
                    # the mutative-verb branch above.  A flag-classified command
                    # (e.g. `curl -X POST`) is just as T3 as a mutative verb, so
                    # it must consult the same approval grant the block-approve-
                    # retry flow minted; otherwise an approved+activated grant is
                    # never honoured and the command re-blocks unconditionally on
                    # every retry (the flag path never reaches the matcher).  The
                    # consume + return semantics replicate the verb branch exactly.
                    try:
                        from gaia.store.writer import reserve_plan_command
                        retry = consent_retry or {}
                        cs_match = None
                        if not requires_bound_retry or retry:
                            cs_match = reserve_plan_command(
                                command,
                                session_id=session_id,
                                tool_use_id=tool_use_id,
                                approval_id=retry.get("approval_id"),
                                agent_id=retry.get("agent_id"),
                                command_fingerprint_value=retry.get("command_fingerprint"),
                                expected_index=retry.get("expected_index"),
                            )
                    except Exception as exc:
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason=f"COMMAND_SET persistence failed closed: {exc}",
                        )
                    if cs_match is not None:
                        return BashValidationResult(
                            allowed=True, tier=SecurityTier.T3_BLOCKED,
                            reason="Ordered COMMAND_SET command reserved",
                            consumed_approval_id=cs_match["approval_id"],
                            command_set_reservation=cs_match,
                        )

                    grant = check_approval_grant(command, session_id=session_id)
                    if grant is not None:
                        # Consume the DB semantic grant immediately (replay
                        # protection) -- identical to the verb branch.
                        db_approval_id = getattr(grant, "_db_approval_id", None)
                        if db_approval_id:
                            try:
                                from gaia.store.writer import consume_db_semantic_grant
                                consumed = consume_db_semantic_grant(db_approval_id)
                                if consumed:
                                    logger.info(
                                        "DB semantic grant consumed (replay "
                                        "protection): approval_id=%s, command='%s'",
                                        db_approval_id[:16], command[:80],
                                    )
                                else:
                                    logger.warning(
                                        "DB semantic grant consume returned False "
                                        "(may already be consumed): approval_id=%s",
                                        db_approval_id[:16],
                                    )
                            except Exception as _cg_err:
                                logger.warning(
                                    "DB semantic grant consume failed (non-fatal): %s",
                                    _cg_err,
                                )
                            # Also mark the companion filesystem grant as used so
                            # the filesystem fallback path cannot replay it.
                            try:
                                from ..security.approval_grants import (
                                    consume_grant as _consume_fs_grant,
                                )
                                _consume_fs_grant(command, session_id=session_id)
                            except Exception as _fs_cg_err:
                                logger.debug(
                                    "Filesystem grant consume (companion cleanup) "
                                    "failed (non-fatal): %s", _fs_cg_err
                                )

                        if grant.confirmed:
                            logger.info(
                                "T3 flag-path command allowed via confirmed grant: "
                                "%s (scope='%s')",
                                command[:80], grant.approved_scope,
                            )
                            return BashValidationResult(
                                allowed=True,
                                tier=SecurityTier.T3_BLOCKED,
                                reason="Grant confirmed",
                                consumed_approval_id=db_approval_id,
                            )
                        else:
                            logger.info(
                                "T3 flag-path command passthrough via active grant: "
                                "%s (scope='%s')",
                                command[:80], grant.approved_scope,
                            )
                            return BashValidationResult(
                                allowed=True,
                                tier=SecurityTier.T3_BLOCKED,
                                reason="Grant active, pending confirmation",
                                consumed_approval_id=db_approval_id,
                            )

                    # No grant matched -- converge on the single T3 decision
                    # point so a flag-dependent mutation in a subagent routes to
                    # deny+approval_id (Gaia approval flow) instead of escaping to
                    # the native ask dialog.  Orchestrator context still falls
                    # back to ask.
                    native_ask_reason = (
                        f"[T3_APPROVAL_REQUIRED] Flag-dependent mutation detected.\n"
                        f"Command: {command}\n"
                        f"Flag: {flag_result.matched_pattern} ({flag_result.command_family})\n"
                        f"Reason: {flag_result.reason}"
                    )
                    return decide_t3_outcome(
                        command,
                        verb=flag_result.matched_pattern or flag_result.command_family,
                        category="MUTATIVE",
                        has_orchestrator_above=is_subagent,
                        native_ask_reason=native_ask_reason,
                        session_id=session_id,
                        agent_type=agent_type,
                    )

        # Not blocked, not mutative -> SAFE by elimination
        return BashValidationResult(
            allowed=True,
            tier=SecurityTier.T0_READ_ONLY,
            reason="Safe by elimination (not blocked, not mutative)",
        )

    def _is_ungranted_t3_component(
        self, component: str, session_id: str, cwd: Optional[str] = None
    ) -> bool:
        """Classify a chain component as ungranted-T3 WITHOUT minting or consuming.

        Returns True when the component is a T3 (mutative-verb or
        flag-dependent) operation for which NO active grant exists -- i.e. the
        component would, on its own, be blocked pending approval. This is a
        read-only probe used by the chain COMMAND_SET intake (AC-8) to decide
        whether >= 2 sub-commands need grouping under ONE consent, BEFORE any
        per-component minting happens.

        It deliberately does NOT call decide_t3_outcome (no pending minted) and
        does NOT consume any grant (match_command_set_grant /
        check_approval_grant are pure lookups; consumption happens later in the
        real _validate_single_command pass at retry). A component that already
        matches a COMMAND_SET or semantic grant is treated as NOT ungranted, so
        it is excluded from a fresh batch.
        """
        component = component.strip()
        if not component:
            return False

        # Is this T3 (mutative verb or flag-dependent mutation)?  Honor the
        # folded cwd so a clean relative script behind a `cd` is not mis-counted
        # as ungranted-T3 (which would wrongly pull it into a COMMAND_SET batch).
        detect = detect_mutative_command(component, cwd=cwd)
        is_t3 = detect.is_mutative
        if not is_t3:
            flag_result = classify_by_flags(component)
            if (
                flag_result is not None
                and flag_result.outcome == FLAG_MUTATIVE
                and not flag_result.command_family.startswith("git_")
            ):
                is_t3 = True
        if not is_t3:
            return False

        # Already covered by an active grant? Then it is NOT ungranted -- exclude
        # it from a fresh batch (pure lookups, no consumption).
        try:
            if match_command_set_grant(component) is not None:
                return False
        except Exception:
            pass
        try:
            if check_approval_grant(component, session_id=session_id) is not None:
                return False
        except Exception:
            pass
        return True

    def _validate_compound_command(
        self,
        components: List[str],
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        cwd: Optional[str] = None,
    ) -> BashValidationResult:
        """Validate a compound command (multiple components).

        Compound T3 execution is REFUSED here, never grouped. If ANY component
        classifies T3 the whole chain is denied (subagent) or asked (primary):
        "Compound T3 execution is disabled. Create a plan-first request-set and
        issue each command as a separate Bash call." There is no COMMAND_SET
        intake on this path -- consent grouping is requested plan-first via
        ``gaia approvals request-set``, and execution stays one command per Bash
        call, so a chain is never the surface on which a set is discovered.

        A chain with NO T3 component runs the per-component pass: each component
        is validated by ``_validate_single_command``, a blocked component fails
        the chain fast, and the chain's tier is the highest component tier.

        ``cwd`` is the SEED for the directory fold below (the real invocation
        directory from the hook payload, or ``None`` when the caller has
        none) -- not the fold's final value. A leading ``cd`` component still
        overrides it exactly as before.
        """
        logger.info(f"Compound command detected with {len(components)} components")

        # Fold the effective cwd across components for THIS scan too, mirroring
        # the loop below: a leading `cd X` component must resolve a later
        # relative script (`node rel.mjs`) against X, not the hook's own cwd.
        # Without this fold a clean chain with zero real T3 operations reads
        # its script under the wrong cwd, finds it unreadable, and the
        # conservative `script-file-unreadable` default falsely trips has_t3
        # -- denying the whole chain for a mutation that was never there.
        # Seeded with the payload's real invocation directory (``cwd``, see
        # this method's docstring) rather than ``None``, so a chain with NO
        # leading `cd` at all still resolves its first relative script
        # against where the user actually ran it.
        has_t3 = False
        scan_cwd: Optional[str] = cwd
        for comp in components:
            detected = detect_mutative_command(comp, cwd=scan_cwd)
            flagged = classify_by_flags(comp)
            scan_cwd = cwd_after_component(comp, scan_cwd)
            if detected.is_mutative or (
                flagged is not None
                and flagged.outcome == FLAG_MUTATIVE
                and not flagged.command_family.startswith("git_")
            ):
                has_t3 = True
                break
        if has_t3:
            reason = (
                "Compound T3 execution is disabled. Create a plan-first "
                "request-set and issue each command as a separate Bash call."
            )
            decision = "deny" if is_subagent else "ask"
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=reason,
                block_response=build_hook_permission_response(decision, reason),
            )

        component_results: List[BashValidationResult] = []
        # Fold the effective cwd across components: a `cd X` component sets the
        # cwd for the components that FOLLOW it, so a relative script path in a
        # later `node rel.mjs` component resolves against X, not the hook's cwd.
        # `cd` and the script land in SEPARATE components (the chain is split on
        # `&&`), so the fold must happen here -- detect_mutative_command only
        # sees one component at a time. Seeded with the payload cwd (see
        # scan_cwd above) rather than ``None``, so a chain with no leading cd
        # still resolves its first component's relative script correctly; an
        # explicit `cd` still overrides it via cwd_after_component below.
        running_cwd: Optional[str] = cwd
        for i, component in enumerate(components, 1):
            result = self._validate_single_command(
                component, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, cwd=running_cwd,
            )
            # Advance the cwd AFTER classifying this component, so a `cd` only
            # affects subsequent components (POSIX chain semantics).
            running_cwd = cwd_after_component(component, running_cwd)

            if not result.allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=(
                        f"Compound command blocked: component {i}/{len(components)} "
                        f"'{component[:50]}' is not allowed\n"
                        f"Reason: {result.reason}"
                    ),
                    suggestions=result.suggestions,
                    block_response=result.block_response,
                )
            component_results.append(result)

        # All components validated -- derive highest tier from results already
        # computed by _validate_single_command (avoids redundant classification).
        tier_order = ["T0", "T1", "T2", "T3"]
        highest_tier = max(
            (r.tier for r in component_results),
            key=lambda t: tier_order.index(t.value),
        )

        # Propagate the consumed approval_id from whichever component matched a
        # grant, so the terminal event is recorded for that approval (EXECUTED
        # by PostToolUse on a clean exit, or FAILED by the Stop-hook
        # reconciliation on a non-zero exit).
        consumed_approval_id = next(
            (r.consumed_approval_id for r in component_results if r.consumed_approval_id),
            None,
        )

        return BashValidationResult(
            allowed=True,
            tier=highest_tier,
            reason=f"All {len(components)} components validated",
            consumed_approval_id=consumed_approval_id,
        )

    def _phase4_check_composition(
        self,
        decomposed: DecomposedCommand,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
    ) -> Optional[BashValidationResult]:
        """Check cross-stage composition patterns (Phase 4).

        Detects dangerous pipe compositions:
        - Exfiltration: sensitive_read | network_write  -> permanent block
        - RCE: network_read | exec_sink                 -> permanent block
        - Obfuscated exec: decode | exec_sink           -> permanent block
        - File-to-exec: file_read | exec_sink           -> escalate (ask)

        Args:
            decomposed: Output from StageDecomposer.decompose().

        Returns:
            BashValidationResult if a composition rule fires, else None.
        """
        if not decomposed.stages or len(decomposed.stages) < 2:
            return None

        # Check whether any stages are pipe-connected.
        has_pipe = any(s.operator == "|" for s in decomposed.stages)
        if not has_pipe:
            return None

        # Build classified composition stages and check rules.
        comp_stages = build_composition_stages(decomposed.stages)
        result = check_composition(comp_stages)

        if result.decision == CompositionDecision.BLOCK:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Dangerous pipe composition blocked: {result.reason}",
            )

        if result.decision == CompositionDecision.ESCALATE:
            # Converge on the single T3 decision point.  A file_to_exec
            # composition in a subagent must route to deny+approval_id (Gaia
            # approval flow) rather than escaping to the native ask dialog;
            # orchestrator context still falls back to ask.
            native_ask_reason = (
                f"[T3_APPROVAL_REQUIRED] Potentially dangerous pipe composition.\n"
                f"Pattern: {result.pattern}\n"
                f"Reason: {result.reason}"
            )
            return decide_t3_outcome(
                decomposed.raw,
                verb=result.pattern,
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                native_ask_reason=native_ask_reason,
                session_id=session_id,
                agent_type=agent_type,
            )

        # No composition rule fired — continue to Phase 5.
        return None

    def _detect_claude_footers(self, command: str) -> bool:
        """Detect Claude Code attribution footers in command."""
        for pattern in FORBIDDEN_FOOTER_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    def _strip_claude_footers(self, command: str) -> str:
        """
        Strip AI attribution footers from a commit command.

        Removes full lines matching forbidden footer patterns.
        Works on raw command string regardless of quoting/HEREDOC format.
        Preserves trailing quote/paren characters that close the commit
        message (e.g., the closing " in -m "...footer").

        Covers, kept ALIGNED with FORBIDDEN_FOOTER_PATTERNS (the detector):
          - Co-authored-by / Co-authored-with: Claude, Copilot, aider,
            Windsurf, Cursor, Codex, Gemini, and the Anthropic model family
            (Opus / Sonnet / Haiku)
          - "Generated with [Claude Code]" and the bare "🤖 Generated with ..."
          - a bare robot emoji 🤖 line
          - "Approved-by:" trailers
          - "Claude-Session:" trailers (the claude.ai/code session URL)
        Both newline-anchored footer LINES and footers carried in a SECOND
        ``-m "..."`` argument (no preceding newline) are handled.

        RESIDUAL GAP (accepted, no longer covered by a second layer) --
        this in-Bash command-string stripper is the ONLY remaining footer
        suppression path. It sees only footers present as literal text in the
        Bash ``command`` string it receives, so footers still leak on any path
        whose body is NOT in that string:
          - ``git commit -F <file>`` / ``--file=<file>``: the message body lives
            in a file; the footer is not in the command string. This stripper
            deliberately does NOT read the referenced file (reading arbitrary
            paths from a hook would be an unbounded side effect and a new attack
            surface).
          - a commit made outside Claude Code's Bash tool entirely -- a plain
            terminal ``git commit``, or an IDE / editor commit.
        A ``commit-msg`` git hook previously backstopped these paths at the git
        level, but it was removed (commit 4dd88f2, ``git-hooks/commit-msg``), so
        there is now no layer that catches them. Closing this gap would require
        reintroducing a git-level hook; it is out of scope for the command-string
        stripper here.

        Args:
            command: Raw command string

        Returns:
            Command with footer lines removed
        """
        # Author/model alternation reused across line- and -m-shaped patterns.
        _authors = (
            r"Claude|GitHub Copilot|aider|Windsurf|Cursor|Codex|Gemini"
            r"|Opus|Sonnet|Haiku"
        )

        # (1) Remove full lines that contain AI attribution patterns.
        # Each pattern matches the newline + footer content, then uses a
        # lookahead to stop before any trailing quote/paren/bracket
        # sequence that closes the command structure.  The captured group
        # is replaced with empty string, leaving the closing chars intact.
        footer_line_patterns = [
            r'\n\s*Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):\s+(?:' + _authors + r')[^\n]*?(?=["\')\]]*(?:\n|$))',
            # Co-authored-* lines naming an Anthropic model anywhere on the line.
            r'\n\s*Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*?\b(?:Opus|Sonnet|Haiku)\b[^\n]*?(?=["\')\]]*(?:\n|$))',
            r'\n\s*Approved-by:[^\n]*?(?=["\')\]]*(?:\n|$))',
            r'\n\s*Generated with\s+\[?Claude Code\]?[^\n]*?(?=["\')\]]*(?:\n|$))',
            r'\n\s*🤖\s*Generated with[^\n]*?(?=["\')\]]*(?:\n|$))',
            # Bare robot-emoji line (emoji not followed by "Generated with").
            r'\n\s*🤖[^\n]*?(?=["\')\]]*(?:\n|$))',
            # Claude Code session trailer: "Claude-Session: https://claude.ai/code/..."
            r'\n\s*Claude-Session:[^\n]*?(?=["\')\]]*(?:\n|$))',
        ]
        for pattern in footer_line_patterns:
            command = re.sub(pattern, '', command, flags=re.IGNORECASE)

        # (2) Remove footers carried in a SEPARATE ``-m "..."`` / ``-m '...'``
        # argument.  Repeated ``-m`` flags are concatenated by git as separate
        # paragraphs, so an attribution footer often arrives as
        #   git commit -m "real message" -m "Co-Authored-By: ... Opus"
        # with NO preceding newline -- the line patterns above cannot see it.
        # Drop the entire trailing ``-m "<footer>"`` flag+value when its value
        # is (essentially) just an attribution footer.
        m_footer_patterns = [
            r'''\s+-m\s+(["'])\s*Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):\s+(?:''' + _authors + r''')[^"']*\1''',
            r'''\s+-m\s+(["'])\s*Approved-by:[^"']*\1''',
            r'''\s+-m\s+(["'])\s*🤖[^"']*\1''',
            r'''\s+-m\s+(["'])\s*Generated with\s+\[?Claude Code\]?[^"']*\1''',
            r'''\s+-m\s+(["'])\s*Claude-Session:[^"']*\1''',
        ]
        for pattern in m_footer_patterns:
            command = re.sub(pattern, '', command, flags=re.IGNORECASE)

        # Clean up trailing whitespace inside quotes/heredoc
        # Collapse 3+ consecutive newlines to 2
        command = re.sub(r'\n{3,}', '\n\n', command)

        return command

    def _validate_commit_message(self, command: str) -> BashValidationResult:
        """
        Validate git commit message using commit_validator.

        Args:
            command: Git commit command to validate

        Returns:
            BashValidationResult with validation status
        """
        # Extract commit message from command
        # Handles both: git commit -m "message" and git commit -m "$(cat <<'EOF'...)"
        message = self._extract_commit_message(command)

        if not message:
            # Could not extract message - let it pass, git will handle it
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason="Could not extract commit message for validation"
            )

        # Import validator (lazy import to avoid startup cost)
        try:
            import sys
            from pathlib import Path

            # Import from sibling module (hooks/modules/validation)
            from ..validation.commit_validator import validate_commit_message

            # Validate message
            validation = validate_commit_message(message)

            if not validation.valid:
                # Build suggestions from errors
                suggestions = []
                for error in validation.errors:
                    suggestions.append(f"{error['type']}: {error['fix']}")

                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Commit message validation failed: {validation.errors[0]['message']}",
                    suggestions=suggestions[:3]  # Limit to 3 suggestions
                )

            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason="Commit message validated successfully"
            )

        except Exception as e:
            logger.warning(f"Failed to validate commit message: {e}")
            # If validation fails, allow the command (don't block on validator failure)
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason=f"Commit validation skipped (validator error: {e})"
            )

    def _extract_commit_message(self, command: str) -> Optional[str]:
        """
        Extract commit message from git commit command.

        Handles formats:
        - git commit -m "message"
        - git commit -m 'message'
        - git commit -m "$(cat <<'EOF'\nmessage\nEOF\n)"
        - git commit -m "$(cat <<EOF\nmessage\nEOF\n)"

        Returns:
            Extracted message or None if cannot extract
        """
        # Level 1: HEREDOC pattern (most common in Claude Code)
        # Handles: <<'EOF', <<EOF, <<"EOF" with flexible whitespace
        if "<<" in command:
            heredoc_match = re.search(
                r"<<['\"]?EOF['\"]?\s*\n(.*?)\n\s*EOF",
                command, re.DOTALL
            )
            if heredoc_match:
                return heredoc_match.group(1).strip()

        # Level 2: Simple -m "message" or -m 'message' (non-heredoc)
        match = re.search(r'-m\s+(["\'])(.*?)\1', command, re.DOTALL)
        if match:
            msg = match.group(2)
            # Skip if it's a $(cat... wrapper — heredoc parse failed above
            if msg.lstrip().startswith("$(cat"):
                return None
            return msg.strip()

        return None

# ---------------------------------------------------------------------------
# T2.1 DB-backed helpers (cutover from filesystem approval cache)
# ---------------------------------------------------------------------------

def _find_pending_in_db(session_id: str, command: str) -> Optional[str]:
    """Query the DB for an existing pending approval matching this command/session.

    Replaces find_pending_for_command() (filesystem) as part of the T2.1
    cutover. Looks up approvals with status='pending' in the DB and matches
    each pending's stored command against the incoming command using the SAME
    semantic matcher the consumption path uses (check_db_semantic_grant /
    matches_approval_signature), instead of a byte-exact comparison (Fix B).

    Why semantic, not byte-exact (double-approval fix, B):
        The block-approve-retry flow can present the same operation under
        cosmetically different strings -- most commonly a shell redirect that
        the agent appends on the retry (``git push`` blocked, retried as
        ``git push 2>&1``). A byte-exact dedup would MISS the existing pending
        on that retry and mint a fresh approval_id, so the user is asked to
        approve "the same command" twice. Matching on the semantic signature
        makes the retry reuse the existing pending: the signature already
        normalizes redirects out (Fix A) while still binding every
        identity-bearing token (incl. the ``-C <path>`` directory, per the
        keep-path policy), so distinct operations are NOT collapsed together.

    Args:
        session_id: Current session identifier (empty string if unknown).
            Retained for signature compatibility; NOT used to scope the query
            (see cross-session note below).
        command: The Bash command that was blocked.

    Returns:
        The approval_id (P-{hex}) if a matching pending exists, else None.
    """
    try:
        from gaia.approvals.store import get_pending
        from ..security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )
        import json as _json
        # Dedup MUST be cross-session (all_sessions=True). A T3 command can be
        # blocked under the subagent session and a pending row minted there;
        # if this lookup were scoped to the current session it would miss that
        # row on any cross-session retry, and insert_requested() would mint a
        # fresh P- on every miss -- a new approval conjured "from thin air" each
        # time. The semantic match below keeps the reuse pinned to THIS
        # command's operation, so widening the session scope does not collapse
        # distinct commands together.
        rows = get_pending(all_sessions=True)
        # Newest-first so a retry reuses the most recent matching pending,
        # mirroring check_db_semantic_grant()'s ORDER BY created_at DESC.
        for row in reversed(rows):
            payload_str = row.get("payload_json")
            if not payload_str:
                continue
            try:
                payload = _json.loads(payload_str)
            except Exception:
                continue
            pending_command = payload.get("exact_content")
            if not pending_command:
                continue
            # Fast path: byte-exact still reuses (covers the common no-decoration
            # retry without building a signature).
            if pending_command == command:
                return row.get("id")
            # Semantic path: rebuild the pending's signature and compare against
            # the incoming command with the same matcher the grant plane uses.
            try:
                pending_sig = build_approval_signature(
                    pending_command,
                    scope_type=SCOPE_SEMANTIC_SIGNATURE,
                )
                if pending_sig is not None and matches_approval_signature(
                    pending_sig, command
                ):
                    return row.get("id")
            except Exception:
                continue
    except Exception as _err:
        logger.debug("_find_pending_in_db query failed (non-fatal): %s", _err)
    return None


def _find_pending_plan_set_in_db(command: str) -> Optional[str]:
    """Return the id of a pending plan-first COMMAND_SET that carries ``command``.

    A blocked command that is an item of a set the user has not answered yet
    must be refused under THAT approval's id. Only a payload whose
    ``request_type`` is ``COMMAND_SET`` and which carries a
    ``request_fingerprint`` activates into the reservation lane
    (``insert_plan_command_set``, reached from ``activate_db_pending_by_id``
    and from ``cmd_opencode_decide``); a singular id approved in its place runs
    ``store.approve``, creates no grant, and the retry re-blocks.

    Membership is the exact command string -- the same identity the reservation
    lane recomputes at retry, which is why the per-item ``fingerprint`` sealed
    in the payload is derived from it rather than trusted in its place. Every
    item is matched, not only the next one: nothing is consumed while the
    approval is still pending, so the set as a whole is what consent is being
    sought for. Naming it grants no ordering freedom -- ``reserve_plan_command``
    still reserves only at ``next_index``.

    Args:
        command: The Bash command that was classified T3.

    Returns:
        The approval_id (P-{hex}) of the pending set, else None.
    """
    try:
        from gaia.approvals.store import get_pending
        import json as _json

        # Newest-first, mirroring _find_pending_in_db: get_pending returns
        # oldest-first, and the most recently requested set is the one the user
        # is being asked about.
        for row in reversed(get_pending(all_sessions=True)):
            payload_str = row.get("payload_json")
            if not payload_str:
                continue
            try:
                payload = _json.loads(payload_str)
            except Exception:
                continue
            if payload.get("request_type") != "COMMAND_SET":
                continue
            request_fingerprint = payload.get("request_fingerprint")
            if not isinstance(request_fingerprint, str) or not request_fingerprint:
                continue
            items = payload.get("command_set")
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("command") == command:
                    return row.get("id")
    except Exception as _err:
        logger.debug(
            "_find_pending_plan_set_in_db query failed (non-fatal): %s", _err
        )
    return None


#: What the sealed payload may state for impact, rollback and verification,
#: selected only by a named input of the classifier verdict: the mutative verb,
#: and failing that the verb category. Each statement is written out whole and
#: reviewed here rather than composed at build time, because the producer sees
#: intercepted command text and a verdict and never sees what the agent meant to
#: accomplish -- a sentence assembled from the command's arguments would assert a
#: consequence nothing determined. Neither table carries a catch-all: a verdict
#: absent from both contributes no statement and the presented surface states the
#: absence (``adapters.consent_presentation.VISIBLE_FIELDS``), which is the only
#: honest answer where reach or reversibility is not a property of the verdict.
#: rollback in particular never claims an effect can be undone when that depends
#: on state no interception can observe.
_STATEMENTS_BY_VERB: dict = {
    "push": {
        "impact": (
            "Writes to a remote repository: the pushed refs leave this machine "
            "and become visible to every other consumer of that remote."
        ),
        "rollback": (
            "A push is not undone by this session. Reversing it takes a further "
            "remote write -- a revert commit, or a force-update where the remote "
            "permits one -- and anything that already fetched keeps the pushed "
            "refs."
        ),
        "verification": (
            "Read the remote back with the same tool (git ls-remote, or git log "
            "on the pushed ref) and confirm the ref points where you intended."
        ),
    },
    "apply": {
        "impact": (
            "Converges a live target on the configuration the command names; the "
            "change lands on whatever target the tool's current context selects, "
            "not on this working tree."
        ),
        "rollback": (
            "Apply has no inverse of its own: returning to the previous state "
            "means re-applying the previous configuration or restoring a prior "
            "state snapshot, neither of which this interception captured."
        ),
        "verification": (
            "Re-read the target with the same tool's read verb (a get, show or "
            "state read) and compare the live values against what was applied."
        ),
    },
    "delete": {
        "impact": (
            "Removes the named object from the live target the tool's current "
            "context selects."
        ),
        "rollback": (
            "Deletion has no inverse in the command itself, and whether the "
            "object can be restored depends on a backup or a provider retention "
            "window this interception cannot observe -- do not assume it can be "
            "undone."
        ),
        "verification": (
            "Query the same object with the tool's read verb and confirm the "
            "target reports it absent."
        ),
    },
    "destroy": {
        "impact": (
            "Tears down the resources the selected state or stack tracks, on the "
            "live target the tool's current context selects."
        ),
        "rollback": (
            "Destroy has no inverse: re-creating the resources is a fresh create "
            "with new identities, and data those resources held is not recovered "
            "by it."
        ),
        "verification": (
            "Read the tool's state back (a state list or show) and confirm it "
            "reports no remaining tracked resources for the selection destroyed."
        ),
    },
    "create": {
        "impact": (
            "Adds a new object to the live target the tool's current context "
            "selects; it exists from that moment, independently of this session."
        ),
        "rollback": (
            "The inverse of a create is a delete of the object it created, which "
            "is itself a T3 operation needing its own consent; the create is not "
            "undone by this session."
        ),
        "verification": (
            "Read the new object back with the tool's read verb and confirm it "
            "exists with the properties intended."
        ),
    },
}

#: Fallback selection when the verb is none of the verbs above, keyed on the
#: category the classifier assigned. Only the destructive category is stated:
#: it is the one category whose direction -- removing or overwriting rather than
#: adding -- the verdict determines on its own. No classifier reaching this
#: producer emits it today, the same pre-existing condition as the risk_level
#: ternary below, so production coverage rests on the verb table.
#:
#: Because it never fires, this table is not evidence: a statement read from
#: here proves nothing about what a user was shown, and it must not be cited
#: to satisfy a gate or a test that asks what production sealed. Verify that
#: against the verb table, which is the only producer with reachable output.
_STATEMENTS_BY_CATEGORY: dict = {
    "DESTRUCTIVE": {
        "impact": (
            "The classifier scored this command destructive: it removes or "
            "overwrites state on the live target rather than adding to it."
        ),
        "rollback": (
            "A destructive verdict carries no inverse command. Recovery depends "
            "on a backup or retention window this interception cannot observe -- "
            "do not assume the effect can be undone."
        ),
        "verification": (
            "Read the affected target back with a read-only command of the same "
            "tool and confirm what remains matches what was meant to be kept."
        ),
    },
}


def _authored_statements(verb: str, category: str) -> dict:
    """Return the impact/rollback/verification statements a verdict selects.

    Empty when neither table covers the verdict, so the caller seals no claim
    and the presented surface keeps its declared-absence text.
    """
    by_verb = _STATEMENTS_BY_VERB.get(str(verb or "").strip().lower())
    if by_verb is not None:
        return by_verb
    return _STATEMENTS_BY_CATEGORY.get(str(category or "").strip().upper(), {})


def _build_sealed_payload(
    command: str,
    verb: str,
    category: str,
    agent_type: str = "",
    command_set: list | None = None,
) -> dict:
    """Build a sealed_payload dict from hook-intercepted command context.

    Used by the T2.1 cutover path when bash_validator detects a T3 command
    and calls store.insert_requested(). The 7 D13 fields are populated from
    what is available at intercept time.

    Single vs. multi-command (COMMAND_SET):
        By default this builds a SINGLE-command payload -- ``commands`` is
        ``[command]`` and no ``command_set`` key is present, so activation
        mints a single-use SCOPE_SEMANTIC_SIGNATURE grant.

        When ``command_set`` is supplied (a list of ``{command, rationale}``
        dicts representing more than one command the agent wants under ONE
        consent), the payload additionally carries a ``command_set`` key
        verbatim and ``commands`` lists every command string in the set. This
        is the signal ``activate_db_pending_by_id`` reads to branch into
        ``create_command_set_grant`` instead of degrading to a single command.
        The set is NOT collapsed -- every item survives into the grant.

    Impact, rollback and verification:
        These three come from ``_authored_statements``, keyed on the verdict's
        verb and category. A verdict neither table covers seals ``None`` for all
        three and the presented surface says so, rather than a claim about a
        reach or a reversibility the verdict did not determine.

    Args:
        command: The full Bash command string that was blocked (the primary /
            first command; used for ``exact_content`` and the singular display).
        verb: The detected mutative verb (e.g. 'push', 'delete').
        category: The verb category string (e.g. 'MUTATIVE').
        agent_type: Name of the originating agent (may be empty).
        command_set: Optional list of ``{command, rationale}`` dicts. When it
            contains more than one item, the payload becomes a COMMAND_SET
            envelope. A list with a single item (or None) keeps the singular
            semantic-signature behaviour.

    Returns:
        Dict with the 7 sealed_payload fields from D13, plus an optional
        ``command_set`` key when a multi-command set was supplied.
    """
    # Normalize the command_set into the canonical [{command, rationale}, ...]
    # shape and decide whether this is a genuine multi-command envelope. A set
    # of length <= 1 is NOT multi-command -- it stays the singular path so we
    # never mint a COMMAND_SET grant for one command.
    normalized_set: list = []
    if command_set:
        for item in command_set:
            if isinstance(item, dict) and item.get("command"):
                normalized_set.append(
                    {
                        "command": item["command"],
                        "rationale": item.get("rationale", ""),
                    }
                )
    is_command_set = len(normalized_set) > 1

    # Authored at mint, never merged in afterwards: the approvals row is
    # write-once and its dedup fingerprint is derived from this payload at
    # insert, so a statement added later would either be dropped or require
    # mutating a sealed row. Neither fingerprint that binds a decision to an
    # execution reads a descriptive field -- command_fingerprint hashes the
    # command bytes, request_fingerprint the ordered command list -- so these
    # statements cannot move what a post-grant retry must keep byte-identical.
    authored = _authored_statements(verb, category)

    payload = {
        "operation": f"{category} command intercepted: {verb}",
        "exact_content": command,
        "scope": command.split()[0] if command.strip() else "unknown",
        "risk_level": "high" if category.upper() == "DESTRUCTIVE" else "medium",
        "impact": authored.get("impact"),
        "rollback_hint": authored.get("rollback"),
        "verification": authored.get("verification"),
        "rationale": (
            f"Agent '{agent_type}' attempted a {category.lower()} ({verb}) command "
            "that requires user approval per the T3 security policy."
            if agent_type
            else f"A {category.lower()} ({verb}) command requires user approval per T3 policy."
        ),
        "commands": (
            [it["command"] for it in normalized_set] if is_command_set else [command]
        ),
    }

    if is_command_set:
        # Carry the full {command, rationale} set verbatim. This is the
        # multi-command signal the activation path branches on.
        payload["command_set"] = normalized_set

    return payload


def decide_t3_outcome(
    command: str,
    verb: str,
    category: str,
    *,
    has_orchestrator_above: bool,
    native_ask_reason: str,
    session_id: str = "",
    agent_type: str = "",
    command_set: list | None = None,
) -> BashValidationResult:
    """Single decision point for the outcome of a T3 (state-mutating) command.

    Every T3 classifier -- mutative verbs, pipe composition (file_to_exec), and
    flag-dependent mutations -- converges here so the deny-vs-ask policy is
    expressed in ONE place instead of being re-decided (and diverging) per
    classifier.

    Routing dimension: ``has_orchestrator_above`` -- True when there is an
    orchestrator above this turn that owns the Gaia approval flow (a subagent
    running under the orchestrator).  In that case the command is DENIED with a
    persisted ``approval_id`` so the orchestrator can drive the approval cycle.

    When there is no orchestrator above (the main session / orchestrator
    itself, which cannot hand off a T3 approval to itself), the command falls
    back to the native Claude Code ``ask`` dialog -- a deliberate, correct
    defensive fallback. This is the main-session T3 mutation-safety floor: it
    is driven solely by ``has_orchestrator_above`` (False for the main
    session) and is independent of any plugin mode.

    The ``block_response`` on the returned result already encodes the outcome
    (deny+approval_id, or native ask); the Claude Code adapter delivers it
    verbatim.

    Args:
        command: Full Bash command being classified.
        verb: Detected mutative verb (or a classifier-specific label).
        category: Verb category ("MUTATIVE", "DESTRUCTIVE", ...).
        has_orchestrator_above: True when a subagent runs under the orchestrator.
        native_ask_reason: Reason text for the native-ask fallback branch.
        session_id: Session ID for pending-approval scoping.
        agent_type: Originating agent name (for the sealed payload).
        command_set: Optional list of ``{command, rationale}`` dicts. When it
            carries MORE THAN ONE item, this T3 decision covers a chain
            (``a && b && c``) whose sub-commands are all T3, and the pending is
            minted as ONE COMMAND_SET envelope (the chain-intake path, AC-8)
            instead of a single semantic-signature pending. ONE user approval
            then covers the whole chain; each sub-command is still consumed
            byte-for-byte by its own signature at retry. A None / single-item
            set keeps the singular behaviour. Only honoured in the
            subagent-under-orchestrator branch (the native-ask branch has no
            COMMAND_SET concept).

    Returns:
        A blocked BashValidationResult (allowed=False, tier T3) whose
        block_response is either a "deny" (with approval_id) or an "ask".
    """
    # Breadcrumb for the fail-open path. Reaching this function IS the moment
    # the command becomes known to require consent, so from here on a failure
    # anywhere in the gate must not degrade it into a permission -- see
    # modules.security.fail_open. Recorded before any branch so it holds no
    # matter which outcome this call produces.
    note_mutative_classification(command, verb, category)

    # A genuine multi-command chain is a set of >= 2 items. Anything else
    # collapses to the singular path so we never mint a COMMAND_SET for one
    # command (mirrors _build_sealed_payload's is_command_set guard).
    _normalized_set: list = []
    if command_set:
        for _item in command_set:
            if isinstance(_item, dict) and _item.get("command"):
                _normalized_set.append(
                    {
                        "command": _item["command"],
                        "rationale": _item.get("rationale", ""),
                    }
                )
    is_chain_command_set = len(_normalized_set) > 1

    if has_orchestrator_above:
        # Subagent-under-orchestrator: deny + persisted approval_id so the
        # orchestrator can run the approval cycle.  Reuse an existing pending
        # approval on retry to avoid generating duplicates while the user reviews.
        #
        # For a COMMAND_SET chain the pending id is CONTENT-derived (matching the
        # plan-first intake), so a retry of the same chain produces the same id
        # and the fingerprint-dedup in insert_requested reuses the pending. The
        # singular reuse probe (_find_pending_in_db) matches a SINGLE command's
        # signature and must NOT be consulted for the chain -- it would match one
        # leftover single pending of a sub-command and degrade the chain back to
        # a single grant. So the chain path skips it entirely.
        if not is_chain_command_set:
            # A pending plan-first COMMAND_SET carrying this exact command is
            # named ahead of the singular probe. The consent being sought is
            # the set's, and only the set's id routes the user's reply into the
            # reservation lane; a singular id named here -- freshly minted or
            # reused -- strands the set, because approving it never creates a
            # grant and the retry blocks again.
            approval_id = _find_pending_plan_set_in_db(command)
            if approval_id:
                logger.info(
                    "Naming pending plan-first COMMAND_SET approval_id=%s for: %s",
                    approval_id, command[:80],
                )
            else:
                approval_id = _find_pending_in_db(session_id or "", command)
                if approval_id:
                    logger.info(
                        "Reusing pending approval_id=%s for retry: %s",
                        approval_id, command[:80],
                    )
            if approval_id:
                reason = build_t3_blocked_denial_message(
                    approval_id=approval_id,
                    command=command,
                    verb=verb,
                    category=category,
                )
                hook_deny = build_hook_permission_response("deny", reason)
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"T3 {category.lower()} command: {command[:60]}",
                    block_response=hook_deny,
                )

        # No existing pending -- insert via DB (D16: exclusive path).
        sealed_payload = _build_sealed_payload(
            command=command,
            verb=verb,
            category=category,
            agent_type=agent_type,
            command_set=_normalized_set if is_chain_command_set else None,
        )
        try:
            from gaia.approvals.store import insert_requested
            # COMMAND_SET chains use a CONTENT-derived id (deterministic over the
            # sub-command list) so a retry of the same chain reproduces the same
            # id and reuses the pending via fingerprint dedup -- identical to the
            # plan-first intake in handoff_persister. Singular T3 keeps uuid4.
            supplied_id = None
            if is_chain_command_set:
                from gaia.approvals.store import derive_command_set_id
                supplied_id = derive_command_set_id(
                    [it["command"] for it in _normalized_set]
                )
            approval_id = insert_requested(
                sealed_payload,
                agent_id=agent_type or None,
                session_id=session_id or None,
                approval_id=supplied_id,
            )
        except Exception as _store_err:
            logger.warning(
                "DB insert_requested failed after retries for subagent; "
                "denying the already-classified T3 command: %s -- %s",
                command[:80], _store_err,
            )
            # ---- Q1 sensor: persist-failure (always-on) ----------------------
            # Diagnosability: the logger above routes through a NullHandler when
            # GAIA_DEBUG is unset (the default), so its warning is a no-op and the
            # exact exception would be unrecoverable. Persist the underlying error
            # to the always-on audit sink (audit-*.jsonl, not gated by GAIA_DEBUG)
            # so the NEXT occurrence of a persistence failure is diagnosable after
            # the fact. Best-effort: never let the diagnostic sink mask the
            # outcome of this branch. Tagged "approval_persist_failed" -- the
            # canonical vocabulary `gaia metrics` groups on (persist-failure
            # sensor, complementary to the t3_degraded_block sensor below).
            try:
                from ..audit.logger import log_error
                log_error(
                    component="gaia.approvals",
                    error_type="approval_persist_failed",
                    detail=f"{type(_store_err).__name__}: {_store_err}",
                    context={
                        "command": command[:200],
                        "agent_id": agent_type or "",
                        "session_id": session_id or "",
                    },
                )
            except Exception:
                pass

            # ---- Deny-list re-assertion (defense-in-depth) -------------------
            # The Q3 degraded-allow must NEVER cover a deny-listed destructive
            # command. Two prior barriers already guarantee this: the harness
            # native deny-list (settings _DENY_RULES) blocks such commands
            # BEFORE the hook runs, and validate() Phase 3a/3b (is_blocked_command)
            # blocks them BEFORE reaching decide_t3_outcome. We re-assert it here
            # so THIS branch is structurally incapable of allowing a blocked
            # command even if the upstream flow ever changed -- a blocked command
            # falls to a permanent hard deny (exit 2), never a degraded allow.
            _blocked = is_blocked_command(command)
            if _blocked.is_blocked:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Command blocked by security policy: {_blocked.category}",
                    suggestions=[_blocked.suggestion] if _blocked.suggestion else [],
                )

            # ---- Degradation sensor (always-on) ------------------------------
            # Reuse the approval store's SAME fingerprint (SHA-256 of the
            # canonical sealed_payload) so the audit event redacts the command to
            # a hash -- no secret is logged. Best-effort: a fingerprint failure
            # must not change the outcome.
            try:
                from gaia.approvals.chain import fingerprint_payload
                _fp = fingerprint_payload(sealed_payload)
            except Exception:
                _fp = None
            try:
                from ..audit.logger import log_event
                log_event(
                    event="t3_degraded_block",
                    component="gaia.bash_validator",
                    tier="T3",
                    reason="approval_persist_failed",
                    fingerprint=_fp,
                    # This branch only runs inside `has_orchestrator_above`, i.e.
                    # a subagent running under the orchestrator.
                    origin="subagent",
                    context={"verb": verb, "category": category},
                )
            except Exception:
                pass

            # ---- Policy: deny, never a permission ----------------------------
            # Reaching this point means the command was ALREADY classified T3 and
            # only the approval record failed to persist. This branch used to
            # ALLOW it through, so that an unattended run would not hang on a
            # human prompt -- but the allow granted precisely what the
            # classification had just decided to withhold, and a persistence
            # outage silently became blanket consent for every mutation behind
            # it. Denying instead does not reintroduce the hang it was avoiding:
            # a deny returns immediately and the session continues, and it
            # reaches only commands that were already going to stop and ask.
            reason = build_t3_degraded_block_message()
            hook_deny = build_hook_permission_response("deny", reason)
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason="T3 degraded-block: approval persistence failed",
                block_response=hook_deny,
            )
        reason = build_t3_blocked_denial_message(
            approval_id=approval_id,
            command=command,
            verb=verb,
            category=category,
        )
        hook_deny = build_hook_permission_response("deny", reason)
        return BashValidationResult(
            allowed=False,
            tier=SecurityTier.T3_BLOCKED,
            reason=f"T3 {category.lower()} command: {command[:60]}",
            block_response=hook_deny,
        )

    # No orchestrator above (orchestrator itself, or security context): the
    # native Claude Code 'ask' dialog handles approval.  No approval_id.
    hook_ask = build_hook_permission_response("ask", native_ask_reason)
    return BashValidationResult(
        allowed=False,
        tier=SecurityTier.T3_BLOCKED,
        reason=f"Dangerous {category.lower()} command requires approval: {command[:60]}",
        block_response=hook_ask,
    )


def validate_bash_command(
    command: str,
    is_subagent: bool = False,
    session_id: str = "",
    agent_type: str = "",
) -> BashValidationResult:
    """
    Validate a Bash command (convenience function).

    Args:
        command: Command to validate
        is_subagent: True when running in subagent context
        session_id: Session ID for approval scoping
        agent_type: Name of the originating agent (for pending approval context)

    Returns:
        BashValidationResult
    """
    validator = BashValidator()
    return validator.validate(
        command, is_subagent=is_subagent, session_id=session_id, agent_type=agent_type,
    )
