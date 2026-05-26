#!/usr/bin/env python3
# test_batch_approval.py -- removed in M3 (verb_family path removed)
#
# The SCOPE_VERB_FAMILY / create_verb_family_grant path was removed in
# agent-contract-handoff M3. COMMAND_SET grants replace the batch mechanism.
# Tests for the new COMMAND_SET path live in tests/hooks/test_approval_grants.py.
import pytest

pytest.skip(
    "verb_family path removed in M3 -- see tests/hooks/test_approval_grants.py",
    allow_module_level=True,
)


def test_placeholder():
    """Placeholder to keep pytest collection happy."""
    pass
