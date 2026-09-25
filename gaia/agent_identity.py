"""The one place an agent name from outside Gaia becomes the name Gaia compares.

Claude Code renames every agent a plugin ships to ``<plugin>:<name>``, so the
same agent reaches Gaia as ``gaia-orchestrator`` from an npm install and as
``gaia:gaia-orchestrator`` from the plugin. Every identity check in Gaia --
the orchestrator's delegate-mode role, the memory and handoff write guards,
the fleet and roster lookups -- is written against the bare name, so the
namespace is removed once, where the name enters, and nowhere else.

Only Gaia's own namespace is removed. A name carrying any other plugin's
namespace (``other:gaia-orchestrator``) is returned unchanged, so it never
equals a bare Gaia name and cannot borrow the permissions of one.
"""

from __future__ import annotations

import os

# The plugin name Claude Code prefixes to Gaia's agents. Must equal ``name`` in
# .claude-plugin/plugin.json (generated from build/gaia.manifest.json, the one
# plugin scripts/build-plugin.py builds): if they diverge, every plugin-prefixed
# agent is read as a foreign one and loses its identity again.
# tests/test_agent_identity.py pins the equality.
PLUGIN_NAMESPACE = "gaia"

DISPATCH_AGENT_ENV = "GAIA_DISPATCH_AGENT"


def canonical_agent_name(name: object) -> str:
    """Return *name* without Gaia's plugin namespace; any other string is returned as is.

    Whitespace is left alone on purpose: each guard already decides for itself
    whether a blank identity means "unset" or "refuse", and that decision is
    not this function's to change.
    """
    if not isinstance(name, str):
        return ""
    prefix = f"{PLUGIN_NAMESPACE}:"
    if name.startswith(prefix) and len(name) > len(prefix):
        return name[len(prefix):]
    return name


def dispatch_agent_from_env() -> str:
    """Return the canonical dispatched-agent name, or "" for a human CLI caller."""
    return canonical_agent_name(os.environ.get(DISPATCH_AGENT_ENV))
