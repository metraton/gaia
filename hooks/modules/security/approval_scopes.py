"""
Approval scope builders and matching for nonce-based T3 grants.

The approval system supports two explicit scope shapes:

- exact_command: tokenized command must match exactly
- semantic_signature: same semantic command and normalized flags
"""

from dataclasses import asdict, dataclass
from typing import Optional, Tuple

from .command_semantics import analyze_command, tokenize_command
from .dangerous_verbs import CATEGORY_UNKNOWN, CLI_FAMILY_LOOKUP, detect_dangerous_command

APPROVAL_SCOPE_VERSION = 2

SCOPE_EXACT_COMMAND = "exact_command"
SCOPE_SEMANTIC_SIGNATURE = "semantic_signature"

SUPPORTED_SCOPE_TYPES = frozenset({
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
})


@dataclass(frozen=True)
class ApprovalSignature:
    """Stable representation of an approved command scope."""

    version: int = APPROVAL_SCOPE_VERSION
    scope_type: str = SCOPE_SEMANTIC_SIGNATURE
    base_cmd: str = ""
    cli_family: str = "unknown"
    danger_category: str = CATEGORY_UNKNOWN
    verb: str = ""
    semantic_tokens: Tuple[str, ...] = ()
    normalized_flags: Tuple[str, ...] = ()
    dangerous_flags: Tuple[str, ...] = ()
    exact_tokens: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Return a JSON-serializable payload."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalSignature":
        """Build a signature from persisted JSON data."""
        return cls(
            version=int(data.get("version", APPROVAL_SCOPE_VERSION)),
            scope_type=data.get("scope_type", SCOPE_SEMANTIC_SIGNATURE),
            base_cmd=data.get("base_cmd", ""),
            cli_family=data.get("cli_family", "unknown"),
            danger_category=data.get("danger_category", CATEGORY_UNKNOWN),
            verb=data.get("verb", ""),
            semantic_tokens=tuple(data.get("semantic_tokens", ())),
            normalized_flags=tuple(data.get("normalized_flags", ())),
            dangerous_flags=tuple(data.get("dangerous_flags", ())),
            exact_tokens=tuple(data.get("exact_tokens", ())),
        )


def build_approval_signature(
    command: str,
    scope_type: str = SCOPE_SEMANTIC_SIGNATURE,
    *,
    danger_verb: str = "",
    danger_category: str = CATEGORY_UNKNOWN,
) -> Optional[ApprovalSignature]:
    """Build a stable scope signature for a command."""
    if scope_type not in SUPPORTED_SCOPE_TYPES:
        return None

    stripped = command.strip() if command else ""
    exact_tokens = tuple(tokenize_command(stripped))
    if not exact_tokens:
        return None

    semantics = analyze_command(stripped)
    if not semantics.base_cmd:
        return None

    danger = detect_dangerous_command(stripped)
    resolved_category = danger.category
    if resolved_category == CATEGORY_UNKNOWN and danger_category:
        resolved_category = danger_category
    if danger.category == CATEGORY_UNKNOWN and danger_verb:
        resolved_verb = danger_verb.lower()
    else:
        resolved_verb = (danger.verb or danger_verb or "").lower()

    if scope_type != SCOPE_EXACT_COMMAND and not resolved_verb:
        return None

    return ApprovalSignature(
        scope_type=scope_type,
        base_cmd=semantics.base_cmd,
        cli_family=CLI_FAMILY_LOOKUP.get(semantics.base_cmd, "unknown"),
        danger_category=resolved_category,
        verb=resolved_verb,
        semantic_tokens=tuple(semantics.semantic_tokens),
        normalized_flags=_sorted_unique_lower(semantics.flag_tokens),
        dangerous_flags=_sorted_unique_lower(danger.dangerous_flags),
        exact_tokens=exact_tokens,
    )


def matches_approval_signature(signature: ApprovalSignature, command: str) -> bool:
    """Return True when a command falls inside an approved scope."""
    stripped = command.strip() if command else ""
    exact_tokens = tuple(tokenize_command(stripped))
    if not exact_tokens:
        return False

    if signature.scope_type == SCOPE_EXACT_COMMAND:
        return exact_tokens == signature.exact_tokens

    if signature.scope_type not in SUPPORTED_SCOPE_TYPES:
        return False

    semantics = analyze_command(stripped)
    if semantics.base_cmd != signature.base_cmd:
        return False

    danger = detect_dangerous_command(stripped)
    incoming_dangerous_flags = _sorted_unique_lower(danger.dangerous_flags)
    if incoming_dangerous_flags != signature.dangerous_flags:
        return False

    if signature.verb and danger.verb and danger.verb.lower() != signature.verb:
        return False

    if (
        signature.danger_category != CATEGORY_UNKNOWN
        and danger.category != CATEGORY_UNKNOWN
        and danger.category != signature.danger_category
    ):
        return False

    incoming_semantic_tokens = tuple(semantics.semantic_tokens)
    if signature.scope_type == SCOPE_SEMANTIC_SIGNATURE:
        incoming_flags = _sorted_unique_lower(semantics.flag_tokens)
        return (
            incoming_semantic_tokens == signature.semantic_tokens
            and incoming_flags == signature.normalized_flags
        )

    return False


def _sorted_unique_lower(values: Tuple[str, ...] | list[str]) -> Tuple[str, ...]:
    """Normalize string tokens for deterministic matching."""
    return tuple(sorted({value.lower() for value in values if value}))
