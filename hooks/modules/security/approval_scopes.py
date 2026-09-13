"""
Approval scope builders and matching for nonce-based T3 grants.

The approval system supports two explicit scope shapes:

- exact_command: tokenized command must match exactly
- semantic_signature: same semantic command and normalized flags

The verb_family scope type has been removed (M3 D7 decision).  Stale DB rows
with scope='verb_family' are inert -- they will not match any new grants.
"""

from dataclasses import asdict, dataclass
from typing import Optional, Tuple, Union

from .command_semantics import analyze_command, tokenize_command
from .mutative_verbs import CATEGORY_UNKNOWN, CLI_FAMILY_LOOKUP, detect_mutative_command

APPROVAL_SCOPE_VERSION = 2

SCOPE_EXACT_COMMAND = "exact_command"
SCOPE_SEMANTIC_SIGNATURE = "semantic_signature"
SCOPE_FILE_PATH = "file_path"

SUPPORTED_SCOPE_TYPES = frozenset({
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
    SCOPE_FILE_PATH,
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

    danger = detect_mutative_command(stripped)
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
        semantic_tokens=_approval_identity_tokens(semantics),
        normalized_flags=_sorted_unique(semantics.flag_tokens_raw),
        dangerous_flags=_sorted_unique(danger.dangerous_flags),
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

    # Identity for a semantic-signature grant is derived ENTIRELY from
    # analyze_command (base_cmd + semantic_tokens + normalized_flags) -- the
    # SAME source used at build time (build_approval_signature) and here at
    # match time, so the comparison is symmetric and reflexive for EVERY command
    # class, including curl (which reaches T3 via the flag classifier, not
    # detect_mutative_command). The previous verb/dangerous_flags guards re-ran
    # detect_mutative_command at match time, which is NOT the verb source used at
    # build for flag-classified commands: curl's grant verb is '-x put' but
    # detect_mutative_command(curl) returns the URL -> the verb-equality guard
    # rejected curl against its OWN grant on every retry. Byte-binding is fully
    # preserved: semantic_tokens captures every non-flag token (incl. the URL and
    # positional args) and normalized_flags captures every flag token (incl. the
    # -d JSON body, -H auth header, -X method).
    semantics = analyze_command(stripped)
    if semantics.base_cmd != signature.base_cmd:
        return False

    if signature.scope_type == SCOPE_SEMANTIC_SIGNATURE:
        incoming_semantic_tokens = _approval_identity_tokens(semantics)
        incoming_flags = _sorted_unique(semantics.flag_tokens_raw)
        return (
            incoming_semantic_tokens == signature.semantic_tokens
            and incoming_flags == signature.normalized_flags
        )

    return False


def build_file_path_signature(file_path: str) -> Optional[ApprovalSignature]:
    """Build a stable scope signature for a Write/Edit file path.

    Unlike build_approval_signature, this does not parse a shell command.
    The file path is stored verbatim as a single exact_token so that
    matches_approval_signature can do exact-path matching.

    Args:
        file_path: Absolute or relative path to the file being written/edited.

    Returns:
        ApprovalSignature with scope_type=SCOPE_FILE_PATH, or None if the
        path is empty.
    """
    stripped = file_path.strip() if file_path else ""
    if not stripped:
        return None

    return ApprovalSignature(
        version=APPROVAL_SCOPE_VERSION,
        scope_type=SCOPE_FILE_PATH,
        base_cmd="",
        cli_family="unknown",
        danger_category="FILE_WRITE",
        verb="write",
        semantic_tokens=(),
        normalized_flags=(),
        dangerous_flags=(),
        exact_tokens=(stripped,),
    )


def matches_file_path_approval(signature: ApprovalSignature, file_path: str) -> bool:
    """Return True when file_path is covered by a SCOPE_FILE_PATH grant.

    Exact-path comparison only -- both sides are normalised by stripping
    leading/trailing whitespace.

    Symlink resolution is NOT performed here, and must already have happened to
    both sides before they arrive: the hook resolves a write target once
    (``protected_paths.resolved_write_target``) and keys the grant, the pending
    and the consent surface on that single form. Comparing raw here is what
    makes a link retargeted after the grant was minted resolve elsewhere on the
    retry, fail to match, and be blocked again under a new approval_id -- the
    intended behaviour, not a gap for this function to close.

    Args:
        signature: The ApprovalSignature from a stored grant.
        file_path: The file path being written/edited right now.

    Returns:
        True if the signature covers this file path.
    """
    if signature.scope_type != SCOPE_FILE_PATH:
        return False
    stripped = file_path.strip() if file_path else ""
    return bool(signature.exact_tokens) and signature.exact_tokens[0] == stripped


def _approval_identity_tokens(semantics) -> Tuple[str, ...]:
    """The tokens a grant binds to: the command, then its operands verbatim.

    ``analyze_command`` produces two views of the same operands and they are not
    interchangeable here. CLASSIFICATION reads the folded one
    (``semantic_tokens``), correctly: a mutative verb is the verb however it is
    typed, and unfolding it would open gating holes. An APPROVAL SIGNATURE binds
    a consent to the thing consented over, and ``s3://bucket/Archive/old.tar``
    and ``s3://bucket/archive/old.tar`` are two objects -- as are two POSIX
    paths or two git refs differing only in case.

    ``base_cmd`` stays folded because it names the CLI, not an object, and the
    signature already compares it as its own field.
    """
    return (semantics.base_cmd, *semantics.non_flag_tokens_raw)


def _sorted_unique(values: Union[Tuple[str, ...], list[str]]) -> Tuple[str, ...]:
    """Normalize flag tokens for deterministic matching.

    Order and multiplicity are dropped because neither carries meaning in the
    CLIs this layer classifies. Case is NOT dropped: ``-d`` and ``-D`` are git's
    safe and force deletions, and folding them let a grant minted for one be
    consumed by the other.
    """
    return tuple(sorted({value for value in values if value}))
