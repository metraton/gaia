#!/usr/bin/env python3
"""A formatter rewrites files unless a flag it honors turns that off.

``terragrunt hclfmt --check --working-dir <dir>`` on v0.72.6 knows neither flag
(the real ones are ``--terragrunt-check`` and ``--terragrunt-working-dir``), so
it ran against the shell's cwd and rewrote HCL in four unrelated repositories
while the classifier read it as a check. Each case is a literal command fed to
the live classifier.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

REWRITING_FORMATTERS = [
    "terragrunt hclfmt --check --working-dir /repo",
    "terragrunt hclfmt --terragrunt-diff",
    "terragrunt hclfmt",
    "terragrunt hclfmt --terragrunt-check=false",
    "terragrunt hclfmt --terragrunt-check --terragrunt-check=false",
    "terragrunt fmt",
    "terragrunt hcl fmt",
    "terraform fmt",
    "terraform fmt -diff",
    "terraform fmt -recursive",
    "terraform fmt -check=false",
    "terraform fmt -check -check=false",
    "terraform fmt -write=true",
    "terraform fmt -write=false -write=true",
    "terraform -chdir=/repo fmt",
    "tofu fmt",
    "tofu fmt -recursive",
]

CHECKING_FORMATTERS = [
    "terragrunt hclfmt --terragrunt-check",
    "terragrunt hclfmt --terragrunt-check --terragrunt-working-dir /repo",
    "terragrunt fmt -check",
    "terragrunt hcl fmt --check",
    "terraform fmt -check",
    "terraform fmt -check -recursive",
    "terraform fmt -recursive -check",
    "terraform fmt --check",
    "terraform fmt -write=false",
    "terraform -chdir=/repo fmt -check",
    "tofu fmt -check",
    "tofu fmt -write=false -diff",
    "terraform fmt -check=false -check",
    "terragrunt hclfmt --terragrunt-hclfmt-stdin",
]

NEARBY_READS = [
    ("terraform validate", SecurityTier.T1_VALIDATION),
    ("terraform plan", SecurityTier.T2_DRY_RUN),
    ("terragrunt plan", SecurityTier.T2_DRY_RUN),
]

HELP_FORMS = ["terraform fmt --help", "terragrunt hclfmt --help", "tofu fmt --help"]


@pytest.mark.parametrize("command", REWRITING_FORMATTERS)
def test_formatter_without_a_honored_check_flag_asks_a_signature(command):
    assert detect_mutative_command(command).is_mutative, command
    assert classify_command_tier(command) == SecurityTier.T3_BLOCKED, command


@pytest.mark.parametrize("command", CHECKING_FORMATTERS)
def test_formatter_with_a_honored_check_flag_runs_free(command):
    assert not detect_mutative_command(command).is_mutative, command
    assert classify_command_tier(command) != SecurityTier.T3_BLOCKED, command


@pytest.mark.parametrize("command", HELP_FORMS)
def test_help_prints_and_never_formats(command):
    assert not detect_mutative_command(command).is_mutative, command
    assert classify_command_tier(command) != SecurityTier.T3_BLOCKED, command


@pytest.mark.parametrize("command,tier", NEARBY_READS)
def test_nearby_read_forms_keep_their_tier(command, tier):
    assert not detect_mutative_command(command).is_mutative, command
    assert classify_command_tier(command) == tier, command
