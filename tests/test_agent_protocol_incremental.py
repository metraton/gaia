"""AC-4 -- the specialist definitions instruct the ADOPTED, incremental contract.

The substrate changed underneath the prompts: a contract row is now born at
dispatch under a real, adoptable identity that is injected into the subagent's
context, ``gaia contract set/add/fill`` mirror the partial envelope onto that
row while the turn is still running, and ``finalize`` converges the same row
instead of creating a second one. A definition that still teaches "mint your own
id, compose the envelope at the end" sends the agent down the path the substrate
no longer takes -- and the failure is silent, because nothing rejects a rival
draft; it just leaves the bound row unclosed.

So the check is on the DEFINITIONS, and it is anchored to the implementation
rather than to a wording preference:

  * the block heading each definition tells the agent to look for is imported
    from ``modules.agents.dispatch_identity``, so renaming the injected block
    fails this test instead of quietly stranding eight prompts;
  * the verbs each definition calls "incremental" are cross-checked against the
    ``gaia contract`` subcommands that actually pass ``mirror=True`` in
    ``bin/cli/contract.py``;
  * adoption is the FIRST WRITE against the born draft: a definition
    must instruct ``gaia contract set/add/fill --draft-id ...`` and must NOT
    carry the retired ``gaia contract init --agent-id ... --draft-id ...``
    command, nor an invented ``--session-id <sid>`` on finalize.

``missing_protocol_clauses`` is exercised against a stub with none of the
clauses, so a check that can no longer fail is itself a failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.agents.dispatch_identity import (  # noqa: E402
    IDENTITY_BLOCK_HEADING,
)

AGENTS_DIR = _REPO_ROOT / "agents"
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

# The orchestrator carries no shell and finalizes no contract of its own, so the
# specialist fleet is every other definition in agents/.
NON_SPECIALIST = {"gaia-orchestrator"}

EXPECTED_SPECIALIST_COUNT = 8

# The anchor is the heading's stable prefix (any parenthetical suffix a future
# rename might add is tolerated) -- still imported, never retyped. v43: the
# heading is "# Your Contract", the dispatch kernel's own marker.
IDENTITY_ANCHOR = IDENTITY_BLOCK_HEADING.split("(")[0].strip()

# Adoption is the FIRST WRITE against the born draft, not an init. The retired
# `gaia contract init --agent-id ... --draft-id ...` command is now a
# contradiction a definition must NOT carry.
_ADOPT_RE = re.compile(
    r"gaia contract set/add/fill\s+--draft-id\s+\S+"
)
_RETIRED_ADOPT_INIT_RE = re.compile(
    r"gaia contract init\s+--agent-id\s+\S+\s+--draft-id\s+\S+"
)
_FINALIZE_RE = re.compile(r"gaia contract finalize\s+--draft-id\s+\S+")
# The invented-session-id instruction that clobbered real birth attribution
# (measured: handoff 10915). A definition must not instruct a placeholder.
_FINALIZE_SESSION_PLACEHOLDER = "--session-id <sid>"
_INIT_MENTION_RE = re.compile(r"^.*gaia contract init.*$", re.MULTILINE)
_INCREMENTAL_TIMING_RE = re.compile(
    r"incrementall?y|as you (?:make|discover|go|reach)|during the turn",
    re.IGNORECASE,
)
_FINALIZE_LAST_RE = re.compile(
    r"last tool call|finalize last|as the last", re.IGNORECASE
)
_SOLE_PROMOTION_RE = re.compile(
    r"only promotion|sole promotion|only way to promote", re.IGNORECASE
)
_CUT_RATIONALE_RE = re.compile(
    r"cut|truncat|interrupt", re.IGNORECASE
)


def specialist_definitions() -> "list[Path]":
    """Every specialist agent definition under ``agents/``."""
    return sorted(
        p
        for p in AGENTS_DIR.glob("*.md")
        if p.stem not in NON_SPECIALIST and p.name != "README.md"
    )


def mirroring_cli_verbs() -> "set[str]":
    """The ``gaia contract`` verbs whose CLI handler mirrors to the row.

    Derived from the source rather than restated, so a verb that gains or loses
    the mirror moves this expectation with it.
    """
    source = CONTRACT_CLI.read_text(encoding="utf-8")
    verbs = set()
    for match in re.finditer(
        r"def cmd_(\w+)\(args\) -> int:(.*?)(?=\ndef |\Z)", source, re.DOTALL
    ):
        if "mirror=True" in match.group(2):
            verbs.add(match.group(1))
    return verbs


def missing_protocol_clauses(text: str, mirror_verbs: "set[str]") -> "list[str]":
    """Return the protocol clauses a definition fails to instruct.

    An empty list means the definition teaches the full flow: adopt the injected
    identity, fill incrementally during the turn, finalize last as the sole
    promotion -- and carries no leftover instruction to mint a rival identity.
    """
    missing = []

    if IDENTITY_ANCHOR not in text:
        missing.append(f"does not name the injected {IDENTITY_ANCHOR!r} block")
    if not _ADOPT_RE.search(text):
        missing.append(
            "no first-write adoption (gaia contract set/add/fill --draft-id ...)"
        )
    if _RETIRED_ADOPT_INIT_RE.search(text):
        missing.append(
            "retired adopt-with-init instruction "
            "(gaia contract init --agent-id ... --draft-id ...)"
        )

    for verb in sorted(mirror_verbs):
        if f"gaia contract {verb}" not in text:
            missing.append(f"does not instruct the incremental verb {verb!r}")
    if not _INCREMENTAL_TIMING_RE.search(text):
        missing.append("does not say the filling happens DURING the turn")
    if not _CUT_RATIONALE_RE.search(text):
        missing.append("does not explain WHY (a cut turn must leave evidence)")

    if not _FINALIZE_RE.search(text):
        missing.append("no finalize command carrying --draft-id")
    if _FINALIZE_SESSION_PLACEHOLDER in text:
        missing.append(
            "instructs an invented --session-id on finalize "
            "(the born row already carries the session attribution)"
        )
    if not _FINALIZE_LAST_RE.search(text):
        missing.append("does not place finalize last in the turn")
    if not _SOLE_PROMOTION_RE.search(text):
        missing.append("does not name finalize the only promotion to a clean close")

    # The one contradiction that survives an additive edit: an older line that
    # still tells the agent to run a bare `gaia contract init`. Legitimate only
    # where it is explicitly the no-contract-block fallback, or where the line
    # itself forbids running init.
    for line in _INIT_MENTION_RE.findall(text):
        if re.search(
            r"fallback|no identity block|no `# Your Contract` block|do not run",
            line,
            re.IGNORECASE,
        ):
            continue
        missing.append(f"stale bare-init instruction: {line.strip()!r}")

    return missing


def test_agent_protocol_incremental_fleet_is_the_eight_specialists():
    definitions = specialist_definitions()
    names = [p.stem for p in definitions]
    assert len(definitions) == EXPECTED_SPECIALIST_COUNT, names
    assert "gaia-orchestrator" not in names


def test_agent_protocol_incremental_mirror_verbs_are_set_add_fill():
    assert mirroring_cli_verbs() == {"set", "add", "fill"}


@pytest.mark.parametrize(
    "definition", specialist_definitions(), ids=lambda p: p.stem
)
def test_agent_protocol_incremental_definition_instructs_the_flow(definition):
    missing = missing_protocol_clauses(
        definition.read_text(encoding="utf-8"), mirroring_cli_verbs()
    )
    assert not missing, f"{definition.name}: " + "; ".join(missing)


def test_agent_protocol_incremental_adoption_addresses_the_born_draft():
    """Definitions instruct the --draft-id discipline and never the retired
    adopt-with-init command (the first write IS the adoption)."""
    for definition in specialist_definitions():
        text = definition.read_text(encoding="utf-8")
        assert "--draft-id" in text, f"{definition.name} omits --draft-id"
        assert not _RETIRED_ADOPT_INIT_RE.search(text), (
            f"{definition.name} still instructs adopt-with-init"
        )


def test_agent_protocol_incremental_check_rejects_a_definition_without_the_flow():
    """The check must be able to fail -- a stub definition trips every clause."""
    missing = missing_protocol_clauses(
        "# some-agent\n\nDo the work and emit a contract at the end.\n",
        {"set", "add", "fill"},
    )
    assert len(missing) >= 6


def test_agent_protocol_incremental_check_catches_a_stale_bare_init():
    """A leftover bare `gaia contract init` is the contradiction that matters."""
    good = (specialist_definitions()[0]).read_text(encoding="utf-8")
    assert not missing_protocol_clauses(good, {"set", "add", "fill"})
    stale = good + "\n\nBuild it with `gaia contract init` and finalize.\n"
    assert any(
        m.startswith("stale bare-init instruction")
        for m in missing_protocol_clauses(stale, {"set", "add", "fill"})
    )
