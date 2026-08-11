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
from typing import Dict, FrozenSet, List, Optional, Tuple, Union

from .approval_messages import build_t3_approval_instructions
from .command_semantics import CommandSemantics, analyze_command

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
    from .inline_ast_analyzer import (
        is_provably_read_only_python as _is_provably_read_only_python,
    )
except ImportError:  # pragma: no cover -- defensive
    _analyze_python_inline = None
    _is_provably_read_only_python = None
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
})


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
    # `gaia notifications <verb>` (add/list/show/ack): the headless scheduled-task
    # inbox in gaia.db — episodic, reversible, purely local bookkeeping (ack only
    # flips an `unread` flag; add appends a report row). A headless task MUST be
    # able to `notifications add` its final report without stalling on a T3 gate
    # (it cannot ask the user anything), so the whole group is T0 like brief/ac/
    # plan/task. There is no destructive verb here (no delete/purge), so the
    # global deny-verb guard leaves every notifications verb exempt.
    ("gaia", "notifications"): CATEGORY_READ_ONLY,
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
    # `gaia schedule <verb>` -- the scheduled-task DESIRED-STATE registry in
    # gaia.db (see the `scheduled-task` skill and the scheduled_tasks table).
    # register/add/list/show/status/enable/disable/suspend/resume are reversible
    # local bookkeeping on the desired state -- they never touch the machine
    # scheduler, so they are T0 like brief/plan/task/notifications. WITHOUT this
    # exception `register`, `enable`, `disable`, `suspend` and `resume` would trip
    # the generic MUTATIVE_VERBS scan (all five are in MUTATIVE_VERBS) and gate on
    # every desired-state edit.
    #
    # `suspend` and `resume` sit at T0 for the same reason `disable` and `enable`
    # do, and the pairing is the justification. `suspend` only switches something
    # OFF: it reduces what runs, which is the direction that never needs consent.
    # `resume` does restore capability -- but only in gaia.db, and nothing runs
    # because a row says it should: the task reaches this machine's scheduler
    # exclusively through `sync`, which is T3. So the consent boundary stays where
    # the design put it, at MATERIALIZATION, and is not duplicated onto every
    # bookkeeping edit. Gating `resume` while leaving `enable` free would also be
    # incoherent, since `enable` restores strictly more (it has no deadline).
    # The TWO verbs that reach outside the DB stay T3 via the per-group deny set
    # below: `sync` MATERIALIZES desired state into the OS scheduler (writes the
    # crontab -- a real machine mutation that must be shown verbatim and
    # consented) and `remove` is irreversible row deletion (like `gaia task
    # remove`). Writing desired state is cheap; imprinting it on the machine
    # requires consent -- that asymmetry is the whole design.
    ("gaia", "schedule"): CATEGORY_READ_ONLY,
    # `gaia memory <verb>` (add/edit/append/reclassify/link/checkpoint/search/
    # show/list/stats/conflicts): curated-memory bookkeeping in gaia.db --
    # reversible, local-only, no external effects, exactly like brief/ac/plan.
    # Two false positives motivated this, both measured: `edit` is a generic
    # MUTATIVE_VERB, so EVERY `gaia memory edit <id>` demanded T3 to correct a
    # note; and the verb scan reads the ATOM'S OWN TEXT, so a payload that is
    # itself a mutative word (`--body apply`) gated the write on the content
    # of the note. An atom body is data -- no verb spelled inside it executes.
    # `gaia memory delete` stays T3 through the global deny-verb guard
    # (tombstoning a curated atom is the one destructive verb in this group),
    # and the orthogonal subagent_memory_write_guard still denies memory
    # WRITES from a dispatched subagent regardless of tier -- this exception
    # changes the tier, never who is allowed to write.
    ("gaia", "memory"): CATEGORY_READ_ONLY,
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
    # `gaia schedule` is exempted to T0 for desired-state bookkeeping (above),
    # but two verbs must stay gated within that exception:
    #   - `sync`   MATERIALIZES desired state into the OS scheduler (writes the
    #              user's crontab via `crontab -`). That is a real machine
    #              mutation, so it must be shown verbatim and consented (T3).
    #   - `remove` is irreversible desired-state row deletion (the reversible
    #              path is `disable`), like `gaia task remove`.
    # Both are already generic MUTATIVE_VERBS, so without re-gating them here the
    # group exception would silently downgrade them to T0.
    ("gaia", "schedule"): frozenset({"sync", "remove"}),
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

    An empty ``path`` is refused. It would anchor a whole CLI on a flag alone —
    the global behaviour this model exists to replace; a CLI-wide condition is
    spelled out as one anchor per subcommand that warrants it. Tokens and flags
    must be lowercase because the classifier lowercases what it matches against:
    an uppercase entry could never fire, and a declaration that reads as
    coverage while classifying nothing is worse than no declaration at all.
    """

    path: Tuple[str, ...]
    flags: FrozenSet[str] = frozenset()

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
        for flag in self.flags:
            if not flag or flag != flag.lower():
                raise ValueError(
                    f"MutativeAnchor.flags entry {flag!r} must be non-empty lowercase"
                )

    def matched_flags(self, semantics: CommandSemantics) -> Optional[Tuple[str, ...]]:
        """Return the flags that satisfied this anchor, or None if it does not fire.

        An empty tuple is a MATCH by path alone, so callers must test against
        ``None`` rather than for truthiness.
        """
        if semantics.non_flag_tokens[:len(self.path)] != self.path:
            return None
        if not self.flags:
            return ()
        present = self.flags.intersection(semantics.flag_tokens)
        return tuple(sorted(present)) if present else None


def _anchor_shadows(earlier: MutativeAnchor, later: MutativeAnchor) -> bool:
    """Does ``earlier`` answer every command ``later`` could ever match?

    Anchors of one CLI are tried in declaration order and the first match
    returns, so ``later`` is reachable only while some command matches it and
    NOT ``earlier``. Two conditions together make that impossible:

    * ``earlier``'s path is a prefix of ``later``'s -- matching is by prefix,
      and an equal path is a prefix of itself; and
    * ``earlier``'s condition is no narrower. An unflagged ``earlier`` fires on
      the path alone, so it swallows everything beneath it. A flagged one
      swallows ``later`` only when every flag that could fire ``later`` also
      fires ``earlier`` -- which is why an unflagged ``later`` under a flagged
      ``earlier`` survives: it still answers the commands that carry no flag.
    """
    if later.path[:len(earlier.path)] != earlier.path:
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
        # `gaia scan --workspace <name>` (write mode) UPSERTs (workspace,
        # project) rows into gaia.db and promotes the resolved workspace -- a
        # persistent DB mutation. Like `context`, `scan` carries no verb in
        # MUTATIVE_VERBS, so it would classify READ_ONLY by elimination. `scan`
        # is a flat command with no read-only subcommands (only flags:
        # --workspace, --dry-run, positional root), so the WHOLE group is
        # anchored. The `--dry-run` classify-only mode is NOT re-implemented
        # here: it is a SIMULATION_FLAG resolved by Step 3 above this check.
        MutativeAnchor(path=("scan",)),
    ),
    "gcloud": (
        # Changing an IAM policy binding was gated in ONE direction and on a
        # subset of surfaces, for two independent reasons.
        #
        # `add-iam-policy-binding` hyphen-splits onto `add`, which is
        # deliberately absent from MUTATIVE_VERBS so that `git add` stays free;
        # nothing matched it on any surface. And the hyphen split itself only
        # runs at semantic_index <= 2 (deeper tokens are argument slugs, not
        # subcommands), so on the three-token paths even
        # `remove-iam-policy-binding` sits too deep to reach `remove`. Removal
        # was therefore gated on `projects` and `secrets` alone -- their paths
        # are two tokens -- and granting was gated nowhere.
        #
        # Granting is not the lesser half. It widens whoever receives it, and
        # unlike a removal nothing observable happens until someone uses the
        # capability, so it is the direction more likely to pass unnoticed.
        #
        # Anchored per surface rather than by returning `add` to MUTATIVE_VERBS:
        # `add` in its ordinary form is harmless, and a global entry would tax
        # `git add` and every other CLI that shares the word.
        #
        # `projects remove-iam-policy-binding` and `secrets
        # remove-iam-policy-binding` are absent on purpose -- the verb scan
        # already decides them MUTATIVE, and an anchor that re-decides a form
        # already correct would add a declaration without adding coverage.
        MutativeAnchor(path=("projects", "add-iam-policy-binding")),
        MutativeAnchor(path=("secrets", "add-iam-policy-binding")),
        MutativeAnchor(path=("storage", "buckets", "add-iam-policy-binding")),
        MutativeAnchor(path=("storage", "buckets", "remove-iam-policy-binding")),
        MutativeAnchor(path=("iam", "service-accounts", "add-iam-policy-binding")),
        MutativeAnchor(
            path=("iam", "service-accounts", "remove-iam-policy-binding")
        ),
    ),
})


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
    "node", "ruby", "perl", "php",
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
# Once the descent reaches this cap the body is NO LONGER opened: the invocation
# classifies by ordinary token scan instead (safe by elimination), which breaks
# the cycle deterministically.  Legitimate nesting is only a few levels deep
# (`npm run x` -> `bash a.sh` -> `bash b.sh`), so this cap never truncates a real
# chain; it exists solely to guarantee termination on a self/mutual reference.
_MAX_SCRIPT_RECURSION_DEPTH = 12

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
# Each entry maps the runner to the subcommand token that must follow it
# (``uv run``, ``poetry run``, ``pipx run``) or ``None`` when the runner takes
# the wrapped command directly (``npx <pkg> <args>``).  ``value_flags`` are the
# runner's OWN options that consume the following token as their value; without
# them the value would be mistaken for the wrapped command.  This table is
# inherently OPEN: a runner nobody listed still bypasses the lane (see the
# fallback rationale in ``_check_prefix_runner``).
_PREFIX_RUNNER_SUBCOMMANDS: Dict[str, "Optional[str]"] = {
    "uv": "run",
    "uvx": None,
    "poetry": "run",
    "pipx": "run",
    "npx": None,
}

_PREFIX_RUNNER_VALUE_FLAGS: Dict[str, FrozenSet[str]] = {
    "uv": frozenset({
        "--with", "--with-editable", "--with-requirements", "--python", "-p",
        "--directory", "--project", "--extra", "--index", "--index-url",
        "--refresh-package", "--isolated-package",
    }),
    "uvx": frozenset({
        "--with", "--from", "--python", "-p", "--index", "--index-url",
    }),
    "poetry": frozenset({"--directory", "-C", "--project", "-P"}),
    "pipx": frozenset({"--spec", "--python", "--pip-args", "--index-url"}),
    "npx": frozenset({"--package", "-p", "--node-options", "--userconfig"}),
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

# Composition operators that separate pipeline / statement stages:  ';', '|',
# '&&', '||'.  The cmdlet/verb classification runs PER STAGE on the FIRST
# cmdlet-shaped token only (the command position); a Verb-Noun-shaped PATH or
# FLAG argument ('C:\x\my-folder', 'a-b\file.txt') sits in an ARGUMENT position
# -- AFTER the command in its stage -- so it must not be read as a cmdlet.
# Two-char operators are listed first in the alternation so '||'/'&&' win over
# the single-char '|'.
_PS_STAGE_SPLIT_RE = _re.compile(r"\|\||&&|[;|]")


def _ps_stage_command_cmdlets(payload: str) -> "List[Tuple[str, str]]":
    """Split a PowerShell payload into composition stages and return the
    command-position cmdlet ``(verb, noun)`` of each stage that has one.

    Stages split on ';', '|', '&&', '||' (reusing the composition taxonomy).
    Within a stage, ONLY the FIRST Verb-Noun-shaped token is the command; any
    later Verb-Noun token is an ARGUMENT (a hyphenated path such as 'my-folder'
    or a cmdlet name embedded in a file path) and is intentionally ignored, so
    it can never be misread as a cmdlet.  Taking the first *match* rather than
    the literal first whitespace token also transparently skips a leading
    interpreter wrapper / flags ('powershell.exe -Command "..."') without a
    fragile payload-extraction step.  A stage with no cmdlet-shaped token
    contributes nothing (it is an alias / path / builtin command position).

    Obfuscation / arbitrary-execution markers are NOT filtered here -- they are
    scanned across the WHOLE payload by the caller (``_PS_OBFUSCATION_RES``)
    before this per-stage scan runs.
    """
    cmdlets: "List[Tuple[str, str]]" = []
    for stage in _PS_STAGE_SPLIT_RE.split(payload):
        stage = stage.strip()
        if not stage:
            continue
        m = _PS_CMDLET_RE.search(stage)  # FIRST cmdlet in the stage = the command
        if m:
            cmdlets.append((m.group(1), m.group(2)))
    return cmdlets

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
# `_check_script_file` (unreadable -> T3) and `_check_npm_script_runner`
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
# rm -- scratch-directory tier override (T0 inside Gaia scratch, T3 otherwise)
# ============================================================================
# `rm` (including `rm -rf`) is normally a MUTATIVE command alias requiring T3
# approval, and the truly catastrophic forms (`rm -rf /`, `/*`, `~`) are
# permanently blocked by the `rm_critical` floor in blocked_commands.py, which
# runs BEFORE this detector (bash_validator.py phase 3a).
#
# Narrowly-scoped exception (Option A): `rm` is downgraded to T0 (no approval)
# ONLY when EVERY target path resolves strictly inside the Gaia scratch
# directory (`~/.gaia/scratch`, or the equivalent under a GAIA_DATA_DIR
# override).  Scratch is ephemeral agent working space by design, mirroring the
# way mkdir already treats /tmp and /run as scratch; deleting inside it carries
# no system-state risk.  The floor cooperates via a matching, lenient
# scratch-confinement check in blocked_commands.py so scratch operations reach
# this detector instead of being swallowed by the catastrophic `~` patterns.
#
# The check is STRICT and fail-closed (see _rm_targets_only_scratch): globs,
# `..` traversal, symlinks escaping scratch, or any single out-of-scratch path
# all keep the command at T3.  `-rf` recursion is allowed only when confined
# to scratch.

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


def _rm_targets_only_scratch(tokens: tuple) -> bool:
    """Return True only if every rm target resolves strictly inside scratch.

    STRICT and fail-closed. Returns True only when ALL of the following hold:
      (a) at least one positional (non-flag) path argument is present;
      (b) NO token contains an unexpanded glob metacharacter (``*?[]{}``) or
          a parent-traversal component (``..``);
      (c) each path, after os.path.expanduser + os.path.realpath, is the
          scratch root itself or lives under ``scratch_root + os.sep``.

    Any ambiguity -- no positional path, an unresolvable scratch root, a glob,
    a ``..``, an unexpandable ``~user``, or a single path outside scratch --
    returns False so the command keeps its T3 classification.  realpath (not
    normpath) is used deliberately so a symlink inside scratch that points
    outside is detected and does NOT qualify for the T0 exception.

    Args:
        tokens: Full token tuple from tokenize_command (index 0 is the base
            command and is skipped).

    Returns:
        True  -> all path arguments resolve strictly inside scratch (T0).
        False -> anything else (T3).
    """
    import os
    scratch_root = _gaia_scratch_root()
    if not scratch_root:
        return False

    seen_end_of_opts = False
    path_tokens = []
    i = 1  # skip base_cmd at index 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "--":
            seen_end_of_opts = True
            continue
        if not seen_end_of_opts and token.startswith("-"):
            # rm has no value-consuming short flags relevant here.
            continue
        path_tokens.append(token)

    if not path_tokens:
        return False  # (a) -- no clear path target

    for token in path_tokens:
        # (b) reject unexpanded globs and parent traversal outright.
        if any(ch in _RM_GLOB_CHARS for ch in token):
            return False
        if ".." in token:
            return False
        expanded = os.path.expanduser(token)
        # A residual ~ (unexpandable, e.g. ~unknownuser) is not confined.
        if expanded.startswith("~"):
            return False
        # (c) canonicalise and require strict containment in scratch.
        real = os.path.realpath(expanded)
        if not (real == scratch_root or real.startswith(scratch_root + os.sep)):
            return False

    return True


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
#                  Gaia's central worktrees root and no force signal is present
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


def _gaia_worktrees_root() -> "str | None":
    """Return the canonical (realpath) Gaia worktrees root, or None.

    Reads the location from gaia.paths.resolver.worktrees_dir() so a
    GAIA_DATA_DIR override is honoured, then canonicalises it with
    os.path.realpath.  Resolving at call time (rather than comparing against a
    literal string prefix) is what makes the containment check in
    _git_worktree_recycles_only_managed_root a statement about the real
    directory instead of about the spelling of a path.

    Fail-closed: any failure to import the resolver or resolve the path returns
    None, which makes the worktree exception decline (stay T3).
    """
    import os
    try:
        from gaia.paths.resolver import worktrees_dir
        return os.path.realpath(str(worktrees_dir()))
    except Exception:
        return None


def _git_worktree_positionals(tokens: tuple) -> List[str]:
    """Return the positional tokens of a git command, original case preserved.

    Mirrors the flag grammar analyze_command applies -- a single-letter short
    flag appearing before the first positional consumes the next token as its
    value, so the path in ``git -C /repo worktree remove wt`` is absorbed and
    never mistaken for a subcommand or a removal target.

    semantics.non_flag_tokens cannot be used here: it lowercases every token,
    which corrupts any path with an uppercase component on a case-sensitive
    filesystem and would make the containment check resolve the wrong
    directory.
    """
    positionals: List[str] = []
    skip_next = False
    seen_positional = False
    args = list(tokens[1:])
    for index, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-") and token != "-":
            is_short_value_flag = (
                not token.startswith("--")
                and len(token) == 2
                and token[1].isalpha()
            )
            if (
                not seen_positional
                and is_short_value_flag
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
    """Return True only if a `git worktree remove` recycles inside Gaia's root.

    STRICT and fail-closed.  Returns True only when ALL of the following hold:
      (a) the worktrees root resolves (GAIA_DATA_DIR honoured);
      (b) no force signal is present -- see _git_worktree_has_force_signal;
      (c) at least one removal target is present after ``worktree remove``;
      (d) every target is ABSOLUTE after expanduser.  A relative target is
          refused because ``git -C <repo>`` resolves it against the repo, not
          against the hook's cwd, so its real destination is not knowable here;
      (e) no target carries a glob metacharacter or a ``..`` component;
      (f) every target, after os.path.realpath, lives strictly UNDER
          ``worktrees_root + os.sep``.

    realpath (not normpath) is deliberate: a path fabricated out of symlinks or
    parent-traversal segments is resolved to the directory it actually names
    before containment is tested, so satisfying the text of the prefix is not
    enough to satisfy the check.  Containment is strict -- the root itself is
    not a worktree and does not qualify.

    Any ambiguity returns False, keeping the command at T3.
    """
    import os
    worktrees_root = _gaia_worktrees_root()
    if not worktrees_root:
        return False

    if _git_worktree_has_force_signal(tokens):
        return False

    positionals = _git_worktree_positionals(tokens)
    # positionals[0] == "worktree", positionals[1] == "remove"; targets follow.
    targets = positionals[2:]
    if not targets:
        return False

    for target in targets:
        if any(char in _WORKTREE_GLOB_CHARS for char in target):
            return False
        if ".." in target:
            return False
        expanded = os.path.expanduser(target)
        if not os.path.isabs(expanded):
            return False
        real = os.path.realpath(expanded)
        if not real.startswith(worktrees_root + os.sep):
            return False

    return True


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
                "worktrees root (~/.gaia/worktrees); every target resolves "
                "strictly under the runtime-resolved root via realpath and no "
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

# These CLIs parse help as a global, non-executing request even when the
# subcommand path is deeper than the generic two-token boundary below. Keep
# this list deliberately small: it exists for command trees such as
# ``gcloud identity groups create --help`` where counting the group nouns as
# positional mutation arguments produced a false T3.
HELP_ANY_POSITION_BASE_CMDS: FrozenSet[str] = frozenset({
    "gcloud",
    "terraform",
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
        elif len(token) > 2 and token[0] == "-" and token[1] != "-":
            flag_chars = token[1:]
            if _is_posix_short_flag_cluster(flag_chars):
                if "r" in flag_chars and "f" in flag_chars:
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
    A read-only verb carrying an ALWAYS flag -- e.g. ``git fetch --prune``,
    which deletes stale remote-tracking refs -- is a real mutation that the
    Step 5 scan would catch, but the read-only early-return returns before
    Step 5 is ever reached. Escalating here closes that hole.
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
    An ``env [opts]`` wrapper ahead of the assignments is skipped so
    ``env NAME=$(...) cmd`` is examined exactly like the bare form -- the
    peeler would otherwise discard the substitution unseen.
    Returns ``None`` when a real command follows the assignments so ordinary
    classification can continue after all substitutions have been checked.
    """
    n = len(command)
    saw_assignment = False
    cursor = _skip_env_wrapper(command, 0)
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


# ============================================================================
# Main Detection Function
# ============================================================================

@functools.lru_cache(maxsize=128)
def detect_mutative_command(
    command: str, from_source_code: bool = False, cwd: "Optional[str]" = None,
    _depth: int = 0,
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

    # --- Honor a leading env-var assignment / `env` wrapper prefix ---
    # A shell command may be prefixed with one or more ``NAME=value`` assignments
    # (``GITHUB_TOKEN=$(gh auth token) terragrunt apply``) or wrapped in ``env``
    # (``env FOO=bar terragrunt apply``).  In both forms the REAL command -- and
    # its mutative verb -- follows the prefix, but ``analyze_command`` keys off
    # ``tokens[0]``, which lands on the assignment token (``github_token=$(gh``)
    # or on ``env`` (a read-only base command), so the mutative verb was never
    # scanned and the operation slipped the T3 gate.  Peel the prefix and
    # re-classify the underlying command -- SAME command minus an inert prefix,
    # so ``_depth`` is carried through unchanged (mirrors the ``cd`` peel above).
    env_remainder, env_peeled = _peel_leading_env_prefix(command)
    if env_peeled and env_remainder and env_remainder != command.strip():
        return detect_mutative_command(
            env_remainder, from_source_code=from_source_code, cwd=cwd,
            _depth=_depth,
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

        # rm scratch-directory override: classify as T0 when EVERY target path
        # resolves strictly inside the Gaia scratch directory (~/.gaia/scratch).
        # `-rf` recursion is permitted only within scratch.  Globs, `..`, and
        # symlinks escaping scratch keep T3 (see _rm_targets_only_scratch).
        # The catastrophic floor (rm -rf /, /*, ~) still runs first in
        # blocked_commands.py, which defers to this detector only for
        # scratch-confined rm commands.
        if base_cmd == "rm" and _rm_targets_only_scratch(tuple(tokens)):
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb=base_cmd,
                cli_family="system",
                confidence="high",
                reason=(
                    "rm targeting only the Gaia scratch directory "
                    "(~/.gaia/scratch); all paths resolve strictly inside "
                    "scratch via realpath"
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

    # --- Step 1e: npm script-runner resolution (AC-3) ---
    # `npm run <script>` classifies by the SCRIPT NAME under the verb scanner,
    # but the name is arbitrary: `npm run db-migrate` / `npm ci` bypass consent
    # as SAFE while `npm run start` false-positives.  Resolve `npm run <script>`
    # to its real package.json body and classify THAT (mirroring the script-file
    # lane); `npm ci` is unconditionally mutative; unresolvable -> conservative
    # T3.  Returns None for other npm forms so ordinary detection continues.
    npm_result = _check_npm_script_runner(
        base_cmd, family, semantics, cwd=cwd, _depth=_depth,
    )
    if npm_result is not None:
        return npm_result

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
        # A help flag only counts when it sits BEFORE a literal `--`
        # separator: tokens after `--` are a verbatim payload the CLI passes
        # through (gcloud compute ssh ... -- <remote command> --help runs the
        # remote command; the trailing --help is ssh's argument, not gcloud's).
        if "--" in tokens:
            separator_index = tokens.index("--")
            help_requested = any(
                t in HELP_FLAGS for t in tokens[:separator_index]
            )
        else:
            help_requested = bool(flag_set & HELP_FLAGS)
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
                ),
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
            # An ALWAYS-dangerous flag escalates even a read-only verb: the
            # Step 5 flag scan below never runs for a read-only verb because
            # this early-return fires first, so `git fetch --prune` (fetch is
            # read-only, --prune deletes stale remote-tracking refs) would
            # otherwise skip the ALWAYS escalation entirely. Only ALWAYS flags
            # override here -- CONTEXT flags are family-gated and a benign
            # `-r`/`-f` on a read-only verb must NOT be treated as mutative.
            always_flags = _scan_always_dangerous_flags(tokens)
            if always_flags:
                return MutativeResult(
                    is_mutative=True,
                    category=CATEGORY_MUTATIVE,
                    verb=verb,
                    dangerous_flags=always_flags,
                    cli_family=family,
                    confidence="high",
                    reason=(
                        f"Read-only verb '{verb}' escalated by ALWAYS-dangerous "
                        f"flag(s) {always_flags}"
                    ),
                )
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
) -> "Optional[Tuple[str, Tuple[str, ...]]]":
    """Return ``(wrapped_command_token, remaining_args)`` for a prefix runner.

    Walks the runner's own options -- skipping boolean flags, consuming the
    value of a ``value_flags`` option, and accepting the self-contained
    ``--flag=value`` form -- until the first true positional, which is the
    command the runner will execute.  For a runner that requires a subcommand
    (``uv run``) the subcommand must appear before that positional, otherwise
    this is a DIFFERENT operation of the same CLI (``uv pip install``, ``poetry
    add``) that ordinary verb detection already owns.

    Returns ``None`` whenever the command is not a runner invocation with a
    resolvable payload, so the caller leaves classification unchanged.
    """
    if base_cmd not in _PREFIX_RUNNER_SUBCOMMANDS:
        return None

    required_subcommand = _PREFIX_RUNNER_SUBCOMMANDS[base_cmd]
    value_flags = _PREFIX_RUNNER_VALUE_FLAGS.get(base_cmd, frozenset())
    raw_tokens = semantics.tokens

    subcommand_seen = required_subcommand is None
    i = 1
    while i < len(raw_tokens):
        token = raw_tokens[i]
        if token == "--":
            i += 1
            continue
        if token.startswith("-"):
            if base_cmd == "npx" and token in _NPX_SHELL_CALL_FLAGS:
                # ``npx --call "<shell command>"``: the value is a command
                # string, not a package -- hand it back as the payload with no
                # remaining args so the caller re-classifies it as a command.
                if i + 1 < len(raw_tokens):
                    return (raw_tokens[i + 1], ())
                return None
            if token in value_flags:
                i += 2
                continue
            i += 1
            continue
        if not subcommand_seen:
            if token.lower() != required_subcommand:
                return None
            subcommand_seen = True
            i += 1
            continue
        return (token, tuple(raw_tokens[i + 1:]))

    return None


def _check_prefix_runner(
    base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Re-dispatch ``uv run`` / ``poetry run`` / ``pipx run`` / ``npx`` payloads.

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
    unrecognized runner, and the honest scope of this lane is: the four named
    shapes are inspected; a fifth runner, a project-local wrapper script, or an
    interpreter reached by a path whose basename is not a known interpreter name
    still passes as T0.

    Returns ``None`` when the command is not a resolvable runner invocation.
    """
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return None

    resolved = _resolve_prefix_runner_payload(base_cmd, semantics)
    if resolved is None:
        return None

    payload, rest = resolved

    import shlex

    interpreter = None
    lowered = payload.lower()
    for ext, ext_interpreter in _SCRIPT_EXT_INTERPRETERS.items():
        if lowered.endswith(ext):
            interpreter = ext_interpreter
            break

    tokens = (payload, *rest) if interpreter is None else (interpreter, payload, *rest)
    rewritten = " ".join(shlex.quote(t) for t in tokens)

    inner = detect_mutative_command(rewritten, cwd=cwd, _depth=_depth + 1)
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


def _check_gaia_cli_dispatcher(
    script_path: str, content: str, semantics: "CommandSemantics", family: str,
    _depth: int = 0,
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

    Returns ``None`` when the script is not the gaia dispatcher, so the caller
    continues with ordinary AST/regex script analysis.
    """
    import os

    if os.path.basename(script_path) != "gaia":
        return None
    if os.path.basename(os.path.dirname(script_path)) != "bin":
        return None
    if not all(sig in content for sig in _GAIA_DISPATCHER_SIGNATURE):
        return None

    # Tokens AFTER the script positional are the gaia argv.
    raw_tokens = semantics.tokens
    try:
        idx = raw_tokens.index(script_path)
    except ValueError:
        return None
    sub_args = raw_tokens[idx + 1:]

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
    if sem.base_cmd != "cd":
        return None
    args = [t for t in sem.tokens[1:] if not t.startswith("-")]
    flags = [t for t in sem.tokens[1:] if t.startswith("-")]
    if flags or len(args) != 1:
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

    base = base_cwd if base_cwd is not None else os.getcwd()
    target = os.path.expanduser(target)
    if os.path.isabs(target):
        return os.path.normpath(target)
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
    return cwd if peeled else base_cwd


# A leading shell env-var assignment:  NAME=  (value consumed separately, so it
# may be empty, a bare word, a quoted string, or a $(...)/`...` substitution).
# Anchored at the scan cursor -- a `=` inside a LATER argument is never matched
# because the cursor only ever sits at the start of the command or immediately
# after a fully-consumed prior assignment / peeled `env` token.
_ENV_ASSIGN_PREFIX_RE = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

# `env`-wrapper option flags.  Boolean flags carry no value; value flags consume
# the following token (short form) as their argument.  Long `--opt=value` forms
# are self-contained and handled inline.
_ENV_WRAPPER_BOOL_FLAGS = frozenset({
    "-i", "--ignore-environment", "-0", "--null", "-v", "--debug", "-",
})
_ENV_WRAPPER_VALUE_FLAGS = frozenset({"-u", "-C", "-S"})
_ENV_WRAPPER_VALUE_LONG_FLAGS = frozenset({
    "--unset", "--chdir", "--split-string",
    "--block-signal", "--default-signal", "--ignore-signal",
})


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


def _skip_env_wrapper(s: str, i: int) -> int:
    """Return the index just past a leading ``env [opts]`` wrapper at ``s[i]``.

    Skips leading whitespace, the bare ``env`` token, and its option flags,
    stopping at the first assignment or wrapped-command token.  Returns ``i``
    unchanged (modulo leading whitespace) when no wrapper is present.  Shared
    by the env-prefix peeler and the assignment-substitution guard so both see
    the same prefix boundary -- if only the peeler skipped the wrapper, an
    ``env NAME=$(mutative) cmd`` form would have its substitution discarded
    without ever being classified.
    """
    n = len(s)
    while i < n and s[i].isspace():
        i += 1
    # Only a bare `env` TOKEN (followed by whitespace or end) is the wrapper;
    # `env=x` is an assignment named `env`, handled by the assignment loop.
    if not (s[i : i + 3] == "env" and (i + 3 >= n or s[i + 3].isspace())):
        return i
    i += 3
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        # An assignment ends the env-flag scan; the assignment loop takes over.
        if _ENV_ASSIGN_PREFIX_RE.match(s, i):
            break
        tok_end = _consume_shell_word(s, i)
        tok = s[i:tok_end]
        if tok == "--":
            i = tok_end
            break
        if tok in _ENV_WRAPPER_BOOL_FLAGS:
            i = tok_end
            continue
        if tok in _ENV_WRAPPER_VALUE_FLAGS:
            # Short value flag consumes the following token as its argument.
            i = tok_end
            while i < n and s[i].isspace():
                i += 1
            i = _consume_shell_word(s, i)
            continue
        if tok.startswith("--") and "=" in tok:
            # Self-contained long option (--chdir=/x, --unset=FOO).
            i = tok_end
            continue
        if tok in _ENV_WRAPPER_VALUE_LONG_FLAGS:
            i = tok_end
            while i < n and s[i].isspace():
                i += 1
            i = _consume_shell_word(s, i)
            continue
        # Any other token is the wrapped command -- stop peeling here.
        break
    return i


def _peel_leading_env_prefix(command: str) -> "Tuple[str, bool]":
    """Peel a leading env-var assignment / ``env`` wrapper prefix off *command*.

    Returns ``(remainder, peeled)`` where ``remainder`` is the underlying command
    with any leading ``NAME=value`` assignments and/or a leading ``env [opts]``
    wrapper removed, and ``peeled`` reports whether anything was stripped.  The
    real (possibly mutative) command follows such a prefix, but the token-0 base
    command scan lands on the assignment token or on ``env`` (a read-only base
    command) and never sees the verb -- this restores it (T3 gate-evasion fix).

    Careful NOT to over-strip: the assignment regex is anchored at the scan
    cursor, which only ever sits at the command start or immediately after a
    fully-consumed prior assignment / peeled ``env`` token.  A ``=`` inside a
    later argument (``echo A=B terragrunt apply``) is therefore never mistaken
    for a leading assignment -- the cursor is past ``echo`` (a real command) by
    then and the loop has already stopped.
    """
    s = command.strip() if command else ""
    n = len(s)
    i = _skip_env_wrapper(s, 0)
    peeled = i > 0

    # --- Leading NAME=value assignments (one or more) ---
    while True:
        while i < n and s[i].isspace():
            i += 1
        m = _ENV_ASSIGN_PREFIX_RE.match(s, i)
        if not m:
            break
        i = _consume_shell_word(s, m.end())
        peeled = True

    remainder = s[i:].strip()
    return remainder, peeled


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
        if base_cmd in _PYTHON_INTERPRETERS:
            lane = "python"
        elif base_cmd in _NON_SHELL_SCRIPT_INTERPRETERS:
            lane = "code"
        else:
            lane = "shell"
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
) -> MutativeResult:
    """Classify a PowerShell script payload by cmdlet verb taxonomy.

    ``payload`` is the raw text of a ``-Command`` string or a ``.ps1`` file.
    Two concerns are kept separate:

      COMPOSITION / OBFUSCATION (whole-payload scan).  Obfuscation and
      arbitrary-execution markers (``iex``/``iwr``/``icm``,
      ``Invoke-Expression``, a ``&``/``.`` call operator) escalate to T3 FIRST
      -- before the cmdlet scan -- so a read cmdlet piped into ``iex`` cannot
      launder the payload.  These markers may appear as a stage of their own OR
      embedded mid-stage, so they are matched across the ENTIRE payload, not per
      first token.

      CMDLET / VERB (per-stage FIRST-token scan).  The payload is split into
      composition stages ('; | && ||') and ONLY the leading token of each stage
      -- the command position -- is classified by its Verb-Noun verb.  Path and
      flag ARGUMENTS ('C:\\x\\my-folder', 'a-b\\file.txt') sit in argument
      positions and are NOT read as cmdlets, so a hyphenated path no longer
      forces a false T3.  Rules (conservative / positive-allowlist):
        * ANY change or unknown verb at a command position escalates the WHOLE
          payload to T3 (tier = MAX across stages).
        * To drop BELOW T3 at least one command-position cmdlet must be present
          AND every command-position cmdlet must be a read verb -- a payload
          with no recognizable command-position cmdlet is T3 (cannot prove it is
          read-only; no safe-by-elimination here).
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

    # 2. Per-stage cmdlet scan: classify ONLY the FIRST cmdlet (the command) of
    #    each composition stage.  A Verb-Noun-shaped path/flag argument
    #    ('my-folder', 'a-b') follows the command in its stage, so it is skipped.
    saw_read_cmdlet = False
    for verb, noun in _ps_stage_command_cmdlets(payload):
        v, n = verb.lower(), noun.lower()
        kind = _classify_powershell_verb(v, n)
        if kind == "change":
            return MutativeResult(
                is_mutative=True,
                category=CATEGORY_MUTATIVE,
                verb=f"{v}-{n}",
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
                verb=f"{v}-{n}",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"PowerShell {source} invokes cmdlet '{verb}-{noun}' whose "
                    f"verb '{verb}' is not an approved read verb "
                    f"(conservative default)"
                ),
            )
        saw_read_cmdlet = True

    # 3a. No recognizable command-position cmdlet -> cannot prove read-only.
    if not saw_read_cmdlet:
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="powershell-uninspectable",
            cli_family=family,
            confidence="medium",
            reason=(
                f"PowerShell {source} has no recognizable Verb-Noun cmdlet at a "
                f"command position -- cannot prove it is read-only "
                f"(conservative default)"
            ),
        )

    # 3b. Every command-position cmdlet is a read verb.
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
      * ``-Command``/``-c`` inline   -> classify the raw command text (the
        interpreter flags carry no Verb-Noun cmdlet, so scanning the whole
        string is safe and captures the payload regardless of quoting).
      * no explicit payload flag      -> scan the whole command text anyway; a
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
    if ps_files:
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
        return _classify_powershell_payload(content, family, "script file")

    # -Command / -c inline, or no explicit payload flag: scan the command text.
    return _classify_powershell_payload(command, family, "-Command payload")


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
    ``_check_npm_script_runner`` (unresolvable -> T3).
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
            return _read(
                base_cmd,
                f"PowerShell alias '{base_cmd}' -> {cmdlet} (read-only)",
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

    # 5. Bare Verb-Noun cmdlet: classify the WHOLE command via the shared
    #    payload classifier (handles composition ';'/'|', ambiguous nouns, and
    #    obfuscation identically to the wrapped -Command lane).
    return _classify_powershell_payload(command, "powershell", "bare cmdlet")


def _read_script_content(
    path: str, cwd: "Optional[str]" = None,
) -> "Optional[str]":
    """Read a bounded prefix of a script file for content classification.

    A relative *path* resolves against *cwd* when given (the ``cd`` target of
    the surrounding chain), else against the process cwd -- the previous,
    hook-cwd-only behavior.  This is what lets ``cd /repo && node build.mjs``
    find ``build.mjs`` under ``/repo`` instead of the Gaia install dir.  An
    absolute path is unaffected.

    Returns ``None`` when the path cannot be resolved to a readable regular
    file -- the caller treats that as the conservative (mutative) case, because
    an interpreter pointed at an un-inspectable payload could do anything.  The
    genuinely-unreadable case is preserved: an honored-but-still-missing path
    still returns ``None`` here.
    """
    import os

    resolved = path
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
    DEPTH`` the script body is NOT opened -- returning ``None`` here makes the
    invocation classify by ordinary token scan instead, which breaks a self- or
    mutual-reference cycle (a script whose text names its own path re-reads and
    re-classifies itself forever otherwise -- see the constant's docstring).

    Returns ``None`` when the command is not a script-file invocation.
    """
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        # Recursion budget exhausted: refuse to descend into (re-read) the
        # referenced body. Falling through to ordinary token classification is
        # the deterministic cycle break.
        return None

    resolved = _resolve_script_argument(base_cmd, semantics)
    if resolved is None:
        # No script FILE to open -- the payload may still be a program the
        # interpreter reads from stdin, which is un-inspectable rather than
        # absent.  Every other shape returns None and classifies normally.
        return _check_stdin_script_payload(command, base_cmd, family, semantics)

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
        script_path, content, semantics, family, _depth=_depth,
    )
    if gaia_result is not None:
        return gaia_result

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
            return _classify_source_with_lexer(
                content, script_path, family, spec, cwd=cwd, _depth=_depth + 1,
            )

    # "code" lane (non-shell source: ruby/perl/php) suppresses camelCase
    # subcommand splitting so language identifiers are not read as CLI verbs;
    # "shell" lane keeps the full command semantics.  ``cwd`` is threaded
    # through so a mutative line INSIDE the script body that itself invokes a
    # relative script/npm-run resolves against the SAME directory the outer
    # script was read from, not the hook's own cwd (see _classify_script_
    # content_by_regex's docstring).
    return _classify_script_content_by_regex(
        content, script_path, family, from_source_code=(lane == "code"),
        cwd=cwd, _depth=_depth + 1,
    )


def _classify_source_with_lexer(
    content: str, script_path: str, family: str, spec,
    cwd: "Optional[str]" = None, _depth: int = 0,
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
    """
    stripped = _strip_source(content, spec)
    verb_lines = stripped.verb_view.splitlines()
    exec_lines = stripped.exec_view.splitlines()

    for verb_line, exec_line in zip(verb_lines, exec_lines):
        code_line = verb_line.strip()
        if code_line and _is_blocked_command is not None:
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

        if code_line:
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

    return MutativeResult(
        is_mutative=False,
        category=CATEGORY_READ_ONLY,
        verb="script-file",
        cli_family=family,
        confidence="medium",
        reason=f"Script '{script_path}' has no mutative or blocked line",
    )


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
# npm run <script> body resolution (Brief gaia-system-security-lifecycle, AC-3)
# ---------------------------------------------------------------------------
# The verb scanner matches the npm SCRIPT NAME against the taxonomy, but the
# name is arbitrary: ``npm run db-migrate`` / ``npm ci`` slip through as SAFE
# (a consent bypass) while ``npm run start`` / ``copy-assets`` false-positive.
# The fix mirrors the script-file lane: resolve ``npm run <script>`` to its real
# command body from ``package.json`` (``scripts.<script>``) and classify THAT
# body with the existing engine.  When the body cannot be resolved -- no
# package.json, unparseable JSON, or the script entry is absent -- fall back to
# the same conservative T3 default the unreadable-script-file case uses.

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
    import json

    base = cwd if cwd is not None else os.getcwd()
    path = os.path.join(base, "package.json")
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return None
    body = scripts.get(script_name)
    if not isinstance(body, str) or not body.strip():
        return None
    return body


def _extract_npm_prefix_override(tokens: "Tuple[str, ...]") -> "Optional[str]":
    """Return the directory value of npm's global ``--prefix``/``-C`` option.

    npm's ``--prefix <dir>`` (or its short alias ``-C <dir>``) tells npm to
    resolve ``package.json`` from *dir* instead of the invoking cwd -- this is
    exactly how a wrapped dev loop runs a workspace's scripts from an
    arbitrary launch directory (``npm run build --prefix /path/to/repo``).
    Without this, ``--prefix`` is invisible to the classifier: package.json
    resolution silently falls back to ``os.getcwd()`` (the hook's own cwd,
    typically the monorepo root), so the script body is read from the WRONG
    package.json (or none at all) and the invocation falls to the
    conservative ``npm-run-unresolved`` T3 regardless of the real script.

    Recognizes both the space form (``--prefix /dir``) and the inline
    ``--prefix=/dir`` form, and either flag position -- before or after the
    subcommand -- since npm accepts both. Scanning the raw ``tokens`` (not
    ``non_flag_tokens``) preserves the directory's original case, which
    matters on case-sensitive filesystems. Returns ``None`` when neither flag
    is present or the flag has no value.
    """
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok.startswith("--prefix="):
            value = tok.split("=", 1)[1]
            return value or None
        if tok in ("--prefix", "-C") and i + 1 < n:
            return tokens[i + 1]
    return None


def _check_npm_script_runner(
    base_cmd: str, family: str, semantics: "CommandSemantics",
    cwd: "Optional[str]" = None, _depth: int = 0,
) -> "Optional[MutativeResult]":
    """Classify ``npm run <script>`` / ``npm ci`` by real effect, not by name.

    * ``npm ci`` performs a clean install that rewrites ``node_modules`` -- it
      is unconditionally mutative (T3), regardless of the verb taxonomy.
    * ``npm run <script>`` is resolved to its ``package.json`` body and that
      body is classified by the shell/regex engine (``_classify_script_content_
      by_regex``), the same standard the script-file lane meets.  An
      unresolvable script (missing/unparseable package.json or absent entry)
      falls back to conservative T3.
    * A ``--prefix <dir>`` / ``-C <dir>`` global option OVERRIDES *cwd* for
      this resolution (see ``_extract_npm_prefix_override``), so
      ``npm run build --prefix /repo`` resolves ``/repo/package.json``
      instead of falling back to the process cwd.  This mirrors how ``cd
      /repo && npm run build`` is already honored -- ``--prefix`` is simply
      npm's OWN cwd-fixing flag, so it is folded the same way.

    Returns ``None`` for any other npm invocation so ordinary detection
    continues unchanged (``npm run`` with no script lists scripts -- read-only;
    ``npm install`` and friends keep their existing classification).
    """
    if base_cmd != "npm":
        return None

    non_flag = semantics.non_flag_tokens
    if not non_flag:
        return None
    sub = non_flag[0]

    prefix_override = _extract_npm_prefix_override(semantics.tokens)
    if prefix_override:
        cwd = _resolve_dir_against_cwd(cwd, prefix_override)

    # `npm ci` -- clean install, always mutates node_modules.
    if sub == "ci":
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="ci",
            cli_family=family,
            confidence="high",
            reason=(
                "`npm ci` performs a clean install that rewrites node_modules "
                "-- state-mutating, requires consent"
            ),
        )

    if sub not in ("run", "run-script"):
        return None  # not a script-runner form -- ordinary detection handles it

    # `npm run` with no script name lists available scripts -- read-only.
    if len(non_flag) < 2:
        return None

    script_name = non_flag[1]

    # Recursion budget exhausted: refuse to descend into (re-resolve and
    # re-classify) the package.json body. Returning None lets the invocation
    # classify by ordinary token scan, breaking an npm-run self/mutual cycle.
    if _depth >= _MAX_SCRIPT_RECURSION_DEPTH:
        return None

    body = _resolve_npm_script_body(script_name, cwd=cwd)
    if body is None:
        # Conservative default: the script body cannot be resolved, so we
        # cannot prove it is safe -- mirror the unreadable-script-file case.
        return MutativeResult(
            is_mutative=True,
            category=CATEGORY_MUTATIVE,
            verb="npm-run-unresolved",
            cli_family=family,
            confidence="medium",
            reason=(
                f"`npm run {script_name}` could not be resolved to a "
                f"package.json script body -- cannot verify the payload, "
                f"requiring approval (conservative default)"
            ),
        )

    # Classify the resolved body: split into per-clause commands (a mutation may
    # live in any clause of `tsc && rm -rf dist`) and feed the existing engine.
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
            f"`npm run {script_name}` resolved to body {body!r}: {inner.reason}"
        ),
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

    # ---- Layer 2a: Exec-sink string-argument re-classification ----
    # Shared with the script-file code lane (``_classify_script_content_by_regex``)
    # so the two lanes cannot diverge on how a command handed to a subprocess
    # sink is detected.  Runs before the broader Layer 2b keyword scan.  For a
    # payload that already parsed cleanly as Python this point is unreachable
    # (the AST block returned above), so Python behavior is unchanged.
    sink_result = _scan_exec_sink_string_args(command, family)
    if sink_result is not None and sink_result.is_mutative:
        return sink_result

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
        # AC-9 (Brief: endurecimiento-de-tests-del-security-core): the length
        # heuristic is a *proxy* for "too complex to vet"; it must not flag
        # inline code that is PROVABLY read-only just because it is long.  For
        # Python payloads we re-parse the exact code and require a positive
        # allowlist match (import + SELECT/PRAGMA + print + local assignments,
        # no write call, no attribute/subscript assignment, no dynamic
        # dispatch).  This is the inverse of analyze_python_inline's blocklist:
        # a mutation never classifies as read-only, so the exemption cannot
        # open a hole -- an AST-clean-but-mutating payload (``cur.execute(
        # 'INSERT ...')``, ``con.commit()``) is NOT provably read-only and
        # stays flagged.  Non-Python payloads are never exempted (no AST).
        if (
            base_cmd in _PYTHON_INTERPRETERS
            and _is_provably_read_only_python is not None
            and _is_provably_read_only_python(_extract_python_payload(command, base_cmd))
        ):
            return MutativeResult(
                is_mutative=False,
                category=CATEGORY_READ_ONLY,
                verb="inline-code-readonly",
                cli_family=family,
                confidence="medium",
                reason=(
                    f"Inline Python is long ({len(code_portion)} chars) but "
                    "provably read-only (allowlisted constructs only)"
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
