"""Plan-first COMMAND_SET validation and immutable command fingerprints.

Requests contain atomic Bash invocations that have already been shown to the
user.  The runtime accepts one or more exact T3 commands; shell composition,
protected paths, interactive programs, and commands that are safe or
permanently blocked are rejected before an approval row can be minted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Iterable


class CommandSetValidationError(ValueError):
    """Raised when a proposed request-set is not eligible for COMMAND_SET."""


_COMPOUND = re.compile(r"(?:&&|\|\||[;|]|\n|`|\$\()")
_INTERACTIVE = re.compile(
    r"^(?:sudo\s+)?(?:vim?|nano|emacs|less|more|top|htop|watch|ssh|mysql|psql|python|node)\b"
)


def command_fingerprint(command: str) -> str:
    """Return the hard byte fingerprint used at request and execution time."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def request_fingerprint(commands: Iterable[str]) -> str:
    """Return an order-sensitive fingerprint for an exact command sequence."""
    payload = json.dumps(list(commands), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_request_set(commands: list[str], *, cwd: str | None = None) -> list[dict]:
    """Validate and normalize a plan-first set without minting or consuming grants."""
    if not isinstance(commands, list) or len(commands) < 1:
        raise CommandSetValidationError("COMMAND_SET requires at least one command")

    hooks_dir = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    from modules.security.blocked_commands import is_blocked_command
    from modules.security.flag_classifiers import (
        OUTCOME_BLOCKED,
        OUTCOME_MUTATIVE,
        classify_by_flags,
    )
    from modules.security.mutative_verbs import detect_mutative_command
    from modules.security.protected_path_guard import check as check_protected

    normalized: list[dict] = []
    for index, raw in enumerate(commands):
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise CommandSetValidationError(f"command[{index}] must be a non-empty exact string")
        if _COMPOUND.search(raw):
            raise CommandSetValidationError(f"command[{index}] must be one atomic Bash invocation")
        if _INTERACTIVE.search(raw):
            raise CommandSetValidationError(f"command[{index}] is interactive")
        protected_allowed, _protected_reason = check_protected(raw)
        if not protected_allowed:
            raise CommandSetValidationError(f"command[{index}] targets a protected path")
        blocked = is_blocked_command(raw)
        if blocked.is_blocked:
            raise CommandSetValidationError(f"command[{index}] is permanently blocked")

        detected = detect_mutative_command(raw, cwd=cwd)
        flag = classify_by_flags(raw)
        if flag is not None and flag.outcome == OUTCOME_BLOCKED:
            raise CommandSetValidationError(f"command[{index}] is permanently blocked")
        is_t3 = detected.is_mutative or (
            flag is not None
            and flag.outcome == OUTCOME_MUTATIVE
            and not flag.command_family.startswith("git_")
        )
        if not is_t3:
            raise CommandSetValidationError(f"command[{index}] is not classified T3")
        normalized.append(
            {"command": raw, "fingerprint": command_fingerprint(raw), "rationale": ""}
        )
    return normalized
