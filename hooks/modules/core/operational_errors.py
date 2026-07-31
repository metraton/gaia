"""Classification of hook infrastructure failures.

Operational failures must not masquerade as security-policy denials.  The
helpers here intentionally recognize only high-confidence storage exhaustion
signals; unknown exceptions retain the existing fail-closed behavior.
"""

from __future__ import annotations

import errno
from typing import Optional


_STORAGE_EXHAUSTION_MESSAGES = (
    "no space left on device",
    "database or disk is full",
    "disk quota exceeded",
)


def storage_exhaustion_message(exc: BaseException) -> Optional[str]:
    """Return an operational error message when *exc* means storage exhaustion."""
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in {
            errno.ENOSPC,
            errno.EDQUOT,
        }:
            return (
                "Operational hook failure: local storage is exhausted. "
                "This is not a security-policy denial. Free disk space or "
                "quota, then retry the original operation."
            )
        text = str(current).lower()
        if any(marker in text for marker in _STORAGE_EXHAUSTION_MESSAGES):
            return (
                "Operational hook failure: local storage is exhausted. "
                "This is not a security-policy denial. Free disk space or "
                "quota, then retry the original operation."
            )
        current = current.__cause__ or current.__context__
    return None
