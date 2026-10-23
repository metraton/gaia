"""The CLI a user-declared wrapper stands in for, read from ``GAIA_CLI_ALIASES``.

A wrapper the user installed around a CLI (a launcher that pins an account,
say) takes the same subcommands and has the same effects, so every gate keyed
on the wrapped CLI -- the permanent-deny floor included -- must reach it too.
Gaia knows no wrapper by name: the mapping exists only when the user declares
it, as comma-separated ``wrapper=cli`` pairs (``GAIA_CLI_ALIASES="mywrap=gh"``).
Nothing declared means no alias. Malformed pairs are ignored rather than
guessed at.

The wrapped CLI's verdict counts in both directions only for a wrapper the
classifier knows nothing about: it then inherits that CLI's exceptions as well
as its gates. A wrapper named after a CLI the classifier models keeps its own
gates, the stricter verdict standing, and the permanent-deny floor always takes
the stricter of both readings.

The tier classifier caches its verdicts per command string, so the
declaration is assumed fixed for the life of the hook process, which is how
the host delivers it.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Dict, Optional, Tuple

ENV_VAR = "GAIA_CLI_ALIASES"

# Leading `env` and NAME=value assignments stay in place; the token after them
# is the command the shell runs.
_LEADING_COMMAND = re.compile(
    r"^(\s*(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)(\S+)(.*)$", re.DOTALL,
)


@lru_cache(maxsize=8)
def _parse(raw: str) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for pair in raw.split(","):
        wrapper, sep, target = pair.partition("=")
        wrapper, target = wrapper.strip(), target.strip()
        if not sep or not wrapper or not target or wrapper == target:
            continue
        if any(ch.isspace() or ch == "/" for ch in wrapper + target):
            continue
        aliases[wrapper] = target
    return aliases


def aliased_cli(base_cmd: str) -> Optional[str]:
    """Return the CLI the user declared *base_cmd* to wrap, or None."""
    return _parse(os.environ.get(ENV_VAR, "")).get(base_cmd)


def is_cli(base_cmd: str, cli: str) -> bool:
    """True when *base_cmd* is *cli* itself or a wrapper the user declared for it."""
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
    aliases = _parse(os.environ.get(ENV_VAR, ""))
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
