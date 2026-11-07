"""The CLI a user's tool stands in for, read from the user's ``kind=tool`` memory rows.

A wrapper the user built around a CLI (a launcher that pins an account, say)
takes the same subcommands and has the same effects, so every gate keyed on
the wrapped CLI -- the permanent-deny floor included -- must reach it too.
Gaia knows no wrapper by name: the mapping is the ``replaces`` column of a
tool row (``gaia memory add --type=user --kind=tool --binary=<name>
--replaces=<cli>``), read once per hook process; a read that fails means no
alias, never a guess.

The wrapped CLI's verdict counts in both directions only for a wrapper the
classifier knows nothing about: it then inherits that CLI's exceptions as well
as its gates. A wrapper named after a CLI the classifier models keeps its own
gates, the stricter verdict standing, and the permanent-deny floor always takes
the stricter of both readings.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Dict, Optional, Tuple

# Leading `env` and NAME=value assignments stay in place; the token after them
# is the command the shell runs.
_LEADING_COMMAND = re.compile(
    r"^(\s*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)(\S+)(.*)$", re.DOTALL,
)

_CACHE: Optional[Dict[str, str]] = None


def _load_tool_aliases() -> Dict[str, str]:
    try:
        from gaia.store.reader import user_tool_rows

        rows = user_tool_rows()
    except Exception:
        return {}
    aliases: Dict[str, str] = {}
    for row in rows:
        binary, target = row.get("binary"), row.get("replaces")
        if binary and target and binary != target:
            aliases[binary] = target
    return aliases


def declared_aliases() -> Dict[str, str]:
    """``{binary: cli}`` from the live tool rows, read once per process; ``{}`` when the read fails."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_tool_aliases()
    return _CACHE


def reset_cache() -> None:
    """Forget the rows read so far; the next lookup reads them again."""
    global _CACHE
    _CACHE = None


def aliased_cli(base_cmd: str) -> Optional[str]:
    """Return the CLI a tool row declares *base_cmd* replaces, or None."""
    return declared_aliases().get(base_cmd)


def is_cli(base_cmd: str, cli: str) -> bool:
    """True when *base_cmd* is *cli* itself or a tool declared to replace it."""
    return base_cmd == cli or aliased_cli(base_cmd) == cli


@lru_cache(maxsize=8)
def _wrapper_token(wrappers: Tuple[str, ...]) -> "re.Pattern[str]":
    # A wrapper counts only as a whole token: `/usr/bin/ghwrap` and `$(ghwrap`
    # qualify, `ghwrapper`, `x-ghwrap`, `$ghwrap` and `ghwrap=1` do not.
    names = "|".join(re.escape(name) for name in sorted(wrappers, key=len, reverse=True))
    return re.compile(rf"(?<![\w.$-])(?:{names})(?![\w.=-])")


def with_wrappers_resolved(command: str) -> Optional[str]:
    """Return *command* with every declared wrapper token replaced by its CLI, or None.

    Unlike ``as_wrapped_cli`` this reaches every position, so a wrapper inside
    a substitution, a group or a chain meets the rules its CLI would meet there.
    """
    aliases = declared_aliases()
    if not aliases or not command:
        return None
    resolved = _wrapper_token(tuple(aliases)).sub(lambda m: aliases[m.group(0)], command)
    return resolved if resolved != command else None


def declared_wrapper(command: str) -> Optional[Tuple[str, str]]:
    """Return ``(wrapper, command as the wrapped CLI)`` when *command* runs a declared wrapper."""
    match = _LEADING_COMMAND.match(command or "")
    if not match:
        return None
    prefix, head, rest = match.groups()
    wrapper = os.path.basename(head)
    target = aliased_cli(wrapper)
    if target is None:
        return None
    return wrapper, f"{prefix}{target}{rest}"


def as_wrapped_cli(command: str) -> Optional[str]:
    """Return *command* with a declared wrapper replaced by the CLI it wraps, or None."""
    declared = declared_wrapper(command)
    return None if declared is None else declared[1]
