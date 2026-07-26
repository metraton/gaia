"""Conforming ``agent_id`` handles for tests, minted instead of hand-written.

Every handle a test hands to the contract validator, to the ``gaia contract``
CLI, or to ``validate_response_contract`` has to satisfy
``gaia.contract.validator.AGENT_ID_PATTERN_TEXT``. Hand-written literals do
not survive a change to that floor: they were written against an older,
shorter one and each raise turns into a file-by-file sweep of string
constants -- a sweep that silently misses every handle it does not happen to
match textually.

Minting here removes that class of breakage. The length is read from
``AGENT_ID_MIN_HEX`` rather than copied, so raising the floor again costs
nothing on the test side: every handle these helpers produce conforms by
construction, at whatever the floor currently is.

Handles are derived deterministically from a seed, so a failing run
reproduces exactly and two different seeds never collide inside a module.
Tests that deliberately exercise REJECTION must keep writing their invalid
handle inline -- an invalid value is the subject of those assertions, not an
incidental fixture, and it must not silently become valid when the floor
moves.
"""

import hashlib

from gaia.contract.validator import AGENT_ID_MIN_HEX


def valid_agent_id(seed: str) -> str:
    """Return a conforming ``agent_id`` derived deterministically from ``seed``.

    The digest is repeated before slicing so the helper keeps working if the
    floor is ever raised past the 64 hex digits a single sha256 provides.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    repeats = -(-AGENT_ID_MIN_HEX // len(digest))
    return "a" + (digest * repeats)[:AGENT_ID_MIN_HEX]
