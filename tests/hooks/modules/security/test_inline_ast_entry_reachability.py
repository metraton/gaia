#!/usr/bin/env python3
"""Reachability closure: every catalog entry must FIRE via its idiomatic form.

Presence in a table is not evidence of detection.  Seven `pathlib.Path.*`
entries sat in `_DANGEROUS_CALLS` while being unreachable for every call form a
human actually writes, because the name resolver stopped at the first
intermediate `Call`.  They read as coverage and were dead.  Any entry added
after that discovery inherits the same doubt, so reachability is treated here
as UNPROVEN until exercised rather than assumed from membership.

The suite is exhaustive BY DERIVATION, not by enumeration: the cases are
generated from `_DANGEROUS_CALLS`, `_MODE_ARG_INDEX`, `_CLOUD_SDK_PREFIXES` and
`_CLOUD_SDK_MUTATIVE_VERBS` themselves.  An entry added tomorrow is picked up
with no edit here -- and if its dotted shape has no synthesis rule, the shape
guard FAILS and demands a declared form instead of silently falling back to a
form that would not exercise it.

Two properties make the assertions meaningful:

1. The generated payload is the BOUND, idiomatic form -- `Path(x).unlink()`,
   never the unbound `pathlib.Path.unlink(p)` that nobody writes and that
   matched even while the resolver was broken.
2. Each case asserts the SPECIFIC `label` and `category` carried by that
   table row, never merely `is_dangerous`.  An entry that is dead but happens
   to be shadowed by a different rule fires with the WRONG label, and that
   reads as a failure here instead of a pass.
"""

import pytest

from hooks.modules.security.inline_ast_analyzer import (
    _CLOUD_SDK_MUTATIVE_VERBS,
    _CLOUD_SDK_PREFIXES,
    _DANGEROUS_CALLS,
    _MODE_ARG_INDEX,
    _OS_OPEN_WRITE_FLAGS,
    analyze_python_inline,
)


_PATH_LITERAL = '"/tmp/reach"'

# A dotted name with two or more dots cannot take the generic
# `import <module>; <module>.<leaf>()` rule blindly -- `pathlib.Path.unlink` is
# a method on a class, not a function in a module named `pathlib.Path`.  Each
# such prefix needs a synthesis rule that is DECLARED, and the shape guard
# below fails on any new one so it cannot silently take the module rule.
_DECLARED_MULTIDOT_PREFIXES = frozenset({"pathlib.Path", "urllib.request"})

# One idiomatic chain per cloud SDK prefix.  Declared by hand because the
# chain shape is SDK-specific knowledge (which factory, how many intermediate
# calls) that no table carries; `test_every_cloud_prefix_has_a_declared_chain`
# fails if a prefix is added to `_CLOUD_SDK_PREFIXES` without one.  Each is the
# real invocation form, with the mutation reached through two or three
# intermediate calls exactly as a caller writes it.
_CLOUD_CHAINS = {
    "boto3": 'import boto3\nboto3.client("s3").{method}()',
    "google.cloud": (
        'from google.cloud import storage\n'
        'storage.Client().bucket("b").{method}()'
    ),
    "googleapiclient": (
        'from googleapiclient.discovery import build\n'
        'build("compute", "v1").instances().{method}()'
    ),
    "kubernetes.client": (
        'from kubernetes import client\nclient.CoreV1Api().{method}()'
    ),
    "python_terraform": (
        'from python_terraform import Terraform\n'
        'Terraform(working_dir="/repo").{method}()'
    ),
}


def _call_arguments(dotted: str) -> str:
    """Return the argument text needed to make ``dotted`` actually escalate.

    Most entries escalate on being invoked at all, so they need no arguments.
    The mode- and flag-gated openers escalate ONLY on a writing argument, and
    the position of that argument is read from `_MODE_ARG_INDEX` rather than
    hardcoded -- so a newly gated opener is exercised correctly with no edit.
    """
    if dotted in _MODE_ARG_INDEX:
        mode_index = _MODE_ARG_INDEX[dotted]
        return ", ".join(([_PATH_LITERAL] * mode_index) + ['"w"'])
    if dotted == "os.open":
        return f"{_PATH_LITERAL}, os.{sorted(_OS_OPEN_WRITE_FLAGS)[0]}"
    return ""


def _synthesize_idiomatic_call(dotted: str) -> str:
    """Build the source a human would write to invoke ``dotted``."""
    args = _call_arguments(dotted)
    if "." not in dotted:
        return f"{dotted}({args})"
    if dotted.startswith("pathlib.Path."):
        method = dotted.rsplit(".", 1)[1]
        return (
            "from pathlib import Path\n"
            f"Path({_PATH_LITERAL}).{method}({args})"
        )
    module, leaf = dotted.rsplit(".", 1)
    return f"import {module}\n{module}.{leaf}({args})"


# ---------------------------------------------------------------------------
# Guards on the derivation itself
# ---------------------------------------------------------------------------
class TestDerivationIsExhaustive:
    """The generator must cover the table, or say so loudly."""

    def test_catalog_is_not_empty(self):
        assert len(_DANGEROUS_CALLS) > 0

    def test_no_duplicate_dotted_names(self):
        dotted_names = [entry[0] for entry in _DANGEROUS_CALLS]
        assert len(dotted_names) == len(set(dotted_names))

    def test_every_entry_shape_has_a_synthesis_rule(self):
        undeclared = sorted(
            dotted for dotted, _, _ in _DANGEROUS_CALLS
            if dotted.count(".") >= 2
            and dotted.rsplit(".", 1)[0] not in _DECLARED_MULTIDOT_PREFIXES
        )
        assert undeclared == [], (
            "These entries have a dotted shape with no declared synthesis "
            "rule, so the generic module rule would generate a form that does "
            "NOT exercise them. Declare the idiomatic form (and add the prefix "
            f"to _DECLARED_MULTIDOT_PREFIXES): {undeclared}"
        )

    def test_every_cloud_prefix_has_a_declared_chain(self):
        assert set(_CLOUD_SDK_PREFIXES) == set(_CLOUD_CHAINS), (
            "A cloud SDK prefix without a declared chain would go untested. "
            f"missing={sorted(set(_CLOUD_SDK_PREFIXES) - set(_CLOUD_CHAINS))} "
            f"extra={sorted(set(_CLOUD_CHAINS) - set(_CLOUD_SDK_PREFIXES))}"
        )


class TestTheReachabilityCheckHasTeeth:
    """A reachability suite that cannot fail is the bug it exists to catch.

    These two tests validate the CHECK, not the analyzer: that the generated
    form is the one that was dead, and that a name with no implementation
    behind it is reported as not firing rather than passing quietly.
    """

    def test_pathlib_entries_are_exercised_bound_not_unbound(self):
        # The unbound `pathlib.Path.unlink(p)` matched even while every
        # idiomatic form was dead, so generating THAT form would make this
        # whole suite vacuous. The generated source must bind a receiver.
        source = _synthesize_idiomatic_call("pathlib.Path.unlink")
        assert f"Path({_PATH_LITERAL}).unlink(" in source
        assert "pathlib.Path.unlink(" not in source

    def test_a_name_with_no_implementation_is_reported_as_not_firing(self):
        # The negative control for the assertion machinery: a plausible-looking
        # dotted name that is NOT in the catalog must come back not-dangerous
        # through the exact same synthesis path. If this ever passes as
        # dangerous, the per-entry assertions below prove nothing.
        source = _synthesize_idiomatic_call("shutil.copyfile_but_not_really")
        assert analyze_python_inline(source).is_dangerous is False

    def test_a_cloud_chain_with_an_uncatalogued_prefix_does_not_fire(self):
        # Same control for the cloud lane: the chain shape alone must not
        # escalate; only the prefix-plus-verb rule may.
        source = 'import azuresdk\nazuresdk.Client().groups().delete_resource()'
        assert analyze_python_inline(source).is_dangerous is False


# ---------------------------------------------------------------------------
# Every dotted entry, through the form a human types
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "dotted,label,category",
    _DANGEROUS_CALLS,
    ids=[entry[0] for entry in _DANGEROUS_CALLS],
)
def test_catalog_entry_fires_via_idiomatic_form(dotted, label, category):
    source = _synthesize_idiomatic_call(dotted)
    result = analyze_python_inline(source)
    assert result.is_dangerous is True, (
        f"DEAD ENTRY: {dotted} is in _DANGEROUS_CALLS but did not fire for "
        f"its idiomatic form:\n{source}"
    )
    # Asserting the exact row distinguishes "this entry fired" from "something
    # else fired and made the entry look alive".
    assert result.label == label, (
        f"{dotted} fired as {result.label!r}, not its own label {label!r} -- "
        f"it is shadowed by another rule, not reachable itself:\n{source}"
    )
    assert result.category == category


# ---------------------------------------------------------------------------
# The cloud SDK lane, across both dimensions of its rule
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("prefix", sorted(_CLOUD_CHAINS))
@pytest.mark.parametrize("verb", sorted(_CLOUD_SDK_MUTATIVE_VERBS))
def test_cloud_verb_fires_for_every_prefix_snake_case(verb, prefix):
    source = _CLOUD_CHAINS[prefix].format(method=f"{verb}_resource")
    result = analyze_python_inline(source)
    assert result.is_dangerous is True, (
        f"DEAD CLOUD RULE: verb {verb!r} did not fire for prefix {prefix!r}:"
        f"\n{source}"
    )
    assert result.label == f"cloud-sdk-{verb}"
    assert result.category == "CLOUD_SDK"


@pytest.mark.parametrize("prefix", sorted(_CLOUD_CHAINS))
@pytest.mark.parametrize("verb", sorted(_CLOUD_SDK_MUTATIVE_VERBS))
def test_cloud_verb_fires_for_every_prefix_camel_case(verb, prefix):
    camel = f"{verb}{'Resource'}"
    source = _CLOUD_CHAINS[prefix].format(method=camel)
    result = analyze_python_inline(source)
    assert result.is_dangerous is True, (
        f"DEAD CLOUD RULE: camelCase verb {camel!r} did not fire for prefix "
        f"{prefix!r}:\n{source}"
    )
    assert result.label == f"cloud-sdk-{verb}"


@pytest.mark.parametrize("prefix", sorted(_CLOUD_CHAINS))
def test_cloud_chain_itself_does_not_fire_without_a_verb(prefix):
    """The control for the two tests above.

    If a chain template happened to contain something the catalog matches, the
    positive cases would pass for the wrong reason and a dead verb rule would
    hide behind them.  The same chain with a read method must stay T0, which
    proves the escalation came from the VERB and not from the scaffolding.
    """
    source = _CLOUD_CHAINS[prefix].format(method="get_resource")
    result = analyze_python_inline(source)
    assert result.is_dangerous is False, (
        f"Chain scaffolding for {prefix!r} fires on its own "
        f"(label={result.label!r}), so the verb tests above prove nothing:"
        f"\n{source}"
    )
