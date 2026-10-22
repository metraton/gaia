"""
protected_path_guard.py -- Bash command-string protected-path guard.

The Write/Edit sensitive-path backstop (`_is_protected` in
adapters/claude_code.py) only inspects the ``file_path`` parameter of Write and
Edit tool calls. It never sees a Bash ``command`` string, so a shell command
that writes into the protected ``.claude/`` tree bypasses it entirely.

That gap became reachable once ``git mv`` was routed through
GIT_LOCAL_SAFE_SUBCOMMANDS: ``git mv payload.py .claude/hooks/pre_tool_use.py``
short-circuits to non-mutative (T0) and never reaches the tier gate, so hook
code could be overwritten through Bash with no consent. Plain ``mv``/``cp`` were
"only" T3-approvable, and shell redirects into any path are blocked generically
by the cloud-pipe validator -- but nothing enforced the ``.claude/`` boundary
for the git working-tree writers.

This guard closes that hole independently of the mutative classifier: it scans
the raw command string (per operator-split component) and CATEGORICALLY denies
any WRITE-capable command whose target is a protected Gaia path. The scope is
not restated here: it is ``protected_paths.is_protected_hook_path``, the same
predicate the Write/Edit gate calls. Two surfaces, one predicate -- because the
scope used to be duplicated in prose between them ("mirrors the Write/Edit
backstop") and the drift that produced was a security control whose reach
depended on the deployment layout. Widening one surface now widens both, so the
shell route cannot stay open against a tree the file-write route protects.

Like gaia_db_write_guard and the subagent memory-write guard, the block is
categorical and NOT approvable -- there is no T3 grant that lifts it. This is
the faithful implementation of the ``.claude/`` hard-boundary policy for shell
mechanisms: "do not attempt it", not "run it and let the hook decide".

Write capability is judged by the component's BASE TOKEN, with no flag
inspection, so do NOT expect reads to pass untouched. A command whose base is
not a listed writer does pass (``git diff .claude/hooks/x.py``,
``cat .claude/settings.json``, ``grep -r x .claude/``), but a READ-ONLY
SPELLING OF A LISTED WRITER is denied all the same: ``sed -n '1,40p'
.claude/settings.json`` prints and mutates nothing and is still categorically
denied, because the base token is ``sed``. The read itself is not withheld --
reach it with the Read tool, ``cat``, ``grep`` or ``awk``, a token outside the
write set. Do not make the guard flag-aware to soften this: ``sed`` writes its
target from a ``w`` script command with no in-place flag anywhere in the
invocation, so a flag-based guard would pass a real write through a boundary
that is categorical and therefore not approvable.

Residual limitation (accepted): a write assembled indirectly -- a protected
path reconstructed by variable interpolation or read from a file
(``git apply patch`` whose diff targets ``.claude/``) -- is not visible as a
literal token and is not caught here. The literal-token slice is the bounded,
low-false-positive portion that is closed.

Public API:
    targets_protected_path(command: str, cwd: str | None = None) -> str | None
    rejection_message(path: str) -> str
    check(command: str, cwd: str | None = None) -> tuple[bool, str | None]
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

from .canonical_path import command_name
from .mutative_verbs import cwd_after_component
from .protected_paths import is_protected_hook_path, is_read_spool_path
from .shell_grouping import strip_grouping_wrappers
from .shell_substitution import extract_substitutions

# ---------------------------------------------------------------------------
# Write-capability sets
# ---------------------------------------------------------------------------
# Git subcommands that write/replace working-tree files (the ones that live in
# GIT_LOCAL_SAFE_SUBCOMMANDS and therefore short-circuit the tier gate). A read
# subcommand (diff, log, show, status, blame) is deliberately absent so a read
# targeting a protected path is never blocked. ``add`` is absent for the same
# reason: it reads the working tree and writes the INDEX, so it can never
# overwrite a protected file's bytes -- and because the block is not
# approvable, listing it would leave a turn authoring a new hook module with no
# way to stage it except from the repo root, sweeping in unrelated work.
_GIT_WRITE_SUBCOMMANDS = frozenset({
    "mv", "checkout", "switch", "restore", "stash", "reset", "revert",
    "cherry-pick", "apply", "am", "rebase", "merge", "pull", "clone",
})

# Non-git base commands that write files. Plain mv/cp were only T3-approvable;
# scoping them here makes a ``.claude/`` target a categorical block regardless
# of the tier classifier -- one coherent rule: ".claude/ writes via Bash are
# blocked".
_FILESYSTEM_WRITE_COMMANDS = frozenset({
    "mv", "cp", "install", "dd", "tee", "ln", "rsync", "touch", "truncate",
    "mkdir", "sed", "chmod", "chown", "chgrp", "shred", "unlink", "rm",
    # cmd.exe builtins and PowerShell cmdlets with their aliases.
    "copy", "xcopy", "robocopy", "copy-item", "cpi", "move", "move-item", "mi",
    "ren", "rename", "rename-item", "rni", "del", "erase", "rd", "rmdir",
    "remove-item", "ri", "new-item", "ni", "set-content", "sc", "add-content", "ac",
    "out-file", "clear-content", "clc", "icacls", "attrib",
})

# Shell operators that separate independent command components. Splitting on
# these keeps a read in one component from being associated with a writer in
# another (``cat .claude/settings.json && ls`` must not fire on ``ls``).
_OPERATOR_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def _protected_spelling(token: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return the protected path `token` names, or None.

    This decides only what a shell TOKEN is -- the scope of the protected set
    belongs to ``protected_paths.is_protected_hook_path`` and is deliberately
    not restated. The set covers every Gaia hook tree, the source checkout
    included, not just the ``.claude/`` copy. A relative token is also judged
    anchored at `cwd`; when only that anchoring makes it protected, the
    absolute path is returned so ``rejection_message`` can still name it.
    """
    if not token or token.startswith("-"):
        return None

    # Strip any leading redirect / quoting cruft that survived tokenization
    # (e.g. ">file" from an unspaced redirect).
    cleaned = token.lstrip("<>&|")
    cleaned = cleaned.strip("'\"")
    if not cleaned:
        return None

    if is_protected_hook_path(cleaned) or is_read_spool_path(cleaned):
        return cleaned
    if cwd and not os.path.isabs(cleaned):
        anchored = os.path.join(cwd, cleaned)
        if is_protected_hook_path(anchored) or is_read_spool_path(anchored):
            return anchored
    return None


def _is_protected_claude_path(token: str, cwd: Optional[str] = None) -> bool:
    """Return True iff `token` names a protected Gaia path."""
    return _protected_spelling(token, cwd) is not None


def _tokenize(component: str) -> List[str]:
    """Best-effort tokenization of a single command component."""
    try:
        return shlex.split(component, posix=True)
    except ValueError:
        # Unbalanced quotes -- fall back to whitespace split so a malformed
        # command still gets scanned rather than silently skipped.
        return component.split()


def _component_writes_protected_path(component: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return the offending protected path if `component` writes to one.

    The component is unwrapped before tokenization: the write capability is
    decided by ``tokens[0]``, so ``(cp x .claude/hooks/y.py)`` put ``(cp``
    there and walked through this categorical boundary. Unwrapping is applied
    to the component STRING, never per token -- a dangerous command quoted
    inside another command's argument stays one opaque token and must keep
    reading as a mention rather than a use.
    """
    tokens = _tokenize(strip_grouping_wrappers(component))
    if not tokens:
        return None

    # Any protected-path token present in this component.
    protected_tokens = [p for p in (_protected_spelling(t, cwd) for t in tokens) if p]
    if not protected_tokens:
        return None

    base = command_name(tokens[0])

    # Redirect into a protected path: ">"/">>" token or an unspaced ">file".
    for tok in tokens:
        stripped = tok.lstrip("<>&|")
        if tok != stripped:
            hit = _protected_spelling(stripped, cwd)
            if hit:
                return hit
    if any(t in (">", ">>") for t in tokens):
        # A bare redirect operator with a protected target already matched
        # above via protected_tokens; surface the first protected path.
        return protected_tokens[0]

    if base == "git":
        # First non-flag token after "git" is the subcommand.
        subcommand = next(
            (t for t in tokens[1:] if not t.startswith("-")), ""
        )
        if subcommand in _GIT_WRITE_SUBCOMMANDS:
            return protected_tokens[0]
        return None

    if base in _FILESYSTEM_WRITE_COMMANDS:
        return protected_tokens[0]

    return None


def targets_protected_path(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return the offending path if `command` writes into the protected tree.

    Args:
        command: The full Bash command line (may contain operator-linked
            components, quotes, and redirects).
        cwd: The directory the command runs in; relative targets anchor there,
            and a ``cd`` component moves it for the components that follow.

    Returns:
        The protected path string being written (absolute when it was spelled
        relative), or None if the command does not write into the protected
        tree.
    """
    if not command:
        return None

    running_cwd = cwd
    seen_cwds = [cwd]
    for component in _OPERATOR_SPLIT.split(command):
        component = component.strip()
        if not component:
            continue
        hit = _component_writes_protected_path(component, running_cwd)
        if hit is not None:
            return hit
        running_cwd = cwd_after_component(component, running_cwd)
        seen_cwds.append(running_cwd)

    # A command substitution runs BEFORE the command that contains it, so
    # ``echo $(cp payload.py .claude/hooks/pre_tool_use.py)`` overwrites the
    # hook while presenting ``echo`` as tokens[0] -- the categorical boundary
    # never fired. Scan what would actually execute. The extraction runs on the
    # FULL command rather than per component, because it is quoting that tells
    # a use from a mention and the operator split above breaks quoting; and it
    # yields nothing at all for a single-quoted or escaped mention, so a
    # protected path merely NAMED inside an argument stays free.
    for inner in extract_substitutions(command):
        for inner_component in _OPERATOR_SPLIT.split(inner):
            inner_component = inner_component.strip()
            if not inner_component:
                continue
            for inner_cwd in seen_cwds:
                hit = _component_writes_protected_path(inner_component, inner_cwd)
                if hit is not None:
                    return hit

    return None


def rejection_message(path: str) -> str:
    """Return the canonical rejection message for a protected-path write."""
    if is_read_spool_path(path):
        return (
            f"[PROTECTED_PATH] Refusing to write the harness read spool: {path}. Only "
            f"the PreToolUse recorder writes it; a line written by any tool would "
            f"forge a turn's files_checked. Not approvable."
        )
    return (
        f"[PROTECTED_PATH] Refusing to write Gaia hook code or settings via "
        f"Bash: {path}. Every Gaia hook tree -- the source checkout and each "
        f"installed copy -- plus the .claude settings files are a hard security "
        f"boundary: no shell command may modify them, and this block is not "
        f"approvable. Use the Write/Edit surface on the source tree under "
        f"gaia/, which asks the user for consent, and let `gaia install` "
        f"propagate the change."
    )


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash hook integration.

    Args:
        command: The Bash command line.
        cwd: The directory the command runs in, when the caller knows it.

    Returns:
        (allowed, reason)
        - (True, None)  if command does not write the protected tree
        - (False, msg)  if command writes into the protected .claude/ tree
    """
    hit = targets_protected_path(command, cwd)
    if hit is not None:
        return False, rejection_message(hit)
    return True, None
