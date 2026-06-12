"""
Shell Unwrapper - Detect and strip wrapper shells from commands.

Many commands arrive wrapped in a shell invocation:
    bash -c "actual command"
    sh -c 'rm -rf /tmp/build'
    env bash -c "sh -c 'inner command'"

The wrapped inner command is what matters for classification, not the
wrapper itself.  ShellUnwrapper recursively peels wrapper shells until
it reaches the actual payload.

Handles:
- bash -c, sh -c, zsh -c, dash -c
- /bin/bash -c, /usr/bin/env bash -c
- exec bash -c
- env bash -c (with optional env vars like VAR=val)
- Nested wrappers: bash -c "sh -c 'inner'"
- Single-quoted, double-quoted, and unquoted payloads

Dependencies: Python stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class UnwrapResult:
    """Result of unwrapping a shell command."""

    # The innermost command after all wrappers are stripped.
    inner: str
    # Number of wrapper layers removed (0 = no wrapper detected).
    depth: int
    # True if any wrapper was detected and stripped.
    was_wrapped: bool

    def __str__(self) -> str:
        return self.inner


# ---------------------------------------------------------------------------
# Wrapper detection patterns
# ---------------------------------------------------------------------------
# Optional path prefix: /bin/, /usr/bin/, /usr/local/bin/
_OPT_PATH = r"(?:/(?:usr/(?:local/)?)?s?bin/)?"

# Optional prefix commands that can precede the shell: env, exec, nohup,
# sudo, nice, etc.  env can carry VAR=val assignments before the shell.
_PREFIX = (
    r"(?:(?:exec|nohup|sudo|nice|ionice|setsid|time)\s+)*"
    r"(?:(?:" + _OPT_PATH + r")?env\s+(?:[A-Za-z_][A-Za-z_0-9]*=[^\s]*\s+)*)?"
)

# Shell interpreters that accept -c.
_SHELLS = r"(?:bash|sh|zsh|dash|ksh)"

# Combined regex: optional_prefix + optional_path + shell + -c + payload
# Three capture groups after -c for the three quoting styles:
#   group 1: double-quoted payload
#   group 2: single-quoted payload
#   group 3: unquoted payload (rest of string)
_WRAPPER_RE = re.compile(
    r"^\s*"
    + _PREFIX
    + _OPT_PATH
    + _SHELLS
    + r"""\s+-c\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S.*))""",
    re.DOTALL,
)

# Maximum recursion depth to prevent infinite loops on pathological input.
_MAX_DEPTH = 10


class ShellUnwrapper:
    """
    Detect and recursively strip shell wrapper invocations.

    Zero external dependencies -- Python stdlib only.

    Usage::

        unwrapper = ShellUnwrapper()
        result = unwrapper.unwrap('bash -c "ls -la"')
        # result.inner      == "ls -la"
        # result.depth      == 1
        # result.was_wrapped == True

        result = unwrapper.unwrap("ls -la")
        # result.inner      == "ls -la"
        # result.depth      == 0
        # result.was_wrapped == False
    """

    def unwrap(self, command: str) -> UnwrapResult:
        """
        Recursively strip shell wrappers from *command*.

        Args:
            command: Raw shell command string.

        Returns:
            UnwrapResult with the innermost command, depth, and whether
            any wrapper was detected.
        """
        if not command or not command.strip():
            return UnwrapResult(inner=command or "", depth=0, was_wrapped=False)

        current = command.strip()
        depth = 0

        while depth < _MAX_DEPTH:
            inner = self._try_unwrap_once(current)
            if inner is None:
                break
            current = inner.strip()
            depth += 1

        return UnwrapResult(
            inner=current,
            depth=depth,
            was_wrapped=depth > 0,
        )

    def is_wrapped(self, command: str) -> bool:
        """Return True if *command* has a shell wrapper layer."""
        if not command or not command.strip():
            return False
        return self._try_unwrap_once(command.strip()) is not None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _try_unwrap_once(self, command: str) -> Optional[str]:
        """
        Attempt to strip one wrapper layer.

        Returns the inner payload string, or None if no wrapper detected.
        """
        m = _WRAPPER_RE.match(command)
        if m is None:
            return None

        # Exactly one of the three groups will be non-None.
        payload = m.group(1)  # double-quoted
        if payload is not None:
            # Unescape \" and \\ inside double-quoted payload
            payload = payload.replace('\\"', '"').replace("\\\\", "\\")
            return payload.strip()

        payload = m.group(2)  # single-quoted
        if payload is not None:
            return payload.strip()

        payload = m.group(3)  # unquoted
        if payload is not None:
            return payload.strip()

        return None
