"""
Agnostic dangerous verb detector for shell commands.

Classifies commands by scanning tokens for known verb patterns, dangerous flags,
and command aliases. CLI-agnostic: no per-tool extraction logic. Produces a
structured DangerResult for use by hooks, tier classification, or standalone analysis.

Three-Category Security Model:
- DESTRUCTIVE verbs are checked by blocked_commands.py (exit 2, never approvable).
  This module does NOT classify anything as DESTRUCTIVE. Destructive detection is
  pattern-based (command + arguments), not verb-based.
- MUTATIVE: ALL state-modifying verbs including delete, destroy, remove, kill, etc.
  These are approvable via the nonce workflow. The verb detector treats every
  state-modifying verb identically -- the user always decides.
- SIMULATION: plan, diff, preview, template, validate, lint, etc.
- READ_ONLY: get, list, describe, show, logs, status, etc.
"""

import functools
import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

from .approval_messages import build_t3_approval_instructions
from .command_semantics import analyze_command

logger = logging.getLogger(__name__)


# ============================================================================
# Category Constants
# ============================================================================

CATEGORY_DESTRUCTIVE = "DESTRUCTIVE"
CATEGORY_MUTATIVE = "MUTATIVE"
CATEGORY_SIMULATION = "SIMULATION"
CATEGORY_READ_ONLY = "READ_ONLY"
CATEGORY_UNKNOWN = "UNKNOWN"


# ============================================================================
# DangerResult
# ============================================================================

@dataclass(frozen=True)
class DangerResult:
    """Structured result of dangerous verb detection.

    Attributes:
        is_dangerous: Whether the command is classified as dangerous (T3).
        category: Verb category: CATEGORY_DESTRUCTIVE, CATEGORY_MUTATIVE,
            CATEGORY_SIMULATION, CATEGORY_READ_ONLY, or CATEGORY_UNKNOWN.
        verb: The extracted verb (e.g., "delete", "apply", "get").
        verb_position: Token index where the verb was found (-1 if none).
        dangerous_flags: Tuple of flags that escalate the danger level.
        cli_family: Lightweight CLI family hint (e.g., "k8s", "cloud", "git").
        confidence: Confidence level: "high", "medium", or "low".
        reason: Human-readable explanation of the classification.
    """
    is_dangerous: bool = False
    category: str = CATEGORY_UNKNOWN
    verb: str = ""
    verb_position: int = -1
    dangerous_flags: Tuple[str, ...] = ()
    cli_family: str = "unknown"
    confidence: str = "low"
    reason: str = ""


# ============================================================================
# Verb Taxonomy Constants
# ============================================================================

# DESTRUCTIVE_VERBS is intentionally empty. All destructive-at-scale detection
# is handled by blocked_commands.py using pattern matching (command + arguments).
# The verb detector treats ALL state-modifying verbs as MUTATIVE (approvable).
# This prevents the verb detector from permanently blocking commands that are
# only dangerous in specific contexts (e.g., "delete pod" is fine, "delete
# namespace" is not -- but the verb "delete" is the same).
DESTRUCTIVE_VERBS: FrozenSet[str] = frozenset()

MUTATIVE_VERBS: FrozenSet[str] = frozenset({
    # Creation / addition
    "apply", "create", "add", "put", "insert", "register",
    # Modification
    "update", "patch", "set", "modify", "edit", "configure",
    "replace", "overwrite", "write",
    # Deployment / packaging
    "deploy", "install", "upgrade", "downgrade", "publish", "release", "promote",
    # Scaling
    "scale", "resize", "autoscale",
    # Lifecycle
    "start", "restart", "reboot", "reload", "refresh", "resume",
    "uncordon", "unsuspend", "enable", "disable", "suspend", "pause",
    "stop", "shutdown", "halt", "abort",
    # Movement / transfer
    "move", "rename", "copy", "sync",
    "import", "export", "migrate", "transfer",
    # Attachment
    "attach", "bind", "connect", "mount", "link",
    # Execution
    "exec", "run", "execute", "invoke", "trigger", "send",
    # Git operations
    "commit", "push", "merge", "rebase", "cherry-pick", "stash",
    "revert", "rollback",
    # Access control
    "grant", "assign", "revoke",
    # Reconciliation
    "reconcile", "rsync",
    # Deletion / removal (approvable via nonce -- blocked_commands.py catches
    # the truly destructive patterns like "delete namespace", "delete-vpc", etc.)
    "delete", "destroy", "remove", "drop", "purge", "wipe", "clean",
    "truncate", "kill", "terminate", "uninstall", "unpublish",
    "drain", "evict", "cordon", "deregister", "detach",
    "disconnect", "unbind", "reset", "force-delete", "force-remove", "erase",
    # Collaboration (GitHub/GitLab CLI)
    "comment", "label", "annotate", "approve", "close", "reopen", "tag",
    # Helm-specific
    "uninstall",
})

SIMULATION_VERBS: FrozenSet[str] = frozenset({
    "plan", "diff", "preview", "template", "render", "simulate",
    "test", "check", "verify", "lint", "validate", "fmt", "format", "audit",
})

READ_ONLY_VERBS: FrozenSet[str] = frozenset({
    "get", "list", "describe", "show", "read", "view", "inspect",
    "info", "status", "log", "logs", "tail", "head",
    "search", "find", "query", "scan", "fetch", "download",
    "version", "help", "whoami", "which", "explain",
    "top", "stat", "history", "blame", "tree", "shortlog", "reflog",
    "env", "auth", "config", "cluster-info", "api-resources", "ls",
})


# ============================================================================
# Command Aliases (single-token commands that map to a category)
# ============================================================================

# All command aliases are MUTATIVE (approvable via nonce).
# The truly destructive patterns (rm -rf /, dd of=/dev/sda, mkfs, fdisk) are
# permanently blocked by blocked_commands.py before the verb detector runs.
COMMAND_ALIASES: Dict[str, str] = {
    "rm": CATEGORY_MUTATIVE,
    "rmdir": CATEGORY_MUTATIVE,
    "mv": CATEGORY_MUTATIVE,
    "cp": CATEGORY_MUTATIVE,
    "ln": CATEGORY_MUTATIVE,
    "dd": CATEGORY_MUTATIVE,
    "mkfs": CATEGORY_MUTATIVE,
    "fdisk": CATEGORY_MUTATIVE,
    "chmod": CATEGORY_MUTATIVE,
    "chown": CATEGORY_MUTATIVE,
    "chgrp": CATEGORY_MUTATIVE,
}


# ============================================================================
# Always-Safe CLIs (inherently read-only regardless of arguments)
# ============================================================================

ALWAYS_SAFE_CLIS: FrozenSet[str] = frozenset({
    # Text processing / viewers
    "jq", "yq", "bat", "rg", "fd", "fzf", "exa", "eza",
    "tokei", "hyperfine", "delta", "dust", "duf", "procs",
    "btm", "bottom", "tldr", "tree", "htop", "ncdu",
    "less", "more", "wc", "sort", "uniq", "cut", "tr",
    "diff", "comm", "file", "stat", "which", "whereis",
    "whatis", "whoami", "id", "date", "cal", "uname",
    "uptime", "free", "df", "du", "env", "printenv",
    "echo", "printf", "cat", "head", "tail", "pwd", "ls",
    "watch",
    # Kubernetes read-only TUI/viewers
    "k9s", "stern",
    # Linters / type checkers / formatters (local-only, no state modification)
    "mypy", "flake8", "pylint",
})


# ============================================================================
# Simulation Flags (--dry-run and equivalents)
# ============================================================================

SIMULATION_FLAGS: FrozenSet[str] = frozenset({
    "--dry-run",
    "--dryrun",
    "--dry-run=client",
    "--dry-run=server",
})


# ============================================================================
# Dangerous Flags (context-sensitive)
# ============================================================================

DANGEROUS_FLAGS: Dict[str, str] = {
    "--force": "ALWAYS",
    "--no-preserve-root": "ALWAYS",
    "--force-with-lease": "ALWAYS",
    "--prune": "ALWAYS",
    "--cascade": "ALWAYS",
    "--grace-period=0": "ALWAYS",
    "--now": "ALWAYS",
    "-f": "CONTEXT",
    "-r": "CONTEXT",
    "-R": "CONTEXT",
    "-D": "CONTEXT",
    "-M": "CONTEXT",
    "--all": "CONTEXT",
    "--recursive": "CONTEXT",
    "--delete": "CONTEXT",
    "-rf": "ALWAYS",
    "-fr": "ALWAYS",
}

# CLIs where -f means --force (not --file or --format)
F_FLAG_MEANS_FORCE: FrozenSet[str] = frozenset({
    "rm", "cp", "mv", "ln", "docker", "podman",
    "kubectl", "helm", "apt-get", "brew",
})

# CLIs where -r means recursive delete (not --region or --role)
R_FLAG_MEANS_RECURSIVE_DELETE: FrozenSet[str] = frozenset({
    "rm", "cp", "chmod", "chown", "chgrp", "find",
    "gsutil",
})

# CLIs where -D means force-delete (not -D for other meanings)
D_FLAG_MEANS_FORCE_DELETE: FrozenSet[str] = frozenset({
    "git",
})

# CLIs where -M means force-move/rename (not -M for other meanings)
M_FLAG_MEANS_FORCE_MOVE: FrozenSet[str] = frozenset({
    "git",
})

# CLIs where --delete is a destructive flag (not a query filter)
DELETE_FLAG_IS_DESTRUCTIVE: FrozenSet[str] = frozenset({
    "git", "rsync",
})


# ============================================================================
# Lightweight CLI Family Lookup (metadata only, not routing)
# ============================================================================

CLI_FAMILY_LOOKUP: Dict[str, str] = {
    "kubectl": "k8s", "helm": "k8s", "flux": "k8s", "kustomize": "k8s",
    "k9s": "k8s", "kubectx": "k8s", "kubens": "k8s", "stern": "k8s",
    "terraform": "iac", "terragrunt": "iac", "pulumi": "iac", "cdktf": "iac",
    "git": "git",
    "docker": "docker", "podman": "docker",
    "docker-compose": "docker", "podman-compose": "docker",
    "aws": "cloud", "gcloud": "cloud", "gsutil": "cloud", "az": "cloud",
    "eksctl": "cloud", "gh": "cloud", "glab": "cloud",
    "vercel": "cloud", "netlify": "cloud",
    "fly": "cloud", "flyctl": "cloud", "heroku": "cloud",
    "npm": "package", "npx": "package", "pnpm": "package",
    "yarn": "package", "bun": "package", "deno": "package",
    "pip": "package", "pip3": "package", "poetry": "package",
    "pipenv": "package", "uv": "package",
    "apt": "package", "apt-get": "package", "brew": "package",
    "cargo": "package", "go": "package",
    "make": "build", "cmake": "build", "bazel": "build",
    "gradle": "build", "mvn": "build",
    "node": "runtime", "python": "runtime", "python3": "runtime",
    "tsx": "runtime", "ts-node": "runtime",
    "pytest": "linter", "mypy": "linter", "black": "linter",
    "ruff": "linter", "flake8": "linter", "pylint": "linter",
    "systemctl": "system", "service": "system", "supervisorctl": "system",
}


# ============================================================================
# Dangerous Flag Scanning
# ============================================================================

def _scan_dangerous_flags(
    tokens: List[str] | tuple,
    cli: str,
    verb_category: str = CATEGORY_UNKNOWN,
) -> Tuple[str, ...]:
    """Scan tokens for dangerous flags with context sensitivity.

    Context rules:
    - "-f" is only dangerous if cli is in F_FLAG_MEANS_FORCE
    - "-r"/"-R" is only dangerous if cli is in R_FLAG_MEANS_RECURSIVE_DELETE
    - "-D" is only dangerous if cli is in D_FLAG_MEANS_FORCE_DELETE
    - "-M" is only dangerous if cli is in M_FLAG_MEANS_FORCE_MOVE
    - "--delete" is only dangerous if cli is in DELETE_FLAG_IS_DESTRUCTIVE
    - "--all" only escalates when combined with a DESTRUCTIVE verb
    - Compound flags like "-rf" are always dangerous

    Args:
        tokens: Tokenized command.
        cli: CLI tool name.
        verb_category: Classified verb category.

    Returns:
        Tuple of dangerous flag strings found.
    """
    found: List[str] = []

    for token in tokens:
        if not token.startswith("-"):
            continue

        # Check exact matches in DANGEROUS_FLAGS
        if token in DANGEROUS_FLAGS:
            flag_type = DANGEROUS_FLAGS[token]

            if flag_type == "ALWAYS":
                found.append(token)
                continue

            # CONTEXT-sensitive flags
            if token == "-f":
                if cli in F_FLAG_MEANS_FORCE:
                    found.append(token)
            elif token in ("-r", "-R"):
                if cli in R_FLAG_MEANS_RECURSIVE_DELETE:
                    found.append(token)
            elif token == "-D":
                if cli in D_FLAG_MEANS_FORCE_DELETE:
                    found.append(token)
            elif token == "-M":
                if cli in M_FLAG_MEANS_FORCE_MOVE:
                    found.append(token)
            elif token == "--delete":
                if cli in DELETE_FLAG_IS_DESTRUCTIVE:
                    found.append(token)
            elif token == "--all":
                if verb_category == CATEGORY_DESTRUCTIVE:
                    found.append(token)
            elif token == "--recursive":
                if cli in R_FLAG_MEANS_RECURSIVE_DELETE:
                    found.append(token)

        # Check for compound short flags containing dangerous combos
        # e.g., "-rfi" contains both -r and -f
        elif len(token) > 2 and token[0] == "-" and token[1] != "-":
            flag_chars = token[1:]
            if "r" in flag_chars and "f" in flag_chars:
                found.append(token)
            elif "f" in flag_chars and cli in F_FLAG_MEANS_FORCE:
                found.append(token)
            elif "r" in flag_chars and cli in R_FLAG_MEANS_RECURSIVE_DELETE:
                found.append(token)

    return tuple(found)


# ============================================================================
# Main Detection Function
# ============================================================================

@functools.lru_cache(maxsize=128)
def detect_dangerous_command(command: str) -> DangerResult:
    """Analyze a shell command and return a structured danger assessment.

    Algorithm (CLI-agnostic):
    1. Tokenize the command.
    2. ALWAYS_SAFE_CLIS fast-path.
    3. COMMAND_ALIASES fast-path.
    4. Simulation flag override: --dry-run anywhere = safe.
    5. Scan the first semantic non-flag tokens after the base CLI.
    6. Scan for dangerous flags.
    7. No match: not dangerous, unknown.

    Args:
        command: Raw shell command string.

    Returns:
        DangerResult with full classification details.
    """
    # --- Edge case: empty command ---
    if not command or not command.strip():
        return DangerResult(
            is_dangerous=False,
            category=CATEGORY_UNKNOWN,
            reason="Empty command",
            confidence="high",
        )

    semantics = analyze_command(command)
    tokens = list(semantics.tokens)
    if not tokens:
        return DangerResult(
            is_dangerous=False,
            category=CATEGORY_UNKNOWN,
            reason="No tokens after parsing",
            confidence="high",
        )

    base_cmd = semantics.base_cmd
    family = CLI_FAMILY_LOOKUP.get(base_cmd, "unknown")

    # --- Step 1: Always-safe CLI fast-path ---
    if base_cmd in ALWAYS_SAFE_CLIS:
        safe_family = CLI_FAMILY_LOOKUP.get(base_cmd, "text")
        return DangerResult(
            is_dangerous=False,
            category=CATEGORY_READ_ONLY,
            verb=base_cmd,
            verb_position=0,
            cli_family=safe_family,
            confidence="high",
            reason=f"Always-safe CLI '{base_cmd}'",
        )

    # --- Step 2: Command alias fast-path ---
    if base_cmd in COMMAND_ALIASES:
        alias_category = COMMAND_ALIASES[base_cmd]
        dangerous_flags = _scan_dangerous_flags(tokens, base_cmd, alias_category)
        return DangerResult(
            is_dangerous=True,
            category=alias_category,
            verb=base_cmd,
            verb_position=0,
            dangerous_flags=dangerous_flags,
            cli_family=family if family != "unknown" else "system",
            confidence="high",
            reason=f"Command alias '{base_cmd}' is {alias_category.lower()}",
        )

    # --- Step 3: Single-token command (no verb to extract) ---
    if len(tokens) == 1:
        return DangerResult(
            is_dangerous=False,
            category=CATEGORY_UNKNOWN,
            verb=base_cmd,
            verb_position=0,
            cli_family=family,
            confidence="low",
            reason=f"Single-token command '{base_cmd}' with no verb",
        )

    # --- Step 4: Simulation flag override ---
    if any(t.lower() in SIMULATION_FLAGS for t in tokens):
        # Find the first non-flag token after base_cmd for the verb
        verb, verb_pos = _find_first_non_flag(semantics.semantic_head_tokens)
        return DangerResult(
            is_dangerous=False,
            category=CATEGORY_SIMULATION,
            verb=verb,
            verb_position=verb_pos,
            cli_family=family,
            confidence="high",
            reason=f"Simulation flag detected (command has --dry-run or equivalent)",
        )

    # --- Step 5: Scan semantic non-flag tokens near the command head ---
    # Priority order: SIMULATION > MUTATIVE > READ_ONLY > ALIASES
    # DESTRUCTIVE_VERBS is intentionally empty -- all destructive detection
    # is handled by blocked_commands.py before this function runs.
    for semantic_index, token in enumerate(semantics.semantic_head_tokens[1:], start=1):
        # Split hyphenated tokens: "delete-stack" -> check "delete"
        candidate = token.split("-", 1)[0] if "-" in token else token

        # Also check full token for exact matches (e.g., "force-delete")
        full_lower = token

        # Determine confidence from position
        confidence = "high" if semantic_index <= 2 else "medium"

        # Check verb taxonomy in priority order
        if candidate in SIMULATION_VERBS or full_lower in SIMULATION_VERBS:
            verb = candidate if candidate in SIMULATION_VERBS else full_lower
            return DangerResult(
                is_dangerous=False,
                category=CATEGORY_SIMULATION,
                verb=verb,
                verb_position=semantic_index,
                cli_family=family,
                confidence=confidence,
                reason=f"Simulation verb '{verb}'",
            )

        if candidate in MUTATIVE_VERBS or full_lower in MUTATIVE_VERBS:
            verb = candidate if candidate in MUTATIVE_VERBS else full_lower
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd, CATEGORY_MUTATIVE)
            flag_detail = (
                f" with dangerous flags {dangerous_flags}"
                if dangerous_flags else ""
            )
            return DangerResult(
                is_dangerous=True,
                category=CATEGORY_MUTATIVE,
                verb=verb,
                verb_position=semantic_index,
                dangerous_flags=dangerous_flags,
                cli_family=family,
                confidence=confidence,
                reason=f"Mutative verb '{verb}'{flag_detail}",
            )

        if candidate in READ_ONLY_VERBS or full_lower in READ_ONLY_VERBS:
            verb = candidate if candidate in READ_ONLY_VERBS else full_lower
            return DangerResult(
                is_dangerous=False,
                category=CATEGORY_READ_ONLY,
                verb=verb,
                verb_position=semantic_index,
                cli_family=family,
                confidence=confidence,
                reason=f"Read-only verb '{verb}'",
            )

        # Check command aliases as verb (e.g., "docker rm" -> rm is alias)
        if candidate in COMMAND_ALIASES:
            alias_cat = COMMAND_ALIASES[candidate]
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd, alias_cat)
            return DangerResult(
                is_dangerous=True,
                category=alias_cat,
                verb=candidate,
                verb_position=semantic_index,
                dangerous_flags=dangerous_flags,
                cli_family=family,
                confidence=confidence,
                reason=f"Verb alias '{candidate}' is {alias_cat.lower()}",
            )

    # --- Step 6: Scan for dangerous flags (no verb found) ---
    dangerous_flags = _scan_dangerous_flags(tokens, base_cmd, CATEGORY_UNKNOWN)
    if dangerous_flags:
        # Find first non-flag token as the "verb" for reporting
        verb, verb_pos = _find_first_non_flag(semantics.semantic_head_tokens)
        return DangerResult(
            is_dangerous=True,
            category=CATEGORY_UNKNOWN,
            verb=verb,
            verb_position=verb_pos,
            dangerous_flags=dangerous_flags,
            cli_family=family,
            confidence="low",
            reason=f"Unknown verb '{verb}' with dangerous flags {dangerous_flags}",
        )

    # --- Step 7: No match ---
    verb, verb_pos = _find_first_non_flag(semantics.semantic_head_tokens)
    return DangerResult(
        is_dangerous=False,
        category=CATEGORY_UNKNOWN,
        verb=verb,
        verb_position=verb_pos,
        cli_family=family,
        confidence="low",
        reason=f"Unknown verb '{verb}' with no dangerous flags",
    )


# ============================================================================
# Helpers
# ============================================================================

def _find_first_non_flag(tokens: List[str] | tuple) -> tuple:
    """Find the first semantic token after tokens[0].

    Returns:
        (verb, position) tuple. ("", -1) if no non-flag token found.
    """
    for i in range(1, len(tokens)):
        if tokens[i]:
            return tokens[i], i
    return "", -1


# ============================================================================
# Hook Response Builder
# ============================================================================

def build_t3_block_response(
    command: str,
    danger: DangerResult,
    nonce: str = "",
) -> dict:
    """Build an internal block response dict for T3 commands.

    Returns an internal dict consumed by bash_validator, which wraps the
    'message' field into a hookSpecificOutput with permissionDecision: "deny".
    The 'decision' key is internal only and never sent to Claude Code.

    Args:
        command: The original shell command.
        danger: DangerResult from detect_dangerous_command.
        nonce: Cryptographic nonce for this pending approval. When provided,
            the block message includes the approval code that the agent must
            present to the user.

    Returns:
        Dict with 'decision' (internal) and 'message' (forwarded to agent) keys.
    """
    flag_warning = ""
    if danger.dangerous_flags:
        flag_warning = (
            f"\nDangerous flags detected: {', '.join(danger.dangerous_flags)}"
        )

    message = (
        f"BLOCKED: {danger.category} operation detected.\n"
        f"Command: {command}\n"
        f"Verb: '{danger.verb}' (CLI family: {danger.cli_family})\n"
        f"Confidence: {danger.confidence}\n"
        f"Reason: {danger.reason}{flag_warning}\n"
        f"\n"
        f"{build_t3_approval_instructions(nonce)}"
    )

    return {
        "decision": "block",
        "message": message,
    }
