#!/usr/bin/env python3
"""gcloud anchors still match behind a global flag that takes its value as the next token.

`gcloud --project p auth login` read `p` as the command group, so every
anchor declared for gcloud missed. The verifier's 11-form probe of the login
gate is kept here as a test, so re-running it needs no inline probe.
"""

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

BEHIND_A_GLOBAL_FLAG = [
    ("auth-login-project", "gcloud --project p auth login a@b.co", T3),
    ("auth-login-account", "gcloud --account x@y.co auth login a@b.co", T3),
    ("auth-login-configuration", "gcloud --configuration c auth login a@b.co", T3),
    ("auth-login-flags-file-global", "gcloud --flags-file f.yaml auth login a@b.co --no-activate", T3),
    ("auth-login-no-activate", "gcloud --project p auth login a@b.co --no-activate", T0),
    ("secrets-versions-add", "gcloud --project p secrets versions add s --data-file=v.txt", T3),
    ("sql-users-set-password", "gcloud --project p sql users set-password u --instance i", T3),
]

VERIFIER_PROBE = [
    ("gcloud auth login a@b.co --no-activate --activate", True),
    ("gcloud auth login a@b.co --activate --no-activate", False),
    ("gcloud auth login a@b.co --no-activate --update-adc", True),
    ("gcloud auth login a@b.co --update-adc --no-activate", True),
    ("gcloud beta auth login a@b.co", True),
    ("gcloud alpha auth login a@b.co", True),
    ("gcloud --project p auth login a@b.co", True),
    ("gcloud --verbosity=info auth login a@b.co", True),
    ("gcloud auth login a@b.co --no-activate --activate=true", True),
    ("gcloud auth login a@b.co --no-activate --update-adc=true", True),
    ("gcloud auth login a@b.co --no-activate --flags-file=f.yaml", True),
]


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.mark.parametrize(
    "case_id,command,tier", BEHIND_A_GLOBAL_FLAG, ids=[c for c, _, _ in BEHIND_A_GLOBAL_FLAG]
)
def test_anchor_matches_behind_a_global_value_flag(case_id, command, tier):
    assert classify_command_tier(command) == tier, detect_mutative_command(command).reason


@pytest.mark.parametrize("command,mutative", VERIFIER_PROBE, ids=[c for c, _ in VERIFIER_PROBE])
def test_verifier_probe(command, mutative):
    assert detect_mutative_command(command).is_mutative is mutative
    assert classify_command_tier(command) == (T3 if mutative else T0)


def test_flags_file_on_the_login_asks_for_a_signature():
    assert classify_command_tier("gcloud auth login a@b.co --no-activate --flags-file f.yaml") == T3
