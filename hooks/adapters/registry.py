"""
Adapter Registry / Factory for Gaia Hooks.

The single construction point for the host :class:`HookAdapter`. The entry
points and ``hook_entry`` / ``hook_response`` obtain their adapter through
:func:`get_adapter` and never name a concrete host class. Supporting a new host
is a :func:`register_adapter` call plus its ``HookAdapter`` subclass. The
adapter is stateless, so one instance per host is cached for the process.

The active host is ``GAIA_HOST`` when set, else :data:`DEFAULT_HOST`
(``"claude_code"``); see :func:`active_host`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Type

from .base import HookAdapter
from .claude_code import ClaudeCodeAdapter
from .opencode import OpenCodeAdapter

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
    mechanism_names=("askuserquestion",),
    surface_names=("claude",),
)
register_adapter(
    "opencode",
    OpenCodeAdapter,
    mechanism_names=json.loads(
        (Path(__file__).resolve().parents[2] / "opencode" / "consent-metadata.json")
        .read_text(encoding="utf-8")
    )["mechanism_names"],
    surface_names=("opencode",),
)


def active_host() -> str:
    """The host key ``get_adapter`` selects when none is named: ``GAIA_HOST``, else :data:`DEFAULT_HOST`."""
    return os.environ.get(HOST_ENV_VAR) or DEFAULT_HOST


def get_adapter(host: Optional[str] = None) -> HookAdapter:
    """Return the shared :class:`HookAdapter` instance for ``host``.

    Lazily builds and caches one instance per host. When ``host`` is omitted
    the key is :func:`active_host`.

    Raises:
        KeyError: If ``host`` has no registered adapter class.
    """
    key = host or active_host()
    instance = _INSTANCES.get(key)
    if instance is None:
        adapter_cls = _REGISTRY[key]
        instance = adapter_cls()
        _INSTANCES[key] = instance
    return instance
