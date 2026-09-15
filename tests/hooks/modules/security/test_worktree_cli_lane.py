"""`gaia worktree <verb>` classifies without demanding a T3 signature.

The three invocations a specialist needs to own its own isolated worktree
(create, list/show, release) must never cost the user's consent -- see
COMMAND_SUBCOMMAND_TIER_EXCEPTIONS[("gaia", "worktree")] in mutative_verbs.py
for why `create` and `release` (both generic MUTATIVE_VERBS tokens) are
downgraded, and gaia_cli_only_guard.ALLOWED_READ_PHRASES for why only the two
reads are open to the ORCHESTRATOR's own bare CLI lane.

Verified through the real BashValidator pipeline, not by reading either
table -- mirrors the method gaia.retention.worktree_reclaim's own module
docstring documents using to settle its own design question.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.tools.bash_validator import BashValidator  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.security import gaia_cli_only_guard as guard  # noqa: E402

_GAIA = str(_REPO_ROOT / "bin" / "gaia")

_WORKTREE_COMMANDS = {
    "create": f"{_GAIA} worktree create --repo /tmp/repo --project gaia-test "
              f"--contract-id c1.abc --agent-id c1 --branch feature/wt-test --json",
    "list": f"{_GAIA} worktree list --repo /tmp/repo --json",
    "show": f"{_GAIA} worktree show /tmp/repo/.gaia/worktrees/wt1 --json",
    "release": f"{_GAIA} worktree release /tmp/repo/.gaia/worktrees/wt1 "
               f"--workspace me --brief b --ac AC-1 --json",
}


@pytest.mark.parametrize("argline", sorted(_WORKTREE_COMMANDS))
def test_worktree_verb_never_reaches_t3(argline):
    """Every `gaia worktree <verb>` command classifies below T3 (no signature)."""
    result = BashValidator().validate(_WORKTREE_COMMANDS[argline])
    assert result.tier != SecurityTier.T3_BLOCKED, (
        f"'gaia worktree {argline}' classified {result.tier}: {result.reason}"
    )
    assert result.allowed is True, result.reason


def test_worktree_create_and_release_are_read_only_by_the_group_exception():
    """`create`/`release` are the two generic-MUTATIVE_VERBS tokens this task
    exists to downgrade -- confirm the classifier's OWN category, not merely
    that BashValidator let the command through for some other reason."""
    from modules.security.mutative_verbs import (
        CATEGORY_READ_ONLY,
        detect_mutative_command,
    )

    for argline in ("create", "release"):
        result = detect_mutative_command(_WORKTREE_COMMANDS[argline])
        assert result.is_mutative is False, (argline, result.reason)
        assert result.category == CATEGORY_READ_ONLY, (argline, result.category)


# ---------------------------------------------------------------------------
# Orchestrator lane: reads only, ties the declared carril to the real one.
# ---------------------------------------------------------------------------

_ORCHESTRATOR_PAYLOAD = {
    "role_context": {
        "role": "gaia-orchestrator",
        "capabilities": ["brief.read"],
        "issuer": "gaia-runtime",
        "attestation": "signed-binding",
        "verified": True,
    }
}


@pytest.mark.parametrize("argline", ["list", "show"])
def test_orchestrator_may_run_worktree_reads(monkeypatch, argline):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _: True)
    allowed, reason = guard.check(_WORKTREE_COMMANDS[argline], _ORCHESTRATOR_PAYLOAD)
    assert allowed is True, reason


@pytest.mark.parametrize("argline", ["create", "release"])
def test_orchestrator_may_not_run_worktree_writes(monkeypatch, argline):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _: True)
    allowed, reason = guard.check(_WORKTREE_COMMANDS[argline], _ORCHESTRATOR_PAYLOAD)
    assert allowed is False
    assert "not approvable" in reason or "not on the orchestrator" in reason


def test_worktree_read_phrases_are_declared_in_the_allowlist():
    """Ties the guard's own declaration to the behavior just proven above --
    a future edit that removes list/show from ALLOWED_READ_PHRASES (silently
    denying them for the orchestrator) fails here before it fails in the
    field."""
    assert ("worktree", "list") in guard.ALLOWED_READ_PHRASES
    assert ("worktree", "show") in guard.ALLOWED_READ_PHRASES
    assert ("worktree", "create") not in guard.ALLOWED_PHRASES
    assert ("worktree", "release") not in guard.ALLOWED_PHRASES


def test_worktree_lane_is_fully_classified_in_the_catalog():
    """Every real `gaia worktree <action>` leaf the CLI registers today is
    either an orchestrator read (ALLOWED_READ_PHRASES) or explicitly a
    specialist-owned write outside the guard's allowlist -- so a fifth
    `worktree` action added tomorrow with no explicit classification shows up
    here as neither, instead of silently defaulting to denied-by-omission
    with nothing to say so.
    """
    import argparse
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader(
        "gaia_cli_entry_worktree_lane", str(_REPO_ROOT / "bin" / "gaia")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(loader.name, module)
    loader.exec_module(module)
    parser = module._build_parser(module._discover_plugins())

    root_subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    worktree_parser = root_subparsers.choices["worktree"]
    wt_subparsers = next(
        a for a in worktree_parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    leaves = set(wt_subparsers.choices)
    assert leaves, "gaia worktree registers no actions -- the plugin regressed"

    known_specialist_writes = {"create", "release"}
    for leaf in leaves:
        phrase = ("worktree", leaf)
        if phrase in guard.ALLOWED_READ_PHRASES:
            continue
        assert leaf in known_specialist_writes, (
            f"'gaia worktree {leaf}' is a real CLI action classified in "
            "neither ALLOWED_READ_PHRASES nor the known specialist-write set "
            "-- it will be silently denied for the orchestrator with nothing "
            "flagging the omission. Classify it explicitly in one of the two."
        )
