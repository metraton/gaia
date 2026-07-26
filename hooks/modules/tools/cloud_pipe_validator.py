"""
Command pipe/redirect/chaining validator.

Two-tier validation:
1. Cloud/infra CLIs (see ``NATIVE_OUTPUT_FLAG_CLIS`` in
   ``security.mutative_verbs`` for the current, single-sourced list) —
   redirects and chaining operators are rejected outright (deny, not
   approvable) whenever any decomposed stage invokes one of these CLIs,
   because these CLIs expose native flags for filtering and formatting. The
   PIPE rule is narrower: it denies only when the cloud CLI is the ORIGIN of
   the pipe (its own output is being piped away); when the cloud CLI is only
   the pipe's DESTINATION (receiving piped-in content it did not produce),
   this validator defers to the normal per-stage T3/mutative-verb
   classification instead of denying outright -- see
   ``_command_has_cloud_pipe_origin`` for why origin and destination are not
   the same risk.
2. All other commands — redirects (>, >>) and background operator (&) are
   rejected because Claude Code tools (Write, Edit) are the correct way to
   produce file output, and background execution hides exit codes.

This validator runs before tier classification so violations are caught early
and the agent receives a corrective response rather than a blocked execution.
Deferring the pipe-destination case is what lets that later classification
run at all for that shape.

Cloud-CLI governance is decided PER STAGE, not by anchoring on the first
token of the whole command string. ``_command_has_cloud_cli_stage`` runs the
command through ``StageDecomposer`` (the same quote/operator-aware split the
security layer relies on elsewhere, see ``workflow_auditor._is_infra_cli_pipe``)
and checks each stage's own executable against ``CLOUD_CLI_PATTERN``. Two
failure modes motivate this: a `cd repo && terragrunt apply ... | tail` or
`; terraform apply ...` evades a whole-string anchor because the cloud CLI is
not the first token of the full string, even though it IS a stage's real
invocation; conversely, matching against a stage's executable (rather than
substring-searching the raw text) means a cloud-CLI name appearing only as an
argument (`grep terragrunt file.tf | head`, `echo "terraform" | wc -l`) is
never mistaken for an invocation, since neither `grep` nor `echo` is the
cloud CLI itself.

``CLOUD_CLI_PATTERN`` itself is DERIVED from ``NATIVE_OUTPUT_FLAG_CLIS``
(``security.mutative_verbs``), not maintained as an independent list here.
That registry is the single place a new CLI gets added, and its own
docstring carries the "why this is a different axis than mutability"
rationale plus the include/exclude completeness test
(``tests/hooks/modules/security/test_mutative_verbs.py``,
``test_pipe_policy_registry_completeness``) that fails when a relevant
CLI is left unclassified rather than deliberately excluded.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .hook_response import build_hook_permission_response
from .stage_decomposer import StageDecomposer
from ..security.mutative_verbs import NATIVE_OUTPUT_FLAG_CLIS

logger = logging.getLogger(__name__)

# Cloud/infra CLIs covered by this policy, derived from the consolidated
# registry so there is exactly one list to update, not two. Sorted longest
# name first purely for deterministic, readable regex source (`\b` after the
# alternation already prevents any prefix-overlap ambiguity regardless of
# order).
CLOUD_CLI_PATTERN = re.compile(
    r'^\s*(' + '|'.join(sorted(NATIVE_OUTPUT_FLAG_CLIS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE
)

# Violation definitions: (name, regex, corrected_approach)
VIOLATIONS = [
    (
        "pipe",
        # Match a single pipe `|` but NOT logical OR `||`.
        # Negative lookbehind (?<!\|) skips the second `|` of `||`.
        # Negative lookahead (?!\|) skips the first `|` of `||`.
        re.compile(r'(?<!\|)\|(?!\|)'),
        (
            "Use native output flags instead of piping to shell utilities.\n"
            "  gcloud: --filter='...' --format='value(field)'\n"
            "  kubectl: -o jsonpath='{...}' or -o go-template='{{...}}'\n"
            "  aws: --query '...' --output text\n"
            "  terraform: use terraform output or -json flag"
        ),
    ),
    (
        "redirect",
        # Match `>` or `>>` for file redirection, but NOT:
        # - `>&` (file descriptor duplication, e.g. `2>&1`)
        # - `--` prefixed flags or other non-redirect uses
        # Negative lookbehind (?<![>&]) avoids matching the `>` in `>&1`.
        # Negative lookahead (?![>&]) avoids matching `>` when followed by `&`.
        re.compile(r'(?<![>&])>{1,2}(?![>&])'),
        (
            "Use the Write tool to write output to a file instead of shell redirection.\n"
            "  Write tool: creates or overwrites files cleanly without shell quoting issues.\n"
            "  For append patterns, use the Edit tool or Read + Write."
        ),
    ),
    (
        "chaining",
        # Match `;` or `&&` but NOT `||` (which is now correctly handled
        # by the pipe regex above via negative lookahead).
        re.compile(r';|&&'),
        (
            "Run each command as a separate, atomic Bash call instead of chaining.\n"
            "  One command per step preserves exit-code isolation and avoids\n"
            "  interactive prompts mid-chain that block Claude Code execution."
        ),
    ),
]


@dataclass
class PipeViolation:
    """A detected pipe/redirect/chaining violation."""
    rule: str            # e.g. "pipe", "redirect", "chaining"
    pattern: str         # the literal character(s) that triggered it
    correction: str      # human-readable corrected approach


# ---------------------------------------------------------------------------
# Universal violations — apply to ALL commands regardless of CLI
# ---------------------------------------------------------------------------
UNIVERSAL_VIOLATIONS = [
    (
        "redirect",
        # Match `>` or `>>` for file redirection, but NOT:
        # - `>&` (file descriptor duplication, e.g. `2>&1`)
        # - process substitution `<(` or `>(` (used in bash -c / diff)
        re.compile(r'(?<![>&])>{1,2}(?![>&(])'),
        (
            "Use the Write tool to write output to a file instead of shell redirection.\n"
            "  Write tool: creates or overwrites files cleanly without shell quoting issues.\n"
            "  For append patterns, use the Edit tool or Read + Write."
        ),
    ),
    (
        "background",
        # Match trailing `&` (background execution), but NOT `&&` or `>&`.
        # Negative lookbehind for `>` avoids `>&` (fd dup).
        # Negative lookbehind for `&` avoids the second `&` in `&&`.
        # Negative lookahead for `&` avoids the first `&` in `&&`.
        re.compile(r'(?<![>&])&(?!&)'),
        (
            "Do not run commands in the background with &. Background execution\n"
            "  hides exit codes and prevents Claude Code from verifying the result.\n"
            "  Run the command normally and let it complete."
        ),
    ),
]


def _command_has_cloud_cli_stage(command: str) -> bool:
    """
    True when *command* invokes a cloud/infra CLI in any decomposed stage.

    Decomposes *command* via ``StageDecomposer`` and checks each stage's own
    executable (the command actually run in that stage, not its arguments)
    against ``CLOUD_CLI_PATTERN``. This is what lets `cd repo && terraform
    apply | tail` be recognized (terraform is a real stage invocation, even
    though it is not the first token of the whole string) while `grep
    terraform file.tf | head` is not (grep is the invoked command; terraform
    is only an argument).
    """
    decomposed = StageDecomposer().decompose(command)
    return any(CLOUD_CLI_PATTERN.match(stage.executable) for stage in decomposed.stages)


def _command_has_cloud_pipe_origin(command: str) -> bool:
    """
    True when a cloud/infra CLI stage pipes its OWN output away (the
    ORIGIN of a pipe), as opposed to only receiving piped-in content (the
    DESTINATION of a pipe).

    Origin and destination are not the same risk. As origin
    (`kubectl get pods | grep`), the CLI already exposes native
    `--format`/`--filter`/`-o jsonpath` flags, so shelling its own output
    out is unnecessary and this validator's "pipe" rule denies it
    outright -- there is always a native substitute. As destination
    (`kustomize build overlay | kubectl apply -f -`), the CLI is not
    producing output to filter; it is CONSUMING content this validator
    cannot inspect. That is not a false positive to wave through -- it is
    routed to the normal per-component T3 classification instead (see
    ``_find_violation``), which independently catches a real mutation
    (`kubectl apply` is a MUTATIVE_VERBS verb) and asks for consent the
    ordinary way, rather than a categorical, non-approvable deny whose
    "use native output flags" correction does not even apply to a CLI
    that is not producing the piped output in the first place.
    """
    decomposed = StageDecomposer().decompose(command)
    return any(
        stage.operator == "|" and CLOUD_CLI_PATTERN.match(stage.executable)
        for stage in decomposed.stages
    )


def _find_violation(command: str) -> Optional[PipeViolation]:
    """
    Return the first pipe/redirect/chaining violation found in command,
    or None if the command is clean.

    Cloud/infra CLIs are checked against ALL violation rules (pipes, redirects,
    chaining).  Non-cloud commands are checked against universal rules only
    (redirects and background operator). A command counts as cloud/infra when
    ANY of its decomposed stages invokes a recognized CLI -- see
    ``_command_has_cloud_cli_stage`` -- not only when the CLI is the first
    token of the whole string.

    The "pipe" rule specifically is further narrowed to the ORIGIN direction
    -- see ``_command_has_cloud_pipe_origin``. When a cloud CLI stage exists
    only as a pipe DESTINATION (receiving piped-in content, never piping its
    own output further), this validator does not flag it: the command is
    left to fall through to the normal per-stage T3/mutative-verb
    classification, which still requires consent for a genuine mutation
    (`kubectl apply`) -- just via the ordinary approvable "ask" path instead
    of this validator's categorical, non-approvable "deny". Redirect and
    chaining are NOT given this direction split: a redirect always writes
    the CLI's OWN output (there is no "destination" reading for `>`), and
    chaining (`;`/`&&`) composes independent commands with no content flow
    between them, so both remain governed exactly as before whenever any
    stage is a recognized cloud CLI.

    Skips characters inside single or double quoted strings to avoid
    false positives (e.g. --filter='status:RUNNING' contains no violation).
    """
    # Strip quoted substrings before scanning for operators.
    # This prevents false positives from flag values like --filter='a|b'.
    unquoted = _strip_quoted_sections(command)

    is_cloud = _command_has_cloud_cli_stage(command)
    has_pipe_origin = _command_has_cloud_pipe_origin(command)

    if is_cloud:
        # Cloud CLIs: check ALL rules (pipe, redirect, chaining), except the
        # "pipe" rule is skipped when the only cloud-CLI involvement is as a
        # pipe destination -- see the docstring above and
        # _command_has_cloud_pipe_origin.
        for rule_name, pattern, correction in VIOLATIONS:
            if rule_name == "pipe" and not has_pipe_origin:
                continue
            match = pattern.search(unquoted)
            if match:
                return PipeViolation(
                    rule=rule_name,
                    pattern=match.group(0),
                    correction=correction,
                )

    # All commands (cloud and non-cloud): check universal rules
    for rule_name, pattern, correction in UNIVERSAL_VIOLATIONS:
        match = pattern.search(unquoted)
        if match:
            return PipeViolation(
                rule=rule_name,
                pattern=match.group(0),
                correction=correction,
            )

    return None


def _strip_quoted_sections(text: str) -> str:
    """
    Replace content inside single and double quotes with spaces.
    Handles simple quoting (no nested quotes, no escape sequences needed
    for the operators we scan for).
    """
    result = []
    in_single = False
    in_double = False

    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
        elif in_single or in_double:
            result.append(' ')  # mask the character
        else:
            result.append(ch)

    return ''.join(result)


def build_block_response(violation: PipeViolation, command: str) -> dict:
    """
    Build the structured JSON block that tells Claude Code to block the command
    and return a corrective reason to the agent.

    Uses permissionDecision: "deny" with exit 0 (NOT exit 2) so the agent
    receives the correction message and adjusts rather than stopping entirely.

    Args:
        violation: The detected violation.
        command:   The original command string (truncated in reason for readability).

    Returns:
        Dict suitable for json.dumps() and print() in the hook entry point.
    """
    truncated = command[:120] + ('...' if len(command) > 120 else '')

    reason = (
        f"Command-execution rule violated: no {violation.rule}s in cloud/infra commands.\n\n"
        f"Violating pattern: '{violation.pattern}' detected in:\n"
        f"  {truncated}\n\n"
        f"Corrected approach:\n"
        f"{violation.correction}"
    )

    return build_hook_permission_response("deny", reason)


def validate_cloud_pipe(command: str) -> Optional[dict]:
    """
    Check a command for cloud pipe/redirect/chaining violations.

    Returns a block-response dict if a violation is found, None otherwise.
    The caller should json.dumps() the result and exit(0).

    Args:
        command: The raw bash command string.

    Returns:
        Block response dict, or None if command is clean.
    """
    violation = _find_violation(command)
    if violation is None:
        return None

    logger.warning(
        f"Cloud pipe violation [{violation.rule}] pattern='{violation.pattern}' "
        f"in: {command[:80]}"
    )
    return build_block_response(violation, command)
