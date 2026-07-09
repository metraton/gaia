# gaia.contract -- Portable, harness-agnostic contract validation core.
#
# The single source of truth for validating an ``agent_contract_handoff``
# envelope. Two layers (brief:
# contract-as-managed-data-agent-contract-handoff-agnostico-por-cli):
#
#   Layer 1 (form)       -- gaia.contract.validator: pure, stdlib-only SHAPE
#                           validation with NAMED error codes. Imports nothing
#                           from hooks/ and no third party (portability boundary
#                           enforced by tests/contract/test_validator_portable.py).
#   Layer 2 (cross-check)-- gaia.contract.crosscheck: consults gaia.db for
#                           approval_id / nonce resolution against the
#                           `approvals` table; degrades gracefully (no-op) to
#                           Layer 1 alone when gaia.db is absent. Never
#                           imports hooks/ (portability/agnosticism holds).
#
# Public surface:
#   from gaia.contract.validator import (
#       validate_form, FormErrorCode, FormError, FormValidationResult,
#       CANONICAL_REPAIR_MESSAGE,
#   )
#   from gaia.contract.crosscheck import (
#       validate_crosscheck, validate, CrossCheckErrorCode, CrossCheckError,
#       CrossCheckResult, EnvelopeValidationResult, CROSSCHECK_REPAIR_MESSAGE,
#   )

# NOTE (T3, portability-critical): gaia.contract.validator is eagerly
# re-exported here -- it is on tests/contract/test_validator_portable.py's
# ALLOWED_GAIA_MODULES whitelist, so importing it at package-init time never
# breaks AC-2. gaia.contract.crosscheck is NOT on that whitelist (it pulls in
# gaia.paths / gaia.approvals.store, both real dependencies of layer 2's
# gaia.db cross-check). Since Python always executes a package's __init__.py
# before any of its submodules, an eager `from gaia.contract.crosscheck import
# ...` HERE would make even `from gaia.contract.validator import validate_form`
# transitively pull in layer 2 -- breaking layer 1's portability boundary for
# every caller, not just those who want layer 2. So crosscheck's re-export is
# LAZY (PEP 562 module `__getattr__`): `gaia.contract.crosscheck` is only
# imported the first time an attribute below is actually accessed as
# `gaia.contract.<name>`; plain `import gaia.contract.validator` never
# triggers it. Direct `from gaia.contract.crosscheck import ...` (as AC-3's
# test suite does) is unaffected either way.
from gaia.contract.validator import (  # noqa: E402  (re-export, appended T3)
    CANONICAL_REPAIR_MESSAGE,
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
    *_LAZY_CROSSCHECK_NAMES,
]
