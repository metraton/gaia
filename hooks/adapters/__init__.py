"""
Adapter Layer for Gaia-Ops Hooks.

Provides CLI-agnostic normalized types and the abstract HookAdapter interface.
Business logic modules consume and produce these types; concrete adapters
translate between these types and CLI-specific JSON protocols.

Modules:
- types: Frozen dataclasses and enums for all hook event/response data
- base: Abstract HookAdapter interface
"""

from .types import (
    HookEventType,
    PermissionDecision,
    DistributionChannel,
    HostCapability,
    HookEvent,
    ValidationRequest,
    ValidationResult,
    ConsentRequest,
    CapabilityDegradation,
    ToolResult,
    AgentCompletion,
    CompletionResult,
    ContextResult,
    BootstrapResult,
    QualityResult,
    VerificationResult,
    HookResponse,
)
from .base import HookAdapter
from .claude_code import ClaudeCodeAdapter
from .registry import get_adapter, register_adapter, DEFAULT_HOST
from .utils import has_stdin_data, warn_if_dual_channel

__all__ = [
    "HookEventType",
    "PermissionDecision",
    "DistributionChannel",
    "HostCapability",
    "HookEvent",
    "ValidationRequest",
    "ValidationResult",
    "ConsentRequest",
    "CapabilityDegradation",
    "ToolResult",
    "AgentCompletion",
    "CompletionResult",
    "ContextResult",
    "BootstrapResult",
    "QualityResult",
    "VerificationResult",
    "HookResponse",
    "HookAdapter",
    "ClaudeCodeAdapter",
    "get_adapter",
    "register_adapter",
    "DEFAULT_HOST",
    "has_stdin_data",
    "warn_if_dual_channel",
]
