"""
Semantic command analysis helpers for security decisions.

This module builds an analysis-friendly representation of a shell command
without mutating the original command string that will be executed.

Key properties:
- Idempotent: analyzing a normalized command produces the same semantic view.
- CLI-agnostic: relies on token structure, not a large per-CLI global-flag table.
- Non-destructive: the real command is never rewritten for execution.
"""

import functools
import shlex
from dataclasses import dataclass
from typing import Iterable, Tuple

# Scan enough semantic tokens to cover CLIs with multiple resource segments and
# several global flag/value pairs before the real verb.
SEMANTIC_SCAN_LIMIT = 12


@dataclass(frozen=True)
class CommandSemantics:
    """Semantic view of a shell command for policy analysis."""

    raw_command: str = ""
    tokens: Tuple[str, ...] = ()
    base_cmd: str = ""
    args: Tuple[str, ...] = ()
    flag_tokens: Tuple[str, ...] = ()
    non_flag_tokens: Tuple[str, ...] = ()
    semantic_tokens: Tuple[str, ...] = ()
    semantic_head_tokens: Tuple[str, ...] = ()

    @property
    def normalized_command(self) -> str:
        """Return the canonical analysis form of the command."""
        return " ".join(self.semantic_tokens)

    def has_flag(self, flag: str) -> bool:
        """Check whether a normalized flag is present."""
        return flag.lower() in self.flag_tokens


def tokenize_command(command: str) -> Tuple[str, ...]:
    """Tokenize a shell command safely, preserving quoted substrings."""
    if not command or not command.strip():
        return ()
    try:
        return tuple(shlex.split(command.strip()))
    except ValueError:
        # Fall back to a simple split for malformed quoting. This keeps the
        # security layer best-effort instead of crashing on parse errors.
        return tuple(command.strip().split())


@functools.lru_cache(maxsize=128)
def analyze_command(command: str, semantic_scan_limit: int = SEMANTIC_SCAN_LIMIT) -> CommandSemantics:
    """Build an idempotent semantic representation for security analysis."""
    raw_command = command.strip() if command else ""
    tokens = tokenize_command(raw_command)
    if not tokens:
        return CommandSemantics(raw_command=raw_command)

    base_cmd = _pathless(tokens[0]).lower()
    args = tuple(tokens[1:])

    flag_tokens = []
    non_flag_tokens = []
    for token in args:
        if _is_flag(token):
            flag_tokens.extend(_normalize_flag_token(token))
            continue
        non_flag_tokens.append(token.lower())

    semantic_tokens = (base_cmd, *non_flag_tokens)
    head_size = max(1, semantic_scan_limit + 1)

    return CommandSemantics(
        raw_command=raw_command,
        tokens=tokens,
        base_cmd=base_cmd,
        args=args,
        flag_tokens=tuple(flag_tokens),
        non_flag_tokens=tuple(non_flag_tokens),
        semantic_tokens=tuple(semantic_tokens),
        semantic_head_tokens=tuple(semantic_tokens[:head_size]),
    )


def _contains_ordered_sequence(tokens: Iterable[str], sequence: Iterable[str]) -> bool:
    """Return True when all sequence tokens appear in order, allowing gaps.

    Internal helper -- callers must supply pre-lowercased inputs.
    Both ``tokens`` (semantic_head_tokens) and ``sequence``
    (SemanticBlockedRule.sequence) are already lowercase when produced by
    :func:`analyze_command` and :class:`SemanticBlockedRule`.
    """
    needles = tuple(sequence)
    if not needles:
        return False

    index = 0
    for token in tokens:
        if token == needles[index]:
            index += 1
            if index == len(needles):
                return True
    return False


def _pathless(token: str) -> str:
    """Strip a leading path prefix from an executable token."""
    return token.rsplit("/", 1)[-1] if "/" in token else token


def _is_flag(token: str) -> bool:
    """Check whether a token is flag-shaped."""
    return token.startswith("-") and token != "-"


def _normalize_flag_token(token: str) -> Tuple[str, ...]:
    """Normalize flag tokens for matching while preserving exact variants."""
    token_lower = token.lower()

    if token_lower.startswith("--"):
        return (token_lower.split("=", 1)[0],)

    normalized = [token_lower]
    short_body = token_lower[1:]
    if len(short_body) > 1 and short_body.isalpha():
        normalized.extend(f"-{char}" for char in short_body)
    return tuple(normalized)
