"""
Adapter Registry / Factory for Gaia Hooks.

The single construction point for the host :class:`HookAdapter`. Every entry
point (``pre_tool_use``, ``post_tool_use``, ``stop_hook``, ``subagent_start``,
``subagent_stop``, ``task_completed``, ``hook_entry``) and the shared
``hook_response`` builder obtain their adapter through :func:`get_adapter`
instead of calling ``ClaudeCodeAdapter()`` directly. Concentrating the
``ClaudeCodeAdapter`` reference here means the core never names a concrete host
class at a call site (AC-5): supporting a new host is a one-line
:func:`register_adapter` call, not an edit to every entry point one by one
(AC-7 / brief #88 "Desacoplar la lógica de Gaia de Claude Code").

Mirrors the ``channel.py`` / ``host_session.py`` / ``host_transcript.py``
pattern: a small standalone module under ``adapters/`` that owns a single
host-coupling concern -- here, *which adapter class to build* -- and is imported
by callers. The adapter is stateless (no ``__init__``, no mutable instance
attributes), so a single cached instance is shared process-wide; this matches
the long-standing module-level ``_adapter = ClaudeCodeAdapter()`` singleton in
``modules/tools/hook_response.py`` that this registry now subsumes.

Host selection
--------------
The active host is keyed by :data:`DEFAULT_HOST` (``"claude_code"``). A future
host registers its class and, when more than one is installed, ``get_adapter``
can be extended to resolve the key from a host-detection signal -- without any
entry point changing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Type

from .base import HookAdapter
from .claude_code import ClaudeCodeAdapter
from .opencode import OpenCodeAdapter

# The only host Gaia ships an adapter for today. Confined to this module so the
# concrete class name appears at exactly one call site in the whole core.
DEFAULT_HOST = "claude_code"
HOST_ENV_VAR = "GAIA_HOST"

@dataclass(frozen=True)
class HostAdapterMetadata:
    """Documentation metadata declared when a host adapter is registered."""

    mechanism_names: tuple[str, ...] = ()
    surface_names: tuple[str, ...] = ()
    skill_document: Optional[str] = None


_REGISTRY: Dict[str, Type[HookAdapter]] = {}
_HOST_METADATA: Dict[str, HostAdapterMetadata] = {}

# Cache of constructed adapters, keyed by host. The adapter is stateless, so a
# single instance per host is reused for the life of the process.
_INSTANCES: Dict[str, HookAdapter] = {}


def register_adapter(
    host: str,
    adapter_cls: Type[HookAdapter],
    *,
    mechanism_names: Iterable[str] = (),
    surface_names: Iterable[str] = (),
    skill_document: Optional[str] = None,
) -> None:
    """Register ``adapter_cls`` as the adapter for host key ``host``.

    Supporting a new host CLI is this call plus the new ``HookAdapter``
    subclass -- no entry point changes. Re-registering a host replaces the
    class and drops any cached instance so the next :func:`get_adapter` builds
    the new one.

    Raises:
        TypeError: If ``adapter_cls`` is not a ``HookAdapter`` subclass.
    """
    if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, HookAdapter)):
        raise TypeError(
            f"adapter_cls must be a HookAdapter subclass, got {adapter_cls!r}"
        )
    _REGISTRY[host] = adapter_cls
    _HOST_METADATA[host] = HostAdapterMetadata(
        mechanism_names=tuple(mechanism_names),
        surface_names=tuple(surface_names),
        skill_document=skill_document,
    )
    _INSTANCES.pop(host, None)


def registered_host_mechanism_names() -> tuple[str, ...]:
    """Return the mechanism names declared by registered host adapters."""
    return tuple(
        name
        for metadata in _HOST_METADATA.values()
        for name in metadata.mechanism_names
    )


def registered_adapter_skill_documents() -> tuple[str, ...]:
    """Return skill documents declared by registered host adapters."""
    return tuple(
        metadata.skill_document
        for metadata in _HOST_METADATA.values()
        if metadata.skill_document is not None
    )


def registered_host_surface_names() -> tuple[str, ...]:
    """Return host names declared for presentation-surface checks."""
    return tuple(
        name
        for metadata in _HOST_METADATA.values()
        for name in metadata.surface_names
    )


register_adapter(
    DEFAULT_HOST,
    ClaudeCodeAdapter,
    mechanism_names=("askuserquestion", "elicitationresult"),
    surface_names=("claude",),
    skill_document="claude-code-consent-adapter/SKILL.md",
)
register_adapter(
    "opencode",
    OpenCodeAdapter,
    mechanism_names=("permission.replied", "permission.v2.replied"),
    surface_names=("opencode",),
)


def get_adapter(host: Optional[str] = None) -> HookAdapter:
    """Return the shared :class:`HookAdapter` instance for ``host``.

    The single construction point for the host adapter. Lazily builds and
    caches one instance per host (the adapter is stateless, so the instance is
    safe to share). Defaults to :data:`DEFAULT_HOST` when ``host`` is omitted.

    Raises:
        KeyError: If ``host`` has no registered adapter class.
    """
    key = host or os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)
    instance = _INSTANCES.get(key)
    if instance is None:
        adapter_cls = _REGISTRY[key]
        instance = adapter_cls()
        _INSTANCES[key] = instance
    return instance
