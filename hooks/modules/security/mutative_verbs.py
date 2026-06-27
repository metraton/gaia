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
from typing import Dict, FrozenSet, List, Tuple, Union

from .approval_messages import build_t3_approval_instructions
from .command_semantics import analyze_command

try:
    from .capability_classes import (
        CATEGORY_MUTATIVE as _CAP_MUTATIVE,
        CATEGORY_READ_ONLY as _CAP_READ_ONLY,
        classify_capability as _classify_capability,
        is_capability_verb as _is_capability_verb,
    )
except ImportError:  # pragma: no cover -- defensive
    _classify_capability = None
    _is_capability_verb = None
    _CAP_MUTATIVE = "MUTATIVE"
    _CAP_READ_ONLY = "READ_ONLY"
    logging.getLogger(__name__).warning(
        "capability_classes not importable; database CLI capability "
        "classification disabled"
    )

try:
    from .blocked_commands import is_blocked_command as _is_blocked_command
except ImportError:
    _is_blocked_command = None
    logging.getLogger(__name__).warning(
        "blocked_commands.is_blocked_command not importable; "
        "inline code Layer 1 (shell extraction) disabled"
    )

try:
    from .inline_ast_analyzer import analyze_python_inline as _analyze_python_inline
except ImportError:  # pragma: no cover -- defensive
    _analyze_python_inline = None
    logging.getLogger(__name__).warning(
        "inline_ast_analyzer.analyze_python_inline not importable; "
        "AST-based Python inline analysis disabled (falling back to regex)"
    )

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
    # NOTE: "release" removed -- it is a CLI subcommand group noun in `gh release`,
    # `glab release`, etc. The actual mutative actions (create, delete, edit, upload)
    # are already in MUTATIVE_VERBS. Keeping "release" here causes false positives on
    # `gh release view` and any command with "release" as an argument string.
    "deploy", "install", "upgrade", "downgrade", "publish", "promote",
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
    "exec", "execute", "invoke", "trigger", "send", "reply",
    # Git operations
    # NOTE: "stash" removed -- safe by elimination (local-only operation)
    # NOTE: "commit" removed -- local-only operation, trust system
    "push", "merge", "rebase",
    "rollback",
    # Access control
    "grant", "assign", "revoke",
    # Reconciliation
    "reconcile", "rsync",
    # Deletion / removal (approvable via nonce -- blocked_commands.py catches
    # the truly destructive patterns like "delete namespace", "delete-vpc", etc.)
    "delete", "destroy", "remove", "drop", "purge", "wipe", "clean",
    "trash", "shred", "srm",
    "truncate", "kill", "terminate", "uninstall", "unpublish",
    "drain", "evict", "cordon", "deregister", "detach",
    "disconnect", "unbind", "force-delete", "force-remove", "erase",
    # Collaboration (GitHub/GitLab CLI)
    "comment", "label", "annotate", "approve", "close", "reopen", "tag",
    # HTTP methods (e.g., glab api -X POST, gh api -X DELETE)
    # NOTE: "put" and "patch" already appear under Modification above, and
    # "uninstall" under Deletion/removal -- so only "post" is new here.
    "post",
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
# Git Local-Only Subcommands (early-exit guard)
# ============================================================================
# Git subcommands that NEVER mutate remote state.  When the base command is
# "git" and the first non-flag token is one of these, short-circuit to
# non-mutative.  This prevents message body text (after -m) from leaking
# into the verb scanner and triggering false positives on words like
# "update", "create", "deploy" inside commit messages.
#
# NOT included here (intentionally left to the verb scanner):
#   push    -- mutates remote
#   merge   -- in MUTATIVE_VERBS (local but destructive merge commit)
#   rebase  -- in MUTATIVE_VERBS (rewrites history)
#   tag     -- in MUTATIVE_VERBS (creates refs, tag -d deletes)

GIT_LOCAL_SAFE_SUBCOMMANDS: FrozenSet[str] = frozenset({
    "commit",
    "stash",
    "add",
    "log",
    "diff",
    "status",
    "branch",
    "checkout",
    "switch",
    "reflog",
    "bisect",
    "blame",
    "show",
    "shortlog",
    "whatchanged",
    "notes",
    "reset",       # local-only: modifies local refs/staging, never touches remote
    "revert",      # local-only: creates a new commit undoing changes, no remote side effects
    "cherry-pick", # local-only: applies commits from another branch, no remote side effects
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
# CLI-Verb Tier Exceptions (unconditional downgrade from MUTATIVE)
# ============================================================================
# Downgrade specific (cli_family, verb) combos to a lower tier regardless of
# flags.  Use only when the API-level semantics of the verb are safe despite
# the generic verb name being in MUTATIVE_VERBS.
#
# Key:   (cli_family, verb)  — cli_family comes from CLI_FAMILY_LOOKUP above.
# Value: target category constant (CATEGORY_READ_ONLY or CATEGORY_SIMULATION).
#
# This dict is protected by the Write/Edit T3 hook (it lives inside the hooks
# directory).  Modifications require user approval.

CLI_VERB_TIER_EXCEPTIONS: Dict[Tuple[str, str], str] = {
    # Gmail API: "modify" only changes labels/flags on messages — it cannot
    # alter message content, send mail, or delete anything.  Safe as T0.
    ("workspace", "modify"): CATEGORY_READ_ONLY,
}


# ============================================================================
# Command+Subcommand Tier Exceptions (anchored, not family-wide)
# ============================================================================
# Some project-CLI subcommand GROUPS are local-only bookkeeping: they edit a
# row in the local planning store (briefs, acceptance criteria) and have no
# external/remote side effects.  Any verb under such a group (edit, set-status,
# add, remove, ...) would otherwise trip MUTATIVE_VERBS and demand T3 approval
# from subagents, even though the operation is reversible and purely local.
#
# This is anchored EXPLICITLY to (base_cmd, subcommand-group) -- NOT a generic
# `gaia *` exemption.  A family-wide exemption would also un-gate
# `gaia approvals approve` / `gaia approvals revoke`, which must stay T3
# because they ARE the consent layer itself.  Anchoring keeps the gate intact
# everywhere except the two named planning groups.
#
# Key:   (base_cmd, subcommand)  — base_cmd is the resolved (pathless) CLI name,
#        subcommand is non_flag_tokens[0] (the group token right after `gaia`).
# Value: target category constant (CATEGORY_READ_ONLY or CATEGORY_SIMULATION).
#
# NOTE: treated exactly like git "commit" (local-only operation, trust system):
# local planning bookkeeping, reversible, no external effects.
COMMAND_SUBCOMMAND_TIER_EXCEPTIONS: Dict[Tuple[str, str], str] = {
    # `gaia brief <verb>` (new/edit/show/list/set-status/set-field): local
    # planning bookkeeping in the brief store — reversible, no external effects.
    ("gaia", "brief"): CATEGORY_READ_ONLY,
    # `gaia ac <verb>` (add/remove/edit/show/list/set-status): local acceptance-
    # criteria bookkeeping — reversible, no external effects.
    ("gaia", "ac"): CATEGORY_READ_ONLY,
    # `gaia plan <verb>` (save/edit/show/list/set-status): local planning
    # bookkeeping in the plan store — reversible, no external effects.  Anchored
    # here (not left to the SIMULATION_VERBS['plan'] lexical collision) so the
    # exemption is explicit and carries the same DENY-verb guard as `gaia brief`:
    # `gaia plan delete` (whole-record destruction) stays T3.
    ("gaia", "plan"): CATEGORY_READ_ONLY,
    # `gaia task <verb>` (add/set-status/reorder/show/list): local task-lifecycle
    # bookkeeping in gaia.db — reversible status transitions, no external effects,
    # mirrors the brief/ac/plan exemptions.  `gaia task remove` (irreversible row
    # deletion) stays T3 via the per-group deny-verbs guard in
    # COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS below.
    ("gaia", "task"): CATEGORY_READ_ONLY,
}

# Verbs that stay gated even under an excepted group above.  The exception
# covers REVERSIBLE bookkeeping (edit, set-*, add, remove a single AC row);
# whole-record DESTRUCTION (`gaia brief delete <id>`) is irreversible and must
# keep its T3 contract — pinned by test_gaia_brief_delete_still_blocks.
COMMAND_SUBCOMMAND_EXCEPTION_DENY_VERBS: FrozenSet[str] = frozenset({
    "delete", "destroy", "purge", "wipe", "drop", "shred", "erase",
})

# Per-group EXTRA deny verbs that augment the global set above for specific
# (base_cmd, subcommand) pairs.  Use this when a verb is destructive for one
# group but is a legitimate reversible bookkeeping operation for another
# (e.g., `gaia ac remove` removes a single reversible AC row and must stay
# non-T3, but `gaia task remove` deletes the task record permanently and must
# stay T3).  The enforcement logic ORs the global set with this per-group set.
COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS: Dict[Tuple[str, str], FrozenSet[str]] = {
    # `gaia task remove` is an irreversible row deletion (no un-delete in the
    # tasks store), unlike `gaia ac remove` (AC rows can be re-added).
    ("gaia", "task"): frozenset({"remove"}),
}


# ============================================================================
# PRINCIPLE: consent-REDUCING operations are not T3.
# ----------------------------------------------------------------------------
# An operation requires T3 approval because it GRANTS capability or DESTROYS
# state — it moves the system toward *more* power or *less* recoverability, the
# directions that need informed consent.  An operation that REVOKES, REJECTS,
# or CLEANS a consent grant Gaia itself issued moves in the opposite direction:
# it can only REDUCE the capability already granted.  It never grants anything
# and never reaches outside the local approval store.  Gating it creates an
# absurd loop — you would need an approval to clean up approvals.
#
# So: within Gaia's own consent layer (`gaia approvals ...`), verbs that REDUCE
# consent are exempted to read-only; the one verb that GRANTS capability
# (`approve`) is deliberately NOT in this set and stays T3.  That asymmetry is
# the whole point: `approve` hands out capability without the AskUserQuestion
# flow, so it must remain gated; `revoke`/`reject`/`reject-all`/`clean` only
# take capability back, so they must not be.
#
# This is anchored to (base_cmd, group) so it applies ONLY to Gaia's own
# consent store, not to any other CLI's notion of "revoke"/"reject" (e.g. a
# cloud IAM revoke is a real remote mutation and must stay T3).
#
# Key:   (base_cmd, subcommand-group)  — e.g. ("gaia", "approvals").
# Value: frozenset of consent-REDUCING verbs under that group that are exempt.
CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS: Dict[Tuple[str, str], FrozenSet[str]] = {
    ("gaia", "approvals"): frozenset({
        "revoke", "reject", "reject-all", "clean",
    }),
}


# ============================================================================
# Inline Code Detection — Language-Agnostic 3-Layer Approach
# ============================================================================
# When the base command is a runtime interpreter with an inline code flag
# (e.g., python3 -c, node -e, ruby -e, perl -e), scan the code string
# using three layers instead of verb-matching tokens:
#   Layer 1: Extract string literals → check against blocked_commands
#   Layer 2: Universal dangerous API keyword patterns
#   Layer 3: Heuristic safety classification (length, paths, encoding)
import re as _re

# ---------------------------------------------------------------------------
# CLI → inline-code flag mapping (Step 1a)
# ---------------------------------------------------------------------------
_INLINE_CODE_MAP: Dict[str, FrozenSet[str]] = {
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "python3.10": frozenset({"-c"}),
    "python3.11": frozenset({"-c"}),
    "python3.12": frozenset({"-c"}),
    "python3.13": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval"}),
    "ruby": frozenset({"-e"}),
    "perl": frozenset({"-e", "-E"}),
    "php": frozenset({"-r"}),
    "lua": frozenset({"-e"}),
    "rscript": frozenset({"-e"}),
}
_INLINE_CODE_CLIS: FrozenSet[str] = frozenset(_INLINE_CODE_MAP.keys())

# Python interpreters whose ``-c`` payload is parsed by the AST analyzer.
_PYTHON_INTERPRETERS: FrozenSet[str] = frozenset({
    "python", "python3",
    "python3.10", "python3.11", "python3.12", "python3.13",
})

# ---------------------------------------------------------------------------
# Script-file interpreters (Step 3b2)
# ---------------------------------------------------------------------------
# Interpreters that take a SCRIPT FILE as a positional argument
# (``python3 deploy.py``, ``bash setup.sh``, ``node migrate.js``).  Without
# this set the verb scanner sees only the filename token -- which carries a
# ``.`` and so is rejected as a non-subcommand -- and the command slips through
# as safe by elimination, executing the file's mutations without approval.
# The fix reads the file and classifies it by REAL invocation (AST for Python,
# the blocked/mutative regex layer for shells and other interpreters), never by
# the bare ``<interp> <file>`` shape.  ``ruby``/``perl``/``php``/``node`` have
# no vendored AST, so their files go through the same regex layer as shells.
_SCRIPT_FILE_INTERPRETERS: FrozenSet[str] = frozenset({
    "python", "python3",
    "python3.10", "python3.11", "python3.12", "python3.13",
    "bash", "sh", "zsh", "dash", "ksh",
    "node", "ruby", "perl", "php",
})

# File extensions whose interpreter is implied by ``./script`` (no explicit
# interpreter token).  Maps the extension to the analysis lane used for its
# content: "python" routes through the AST analyzer, "shell" through the
# blocked/mutative regex layer.
_SHEBANG_EXT_LANES: Dict[str, str] = {
    ".py": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".js": "shell",
    ".mjs": "shell",
    ".cjs": "shell",
    ".rb": "shell",
    ".pl": "shell",
    ".php": "shell",
}

# Cap on bytes read from a script file during classification.  A script larger
# than this is unusual for the inline-evasion case and reading it in full would
# add latency to every hook invocation; we read a bounded prefix, which is
# enough to catch the mutative calls an evasion script front-loads.
_MAX_SCRIPT_READ_BYTES = 256 * 1024

# Interpreter flags that CONSUME the next token as their value AND mean the
# invocation has no script-file positional (the payload is inline code or a
# module name).  When one of these is present the script-file lane defers --
# the inline path (Step 3b) or ordinary verb scanning handles the command.
#   python -c <code> / -m <module>   bash -c <code>   node -e <code>
_INTERP_NON_SCRIPT_VALUE_FLAGS: Dict[str, FrozenSet[str]] = {
    "python": frozenset({"-c", "-m"}),
    "python3": frozenset({"-c", "-m"}),
    "python3.10": frozenset({"-c", "-m"}),
    "python3.11": frozenset({"-c", "-m"}),
    "python3.12": frozenset({"-c", "-m"}),
    "python3.13": frozenset({"-c", "-m"}),
    "bash": frozenset({"-c"}),
    "sh": frozenset({"-c"}),
    "zsh": frozenset({"-c"}),
    "dash": frozenset({"-c"}),
    "ksh": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval", "-p", "--print", "-r", "--require"}),
    "ruby": frozenset({"-e"}),
    "perl": frozenset({"-e", "-E"}),
    "php": frozenset({"-r"}),
}

# ---------------------------------------------------------------------------
# Python ``-m <package-manager>`` re-dispatch (Brief 91, AC-7)
# ---------------------------------------------------------------------------
# ``python3 -m pip install x`` is the SAME operation as ``pip install x`` -- the
# ``-m`` form merely runs the package manager as a module of the interpreter.
# Before this guard, the interpreter (``python3``) was the base command, the
# module name (``pip``) was swallowed into flag_tokens as the value of ``-m``,
# and classification limped along ONLY when a generic verb (``install``)
# happened to remain in MUTATIVE_VERBS.  That is accidental, not robust:
#   * ``python3 -m poetry add x`` slipped through (``add`` was removed from
#     MUTATIVE_VERBS as a git-add false-positive), bypassing T3 entirely.
#   * the command reported cli_family=runtime, never recognized as ``package``.
# The fix recognizes ``<python> -m <pkg-mgr> <args...>`` and RE-DISPATCHES it as
# ``<pkg-mgr> <args...>`` so it classifies identically to the direct CLI form:
# ``install``/``uninstall``/``add`` -> MUTATIVE/T3, ``list``/``download`` ->
# READ_ONLY (matching real pip semantics).  Scoped to the package-manager
# modules below so ``python3 -m pytest`` / ``python3 -m http.server`` are NOT
# rerouted -- they fall through to ordinary detection unchanged.
_PY_MODULE_PACKAGE_MANAGERS: FrozenSet[str] = frozenset({
    "pip", "pip3", "pipenv", "poetry", "uv",
})

# ---------------------------------------------------------------------------
# Layer 1: Shell command extraction from string literals
# ---------------------------------------------------------------------------
_STRING_LITERAL_RE = _re.compile(r"""(?:['"])((?:[^'"\\\n]|\\.){3,})(?:['"])""")


def _extract_embedded_shell_commands(code: str) -> List[str]:
    """Extract string literals from inline code that may contain shell commands."""
    return [m.group(1) for m in _STRING_LITERAL_RE.finditer(code)]


# ---------------------------------------------------------------------------
# Layer 2: Universal dangerous API keyword patterns (category-based)
# ---------------------------------------------------------------------------
_UNIVERSAL_DANGEROUS_PATTERNS: Tuple[Tuple[_re.Pattern, str, str], ...] = (
    # Category: Process Execution
    (_re.compile(r"\b(child_process|subprocess)\b"), "process-module", "PROCESS_EXECUTION"),
    (_re.compile(r"\b(execSync|execFile|execFileSync)\s*\("), "exec-sync", "PROCESS_EXECUTION"),
    (_re.compile(r"\bos\.(system|popen|exec[lv]?[pe]?)\s*\("), "os-exec", "PROCESS_EXECUTION"),
    (_re.compile(r"\b(system|exec)\s*\("), "system-call", "PROCESS_EXECUTION"),
    (_re.compile(r"\bspawn(Sync)?\s*\("), "spawn-call", "PROCESS_EXECUTION"),
    (_re.compile(r"\bPopen\s*\("), "popen-call", "PROCESS_EXECUTION"),
    (_re.compile(r"`[^`]{3,}`"), "backtick-exec", "PROCESS_EXECUTION"),

    # Category: File Deletion
    (_re.compile(r"\b(os\.remove|os\.unlink|os\.rmdir)\s*\("), "os-delete", "FILE_DELETION"),
    (_re.compile(r"\b(shutil\.rmtree|shutil\.move)\s*\("), "shutil-delete", "FILE_DELETION"),
    (_re.compile(r"\bfs\.(unlink|rmdir|rm)(Sync)?\s*\("), "fs-delete", "FILE_DELETION"),
    # Also match .unlinkSync( / .rmSync( / .rmdirSync( as method calls (e.g., require('fs').unlinkSync())
    (_re.compile(r"\.(unlink|rmdir|rm)(Sync)?\s*\("), "fs-delete", "FILE_DELETION"),
    (_re.compile(r"\bFile\.(delete|unlink)\s*\("), "file-delete", "FILE_DELETION"),
    (_re.compile(r"\bunlink\s*\("), "unlink-call", "FILE_DELETION"),
    (_re.compile(r"\brmtree\s*\("), "rmtree-call", "FILE_DELETION"),
    (_re.compile(r"\bFileUtils\.rm"), "fileutils-rm", "FILE_DELETION"),
    (_re.compile(r"pathlib\.Path\([^)]*\)\.(unlink|rmdir)"), "pathlib-delete", "FILE_DELETION"),

    # Category: File Write
    (_re.compile(r"open\s*\([^)]*['\"][wWaA]"), "file-write-open", "FILE_WRITE"),
    (_re.compile(r"\bfs\.writeFile(Sync)?\s*\("), "fs-write", "FILE_WRITE"),
    # Also match .writeFileSync( / .appendFileSync( as method calls
    (_re.compile(r"\.writeFile(Sync)?\s*\("), "fs-write", "FILE_WRITE"),
    (_re.compile(r"\bfs\.appendFile(Sync)?\s*\("), "fs-append", "FILE_WRITE"),
    (_re.compile(r"\.appendFile(Sync)?\s*\("), "fs-append", "FILE_WRITE"),
    (_re.compile(r"\bFile\.(write|open)\b.*['\"][wWaA]"), "file-write-ruby", "FILE_WRITE"),
    (_re.compile(r"\.write\s*\("), "file-write", "FILE_WRITE"),
    (_re.compile(r"pathlib\.Path\([^)]*\)\.(rename|write_)"), "pathlib-write", "FILE_WRITE"),

    # Category: File System Mutation (os.rename, os.makedirs, shutil.copy)
    (_re.compile(r"\bos\.rename\s*\("), "os-rename", "FILE_MUTATION"),
    (_re.compile(r"\bos\.makedirs?\s*\("), "os-makedirs", "FILE_MUTATION"),
    (_re.compile(r"\bshutil\.copy\s*\("), "shutil-copy", "FILE_MUTATION"),

    # Category: Network
    (_re.compile(r"\bhttps?://\S+"), "url-literal", "NETWORK"),
    (_re.compile(r"\b(fetch|axios|requests\.get|urllib)\s*\("), "http-call", "NETWORK"),
    (_re.compile(r"\bNet::HTTP\b"), "net-http", "NETWORK"),

    # Category: Permission Modification
    (_re.compile(r"\bos\.chmod\s*\("), "os-chmod", "PERMISSION_MOD"),
    (_re.compile(r"\bfs\.chmod(Sync)?\s*\("), "fs-chmod", "PERMISSION_MOD"),
)

# ---------------------------------------------------------------------------
# Layer 3: Heuristic safety classification
# ---------------------------------------------------------------------------
_SUSPICIOUS_HEURISTICS: Tuple[Tuple[_re.Pattern, str], ...] = (
    (_re.compile(r"\b(base64|b64encode|b64decode|atob|btoa)\b"), "encoding"),
    (_re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "ip-address"),
)

MAX_SAFE_INLINE_LENGTH = 150
MAX_NORMAL_INLINE_LENGTH = 500


# ============================================================================
# Command Aliases (single-token commands that map to a category)
# ============================================================================

# All command aliases are MUTATIVE (approvable via nonce).
# The truly destructive patterns (rm -rf /, dd of=/dev/sda, mkfs, fdisk) are
# permanently blocked by blocked_commands.py before the verb detector runs.
COMMAND_ALIASES: Dict[str, str] = {
    "rm": CATEGORY_MUTATIVE,
    "rmdir": CATEGORY_MUTATIVE,
    "mkdir": CATEGORY_MUTATIVE,
    "mv": CATEGORY_MUTATIVE,
    "cp": CATEGORY_MUTATIVE,
    "ln": CATEGORY_MUTATIVE,
    "dd": CATEGORY_MUTATIVE,
    "mkfs": CATEGORY_MUTATIVE,
    "fdisk": CATEGORY_MUTATIVE,
    "chmod": CATEGORY_MUTATIVE,
    "chown": CATEGORY_MUTATIVE,
    "chgrp": CATEGORY_MUTATIVE,
    "nohup": CATEGORY_MUTATIVE,
}


# ============================================================================
# Read-Only Base Commands (fast-path whitelist)
# ============================================================================
# Common read-only inspection commands. When the base_cmd matches, short-circuit
# to safe BEFORE any verb-token or camelCase scanning. This prevents false
# positives where a quoted argument value (e.g., "SessionStart") gets split
# into camelCase parts and matches a mutative verb.
#
# CRITICAL: Do not include `sed` here. `sed -i` mutates files in-place; `sed`
# without `-i` is read-only, but distinguishing flags here is fragile and the
# verb scanner already classifies bare `sed` as safe by elimination.
#
# These commands are read-only by design at the syscall level: they open files
# for reading and write to stdout. Any flag combination that would mutate state
# (e.g., `find -delete`) is caught by the dangerous-flags scanner via the
# generic CLI families that use those flags, or by `blocked_commands.py`.

READ_ONLY_BASE_CMDS: FrozenSet[str] = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "find", "fd", "locate",
    "ls", "ll", "la", "tree",
    "cat", "bat", "less", "more",
    "head", "tail",
    "awk", "sort", "uniq", "cut", "tr", "wc", "column",
    "stat", "file", "du", "df",
    "readlink", "realpath", "dirname", "basename",
    "which", "whereis", "type", "command",
    "echo", "printf", "yes", "true", "false",
    "date", "uptime", "id", "whoami", "groups", "hostname",
    "ps", "pgrep",
    "env",
    "diff", "cmp",
    "xxd", "od", "hexdump", "strings",
})


# Find -- special handling: `-delete` flag mutates. We scan for it explicitly
# in the find fast-path so we can keep `find` in the read-only whitelist for
# the common case while still flagging the destructive flag.
_FIND_MUTATIVE_FLAGS: FrozenSet[str] = frozenset({"-delete"})


# ============================================================================
# mkdir -- path-sensitive tier override (T3 for sensitive paths, T0 otherwise)
# ============================================================================
# `mkdir` within the working tree (relative paths, home-relative paths, or
# absolute paths that are not system directories) is non-destructive and
# idempotent with `-p`.  It creates new directories but cannot corrupt existing
# files or system state, so working-tree use is classified as T0.
#
# HOWEVER: directing `mkdir` at kernel pseudo-filesystems or privileged OS
# directories is a signal of unusual intent and keeps T3 classification.
# The chosen set of sensitive prefixes is the complete system namespace MINUS
# scratch space (/tmp, /run):
#
#   /dev    -- device nodes; creating here can override device files.
#   /sys    -- kernel parameter tree (sysfs); writing can alter kernel state.
#   /proc   -- kernel process/memory interfaces; structurally read-only but
#               creating entries here has caused kernel panics in edge cases.
#   /etc    -- system-wide configuration; writes affect all users and services.
#   /boot   -- bootloader and kernel images; corruption bricks the system.
#   /usr    -- system binaries and libraries; tampering affects all users.
#   /bin    -- essential user binaries; modifying can break basic shell tools.
#   /sbin   -- essential system binaries; modifying can break boot/recovery.
#   /lib    -- shared libraries for /bin and /sbin; modifying breaks executables.
#   /lib64  -- 64-bit shared libraries; same risk profile as /lib.
#   /root   -- root user's home directory; any write here is privileged access.
#
# Scratch space (/tmp, /run) is explicitly excluded: these directories are
# ephemeral, world-writable by design, and creating subdirectories there is
# routine working-tree activity with no system-state risk.
MKDIR_SENSITIVE_PATH_PREFIXES: FrozenSet[str] = frozenset({
    "/dev",
    "/sys",
    "/proc",
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/root",
})


def _mkdir_targets_sensitive_path(tokens: tuple) -> bool:
    """Return True if any path argument to mkdir falls under a sensitive prefix.

    Scans all non-flag tokens after the base command (skipping `--` separators
    and flag values).  A path is sensitive when it is absolute and its first
    component matches MKDIR_SENSITIVE_PATH_PREFIXES.

    Conservative by design: if there are no path arguments at all (ambiguous),
    the caller treats the command as T3.  Relative paths and home-relative
    paths (~/...) are not sensitive; they resolve inside the user's working
    tree and are always safe.

    Args:
        tokens: Full token tuple from tokenize_command (includes base cmd).

    Returns:
        True  -> at least one path argument is under a sensitive prefix (T3).
        False -> all path arguments are working-tree paths (T0 eligible).
    """
    import os
    seen_end_of_opts = False
    i = 1  # skip base_cmd at index 0
    while i < len(tokens):
        token = tokens[i]
        i += 1

        if token == "--":
            seen_end_of_opts = True
            continue

        if not seen_end_of_opts and token.startswith("-"):
            # Consume the value of known value-taking flags (-m/--mode needs a value).
            if token in ("-m", "--mode"):
                i += 1  # skip the mode value
            continue

        # token is a path argument (positional, or after --)
        # Expand a leading ~ conservatively (no env var expansion).
        if token.startswith("~/") or token == "~":
            # Home-relative paths are always safe -- they resolve under $HOME.
            continue

        if not os.path.isabs(token):
            # Relative path -> working-tree, not sensitive.
            continue

        # Absolute path: check whether it starts with any sensitive prefix.
        # Normalise the token to eliminate // or trailing slashes.
        norm = os.path.normpath(token)
        for prefix in MKDIR_SENSITIVE_PATH_PREFIXES:
            # Match /etc exactly or /etc/<something> (not /etc_custom).
            if norm == prefix or norm.startswith(prefix + "/"):
                return True

    return False


# ============================================================================
# Simulation Flags (--dry-run and equivalents)
# ============================================================================

SIMULATION_FLAGS: FrozenSet[str] = frozenset({
    "--dry-run",
    "--dryrun",
    "--dry-run=client",
    "--dry-run=server",
    # Workspace analysis flags that generate a report without mutating state.
    # "gaia workspace merge --report-duplicates" reads and analyses context;
    # the flag explicitly signals a read-only report operation.
    "--report-duplicates",
})


# ============================================================================
# --help Exemption Whitelist (T3 -> T0 downgrade for well-known CLIs)
# ============================================================================
# Only CLIs listed here get the --help exemption in Step 3.5 of the detection
# algorithm. Command aliases (rm, mv, dd, etc.) are intentionally excluded:
# they may process arguments before honoring --help, so keeping them T3
# preserves safety.

HELP_FLAGS: FrozenSet[str] = frozenset({"--help", "-h", "-?", "--usage"})

# CLI families where `<cli> <verb> --help` is idempotent (exit-and-print).
# Keys come from CLI_FAMILY_LOOKUP (defined further below in this module).
HELP_IDEMPOTENT_FAMILIES: FrozenSet[str] = frozenset({
    "k8s",       # kubectl, helm, flux, kustomize
    "iac",       # terraform, terragrunt, pulumi
    "git",       # git subcommands respect --help via man page
    "docker",    # docker, podman
    "cloud",     # aws, gcloud, gh, glab, az
    "package",   # npm, pip, yarn, bun, cargo, brew, apt
    "build",     # make, cmake, bazel, gradle, mvn
    "runtime",   # node, python, python3
    "linter",    # pytest, mypy, ruff, black, flake8
})

# Explicit base_cmd whitelist (not covered by CLI_FAMILY_LOOKUP).
HELP_IDEMPOTENT_BASE_CMDS: FrozenSet[str] = frozenset({
    "gaia",      # project CLI, not in CLI_FAMILY_LOOKUP
})

# Shell redirect tokens (e.g., "2>&1", ">out", "<in") that shlex produces
# as non-flag tokens but carry no CLI semantic value. Used by the --help
# exemption to count only real positional args.
import re as _re_help
_SHELL_REDIRECT_RE = _re_help.compile(r"^(\d*[<>]&?\d*|[<>]{1,2}.*)$")


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
    "--hard": "CONTEXT",
    "-rf": "ALWAYS",
    "-fr": "ALWAYS",
}

# Git-specific flags that promote a normally local-safe subcommand to T3.
# ``git reset`` and ``git reset --soft`` only adjust the index and HEAD
# without touching the working tree, but ``git reset --hard`` discards
# uncommitted changes and is approvable rather than blocked.  Keeping the
# flag set centralized makes the policy auditable and lets test_blocked_*
# pin the contract.
GIT_HARD_RESET_FLAGS: FrozenSet[str] = frozenset({"--hard"})

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

# CLIs where --hard discards live state (currently only `git reset --hard`).
HARD_FLAG_IS_DESTRUCTIVE: FrozenSet[str] = frozenset({
    "git",
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
    "eksctl": "cloud", "gh": "cloud", "glab": "cloud", "gws": "workspace",
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
    tokens: Union[List[str], tuple],
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
            elif token == "--hard":
                if cli in HARD_FLAG_IS_DESTRUCTIVE:
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
# CamelCase Splitting
# ============================================================================

def split_camel_case(token: str) -> List[str]:
    """Split a camelCase token into lowercase component words.

    Examples:
        "batchDelete"   -> ["batch", "delete"]
        "deleteMessages" -> ["delete", "messages"]
        "GET"           -> ["get"]          (all-caps stays as one token)
        "already_snake" -> ["already_snake"] (no camelCase boundary)
    """
    parts = _re.sub(r"([a-z])([A-Z])", r"\1 \2", token).split()
    return [p.lower() for p in parts] if len(parts) > 1 else [token.lower()]


# ============================================================================
# Subcommand Identifier Predicate
# ============================================================================
# A real CLI subcommand identifier is a single word: letters, digits,
# underscores, and at most internal hyphens (e.g., "delete", "force-delete",
# "batchDelete", "merge_base"). Tokens that carry path separators, scope
# separators, dots, colons, equals, commas, or other non-identifier
# characters are argument values (file paths, test selectors like
# "tests/x.py::TestStop", URLs, query strings, KV pairs, module names) --
# never subcommands. Verb splitting (camelCase or hyphen) must NOT be
# applied to those tokens; otherwise an argument value like
# "tests/test_install.py::TestStop" gets split as "test", "stop" and the
# whole command is misclassified as MUTATIVE.

# Characters that, if present in a raw token, mean it is NOT a CLI
# subcommand identifier. Hyphens and underscores are allowed.
_NON_SUBCOMMAND_CHARS: FrozenSet[str] = frozenset({
    "/",  # path separator
    ".",  # file extension / module path
    ":",  # pytest scope separator (::) / URL scheme separator
    "=",  # KV pair
    ",",  # arg list separator
    "@",  # ref / module qualifier
    "?",  # query string
    "#",  # URL fragment / anchor
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    "*",
    "%",
    "$",
    "\\",
    "+",  # macro prefix already stripped by the caller; bare '+' tokens are not subcommands
})


def _is_subcommand_identifier(token: str) -> bool:
    """Return True if ``token`` looks like a CLI subcommand identifier.

    A subcommand identifier is a non-empty word made of letters, digits,
    underscores, and internal hyphens.  Tokens containing path separators,
    dots, colons, equals, etc. are argument values, not subcommands, and
    verb-splitting (camelCase, hyphen) must skip them.
    """
    if not token:
        return False
    for ch in token:
        if ch in _NON_SUBCOMMAND_CHARS:
            return False
    return True


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

        # mkdir path-sensitive override: classify as T0 when all path arguments
        # are inside the working tree (relative, or absolute non-sensitive).
        # Keeps T3 when any path targets a kernel or privileged OS directory.
        # Conservative fallback: if there are no path arguments (no positional
        # tokens after flags), remain T3 -- cannot confirm safety without a
        # known destination.
        if base_cmd == "mkdir":
            path_tokens = [
                t for t in tokens[1:]
                if not t.startswith("-") and t != "--"
            ]
            if path_tokens and not _mkdir_targets_sensitive_path(tokens):
                return MutativeResult(
                    is_mutative=False,
                    category=CATEGORY_READ_ONLY,
                    verb=base_cmd,
                    cli_family="system",
                    confidence="high",
                    reason=(
                        "mkdir targeting working-tree paths only "
                        "(no sensitive system prefix)"
                    ),
                )
            # No path arguments or sensitive path detected -> fall through to T3.

        return MutativeResult(
            is_mutative=True,
            category=alias_category,
            verb=base_cmd,
            dangerous_flags=dangerous_flags,
            cli_family=family if family != "unknown" else "system",
            confidence="high",
            reason=f"Command alias '{base_cmd}' is {alias_category.lower()}",
        )

    # --- Step 1b: Read-only base command fast-path ---
    # When the base_cmd is a known read-only inspection tool (grep, find, ls,
    # cat, etc.), short-circuit to safe BEFORE the verb scanner runs. This
    # prevents quoted argument values from being scanned as verb tokens,
    # which previously caused false positives such as
    #   grep -rn "SessionStart" file.json
    # being flagged as MUTATIVE because camelCase splitting on "SessionStart"
    # produced "start" (a mutative verb).
    #
    # Special-case: `find ... -delete` is destructive — flag it explicitly
    # before falling into the safe path.
    if base_cmd in READ_ONLY_BASE_CMDS:
        if base_cmd == "find" and any(
            t.lower() in _FIND_MUTATIVE_FLAGS for t in tokens
        ):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="find",
                dangerous_flags=("-delete",),
                cli_family="system",
                confidence="high",
                reason="`find -delete` removes matched files",
            )
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb=base_cmd,
            cli_family=family if family != "unknown" else "system",
            confidence="high",
            reason=f"Read-only base command '{base_cmd}' (whitelist fast-path)",
        )

    # --- Step 1c: Capability-class fast-path ---
    # Some CLIs (sqlite3, psql, mysql, mongosh, ...) accept the entire
    # mutation language as an argument string, so the verb scanner cannot
    # see the intent.  capability_classes.py groups these tools into one
    # rule set: default MUTATIVE, with explicit overrides for read-only
    # flags and inline read-only payloads.  Without this layer,
    # `sqlite3 db < file.sql` slips through as safe-by-elimination and
    # silently applies whatever the file contains.
    if _classify_capability is not None and _is_capability_verb is not None:
        if _is_capability_verb(base_cmd):
            cap = _classify_capability(semantics)
            if cap.matched:
                if cap.intent == _CAP_READ_ONLY:
                    return MutativeResult(
                        is_mutative=False,
                        category=CATEGORY_READ_ONLY,
                        verb=base_cmd,
                        cli_family="database",
                        confidence="high",
                        reason=cap.reason,
                    )
                # MUTATIVE -- still scan dangerous flags so the response
                # surfaces them in the approval prompt.
                dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=base_cmd,
                    dangerous_flags=dangerous_flags,
                    cli_family="database",
                    confidence="high",
                    reason=cap.reason,
                )

    # --- Step 1c-py: Python ``-m <pkg-mgr>`` re-dispatch (Brief 91, AC-7) ---
    # ``python3 -m pip install x`` is the same operation as ``pip install x``.
    # Recognize the ``<python> -m <package-manager> <args...>`` shape and re-run
    # detection on the rewritten ``<package-manager> <args...>`` command so it
    # classifies IDENTICALLY to the direct CLI form (install/uninstall -> T3,
    # list/download -> read-only).  Returns None when the command is not a
    # package-manager module invocation, so detection continues unchanged --
    # ``python3 -m pytest`` and ``python3 -m http.server`` are NOT rerouted.
    py_module_result = _check_python_module_runner(base_cmd, semantics)
    if py_module_result is not None:
        return py_module_result

    # --- Step 1d: Script-file analysis (python3 deploy.py, bash setup.sh, ./x) ---
    # An interpreter invoked with a script FILE as a positional argument, or a
    # direct ``./script`` invocation, hides its mutations inside the file --
    # the verb scanner sees only the filename.  Read the referenced file and
    # classify it by REAL invocation, the same standard the inline -c path meets.
    # Placed before the single-token early return so a bare ``./deploy.sh`` (one
    # token) is still inspected.  Returns None when the command is not a
    # recognized script-file shape, so detection continues normally.
    script_result = _check_script_file(command, base_cmd, family, semantics)
    if script_result is not None:
        return script_result

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

    # --- Step 3.5: --help exemption (whitelist + positional boundary) ---
    # A --help / -h invocation on a well-known CLI only prints usage text.
    # Three simultaneous conditions are required so the exemption does not
    # degrade safety on command-aliases or unknown tools:
    #   (a) CLI is in the whitelist (family OR base_cmd explicitly trusted)
    #   (b) a help flag is present in the PARSED flags (ignores strings
    #       that happen to contain "--help" inside a path or argument value)
    #   (c) the command invocation is simple (<=2 non-flag positional tokens):
    #       "gaia update --help" (1), "gaia approvals clean --help" (2) OK;
    #       "kubectl delete pod mypod --help" (3) stays T3 because the CLI
    #       may process the mutative args before honoring --help.
    # Root cause: ghost pendings P-738355ab and P-0b06738b were created by
    # `gaia update --help` and `gaia approvals clean --help` being
    # classified as MUTATIVE; this exemption prevents recurrence.
    if (
        base_cmd in HELP_IDEMPOTENT_BASE_CMDS
        or family in HELP_IDEMPOTENT_FAMILIES
    ):
        flag_set = set(semantics.flag_tokens)
        # Count semantic non-flag tokens only. Shell redirect shorthands
        # like "2>&1", ">file", "<file" appear as non-flag tokens in shlex
        # output but carry no CLI semantic value -- filtering them keeps
        # "gaia approvals clean --help 2>&1" at threshold 2 instead of 3.
        semantic_non_flags = [
            t for t in semantics.non_flag_tokens
            if not _SHELL_REDIRECT_RE.match(t)
        ]
        if flag_set & HELP_FLAGS and len(semantic_non_flags) <= 2:
            verb = (
                semantic_non_flags[0]
                if semantic_non_flags
                else "help"
            )
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb=verb,
                cli_family=family,
                confidence="high",
                reason=(
                    f"--help on whitelisted CLI '{base_cmd}' "
                    f"with simple invocation (<=2 non-flag tokens)"
                ),
            )

    # --- Step 3b: Inline code safety check (python3 -c, node -e, etc.) ---
    # For runtime interpreters with inline code flags, scan the code string
    # using the 3-layer approach instead of verb-matching tokens (which would
    # false-positive on generic keywords like "import", "create", etc.).
    cli_flags = _INLINE_CODE_MAP.get(base_cmd, frozenset())
    if base_cmd in _INLINE_CODE_CLIS and cli_flags & set(semantics.flag_tokens):
        return _check_inline_code(command, base_cmd, family)

    # --- Step 3c: Heredoc safety check ---
    # When a runtime interpreter is invoked with '-' (stdin) and the command
    # contains a heredoc ('<<'), the heredoc body is script source --
    # not shell subcommands.  Route through inline code analysis.
    # The length heuristic is suppressed: multi-line heredocs are normal
    # and must not be flagged on size alone.
    if (
        base_cmd in _INLINE_CODE_CLIS
        and "<<" in command
        and semantics.non_flag_tokens
        and semantics.non_flag_tokens[0] == "-"
    ):
        return _check_inline_code(command, base_cmd, family, skip_length_check=True)

    # --- Step 3d: Git local-only subcommand guard ---
    # When base_cmd is "git" and the subcommand is local-only (commit, stash,
    # add, log, etc.), short-circuit to non-mutative.  This prevents message
    # body text after -m from leaking into the verb scanner and triggering
    # false positives on words like "update", "create", "deploy".
    # Dangerous flags (-D, -M, --force) are still checked so that
    # "git branch -D feature" remains flagged.
    if base_cmd == "git" and semantics.non_flag_tokens:
        git_subcmd = semantics.non_flag_tokens[0]
        if git_subcmd in GIT_LOCAL_SAFE_SUBCOMMANDS:
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            if dangerous_flags:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=git_subcmd,
                    dangerous_flags=dangerous_flags,
                    cli_family=family,
                    confidence="high",
                    reason=f"Git local subcommand '{git_subcmd}' with dangerous flags {dangerous_flags}",
                )
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_SIMULATION if git_subcmd in SIMULATION_VERBS else CATEGORY_READ_ONLY if git_subcmd in READ_ONLY_VERBS else CATEGORY_UNKNOWN,
                verb=git_subcmd,
                cli_family=family,
                confidence="high",
                reason=f"Git local-only subcommand '{git_subcmd}' is safe",
            )

    # --- Step 3e: Command+subcommand tier exception (anchored) ---
    # Some project-CLI subcommand groups (e.g., `gaia brief`, `gaia ac`) are
    # local-only planning bookkeeping: edit/set-status/add/remove only touch a
    # row in the local store and have no external side effects.  Treated like
    # git "commit" (local-only operation, trust system).  Anchored EXPLICITLY to
    # (base_cmd, subcommand) so the gate stays intact for sibling groups such as
    # `gaia approvals approve/revoke`, which are the consent layer and must stay
    # T3.  Dangerous flags are still scanned so a slip like `--force` re-gates.
    if semantics.non_flag_tokens:
        subcommand_key = (base_cmd, semantics.non_flag_tokens[0])
        group_verb = (
            semantics.non_flag_tokens[1]
            if len(semantics.non_flag_tokens) > 1 else ""
        )
        # Whole-record destruction (delete/destroy/...) stays gated even within
        # an excepted group; only reversible bookkeeping is exempted.  Also
        # check per-group extra deny verbs (COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS)
        # for verbs that are destructive in one group but reversible in another.
        _extra_deny = COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS.get(subcommand_key, frozenset())
        verb_is_destructive = (
            group_verb.split("-", 1)[0] in COMMAND_SUBCOMMAND_EXCEPTION_DENY_VERBS
            or group_verb in COMMAND_SUBCOMMAND_EXCEPTION_DENY_VERBS
            or group_verb.split("-", 1)[0] in _extra_deny
            or group_verb in _extra_deny
        )
        if subcommand_key in COMMAND_SUBCOMMAND_TIER_EXCEPTIONS:
            if verb_is_destructive:
                # Whole-record destruction (e.g. `gaia plan delete`) must stay
                # T3 even inside an excepted group.  Anchor it MUTATIVE here
                # instead of falling through to Step 4: the group token itself
                # (`plan`) collides lexically with SIMULATION_VERBS['plan'], so
                # the verb scanner would otherwise mis-classify the whole
                # command as SIMULATION and silently un-gate the delete.  This
                # explicit return is what makes `gaia plan delete` behave like
                # `gaia brief delete` (where `brief` has no such collision).
                dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=group_verb.split("-", 1)[0],
                    dangerous_flags=dangerous_flags,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Whole-record destruction "
                        f"'{base_cmd} {semantics.non_flag_tokens[0]} {group_verb}' "
                        f"stays T3 despite the local bookkeeping exception"
                    ),
                )
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            if not dangerous_flags:
                target_category = COMMAND_SUBCOMMAND_TIER_EXCEPTIONS[subcommand_key]
                return MutativeResult(
                    is_mutative=False,
                    category=target_category,
                    verb=semantics.non_flag_tokens[0],
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Local-only planning bookkeeping "
                        f"'{base_cmd} {semantics.non_flag_tokens[0]}' "
                        f"excepted to {target_category.lower()} by config"
                    ),
                )

    # --- Step 3f: Consent-reducing operations are not T3 (anchored) ---
    # Within Gaia's own consent layer (`gaia approvals ...`), verbs that REDUCE
    # consent (revoke/reject/reject-all/clean) can only take back capability
    # already granted — they never grant anything and never reach outside the
    # local approval store, so they are not T3.  The one consent-GRANTING verb
    # (`approve`) is deliberately absent from CONSENT_REDUCING_SUBCOMMAND_
    # EXCEPTIONS and falls through to Step 4, where it stays MUTATIVE/T3.  That
    # asymmetry is the principle: granting capability needs consent, reducing it
    # does not.  Anchored to (base_cmd, group) so it never relaxes another CLI's
    # "revoke" (e.g. a cloud IAM revoke is a real remote mutation, still T3).
    # Dangerous flags are still scanned so a slip like `--force` re-gates.
    if semantics.non_flag_tokens:
        consent_group_key = (base_cmd, semantics.non_flag_tokens[0])
        consent_verb = (
            semantics.non_flag_tokens[1]
            if len(semantics.non_flag_tokens) > 1 else ""
        )
        reducing_verbs = CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS.get(consent_group_key)
        if reducing_verbs is not None and consent_verb in reducing_verbs:
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            if not dangerous_flags:
                return MutativeResult(
                    is_mutative=False,
                    category=CATEGORY_READ_ONLY,
                    verb=consent_verb,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Consent-reducing operation "
                        f"'{base_cmd} {semantics.non_flag_tokens[0]} {consent_verb}' "
                        f"only revokes/rejects capability already granted — "
                        f"not state-granting, so not T3"
                    ),
                )

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

        # Strip leading '+' macro prefix before verb lookup.
        # Some CLIs (notably `gws`) expose convenience macros with a '+' prefix
        # that wrap an underlying mutative API call:
        #   gws gmail +reply   -> sends a reply (equivalent to messages send)
        #   gws gmail +send    -> sends a new message
        #   gws gmail +search  -> list/search wrapper (read-only)
        # Without stripping '+', these tokens miss MUTATIVE_VERBS / READ_ONLY_VERBS
        # lookups and fall through to "safe by elimination", bypassing T3 approval.
        # Stripping here resolves the macro to its base verb so the taxonomy below
        # classifies it correctly.
        stripped_token = token.lstrip("+")

        # Tokens carrying path/scope/KV characters are argument values, not
        # CLI subcommands.  Verb splitting must skip them entirely (see
        # _is_subcommand_identifier for the principle).  Without this guard,
        # an argument like ``tests/test_install.py::TestStop`` is hyphen-split
        # or camelCase-split and the inner fragments match mutative verbs.
        is_subcmd_shape = _is_subcommand_identifier(stripped_token)

        # Split hyphenated tokens: "delete-stack" -> check "delete"
        # IMPORTANT: only apply hyphen-split at subcommand positions
        # (semantic_index <= 2).  At deeper positions (index >= 3), tokens are
        # typically argument values or slugs like "remove-live-state-from-context"
        # or "some-name-with-delete-in-it".  Splitting those produces false
        # positives -- the first fragment matches a mutative verb even though
        # the token is not a CLI subcommand.  The camelCase guard already applies
        # the same constraint (semantic_index == 1).
        if semantic_index <= 2 and "-" in stripped_token and is_subcmd_shape:
            candidate = stripped_token.split("-", 1)[0]
        else:
            candidate = stripped_token

        # Also check full token for exact matches (e.g., "force-delete")
        full_lower = stripped_token

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

            # Check unconditional tier exceptions: some (cli_family, verb)
            # combos are safe despite the verb being in MUTATIVE_VERBS.
            # Example: Gmail API "modify" only changes labels/flags.
            exception_key = (family, verb)
            if exception_key in CLI_VERB_TIER_EXCEPTIONS:
                target_category = CLI_VERB_TIER_EXCEPTIONS[exception_key]
                return MutativeResult(
                    is_mutative=False,
                    category=target_category,
                    verb=verb,
                    cli_family=family,
                    confidence="high",
                    reason=f"Verb '{verb}' for '{family}' CLI excepted to {target_category.lower()} by config",
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

        # --- Secondary check: camelCase splitting ---
        # "batchDelete" -> ["batch", "delete"] -> "delete" is in MUTATIVE_VERBS
        # Use the raw (original-case) token because semantic_head_tokens is
        # lowercased, which destroys the camelCase word boundaries that
        # split_camel_case relies on (regex: [a-z][A-Z]).
        #
        # IMPORTANT: only apply camelCase splitting at the subcommand position
        # (semantic_index == 1). At later positions, tokens are typically
        # argument values (paths, query strings, identifiers) and splitting
        # them produces false positives — e.g., the literal "SessionStart"
        # passed as a grep pattern would split to ["session", "start"] and
        # incorrectly trigger the mutative verb "start".
        #
        # Additional guard: if the raw token carries path/scope/KV characters
        # (e.g. ``tests/foo.py::TestStop``), it is an argument value -- the
        # camelCase fragment buried inside it is not a CLI subcommand even at
        # semantic_index == 1.  Without this guard, ``pytest
        # tests/test_install.py::TestStop`` splits ``TestStop`` and matches
        # ``stop``, misclassifying the whole command as MUTATIVE.
        raw_token = semantics.semantic_head_tokens_raw[semantic_index] if semantic_index < len(semantics.semantic_head_tokens_raw) else token
        camel_parts = split_camel_case(raw_token)
        if (
            semantic_index == 1
            and len(camel_parts) > 1
            and _is_subcommand_identifier(raw_token)
        ):
            for part in camel_parts:
                if part in MUTATIVE_VERBS:
                    override_key = (family, part)
                    if override_key in VERB_FLAG_READ_ONLY_OVERRIDES:
                        override_flags = VERB_FLAG_READ_ONLY_OVERRIDES[override_key]
                        if override_flags & frozenset(semantics.flag_tokens):
                            return MutativeResult(
                                is_mutative=False,
                                category=CATEGORY_READ_ONLY,
                                verb=part,
                                cli_family=family,
                                confidence="high",
                                reason=f"CamelCase verb '{part}' (from '{raw_token}') overridden to read-only by flag",
                            )
                    if (family, part) in CLI_VERB_TIER_EXCEPTIONS:
                        target_category = CLI_VERB_TIER_EXCEPTIONS[(family, part)]
                        return MutativeResult(
                            is_mutative=False,
                            category=target_category,
                            verb=part,
                            cli_family=family,
                            confidence="high",
                            reason=f"CamelCase verb '{part}' (from '{raw_token}') excepted to {target_category.lower()} by config",
                        )
                    dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
                    flag_detail = (
                        f" with dangerous flags {dangerous_flags}"
                        if dangerous_flags else ""
                    )
                    return MutativeResult(
                        is_mutative=True,
                        category=CATEGORY_MUTATIVE,
                        verb=part,
                        dangerous_flags=dangerous_flags,
                        cli_family=family,
                        confidence=confidence,
                        reason=f"CamelCase verb '{part}' (from '{raw_token}'){flag_detail}",
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

def _extract_python_payload(command: str, base_cmd: str) -> str:
    """Return the Python source string passed via ``-c "..."`` or ``-`` heredoc.

    The extractor looks for the canonical ``<python> -c <payload>`` shape,
    accepting single, double, or unquoted payloads.  When the interpreter is
    invoked with ``-`` (stdin) and a heredoc body, the body between
    ``<<MARKER`` and ``MARKER`` is returned instead.

    Empty string is returned when extraction fails -- the caller is expected
    to fall back to its regex layer in that case.
    """
    # Heredoc form: python3 - <<'PYEOF' ... PYEOF
    if "<<" in command:
        m = _re.search(
            r"<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*$",
            command, _re.DOTALL,
        )
        if m:
            return m.group(2)

    # Inline -c form, with quoted or unquoted payload.
    m = _re.search(
        r"(?:^|\s)" + _re.escape(base_cmd) + r"\b[^-]*?-c\s+(['\"])(.*?)\1",
        command, _re.DOTALL,
    )
    if m:
        return m.group(2)

    # Fallback: greedy unquoted -c payload up to end of line / chain operator.
    m = _re.search(r"-c\s+(\S.*)$", command, _re.DOTALL)
    if m:
        # Strip a trailing matched quote pair if shlex left them in place.
        return m.group(1).strip().strip("'\"")
    return ""


def _check_python_module_runner(
    base_cmd: str, semantics: "CommandSemantics",
) -> "Optional[MutativeResult]":
    """Re-dispatch ``python -m <pkg-mgr> ...`` as the package-manager command.

    Closes the AC-7 evasion (Brief 91): ``python3 -m pip install x`` is the same
    operation as ``pip install x``, but the verb scanner sees ``python3`` as the
    base command and the module name (``pip``) gets absorbed into flag_tokens as
    the value of ``-m`` -- so the command was classified only by whatever generic
    verb happened to follow, missing cases like ``python3 -m poetry add x``.

    This helper recognizes ``<python> [interp-flags] -m <pkg-mgr> <args...>``,
    rewrites it to ``<pkg-mgr> <args...>``, and re-runs ``detect_mutative_command``
    on the rewrite so the result is IDENTICAL to the direct CLI form.  The verb
    ``-m`` consumes the immediately following token as the module name (POSIX
    short-flag-with-value), which ``analyze_command`` already lands as
    ``flag_tokens[i+1]``; here we read the module directly from the raw token
    stream so the re-dispatch is robust to interpreter switches before ``-m``.

    Returns ``None`` when the command is not a recognized package-manager module
    invocation, so ordinary detection continues unchanged (``python3 -m pytest``,
    ``python3 -m http.server``, ``python3 -m pip`` with no args).
    """
    if base_cmd not in _PYTHON_INTERPRETERS:
        return None

    raw_tokens = semantics.tokens
    # Walk args after the interpreter; find the ``-m`` flag and the module token
    # it consumes.  Standalone interpreter switches (-u, -O, -E, ...) are skipped.
    module = None
    module_idx = None
    for i in range(1, len(raw_tokens)):
        if raw_tokens[i] == "-m":
            if i + 1 < len(raw_tokens):
                module = raw_tokens[i + 1]
                module_idx = i + 1
            break
        # A non-flag token before any ``-m`` means a script-file / positional
        # invocation, not ``-m`` module mode -- defer to the script-file lane.
        if not raw_tokens[i].startswith("-"):
            return None

    if module is None or module_idx is None:
        return None
    if module.lower() not in _PY_MODULE_PACKAGE_MANAGERS:
        return None

    # Rewrite ``python3 [flags] -m <pkg-mgr> <rest...>`` -> ``<pkg-mgr> <rest...>``
    # and re-classify.  ``shlex.quote`` keeps argument boundaries intact so a
    # rewritten command tokenizes the same way the direct CLI form would.
    import shlex
    rest = raw_tokens[module_idx + 1:]
    rewritten = " ".join(shlex.quote(t) for t in (module, *rest))
    inner = detect_mutative_command(rewritten)
    # Re-wrap the reason so the audit trail shows the re-dispatch explicitly,
    # but preserve the inner classification verbatim (category, verb, flags).
    return MutativeResult(
        is_mutative=inner.is_mutative,
        category=inner.category,
        verb=inner.verb,
        dangerous_flags=inner.dangerous_flags,
        cli_family=inner.cli_family,
        confidence=inner.confidence,
        reason=(
            f"'{base_cmd} -m {module}' re-dispatched as '{module}': {inner.reason}"
        ),
    )


def _resolve_script_argument(
    base_cmd: str, semantics: "CommandSemantics",
) -> "Optional[Tuple[str, str]]":
    """Identify a script-file invocation and return ``(path, lane)``.

    Two shapes are recognized:

    * ``<interpreter> <script-file>`` -- the first positional argument after a
      known interpreter, whose lane (``"python"`` or ``"shell"``) is decided by
      the interpreter, not the filename.
    * ``./script`` / ``path/to/script`` -- a direct executable invocation whose
      lane is inferred from the file extension via ``_SHEBANG_EXT_LANES``.

    Returns ``None`` when the command is not a script-file invocation, so the
    caller continues with ordinary verb detection.
    """
    raw_tokens = semantics.tokens
    if not raw_tokens:
        return None

    if base_cmd in _SCRIPT_FILE_INTERPRETERS:
        lane = "python" if base_cmd in _PYTHON_INTERPRETERS else "shell"
        defer_flags = _INTERP_NON_SCRIPT_VALUE_FLAGS.get(base_cmd, frozenset())
        # Walk the args (original casing) and return the first true positional
        # -- the script file.  Standalone interpreter switches (-u, -O, -x, ...)
        # are skipped; flags that consume the next token as inline code or a
        # module name (-c, -m, -e, ...) mean there is NO script file, so we
        # defer to the inline path / verb scanner by returning None.  The stdin
        # sentinel "-" likewise defers (heredoc path owns it).
        for token in raw_tokens[1:]:
            if token == "-":
                return None
            if token in defer_flags:
                return None
            if token.startswith("-"):
                continue
            return (token, lane)
        return None

    # Direct ``./script`` or ``path/script.ext`` invocation: the executable
    # token IS the script.  Use the original-case token so the path resolves
    # correctly on case-sensitive filesystems.
    invoked = raw_tokens[0]
    if "/" in invoked:
        for ext, lane in _SHEBANG_EXT_LANES.items():
            if invoked.endswith(ext):
                return (invoked, lane)
    return None


def _read_script_content(path: str) -> "Optional[str]":
    """Read a bounded prefix of a script file for content classification.

    Returns ``None`` when the path cannot be resolved to a readable regular
    file -- the caller treats that as the conservative (mutative) case, because
    an interpreter pointed at an un-inspectable payload could do anything.
    """
    import os

    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(_MAX_SCRIPT_READ_BYTES)
    except (OSError, ValueError):
        return None


def _check_script_file(
    command: str, base_cmd: str, family: str, semantics: "CommandSemantics",
) -> "Optional[MutativeResult]":
    """Classify ``<interpreter> <file>`` / ``./script`` by the file's content.

    Closes the file-argument evasion: the verb scanner only sees the filename,
    so a script that deletes files or calls the network would otherwise pass as
    safe by elimination.  Classification is by REAL invocation, mirroring the
    inline ``-c`` path -- an analytic Python script with no mutative calls and a
    read-only shell script both stay non-mutative, so the existing
    overbroad-classification complaint is not reintroduced.

    Returns ``None`` when the command is not a script-file invocation.
    """
    resolved = _resolve_script_argument(base_cmd, semantics)
    if resolved is None:
        return None

    script_path, lane = resolved
    content = _read_script_content(script_path)
    if content is None:
        # Conservative default: an interpreter invoked on a missing or
        # unreadable file is treated as mutative.  We cannot prove the payload
        # is safe, and an un-inspectable executable payload requires consent.
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="script-file-unreadable",
            cli_family=family,
            confidence="medium",
            reason=(
                f"Interpreter '{base_cmd}' invoked on script "
                f"'{script_path}' that is not a readable file -- cannot "
                f"verify the payload, requiring approval (conservative default)"
            ),
        )

    if lane == "python" and _analyze_python_inline is not None:
        ast_result = _analyze_python_inline(content)
        if ast_result.is_dangerous:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=ast_result.label,
                cli_family=family,
                confidence="high",
                reason=(
                    f"Script '{script_path}' invokes {ast_result.detail} "
                    f"({ast_result.category})"
                ),
            )
        if not ast_result.parse_failed:
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb="script-file",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"Python script '{script_path}' has no mutative invocation "
                    f"(AST analysis)"
                ),
            )
        # parse_failed -> fall through to the shell/regex lane below.

    return _classify_script_content_by_regex(content, script_path, family)


def _classify_script_content_by_regex(
    content: str, script_path: str, family: str,
) -> MutativeResult:
    """Classify shell / non-Python script content via the existing regex layer.

    No AST parser is vendored for bash, node, ruby, perl, or php (see
    ``inline_ast_analyzer`` docstring), so content is scanned line-by-line with
    the same two engines the inline path uses:

    * ``is_blocked_command`` -- catches permanently-blocked destructive lines
      (``rm -rf /``, ``dd of=/dev/sda``, ...).
    * ``detect_mutative_command`` -- the CLI-agnostic mutative engine, reused
      per logical line so a ``kubectl apply`` or ``curl -X POST`` inside the
      file is detected the same way it would be on the command line.

    This reuses the existing layers rather than introducing a new parser, per
    the design constraint.
    """
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if _is_blocked_command is not None:
            blocked = _is_blocked_command(line)
            if blocked.is_blocked:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb="script-blocked-cmd",
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Script '{script_path}' contains blocked command: "
                        f"{blocked.category}"
                    ),
                )

        line_result = detect_mutative_command(line)
        if line_result.is_mutative:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=line_result.verb,
                dangerous_flags=line_result.dangerous_flags,
                cli_family=family,
                confidence=line_result.confidence,
                reason=(
                    f"Script '{script_path}' line is mutative: "
                    f"{line_result.reason}"
                ),
            )

    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="script-file",
        cli_family=family,
        confidence="medium",
        reason=f"Script '{script_path}' has no mutative or blocked line",
    )


def _check_inline_code(command: str, base_cmd: str, family: str, skip_length_check: bool = False) -> MutativeResult:
    """Check inline code for dangerous patterns.

    Pipeline:

    Layer 1: Extract string literals from inline code and check them against
             ``blocked_commands`` (catches embedded shell commands like ``rm -rf /``).

    Layer 2 (Python only): Parse the payload with ``ast.parse`` and walk
             ``Call`` nodes.  ``import subprocess; print('hi')`` is **not**
             flagged because ``subprocess.run`` is never invoked, while
             ``subprocess.run([...])`` is.  When parsing fails we degrade to
             Layer 2b.

    Layer 2b: Universal regex scan for dangerous API keywords (used for non-
             Python interpreters and as the fallback for un-parseable Python).

    Layer 3: Heuristic safety classification (length, sensitive paths, encoding).

    Args:
        command: Full raw command string.
        base_cmd: The interpreter (e.g., ``python3``, ``node``, ``ruby``).
        family: CLI family hint.

    Returns:
        MutativeResult -- MUTATIVE if any layer triggers, else safe.
    """
    # ---- Layer 1: Extract string literals → check against blocked_commands ----
    if _is_blocked_command is not None:
        embedded_strings = _extract_embedded_shell_commands(command)
        for literal in embedded_strings:
            blocked = _is_blocked_command(literal)
            if blocked.is_blocked:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb="embedded-blocked-cmd",
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Inline code contains blocked shell command in "
                        f"string literal: {blocked.category}"
                    ),
                )

    # ---- Layer 2 (Python only): AST-based invocation analysis ----
    if base_cmd in _PYTHON_INTERPRETERS and _analyze_python_inline is not None:
        payload = _extract_python_payload(command, base_cmd)
        if payload:
            ast_result = _analyze_python_inline(payload)
            if ast_result.is_dangerous:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=ast_result.label,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Inline Python invokes {ast_result.detail} "
                        f"({ast_result.category})"
                    ),
                )
            if not ast_result.parse_failed:
                # AST parsed cleanly and found nothing dangerous: trust it
                # and skip the regex layer (which would false-positive on
                # bare ``import subprocess`` or quoted strings).  Fall
                # through to Layer 3 heuristics for length only.
                return _layer3_length_check(
                    command, base_cmd, family, skip_length_check,
                )
            # parse_failed=True -> fall through to regex layer 2b below.

    # ---- Layer 2b: Universal dangerous API keyword patterns ----
    for pattern, label, category in _UNIVERSAL_DANGEROUS_PATTERNS:
        if pattern.search(command):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=label,
                cli_family=family,
                confidence="medium",
                reason=f"Inline code contains dangerous pattern: {label} ({category})",
            )

    # ---- Layer 3: Heuristic safety classification (suspicious patterns + length) ----
    return _layer3_full_check(command, base_cmd, family, skip_length_check)


def _layer3_full_check(
    command: str, base_cmd: str, family: str, skip_length_check: bool,
) -> MutativeResult:
    """Run heuristic checks (3a suspicious patterns + 3b length).

    Used as the tail for non-Python interpreters and for Python payloads
    whose AST parse failed -- the regex layer needs the suspicious-pattern
    fallback to keep network/encoding heuristics live.
    """
    # 3a: Check for suspicious indicators (sensitive paths, encoding, IPs)
    for pattern, label in _SUSPICIOUS_HEURISTICS:
        if pattern.search(command):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=f"heuristic-{label}",
                cli_family=family,
                confidence="low",
                reason=f"Inline code flagged by heuristic: {label}",
            )

    return _layer3_length_check(command, base_cmd, family, skip_length_check)


def _layer3_length_check(
    command: str, base_cmd: str, family: str, skip_length_check: bool,
) -> MutativeResult:
    """Run only the length heuristic (3b).

    Used when AST analysis cleared a Python payload -- we trust the AST for
    semantic safety but still flag suspiciously long inline code.
    """
    code_portion = command
    cli_flag_tokens = _INLINE_CODE_MAP.get(base_cmd, frozenset())
    for flag in cli_flag_tokens:
        idx = command.find(f" {flag} ")
        if idx != -1:
            code_portion = command[idx + len(flag) + 2:]
            break

    if not skip_length_check and len(code_portion) > MAX_NORMAL_INLINE_LENGTH:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="heuristic-long-code",
            cli_family=family,
            confidence="low",
            reason=(
                f"Inline code is unusually long ({len(code_portion)} chars > "
                f"{MAX_NORMAL_INLINE_LENGTH} limit)"
            ),
        )

    # ---- No layers triggered -- safe inline code ----
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="inline-code",
        cli_family=family,
        confidence="medium",
        reason=f"Inline code ({base_cmd}) with no dangerous patterns",
    )


def _find_first_non_flag(tokens: Union[List[str], tuple]) -> tuple:
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


