"""
Mutative verb detector for shell commands.

Simplified three-category pipeline:
  blocked_commands.py  ->  BLOCKED (exit 2, permanently denied)
  mutative_verbs.py    ->  MUTATIVE (needs user approval via nonce)
  everything else      ->  SAFE (auto-approved by elimination)

This module detects MUTATIVE commands by scanning tokens for known verb patterns,
dangerous flags, and command aliases. If a command is not blocked and not mutative,
it is safe by elimination -- no allowlist needed.

Categories retained internally for verb classification:
- MUTATIVE: ALL state-modifying verbs (approvable via nonce workflow)
- SIMULATION: plan, diff, preview, template, validate, lint, etc.
- READ_ONLY: get, list, describe, show, logs, status, etc.
"""

import functools
import logging
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Tuple

from .approval_messages import build_t3_approval_instructions
from .command_semantics import analyze_command

logger = logging.getLogger(__name__)


# ============================================================================
# Category Constants
# ============================================================================

CATEGORY_MUTATIVE = "MUTATIVE"
CATEGORY_SIMULATION = "SIMULATION"
CATEGORY_READ_ONLY = "READ_ONLY"
CATEGORY_UNKNOWN = "UNKNOWN"


# ============================================================================
# MutativeResult
# ============================================================================

@dataclass(frozen=True)
class MutativeResult:
    """Structured result of mutative verb detection.

    Attributes:
        is_mutative: Whether the command is classified as mutative (T3).
        category: Verb category: CATEGORY_MUTATIVE, CATEGORY_SIMULATION,
            CATEGORY_READ_ONLY, or CATEGORY_UNKNOWN.
        verb: The extracted verb (e.g., "delete", "apply", "get").
        dangerous_flags: Tuple of flags that escalate the danger level.
        cli_family: Lightweight CLI family hint (e.g., "k8s", "cloud", "git").
        confidence: Confidence level: "high", "medium", or "low".
        reason: Human-readable explanation of the classification.
    """
    is_mutative: bool = False
    category: str = CATEGORY_UNKNOWN
    verb: str = ""
    dangerous_flags: Tuple[str, ...] = ()
    cli_family: str = "unknown"
    confidence: str = "low"
    reason: str = ""



# ============================================================================
# Verb Taxonomy Constants
# ============================================================================

MUTATIVE_VERBS: FrozenSet[str] = frozenset({
    # Creation / addition
    # NOTE: "add" removed -- safe by elimination (e.g., git add is local-only)
    "apply", "create", "put", "insert", "register",
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
    # NOTE: "link" removed -- false positive in shell variable names (e.g., "for link in ...").
    #       The `ln` command is already covered as a COMMAND_ALIAS.
    "attach", "bind", "connect", "mount",
    # Execution
    # NOTE: "run" removed -- safe by elimination (e.g., docker run is common dev workflow)
    "exec", "execute", "invoke", "trigger", "send",
    # Git operations
    # NOTE: "stash" removed -- safe by elimination (local-only operation)
    "commit", "push", "merge", "rebase", "cherry-pick",
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
    # HTTP methods (e.g., glab api -X POST, gh api -X DELETE)
    "post", "put", "patch",
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
    # Compound subcommands that look mutative after hyphen-split but are read-only
    "merge-base",
})


# ============================================================================
# Compound Read-Only Subcommands
# ============================================================================
# Full subcommand tokens that must be matched BEFORE the hyphen-split logic.
# Without this, "merge-base" would be split to "merge" and flagged as MUTATIVE.

COMPOUND_READ_ONLY_SUBCOMMANDS: FrozenSet[str] = frozenset({
    "merge-base",
})


# ============================================================================
# Verb+Flag Overrides (mutative verb downgraded to READ_ONLY by a flag)
# ============================================================================
# Map of (cli_family, verb) -> frozenset of flag tokens that override to READ_ONLY.
# Checked AFTER a mutative verb is found but BEFORE returning the MUTATIVE result.

VERB_FLAG_READ_ONLY_OVERRIDES: Dict[Tuple[str, str], FrozenSet[str]] = {
    # "git tag -l" / "git tag --list" is listing, not creating/deleting
    ("git", "tag"): frozenset({"-l", "--list"}),
}


# ============================================================================
# Inline Code Dangerous Patterns (python3 -c, python -c)
# ============================================================================
# When the base command is a runtime interpreter with inline code (-c flag),
# scan the code string for dangerous patterns instead of verb-matching tokens.
import re as _re

_DANGEROUS_INLINE_PATTERNS: Tuple[Tuple[_re.Pattern, str], ...] = (
    (_re.compile(r"os\.remove\b"), "os.remove"),
    (_re.compile(r"os\.unlink\b"), "os.unlink"),
    (_re.compile(r"os\.rmdir\b"), "os.rmdir"),
    (_re.compile(r"os\.rename\b"), "os.rename"),
    (_re.compile(r"os\.makedirs?\b"), "os.makedirs"),
    (_re.compile(r"os\.system\b"), "os.system"),
    (_re.compile(r"shutil\.rmtree\b"), "shutil.rmtree"),
    (_re.compile(r"shutil\.move\b"), "shutil.move"),
    (_re.compile(r"shutil\.copy\b"), "shutil.copy"),
    # NOTE: subprocess intentionally omitted -- commonly used for read-only
    # inspection (e.g., subprocess.run(["echo","hello"])). The actual command
    # inside the subprocess will be evaluated by the hook when it runs.
    (_re.compile(r"open\s*\([^)]*['\"][wWaA]['\"]"), "file write via open()"),
    (_re.compile(r"\.write\s*\("), "file write"),
    (_re.compile(r"pathlib\.Path\([^)]*\)\.(unlink|rmdir|rename|write_)"), "pathlib mutation"),
)

# CLIs that accept -c for inline code execution
_INLINE_CODE_CLIS: FrozenSet[str] = frozenset({
    "python", "python3", "python3.10", "python3.11", "python3.12", "python3.13",
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
) -> Tuple[str, ...]:
    """Scan tokens for dangerous flags with context sensitivity.

    Context rules:
    - "-f" is only dangerous if cli is in F_FLAG_MEANS_FORCE
    - "-r"/"-R" is only dangerous if cli is in R_FLAG_MEANS_RECURSIVE_DELETE
    - "-D" is only dangerous if cli is in D_FLAG_MEANS_FORCE_DELETE
    - "-M" is only dangerous if cli is in M_FLAG_MEANS_FORCE_MOVE
    - "--delete" is only dangerous if cli is in DELETE_FLAG_IS_DESTRUCTIVE
    - Compound flags like "-rf" are always dangerous

    Args:
        tokens: Tokenized command.
        cli: CLI tool name.

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
def detect_mutative_command(command: str) -> MutativeResult:
    """Analyze a shell command and return a structured mutative assessment.

    Simplified algorithm (CLI-agnostic):
    1. Tokenize the command.
    2. COMMAND_ALIASES fast-path.
    3. Simulation flag override: --dry-run anywhere = not mutative.
    4. Scan the first semantic non-flag tokens after the base CLI.
    5. Scan for dangerous flags.
    6. No match: not mutative (safe by elimination).

    Args:
        command: Raw shell command string.

    Returns:
        MutativeResult with full classification details.
    """
    # --- Edge case: empty command ---
    if not command or not command.strip():
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_UNKNOWN,
            reason="Empty command",
            confidence="high",
        )

    semantics = analyze_command(command)
    tokens = list(semantics.tokens)
    if not tokens:
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_UNKNOWN,
            reason="No tokens after parsing",
            confidence="high",
        )

    base_cmd = semantics.base_cmd
    family = CLI_FAMILY_LOOKUP.get(base_cmd, "unknown")

    # --- Step 1: Command alias fast-path ---
    if base_cmd in COMMAND_ALIASES:
        alias_category = COMMAND_ALIASES[base_cmd]
        dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
        return MutativeResult(
            is_mutative=True,
            category=alias_category,
            verb=base_cmd,
            dangerous_flags=dangerous_flags,
            cli_family=family if family != "unknown" else "system",
            confidence="high",
            reason=f"Command alias '{base_cmd}' is {alias_category.lower()}",
        )

    # --- Step 2: Single-token command (no verb to extract) ---
    if len(tokens) == 1:
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_UNKNOWN,
            verb=base_cmd,
            cli_family=family,
            confidence="low",
            reason=f"Single-token command '{base_cmd}' with no verb",
        )

    # --- Step 3: Simulation flag override ---
    if any(t.lower() in SIMULATION_FLAGS for t in tokens):
        # Find the first non-flag token after base_cmd for the verb
        verb, _ = _find_first_non_flag(semantics.semantic_head_tokens)
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_SIMULATION,
            verb=verb,
            cli_family=family,
            confidence="high",
            reason=f"Simulation flag detected (command has --dry-run or equivalent)",
        )

    # --- Step 3b: Inline code safety check (python3 -c "...") ---
    # For runtime interpreters with -c, scan the code string for dangerous
    # patterns instead of verb-matching tokens (which would false-positive on
    # generic keywords like "import", "create", etc.).
    if base_cmd in _INLINE_CODE_CLIS and "-c" in semantics.flag_tokens:
        return _check_inline_code(command, base_cmd, family)

    # --- Step 4: Scan semantic non-flag tokens near the command head ---
    # Priority order: SIMULATION > MUTATIVE > READ_ONLY > ALIASES
    for semantic_index, token in enumerate(semantics.semantic_head_tokens[1:], start=1):
        # Check compound read-only subcommands BEFORE hyphen-split.
        # Without this, "merge-base" would be split to "merge" -> MUTATIVE.
        if token in COMPOUND_READ_ONLY_SUBCOMMANDS:
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb=token,
                cli_family=family,
                confidence="high",
                reason=f"Compound read-only subcommand '{token}'",
            )

        # Split hyphenated tokens: "delete-stack" -> check "delete"
        candidate = token.split("-", 1)[0] if "-" in token else token

        # Also check full token for exact matches (e.g., "force-delete")
        full_lower = token

        # Determine confidence from position
        confidence = "high" if semantic_index <= 2 else "medium"

        # Check verb taxonomy in priority order
        if candidate in SIMULATION_VERBS or full_lower in SIMULATION_VERBS:
            verb = candidate if candidate in SIMULATION_VERBS else full_lower
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_SIMULATION,
                verb=verb,
                cli_family=family,
                confidence=confidence,
                reason=f"Simulation verb '{verb}'",
            )

        if candidate in MUTATIVE_VERBS or full_lower in MUTATIVE_VERBS:
            verb = candidate if candidate in MUTATIVE_VERBS else full_lower

            # Check verb+flag overrides: some verbs become READ_ONLY with
            # specific flags (e.g., "git tag -l" is listing, not creating).
            override_key = (family, verb)
            if override_key in VERB_FLAG_READ_ONLY_OVERRIDES:
                override_flags = VERB_FLAG_READ_ONLY_OVERRIDES[override_key]
                if override_flags & frozenset(semantics.flag_tokens):
                    return MutativeResult(
                        is_mutative=False,
                        category=CATEGORY_READ_ONLY,
                        verb=verb,
                        cli_family=family,
                        confidence="high",
                        reason=f"Verb '{verb}' overridden to read-only by flag",
                    )

            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            flag_detail = (
                f" with dangerous flags {dangerous_flags}"
                if dangerous_flags else ""
            )
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=verb,
                dangerous_flags=dangerous_flags,
                cli_family=family,
                confidence=confidence,
                reason=f"Mutative verb '{verb}'{flag_detail}",
            )

        if candidate in READ_ONLY_VERBS or full_lower in READ_ONLY_VERBS:
            verb = candidate if candidate in READ_ONLY_VERBS else full_lower
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb=verb,
                cli_family=family,
                confidence=confidence,
                reason=f"Read-only verb '{verb}'",
            )

        # Check command aliases as verb (e.g., "docker rm" -> rm is alias)
        if candidate in COMMAND_ALIASES:
            alias_cat = COMMAND_ALIASES[candidate]
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            return MutativeResult(
                is_mutative=True,
                category=alias_cat,
                verb=candidate,
                dangerous_flags=dangerous_flags,
                cli_family=family,
                confidence=confidence,
                reason=f"Verb alias '{candidate}' is {alias_cat.lower()}",
            )

    # --- Step 4b: API subcommand with no explicit mutative HTTP method ---
    # CLIs like `gh api` and `glab api` default to GET when no -X flag is
    # specified.  If the semantic scan found no verb and the subcommand is
    # "api", treat the command as read-only.
    if (
        not any(
            t in MUTATIVE_VERBS
            for t in semantics.semantic_head_tokens[1:]
        )
        and len(semantics.semantic_head_tokens) > 1
        and semantics.semantic_head_tokens[1] == "api"
    ):
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb="api",
            cli_family=family,
            confidence="high",
            reason="API call with implicit GET method",
        )

    # --- Step 5: Scan for dangerous flags (no verb found) ---
    dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
    if dangerous_flags:
        # Find first non-flag token as the "verb" for reporting
        verb, _ = _find_first_non_flag(semantics.semantic_head_tokens)
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_UNKNOWN,
            verb=verb,
            dangerous_flags=dangerous_flags,
            cli_family=family,
            confidence="low",
            reason=f"Unknown verb '{verb}' with dangerous flags {dangerous_flags}",
        )

    # --- Step 6: No match -- not mutative (safe by elimination) ---
    verb, _ = _find_first_non_flag(semantics.semantic_head_tokens)
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_UNKNOWN,
        verb=verb,
        cli_family=family,
        confidence="low",
        reason=f"Unknown verb '{verb}' with no dangerous flags",
    )


# ============================================================================
# Helpers
# ============================================================================

def _check_inline_code(command: str, base_cmd: str, family: str) -> MutativeResult:
    """Check inline code (python3 -c "...") for dangerous patterns.

    Instead of verb-matching tokens (which false-positive on generic keywords
    like "import", "create"), scan the raw code string for known dangerous
    function calls.

    Args:
        command: Full raw command string.
        base_cmd: The interpreter (e.g., "python3").
        family: CLI family hint.

    Returns:
        MutativeResult -- MUTATIVE if dangerous patterns found, else safe.
    """
    for pattern, label in _DANGEROUS_INLINE_PATTERNS:
        if pattern.search(command):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=label,
                cli_family=family,
                confidence="medium",
                reason=f"Inline code contains dangerous pattern: {label}",
            )

    # No dangerous patterns found -- safe inline code
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="inline-code",
        cli_family=family,
        confidence="medium",
        reason=f"Inline code ({base_cmd} -c) with no dangerous patterns",
    )


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
    danger: MutativeResult,
    nonce: str = "",
) -> dict:
    """Build an internal block response dict for T3 commands.

    Returns an internal dict consumed by bash_validator, which wraps the
    'message' field into a hookSpecificOutput with permissionDecision: "deny".
    The 'decision' key is internal only and never sent to Claude Code.

    Args:
        command: The original shell command.
        danger: MutativeResult from detect_mutative_command.
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
        f"[T3_APPROVAL_REQUIRED] {danger.category} operation detected.\n"
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


