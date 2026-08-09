"""The read map must name only real verbs, and must name all of them.

``skills/agent-protocol/read-map.md`` is the single place Gaia writes down what a
turn can read. A map is worth exactly its accuracy, and a document that describes
CLI verbs rots in two directions that no proofread catches:

* it can name a verb or flag that was renamed or removed, sending a reader after
  a coordinate it can no longer open; and
* it can silently OMIT a read capability the code grew, which is the failure the
  map exists to fix -- an unknown read verb produces no error, only absence.

So both directions are asserted against machinery rather than prose. Forward:
every ``gaia ...`` command the document spells is resolved against the REAL
argparse tree ``bin/gaia`` builds, and every long flag it names must be a real
option of that verb. Backward: every phrase in the guard's own
``ALLOWED_READ_PHRASES`` -- the enumerated, code-owned declaration of which verbs
are read-only -- must appear in the map.

Failure mode is open, not closed: if the document's tables were reformatted past
recognition, commands would stop being extracted rather than start failing. The
emptiness assertions below are the guard against that, the same way
``test_canonical_example_is_reachable.py`` asserts its docs teach at least one
block before checking the blocks.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_READ_MAP = _REPO_ROOT / "skills" / "agent-protocol" / "read-map.md"
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"

# Phrases that are read-only per the guard but deliberately NOT in the map, each
# with the reason it is elsewhere. An entry here is a claim that the capability
# is documented somewhere better, not that it is undocumented.
_EXEMPT_FROM_MAP: dict = {}

# Verbs the map names that the orchestrator's read allowlist does NOT carry.
# They are live and read-only for a SPECIALIST (governed by mutative_verbs, not
# by this allowlist), and the map states that asymmetry explicitly. Pinned here
# so that closing the gap in either direction forces the map's paragraph to be
# rewritten rather than left stale.
_READ_FOR_SPECIALIST_ONLY = {
    ("contract", "chain"),
    ("context", "query"),
}

# A command span is a backticked run starting with `gaia `. Placeholder tokens
# (<slug>, [<P-id>], "<SELECT ...>") are stripped before resolution: they stand
# for values, never for subcommands.
_COMMAND_SPAN = re.compile(r"`(gaia\s[^`]*)`")
_LONG_FLAG = re.compile(r"(--[a-z][a-z0-9-]*)")


def _cli_parser() -> argparse.ArgumentParser:
    """The real parser, built the way ``gaia`` itself builds it.

    ``bin/gaia`` has no ``.py`` suffix, so it is loaded by explicit source
    loader. Its module body has no side effects (everything runs under
    ``main()``), which is what makes importing it for introspection safe.
    """
    loader = importlib.machinery.SourceFileLoader("gaia_cli_entry", str(_GAIA_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(loader.name, module)
    loader.exec_module(module)
    return module._build_parser(module._discover_plugins())


def _allowed_read_phrases() -> frozenset:
    hooks_dir = str(_REPO_ROOT / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from modules.security.gaia_cli_only_guard import ALLOWED_READ_PHRASES

    return ALLOWED_READ_PHRASES


def _expand_alternatives(token: str) -> list:
    """`show\\|list\\|deps` in a table cell is three verbs, not one."""
    return [part for part in re.split(r"\\?\|", token) if part]


def _is_placeholder(token: str) -> bool:
    return (
        token.startswith("<")
        or token.startswith("[")
        or token.startswith("--")
        or token.startswith('"')
        or token.startswith("'")
    )


def _phrases_from_span(span: str) -> list:
    """Every subcommand phrase a single backticked command span denotes."""
    tokens = span.split()[1:]  # drop the leading `gaia`
    phrase: list = []
    for token in tokens:
        if _is_placeholder(token):
            break
        phrase.append(token)
    if not phrase:
        return []
    head, last = phrase[:-1], phrase[-1]
    return [tuple(head + [alt]) for alt in _expand_alternatives(last)]


# A markdown table cell boundary: a `|` that is not the escaped `\|` an
# alternation inside a cell is spelled with. Splitting on the bare character
# tore `gaia brief show\|list\|...` into fragments and made those five verbs
# invisible to the guard.
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")

# A backticked span that is NOT a command -- a bare flag standing alone in a
# table's addressing column, e.g. `--harness-id` or `--cut [reason]`.
_LOOSE_SPAN = re.compile(r"`([^`]*)`")


def _documented() -> dict:
    """Map every phrase the document names to the long flags it claims for it.

    Two sources of flags, kept apart on purpose. A flag written INSIDE a command
    span belongs to that command alone -- a row naming both `gaia evidence list
    --brief <b>` and `gaia evidence show <id>` must not lend `--brief` to the
    second. A flag standing alone in a table cell belongs to the verbs of that
    row's first cell, which is what the addressing column is.
    """
    text = _READ_MAP.read_text(encoding="utf-8")
    found: dict = {}

    def _record(phrases, flags):
        for phrase in phrases:
            found.setdefault(phrase, set()).update(flags)

    for line in text.splitlines():
        for span in _COMMAND_SPAN.findall(line):
            _record(_phrases_from_span(span), set(_LONG_FLAG.findall(span)))

        if not line.lstrip().startswith("|"):
            continue
        cells = _CELL_BOUNDARY.split(line.strip().strip("|"))
        row_verbs = [
            phrase
            for span in _COMMAND_SPAN.findall(cells[0])
            for phrase in _phrases_from_span(span)
        ]
        if not row_verbs:
            continue
        loose = {
            flag
            for cell in cells
            for span in _LOOSE_SPAN.findall(cell)
            if not span.startswith("gaia ")
            for flag in _LONG_FLAG.findall(span)
        }
        _record(row_verbs, loose)

    return found


def _resolve(parser: argparse.ArgumentParser, phrase: tuple):
    """Walk the subparser tree; return the leaf parser, or None."""
    current = parser
    for token in phrase:
        subparsers = next(
            (a for a in current._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        if subparsers is None or token not in subparsers.choices:
            return None
        current = subparsers.choices[token]
    return current


def _options(parser: argparse.ArgumentParser) -> set:
    return {opt for action in parser._actions for opt in action.option_strings}


_DOCUMENTED = _documented()
_PARSER = _cli_parser()


# ---------------------------------------------------------------------------
# The guard is not vacuous.
# ---------------------------------------------------------------------------
def test_the_map_still_spells_commands_this_test_can_read():
    """If the tables were reformatted past recognition every other assertion
    below would pass on an empty set. This is the tripwire for that."""
    assert len(_DOCUMENTED) >= 30, (
        f"only {len(_DOCUMENTED)} commands extracted from {_READ_MAP.name} -- "
        "the document's format changed and the guard has gone blind"
    )
    for family in ("contract", "memory", "context", "brief", "task", "approvals"):
        assert any(phrase[0] == family for phrase in _DOCUMENTED), (
            f"no {family} verb extracted -- the guard is partially blind"
        )


# ---------------------------------------------------------------------------
# Forward: nothing the map names is fictional.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", sorted(_DOCUMENTED))
def test_every_documented_verb_exists_in_the_cli(phrase):
    assert _resolve(_PARSER, phrase) is not None, (
        f"read-map.md names `gaia {' '.join(phrase)}`, which the real CLI "
        "parser does not resolve"
    )


@pytest.mark.parametrize("phrase", sorted(_DOCUMENTED))
def test_every_flag_the_map_claims_is_a_real_flag(phrase):
    leaf = _resolve(_PARSER, phrase)
    if leaf is None:
        pytest.skip("verb resolution is asserted by its own test")
    real = _options(leaf)
    for flag in sorted(_DOCUMENTED[phrase]):
        assert flag in real, (
            f"read-map.md claims `{flag}` for `gaia {' '.join(phrase)}`, "
            f"which accepts {sorted(real)}"
        )


@pytest.mark.parametrize("phrase", sorted(_DOCUMENTED))
def test_every_documented_verb_is_a_read(phrase):
    """The map may only carry verbs the code itself declares read-only.

    ``ALLOWED_READ_PHRASES`` is that declaration -- each entry justified by
    following what the handler calls. The two specialist-only reads are pinned
    separately rather than waved through.
    """
    allowed = _allowed_read_phrases()
    assert phrase in allowed or phrase in _READ_FOR_SPECIALIST_ONLY, (
        f"read-map.md names `gaia {' '.join(phrase)}`, which is neither in "
        "ALLOWED_READ_PHRASES nor pinned as a specialist-only read"
    )


def test_the_specialist_only_reads_are_still_outside_the_orchestrator_lane():
    """The asymmetry the map documents must still be the real one.

    If one of these is added to the guard, the map's paragraph about the two
    lanes becomes false and has to be rewritten -- so this fails loudly instead
    of letting the prose drift.
    """
    allowed = _allowed_read_phrases()
    for phrase in sorted(_READ_FOR_SPECIALIST_ONLY):
        assert phrase not in allowed, (
            f"`gaia {' '.join(phrase)}` is now in ALLOWED_READ_PHRASES; "
            "read-map.md still describes it as outside the orchestrator's lane"
        )


# ---------------------------------------------------------------------------
# Backward: THE property. The map cannot be quietly incomplete.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", sorted(_allowed_read_phrases()))
def test_every_read_verb_the_code_allows_is_on_the_map(phrase):
    if phrase in _EXEMPT_FROM_MAP:
        pytest.skip(_EXEMPT_FROM_MAP[phrase])
    assert phrase in _DOCUMENTED, (
        f"`gaia {' '.join(phrase)}` is a read verb the code allows and "
        "read-map.md does not name it -- a capability nobody knows about "
        "produces no error, only absence"
    )
