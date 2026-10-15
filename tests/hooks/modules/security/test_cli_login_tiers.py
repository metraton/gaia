#!/usr/bin/env python3
"""A CLI login asks for a signature only when it would switch the active account.

`gcloud auth login` activates the new account by default, rewriting the account
every other session on the machine resolves to; `--no-activate` adds it without
touching that slot. `gaia login start` always runs the `--no-activate` form, so
it is free like the form it runs.
"""

import re
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

SWITCHES_ACTIVE_ACCOUNT = [
    ("gcloud-login-bare", "gcloud auth login"),
    ("gcloud-login-account", "gcloud auth login someone@example.com"),
    ("gcloud-login-explicit-activate", "gcloud auth login someone@example.com --activate"),
    ("gcloud-login-no-launch-browser", "gcloud auth login --no-launch-browser"),
]

FREE = [
    ("gcloud-login-no-activate", "gcloud auth login someone@example.com --no-activate"),
    ("gcloud-login-no-activate-first", "gcloud auth login --no-activate someone@example.com"),
    ("gcloud-auth-list", "gcloud auth list --filter=account:someone@example.com --format=value(account)"),
    ("gaia-login-start", "gaia login start gcloud --account someone@example.com"),
    ("gaia-login-status", "gaia login status 0123456789ab"),
]


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.mark.parametrize("case_id,command", SWITCHES_ACTIVE_ACCOUNT, ids=[c for c, _ in SWITCHES_ACTIVE_ACCOUNT])
def test_login_that_activates_the_account_requires_consent(case_id, command):
    result = detect_mutative_command(command)
    assert result.is_mutative is True, f"{case_id}: {result.category}: {result.reason}"
    assert classify_command_tier(command) == T3


@pytest.mark.parametrize("case_id,command", FREE, ids=[c for c, _ in FREE])
def test_login_that_keeps_the_active_account_is_free(case_id, command):
    result = detect_mutative_command(command)
    assert result.is_mutative is False, f"{case_id}: {result.category}: {result.reason}"
    assert classify_command_tier(command) == T0


def test_the_denial_names_free_ways_to_add_the_account():
    guidance = detect_mutative_command("gcloud auth login someone@example.com").guidance
    suggested = re.findall(r"`([^`]+)`", guidance)
    assert any("--no-activate" in s for s in suggested), guidance
    assert any(s.startswith("gaia login start") for s in suggested), guidance
    for suggestion in suggested:
        assert classify_command_tier(suggestion) == T0, f"suggested {suggestion!r} is gated"
