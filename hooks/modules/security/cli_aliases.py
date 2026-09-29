"""The CLI a user-declared wrapper stands in for, read from ``GAIA_CLI_ALIASES``.

A wrapper the user installed around a CLI (a launcher that pins an account,
say) takes the same subcommands and has the same effects, so every gate keyed
on the wrapped CLI -- the permanent-deny floor included -- must reach it too.
Gaia knows no wrapper by name: the mapping exists only when the user declares
it, as comma-separated ``wrapper=cli`` pairs (``GAIA_CLI_ALIASES="mywrap=gh"``).
Nothing declared means no alias. Malformed pairs are ignored rather than
guessed at.

Callers treat a declared alias as escalate-only: the wrapper is classified
both as written and as the wrapped CLI, and the stricter verdict stands, so
the variable can cost a consent prompt or a block but can never open one.

The tier classifier caches its verdicts per command string, so the
declaration is assumed fixed for the life of the hook process, which is how
the host delivers it.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Dict, Optional

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


def as_wrapped_cli(command: str) -> Optional[str]:
    """Return *command* with a declared wrapper replaced by the CLI it wraps, or None."""
    match = _LEADING_COMMAND.match(command or "")
    if not match:
        return None
    prefix, head, rest = match.groups()
    target = aliased_cli(os.path.basename(head))
    if target is None:
        return None
    return f"{prefix}{target}{rest}"
