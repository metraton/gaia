"""
Bash command validator.

Primary security gate for all Bash tool invocations. With Bash(*) in the
settings.json allow list, ALL commands reach this hook -- it is the sole
enforcement layer for dangerous command detection.

5-Phase Pipeline:
  1. UNWRAP      -- ShellUnwrapper measures wrapper-shell nesting (bash -c,
                    sh -c, ...); depth >= _OBFUSCATION_DEPTH_LIMIT = permanent
                    block. _detect_indirect_execution() then judges shell
                    wrappers, eval, python -c, node -e and the rest of
                    INDIRECT_EXEC_PATTERNS.
  2. DECOMPOSE   -- StageDecomposer splits into operator-linked stages.
  3. CLASSIFY    -- blocked_commands + cloud_pipe_validator + mutative_verbs.
  4. COMPOSITION -- cross-stage composition rules (exfiltration, RCE,
                    obfuscated exec via pipe analysis).
  5. AGGREGATE   -- combine stage results into final BashValidationResult.

Order within and before the phases:
  - Footer stripping runs before phase 1 (EARLY NORMALIZATION)
  - Smart sanitization (strip nohup / trailing & / trailing redirect) also
    runs in EARLY NORMALIZATION, after footer stripping and after the write
    guards, but before phase 1. It strips the decorator and lets the CLEANED
    command flow into phase 1 like any other command -- it never classifies
    the command itself, so a decorator can never move a command's tier by
    hiding it from phases 1-5 (see _try_sanitize_command's docstring).
  - Indirect execution detection follows the unwrap check in phase 1
  - Blocked commands run before cloud_pipe and mutative_verbs in phase 3

Phase 0 -- GAIA CLI ONLY GUARD (gaia_cli_only_guard.check): runs before
everything above, including footer stripping and smart sanitization. It is a
closed-by-default restriction for the ORCHESTRATOR role only (see that
module's docstring), which delegate_mode grants a Bash lane that reaches it. It
evaluates the RAW string the harness sent, never a version something upstream
already normalized. Only invoked when a caller supplies `hook_payload` -- see
validate()'s own docstring for why "not supplied" and "supplied with an
empty role" are kept distinct rather than collapsed by a default.
"""

from __future__ import annotations

import functools
import os
import re
import json
import shlex
import shutil
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..security.tiers import SecurityTier
from ..security.blocked_commands import is_blocked_command
from ..security.kubectl_secret_output import check_kubectl_secret_output
from ..security.mutative_verbs import (
    _peel_leading_command_wrappers,
    account_path_redirect_target,
    detect_mutative_command,
    build_t3_block_response,
    LocalWorkCaller,
    local_work_callers,
    cwd_after_component,
    git_under_injected_config,
    injects_git_config,
    node_test_under_node_options,
    code_injecting_assignments,
    runs_under_injected_environment,
    rewrites_package_inputs,
    runs_package_script,
    sets_node_options,
    target_under_line,
)
from ..security.nested_executors import targets_set_on_line
from ..security.targets import naming_variables_on_line, production_target_verdict
from ..security.flag_classifiers import (
    classify_by_flags,
    OUTCOME_BLOCKED as FLAG_BLOCKED,
    OUTCOME_MUTATIVE as FLAG_MUTATIVE,
)
from ..security.approval_grants import (
    check_approval_grant,
    confirm_grant,
    last_check_found_expired,
)
from ..security.approval_messages import (
    build_t3_approval_instructions,
    build_t3_blocked_denial_message,
    build_t3_degraded_block_message,
)
from ..security.fail_open import clear_classification, note_mutative_classification
from ..security.verdict import COMPOSITION_RULES, UNDETERMINED, UNKNOWN_SPAN, blocked_rule, render, signing_rule
from ..security.shell_unwrapper import ShellUnwrapper, shell_command_string
from ..security.data_heredoc import data_heredoc_header
from ..security.program_heredoc import heredoc_program
from ..security.shell_substitution import (
    _read_heredoc_opener, extract_substitutions, extract_substitutions_truncated,
)
from ..security.gaia_db_write_guard import check as check_gaia_db_write, sqlite_unproven_effect
from ..security.host_consent_verb_guard import check as check_host_consent_verb
from ..security.subagent_resume_point_guard import check as check_subagent_resume_point
from ..security.memory_skill_guard import check as check_memory_skill_loaded
from ..security.subagent_memory_write_guard import (
    COMPUTED_WORDS_REJECTION,
    IDENTITY_REJECTION,
    check as check_subagent_memory_write,
)

_MEMORY_GUARD_RULES = {
    IDENTITY_REJECTION: "floor.dispatch-identity",
    COMPUTED_WORDS_REJECTION: "floor.computed-gaia-words",
}
from ..security.contract_ownership_guard import check as check_contract_ownership
from ..security.protected_path_guard import (
    check as check_protected_path_write,
    unprovable_write_target,
)
from ..security.shell_write_guard import check as check_shell_write
from ..security.sensitive_read_guard import check as check_sensitive_read, remote_copy_destination
from ..security.publish_attribution_guard import check as check_publish_attribution
from ..security.comment_push_gate import check as check_comment_push
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
    redirected_into_sender,
    CompositionDecision,
)
from .shell_parser import get_shell_parser, strip_comment
from .cloud_pipe_validator import validate_cloud_pipe, violation_kind
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
    # The approval_id of the grant a T3 command matched and consumed; the
    # adapter keeps it in HookState so the call's own terminal event records
    # EXECUTED or FAILED against it. None for non-T3 / no-grant paths.
    consumed_approval_id: Optional[str] = None
    command_set_reservation: Optional[Dict[str, Any]] = None
    # The pending approval a T3 denial asks consent for, named structurally so
    # no adapter reads it back out of the denial text.
    approval_id: Optional[str] = None
    rule: str = ""
    undetermined: str = ""
    # The (rule, undetermined) a T3 would sign under, kept when no requester
    # could be named, so a nested probe still reads it as a signature.
    withheld: Optional[Tuple[str, str]] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []


def _carries_ruling(validate):
    """Make every refusal and signature request a validator returns name its rule and remediation."""
    @functools.wraps(validate)
    def wrapper(self, command: str, *args, **kwargs) -> BashValidationResult:
        result = validate(self, command, *args, **kwargs)
        if result.allowed:
            return result
        line = render(result.rule or "floor.unclassified")
        if result.undetermined:
            line = f"{result.undetermined[:1].upper()}{result.undetermined[1:]} {UNDETERMINED}. {line}"
        result.reason = f"{result.reason}\n{line}"
        if result.block_response is not None:
            decision = read_permission_decision(result.block_response) or "deny"
            result.block_response = build_hook_permission_response(
                decision, f"{read_permission_reason(result.block_response)}\n{line}",
                updated_input=result.modified_input if decision == "ask" else None,
            )
        return result
    return wrapper


_COMPOUND_T3_DENIAL = (
    "Compound T3 execution is disabled. Create a plan-first "
    "request-set and run each command directly as a separate Bash "
    "call, with no wrapper (bash -c, sh -c, eval)."
)
_HEREDOC_DATA_READERS = frozenset({
    "cat", "head", "tail", "wc", "sort", "uniq", "tr", "cut", "nl", "rev", "fold", "tee", "jq",
})
_BASE64_DECODE_ARGS = frozenset({"-d", "--decode", "-i", "--ignore-garbage"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_GREP_PATTERN_FILE = frozenset({"-f", "--file"})
_XARGS_VALUE_FLAGS = frozenset({"-a", "--arg-file", "-E", "-I", "-L", "-n", "-P", "-s", "-d"})
_PATH_VALUE_OPTIONS = frozenset({"-C", "-c", "-R", "--repo"})
_HEREDOC_MESSAGE_FLAGS = {
    "git": (
        frozenset({("commit",), ("tag",), ("notes",)}),
        frozenset({"-F", "--file"}),
    ),
    "gh": (
        frozenset({(noun, verb) for noun in ("pr", "issue") for verb in ("create", "edit", "comment")})
        | {("release", "create"), ("release", "edit")},
        frozenset({"--body-file", "--notes-file", "-F"}),
    ),
    "glab": (
        frozenset({(noun, verb) for noun in ("mr", "issue") for verb in ("create", "update", "note")}),
        frozenset({"--description"}),
    ),
}


def _flag_values(args: List[str], flags: frozenset) -> List[str]:
    values = []
    for arg, following in zip(args, args[1:] + [""]):
        if arg in flags:
            values.append(following)
        elif arg.startswith("--") and arg.split("=", 1)[0] in flags:
            values.append(arg.split("=", 1)[1] if "=" in arg else "")
        elif arg[:1] == "-" and arg[:2] in flags:
            values.append(arg[2:])
        elif arg[:1] == "-" and arg[1:2] != "-" and f"-{arg[-1:]}" in flags:
            values.append(following)
    return values


def _command_path(args: List[str], length: int) -> Tuple[str, ...]:
    path, skip_value = [], False
    for arg in args:
        if skip_value:
            skip_value = False
        elif arg in _PATH_VALUE_OPTIONS:
            skip_value = True
        elif not arg.startswith("-"):
            path.append(arg)
            if len(path) == length:
                break
    return tuple(path)


def _reads_message_from_stdin(program: str, args: List[str]) -> bool:
    if program not in _HEREDOC_MESSAGE_FLAGS:
        return False
    paths, flags = _HEREDOC_MESSAGE_FLAGS[program]
    return _command_path(args, len(next(iter(paths)))) in paths and "-" in _flag_values(args, flags)


def _xargs_payload(reader: str) -> Optional[str]:
    words = _peel_leading_command_wrappers(reader)[0].split()
    if not words or os.path.basename(words[0]) != "xargs":
        return None
    return " ".join(_xargs_target(words[1:])) or "echo"


def _xargs_target(args: List[str]) -> List[str]:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "--":
            return args[index + 1:]
        index += 2 if args[index] in _XARGS_VALUE_FLAGS else 1
    return args[index:]


def _heredoc_reader(head: str) -> bool:
    words = _peel_leading_command_wrappers(head)[0].split()
    if not words:
        return False
    program, args = os.path.basename(words[0]), words[1:]
    if program in _GREPS:
        return not _flag_values(args, _GREP_PATTERN_FILE)
    if program == "base64":
        return set(args) <= _BASE64_DECODE_ARGS
    return _reads_message_from_stdin(program, args) or program in _HEREDOC_DATA_READERS


@dataclass
class _Heredoc:
    reader: str
    delimiter: str
    strip_tabs: bool
    expands: bool
    segment: int
    piped: bool = False
    body: List[str] = field(default_factory=list)

    @property
    def is_data(self) -> bool:
        return _heredoc_reader(self.reader)


def _starts_command(line: str, index: int) -> bool:
    if line[index] != "&":
        return line[index] in "|;("
    return not ((index and line[index - 1] in "<>") or line[index + 1:index + 2] == ">")


def _heredoc_openers(line: str) -> List[_Heredoc]:
    line = strip_comment(line)
    openers, quote, index, start = [], "", 0, 0
    while index < len(line):
        char = line[index]
        if quote:
            quote = "" if char == quote else quote
        elif char == "\\":
            index += 1
        elif char in "'\"`":
            quote = char
        elif line.startswith(("||", "&&"), index):
            index = start = index + 2
            continue
        elif _starts_command(line, index):
            for heredoc in openers:
                heredoc.piped = heredoc.piped or (char == "|" and heredoc.segment == start)
            start = index + 1
        elif line.startswith("<<<", index):
            index += 2
        elif line.startswith("<<", index):
            opener = _read_heredoc_opener(line, index)
            if opener is not None:
                openers.append(_Heredoc(line[start:index].strip(), opener[0], opener[2], opener[1], start))
                index = opener[3]
                continue
        index += 1
    return openers


def _heredoc_as_program(heredoc: _Heredoc) -> Optional[str]:
    quote = "" if heredoc.expands else "'"
    command = f"{heredoc.reader} <<{quote}GAIA_BODY_END{quote}\n" + "".join(f"{b}\n" for b in heredoc.body) + "GAIA_BODY_END"
    program = heredoc_program(command)
    return command if program is not None and program.is_readable else None


def _heredoc_lines(body: List[str]) -> List[str]:
    return [line for line in (b.strip() for b in body) if line]


def _scan_heredocs(command: str) -> Tuple[str, List[_Heredoc]]:
    lines, pending, code = [], [], []
    for line in command.split("\n"):
        if not pending:
            lines.append(line)
            pending = _heredoc_openers(line)
            for heredoc in pending if line.endswith("\\") else []:
                heredoc.piped = True
            continue
        heredoc = pending[0]
        text = line.lstrip("\t") if heredoc.strip_tabs else line
        if text != heredoc.delimiter:
            heredoc.body.append(text)
            continue
        if heredoc.is_data and not heredoc.piped:
            lines.extend(f"echo {b}" for b in heredoc.body if heredoc.expands and set("$`") & set(b))
        else:
            lines.extend(heredoc.body)
        code.extend([] if heredoc.is_data else [heredoc])
        lines.append(line)
        pending.pop(0)
    for heredoc in pending:
        lines.extend(heredoc.body)
        code.extend([] if heredoc.is_data else [heredoc])
    return "\n".join(lines), code


def _rules_t3_outcome(decide):
    """Name the rule of a T3 outcome; an undeterminable span the decision reads makes it UNKNOWN."""
    @functools.wraps(decide)
    def wrapper(command: str, verb: str, category: str, *, rule: str = "", **kwargs) -> BashValidationResult:
        result = decide(command, verb, category, **kwargs)
        signing, undetermined = signing_rule(verb, category, command, rule)
        if signing.startswith("unknown.") and not undetermined:
            undetermined = "the span the decision reads"
        if not result.rule:
            result.rule, result.undetermined = signing, undetermined
        elif result.rule == "floor.no-requester":
            result.withheld = (signing, undetermined)
        return result
    return wrapper


# AI attribution footers (Claude Code, Copilot, aider, Windsurf, Cursor, Codex,
# Gemini, the Anthropic model names) that `_detect_claude_footers` looks for.
# The stripper only runs when the detector fires, so every shape
# `_strip_claude_footers` removes needs an entry here.
# No pattern anchors on a newline, so a footer in a second `-m "..."` argument matches too.
FORBIDDEN_FOOTER_PATTERNS = [
    r"Generated with\s+Claude Code",
    r"Generated with\s+\[?Claude Code\]?",
    r"🤖\s*Generated with",
    r"🤖",
    r"Co-Authored-By:\s+Claude\b",
    r"Claude-Session:",
    r"claude\.ai/code/session",
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bOpus\b",
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bSonnet\b",
    r"Co-[Aa]uthored-(?:[Bb]y|[Ww]ith):[^\n]*\bHaiku\b",
    r"Approved-by:",
    r"Co-authored-by:\s+GitHub Copilot\b",
    r"Co-authored-by:\s+aider\b",
    r"Co-authored-by:\s+Windsurf\b",
    r"Co-authored-by:\s+Cursor\b",
    r"Co-authored-by:\s+Codex\b",
    r"Co-authored-by:\s+Gemini\b",
]

# Indirect execution other than a shell's command string (which
# shell_command_string recognises): eval, inline interpreter code and a
# process substitution fed to a shell, matched as written and wrapper-peeled.

INDIRECT_EXEC_PATTERNS = [
    re.compile(r"^\s*eval\s+", re.IGNORECASE),
    re.compile(r"^\s*python3?\s+-c\s+", re.IGNORECASE),
    re.compile(r"^\s*node\s+-e\s+", re.IGNORECASE),
    re.compile(r"^\s*perl\s+-e\s+", re.IGNORECASE),
    re.compile(r"^\s*ruby\s+-e\s+", re.IGNORECASE),
    re.compile(r"^\s*bash\s+<\(", re.IGNORECASE),
    re.compile(r"^\s*sh\s+<\(", re.IGNORECASE),
]

def _plan_grant_for_denial(command: str) -> Optional[dict]:
    """Identify the plan-first grant a refused command belongs to, or None."""
    try:
        from gaia.store.writer import find_plan_grant_for_command

        return find_plan_grant_for_command(command)
    except Exception:
        return None


def _record_plan_command_denial(
    match: Optional[dict],
    reason_code: str,
    *,
    session_id: str,
    tool_use_id: str,
    agent_type: str,
) -> None:
    """Leave a durable denial on the approval a refused command belongs to.

    Never raises and changes no grant state: the caller has already decided the
    denial. A command belonging to no live plan-first grant has no approval row
    to write on and is skipped.
    """
    if match is None:
        return
    try:
        from gaia.approvals.store import record_execution_denial

        record_execution_denial(
            match["approval_id"],
            reason_code,
            host=os.environ.get("GAIA_HOST", "claude_code"),
            session_id=session_id,
            call_id=tool_use_id,
            index=match["index"],
            command_fingerprint=match["fingerprint"],
            agent_id=agent_type,
        )
    except Exception:
        return


def _uncorrelated_set_denial(pending_set: dict, *, session_id: str, tool_use_id: str) -> str:
    """Explain why a signed COMMAND_SET item was refused for want of host correlation.

    The refusal touches no grant state, so the message says the signature is
    still live and names the two ways forward instead of ending in a dead end.
    A chain reaches here too (its components carry no ``tool_use_id``), so the
    message also names running the item alone.
    """
    missing = " and ".join(
        name for name, value in (("session_id", session_id), ("tool_use_id", tool_use_id))
        if not value
    )
    approval_id = pending_set["approval_id"]
    return (
        "COMMAND_SET denied: adapter lacks stable tool-call correlation -- this call "
        f"carries no {missing}, and item [{pending_set['index']}] of {approval_id} is "
        f"reserved and settled against it. {approval_id} was not consumed and stays "
        "approved until its window closes: retry the byte-identical command alone (not "
        f"inside a chain) from a call whose host event carries a {missing}, or close BLOCKED "
        f"naming {approval_id} and this missing {missing} so the user knows the signed "
        "command did not run."
    )


class BashValidator:
    """Validator for Bash tool invocations.

    Implements a 5-phase pipeline: unwrap -> decompose -> classify ->
    composition -> aggregate.  See module docstring for phase details.
    """

    last_warning = ""

    def __init__(self, agent_names_scratch: bool = False):
        """Initialize the validator; only a host whose agent id names scratch entries passes *agent_names_scratch*, the ownership local-work relief keys on."""
        self.agent_names_scratch = agent_names_scratch
        self.shell_parser = get_shell_parser()
        self._unwrapper = ShellUnwrapper()
        self._decomposer = StageDecomposer()

    def _resolve_bare_gaia_token_for_guard(self, command: str) -> str:
        """Rewrite a bare `gaia` binary token to its $PATH-resolved absolute path.

        For gaia_cli_only_guard.check() only: the returned string is never
        executed. The guard requires an absolute binary path (it does not trust
        the ambient $PATH), so a bare word would never pass; resolving it here
        lets a normally typed `gaia ...` reach the guard's package-provenance
        identity check, which a binary no trusted package declares still fails.

        Returns *command* unchanged when no stage has a bare `gaia` token or
        $PATH has no `gaia`.
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

    def _detect_indirect_execution(
        self, command: str, is_subagent: bool = False,
    ) -> Optional[BashValidationResult]:
        """Judge indirect execution wrappers, which hide the real command in a string.

        A wrapped command that is blocked is denied. A subagent's wrapper is
        refused with the correct form; for the main session a mutative one
        returns None and reaches the T3 gate, and any other asks the host.
        Inline interpreter code (python3 -c, ...) also returns None, to its own
        scanner in mutative_verbs. None as well when no wrapper is present.
        """
        shell_string = shell_command_string(command)
        peeled = _peel_leading_command_wrappers(command)[0]
        if shell_string is None and not any(
            pattern.search(text) for pattern in INDIRECT_EXEC_PATTERNS for text in (command, peeled)
        ):
            return None

        inner = shell_string
        if inner is None:
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
                    rule="floor.indirect-execution",
                )

        logger.info("Indirect execution detected: %s", command[:80])
        result = detect_mutative_command(command)

        # Inline interpreter code (python3 -c) has its own scanner in
        # mutative_verbs, whose verdict is trusted over this generic dialog.
        from ..security.mutative_verbs import _INLINE_CODE_CLIS
        base_cmd = command.strip().split()[0].rsplit("/", 1)[-1].lower()
        if base_cmd in _INLINE_CODE_CLIS:
            logger.info(
                "Inline code classified by pattern scanner: %s",
                command[:80],
            )
            return None

        # A mutative shell wrapper reaches mutative_verbs' T3 gate for the
        # orchestrator. A subagent never signs a wrapped command: it is
        # refused below so the inner command runs directly and gets its own
        # gate.
        if result.is_mutative and not is_subagent:
            return None

        reason_msg = "Indirect execution wrapper detected — requires confirmation"
        if inner:
            inner_result = detect_mutative_command(inner)
            if inner_result.is_mutative and inner_result.verb:
                reason_msg = (
                    f"Indirect execution detected: inner mutative verb "
                    f"'{inner_result.verb}' — requires confirmation"
                )
        if is_subagent:
            deny_msg = (
                "Shell wrapper refused (bash -c, sh -c, eval). Run the "
                "command directly as one command; the Bash tool reports "
                "its exit code itself, so do not wrap it in a shell to "
                "capture it. For multi-step logic, commit a script file "
                "and invoke that, or use python3 <file>."
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T2_DRY_RUN,
                reason=deny_msg,
                block_response=build_hook_permission_response(
                    "deny", deny_msg,
                ),
                rule="floor.indirect-execution",
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
            rule="floor.indirect-execution",
        )

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
        background operators.  This is a fast, quote-blind pre-filter: an
        operator inside quotes (``bash -c 'a; b'``) also returns True, and the
        quote-aware shell parser downstream then yields a single component, so
        the branch that handles that outcome must forward every input the
        operator-free path gets (``tool_use_id`` above all).

        Note: '>' and '&' are included so a command still carrying one of
        these tokens after EARLY NORMALIZATION's sanitization pass (e.g. an
        embedded ``2>&1``, or a redirect that is not the trailing one
        sanitization strips) is still routed through the compound-parsing
        path below instead of being mis-treated as a simple command.
        """
        if not any(op in command for op in ['|', '&&', '||', ';', '\n', '>', '&']):
            return False
        return True

    # Patterns and helpers for decorators that can be safely stripped.
    _NOHUP_PREFIX_RE = re.compile(r"^\s*nohup\s+")
    _TRAILING_BG_RE = re.compile(r"\s*&\s*$")
    # Fd duplication (2>&1) is harmless and should NOT be stripped.
    _FD_DUP_RE = re.compile(r"\d+>&\d+")

    @staticmethod
    def _trailing_redirect_start(command: str) -> Optional[int]:
        """Return the start of a trailing file redirect outside shell quotes."""
        redirects: List[tuple[int, int]] = []
        quote: Optional[str] = None
        index = 0

        while index < len(command):
            char = command[index]
            if char == "\\" and quote != "'" and index + 1 < len(command):
                index += 2
                continue
            if char in ("'", '"'):
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None
                index += 1
                continue
            if quote is not None or char not in "<>":
                index += 1
                continue

            start = index
            if index > 0 and command[index - 1] == "&":
                boundary = index - 2
                if boundary < 0 or command[boundary].isspace():
                    start = index - 1
            else:
                descriptor_start = index
                while descriptor_start > 0 and command[descriptor_start - 1].isdigit():
                    descriptor_start -= 1
                boundary = descriptor_start - 1
                if descriptor_start < index and (boundary < 0 or command[boundary].isspace()):
                    start = descriptor_start

            following = command[index + 1:index + 2]
            if following == "(":
                index += 2
                continue
            if char == "<" and following == "<":
                operator_end = index + 2
                while operator_end < len(command) and command[operator_end] == "<":
                    operator_end += 1
                if operator_end < len(command) and command[operator_end] == "-":
                    operator_end += 1
                index = operator_end
                continue
            if following == "&":
                index += 2
                continue

            operator_end = index + 1
            if following == char or (char == "<" and following == ">") or (char == ">" and following == "|"):
                operator_end += 1
            redirects.append((start, operator_end))
            index = operator_end

        for start, operator_end in reversed(redirects):
            target = command[operator_end:].strip()
            if not target:
                continue
            try:
                if len(shlex.split(target, posix=True)) == 1:
                    return start
            except ValueError:
                continue
        return None

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

        # Strip one trailing file redirect. Quoted and escaped operators are data.
        redirect_start = self._trailing_redirect_start(cleaned)
        if redirect_start is not None:
            before_redirect = cleaned[:redirect_start].rstrip()
            if before_redirect and not before_redirect.endswith("="):
                cleaned = before_redirect
                stripped_parts.append("redirect")

        if not stripped_parts:
            return None  # Nothing to sanitize

        if cleaned == original:
            return None  # Sanitization didn't change anything

        return cleaned, stripped_parts

    @_carries_ruling
    def validate(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        hook_payload: Optional[Dict[str, Any]] = None,
        cwd: Optional[str] = None,
    ) -> BashValidationResult:
        """
        Validate a Bash command through the 5-phase pipeline.

        Phases:
            1. UNWRAP      - strip shell wrappers, detect obfuscation
            2. DECOMPOSE   - split into operator-linked stages
            3. CLASSIFY    - blocked_commands + cloud_pipe + mutative_verbs
            4. COMPOSITION - cross-stage pipe rules (_phase4_check_composition)
            5. AGGREGATE   - combine results into final verdict

        A phase 0, ahead of all of the above, runs the GAIA CLI ONLY GUARD.

        Args:
            command: Command string to validate
            is_subagent: True when running in subagent context
            session_id: Session ID for approval scoping
            agent_type: Name of the originating agent (for pending approval context)
            hook_payload: The full stdin JSON dict from the harness, when the
                caller has one. `None`, never a substituted `{}`, keeps the
                phase 0 guard inert: a dict with an empty agent_type is the
                orchestrator role and is guarded.
            cwd: The caller's directory when no hook_payload carries one.

        Returns:
            BashValidationResult with validation details
        """
        # The fail-open mark belongs to the previous command; dropped ahead of
        # phase 0, which can return early. See modules.security.fail_open.
        clear_classification()
        self.last_warning = ""
        cwd = (hook_payload or {}).get("cwd") or cwd

        # Phase 0 runs ahead of every transformation of `command`, because the
        # role closure must judge the string exactly as the harness sent it.
        # The payload goes to the guard unflattened: classify_session_role reads
        # an empty agent_type as the orchestrator role (gaia_cli_only_guard.py,
        # Design decision 1).
        if hook_payload is not None:
            from ..security.gaia_cli_only_guard import check as check_gaia_cli_only

            guard_command = self._resolve_bare_gaia_token_for_guard(command)
            guard_allowed, guard_reason = check_gaia_cli_only(guard_command, hook_payload)
            if not guard_allowed:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=guard_reason,
                    rule="floor.gaia-cli-only",
                )

        if not command or not command.strip():
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason="Empty command not allowed",
                rule="floor.empty-command",
            )

        command = command.strip()

        # The OpenCode plugin's consent verbs run from its own spawn; from a
        # shell they would let a requester approve itself. Categorical, and ahead
        # of the sealed-retry lane so no signature can carry them.
        consent_verb_allowed, consent_verb_reason = check_host_consent_verb(
            command, cwd=cwd,
        )
        if not consent_verb_allowed:
            logger.warning("BLOCKED host consent verb from a shell: %s", command[:120])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=consent_verb_reason,
                rule="floor.host-consent-verb",
            )

        sealed = self._sealed_elsewhere(command, session_id, agent_type, hook_payload)
        if sealed is not None:
            return self._validate_sealed_elsewhere(
                sealed, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, hook_payload=hook_payload,
            )

        # EARLY NORMALIZATION: footers are stripped first so every later check
        # and the pending-approval lookup see the same string on retry.
        command_was_modified = False
        if self._detect_claude_footers(command):
            command = self._strip_claude_footers(command)
            command_was_modified = True
            logger.info("Auto-stripped Claude Code footer from commit command")

        # Refuses a publishing command whose text or named body file still
        # carries Claude attribution after the strip; ahead of tier
        # classification so a refusal mints no approval.
        attribution_allowed, attribution_reason = check_publish_attribution(
            command, cwd,
        )
        if not attribution_allowed:
            logger.warning("BLOCKED Claude attribution in published text: %s", command[:100])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=attribution_reason,
                rule="floor.publish-attribution",
            )

        # Ahead of tier classification so a refused push mints no approval; a
        # counter failure allows it and leaves its warning in `last_warning`.
        comment_push = check_comment_push(command, cwd)
        if not comment_push.allowed:
            logger.warning("BLOCKED push over the comment limit: %s", command[:100])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=comment_push.reason,
                rule="floor.comment-push-gate",
            )
        self.last_warning = comment_push.warning or ""

        # A data heredoc's body is stdin the shell never runs, so the write
        # guards below read only its command line; a command line carrying a
        # redirect is not a data heredoc at all and is read whole.
        write_guard_scope = data_heredoc_header(command) or command

        # The guards from here to the sensitive-read check run before
        # unwrap/decompose so wrappers and heredocs are inspected intact.
        db_write_allowed, db_write_reason = check_gaia_db_write(write_guard_scope, cwd)
        if not db_write_allowed:
            logger.warning("BLOCKED gaia.db direct write: %s", command[:100])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=db_write_reason,
                rule="floor.gaia-db-write",
                suggestions=[
                    "Use the `gaia context` CLI or emit update_contracts.",
                ],
            )

        # A subagent proposes memory through `memory_delta`; the direct write is
        # a categorical deny, not an approval. See the memory skill's "Who writes".
        mem_write_allowed, mem_write_reason = check_subagent_memory_write(
            write_guard_scope, is_subagent=is_subagent, agent_type=agent_type,
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
                rule=_MEMORY_GUARD_RULES.get(mem_write_reason, "floor.subagent-memory-write"),
                suggestions=[] if mem_write_reason in _MEMORY_GUARD_RULES else [
                    "Propose it in your contract row's `memory_delta` "
                    "with `gaia contract set memory_delta '<json>'` instead.",
                ],
            )

        skill_allowed, skill_reason = check_memory_skill_loaded(
            command, is_subagent=is_subagent, session_id=session_id,
        )
        if not skill_allowed:
            logger.warning("BLOCKED memory write without Skill('memory'): %s", command[:100])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=skill_reason,
                rule="floor.memory-skill-not-loaded",
            )

        resume_allowed, resume_reason = check_subagent_resume_point(
            command, is_subagent=is_subagent,
        )
        if not resume_allowed:
            logger.warning(
                "BLOCKED subagent resume-point write (agent=%s): %s",
                agent_type or "?", command[:100],
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=resume_reason,
                rule="floor.subagent-resume-point",
            )

        # A subagent that inits a second contract or writes one another turn
        # adopted is told at the attempt, not at the stop gate. Categorical.
        ownership_allowed, ownership_reason = check_contract_ownership(
            command, (hook_payload or {}).get("agent_id") or "",
            (hook_payload or {}).get("session_id") or "",
        )
        if not ownership_allowed:
            logger.warning(
                "BLOCKED contract ownership (agent=%s): %s",
                agent_type or "?", command[:100],
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=ownership_reason,
                rule="floor.contract-ownership",
                suggestions=[
                    "Address your own contract with --draft-id <contract_id>.",
                ],
            )

        # The Write/Edit backstop never sees a Bash command string, so a writer
        # such as `git mv x .claude/hooks/y.py` (local-safe, non-mutative) needs
        # this categorical guard.
        pp_write_allowed, pp_write_reason = check_protected_path_write(
            write_guard_scope, cwd=cwd
        )
        if not pp_write_allowed:
            logger.warning(
                "BLOCKED protected-path write via Bash: %s", command[:120]
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=pp_write_reason,
                rule="floor.protected-path",
                suggestions=[
                    "Edit the SOURCE under gaia/ and run `gaia install` to "
                    "propagate; never modify .claude/ directly.",
                ],
            )

        # A shell-authored write (redirect, `tee`, `sed -i`, `dd of=`) into a git
        # tree is refused as a channel: the same edit through Write/Edit is
        # permitted. Before the sanitizer, which strips a trailing redirect.
        shell_write_allowed, shell_write_reason = check_shell_write(
            write_guard_scope, cwd=cwd,
        )
        if not shell_write_allowed:
            logger.warning(
                "BLOCKED shell-authored working-tree write: %s", command[:120]
            )
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=shell_write_reason,
                rule="floor.shell-write",
                suggestions=[
                    "Use the Write or Edit tool on the file instead; a "
                    "throwaway dump belongs under ~/.gaia/scratch.",
                ],
            )

        # A sensitive read is refused before any check that can ask, and before the
        # sanitizer, which would strip a `< file` input with the trailing redirect.
        sensitive_allowed, sensitive_reason = check_sensitive_read(
            _scan_heredocs(command)[0], cwd, program=command,
        )
        if not sensitive_allowed:
            logger.warning("BLOCKED sensitive read via Bash: %s", command[:120])
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=sensitive_reason,
                rule="floor.secret-read",
            )

        # A redirect onto an account path is decided here as T3, before the
        # sanitizer strips the redirect and before the cloud-pipe phase refuses
        # a redirect as a channel with no way to consent.
        account_write_target = account_path_redirect_target(write_guard_scope)
        if account_write_target:
            return decide_t3_outcome(
                command,
                verb="account-path-write",
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                session_id=session_id,
                agent_type=agent_type,
            )

        unprovable_target = unprovable_write_target(write_guard_scope, cwd)
        if unprovable_target:
            return decide_t3_outcome(
                command,
                verb="unprovable-write-target",
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                session_id=session_id,
                agent_type=agent_type,
            )

        sqlite_guidance = sqlite_unproven_effect(write_guard_scope, cwd)
        if sqlite_guidance:
            return decide_t3_outcome(
                command,
                verb="sqlite-reach",
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                session_id=session_id,
                agent_type=agent_type,
                guidance=sqlite_guidance,
            )

        # A file fed by redirect into a sender, or copied to another host, leaves the
        # machine; decided before the sanitizer, which strips the `< file` it rests on.
        redirect_sender = (redirected_into_sender(write_guard_scope)
                           or remote_copy_destination(write_guard_scope))
        if redirect_sender:
            return decide_t3_outcome(
                command,
                verb="egress",
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                session_id=session_id,
                agent_type=agent_type,
            )

        # After the write guards, so their refusals win; the operator splitter
        # would otherwise cut a data heredoc's prose into "components".
        data_heredoc_result = self._validate_data_heredoc(
            command,
            command_was_modified=command_was_modified,
            is_subagent=is_subagent,
            session_id=session_id,
            agent_type=agent_type,
            hook_payload=hook_payload,
        )
        if data_heredoc_result is not None:
            return data_heredoc_result

        # SMART SANITIZATION strips nohup, a trailing `&` or a trailing redirect.
        # It runs after the write guards, which must see a redirect into a
        # guarded target, and returns no verdict: the cleaned command flows
        # into phase 1 (tests/hooks/test_sanitizer_redirect_t3_evasion.py).
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

        # PHASE 1: UNWRAP
        unwrap_result = self._unwrapper.unwrap(command)
        if unwrap_result.depth >= _OBFUSCATION_DEPTH_LIMIT:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=(
                    f"Obfuscated shell nesting detected: {unwrap_result.depth} "
                    f"wrapper layers exceeds limit of {_OBFUSCATION_DEPTH_LIMIT}"
                ),
                rule="floor.obfuscated-nesting",
            )

        indirect_result = self._detect_indirect_execution(
            command, is_subagent=is_subagent,
        )
        if indirect_result is not None:
            return indirect_result

        # PHASE 2: DECOMPOSE keeps the operators that ShellCommandParser.parse()
        # discards, for the phase 4 composition rules.
        classified, code_heredocs = _scan_heredocs(command)
        decomposed = self._decomposer.decompose(classified)

        # PHASE 3: CLASSIFY STAGES (3a-3d here; 3e is the dispatch in phase 5)

        # 3a. Blocked commands on the command minus its data-heredoc bodies; this
        # runs before any other classifier so a permanent block always exits 2.
        blocked_result = is_blocked_command(classified)
        if blocked_result.is_blocked:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Command blocked by security policy: {blocked_result.category}",
                suggestions=[blocked_result.suggestion] if blocked_result.suggestion else [],
                rule=blocked_rule(blocked_result.category),
            )

        # 3b. Each compound component against the deny list, so
        # "ls && kubectl delete namespace prod" is caught here.
        has_operators = self._has_operators(command)
        parsed_components = None
        if has_operators:
            parsed_components = self.shell_parser.parse(classified)
            for component in parsed_components:
                comp_blocked = is_blocked_command(component.strip())
                if comp_blocked.is_blocked:
                    return BashValidationResult(
                        allowed=False,
                        tier=SecurityTier.T3_BLOCKED,
                        reason=f"Command blocked by security policy: {comp_blocked.category}",
                        suggestions=[comp_blocked.suggestion] if comp_blocked.suggestion else [],
                        rule=blocked_rule(comp_blocked.category),
                    )

        secret_violation = check_kubectl_secret_output(classified)
        if secret_violation is not None:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=secret_violation.reason,
                suggestions=[secret_violation.suggestion],
                rule="floor.kubectl-secret-output",
            )

        # 3c. Validate git commit messages (on the potentially cleaned command).
        if "git commit" in command and "-m" in command:
            commit_validation = self._validate_commit_message(command)
            if not commit_validation.allowed:
                return commit_validation

        # 3d. Cloud pipe/redirect/chaining check.
        pipe_block = validate_cloud_pipe(classified)
        if pipe_block is not None:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=read_permission_reason(pipe_block),
                suggestions=[],
                modified_input=None,
                block_response=pipe_block,
                rule="floor.shell-write" if violation_kind(classified) == "redirect" else "floor.cloud-pipe",
            )

        # PHASE 4: CHECK COMPOSITION of pipe-connected stages; &&/; stages are
        # independent (exfiltration, RCE, decode-to-exec, file-to-exec).
        _composition_result = self._phase4_check_composition(
            decomposed,
            is_subagent=is_subagent,
            session_id=session_id,
            agent_type=agent_type,
            tool_use_id=str((hook_payload or {}).get("tool_use_id", "")),
            cwd=(hook_payload or {}).get("cwd") or None,
        )
        if _composition_result is not None:
            return _composition_result

        # PHASE 5: AGGREGATE (3e). The invocation directory seeds the cwd fold; a
        # fold from None would resolve a relative script against the hook's own
        # cwd and fall to the script-file-unreadable T3. A later `cd` overrides it.
        payload_cwd = cwd
        tool_use_id = str((hook_payload or {}).get("tool_use_id", ""))
        program = heredoc_program(command)
        # An interpreter's heredoc is its program: splitting it into shell
        # components would validate the body's lines one by one and never
        # show the classifier the program whose effect decides the tier.
        agent_id = str((hook_payload or {}).get("agent_id") or "") if self.agent_names_scratch else ""
        if not has_operators or program is not None:
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, tool_use_id=tool_use_id, cwd=payload_cwd,
                caller=LocalWorkCaller(agent_id, payload_cwd),
            )
        elif parsed_components is not None and len(parsed_components) > 1:
            result = self._validate_compound_command(
                parsed_components, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, cwd=payload_cwd,
                command=command, tool_use_id=tool_use_id, agent_id=agent_id,
            )
        else:
            result = self._validate_single_command(
                command, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, tool_use_id=tool_use_id, cwd=payload_cwd,
                caller=LocalWorkCaller(agent_id, payload_cwd),
            )
        if code_heredocs:
            result = self._fail_closed_on_code_heredoc(
                code_heredocs, result, is_subagent=is_subagent, cwd=payload_cwd,
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

    def _fail_closed_on_code_heredoc(
        self, heredocs: List[_Heredoc], result: BashValidationResult, *, is_subagent: bool, cwd: Optional[str],
    ) -> BashValidationResult:
        """Hold a heredoc no pure data reader reads to at least UNKNOWN; a body reading on a floor blocks, a mutative one signs."""
        readings, xargs_commands, unread, unplaced = [], [], False, False
        for heredoc in heredocs:
            program, payload = _heredoc_as_program(heredoc), _xargs_payload(heredoc.reader)
            if program:
                readings.append(program)
            elif payload is not None:
                lines = _heredoc_lines(heredoc.body)
                xargs_commands.extend(f"{payload} {line}" for line in lines)
                unplaced = unplaced or (cwd is None and any(w[:1] not in "/~-" for line in lines for w in line.split()))
            else:
                unread = True
                readings.extend(_heredoc_lines(heredoc.body))
        signed = None
        for xargs_command in xargs_commands:
            verdict = BashValidator.validate.__wrapped__(self, xargs_command, cwd=cwd)
            asks = verdict.withheld is not None or (
                verdict.block_response is not None and read_permission_decision(verdict.block_response) == "ask"
            )
            if not (verdict.allowed or asks):
                return verdict
            if verdict.withheld is not None:
                verdict.rule, verdict.undetermined = verdict.withheld
            signed = signed or (None if verdict.allowed else verdict)
        for reading in readings:
            blocked = is_blocked_command(reading)
            if blocked.is_blocked:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Command blocked by security policy: {blocked.category}",
                    suggestions=[blocked.suggestion] if blocked.suggestion else [],
                    rule=blocked_rule(blocked.category),
                )
        if not result.allowed:
            return result
        rule, undetermined = UNKNOWN_SPAN, "the program its heredoc feeds"
        if unplaced and not unread:
            undetermined = "the directory a relative xargs argument resolves against"
        tripped = signed is not None
        if tripped:
            rule, undetermined = signed.rule, signed.undetermined
        for reading in [] if tripped else readings:
            detected = detect_mutative_command(reading, cwd=cwd)
            if detected.is_mutative:
                rule, undetermined = signing_rule(detected.verb, detected.category, reading, detected.rule)
                if rule.startswith("unknown.") and not undetermined:
                    undetermined = "the span the decision reads"
                tripped = True
                break
        if not (tripped or unread or unplaced):
            return result
        return BashValidationResult(
            allowed=False,
            tier=SecurityTier.T3_BLOCKED,
            reason=_COMPOUND_T3_DENIAL,
            block_response=build_hook_permission_response("deny", _COMPOUND_T3_DENIAL),
            rule=rule,
            undetermined=undetermined,
        )

    def _validate_data_heredoc(
        self,
        command: str,
        command_was_modified: bool,
        is_subagent: bool,
        session_id: str,
        agent_type: str,
        hook_payload: Optional[Dict[str, Any]],
    ) -> Optional[BashValidationResult]:
        """Classify a data-heredoc command by its header alone, or return None.

        None hands the command back to the ordinary pipeline unchanged. Only an
        allowed verdict with nothing to rewrite is adopted: a T3 header must
        be consented over bytes that include its body, so it is refused before
        the header is classified (classifying it would mint a pending approval
        for the body-less string), and a sanitizer rewrite of the header would
        otherwise become updatedInput and drop the body from what runs. The
        permanent-deny floor reads the header too: the body is stdin the CLI
        stores, so a force push it merely names runs nowhere.
        """
        header = data_heredoc_header(command)
        if header is None or is_blocked_command(header).is_blocked:
            return None
        cwd = (hook_payload or {}).get("cwd") or None
        if detect_mutative_command(header, cwd=cwd).is_mutative:
            return None
        result = self.validate(
            header,
            is_subagent=is_subagent,
            session_id=session_id,
            agent_type=agent_type,
            hook_payload=hook_payload,
        )
        if (
            not result.allowed
            or result.tier == SecurityTier.T3_BLOCKED
            or result.modified_input is not None
            or result.block_response is not None
        ):
            return None
        if command_was_modified:
            result.modified_input = {"command": command}
        return result

    @staticmethod
    def _sealed_elsewhere(
        command: str, session_id: str, agent_type: str, hook_payload: Optional[Dict[str, Any]],
    ) -> Optional[tuple]:
        """The ``(cwd, command)`` of an exact sealed-directory form (D24), else ``None``.

        Only a live host event qualifies: without its payload there is no
        requester to bind the form to and no directory to run the command in.
        """
        if hook_payload is None or " && " not in command:
            return None
        from gaia.approvals.core import sealed_elsewhere

        return sealed_elsewhere(command, session_id=session_id, agent_id=agent_type)

    def _validate_sealed_elsewhere(
        self,
        sealed: tuple,
        *,
        is_subagent: bool,
        session_id: str,
        agent_type: str,
        hook_payload: Dict[str, Any],
    ) -> BashValidationResult:
        """Validate the one compound accepted as its sealed command run alone in its sealed directory.

        The chain rules never see it, and every check a single command gets
        applies to the sealed command. A rewrite of that command keeps the
        ``cd`` so the host still runs it where it was sealed.
        """
        from gaia.approvals.core import sealed_invocation

        sealed_cwd, sealed_command = sealed
        result = self.validate(
            sealed_command, is_subagent=is_subagent, session_id=session_id,
            agent_type=agent_type, hook_payload={**hook_payload, "cwd": sealed_cwd},
        )
        if result.modified_input and "command" in result.modified_input:
            result.modified_input = {
                **result.modified_input,
                "command": sealed_invocation(sealed_cwd, result.modified_input["command"]),
            }
        return result

    def _validate_single_command(
        self,
        command: str,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        cwd: Optional[str] = None,
        tool_use_id: str = "",
        caller: Optional[LocalWorkCaller] = None,
        piped_from: Optional[str] = None,
    ) -> BashValidationResult:
        """Validate a single command (no operators); *caller* is what the local-work relief needs, *piped_from* the stage feeding its stdin.

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

        # A component of a split chain ("cd /tmp && python3 -c ...") gets the same
        # indirect-execution gate as the whole command.
        indirect_result = self._detect_indirect_execution(
            command, is_subagent=is_subagent,
        )
        if indirect_result is not None:
            return indirect_result

        # ``cwd`` is the directory folded from a preceding ``cd`` component (see
        # _validate_compound_command), against which a relative script resolves.
        result = detect_mutative_command(command, cwd=cwd, caller=caller)
        result = production_target_verdict(command, result, cwd=cwd) or result
        if result.is_mutative:
            from gaia.store.writer import find_pending_plan_command
            if not session_id or not tool_use_id:
                try:
                    pending_set = find_pending_plan_command(command)
                except Exception as exc:
                    return BashValidationResult(
                        allowed=False, tier=SecurityTier.T3_BLOCKED,
                        reason=f"COMMAND_SET persistence failed closed: {exc}", rule="floor.approval-store",
                    )
                if pending_set is not None:
                    _record_plan_command_denial(
                        pending_set, "adapter_lacks_correlation",
                        session_id=session_id, tool_use_id=tool_use_id,
                        agent_type=agent_type,
                    )
                    return BashValidationResult(
                        allowed=False, tier=SecurityTier.T3_BLOCKED,
                        reason=_uncorrelated_set_denial(
                            pending_set, session_id=session_id, tool_use_id=tool_use_id,
                        ),
                    )
            try:
                from gaia.approvals.core import match_command
                cs_match = match_command(
                    command, cwd=cwd or os.getcwd(), session_id=session_id,
                    agent_id=agent_type or None, tool_use_id=tool_use_id,
                )
            except Exception as exc:
                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"COMMAND_SET persistence failed closed: {exc}", rule="floor.approval-store",
                )
            if cs_match is not None:
                return BashValidationResult(
                    allowed=True, tier=SecurityTier.T3_BLOCKED,
                    reason="Ordered COMMAND_SET command reserved",
                    consumed_approval_id=cs_match["approval_id"],
                    command_set_reservation=cs_match,
                )

            from gaia.approvals.core import grant_lookup_filter
            grant = check_approval_grant(
                command, session_id=session_id,
                requester=grant_lookup_filter(
                    cwd=cwd or os.getcwd(), session_id=session_id, agent_id=agent_type,
                ),
            )
            if grant is not None:
                # Consume the DB semantic grant immediately (replay protection):
                # a consumed grant will not match on a second attempt within the
                # TTL window.
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

                if grant.confirmed:
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
                    # Granted but not yet confirmed: let it through; PostToolUse
                    # confirms the grant after successful execution.
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
                _record_plan_command_denial(
                    _plan_grant_for_denial(command), "plan_command_not_reserved",
                    session_id=session_id, tool_use_id=tool_use_id,
                    agent_type=agent_type,
                )
                return decide_t3_outcome(
                    command,
                    verb=result.verb,
                    category=result.category,
                    has_orchestrator_above=is_subagent,
                    session_id=session_id,
                    agent_type=agent_type,
                    guidance=result.guidance,
                    cwd=cwd,
                    rule=result.rule,
                )

        # Flag-dependent mutations a verb scan misses ("sed" has no mutative verb,
        # "sed -i" is). Git is excluded from the MUTATIVE path, since
        # detect_mutative_command() handles git deliberately; a git BLOCKED
        # result still stops the command.
        flag_result = classify_by_flags(command, caller, piped_from=piped_from)
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
                    # A flag-classified command (`curl -X POST`) consults the same
                    # grants as the mutative-verb branch above, or an approved
                    # grant would never be honoured on retry.
                    try:
                        from gaia.approvals.core import match_command
                        cs_match = match_command(
                            command, cwd=cwd or os.getcwd(), session_id=session_id,
                            agent_id=agent_type or None, tool_use_id=tool_use_id,
                        )
                    except Exception as exc:
                        return BashValidationResult(
                            allowed=False,
                            tier=SecurityTier.T3_BLOCKED,
                            reason=f"COMMAND_SET persistence failed closed: {exc}", rule="floor.approval-store",
                        )
                    if cs_match is not None:
                        return BashValidationResult(
                            allowed=True, tier=SecurityTier.T3_BLOCKED,
                            reason="Ordered COMMAND_SET command reserved",
                            consumed_approval_id=cs_match["approval_id"],
                            command_set_reservation=cs_match,
                        )

                    from gaia.approvals.core import grant_lookup_filter
                    grant = check_approval_grant(
                        command, session_id=session_id,
                        requester=grant_lookup_filter(
                            cwd=cwd or os.getcwd(), session_id=session_id,
                            agent_id=agent_type,
                        ),
                    )
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

                    return decide_t3_outcome(
                        command,
                        verb=flag_result.matched_pattern or flag_result.command_family,
                        category="MUTATIVE",
                        has_orchestrator_above=is_subagent,
                        session_id=session_id,
                        agent_type=agent_type,
                        cwd=cwd,
                    )

        # Not blocked, not mutative -> SAFE by elimination
        return BashValidationResult(
            allowed=True,
            tier=SecurityTier.T0_READ_ONLY,
            reason="Safe by elimination (not blocked, not mutative)",
        )

    def _validate_compound_command(
        self,
        components: List[str],
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        cwd: Optional[str] = None,
        command: str = "",
        tool_use_id: str = "",
        agent_id: str = "",
    ) -> BashValidationResult:
        """Validate a compound command (multiple components).

        Compound T3 execution is REFUSED here, never grouped. If ANY component
        classifies T3 the whole chain is denied (subagent) or asked (primary):
        "Compound T3 execution is disabled. Create a plan-first request-set and
        run each command directly as a separate Bash call, with no wrapper
        (bash -c, sh -c, eval)." There is no COMMAND_SET
        intake on this path -- consent grouping is requested plan-first via
        ``gaia approvals request-set``, and execution stays one command per Bash
        call, so a chain is never the surface on which a set is discovered.
        The one exception is a pipeline whose exact bytes (``command``) are a
        live signed set item: request-set seals pipelines as one item, so that
        item is matched and reserved here instead of denied.

        A chain with NO T3 component runs the per-component pass: each component
        is validated by ``_validate_single_command``, a blocked component fails
        the chain fast, and the chain's tier is the highest component tier.

        ``cwd`` seeds the directory fold below (the hook payload's directory, or
        ``None``); a leading ``cd`` component overrides it.
        """
        logger.info(f"Compound command detected with {len(components)} components")

        # The cwd is folded across components here as in the loop below, or a
        # relative script after `cd X` would read as unreadable and falsely trip
        # has_t3 through the script-file-unreadable default.
        has_t3 = False
        scan_cwd: Optional[str] = cwd
        # A NODE_OPTIONS set by an earlier component reaches every later node.
        node_options_set = False
        # Code-injecting variables an earlier component set (export, assignment) reach every later program.
        injected_environment: tuple = ()
        git_config_injected = False
        # A package.json or node_modules path rewritten by an earlier component
        # changes the script a later run executes, which was read before the chain ran.
        package_inputs_rewritten = False
        line_targets = targets_set_on_line(command or "\n".join(components))
        line_naming = naming_variables_on_line(command or "\n".join(components))
        tripped, tripped_command = None, command
        callers = local_work_callers(command, components, cwd, agent_id)
        feeders = self._decomposer.pipe_feeders(command or "", components)
        for comp, caller, feeder in zip(components, callers, feeders):
            detected = detect_mutative_command(comp, cwd=scan_cwd, caller=caller)
            if package_inputs_rewritten and runs_package_script(comp):
                has_t3 = True
                break
            package_inputs_rewritten = package_inputs_rewritten or rewrites_package_inputs(comp)
            if node_options_set and not detected.is_mutative:
                detected = node_test_under_node_options(comp, scan_cwd) or detected
            node_options_set = node_options_set or sets_node_options(comp)
            if injected_environment and not detected.is_mutative:
                detected = runs_under_injected_environment(comp, injected_environment, scan_cwd) or detected
            injected_environment += code_injecting_assignments(comp)
            if git_config_injected and not detected.is_mutative:
                detected = git_under_injected_config(comp) or detected
            git_config_injected = git_config_injected or injects_git_config(comp)
            if not detected.is_mutative:
                detected = target_under_line(comp, line_targets, cwd=scan_cwd) or detected
            detected = production_target_verdict(comp, detected, cwd=scan_cwd, inherited=line_naming) or detected
            flagged = classify_by_flags(comp, caller, piped_from=feeder)
            scan_cwd = cwd_after_component(comp, scan_cwd)
            if detected.is_mutative or (
                flagged is not None
                and flagged.outcome == FLAG_MUTATIVE
                and not flagged.command_family.startswith("git_")
            ):
                has_t3 = True
                tripped, tripped_command = detected, comp
                break
        if not has_t3:
            has_t3 = self._has_mutative_substitution_outside_components(command, components, cwd)
        if has_t3:
            signed = self._match_signed_pipeline(
                command, components, session_id=session_id, agent_type=agent_type,
                tool_use_id=tool_use_id, cwd=cwd,
            )
            if signed is not None:
                return signed
            reason = _COMPOUND_T3_DENIAL
            decision = "deny"
            rule, undetermined = (
                signing_rule(tripped.verb, tripped.category, tripped_command, tripped.rule)
                if tripped is not None else signing_rule("", "MUTATIVE", command)
            )
            if rule.startswith("unknown.") and not undetermined:
                undetermined = "the span the decision reads"
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=reason,
                block_response=build_hook_permission_response(decision, reason),
                rule=rule,
                undetermined=undetermined,
            )

        component_results: List[BashValidationResult] = []
        # `cd` and a later script are separate components, and
        # detect_mutative_command sees one at a time, so the fold lives here.
        running_cwd: Optional[str] = cwd
        for i, component in enumerate(components, 1):
            # tool_use_id is withheld on purpose: a COMMAND_SET item runs alone
            # and must never be reserved as one component of a chain.
            result = self._validate_single_command(
                component, is_subagent=is_subagent, session_id=session_id,
                agent_type=agent_type, cwd=running_cwd, caller=callers[i - 1], piped_from=feeders[i - 1],
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
        # grant, so the call's terminal event closes that approval.
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

    @staticmethod
    def _has_mutative_substitution_outside_components(
        command: str, components: List[str], cwd: Optional[str],
    ) -> bool:
        """Whether the whole command runs a mutative substitution no component shows.

        A split cuts a heredoc's body into lines read outside the heredoc, where
        quotes quote; in the unquoted body they are plain text and
        ``'$(rm -rf x)'`` runs. Bodies a component already shows were judged
        there, in that component's folded cwd, and are not judged again here.
        """
        bodies, truncated = extract_substitutions_truncated(command, top_level_only=True)
        if truncated:
            return True
        shown = {
            body for comp in components
            for body in extract_substitutions(comp, top_level_only=True)
        }
        return any(
            detect_mutative_command(body, cwd=cwd).is_mutative
            for body in bodies if body not in shown
        )

    def _match_signed_pipeline(
        self,
        command: str,
        components: List[str],
        *,
        session_id: str,
        agent_type: str,
        tool_use_id: str,
        cwd: Optional[str],
    ) -> Optional[BashValidationResult]:
        """Reserve a pipeline whose exact bytes are a live signed set item, else None.

        Only a chain whose every separator is a pipe qualifies: the component
        count must equal the unquoted pipe-stage count ``request-set`` sealed.
        """
        if not (command and session_id and tool_use_id):
            return None
        from gaia.approvals.command_set import pipe_stages
        try:
            if len(pipe_stages(command)) != len(components):
                return None
        except ValueError:
            return None
        try:
            from gaia.approvals.core import match_command
            cs_match = match_command(
                command, cwd=cwd or os.getcwd(), session_id=session_id,
                agent_id=agent_type or None, tool_use_id=tool_use_id,
            )
        except Exception as exc:
            return BashValidationResult(
                allowed=False, tier=SecurityTier.T3_BLOCKED,
                reason=f"COMMAND_SET persistence failed closed: {exc}", rule="floor.approval-store",
            )
        if cs_match is None:
            return None
        return BashValidationResult(
            allowed=True, tier=SecurityTier.T3_BLOCKED,
            reason="Ordered COMMAND_SET command reserved",
            consumed_approval_id=cs_match["approval_id"],
            command_set_reservation=cs_match,
        )

    def _phase4_check_composition(
        self,
        decomposed: DecomposedCommand,
        is_subagent: bool = False,
        session_id: str = "",
        agent_type: str = "",
        tool_use_id: str = "",
        cwd: Optional[str] = None,
    ) -> Optional[BashValidationResult]:
        """Check cross-stage composition patterns (Phase 4).

        Detects dangerous pipe compositions:
        - Exfiltration: sensitive_read | network_write  -> permanent block
        - RCE: network_read | exec_sink                 -> permanent block
        - Obfuscated exec: decode | exec_sink           -> permanent block
        - File-to-exec: file_read | exec_sink           -> escalate (T3 outcome)

        Args:
            decomposed: Output from StageDecomposer.decompose().

        Returns:
            BashValidationResult if a composition rule fires, else None.
        """
        if not decomposed.stages or len(decomposed.stages) < 2:
            return None

        has_pipe = any(s.operator == "|" for s in decomposed.stages)
        if not has_pipe:
            return None

        comp_stages = build_composition_stages(decomposed.stages)
        result = check_composition(comp_stages)

        if result.decision == CompositionDecision.BLOCK:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Dangerous pipe composition blocked: {result.reason}",
                rule=COMPOSITION_RULES.get(result.pattern, "floor.unclassified"),
            )

        if result.decision == CompositionDecision.ESCALATE:
            # A signable pipe runs on its grant here: returning the T3 outcome
            # first would leave the signed set item unreachable.
            if check_sensitive_read(decomposed.raw, cwd)[0]:
                signed = self._match_signed_pipeline(
                    decomposed.raw, decomposed.stages, session_id=session_id,
                    agent_type=agent_type, tool_use_id=tool_use_id, cwd=cwd,
                )
                if signed is not None:
                    return signed
            return decide_t3_outcome(
                decomposed.raw,
                verb=result.pattern,
                category="MUTATIVE",
                has_orchestrator_above=is_subagent,
                session_id=session_id,
                agent_type=agent_type,
            )

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

        This stripper sees only footers present as literal text in the Bash
        ``command`` string and never rewrites files. What it leaves behind in
        a publishing command -- a footer shape it does not remove, or a body
        file such as ``git commit -F <file>`` / ``gh pr create --body-file`` --
        is refused by ``security.publish_attribution_guard``, which runs right
        after it. A commit made outside the Bash tool entirely (a terminal or
        IDE commit) is reached by neither; the ``commit-msg`` git hook that once
        backstopped it was removed in commit 4dd88f2.

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

        # Collapse the blank lines a removed footer leaves behind.
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
        message = self._extract_commit_message(command)

        if not message:
            # Could not extract message - let it pass, git will handle it
            return BashValidationResult(
                allowed=True,
                tier=SecurityTier.T2_DRY_RUN,
                reason="Could not extract commit message for validation"
            )

        try:
            import sys
            from pathlib import Path

            from ..validation.commit_validator import validate_commit_message

            validation = validate_commit_message(message)

            if not validation.valid:
                suggestions = []
                for error in validation.errors:
                    suggestions.append(f"{error['type']}: {error['fix']}")

                return BashValidationResult(
                    allowed=False,
                    tier=SecurityTier.T3_BLOCKED,
                    reason=f"Commit message validation failed: {validation.errors[0]['message']}",
                    rule="floor.commit-message",
                    suggestions=suggestions[:3]
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
        # Level 1: a HEREDOC whose delimiter is EOF: <<'EOF', <<EOF, <<"EOF"
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


def _pending_requests_of(session_id: str, agent_id: str) -> list:
    """Return the pending requests, oldest first, whose sealed requester is this session and agent.

    A pending is reused only by its own requester: approving it seals a
    grant bound to ``requested_by``, so naming it to anyone else yields a
    grant that requester cannot consume and a second signature for the same
    command. A payload that names no requester is never reused.
    """
    from gaia.approvals.store import get_pending
    import json as _json

    requester = {"session_id": session_id, "agent_id": agent_id}
    own = []
    for row in get_pending(all_sessions=True):
        try:
            payload = _json.loads(row.get("payload_json") or "")
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("requested_by") == requester:
            own.append((row, payload))
    return own


def _find_pending_in_db(session_id: str, command: str, agent_id: str) -> Optional[str]:
    """Return the requester's own pending approval matching this command, else None.

    Looks up the pending requests of this session and agent and matches each
    one's stored command against the incoming command using the SAME semantic
    matcher the consumption path uses (check_db_semantic_grant /
    matches_approval_signature), after a byte-exact fast path.

    Why semantic, not byte-exact:
        The block-approve-retry flow can present the same operation under
        cosmetically different strings -- most commonly a shell redirect that
        the agent appends on the retry (``git push`` blocked, retried as
        ``git push 2>&1``). A byte-exact dedup would MISS the existing pending
        on that retry and mint a fresh approval_id, so the user is asked to
        approve "the same command" twice. Matching on the semantic signature
        makes the retry reuse the existing pending: the signature strips
        output redirects (``command_semantics.strip_redirect_tokens``) while
        still binding every identity-bearing token, so distinct operations are
        NOT collapsed together.

    Args:
        session_id: Session of the host event that was blocked.
        command: The Bash command that was blocked.
        agent_id: Agent of the host event that was blocked.

    Returns:
        The approval_id (P-{hex}) if a matching pending exists, else None.
    """
    try:
        from ..security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )
        # Newest-first so a retry reuses the most recent matching pending,
        # mirroring check_db_semantic_grant()'s ORDER BY created_at DESC.
        for row, payload in reversed(_pending_requests_of(session_id, agent_id)):
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


def _find_pending_plan_set_in_db(command: str, session_id: str, agent_id: str) -> Optional[str]:
    """Return the id of this requester's pending plan-first COMMAND_SET that carries ``command``.

    A blocked command that is an item of a set its requester has not had
    answered yet must be refused under THAT approval's id. Only a payload whose
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
        session_id: Session of the host event that was blocked.
        agent_id: Agent of the host event that was blocked.

    Returns:
        The approval_id (P-{hex}) of the pending set, else None.
    """
    try:
        # Newest-first, mirroring _find_pending_in_db: the most recently
        # requested set is the one the user is being asked about.
        for row, payload in reversed(_pending_requests_of(session_id, agent_id)):
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


def _phrases_request_line(approval_id: str, payload: Optional[dict] = None, *, requester: str = "") -> str:
    """The request line the denial asks for when the named pending has no phrases; else empty.

    No host shows a request without its requester's phrases, so naming
    such a pending as the approval to report would strand the requester.
    *requester* names a main-session requester, whose shell carries no
    dispatch identity for the request to be sealed under.
    """
    from gaia.approvals import core
    from gaia.approvals.store import get_by_id

    if payload is None:
        payload = json.loads((get_by_id(approval_id) or {}).get("payload_json") or "{}")
    return core.request_line(payload, agent_id=requester) if core.missing_phrases(payload) else ""


#: What the sealed payload may state for impact, rollback and verification,
#: selected by the verdict's verb, else its category. The statements are written
#: whole rather than composed from the command's arguments, which would assert a
#: consequence nothing determined. A verdict in neither table contributes none
#: and the presented surface states the absence
#: (``adapters.consent_presentation.VISIBLE_FIELDS``).
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

#: Fallback keyed on the verdict's category when the verb is none of the verbs
#: above. No classifier under hooks/ emits DESTRUCTIVE, so this table never
#: fires in production: a statement read here proves nothing about what a user
#: was shown. Verify what production sealed against the verb table.
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
    *,
    cwd: str | None = None,
    session_id: str = "",
) -> dict:
    """Build a sealed_payload dict from hook-intercepted command context.

    Sealed through gaia.approvals.core.seal_request like every other request:
    each item carries the directory it was attempted in (``cwd``, else this
    process's), no declared non-zero exit, its position and fingerprint, and
    the payload names the requesting session and agent exactly as the host
    event gave them (``core.RequesterError`` when either is missing).

    Called by decide_t3_outcome before it stores the request through
    gaia.approvals.store.insert_requested.

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

    risk_level seals 'medium' in production: no classifier reaching this
    producer emits DESTRUCTIVE, so the 'high' arm goes live only if one starts.

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
        The payload from ``seal_request("command", ...)``, which carries a
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

    # Authored at mint: the approvals row is write-once. Neither command_fingerprint
    # (command bytes) nor request_fingerprint (ordered commands) reads these
    # fields, so they cannot move what a retry must keep byte-identical.
    authored = _authored_statements(verb, category)
    from gaia.approvals.core import seal_request

    directory = cwd or os.getcwd()
    items = normalized_set if is_command_set else [{"command": command, "rationale": ""}]
    # The program's name, not its path: the title is limited to 120 characters.
    base = os.path.basename(command.split()[0]) if command.strip() else "unknown"
    return seal_request(
        "command",
        [{**item, "cwd": directory, "expect_exit": []} for item in items],
        what=f"Run {base} {verb} ({category.lower()})",
        session_id=session_id,
        agent_id=agent_type,
        rollback=authored.get("rollback"),
        verification=authored.get("verification"),
        impact=authored.get("impact"),
        rationale=(
            f"Agent '{agent_type}' attempted a {category.lower()} ({verb}) command "
            "that requires user approval per the T3 security policy."
            if agent_type
            else f"A {category.lower()} ({verb}) command requires user approval per T3 policy."
        ),
        operation=f"{category} command intercepted: {verb}",
        risk_level="high" if category.upper() == "DESTRUCTIVE" else "medium",
    )


def _categorical_refusal(command: str, cwd: str | None) -> BashValidationResult | None:
    """The refusal *command* carries anywhere in it, which outranks every signature request."""
    written, command = command, _scan_heredocs(command)[0]
    parts = [command, *(part.strip() for part in get_shell_parser().parse(command))]
    blocked = next((verdict for verdict in map(is_blocked_command, parts) if verdict.is_blocked), None)
    if blocked is not None:
        reason, rule = f"Command blocked by security policy: {blocked.category}", "floor.blocked"
    else:
        reason, rule = check_sensitive_read(command, cwd, program=written)[1], "floor.secret-read"
    if not reason:
        return None
    return BashValidationResult(allowed=False, tier=SecurityTier.T3_BLOCKED, reason=reason, rule=rule)


@_rules_t3_outcome
def decide_t3_outcome(
    command: str,
    verb: str,
    category: str,
    *,
    has_orchestrator_above: bool,
    session_id: str = "",
    agent_type: str = "",
    command_set: list | None = None,
    guidance: str = "",
    cwd: str | None = None,
) -> BashValidationResult:
    """Single decision point for the outcome of a T3 (state-mutating) command.

    ``cwd`` is the directory the command was attempted in; it is sealed into
    the pending request so the grant matches only there.

    Every T3 classifier -- mutative verbs, pipe composition (file_to_exec), and
    flag-dependent mutations -- converges here, and every caller gets the same
    outcome: a deny naming a persisted ``approval_id``, or a refusal with none.
    The main session is no exception: a host permission prompt would let the
    command run without the consent Gaia records, so its denial carries the
    request line with ``--agent-id`` and the orchestrator presents it through
    ``gaia approvals question``.

    Args:
        command: Full Bash command being classified.
        verb: Detected mutative verb (or a classifier-specific label).
        category: Verb category ("MUTATIVE", "DESTRUCTIVE", ...).
        has_orchestrator_above: True when a subagent runs under the orchestrator;
            False names the main session as the requester.
        session_id: Session ID for pending-approval scoping.
        agent_type: Originating agent name (for the sealed payload).
        command_set: Optional list of ``{command, rationale}`` dicts. When it
            carries MORE THAN ONE item, this T3 decision covers a chain
            (``a && b && c``) whose sub-commands are all T3, and the pending is
            minted as ONE COMMAND_SET envelope instead of a single
            semantic-signature pending. ONE user approval
            then covers the whole chain; each sub-command is still consumed
            byte-for-byte by its own signature at retry. A None / single-item
            set keeps the singular behaviour.
        guidance: The classifier's non-mutating alternative, when it knows one.

    Returns:
        A blocked BashValidationResult (allowed=False, tier T3) whose
        block_response is a "deny", naming an approval_id unless it refuses.
    """
    refusal = _categorical_refusal(command, cwd)
    if refusal is not None:
        return refusal

    # From here a failure anywhere in the gate must not degrade a command known
    # to need consent into a permission; see modules.security.fail_open.
    note_mutative_classification(command, verb, category)

    from gaia.redaction import has_clear_secret

    if has_clear_secret(command):
        from gaia.approvals.core import CLEAR_SECRET_REFUSAL

        reason = f"T3 {category.lower()} command refused: {CLEAR_SECRET_REFUSAL}."
        return BashValidationResult(
            allowed=False,
            tier=SecurityTier.T3_BLOCKED,
            reason=reason,
            block_response=build_hook_permission_response("deny", reason),
            rule="floor.clear-secret",
        )

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

    # No requester in the host event: nothing is sealed and no approval_id is
    # named, because a request bound to a guessed identity could be consumed by
    # whoever shares the guess.
    from gaia.approvals.core import RequesterError, resolve_requester
    try:
        resolve_requester(session_id, agent_type)
    except RequesterError as exc:
        reason = f"T3 {category.lower()} command denied without a signature: {exc}"
        return BashValidationResult(
            allowed=False,
            tier=SecurityTier.T3_BLOCKED,
            reason=reason,
            block_response=build_hook_permission_response("deny", reason),
            rule="floor.no-requester",
        )
    requester = "" if has_orchestrator_above else agent_type

    # A chain's pending id is content-derived, so a retry reuses it through the
    # fingerprint dedup in insert_requested; the singular probe would match a
    # leftover pending of one sub-command and degrade the chain to one grant.
    if not is_chain_command_set:
        # A pending plan-first COMMAND_SET carrying this exact command is named
        # ahead of the singular probe: only the set's id routes the user's reply
        # into the reservation lane, and a singular id would strand the set.
        approval_id = _find_pending_plan_set_in_db(command, session_id, agent_type)
        if approval_id:
            logger.info(
                "Naming pending plan-first COMMAND_SET approval_id=%s for: %s",
                approval_id, command[:80],
            )
        else:
            approval_id = _find_pending_in_db(session_id, command, agent_type)
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
                guidance=guidance,
                request_line=_phrases_request_line(approval_id, requester=requester),
            )
            hook_deny = build_hook_permission_response("deny", reason)
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"T3 {category.lower()} command: {command[:60]}",
                block_response=hook_deny,
                approval_id=approval_id,
            )

    sealed_payload = _build_sealed_payload(
        command=command,
        verb=verb,
        category=category,
        agent_type=agent_type,
        command_set=_normalized_set if is_chain_command_set else None,
        cwd=cwd,
        session_id=session_id,
    )
    try:
        from gaia.approvals.store import insert_requested
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
            "DB insert_requested failed after retries; "
            "denying the already-classified T3 command: %s -- %s",
            command[:80], _store_err,
        )
        # The logger is a no-op unless GAIA_DEBUG is set, so the error goes to
        # the always-on audit sink, under the name `gaia metrics` groups on.
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

        # Re-asserted so this branch cannot soften a deny-listed command even if
        # the upstream floor check moves.
        _blocked = is_blocked_command(command)
        if _blocked.is_blocked:
            return BashValidationResult(
                allowed=False,
                tier=SecurityTier.T3_BLOCKED,
                reason=f"Command blocked by security policy: {_blocked.category}",
                suggestions=[_blocked.suggestion] if _blocked.suggestion else [],
                rule=blocked_rule(_blocked.category),
            )

        # The fingerprint stands in for the command, so no secret is logged.
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
                origin="subagent" if has_orchestrator_above else "orchestrator",
                context={"verb": verb, "category": category},
            )
        except Exception:
            pass

        # Deny, never allow: a persistence outage must not become blanket
        # consent for every mutation behind it.
        reason = build_t3_degraded_block_message()
        hook_deny = build_hook_permission_response("deny", reason)
        return BashValidationResult(
            allowed=False,
            tier=SecurityTier.T3_BLOCKED,
            reason="T3 degraded-block: approval persistence failed",
            block_response=hook_deny,
            rule="floor.approval-store",
        )
    reason = build_t3_blocked_denial_message(
        approval_id=approval_id,
        command=command,
        verb=verb,
        category=category,
        guidance=guidance,
        request_line=_phrases_request_line(approval_id, sealed_payload, requester=requester),
    )
    hook_deny = build_hook_permission_response("deny", reason)
    return BashValidationResult(
        allowed=False,
        tier=SecurityTier.T3_BLOCKED,
        reason=f"T3 {category.lower()} command: {command[:60]}",
        block_response=hook_deny,
        approval_id=approval_id,
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
