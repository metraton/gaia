# gaia.contract -- Portable, harness-agnostic contract validation core.
#
# The single source of truth for validating an ``agent_contract_handoff``
# envelope, in two layers:
#
#   Layer 1 (form)       -- gaia.contract.validator: pure, stdlib-only SHAPE
#                           validation with NAMED error codes. Imports nothing
#                           from hooks/ and no third party (portability boundary
#                           enforced by tests/contract/test_validator_portable.py).
#   Layer 2 (cross-check)-- gaia.contract.crosscheck: consults gaia.db for
#                           approval_id / nonce resolution against the
#                           `approvals` table; degrades gracefully (no-op) to
#                           Layer 1 alone when gaia.db is absent.

# gaia.contract.validator is re-exported eagerly: it is on
# tests/contract/test_validator_portable.py's ALLOWED_GAIA_MODULES whitelist.
# gaia.contract.crosscheck is not, and Python runs a package's __init__.py before
# any of its submodules, so an eager import of crosscheck here would put layer 2
# in the module graph of every `from gaia.contract.validator import validate_form`.
# Its re-export is therefore lazy (PEP 562 module `__getattr__`): crosscheck is
# imported the first time one of _LAZY_CROSSCHECK_NAMES is read from this package.
from gaia.contract.validator import (  # noqa: E402
    CANONICAL_REPAIR_MESSAGE,
    ROW_ENVELOPE_REPAIR_MESSAGE,
    FormError,
    FormErrorCode,
    FormValidationResult,
    validate_form,
)

_LAZY_CROSSCHECK_NAMES = (
    "validate_crosscheck",
    "validate",
    "CrossCheckErrorCode",
    "CrossCheckError",
    "CrossCheckResult",
    "EnvelopeValidationResult",
    "CROSSCHECK_REPAIR_MESSAGE",
)


def __getattr__(name: str):
    """PEP 562 lazy attribute loader -- see NOTE above."""
    if name in _LAZY_CROSSCHECK_NAMES:
        from gaia.contract import crosscheck as _crosscheck

        return getattr(_crosscheck, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "validate_form",
    "FormErrorCode",
    "FormError",
    "FormValidationResult",
    "CANONICAL_REPAIR_MESSAGE",
    "ROW_ENVELOPE_REPAIR_MESSAGE",
    *_LAZY_CROSSCHECK_NAMES,
]
