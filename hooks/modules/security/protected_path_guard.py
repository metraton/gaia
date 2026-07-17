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
any WRITE-capable command whose target resolves into the protected ``.claude/``
tree. The scope mirrors the deterministic Write/Edit backstop exactly:

  * anything under a ``.claude/hooks/`` path (EXCEPT ``.md`` docs -- they do not
    execute code), and
  * ``settings.json`` / ``settings.local.json`` anywhere under a ``.claude/``
    path.

Like gaia_db_write_guard and the subagent memory-write guard, the block is
categorical and NOT approvable -- there is no T3 grant that lifts it. This is
the faithful implementation of the ``.claude/`` hard-boundary policy for shell
mechanisms: "do not attempt it", not "run it and let the hook decide". READS
(``git diff .claude/hooks/x.py``, ``cat .claude/settings.json``,
``grep -r x .claude/``) are NOT write-capable and pass through untouched.

Residual limitation (accepted): a write assembled indirectly -- a protected
path reconstructed by variable interpolation or read from a file
(``git apply patch`` whose diff targets ``.claude/``) -- is not visible as a
literal token and is not caught here. The literal-token slice is the bounded,
low-false-positive portion that is closed.

Public API:
    targets_protected_path(command: str) -> str | None   -- offending path or None
    rejection_message(path: str) -> str
    check(command: str) -> tuple[bool, str | None]        -- main entrypoint
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Write-capability sets
# ---------------------------------------------------------------------------
# Git subcommands that write/replace working-tree files (the ones that live in
# GIT_LOCAL_SAFE_SUBCOMMANDS and therefore short-circuit the tier gate). A read
# subcommand (diff, log, show, status, blame) is deliberately absent so a read
# targeting a protected path is never blocked.
_GIT_WRITE_SUBCOMMANDS = frozenset({
    "mv", "checkout", "switch", "restore", "stash", "reset", "revert",
    "cherry-pick", "apply", "am", "rebase", "merge", "pull", "clone", "add",
})

# Non-git base commands that write files. Plain mv/cp were only T3-approvable;
# scoping them here makes a ``.claude/`` target a categorical block regardless
# of the tier classifier -- one coherent rule: ".claude/ writes via Bash are
# blocked".
_FILESYSTEM_WRITE_COMMANDS = frozenset({
    "mv", "cp", "install", "dd", "tee", "ln", "rsync", "touch", "truncate",
    "mkdir", "sed", "chmod", "chown", "chgrp", "shred", "unlink", "rm",
})

# Shell operators that separate independent command components. Splitting on
# these keeps a read in one component from being associated with a writer in
# another (``cat .claude/settings.json && ls`` must not fire on ``ls``).
_OPERATOR_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")

_SETTINGS_BASENAMES = frozenset({"settings.json", "settings.local.json"})


def _is_protected_claude_path(token: str) -> bool:
    """Return True iff `token` names a path inside the protected .claude/ tree.

    Scope mirrors _is_protected() in adapters/claude_code.py:
      * under a ``.claude/hooks/`` path and NOT a ``.md`` file, OR
      * basename is settings.json / settings.local.json anywhere under
        ``.claude/``.

    Detection is structural (component match on the normalized path), not a
    filesystem resolve, so it holds for any workspace Gaia governs and for a
    destination path that does not exist yet (the overwrite target of a move).
    """
    if not token or token.startswith("-"):
        return False

    # Strip any leading redirect / quoting cruft that survived tokenization
    # (e.g. ">file" from an unspaced redirect).
    cleaned = token.lstrip("<>&|")
    cleaned = cleaned.strip("'\"")
    if not cleaned:
        return False

    # normpath collapses "." and ".." components (foo/../.claude/hooks/x ->
    # .claude/hooks/x) without touching the filesystem or resolving symlinks.
    normalized = os.path.normpath(cleaned)
    parts = normalized.split(os.sep)
    # Also split on "/" in case of mixed separators.
    if os.sep != "/":
        parts = [p for seg in parts for p in seg.split("/")]

    if ".claude" not in parts:
        return False

    basename = parts[-1]
    if basename in _SETTINGS_BASENAMES:
        return True

    claude_idx = parts.index(".claude")
    if "hooks" in parts[claude_idx + 1:]:
        # Docs under hooks/ do not execute code and are exempt, matching the
        # .md carve-out in _is_protected().
        if basename.endswith(".md"):
            return False
        return True

    return False


def _tokenize(component: str) -> List[str]:
    """Best-effort tokenization of a single command component."""
    try:
        return shlex.split(component, posix=True)
    except ValueError:
        # Unbalanced quotes -- fall back to whitespace split so a malformed
        # command still gets scanned rather than silently skipped.
        return component.split()


def _component_writes_protected_path(component: str) -> Optional[str]:
    """Return the offending protected path if `component` writes to one."""
    tokens = _tokenize(component)
    if not tokens:
        return None

    # Any protected-path token present in this component.
    protected_tokens = [t for t in tokens if _is_protected_claude_path(t)]
    if not protected_tokens:
        return None

    base = os.path.basename(tokens[0])

    # Redirect into a protected path: ">"/">>" token or an unspaced ">file".
    for tok in tokens:
        stripped = tok.lstrip("<>&|")
        if tok != stripped and _is_protected_claude_path(stripped):
            return stripped
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


def targets_protected_path(command: str) -> Optional[str]:
    """Return the offending path if `command` writes into the protected tree.

    Args:
        command: The full Bash command line (may contain operator-linked
            components, quotes, and redirects).

    Returns:
        The protected path string being written, or None if the command does
        not write into the protected ``.claude/`` tree.
    """
    if not command:
        return None

    for component in _OPERATOR_SPLIT.split(command):
        component = component.strip()
        if not component:
            continue
        hit = _component_writes_protected_path(component)
        if hit is not None:
            return hit

    return None


def rejection_message(path: str) -> str:
    """Return the canonical rejection message for a protected-path write."""
    return (
        f"[PROTECTED_PATH] Refusing to write into the protected .claude/ tree "
        f"via Bash: {path}. The Gaia hooks directory and .claude settings files "
        f"are a hard security boundary -- no shell command may modify them, and "
        f"this block is not approvable. Edit the SOURCE under gaia/ and let "
        f"`gaia install` propagate the change."
    )


def check(command: str) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash hook integration.

    Args:
        command: The Bash command line.

    Returns:
        (allowed, reason)
        - (True, None)  if command does not write the protected tree
        - (False, msg)  if command writes into the protected .claude/ tree
    """
    hit = targets_protected_path(command)
    if hit is not None:
        return False, rejection_message(hit)
    return True, None
