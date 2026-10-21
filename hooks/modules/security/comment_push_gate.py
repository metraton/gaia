"""Refuse a branch push whose comments exceed the limit `gaia lint comments` measures."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, NamedTuple, Optional

from .publish_attribution_guard import _ENV_ASSIGNMENT, _segments

logger = logging.getLogger(__name__)

_MAX_LISTED = 30
_NOTHING_TO_COMPARE_FLAGS = {"--tags", "--delete", "-d", "--dry-run", "-n", "--mirror", "--all"}
_VALUE_FLAGS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


class GateVerdict(NamedTuple):
    allowed: bool
    reason: Optional[str] = None
    warning: Optional[str] = None


def _git_read(repo: str, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _push_in(tokens: List[str], cwd: str) -> Optional[tuple]:
    while tokens and (tokens[0] == "env" or _ENV_ASSIGNMENT.match(tokens[0])):
        tokens = tokens[1:]
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None
    args, repo = tokens[1:], cwd
    while len(args) >= 2 and args[0] in ("-C", "-c"):
        if args[0] == "-C":
            repo = os.path.join(repo, os.path.expanduser(args[1]))
        args = args[2:]
    if not args or args[0] != "push":
        return None
    return repo, args[1:]


def _branches(repo: str, rest: List[str]) -> List[str]:
    operands: List[str] = []
    skip = False
    for token in rest:
        if skip:
            skip = False
        elif token in _NOTHING_TO_COMPARE_FLAGS:
            return []
        elif token in _VALUE_FLAGS:
            skip = True
        elif not token.startswith("-"):
            operands.append(token)
    current = _git_read(repo, "symbolic-ref", "--short", "HEAD")
    found: List[str] = []
    for spec in operands[1:] or ([current] if current else []):
        source = spec.lstrip("+").split(":", 1)[0]
        if spec.startswith(":") or source.startswith("refs/tags/"):
            continue
        source = current if source == "HEAD" else source.removeprefix("refs/heads/")
        if source and _git_read(repo, "show-ref", "--verify", f"refs/heads/{source}") is not None:
            found.append(source)
    return found


def _refusal(branch: str, report) -> str:
    return (
        f"[COMMENT_LIMIT] Refusing to push '{branch}': it adds {report.net} net comment and "
        f"docstring lines over {report.added_code} added code lines (limit {report.limit}). "
        f"Trim them, commit, and push again; `gaia lint comments` prints this list.\n"
        f"{report.render(_MAX_LISTED)}\n"
        f"A comment survives only for a fact the code cannot say. This block is not approvable."
    )


def check(command: str, cwd: Optional[str] = None) -> GateVerdict:
    """Return the verdict for ``command``: refused when it pushes a branch over the comment limit."""
    if "push" not in (command or ""):
        return GateVerdict(True)
    current_dir = cwd or os.getcwd()
    for tokens in _segments(command):
        if tokens[0] == "cd" and len(tokens) > 1:
            current_dir = os.path.join(current_dir, os.path.expanduser(tokens[1]))
            continue
        push = _push_in(tokens, current_dir)
        if push is None:
            continue
        repo, rest = push
        try:
            from gaia.comment_lint import measure

            for branch in _branches(repo, rest):
                report = measure(repo, head=branch)
                if report.blocked:
                    return GateVerdict(False, _refusal(branch, report))
        except Exception as exc:  # noqa: BLE001
            logger.warning("comment gate skipped: %s", exc)
            return GateVerdict(True, warning=(
                f"[COMMENT_GATE_SKIPPED] The comment counter failed ({str(exc)[:160]}), so this push "
                f"was not checked. Run `gaia lint comments` on the branch before relying on it."
            ))
    return GateVerdict(True)
