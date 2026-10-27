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
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

try:
    import tomllib
except ImportError:
    tomllib = None

from .approval_messages import build_t3_approval_instructions
from .canonical_path import is_launcher, program_identity
from .cli_aliases import as_wrapped_cli, declared_wrapper
from .forge_shapes import change_request_action
from .command_semantics import (
    BOOLEAN_SHORT_FLAGS,
    CommandSemantics,
    absorbs_next_token,
    analyze_command,
    is_boolean_short_flag,
    tokenize_command,
    _is_flag,
    _is_short_value_flag,
)
from .nested_executors import nested_run, opens_session, reads_local_state, with_target_flags, writes_or_sends
from .program_heredoc import heredoc_program
from .shell_substitution import extract_substitutions_truncated

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
    from .inline_ast_analyzer import parse_finds_no_effect as _parse_finds_no_effect
    from .inline_ast_analyzer import runs_code_string as _runs_code_string
    from .inline_ast_analyzer import loaded_modules as _loaded_modules
except ImportError:  # pragma: no cover -- defensive
    _analyze_python_inline = None
    _loaded_modules = None
    _parse_finds_no_effect = None
    _runs_code_string = None
    logging.getLogger(__name__).warning(
        "inline_ast_analyzer.analyze_python_inline not importable; "
        "AST-based Python inline analysis disabled (falling back to regex)"
    )

try:
    from .source_lexer import (
        spec_for_script as _spec_for_script,
        strip_source as _strip_source,
    )
except ImportError:  # pragma: no cover -- defensive
    _spec_for_script = None
    _strip_source = None
    logging.getLogger(__name__).warning(
        "source_lexer not importable; comment/string-aware scanning of "
        "JS source files disabled (falling back to naive line scan)"
    )

logger = logging.getLogger(__name__)


# ============================================================================
# Category Constants
# ============================================================================

CATEGORY_MUTATIVE = "MUTATIVE"
CATEGORY_SIMULATION = "SIMULATION"
CATEGORY_READ_ONLY = "READ_ONLY"
CATEGORY_UNKNOWN = "UNKNOWN"
_READING_CATEGORIES = frozenset({CATEGORY_READ_ONLY, CATEGORY_SIMULATION})


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
        guidance: The non-mutating way to reach the same outcome, when one
            exists. Empty for every classification that has no such equivalent.
        rule: The verdict rule a lane settled itself; empty when derived from the verb.
    """
    is_mutative: bool = False
    category: str = CATEGORY_UNKNOWN
    verb: str = ""
    dangerous_flags: Tuple[str, ...] = ()
    cli_family: str = "unknown"
    confidence: str = "low"
    reason: str = ""
    guidance: str = ""
    rule: str = ""



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
    "stop", "shutdown", "halt", "abort", "up", "seal", "unseal",
    # Movement / transfer
    "move", "rename", "copy", "sync",
    "import", "export", "migrate", "transfer",
    # Attachment
    # NOTE: "link" removed -- false positive in shell variable names (e.g., "for link in ...").
    #       The `ln` command is already covered as a COMMAND_ALIAS.
    "attach", "bind", "connect", "mount",
    # Execution
    # NOTE: "run" removed -- safe by elimination (e.g., docker run is common dev workflow)
    # "replay" re-executes a previously recorded command (e.g. `gaia approvals
    # replay <id>` re-runs the sealed_payload of an already-used approval via
    # `subprocess.run(cmd, shell=True)`, including commands that were
    # originally T3). Global, not a `gaia`-specific exception: replaying a
    # stored command has the exact same effect as running it the first time,
    # so it must classify at least as mutative as the taxonomy already treats
    # "execute"/"invoke". `--dry-run` (which only prints, never executes)
    # still short-circuits to non-mutative via the SIMULATION_FLAGS check in
    # Step 3, which runs before this table is ever consulted.
    "exec", "execute", "invoke", "trigger", "send", "reply", "replay",
    # Remote execution entry points: re-run a finished run, fire a dispatch
    # event, stop a run in flight. `run` itself stays out (see above); these
    # three name the effect, so they need no per-binary anchor.
    "rerun", "dispatch", "cancel",
    # Git operations
    # NOTE: "stash" removed -- safe by elimination (local-only operation)
    # NOTE: "commit" removed -- local-only operation, trust system
    "push", "merge", "rebase",
    "rollback",
    # Access control
    "grant", "assign", "revoke", "login", "select", "get-credentials",
    # Reconciliation
    "reconcile", "rsync",
    # Deletion / removal (approvable via nonce -- blocked_commands.py catches
    # the truly destructive patterns like "delete namespace", "delete-vpc", etc.)
    # NOTE: "cleanup" sits alongside "clean" rather than relying on hyphen-split
    # collapsing it to "clean" -- there is no hyphen in "cleanup" to split, and
    # the CLI subcommand token IS the literal word "cleanup" (`gaia cleanup`,
    # no flags, removes CLAUDE.md/settings.json/symlinks/plugin markers and
    # applies retention deletions with no prompt). `--dry-run` still
    # short-circuits to non-mutative via SIMULATION_FLAGS in Step 3, which
    # runs before this table is consulted.
    "delete", "destroy", "remove", "drop", "purge", "wipe", "clean", "cleanup",
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

COMPOUND_HEAD_MUTATIVE_VERBS: FrozenSet[str] = frozenset({
    "run", "add", "use", "report", "associate", "disassociate", "schedule",
})
GROUP_PATH_HYPHEN_DEPTH = 3
GROUP_READ_WORDS: FrozenSet[str] = frozenset({"auth", "config"})
LIVE_SIMULATION_VERBS: Dict[str, FrozenSet[str]] = {"test": frozenset({"iac", "k8s"})}
VERB_DESTROYING_FLAGS: Dict[str, FrozenSet[str]] = {
    "down": frozenset({"-v", "--volumes", "--rmi"}),
    "prune": frozenset({"-a", "--all", "--volumes"}),
}
APPLYING_PROGRAMS: Dict[str, FrozenSet[str]] = {
    "ansible-playbook": frozenset({"--check", "-C", "--syntax-check", "--list-tasks", "--list-hosts", "--list-tags"}),
}
LOCAL_CACHE_PATHS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "helm": (("repo", "update"),),
    "apt": (("update",),),
    "apt-get": (("update",),),
    "act": (("push",),),
}
LOCAL_CACHE_SAFE_FLAGS: FrozenSet[str] = frozenset({"-q", "-qq", "--quiet", "-y", "--yes"})
_PUSHING_OUTPUT_RE = re.compile(r"(?:^|,)(?:type=registry|push=true)(?:,|$)")


def _names_operation(token: str) -> bool:
    """Return True when a head token, whole or by its head fragment, is a word of the effect lexicon."""
    head = token.split("-", 1)[0]
    tables = (MUTATIVE_VERBS, SIMULATION_VERBS, READ_ONLY_VERBS)
    return head in COMPOUND_HEAD_MUTATIVE_VERBS or any(word in table for word in (token, head) for table in tables)


def _hyphen_candidate(token: str, is_group: bool = False) -> Tuple[str, bool]:
    """Return the fragment naming a hyphenated subcommand's effect (only the head for a group name) and whether it is a compound-only change verb."""
    head, *rest = token.split("-")
    if not rest or head in SIMULATION_VERBS or head in MUTATIVE_VERBS or head in READ_ONLY_VERBS:
        return head, False
    if head in COMPOUND_HEAD_MUTATIVE_VERBS and rest != ["all"]:
        return head, True
    if not is_group:
        for fragment in rest:
            if fragment in MUTATIVE_VERBS:
                return fragment, False
    return head, False


def _head_token_mutates(token: str) -> bool:
    """Return True when a head token, whole or by its effect fragment, names a change."""
    candidate, compound_change = _hyphen_candidate(token) if _is_subcommand_identifier(token) else (token, False)
    return compound_change or token in MUTATIVE_VERBS or candidate in MUTATIVE_VERBS


def _later_head_mutates(
    head_tokens: Tuple[str, ...], index: int, hyphen_depth: int, values: FrozenSet[int] = frozenset(),
) -> bool:
    """Return True when a command word after *index* names a change: any whole verb, or a compound within the path depth."""
    return any(
        position not in values
        and (token in MUTATIVE_VERBS or (position <= hyphen_depth and _head_token_mutates(token)))
        for position, token in enumerate(head_tokens[index + 1:], start=index + 1)
    )


def _option_value_positions(semantics: "CommandSemantics") -> FrozenSet[int]:
    """Return the semantic head positions that hold an option's value, never a command word."""
    positions, index, previous = set(), 0, ""
    for token in semantics.args:
        expected = semantics.non_flag_tokens_raw
        if index < len(expected) and token == expected[index]:
            index += 1
            if previous.startswith("-") and "=" not in previous and (previous.startswith("--") or len(previous) == 2):
                positions.add(index)
        previous = token
    return frozenset(positions)


# Global options of the cloud CLIs and how many words each takes. They hold no command word, so they are read
# away before the command path is classified; an option missing here may take a value, so it is read both ways.
_CLOUD_GLOBAL_OPTIONS: Dict[str, Dict[str, int]] = {
    "aws": {
        "--profile": 1, "--region": 1, "--output": 1, "--query": 1, "--endpoint-url": 1, "--color": 1,
        "--ca-bundle": 1, "--cli-read-timeout": 1, "--cli-connect-timeout": 1, "--cli-binary-format": 1,
        "--debug": 0, "--no-verify-ssl": 0, "--no-paginate": 0, "--no-sign-request": 0, "--no-cli-pager": 0,
    },
    "az": {"--subscription": 1, "--output": 1, "-o": 1, "--query": 1, "--debug": 0, "--verbose": 0, "--only-show-errors": 0},
    "gcloud": {
        "--project": 1, "--account": 1, "--configuration": 1, "--format": 1, "--verbosity": 1,
        "--billing-project": 1, "--trace-token": 1, "--quiet": 0, "--log-http": 0,
    },
}


_CLOUD_SESSION_WORDS: FrozenSet[str] = frozenset({"ssh", "scp", "connect", "tunnel"})


def _cloud_command_readings(base_cmd: str, tokens: List[str]) -> Tuple[List[str], bool]:
    """Return the command without its global options and whether that reading is exact; an unknown option before the operation word is read both ways."""
    arity = _CLOUD_GLOBAL_OPTIONS.get(base_cmd)
    start = next((i for i, t in enumerate(tokens) if program_identity(t) == base_cmd), None) if arity else None
    if start is None:
        return [], True
    kept, rest, index, operation_seen = tokens[:start + 1], tokens[start + 1:], 0, False
    while index < len(rest):
        token = rest[index]
        name = token.split("=", 1)[0]
        if token == "--":
            kept += rest[index:]
            break
        if name in arity:
            index += 1 + (0 if "=" in token else arity[name])
            continue
        if token.startswith("-") and "=" not in token and not operation_seen:
            after = rest[index + 1:]
            return [shlex.join(kept + after), shlex.join(kept + after[1:])], False
        operation_seen = operation_seen or (not token.startswith("-") and (
            _names_operation(token) or _is_known_verb(token) or token in _CLOUD_SESSION_WORDS
        ))
        kept.append(token)
        index += 1
    return ([shlex.join(kept)] if len(kept) < len(tokens) else []), True


# Reads that take the resource they read as an operand: a config after one is that operand, not the verb.
_OPERAND_READ_VERBS: FrozenSet[str] = frozenset({"get", "list", "describe", "show", "read", "view", "inspect", "ls"})


def _configure_position(head_tokens: Tuple[str, ...], values: FrozenSet[int]) -> int:
    """Return where a group-path command ends on the verb ``config`` (az webapp log config), or 0 when a read names it."""
    for position, token in enumerate(head_tokens):
        if position in values:
            continue
        if token.split("-", 1)[0] in _OPERAND_READ_VERBS:
            return 0
        if token == "config" and position >= 2:
            later = [t for p, t in enumerate(head_tokens[position + 1:], start=position + 1) if p not in values]
            return 0 if any(_names_operation(t) for t in later) else position
    return 0


def _local_cache_refresh(base_cmd: str, semantics: "CommandSemantics") -> str:
    """Return the head path of a command that only refreshes a local cache and carries no option that loads code, or ""."""
    if not set(semantics.flag_tokens) <= LOCAL_CACHE_SAFE_FLAGS:
        return ""
    for path in LOCAL_CACHE_PATHS.get(base_cmd, ()):
        if semantics.non_flag_tokens == path:
            return " ".join(path)
    return ""


def _output_pushes(tokens: Tuple[str, ...]) -> bool:
    """Return True when an output option sends the build to a registry."""
    for flag, value in zip(tokens, tokens[1:]):
        if flag in ("-o", "--output") and _PUSHING_OUTPUT_RE.search(value):
            return True
    return any(t.startswith("--output=") and _PUSHING_OUTPUT_RE.search(t[len("--output="):]) for t in tokens)


def _simulation_flag_holds(tokens: Tuple[str, ...]) -> bool:
    """Return True when a simulation flag is present and no later dry-run spelling turns it off."""
    lowered = [t.lower() for t in tokens]
    dry_runs = [t for t in lowered if t.startswith(("--dry-run", "--dryrun"))]
    if dry_runs and dry_runs[-1] not in SIMULATION_FLAGS:
        return False
    return any(t in SIMULATION_FLAGS for t in lowered)


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
    "mv",         # local-only: renames/moves a tracked path in the working tree
                  # and index -- no remote side effects, fully reversible (a
                  # rename recoverable via `git mv` back or `git checkout`).
                  # Purely local like `git add`; was a false T3 because the verb
                  # scanner matched the standalone `mv` filesystem verb. An
                  # ALWAYS-dangerous flag (`--force`) still escalates via
                  # _scan_dangerous_flags. NOTE: `rm` is deliberately NOT here --
                  # `git rm` deletes working-tree files (data loss on uncommitted
                  # content, `-r` recursion) and stays T3 by design.
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
    "grep",        # read-only search: its pattern is data, never a verb to run
                   # (except -O, see git_grep_opens_pager)
    "merge-tree",
})


def git_grep_opens_pager(tokens: Tuple[str, ...]) -> bool:
    """Return True when a ``git grep`` hands its matches to a command (``-O<cmd>``).

    ``--open-files-in-pager`` runs the named program over every matching file,
    the one spelling of the search that executes something. A short bundle
    carrying ``O`` counts, erring toward gating a search rather than freeing a run.
    """
    return any(
        token.startswith("--open-files-in-pager")
        or (token.startswith("-") and not token.startswith("--") and "O" in token)
        for token in tokens
    )

_GIT_OUTPUT_LONG_OPTIONS: Tuple[str, ...] = ("output", "output-directory")
_GIT_SHORT_O_WRITES: FrozenSet[str] = frozenset({"format-patch", "archive", "bugreport", "diagnose"})
_GIT_WRITES_CWD_BY_DEFAULT: FrozenSet[str] = frozenset({"format-patch", "bugreport", "diagnose"})


def _git_output_targets(tokens: tuple, subcommand: str) -> "List[str]":
    """Return every file or directory a git command writes by option (unique long prefixes included), plus ``.`` for a report or patch written without one."""
    split = _git_subcommand_split(tokens)
    args = split[2] if split else list(tokens[1:])
    targets: List[str] = []
    for index, token in enumerate(args):
        following = args[index + 1] if index + 1 < len(args) else ""
        if token == "--":
            break
        name, has_value, value = token[2:].partition("=")
        if token.startswith("--") and len(name) >= 3 and any(o.startswith(name) for o in _GIT_OUTPUT_LONG_OPTIONS):
            targets.append(value if has_value else following)
        elif subcommand in _GIT_SHORT_O_WRITES and token.startswith("-") and not token.startswith("--") and "o" in token:
            targets.append(token.split("o", 1)[1] or following)
    if subcommand in _GIT_WRITES_CWD_BY_DEFAULT and not targets and "--stdout" not in args:
        targets.append(".")
    return targets


def _git_output_in_owned_entry(tokens: tuple, targets: "List[str]", caller: "Optional[LocalWorkCaller]") -> bool:
    """True when each output path, resolved from the directory git runs in, lies in the caller's own scratch entry."""
    split = _git_subcommand_split(tokens)
    if split is None or caller is None:
        return False
    base = _git_repo_dir(split[0], caller.cwd)
    for target in targets:
        real = _resolve_local_work_path(target, base)
        if real is None or _scratch_entry(real) is None or _local_work_root(real, caller, allow_top=True) is None:
            return False
        if _shares_inode(real):
            return False
    return True


# Flags that turn a local-safe branch switch into a reset of an EXISTING branch:
# `-B`/`-C` create the branch, or repoint it when it already exists, so the
# commits only it reached become unreachable. The lowercase `-b`/`-c` refuse an
# existing name and stay free. Checked before GIT_LOCAL_SAFE_SUBCOMMANDS.
_GIT_BRANCH_RESET_FLAGS: Dict[str, FrozenSet[str]] = {
    "checkout": frozenset({"-B"}),
    "switch": frozenset({"-C", "--force-create"}),
}


# ============================================================================
# Verb+Flag Overrides (mutative verb downgraded to READ_ONLY by a flag)
# ============================================================================
# Map of (cli_family, verb) -> frozenset of flag tokens that override to READ_ONLY.
# Checked AFTER a mutative verb is found but BEFORE returning the MUTATIVE result.

VERB_FLAG_READ_ONLY_OVERRIDES: Dict[Tuple[str, str], FrozenSet[str]] = {
    # Generic flag-set override: a mutative verb downgraded to READ_ONLY when a
    # listing flag is present. For a whole-token `git tag`, the dedicated
    # discriminator `_classify_git_tag` below (wired at the MUTATIVE_VERBS
    # branch) takes precedence and handles the full dual-mode split (bare
    # listing, --points-at/--contains/--merged, create-by-name, -a/-d/-f). This
    # entry additionally backstops the camelCase-split path (a token like
    # `tagCreate` -> parts [tag, create]) where the discriminator is not
    # invoked, and keeps the generic override mechanism exercised.
    ("git", "tag"): frozenset({"-l", "--list"}),
}


# ============================================================================
# git tag read-vs-write discriminator
# ============================================================================
# `git tag` is dual-mode: with no tag NAME and no mutation flags it LISTS tags
# (read-only, T0); with a positional tag NAME to create or a
# create/delete/force/sign/annotate flag it MUTATES refs (T3). The generic
# MUTATIVE_VERBS entry ("tag") classified EVERY `git tag ...` as T3, so the
# listing forms (`git tag`, `git tag -l`, `git tag --points-at HEAD`,
# `git tag --contains <sha>`) demanded approval needlessly. This mirrors the
# read-vs-write-by-flags refinement already applied to PowerShell cmdlets.
#
# git selects the tag operating mode from the flags, mirrored here:
#   MUTATIVE (create/delete/verify-force): any create/delete/force/sign/
#            annotate/message flag is present (-a/--annotate, -s/--sign,
#            -d/--delete, -f/--force, -m/--message, -F/--file, -u/--local-user,
#            -e/--edit, --cleanup).
#   READ-ONLY (LIST mode): any listing flag is present (-l/--list, -n,
#            --contains/--no-contains, --points-at, --merged/--no-merged,
#            --sort, --format, --column/--no-column, -i/--ignore-case,
#            -v/--verify). In list mode a trailing positional is a filter
#            PATTERN (`git tag -l "v*"`), never a tag name to create.
#   Otherwise (no recognized flags): a trailing positional is a tag NAME to
#            create -> MUTATIVE (`git tag v1.2.3`); bare `git tag` lists -> READ-ONLY.

GIT_TAG_MUTATIVE_FLAGS: FrozenSet[str] = frozenset({
    "-a", "--annotate",
    "-s", "--sign",
    "-d", "--delete",
    "-f", "--force",
    "-m", "--message",
    "-F", "--file",
    "-u", "--local-user",
    "-e", "--edit",
    "--cleanup",
})

# Presence of any of these puts `git tag` in LIST/VERIFY mode: positionals are
# filter patterns or a tag to inspect, never a tag name to create. Long flags
# carrying an inline value (`--sort=...`, `--format=...`) still emit their bare
# key into flag_tokens (see _normalize_flag_token), so the bare forms suffice.
GIT_TAG_LISTING_FLAGS: FrozenSet[str] = frozenset({
    "-l", "--list",
    "-n",
    "--contains", "--no-contains",
    "--points-at",
    "--merged", "--no-merged",
    "--sort",
    "--format",
    "--column", "--no-column",
    "-i", "--ignore-case",
    "-v", "--verify",
})


def _classify_git_tag(semantics: CommandSemantics) -> str:
    """Return CATEGORY_MUTATIVE or CATEGORY_READ_ONLY for a `git tag ...` command.

    See the block comment above for the discriminator. Purely a function of the
    parsed flag/non-flag tokens.
    """
    flags = frozenset(semantics.flag_tokens)

    # Signal 1: an explicit create/delete/force/sign/annotate/message flag.
    if flags & GIT_TAG_MUTATIVE_FLAGS:
        return CATEGORY_MUTATIVE

    # Signal 2: any listing/verify flag -> LIST mode; positionals are patterns.
    if flags & GIT_TAG_LISTING_FLAGS:
        return CATEGORY_READ_ONLY

    # Signal 3: no recognized flags. A positional after the "tag" subcommand is
    # a tag NAME to create; bare `git tag` merely lists.
    positionals_after_tag = max(0, len(semantics.non_flag_tokens) - 1)
    if positionals_after_tag > 0:
        return CATEGORY_MUTATIVE

    return CATEGORY_READ_ONLY


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
    # alter message content, send mail, or delete anything.  Safe as T0 unless
    # it may remove INBOX (archive), which _may_archive_gmail carves back out.
    ("workspace", "modify"): CATEGORY_READ_ONLY,
    ("docker", "up"): CATEGORY_READ_ONLY,
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
    # `gaia notifications`: reversible local rows that start no process, and the
    # group has no delete/purge verb, so the deny-verb guard exempts all of it.
    ("gaia", "notifications"): CATEGORY_READ_ONLY,
    # `gaia login start` only runs logins that keep the active account
    # (`gcloud auth login --no-activate`); the consent is the user's own, given
    # in the browser.
    ("gaia", "login"): CATEGORY_READ_ONLY,
    # `gaia contract <verb>` (init/set/add/fill/finalize/view/validate): the
    # by-value agent_contract_handoff draft store under
    # `data_dir()/contract_drafts/` — a subagent's own turn-scoped draft, edited
    # field-by-field across the turn and finalized once (see the
    # `agent-protocol` skill's "Building the contract" section). Without this
    # exemption `set`/`add` trip the generic MUTATIVE_VERBS scan (`set` and,
    # were "add" not already removed for the git-add false-positive, `add` too)
    # on EVERY field write, making the by-value flow impractical — a subagent
    # would hit an approval gate on every `gaia contract set`. Mirrors brief/ac/
    # plan/task/notifications exactly: reversible, local-only, no external side
    # effects, no un-delete needed because there is no destructive verb in this
    # group (no `gaia contract delete` — a draft is superseded by `finalize`,
    # never destroyed), so the global deny-verb guard leaves every contract
    # verb exempt.
    ("gaia", "contract"): CATEGORY_READ_ONLY,
    # `gaia memory <verb>` (add/append/reclassify/link/checkpoint/search/
    # show/list/stats/conflicts): curated-memory bookkeeping in gaia.db --
    # reversible, local-only, no external effects, exactly like brief/ac/plan.
    # The verb scan reads the ATOM'S OWN TEXT, so without this a payload that
    # is itself a mutative word (`--body apply`) gated the write on the content
    # of the note. An atom body is data -- no verb spelled inside it executes.
    # Three forms stay T3: `delete`, through the global deny-verb guard, and
    # the in-place rewrite `add --replace` and the edge removal
    # `link --delete`, anchored in COMMAND_PATH_MUTATIVE_UPGRADES, which is
    # consulted before this table.
    # The orthogonal subagent_memory_write_guard still denies memory
    # WRITES from a dispatched subagent regardless of tier -- this exception
    # changes the tier, never who is allowed to write.
    ("gaia", "memory"): CATEGORY_READ_ONLY,
    # `gaia worktree <verb>` (create/list/show/release): the host-agnostic
    # door onto Gaia's own agentic-worktree machinery (gaia.worktree,
    # gaia.retention.worktree_reclaim/worktree_collector). Without this
    # exception `create` and `release` (both in MUTATIVE_VERBS generically)
    # would trip the Step 4 verb scan and demand T3 -- on the OUTER `gaia`
    # invocation, never on the `git worktree add`/`remove` subprocess the CLI
    # runs internally, which never passes through this hook at all (it is a
    # subprocess of the already-classified CLI process, not a separate Bash
    # tool call). `create` is bounded by construction: the worktree is born
    # under the workspace's git-ignored .project-worktrees root, its only
    # network effect is fetching the remote's default branch into a
    # remote-tracking ref, and its identity is stamped into the git lock's
    # reason so the reclaim machinery can always find and account for it.
    # `release` can never destroy
    # uncaptured work -- reclaim_worktree deposits a dirty worktree's full
    # diff as evidence BEFORE anything about it changes, and only ever calls
    # `git worktree remove` on a worktree already confirmed clean (see
    # gaia.retention.worktree_reclaim's module docstring). That call is
    # forced ONLY to override an older worktree's untracked in-tree
    # `.gaia-worktree.json` -- the one case a fresh, independently-recomputed content check
    # (`_exempt_metadata_filename`) has already proven carries no agent work
    # -- and stays unforced otherwise; `--force` never overrides anything
    # this module has not itself just verified is safe. Both `create` and
    # `release` are therefore reversible-by-design, exactly like the brief/ac/plan/
    # task/notifications/contract/memory groups above, and there is
    # no destructive verb in this group for the global deny-verb guard to
    # re-gate (`create`/`list`/`show`/`release` all miss
    # COMMAND_SUBCOMMAND_EXCEPTION_DENY_VERBS).
    ("gaia", "worktree"): CATEGORY_READ_ONLY,
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

# Per-group deny verbs that live one level DEEPER than
# COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS -- for a subcommand GROUP that itself
# nests its own action verb (`gaia task gate <add|list|remove|set-status>`).
# The two dicts above only ever inspect non_flag_tokens[1] (the token right
# after the exempted group, e.g. "gate"); they never look at
# non_flag_tokens[2] (the nested action, e.g. "remove"), so a destructive verb
# hiding one level deeper than the group token passed through as bookkeeping.
# `gaia task gate remove` hard-deletes a `task_gates` row (see
# gaia.store.writer.remove_gate_from_task) -- an irreversible destruction of a
# verification record, not reversible status bookkeeping like `gate
# set-status`, so it must stay T3 exactly like `gaia task remove` above.
#
# Anchored to the SPECIFIC (base_cmd, group, subgroup) triple rather than a
# generic "always check token[2]" rule: `gaia brief milestone remove` and
# `gaia brief ac remove` are the same three-token shape (`brief` -> `milestone`
# / `ac` -> `remove`) but are DELIBERATELY exempt already (see
# `_cmd_milestone` / `_cmd_ac` in `bin/cli/brief.py` -- documented as
# reversible single-row deletes, re-addable). A blanket token[2] check would
# silently re-gate those two as a side effect; this table changes the
# classification of `gaia task gate remove` ONLY.
#
# Key:   (base_cmd, group, subgroup)  -- group is non_flag_tokens[1],
#        subgroup is non_flag_tokens[2].
# Value: frozenset of nested verbs that stay/become gated (T3) despite the
#        group's own tier exception.
NESTED_SUBCOMMAND_EXTRA_DENY_VERBS: Dict[Tuple[str, str, str], FrozenSet[str]] = {
    ("gaia", "task", "gate"): frozenset({"remove"}),
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


# Actions exempted by name under a group whose other actions stay gated: the
# verbs proven not to write. Every other verb under the group, including one
# added later, still falls to the scan.
#
# Key:   (base_cmd, group)  -- group is non_flag_tokens[0].
# Value: {verb: category}.
SUBCOMMAND_VERB_TIER_EXCEPTIONS: Dict[Tuple[str, str], Dict[str, str]] = {
    ("gaia", "migrate"): {"plan": CATEGORY_SIMULATION},
    ("gh", "label"): {"list": CATEGORY_READ_ONLY},
}

# A change request runs nothing until merged; these flags take the merge's decision.
_CHANGE_REQUEST_OWN_WORK: FrozenSet[str] = frozenset({"create", "edit", "update"})
_CHANGE_REQUEST_LANDING_FLAGS: Tuple[str, ...] = ("--auto-complete", "--auto-merge", "--bypass-policy")
_CHANGE_REQUEST_EDIT_FLAGS: Tuple[str, ...] = (
    "--base", "-B", "--target-branch", "--ready", "--draft", "--status", "--state",
)
_CHANGE_REQUEST_VOICE: FrozenSet[str] = frozenset({"ready", "comment", "note"})
_FORGE_CLIS: FrozenSet[str] = frozenset({"gh", "glab", "tea", "az"})


def _carries_revoking_flag(tokens: Union[List[str], tuple], flags: Tuple[str, ...]) -> bool:
    """True when any of *flags* appears, bare, as ``--long=value``, or as an attached ``-Xvalue``."""
    for token in tokens:
        for flag in flags:
            if token == flag or (flag.startswith("--") and token.startswith(f"{flag}=")):
                return True
            if not flag.startswith("--") and token.startswith(flag):
                return True
    return False


# ============================================================================
# Command-path Tier UPGRADES (anchored) — the symmetric opposite of
# COMMAND_SUBCOMMAND_TIER_EXCEPTIONS above.
# ----------------------------------------------------------------------------
# Some subcommands perform a state-mutating operation but carry no verb in
# MUTATIVE_VERBS, so they would fall through to Step 4 and classify READ_ONLY
# "by elimination" — silently un-gated. `gaia dev` (npm pack + install into a
# workspace's node_modules + wire .claude/ symlinks + bootstrap the DB) is
# exactly this case. This table is where such a form is anchored MUTATIVE (T3)
# by name, one exact command path at a time, instead of widening a global verb
# table and taxing every unrelated CLI that shares the word.
#
# The `--help` override (Step 3.5) and any simulation flag (Step 3) are
# resolved ABOVE this check and keep winning, so `gaia dev --help` and
# `gaia scan --dry-run` stay READ_ONLY without re-implementing either rule
# here. This dict lives inside the hooks directory and is itself T3-protected.


# Carried by `<cli> auth switch|logout` (see _check_generic_cli_shape): one
# account slot, two ways to disturb it, one replacement for both.
_ACCOUNT_SLOT_GUIDANCE = (
    "The active account of a CLI is global state shared with every other "
    "session on this machine -- choose the account per process instead of "
    "switching it: set the CLI's token variable for that one process, e.g. "
    "`GH_TOKEN=$ACCOUNT_TOKEN gh pr list`, or run it through a wrapper that "
    "does the same. `gh auth status` lists the accounts. Adding a missing "
    "account is the user's call: `gh auth login` makes the new account the "
    "active one."
)

_GCLOUD_LOGIN_GUIDANCE = (
    "A gcloud login makes the new account the active one for every "
    "session on this machine. Add it without switching: "
    "`gcloud auth login <account> --no-activate`, or start it detached with "
    "`gaia login start gcloud --account <account>`."
)

_REMOTE_RUN_NOUNS: FrozenSet[str] = frozenset({
    "workflow", "workflows", "pipeline", "pipelines", "ci",
})
_ACCOUNT_SLOT_ACTIONS: FrozenSet[str] = frozenset({"switch", "logout"})
_API_BODY_FLAGS: FrozenSet[str] = frozenset({"--field", "--raw-field", "--input"})
_API_READ_METHODS: FrozenSet[str] = frozenset({"get", "head"})
_GRAPHQL_MUTATION_RE = re.compile(r"\bmutation\b", re.IGNORECASE)
_REVIEW_VERDICT_FLAGS: FrozenSet[str] = frozenset({
    "--approve", "--request-changes", "--comment", "-a", "-r", "-c",
})
_GMAIL_ARCHIVE_RE = re.compile(r"removelabelids\W[^\]]*\binbox\b", re.DOTALL)
_GMAIL_HIDDEN_PAYLOAD_RE = re.compile(
    r"--[\w-]*file\b"
    r"|--[\w-]*(?:json|body|data|params)[\w-]*(?:=|\s+)@"
    r"|--[\w-]*(?:json|body|data)(?:=|\s+)-(?:\s|$)"
    r"|<\s*[^\s<]"
    r"|<<"
)


def _may_archive_gmail(semantics: CommandSemantics) -> bool:
    """Can this Gmail `modify` remove INBOX, i.e. archive the message?

    Yes when the inline label payload removes INBOX, and also when the payload
    comes from a file or stdin: the command text cannot show what it removes,
    so it asks. An inline payload that names labels without removing INBOX
    stays free.
    """
    text = semantics.raw_command.lower()
    if _GMAIL_ARCHIVE_RE.search(text):
        return True
    if "labelids" in text:
        return False
    return bool(_GMAIL_HIDDEN_PAYLOAD_RE.search(text))


def _api_body_flags(args: Tuple[str, ...]) -> List[Tuple[str, str]]:
    """(flag, value) for each body flag of an `api` call, however the value is attached."""
    found = []
    for i, token in enumerate(args):
        flag, eq, attached = token.partition("=")
        following = args[i + 1] if i + 1 < len(args) else ""
        if flag in _API_BODY_FLAGS:
            found.append((flag, attached if eq else following))
        elif token[:2] in ("-f", "-F"):
            found.append(("-f", token[2:] or following))
    return found


def _graphql_document_mutates(bodies: List[Tuple[str, str]]) -> bool:
    """True when a GraphQL `query` field or `--input` document holds `mutation`, or cannot be read."""
    for flag, value in bodies:
        if flag != "--input":
            key, _, value = value.partition("=")
            if key != "query":
                continue
        if flag == "--input" or value.startswith("@"):
            try:
                value = Path(value if flag == "--input" else value[1:]).read_text(errors="replace")
            except OSError:
                return True
        if _GRAPHQL_MUTATION_RE.search(value):
            return True
    return False


def _api_mutating_verb(args: Tuple[str, ...], graphql: bool = False) -> Optional[str]:
    """The method, or "api" for an implicit POST, when an `api` call writes; an explicit method decides alone."""
    bodies = _api_body_flags(args)
    if graphql:
        return "api" if _graphql_document_mutates(bodies) else None
    method: Optional[str] = None
    for i, token in enumerate(args):
        if token in ("-X", "--method"):
            method = args[i + 1] if i + 1 < len(args) else ""
        elif token.startswith("--method="):
            method = token.split("=", 1)[1]
        elif token.startswith("-X"):
            method = token[2:]
    if method is None:
        return "api" if bodies else None
    method = method.lower()
    return None if method in _API_READ_METHODS else method or "api"


def _check_generic_cli_shape(
    base_cmd: str, family: str, semantics: CommandSemantics
) -> Optional["MutativeResult"]:
    """T3 for command shapes whose effect does not depend on the binary's name.

    `<noun> run` for a remote-run noun triggers a remote execution, `config
    <write verb>` rewrites where every later command points, and `auth
    switch|logout` rewrites the shared account slot. `config` heads the read
    forms people run all day, so only a write verb after it decides; `use-context`
    and `activate` carry none and stay out. `api` with a body or non-GET method
    (`api graphql` only for a mutation), `pr ready` and `<noun> review` with a verdict flag publish.
    Only the leading path is read, so `docker run ci run`, `npm run test` and
    `gh run list` are untouched.
    """
    path = semantics.non_flag_tokens
    if len(path) < 2:
        return None
    head, action = path[0], path[1]
    verb = action
    guidance = ""
    if head in _REMOTE_RUN_NOUNS and action == "run":
        pass
    elif head == "config" and action.split("-", 1)[0] in MUTATIVE_VERBS:
        pass
    elif head == "auth" and action in _ACCOUNT_SLOT_ACTIONS:
        guidance = _ACCOUNT_SLOT_GUIDANCE
    elif head == "api" and (api_verb := _api_mutating_verb(semantics.args, action == "graphql")):
        verb = api_verb
    elif change_request_action(path) in _CHANGE_REQUEST_VOICE:
        verb = change_request_action(path)
    elif action == "review" and _REVIEW_VERDICT_FLAGS.intersection(semantics.flag_tokens):
        pass
    else:
        return None
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb=verb,
        cli_family=family,
        confidence="high",
        reason=(
            f"State-mutating command shape '{base_cmd} {head} {action}' "
            f"is MUTATIVE (T3) for any binary"
            + (f". {guidance}" if guidance else "")
        ),
        guidance=guidance,
    )


@dataclass(frozen=True)
class MutativeAnchor:
    """One command form declared MUTATIVE (T3) regardless of the verbs in it.

    ``path`` is matched as a PREFIX of the non-flag tokens that follow the base
    command, so ``("dev",)`` anchors every ``gaia dev ...`` while
    ``("context", "prune-workspaces")`` anchors that leaf alone and leaves its
    read/inspect siblings untouched. Depth is whatever the CLI needs: cloud CLIs
    routinely spell an operation as ``<cli> <group> <subgroup> <verb>``, which
    does not fit in a fixed two.

    ``flags`` makes the anchor conditional on one of those flags being present;
    empty means the path alone decides. This is the family-scoped inverse of an
    ALWAYS entry in DANGEROUS_FLAGS: the flag escalates on the CLI where it
    destroys and stays inert everywhere else, so gating it does not tax the CLI
    where the same spelling is routine.

    ``guidance`` names the non-mutating way to reach the same outcome, when one
    exists, and travels into the denial the agent reads. A gate that only says
    "no" to a command with a safe equivalent teaches nothing, and leaves the
    agent hunting for a spelling that passes.

    ``exempt_flags`` stand the anchor down, unless a flag in
    ``exempt_cancelled_by`` comes after them: CLIs such as gcloud let the last
    of ``--no-activate``/``--activate`` win, so presence alone would let
    ``--no-activate --activate`` through.

    An empty ``path`` is refused. It would anchor a whole CLI on a flag alone —
    the global behaviour this model exists to replace; a CLI-wide condition is
    spelled out as one anchor per subcommand that warrants it. Tokens and flags
    must be lowercase because the classifier lowercases what it matches against:
    an uppercase entry could never fire, and a declaration that reads as
    coverage while classifying nothing is worse than no declaration at all.
    """

    path: Tuple[str, ...]
    flags: FrozenSet[str] = frozenset()
    guidance: str = ""
    exempt_flags: FrozenSet[str] = frozenset()
    exempt_cancelled_by: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError(
                "MutativeAnchor.path must name at least one subcommand token"
            )
        for token in self.path:
            if not token or token != token.lower():
                raise ValueError(
                    f"MutativeAnchor.path token {token!r} must be non-empty lowercase"
                )
        for flag in self.flags | self.exempt_flags | self.exempt_cancelled_by:
            if not flag or flag != flag.lower():
                raise ValueError(
                    f"MutativeAnchor flag {flag!r} must be non-empty lowercase"
                )

    def matched_flags(self, semantics: CommandSemantics) -> Optional[Tuple[str, ...]]:
        """Return the flags that satisfied this anchor, or None if it does not fire.

        An empty tuple is a MATCH by path alone, so callers must test against
        ``None`` rather than for truthiness.
        """
        if semantics.non_flag_tokens[:len(self.path)] != self.path:
            return None
        toggles = [
            flag for flag in semantics.flag_tokens
            if flag in self.exempt_flags or flag in self.exempt_cancelled_by
        ]
        if toggles and toggles[-1] in self.exempt_flags:
            return None
        if not self.flags:
            return ()
        present = self.flags.intersection(semantics.flag_tokens)
        return tuple(sorted(present)) if present else None


def _anchor_shadows(earlier: MutativeAnchor, later: MutativeAnchor) -> bool:
    """Does ``earlier`` answer every command ``later`` could ever match?

    Anchors of one CLI are tried in declaration order and the first match
    returns, so ``later`` is reachable only while some command matches it and
    NOT ``earlier``. Three conditions together make that impossible:

    * ``earlier``'s path is a prefix of ``later``'s -- matching is by prefix,
      and an equal path is a prefix of itself;
    * ``earlier`` has no exemption, or exactly the same exemption as
      ``later``, otherwise ``later`` may still answer a command ``earlier``
      stands down for; and
    * ``earlier``'s condition is no narrower. An unflagged ``earlier`` fires on
      the path alone, so it swallows everything beneath it. A flagged one
      swallows ``later`` only when every flag that could fire ``later`` also
      fires ``earlier`` -- which is why an unflagged ``later`` under a flagged
      ``earlier`` survives: it still answers the commands that carry no flag.
    """
    if later.path[:len(earlier.path)] != earlier.path:
        return False
    if earlier.exempt_flags and (earlier.exempt_flags, earlier.exempt_cancelled_by) != (
        later.exempt_flags, later.exempt_cancelled_by
    ):
        return False
    if not earlier.flags:
        return True
    return bool(later.flags) and later.flags <= earlier.flags


def _validated_anchor_table(
    table: Dict[str, Tuple[MutativeAnchor, ...]],
) -> Dict[str, Tuple[MutativeAnchor, ...]]:
    """Return the table, or refuse it because one anchor could never fire.

    Declaration order is what this seam matches on, so an anchor already
    covered by an earlier one under the same CLI is dead config -- and dead
    config is the failure this model exists to avoid: it reads as coverage and
    classifies nothing. The verdict does not change (both entries say T3);
    what degrades is the ``verb`` and ``reason`` the user is shown, which name
    the BROAD form when the real one was the narrow one. Informed consent is
    this layer's product, so that is not cosmetic.

    Refusing HARD, at declaration, is the same stance ``__post_init__`` already
    takes on an empty path or an uppercase token, and for the same reason: the
    only writer of an anchor table is a source edit, so the error belongs to
    whoever writes it, surfaced on the first import rather than discovered as a
    missing description in a live approval prompt.

    Only the dead ORDER is refused, never the pair: declaring the specific
    anchor ahead of the broad one is the fix, and two anchors of the same path
    that differ in their flag conditions are both reachable and both allowed.
    """
    for base_cmd, anchors in table.items():
        for index, anchor in enumerate(anchors):
            for earlier in anchors[:index]:
                if _anchor_shadows(earlier, anchor):
                    raise ValueError(
                        f"{base_cmd!r} anchor {anchor} (index {index}) can "
                        f"never fire: {earlier} is declared before it and "
                        f"matches every command it would. Declare the more "
                        f"specific anchor first, or drop the covered one -- "
                        f"first match decides, so a covered entry reads as "
                        f"coverage and classifies nothing."
                    )
    return table


# A tool ignores a flag it does not know, so only these exact spellings free a
# formatter that otherwise rewrites files in place.
# `-check=false` and `-write=true` turn them back off, and a later one cancels.
# `-check=true` and `fmt -` are not exempted and ask a signature.
_TERRAFORM_FMT_NO_WRITE_FLAGS = frozenset({"-check", "--check", "-write=false", "--write=false"})
_TERRAFORM_FMT_WRITE_FLAGS = frozenset({"-check=false", "--check=false", "-write=true", "--write=true"})


def _terraform_fmt_anchor() -> "MutativeAnchor":
    return MutativeAnchor(
        path=("fmt",),
        exempt_flags=_TERRAFORM_FMT_NO_WRITE_FLAGS,
        exempt_cancelled_by=_TERRAFORM_FMT_WRITE_FLAGS,
        guidance=(
            "fmt rewrites the files it finds unless -check or -write=false is "
            "given. Run it as `terraform fmt -check` to see what would change."
        ),
    )


# Keyed by base_cmd; the anchors of one CLI are tried in declaration order and
# the first match decides. Every match yields the same verdict, so order only
# affects which path the reason names -- and an order that leaves an anchor
# unreachable is refused by _validated_anchor_table when this table is built.
COMMAND_PATH_MUTATIVE_UPGRADES: Dict[str, Tuple[MutativeAnchor, ...]] = _validated_anchor_table({
    "gaia": (
        MutativeAnchor(path=("dev",)),
        # `gaia context prune-workspaces --yes` HARD-DELETEs workspaces rows
        # (and, via ON DELETE CASCADE, their children) from gaia.db -- a
        # persistent, destructive DB mutation. But `context` carries no verb in
        # MUTATIVE_VERBS, so the whole `gaia context ...` group falls through to
        # Step 4 and classifies READ_ONLY by elimination, leaving the
        # destructive prune un-gated. Anchor ONLY the destructive leaf; the
        # other `gaia context` read/inspect subcommands stay READ_ONLY.
        MutativeAnchor(path=("context", "prune-workspaces")),
        MutativeAnchor(path=("context", "relocate")),
        # `gaia scan --workspace <name>` (write mode) UPSERTs (workspace,
        # project) rows into gaia.db and promotes the resolved workspace -- a
        # persistent DB mutation. Like `context`, `scan` carries no verb in
        # MUTATIVE_VERBS, so it would classify READ_ONLY by elimination. `scan`
        # is a flat command with no read-only subcommands (only flags:
        # --workspace, --dry-run, positional root), so the WHOLE group is
        # anchored. The `--dry-run` classify-only mode is NOT re-implemented
        # here: it is a SIMULATION_FLAG resolved by Step 3 above this check.
        MutativeAnchor(path=("scan",)),
        # `gaia release check` runs npm's prepack lifecycle twice over -- via
        # pack_tarball's `npm pack` and via bin/validate-sandbox.sh's `npm
        # install` -- and prepack executes scripts/build-plugin.py, which
        # rewrites the plugin root manifests including hooks/hooks.json, a
        # categorically protected path. Nothing above this line gated it:
        # `release` is a group noun carrying no verb in MUTATIVE_VERBS, and
        # `check` is a SIMULATION verb, so Step 4 answered CATEGORY_SIMULATION.
        # Anchored at the leaf like `context prune-workspaces` because `release
        # publish` is already MUTATIVE by verb.
        MutativeAnchor(path=("release", "check")),
        # `gaia workspace retire` re-keys a workspace's rows in gaia.db into
        # another and records an alias; `--undo` reverses it. `workspace` and
        # `retire` carry no verb in MUTATIVE_VERBS, so both would run free;
        # `--dry-run` stays free as a SIMULATION_FLAG resolved above.
        MutativeAnchor(path=("workspace", "retire")),
        # `gaia workspace curate` retires phantom workspaces (re-keying their
        # rows like retire does), drops stale aliases and deletes facets whose
        # folder is gone; `--dry-run` stays free as a SIMULATION_FLAG.
        MutativeAnchor(path=("workspace", "curate")),
        # Curated memory is append-only; `add --replace` is the one in-place
        # rewrite left, and it changes what every later read sees.
        MutativeAnchor(
            path=("memory", "add"),
            flags=frozenset({"--replace"}),
            guidance=(
                "A changed agreement needs no signature: write a new row and "
                "run `gaia memory link <new> <old> --kind=supersedes`."
            ),
        ),
        # memory_links keeps no history, so removing an edge -- a supersedes
        # one returns the old row to every injection -- is as unrecoverable
        # as `delete`.
        MutativeAnchor(path=("memory", "link"), flags=frozenset({"--delete"})),
    ),
    "gcloud": (
        # `set-password` sits three tokens below the gcloud root, beyond the
        # shallow compound-verb scan, so paths and arguments containing cheap
        # tier words must not leave a real credential mutation ungated.
        MutativeAnchor(path=("sql", "users", "set-password")),
        # `config set` is covered by _check_generic_cli_shape; the nested
        # `configurations` group sits one token deeper than that rule reads.
        MutativeAnchor(path=("config", "configurations", "create")),
        MutativeAnchor(path=("config", "configurations", "delete")),
        # `add` is kept out of MUTATIVE_VERBS so `git add` stays free; this one
        # publishes a new secret value to every consumer of `latest`.
        MutativeAnchor(path=("secrets", "versions", "add")),
        # `--update-adc` rewrites the application default credentials that
        # client libraries read, whatever the active account does; a
        # `--flags-file` can turn that or activation back on out of sight.
        MutativeAnchor(
            path=("auth", "login"),
            flags=frozenset({"--update-adc", "--flags-file"}),
        ),
        MutativeAnchor(
            path=("auth", "login"),
            exempt_flags=frozenset({"--no-activate"}),
            exempt_cancelled_by=frozenset({"--activate"}),
            guidance=_GCLOUD_LOGIN_GUIDANCE,
        ),
    ),
    # `config <write verb>` is generic (_check_generic_cli_shape). NOT gated,
    # and left open on purpose: `kubectl config use-context` and `gcloud config
    # configurations activate` carry no write verb -- `use` and `activate` are
    # absent from the verb taxonomy -- and need their own decision.
    "kubectl": (
        # `run` is deliberately absent from MUTATIVE_VERBS ("safe by
        # elimination" -- a global entry would gate every `docker run` and
        # similar dev-workflow invocation), so `kubectl run` fell through to
        # Step 4 and classified READ_ONLY by elimination even though it
        # creates a live, scheduled workload against whatever cluster the
        # current context points at. Scoped to the one shape that actually
        # brings a workload to life -- an explicit container image -- rather
        # than the bare subcommand, so a hypothetical future `kubectl run`
        # invocation that cannot start a pod (none exists today; every real
        # invocation carries `--image`) is not gated on the path alone.
        MutativeAnchor(path=("run",), flags=frozenset({"--image"})),
    ),
    "gh": (
        # Merges or rebases the base branch into the PR's REMOTE head: a push
        # to someone's branch, and a CI trigger besides. Today the verb scan
        # reaches it only by splitting `update-branch`; the anchor names it.
        MutativeAnchor(path=("pr", "update-branch")),
    ),
    # `git remote add` creates a NEW remote destination the repository will
    # push to and fetch from thereafter -- the same "grants a new capability"
    # shape as the IAM bindings above, reached through the same door: "add" is
    # deliberately absent from MUTATIVE_VERBS, so the whole command fell
    # through to Step 4 and classified READ_ONLY by elimination. Two sibling
    # forms already gate today and are deliberately NOT re-anchored here:
    # `git worktree add` has its own family classifier (_check_git_worktree),
    # and `git remote set-url` -- repointing an EXISTING destination -- is
    # already caught by the generic verb scan on "set". The one residual open
    # surface is standing up a NEW remote, so only `remote add` is anchored.
    # `git remote -v` / `git remote show` / `git remote rename` are untouched
    # and stay free: the anchor path requires "add" as the second token.
    "git": (
        MutativeAnchor(path=("remote", "add")),
    ),
    # `terraform init` / `terragrunt init` re-run against a workspace that
    # already has provider plugins and remote state -- bare init is idempotent
    # bootstrapping and stays free. But three flags turn the same subcommand
    # into a state-mutating operation: `-upgrade` rewrites installed provider
    # versions, `-migrate-state` moves the backend's state to a new
    # configuration, and `-reconfigure` discards the existing backend config
    # and re-initializes it. None of the three carries a verb in
    # MUTATIVE_VERBS ("init" names no lifecycle action the taxonomy tracks),
    # so all three fell through to Step 4 and classified READ_ONLY by
    # elimination alongside the harmless bare form. One flag-conditioned
    # anchor per CLI (the mechanism built in the M2 PREVIA task) scopes the
    # escalation to exactly the flags that mutate, on both infra CLIs this
    # repository observed in use -- `terraform` and `terragrunt` do not share
    # a base_cmd, so each needs its own entry; a bare `terraform init` /
    # `terragrunt init` with none of the three flags is untouched.
    "terraform": (
        MutativeAnchor(
            path=("init",),
            flags=frozenset({"-upgrade", "-migrate-state", "-reconfigure"}),
        ),
        _terraform_fmt_anchor(),
    ),
    "tofu": (_terraform_fmt_anchor(),),
    "helm": (MutativeAnchor(path=("repo", "add")),),
    "terragrunt": (
        MutativeAnchor(
            path=("init",),
            flags=frozenset({"-upgrade", "-migrate-state", "-reconfigure"}),
        ),
        _terraform_fmt_anchor(),
        # `--check` is honored only by terragrunt releases after the v0.72 flag
        # redesign, so it asks; `--terragrunt-hclfmt-stdin` formats stdin to stdout.
        MutativeAnchor(
            path=("hclfmt",),
            exempt_flags=frozenset({"--terragrunt-check", "--terragrunt-hclfmt-stdin"}),
            exempt_cancelled_by=frozenset({"--terragrunt-check=false"}),
            guidance=(
                "hclfmt rewrites HCL in place unless --terragrunt-check is "
                "given; --check and --working-dir are not flags of this "
                "terragrunt and are ignored."
            ),
        ),
        MutativeAnchor(
            path=("hcl", "fmt"),
            exempt_flags=frozenset({"--check"}),
            exempt_cancelled_by=frozenset({"--check=false"}),
            guidance="hcl fmt rewrites HCL in place unless --check is given.",
        ),
    ),
})


# Keyed by base command; a CLI absent from this table has no track grammar and
# is left completely untouched. Only ``gcloud`` publishes pre-GA surfaces as a
# word between the tool and the command, so only ``gcloud`` declares one. Every
# word listed here is a word this layer DELETES from a command before reading
# it, which is why the set is the two tracks gcloud actually ships and not a
# guess: ``preview`` was a third one years ago and is gone, so including it
# would license deleting an ordinary word for a grammar that no longer exists.
RELEASE_TRACK_PREFIXES: Dict[str, FrozenSet[str]] = {
    "gcloud": frozenset({"alpha", "beta"}),
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
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "ruby": frozenset({"-e"}),
    "perl": frozenset({"-e", "-E"}),
    "php": frozenset({"-r"}),
    "lua": frozenset({"-e"}),
    "luajit": frozenset({"-e"}),
    "rscript": frozenset({"-e"}),
    "r": frozenset({"-e"}),
    "deno": frozenset({"eval"}),
    "bun": frozenset({"-e", "--eval", "-p", "--print"}),
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
    "node", "ruby", "perl", "php", "lua", "luajit", "rscript",
})

# Non-shell, non-Python interpreters: their script files are SOURCE CODE in a
# programming language, not shell command lists.  Their content lane is "code"
# rather than "shell" so the regex classifier suppresses camelCase subcommand
# splitting -- a camelCase token in JS/Ruby/etc. (``execPath``, ``setState``)
# is a language identifier, not a CLI subcommand.  Suppressing it removes only
# false positives: whole-token verbs and command aliases (``rm``/``cp`` used
# bare), dangerous flags, and blocked-command patterns are still scanned, and
# camelCase multi-word tokens embedded inside a quoted string argument were
# never matched anyway (the quote makes the whole string a single token).
_NON_SHELL_SCRIPT_INTERPRETERS: FrozenSet[str] = frozenset({
    "node", "ruby", "perl", "php", "lua", "luajit", "rscript",
})

# File extensions whose interpreter is implied by ``./script`` (no explicit
# interpreter token).  Maps the extension to the analysis lane used for its
# content: "python" routes through the AST analyzer, "shell" through the
# blocked/mutative regex layer, "code" through the same regex layer but with
# camelCase subcommand splitting suppressed (non-shell source languages).
_SHEBANG_EXT_LANES: Dict[str, str] = {
    ".py": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".js": "code",
    ".mjs": "code",
    ".cjs": "code",
    ".rb": "code",
    ".pl": "code",
    ".php": "code",
}

# Canonical interpreter for a script EXTENSION, used when a script path is
# reached through a wrapper that hides the interpreter (``uv run deploy.py``,
# ``npx ./tool.js``).  Rewriting the invocation as ``<interpreter> <script>``
# routes it into the SAME ``_check_script_file`` lane the direct form uses, so
# the resolved script is classified by its real content (Python AST, JS lexer,
# shell regex) instead of by the wrapper's own verb.  Prepending the
# interpreter -- rather than re-dispatching the bare path -- is what makes the
# rewrite independent of path SHAPE: the direct ``./script`` branch of
# ``_resolve_script_argument`` requires a ``/`` in the token, so a bare
# ``uv run c.py`` would otherwise resolve to nothing.
_SCRIPT_EXT_INTERPRETERS: Dict[str, str] = {
    ".py": "python3",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".rb": "ruby",
    ".pl": "perl",
    ".php": "php",
}

# Cap on bytes read from a script file during classification.  A script larger
# than this is unusual for the inline-evasion case and reading it in full would
# add latency to every hook invocation; we read a bounded prefix, which is
# enough to catch the mutative calls an evasion script front-loads.
_MAX_SCRIPT_READ_BYTES = 256 * 1024

# Maximum nesting depth for script-body / npm-run / exec-sink re-classification.
# Each time detection descends INTO a referenced body (an interpreter reading a
# script file, `npm run` resolving a package.json entry, an exec-sink string, or
# the gaia-CLI re-dispatch) the depth increments by one.  A referenced body that
# names its OWN path -- even in a usage/help heredoc such as validate-sandbox.sh's
# ``bin/validate-sandbox.sh [--version ...]`` line -- or a mutual A->B->A cycle
# would otherwise re-read and re-classify the same file forever, blowing Python's
# stack ("maximum recursion depth exceeded") and crashing the PreToolUse hook.
# Legitimate nesting is only a few levels deep (`npm run x` -> `bash a.sh` ->
# `bash b.sh`), so this cap never truncates a real chain; it exists solely to
# guarantee termination on a self/mutual reference.
#
# REACHING THE CAP STOPS THE DESCENT AND RETAINS THE VERDICT. Those are two
# separate acts, and conflating them was a measured hole: the body was no longer
# opened AND the invocation fell through to ordinary token classification, which
# answers safe by elimination -- so the level that spent the last unit of budget
# was released rather than withheld. A nesting chain whose innermost body was a
# script invocation passed FREE at EXACTLY this value, with the levels on either
# side of it gated. Raising the number would only move that hole.
#
# The principle the three exhaustion sites now share: budget exhaustion is a
# FAILURE TO PROVE SAFETY, not a proof of safety. A lane that declines to open a
# body because the budget ran out is in the same epistemic position as one that
# could not read the file, and the module already resolves that position
# conservatively (`script-file-unreadable`, `npm-run-unresolved`). Cycle-breaking
# needs the descent to STOP; it never needed the verdict to be free.
_MAX_SCRIPT_RECURSION_DEPTH = 12

# The verdict every lane returns when the descent budget is exhausted. Built here
# rather than inline at the three sites so the three cannot drift apart, and so a
# fourth descending lane added later has one obvious thing to call.
def _budget_exhausted_result(lane: str) -> MutativeResult:
    """Return the conservative verdict for a body left unopened by the bound.

    Args:
        lane: The descending lane that ran out of budget, named in the reason so
            an operator reading a block can tell which body went uninspected.

    Returns:
        A mutative result. Confidence is ``low`` because nothing about the body
        was actually read -- the verdict states that safety is unproven, not that
        a mutation was found.
    """
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="descent-budget-exhausted",
        cli_family="system",
        confidence="low",
        reason=(
            f"Nested {lane} exceeded the analysis depth limit; the body was "
            f"not opened and is classified conservatively"
        ),
    )

# Maximum descent for the command-substitution lane, counted on the SAME
# ``_depth`` budget as the script lane above -- one command may only ever be
# opened so many times in total, whichever mix of routes did the opening.
#
# The value is shared; the EXHAUSTION RULE is not, and the difference is the
# point.  The script lane exhausts by declining to open the body and letting
# ordinary token classification decide, because its cycle risk is a file that
# names itself and there is no bounded quantity to run out of.  A substitution
# body cannot cycle -- it is a strict substring of its parent, at least three
# characters shorter, so the descent is finite on its own -- and that is exactly
# why exhaustion here means the string is pathological rather than merely deep.
# So this lane exhausts CLOSED: past the cap a command that still carries an
# executing substitution is classified mutative WITHOUT the body being read. A
# cap that let the command through on the way out would be a bypass with
# instructions -- nest one level past the number and the gate stops applying.
_MAX_SUBSTITUTION_RECURSION_DEPTH = 12

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
    "lua": frozenset({"-e", "-l"}),
    "luajit": frozenset({"-e", "-l"}),
    "rscript": frozenset({"-e"}),
}

_PYTHON_OPTION_VALUE_FLAGS: FrozenSet[str] = frozenset({"-W", "-X", "--check-hash-based-pycs"})


def script_positional(interpreter: str, arguments: List[str]) -> Optional[str]:
    """The script file *interpreter* runs from *arguments*, or None when it runs inline code, a module or stdin."""
    defer_flags = _INTERP_NON_SCRIPT_VALUE_FLAGS.get(interpreter, frozenset())
    value_flags = _PYTHON_OPTION_VALUE_FLAGS if interpreter in _PYTHON_INTERPRETERS else frozenset()
    skip_value = False
    for token in arguments:
        if skip_value:
            skip_value = False
        elif token == "-" or token in defer_flags:
            return None
        elif token in value_flags:
            skip_value = True
        elif not token.startswith("-"):
            return token
    return None


# Interpreter flags that VALIDATE SYNTAX ONLY and never execute the script
# body.  ``bash -n <script>`` reads the script and reports parse errors without
# running a single command; ``node --check <script>`` / ``node -c <script>``
# does the same for JavaScript.  When one of these leads the interpreter flags
# (i.e. it precedes the script positional), the invocation cannot mutate state,
# so the script-file lane classifies it NON-mutative WITHOUT reading the file's
# contents -- the body is never run regardless of what it contains.
#
# Gating: a flag is only honored when it appears BEFORE the first positional
# (a ``-n`` after the script is an argument to the script, not to bash).  A
# co-occurring inline-exec flag (``bash -c <code>``) is excluded upstream --
# ``_resolve_script_argument`` returns None the moment a defer flag from
# ``_INTERP_NON_SCRIPT_VALUE_FLAGS`` precedes a positional -- so this downgrade
# is only reached when the payload truly is a non-executed script file.  Note
# ``node`` uses ``-c``/``--check`` for the syntax check (its inline-exec flag is
# ``-e``), while for the shells ``-c`` is inline exec, not a syntax check.
_INTERP_SYNTAX_CHECK_FLAGS: Dict[str, FrozenSet[str]] = {
    "bash": frozenset({"-n"}),
    "sh": frozenset({"-n"}),
    "zsh": frozenset({"-n"}),
    "dash": frozenset({"-n"}),
    "ksh": frozenset({"-n"}),
    "node": frozenset({"-c", "--check"}),
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
# Prefix-runner re-dispatch (Step 1c-run)
# ---------------------------------------------------------------------------
# A prefix runner executes ANOTHER command in an environment it prepares:
# ``uv run deploy.py``, ``poetry run alembic upgrade head``, ``npx prisma
# migrate deploy``.  Its own base token is not an interpreter, so
# ``_check_script_file`` returns None and the wrapped command is never
# inspected -- the invocation falls to safe-by-elimination (T0) even when the
# wrapped script mutates.  Measured consequence: ``uv run script.py`` whose body
# calls ``shutil.copy`` classified T0 while the identical ``python3 script.py``
# classified T3, so the wrapper DISABLED the whole content-inspection layer.
#
# The fix re-dispatches the wrapped command, mirroring the two re-dispatch
# precedents in this module (``_check_python_module_runner`` for ``python -m
# pip``, ``_check_gaia_cli_dispatcher`` for ``bin/gaia``): rewrite the
# invocation as the command the runner would actually execute and re-run
# ``detect_mutative_command`` on it, so the result is IDENTICAL to the
# unwrapped form -- including the Python AST lane's dangerous-call table, which
# is invoked through the existing lane rather than duplicated here.
#
# Each entry maps the runner to the subcommand tokens one of which must follow it
# (``uv run``, ``npm exec``, ``bun x``, ``pnpm dlx``) or ``None`` when the
# runner takes the wrapped command directly (``npx <pkg> <args>``).
# ``value_flags`` are the runner's OWN options that consume the following token
# as their value; without them the value would be mistaken for the wrapped
# command.  This table is inherently OPEN: a runner nobody listed still bypasses
# the lane (see the fallback rationale in ``_check_prefix_runner``).
_PREFIX_RUNNER_SUBCOMMANDS: Dict[str, "Optional[FrozenSet[str]]"] = {
    "uv": frozenset({"run"}),
    "uvx": None,
    "poetry": frozenset({"run"}),
    "pipx": frozenset({"run"}),
    "npx": None,
    "bunx": None,
    "pnpx": None,
    "npm": frozenset({"x", "exec", "exe"}),
    "bun": frozenset({"x", "exec"}),
    "pnpm": frozenset({"dlx", "exec"}),
    "yarn": frozenset({"dlx"}),
}

# Where each runner's code comes from, by ``(runner, subcommand)``: "installed"
# runs the project's node_modules/.bin copy when there is one and otherwise
# fetches or fails; "registry" always fetches into a throwaway environment, so
# a dependency the project declares does not make it the project's code;
# "project" runs inside the project environment, and only the packages its
# ``--with`` adds are fetched.  ``poetry run`` is absent: it fetches nothing.
_RUNNER_CODE_ORIGIN: Dict[Tuple[str, "Optional[str]"], str] = {
    ("npx", None): "installed",
    ("bunx", None): "installed",
    ("npm", "x"): "installed",
    ("npm", "exec"): "installed",
    ("npm", "exe"): "installed",
    ("bun", "x"): "installed",
    ("bun", "exec"): "installed",
    ("pnpm", "exec"): "installed",
    ("pnpm", "dlx"): "registry",
    ("pnpx", None): "registry",
    ("yarn", "dlx"): "registry",
    ("pipx", "run"): "registry",
    ("uvx", None): "registry",
    ("uv", "run"): "project",
}

# Per ecosystem: the options naming the package to fetch in place of the wrapped
# command (``npx -p cowsay say``), and the options adding packages beside it.
# ``-p`` is a package only for node runners; ``uvx -p`` selects a Python.
_RUNNER_PACKAGE_FLAGS: Dict[str, Tuple[FrozenSet[str], FrozenSet[str]]] = {
    "node": (frozenset({"--package", "-p"}), frozenset()),
    "python": (frozenset({"--spec", "--from"}), frozenset({"--with"})),
}

# The only options ``uv run`` may carry and stay unsigned; none adds code beyond
# the project's lockfile, and ``--with``/``-w`` only with local paths.  The
# value-taking ones are part of the resolver's uv value table, so the command it
# finds is the one this shape was checked up to.
_UV_RUN_WITH_FLAGS: FrozenSet[str] = frozenset({"--with", "-w"})
_UV_RUN_SAFE_VALUE_FLAGS: FrozenSet[str] = frozenset({
    "--extra", "--no-extra", "--group", "--no-group", "--only-group", "--package",
    "--color",
})
_UV_RUN_SAFE_FLAGS: FrozenSet[str] = frozenset({
    "--all-extras", "--no-dev", "--only-dev", "--no-default-groups", "--all-groups",
    "--no-editable", "--exact", "--no-env-file", "--isolated", "--active",
    "--no-sync", "--locked", "--frozen", "--all-packages", "--no-project", "-m",
    "--module", "-q", "--quiet", "-v", "--verbose", "--offline", "--no-progress",
    "--native-tls", "--no-cache", "-n", "--no-config", "--no-python-downloads",
})

_PREFIX_RUNNER_VALUE_FLAGS: Dict[str, FrozenSet[str]] = {
    "uv": frozenset({
        "--with-editable", "--with-requirements", "--python", "-p",
        "--directory", "--project", "--index", "--index-url",
        "--refresh-package", "--isolated-package", "--env-file",
    }) | _UV_RUN_WITH_FLAGS | _UV_RUN_SAFE_VALUE_FLAGS,
    "uvx": frozenset({
        "--with", "--from", "--python", "-p", "--index", "--index-url",
    }),
    "poetry": frozenset({"--directory", "-C", "--project", "-P"}),
    "pipx": frozenset({"--spec", "--python", "--pip-args", "--index-url"}),
    "npx": frozenset({
        "--package", "-p", "--node-options", "--userconfig", "--globalconfig",
        "--cache", "--registry", "--prefix",
    }),
    "npm": frozenset({
        "--package", "-p", "--node-options", "--userconfig", "--globalconfig",
        "--cache", "--registry", "--prefix", "-C", "--workspace", "-w",
    }),
    "bunx": frozenset({"--package", "-p"}),
    "bun": frozenset({"--package", "-p", "--cwd"}),
    "pnpm": frozenset({"--package", "--dir", "-C", "--filter", "-F"}),
    "pnpx": frozenset({"--package"}),
    "yarn": frozenset({"--package", "-p", "--cwd"}),
}

# Runner options known to take NO value.  For a "registry" runner, an option
# before the package that is in neither this table nor
# ``_PREFIX_RUNNER_VALUE_FLAGS`` may have consumed the next token, so the
# package cannot be identified and the invocation is signed.
_PREFIX_RUNNER_BOOLEAN_FLAGS: Dict[str, FrozenSet[str]] = {
    "npx": frozenset({
        "-y", "--yes", "--no", "--no-install", "--ignore-existing", "-q",
        "--quiet", "-s", "--silent", "--verbose", "--prefer-offline",
        "--prefer-online", "--offline", "--legacy-peer-deps",
    }),
    "npm": frozenset({
        "-y", "--yes", "--no", "-q", "--quiet", "-s", "--silent", "--verbose",
        "--prefer-offline", "--prefer-online", "--offline", "--legacy-peer-deps",
        "--workspaces", "--ws", "--include-workspace-root", "--if-present",
    }),
    "bunx": frozenset({"--bun", "--silent", "--verbose", "--no-install"}),
    "bun": frozenset({"--bun", "-b", "--silent", "--verbose", "--no-install"}),
    "pnpm": frozenset({
        "-s", "--silent", "--shell-mode", "-r", "--recursive", "-w",
        "--workspace-root", "--parallel", "--if-present", "--reverse",
        "--stream", "--no-bail", "--use-stderr",
    }),
    "yarn": frozenset({"-q", "--quiet", "--silent", "--verbose"}),
    "uvx": frozenset({
        "--isolated", "--no-cache", "-n", "--offline", "-q", "--quiet", "-v",
        "--verbose", "--no-config", "--refresh", "--native-tls", "--no-progress",
        "--preview",
    }),
    "pipx": frozenset({
        "-q", "--quiet", "--verbose", "--no-cache", "--pypackages",
        "--system-site-packages",
    }),
}

# The only options an "installed" runner may carry and stay unsigned, besides its
# directory option: they take no value and cannot change which copy runs.  Not
# ``_PREFIX_RUNNER_BOOLEAN_FLAGS``: ``--ignore-existing`` takes no value and
# still skips the local copy.
_INSTALLED_RUNNER_SAFE_FLAGS: Dict[str, FrozenSet[str]] = {
    "npx": frozenset({
        "-y", "--yes", "--no", "--no-install", "-q", "--quiet", "-s", "--silent",
        "--verbose", "--offline", "--prefer-offline",
    }),
    "npm": frozenset({
        "-y", "--yes", "--no", "-q", "--quiet", "-s", "--silent", "--verbose",
        "--offline", "--prefer-offline",
    }),
    "bunx": frozenset({"--bun", "--silent", "--verbose", "--no-install"}),
    "bun": frozenset({"--bun", "-b", "--silent", "--verbose", "--no-install"}),
    "pnpm": frozenset({"-s", "--silent", "--use-stderr"}),
}

# ``npx`` runs an arbitrary SHELL command when given ``--call``/``-c``, so the
# flag's value is a command string to re-classify, not a package name.
_NPX_SHELL_CALL_FLAGS: FrozenSet[str] = frozenset({"--call", "-c"})

# The stdin sentinel: ``python3 -`` / ``node -`` / ``bash -`` read their PROGRAM
# from standard input.  Unlike a script file the payload has no path to open, so
# it cannot be inspected at classification time -- ``cat payload.py | python3``
# is caught only because the composition layer sees the ``file_to_exec`` pipe,
# while ``python3 - < payload.py`` reached the interpreter with no verb to match
# and classified T0 through the FULL validator.  Treated like an unreadable
# script file: conservative mutative.  A HEREDOC payload is excluded (the body
# IS in the command string, and Step 3c already analyzes it).
_STDIN_SCRIPT_SENTINEL = "-"

# ---------------------------------------------------------------------------
# PowerShell lane (Step 1c-ps): Windows/.NET interpreter introspection
# ---------------------------------------------------------------------------
# The POSIX verb scanner is blind to PowerShell: `powershell.exe -Command
# "<script>"` collapses the payload into one opaque token, so a mutative
# `Remove-Item` inside it is never seen (false negative), while the old `-rf`
# flag heuristic mis-read `-NoProfile` as `-rf` and over-blocked EVERY call
# (false positive).  This lane mirrors `_INLINE_CODE_MAP`/`_check_script_file`
# for the Windows shell: it introspects the payload and classifies each cmdlet
# by its Verb-Noun VERB (the part before the hyphen) against PowerShell's own
# approved-verb taxonomy -- so a never-seen cmdlet classifies correctly by its
# verb (`Get-FooBar` -> read, `Set-FooBar` -> change) with NO per-cmdlet list.
_POWERSHELL_INTERPRETERS: FrozenSet[str] = frozenset({
    "powershell", "powershell.exe", "pwsh", "pwsh.exe",
})

# Read/inspection verbs -> NON-mutative (T0/T1).  The part before the hyphen of
# a Verb-Noun cmdlet; matched case-insensitively.  Drawn from PowerShell's
# approved-verb groups (Common/Data/Diagnostic) that only OBSERVE state.
_PS_READ_VERBS: FrozenSet[str] = frozenset({
    "get", "measure", "select", "where", "sort", "compare",
    "test", "resolve", "find", "search", "show",
    "convertfrom", "convertto", "group", "join", "split", "read",
    # NOTE: "format" is NOT here -- it is ambiguous by noun (see
    # _PS_FORMAT_READ_NOUNS / _classify_powershell_verb): Format-Table/List/Wide
    # only render (read) while Format-Volume/Format-* of storage WRITE (change).
})

# Change verbs -> MUTATIVE (T3).  Any of these at a command position (the first
# cmdlet of any composition stage) escalates the WHOLE payload (composition rule
# mirror: any mutative stage -> T3).
_PS_CHANGE_VERBS: FrozenSet[str] = frozenset({
    "set", "new", "remove", "clear", "add", "move", "copy", "rename",
    "start", "stop", "restart", "suspend", "resume", "register",
    "unregister", "install", "uninstall", "import", "export", "write",
    "enable", "disable", "mount", "dismount", "invoke", "push", "pop",
    "save", "publish", "send", "update", "edit", "reset", "limit", "block",
})

# The `Out-*` verb is ambiguous: `Out-String`/`Out-Host`/`Out-Null` only render
# to the pipeline/console (read), while `Out-File`/`Out-Printer` WRITE (change).
# Split by noun rather than lumping the whole verb into one set.
_PS_OUT_READ_NOUNS: FrozenSet[str] = frozenset({
    "string", "host", "null", "default", "gridview",
})

# The `Format-*` verb is likewise ambiguous by noun: `Format-Table`/`Format-List`/
# `Format-Wide`/`Format-Custom`/`Format-Hex` only render for display (read), while
# `Format-Volume` and any other storage `Format-*` DESTROY data (change/T3).
# Verb alone is insufficient -- classify by noun.
_PS_FORMAT_READ_NOUNS: FrozenSet[str] = frozenset({
    "table", "list", "wide", "custom", "hex", "default",
})

# Union of every recognized PowerShell verb (read + change + the two
# noun-ambiguous verbs).  Used to decide whether a lowercase bare Verb-Noun
# token is actually a PowerShell cmdlet vs an ordinary hyphenated POSIX command
# name (`docker-compose`, `pre-commit`): a lowercase token counts as a cmdlet
# only when its verb is a real PS verb; a PascalCase token (`Remove-Item`,
# `Frobnicate-Thing`) is a cmdlet by its casing alone (see the bare-cmdlet lane).
_PS_ALL_VERBS: FrozenSet[str] = (
    _PS_READ_VERBS | _PS_CHANGE_VERBS | frozenset({"out", "format"})
)

# A Verb-Noun cmdlet token: a letter-led word, a hyphen, then a noun word.
# A leading flag ("-Recurse", "-Command") cannot match -- the pattern requires
# a letter immediately BEFORE the hyphen, and flags start with the hyphen.
_PS_CMDLET_RE = _re.compile(r"\b([A-Za-z][A-Za-z]*)-([A-Za-z][A-Za-z0-9]*)\b")

# Operators after which PowerShell starts a new command: statement and pipeline
# separators, and the opening or closing of a script block or (sub)expression,
# whose body runs as commands of its own -- ``{ Remove-Item $_ }`` and
# ``"$(Remove-Item x)"`` included. Two-char operators come first so they win.
_PS_POSITION_SEPARATORS: Tuple[str, ...] = ("||", "&&", ";", "|", "{", "}", "(", ")", "\n")

# A word starting with one of these is a value, variable, operator or comment,
# never a command PowerShell starts.
_PS_EXPRESSION_LEADS = frozenset("$'\"@[-#,=+*!<>0123456789")

# Language keywords: a position they lead is a statement header, and the body
# it governs is split off as positions of its own.
_PS_KEYWORDS: FrozenSet[str] = frozenset({
    "if", "elseif", "else", "for", "while", "do", "until", "switch", "try",
    "catch", "finally", "trap", "function", "filter", "param", "begin",
    "process", "end", "return", "break", "continue", "throw", "exit", "data",
    "dynamicparam",
})

# ForEach-Object runs its script blocks, which are classified as positions of
# their own; given a member NAME instead (``% Delete``, ``-MemberName``) it
# calls that method on every input, an effect no name list can judge.
_PS_SCRIPTBLOCK_INVOKERS: FrozenSet[str] = frozenset({"foreach-object", "foreach", "%"})
_PS_INVOKER_BLOCK_FLAGS: FrozenSet[str] = frozenset({"-process", "-begin", "-end", "-parallel"})


def _ps_command_positions(payload: str) -> "List[str]":
    """Return the text of every command position in a PowerShell payload, in order.

    Single-quoted text is literal in PowerShell and never splits; double-quoted
    text does, because a ``$(...)`` inside it runs.
    """
    positions: "List[str]" = []
    start = i = 0
    in_single = in_double = False
    while i < len(payload):
        ch = payload[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single:
            separator = next(
                (s for s in _PS_POSITION_SEPARATORS if payload.startswith(s, i)), None,
            )
            if separator:
                positions.append(payload[start:i].strip())
                i += len(separator)
                start = i
                continue
        i += 1
    positions.append(payload[start:].strip())
    return [position for position in positions if position]


# A ``.Name(`` or ``::Name(`` call invokes a .NET method, and a method can
# mutate (``.Delete()``, ``[IO.File]::Delete(x)``) under any name; a property
# read carries no call parentheses.
_PS_METHOD_CALL_RE = _re.compile(r"(?:\.|::)[A-Za-z_]\w*\(")

# A call operator (``&``) or dot-source (``. ``) in front of a command word.
_PS_CALL_OPERATOR_RE = _re.compile(r"^(?:&|\.(?=\s))\s*")


def _powershell_floor_readings(command: str) -> "Tuple[str, ...]":
    """Return every command position a PowerShell command line runs, for the block floor.

    Empty unless *command* is a ``powershell``/``pwsh`` invocation, whose
    ``-Command`` script is split, or is itself PowerShell-shaped: led by a
    cmdlet, a keyword, a script-block invoker, a call operator or an expression.
    """
    head = command[:_consume_shell_word(command, 0)].strip("\"'")
    if program_identity(head) in _POWERSHELL_INTERPRETERS:
        command = _powershell_payload(command)[1]
    elif not (
        _PS_CMDLET_RE.fullmatch(head)
        or head.lower() in _PS_KEYWORDS | _PS_SCRIPTBLOCK_INVOKERS
        or head[:1] in ("&", "$", "(")
    ):
        return ()
    return tuple(
        _name_program_head(_PS_CALL_OPERATOR_RE.sub("", position))[0]
        for position in _ps_command_positions(command)
    )

# Obfuscation / non-inspectable-execution markers -> T3 regardless of the
# surrounding cmdlets.  These run BEFORE the cmdlet allowlist so a benign read
# cmdlet piped into `iex` (`Get-Content x | iex`) cannot launder the payload.
#   iex / iwr / icm  : bare aliases (Invoke-Expression / -WebRequest / -Command)
#   call operators   : `&`/`.` at a statement boundary invoke an arbitrary target
_PS_OBFUSCATION_RES: Tuple["_re.Pattern[str]", ...] = (
    _re.compile(r"\b(iex|iwr|icm)\b", _re.IGNORECASE),
    _re.compile(r"\binvoke-expression\b", _re.IGNORECASE),
    # `&` or `.` used as a call operator at a statement boundary (start of
    # payload or right after `;` `|` `{` `(` `&&`), followed by whitespace and a
    # target.  Excludes a trailing path dot ("Get-ChildItem .") which has no
    # following target.
    _re.compile(r"(?:^|[;|{(]|&&)\s*[&.]\s+\S"),
)

# PowerShell `-EncodedCommand <base64>` (and its unambiguous abbreviations) hides
# the real script inside a base64 blob that cannot be introspected -> T3.  Any
# flag whose body is a prefix of "encodedcommand" (len>=2) or the documented
# short alias "ec" is treated as the encoded-command flag.
def _is_ps_encoded_flag(flag: str) -> bool:
    body = flag.lstrip("-").lower()
    if not body:
        return False
    if body == "ec":
        return True
    return len(body) >= 2 and "encodedcommand".startswith(body)


# PowerShell interpreter options that consume the following word.
_PS_INTERPRETER_VALUE_FLAGS: FrozenSet[str] = frozenset({
    "-executionpolicy", "-ep", "-ex", "-windowstyle", "-w", "-workingdirectory",
    "-wd", "-configurationname", "-config", "-inputformat", "-if",
    "-outputformat", "-of", "-version", "-v", "-psconsolefile",
    "-settingsfile", "-file", "-f", "-custompipename",
})


def _powershell_payload(command: str) -> "Tuple[bool, str]":
    """Return ``(has_command_flag, script)`` for a ``powershell``/``pwsh`` command line.

    The script is every word after ``-Command`` (any prefix of it, ``-c``
    included), which PowerShell joins and runs; without that flag it is the
    words after the interpreter's own options.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    i = 1
    while i < len(words):
        flag = words[i].lower()
        if len(flag) >= 2 and "-command".startswith(flag):
            return True, " ".join(words[i + 1:])
        if not flag.startswith("-"):
            break
        i += 2 if flag in _PS_INTERPRETER_VALUE_FLAGS else 1
    return False, " ".join(words[i:])


# ---------------------------------------------------------------------------
# Bare Windows command lane (Step 1b-win): NO powershell.exe/pwsh wrapper
# ---------------------------------------------------------------------------
# rc.3 hole: a PEELED `Remove-Item -Recurse -Force`, a cmd.exe `del`/`rd`, or a
# PowerShell alias (`gci`, `del`) reaches the POSIX verb scanner, which has no
# subcommand to match, so it falls to safe-by-elimination -> T0 and mutates
# without a gate.  The wrapped PS lane (`_check_powershell_command`) only fires
# when base_cmd is an interpreter, so it never sees these.  This lane inverts the
# default to conservative DEFAULT-DENY *scoped to recognized Windows tokens*: an
# unknown verb / cmdlet / subcommand in a Windows context -> T3, mirroring the
# `_check_script_file` (unreadable -> T3) and `_check_package_manager`
# (unresolvable -> T3) fallbacks.  It is deliberately NOT applied to arbitrary
# bash tokens: an unrecognized base_cmd returns None so POSIX classification is
# untouched.

# cmd.exe single-token builtins that MUTATE filesystem/system state -> T3.
# `del`/`rd`/`erase`/`ren`/`rename`/`move` also appear as PS aliases below; both
# paths yield T3 (the documented del/rd collision).  `rmdir` is pre-empted by
# COMMAND_ALIASES (T3) before this lane and is intentionally omitted here.
_CMD_MUTATIVE_BUILTINS: FrozenSet[str] = frozenset({
    "del", "erase", "rd", "ren", "rename", "move", "format",
    "attrib", "taskkill", "shutdown", "diskpart", "cipher",
    "takeown", "icacls",
})

# cmd.exe single-token builtins that only READ/observe state -> T0.  Several
# overlap POSIX read commands already in READ_ONLY_BASE_CMDS (find/type/echo/
# tree/more/whoami/hostname); those are caught earlier and never reach this lane
# -- listed here for spec fidelity, harmless because unreachable.
_CMD_READ_BUILTINS: FrozenSet[str] = frozenset({
    "dir", "type", "findstr", "find", "where", "echo", "ver",
    "whoami", "hostname", "ipconfig", "systeminfo", "tasklist",
    "query", "netstat", "tree", "more", "fc", "comp",
})

# cmd.exe TWO-token builtins: base -> (mutative subcommands, read subcommands).
# An unrecognized subcommand on a recognized base is default-denied to T3.
_CMD_SUBCOMMAND_BUILTINS: "Dict[str, Tuple[FrozenSet[str], FrozenSet[str]]]" = {
    "reg": (
        frozenset({"delete", "add", "import", "restore", "load", "unload", "copy", "save"}),
        frozenset({"query", "export", "compare"}),
    ),
    "sc": (
        frozenset({"create", "delete", "config", "start", "stop", "pause",
                   "continue", "control", "failure", "sdset"}),
        frozenset({"query", "queryex", "qc", "getdisplayname", "enumdepend", "sdshow"}),
    ),
    "vssadmin": (
        frozenset({"delete", "create", "resize", "revert", "add"}),
        frozenset({"list", "query"}),
    ),
}

# PowerShell aliases (Microsoft's verbatim alias table) -> (kind, cmdlet).
# kind: "read" -> T0, "change" -> T3.  Navigation/output aliases (cd/sl/chdir,
# cls/clear, write/echo) resolve to Set-Location/Clear-Host/Write-Output, which
# do NOT destroy or grant, so they are "read" (T0) -- consistent with the tier
# philosophy that T3 gates destruction/grant, not every "set"/"write" verb.
# rm/rmdir/cp/mv are pre-empted by COMMAND_ALIASES before this lane; listed for
# fidelity, unreachable, and consistent (all -> T3).
_PS_ALIASES: "Dict[str, Tuple[str, str]]" = {
    "rm": ("change", "Remove-Item"), "del": ("change", "Remove-Item"),
    "erase": ("change", "Remove-Item"), "rd": ("change", "Remove-Item"),
    "rmdir": ("change", "Remove-Item"),
    "ls": ("read", "Get-ChildItem"), "dir": ("read", "Get-ChildItem"),
    "gci": ("read", "Get-ChildItem"),
    "cat": ("read", "Get-Content"), "type": ("read", "Get-Content"),
    "gc": ("read", "Get-Content"),
    "cp": ("change", "Copy-Item"), "copy": ("change", "Copy-Item"),
    "cpi": ("change", "Copy-Item"),
    "mv": ("change", "Move-Item"), "move": ("change", "Move-Item"),
    "mi": ("change", "Move-Item"),
    "ni": ("change", "New-Item"),
    "ren": ("change", "Rename-Item"), "rename": ("change", "Rename-Item"),
    "rni": ("change", "Rename-Item"),
    "sl": ("read", "Set-Location"), "cd": ("read", "Set-Location"),
    "chdir": ("read", "Set-Location"),
    "cls": ("read", "Clear-Host"), "clear": ("read", "Clear-Host"),
    "write": ("read", "Write-Output"), "echo": ("read", "Write-Output"),
    "ri": ("change", "Remove-Item"), "rp": ("change", "Remove-ItemProperty"),
    "clc": ("change", "Clear-Content"), "cli": ("change", "Clear-Item"),
    "clp": ("change", "Clear-ItemProperty"), "si": ("change", "Set-Item"),
    "sp": ("change", "Set-ItemProperty"), "spps": ("change", "Stop-Process"),
    "spsv": ("change", "Stop-Service"), "sasv": ("change", "Start-Service"),
    "irm": ("change", "Invoke-RestMethod"),
    "gi": ("read", "Get-Item"), "gp": ("read", "Get-ItemProperty"),
    "gl": ("read", "Get-Location"), "gcm": ("read", "Get-Command"),
    "gal": ("read", "Get-Alias"), "ghy": ("read", "Get-History"),
    "gps": ("read", "Get-Process"), "gsv": ("read", "Get-Service"),
    "sls": ("read", "Select-String"), "ft": ("read", "Format-Table"),
    "fl": ("read", "Format-List"), "fw": ("read", "Format-Wide"),
    "?": ("read", "Where-Object"), "where": ("read", "Where-Object"),
}

# Bare PowerShell execution aliases: recognizing them enters this lane so the
# obfuscation scan (Step 0) can escalate them to T3 (arbitrary code execution).
_PS_BARE_OBFUSCATION_ALIASES: FrozenSet[str] = frozenset({"iex", "iwr", "icm"})

# A PascalCase Verb-Noun token (`Remove-Item`, `Frobnicate-Thing`) is a
# PowerShell cmdlet by casing alone -- distinguishes a real cmdlet from a
# lowercase hyphenated POSIX command (`docker-compose`, `pre-commit`) whose verb
# is not a PS verb.  Matched on the RAW (original-case) base token.
_PS_PASCAL_CMDLET_RE = _re.compile(r"^[A-Z][A-Za-z]*-[A-Z][A-Za-z0-9]*$")


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
# Direct filesystem mutation in lexed source (script-file "code" lane)
# ---------------------------------------------------------------------------
# The lexer lane used to run ONLY the exec-sink detector, on the premise that a
# real mutation in a source language always reaches the shell through an exec
# sink.  That premise is false for the filesystem API: ``fs.rmSync(p, {recursive:
# true})`` deletes without spawning anything, so ``node -e "require('fs').
# rmSync(...)"`` classified T3 (the inline path runs the universal pattern set)
# while the SAME call inside ``node script.js`` classified T0.  These patterns
# close that divergence for the calls that mutate directly.
#
# They are run on the ``verb_view`` projection -- comments blanked and string
# CONTENTS blanked -- which is what makes them safe to add: the false positives
# the exec-sink-only design avoided came from matching text inside a comment or
# a string literal, and that text no longer exists in this view.
#
# Two deliberate exclusions keep the lane consistent with how the shell lane
# treats the same effects: directory CREATION (``fs.mkdirSync``) is omitted
# because ``mkdir`` into the working tree is explicitly classified T0 by
# ``_mkdir_targets_sensitive_path``, and stream/handle OPENING
# (``createWriteStream``, ``fs.open``) is omitted because opening is not yet a
# write and the write itself is matched by its own method.
#
# The receiver requirement is the second false-positive gate: a match needs an
# fs-ish receiver (``fs.``, ``fsp.``, ``fsPromises.``, an inline
# ``require('fs').``, or a ``.promises.`` chain on either), OR a bare call whose
# name carries the ``Sync`` suffix of the exact Node API.  A destructured
# non-Sync import (``import { rm } from 'node:fs/promises'; await rm(p)``) is
# therefore NOT matched -- a bare ``rm(`` is indistinguishable from any local
# helper of that name in this projection, and matching it would cost more than
# it catches.  That is a stated residual, not a closed case.
_JS_FS_DELETE_METHODS = ("rmdirSync", "unlinkSync", "rmdir", "unlink", "rmSync", "rm")
_JS_FS_WRITE_METHODS = (
    "writeFileSync", "appendFileSync", "ftruncateSync", "truncateSync",
    "writevSync", "writeSync", "writeFile", "appendFile", "ftruncate",
    "truncate", "writev", "write",
)
_JS_FS_MUTATE_METHODS = (
    "copyFileSync", "symlinkSync", "renameSync", "linkSync", "copyFile",
    "symlink", "rename", "cpSync", "link", "cp",
)
_JS_FS_PERM_METHODS = (
    "lchmodSync", "lchownSync", "chmodSync", "chownSync", "lchmod", "lchown",
    "chmod", "chown",
)

_JS_FS_RECEIVER = r"(?:(?:^|[^\w$.])(?:fs|fsp|fsPromises)|require\s*\([^)]*\))"


def _js_fs_mutation_pattern(methods: Tuple[str, ...]) -> "_re.Pattern":
    """Compile the receiver-qualified + bare-``Sync`` matcher for *methods*."""
    alternation = "|".join(methods)
    sync_methods = [m for m in methods if m.endswith("Sync")]
    sync_alternation = "|".join(sync_methods)
    return _re.compile(
        rf"{_JS_FS_RECEIVER}\s*\.\s*(?:promises\s*\.\s*)?(?:{alternation})\s*\("
        rf"|(?:^|[^\w$.])(?:{sync_alternation})\s*\("
    )


_JS_DIRECT_MUTATION_PATTERNS: Tuple[Tuple["_re.Pattern", str, str], ...] = (
    (_js_fs_mutation_pattern(_JS_FS_DELETE_METHODS), "fs-delete", "FILE_DELETION"),
    (_js_fs_mutation_pattern(_JS_FS_WRITE_METHODS), "fs-write", "FILE_WRITE"),
    (_js_fs_mutation_pattern(_JS_FS_MUTATE_METHODS), "fs-mutate", "FILE_MUTATION"),
    (_js_fs_mutation_pattern(_JS_FS_PERM_METHODS), "fs-chmod", "PERMISSION_MOD"),
)

# Keyed by ``LanguageSpec.name`` so a language with no entry is unaffected.
# Only the JS family is registered: ruby/perl/php carry the SAME divergence
# (``FileUtils.rm_rf``, ``File.delete``, ``unlink``) and are deliberately left
# open here rather than closed untested, since each additional grammar widens
# the false-positive surface on a shared code path.
_DIRECT_MUTATION_PATTERNS_BY_LANGUAGE: Dict[str, Tuple[Tuple["_re.Pattern", str, str], ...]] = {
    "javascript": _JS_DIRECT_MUTATION_PATTERNS,
}


_JS_MUTATION_METHOD_NAMES_RE = _re.compile(
    r"\b(?:"
    + "|".join(
        _JS_FS_DELETE_METHODS + _JS_FS_WRITE_METHODS
        + _JS_FS_MUTATE_METHODS + _JS_FS_PERM_METHODS
    )
    + r")\b"
)


def _may_mutate_directly(content: str, language: str) -> bool:
    """False only when *content* provably holds no direct-mutation match.

    Lexing blanks characters and never creates them, so a mutation method name
    absent from the raw text is absent from every view and the per-line scan
    can be skipped.  A language with no table has nothing to scan.
    """
    if language not in _DIRECT_MUTATION_PATTERNS_BY_LANGUAGE:
        return False
    return _JS_MUTATION_METHOD_NAMES_RE.search(content) is not None


def _scan_direct_mutation(code: str, language: str) -> "Optional[Tuple[str, str]]":
    """Return ``(label, category)`` for a direct filesystem mutation, or None.

    *code* must be a ``verb_view`` line (comments and string contents blanked);
    passing raw source would reintroduce the comment/string false positives this
    detector depends on the projection to remove.
    """
    for pattern, label, category in _DIRECT_MUTATION_PATTERNS_BY_LANGUAGE.get(language, ()):
        if pattern.search(code):
            return (label, category)
    return None


# ---------------------------------------------------------------------------
# Exec-sink string-argument extraction (SHARED: inline code path + script-file
# code lane)
# ---------------------------------------------------------------------------
# ``_scan_exec_sink_string_args`` is the single detector both the inline
# ``-c``/``-e`` path (``_check_inline_code``) and the script-file "code" lane
# (``_classify_script_content_by_regex``) call, so exec-sink detection cannot
# diverge between them.
#
# The problem it closes: a command handed to a subprocess sink as a STRING
# LITERAL -- ``execSync("kubectl delete deployment foo")`` -- is invisible to
# the verb scanner because the quotes make the whole command a single token.
# The inline path caught the sink CALL via ``_UNIVERSAL_DANGEROUS_PATTERNS``,
# but the script-file lane never ran those patterns, so ``node deploy.js`` with
# an ``execSync(...)`` mutation slipped through as READ_ONLY.
#
# False-positive mitigation (required): this is the PROCESS_EXECUTION subset of
# the universal patterns -- ONLY the call forms that take a command string --
# NOT the full pattern set (which would flag every legitimate fs.writeFile /
# fetch / URL literal in a real source file).  Escalation is gated on the
# EXTRACTED INNER command itself classifying mutative/blocked, so a benign
# ``execSync("ls")`` is not escalated.
#
# ``exec``/``system`` are intentionally generic (they match ruby/perl/php
# ``system(...)`` and node ``exec(...)``); the inner-command gate keeps the
# false-positive cost near zero -- ``regex.exec("literal")`` extracts
# ``literal``, which is not a mutative command, so nothing escalates.
_EXEC_SINK_STRING_ARG_RE = _re.compile(
    r"\b(?:execSync|execFileSync|execFile|spawnSync|spawn|shell_exec|passthru|"
    r"proc_open|system|popen|Popen|exec)\s*\(\s*"
    r"(?P<q>['\"])(?P<cmd>(?:[^'\"\\]|\\.)*)(?P=q)"
)
# Backtick / ``%x{...}`` shell execution (ruby / perl / php): the body IS the
# command handed to the shell.
_EXEC_SINK_BACKTICK_RE = _re.compile(r"`([^`\n]{2,})`")
_EXEC_SINK_PERCENT_X_RE = _re.compile(r"%x[\{\(\[]([^\}\)\]\n]{2,})[\}\)\]]")


_EXEC_SINK_ARGV_RE = _re.compile(
    r"\b(?:spawnSync|spawn|execFileSync|execFile)\s*\(\s*"
    r"(?P<q>['\"])(?P<prog>(?:[^'\"\\]|\\.)*)(?P=q)\s*,\s*\[(?P<args>[^\]]*)\]"
)
_ARGV_LITERAL_ITEM_RE = _re.compile(r"""\s*(?P<q>['"])(?P<item>(?:[^'"\\]|\\.)*)(?P=q)\s*""")
_ARGV_SHELL_PROGRAMS: FrozenSet[str] = frozenset({"cmd", "powershell", "pwsh"})


def _scan_exec_sink_argv(
    code: str, family: str, cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Judge ``spawn``/``execFile`` calls by the argv they run; a shell handed a computed argv asks."""
    import os
    from .shell_unwrapper import _SHELLS
    for match in _EXEC_SINK_ARGV_RE.finditer(code):
        program = match.group("prog")
        items = [part for part in match.group("args").split(",") if part.strip()]
        literals = [_ARGV_LITERAL_ITEM_RE.fullmatch(part) for part in items]
        if all(literals):
            argv = (program, *(literal.group("item") for literal in literals))
            if not _argv_command_is_mutative(argv, cwd=cwd, _depth=_depth + 1):
                continue
            reason = f"exec-sink argv runs a mutative command: {shlex.join(argv)}"
        else:
            name = os.path.basename(program.replace("\\", "/")).lower().removesuffix(".exe")
            if name not in _SHELLS and name not in _ARGV_SHELL_PROGRAMS:
                continue
            reason = f"exec-sink hands shell '{program}' an argv computed at runtime"
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="exec-sink-argv",
            cli_family=family,
            confidence="medium",
            reason=reason,
        )
    return None


def _scan_exec_sink_string_args(
    code: str, family: str, shell_backticks: bool = True,
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Extract shell commands handed to exec sinks and re-classify them.

    Shared by ``_check_inline_code`` (inline ``-c``/``-e`` payloads) and
    ``_classify_script_content_by_regex`` (``node deploy.js`` script files) so
    exec-sink detection cannot diverge between the two lanes.

    For each exec-sink call whose first argument is a string literal
    (``execSync("...")``, ``system("...")``, ``spawn("...")``, ...) and for each
    backtick / ``%x{}`` shell body, the extracted command is re-classified with
    ``is_blocked_command`` and ``detect_mutative_command``.  A MUTATIVE result
    is returned ONLY when the inner command is itself blocked or mutative -- a
    benign inner command (``execSync("ls")``) yields ``None`` (false-positive
    mitigation).

    ``shell_backticks`` records whether a backtick delimits SHELL EXECUTION
    (ruby/perl/php, and shell inline payloads -- the default) or a STRING /
    TEMPLATE LITERAL (JavaScript).  When False, the backtick and ``%x{}``
    bodies are NOT treated as commands -- a JS template literal such as
    ```// ... npm install ...``` is not a shell invocation and must
    not be re-classified, which was a false-positive source.

    ``cwd`` is forwarded to the inner ``detect_mutative_command`` re-
    classification: an exec-sink string extracted from a script/npm-run body
    (``execSync('node engine/build-data.mjs')``) may itself be a RELATIVE
    script invocation, and that relative path resolves against the directory
    the OUTER script/body was read from, not the hook's own cwd.  ``None``
    (the default) keeps the previous, hook-cwd-only behavior for the inline
    ``-c``/``-e`` caller, which has no surrounding script file to anchor to.
    """
    argv_result = _scan_exec_sink_argv(code, family, cwd=cwd, _depth=_depth)
    if argv_result is not None:
        return argv_result
    candidates: List[str] = []
    for m in _EXEC_SINK_STRING_ARG_RE.finditer(code):
        candidates.append(m.group("cmd"))
    if shell_backticks:
        for m in _EXEC_SINK_BACKTICK_RE.finditer(code):
            candidates.append(m.group(1))
        for m in _EXEC_SINK_PERCENT_X_RE.finditer(code):
            candidates.append(m.group(1))

    for raw_inner in candidates:
        inner = raw_inner.strip()
        if not inner:
            continue
        if _is_blocked_command is not None:
            blocked = _is_blocked_command(inner)
            if blocked.is_blocked:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb="exec-sink-blocked-cmd",
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"exec-sink argument is a blocked command: "
                        f"{blocked.category}"
                    ),
                )
        inner_result = detect_mutative_command(inner, cwd=cwd, _depth=_depth)
        if inner_result.is_mutative:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=inner_result.verb,
                dangerous_flags=inner_result.dangerous_flags,
                cli_family=family,
                confidence=inner_result.confidence,
                reason=(
                    f"exec-sink argument '{inner}' -> {inner_result.reason}"
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Layer 3: Heuristic safety classification
# ---------------------------------------------------------------------------
_SUSPICIOUS_HEURISTICS: Tuple[Tuple[_re.Pattern, str], ...] = (
    (_re.compile(r"\b(base64|b64encode|b64decode|atob|btoa)\b"), "encoding"),
    (_re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "ip-address"),
)

MAX_SAFE_INLINE_LENGTH = 150
MAX_NORMAL_INLINE_LENGTH = 500

_JS_INLINE_INTERPRETERS: FrozenSet[str] = frozenset({"node", "deno", "bun"})
# Long-JavaScript effects Layers 1-3a miss: built code, a network or process
# module, an fs change outside the delete/write/append patterns.
_JS_UNSEEN_EFFECT_RE = _re.compile(
    r"\b(?:eval|Function|import)\s*\(|\bnew\s+Function\b|\bvm\b|\brequire\s*\(\s*[^'\"\s]"
    r"|\brequire\s*\(\s*['\"](?:node:)?(?:net|https?|http2|dgram|tls|dns|cluster|worker_threads|inspector|child_process)['\"]"
    r"|\bprocess\.(?:binding|dlopen|kill)\b"
    r"|\b(?:rename|copyFile|cp|mkdir|mkdtemp|symlink|link|truncate|ftruncate|utimes|chown|lchown|open|"
    r"createWriteStream|writev?)(?:Sync)?\s*\("
)
# JavaScript effects judged at every length and in every lane.
_JS_NETWORK_MODULE_RE = _re.compile(
    r"""(?:\brequire\s*\(\s*|\bimport\s*\(\s*|\bfrom\s+|\bimport\s+)['"](?:node:)?"""
    r"""(?:net|https?|http2|dgram|tls|cluster|worker_threads|inspector|vm)['"]"""
)
_JS_HIDDEN_CALL_RE = _re.compile(
    r"\b(?:eval|Function)\s*\(|\bnew\s+Function\b|\bimport\s*\(|\brequire\s*\(\s*[^'\"`\s)]"
    r"|\bprocess\s*\.\s*(?:binding|dlopen)\b|\brequire\s*\([^)]*\)\s*\["
    r"|\bfetch\s*\([^)]*\b(?:body|method)\s*:|\bcreateRequire\b"
)
# Deno and Bun reach the filesystem, processes and the network through their own
# globals; only the members named here are reads, any other member asks.
_JS_RUNTIME_EFFECT_RE = _re.compile(
    r"\bDeno\s*(?:\?\.\s*)?\[|\bBun\s*(?:\?\.\s*)?\["
    r"|\bDeno\s*\??\.\s*(?!(?:readTextFile|readFile|readDir|stat|lstat|realPath|readLink)(?:Sync)?\b"
    r"|(?:cwd|args|env|version|build|pid|ppid|noColor|inspect|mainModule|execPath|exit|hostname|osRelease"
    r"|memoryUsage|stdout|stderr|stdin|errors)\b)"
    r"|\bBun\s*\??\.\s*(?!(?:version|revision|argv|env|main|which|sleep|sleepSync|nanoseconds|hash|inspect"
    r"|deepEquals|escapeHTML|stringWidth)\b)"
)
_JS_RUNTIME_MODULE_RE = _re.compile(
    r"""(?:\brequire\s*\(\s*|\bimport\s*\(\s*|\bfrom\s+|\bimport\s+)['"]"""
    r"""(?:bun(?::[\w-]+)?|(?:https?|jsr|npm):[^'"]*)['"]"""
)
_JS_EFFECT_BINDING_RE = _re.compile(
    r"""\b(?:const|let|var)\s+(\w+)\s*=\s*(?:await\s+)?(?:require|import)\s*\(\s*['"]"""
    r"""(?:node:)?(?:fs|fs/promises|net|child_process)['"]\s*\)"""
    r"""|\bimport\s+(?:\*\s+as\s+)?(\w+)\s+from\s+['"](?:node:)?(?:fs|fs/promises|net|child_process)['"]"""
)
# Ruby, perl, php, lua and R have no parser here: their code asks unless every
# statement prints only literals (no interpolation), numbers and arithmetic.
_UNPARSED_CODE_INTERPRETERS: FrozenSet[str] = frozenset({"ruby", "perl", "php", "lua", "luajit", "rscript", "r"})
_PRINT_ONLY_STATEMENT_RE = _re.compile(
    r"""\s*(?:(?:print|puts|pp|p|echo|say|printf)\b"""
    r"""(?:'(?:[^'\\]|\\['\\])*'|"(?:[^"\\$#@{`]|\\[nt"\\])*"|\d+(?:\.\d+)?|[\s+\-*/%.,()])*)?\s*"""
)


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
# tee write discriminator (stands aside unless the destination is sensitive)
# ============================================================================
# `tee` writes stdin verbatim onto every file argument it is given, and carries
# no verb in MUTATIVE_VERBS -- it falls through every step of detection and
# classifies READ_ONLY by elimination regardless of what it targets. So a
# direct write onto a privileged OS directory or onto Gaia's own live hooks
# tree needs a decision of its own.
#
# It cannot be that decision's home in COMMAND_ALIASES. That table classifies by
# TOOL NAME, and `tee` is neither mutative nor harmless by its name -- its
# ARGUMENT decides. Anchoring the name and subtracting the safe cases back out
# inverts the default: with no file argument at all, there is nothing to subtract
# and the command stays gated. But an absent file argument is not a write that
# could not be analyzed; it is the whole of the READ idiom. `cmd | tee` copies
# stdin to stdout and writes nothing, and because the validator splits a pipeline
# on its operators and classifies each component, the bare `tee` component denied
# the entire pipeline -- charging consent for looking at what flows through it.
#
# `git tag` and `git config` had the identical shape (one spelling, two modes,
# separated by arguments rather than by name) and took the identical answer: a
# discriminator that STANDS ASIDE by default and only ever ADDS a verdict. Every
# form it does not recognize as a sensitive write returns None and keeps the
# classification it already had, so a mistake here cannot start charging for a
# read. A target is sensitive when it resolves onto:
#
#   * a privileged OS directory (MKDIR_SENSITIVE_PATH_PREFIXES -- the same
#     set mkdir already gates, minus scratch space by the same reasoning), or
#   * Gaia's own live hooks tree (_gaia_hooks_root) -- a direct write onto the
#     security-enforcement code itself is sensitive on every machine Gaia
#     runs on, regardless of whether that tree happens to sit under one of
#     the fixed OS prefixes; docs (``.md``) are exempt because they do not
#     execute, mirroring the same carve-out in protected_path_guard.py for
#     the installed ``.claude/hooks`` tree -- this is that guard's
#     SOURCE-tree sibling, reached through the tier classifier instead of a
#     categorical block, because a source checkout is not `.claude/`, or
#
#   * a path under $HOME that is sensitive by its CONTENTS rather than by its
#     location (is_account_sensitive_path) -- `~/.ssh`, the shell rc files,
#     the credential stores.
#
# Everything else -- no file at all, a relative path, the working tree, the
# rest of the user's home, /tmp, the Gaia scratch directory -- is left exactly
# where it was.


def _tee_sensitive_targets(tokens: tuple) -> Tuple[str, ...]:
    """Return the tee file arguments that resolve onto a sensitive path.

    Scans all non-flag tokens after the base command (skipping `--` and
    tee's own valueless flags: -a/--append, -i, -p, --output-error[=MODE]).
    A path is sensitive when it is absolute and resolves onto a privileged OS
    directory or Gaia's own live hooks tree (see the module comment above).

    Args:
        tokens: Full token tuple from tokenize_command (includes base cmd).

    Returns:
        The sensitive targets, in the order they appear. Empty when there is
        no file argument at all, or when every one of them is an ordinary
        working-tree/scratch destination.
    """
    import os
    sensitive: List[str] = []
    seen_end_of_opts = False
    i = 1  # skip base_cmd at index 0
    while i < len(tokens):
        token = tokens[i]
        i += 1

        if token == "--":
            seen_end_of_opts = True
            continue

        if not seen_end_of_opts and token.startswith("-"):
            # tee's own flags take no separate value argument.
            continue

        # token is a file argument (positional, or after --)
        if is_account_sensitive_path(token):
            sensitive.append(token)
            continue

        if token.startswith("~/") or token == "~" or not os.path.isabs(token):
            # Every other home-relative or relative destination is ordinary
            # working-tree, cache or scratch space.
            continue

        norm = os.path.normpath(token)
        if any(
            norm == prefix or norm.startswith(prefix + "/")
            for prefix in MKDIR_SENSITIVE_PATH_PREFIXES
        ):
            sensitive.append(token)
            continue

        if not norm.endswith(".md"):
            hooks_root = _gaia_hooks_root()
            if hooks_root and (
                norm == hooks_root or norm.startswith(hooks_root + os.sep)
            ):
                sensitive.append(token)

    return tuple(sensitive)


IAM_BINDING_TOKEN_SUFFIXES: FrozenSet[str] = frozenset({
    "-iam-policy-binding",
})


def _check_iam_policy_binding(
    family: str,
    semantics: "CommandSemantics",
) -> "Optional[MutativeResult]":
    """Gate a cloud IAM binding change by the FORM of its subcommand token.

    Granting a capability must never classify below revoking it, and the verb
    scan cannot deliver that: `add-iam-policy-binding` splits onto `add`, absent
    from MUTATIVE_VERBS so `git add` stays free, while
    `remove-iam-policy-binding` splits onto `remove` and gates. The split also
    stops at semantic_index <= 2, so a deeper path reaches neither.

    By form rather than one anchor per surface because the surfaces are
    open-ended: every gcloud group owning a resource grows the same pair, so an
    enumeration reads as coverage while the newest surface stays ungated. The
    read forms (`get-iam-policy`, `describe`, `list`) do not carry the suffix,
    which is what keeps the rule from taxing them.
    """
    if family != "cloud":
        return None

    for token in semantics.non_flag_tokens:
        lowered = token.lower()
        if not any(
            lowered.endswith(suffix) and lowered != suffix
            for suffix in IAM_BINDING_TOKEN_SUFFIXES
        ):
            continue
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb=lowered,
            cli_family=family,
            confidence="high",
            reason=(
                f"IAM policy binding change '{lowered}' mutates who holds a "
                f"capability, in either direction"
            ),
        )
    return None


# A redirect's destination, taken from the operator onward, the clobber
# override `>|` included: it writes the same file as `>`, and a gate one
# operator away is not a gate. `2>&1` yields the fd `1`, which no path
# predicate matches, so the fd forms need no exclusion.
_REDIRECT_TARGET_RE = _re.compile(r"\d?>>?\|?\s*&?\s*([^\s;|&]+)")


def is_account_sensitive_path(token: str) -> bool:
    """True when writing *token* hands out access to the user's own account.

    Deliberately NOT MKDIR_SENSITIVE_PATH_PREFIXES: that set is sensitive by
    LOCATION (privileged OS directories). The account set is sensitive by
    CONTENTS and lives in ``sensitive_paths``, the one list every lane reads.
    """
    from .sensitive_paths import is_account_path

    return is_account_path(token)


def account_path_redirect_target(command: str) -> str:
    """The first redirect or HTTP-client output destination in *command* that grants account access."""
    from .sensitive_read_guard import copy_written_paths, http_client_written_paths

    redirected = [match.group(1).strip("\"'") for match in _REDIRECT_TARGET_RE.finditer(command)]
    for target in redirected + http_client_written_paths(command) + copy_written_paths(command):
        if is_account_sensitive_path(target):
            return target
    return ""


def _check_account_path_redirect(command: str) -> "Optional[MutativeResult]":
    """Classify a redirect whose destination grants account access.

    The destination, not the verb, is what makes this mutative:
    `echo 'ssh-rsa ...' >> ~/.ssh/authorized_keys` grants login access while
    its base command is a read.
    """
    target = account_path_redirect_target(command)
    if target:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="account-path-write",
            cli_family="system",
            confidence="high",
            reason=(
                f"Redirect writes '{target}', which grants access to the "
                f"user's own account"
            ),
        )
    return None


def _check_tee_write(
    base_cmd: str,
    tokens: tuple,
) -> "Optional[MutativeResult]":
    """Classify a `tee` write onto a sensitive path, or return None to stand aside.

    Stands aside for every other form -- including the bare stdin-to-stdout
    passthrough, which writes nothing -- so the read side of this tool keeps
    whatever verdict it already had.
    """
    if base_cmd != "tee":
        return None

    sensitive = _tee_sensitive_targets(tokens)
    if not sensitive:
        return None

    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="tee",
        dangerous_flags=_scan_dangerous_flags(list(tokens), base_cmd),
        cli_family="system",
        confidence="high",
        reason=(
            f"tee writes directly onto a sensitive path "
            f"({', '.join(sensitive)})"
        ),
    )


# ============================================================================
# rm -- scratch-directory tier override (T0 inside Gaia scratch, T3 otherwise)
# ============================================================================
# `rm` (including `rm -rf`) is normally a MUTATIVE command alias requiring T3
# approval, and the truly catastrophic forms (`rm -rf /`, `/*`, `~`) are
# permanently blocked by the `rm_critical` floor in blocked_commands.py, which
# runs BEFORE this detector (bash_validator.py phase 3a).
#
# Narrowly-scoped exception (Option A): `rm` is downgraded to T0 (no approval)
# ONLY when BOTH conditions hold -- EVERY target path resolves strictly inside
# the Gaia scratch directory (`~/.gaia/scratch`, or the equivalent under a
# GAIA_DATA_DIR override), AND nothing the command reaches carries the evidence
# of a durable store.  The floor cooperates via a matching, lenient
# scratch-confinement check in blocked_commands.py so scratch operations reach
# this detector instead of being swallowed by the catastrophic `~` patterns.
#
# The containment half is STRICT and fail-closed (see _rm_targets_only_scratch):
# globs, `..` traversal, symlinks escaping scratch, or any single out-of-scratch
# path all keep the command at T3.  `-rf` recursion is allowed only when
# confined to scratch.
#
# WHY LOCATION ALONE IS NOT THE JUDGMENT, and what the second half exists for:
# the exemption used to reason purely from the path, so an object whose value
# had nothing to do with where it sat became disposable by being moved there.
# The counterexample was on disk: a 185 MB copy of the user's memory database,
# 1358 curated rows, deleted at T0.  That mis-judgement did not merely leave the
# deletion ungated -- it made it IMPOSSIBLE TO CONSENT TO, because the approval
# registrar (gaia/approvals/command_set.py::_validate_commands) refuses to issue
# a grant for anything below T3.  Classifier and registrar are the same
# judgement, so the safer something is judged, the less it can be deliberately
# approved.  Repairing the judgement closes both.
#
# THE EVIDENCE IS INTRINSIC, so nothing has to be declared: an object is treated
# as valuable when its OWN FIRST BYTES carry a durable-store format signature
# (_DURABLE_STORE_SIGNATURES) AND it is larger than _DURABLE_STORE_MIN_BYTES.
# A signature survives renaming, moving and mislabelling, which an extension
# does not; magnitude separates a store that ACCUMULATED state from one a
# command just created.  Both axes are required, and the measurement is why:
# the live scratch tree held 174 SQLite files, of which 172 were <= 917 KB
# throwaway bootstraps and browser caches and 2 were the ~185 MB memory copies.
# Signature alone would have put all 174 behind a prompt, training approval
# without reading -- worse than no gate; magnitude alone would gate any large
# ephemeral blob.  The SQLite change counter was measured and rejected as a
# third axis: a copy does not inherit write history (4 vs 2).
#
# COST, measured on the live tree: 0.01 ms for one file, 0.03 ms for an
# ordinary turn directory, 25.6 ms for the whole 2741-entry scratch root.  The
# walk stats every entry but OPENS only files already big enough to matter (6
# of 1963), which is what keeps the common case free.
#
# RESIDUALS, deliberately not covered: a curated store SMALLER than the
# magnitude floor, a valuable object in a format carrying no signature (a large
# plain-text corpus), and a git repository holding uncommitted work.

_RM_GLOB_CHARS: FrozenSet[str] = frozenset("*?[]{}")


def _gaia_scratch_root() -> "str | None":
    """Return the canonical (realpath) Gaia scratch directory, or None.

    Reads the location from gaia.paths.resolver.scratch_dir() so a
    GAIA_DATA_DIR override is honoured, then canonicalises it with
    os.path.realpath so the comparison in _rm_targets_only_scratch is done
    against a symlink-resolved absolute root.

    Fail-closed: any failure to import the resolver or resolve the path
    returns None, which makes the rm scratch-exception decline (stay T3).
    """
    import os
    try:
        from gaia.paths.resolver import scratch_dir
        return os.path.realpath(str(scratch_dir()))
    except Exception:
        return None


def _scratch_entry(real: str) -> "str | None":
    """Return the turn entry (``<scratch>/<first component>``) holding *real*, or None for the shared root or outside it."""
    import os
    scratch = _gaia_scratch_root()
    if not scratch or not real.startswith(scratch + os.sep):
        return None
    return os.path.join(scratch, os.path.relpath(real, scratch).split(os.sep, 1)[0])


@dataclass(frozen=True)
class LocalWorkCaller:
    """Who a local-work write runs for and the directory its relative paths resolve against (None when unproven)."""

    agent_id: str
    cwd: Optional[str]


def _owned_by(entry: "str | None", caller: "Optional[LocalWorkCaller]") -> bool:
    """True when *entry* is a scratch entry named ``<agent_id>`` or ``<agent_id>.<token>`` for the caller's agent."""
    import os
    if not entry or caller is None or not caller.agent_id:
        return False
    return os.path.basename(entry).split(".", 1)[0] == caller.agent_id


def _gaia_hooks_root() -> "str | None":
    """Return the canonical (realpath) Gaia hooks directory this module ships in.

    Resolved from ``__file__`` (this module lives at
    ``hooks/modules/security/mutative_verbs.py``), so it names whichever hooks
    tree is ACTUALLY governing this process -- a source checkout under active
    development, an installed dev-pack, or a published package -- rather than a
    single hardcoded install location that would be wrong for every layout but
    one. Used by the tee sensitive-path override below: a direct write onto
    Gaia's own security-enforcement code is a sensitive target whose location
    cannot be a fixed prefix (unlike ``/etc`` or ``/usr``, which are the same
    path on every machine), so it is resolved live instead of declared.

    Fail-closed, mirroring ``_gaia_scratch_root``: any resolution failure
    returns None, which makes the sensitivity check decline to match rather
    than silently treat every path as safe.
    """
    import os
    try:
        here = os.path.dirname(os.path.realpath(__file__))
        return os.path.realpath(os.path.join(here, "..", ".."))
    except Exception:
        return None


def _rm_scratch_confined_targets(tokens: tuple, caller: "Optional[LocalWorkCaller]" = None) -> "Tuple[str, ...] | None":
    """Return the canonical rm targets when every one is confined to scratch.

    STRICT and fail-closed. Returns the resolved paths only when ALL of the
    following hold:
      (a) at least one positional (non-flag) path argument is present;
      (b) NO token contains an unexpanded glob metacharacter (``*?[]{}``) or
          a parent-traversal component (``..``);
      (c) each path, resolved against the caller's cwd (never the hook's), lies
          in a scratch entry the caller owns (``_owned_by``); ``rmdir -p`` also
          removes every parent its operand names, so those are targets too.

    Any ambiguity -- no positional path, an unresolvable scratch root, a glob,
    a ``..``, an unexpandable ``~user``, or a single path outside scratch --
    returns None so the command keeps its T3 classification.  realpath (not
    normpath) is used deliberately so a symlink inside scratch that points
    outside is detected and does NOT qualify for the T0 exception.

    Args:
        tokens: Full token tuple from tokenize_command (index 0 is the base
            command and is skipped).

    Returns:
        The canonical (realpath) target paths, or None when confinement to
        scratch cannot be established.
    """
    import os
    seen_end_of_opts = False
    removes_parents = False
    path_tokens = []
    i = 1  # skip base_cmd at index 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "--":
            seen_end_of_opts = True
            continue
        if not seen_end_of_opts and token.startswith("-"):
            removes_parents = removes_parents or token == "--parents" or (
                not token.startswith("--") and "p" in token
            )
            continue
        path_tokens.append(token)

    if not path_tokens:
        return None  # (a) -- no clear path target
    if removes_parents and os.path.basename(tokens[0]) == "rmdir":
        for token in list(path_tokens):
            head = os.path.dirname(token.rstrip(os.sep))
            while head:
                path_tokens.append(head)
                head = "" if head == os.sep else os.path.dirname(head)

    resolved = []
    for token in path_tokens:
        # (b) reject unexpanded globs and parent traversal outright.
        if any(ch in _RM_GLOB_CHARS for ch in token):
            return None
        if ".." in token:
            return None
        real = _resolve_local_work_path(token, caller.cwd if caller else None)
        if real is None or not _owned_by(_scratch_entry(real), caller) or _shares_inode(real):
            return None
        resolved.append(real)
    return tuple(resolved)


_LOCAL_WORK_UNRESOLVED_CHARS: FrozenSet[str] = frozenset("*?[]{}$`")


def _never_free_paths() -> "Tuple[Tuple[str, ...], Tuple[str, ...]]":
    """Return the harness-trusted files and directories (database, journals, backups, drafts) no root may cover."""
    import os
    from pathlib import Path
    from gaia.paths.resolver import data_dir, db_path
    files, dirs = [], []
    for db in {db_path(), Path.home() / ".gaia" / "gaia.db"}:
        files += [os.path.realpath(f"{db}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")]
        dirs.append(os.path.realpath(db.parent / "backups"))
    for base in {data_dir(), Path.home() / ".gaia"}:
        dirs += [os.path.realpath(base / "contract_drafts"), os.path.realpath(base / "backups")]
    return tuple(files), tuple(dirs)


_KERNEL_VIEW_ROOTS: Tuple[str, ...] = ("/proc", "/dev")


def _enters_kernel_view(path: str) -> bool:
    """True when resolving *path* passes through /proc or /dev, whose links name the resolving process's cwd, root and fds."""
    import os
    pending = [part for part in reversed(path.split(os.sep)) if part]
    current, hops = os.sep, 0
    while pending:
        part = pending.pop()
        if part in (".", ".."):
            current = current if part == "." else os.path.dirname(current)
            continue
        current = os.path.join(current, part)
        if any(current == root or current.startswith(root + os.sep) for root in _KERNEL_VIEW_ROOTS):
            return True
        if hops < 40 and os.path.islink(current):
            try:
                target = os.readlink(current)
            except OSError:
                return True
            hops += 1
            pending.extend(part for part in reversed(target.split(os.sep)) if part)
            current = os.sep if os.path.isabs(target) else os.path.dirname(current)
    return False


def _resolve_local_work_path(token: str, cwd: "Optional[str]", follow_last: bool = True) -> "str | None":
    """Return the file the kernel would open for *token* (only its parent unless follow_last), or None if unresolvable."""
    import os
    if not token or any(ch in _LOCAL_WORK_UNRESOLVED_CHARS for ch in token):
        return None
    expanded = os.path.expanduser(token)
    if expanded.startswith("~"):
        return None
    if not os.path.isabs(expanded):
        if not cwd or not os.path.isabs(cwd):
            return None
        expanded = os.path.join(cwd, expanded)
    if _enters_kernel_view(expanded):
        return None
    if follow_last:
        return os.path.realpath(expanded)
    parent, name = os.path.split(expanded.rstrip(os.sep))
    if name in ("", ".", ".."):
        return None
    return os.path.join(os.path.realpath(parent or os.sep), name)


def _local_work_root(real: str, caller: "Optional[LocalWorkCaller]", allow_top: bool = False) -> "str | None":
    """Return the caller-owned scratch entry or managed worktree holding *real* outside any ``.git``, or None (fail closed)."""
    import os
    try:
        files, dirs = _never_free_paths()
        from .protected_paths import is_protected_hook_path
        from .sensitive_paths import is_account_path
        if real in files or any(real == d or real.startswith(d + os.sep) for d in dirs):
            return None
        if is_protected_hook_path(real) or is_account_path(real):
            return None
    except Exception:
        return None
    entry = _scratch_entry(real)
    if entry is not None:
        if not _owned_by(entry, caller):
            return None
        top = entry
    elif real == _gaia_scratch_root():
        return None
    else:
        managed = _gaia_worktrees_root(real)
        if managed is None:
            return None
        top = os.path.dirname(real) if not allow_top else real
        while top != managed and top.startswith(managed + os.sep) and not os.path.lexists(os.path.join(top, ".git")):
            top = os.path.dirname(top)
        if top == managed or not top.startswith(managed + os.sep):
            return None
    if ".git" in os.path.relpath(real, top).split(os.sep):
        return None
    return top


def _confined_to_local_work(paths: "List[str | None]", caller: "Optional[LocalWorkCaller]") -> "Tuple[str, ...] | None":
    """Return the written or removed *paths* when each resolved into a caller-owned local-work root; reads need no owner."""
    if not paths or any(p is None or _local_work_root(p, caller) is None for p in paths):
        return None
    return tuple(paths)


_TREE_WALK_BUDGET = 20000


def _shares_inode(real: "str | None") -> bool:
    """True when *real* is an existing non-directory with another hard link, which a write through it also changes."""
    import os
    import stat
    try:
        status = os.lstat(real) if real else None
    except OSError:
        return False
    return status is not None and not stat.S_ISDIR(status.st_mode) and status.st_nlink > 1


def _tree_shares_inode(real: str) -> bool:
    """True when any file under *real* has another hard link, or the tree cannot be walked within budget (fail closed)."""
    import os
    seen = 0
    for top, _, files in os.walk(real):
        seen += len(files)
        if seen > _TREE_WALK_BUDGET or any(_shares_inode(os.path.join(top, name)) for name in files):
            return True
    return False


def _tree_carries_git(real: "str | None") -> bool:
    """True when the tree at *real* is or holds a ``.git`` entry, or cannot be walked within budget (fail closed)."""
    import os
    if real is None or os.path.basename(real) == ".git":
        return True
    if os.path.islink(real) or not os.path.isdir(real):
        return False
    seen = 0
    for _, dirs, files in os.walk(real, followlinks=True):
        seen += len(dirs) + len(files)
        if ".git" in dirs or ".git" in files or seen > _TREE_WALK_BUDGET:
            return True
    return False


# Options that cannot move where mv or cp write; -t, -T, -l, -s, -b and any
# unlisted option keep the alias verdict.
_MV_LOCAL_WORK_SHORT_FLAGS: FrozenSet[str] = frozenset("fivnu")
_MV_LOCAL_WORK_LONG_FLAGS: FrozenSet[str] = frozenset({
    "--force", "--interactive", "--no-clobber", "--verbose", "--update",
})
_CP_LOCAL_WORK_SHORT_FLAGS: FrozenSet[str] = frozenset("pvfniuLHPrRad")
_CP_LOCAL_WORK_LONG_FLAGS: FrozenSet[str] = frozenset({
    "--verbose", "--force", "--no-clobber", "--interactive", "--update",
    "--dereference", "--no-dereference", "--preserve", "--recursive", "--archive",
})
_CP_RECURSIVE_SHORT_FLAGS: FrozenSet[str] = frozenset("rRa")


def _local_work_operands(tokens: tuple, short: FrozenSet[str], long: FrozenSet[str]) -> "Tuple[List[str], Set[str]] | None":
    """Split mv/cp tokens into positionals and the option letters/words used, or None on an unlisted option."""
    positionals: List[str] = []
    used: Set[str] = set()
    end_of_options = False
    for token in tokens[1:]:
        if not end_of_options and token == "--":
            end_of_options = True
        elif not end_of_options and token.startswith("--"):
            name = token.split("=", 1)[0]
            if name not in long:
                return None
            used.add(name)
        elif not end_of_options and token.startswith("-") and token != "-":
            if not set(token[1:]) <= short:
                return None
            used.update(token[1:])
        else:
            positionals.append(token)
    return (positionals, used) if len(positionals) >= 2 else None


def _local_work_destinations(sources: List[str], destination: str, cwd: "Optional[str]") -> "List[str | None] | None":
    """Return the paths a copy or move of *sources* onto *destination* creates."""
    import os
    real_destination = _resolve_local_work_path(destination, cwd)
    if real_destination is None:
        return None
    if not os.path.isdir(real_destination):
        return [real_destination] if len(sources) == 1 else None
    written: "List[str | None]" = []
    for source in sources:
        name = os.path.basename(source.rstrip(os.sep))
        if name in ("", ".", "..") or source.rstrip(os.sep).endswith(os.sep + "."):
            return None
        written.append(_resolve_local_work_path(os.path.join(real_destination, name), None))
    return written


def _cp_local_work_written_paths(tokens: tuple, caller: LocalWorkCaller) -> "Tuple[str, ...] | None":
    """Return the paths a ``cp`` writes when all are the caller's local work; recursive only onto new paths, never a ``.git``."""
    import os
    parsed = _local_work_operands(tokens, _CP_LOCAL_WORK_SHORT_FLAGS, _CP_LOCAL_WORK_LONG_FLAGS)
    if parsed is None:
        return None
    (*sources, destination), used = parsed
    written = _local_work_destinations(sources, destination, caller.cwd)
    if written is None or any(map(_shares_inode, written)):
        return None
    read = [_resolve_local_work_path(source, caller.cwd) for source in sources]
    if used & (_CP_RECURSIVE_SHORT_FLAGS | {"--recursive", "--archive"}):
        if any(p is None or os.path.lexists(p) for p in written) or any(map(_tree_carries_git, read)):
            return None
    return _confined_to_local_work(written, caller)


def _mv_local_work_written_paths(tokens: tuple, caller: LocalWorkCaller) -> "Tuple[str, ...] | None":
    """Return every path an ``mv`` removes (link itself, not its target) or creates when all are the caller's local work."""
    parsed = _local_work_operands(tokens, _MV_LOCAL_WORK_SHORT_FLAGS, _MV_LOCAL_WORK_LONG_FLAGS)
    if parsed is None:
        return None
    (*sources, destination), _ = parsed
    written = _local_work_destinations(sources, destination, caller.cwd)
    if written is None or any(map(_shares_inode, written)):
        return None
    removed = [_resolve_local_work_path(source, caller.cwd, follow_last=False) for source in sources]
    if any(map(_tree_carries_git, removed)):
        return None
    return _confined_to_local_work(removed + written, caller)


# Stages that never create, move or extract a symlink; every stage of a line is
# classified before the first runs, so any other stage could redirect a freed write.
_PLANTS_NO_LINK: FrozenSet[str] = frozenset({
    "cd", "pushd", "popd", "mkdir", "touch", "rm", "rmdir", "echo", "printf",
    "true", "false", "pwd", "ls", "cat", "test", "[", "stat", "wc", "head",
    "tail", "grep", "diff", "file", "du", "df", "realpath", "readlink",
    "basename", "dirname", "sleep", "date", "sha256sum", "md5sum",
})
_LOCAL_WORK_RELIEF_COMMANDS: FrozenSet[str] = frozenset({"mv", "cp", "tar", "rm", "rmdir", "git"})


def chain_withdraws_local_work_relief(components: List[str]) -> List[str]:
    """Return the mv/cp/tar/rm stages whose path-judged write another stage of the line could redirect."""
    import os
    bases = []
    for component in components:
        tokens = tokenize_command(component)
        bases.append(os.path.basename(tokens[0]) if tokens else "")
    return [
        components[index] for index, base in enumerate(bases)
        if base in _LOCAL_WORK_RELIEF_COMMANDS
        and any(other not in _PLANTS_NO_LINK for i, other in enumerate(bases) if i != index)
    ]


_LITERAL_CD_AND_RE = re.compile(r"\s*cd\s+([^\s$`*?\[\]{}()<>|;&'\"\\]+)\s*&&")
_SHELL_CWD_WORD_RE = re.compile(r"(?<![\w./-])(?:cd|pushd|popd)(?![\w./-])")


def local_work_cwd_known(command: str) -> bool:
    """True when every cwd change on the line is a leading, success-gated ``cd <absolute or ~/ path> &&``; CDPATH can redirect a relative one."""
    remaining = command
    while True:
        match = _LITERAL_CD_AND_RE.match(remaining)
        target = match.group(1) if match else ""
        if not target.startswith("/") and target != "~" and not target.startswith("~/"):
            break
        remaining = remaining[match.end():]
    return _SHELL_CWD_WORD_RE.search(remaining) is None


def local_work_callers(
    command: str, components: List[str], cwd: "Optional[str]", agent_id: str,
) -> "List[Optional[LocalWorkCaller]]":
    """Return each component's local-work caller: None when another stage could redirect its write, cwd None when unproven."""
    withdrawn = set(chain_withdraws_local_work_relief(components))
    known = local_work_cwd_known(command)
    callers: "List[Optional[LocalWorkCaller]]" = []
    for component in components:
        callers.append(None if component in withdrawn else LocalWorkCaller(agent_id, cwd if known else None))
        cwd = cwd_after_component(component, cwd)
    return callers


_DURABLE_STORE_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"SQLite format 3\x00", "SQLite database"),
)

_DURABLE_STORE_HEADER_BYTES: int = max(
    len(signature) for signature, _ in _DURABLE_STORE_SIGNATURES
)

# Calibrated against the measured scratch population, an order of magnitude
# above the largest store a command creates incidentally there (a fresh Gaia
# bootstrap, ~917 KB) and two orders below an accumulated corpus (~185 MB).
_DURABLE_STORE_MIN_BYTES: int = 8 * 1024 * 1024

# A tree larger than this is not certified: the scan stops and the exemption is
# declined.  Sized above the whole live scratch root (2741 entries) so ordinary
# cleanup completes, and deliberately counted in entries rather than elapsed
# time -- a wall-clock budget would let the same command classify differently on
# two runs, which a security classifier cannot afford.
_SCRATCH_SCAN_ENTRY_BUDGET: int = 4096


def _durable_store_under_scratch_targets(
    targets: "Tuple[str, ...]",
) -> "str | None":
    """Return why a scratch rm must keep T3, or None if nothing valuable is reached.

    Walks each target (files directly, directories by descent) and reports the
    first object whose own first bytes carry a durable-store signature while
    being at least _DURABLE_STORE_MIN_BYTES.  Files below that size are never
    opened, which is what keeps the walk cheap on the hot path.

    Fail-closed on uncertainty: an unreadable entry, an undescendable directory,
    or a tree exceeding _SCRATCH_SCAN_ENTRY_BUDGET all return a reason rather
    than silence.  A missing path is an absence, not an uncertainty -- nothing
    can be destroyed there -- so it is skipped.  Symlinks are never followed:
    `rm` removes the link itself, and following one would leave the tree the
    containment check already bounded.

    Args:
        targets: Canonical paths already confirmed confined to scratch.

    Returns:
        A human-readable reason to decline the exemption, or None to allow it.
    """
    import os
    import stat as stat_module

    budget = _SCRATCH_SCAN_ENTRY_BUDGET
    pending = list(targets)
    while pending:
        current = pending.pop()
        budget -= 1
        if budget < 0:
            return (
                f"scratch target tree exceeds {_SCRATCH_SCAN_ENTRY_BUDGET} "
                f"entries; its contents cannot be certified ephemeral"
            )
        try:
            entry_stat = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"{current} cannot be inspected ({exc.strerror})"

        if stat_module.S_ISDIR(entry_stat.st_mode):
            try:
                with os.scandir(current) as scan:
                    pending.extend(child.path for child in scan)
            except OSError as exc:
                return f"{current} cannot be listed ({exc.strerror})"
            continue

        if not stat_module.S_ISREG(entry_stat.st_mode):
            continue
        if entry_stat.st_size < _DURABLE_STORE_MIN_BYTES:
            continue

        try:
            with open(current, "rb") as handle:
                header = handle.read(_DURABLE_STORE_HEADER_BYTES)
        except OSError as exc:
            return f"{current} cannot be read ({exc.strerror})"

        for signature, label in _DURABLE_STORE_SIGNATURES:
            if header.startswith(signature):
                return (
                    f"{current} is a {label} of {entry_stat.st_size} bytes; "
                    f"its value is in its contents, not in its location"
                )

    return None


# ============================================================================
# git worktree -- family classifier and managed-root recycling exception
# ============================================================================
# The worktree verb family was never modelled here, and the resulting economy
# was inverted: `git worktree add` carries no verb in MUTATIVE_VERBS ("add" is
# deliberately absent, see the note in that table) so creating a worktree fell
# through to Step 4 and classified READ_ONLY by elimination, while
# `git worktree remove` was gated only because the generic "remove" token
# happens to sit in MUTATIVE_VERBS.  So the one act that CONTRACTS a cleanup
# obligation was free, and the act that DISCHARGES it cost an approval -- the
# mechanism behind the orphaned worktrees accumulating in the user's repos.
#
# This lane models the family explicitly and inverts that economy:
#   add / move  -> MUTATIVE.  Creating a worktree contracts an obligation to
#                  clean it up later; that is the act worth a signature.
#   remove      -> MUTATIVE, EXCEPT when every target resolves strictly inside
#                  a Gaia-managed worktrees root and no force signal is present
#                  (see _git_worktree_recycles_only_managed_root).
#   anything else (list, prune, lock, unlock, repair, or a subcommand added by
#                  a future git) -> not handled here; the lane returns None and
#                  the pre-existing classification stands untouched.
#
# WHY --force is never exempt, and why that is the whole calibration: what needs
# consent is DESTROYING UNCAPTURED WORK, not deleting a directory whose contents
# are already safe.  `git worktree remove` refuses on its own when the worktree
# has uncommitted changes; `--force` is precisely the override of that refusal.
# Keeping the force form at T3 means the exempted path can never destroy work
# git itself would have protected -- git's own check does the load-bearing work
# and the tier gate sits exactly on top of the bypass.
#
# This is a NEW predicate over the worktree verb family, deliberately NOT a
# reuse of _rm_targets_only_scratch: that one is specific to the `rm` verb and
# to the scratch root, parses `rm`'s own flag grammar, and permits the root
# itself as a target.  Sharing it would couple two unrelated exemptions and let
# a change to either silently widen the other.

_WORKTREE_GLOB_CHARS: FrozenSet[str] = frozenset("*?[]{}")

# Worktree subcommands anchored MUTATIVE by this lane.  "remove" is listed
# because it must be gated by an explicit model rather than by the incidental
# match on the generic "remove" verb; its exemption is applied separately.
GIT_WORKTREE_MUTATIVE_SUBCOMMANDS: FrozenSet[str] = frozenset({
    "add",
    "move",
    "remove",
})


def _gaia_worktrees_root(real_target: str) -> "str | None":
    """Return the Gaia-managed worktrees root strictly containing *real_target*, or None.

    The roots are those gaia.worktree creates under: the workspace root of the
    repository the target belongs to, and the legacy central root.  Any
    failure to resolve them returns None, which keeps the command at T3.
    """
    try:
        from gaia.worktree import managed_root_containing
        root = managed_root_containing(real_target)
    except Exception:
        return None
    return None if root is None else str(root)


def _git_worktree_positionals(tokens: tuple) -> List[str]:
    """Return the positional tokens of a git command, original case preserved.

    Mirrors the flag grammar analyze_command applies -- a single-letter short
    flag appearing before the first positional consumes the next token as its
    value, so the path in ``git -C /repo worktree remove wt`` is absorbed and
    never mistaken for a subcommand or a removal target.  The grammar is ASKED
    FOR rather than restated: ``absorbs_next_token`` knows that git's ``-p``
    and ``-P`` take nothing, so ``git -p worktree remove wt`` keeps ``worktree``
    at the head here exactly as it does in ``non_flag_tokens``.  A second copy
    of the rule would drift from the first and put the two views into
    disagreement about where the subcommand starts.

    semantics.non_flag_tokens cannot be used here: it lowercases every token,
    which corrupts any path with an uppercase component on a case-sensitive
    filesystem and would make the containment check resolve the wrong
    directory.
    """
    positionals: List[str] = []
    skip_next = False
    seen_positional = False
    args = list(tokens[1:])
    base_cmd = str(tokens[0]).rsplit("/", 1)[-1].lower() if tokens else ""
    for index, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-") and token != "-":
            if (
                not seen_positional
                and absorbs_next_token(base_cmd, token)
                and index + 1 < len(args)
                and not args[index + 1].startswith("-")
            ):
                skip_next = True
            continue
        positionals.append(token)
        seen_positional = True
    return positionals


def _git_worktree_has_force_signal(tokens: tuple) -> bool:
    """Return True when a git worktree command carries a force override.

    Matches the long form ``--force`` and any single-dash short cluster
    containing ``f`` (``-f``, ``-ff``), which is how `git worktree remove`
    spells the override of its own uncommitted-changes refusal.  A value token
    absorbed after ``-C``/``-c`` is a bare path and never dash-prefixed, so it
    cannot trip this check.
    """
    for token in tokens[1:]:
        lowered = token.lower()
        if lowered == "--force":
            return True
        if lowered.startswith("--"):
            continue
        if lowered.startswith("-") and len(lowered) > 1:
            body = lowered[1:]
            if body.isalpha() and "f" in body:
                return True
    return False


def _git_worktree_recycles_only_managed_root(tokens: tuple) -> bool:
    """Return True only if a `git worktree remove` recycles inside Gaia's managed roots.

    STRICT and fail-closed.  Returns True only when ALL of the following hold:
      (a) no force signal is present -- see _git_worktree_has_force_signal;
      (b) at least one removal target is present after ``worktree remove``;
      (c) every target is ABSOLUTE after expanduser.  A relative target is
          refused because ``git -C <repo>`` resolves it against the repo, not
          against the hook's cwd, so its real destination is not knowable here;
      (d) no target carries a glob metacharacter or a ``..`` component;
      (e) every target, after os.path.realpath, lives strictly under a
          managed root -- see _gaia_worktrees_root.

    realpath (not normpath) is deliberate: a path fabricated out of symlinks or
    parent-traversal segments is resolved to the directory it actually names
    before containment is tested, so satisfying the text of the prefix is not
    enough to satisfy the check.  Containment is strict -- the root itself is
    not a worktree and does not qualify.

    Any ambiguity returns False, keeping the command at T3.
    """
    if _git_worktree_has_force_signal(tokens):
        return False

    positionals = _git_worktree_positionals(tokens)
    # positionals[0] == "worktree", positionals[1] == "remove"; targets follow.
    targets = positionals[2:]
    if not targets:
        return False

    return all(_is_inside_managed_worktrees_root(target) for target in targets)


def _is_inside_managed_worktrees_root(target: str) -> bool:
    """True when *target* is absolute, glob- and ``..``-free, and resolves inside a managed root."""
    import os
    if any(char in _WORKTREE_GLOB_CHARS for char in target) or ".." in target:
        return False
    expanded = os.path.expanduser(target)
    if not os.path.isabs(expanded):
        return False
    return _gaia_worktrees_root(os.path.realpath(expanded)) is not None


# `git push <remote> <branch>` and `git -C <worktree> merge --ff-only <rev>`
# are freed only in these exact shapes; any other flag, global option or
# operand count leaves the command to the verb scan, where both stay T3.
_GIT_PUSH_RELIEF_FLAGS: FrozenSet[str] = frozenset({"-u", "--set-upstream"})
_GIT_DEFAULT_BRANCHES: FrozenSet[str] = frozenset({"main", "master"})
_GIT_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GIT_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _git_subcommand_split(tokens: tuple) -> "Optional[Tuple[Optional[str], str, List[str]]]":
    """Split ``git [-C <path>] <subcommand> <rest>`` into ``(path, subcommand, rest)``.

    None for any global option other than one ``-C <path>``: the others
    (``-c``, ``--git-dir``, ``--work-tree``) can redirect what the command acts on.
    """
    args = list(tokens[1:])
    path: Optional[str] = None
    if len(args) >= 2 and args[0] == "-C":
        path, args = args[1], args[2:]
    if not args or args[0].startswith("-"):
        return None
    return path, args[0], args[1:]


def _git_repo_dir(path: Optional[str], cwd: Optional[str]) -> Optional[str]:
    """The repository a command acts on, or None when only the hook process's own cwd could name it."""
    import os
    expanded = os.path.expanduser(path) if path else None
    if expanded and os.path.isabs(expanded):
        return expanded
    if cwd:
        return os.path.join(cwd, expanded) if expanded else cwd
    return None


def _git_run(repo: str, *args: str) -> Optional[Tuple[int, str]]:
    """``(exit code, stdout)`` of a read-only ``git -C <repo> <args>``, or None when git cannot run."""
    import subprocess
    try:
        completed = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode, completed.stdout.strip()


def _git_read(repo: str, *args: str) -> Optional[str]:
    """Stdout of a read-only ``git -C <repo> <args>``, or None when it fails."""
    ran = _git_run(repo, *args)
    return ran[1] if ran is not None and ran[0] == 0 else None


def _git_config(repo: str, key: str) -> Tuple[str, Optional[str]]:
    """``("set", value)``, ``("unset", None)`` or ``("error", None)`` for one ``git config --get``.

    Exit 1 is git's documented "key not set"; every other failure is an error.
    """
    ran = _git_run(repo, "config", "--get", key)
    if ran is None or ran[0] not in (0, 1):
        return "error", None
    return ("set", ran[1]) if ran[0] == 0 else ("unset", None)


def _push_config_keeps_branch(repo: str, remote: str, branch: str, default: FrozenSet[str]) -> bool:
    """True when the repository's configuration pushes *branch* to itself, onto no default branch.

    ``remote.<remote>.push`` and ``remote.<remote>.mirror`` replace the
    refspec git pushes; under ``push.default`` upstream or tracking the branch
    goes to its configured upstream, which must then be a non-default branch.
    Any config read that fails keeps the push gated.
    """
    for key in (f"remote.{remote}.push", f"remote.{remote}.mirror"):
        state, value = _git_config(repo, key)
        if state == "error" or (state == "set" and value != "false"):
            return False
    state, mode = _git_config(repo, "push.default")
    if state == "error":
        return False
    if state == "unset" or mode not in ("upstream", "tracking"):
        return True
    state, merge = _git_config(repo, f"branch.{branch}.merge")
    if state != "set" or not merge or not merge.startswith("refs/heads/"):
        return False
    return merge[len("refs/heads/"):].lower() not in default


def _remote_head_branch(repo: str, remote: str) -> Optional[str]:
    """The branch ``refs/remotes/<remote>/HEAD`` points at, or None when unresolved."""
    prefix = f"refs/remotes/{remote}/"
    ref = _git_read(repo, "symbolic-ref", "--quiet", f"{prefix}HEAD")
    if not ref or not ref.startswith(prefix):
        return None
    return ref[len(prefix):]


def _is_local_branch_only(repo: str, name: str) -> bool:
    """True when *name* is a local branch and no tag shares it, so git pushes that branch."""
    return (
        _git_read(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{name}") is not None
        and _git_read(repo, "show-ref", "--verify", "--quiet", f"refs/tags/{name}") is None
    )


def _git_push_targets_feature_branch(path: Optional[str], rest: List[str], cwd: Optional[str]) -> bool:
    """True for ``push [-u|--set-upstream] <remote> <branch>`` onto a branch that is not the default.

    The default is main, master and whatever the remote's HEAD names; an
    unresolved HEAD leaves only main and master. A ``+`` refspec, a ``src:dst``
    whose sides name different branches, a remote that is not configured,
    ``HEAD``, any ref outside ``refs/heads/`` and a name that is not a local
    branch, or is also a tag, never qualify: git would push something else.
    """
    flags = [token for token in rest if token.startswith("-")]
    operands = [token for token in rest if not token.startswith("-")]
    if any(flag not in _GIT_PUSH_RELIEF_FLAGS for flag in flags) or len(operands) != 2:
        return False
    remote, branch = operands
    source, colon, destination = branch.partition(":")
    if colon:
        if destination not in (source, f"refs/heads/{source}"):
            return False
        branch = source
    if not _GIT_REMOTE_NAME_RE.match(remote) or not _GIT_BRANCH_NAME_RE.match(branch):
        return False
    if branch.startswith("refs/"):
        if not branch.startswith("refs/heads/"):
            return False
        branch = branch[len("refs/heads/"):]
    if ".." in branch or branch.endswith(("/", ".lock")) or branch.lower() == "head":
        return False
    repo = _git_repo_dir(path, cwd)
    if repo is None or remote not in _git_remote_names(repo):
        return False
    default = set(_GIT_DEFAULT_BRANCHES)
    remote_head = _remote_head_branch(repo, remote)
    if remote_head:
        default.add(remote_head.lower())
    return (
        branch.lower() not in default
        and _is_local_branch_only(repo, branch)
        and _push_config_keeps_branch(repo, remote, branch, frozenset(default))
    )


def _git_relief_under_prefix(
    readings: "Tuple[str, ...]", cwd: Optional[str],
) -> Optional["MutativeResult"]:
    """T3 for a push or ff-only merge the relief would free, run behind an assignment or wrapper.

    ``GIT_DIR=``, ``GIT_CONFIG_GLOBAL=``, ``HOME=`` or ``sudo -u`` change which
    repository, configuration or user git acts with, none of which the relief
    read, so the freed shape is only trusted when nothing precedes ``git``.
    """
    for reading in readings:
        semantics = analyze_command(reading)
        if semantics.base_cmd != "git":
            continue
        relief = _check_git_publish_relief(semantics, tuple(semantics.tokens), "git", cwd)
        if relief is not None:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=relief.verb,
                cli_family="git",
                confidence="high",
                reason=(
                    f"'git {relief.verb}' behind an environment assignment or wrapper "
                    f"may act on another repository, configuration or user"
                ),
            )
    return None


def _check_git_publish_relief(
    semantics: CommandSemantics, tokens: tuple, family: str, cwd: Optional[str],
) -> Optional["MutativeResult"]:
    """Free a feature-branch push and a fast-forward merge in a managed worktree.

    Returns None for every other git command, which keeps its verdict.
    """
    split = _git_subcommand_split(tokens)
    if split is None:
        return None
    path, subcommand, rest = split
    if subcommand == "push" and _git_push_targets_feature_branch(path, rest, cwd):
        reason = "'git push' of a named non-default branch, without force, delete or refspec"
    elif (
        subcommand == "merge"
        and path is not None
        and len(rest) == 2
        and rest[0] == "--ff-only"
        and not rest[1].startswith("-")
        and _is_inside_managed_worktrees_root(path)
    ):
        reason = "'git merge --ff-only' inside a Gaia-managed worktree"
    else:
        return None
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_UNKNOWN,
        verb=subcommand,
        cli_family=family,
        confidence="high",
        reason=reason,
    )


_GIT_SYNC_OPERAND_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/@+-]*$")
_GIT_SYNC_SHELL_CHARS_RE = re.compile(r"[;&|<>`$()\\\n'\"*?\[\]{}~#!]")


def _git_current_branch(repo: str) -> Optional[str]:
    """The checked-out branch, or None on a detached HEAD or when git cannot say."""
    return _git_read(repo, "symbolic-ref", "--quiet", "--short", "HEAD") or None


def _git_remote_names(repo: str) -> List[str]:
    return (_git_read(repo, "remote") or "").split()


def _git_merge_is_own_fast_forward(repo: str, rest: List[str]) -> bool:
    """True for ``merge --ff-only <remote>/<branch>`` where <branch> is the checked-out one."""
    if len(rest) != 2 or rest[0] != "--ff-only" or not _GIT_SYNC_OPERAND_RE.match(rest[1]):
        return False
    current = _git_current_branch(repo)
    if current is None:
        return False
    return any(rest[1] == f"{remote}/{current}" for remote in _git_remote_names(repo))


def _git_local_sync(
    command: str, cwd: Optional[str], in_prefix: bool,
) -> Optional["MutativeResult"]:
    """Free ``git merge --ff-only <remote>/<branch>`` when <branch> is the checked-out one; None otherwise."""
    if in_prefix or _GIT_SYNC_SHELL_CHARS_RE.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "git":
        return None
    split = _git_subcommand_split(tuple(tokens))
    if split is None:
        return None
    path, subcommand, rest = split
    repo = _git_repo_dir(path, cwd)
    if subcommand != "merge" or repo is None or not _git_merge_is_own_fast_forward(repo, rest):
        return None
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_UNKNOWN,
        verb=subcommand,
        cli_family="git",
        confidence="high",
        reason="'git merge --ff-only' of the current branch to its own remote ref",
    )


_REPO_OPTIONS: FrozenSet[str] = frozenset({"-R", "--repo"})


def _repo_option_values(args: List[str]) -> List[str]:
    values: List[str] = []
    for index, arg in enumerate(args):
        flag, equals, value = arg.partition("=")
        if arg in _REPO_OPTIONS and index + 1 < len(args):
            values.append(args[index + 1])
        elif equals and flag in _REPO_OPTIONS:
            values.append(value)
        elif arg.startswith("-R") and len(arg) > 2:
            values.append(arg[2:])
    return values


def _repo_segments(location: str) -> List[str]:
    bare = re.sub(r"^[a-z][a-z0-9+.-]*://", "", location.strip().lower())
    bare = re.sub(r"^[^/@]+@", "", bare).replace(":", "/")
    bare = bare.rstrip("/").removesuffix(".git")
    return [segment for segment in bare.split("/") if segment]


def _change_request_elsewhere(command: str, cwd: Optional[str]) -> "Optional[MutativeResult]":
    """Sign a change request whose ``-R/--repo`` is not provably this checkout's origin; reads git, so uncached."""
    for reading in _peel_wrapper_readings(command)[0]:
        try:
            tokens = shlex.split(reading)
        except ValueError:
            continue
        base = tokens[0].rsplit("/", 1)[-1] if tokens else ""
        if base not in _FORGE_CLIS and not _is_unmodelled_cli(base):
            continue
        args = tokens[1:]
        directory = args[1] if args[:1] == ["-C"] and len(args) > 1 else None
        path = [a for a in args if not a.startswith("-")]
        targets = _repo_option_values(args)
        if not targets or not any(
            change_request_action(path[i:]) in _CHANGE_REQUEST_OWN_WORK for i in range(len(path))
        ):
            continue
        repo = _git_repo_dir(directory, cwd)
        origin = _repo_segments(_git_read(repo, "remote", "get-url", "origin") or "") if repo else []
        for target in targets:
            wanted = _repo_segments(target)
            if not wanted or origin[-len(wanted):] != wanted:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb="post",
                    cli_family="forge",
                    confidence="high",
                    reason=f"the change request targets {target}, which is not this checkout's origin",
                    rule="sign.speaks",
                )
    return None


def _check_git_worktree_unlock(
    targets: "Tuple[str, ...]", family: str,
) -> "Optional[MutativeResult]":
    """Gate unlocking a worktree Gaia retains; stand aside for any other worktree.

    Gaia locks each worktree it creates so retention can tell owned work from
    abandoned work; the unlock is what exposes it to `git worktree prune` and
    the reclaimer. A relative target resolves against `git -C`'s repository,
    not the hook's cwd, so it cannot be ruled out and is gated.
    """
    import os

    for target in targets:
        expanded = os.path.expanduser(target)
        if os.path.isabs(expanded) and _gaia_worktrees_root(os.path.realpath(expanded)) is None:
            continue
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="worktree unlock",
            cli_family=family,
            confidence="high",
            reason=(
                "git worktree unlock releases the retention lock Gaia holds on "
                "a worktree it manages, leaving uncaptured work open to prune"
            ),
        )
    return None


def _check_git_worktree(
    semantics: CommandSemantics,
    tokens: tuple,
    family: str,
) -> "Optional[MutativeResult]":
    """Classify the `git worktree` verb family, or return None to stand aside.

    Returns None for any worktree subcommand this lane does not model, leaving
    the pre-existing classification of that subcommand exactly as it was.
    """
    non_flag = semantics.non_flag_tokens
    if len(non_flag) < 2 or non_flag[0] != "worktree":
        return None

    subcommand = non_flag[1]
    if subcommand == "unlock":
        return _check_git_worktree_unlock(non_flag[2:], family)
    if subcommand not in GIT_WORKTREE_MUTATIVE_SUBCOMMANDS:
        return None

    if subcommand == "remove" and _git_worktree_recycles_only_managed_root(tokens):
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb="worktree remove",
            cli_family=family,
            confidence="high",
            reason=(
                "git worktree remove recycling only inside Gaia's managed "
                "worktrees roots (<workspace>/.project-worktrees or the legacy "
                "~/.gaia/worktrees); every target resolves strictly under one "
                "via realpath and no "
                "force flag overrides git's own uncommitted-changes refusal"
            ),
        )

    if subcommand == "add":
        reason = (
            "git worktree add creates a worktree, contracting an obligation to "
            "clean it up later"
        )
    else:
        reason = f"State-mutating command path 'git worktree {subcommand}'"

    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb=f"worktree {subcommand}",
        dangerous_flags=_scan_dangerous_flags(list(tokens), "git"),
        cli_family=family,
        confidence="high",
        reason=reason,
    )


# ============================================================================
# git config read-vs-write discriminator
# ============================================================================
# `git config` writes the identity every later commit is signed with and the
# URLs every later fetch and push resolve through, yet it reached T0 -- and NOT
# because `config` sits in READ_ONLY_VERBS, which is what covers the sibling
# `gcloud config set`. Withdrawing that noun leaves `git config user.email x`
# at T0 all the same: it carries no verb at all, so it arrives at "safe by
# elimination". Two forms with the same effect, held open by two different
# mechanisms.
#
# It also cannot be an anchor in COMMAND_PATH_MUTATIVE_UPGRADES. That seam
# matches a subcommand path plus the PRESENCE of a flag, while git's write form
# is defined by the ABSENCE of a read flag together with a key AND a value --
# `git config user.email` reads, `git config user.email x` writes, and both
# spell the same path. `git tag` had the identical shape and took the identical
# answer: a discriminator, invoked from the git family guard.
#
# This lane only ever ADDS a verdict. Every read form -- and any form it does
# not recognize -- returns None and keeps the classification it already had, so
# a mistake here cannot start charging for a read.

# Editing, adding, replacing, or removing a value or a whole section. Each of
# these writes on its own, with no positional value needed.
GIT_CONFIG_WRITE_FLAGS: FrozenSet[str] = frozenset({
    "--add",
    "--replace-all",
    "--unset",
    "--unset-all",
    "--remove-section",
    "--rename-section",
    "--edit", "-e",
})

# Query modes. Their positionals are names and patterns to look up, never a
# value to store, so arity says nothing about them and they are answered first.
GIT_CONFIG_READ_FLAGS: FrozenSet[str] = frozenset({
    "--get",
    "--get-all",
    "--get-regexp",
    "--get-urlmatch",
    "--get-color",
    "--get-colorbool",
    "--list", "-l",
})


def _check_git_config(
    semantics: CommandSemantics,
    tokens: tuple,
    family: str,
) -> "Optional[MutativeResult]":
    """Classify a `git config` WRITE, or return None to stand aside.

    Stands aside for every read form and for anything unrecognized, so the
    read side of this command keeps whatever verdict it already had.
    """
    non_flag = semantics.non_flag_tokens
    if not non_flag or non_flag[0] != "config":
        return None

    flags = frozenset(semantics.flag_tokens)
    if flags & GIT_CONFIG_READ_FLAGS:
        return None

    write_flags = flags & GIT_CONFIG_WRITE_FLAGS
    if write_flags:
        reason = (
            f"git config writes configuration with "
            f"{', '.join(sorted(write_flags))}"
        )
    elif len(non_flag) >= 3:
        # A name alone is a query; a name followed by a value is an assignment.
        reason = (
            f"git config assigns '{non_flag[1]}', writing configuration that "
            f"every later git command reads"
        )
    else:
        return None

    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="config",
        dangerous_flags=_scan_dangerous_flags(list(tokens), "git"),
        cli_family=family,
        confidence="high",
        reason=reason,
    )


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
    "claude",    # Claude Code CLI: `claude plugin uninstall --help` only prints usage
})

# These CLIs parse help as a global, non-executing request even when the
# subcommand path is deeper than the generic two-token boundary below. Keep
# this list deliberately small: it exists for command trees such as
# ``gcloud identity groups create --help`` where counting the group nouns as
# positional mutation arguments produced a false T3.
HELP_ANY_POSITION_BASE_CMDS: FrozenSet[str] = frozenset({
    "gcloud",
    "terraform",
    # argparse prints the deepest subparser's help and exits while parsing,
    # before any handler runs, however many positionals precede the flag.
    "gaia",
})

# State-changing command paths whose action verb is intentionally absent from
# the global verb taxonomy. ``add``/``link``/``unlink`` cannot safely be
# global mutative verbs (for example, ``git add`` is local bookkeeping), and
# Terraform's ``taint`` vocabulary is product-specific. Anchor these paths to
# their CLI family so remote mutations cannot fall through as T0 while
# unrelated uses of the same words remain unaffected.
CLI_MUTATIVE_PATH_PREFIXES: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "terraform": (
        ("taint",),
        ("untaint",),
        ("import",),
        ("force-unlock",),
        ("state", "rm"),
        ("state", "mv"),
        ("state", "push"),
        ("state", "replace-provider"),
    ),
    "gcloud": (
        ("billing", "projects", "link"),
        ("billing", "projects", "unlink"),
        ("identity", "groups", "memberships", "add"),
        ("identity", "groups", "memberships", "remove"),
        ("identity", "groups", "memberships", "modify-membership-roles"),
    ),
}

# Shell redirect tokens (e.g., "2>&1", ">out", "<in") that shlex produces
# as non-flag tokens but carry no CLI semantic value. Used by the --help
# exemption to count only real positional args.
import re as _re_help
_SHELL_REDIRECT_RE = _re_help.compile(r"^(\d*[<>]&?\d*|[<>]{1,2}.*)$")


def _program_asked_for_help(command: str, base_cmd: str) -> bool:
    """True when the program's own argv carries a help flag that is not an option's value.

    Read from shell_parser.program_argv, so a comment, a later command, a
    redirect target or anything after `--` never counts; a help flag right
    after a short option that takes a value (`-l --help`) is that option's
    value, not a request.
    """
    from ..tools.shell_parser import program_argv

    argv = program_argv(command)[1:]
    if "--" in argv:
        argv = argv[:argv.index("--")]
    return any(
        word in HELP_FLAGS and not (index and absorbs_next_token(base_cmd, argv[index - 1]))
        for index, word in enumerate(argv)
    )


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
    "--push": "ALWAYS",
    "--auto-approve": "ALWAYS",
    "-auto-approve": "ALWAYS",
    "--admin": "ALWAYS",
    "-f": "CONTEXT",
    "-r": "CONTEXT",
    "-R": "CONTEXT",
    "-D": "CONTEXT",
    "-M": "CONTEXT",
    "--recursive": "CONTEXT",
    "--delete": "CONTEXT",
    "--hard": "CONTEXT",
    # "-rf"/"-fr" are deliberately NOT listed here as exact-match ALWAYS
    # entries.  An exact-match ALWAYS entry escalates the literal token on
    # EVERY cli, command-agnostic -- which is exactly the failure this pair
    # produced: pytest's own "-rf" report flag (show extra summary for
    # Failed tests) is spelled identically to `rm -rf` and paid the same T3
    # toll on every command carrying it, regardless of cli. The packed-bundle
    # scan in ``_scan_dangerous_flags`` below already matches "-rf"/"-fr" (and
    # every other packed r+f bundle, "-rfi"/"-rfv"/...) through
    # ``_is_posix_short_flag_cluster``, scoped to cli membership in BOTH
    # R_FLAG_MEANS_RECURSIVE_DELETE and F_FLAG_MEANS_FORCE -- an entry here
    # would short-circuit that scoping and reintroduce the same bug for these
    # two exact spellings alone.
}


# ============================================================================
# ALWAYS-flag exemptions (anchored) — the family-scoped narrowing of an
# ALWAYS entry in DANGEROUS_FLAGS.
# ============================================================================
# An ALWAYS flag escalates a read-only verb regardless of CLI (see
# ``_scan_always_dangerous_flags``): that is the right default for a flag that
# destroys real state on every CLI it appears on (``--force``, ``--cascade``,
# ``--grace-period=0``, ``--now``). ``--prune`` is not that kind of flag — its
# effect depends entirely on WHAT is being pruned. ``git fetch --prune``
# deletes only LOCAL remote-tracking refs whose branch no longer exists on the
# remote: bookkeeping that mirrors reality the user has already lost, not a
# destruction of anything they own. The same literal flag on a cluster or
# infrastructure CLI reaches live state instead (measured: ``kubectl apply
# --prune`` deletes resources not present in the applied set; a bare
# read-only verb carrying ``--prune`` on ``terraform``/``terragrunt`` stays
# gated by the unmodified ALWAYS default). Making the flag universally safe
# would free the one case that only cleans up local bookkeeping and the ones
# that destroy live state in the same stroke — the exact overcorrection this
# table exists to prevent.
#
# This table narrows an ALWAYS flag's reach to an exact (CLI, path) anchor,
# reusing the SAME ``MutativeAnchor`` shape and ``_validated_anchor_table``
# guard as ``COMMAND_PATH_MUTATIVE_UPGRADES`` — the family-scoped-by-flag
# capability that table already expresses (built for the M2 IAM/run/init
# anchors), applied here in the opposite direction: subtracting one flag from
# the escalation set for one exact path, rather than adding a verdict. An
# anchor here can only ever narrow an ALWAYS flag's reach; it never touches a
# verb already decided MUTATIVE by an earlier step (that verdict is returned
# long before this table is consulted), and it never exempts any OTHER
# ALWAYS flag on the same command — ``git fetch --prune --force`` still
# escalates on ``--force``, because the exemption is keyed to the exact flag
# token, not to the command as a whole.
COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS: Dict[str, Tuple[MutativeAnchor, ...]] = _validated_anchor_table({
    "git": (
        # Scoped to `fetch` alone, not to `git` as a whole: `git push --prune`
        # (deletes REMOTE branches) stays gated -- unaffected by this table
        # anyway, since `push` is already a MUTATIVE_VERBS verb decided before
        # Step 5 ever runs -- and `git gc --prune=<date>` carries a different
        # token entirely (`--prune=<date>` is not the exact string `--prune`),
        # so it was never matched by the ALWAYS scan in the first place.
        MutativeAnchor(path=("fetch",), flags=frozenset({"--prune"})),
    ),
    # `--force` only makes gcloud fetch a credential it already stores again.
    # Activation and `--update-adc` stay gated by the `auth login` anchors in
    # COMMAND_PATH_MUTATIVE_UPGRADES, which are decided before this table.
    "gcloud": (
        MutativeAnchor(path=("auth", "login"), flags=frozenset({"--force"})),
    ),
    # `gaia login start --force` hands `--force` to the `--no-activate` login it runs.
    "gaia": (
        MutativeAnchor(path=("login", "start"), flags=frozenset({"--force"})),
    ),
})


def _always_flag_exemptions(base_cmd: str, semantics: "CommandSemantics") -> FrozenSet[str]:
    """Return the ALWAYS-dangerous flags exempted for this exact command path.

    Consulted only from the read-only-verb ALWAYS-flag escalation branch of
    Step 4 and from the gaia local-bookkeeping exception, after its
    destructive verbs have returned, so a verb already decided MUTATIVE by an
    earlier step never reaches this table and can never be downgraded by it.
    """
    exempted: set = set()
    for anchor in COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS.get(base_cmd, ()):
        matched = anchor.matched_flags(semantics)
        if matched is not None:
            exempted.update(matched)
    return frozenset(exempted)


# Git-specific flags that promote a normally local-safe subcommand to T3.
# ``git reset`` and ``git reset --soft`` only adjust the index and HEAD
# without touching the working tree, but ``git reset --hard`` discards
# uncommitted changes and is approvable rather than blocked.  Keeping the
# flag set centralized makes the policy auditable and lets test_blocked_*
# pin the contract.
GIT_HARD_RESET_FLAGS: FrozenSet[str] = frozenset({"--hard"})

# CLIs where -f means --force (not --file or --format)
#
# `git` is included so the short-form force flag escalates the same way the
# long-form `--force` (an ALWAYS flag in DANGEROUS_FLAGS) already does. Without
# it, `git mv -f src existing_dst` (force-overwrite of the destination) and
# `git checkout -f` (discard uncommitted changes) silently classified as
# non-mutative because their subcommands live in GIT_LOCAL_SAFE_SUBCOMMANDS and
# `git` was absent here -- the `-f` was never collected by _scan_dangerous_flags.
# This mirrors D_FLAG_MEANS_FORCE_DELETE / M_FLAG_MEANS_FORCE_MOVE, which already
# list `git` for `-D` / `-M`.
F_FLAG_MEANS_FORCE: FrozenSet[str] = frozenset({
    "rm", "cp", "mv", "ln", "docker", "podman",
    "kubectl", "helm", "apt-get", "brew", "git",
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
    "tofu": "iac", "cdk": "iac",
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
# Pipe/Redirect/Chaining Policy Registry (consumed by cloud_pipe_validator)
# ============================================================================
#
# This classifies CLIs on an axis DIFFERENT from MUTATIVE_VERBS/tiers.py.
# Those answer "does the invoked VERB mutate live state" (T0-T3). This
# registry answers "does this CLI expose its OWN native structured-output
# flags (--format, --filter, -o jsonpath, --query, -json) that substitute
# for piping its output to a shell utility". The two axes are independent:
# a read-only `kubectl get pods | grep` is exactly as much a policy
# violation as `kubectl delete ... | grep`, because the harm this registry
# guards against (losing the exit code and the CLI's own structured output)
# does not depend on whether the verb mutates anything. Deriving this
# registry from MUTATIVE_VERBS or SecurityTier would silently exempt every
# read-only invocation of these same CLIs -- exactly the case
# cloud_pipe_validator exists to catch, so that derivation is deliberately
# NOT done here.
#
# PIPE_POLICY_RELEVANT_FAMILIES narrows CLI_FAMILY_LOOKUP to the families
# where the question is even meaningful (k8s/iac/cloud infra CLIs). Every
# name CLI_FAMILY_LOOKUP tags with one of these families MUST appear in
# either NATIVE_OUTPUT_FLAG_CLIS (opted in) or PIPE_POLICY_EXCLUDED_CLIS
# (opted out, with a reason). test_pipe_policy_registry_completeness in
# tests/hooks/modules/security/test_mutative_verbs.py enforces this as a
# COMPLETENESS property, not a set-equality property: an EXCLUDED entry is a
# deliberate, reasoned decision and must never fail the test (stern is
# excluded on purpose -- it is designed to be piped/tailed, so including it
# would be wrong, not merely stricter). The one failure mode the test
# enforces is an UNCLASSIFIED name: present in a relevant CLI_FAMILY_LOOKUP
# family but in neither set, meaning nobody decided. That is how a new CLI
# (tomorrow's `opentofu`/`cdk`) stops being a silent hole: adding it to
# CLI_FAMILY_LOOKUP without also classifying it here fails the test instead
# of quietly falling through.

PIPE_POLICY_RELEVANT_FAMILIES: FrozenSet[str] = frozenset({"k8s", "iac", "cloud"})

# CLIs that expose native output-shaping flags, so piping/redirecting/
# chaining their invocation is a real policy violation (use --format/
# --filter/-o/-json instead). cloud_pipe_validator's per-stage cloud-CLI
# check is built directly from this set.
NATIVE_OUTPUT_FLAG_CLIS: FrozenSet[str] = frozenset({
    "gcloud", "aws", "gsutil", "az", "eksctl",         # cloud
    "kubectl", "helm", "flux",                        # k8s
    "terraform", "terragrunt", "tofu", "cdk", "pulumi", "cdktf",  # iac
})

# CLIs in a PIPE_POLICY_RELEVANT_FAMILIES family that are deliberately NOT
# in NATIVE_OUTPUT_FLAG_CLIS, with the reason written down so the exclusion
# reads as a decision, not an oversight.
PIPE_POLICY_EXCLUDED_CLIS: Dict[str, str] = {
    "stern": (
        "designed to stream/tail logs for piping into grep/awk; there is no "
        "native --format substitute for that use, so including it would "
        "break its entire purpose."
    ),
    "kustomize": (
        "`kustomize build | kubectl apply -f -` is the canonical, documented "
        "usage idiom; kustomize's own output IS meant to be piped."
    ),
    "k9s": "an interactive TUI, never invoked as a one-shot command whose output would be piped.",
    "kubectx": "a context switcher with no structured output to filter.",
    "kubens": "a namespace switcher with no structured output to filter.",
    "gh": "a git-hosting CLI (issues/PRs/releases), outside this policy's cloud/infra-state scope.",
    "glab": "same as gh -- GitLab's git-hosting CLI, outside this policy's scope.",
    "vercel": "a PaaS deploy CLI outside this policy's cloud/infra-state scope.",
    "netlify": "same as vercel.",
    "fly": "same as vercel.",
    "flyctl": "same as vercel (flyctl is fly's CLI binary).",
    "heroku": "same as vercel.",
}


# ============================================================================
# Dangerous Flag Scanning
# ============================================================================

# Longest packed POSIX short-flag bundle the ``-rf`` heuristic will consider.
# Real destructive bundles are short ("-rf"=2, "-rfi"=3, "-rfvd"=4); a token
# longer than this is a long-form word flag ("-NoProfile", "-Recurse"), not a
# bundle of single-character flags.
_MAX_POSIX_SHORT_FLAG_CLUSTER = 4

# A CamelCase boundary (an uppercase letter immediately followed by a lowercase
# one) marks a word, not a flag bundle: "No"/"Pro" in "-NoProfile", "Fo" in
# "-Force".  Genuine packed short-flag bundles are lowercase ("-rf", "-rfi").
_CAMEL_WORD_RE = _re.compile(r"[A-Z][a-z]")


def _is_posix_short_flag_cluster(flag_chars: str) -> bool:
    """True when *flag_chars* looks like a genuine packed POSIX short-flag bundle.

    ``flag_chars`` is the token body with the leading ``-`` already stripped
    ("rf" for "-rf", "NoProfile" for "-NoProfile").  A packed bundle is a run of
    single-character flags -- short, all letters, no CamelCase word boundary.  A
    long single-dash word flag from the .NET/PowerShell/Java family
    ("-NoProfile", "-Force", "-Recurse", "-ExecutionPolicy") is NOT a bundle and
    must not be mined for stray ``r``/``f`` characters.

    Trade-off (documented): an uppercase-led bundle such as ``-Rf`` is treated
    as a word ("Rf" trips the CamelCase gate) and so is NOT matched here.  This
    is deliberate -- excluding the ubiquitous ``-NoProfile`` false positive on
    every PowerShell command is worth far more than catching the rare
    uppercase-packed ``-Rf`` form, and the ``-R``/``-r``/``-f`` single flags are
    still caught by their exact-match handling above.
    """
    if not flag_chars or len(flag_chars) > _MAX_POSIX_SHORT_FLAG_CLUSTER:
        return False
    if not flag_chars.isalpha():
        return False
    if _CAMEL_WORD_RE.search(flag_chars):
        return False
    return True


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
    - A packed compound like "-rf" is dangerous only if cli is in BOTH
      R_FLAG_MEANS_RECURSIVE_DELETE and F_FLAG_MEANS_FORCE (both letters must
      mean something destructive on THIS cli); a cli dangerous on only one
      letter still escalates through that letter's own context rule

    Args:
        tokens: Tokenized command.
        cli: CLI tool name.

    Returns:
        Tuple of dangerous flag strings found.
    """
    found: List[str] = []

    for index, token in enumerate(tokens):
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
                # On git, -M is --find-renames for show/log/diff and a forced
                # rename only after `branch`.
                if cli in M_FLAG_MEANS_FORCE_MOVE and (
                    cli != "git" or "branch" in tokens[:index]
                ):
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
        # e.g., "-rfi" contains both -r and -f.
        #
        # This heuristic MUST fire only on genuine packed POSIX short-flag
        # bundles ("-rf", "-rfi", "-fv"), never on a long single-dash *word*
        # flag from the .NET/PowerShell/Java family ("-NoProfile", "-Force",
        # "-Recurse", "-Xmx").  The old rule tested only ``"r" in chars and "f"
        # in chars`` on any >2-char single-dash token, so "-NoProfile"
        # (P**rof**ile carries both r and f) was mis-read as "-rf" -- turning
        # EVERY PowerShell invocation (Claude Code prepends ``-NoProfile``) into
        # a spurious T3.  ``_is_posix_short_flag_cluster`` gates the branch so a
        # word-flag no longer matches, while real packed bundles still do.
        #
        # The r+f branch used to fire unconditionally once the bundle shape
        # matched, regardless of *cli* -- a bare-string match ("does the
        # token contain both letters") agnostic to what those letters mean on
        # THIS command.  ``pytest``'s own ``-r`` is its report-selection flag
        # (``-rf`` = show extra summary for Failed tests), not "recursive",
        # and pytest carries no "-f" force meaning either, so `pytest -rf`
        # read exactly like `rm -rf` and paid the same T3 toll -- on the one
        # command the project's own testing discipline requires running to
        # verify the classifier itself (see ``security-tiers``: ad-hoc inline
        # probes are explicitly disallowed, so pytest is the ONLY sanctioned
        # way to check a fix here).  Scoped the same way the two single-flag
        # branches below already are: the combo only means "recursive force"
        # when the CLI is independently known to give BOTH letters that
        # meaning (``rm``, ``cp``) -- membership in both context sets, not
        # one.  A CLI known dangerous on only one of the two letters (e.g.
        # ``kubectl``/``docker``/``git`` for ``-f``, ``chmod``/``find`` for
        # ``-r``) is unaffected: it still escalates via the two ``elif``
        # branches below, which each check their own single-letter context
        # set independently of this one.
        elif len(token) > 2 and token[0] == "-" and token[1] != "-":
            flag_chars = token[1:]
            if _is_posix_short_flag_cluster(flag_chars):
                if (
                    "r" in flag_chars
                    and "f" in flag_chars
                    and cli in R_FLAG_MEANS_RECURSIVE_DELETE
                    and cli in F_FLAG_MEANS_FORCE
                ):
                    found.append(token)
                elif "f" in flag_chars and cli in F_FLAG_MEANS_FORCE:
                    found.append(token)
                elif "r" in flag_chars and cli in R_FLAG_MEANS_RECURSIVE_DELETE:
                    found.append(token)

    return tuple(found)


def _scan_always_dangerous_flags(
    tokens: Union[List[str], tuple],
) -> Tuple[str, ...]:
    """Return only the ALWAYS-type dangerous flags present in ``tokens``.

    ALWAYS flags (``--prune``, ``--force``, ``--force-with-lease``,
    ``--cascade``, ``--now``, ``-rf`` ...) are dangerous regardless of the CLI
    family -- that is what distinguishes them from CONTEXT flags (``-f``,
    ``-r``, ``--delete`` ...), which are only dangerous for specific CLIs.

    This exists so the read-only-verb path can escalate on an ALWAYS flag
    WITHOUT pulling in CONTEXT-flag handling (which is family-gated and must
    not fire on a read-only verb just because a benign ``-r``/``-f`` appears).
    A read-only verb carrying an ALWAYS flag -- e.g. ``terraform state list
    --prune``, a form the Step 5 scan would catch on its own -- is a real
    mutation, but the read-only early-return returns before Step 5 is ever
    reached. Escalating here closes that hole. The caller narrows this further
    per (CLI, path) via ``COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS`` for a flag
    whose destructive meaning does not hold on every CLI that carries it --
    see that table for ``git fetch --prune``, which this function still
    reports as ALWAYS-dangerous.
    """
    found: List[str] = []
    for token in tokens:
        if not token.startswith("-"):
            continue
        if DANGEROUS_FLAGS.get(token) == "ALWAYS":
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
        if ch.isspace() or ch in _NON_SUBCOMMAND_CHARS:
            return False
    return True


_SHELL_ARITHMETIC_EXPANSION_RE = _re.compile(r"\$\(\([^()]*\)\)")


def _extract_command_substitutions(text: str) -> "Optional[List[str]]":
    """Extract the top-level ``$(...)`` / backtick bodies from *text*.

    Uses a balanced-paren scanner (the same conventions as
    ``_consume_shell_word``) so a NESTED substitution such as
    ``$(rm -rf $(pwd))`` yields its full OUTER body -- a flat regex stops at
    the first ``)`` and hands the classifier only the innermost (often
    read-only) command, hiding the mutative outer one.  Single-quoted spans
    are skipped because the shell never expands inside them; double quotes do
    not suppress expansion, so their content is scanned normally.

    Returns ``None`` when an opener is present but no balanced body can be
    extracted (unterminated ``$(`` or backtick, or an opener hidden behind an
    unterminated quote) -- the caller must treat that as unparseable and
    classify conservatively.
    """
    bodies: "List[str]" = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                # Unterminated quote: anything after it is ambiguous.
                if "$(" in text[i:] or "`" in text[i + 1:]:
                    return None
                break
            i = j + 1
            continue
        if c == "`":
            j = text.find("`", i + 1)
            if j == -1:
                return None
            bodies.append(text[i + 1:j])
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            depth = 1
            j = i + 2
            while j < n and depth:
                cj = text[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "'":
                    q = text.find("'", j + 1)
                    if q == -1:
                        return None
                    j = q + 1
                    continue
                if cj == '"':
                    j += 1
                    while j < n:
                        if text[j] == "\\":
                            j += 2
                            continue
                        if text[j] == '"':
                            j += 1
                            break
                        j += 1
                    continue
                if cj == "(":
                    depth += 1
                elif cj == ")":
                    depth -= 1
                j += 1
            if depth:
                return None
            bodies.append(text[i + 2:j - 1])
            i = j
            continue
        i += 1
    return bodies


def _classify_leading_shell_assignments(
    command: str, *, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Classify leading ``NAME=value`` words and substitutions they execute.

    A pure assignment is transient shell bookkeeping. A ``$(...)`` or
    backtick inside its value, however, executes a command and must be judged
    by that command's real effect before the env-prefix peeler discards it.
    Wrappers ahead of the assignments (``env``, ``sudo``...) are skipped so
    ``env NAME=$(...) cmd`` is examined exactly like the bare form -- the
    peeler would otherwise discard the substitution unseen.
    Returns ``None`` when a real command follows the assignments so ordinary
    classification can continue after all substitutions have been checked.
    """
    n = len(command)
    saw_assignment = False
    cursor = _skip_command_wrappers(command, 0)
    while cursor < n:
        while cursor < n and command[cursor].isspace():
            cursor += 1
        if _ENV_ASSIGN_PREFIX_RE.match(command, cursor) is None:
            break
        saw_assignment = True
        end = _consume_shell_word(command, cursor)
        assignment = command[cursor:end]
        # ``$((1 + 2))`` is arithmetic expansion, not command execution.
        # Remove simple arithmetic forms before looking for ``$(...)``.
        executable_text = _SHELL_ARITHMETIC_EXPANSION_RE.sub("", assignment)
        substitutions = _extract_command_substitutions(executable_text)
        if substitutions is None:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="shell-substitution-unparseable",
                cli_family="system",
                confidence="low",
                reason=(
                    "Shell assignment contains a command substitution that "
                    "could not be parsed conservatively"
                ),
            )
        for inner_body in substitutions:
            inner = inner_body.strip()
            if not inner:
                continue
            inner_result = detect_mutative_command(
                inner, cwd=cwd, _depth=_depth + 1,
            )
            if inner_result.is_mutative:
                return MutativeResult(
                    is_mutative=True,
                    category=inner_result.category,
                    verb=inner_result.verb,
                    dangerous_flags=inner_result.dangerous_flags,
                    cli_family=inner_result.cli_family,
                    confidence=inner_result.confidence,
                    reason=(
                        f"Shell assignment executes mutative substitution "
                        f"{inner!r}: {inner_result.reason}"
                    ),
                )
        cursor = end

    if not saw_assignment:
        return None
    if command[cursor:].strip():
        return None
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="shell-assignment",
        cli_family="system",
        confidence="high",
        reason="Shell variable assignment with no mutative substitution",
    )


# Shells that take a command as a STRING argument to ``-c``. The payload is a
# program, not an argument: whatever it says, the shell runs.
_STRING_PAYLOAD_SHELLS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "ash", "mksh", "busybox",
})

# Wrappers whose FIRST token is not the shell -- the real shell follows.
_SHELL_MULTICALL_WRAPPERS = frozenset({"busybox"})

# `find` hands each match to a command named on its own command line. The
# tokens between the flag and the terminator ARE a command by definition, which
# is what makes reading them safe here rather than a heuristic.
_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_EXEC_TERMINATORS = frozenset({";", "\\;"})


def _find_exec_payloads(tokens: "List[str]") -> "Tuple[str, ...]":
    """Every argv a find line hands to -exec/-execdir/-ok/-okdir, in order; ``+`` ends one only right after ``{}``, as find reads it."""
    payloads: "List[str]" = []
    index = 0
    while index < len(tokens):
        if tokens[index].lower() not in _FIND_EXEC_FLAGS:
            index += 1
            continue
        words: "List[str]" = []
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if token in _FIND_EXEC_TERMINATORS or (token == "+" and words[-1:] == ["{}"]):
                break
            words.append(token)
            index += 1
        index += 1
        if words:
            payloads.append(shlex.join(words))
    return tuple(payloads)


def _resolve_executor_payload(
    base_cmd: str, semantics: "CommandSemantics",
) -> "Optional[str]":
    """Return the command text *base_cmd* would hand to an executor, or None.

    Shapes that pass a COMMAND as DATA (find's exec actions are
    ``_find_exec_payloads``, since one find can carry several):

        <shell> -c "<command>"      the payload is a program
        eval <words>                the words are re-parsed as a command
        a ``_COMMAND_EXECUTORS`` line  its ``-c`` option or trailing words

    Returns:
        The payload as a command string, or ``None`` when this command is not
        one of these shapes. An empty or flag-only payload also returns
        ``None`` -- there is nothing to classify and inventing a verdict for it
        would gate the shell's own ``--help``.
    """
    tokens = list(semantics.tokens)
    if not tokens:
        return None

    if base_cmd in _SHELL_MULTICALL_WRAPPERS:
        # `busybox sh -c ...` -- the applet name is the real shell.
        tokens = tokens[1:]
        if not tokens:
            return None
        base_cmd = tokens[0].rsplit("/", 1)[-1].lower()

    if base_cmd in _STRING_PAYLOAD_SHELLS:
        for index, token in enumerate(tokens[1:], start=1):
            if token == "-c" and index + 1 < len(tokens):
                return tokens[index + 1].strip() or None
        return None

    if base_cmd == "eval":
        return " ".join(tokens[1:]).strip() or None

    grammar = _COMMAND_EXECUTORS.get(base_cmd)
    if grammar is not None:
        return _executor_grammar_payload(tokens[1:], grammar)

    return None


@dataclass(frozen=True)
class _ExecutorGrammar:
    """Where an executor carries the command it runs.

    ``command_option``: a ``-c``/``--command`` value is the payload.
    ``trailing``: the words after the executor's own flags and ``positionals``
    are the payload -- ``"argv"`` when they are exec'd as an argument vector,
    ``"shell"`` when they are joined with spaces and handed to a shell.
    """

    value_flags: FrozenSet[str] = frozenset()
    positionals: int = 0
    command_option: bool = False
    trailing: "Optional[str]" = None


# Executors that take the command to run as a quoted string or trailing words.
# `ssh` runs the payload on another host; the effect is the same mutation.
_COMMAND_EXECUTORS: Dict[str, _ExecutorGrammar] = {
    "xargs": _ExecutorGrammar(
        value_flags=frozenset({
            "-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s",
            "--arg-file", "--delimiter", "--max-lines", "--max-args",
            "--max-procs", "--max-chars", "--process-slot-var",
        }),
        trailing="argv",
    ),
    "watch": _ExecutorGrammar(
        value_flags=frozenset({"-n", "-q", "--interval", "--equexit"}),
        trailing="shell",
    ),
    "ssh": _ExecutorGrammar(
        value_flags=frozenset({
            "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l",
            "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w", "-B",
        }),
        positionals=1,
        trailing="shell",
    ),
    "flock": _ExecutorGrammar(
        value_flags=frozenset({"-w", "-E", "--timeout", "--conflict-exit-code"}),
        positionals=1,
        command_option=True,
        trailing="argv",
    ),
    "sg": _ExecutorGrammar(positionals=1, command_option=True, trailing="shell"),
    "runuser": _ExecutorGrammar(
        value_flags=frozenset({
            "-u", "-g", "-G", "-s", "-w", "--user", "--group", "--supp-group",
            "--shell", "--whitelist-environment",
        }),
        command_option=True,
    ),
    "su": _ExecutorGrammar(
        value_flags=frozenset({
            "-s", "-g", "-G", "-w", "--shell", "--group", "--supp-group",
            "--whitelist-environment",
        }),
        command_option=True,
    ),
    "script": _ExecutorGrammar(
        value_flags=frozenset({
            "-E", "-I", "-O", "-B", "-T", "--echo", "--log-in", "--log-out",
            "--log-io", "--log-timing", "--logging-format",
        }),
        command_option=True,
    ),
}


def _executor_grammar_payload(
    args: "List[str]", grammar: _ExecutorGrammar,
) -> "Optional[str]":
    """Return the command an executor described by *grammar* runs, or None."""
    if grammar.command_option:
        payload = _command_option_payload(args)
        if payload is not None:
            return payload
    if grammar.trailing is None:
        return None

    index = 0
    positionals = grammar.positionals
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-") and len(token) > 1:
            index += 2 if token in grammar.value_flags else 1
            continue
        if positionals:
            positionals -= 1
            index += 1
            continue
        break
    words = args[index:]
    if not words:
        return None
    return shlex.join(words) if grammar.trailing == "argv" else " ".join(words)


def _command_option_payload(args: "List[str]") -> "Optional[str]":
    """Return the value of a ``-c``/``--command`` option (``-qc CMD`` included), or None."""
    for index, token in enumerate(args):
        if token.startswith("--command="):
            return token.split("=", 1)[1].strip() or None
        is_short_c = (
            token.startswith("-") and not token.startswith("--")
            and token[1:].isalpha() and token.endswith("c")
        )
        if (token == "--command" or is_short_c) and index + 1 < len(args):
            return args[index + 1].strip() or None
    return None


def _executor_floor_payloads(command: str) -> "Tuple[str, ...]":
    """Return every command *command* hands to an executor (``_resolve_executor_payload``, or each find exec action); the floor judges each as written."""
    semantics = analyze_command(command)
    if semantics.base_cmd == "find":
        return _find_exec_payloads(list(semantics.tokens))
    nested = nested_run(semantics.tokens)
    handed = (nested.inner, *nested.alternatives) if nested else ()
    payloads = (_resolve_executor_payload(semantics.base_cmd, semantics), *handed)
    return tuple(payload for payload in payloads if payload)


def flag_floor_block(command: str) -> "Optional[FlagClassifierResult]":
    """The flag classifier's permanent block on *command* as ``bash_validator`` reaches it bare: after a verb that signs."""
    from .flag_classifiers import OUTCOME_BLOCKED, classify_by_flags

    flagged = classify_by_flags(command)
    if flagged is None or flagged.outcome != OUTCOME_BLOCKED:
        return None
    return None if detect_mutative_command(command).is_mutative else flagged


def _check_target_environment(
    command: str, remainder: str, *, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Escalate *remainder* when a variable assigned anywhere down its chain of local executors picks the target or identity of the innermost program as one of its flags would."""
    from ..tools.shell_parser import shell_split

    dropped, expanded, _, _ = _expand_split_strings(command)
    try:
        written, words = shell_split(dropped) + shell_split(expanded), shell_split(remainder)
    except ValueError:
        return None
    return _target_verdict(written[: len(written) - len(words)], words, cwd=cwd, _depth=_depth)


def target_under_line(
    command: str, assigned: "Dict[str, str]", *, cwd: "Optional[str]",
) -> "Optional[MutativeResult]":
    """Escalate *command* when a variable its line may set (*assigned*, name to value) picks its innermost program's target or identity."""
    from ..tools.shell_parser import shell_split

    if not assigned:
        return None
    readings, _ = _peel_wrapper_readings(command)
    dropped, expanded, _, _ = _expand_split_strings(command)
    try:
        written, words = shell_split(dropped) + shell_split(expanded), shell_split(readings[0])
    except ValueError:
        return None
    inherited = [f"{name}={value}" for name, value in assigned.items()]
    return _target_verdict(inherited + written[: len(written) - len(words)], words, cwd=cwd, _depth=0)


def _target_verdict(
    assigned: "List[str]", words: "List[str]", *, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    from ..tools.shell_parser import shell_split

    run = nested_run(words)
    while run is not None and not run.remote and run.unparsed is None and run.inner:
        dropped, expanded, _, _ = _expand_split_strings(run.inner)
        try:
            inner_words = shell_split(dropped) + shell_split(expanded)
            words = shell_split(_peel_wrapper_readings(run.inner)[0][0])
        except ValueError:
            return None
        assigned += inner_words[: len(inner_words) - len(words)]
        run = nested_run(words)
    flagged = with_target_flags(assigned, words)
    if flagged is None:
        return None
    result = detect_mutative_command(shlex.join(flagged), cwd=cwd, _depth=_depth + 1)
    return replace(result, reason=f"run as {shlex.join(flagged)!r}: {result.reason}") if result.is_mutative else None


def _component_verdict(component: str, *, cwd: "Optional[str]", _depth: int) -> MutativeResult:
    """Classify one component with the flag-dependent effects (``sed -i``, ``curl -X POST``) the verb ladder leaves to the validator."""
    from .flag_classifiers import OUTCOME_READ_ONLY, classify_by_flags

    result = detect_mutative_command(component, cwd=cwd, _depth=_depth)
    if result.is_mutative:
        return result
    readings, peeled = _peel_wrapper_readings(component)
    flagged = classify_by_flags(readings[0], wrapped=peeled)
    if flagged is None or flagged.command_family.startswith("git_"):
        return result
    if flagged.outcome == OUTCOME_READ_ONLY:
        return replace(result, category=CATEGORY_READ_ONLY)
    return replace(
        result, is_mutative=True, category=CATEGORY_MUTATIVE, verb=flagged.matched_pattern or result.verb,
        reason=f"Flag classifier: {flagged.reason}",
    )


def _strictest_component(command: str, *, cwd: "Optional[str]", _depth: int) -> MutativeResult:
    """Classify each component of the chain or pipeline *command*; the first mutative one wins."""
    from ..tools.shell_parser import parse_command

    for component in parse_command(command) or [command]:
        result = _component_verdict(component, cwd=cwd, _depth=_depth)
        if result.is_mutative:
            break
    return result


def _unproven_writer_or_sender(command: str, *, cwd: "Optional[str]", _depth: int) -> str:
    """The first component of *command* whose program writes files or sends data and that no lane showed to only read."""
    from ..tools.shell_parser import parse_command
    from .shell_unwrapper import shell_command_string

    for component in parse_command(command) or [command]:
        payload = shell_command_string(component)
        if payload is not None:
            found = _unproven_writer_or_sender(payload, cwd=cwd, _depth=_depth + 1)
        else:
            program = analyze_command(_peel_wrapper_readings(component)[0][0]).base_cmd
            reads = _component_verdict(component, cwd=cwd, _depth=_depth).category in _READING_CATEGORIES
            found = "" if reads or not writes_or_sends(program) else component
        if found:
            return found
    return ""


def _check_nested_executor(
    semantics: "CommandSemantics", *, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Judge an executor that runs a command elsewhere (``nested_executors``) by that command; only a full parse relieves."""
    run = nested_run(semantics.tokens)
    if run is None:
        return None
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result("nested executor")
    if run.unparsed is None and (run.inner is None or opens_session(run.inner)):
        return MutativeResult(
            is_mutative=True, category=CATEGORY_MUTATIVE, verb="exec", cli_family=semantics.base_cmd,
            reason=f"'{run.executor}' opens an interactive session", rule="sign.speaks",
        )
    inner = _strictest_component(run.inner, cwd=cwd, _depth=_depth + 1) if run.inner else None
    if inner is not None and inner.is_mutative:
        return replace(inner, reason=f"'{run.executor}' runs {run.inner!r}: {inner.reason}")
    if run.unparsed is not None:
        if run.generic:
            return None
        undetermined = f"'{run.executor}' word {run.unparsed!r} is outside its grammar"
    elif run.remote and reads_local_state(run.inner):
        undetermined = f"'{run.executor}' runs {run.inner!r} on another host, whose files decide its effect"
    elif _unproven_writer_or_sender(run.inner, cwd=cwd, _depth=_depth + 1):
        undetermined = f"'{run.executor}' runs {run.inner!r}, which may write files or send data"
    else:
        return MutativeResult(
            is_mutative=False, category=CATEGORY_READ_ONLY, verb=inner.verb, cli_family=semantics.base_cmd,
            confidence=inner.confidence, reason=f"'{run.executor}' runs {run.inner!r}: {inner.reason}",
        )
    return MutativeResult(
        is_mutative=True, category=CATEGORY_UNKNOWN, verb="executor-unparsed-flag", cli_family=semantics.base_cmd,
        reason=f"{undetermined}, so the command it runs could not be determined", rule="unknown.dynamic-span",
    )


_VERB_FIRST_FAMILIES: FrozenSet[str] = frozenset({"k8s", "iac", "git", "docker", "cloud", "workspace", "system"})
# CLIs whose command path runs through group words before the verb (gcloud compute instances delete).
_GROUP_PATH_CLIS: FrozenSet[str] = frozenset({"gcloud", "aws", "az", "gsutil", "oci"})
_SHARED_VERBS: FrozenSet[str] = MUTATIVE_VERBS | READ_ONLY_VERBS | SIMULATION_VERBS | {"ls", "ps", "rm", "cp", "mv"}


def _is_shell_expansion(word: str) -> bool:
    return word[:1] in ("$", "`")


def _verb_word_effect(word: str) -> str:
    """Return the strongest category among a verb word's hyphen parts: MUTATIVE, READ_ONLY, or "" when none is a verb."""
    parts = word.lower().split("-")
    if any(part in MUTATIVE_VERBS or part == "rm" for part in parts):
        return CATEGORY_MUTATIVE
    return CATEGORY_READ_ONLY if any(part in _SHARED_VERBS for part in parts) else ""


_EXPRESSION_FIRST_TOOLS: FrozenSet[str] = frozenset({
    "jq", "yq", "sed", "awk", "gawk", "shellcheck", "curl", "wget", "grep", "egrep", "fgrep", "rg",
    "test", "[", "[[", "printf", "cut", "sort", "tr", "xmllint", "dasel",
})


def _literal_words(base_cmd: str, args: "List[str]") -> "List[str]":
    """Return the literal positionals of *args*, with every flag value and shell expansion left out."""
    words, index = [], 0
    while index < len(args):
        token = args[index]
        if token.startswith("-") and len(token) > 1:
            follower = args[index + 1] if index + 1 < len(args) else "-"
            unknown_long = token.startswith("--") and not follower.startswith("-")
            takes_value = "=" not in token and (absorbs_next_token(base_cmd, token) or unknown_long)
            index += 2 if takes_value else 1
            continue
        closer = ")" if token.startswith("$(") else "`" if token.startswith("`") else ""
        if closer and not (token.endswith(closer) and len(token) > 2):
            while index + 1 < len(args) and not args[index].endswith(closer):
                index += 1
        elif not _is_shell_expansion(token):
            words.append(token)
        index += 1
    return words


def _is_known_verb(word: str) -> bool:
    word = word.lower()
    return word in _SHARED_VERBS or word.split("-", 1)[0] in _SHARED_VERBS


def _literal_verb_follows(args: "List[str]") -> bool:
    """Return True when a known verb follows among the literal words, before any other shell expansion."""
    for token in args:
        if _is_shell_expansion(token):
            return False
        if not token.startswith("-") and _is_known_verb(token):
            return True
    return False


def _dynamic_verb_span_word(base_cmd: str, args: "List[str]") -> "Optional[str]":
    """Return a shell-expanded word that may stand on a CLI's command path, or None.

    An unknown long flag followed by an expansion is resolved by the shared verb
    vocabulary: a known verb after it makes the expansion the flag's value.
    """
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            return None
        if token.startswith("-") and len(token) > 1:
            if "=" not in token and absorbs_next_token(base_cmd, token):
                index += 2
                continue
            follower = args[index + 1] if index + 1 < len(args) else "-"
            unknown_long = token.startswith("--") and "=" not in token and not follower.startswith("-")
            if unknown_long and _is_shell_expansion(follower) and not _literal_verb_follows(args[index + 2:]):
                return follower
            index += 2 if unknown_long and not _is_known_verb(follower) else 1
            continue
        if _is_shell_expansion(token):
            return token
        if base_cmd not in _GROUP_PATH_CLIS or _is_known_verb(token):
            return None
        index += 1
    return None


# Executors whose every option is known, so a flag outside both sets leaves the payload's start unknown.
_EXECUTOR_BOOLEAN_FLAGS: Dict[str, FrozenSet[str]] = {
    "ssh": frozenset(f"-{ch}" for ch in "46AaCfGgKkMNnqsTtVvXxYy"),
}


def _executor_unparsed_flag(base_cmd: str, args: "List[str]") -> "Optional[str]":
    """Return the first option before an executor's payload that its grammar does not name, or None."""
    grammar, boolean = _COMMAND_EXECUTORS.get(base_cmd), _EXECUTOR_BOOLEAN_FLAGS.get(base_cmd)
    if grammar is None or boolean is None:
        return None
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "--":
        token = args[index]
        if token in grammar.value_flags or token[:2] in grammar.value_flags:
            index += 2 if token in grammar.value_flags else 1
        elif all(f"-{ch}" in boolean for ch in token[1:]):
            index += 1
        else:
            return token
    return None


def _check_executor_payload(
    base_cmd: str, semantics: "CommandSemantics", *,
    cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Classify the command an executor is handed as a string.

    The validator has always unwrapped a shell payload, but it does so in an
    earlier phase and only on the command AS WRITTEN. Detection itself never
    unwrapped one, so the same payload reached through any re-dispatch -- a
    command substitution body, a line inside a script file, an assignment
    value -- was classified by its carrier and came back free by elimination.
    Measured both ways: the bare form reported ``is_mutative=False`` with an
    empty verb while still being gated by that earlier phase, and a script file
    whose only mutation sat behind ``sh -c`` classified non-mutative outright.

    That measurement is what decided WHERE this belongs. Unwrapping inside the
    substitution lane would have closed only the route that was reported, and
    the script-file reading shows the blindness is the detector's, not that
    lane's. Reusing the validator's own unwrapper was not available: this
    package is imported BY the validator, so the dependency only runs one way,
    and that unwrapper produces a confirm-dialog verdict rather than a
    classification.

    ESCALATE-ONLY, which is what keeps a payload lane from taxing every
    ``sh -c`` in existence: the payload's verdict is adopted only when it comes
    back mutative. ``bash -c "ls"`` returns ``None`` here and is classified
    exactly as it was before.

    Returns:
        A mutative result when the payload is mutative, ``None`` otherwise --
        including for every command that is not one of the executor shapes.
    """
    unparsed = _executor_unparsed_flag(base_cmd, list(semantics.tokens[1:]))
    if unparsed is not None:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_UNKNOWN,
            verb="executor-unparsed-flag",
            cli_family=base_cmd,
            reason=f"'{base_cmd}' option {unparsed!r} is not in its grammar, so the command it runs could not be determined",
            rule="unknown.dynamic-span",
        )
    if base_cmd == "find":
        payloads = _find_exec_payloads(list(semantics.tokens))
    else:
        payload = _resolve_executor_payload(base_cmd, semantics)
        payloads = (payload,) if payload is not None else ()
    if not payloads:
        return None

    # Budget exhausted: stop descending AND retain -- see the constant.
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result("executor payload")

    for payload in payloads:
        inner = _strictest_component(payload, cwd=cwd, _depth=_depth + 1)
        if inner.is_mutative:
            break
    else:
        return None
    return MutativeResult(
        is_mutative=True,
        category=inner.category,
        verb=inner.verb,
        dangerous_flags=inner.dangerous_flags,
        cli_family=inner.cli_family,
        confidence=inner.confidence,
        reason=(
            f"Executor '{base_cmd}' runs mutative payload {payload!r}: "
            f"{inner.reason}"
        ),
        rule=inner.rule,
    )


def _check_command_substitutions(
    command: str, *, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Classify what a substitution ANYWHERE in *command* would execute.

    ``_classify_leading_shell_assignments`` already applies this exact standard
    to a substitution in the value of a leading assignment: the body is a real
    command, so it is judged by its own effect rather than by the word that
    happens to be at position 0.  One token further right the same body was
    judged by nothing at all -- every layer keys on the base command, and the
    base command of ``ls $(<a cluster deletion>)`` is ``ls``, which is
    read-only by elimination.  The permanent-deny floor and the ``.claude``
    boundary were closed through a substitution earlier; the APPROVABLE tier,
    which is where ordinary destruction lives, was not.  This extends the
    assignment policy to argument position, unchanged in standard: the same
    classifier, on the same body, reaching the same verdict.

    The verdict is deliberately the INNER one, so a mutation does not change
    category by being written inside a substitution.  A body that needs consent
    still needs exactly consent; a body on the permanent floor is not this
    lane's to escalate -- ``blocked_commands`` and ``protected_path_guard``
    already read the same bodies and answer categorically before this runs.

    Args:
        command: The command as it reaches classification, AFTER the ``cd`` /
            env / release-track peels, so *cwd* is the directory the body would
            really run in.  A body resolved against the wrong directory is not
            a harmless imprecision: a relative script path that does not exist
            THERE reads as unreadable and takes the conservative T3 default,
            which is a false approval prompt on a legitimate command.
        cwd: The effective working directory of *command*, inherited by every
            body -- which is what the shell does, since a substitution runs in
            a subshell of the current directory.  A body that starts with its
            own ``cd`` overrides it on re-entry, exactly as it would alone.
        _depth: Descent already spent by every lane, shared budget.

    Returns:
        A mutative result when some body is mutative, ``None`` otherwise --
        including when there is no substitution at all, which is the common
        case and costs one substring check.  Never returns a NON-mutative
        result: this lane may only ADD a verdict, so a read-only body leaves
        classification exactly where it found it.
    """
    bodies, truncated = extract_substitutions_truncated(
        command, top_level_only=True,
    )
    if not bodies:
        return None

    # The extractor stopped looking rather than finished looking, so the bodies
    # to the right of its cap were never classified. Same rule as the descent
    # bound below: an analysis that ran out of room retains.
    if truncated:
        return _budget_exhausted_result("command substitution count")

    if _depth >= _MAX_SUBSTITUTION_RECURSION_DEPTH:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="substitution-depth-exceeded",
            cli_family="system",
            confidence="low",
            reason=(
                "Command substitution nested past the analysis depth limit; "
                "the body was not read and is classified conservatively"
            ),
        )

    for body in bodies:
        inner = detect_mutative_command(body, cwd=cwd, _depth=_depth + 1)
        if not inner.is_mutative:
            continue
        return MutativeResult(
            is_mutative=True,
            category=inner.category,
            verb=inner.verb,
            dangerous_flags=inner.dangerous_flags,
            cli_family=inner.cli_family,
            confidence=inner.confidence,
            reason=(
                f"Command substitution executes mutative command "
                f"{body!r}: {inner.reason}"
            ),
        )
    return None


# ============================================================================
# Main Detection Function
# ============================================================================


# Programs whose own effect is destructive, remote or an execution of other
# commands. A wrapper declared under one of these names would otherwise shed
# that effect's gate by inheriting the wrapped CLI's exceptions.
_NEVER_INHERITING_PROGRAMS: FrozenSet[str] = frozenset({
    "shred", "truncate", "dd", "wipefs", "srm", "fdisk", "sfdisk", "cfdisk",
    "gdisk", "sgdisk", "parted", "blkdiscard", "mkswap", "hdparm", "rm",
    "rmdir", "unlink", "mv", "cp", "ln", "install", "tee", "chmod", "chown",
    "chgrp", "chattr", "kill", "pkill", "killall", "reboot", "shutdown", "halt",
    "poweroff", "crontab", "at", "sudo", "su", "doas", "eval", "exec", "nohup",
    "ssh", "scp", "sftp", "rsync", "xargs", "watch", "flock", "parallel",
    "curl", "wget", "nc", "ncat", "socat", "sed", "awk", "find",
})


def _is_unmodelled_cli(name: str) -> bool:
    """True when no rule of the classifier, nor its destructive-program list, names the program *name*."""
    from .flag_classifiers import _CLASSIFIER_REGISTRY
    from .shell_unwrapper import _SHELLS
    from .command_semantics import LONG_VALUE_FLAGS

    base = name.lower()
    if base != name or base.startswith("mkfs"):
        return False
    if "-" in base and base.split("-", 1)[0] in _PS_ALL_VERBS:
        return False
    name_keyed_tables = (
        CLI_FAMILY_LOOKUP, COMMAND_ALIASES, READ_ONLY_BASE_CMDS,
        _SCRIPT_FILE_INTERPRETERS, _INLINE_CODE_MAP, _POWERSHELL_INTERPRETERS,
        COMMAND_PATH_MUTATIVE_UPGRADES, COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS,
        CLI_MUTATIVE_PATH_PREFIXES, _COMMAND_EXECUTORS, _COMMAND_WRAPPERS,
        _PREFIX_RUNNER_SUBCOMMANDS, _PACKAGE_TOOL_COMMANDS, _PACKAGE_MANAGERS,
        _CMD_MUTATIVE_BUILTINS, _CMD_READ_BUILTINS, _CMD_SUBCOMMAND_BUILTINS,
        _PS_ALIASES, _PS_BARE_OBFUSCATION_ALIASES, F_FLAG_MEANS_FORCE,
        R_FLAG_MEANS_RECURSIVE_DELETE, D_FLAG_MEANS_FORCE_DELETE,
        M_FLAG_MEANS_FORCE_MOVE, DELETE_FLAG_IS_DESTRUCTIVE,
        HARD_FLAG_IS_DESTRUCTIVE, BOOLEAN_SHORT_FLAGS, LONG_VALUE_FLAGS,
        MUTATIVE_VERBS, SIMULATION_VERBS, READ_ONLY_VERBS,
        _CLASSIFIER_REGISTRY, _SHELLS, _NEVER_INHERITING_PROGRAMS,
    )
    return not any(base in table for table in name_keyed_tables)


def _absorbing_form(command: str) -> "Optional[str]":
    """Rewrite *command* as the pre-table tokenizer read it, or None if identical.

    ``BOOLEAN_SHORT_FLAGS`` narrowed which short flags spend the token after
    them.  For a flag it newly declares valueless, the token that used to be
    spent is now a positional, which moves every later positional one place
    toward the head.  This produces the string whose positionals are what the
    OLD grammar produced -- the flag stays, the token it used to swallow is
    dropped -- so the two readings can be classified against each other.

    Returns ``None`` when no declared flag fires, which is every command on a
    CLI outside the table and most commands on one inside it.

    The rebuilt string is a CLASSIFICATION artifact only, in the same sense as
    ``_peel_release_track_prefix``'s: nothing executes it and it never reaches
    an approval signature, which is built by
    ``approval_scopes.build_approval_signature`` from the command as the user
    wrote it.
    """
    tokens = tokenize_command(command)
    if len(tokens) < 3:
        return None

    base_cmd = tokens[0].rsplit("/", 1)[-1].lower()
    if base_cmd not in BOOLEAN_SHORT_FLAGS:
        return None

    kept: List[str] = [tokens[0]]
    args = list(tokens[1:])
    changed = False
    skip_next = False
    seen_non_flag = False
    for index, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if _is_flag(token):
            kept.append(token)
            if (
                not seen_non_flag
                and _is_short_value_flag(token)
                and index + 1 < len(args)
                and not _is_flag(args[index + 1])
            ):
                skip_next = True
                changed = changed or is_boolean_short_flag(base_cmd, token)
            continue
        kept.append(token)
        seen_non_flag = True

    if not changed:
        return None
    return shlex.join(kept)


def detect_mutative_command(
    command: str, from_source_code: bool = False, cwd: "Optional[str]" = None,
    _depth: int = 0, _in_prefix: bool = False, caller: "Optional[LocalWorkCaller]" = None,
) -> MutativeResult:
    """Classify *command*, then hold the result to a non-regression floor.

    ``BOOLEAN_SHORT_FLAGS`` decides, per CLI, which short flags take no value.
    A correct entry restores the reading the long form already had.  A WRONG
    entry -- a flag that really does take a value -- leaves that value standing
    as a positional and shifts the head by one, which drops every anchor path
    and subcommand key that matches from position 0.  That failure would open a
    gate, and a table of hand-audited facts about a dozen CLIs is not something
    to bet a gate on.

    So the verdict is the HIGHER of the two readings: the command as tokenized
    today, and the same command as the old absorbing grammar read it.  The
    floor is what makes the direction of a table error one-way -- a wrong entry
    can only cost a spurious approval prompt, never a mutation that walks
    through.  It runs at most once per command, only when a declared flag
    actually fires AND the primary reading came back non-mutative, so the
    ordinary path pays nothing for it.

    The floor is deliberately not symmetric: it never LOWERS a verdict.  The
    old reading is the corrupted one, and it is consulted only as a source of
    escalation.

    A wrapper declared in ``GAIA_CLI_ALIASES`` is also read as the CLI it
    wraps, so it is never classified more leniently than that CLI.  A wrapper
    the classifier does not model is classified as that CLI outright, which
    lets it inherit the CLI's exceptions (``gh label list``); a declared name
    the classifier models keeps its own gates.  The lookup stays outside the
    cached function so the cache remains keyed on the command text alone.
    """
    # The sync verdict depends on the repository's state, which the lru_cache
    # on _detect_mutative_command would freeze, so it is decided here.
    sync = None if from_source_code else _git_local_sync(command, cwd, _in_prefix)
    if sync is not None:
        return sync

    result = _detect_mutative_command(command, from_source_code, cwd, _depth, caller, _in_prefix)

    declared = declared_wrapper(command)
    if declared is not None:
        wrapper, wrapped = declared
        # One hop through the cached classifier: a declared cycle
        # (a=b,b=a) must not recurse.
        wrapped_result = _detect_mutative_command(wrapped, from_source_code, cwd, _depth)
        if wrapped_result.is_mutative and not result.is_mutative:
            return replace(
                wrapped_result,
                reason=f"{wrapped_result.reason} (declared in GAIA_CLI_ALIASES as a wrapper of this CLI)",
            )
        if result.is_mutative and not wrapped_result.is_mutative and _is_unmodelled_cli(wrapper):
            command = wrapped
            result = replace(
                wrapped_result,
                reason=f"{wrapped_result.reason} (inherited by a wrapper declared in GAIA_CLI_ALIASES)",
            )

    if result.is_mutative:
        return result

    elsewhere = None if from_source_code else _change_request_elsewhere(command, cwd)
    if elsewhere is not None:
        return elsewhere

    absorbing = _absorbing_form(command)
    if absorbing is None or absorbing == command:
        return result

    floor = _detect_mutative_command(absorbing, from_source_code, cwd, _depth)
    if not floor.is_mutative:
        return result
    return replace(
        floor,
        reason=(
            f"{floor.reason} (held at the pre-narrowing reading of the command: "
            f"a short flag declared valueless may have taken a value)"
        ),
    )


@functools.lru_cache(maxsize=128)
def _detect_mutative_command(  # noqa: C901 -- classification ladder, one step per lane
    command: str, from_source_code: bool = False, cwd: "Optional[str]" = None,
    _depth: int = 0, caller: "Optional[LocalWorkCaller]" = None, _in_prefix: bool = False,
) -> MutativeResult:
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
        from_source_code: True when ``command`` is a single line drawn from a
            non-shell SOURCE file (``.js``/``.ts``/``.rb``/...) being scanned
            by the script-content classifier, rather than a shell command line
            or a line from a shell script.  When True, camelCase subcommand
            splitting is suppressed: a camelCase token in source is a language
            identifier (``execPath``, ``setState``), not a CLI subcommand, and
            splitting it produced spurious mutative matches.  Whole-token and
            hyphen matching are unaffected.  Defaults to False so shell command
            lines and shell scripts keep the full camelCase behavior.
        cwd: Base directory against which a RELATIVE script-file / package.json
            path is resolved.  ``None`` (the default) falls back to the process
            cwd -- the previous, hook-cwd-only behavior.  A leading ``cd <dir>``
            in the command chain overrides this (see the ``cd``-peel below), so
            Gaia governs a script in the workspace it is actually run from, not
            only the Gaia install dir.
        caller: Who runs the command and from where, as the validator proved
            it; without one the rm/rmdir/mv/cp relief never applies.  The peels
            below re-classify without it, so a wrapper (``sudo``, ``env -C``)
            or a cd inside the string withdraws the relief.

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

    # A variable assignment changes only the current shell's transient
    # environment, but command substitutions in its value still execute.
    # Inspect those before env-prefix peeling; otherwise
    # ``TOKEN=$(mutative-command) read-only-command`` loses the mutation.
    git_config_result = _check_git_code_config(command)
    if git_config_result is not None:
        return git_config_result
    environment_result = runs_under_injected_environment(
        command, code_injecting_assignments(command), cwd=cwd, _depth=_depth,
    )
    if environment_result is not None:
        return environment_result

    assignment_result = _classify_leading_shell_assignments(
        command, cwd=cwd, _depth=_depth,
    )
    if assignment_result is not None:
        return assignment_result

    # --- Honor leading `cd <dir>` navigation in a chain ---
    # ``cd /repo && node build.mjs`` runs the script relative to /repo, not the
    # hook's cwd.  Peel the leading ``cd`` clause(s), fold the effective cwd,
    # and re-classify the remainder from there.  Only the first non-``cd`` clause
    # is classified here (this function classifies ONE command; the compound
    # validator handles the remaining clauses of a chain) -- the peel only
    # corrects the cwd the script path resolves against.
    peeled_cwd, remainder, peeled = _peel_leading_cd(command, cwd)
    if peeled and remainder:
        # Peeling a leading `cd` re-classifies the SAME command (minus the cd),
        # not a nested body -- carry _depth through unchanged so the recursion
        # budget is not consumed by cwd normalization.
        return detect_mutative_command(
            remainder, from_source_code=from_source_code, cwd=peeled_cwd,
            _depth=_depth,
        )

    # --- Honor leading assignments and transparent wrappers ---
    # ``GITHUB_TOKEN=$(gh auth token) terragrunt apply``, ``env FOO=bar ...``,
    # ``sudo``/``timeout 30``/``nice -n 5`` ...: the REAL command and its verb
    # follow the prefix, but ``analyze_command`` keys off ``tokens[0]``. Peel and
    # re-classify -- SAME command minus an inert prefix, so ``_depth`` is carried
    # through unchanged (mirrors the ``cd`` peel above).
    readings, env_peeled = _peel_wrapper_readings(command)
    env_remainder = readings[0]
    if env_peeled and env_remainder and env_remainder != command.strip():
        config_result = _check_package_config_environment(command, env_remainder)
        if config_result is not None:
            return config_result
        target_result = _check_target_environment(command, env_remainder, cwd=cwd, _depth=_depth)
        if target_result is not None:
            return target_result
        if sets_node_options(command):
            node_options_result = node_test_under_node_options(env_remainder, cwd)
            if node_options_result is not None:
                return node_options_result
        prefixed_relief = _git_relief_under_prefix(readings, cwd)
        if prefixed_relief is not None:
            return prefixed_relief
        for reading in readings:
            result = detect_mutative_command(
                reading, from_source_code=from_source_code, cwd=cwd,
                _depth=_depth, _in_prefix=True,
            )
            if result.is_mutative:
                break
        return result
    for reading in readings[1:] if env_peeled else ():
        result = detect_mutative_command(
            reading, from_source_code=from_source_code, cwd=cwd,
            _depth=_depth, _in_prefix=True,
        )
        if result.is_mutative:
            return result

    # --- Honor a release-track prefix (`gcloud alpha ...`, `gcloud beta ...`) ---
    # A track word between the tool and the command names a different API track
    # for the SAME operation, and shifts every following token one position.
    # Both position-sensitive mechanisms below read those positions -- the
    # anchored upgrade in Step 3e.5 matches a path as a PREFIX, and the Step 4
    # verb scan splits a compound subcommand only near the head -- so peeling
    # here, ABOVE both, is what lets one normalization serve them rather than
    # each being patched on its own. SAME command minus a routing word, so
    # ``_depth`` is carried through unchanged (mirrors the two peels above).
    track_remainder, track_peeled = _peel_release_track_prefix(command)
    if track_peeled and track_remainder and track_remainder != command.strip():
        return detect_mutative_command(
            track_remainder, from_source_code=from_source_code, cwd=cwd,
            _depth=_depth,
        )

    # --- Classify what a command substitution would execute ---
    # The shell evaluates ``$(...)`` / ``` `...` ``` / ``<(...)`` BEFORE the
    # outer command runs, so the body executes whatever the carrier turns out to
    # be -- and every step below this one keys on the carrier.  Placed AFTER the
    # three peels above so the body inherits the cwd the outer command actually
    # has: the peels are what fold a leading ``cd`` into it, and running this
    # ahead of them would resolve a relative script in the body against the
    # wrong directory.  Placed BEFORE the ladder so the read-only fast path
    # cannot answer for a carrier whose argument is a mutation.
    #
    # ``from_source_code`` suppresses it: there, ``$(`` is a language construct
    # (a DOM/jQuery call, a PHP variable) and not shell execution.  A shell
    # command a source file really runs reaches this lane through the exec-sink
    # extractor, which re-dispatches it as the shell command it is.
    if not from_source_code:
        substitution_result = _check_command_substitutions(
            command, cwd=cwd, _depth=_depth,
        )
        if substitution_result is not None:
            return substitution_result

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

    # --- Step 0-redirect: a redirect onto an account-sensitive path ---
    # Ahead of every step below: the alias fast-path and the read-only base
    # commands key on the base command alone and return before any destination
    # is looked at.
    account_redirect = _check_account_path_redirect(command)
    if account_redirect is not None:
        return account_redirect

    # --- Step 0-dynamic: the program, or the verb word of a verb-first CLI, is a shell expansion ---
    program = command.lstrip()
    program_is_dynamic = _is_shell_expansion(tokens[0]) or program.startswith(("$(", "`"))
    literal_words = _literal_words(base_cmd, tokens[1:])
    first_word = literal_words[0] if literal_words else ""
    unlisted_subcommand = (
        family == "unknown" and base_cmd not in READ_ONLY_BASE_CMDS | _EXPRESSION_FIRST_TOOLS
        and any(_is_known_verb(word) for word in literal_words)
    )
    if program_is_dynamic:
        dynamic_word = tokens[0]
    elif family in _VERB_FIRST_FAMILIES or unlisted_subcommand:
        dynamic_word = _dynamic_verb_span_word(base_cmd, tokens[1:])
    else:
        dynamic_word = None
    if dynamic_word is not None and not from_source_code:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_UNKNOWN,
            verb="dynamic-program" if program_is_dynamic else "dynamic-verb",
            cli_family=family,
            reason=f"the word {dynamic_word!r} naming what runs could not be determined",
            rule="unknown.dynamic-span",
        )
    spellings, exact = _cloud_command_readings(base_cmd, tokens)
    readings = [_detect_mutative_command(s, from_source_code, cwd, _depth, caller, _in_prefix) for s in spellings]
    changed = next((reading for reading in readings if reading.is_mutative), None)
    if changed is not None or (readings and exact):
        return changed or readings[0]
    if unlisted_subcommand and "-" in first_word and _verb_word_effect(first_word) == CATEGORY_MUTATIVE:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb=first_word,
            cli_family=family,
            confidence="medium",
            reason=f"the verb word {first_word!r} carries a mutative part",
        )

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

        # rm scratch-directory override: classify as T0 only when every target
        # is confined to scratch AND nothing it reaches carries durable-store
        # evidence.  Location alone is not the judgement -- an object valuable
        # by its contents stays T3 wherever it sits, which is also what makes
        # its deletion consentable at all.  The catastrophic floor (rm -rf /,
        # /*, ~) still runs first in blocked_commands.py, which defers to this
        # detector only for scratch-confined rm commands.
        if base_cmd in ("rm", "rmdir"):
            scratch_targets = _rm_scratch_confined_targets(tuple(tokens), caller)
            if scratch_targets:
                valuable = _durable_store_under_scratch_targets(scratch_targets)
                if valuable is None:
                    return MutativeResult(
                        is_mutative=False,
                        category=CATEGORY_READ_ONLY,
                        verb=base_cmd,
                        cli_family="system",
                        confidence="high",
                        reason=(
                            "rm targeting only the Gaia scratch directory "
                            "(~/.gaia/scratch); all paths resolve strictly "
                            "inside scratch via realpath and reach no "
                            "durable store"
                        ),
                    )
                return MutativeResult(
                    is_mutative=True,
                    category=alias_category,
                    verb=base_cmd,
                    dangerous_flags=dangerous_flags,
                    cli_family="system",
                    confidence="high",
                    reason=(
                        f"rm is confined to the Gaia scratch directory but "
                        f"reaches durable content: {valuable}"
                    ),
                )

        # mv and cp are free when every path they touch stays in scratch or a
        # managed worktree and none overwrites a durable store parked there.
        if base_cmd in ("cp", "mv") and caller is not None:
            local_work = _cp_local_work_written_paths if base_cmd == "cp" else _mv_local_work_written_paths
            written = local_work(tuple(tokens), caller)
            if written and _durable_store_under_scratch_targets(written) is None:
                return MutativeResult(
                    is_mutative=False,
                    category=CATEGORY_READ_ONLY,
                    verb=base_cmd,
                    cli_family="system",
                    confidence="high",
                    reason=(
                        f"{base_cmd} writing only inside the Gaia scratch directory "
                        f"or a Gaia-managed worktree, overwriting no durable store"
                    ),
                )

        return MutativeResult(
            is_mutative=True,
            category=alias_category,
            verb=base_cmd,
            dangerous_flags=dangerous_flags,
            cli_family=family if family != "unknown" else "system",
            confidence="high",
            reason=f"Command alias '{base_cmd}' is {alias_category.lower()}",
        )

    nested_result = _check_nested_executor(semantics, cwd=cwd, _depth=_depth)
    if nested_result is not None:
        return nested_result

    # --- Step 1a-exec: a command handed to an executor as a STRING ---
    # Runs ahead of the read-only fast path because one of the executors that
    # takes a command as data (`find ... -exec`) is itself on that whitelist,
    # and the fast path would answer for the carrier before the payload is ever
    # looked at.
    executor_result = _check_executor_payload(
        base_cmd, semantics, cwd=cwd, _depth=_depth,
    )
    if executor_result is not None:
        return executor_result

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

    # --- Step 1b-win: Bare Windows command (no powershell.exe/pwsh wrapper) ---
    # A peeled `Remove-Item -Recurse -Force`, a cmd.exe `del`/`rd`, or a PS alias
    # would otherwise reach the POSIX verb scanner with no subcommand to match
    # and fall to safe-by-elimination (T0), mutating without a gate.  This lane
    # inverts the default to conservative DEFAULT-DENY for recognized Windows
    # tokens (unknown verb/cmdlet/subcommand -> T3) and returns None for
    # everything else so POSIX/bash classification is untouched.  Placed AFTER
    # COMMAND_ALIASES and READ_ONLY_BASE_CMDS so rm/mv/cp/mkdir keep their POSIX
    # overrides (scratch/sensitive-path) and ls/cat/type/echo stay T0.
    win_result = _check_windows_native_command(
        command, base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if win_result is not None:
        return win_result

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
    load_result = _check_interpreter_loads(base_cmd, semantics, cwd, _depth)
    if load_result is not None:
        return load_result

    py_module_result = _check_python_module_runner(base_cmd, semantics)
    if py_module_result is not None:
        return py_module_result

    # --- Step 1c-run: prefix-runner re-dispatch (uv run, poetry run, npx) ---
    # A runner's own base token is not an interpreter, so the script-file lane
    # below never opens the wrapped script and the whole content-inspection
    # layer is skipped: ``uv run deploy.py`` classified T0 while the identical
    # ``python3 deploy.py`` classified T3.  Resolve the wrapped command and
    # re-classify it so both spellings agree.  Returns None for any other form
    # of these CLIs (``uv pip install``, ``poetry add``), which ordinary verb
    # detection already owns.
    runner_result = _check_prefix_runner(
        base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if runner_result is not None:
        return runner_result

    # --- Step 1c-ps: PowerShell command / script introspection ---
    # ``powershell.exe -Command "<script>"`` collapses its payload into a single
    # opaque token, so the POSIX verb scanner never sees the cmdlets inside it --
    # a destructive ``Remove-Item -Recurse`` slips through as safe-by-elimination
    # while every benign PowerShell call is over-blocked.  Introspect the payload
    # of ``-Command``/``-c`` (and ``-File <script.ps1>`` via its file contents),
    # classify each cmdlet by its Verb-Noun verb against the approved-verb
    # taxonomy, escalate the WHOLE payload to T3 on any change/unknown verb or
    # obfuscation marker, and fall back to conservative T3 on an un-inspectable
    # payload.  Returns None when the command is not a PowerShell interpreter.
    ps_result = _check_powershell_command(
        command, base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if ps_result is not None:
        return ps_result

    # --- Step 1d: Script-file analysis (python3 deploy.py, bash setup.sh, ./x) ---
    # An interpreter invoked with a script FILE as a positional argument, or a
    # direct ``./script`` invocation, hides its mutations inside the file --
    # the verb scanner sees only the filename.  Read the referenced file and
    # classify it by REAL invocation, the same standard the inline -c path meets.
    # Placed before the single-token early return so a bare ``./deploy.sh`` (one
    # token) is still inspected.  Returns None when the command is not a
    # recognized script-file shape, so detection continues normally.
    script_result = _check_script_file(
        command, base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if script_result is not None:
        return script_result

    # --- Step 1e: package managers (npm, bun, pnpm, yarn) ---
    # A script name is arbitrary, so `<manager> run <script>` classifies by the
    # script's package.json body (unresolvable -> conservative T3); adding a
    # dependency is T3; a lockfile-frozen install is classified by the project's
    # install lifecycle scripts.  Returns None for other forms.
    package_result = _check_package_manager(
        base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if package_result is not None:
        return package_result

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
    # Deliberately AFTER the content-inspection lanes (Step 1d/1e), not before
    # them.  A simulation-shaped flag is a claim made by the invocation line;
    # it is not evidence about what the invoked payload does.  Running it first
    # made ANY script invocation carrying such a token non-mutative
    # unconditionally, without reading the script and without checking that the
    # script honors the flag -- measured on this repository's own
    # ``scripts/release-prepare.mjs`` (an unconditional ``fs.writeFileSync``
    # that never reads ``process.argv``): it classified MUTATIVE bare, and free
    # with ``--dry-run`` appended, and free again with a simulation-set flag
    # belonging to an unrelated CLI.  The legitimate case it was moved for -- a
    # script whose mutation sits behind its own ``--dry-run`` guard -- is served
    # instead inside the script-file lane by ``_script_honored_simulation_flag``,
    # which frees the invocation only when the script's own executable text
    # references the flag that was passed.
    if _simulation_flag_holds(tokens):
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
        # Count semantic non-flag tokens only. Shell redirect shorthands
        # like "2>&1", ">file", "<file" appear as non-flag tokens in shlex
        # output but carry no CLI semantic value -- filtering them keeps
        # "gaia approvals clean --help 2>&1" at threshold 2 instead of 3.
        semantic_non_flags = [
            t for t in semantics.non_flag_tokens
            if not _SHELL_REDIRECT_RE.match(t)
        ]
        # A help flag only counts when it sits BEFORE a literal `--`
        # separator: tokens after `--` are a verbatim payload the CLI passes
        # through (gcloud compute ssh ... -- <remote command> --help runs the
        # remote command; the trailing --help is ssh's argument, not gcloud's).
        help_requested = _program_asked_for_help(command, base_cmd)
        # The any-position whitelist must never outrank an explicitly
        # registered mutative command path: `terraform state rm ... --help` /
        # `gcloud billing projects unlink ... --help` stay gated even though
        # their positional count exceeds the generic boundary.
        command_path = tuple(t.lower() for t in semantic_non_flags)
        on_mutative_path = any(
            command_path[:len(prefix)] == prefix
            for prefix in CLI_MUTATIVE_PATH_PREFIXES.get(base_cmd, ())
        )
        within_generic_boundary = len(semantic_non_flags) <= 2
        if (
            help_requested
            and not on_mutative_path
            and (
                within_generic_boundary
                or base_cmd in HELP_ANY_POSITION_BASE_CMDS
            )
        ):
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
                # Beyond the generic 2-positional boundary the exemption rests
                # on the any-position whitelist alone -- do not report it with
                # full confidence.
                confidence="high" if within_generic_boundary else "medium",
                reason=(
                    f"--help on whitelisted CLI '{base_cmd}' "
                    "with simple invocation (<=2 non-flag tokens)"
                    if within_generic_boundary
                    else f"--help on whitelisted CLI '{base_cmd}' "
                    "via any-position help whitelist"
                ),
            )

    # --- Step 3b: Inline code safety check (python3 -c, node -e, etc.) ---
    # For runtime interpreters with inline code flags, scan the code string
    # using the 3-layer approach instead of verb-matching tokens (which would
    # false-positive on generic keywords like "import", "create", etc.).
    cli_flags = _INLINE_CODE_MAP.get(base_cmd, frozenset())
    if base_cmd in _INLINE_CODE_CLIS and (
        cli_flags & set(semantics.flag_tokens) or (base_cmd == "deno" and tuple(semantics.non_flag_tokens[:1]) == ("eval",))
    ):
        return _check_inline_code(command, base_cmd, family, cwd=cwd, _depth=_depth)

    # --- Step 3c: Heredoc safety check ---
    # When a runtime interpreter is invoked with '-' (stdin) and the command
    # contains a heredoc ('<<'), the heredoc body is script source --
    # not shell subcommands.  Route through inline code analysis.
    # The length heuristic is suppressed: multi-line heredocs are normal
    # and must not be flagged on size alone. A shell's heredoc program is a
    # script, so it is read line by line as ``bash script.sh`` would be.
    program = heredoc_program(command)
    if program is not None and program.interpreter == base_cmd and program.is_shell:
        return _classify_script_content_by_regex(
            program.body, "<heredoc>", family, cwd=cwd, _depth=_depth + 1,
        )
    if base_cmd in _INLINE_CODE_CLIS and "<<" in command and (
        program is not None
        or (semantics.non_flag_tokens and semantics.non_flag_tokens[0] == "-")
    ):
        return _check_inline_code(command, base_cmd, family, skip_length_check=True, cwd=cwd, _depth=_depth)

    # --- Step 3d: CLI-specific mutative command paths ---
    command_path = tuple(token.lower() for token in semantics.non_flag_tokens)
    for mutative_prefix in CLI_MUTATIVE_PATH_PREFIXES.get(base_cmd, ()):
        if command_path[:len(mutative_prefix)] == mutative_prefix:
            dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=mutative_prefix[-1],
                dangerous_flags=dangerous_flags,
                cli_family=family,
                confidence="high",
                reason=(
                    f"State-mutating command path "
                    f"'{' '.join((base_cmd,) + mutative_prefix)}'"
                ),
            )

    # --- Step 3e: Git local-only subcommand guard ---
    # When base_cmd is "git" and the subcommand is local-only (commit, stash,
    # add, log, etc.), short-circuit to non-mutative.  This prevents message
    # body text after -m from leaking into the verb scanner and triggering
    # false positives on words like "update", "create", "deploy".
    # Dangerous flags (-D, -M, --force) are still checked so that
    # "git branch -D feature" remains flagged.
    if base_cmd == "git" and semantics.non_flag_tokens:
        # Step 3e-wt: the `git worktree` family is modelled explicitly rather
        # than left to the verb scanner, which gated `remove` only by an
        # incidental token match and let `add` through as safe by elimination.
        # Stands aside (None) for every subcommand it does not model.
        worktree_result = _check_git_worktree(semantics, tuple(tokens), family)
        if worktree_result is not None:
            return worktree_result

        # Step 3e-cfg: `git config` is dual-mode and its write form carries no
        # verb, so the scanner below reaches it as safe by elimination. Stands
        # aside (None) for every read form, which keeps its verdict unchanged.
        config_result = _check_git_config(semantics, tuple(tokens), family)
        if config_result is not None:
            return config_result

        relief_result = _check_git_publish_relief(semantics, tuple(tokens), family, cwd)
        if relief_result is not None:
            return relief_result

        git_subcmd = semantics.non_flag_tokens[0]
        subcommand_split = _git_subcommand_split(tuple(tokens))
        subcommand_args = subcommand_split[2] if subcommand_split else tokens
        reset_flags = tuple(sorted(
            _GIT_BRANCH_RESET_FLAGS.get(git_subcmd, frozenset()).intersection(subcommand_args)
        ))
        if reset_flags:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=git_subcmd,
                dangerous_flags=reset_flags,
                cli_family=family,
                confidence="high",
                reason=(
                    f"'git {git_subcmd} {reset_flags[0]}' resets the branch if it "
                    f"already exists, discarding the commits only it pointed at"
                ),
            )
        if git_subcmd == "grep" and git_grep_opens_pager(tuple(tokens)):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=git_subcmd,
                cli_family=family,
                confidence="high",
                reason="'git grep -O' runs a command over every matching file",
            )
        written = _git_output_targets(tuple(tokens), git_subcmd)
        if written and not _git_output_in_owned_entry(tuple(tokens), written, caller):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=git_subcmd,
                cli_family=family,
                confidence="high",
                reason=f"'git {git_subcmd}' writes {written[0] or 'a file'} outside the caller's own scratch entry",
            )
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

    # --- Step 3e-tee: direct write onto a sensitive path ---
    # `tee` is a mutative UPGRADE from safe-by-elimination like the anchors
    # below, but its write form is defined by the DESTINATION rather than by a
    # subcommand path or a flag, which an anchor cannot express -- so a
    # discriminator decides it the way `git config` and `git tag` are decided.
    # Stands aside (None) for the passthrough and for every ordinary
    # destination, which keeps their verdict unchanged. Placed here, with the
    # anchors, so simulation (Step 3) and --help (Step 3.5) still outrank it.
    tee_result = _check_tee_write(base_cmd, tuple(tokens))
    if tee_result is not None:
        return tee_result

    # --- Step 3e.4: IAM binding change, decided by the token's form ---
    # Behind simulation (Step 3) and --help (Step 3.5) so both still outrank it,
    # ahead of the Step 4 verb scan that splits `add-iam-policy-binding` onto a
    # verb the taxonomy leaves free.
    iam_binding_result = _check_iam_policy_binding(family, semantics)
    if iam_binding_result is not None:
        return iam_binding_result

    # --- Step 3e.5: Command-path mutative UPGRADE (anchored) ---
    # The symmetric opposite of the downgrade exception in Step 3e: anchor an
    # exact command path to MUTATIVE when it carries no verb in MUTATIVE_VERBS
    # and would otherwise reach Step 4 and be READ_ONLY "by elimination".
    # Position is load-bearing in both directions: AFTER the simulation-flag
    # (Step 3) and --help (Step 3.5) overrides, so `--dry-run`/`--help` still
    # win; BEFORE the Step 4 verb scan, so an anchor is still reached when a
    # read-only noun sits at the head of the path and would short-circuit there.
    if semantics.non_flag_tokens:
        for anchor in COMMAND_PATH_MUTATIVE_UPGRADES.get(base_cmd, ()):
            anchor_flags = anchor.matched_flags(semantics)
            if anchor_flags is None:
                continue
            flag_detail = (
                f" with flag(s) {', '.join(anchor_flags)}" if anchor_flags else ""
            )
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=anchor.path[-1],
                dangerous_flags=anchor_flags,
                cli_family=family,
                confidence="high",
                reason=(
                    f"State-mutating command "
                    f"'{base_cmd} {' '.join(anchor.path)}'{flag_detail} "
                    f"anchored MUTATIVE (T3) by config"
                    + (f". {anchor.guidance}" if anchor.guidance else "")
                ),
                guidance=anchor.guidance,
            )

    shape_result = _check_generic_cli_shape(base_cmd, family, semantics)
    if shape_result is not None:
        return shape_result

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
        # A group can nest its OWN action verb one token deeper (`gaia task
        # gate <add|remove|...>`). The checks above only ever look at
        # non_flag_tokens[1] ("gate"), so a destructive verb hiding at
        # non_flag_tokens[2] ("remove") would otherwise pass as bookkeeping.
        # See NESTED_SUBCOMMAND_EXTRA_DENY_VERBS for why this is anchored to
        # the specific (base_cmd, group, subgroup) triple rather than a
        # blanket token[2] check.
        nested_verb = (
            semantics.non_flag_tokens[2]
            if len(semantics.non_flag_tokens) > 2 else ""
        )
        _nested_deny = NESTED_SUBCOMMAND_EXTRA_DENY_VERBS.get(
            (base_cmd, semantics.non_flag_tokens[0], group_verb), frozenset()
        )
        nested_verb_is_destructive = (
            nested_verb.split("-", 1)[0] in _nested_deny
            or nested_verb in _nested_deny
        )
        # `gaia session` is classified per verb, not as a group: a literal
        # SESSION_VERBS path (two reads, and the resume-point file replaced on
        # every write, which also drops points older than 30 days) is
        # read-only; a word only the shell resolves is T3, since it may be the
        # write; an undeclared verb falls to the ordinary scan.
        if subcommand_key == ("gaia", "session"):
            from .subagent_resume_point_guard import declared_session_verb, is_shell_resolved

            session_path = declared_session_verb(semantics.non_flag_tokens[1:])
            if session_path is not None:
                return MutativeResult(
                    is_mutative=False,
                    category=CATEGORY_READ_ONLY,
                    verb=" ".join(session_path),
                    cli_family=family,
                    confidence="high",
                    reason=f"'gaia session {' '.join(session_path)}' is declared in SESSION_VERBS",
                )
            unresolved = [t for t in semantics.non_flag_tokens[1:3] if is_shell_resolved(t)]
            if unresolved:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=unresolved[0],
                    cli_family=family,
                    confidence="high",
                    reason=f"'gaia session' verb '{unresolved[0]}' is resolved only by the shell",
                )
        if subcommand_key in COMMAND_SUBCOMMAND_TIER_EXCEPTIONS:
            if verb_is_destructive or nested_verb_is_destructive:
                # Whole-record destruction (e.g. `gaia plan delete`, `gaia
                # task gate remove`) must stay T3 even inside an excepted
                # group.  Anchor it MUTATIVE here instead of falling through
                # to Step 4: the group token itself (`plan`) collides
                # lexically with SIMULATION_VERBS['plan'], so the verb
                # scanner would otherwise mis-classify the whole command as
                # SIMULATION and silently un-gate the delete.  This explicit
                # return is what makes `gaia plan delete` behave like `gaia
                # brief delete` (where `brief` has no such collision).
                destructive_verb = (
                    nested_verb.split("-", 1)[0]
                    if nested_verb_is_destructive
                    else group_verb.split("-", 1)[0]
                )
                destructive_path = (
                    f"{base_cmd} {semantics.non_flag_tokens[0]} {group_verb} {nested_verb}"
                    if nested_verb_is_destructive
                    else f"{base_cmd} {semantics.non_flag_tokens[0]} {group_verb}"
                )
                dangerous_flags = _scan_dangerous_flags(tokens, base_cmd)
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=destructive_verb,
                    dangerous_flags=dangerous_flags,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Whole-record destruction "
                        f"'{destructive_path}' "
                        f"stays T3 despite the local bookkeeping exception"
                    ),
                )
            exempt_flags = _always_flag_exemptions(base_cmd, semantics)
            dangerous_flags = tuple(
                flag for flag in _scan_dangerous_flags(tokens, base_cmd)
                if flag not in exempt_flags
            )
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

        cr_action = change_request_action(semantics.non_flag_tokens)
        cr_revoking = _CHANGE_REQUEST_LANDING_FLAGS + (_CHANGE_REQUEST_EDIT_FLAGS if cr_action != "create" else ())
        if (
            cr_action in _CHANGE_REQUEST_OWN_WORK
            and (base_cmd in _FORGE_CLIS or _is_unmodelled_cli(base_cmd))
            and not _scan_dangerous_flags(tokens, base_cmd)
            and not _carries_revoking_flag(tokens, cr_revoking)
        ):
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_UNKNOWN,
                verb=cr_action,
                cli_family=family,
                confidence="high",
                reason=f"'{base_cmd} {' '.join(semantics.non_flag_tokens[:3])}' opens or edits a change request, free for any forge binary",
            )

        verb_categories = SUBCOMMAND_VERB_TIER_EXCEPTIONS.get(subcommand_key, {})
        if group_verb in verb_categories and not _scan_dangerous_flags(tokens, base_cmd):
            return MutativeResult(
                is_mutative=False,
                category=verb_categories[group_verb],
                verb=group_verb,
                cli_family=family,
                confidence="high",
                reason=(
                    f"'{base_cmd} {semantics.non_flag_tokens[0]} {group_verb}' "
                    f"excepted to {verb_categories[group_verb].lower()} by config"
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

    if base_cmd in APPLYING_PROGRAMS and not APPLYING_PROGRAMS[base_cmd] & set(tokens):
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb=base_cmd,
            dangerous_flags=_scan_dangerous_flags(tokens, base_cmd),
            cli_family=family,
            confidence="high",
            reason=f"'{base_cmd}' applies its configuration to the hosts it names",
        )
    if _output_pushes(tuple(tokens)):
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="push",
            cli_family=family,
            confidence="high",
            reason="An output option sends the build to a registry",
        )
    local_cache_path = "" if _in_prefix else _local_cache_refresh(base_cmd, semantics)
    if local_cache_path and not _scan_dangerous_flags(tokens, base_cmd):
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb=local_cache_path.split()[-1],
            cli_family=family,
            confidence="high",
            reason=f"'{base_cmd} {local_cache_path}' only refreshes a local cache",
        )
    hyphen_depth = GROUP_PATH_HYPHEN_DEPTH if base_cmd in _GROUP_PATH_CLIS else 2
    option_values = _option_value_positions(semantics)
    configure_at = (
        _configure_position(semantics.semantic_head_tokens, option_values) if base_cmd in _GROUP_PATH_CLIS else 0
    )
    path_anchored = any(
        semantics.non_flag_tokens[:len(anchor.path)] == anchor.path
        for anchor in COMMAND_PATH_MUTATIVE_UPGRADES.get(base_cmd, ())
    )

    # --- Step 4: Scan semantic non-flag tokens near the command head ---
    # Priority order: SIMULATION > MUTATIVE > READ_ONLY > ALIASES
    for semantic_index, token in enumerate(semantics.semantic_head_tokens[1:], start=1):
        # `deploy-key` is a noun, so hyphen-splitting it onto `deploy` reads a
        # listing as a deployment; add/delete still reach the split below.
        if token == "deploy-key" and tuple(
            semantics.semantic_head_tokens[semantic_index + 1:semantic_index + 2]
        ) == ("list",):
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb="list",
                cli_family=family,
                confidence="high",
                reason="Listing deploy keys is read-only",
            )

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

        # Deeper than the subcommand path, a hyphenated token is an argument
        # slug ("some-name-with-delete-in-it"), never split.
        if semantic_index <= hyphen_depth and "-" in stripped_token and is_subcmd_shape:
            is_group = any(
                _names_operation(t)
                for p, t in enumerate(semantics.semantic_head_tokens[:hyphen_depth + 1])
                if p > semantic_index and p not in option_values
            )
            candidate, compound_change = _hyphen_candidate(stripped_token, is_group)
        else:
            candidate, compound_change = stripped_token, False

        full_lower = stripped_token
        confidence = "high" if semantic_index <= 2 else "medium"

        if configure_at and semantic_index == configure_at:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="config",
                dangerous_flags=_scan_dangerous_flags(tokens, base_cmd),
                cli_family=family,
                confidence=confidence,
                reason=f"'{base_cmd} {' '.join(semantics.non_flag_tokens[:configure_at])}' configures the group it ends",
            )

        destroying_flags = VERB_DESTROYING_FLAGS.get(full_lower, frozenset()) & frozenset(semantics.flag_tokens)
        if destroying_flags:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=full_lower,
                dangerous_flags=tuple(sorted(destroying_flags)),
                cli_family=family,
                confidence="high",
                reason=f"'{full_lower}' with {', '.join(sorted(destroying_flags))} destroys what it held",
                rule="sign.destroys",
            )

        if candidate in SIMULATION_VERBS or full_lower in SIMULATION_VERBS:
            verb = candidate if candidate in SIMULATION_VERBS else full_lower
            if family in LIVE_SIMULATION_VERBS.get(verb, frozenset()):
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=verb,
                    dangerous_flags=_scan_dangerous_flags(tokens, base_cmd),
                    cli_family=family,
                    confidence=confidence,
                    reason=f"'{verb}' runs against live state on a {family} CLI",
                )
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_SIMULATION,
                verb=verb,
                cli_family=family,
                confidence=confidence,
                reason=f"Simulation verb '{verb}'",
            )

        if compound_change or candidate in MUTATIVE_VERBS or full_lower in MUTATIVE_VERBS:
            verb = candidate if compound_change or candidate in MUTATIVE_VERBS else full_lower

            # git tag is dual-mode: a listing form (`git tag`, `-l`,
            # `--points-at`, `--contains`, ...) is read-only (T0), while a
            # create/delete/force form is mutative (T3). A plain flag-set
            # override cannot express this, so a dedicated discriminator
            # decides. See _classify_git_tag.
            if family == "git" and verb == "tag":
                if _classify_git_tag(semantics) == CATEGORY_READ_ONLY:
                    return MutativeResult(
                        is_mutative=False,
                        category=CATEGORY_READ_ONLY,
                        verb=verb,
                        cli_family=family,
                        confidence="high",
                        reason="git tag listing form (no tag name / mutation flags) is read-only",
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
                    reason=f"git tag create/delete/force is mutative{flag_detail}",
                )

            # Check verb+flag overrides: some verbs become READ_ONLY with
            # specific flags.
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
            if exception_key in CLI_VERB_TIER_EXCEPTIONS and not _may_archive_gmail(semantics):
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
        #
        # Source-code guard (word-boundary discipline for non-shell script
        # content): camelCase multi-word tokens are split so a CLI subcommand
        # like ``batchDelete`` -> ``delete`` is caught.  But when the scanned
        # content is a non-shell SOURCE language (``.js``/``.ts``/``.rb`` etc.,
        # see ``from_source_code``), a camelCase token is overwhelmingly a
        # source identifier, not a CLI subcommand -- ``execPath``/``execSync``
        # -> ``exec``, ``setState`` -> ``set``, ``stopPropagation`` -> ``stop``,
        # ``postMessage`` -> ``post``, ``createElement`` -> ``create``.
        # Splitting those forced spurious T3 on read-only scripts.  The guard
        # fires ONLY for source-code content, so shell command lines and shell
        # scripts (``aws batchDelete``, ``mytool batchDelete``) are unchanged,
        # and whole-token / hyphen matching (``mytool install``, ``git tag``)
        # is never gated.
        raw_token = semantics.semantic_head_tokens_raw[semantic_index] if semantic_index < len(semantics.semantic_head_tokens_raw) else token
        camel_parts = split_camel_case(raw_token)
        if (
            semantic_index == 1
            and len(camel_parts) > 1
            and _is_subcommand_identifier(raw_token)
            and not from_source_code
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
                    if (family, part) in CLI_VERB_TIER_EXCEPTIONS and not _may_archive_gmail(semantics):
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
            next_head = semantics.semantic_head_tokens[semantic_index + 1:semantic_index + 2]
            group_word_defers = (
                verb in GROUP_READ_WORDS
                and semantic_index < hyphen_depth
                and next_head
                and _head_token_mutates(next_head[0])
            )
            group_path_defers = base_cmd in _GROUP_PATH_CLIS and (
                configure_at > semantic_index
                or _later_head_mutates(semantics.semantic_head_tokens, semantic_index, hyphen_depth, option_values)
            )
            if not path_anchored and (group_word_defers or group_path_defers):
                continue
            # An ALWAYS-dangerous flag escalates even a read-only verb: the
            # Step 5 flag scan below never runs for a read-only verb because
            # this early-return fires first, so `terraform state list --prune`
            # (`list` is read-only, `--prune` reaches live state on that CLI)
            # would otherwise skip the ALWAYS escalation entirely. Only ALWAYS
            # flags override here -- CONTEXT flags are family-gated and a
            # benign `-r`/`-f` on a read-only verb must NOT be treated as
            # mutative.
            #
            # An exact (CLI, path, flag) anchor in
            # COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS narrows this further: an
            # ALWAYS flag whose destructive meaning is real on some CLIs and
            # inert on this one is subtracted from the escalating set before
            # the decision, never removed from DANGEROUS_FLAGS itself -- every
            # OTHER CLI keeps the unmodified ALWAYS default.
            always_flags = _scan_always_dangerous_flags(tokens)
            exempt_flags = _always_flag_exemptions(base_cmd, semantics)
            escalating_flags = tuple(
                flag for flag in always_flags if flag not in exempt_flags
            )
            if escalating_flags:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=verb,
                    dangerous_flags=escalating_flags,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Read-only verb '{verb}' escalated by ALWAYS-dangerous "
                        f"flag(s) {escalating_flags}"
                    ),
                )
            exempted_detail = (
                f" ({tuple(sorted(exempt_flags & set(always_flags)))} exempted "
                f"for this path)"
                if always_flags
                else ""
            )
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb=verb,
                cli_family=family,
                confidence=confidence,
                reason=f"Read-only verb '{verb}'{exempted_detail}",
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


# The memoization lives on the implementation, but callers -- tests that mutate
# module tables between assertions, above all -- reach for it through the public
# name.  Forwarding keeps that one cache the thing a caller clears; caching the
# wrapper separately would leave a second, stale one behind it.
detect_mutative_command.cache_clear = _detect_mutative_command.cache_clear
detect_mutative_command.cache_info = _detect_mutative_command.cache_info


# ============================================================================
# Helpers
# ============================================================================

def _extract_python_payload(command: str, base_cmd: str) -> str:
    """Return the Python source the interpreter runs: its ``-c`` word as the shell passes it, else a ``-`` heredoc body.

    A ``-c`` program wins over a heredoc, which is then only its stdin, and a
    ``"$(cat <<'D' ... D)"`` word is read as the lines it prints.  Empty string
    is returned when extraction fails.
    """
    from .gaia_cli_invocation import resolve_cat_heredocs

    payload = _inline_code_text(resolve_cat_heredocs(command, keep_newlines=True), base_cmd, "")
    if payload:
        return payload
    if "<<" in command:
        m = _re.search(
            r"<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*$",
            command, _re.DOTALL,
        )
        if m:
            return m.group(2)
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

    # Rewrite ``python3 [flags] -m <pkg-mgr> <rest...>`` -> ``<pkg-mgr> <rest...>``
    # and re-classify.  ``shlex.quote`` keeps argument boundaries intact so a
    # rewritten command tokenizes the same way the direct CLI form would.
    import shlex
    rest = raw_tokens[module_idx + 1:]
    rewritten = " ".join(shlex.quote(t) for t in (module, *rest))
    inner = detect_mutative_command(rewritten)

    # Beyond the package managers, ANY module invoked through ``-m`` is the CLI
    # of that module (``python3 -m twine upload``, ``python3 -m alembic upgrade
    # head``), and the ``-m`` spelling must not classify lower than the direct
    # one.  But a module is NOT a resolvable file here -- widening the rewrite
    # unconditionally would also relabel every benign ``python3 -m pytest`` /
    # ``-m http.server``, replacing their ordinary classification with a
    # re-dispatched one for no security gain.  So the widening is ESCALATE-ONLY,
    # the same false-positive gate ``_scan_exec_sink_string_args`` applies to an
    # extracted inner command: adopt the rewrite when it is mutative, otherwise
    # return None and let ordinary detection classify the command untouched.
    if module.lower() not in _PY_MODULE_PACKAGE_MANAGERS and not inner.is_mutative:
        return None
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


def _resolve_prefix_runner_payload(
    base_cmd: str, semantics: "CommandSemantics",
) -> "Optional[Tuple[Optional[str], str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], bool]]":
    """Return ``(subcommand, wrapped_command_token, remaining_args, packages,
    extra_packages, unknown_option)``.

    Walks the runner's own options -- skipping boolean flags, consuming the
    value of a ``value_flags`` option, and accepting the self-contained
    ``--flag=value`` form -- until the first true positional, which is the
    command the runner will execute.  For a runner that requires a subcommand
    (``uv run``) the subcommand must appear before that positional, otherwise
    this is a DIFFERENT operation of the same CLI (``uv pip install``, ``poetry
    add``) that ordinary verb detection already owns.

    ``packages`` are the package specs the runner has to find or fetch: the
    values of its package options, or else the wrapped command itself;
    ``extra_packages`` are the subset added beside it (``--with``).
    ``unknown_option`` is True when an option outside both runner flag tables
    came first; a positional after it that is not the subcommand is skipped as
    its possible value.

    Returns ``None`` whenever the command is not a runner invocation with a
    resolvable payload, so the caller leaves classification unchanged.
    """
    if base_cmd not in _PREFIX_RUNNER_SUBCOMMANDS:
        return None

    required_subcommands = _PREFIX_RUNNER_SUBCOMMANDS[base_cmd]
    value_flags = _PREFIX_RUNNER_VALUE_FLAGS.get(base_cmd, frozenset())
    raw_tokens = semantics.tokens
    ecosystem = "python" if base_cmd in ("uv", "uvx", "pipx", "poetry") else "node"
    source_flags, extra_flags = _RUNNER_PACKAGE_FLAGS[ecosystem]
    boolean_flags = _PREFIX_RUNNER_BOOLEAN_FLAGS.get(base_cmd, frozenset()) | _NPX_SHELL_CALL_FLAGS
    subcommand: "Optional[str]" = None
    unknown_option = False
    sources: List[str] = []
    extras: List[str] = []

    def option_value(index: int) -> "Optional[str]":
        _, sep, inline = raw_tokens[index].partition("=")
        if sep:
            return inline
        return raw_tokens[index + 1] if index + 1 < len(raw_tokens) else None

    subcommand_seen = required_subcommands is None
    i = 1
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if token == "--":
            i += 1
            continue
        if token.startswith("-") and token != "-":
            flag = token.split("=", 1)[0]
            if subcommand_seen and flag in source_flags | extra_flags:
                value = option_value(i)
                if value is not None:
                    (sources if flag in source_flags else extras).append(value)
            if subcommand_seen and base_cmd in ("npx", "npm") and token in _NPX_SHELL_CALL_FLAGS:
                # ``npx --call "<shell command>"``: the value is a command
                # string, not a package -- hand it back as the payload with no
                # remaining args so the caller re-classifies it as a command.
                if i + 1 < len(raw_tokens):
                    return (
                        subcommand, raw_tokens[i + 1], (), tuple(sources + extras),
                        tuple(extras), unknown_option,
                    )
                return None
            if flag not in value_flags and flag not in boolean_flags:
                unknown_option = True
            if token in value_flags:
                i += 2
                continue
            i += 1
            continue
        if not subcommand_seen:
            if token.lower() in required_subcommands:
                subcommand = token.lower()
                subcommand_seen = True
            elif not unknown_option:
                return None
            # Otherwise the token may be an unknown option's value: keep looking.
            i += 1
            continue
        packages = tuple((sources or [token]) + extras)
        return (
            subcommand, token, tuple(raw_tokens[i + 1:]), packages, tuple(extras),
            unknown_option,
        )

    return None


def _is_bare_node_package(spec: str) -> bool:
    """False when *spec* pins a version, range or URL -- what then runs is not
    provably the local copy."""
    pattern = r"(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*"
    return _re.fullmatch(pattern, spec, _re.IGNORECASE) is not None


def _runner_package_is_local(origin: str, spec: str, project_dir: str) -> bool:
    """True when *spec* is a local path, or -- for an "installed" runner -- a
    bare name installed in the project's node_modules (as a bin or a package).

    Only an explicit path prefix is local: npm reads ``user/repo`` as a GitHub
    fetch."""
    import os

    if spec.startswith(("./", "../", "/", "~/", "file:")):
        return True
    if origin != "installed" or not _is_bare_node_package(spec):
        return False
    modules = os.path.join(project_dir, "node_modules")
    return os.path.isfile(os.path.join(modules, ".bin", spec)) or os.path.isdir(
        os.path.join(modules, spec)
    )


def _installed_runner_fetch_reason(
    base_cmd: str, tokens: "Tuple[str, ...]", payload_index: int,
    cwd: "Optional[str]",
) -> "Optional[str]":
    """Why an "installed" runner may run code that is not the project's copy, or
    ``None`` when the invocation fits the one shape that provably runs it.

    That shape: every runner option is in ``_INSTALLED_RUNNER_SAFE_FLAGS`` or is
    the runner's directory option (the last value wins, as npm reads it), and
    the payload is a path or installed under that directory.  ``npm exec``/``x``
    read their options up to ``--`` even after the package (npm-exec(1)); the
    other runners hand everything after the package to the command.
    """
    import os

    end = len(tokens) if base_cmd == "npm" else payload_index
    if "--" in tokens[1:end]:
        end = tokens.index("--", 1)
    runner_tokens = tokens[1:end]
    dir_flags = (
        frozenset({"--prefix"}) if base_cmd == "npx"
        else _PACKAGE_MANAGER_DIR_FLAGS.get(base_cmd, frozenset())
    )
    safe_flags = _INSTALLED_RUNNER_SAFE_FLAGS.get(base_cmd, frozenset())
    shell_call = False
    takes_value = False
    for token in runner_tokens:
        if takes_value or not token.startswith("-"):
            takes_value = False
            continue
        flag, sep, _ = token.partition("=")
        if base_cmd in ("npx", "npm") and flag in _NPX_SHELL_CALL_FLAGS:
            shell_call = True
            takes_value = not sep
        elif flag in dir_flags:
            takes_value = not sep
        elif sep or flag not in safe_flags:
            return f"option '{flag}' is outside the shape known to run the project's copy"

    project_dir = cwd if cwd is not None else os.getcwd()
    if any(t.partition("=")[0] in dir_flags for t in runner_tokens):
        override = _extract_dir_override(runner_tokens, dir_flags)
        if override is None:
            return "its directory option has no value"
        project_dir = _resolve_dir_against_cwd(project_dir, override)
    payload = tokens[payload_index]
    if shell_call or _runner_package_is_local("installed", payload, project_dir):
        return None
    return f"it would fetch {payload}: not a local path or installed under {project_dir}"


def _uv_run_fetch_reason(tokens: "Tuple[str, ...]", payload_index: int) -> "Optional[str]":
    """Why ``uv run`` may add code beyond the project's lockfile, or ``None`` when
    every option before the command is in ``_UV_RUN_SAFE_FLAGS`` or
    ``_UV_RUN_SAFE_VALUE_FLAGS`` and every comma-separated ``--with`` item is a
    local path."""
    i = 1
    while i < payload_index:
        token = tokens[i]
        if token == "--" or not token.startswith("-"):
            i += 1
            continue
        flag, sep, inline = token.partition("=")
        if flag in _UV_RUN_WITH_FLAGS or flag in _UV_RUN_SAFE_VALUE_FLAGS:
            value = inline if sep else (tokens[i + 1] if i + 1 < len(tokens) else "")
            i += 1 if sep else 2
            if flag in _UV_RUN_WITH_FLAGS:
                fetched = [
                    item for item in value.split(",")
                    if not _runner_package_is_local("project", item, "")
                ]
                if fetched:
                    return f"--with would fetch {', '.join(fetched)} from a registry"
            continue
        if sep or flag not in _UV_RUN_SAFE_FLAGS:
            return f"option '{flag}' is outside the shape known to add no code"
        i += 1
    return None


_PEP723_SCRIPT_BLOCK_RE = _re.compile(r"^# /// script[ \t]*$(.*?)^# ///[ \t]*$", _re.M | _re.S)


_PEP723_DEPENDENCIES_RE = _re.compile(r"""^#\s*(["']?)dependencies\1\s*=""", _re.M)


def _script_declares_inline_dependencies(path: str, cwd: "Optional[str]") -> bool:
    """True when the script at *path* carries a PEP 723 ``# /// script`` block
    with ``dependencies``, which ``uv run`` fetches, or when the read stopped at
    its size cap before a complete block; False when it has none or cannot be
    read."""
    content = _read_script_content(path, cwd)
    if content is None:
        return False
    block = _PEP723_SCRIPT_BLOCK_RE.search(content)
    if block is None:
        return len(content) >= _MAX_SCRIPT_READ_BYTES
    return _PEP723_DEPENDENCIES_RE.search(block.group(1)) is not None


def _uv_tool_run_as_uvx(tokens: "Tuple[str, ...]") -> "Optional[str]":
    """``uv tool run ...`` rewritten as the ``uvx ...`` it is an alias of, or
    ``None``.  ``tool run`` is matched wherever it appears: the rewrite can only
    sign, since ``uvx`` signs every package that is not a path."""
    import shlex

    for i in range(1, len(tokens) - 1):
        if tokens[i] == "tool" and tokens[i + 1] == "run":
            uvx_tokens = ("uvx", *tokens[1:i], *tokens[i + 2:])
            return " ".join(shlex.quote(t) for t in uvx_tokens)
    return None


def _check_prefix_runner(
    base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify a prefix runner by its payload and by where its package comes from.

    A prefix runner's base token is not an interpreter, so the script-file lane
    never opens the wrapped script and the invocation classifies by the runner's
    own verb (``run``, which is deliberately absent from ``MUTATIVE_VERBS``) --
    i.e. T0 regardless of what the payload does.  This resolves the wrapped
    command and re-runs ``detect_mutative_command`` on it, so ``uv run x.py``
    classifies exactly as ``python3 x.py`` does, ``npx prisma migrate deploy``
    as ``prisma migrate deploy``, and the Python AST / JS lexer / shell regex
    lanes reach the payload through their existing entry point.

    When the payload is a script PATH its canonical interpreter is prepended
    (``_SCRIPT_EXT_INTERPRETERS``); otherwise the payload is itself a command
    and is re-classified as written.

    A runner listed in ``_RUNNER_CODE_ORIGIN`` whose payload is not itself
    mutative is unsigned only when the whole command fits the shape that
    provably runs the project's code (``_installed_runner_fetch_reason``,
    ``_uv_run_fetch_reason``); anything outside that shape is signed, rather
    than signed only when a known danger is found.  A "registry" runner is
    signed whenever a package it needs is not a path.  A spec that pins a
    version cannot be shown to be the local copy, so it counts as fetched.

    Fallback choice, stated explicitly because the reachable behavior and the
    documented one disagree: an UNRECOGNIZED runner still falls to T0, and this
    change narrows that class rather than closing it.  The conservative-T3
    default documented for "an unrecognized interpreter" is real only INSIDE
    ``_check_script_file`` -- once a token is known to be an interpreter, an
    un-inspectable payload cannot be proven safe and the population of such
    commands is small.  Inverting the DEFAULT instead (unknown base command, or
    any command carrying a script-shaped positional, -> T3) would put the entire
    long tail of ordinary tooling (``make``, ``cargo``, ``go``, ``just``,
    ``task``, every project-local wrapper) behind a consent prompt on every
    read-only invocation.  That trades a narrow false negative for a broad false
    positive that breaks legitimate work on every surface and trains blind
    approval, and it contradicts the module's stated model -- safe by
    elimination, never by an allow-list.  So T0 remains the fallback for an
    unrecognized runner, and the honest scope of this lane is: the named
    shapes are inspected; an unlisted runner, a project-local wrapper script, or an
    interpreter reached by a path whose basename is not a known interpreter name
    still passes as T0.

    Returns ``None`` when the command is not a resolvable runner invocation.
    """
    uvx_command = _uv_tool_run_as_uvx(semantics.tokens) if base_cmd == "uv" else None
    if uvx_command is not None:
        return detect_mutative_command(uvx_command, cwd=cwd, _depth=_depth + 1)

    resolved = _resolve_prefix_runner_payload(base_cmd, semantics)
    if resolved is None:
        return None

    # Budget exhausted: stop descending AND retain -- see the constant. Placed
    # AFTER the shape check on purpose: the retaining verdict may only reach a
    # command this lane has already recognized as a runner invocation, or every
    # unrelated command that happens to be classified at this depth would be
    # gated by a lane that has nothing to say about it.
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result("runner payload")

    subcommand, payload, rest, packages, _, unknown_option = resolved

    import shlex

    # ``uv run -`` reads a Python program from stdin.
    interpreter = "python3" if base_cmd == "uv" and payload == "-" else None
    lowered = payload.lower()
    for ext, ext_interpreter in _SCRIPT_EXT_INTERPRETERS.items():
        if lowered.endswith(ext):
            interpreter = ext_interpreter
            break

    tokens = (payload, *rest) if interpreter is None else (interpreter, payload, *rest)
    rewritten = " ".join(shlex.quote(t) for t in tokens)

    inner = detect_mutative_command(rewritten, cwd=cwd, _depth=_depth + 1)
    origin = _RUNNER_CODE_ORIGIN.get((base_cmd, subcommand))
    if not inner.is_mutative and origin is not None:
        payload_index = len(semantics.tokens) - len(rest) - 1
        if origin == "installed":
            fetch_reason = _installed_runner_fetch_reason(
                base_cmd, semantics.tokens, payload_index, cwd,
            )
        elif origin == "project":
            fetch_reason = _uv_run_fetch_reason(semantics.tokens, payload_index)
            if fetch_reason is None and lowered.endswith(".py") and (
                _script_declares_inline_dependencies(payload, cwd)
            ):
                fetch_reason = f"{payload} declares inline dependencies (PEP 723)"
        elif unknown_option:
            fetch_reason = "an option outside its known tables hides which package it fetches"
        else:
            fetched = [s for s in packages if not _runner_package_is_local(origin, s, "")]
            fetch_reason = f"it would fetch {', '.join(fetched)}" if fetched else None
        if fetch_reason is not None:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="registry-fetch",
                cli_family=family,
                confidence="medium",
                reason=f"Runner '{base_cmd}' is signed: {fetch_reason}",
            )
    return MutativeResult(
        is_mutative=inner.is_mutative,
        category=inner.category,
        verb=inner.verb,
        dangerous_flags=inner.dangerous_flags,
        cli_family=inner.cli_family,
        confidence=inner.confidence,
        reason=(
            f"Runner '{base_cmd}' re-dispatched as '{rewritten}': {inner.reason}"
        ),
    )


# Body markers that together identify bin/gaia (the unified Gaia CLI dispatcher)
# with near-zero false-positive risk. BOTH must appear in the file for the
# re-dispatch to fire, so an unrelated user script named ``bin/gaia`` is not
# matched.
_GAIA_DISPATCHER_SIGNATURE: Tuple[str, ...] = ("Unified Gaia CLI", "_discover_plugins")


_HOOK_ROOT = Path(__file__).resolve().parents[3]


@functools.lru_cache(maxsize=1)
def _hook_root_is_install() -> bool:
    """True when the Gaia this hook runs from is an install, not the root of a git checkout anyone can edit; an npm install nested in a project's checkout still counts."""
    top = _git_read(str(_HOOK_ROOT), "rev-parse", "--show-toplevel")
    return top is None or os.path.realpath(top) != os.path.realpath(_HOOK_ROOT)


def _gaia_shipped_script(script: Path) -> bool:
    """True when *script* is a file of the installed Gaia this hook runs from; a checkout's copy, Gaia's own source included, is judged by content."""
    real = Path(os.path.realpath(script))
    return real.is_file() and real.is_relative_to(_HOOK_ROOT) and _hook_root_is_install()


def _gaia_cli_group(script: Path) -> "Optional[str]":
    """The group a Gaia-shipped ``bin/cli/<group>.py`` runs as, or None."""
    from .gaia_cli_invocation import _CLI_MODULES

    real = Path(os.path.realpath(script))
    if real.suffix != ".py" or real.stem.startswith("_") or real.stem not in _CLI_MODULES:
        return None
    if real.parent.name != "cli" or real.parent.parent.name != "bin":
        return None
    return real.stem if _gaia_shipped_script(real) else None


def _check_gaia_cli_dispatcher(
    script_path: str, content: str, semantics: "CommandSemantics", family: str,
    _depth: int = 0, cwd: "Optional[str]" = None,
) -> "Optional[MutativeResult]":
    """Re-dispatch ``<python> <path>/bin/gaia <subcmd> ...`` as ``gaia <subcmd> ...``.

    bin/gaia is the unified Gaia CLI dispatcher; its own body calls
    ``subprocess.run(...)`` for the lazy DB bootstrap. Python AST analysis of that
    body (the ``lane == "python"`` branch in ``_check_script_file``) flags the
    subprocess call as mutative, so EVERY ``python3 <path>/bin/gaia <subcmd>``
    invocation would classify T3 -- including read-only subcommands (``doctor``,
    ``release check``, ``--dry-run`` flows). The real effect is determined
    entirely by the SUBCOMMAND, exactly as the installed launcher form
    ``gaia <subcmd>`` is classified. So when the script is recognized as the gaia
    dispatcher (basename ``gaia`` + parent dir ``bin`` + a body signature, to
    avoid matching an unrelated ``bin/gaia``), reconstruct the equivalent
    ``gaia <args...>`` command from the tokens AFTER the script positional and
    re-run ``detect_mutative_command`` on it. The result is IDENTICAL to the
    launcher form: ``dev`` stays T3 via ``COMMAND_PATH_MUTATIVE_UPGRADES``,
    ``install`` stays T3 via ``MUTATIVE_VERBS``, ``--dry-run`` / ``--help`` stay
    non-mutative, and read-only subcommands (``doctor``, ``release check``) stay T0.

    This mirrors the ``_check_python_module_runner`` re-dispatch precedent and is
    narrowly scoped to the gaia dispatcher; it is NOT a general bypass of
    subprocess.run detection for arbitrary Python scripts.

    A ``bin/cli/<group>.py`` script runs as ``gaia <group>`` and is
    re-dispatched the same way.  Either script counts only inside the installed
    Gaia this hook runs from (``_gaia_shipped_script``): a git checkout's copy,
    Gaia's own source worktrees included, can import code anyone edited, so it
    is judged by its content.

    Returns ``None`` when the script is not the gaia dispatcher, so the caller
    continues with ordinary AST/regex script analysis.
    """
    import os

    script = Path(cwd or Path.cwd(), Path(script_path).expanduser())
    if os.path.basename(script_path) == "gaia" and os.path.basename(os.path.dirname(script_path)) == "bin":
        if not all(sig in content for sig in _GAIA_DISPATCHER_SIGNATURE) or not _gaia_shipped_script(script):
            return None
        group: Tuple[str, ...] = ()
    elif (cli_group := _gaia_cli_group(script)) is not None:
        group = (cli_group,)
    else:
        return None

    # Tokens AFTER the script positional are the gaia argv.
    raw_tokens = semantics.tokens
    try:
        idx = raw_tokens.index(script_path)
    except ValueError:
        return None
    sub_args = [*group, *raw_tokens[idx + 1:]]

    if not sub_args:
        # `python3 bin/gaia` with no subcommand prints help -- read-only.
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb="gaia-cli",
            cli_family=family,
            confidence="high",
            reason=(
                f"gaia CLI dispatcher '{script_path}' invoked with no subcommand "
                f"(prints help, read-only)"
            ),
        )

    import shlex

    rewritten = " ".join(shlex.quote(t) for t in ("gaia", *sub_args))
    inner = detect_mutative_command(rewritten, _depth=_depth + 1)
    return MutativeResult(
        is_mutative=inner.is_mutative,
        category=inner.category,
        verb=inner.verb,
        dangerous_flags=inner.dangerous_flags,
        cli_family=inner.cli_family,
        confidence=inner.confidence,
        reason=(
            f"gaia CLI dispatcher '{script_path}' re-dispatched as "
            f"'gaia {' '.join(sub_args)}': {inner.reason}"
        ),
    )


# Operators that establish a SEQUENTIAL cwd hand-off from a leading `cd`.
# ``&&`` and ``;`` (and a newline) run the next clause in the cwd the ``cd``
# just set.  ``||`` is deliberately excluded -- its right side runs only when
# the ``cd`` FAILED, so the target dir is not in effect.  ``|`` / ``&`` are not
# sequential cwd hand-offs either.
_LEADING_CD_SPLIT_RE = _re.compile(r"&&|;|\n")
_CD_FAMILY_RE = _re.compile(r"\s*(?:cd|pushd|popd)(?=\s|$)")
_CD_PLAIN_FLAGS = frozenset({"-P", "-L", "-e", "-@"})

# The cwd after a `cd -`, `popd` or other move the fold cannot follow: a path no
# relative target can be placed under, so a consumer fails closed rather than guess.
UNKNOWN_CWD = "/nonexistent/gaia-unknown-cwd"


def _parse_cd_target(segment: str) -> "Optional[str]":
    """Return the single directory argument of a bare ``cd <dir>`` clause.

    Returns ``None`` when *segment* is not a plain ``cd`` to exactly one
    positional directory (``cd`` with no arg, ``cd -``, or ``cd`` with flags /
    multiple args are not peeled).  The returned token preserves ORIGINAL case
    -- ``analyze_command`` lowercases ``non_flag_tokens``, so the raw
    ``semantics.tokens`` slice is used instead, which matters on case-sensitive
    filesystems.
    """
    sem = analyze_command(segment)
    if sem.base_cmd == "popd" and len(sem.tokens) == 1:
        return "-"
    if sem.base_cmd not in ("cd", "pushd"):
        return None
    args = [t for t in sem.tokens[1:] if t not in _CD_PLAIN_FLAGS]
    if len(args) != 1 or (args[0][0] in "-+" and args[0] != "-"):
        return None
    return args[0]


def _resolve_dir_against_cwd(base_cwd: "Optional[str]", target: str) -> str:
    """Resolve *target* (a ``cd``/``--prefix``/``-C`` directory argument)
    against *base_cwd*, honoring ``~`` and absolute paths.

    Shared normalization for every cwd-fixing form Gaia recognizes: an
    absolute (or ``~``-expanded) target REPLACES the running cwd; a relative
    one is joined onto it.  ``base_cwd=None`` falls back to the process cwd,
    matching the hook's previous default.
    """
    import os
    from .sensitive_paths import expand_home

    if target == "-":
        return UNKNOWN_CWD
    base = base_cwd if base_cwd is not None else os.getcwd()
    target = os.path.expanduser(expand_home(target))
    if os.path.isabs(target):
        return os.path.normpath(target)
    if base == UNKNOWN_CWD:
        return UNKNOWN_CWD
    return os.path.normpath(os.path.join(base, target))


def _peel_leading_cd(
    command: str, base_cwd: "Optional[str]",
) -> "Tuple[Optional[str], str, bool]":
    """Peel leading ``cd <dir>`` navigation from a chain and fold the cwd.

    Gaia governs arbitrary workspaces, so a relative script token behind a
    ``cd`` (``cd /repo && node build.mjs``) must resolve against the ``cd``
    TARGET, not the hook's own cwd.  This walks the leading ``cd`` clauses of a
    ``&&`` / ``;`` chain, resolving each target against the running cwd
    (relative targets join the accumulated cwd; absolute / ``~`` targets replace
    it), and returns ``(effective_cwd, remaining_command, peeled)``:

    * ``effective_cwd`` -- the cwd in effect after the peeled ``cd`` clauses
      (``base_cwd`` unchanged when nothing was peeled).
    * ``remaining_command`` -- the chain from the first NON-``cd`` clause on.
    * ``peeled`` -- whether any ``cd`` clause was consumed.

    A malformed / bogus target simply yields an unreadable path downstream,
    which the conservative T3 fallback still catches -- security is not weakened.
    """
    cwd = base_cwd
    remaining = command.strip() if command else ""
    peeled = False
    while remaining:
        parts = _LEADING_CD_SPLIT_RE.split(remaining, maxsplit=1)
        first = parts[0].strip()
        rest = parts[1].strip() if len(parts) == 2 else ""
        target = _parse_cd_target(first)
        if target is None:
            break
        cwd = _resolve_dir_against_cwd(cwd, target)
        peeled = True
        remaining = rest
    return cwd, remaining, peeled


def cwd_after_component(command: str, base_cwd: "Optional[str]") -> "Optional[str]":
    """Return the cwd in effect AFTER running *command*, given *base_cwd* before.

    A leading/standalone ``cd`` clause (``cd X``, ``cd X && ...``) persists the
    target as the cwd for the NEXT chain component; any other command leaves it
    unchanged.  Used by the compound-command validator to fold cwd across the
    independently-classified components of a ``cd X && node rel.mjs`` chain,
    where the ``cd`` and the script land in SEPARATE components.
    """
    cwd, _remaining, peeled = _peel_leading_cd(command, base_cwd)
    if peeled:
        return cwd
    return UNKNOWN_CWD if _CD_FAMILY_RE.match(command) else base_cwd


# A leading shell env-var assignment:  NAME=  (value consumed separately, so it
# may be empty, a bare word, a quoted string, or a $(...)/`...` substitution).
# Anchored at the scan cursor -- a `=` inside a LATER argument is never matched
# because the cursor only ever sits at the start of the command or immediately
# after a fully-consumed prior assignment / peeled `env` token.
_ENV_ASSIGN_PREFIX_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

@dataclass(frozen=True)
class _WrapperGrammar:
    """How a transparent wrapper's own arguments end and the wrapped command begins.

    ``value_flags`` consume the following word as their argument; any other
    flag -- boolean, clustered (``-oL``, ``-c3``) or ``--long=value`` -- is one
    word. ``positionals`` counts the wrapper's own operands before the wrapped
    command (``timeout``'s duration). A Windows wrapper takes ``/x`` switches,
    matched in any case; one of its ``terminators`` (``cmd /c``) ends its
    arguments, and ``window_title`` lets a leading quoted word be the title
    ``start`` gives the window rather than the command.
    """

    value_flags: FrozenSet[str] = frozenset()
    positionals: int = 0
    windows_switches: bool = False
    terminators: FrozenSet[str] = frozenset()
    window_title: bool = False
    lookup_flags: FrozenSet[str] = frozenset()
    split_string_flags: FrozenSet[str] = frozenset()


# Wrappers that run the command after them with the same effect it has alone.
# ONE table feeds both the permanent-block floor and the T3 detector (through
# _peel_leading_command_wrappers), so a wrapper recognised by one and not the
# other cannot reopen a floor command the other still blocks.
_COMMAND_WRAPPERS: Dict[str, _WrapperGrammar] = {
    "env": _WrapperGrammar(
        value_flags=frozenset({
            "-u", "-C", "--unset", "--chdir",
            "--block-signal", "--default-signal", "--ignore-signal",
        }),
        split_string_flags=frozenset({"-S", "--split-string"}),
    ),
    "command": _WrapperGrammar(lookup_flags=frozenset({"-v", "-V"})),
    "builtin": _WrapperGrammar(),
    "exec": _WrapperGrammar(value_flags=frozenset({"-a"})),
    "busybox": _WrapperGrammar(),
    "setsid": _WrapperGrammar(),
    "chronic": _WrapperGrammar(),
    "unbuffer": _WrapperGrammar(),
    "pkexec": _WrapperGrammar(value_flags=frozenset({"--user"})),
    "chroot": _WrapperGrammar(value_flags=frozenset({"--userspec", "--groups"}), positionals=1),
    "taskset": _WrapperGrammar(positionals=1),
    "chrt": _WrapperGrammar(value_flags=frozenset({"-T", "-P", "-D"}), positionals=1),
    "numactl": _WrapperGrammar(value_flags=frozenset({
        "-N", "-m", "-C", "-p", "-i", "-P", "--cpunodebind", "--membind",
        "--physcpubind", "--preferred", "--interleave",
    })),
    "prlimit": _WrapperGrammar(value_flags=frozenset({"-p", "--pid"})),
    "unshare": _WrapperGrammar(value_flags=frozenset({"-S", "-G", "-R", "-w"})),
    "nsenter": _WrapperGrammar(value_flags=frozenset({
        "-t", "-S", "-G", "-r", "-w", "--target", "--setuid", "--setgid",
    })),
    "strace": _WrapperGrammar(value_flags=frozenset({
        "-e", "-o", "-p", "-s", "-u", "-E", "-a", "-b", "-I", "-O", "-P", "-S", "-X",
    })),
    "ltrace": _WrapperGrammar(value_flags=frozenset({
        "-e", "-o", "-p", "-s", "-u", "-a", "-A", "-D", "-F", "-l", "-n", "-x", "-w",
    })),
    "sudo": _WrapperGrammar(value_flags=frozenset({
        "-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "-R",
        "--user", "--group", "--host", "--prompt", "--close-from",
        "--chdir", "--role", "--type", "--other-user", "--command-timeout",
        "--chroot",
    })),
    "doas": _WrapperGrammar(value_flags=frozenset({"-u", "-C"})),
    "timeout": _WrapperGrammar(
        value_flags=frozenset({"-s", "-k", "--signal", "--kill-after"}),
        positionals=1,
    ),
    "nice": _WrapperGrammar(value_flags=frozenset({"-n", "--adjustment"})),
    "ionice": _WrapperGrammar(value_flags=frozenset({
        "-c", "-n", "--class", "--classdata",
    })),
    "nohup": _WrapperGrammar(),
    "stdbuf": _WrapperGrammar(value_flags=frozenset({
        "-i", "-o", "-e", "--input", "--output", "--error",
    })),
    "time": _WrapperGrammar(value_flags=frozenset({
        "-f", "-o", "--format", "--output",
    })),
    "cmd": _WrapperGrammar(windows_switches=True, terminators=frozenset({"/c", "/k", "/r"})),
    "start": _WrapperGrammar(
        windows_switches=True,
        value_flags=frozenset({"/d", "/node", "/affinity"}),
        window_title=True,
    ),
}

# A switch is one word: ``/b`` or ``/e:on``. A path such as ``/usr/bin/rm``
# carries a second slash and is the wrapped command, not a switch.
_WINDOWS_SWITCH_RE = _re.compile(r"/[A-Za-z?]+(?::\S*)?")
_STRING_PAYLOAD_SWITCH_RE = _re.compile(r"(?:^|\s)/[ckr]\s*$", _re.IGNORECASE)
_PY_LAUNCHER_SELECTOR_RE = _re.compile(r"-(?:\d+(?:\.\d+)?(?:-(?:32|64|arm64))?|V:\S+)")


def _consume_shell_word(s: str, i: int) -> int:
    """Return the index just past the shell word beginning at ``s[i]``.

    Scans a single unquoted-whitespace-delimited word, but treats quotes and
    command substitution as opaque so their inner whitespace does NOT end the
    word.  This is what lets a ``NAME=$(gh auth token)`` assignment value be
    consumed whole instead of splitting at the spaces inside ``$( ... )``:

    * ``'...'``   -- single-quoted: verbatim until the closing quote.
    * ``"..."``   -- double-quoted: until the closing quote, honoring ``\\"``.
    * `` `...` ``  -- backtick substitution: until the closing backtick.
    * ``$( ... )`` -- command substitution: until the balanced closing paren.
    * ``\\x``      -- a backslash escapes the next character (incl. a space).

    An unterminated quote / substitution consumes to end-of-string, which keeps
    the peeler conservative: it never leaves a dangling half-assignment for the
    verb scanner to misread.
    """
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            break
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            i = n if j == -1 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n:
                if s[i] == "\\":
                    i += 2
                    continue
                if s[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "`":
            j = s.find("`", i + 1)
            i = n if j == -1 else j + 1
            continue
        if c == "$" and i + 1 < n and s[i + 1] == "(":
            depth = 0
            i += 1  # now at '('
            while i < n:
                if s[i] == "(":
                    depth += 1
                elif s[i] == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            continue
        i += 1
    return i


def _short_bundle_ends_in_value_flag(tok: str, value_flags: FrozenSet[str]) -> bool:
    """True when the bundled short flags in *tok* end in one that takes the next
    word as its value (``-iu HOME``).  A value flag earlier in the bundle takes
    the rest of the bundle instead (``-iuHOME``)."""
    if tok.startswith("--") or len(tok) < 3:
        return False
    for k in range(1, len(tok)):
        if f"-{tok[k]}" in value_flags:
            return k == len(tok) - 1
    return False


def _split_string_flag(tok: str, grammar: _WrapperGrammar) -> bool:
    if tok.startswith("--"):
        return tok.split("=", 1)[0] in grammar.split_string_flags
    for letter in tok[1:]:
        if f"-{letter}" in grammar.split_string_flags:
            return True
        if f"-{letter}" in grammar.value_flags:
            return False
    return False


def _skip_wrapper_arguments(s: str, i: int, grammar: _WrapperGrammar) -> "Optional[int]":
    """Return the index where the wrapped command starts, given ``s[i:]`` follows a wrapper name."""
    n = len(s)
    positionals = grammar.positionals
    title_allowed = grammar.window_title
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        tok_end = _consume_shell_word(s, i)
        tok = s[i:tok_end]
        if grammar.windows_switches:
            tok = tok.lower()
        if tok == "--" or tok in grammar.terminators:
            return tok_end
        if title_allowed:
            title_allowed = False
            if tok.startswith('"'):
                i = tok_end
                continue
        is_switch = (
            _WINDOWS_SWITCH_RE.fullmatch(tok) is not None
            if grammar.windows_switches
            else tok.startswith("-")
        )
        if is_switch:
            if tok in grammar.lookup_flags:
                return None
            if _split_string_flag(tok, grammar):
                return i
            i = tok_end
            if tok in grammar.value_flags or _short_bundle_ends_in_value_flag(
                tok, grammar.value_flags,
            ):
                while i < n and s[i].isspace():
                    i += 1
                i = _consume_shell_word(s, i)
            continue
        if positionals and not _ENV_ASSIGN_PREFIX_RE.match(tok):
            positionals -= 1
            i = tok_end
            continue
        break
    return i


def _skip_command_wrappers(s: str, i: int) -> int:
    """Return the index just past every leading transparent wrapper at ``s[i]``.

    Stops at the first assignment or wrapped-command token. Shared by the
    wrapper peeler and the assignment-substitution guard so both see the same
    prefix boundary -- if only the peeler skipped a wrapper, an
    ``env NAME=$(mutative) cmd`` form would have its substitution discarded
    without ever being classified.
    """
    return _wrapper_chain(s, i)[0]


def _wrapper_chain(s: str, i: int) -> "Tuple[int, Tuple[_WrapperGrammar, ...]]":
    """Return the index past the leading wrappers at ``s[i]`` and their grammars, outermost first."""
    n = len(s)
    chain: List[_WrapperGrammar] = []
    while True:
        while i < n and s[i].isspace():
            i += 1
        word_end = _consume_shell_word(s, i)
        grammar = _COMMAND_WRAPPERS.get(program_identity(s[i:word_end].strip("\"'")))
        wrapped_start = None if grammar is None else _skip_wrapper_arguments(s, word_end, grammar)
        if wrapped_start is None:
            return i, tuple(chain)
        chain.append(grammar)
        i = wrapped_start


def _peel_leading_command_wrappers(command: str) -> "Tuple[str, bool]":
    """Peel leading wrappers (``_COMMAND_WRAPPERS``) and ``NAME=value`` assignments off *command*.

    Returns ``(remainder, peeled)``, the remainder's head respelled by
    ``_name_program_head``. The wrapped command runs with the effect
    it has alone, but the token-0 scans land on the wrapper or the assignment
    and never see its verb. The block floor and the T3 detector both call this
    one function (DP1), so ``sudo``/``timeout``/``nice`` in front of a floor
    command cannot leave it at consent, nor a T3 command free.

    The assignment regex is anchored at the scan cursor, which only ever sits
    at the command start or just past a consumed wrapper or assignment, so a
    ``=`` inside a later argument (``echo A=B terragrunt apply``) is never
    taken for a leading assignment.
    """
    readings, peeled = _peel_wrapper_readings(command)
    return readings[0], peeled


def _peel_wrapper_readings(command: str) -> "Tuple[Tuple[str, ...], bool]":
    """Return ``(readings, peeled)``: every command the wrapped remainder may run, the remainder first.

    A quoted ``cmd /c`` payload is one word to the shell and a chain to cmd,
    so each of its stages (``shell_parser.parse_command``) is a reading. An
    unquoted first word after ``start`` is the program to Windows but reads
    as a title to anyone expecting ``start "title"``; the words after it are
    a reading too, so either spelling classifies at the stricter verdict.
    """
    dropped, s, i, innermost = _expand_split_strings(command)
    expanded = bool(dropped)
    remainder = s[i:].strip()
    readings = [remainder]
    if innermost is not None and innermost.terminators and _STRING_PAYLOAD_SWITCH_RE.search(s[:i]):
        payload = _unquote_single_word(remainder)
        from ..tools.shell_parser import parse_command

        stages = parse_command(payload)
        readings = [payload, *stages] if len(stages) > 1 else [payload]
    elif innermost is not None and innermost.window_title:
        words_end = _consume_shell_word(remainder, 0)
        after_title = remainder[words_end:].strip()
        if after_title:
            readings.append(after_title)
    named = [_name_program_head(reading) for reading in readings]
    renamed = any(was_renamed for _, was_renamed in named)
    named_readings = [reading for reading, _ in named]
    config_programs = _git_config_programs(named_readings[0])
    return (
        tuple(named_readings + config_programs),
        i > 0 or expanded or renamed or bool(config_programs),
    )


def _expand_split_strings(command: str) -> "Tuple[str, str, int, Optional[_WrapperGrammar]]":
    """``(dropped, s, i, innermost)``: *command* as ``s`` with every ``env -S`` string split into its words, the prefixes that rewrite replaced, and where ``s``'s peeled remainder starts."""
    s = command.strip() if command else ""
    dropped: List[str] = []
    while True:
        i, innermost = _peel_prefix(s)
        split = _expand_split_string(s[i:], innermost)
        if split is None:
            return " ".join(dropped), s, i, innermost
        dropped.append(s[:i])
        s = split


def _peel_prefix(s: str) -> "Tuple[int, Optional[_WrapperGrammar]]":
    n = len(s)
    i = 0
    innermost: "Optional[_WrapperGrammar]" = None
    while True:
        start = i
        i, chain = _wrapper_chain(s, i)
        if chain:
            innermost = chain[-1]
        while True:
            while i < n and s[i].isspace():
                i += 1
            m = _ENV_ASSIGN_PREFIX_RE.match(s, i)
            if not m:
                break
            i = _consume_shell_word(s, m.end())
        if i == start:
            return i, innermost


def _expand_split_string(rest: str, grammar: "Optional[_WrapperGrammar]") -> "Optional[str]":
    """Rewrite ``env -S STRING ARGS`` as ``env STRING ARGS``: env reads the split words as its own arguments."""
    if grammar is None or not grammar.split_string_flags:
        return None
    tok_end = _consume_shell_word(rest, 0)
    tok = rest[:tok_end]
    if not tok.startswith("-") or not _split_string_flag(tok, grammar):
        return None
    if tok.startswith("--"):
        value = tok.split("=", 1)[1] if "=" in tok else None
    else:
        attached = tok[tok.index("S") + 1:]
        value = attached or None
    if value is None:
        value_start = len(rest) - len(rest[tok_end:].lstrip())
        tok_end = _consume_shell_word(rest, value_start)
        value = rest[value_start:tok_end]
    try:
        words = shlex.split(value)
    except ValueError:
        words = [value]
    return f"env {' '.join(words)} {rest[tok_end:].strip()}".strip()


_GIT_CODE_PATH_KEY_RE = _re.compile(r"core\.hookspath|core\.fsmonitor|include\.path|includeif\..+\.path")
_GIT_PROGRAM_KEY_RE = _re.compile(
    r"core\.(?:pager|editor|sshcommand|askpass|gitproxy)|sequence\.editor|diff\.external"
    r"|gpg\.(?:[^.]+\.)?program|credential\.(?:.+\.)?helper|pager\..+|alias\..+"
    r"|(?:filter|diff|merge|difftool|mergetool|browser|man)\..+\."
    r"(?:clean|smudge|process|textconv|command|driver|cmd|path)"
    r"|uploadpack\.packobjectshook|remote\..+\.(?:uploadpack|receivepack)|web\.browser"
)
_GIT_HARMLESS_CODE_VALUES = frozenset({
    "/dev/null", "true", "false", "yes", "no", "on", "off", "1", "0", "",
    "$pager", "${pager}", "$git_pager", "$editor", "${editor}", "$visual", "$git_editor",
})
_GIT_VALUE_OPTIONS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix",
})
_GIT_CONFIG_ENV_RE = _re.compile(
    r"(?<!\S)(?:GIT_CONFIG_PARAMETERS|GIT_CONFIG_KEY_\d+|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG)="
    r"(\S*)"
)


def _git_global_options(command: str) -> "Optional[List[Tuple[str, str]]]":
    """Return git's global options in *command* as ``(option, value)`` pairs, or None when it is not git."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or program_identity(words[0]) != "git":
        return None
    options: List[Tuple[str, str]] = []
    index = 1
    while index < len(words) and words[index].startswith("-"):
        option, has_value, value = words[index].partition("=")
        if not has_value and option in _GIT_VALUE_OPTIONS and index + 1 < len(words):
            index += 1
            value = words[index]
        options.append((option, value))
        index += 1
    return options


def _git_config_programs(command: str) -> "List[str]":
    """Return the commands git runs from program-valued ``-c`` keys of *command*."""
    programs = []
    for option, value in _git_global_options(command) or ():
        key, has_value, program = value.partition("=")
        if option != "-c" or not has_value or not _GIT_PROGRAM_KEY_RE.fullmatch(key.lower()):
            continue
        if program.lower() in _GIT_HARMLESS_CODE_VALUES:
            continue
        if key.lower().startswith("alias.") and not program.startswith("!"):
            programs.append(f"git {program}")
        else:
            programs.append(program.lstrip("!"))
    return programs


def _check_git_code_config(command: str) -> "Optional[MutativeResult]":
    """Sign git run with configuration that points it at code the command line does not show."""
    readings, _ = _peel_wrapper_readings(command)
    options = _git_global_options(readings[0])
    if options is None:
        return None
    reasons = _git_config_env_reasons(command)
    for option, value in options:
        key, _, setting = value.partition("=")
        key = key.lower()
        if option == "-c" and _GIT_CODE_PATH_KEY_RE.fullmatch(key) and setting.lower() not in _GIT_HARMLESS_CODE_VALUES:
            reasons.append(f"-c {key}")
        elif option == "--config-env" and (_GIT_CODE_PATH_KEY_RE.fullmatch(key) or _GIT_PROGRAM_KEY_RE.fullmatch(key)):
            reasons.append(f"--config-env {key}")
        elif option == "--exec-path" and value:
            reasons.append("--exec-path")
    return _git_config_code_result(reasons) if reasons else None


def _git_config_env_reasons(command: str) -> "List[str]":
    reasons = []
    for match in _GIT_CONFIG_ENV_RE.finditer(command):
        name, value = match.group(0).split("=", 1)[0], match.group(1).strip("\"'").lower()
        if name.startswith("GIT_CONFIG_KEY_"):
            if _GIT_CODE_PATH_KEY_RE.fullmatch(value) or _GIT_PROGRAM_KEY_RE.fullmatch(value):
                reasons.append(f"{name}={value}")
        elif value != "/dev/null":
            reasons.append(name)
    return reasons


def injects_git_config(command: str) -> bool:
    """True when *command* sets a ``GIT_CONFIG_*`` variable that can point git at code."""
    return bool(_git_config_env_reasons(command))


def git_under_injected_config(command: str) -> "Optional[MutativeResult]":
    """Sign *command* when it runs git after an earlier command of its chain injected git configuration."""
    if _git_global_options(_peel_wrapper_readings(command)[0][0]) is None:
        return None
    return _git_config_code_result(["GIT_CONFIG_* set earlier in the chain"])


def _git_config_code_result(reasons: "List[str]") -> MutativeResult:
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="git-config-code",
        cli_family="git",
        confidence="high",
        reason=f"git runs code its configuration points at ({', '.join(reasons)})",
    )


def _unquote_single_word(command: str) -> str:
    """Return the text of *command* when it is one quoted word (``cmd /c "del x"``)."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    return words[0] if len(words) == 1 and words[0] != command else command


def _name_program_head(command: str) -> "Tuple[str, bool]":
    """Respell the head of *command* as the program Windows starts for it.

    Returns ``(command, renamed)``. Only a head Windows resolves differently
    from POSIX is respelled -- a backslash path, a PATHEXT suffix, the ``py``
    launcher -- so a ``./script`` path and a PascalCase cmdlet keep the
    spelling their lanes key on. ``py``'s version selector (``-3``,
    ``-3.12-64``, ``-V:3.12``) picks an interpreter and is dropped with it.
    """
    head_end = _consume_shell_word(command, 0)
    word = command[:head_end].strip("\"'")
    program = program_identity(word)
    if "\\" not in word and program == word.rsplit("/", 1)[-1].lower():
        return command, False
    if is_launcher(word):
        selector_start = len(command) - len(command[head_end:].lstrip())
        selector_end = _consume_shell_word(command, selector_start)
        if _PY_LAUNCHER_SELECTOR_RE.fullmatch(command[selector_start:selector_end]):
            head_end = selector_end
    return program + command[head_end:], True


_PACKAGE_CONFIG_ENV_RE = _re.compile(
    r"(?<!\S)(?:(?i:npm_config_)|UV_|PIP_|PNPM_|YARN_|BUN_)[A-Za-z0-9_]*="
)
_PACKAGE_TOOL_COMMANDS: FrozenSet[str] = frozenset({
    "npm", "npx", "pnpm", "pnpx", "yarn", "bun", "bunx", "uv", "uvx", "pip",
    "pip3", "pipx",
})


def _check_package_config_environment(
    command: str, remainder: str,
) -> "Optional[MutativeResult]":
    """Sign a package tool run under a package-manager configuration variable.

    Such a variable can name the package to run, the folder it is looked up
    in or the registry it comes from, none of which the command line shows, so
    the rest of the command cannot prove the code is the project's.  *remainder*
    is *command* with its leading wrappers and assignments peeled.
    """
    import os

    stripped = command.strip()
    prefix = stripped[: len(stripped) - len(remainder)]
    base = os.path.basename(remainder.split(None, 1)[0])
    if base not in _PACKAGE_TOOL_COMMANDS or not _PACKAGE_CONFIG_ENV_RE.search(prefix):
        return None
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="package-config-environment",
        cli_family=CLI_FAMILY_LOOKUP.get(base, "package"),
        confidence="medium",
        reason=f"'{base}' runs under a package-manager configuration variable",
    )


def _peel_release_track_prefix(command: str) -> "Tuple[str, bool]":
    """Peel a CLI's release-track word off *command* so positions line up again.

    Returns ``(remainder, peeled)``. ``gcloud`` names its pre-GA surfaces with a
    word placed between the tool and the command -- ``gcloud alpha projects
    remove-iam-policy-binding`` is the same operation as ``gcloud projects
    remove-iam-policy-binding``, routed to a different API track. The word costs
    nothing to type and shifts every following token one position, which is
    enough to defeat BOTH position-sensitive mechanisms downstream:

    * the anchored upgrade (Step 3e.5) matches an anchor path as a PREFIX of
      ``non_flag_tokens``, so every declared path misses by one; and
    * the Step 4 verb scan hyphen-splits a compound subcommand only while
      ``semantic_index <= 2``, so the shift carries
      ``remove-iam-policy-binding`` past the window where it would reach
      ``remove``.

    Normalizing here -- once, above both -- is what makes one edit serve them.
    Declaring the tracked forms twice in the anchor table would double that
    table today and double the omissions in it tomorrow, and would still leave
    the verb scan shifted, since the verb scan reads no table at all.

    Scoped by CLI, and to ONE position within it. ``alpha`` and ``beta`` are
    ordinary words -- a bucket, a branch, a make target -- so this must not
    reach a command where they are an argument or a resource name. Two
    conditions together keep it to the track slot:

    * ``base_cmd`` declares a track set in ``RELEASE_TRACK_PREFIXES``. Every
      other CLI is returned untouched, so ``make alpha`` and ``git checkout
      beta`` never enter this path at all; and
    * the word is the FIRST non-flag token, agreed on by two views -- the raw
      token walk here and ``analyze_command``'s own ``non_flag_tokens``. The
      agreement matters where the two can disagree: ``analyze_command`` absorbs
      the token after a single-letter short flag as that flag's value, so the
      raw walk can see a word at the head that the semantic view has already
      spent on a flag. Peeling on the raw view alone would then shift a
      DIFFERENT token into the flag's mouth and could break an anchor that was
      matching. Requiring both views to name the same word confines this to the
      case where removing it is a pure shift.

      ``gcloud -q alpha ...`` used to be that disagreement and no longer is:
      ``BOOLEAN_SHORT_FLAGS`` declares gcloud's ``-q`` valueless, so ``alpha``
      stays at the head in both views and the form peels -- which is what makes
      it classify T3, where stacking the two tricks had put it at T0. The guard
      still stands for every single-letter flag NOT declared valueless, which is
      every flag that really does take a value.

    Leading flags are stepped over rather than treated as a stop, because the
    track can legally follow a global flag (``gcloud --project=x alpha ...``);
    stopping at the first flag would leave the same bypass one word longer.

    The rebuilt string is a CLASSIFICATION artifact only. Nothing executes it,
    and it never reaches an approval signature -- those are built by
    ``approval_scopes.build_approval_signature`` from the command as the user
    wrote it, so ``gcloud alpha X`` and ``gcloud X`` stay two distinct grants.
    Normalizing inside ``analyze_command`` instead would have collapsed them
    into one, letting a grant minted for the plain form authorize the tracked
    one.

    Direction of error, when the guards above still let an argument through:
    removing a token can only move later tokens TOWARD the head, and neither
    track word appears in any verb table or at the head of any anchor path, so
    no match can be lost -- only gained. A misfire costs a spurious approval
    prompt; it cannot open a gate.
    """
    semantics = analyze_command(command)
    tracks = RELEASE_TRACK_PREFIXES.get(semantics.base_cmd)
    if not tracks or not semantics.non_flag_tokens:
        return command, False
    if semantics.non_flag_tokens[0] not in tracks:
        return command, False

    tokens = semantics.tokens
    for index, token in enumerate(tokens[1:], start=1):
        if _is_flag(token):
            continue
        if token.lower() not in tracks:
            return command, False
        return shlex.join((*tokens[:index], *tokens[index + 1:])), True
    return command, False


_JS_RUNTIME_SCRIPT_VALUE_FLAGS: Dict[str, FrozenSet[str]] = {
    "deno": frozenset({"-c", "--config", "--import-map", "--lock", "--cert", "--location", "--seed", "--preload"}),
    "bun": frozenset({
        "--cwd", "--env-file", "--config", "-c", "--tsconfig-override", "--port", "-r", "--preload", "--require",
    }),
}
_JS_RUNTIME_SCRIPT_RE = _re.compile(r"[^\s]*(?:/|://)[^\s]*|[^\s]+\.(?:[mc]?[jt]s|[jt]sx)")


def _js_runtime_script(runtime: str, arguments: List[str]) -> Optional[str]:
    """The file ``deno [run]`` or ``bun [run]`` executes from *arguments*, or None for any other subcommand."""
    value_flags = _JS_RUNTIME_SCRIPT_VALUE_FLAGS[runtime]
    positionals: List[str] = []
    skip_value = False
    for token in arguments:
        if skip_value:
            skip_value = False
        elif token in _INLINE_CODE_MAP[runtime]:
            return None
        elif token in value_flags:
            skip_value = True
        elif not token.startswith("-"):
            positionals.append(token)
            if token != "run" or len(positionals) > 1:
                break
    if positionals[:1] == ["run"]:
        positionals = positionals[1:]
    if positionals and _JS_RUNTIME_SCRIPT_RE.fullmatch(positionals[0]):
        return positionals[0]
    return None


def _resolve_script_argument(
    base_cmd: str, semantics: "CommandSemantics",
) -> "Optional[Tuple[str, str]]":
    """Identify a script-file invocation and return ``(path, lane)``.

    Two shapes are recognized:

    * ``<interpreter> <script-file>`` -- the first positional argument after a
      known interpreter, whose lane (``"python"`` or ``"shell"``) is decided by
      the interpreter, not the filename; ``deno``/``bun`` count only a
      file-shaped positional (``_js_runtime_script``), and ``R`` only ``CMD BATCH``.
    * ``./script`` / ``path/to/script`` -- a direct executable invocation whose
      lane is inferred from the file extension via ``_SHEBANG_EXT_LANES``.

    Returns ``None`` when the command is not a script-file invocation, so the
    caller continues with ordinary verb detection.
    """
    raw_tokens = semantics.tokens
    if not raw_tokens:
        return None

    if base_cmd in _SCRIPT_FILE_INTERPRETERS:
        if base_cmd in _PYTHON_INTERPRETERS:
            lane = "python"
        elif base_cmd in _NON_SHELL_SCRIPT_INTERPRETERS:
            lane = "code"
        else:
            lane = "shell"
        path = script_positional(base_cmd, raw_tokens[1:])
        return (path, lane) if path else None
    if base_cmd in _JS_RUNTIME_SCRIPT_VALUE_FLAGS:
        path = _js_runtime_script(base_cmd, raw_tokens[1:])
        return (path, "code") if path else None
    if base_cmd == "r" and [token.upper() for token in raw_tokens[1:3]] == ["CMD", "BATCH"]:
        path = next((token for token in raw_tokens[3:] if not token.startswith("-")), None)
        return (path, "code") if path else None

    # Direct ``./script`` or ``path/script.ext`` invocation: the executable
    # token IS the script.  Use the original-case token so the path resolves
    # correctly on case-sensitive filesystems.
    invoked = raw_tokens[0]
    if "/" in invoked:
        for ext, lane in _SHEBANG_EXT_LANES.items():
            if invoked.endswith(ext):
                return (invoked, lane)
    return None


def _classify_powershell_verb(verb: str, noun: str) -> str:
    """Classify a single PowerShell cmdlet by its Verb-Noun verb.

    Returns one of ``"read"`` (non-mutative), ``"change"`` (mutative), or
    ``"unknown"`` (verb in neither approved set -> conservative T3).  ``verb``
    and ``noun`` are already lowercased.  The ``Out-*`` and ``Format-*`` verbs
    are split by noun: ``Out-String``/``Out-Host``/``Out-Null`` render only
    (read) while ``Out-File``/``Out-Printer`` write (change); ``Format-Table``/
    ``Format-List``/``Format-Wide`` render only (read) while ``Format-Volume``
    and other storage ``Format-*`` destroy data (change).
    """
    if verb == "out":
        return "read" if noun in _PS_OUT_READ_NOUNS else "change"
    if verb == "format":
        return "read" if noun in _PS_FORMAT_READ_NOUNS else "change"
    if verb in _PS_READ_VERBS:
        return "read"
    if verb in _PS_CHANGE_VERBS:
        return "change"
    return "unknown"


def _classify_powershell_payload(
    payload: str, family: str, source: str,
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> MutativeResult:
    """Classify a PowerShell script payload at the verdict of its most mutative command position.

    ``payload`` is the raw text of a ``-Command`` string, a ``.ps1`` file or a
    bare PowerShell command. Obfuscation markers (``iex``, ``Invoke-Expression``,
    a call operator) are matched across the whole payload first, so a read
    cmdlet piped into ``iex`` cannot launder it. Then every command position
    (``_ps_command_positions``) is judged by what leads it: a cmdlet by its
    Verb-Noun verb, an alias as the cmdlet it names, a native command as its
    POSIX twin (``detect_mutative_command``). Only the leading word of a
    position is a command, so a hyphenated path argument is never read as a
    cmdlet. The payload is free only when no position mutates and at least one
    is a recognized read cmdlet or alias; otherwise it cannot be proven
    read-only and is T3.
    """
    # 1. Obfuscation / non-inspectable execution -- whole-payload scan (NOT
    #    restricted to first tokens: a marker may hide mid-stage).
    for rx in _PS_OBFUSCATION_RES:
        if rx.search(payload):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="powershell-obfuscation",
                cli_family=family,
                confidence="high",
                reason=(
                    f"PowerShell {source} contains an obfuscation / "
                    f"arbitrary-execution marker (iex / call-operator) "
                    f"-- requires approval"
                ),
            )

    if _PS_METHOD_CALL_RE.search(payload):
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="powershell-method-call",
            cli_family=family,
            confidence="medium",
            reason=(
                f"PowerShell {source} calls a .NET method, which may mutate "
                f"whatever its name -- requires approval"
            ),
        )

    saw_read_cmdlet = False
    for position in _ps_command_positions(payload):
        position = _PS_CALL_OPERATOR_RE.sub("", position)
        words = position.split()
        if not words:
            continue
        head = words[0].lower()
        if head.strip("\"'").endswith(".ps1"):
            position, head = f"pwsh -File {position}", "pwsh"
        elif head[0] in _PS_EXPRESSION_LEADS or head in _PS_KEYWORDS:
            continue
        if head in _PS_SCRIPTBLOCK_INVOKERS:
            if all(word.lower() in _PS_INVOKER_BLOCK_FLAGS for word in words[1:]):
                saw_read_cmdlet = True
                continue
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="powershell-member-invocation",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"PowerShell {source} runs '{position}', which calls a named "
                    f"member on every input -- requires approval"
                ),
            )
        if head in _PS_ALIASES:
            kind, cmdlet = _PS_ALIASES[head]
            if kind == "change":
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=cmdlet,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"PowerShell {source} runs alias '{head}' -> {cmdlet} "
                        f"(mutative) -- requires approval"
                    ),
                )
            saw_read_cmdlet = True
            continue
        cmdlet = _PS_CMDLET_RE.fullmatch(head)
        if cmdlet is None:
            if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
                native = MutativeResult(
                    is_mutative=True, category=CATEGORY_MUTATIVE,
                    verb="powershell-recursion-limit", cli_family=family,
                    confidence="medium", reason="nesting too deep to inspect",
                )
            else:
                native = detect_mutative_command(position, cwd=cwd, _depth=_depth + 1)
            if native.is_mutative:
                return replace(
                    native,
                    reason=f"PowerShell {source} runs '{position}': {native.reason}",
                )
            continue
        verb, noun = cmdlet.groups()
        kind = _classify_powershell_verb(verb, noun)
        if kind == "change":
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=f"{verb}-{noun}",
                cli_family=family,
                confidence="high",
                reason=(
                    f"PowerShell {source} invokes change cmdlet "
                    f"'{verb}-{noun}' (verb '{verb}') -- requires approval"
                ),
            )
        if kind == "unknown":
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=f"{verb}-{noun}",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"PowerShell {source} invokes cmdlet '{verb}-{noun}' whose "
                    f"verb '{verb}' is not an approved read verb "
                    f"(conservative default)"
                ),
            )
        saw_read_cmdlet = True

    if not saw_read_cmdlet:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="powershell-uninspectable",
            cli_family=family,
            confidence="medium",
            reason=(
                f"PowerShell {source} has no recognized read cmdlet or alias at "
                f"a command position -- cannot prove it is read-only "
                f"(conservative default)"
            ),
        )

    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="powershell-read",
        cli_family=family,
        confidence="high",
        reason=(
            f"PowerShell {source}: all command-position cmdlets are approved "
            f"read verbs (Get/Measure/Select/... ) -- non-mutative"
        ),
    )


def _check_powershell_command(
    command: str, base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify a PowerShell invocation by introspecting its payload.

    Recognizes ``powershell``/``powershell.exe``/``pwsh``/``pwsh.exe``.  The
    payload source is, in priority order:
      * ``-EncodedCommand <base64>`` -> T3 immediately (non-inspectable).
      * ``-File <script.ps1>``       -> read the file and classify its contents
        (unreadable -> conservative T3, mirroring the script-file lane).
      * ``-Command``/``-c`` inline   -> classify the script it runs
        (``_powershell_payload``).
      * no explicit payload flag      -> classify the words after the options; a
        bare interactive ``powershell`` with no cmdlet falls to conservative T3.

    Returns ``None`` when ``base_cmd`` is not a PowerShell interpreter.
    """
    if base_cmd not in _POWERSHELL_INTERPRETERS:
        return None

    flag_tokens = set(semantics.flag_tokens)

    # -EncodedCommand: base64 payload is not inspectable -> conservative T3.
    if any(_is_ps_encoded_flag(f) for f in flag_tokens):
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="powershell-encodedcommand",
            cli_family=family,
            confidence="high",
            reason=(
                "PowerShell invoked with -EncodedCommand (base64 payload) "
                "-- cannot introspect the script, requires approval"
            ),
        )

    # -File <script.ps1>: classify the referenced file's contents.  Routed off
    # an actual ``.ps1`` positional rather than the ``-File``/``-f`` flag: the
    # tokenizer normalizes a long single-dash flag into its single chars
    # (``-NoProfile`` -> ``-n -o ... -f ...``), so a flag-set membership test for
    # ``-f`` would false-positive on every ``-NoProfile`` invocation.  A ``.ps1``
    # positional is the reliable, collision-free signal.
    ps_files = [
        t for t in semantics.non_flag_tokens if t.lower().endswith(".ps1")
    ]
    has_command_flag, payload = _powershell_payload(command)
    if ps_files and not has_command_flag:
        content = _read_script_content(ps_files[0], cwd=cwd)
        if content is None:
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="powershell-file-unreadable",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"PowerShell -File '{ps_files[0]}' is not a readable file "
                    f"-- cannot verify the payload (conservative default)"
                ),
            )
        return _classify_powershell_payload(
            content, family, "script file", cwd=cwd, _depth=_depth,
        )

    return _classify_powershell_payload(
        payload, family, "-Command payload", cwd=cwd, _depth=_depth,
    )


def _check_windows_native_command(
    command: str, base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify a BARE Windows command that has no powershell/pwsh wrapper.

    Closes the rc.3 hole where a peeled ``Remove-Item -Recurse -Force``, a
    cmd.exe ``del``/``rd``, or a PowerShell alias fell to safe-by-elimination
    (T0) under the POSIX verb scanner.  SCOPED default-deny: fires ONLY for
    recognized Windows tokens and returns ``None`` for everything else, so bash
    / POSIX classification is untouched.  Recognition order:

      0. obfuscation / arbitrary-execution markers (iex / iwr / call-operator)
      1. cmd.exe two-token builtins (``reg``/``sc``/``vssadmin`` <sub>)
      2. cmd.exe single-token mutative builtins (``del``/``rd``/``format``/...)
      3. cmd.exe single-token read builtins (``dir``/``findstr``/...)
      4. PowerShell aliases (``gci``/``del``/``cd``/...)
      5. bare Verb-Noun cmdlet (``Remove-Item``, ``Frobnicate-Thing``)

    An unknown verb / cmdlet / subcommand in a recognized Windows context ->
    T3 (default-deny), mirroring ``_check_script_file`` (unreadable -> T3) and
    ``_check_package_manager`` (unresolvable -> T3).
    """
    tokens = list(semantics.tokens)
    non_flags = semantics.non_flag_tokens
    first_sub = non_flags[0] if non_flags else ""
    raw_base = (
        semantics.semantic_head_tokens_raw[0]
        if semantics.semantic_head_tokens_raw else base_cmd
    )

    # Is base_cmd a PowerShell cmdlet?  A lowercase Verb-Noun token counts only
    # when its verb is a real PS verb (excludes docker-compose / pre-commit); a
    # PascalCase token is a cmdlet by casing alone (Remove-Item / Frobnicate-X).
    cmdlet_shape = bool(_PS_CMDLET_RE.fullmatch(base_cmd))
    verb_lc = base_cmd.split("-", 1)[0] if cmdlet_shape else ""
    is_bare_cmdlet = cmdlet_shape and (
        verb_lc in _PS_ALL_VERBS or bool(_PS_PASCAL_CMDLET_RE.match(raw_base))
    )

    recognized = (
        base_cmd in _CMD_SUBCOMMAND_BUILTINS
        or base_cmd in _CMD_MUTATIVE_BUILTINS
        or base_cmd in _CMD_READ_BUILTINS
        or base_cmd in _PS_ALIASES
        or base_cmd in _PS_BARE_OBFUSCATION_ALIASES
        or base_cmd in _PS_SCRIPTBLOCK_INVOKERS
        or is_bare_cmdlet
    )
    if not recognized:
        return None  # not a Windows-native token -- POSIX classification owns it

    def _change(verb: str, reason: str) -> MutativeResult:
        return MutativeResult(
            is_mutative=True, category=CATEGORY_MUTATIVE, verb=verb,
            dangerous_flags=_scan_dangerous_flags(tokens, base_cmd),
            cli_family="powershell", confidence="high", reason=reason,
        )

    def _read(verb: str, reason: str) -> MutativeResult:
        return MutativeResult(
            is_mutative=False, category=CATEGORY_READ_ONLY, verb=verb,
            cli_family="powershell", confidence="high", reason=reason,
        )

    # 0. Obfuscation / arbitrary-execution markers anywhere in the command.
    for rx in _PS_OBFUSCATION_RES:
        if rx.search(command):
            return _change(
                "windows-obfuscation",
                "Bare Windows command contains an obfuscation / "
                "arbitrary-execution marker (iex / iwr / call-operator) "
                "-- requires approval",
            )

    # 1. cmd.exe two-token builtins (reg / sc / vssadmin).
    if base_cmd in _CMD_SUBCOMMAND_BUILTINS:
        mut_subs, read_subs = _CMD_SUBCOMMAND_BUILTINS[base_cmd]
        label = f"{base_cmd} {first_sub}".strip()
        if first_sub in read_subs:
            return _read(label, f"cmd.exe '{label}' is a read-only builtin")
        # mutative subcommand OR unrecognized subcommand -> default-deny.
        return _change(
            label,
            f"cmd.exe '{label}' mutates system state "
            f"(conservative default for unrecognized '{base_cmd}' subcommand)",
        )

    # 2. cmd.exe single-token mutative builtins.
    if base_cmd in _CMD_MUTATIVE_BUILTINS:
        return _change(
            base_cmd,
            f"cmd.exe builtin '{base_cmd}' mutates filesystem/system state "
            f"-- requires approval",
        )

    # 3. cmd.exe single-token read builtins.
    if base_cmd in _CMD_READ_BUILTINS:
        return _read(base_cmd, f"cmd.exe builtin '{base_cmd}' is read-only")

    # 4. PowerShell aliases (obfuscation aliases already handled at Step 0).
    if base_cmd in _PS_ALIASES:
        kind, cmdlet = _PS_ALIASES[base_cmd]
        if kind == "read":
            return _classify_powershell_payload(
                command, "powershell", "bare alias", cwd=cwd, _depth=_depth,
            )
        return _change(
            cmdlet,
            f"PowerShell alias '{base_cmd}' -> {cmdlet} (mutative) "
            f"-- requires approval",
        )

    if base_cmd in _PS_BARE_OBFUSCATION_ALIASES:
        # No marker matched at Step 0 (regex drift guard) -- still arbitrary exec.
        return _change(
            base_cmd,
            f"Bare PowerShell execution alias '{base_cmd}' runs arbitrary code "
            f"-- requires approval",
        )

    # 5. Bare cmdlet or script-block invoker: classify the WHOLE command via the
    #    shared payload classifier, identically to the wrapped -Command lane.
    return _classify_powershell_payload(
        command, "powershell", "bare cmdlet", cwd=cwd, _depth=_depth,
    )


def _read_script_content(
    path: str, cwd: "Optional[str]" = None,
) -> "Optional[str]":
    """Read a bounded prefix of a script file for content classification.

    A relative *path* resolves against *cwd* when given (the ``cd`` target of
    the surrounding chain), else against the process cwd -- the previous,
    hook-cwd-only behavior.  This is what lets ``cd /repo && node build.mjs``
    find ``build.mjs`` under ``/repo`` instead of the Gaia install dir.  An
    absolute path is unaffected, and a leading ``~`` is the home directory the
    shell expands it to.

    Returns ``None`` when the path cannot be resolved to a readable regular
    file -- the caller treats that as the conservative (mutative) case, because
    an interpreter pointed at an un-inspectable payload could do anything.  The
    genuinely-unreadable case is preserved: an honored-but-still-missing path
    still returns ``None`` here.
    """
    import os

    resolved = path = os.path.expanduser(path)
    if not os.path.isabs(path):
        base = cwd if cwd is not None else os.getcwd()
        resolved = os.path.join(base, path)
    try:
        if not os.path.isfile(resolved):
            return None
        with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(_MAX_SCRIPT_READ_BYTES)
    except (OSError, ValueError):
        return None


def _check_stdin_script_payload(
    command: str, base_cmd: str, family: str, semantics: "CommandSemantics",
) -> "Optional[MutativeResult]":
    """Classify ``<interpreter> -`` (program read from stdin) conservatively.

    The stdin sentinel makes the interpreter execute a program that has no path
    to open, so its content cannot be inspected the way a script file's is.
    That is the same epistemic position as the ``script-file-unreadable``
    default -- an executable payload we cannot verify -- and it gets the same
    answer: mutative, requiring consent.  It also removes a spelling asymmetry,
    since ``cat payload.py | python3`` is already T3 via the composition layer's
    ``file_to_exec`` rule while ``python3 - < payload.py`` passed the full
    validator as T0.

    Three guards keep it off the inspectable paths: a HEREDOC payload is left to
    Step 3c (the body is in the command string and IS analyzed), an inline-code
    or module flag means the payload is not stdin at all, and a syntax-check
    flag means nothing executes.  The sentinel must also be the FIRST positional
    -- a later ``-`` is an argument to the script, not the program source.

    Returns ``None`` for every other shape, so ordinary detection continues.
    """
    if base_cmd not in _SCRIPT_FILE_INTERPRETERS and base_cmd not in _INLINE_CODE_CLIS:
        return None
    if not semantics.non_flag_tokens:
        return None
    if semantics.non_flag_tokens[0] != _STDIN_SCRIPT_SENTINEL:
        return None
    if "<<" in command:
        return None

    flag_set = set(semantics.flag_tokens)
    if _INTERP_NON_SCRIPT_VALUE_FLAGS.get(base_cmd, frozenset()) & flag_set:
        return None
    if _INLINE_CODE_MAP.get(base_cmd, frozenset()) & flag_set:
        return None
    if _INTERP_SYNTAX_CHECK_FLAGS.get(base_cmd, frozenset()) & flag_set:
        return None

    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="script-stdin-payload",
        cli_family=family,
        confidence="medium",
        reason=(
            f"Interpreter '{base_cmd}' reads its program from stdin ('-') -- "
            f"the payload has no path to inspect, so it cannot be verified, "
            f"requiring approval (conservative default)"
        ),
    )


_HASH_COMMENT_RE = _re.compile(r"#.*$", _re.MULTILINE)


def _script_honored_simulation_flag(
    content: str, script_path: str, base_cmd: str,
    semantics: "CommandSemantics",
) -> "Optional[str]":
    """Return the simulation flag this script actually honors, or ``None``.

    A simulation-shaped flag on the invocation line is a claim, not evidence:
    the caller asserts the run will not mutate, but only the script decides
    whether the flag changes anything. Absolving a script on the flag alone
    lets any mutation be laundered by appending ``--dry-run``, including a
    flag that belongs to a different CLI entirely. So the exemption is granted
    only when the script's own EXECUTABLE text names the flag that was passed
    -- the minimum observable evidence that the payload reads it at all.

    The flag is matched against the source with comments removed and string
    contents kept: a guard is written as ``argv.includes('--dry-run')``, so the
    literal legitimately lives inside a string, while a usage line in a header
    comment (``node x.mjs --dry-run``) is exactly the false evidence to
    discard. The lexer supplies that projection for the languages it models;
    for the rest (python, shell) a ``#``-to-end-of-line strip is used, which
    can only remove more text than a true lexer would and therefore only ever
    withholds the exemption.

    A ``--flag=value`` token is matched on its name part, so ``--dry-run=client``
    is honored by a script that reads ``--dry-run``.

    Args:
        content: Raw script source.
        script_path: Resolved path of the script (extension drives the spec).
        base_cmd: The interpreter token, or the script path for ``./x``.
        semantics: Parsed command semantics of the invocation.

    Returns:
        The first passed simulation flag the script references, else ``None``.
    """
    passed = [
        token for token in semantics.tokens
        if token.lower() in SIMULATION_FLAGS
    ]
    if not passed:
        return None

    code_view = None
    if _spec_for_script is not None and _strip_source is not None:
        spec = _spec_for_script(base_cmd, script_path)
        if spec is not None:
            code_view = _strip_source(content, spec).exec_view
    if code_view is None:
        code_view = _HASH_COMMENT_RE.sub("", content)

    for token in passed:
        name = token.split("=", 1)[0]
        if name in code_view:
            return token
    return None


_PRELOAD_FLAGS: Dict[str, FrozenSet[str]] = {
    "node": frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"}),
    "bun": frozenset({"-r", "--preload", "--require"}),
    "deno": frozenset({"--preload"}),
}
_NODE_PRELOAD_FLAGS = _PRELOAD_FLAGS["node"]


def _flag_values(args: "List[str]", flags: "FrozenSet[str]") -> "List[str]":
    """The values *flags* take in *args*, spaced (``-r x``), with ``=`` or attached to a short flag (``-rx``)."""
    values = []
    for index, token in enumerate(args):
        flag, attached, value = token.partition("=")
        if flag in flags:
            values.append(value if attached else (args[index + 1] if index + 1 < len(args) else ""))
        elif token[:2] in flags and not token.startswith("--") and len(token) > 2:
            values.append(token[2:])
    return values


def _node_preload_values(args: "List[str]") -> "List[str]":
    """The modules node loads before its entry point, in every spelling of its preload flags."""
    return _flag_values(args, _NODE_PRELOAD_FLAGS)


def _foreign_code_result(reason: str) -> MutativeResult:
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="foreign-code",
        cli_family="interpreter",
        confidence="high",
        reason=f"{reason} -- code the screen did not read, requiring approval",
        rule="sign.foreign-code",
    )


_LUA_PROGRAM_RE = re.compile(r"lua(?:jit)?[\d.]*")
_PYTHON_MODULE_SUFFIXES: FrozenSet[str] = frozenset({".py", ".pyc", ".pyw", ".so", ".pyd"})


def _check_interpreter_loads(
    base_cmd: str, semantics: "CommandSemantics", cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Judge the code an interpreter's own flags load before the code it is given; what it cannot read asks."""
    args = semantics.tokens[1:]
    for preload in _flag_values(args, _PRELOAD_FLAGS.get(base_cmd, frozenset())):
        result = detect_mutative_command(f"node {shlex.quote(preload)}", cwd=cwd, _depth=_depth + 1)
        if result.is_mutative:
            return result
    if base_cmd == "deno":
        return _deno_module_map(args, cwd)
    if _LUA_PROGRAM_RE.fullmatch(base_cmd):
        tokens = iter(args)
        for token in tokens:
            if token.startswith("-l"):
                return _foreign_code_result(f"{base_cmd} {token} requires a module before the code")
            if token == "-e":
                next(tokens, None)
            elif not token.startswith("-"):
                break
    if base_cmd in ("r", "rscript") and _flag_values(args, frozenset({"-f", "--file"})):
        return _foreign_code_result(f"{base_cmd} --file runs another file")
    auto_loaded = _AUTO_LOAD_CHECKS.get(base_cmd)
    if auto_loaded is not None:
        result = auto_loaded(args, cwd, _depth)
        if result is not None:
            return result
    return _python_local_module_run(base_cmd, args, cwd, _depth)


def _bunfig_loads(args: "List[str]", cwd: "Optional[str]", _depth: int) -> "Optional[MutativeResult]":
    """Judge the preloads of the bunfig.toml bun reads: the one named, else the cwd's, and the user's global one."""
    base = Path(cwd or Path.cwd())
    named = [base / Path(value).expanduser() for value in _flag_values(args, frozenset({"-c", "--config"}))]
    user_dir = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("HOME") or str(Path.home())
    for config in (*(named or [base / "bunfig.toml"]), Path(user_dir, ".bunfig.toml")):
        if not config.is_file():
            continue
        if tomllib is None:
            return _foreign_code_result(f"bun config '{config}' needs a TOML reader this Python lacks")
        try:
            table = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _foreign_code_result(f"bun config '{config}' cannot be read")
        if _config_values(table, "plugins"):
            return _foreign_code_result(f"bun config '{config}' loads plugins")
        for preload in _config_values(table, "preload"):
            if not isinstance(preload, str):
                return _foreign_code_result(f"bun config '{config}' preloads {preload!r}")
            result = detect_mutative_command(f"node {shlex.quote(preload)}", cwd=str(config.parent), _depth=_depth + 1)
            if result.is_mutative:
                return result
    return None


def _config_values(table: object, key: str) -> list:
    """Every value *key* holds at any depth of a parsed config table, a list flattened to its items."""
    if not isinstance(table, dict):
        return []
    found: list = []
    for name, value in table.items():
        if name == key:
            found.extend(value if isinstance(value, list) else [value])
        else:
            found.extend(_config_values(value, key))
    return found


def _node_env_files(args: "List[str]", cwd: "Optional[str]", _depth: int) -> "Optional[MutativeResult]":
    """Ask when a ``--env-file`` node reads sets a NODE_OPTIONS that loads code, or cannot be read."""
    base = Path(cwd or Path.cwd())
    for flag in ("--env-file", "--env-file-if-exists"):
        for value in _flag_values(args, frozenset({flag})):
            path = base / Path(value).expanduser()
            if flag == "--env-file-if-exists" and not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                return _foreign_code_result(f"node {flag} '{value}' cannot be read")
            for line in lines:
                name, assigned, option = line.strip().removeprefix("export ").partition("=")
                if assigned and name.strip() == "NODE_OPTIONS" and _node_options_load_code(option.strip().strip("'\"")):
                    return _foreign_code_result(f"node {flag} '{value}' sets NODE_OPTIONS={option.strip()}")
    return None


def _r_startup_files(args: "List[str]", cwd: "Optional[str]", _depth: int) -> "Optional[MutativeResult]":
    """Ask when R starts from a ./.Rprofile, or a ./.Renviron that names startup code or cannot be read."""
    base = Path(cwd or Path.cwd())
    if not {"--vanilla", "--no-init-file"} & set(args) and (base / ".Rprofile").exists():
        return _foreign_code_result("R runs ./.Rprofile before the code")
    environ = base / ".Renviron"
    if {"--vanilla", "--no-environ"} & set(args) or not environ.exists():
        return None
    try:
        lines = environ.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return _foreign_code_result("R reads ./.Renviron, which cannot be read")
    names = _CODE_INJECTING_ENVIRONMENT["r"][1]
    if any(names.fullmatch(line.partition("=")[0].strip()) for line in lines if "=" in line):
        return _foreign_code_result("./.Renviron points R at a startup file")
    return None


def _ruby_requires(args: "List[str]", cwd: "Optional[str]", _depth: int) -> "Optional[MutativeResult]":
    """Judge each ``-r`` feature ruby loads from a path or a ``-I`` directory like the script it is."""
    base = Path(cwd or Path.cwd())
    load_dirs = [base / Path(value).expanduser() for value in _flag_values(args, frozenset({"-I"}))]
    for feature in _flag_values(args, frozenset({"-r", "--require"})):
        is_path = feature.startswith((".", "/", "~"))
        for directory in [base] if is_path else load_dirs:
            stem = directory / Path(feature).expanduser()
            found = next((p for p in (stem, Path(f"{stem}.rb"), Path(f"{stem}.so")) if p.is_file()), None)
            if found is None and not is_path:
                continue
            if found is not None and found.suffix == ".so":
                return _foreign_code_result(f"ruby -r {feature} loads a compiled extension")
            result = detect_mutative_command(f"ruby {shlex.quote(str(found or stem))}", cwd=cwd, _depth=_depth + 1)
            if result.is_mutative:
                return result
            break
    return None


# The configuration each interpreter loads without being asked to on the
# command line, judged before any inline code or script it is given.
_AUTO_LOAD_CHECKS: "Dict[str, Callable[[List[str], Optional[str], int], Optional[MutativeResult]]]" = {
    "bun": _bunfig_loads,
    "node": _node_env_files,
    "r": _r_startup_files,
    "rscript": _r_startup_files,
    "ruby": _ruby_requires,
}


def _python_skips_local_directory(command: str) -> bool:
    """True when ``-I`` or ``-P`` keeps the cwd and the script's directory off Python's ``sys.path``."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    start = next((i for i, word in enumerate(words) if program_identity(word).lower() in _PYTHON_INTERPRETERS), None)
    if start is None:
        return False
    tokens = iter(words[start + 1:])
    for token in tokens:
        if token in _PYTHON_OPTION_VALUE_FLAGS:
            next(tokens, None)
            continue
        if token.startswith("--"):
            continue
        if not token.startswith("-") or token == "-":
            return False
        switches, value_letter = re.match(r"-([^cmWX]*)([cmWX]?)", token).groups()
        if {"I", "P"} & set(switches):
            return True
        if value_letter in ("c", "m"):
            return False
    return False


def _deno_module_map(args: "List[str]", cwd: "Optional[str]") -> "Optional[MutativeResult]":
    """Ask when deno resolves modules through an import map, given directly or inside a config file."""
    if _flag_values(args, frozenset({"--import-map"})):
        return _foreign_code_result("deno resolves modules through an import map")
    configs = _flag_values(args, frozenset({"-c", "--config"}))
    if not configs and "--no-config" not in args:
        configs = _discovered_deno_config(Path(cwd or Path.cwd()))
    for value in configs:
        try:
            config = json.loads(Path(cwd or Path.cwd(), Path(value).expanduser()).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _foreign_code_result(f"deno config '{value}' cannot be read as JSON")
        if not isinstance(config, dict) or {"imports", "scopes", "importMap"} & config.keys():
            return _foreign_code_result(f"deno config '{value}' maps module specifiers")
    return None


def _discovered_deno_config(directory: Path) -> "List[str]":
    """The deno.json or deno.jsonc deno finds in *directory* or its nearest parent when no config is named."""
    for folder in (directory, *directory.parents):
        for name in ("deno.json", "deno.jsonc"):
            if (folder / name).is_file():
                return [str(folder / name)]
    return []


def _local_python_module(name: str, directory: Path) -> "Optional[Path]":
    """The file or package *name* resolves to in *directory*, a compiled file (``.so``, ``.pyc``, ``__pycache__``) ahead of the source Python may skip for it."""
    package = directory / name
    if package.is_dir():
        return package
    candidates = [
        candidate for candidate in sorted(directory.glob(f"{name}.*"))
        if candidate.suffix in _PYTHON_MODULE_SUFFIXES and candidate.name.split(".")[0] == name
    ]
    candidates += sorted((directory / "__pycache__").glob(f"{name}.*.pyc"))
    compiled = [candidate for candidate in candidates if candidate.suffix != ".py"]
    return (compiled or candidates or [None])[0]


def _python_local_module_run(
    base_cmd: str, args: "List[str]", cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Judge ``python -m <module>`` when the module is a file in the working directory, which Python runs first."""
    if base_cmd not in _PYTHON_INTERPRETERS:
        return None
    tokens = iter(args)
    for token in tokens:
        if token.startswith("-m"):
            module = token[2:] or next(tokens, "")
            break
        if token in _PYTHON_OPTION_VALUE_FLAGS:
            next(tokens, None)
        elif token == "-c" or not token.startswith("-"):
            return None
    else:
        return None
    local = _local_python_module(module.split(".")[0], Path(cwd or Path.cwd()))
    if local is None:
        return None
    if local.suffix == ".py" and "." not in module:
        return detect_mutative_command(f"python3 {shlex.quote(str(local))}", cwd=cwd, _depth=_depth + 1)
    return _foreign_code_result(f"python -m {module} runs {local}")


def _python_foreign_module(
    code: str, directory: "Optional[Path]", family: str, cwd: "Optional[str]", _depth: int,
) -> "Optional[MutativeResult]":
    """Judge the modules *code* loads from *directory* (None: off ``sys.path``): a module file is read like a script."""
    if _loaded_modules is None:
        return None
    names, unnamed = _loaded_modules(code)
    if unnamed:
        return _foreign_code_result(
            "Python loads a file by relative import, runpy.run_path, a site directory or a changed sys.path",
        )
    if directory is None:
        return None
    for name in sorted(names):
        local = _local_python_module(name, directory)
        if local is None:
            continue
        if local.suffix != ".py":
            return _foreign_code_result(f"Python imports '{name}' from {local}")
        result = detect_mutative_command(f"python3 {shlex.quote(str(local))}", cwd=cwd, _depth=_depth + 1)
        if result.is_mutative:
            return result
    return None


# Environment variables that make a program load code before or instead of
# what its command line shows, keyed by the program they reach.
_CODE_INJECTING_ENVIRONMENT: "Dict[str, Tuple[re.Pattern[str], re.Pattern[str]]]" = {
    "node": (re.compile(r"node"), re.compile(r"NODE_OPTIONS")),
    "python": (re.compile(r"python[\d.]*"), re.compile(r"PYTHONSTARTUP|PYTHONPATH")),
    "lua": (_LUA_PROGRAM_RE, re.compile(r"LUA_INIT(?:_\d+_\d+)?")),
    "r": (re.compile(r"r|rscript"), re.compile(r"R_(?:PROFILE|ENVIRON)(?:_USER)?")),
    "git": (
        re.compile(r"git"),
        re.compile(r"GIT_(?:PAGER|EDITOR|SEQUENCE_EDITOR|SSH|SSH_COMMAND|EXTERNAL_DIFF|ASKPASS|PROXY_COMMAND)"),
    ),
}
_ASSIGNMENT_WORD_RE = re.compile(r"([A-Za-z_]\w*)=(.*)", re.DOTALL)


def code_injecting_assignments(command: str) -> "Tuple[Tuple[str, str, str], ...]":
    """Every ``(program, name, value)`` assignment in *command* that injects code into a program, wherever it is set."""
    try:
        words = shlex.split(command)
    except ValueError:
        return ()
    found = []
    for word in words:
        match = _ASSIGNMENT_WORD_RE.fullmatch(word)
        if match is None:
            continue
        for program, (_, names) in _CODE_INJECTING_ENVIRONMENT.items():
            if names.fullmatch(match.group(1)):
                found.append((program, match.group(1), match.group(2)))
    return tuple(found)


def runs_under_injected_environment(
    command: str, assignments: "Tuple[Tuple[str, str, str], ...]", cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """The verdict for *command* when it runs a program one of *assignments* injects code into, or None."""
    if not assignments:
        return None
    programs = set()
    for reading in _peel_wrapper_readings(command)[0]:
        try:
            words = shlex.split(reading)
        except ValueError:
            continue
        if words:
            programs.add(program_identity(words[0]).lower())
    for program, name, value in assignments:
        pattern = _CODE_INJECTING_ENVIRONMENT[program][0]
        if not value or not any(pattern.fullmatch(candidate) for candidate in programs):
            continue
        if program == "git":
            if value.lower() in _GIT_HARMLESS_CODE_VALUES:
                continue
            result = detect_mutative_command(value, cwd=cwd, _depth=_depth + 1)
            if result.is_mutative:
                return result
        elif name == "NODE_OPTIONS":
            if _node_options_load_code(value):
                return _foreign_code_result(f"NODE_OPTIONS={value} preloads code into node")
        elif name != "PYTHONPATH" or _pythonpath_is_writable(value, cwd):
            return _foreign_code_result(f"{name} makes {program} load {value}")
    return None


def _node_options_load_code(value: str) -> bool:
    try:
        words = shlex.split(value)
    except ValueError:
        return True
    return "$" in value or "`" in value or bool(_node_preload_values(words))


def _pythonpath_is_writable(value: str, cwd: "Optional[str]") -> bool:
    """True unless every PYTHONPATH entry is an existing directory this process cannot write."""
    for entry in value.split(os.pathsep):
        path = Path(cwd or Path.cwd(), Path(entry).expanduser())
        if not entry or not path.is_dir() or os.access(path, os.W_OK):
            return True
    return False


# Test-runner options that load no code, checked against the Node CLI and
# test-runner docs. node's flag set grows every release, so this is an
# allowlist: a loader, preload, --test-global-setup or any flag not named here
# keeps the script-file lane. --test-reporter also accepts a module specifier
# and --test-reporter-destination a file path, so only the built-in reporters
# and the standard streams qualify.
_NODE_TEST_SAFE_SWITCHES: FrozenSet[str] = frozenset({
    "--test", "--test-only", "--test-force-exit", "--watch", "--experimental-test-coverage",
})
_NODE_TEST_SAFE_VALUE_FLAGS: FrozenSet[str] = frozenset({
    "--test-name-pattern", "--test-skip-pattern", "--test-concurrency", "--test-timeout",
    "--test-shard", "--test-isolation", "--test-coverage-include", "--test-coverage-exclude",
    "--test-coverage-lines", "--test-coverage-branches", "--test-coverage-functions",
})
_NODE_TEST_CHECKED_VALUES: Dict[str, FrozenSet[str]] = {
    "--test-reporter": frozenset({"spec", "tap", "dot", "junit", "lcov"}),
    "--test-reporter-destination": frozenset({"stdout", "stderr"}),
}


def _is_node_test_suite_run(args: "List[str]", cwd: "Optional[str]") -> bool:
    """True when ``node --test`` carries only directories, globs and allowlisted test options.

    That run is the project's test suite, the same act as ``pytest tests/``
    or an ``npm test`` body. Any other argument may be code node runs, so the
    invocation stays in the script-file lane.
    """
    if "--test" not in args:
        return False
    positionals = []
    remaining = iter(args)
    for token in remaining:
        if not token.startswith("-"):
            positionals.append(token)
            continue
        flag, attached, value = token.partition("=")
        if flag in _NODE_TEST_SAFE_SWITCHES and not attached:
            continue
        if flag not in _NODE_TEST_SAFE_VALUE_FLAGS and flag not in _NODE_TEST_CHECKED_VALUES:
            return False
        if not attached:
            value = next(remaining, None)
        if value is None or value not in _NODE_TEST_CHECKED_VALUES.get(flag, {value}):
            return False
    return bool(positionals) and all(
        re.search(r"[*?\[]", token)
        or Path(cwd or Path.cwd(), Path(token).expanduser()).is_dir()
        for token in positionals
    )


_NODE_OPTIONS_ASSIGNMENT_RE = re.compile(r"(?<![\w-])NODE_OPTIONS=")


def sets_node_options(command: str) -> bool:
    """True when *command* assigns NODE_OPTIONS anywhere, inline, under ``env`` or by ``export``."""
    return bool(_NODE_OPTIONS_ASSIGNMENT_RE.search(command))


_PACKAGE_INPUT_TOKEN_RE = re.compile(r"(?:^|/)(?:package\.json|node_modules)(?:/|$)")
_PACKAGE_INPUT_READERS: FrozenSet[str] = frozenset({
    "cat", "grep", "egrep", "fgrep", "rg", "head", "tail", "ls", "stat", "wc", "diff",
    "jq", "less", "more", "file", "test", "[", "tree", "du", "realpath", "readlink",
    "sha256sum", "md5sum", "echo", "printf",
})
_GIT_PACKAGE_INPUT_READS: FrozenSet[str] = frozenset({
    "diff", "show", "log", "blame", "status", "ls-files", "cat-file", "grep",
    "rev-parse", "ls-tree", "describe", "shortlog", "check-ignore",
})
_PACKAGE_SCRIPT_RUNNERS: FrozenSet[str] = frozenset({"npm", "bun", "pnpm", "yarn", "npx", "bunx", "pnpx"})


def rewrites_package_inputs(command: str) -> bool:
    """True when *command* names package.json or a node_modules path as anything but a read.

    A script run is classified from package.json and node_modules as they are
    when the whole command is classified; a chain stage that rewrites either one
    changes what the later run executes. The test is deliberately by exclusion
    (a named reader is free, any other command naming the file is not): a
    writer nobody listed still counts. It does not see a stage that rewrites
    the tree without naming the file (``git checkout <branch>``).
    """
    semantics = analyze_command(command)
    if not any(_PACKAGE_INPUT_TOKEN_RE.search(token) for token in semantics.tokens[1:]):
        return False
    if semantics.base_cmd in _PACKAGE_INPUT_READERS:
        return False
    if semantics.base_cmd == "git":
        subcommand = semantics.non_flag_tokens[0] if semantics.non_flag_tokens else ""
        return subcommand not in _GIT_PACKAGE_INPUT_READS
    return True


def runs_package_script(command: str) -> bool:
    """True when *command* is a package manager or local runner whose code package.json or node_modules decides."""
    return analyze_command(command).base_cmd in _PACKAGE_SCRIPT_RUNNERS


def node_test_under_node_options(command: str, cwd: "Optional[str]") -> "Optional[MutativeResult]":
    """The T3 verdict for a ``node --test`` suite run that NODE_OPTIONS reaches, or None.

    NODE_OPTIONS can carry ``--import``/``--require`` out of sight of the
    command line, so the suite exemption does not apply; the script-file lane
    cannot read a directory, so the run asks as it did before the exemption.
    A NODE_OPTIONS exported by an earlier, separate call is not visible here.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or program_identity(tokens[0]) != "node":
        return None
    if not _is_node_test_suite_run(tokens[1:], cwd):
        return None
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="node-options-test-run",
        cli_family="interpreter",
        confidence="medium",
        reason=(
            "node --test runs under NODE_OPTIONS, which can preload code the "
            "command line does not show -- requiring approval"
        ),
    )


def _check_script_file(
    command: str, base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify ``<interpreter> <file>`` / ``./script`` by the file's content.

    Closes the file-argument evasion: the verb scanner only sees the filename,
    so a script that deletes files or calls the network would otherwise pass as
    safe by elimination.  Classification is by REAL invocation, mirroring the
    inline ``-c`` path -- an analytic Python script with no mutative calls and a
    read-only shell script both stay non-mutative, so the existing
    overbroad-classification complaint is not reintroduced.

    ``_depth`` is the current body-descent depth.  At ``_MAX_SCRIPT_RECURSION_
    DEPTH`` the script body is NOT opened and the invocation is retained rather
    than released -- see the constant, which owns that rationale.

    Returns ``None`` when the command is not a script-file invocation, which
    includes a heredoc program: its body is inspected by Step 3c, and the
    opener token is not a path.
    """
    if heredoc_program(command) is not None:
        return None
    resolved = _resolve_script_argument(base_cmd, semantics)
    if resolved is None:
        # No script FILE to open -- the payload may still be a program the
        # interpreter reads from stdin, which is un-inspectable rather than
        # absent.  Every other shape returns None and classifies normally.
        return _check_stdin_script_payload(command, base_cmd, family, semantics)

    # Budget exhausted: stop descending AND retain -- see the constant. Placed
    # AFTER the shape check for the same reason as the runner lane's: only a
    # command already recognized as a script invocation may be retained by it.
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result("script body")

    script_path, lane = resolved

    # Syntax-check-only invocations (`bash -n <script>`, `node --check <script>`)
    # validate syntax WITHOUT executing the script body -- the file's contents
    # are never run, so the invocation cannot mutate state (T0).  Scan only the
    # leading interpreter flags: stop at the first positional (the script), so a
    # flag appearing AFTER the script is an argument to the script and does not
    # downgrade.  A co-occurring inline-exec flag is already excluded upstream
    # (`_resolve_script_argument` returns None on a defer flag), so reaching here
    # means the payload is a genuine, non-executed script file.
    syntax_flags = _INTERP_SYNTAX_CHECK_FLAGS.get(base_cmd, frozenset())
    if syntax_flags:
        for token in semantics.tokens[1:]:
            if not token.startswith("-"):
                break  # first positional (the script) -- stop scanning flags
            if token in syntax_flags:
                return MutativeResult(
                    is_mutative=False,
                    category=CATEGORY_READ_ONLY,
                    verb="script-syntax-check",
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Interpreter '{base_cmd}' invoked with syntax-check "
                        f"flag '{token}' on '{script_path}' -- validates syntax "
                        f"without executing the script body (non-mutative)"
                    ),
                )

    if base_cmd == "node" and _is_node_test_suite_run(semantics.tokens[1:], cwd):
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb="node-test-runner",
            cli_family=family,
            confidence="medium",
            reason=f"node --test runs the test suite under '{script_path}'",
        )

    content = _read_script_content(script_path, cwd=cwd)
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

    # --- Gaia CLI dispatcher re-dispatch ---
    # bin/gaia's own body calls subprocess.run() for the lazy DB bootstrap, so
    # Python AST analysis below would flag EVERY `python3 <path>/bin/gaia <cmd>`
    # as mutative regardless of the subcommand. The real effect is determined by
    # the SUBCOMMAND, exactly as the launcher form `gaia <cmd>` is. Recognize the
    # dispatcher and re-dispatch to `gaia <args...>` classification. Returns None
    # for any non-dispatcher script, so ordinary analysis continues.
    gaia_result = _check_gaia_cli_dispatcher(
        script_path, content, semantics, family, _depth=_depth, cwd=cwd,
    )
    if gaia_result is not None:
        return gaia_result

    # --- Simulation flag the SCRIPT honors (bounded Step 3, content-level) ---
    # A build/release script commonly reaches its mutation only behind its own
    # runtime guard (``if (dryRun) { return; }``), which static analysis cannot
    # evaluate -- so `node bin/pre-publish-validate.js --dry-run` paid the full
    # T3 toll for a write that never executes. The exemption is granted here,
    # after the file has been read, and only when the script's own executable
    # text names the flag that was passed; a flag the script never reads leaves
    # this branch and is classified by the content below, exactly as if it had
    # not been passed.
    honored_flag = _script_honored_simulation_flag(
        content, script_path, base_cmd, semantics,
    )
    if honored_flag is not None:
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_SIMULATION,
            verb="script-honored-simulation-flag",
            cli_family=family,
            confidence="high",
            reason=(
                f"Script '{script_path}' reads simulation flag "
                f"'{honored_flag}', which the invocation passed -- the run is "
                f"a simulation the script itself implements"
            ),
        )

    if lane == "python" and _analyze_python_inline is not None:
        def argv_is_mutative(argv: Tuple[str, ...]) -> bool:
            return _argv_command_is_mutative(argv, cwd=cwd, _depth=_depth + 1)

        ast_result = _analyze_python_inline(content, argv_is_mutative=argv_is_mutative)
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
        if ast_result.parse_failed:
            return _unparsed_code(family, f"Python script '{script_path}'")
        script_dir = Path(cwd or Path.cwd(), Path(script_path).expanduser()).parent
        if _python_skips_local_directory(command):
            script_dir = None
        foreign = _python_foreign_module(content, script_dir, family, cwd, _depth)
        if foreign is not None:
            return foreign
        if _runs_code_string(content):
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb="dynamic-code",
                cli_family=family,
                confidence="high",
                reason=f"Python script '{script_path}' hands a string to eval/exec/compile",
                rule="sign.foreign-code",
            )
        if not _parse_finds_no_effect(content, argv_is_mutative):
            return _uncatalogued_python_effect(family, f"Python script '{script_path}'")
        return MutativeResult(
            is_mutative=False,
            category=CATEGORY_READ_ONLY,
            verb="script-file",
            cli_family=family,
            confidence="medium",
            reason=f"Python script '{script_path}' has no mutative invocation (AST analysis)",
        )

    # JavaScript family (node / .js / .mjs / .cjs): lex the source so comments
    # (``//``, ``/* */``) and string / template-literal CONTENTS are removed
    # before any verb scan -- a verb that lives only inside a comment or a
    # string can no longer collide (the false-positive class this closes).
    # A REAL mutation reaches the shell through an exec sink whose argument is
    # a string literal (``execSync("kubectl delete ...")``); that string is
    # preserved in the exec view and still re-classified, so detection is not
    # weakened.  Other languages keep the existing regex lane below.
    if lane == "code" and _spec_for_script is not None:
        spec = _spec_for_script(base_cmd, script_path)
        if spec is not None:
            entry_stripped = _strip_source(content, spec)
            entry_result = _classify_source_with_lexer(
                content, script_path, family, spec, cwd=cwd, _depth=_depth + 1,
                stripped=entry_stripped,
            )
            if entry_result.is_mutative:
                return entry_result
            if spec.name != "javascript":
                if _prints_literals_only(entry_stripped.exec_view):
                    return entry_result
                return _unparsed_code(family, f"Script '{script_path}'")
            closure_result = _classify_js_import_closure(
                script_path, entry_stripped, family, spec, cwd=cwd, _depth=_depth + 1,
            )
            return closure_result if closure_result is not None else entry_result

    # "code" lane (non-shell source: ruby/perl/php) suppresses camelCase
    # subcommand splitting so language identifiers are not read as CLI verbs;
    # "shell" lane keeps the full command semantics.  ``cwd`` is threaded
    # through so a mutative line INSIDE the script body that itself invokes a
    # relative script/npm-run resolves against the SAME directory the outer
    # script was read from, not the hook's own cwd (see _classify_script_
    # content_by_regex's docstring).
    regex_result = _classify_script_content_by_regex(
        content, script_path, family, from_source_code=(lane == "code"),
        cwd=cwd, _depth=_depth + 1,
    )
    if regex_result.is_mutative or base_cmd not in _UNPARSED_CODE_INTERPRETERS or _prints_literals_only(content):
        return regex_result
    return _unparsed_code(family, f"Script '{script_path}'")


# A process inherits the script's stdin, so an interpreter started without a
# program in its argv runs whatever arrives there: the argv names no effect to
# judge.  The program is named by a script positional or by the interpreter's
# own inline-code flag (``_INLINE_CODE_MAP``; ``-c`` for a shell).
_ARGV_STDIN_PROGRAM_FLAGS: FrozenSet[str] = frozenset({"-", "-s", "--stdin"})
_ARGV_PYTHON_RE = _re.compile(r"python\d*(\.\d+)?")


def _argv_runs_unnamed_program(argv: Tuple[str, ...]) -> bool:
    """True when *argv* starts an interpreter or shell with no program named in it."""
    import os
    from .shell_unwrapper import _SHELLS
    base = os.path.basename(argv[0])
    if _ARGV_PYTHON_RE.fullmatch(base):
        program_flags = frozenset({"-c", "-m"})
    elif base in _INLINE_CODE_MAP:
        program_flags = _INLINE_CODE_MAP[base]
    elif base in _SCRIPT_FILE_INTERPRETERS or base in _SHELLS:
        program_flags = frozenset({"-c"})
    else:
        return False
    for token in argv[1:]:
        if token in _ARGV_STDIN_PROGRAM_FLAGS:
            return True
        if token in program_flags or not token.startswith("-"):
            return False
    return True


def _argv_command_is_mutative(
    argv: Tuple[str, ...], cwd: "Optional[str]" = None, _depth: int = 0,
) -> bool:
    """Judge what a script's literal argv runs, as the exec-sink lane does."""
    command = shlex.join(argv)
    if _argv_runs_unnamed_program(argv) or _git_config_programs(command):
        return True
    if _is_blocked_command is not None and _is_blocked_command(command).is_blocked:
        return True
    return detect_mutative_command(command, cwd=cwd, _depth=_depth).is_mutative


def _classify_source_with_lexer(
    content: str, script_path: str, family: str, spec,
    cwd: "Optional[str]" = None, _depth: int = 0,
    stripped=None, screen_blocked: bool = True,
) -> MutativeResult:
    """Classify lexed non-shell source (currently the JS family) by real effect.

    Chain of detectors, each run on the projection of the source that makes it
    sound (see ``source_lexer``):

    1. ``is_blocked_command`` on the ``verb_view`` (comments and string CONTENTS
       blanked) as a defense-in-depth safety net: a permanently-blocked
       destructive pattern is denied even in the (invalid-JS) event it appears
       as bare code.  It cannot false-positive here -- the shell syntax it
       matches only survives blanking if it is executable code, which JS is not.
    2. ``_scan_direct_mutation`` on the ``verb_view``: the filesystem API mutates
       WITHOUT reaching the shell (``fs.rmSync(p, {recursive: true})`` spawns
       nothing), so an exec-sink-only lane classified it T0 while the identical
       call under ``node -e`` classified T3.  Running these patterns on the
       projection with comments and string contents blanked is what keeps them
       from re-introducing the false positives that lane was protecting against.
    3. ``_scan_exec_sink_string_args`` on the ``exec_view`` (string contents
       KEPT), with ``shell_backticks=spec.backticks_are_exec`` so a JS template
       literal is not mistaken for a shell command -- this extracts the command
       handed to a subprocess sink as a string literal and escalates ONLY when
       that inner command is itself mutative/blocked.  This is the mutation
       vector for effects that DO go through the shell, whose command is a
       string the exec view preserves.  ``cwd`` is
       forwarded to this call so a relative script/npm-run command embedded in
       an exec-sink string (``execSync('node engine/build.mjs')``) resolves
       against the directory THIS script was read from, not the hook's own
       cwd -- otherwise a genuinely-mutative-by-real-effect inner command
       falls through to the conservative ``script-file-unreadable`` /
       ``npm-run-unresolved`` default instead of being classified by what it
       actually does.

    Deliberately NOT run: the whole-token mutative-VERB scan
    (``detect_mutative_command``) that the shell/regex lane uses.  In a non-shell
    source language a bare word at "subcommand position" is a language
    identifier, not a CLI subcommand -- ``const label = ...``, ``const set =
    ...``, ``let close = ...`` are variable declarations, not the collaboration
    verbs ``label``/``set``/``close``.  The scan caught NO real JS mutation
    (those go through the fs API or an exec sink, steps 2 and 3) and produced only these
    identifier collisions, so removing it strictly reduces false positives
    without opening a hole.  This extends to whole tokens the same "source
    identifier is not a verb" discipline the ``from_source_code`` camelCase
    guard already applied to camelCase tokens.

    The two views share newline positions, so their line lists stay aligned.

    ``stripped`` lets a caller that already lexed *content* share the views.
    ``screen_blocked=False`` skips step 1, which dominates the cost per line
    and cannot fire on valid JS; the import-closure walk uses it for imported
    modules to stay inside the hook's latency budget.
    """
    if stripped is None:
        stripped = _strip_source(content, spec)
    verb_lines = stripped.verb_view.splitlines()
    exec_lines = stripped.exec_view.splitlines()
    scan_direct = _may_mutate_directly(content, spec.name)

    for verb_line, exec_line in zip(verb_lines, exec_lines):
        code_line = verb_line.strip()
        if code_line and screen_blocked and _is_blocked_command is not None:
            blocked = _is_blocked_command(code_line)
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

        if code_line and scan_direct:
            direct = _scan_direct_mutation(code_line, spec.name)
            if direct is not None:
                label, category = direct
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=label,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Script '{script_path}' invokes {label} ({category}) -- "
                        f"direct filesystem mutation, no exec sink involved"
                    ),
                )

        # Exec-sink extraction on the string-preserving view.  For JS,
        # backticks are template literals (not shell), so they are not scanned.
        sink_line = exec_line.strip()
        if sink_line:
            sink_result = _scan_exec_sink_string_args(
                sink_line, family, shell_backticks=spec.backticks_are_exec,
                cwd=cwd, _depth=_depth,
            )
            if sink_result is not None and sink_result.is_mutative:
                return MutativeResult(
                    is_mutative=True,
                    category=sink_result.category,
                    verb=sink_result.verb,
                    dangerous_flags=sink_result.dangerous_flags,
                    cli_family=family,
                    confidence=sink_result.confidence,
                    reason=(
                        f"Script '{script_path}' exec-sink invokes mutative "
                        f"command: {sink_result.reason}"
                    ),
                )

    if spec.name == "javascript" and _js_hidden_effect(stripped.exec_view, stripped.verb_view):
        return _js_hidden_effect_result(family, f"Script '{script_path}'")
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="script-file",
        cli_family=family,
        confidence="medium",
        reason=f"Script '{script_path}' has no mutative or blocked line",
    )


# A node run executes its entry's whole local import closure, so a write in an
# imported module is as much a write of the run as one inlined in the entry.
# Both bounds exist to keep the hook fast and the walk finite; reaching either
# is a failure to prove the closure write-free, so it is retained (T3), never
# released.  Bare package specifiers (node_modules) are not followed.
_JS_IMPORT_CLOSURE_MAX_FILES = 100
_JS_IMPORT_CLOSURE_MAX_DEPTH = 16

_JS_STRING_SLOT = r"""(?P<quote>[`'"])(?P<spec>[^`'"\n]*)(?P=quote)"""
_JS_IMPORT_SYNTAX_RES: Tuple["_re.Pattern", ...] = (
    _re.compile(r"""\b(?:import|export)\b[^'"`;()]*?\bfrom\s*""" + _JS_STRING_SLOT),
    _re.compile(r"\bimport\s*" + _JS_STRING_SLOT),
    _re.compile(r"\b(?:require|import)\s*\(\s*" + _JS_STRING_SLOT + r"\s*[,)]"),
)
_JS_RELATIVE_SPECIFIER_RE = _re.compile(r"(?:\.{1,2}(?:/.*)?|/.*)", _re.DOTALL)
_JS_IMPORT_EXTENSIONS = (".js", ".mjs", ".cjs", ".json")
_JS_IMPORT_INDEX_FILES = tuple(f"index{ext}" for ext in _JS_IMPORT_EXTENSIONS)


def _js_relative_specifiers(stripped) -> "List[str]":
    """Return the literal relative import specifiers of a lexed module.

    The import syntax is matched on the ``verb_view``, where comments and the
    contents of strings are blanked, so ``"usage: require('./x')"`` and a
    commented-out import are not imports.  The specifier is itself a string,
    so its text is read back from the same span of the ``exec_view``.  A
    template literal with ``${}`` is not a literal path and is not followed.
    """
    return [text for text in _js_literal_specifiers(stripped) if _JS_RELATIVE_SPECIFIER_RE.fullmatch(text)]


def _js_literal_specifiers(stripped) -> "List[str]":
    found: List[str] = []
    for pattern in _JS_IMPORT_SYNTAX_RES:
        for match in pattern.finditer(stripped.verb_view):
            text = stripped.exec_view[match.start("spec"):match.end("spec")]
            if "${" not in text:
                found.append(text)
    return found


def _js_package_mapped_specifier(stripped, importer_dir: str) -> "Optional[str]":
    """A ``#`` import or self-reference the nearest package.json maps, or any bare specifier when it is unreadable."""
    bare = [text for text in _js_literal_specifiers(stripped) if not _JS_RELATIVE_SPECIFIER_RE.fullmatch(text)]
    if not bare:
        return None
    directory = Path(importer_dir)
    manifest = next((d / "package.json" for d in (directory, *directory.parents) if (d / "package.json").is_file()), None)
    if manifest is None:
        return None
    try:
        package = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return bare[0]
    if not isinstance(package, dict):
        return bare[0]
    name = package.get("name")
    for specifier in bare:
        if specifier.startswith("#") and "imports" in package:
            return specifier
        if "exports" in package and isinstance(name, str) and specifier.split("/")[: name.count("/") + 1] == name.split("/"):
            return specifier
    return None


def _first_js_file(path: str) -> "Optional[str]":
    """The file node's LOAD_AS_FILE picks for *path*: itself, then with an extension."""
    import os

    for candidate in (path, *(path + ext for ext in _JS_IMPORT_EXTENSIONS)):
        if os.path.isfile(candidate):
            return candidate
    return None


def _first_js_index(directory: str) -> "Optional[str]":
    import os

    for name in _JS_IMPORT_INDEX_FILES:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _package_entry_points(manifest) -> "Optional[List[str]]":
    """Entry paths a ``package.json`` names (``main``, string ``exports`` leaves).

    ``None`` when the manifest is not the shape node reads.  Node ignores
    ``exports`` for a relative ``require`` of a directory; its string leaves
    are returned anyway because following an extra file only widens the
    closure, never narrows it.
    """
    if not isinstance(manifest, dict):
        return None
    entries: List[str] = []
    main = manifest.get("main")
    if isinstance(main, str):
        entries.append(main)
    elif main is not None:
        return None
    pending = [manifest.get("exports")]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if value.startswith("./"):
                entries.append(value)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif value is not None:
            return None
    return entries


def _resolve_js_directory(directory: str) -> "Optional[List[str]]":
    """Files node loads for ``require(<directory>)``, or None if unprovable.

    The ``package.json`` entry points come first and ``index.*`` is the
    fallback when none resolves.  A ``package.json`` that cannot be read or
    parsed returns None: node would follow whatever it names, and a fall back
    to ``index.*`` could miss that file.
    """
    import json
    import os

    entries: List[str] = []
    manifest_path = os.path.join(directory, "package.json")
    if os.path.isfile(manifest_path):
        text = _read_script_content(manifest_path)
        if text is None:
            return None
        try:
            parsed = _package_entry_points(json.loads(text))
        except (ValueError, RecursionError):
            return None
        if parsed is None:
            return None
        entries = parsed
    loaded: List[str] = []
    for entry in entries:
        entry_path = os.path.normpath(os.path.join(directory, entry))
        hit = _first_js_file(entry_path) or _first_js_index(entry_path)
        if hit is not None:
            loaded.append(hit)
    if loaded:
        return loaded
    index = _first_js_index(directory)
    return [index] if index is not None else None


def _resolve_js_relative_import(specifier: str, importer_dir: str) -> "Optional[List[str]]":
    """Resolve a relative *specifier* to the real files node would load, or None.

    A file (exact, then with an extension) wins over a directory, as in node's
    LOAD_AS_FILE / LOAD_AS_DIRECTORY.  The extension search is applied to ES
    imports too, so an extensionless ES import node would refuse is followed,
    but a file node would load is never skipped.  None means no file is
    provably the one loaded, which the caller treats as unresolved.
    """
    import os

    base = os.path.normpath(os.path.join(importer_dir, specifier))
    file_hit = _first_js_file(base)
    loaded = [file_hit] if file_hit is not None else _resolve_js_directory(base)
    if not loaded:
        return None
    return [os.path.realpath(path) for path in loaded]


def _import_closure_unproven(verb: str, entry_path: str, family: str, reason: str) -> MutativeResult:
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb=verb,
        cli_family=family,
        confidence="low",
        reason=(
            f"Script '{entry_path}': {reason} -- cannot prove the import "
            f"closure free of writes, requiring approval (conservative default)"
        ),
    )


def _classify_js_import_closure(
    entry_path: str, entry_stripped, family: str, spec,
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify the relative-import closure of a node entry, or None if write-free.

    Every module reachable through literal ``import``/``export ... from``/
    ``require``/``import()`` of a relative path gets the entry's direct-mutation
    and exec-sink checks (``_classify_source_with_lexer`` without the blocked
    screen), so a write in an imported module classifies as if inlined in the
    entry.  A relative import that cannot be resolved or read, or a closure past
    either bound, returns a mutative result naming the cause.  Cycles end on the
    visited set.
    """
    import os

    entry_abs = entry_path
    if not os.path.isabs(entry_abs):
        entry_abs = os.path.join(cwd if cwd is not None else os.getcwd(), entry_abs)
    entry_abs = os.path.realpath(entry_abs)

    seen = {entry_abs}
    queue = [(entry_abs, entry_stripped, 0)]
    while queue:
        module, stripped, depth = queue.pop(0)
        mapped = _js_package_mapped_specifier(stripped, os.path.dirname(module))
        if mapped is not None:
            return _foreign_code_result(f"'{module}' imports '{mapped}', which package.json maps to code")
        for specifier in _js_relative_specifiers(stripped):
            targets = _resolve_js_relative_import(specifier, os.path.dirname(module))
            if targets is None and specifier.endswith(".json"):
                continue
            if targets is None:
                return _import_closure_unproven(
                    "js-import-unresolved", entry_path, family,
                    f"'{module}' imports '{specifier}', which resolves to no "
                    f"loadable file (missing, or its package.json is unreadable)",
                )
            for target in targets:
                if target in seen:
                    continue
                if depth + 1 > _JS_IMPORT_CLOSURE_MAX_DEPTH:
                    return _import_closure_unproven(
                        "js-import-closure-bound", entry_path, family,
                        f"import chain deeper than depth {_JS_IMPORT_CLOSURE_MAX_DEPTH}",
                    )
                if len(seen) >= _JS_IMPORT_CLOSURE_MAX_FILES:
                    return _import_closure_unproven(
                        "js-import-closure-bound", entry_path, family,
                        f"import closure larger than {_JS_IMPORT_CLOSURE_MAX_FILES} files",
                    )
                seen.add(target)
                if target.endswith(".json"):
                    continue
                imported = _read_script_content(target)
                if imported is None or len(imported) >= _MAX_SCRIPT_READ_BYTES:
                    return _import_closure_unproven(
                        "js-import-unresolved", entry_path, family,
                        f"imported module '{target}' is unreadable or over the "
                        f"{_MAX_SCRIPT_READ_BYTES}-byte read limit",
                    )
                imported_stripped = _strip_source(imported, spec)
                verdict = _classify_source_with_lexer(
                    imported, target, family, spec, cwd=cwd, _depth=_depth,
                    stripped=imported_stripped, screen_blocked=False,
                )
                if verdict.is_mutative:
                    return replace(
                        verdict,
                        reason=f"Imported by '{entry_path}': {verdict.reason}",
                    )
                queue.append((target, imported_stripped, depth + 1))
    return None


def _classify_script_content_by_regex(
    content: str, script_path: str, family: str,
    from_source_code: bool = False, cwd: "Optional[str]" = None,
    _depth: int = 0,
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

    ``from_source_code`` is forwarded to ``detect_mutative_command`` so that
    non-shell source content (``.js``/``.rb``/...) suppresses camelCase
    subcommand splitting -- a source identifier like ``execPath`` must not be
    read as the CLI verb ``exec``.  Shell-script content leaves it False.

    ``cwd`` is also forwarded to the per-line ``detect_mutative_command`` call
    below.  Without this, a line inside a script/npm-run body that itself
    invokes a relative script (``node other.mjs``) or another ``npm run``
    resolved against ``os.getcwd()`` instead of the directory the OUTER script
    was actually read from -- the same false-``script-file-unreadable`` /
    ``npm-run-unresolved`` bug the outer cwd threading fixed, just one level
    deeper. Losing ``cwd`` here silently re-opens that bug for any nested
    invocation.

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

        line_result = detect_mutative_command(
            line, from_source_code=from_source_code, cwd=cwd, _depth=_depth,
        )
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

        # Exec-sink string-argument re-classification (shared with the inline
        # code path via ``_scan_exec_sink_string_args``).  A source line like
        # ``execSync("kubectl delete ...")`` hides a mutative command inside a
        # string literal the verb scanner cannot see (the quotes make it one
        # token).  Extract the command handed to a known exec sink and
        # re-classify it; escalate ONLY when the inner command is itself
        # mutative/blocked, so a benign ``execSync("ls")`` is not escalated.
        sink_result = _scan_exec_sink_string_args(
            line, family, cwd=cwd, _depth=_depth,
        )
        if sink_result is not None and sink_result.is_mutative:
            return MutativeResult(
                is_mutative=True,
                category=sink_result.category,
                verb=sink_result.verb,
                dangerous_flags=sink_result.dangerous_flags,
                cli_family=family,
                confidence=sink_result.confidence,
                reason=(
                    f"Script '{script_path}' exec-sink invokes mutative "
                    f"command: {sink_result.reason}"
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


# ---------------------------------------------------------------------------
# Package-manager commands: npm, bun, pnpm, yarn (Step 1e)
# ---------------------------------------------------------------------------
# The verb scanner matches the SCRIPT NAME against the taxonomy, but the name is
# arbitrary: ``npm run db-migrate`` slips through as SAFE while ``npm run
# deploy`` whose body is ``vite build`` false-positives.  The signature follows
# where the code comes from instead: a project script is resolved to its body in
# ``package.json`` (``scripts.<script>``) and that body is classified with the
# existing engine -- unresolvable falls back to the conservative T3 default of
# the unreadable-script-file case; adding a dependency brings registry code and
# is T3; a frozen install brings only what the lockfile already pins, so it is
# classified by the project's own install lifecycle scripts.

# Splits a script body into the individual commands the shell would run so a
# mutation in ANY clause is seen, not just the first.  Long operators (``&&``,
# ``||``) match before the single-char class so they are not double-counted.
_SHELL_SEGMENT_SPLIT_RE = _re.compile(r"&&|\|\||[;\n|&]")


def _resolve_npm_script_body(
    script_name: str, cwd: "Optional[str]" = None,
) -> "Optional[str]":
    """Read ``scripts.<script_name>`` from the package.json under *cwd*.

    Returns the command-body string, or ``None`` when package.json is missing,
    unparseable, has no ``scripts`` map, or lacks a non-empty entry for the
    script -- the caller treats ``None`` as the conservative (T3) case, matching
    the unreadable-script-file default.  Resolution honors *cwd* (the ``cd``
    target of the surrounding chain) when given, else the process cwd -- the
    same convention the script-file lane uses for a relative path token, so
    ``cd /repo && npm run build`` reads ``/repo/package.json``.
    """
    import os

    data = _read_package_json(cwd if cwd is not None else os.getcwd())
    scripts = data.get("scripts") if data is not None else None
    if not isinstance(scripts, dict):
        return None
    body = scripts.get(script_name)
    if not isinstance(body, str) or not body.strip():
        return None
    return body


def _read_package_json(project_dir: str) -> "Optional[dict]":
    """Return the ``package.json`` object in *project_dir*, or ``None`` when it
    is missing, unreadable, or not a JSON object."""
    import os
    import json

    try:
        with open(os.path.join(project_dir, "package.json"), "r",
                  encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _extract_dir_override(
    tokens: "Tuple[str, ...]", flags: FrozenSet[str],
) -> "Optional[str]":
    """Return the directory value of a package manager's own cwd option.

    npm's ``--prefix <dir>`` (or ``-C <dir>``), pnpm's ``--dir``/``-C`` and
    bun's and yarn's ``--cwd`` make the manager read ``package.json`` from
    *dir* instead of the invoking cwd -- exactly how a wrapped dev loop runs a
    workspace's scripts from an arbitrary launch directory (``npm run build
    --prefix /path/to/repo``).  Without this, the option is invisible to the
    classifier: package.json resolution silently falls back to ``os.getcwd()``
    (the hook's own cwd, typically the monorepo root), so the script body is
    read from the WRONG package.json (or none at all).

    Recognizes both the space form (``--prefix /dir``) and the inline
    ``--prefix=/dir`` form, and either flag position -- before or after the
    subcommand. When the option repeats, the last value wins, as the managers
    read it; after ``--`` the options belong to the script. Scanning the raw
    ``tokens`` (not ``non_flag_tokens``) preserves the directory's original
    case, which matters on case-sensitive filesystems. Returns ``None`` when no
    flag is present or its last occurrence has no value.
    """
    override: "Optional[str]" = None
    for i, tok in enumerate(tokens):
        if tok == "--":
            break
        flag, sep, value = tok.partition("=")
        if flag not in flags:
            continue
        if sep:
            override = value or None
        else:
            override = tokens[i + 1] if i + 1 < len(tokens) else None
    return override


_PACKAGE_MANAGERS: FrozenSet[str] = frozenset({"npm", "bun", "pnpm", "yarn"})

# Options that consume the next token, so it is not read as the subcommand or
# the script name, and the subset naming the directory that holds package.json.
_PACKAGE_MANAGER_VALUE_FLAGS: Dict[str, FrozenSet[str]] = {
    "npm": frozenset({"--prefix", "-C", "--workspace", "-w"}),
    "bun": frozenset({"--cwd", "--filter", "-F"}),
    "pnpm": frozenset({"--dir", "-C", "--filter", "-F"}),
    "yarn": frozenset({"--cwd"}),
}
_PACKAGE_MANAGER_DIR_FLAGS: Dict[str, FrozenSet[str]] = {
    "npm": frozenset({"--prefix", "-C"}),
    "bun": frozenset({"--cwd"}),
    "pnpm": frozenset({"--dir", "-C"}),
    "yarn": frozenset({"--cwd"}),
}

# Commands that fetch a ``create-<x>`` package from the registry and run it;
# ``npm init`` does so only when given an initializer.
_INITIALIZER_SUBCOMMANDS: Dict[str, FrozenSet[str]] = {
    "npm": frozenset({"init", "innit", "create"}),
    "bun": frozenset({"create", "c"}),
    "pnpm": frozenset({"create"}),
    "yarn": frozenset({"create"}),
}

# Other spellings of ``install``: npm's typo aliases, ``add``, and ``it``/
# ``install-test`` (an install followed by the test script); bare ``yarn``
# (``None``) is ``yarn install``.
_INSTALL_ALIASES: Dict[str, FrozenSet[Optional[str]]] = {
    "npm": frozenset({
        "i", "in", "ins", "inst", "insta", "instal",
        "isnt", "isnta", "isntal", "isntall", "add", "it", "install-test",
    }),
    "bun": frozenset({"i", "add", "a"}),
    "pnpm": frozenset({"i", "add"}),
    "yarn": frozenset({None, "add"}),
}

# ``npm ci`` followed by the test script.
_NPM_CI_TEST_COMMANDS: FrozenSet[str] = frozenset({
    "cit", "install-ci-test", "clean-install-test", "sit",
})

# Subcommands that install exactly what the lockfile pins, and the flags of
# which one must be present for them to refuse to rewrite it.
_FROZEN_INSTALL_FORMS: Dict[str, Tuple[FrozenSet[Optional[str]], FrozenSet[str]]] = {
    "npm": (
        frozenset({"ci", "clean-install", "ic", "install-clean", "isntall-clean"})
        | _NPM_CI_TEST_COMMANDS,
        frozenset(),
    ),
    "bun": (frozenset({"install", "i"}), frozenset({"--frozen-lockfile"})),
    "pnpm": (frozenset({"install", "i"}), frozenset({"--frozen-lockfile"})),
    "yarn": (frozenset({None, "install"}), frozenset({"--immutable", "--frozen-lockfile"})),
}

_INSTALL_LIFECYCLE_SCRIPTS: Tuple[str, ...] = (
    "preinstall", "install", "postinstall", "preprepare", "prepare", "postprepare",
)

# The npm commands that run the script of a fixed name; any other script needs
# ``npm run``.
_NPM_SCRIPT_COMMANDS: Dict[str, str] = {
    "test": "test", "t": "test", "tst": "test",
    "start": "start", "stop": "stop", "restart": "restart",
}

# Built-in commands of the managers whose ``<manager> <name>`` runs the script
# of that name.  A builtin wins over a script that shares its name, so the
# shorthand applies only outside this set.  pnpm's and yarn's ``test``/
# ``start``/``stop``/``restart`` run scripts and are deliberately absent;
# ``bun test`` is bun's own test runner.
_SCRIPT_SHORTHAND_BUILTINS: Dict[str, FrozenSet[str]] = {
    "bun": frozenset({
        "a", "add", "audit", "build", "c", "create", "exec", "i", "info",
        "init", "install", "link", "outdated", "patch", "patch-commit", "pm",
        "publish", "remove", "repl", "rm", "run", "test", "unlink", "update",
        "upgrade", "why", "x",
    }),
    "pnpm": frozenset({
        "access", "add", "adduser", "approve-builds", "audit", "bin", "bugs",
        "c", "cache", "cat-file", "cat-index", "ci", "completion", "config",
        "create", "dedupe", "deploy", "deprecate", "dist-tag", "dlx", "docs",
        "doctor", "edit", "env", "exec", "fetch", "find-hash", "get", "help",
        "i", "ignored-builds", "import", "info", "init", "install",
        "install-test", "it", "licenses", "link", "list", "ll", "ln", "login",
        "logout", "ls", "m", "multi", "outdated", "owner", "pack", "patch",
        "patch-commit", "patch-remove", "ping", "pkg", "prefix", "profile",
        "prune", "publish", "rb", "rebuild", "recursive", "remove", "repo",
        "rm", "root", "run", "run-script", "s", "se", "search", "self-update",
        "server", "set", "set-script", "setup", "star", "stars", "store",
        "team", "token", "un", "uninstall", "unlink", "unpublish", "unstar",
        "up", "update", "upgrade", "version", "view", "whoami", "why",
    }),
    "yarn": frozenset({
        "add", "audit", "autoclean", "bin", "cache", "check", "config",
        "constraints", "create", "dedupe", "dlx", "exec", "explain",
        "generate-lock-entry", "global", "help", "import", "info", "init",
        "install", "licenses", "link", "list", "login", "logout", "node",
        "npm", "outdated", "owner", "pack", "patch", "patch-commit", "plugin",
        "policies", "publish", "rebuild", "remove", "run", "search", "set",
        "stage", "tag", "team", "unlink", "unplug", "up", "upgrade",
        "upgrade-interactive", "version", "versions", "why", "workspace",
        "workspaces",
    }),
}


def _package_manager_positionals(
    manager: str, tokens: "Tuple[str, ...]",
) -> "List[Tuple[int, str]]":
    """``(index, token)`` of each positional before ``--``, skipping options
    and the values of ``_PACKAGE_MANAGER_VALUE_FLAGS``."""
    value_flags = _PACKAGE_MANAGER_VALUE_FLAGS[manager]
    positionals: "List[Tuple[int, str]]" = []
    i = 1
    while i < len(tokens) and tokens[i] != "--":
        if tokens[i].startswith("-"):
            i += 2 if tokens[i] in value_flags else 1
            continue
        positionals.append((i, tokens[i]))
        i += 1
    return positionals


def _classify_package_script_body(
    manager: str, script_name: str, body: str, family: str,
    cwd: "Optional[str]", _depth: int,
) -> MutativeResult:
    """Classify a ``package.json`` script body with the shell/regex engine."""
    # Split into per-clause commands (a mutation may live in any clause of
    # `tsc && rm -rf dist`) and feed the existing engine.
    segments = "\n".join(
        seg for seg in _SHELL_SEGMENT_SPLIT_RE.split(body) if seg.strip()
    )
    inner = _classify_script_content_by_regex(
        segments, f"package.json:scripts.{script_name}", family, cwd=cwd,
        _depth=_depth + 1,
    )
    return MutativeResult(
        is_mutative=inner.is_mutative,
        category=inner.category,
        verb=inner.verb,
        dangerous_flags=inner.dangerous_flags,
        cli_family=family,
        confidence=inner.confidence,
        reason=(
            f"`{manager}` script {script_name!r} resolved to body {body!r}: "
            f"{inner.reason}"
        ),
    )


def _classify_package_script(
    manager: str, script_name: str, family: str,
    cwd: "Optional[str]", _depth: int,
) -> MutativeResult:
    """Classify ``<manager> run <script>`` by its body and by the ``pre<script>``
    and ``post<script>`` bodies run around it; an unresolvable script is T3."""
    # Budget exhausted: stop descending AND retain -- see the constant.
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result(f"{manager} script body")

    body = _resolve_npm_script_body(script_name, cwd=cwd)
    if body is not None:
        for hook in (f"pre{script_name}", f"post{script_name}"):
            hook_body = _resolve_npm_script_body(hook, cwd=cwd)
            if hook_body is None:
                continue
            result = _classify_package_script_body(manager, hook, hook_body, family, cwd, _depth)
            if result.is_mutative:
                return result
    if body is None:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb=f"{manager}-run-unresolved",
            cli_family=family,
            confidence="medium",
            reason=(
                f"`{manager} run {script_name}` could not be resolved to a "
                f"package.json script body -- cannot verify the payload, "
                f"requiring approval (conservative default)"
            ),
        )
    return _classify_package_script_body(
        manager, script_name, body, family, cwd, _depth,
    )


def _classify_frozen_install(
    manager: str, family: str, cwd: "Optional[str]", _depth: int,
) -> MutativeResult:
    """Classify a lockfile-frozen install by the project's install lifecycle
    scripts; with no readable package.json it is T3."""
    import os

    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return _budget_exhausted_result(f"{manager} lifecycle script body")

    manifest = _read_package_json(cwd if cwd is not None else os.getcwd())
    if manifest is None:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="frozen-install-unresolved",
            cli_family=family,
            confidence="medium",
            reason=(
                f"`{manager}` frozen install found no readable package.json -- "
                f"cannot verify the lifecycle scripts it runs (conservative default)"
            ),
        )
    scripts = manifest.get("scripts")
    scripts = scripts if isinstance(scripts, dict) else {}
    for name in _INSTALL_LIFECYCLE_SCRIPTS:
        body = scripts.get(name)
        if not isinstance(body, str) or not body.strip():
            continue
        result = _classify_package_script_body(manager, name, body, family, cwd, _depth)
        if result.is_mutative:
            return replace(result, rule="sign.lifecycle-body-verb")
    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_UNKNOWN,
        verb="frozen-install",
        cli_family=family,
        confidence="medium",
        reason=(
            f"`{manager}` frozen install fetches only what the lockfile pins, and "
            f"no install lifecycle script of the project mutates"
        ),
    )


def _check_package_manager(
    base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify npm/bun/pnpm/yarn by where the code they run comes from.

    * ``run <script>``, npm's ``test``/``start``/``stop``/``restart``, and the
      bun, pnpm and yarn ``<script>`` shorthand outside their builtins classify
      by the script's ``package.json`` body.
    * An option written ``--flag=false`` (or ``=0``/``=no``) counts as absent.
    * A frozen install (``_FROZEN_INSTALL_FORMS``) classifies by the project's
      install lifecycle scripts.
    * Any other spelling of ``install`` (``_INSTALL_ALIASES``) is re-classified
      as ``<manager> install ...`` so it answers exactly like that form.
    * The manager's own directory option (``--prefix``, ``--dir``, ``--cwd``)
      overrides *cwd* for package.json resolution, as a leading ``cd`` does.

    Returns ``None`` for any other form so ordinary detection continues.
    """
    if base_cmd not in _PACKAGE_MANAGERS:
        return None

    tokens = semantics.tokens
    positionals = _package_manager_positionals(base_cmd, tokens)
    flags = set()
    for token in tokens[1:]:
        name, sep, value = token.partition("=")
        if token.startswith("-") and not (sep and value.lower() in ("false", "0", "no")):
            flags.add(name)
    sub_index, sub = positionals[0] if positionals else (None, None)
    args = [token for _, token in positionals[1:]]

    if sub in _INITIALIZER_SUBCOMMANDS[base_cmd] and (args or sub not in ("init", "innit")):
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="registry-fetch",
            cli_family=family,
            confidence="medium",
            reason=f"'{base_cmd} {sub}' fetches and runs an initializer package",
        )

    override = _extract_dir_override(tokens, _PACKAGE_MANAGER_DIR_FLAGS[base_cmd])
    if override:
        cwd = _resolve_dir_against_cwd(cwd, override)

    frozen_subs, frozen_flags = _FROZEN_INSTALL_FORMS[base_cmd]
    if (
        sub in frozen_subs and not args and not flags & {"-g", "--global"}
        and (not frozen_flags or flags & frozen_flags)
    ):
        frozen = _classify_frozen_install(base_cmd, family, cwd, _depth)
        if (
            not frozen.is_mutative and base_cmd == "npm" and sub in _NPM_CI_TEST_COMMANDS
            and _resolve_npm_script_body("test", cwd=cwd) is not None
        ):
            return _classify_package_script(base_cmd, "test", family, cwd, _depth)
        return frozen

    if sub in _INSTALL_ALIASES[base_cmd]:
        import shlex
        from dataclasses import replace

        canonical = list(tokens)
        if sub_index is None:
            canonical.insert(1, "install")
        else:
            canonical[sub_index] = "install"
        rewritten = " ".join(shlex.quote(t) for t in canonical)
        inner = detect_mutative_command(rewritten, cwd=cwd, _depth=_depth + 1)
        return replace(inner, reason=f"'{sub or base_cmd}' is '{rewritten}': {inner.reason}")

    if sub in ("run", "run-script"):
        # `<manager> run` with no script name lists the scripts -- read-only.
        if not args:
            return None
        return _classify_package_script(base_cmd, args[0], family, cwd, _depth)

    # Without the script npm falls back to its own default (`node server.js`
    # for start), which ordinary detection keeps judging as before.
    npm_script = _NPM_SCRIPT_COMMANDS.get(sub) if base_cmd == "npm" else None
    if npm_script is not None and _resolve_npm_script_body(npm_script, cwd=cwd) is not None:
        return _classify_package_script(base_cmd, npm_script, family, cwd, _depth)

    builtins = _SCRIPT_SHORTHAND_BUILTINS.get(base_cmd)
    if (
        builtins is not None and sub is not None and sub not in builtins
        and _resolve_npm_script_body(sub, cwd=cwd) is not None
    ):
        return _classify_package_script(base_cmd, sub, family, cwd, _depth)
    return None


def _check_inline_code(
    command: str, base_cmd: str, family: str, skip_length_check: bool = False,
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> MutativeResult:
    """Check inline code for dangerous patterns.

    Pipeline:

    Layer 1: Extract string literals from inline code and check them against
             ``blocked_commands`` (catches embedded shell commands like ``rm -rf /``).

    Layer 2 (Python only): Parse the payload with ``ast.parse`` and walk
             ``Call`` nodes.  ``import subprocess; print('hi')`` is **not**
             flagged because ``subprocess.run`` is never invoked, while
             ``subprocess.run([...])`` is.  Python that does not parse is
             UNKNOWN and asks.

    Layer 2b: Universal regex scan for dangerous API keywords (non-Python
             interpreters).

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
    def argv_is_mutative(argv: Tuple[str, ...]) -> bool:
        return _argv_command_is_mutative(argv, cwd=cwd, _depth=_depth + 1)

    unparsed_source = ""
    if base_cmd in _UNPARSED_CODE_INTERPRETERS and not _prints_literals_only(
        _inline_code_text(command, base_cmd, ""),
    ):
        unparsed_source = f"Inline {base_cmd} code"
    if base_cmd in _PYTHON_INTERPRETERS and _analyze_python_inline is not None:
        payload = _extract_python_payload(command, base_cmd)
        if payload:
            ast_result = _analyze_python_inline(payload, argv_is_mutative)
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
                    rule="sign.foreign-code" if ast_result.category == "DYNAMIC_CODE" else "",
                )
            if ast_result.parse_failed:
                return _unparsed_code(family, "Inline Python that does not parse")
            directory = None if _python_skips_local_directory(command) else Path(cwd or Path.cwd())
            foreign = _python_foreign_module(payload, directory, family, cwd, _depth)
            if foreign is not None:
                return foreign
            if not _parse_finds_no_effect(payload, argv_is_mutative):
                return _uncatalogued_python_effect(family, "Inline Python")
            return _layer3_length_check(
                command, base_cmd, family, skip_length_check, argv_is_mutative,
            )

    # Layer 2a, shared with the script-file code lane so the two cannot diverge
    # on exec sinks; a JS backtick is a template literal, not a command.
    is_js = base_cmd in _JS_INLINE_INTERPRETERS
    if is_js and _strip_source is not None and _spec_for_script is not None:
        spec = _spec_for_script("node", "")
        closure = _classify_js_import_closure(
            str(Path(cwd or Path.cwd(), "[inline]")),
            _strip_source(_inline_code_text(command, base_cmd, ""), spec),
            family, spec, cwd=cwd, _depth=_depth,
        )
        if closure is not None:
            return closure
    sink_result =_scan_exec_sink_string_args(command, family, shell_backticks=not is_js, cwd=cwd, _depth=_depth)
    if sink_result is not None and sink_result.is_mutative:
        return sink_result

    # ---- Layer 2b: Universal dangerous API keyword patterns ----
    for pattern, label, category in _UNIVERSAL_DANGEROUS_PATTERNS:
        if is_js and label == "backtick-exec":
            continue
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
    heuristic = _layer3_full_check(command, base_cmd, family, skip_length_check)
    if heuristic.is_mutative:
        return heuristic
    if unparsed_source:
        return _unparsed_code(family, unparsed_source)
    if is_js and _js_hidden_effect(_inline_code_text(command, base_cmd, command)):
        return _js_hidden_effect_result(family, "Inline JavaScript")
    return heuristic


def _layer3_full_check(
    command: str, base_cmd: str, family: str, skip_length_check: bool,
) -> MutativeResult:
    """Run heuristic checks (3a suspicious patterns + 3b length).

    Used as the tail for non-Python interpreters -- the regex layer needs the
    suspicious-pattern fallback to keep network/encoding heuristics live.
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


def _uncatalogued_python_effect(family: str, source: str) -> MutativeResult:
    """The verdict for Python that parses clean of catalogued calls yet carries an effect the catalog does not name."""
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="uncatalogued-effect",
        cli_family=family,
        confidence="medium",
        reason=(
            f"{source} carries an effect the call catalog does not name (a network, process or "
            "temp-file module, a write through a bound handle, a SQL write, or a name chosen at runtime)"
        ),
    )


def _unparsed_code(family: str, source: str) -> MutativeResult:
    """The verdict for code no parser here can read: its effect is unknown, so it asks."""
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_UNKNOWN,
        verb="unparsed-code",
        cli_family=family,
        confidence="low",
        reason=f"{source} is not parsed here, so what it does is unknown",
        rule="unknown.dynamic-span",
    )


def _prints_literals_only(code: str) -> bool:
    """True when every statement of ruby/perl/php *code* only prints literals (``_PRINT_ONLY_STATEMENT_RE``)."""
    code = _re.sub(r"^\s*<\?php\b|\?>\s*$", "", code.strip())
    return bool(code) and all(_PRINT_ONLY_STATEMENT_RE.fullmatch(part) for part in _re.split(r"[;\n]", code))


def _js_hidden_effect(code: str, calls_view: "Optional[str]" = None) -> bool:
    """True when JavaScript *code* (call shapes read in *calls_view*, strings blanked) reaches an unseen effect."""
    calls = code if calls_view is None else calls_view
    if _JS_NETWORK_MODULE_RE.search(code) or _JS_HIDDEN_CALL_RE.search(calls):
        return True
    if _JS_RUNTIME_MODULE_RE.search(code) or _JS_RUNTIME_EFFECT_RE.search(calls):
        return True
    names = {match.group(1) or match.group(2) for match in _JS_EFFECT_BINDING_RE.finditer(code)}
    if any(_re.search(rf"\b{_re.escape(name)}\s*\[", calls) for name in names):
        return True
    if calls_view is None and _strip_source is not None and _spec_for_script is not None:
        stripped = _strip_source(code, _spec_for_script("node", ""))
        code, calls = stripped.exec_view, stripped.verb_view
    return _js_effect_module_unproven(code, calls)


# fs, fs/promises and child_process run free only through members named here: an fs read
# or mkdir (free as shell mkdir is), or a sink whose literal command the exec-sink scan judges.
_JS_FS_READ_MEMBERS: FrozenSet[str] = frozenset({
    "mkdirSync", "mkdir", "readFileSync", "readFile", "readdirSync", "readdir", "existsSync", "exists", "statSync", "stat",
    "lstatSync", "lstat", "accessSync", "access", "realpathSync", "realpath", "readlinkSync", "readlink",
    "createReadStream", "opendirSync", "opendir", "constants",
})
_JS_CHILD_PROCESS_SINKS: FrozenSet[str] = frozenset({
    "execSync", "execFileSync", "execFile", "spawnSync", "spawn", "exec",
})
_JS_EFFECT_SPECIFIER = r"""(?P<q>['"`])(?:node:)?(?P<module>fs|fs/promises|child_process)(?P=q)"""
_JS_REQUIRE_RE = _re.compile(r"\brequire\s*(?:\?\.\s*)?\(\s*" + _JS_EFFECT_SPECIFIER + r"\s*\)")
_JS_STATIC_IMPORT_RE = _re.compile(r"\bimport\s+([^'\"`;]+?)\s+from\s*" + _JS_EFFECT_SPECIFIER)
_JS_ALIAS_DECLARATION_RE = _re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?$")
_JS_DESTRUCTURE_DECLARATION_RE = _re.compile(r"\b(?:const|let|var)\s*\{([^{}]*)\}\s*=\s*(?:await\s+)?$")
_JS_MEMBER_RE = _re.compile(r"\s*\.\s*(?:promises\s*\.\s*)?([A-Za-z_$][\w$]*)")
_JS_ENV_WRITE_RE = _re.compile(
    r"\bprocess\s*\.\s*env\s*(?:\.\s*[\w$]+|\[[^\]]*\])\s*(?:=(?!=)|\+=)"
    r"|\bObject\s*\.\s*assign\s*\(\s*process\s*\.\s*env\b|\bdelete\s+process\s*\.\s*env\b"
)


def _js_effect_module_unproven(code: str, calls: str) -> bool:
    """True when *code* binds fs, fs/promises or child_process to anything but known members, or sets ``process.env`` before a child process."""
    if _JS_ENV_WRITE_RE.search(calls) and _re.search(r"child_process", code):
        return True
    aliases: "List[Tuple[str, str]]" = []
    for match in _JS_REQUIRE_RE.finditer(code):
        module, before = match.group("module"), code[max(0, match.start() - 400):match.start()]
        alias = _JS_ALIAS_DECLARATION_RE.search(before)
        destructured = _JS_DESTRUCTURE_DECLARATION_RE.search(before)
        member = _JS_MEMBER_RE.match(code, match.end())
        if member:
            if not _js_member_known(member.group(1), module):
                return True
        elif alias:
            aliases.append((alias.group(1), module))
        elif destructured:
            if not _js_destructured_members_known(destructured.group(1), module, ":"):
                return True
        else:
            return True
    for match in _JS_STATIC_IMPORT_RE.finditer(code):
        clause, module = match.group(1), match.group("module")
        braces = _re.search(r"\{([^{}]*)\}", clause)
        if braces and not _js_destructured_members_known(braces.group(1), module, " as "):
            return True
        bare = _re.sub(r"\{[^{}]*\}|\*\s*as\b|,", " ", clause).split()
        aliases.extend((name, module) for name in bare)
    for name, module in aliases:
        for use in _re.finditer(rf"(?<![\w$.]){_re.escape(name)}(?![\w$])", calls):
            if _re.search(r"\b(?:const|let|var|import|as)\s+$", calls[max(0, use.start() - 16):use.start()]):
                continue
            member = _JS_MEMBER_RE.match(calls, use.end())
            if not (member and _js_member_known(member.group(1), module)):
                return True
    return False


def _js_member_known(member: str, module: str) -> bool:
    return member in (_JS_CHILD_PROCESS_SINKS if module == "child_process" else _JS_FS_READ_MEMBERS)


def _js_destructured_members_known(entries: str, module: str, rename: str) -> bool:
    """True when every destructured entry is a known member; a child_process sink must keep its name for the sink scan."""
    for entry in filter(None, (part.strip() for part in entries.split(","))):
        key, renamed, _ = entry.partition(rename)
        key = key.strip()
        if key.startswith("...") or not _js_member_known(key, module) or (renamed and module == "child_process"):
            return False
    return True


def _js_hidden_effect_result(family: str, source: str) -> MutativeResult:
    return MutativeResult(
        is_mutative=True,
        category=CATEGORY_MUTATIVE,
        verb="js-hidden-effect",
        cli_family=family,
        confidence="medium",
        reason=(
            f"{source} builds code at runtime, opens a network module, indexes an effect "
            "module by a computed name, or sends a request body"
        ),
    )


def _inline_code_text(command: str, base_cmd: str, fallback: str) -> str:
    """The code an interpreter's inline flag hands it, unquoted as the shell passes it, else *fallback*."""
    from ..tools.shell_parser import program_argv

    words = program_argv(command)
    flags = _INLINE_CODE_MAP.get(base_cmd, frozenset())
    for index, word in enumerate(words[:-1]):
        if word in flags:
            return words[index + 1]
    return fallback


def _long_code_judged_by_content(
    command: str, code_portion: str, base_cmd: str,
    argv_is_mutative: "Optional[Callable[[Tuple[str, ...]], bool]]",
) -> bool:
    """True when long Python or JavaScript inline code shows no effect, so its content outranks its length."""
    if any(pattern.search(command) for pattern, _ in _SUSPICIOUS_HEURISTICS):
        return False
    if base_cmd in _PYTHON_INTERPRETERS:
        return _parse_finds_no_effect is not None and _parse_finds_no_effect(
            _extract_python_payload(command, base_cmd), argv_is_mutative,
        )
    return base_cmd in _JS_INLINE_INTERPRETERS and not _JS_UNSEEN_EFFECT_RE.search(
        _inline_code_text(command, base_cmd, code_portion),
    )


def _layer3_length_check(
    command: str, base_cmd: str, family: str, skip_length_check: bool,
    argv_is_mutative: "Optional[Callable[[Tuple[str, ...]], bool]]" = None,
) -> MutativeResult:
    """Run only the length heuristic (3b), which yields to a content judgment that finds no effect."""
    code_portion = command
    cli_flag_tokens = _INLINE_CODE_MAP.get(base_cmd, frozenset())
    for flag in cli_flag_tokens:
        idx = command.find(f" {flag} ")
        if idx != -1:
            code_portion = command[idx + len(flag) + 2:]
            break

    if not skip_length_check and len(code_portion) > MAX_NORMAL_INLINE_LENGTH:
        if _long_code_judged_by_content(command, code_portion, base_cmd, argv_is_mutative):
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb="inline-code-readonly",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"Inline {base_cmd} code is long ({len(code_portion)} chars) "
                    "but its content judgment finds no effect"
                ),
            )
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

    Returns a CLI-agnostic internal dict ('decision' + 'message'). The adapter
    layer is responsible for formatting the 'message' into the host-specific
    deny response; this business module never assembles that host shape itself.
    The 'decision' key is internal only and never sent to the host.

    Args:
        command: The original shell command.
        danger: MutativeResult from detect_mutative_command.
        nonce: The approval_id this block asks the user to approve. When provided,
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
