"""The CLI a user-declared wrapper stands in for, read from ``GAIA_CLI_ALIASES``.

A wrapper the user installed around a CLI (a launcher that pins an account,
say) takes the same subcommands and has the same effects, so the gates keyed
on the wrapped CLI must reach it too. Gaia knows no wrapper by name: the
mapping exists only when the user declares it, as comma-separated
``wrapper=cli`` pairs (``GAIA_CLI_ALIASES="ghx=gh"``). Nothing declared means
no alias. Malformed pairs are ignored rather than guessed at.

Callers treat a declared alias as escalate-only -- it adds the wrapped CLI's
gates to the wrapper and never replaces a CLI's own -- so the variable can
cost a consent prompt but can never open one.

The classifier caches its verdicts per command string, so the declaration is
assumed fixed for the life of the hook process, which is how the host
delivers it.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, Optional

ENV_VAR = "GAIA_CLI_ALIASES"


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
