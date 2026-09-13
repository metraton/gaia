"""A T3 consent binds to an OBJECT, and S3 keys and git refs are case-sensitive.

``analyze_command`` serves two purposes with one result. For CLASSIFICATION --
finding the mutative verb, deciding the tier -- folding case is correct and
must stay: a verb is a verb however it is typed. For an APPROVAL SIGNATURE --
binding a consent to the thing it was granted over -- folding case is the
defect: ``s3://bucket/Archive/old.tar`` and ``s3://bucket/archive/old.tar`` are
two objects, and one signature covered both.

These tests hold the two purposes apart: the signature must distinguish the
objects, and the classifier must keep folding.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.security.approval_scopes import (  # noqa: E402
    build_approval_signature,
    matches_approval_signature,
)
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402


def _signature(command: str):
    signature = build_approval_signature(command)
    assert signature is not None, f"{command!r} produced no signature to test"
    return signature


def test_a_grant_does_not_stretch_to_a_differently_cased_object():
    signature = _signature("aws s3 rm s3://bucket/Archive/old.tar")

    assert matches_approval_signature(signature, "aws s3 rm s3://bucket/Archive/old.tar"), (
        "a command must still match its own grant"
    )
    assert not matches_approval_signature(signature, "aws s3 rm s3://bucket/archive/old.tar"), (
        "an S3 key is case-sensitive: these are two different objects"
    )


def test_the_object_stays_distinct_for_paths_and_refs_too():
    path_grant = _signature("rm -rf /srv/Data/archive")
    assert not matches_approval_signature(path_grant, "rm -rf /srv/data/archive"), (
        "POSIX paths are case-sensitive"
    )

    ref_grant = _signature("git push origin Release")
    assert not matches_approval_signature(ref_grant, "git push origin release"), (
        "git refs are case-sensitive"
    )


def test_an_inline_flag_value_keeps_the_case_it_was_signed_with():
    signature = _signature("gh api --method=DELETE /repos/Acme/Widgets")

    assert matches_approval_signature(signature, "gh api --method=DELETE /repos/Acme/Widgets")
    assert not matches_approval_signature(signature, "gh api --method=DELETE /repos/acme/widgets")


def test_two_flags_that_differ_only_in_case_are_two_different_flags():
    """``-d`` and ``-D`` are git's safe and force deletions. Folding the flag
    set let a grant for one be consumed by the other."""
    signature = _signature("git branch -D feature/old")

    assert matches_approval_signature(signature, "git branch -D feature/old")
    assert not matches_approval_signature(signature, "git branch -d feature/old")


def test_reordering_or_repeating_flags_still_consumes_the_same_grant():
    """The accepted residual, measured rather than assumed. Flag ORDER and
    MULTIPLICITY carry no meaning in the CLIs this layer classifies -- only the
    flag's identity and its value do, and those are bound. Their values, when
    written as separate positional tokens, stay ORDERED in the semantic tokens,
    so this does not let a different pairing through."""
    signature = _signature("git push --force --no-verify origin main")

    assert matches_approval_signature(signature, "git push --no-verify --force origin main")
    assert matches_approval_signature(signature, "git push --force --force --no-verify origin main")
    assert not matches_approval_signature(signature, "git push --force origin main"), (
        "dropping a flag is a different command, not a reordering"
    )


def test_classification_keeps_folding_case():
    """The half that must NOT change: an uppercase verb is still the verb, and
    unlearning that would open gating holes rather than close them."""
    assert detect_mutative_command("git PUSH origin main").is_mutative
    assert detect_mutative_command("kubectl DELETE pod web-1").is_mutative
