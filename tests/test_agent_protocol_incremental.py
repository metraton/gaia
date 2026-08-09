"""The contract flow is taught in ONE place, and no document outlives its symbol.

The flow -- adopt the born identity, mirror evidence onto the row during the turn,
finalize last -- used to be restated verbatim inside every specialist definition
as well as in ``skills/agent-protocol/SKILL.md``. Both surfaces reach the same
window (the harness preloads a definition's ``skills:`` before the first turn), so
the copy bought nothing and cost a maintenance seam that measurably rotted: a code
change retired the gate's fence source, the skill was corrected six minutes later,
and the eight copies were never revisited. They carried a false instruction for two
days while this file was green.

So the guard now runs in three directions, and the third is the one that would have
caught that:

  FORWARD (existence) -- the always-injected ``SKILL.md`` instructs the whole flow,
  every clause of it, anchored to machinery rather than to wording: the block
  heading comes from ``modules.agents.dispatch_identity``, the "incremental" verbs
  from the ``mirror=True`` handlers in ``bin/cli/contract.py``, and the finalize
  flags from the REAL argparse tree ``bin/gaia`` builds -- so renaming a flag fails
  here instead of stranding prose.

  BACKWARD (single source) -- no specialist definition re-teaches that flow. This is
  what stops the block from growing back one well-meaning bullet at a time. The
  forbidden verbs are derived from the same argparse tree, not typed out. A
  definition may still carry what is TRUE OF ITS SURFACE ALONE and of no other;
  ``gaia-verifier``'s binding line is that case, and it is pinned positively so a
  later cleanup cannot delete it silently.

  CLAIM <-> SYMBOL (this file's contribution) -- existence and completeness both ask
  whether a name is present on the other side. Neither can ask whether a SENTENCE is
  still true. ``CLAIM_LEDGER`` binds a claim to the code symbol that licensed it:
  while the symbol is absent, no document may assert what it upheld. Each row carries
  its own positive and negative control, so a row that has stopped being able to fire
  fails as loudly as a document that drifted -- this repo has nine recorded instances
  of a test that could not fail.

To add a claim: append a ``Claim`` row. It needs the symbol, a pattern matching the
assertion's SHAPE, the negation markers that license the same shape when a sentence
denies or historicizes it, one sentence that must match, and one that must not.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import re
import sys
from dataclasses import dataclass
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
SKILLS_DIR = _REPO_ROOT / "skills"
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
PROTOCOL_SKILL = SKILLS_DIR / "agent-protocol" / "SKILL.md"

# The orchestrator carries no shell and finalizes no contract of its own, so the
# specialist fleet is every other definition in agents/.
NON_SPECIALIST = {"gaia-orchestrator"}
EXPECTED_SPECIALIST_COUNT = 8

# The heading's stable prefix (a future parenthetical suffix is tolerated) --
# imported, never retyped.
IDENTITY_ANCHOR = IDENTITY_BLOCK_HEADING.split("(")[0].strip()


def specialist_definitions() -> "list[Path]":
    """Every specialist agent definition under ``agents/``."""
    return sorted(
        p
        for p in AGENTS_DIR.glob("*.md")
        if p.stem not in NON_SPECIALIST and p.name != "README.md"
    )


def _cli_parser() -> argparse.ArgumentParser:
    """The real parser, built the way ``gaia`` itself builds it.

    ``bin/gaia`` has no ``.py`` suffix, so it is loaded by explicit source loader.
    Its module body has no side effects (everything runs under ``main()``), which
    is what makes importing it for introspection safe.
    """
    loader = importlib.machinery.SourceFileLoader("gaia_cli_entry", str(GAIA_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(loader.name, module)
    loader.exec_module(module)
    return module._build_parser(module._discover_plugins())


def _contract_subparser(verb: str) -> argparse.ArgumentParser:
    parser = _cli_parser()
    for token in ("contract", verb):
        action = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        parser = action.choices[token]
    return parser


def contract_verbs() -> "set[str]":
    """Every ``gaia contract`` subcommand the real CLI exposes."""
    parser = _cli_parser()
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    contract = action.choices["contract"]
    sub = next(
        a for a in contract._actions if isinstance(a, argparse._SubParsersAction)
    )
    return set(sub.choices)


def finalize_flags() -> "set[str]":
    """The long options ``gaia contract finalize`` really accepts."""
    return {
        opt
        for a in _contract_subparser("finalize")._actions
        for opt in a.option_strings
        if opt.startswith("--")
    }


def mirroring_cli_verbs() -> "set[str]":
    """The ``gaia contract`` verbs whose handler mirrors to the row.

    Derived from the source rather than restated, so a verb that gains or loses
    the mirror moves this expectation with it.
    """
    source = CONTRACT_CLI.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(
            r"def cmd_(\w+)\(args\) -> int:(.*?)(?=\ndef |\Z)", source, re.DOTALL
        )
        if "mirror=True" in match.group(2)
    }


# A `gaia contract set/add/fill` span names three verbs, not one.
_CONTRACT_SPAN = re.compile(r"gaia contract ([a-z][a-z/-]*)")


def contract_verbs_named(text: str) -> "set[str]":
    named: set = set()
    for span in _CONTRACT_SPAN.findall(text):
        named.update(part for part in span.split("/") if part)
    return named


_ADOPT_RE = re.compile(r"gaia contract set/add/fill\s+--draft-id\s+\S+")
_RETIRED_ADOPT_INIT_RE = re.compile(
    r"gaia contract init\s+--agent-id\s+\S+\s+--draft-id\s+\S+"
)
_FINALIZE_RE = re.compile(r"gaia contract finalize\s+--draft-id\s+\S+")
# The invented-session-id instruction that clobbered real birth attribution
# (measured: handoff 10915). No document may instruct a placeholder.
_FINALIZE_SESSION_PLACEHOLDER = "--session-id <sid>"
_INIT_MENTION_RE = re.compile(r"^.*gaia contract init.*$", re.MULTILINE)
# These documents are hard-wrapped, so a phrase can straddle a newline. Every
# multi-word pattern below spells its gaps `\s+` rather than a literal space: a
# reflow is a layout change, never a change of meaning, and a guard that reads it
# as one goes quietly blind.
_INCREMENTAL_TIMING_RE = re.compile(
    r"incrementall?y|as\s+you\s+(?:make|discover|go|reach)|during\s+the\s+turn"
    r"|in\s+flight",
    re.IGNORECASE,
)
_FINALIZE_LAST_RE = re.compile(
    r"finaliz\w*[^.]{0,90}\blast\b|\blast\b[^.]{0,40}tool\s+call", re.IGNORECASE
)
_SOLE_PROMOTION_RE = re.compile(
    r"only\s+promotion|sole\s+promotion|only\s+way\s+to\s+promote", re.IGNORECASE
)
_CUT_RATIONALE_RE = re.compile(r"\bcut\b|truncat|interrupt", re.IGNORECASE)


def missing_protocol_clauses(text: str, mirror_verbs: "set[str]") -> "list[str]":
    """Return the protocol clauses a document fails to instruct.

    An empty list means it teaches the full flow: adopt the injected identity,
    fill incrementally during the turn, finalize last as the sole promotion --
    and carries no leftover instruction to mint a rival identity.
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

    named = contract_verbs_named(text)
    for verb in sorted(mirror_verbs):
        if verb not in named:
            missing.append(f"does not name the incremental verb {verb!r}")
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

    # The contradiction that survives an additive edit: a line that still tells
    # the agent to run a bare `gaia contract init`. Legitimate only where it is
    # explicitly the no-contract-block fallback, or where the line forbids it.
    for line in _INIT_MENTION_RE.findall(text):
        if re.search(
            r"fallback|no identity block|no contract block|"
            r"no `# Your Contract` block|do not run|stray|fits exactly one turn",
            line,
            re.IGNORECASE,
        ):
            continue
        missing.append(f"stale bare-init instruction: {line.strip()!r}")

    return missing


# ===========================================================================
# FORWARD -- the always-injected file teaches the whole flow.
# ===========================================================================
def test_the_protocol_skill_is_the_one_place_the_flow_is_taught():
    missing = missing_protocol_clauses(
        PROTOCOL_SKILL.read_text(encoding="utf-8"), mirroring_cli_verbs()
    )
    assert not missing, "agent-protocol/SKILL.md: " + "; ".join(missing)


def test_the_protocol_skill_names_the_finalize_flags_the_cli_really_takes():
    """The two flags lifted out of the identities must resolve against the real
    parser -- a renamed flag fails here rather than stranding the instruction."""
    text = PROTOCOL_SKILL.read_text(encoding="utf-8")
    real = finalize_flags()
    for flag in ("--plan-task-id", "--session-id"):
        assert flag in real, f"{flag} is no longer a `gaia contract finalize` option"
        assert flag in text, (
            f"SKILL.md does not mention {flag}, which the identities used to "
            "carry and which lives in no other injected file"
        )


def test_the_protocol_skill_stays_inside_its_always_loaded_budget():
    """`skill-creation` caps an always-loaded SKILL.md at under 100 lines: this
    one is paid on EVERY dispatch of EVERY agent, so growth here is not free."""
    lines = len(PROTOCOL_SKILL.read_text(encoding="utf-8").splitlines())
    assert lines < 100, f"agent-protocol/SKILL.md is {lines} lines (budget: < 100)"


def test_the_forward_check_can_fail():
    """A stub document trips every clause -- otherwise the check is decoration."""
    missing = missing_protocol_clauses(
        "# some-doc\n\nDo the work and emit a contract at the end.\n",
        {"set", "add", "fill"},
    )
    assert len(missing) >= 6, missing


def test_the_forward_check_catches_a_stale_bare_init():
    good = PROTOCOL_SKILL.read_text(encoding="utf-8")
    assert not missing_protocol_clauses(good, {"set", "add", "fill"})
    stale = good + "\n\nBuild it with `gaia contract init` and finalize.\n"
    assert any(
        m.startswith("stale bare-init instruction")
        for m in missing_protocol_clauses(stale, {"set", "add", "fill"})
    )


# ===========================================================================
# BACKWARD -- no identity re-teaches the flow.
# ===========================================================================
def test_the_fleet_is_the_eight_specialists():
    names = [p.stem for p in specialist_definitions()]
    assert len(names) == EXPECTED_SPECIALIST_COUNT, names
    assert "gaia-orchestrator" not in names


def test_mirror_verbs_are_set_add_fill():
    assert mirroring_cli_verbs() == {"set", "add", "fill"}


@pytest.mark.parametrize(
    "definition", specialist_definitions(), ids=lambda p: p.stem
)
def test_definition_does_not_re_teach_the_contract_flow(definition):
    """An identity says who the agent is. The flow arrives preloaded from
    `agent-protocol` in the same window, so a second copy here buys nothing and
    drifts independently -- which is exactly how nine false claims survived."""
    text = definition.read_text(encoding="utf-8")
    offenses = []

    named = contract_verbs_named(text) & contract_verbs()
    if named:
        offenses.append(
            f"instructs `gaia contract` verb(s) {sorted(named)} -- "
            "the CLI flow belongs to agent-protocol/SKILL.md alone"
        )
    if IDENTITY_ANCHOR in text:
        offenses.append(
            f"re-teaches the injected {IDENTITY_ANCHOR!r} block, which the "
            "dispatch kernel and the skill already cover"
        )
    if re.search(r"fenced\s+`?agent_contract_handoff", text):
        offenses.append("re-teaches the closing fence")
    if re.search(r"^##+\s+Contract Protocol\s*$", text, re.MULTILINE):
        offenses.append("still carries a `Contract Protocol` section")

    assert not offenses, f"{definition.name}: " + "; ".join(offenses)


def test_the_backward_check_can_fail():
    """Re-teaching must be detectable, or the direction is decoration. The probe
    is the block that was actually removed, in miniature."""
    relapse = (
        "# some-agent\n\n## Contract Protocol\n\n"
        "Adopt the identity injected as a `# Your Contract` block. Your first\n"
        "`gaia contract set/add/fill --draft-id <id>` adopts it; close by emitting\n"
        "the fenced `agent_contract_handoff` block.\n"
    )
    named = contract_verbs_named(relapse) & contract_verbs()
    assert named, "the verb scan went blind"
    assert IDENTITY_ANCHOR in relapse
    assert re.search(r"fenced\s+`?agent_contract_handoff", relapse)
    assert re.search(r"^##+\s+Contract Protocol\s*$", relapse, re.MULTILINE)


def test_the_verifier_keeps_the_binding_rule_only_its_surface_has():
    """The backward direction bans RE-TEACHING, never a surface's own fact.

    A verifier turn is bound by `parent_handoff_id` and carries no
    `plan_task_id`, which is why it is the one turn that may self-`COMPLETE`.
    That is true of this agent and of no other, and it appears in no injected
    file -- so removing it as "more duplication" would take real instruction
    with it. Pinned at both ends: the paragraph must say it, and the symbol that
    makes it true must exist. The check is deliberately NOT a bare substring
    search: `parent_handoff_id` alone occurs wherever the topic is mentioned,
    so the assertion is on the mechanism the paragraph names.
    """
    text = (AGENTS_DIR / "gaia-verifier.md").read_text(encoding="utf-8")
    # The rule spans several sentences of one paragraph, so the span may cross a
    # period -- bounded by length instead, which is what keeps it a paragraph
    # match rather than a match against the whole file.
    binding = re.compile(
        r"bound\s+by\s+`parent_handoff_id[^`]*`[\s\S]{0,700}?self-`?COMPLETE",
        re.IGNORECASE,
    )
    assert binding.search(text), (
        "gaia-verifier.md no longer states that its dispatch binds by "
        "parent_handoff_id and may therefore self-COMPLETE"
    )
    assert "extract_dispatch_binding" in text, (
        "gaia-verifier.md no longer names the parser that stamps the binding"
    )
    hooks_src = "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (_REPO_ROOT / "hooks").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for symbol in ("extract_dispatch_binding", "_blind_verification_required"):
        assert symbol in hooks_src, (
            f"gaia-verifier.md describes {symbol}, which is not in the hooks"
        )


# ===========================================================================
# CLAIM <-> SYMBOL -- prose may not outlive the symbol that licensed it.
# ===========================================================================
_CODE_ROOTS = ("hooks", "gaia", "bin", "tools", "scripts")
_DOC_ROOTS = ("agents", "skills")


@dataclass(frozen=True)
class Claim:
    """One assertion, bound to the code symbol whose STATE decides its truth.

    Two polarities, because prose rots both ways. A claim can die because the
    symbol that upheld it was DELETED (the gate's fence source), and a claim can
    die because a symbol that contradicts it was ADDED (a tolerant fallback that
    accepts the very thing the prose says is rejected). ``forbidden_while`` names
    which state kills this claim -- ``"absent"`` or ``"present"`` -- so one
    mechanism covers both instead of only the first one anybody noticed.

    ``pattern`` matches the SHAPE of the assertion, not one wording, so a
    paraphrase trips it too. ``exempt_when`` are the markers that, inside the same
    sentence, mean the sentence does NOT make the claim; that is polarity-
    dependent on purpose. For an affirmative claim they are negations ("not
    because", "no longer"). For a claim that is ITSELF a negation they are the
    opposite: the vocabulary of the behaviour being denied. Getting this field
    wrong bans the correction, which is worse than not guarding at all -- so every
    row carries a control in each direction and both are asserted every run.
    """

    claim_id: str
    symbol: str
    forbidden_while: str
    pattern: str
    exempt_when: str
    must_match: "tuple[str, ...]"
    must_not_match: "tuple[str, ...]"
    why: str

    def __post_init__(self):
        if self.forbidden_while not in ("absent", "present"):
            raise ValueError(
                f"{self.claim_id}: forbidden_while must be 'absent' or 'present'"
            )


CLAIM_LEDGER: "tuple[Claim, ...]" = (
    Claim(
        claim_id="fence-is-a-gate-fallback",
        symbol="GATE_SOURCE_FENCE",
        forbidden_while="absent",
        # fence and fallback in either order, inside one sentence.
        pattern=(
            r"fence[^.]{0,140}(?:fall(?:s|ing|en)?\s+back|\bfallback\b)"
            r"|(?:fall(?:s|ing|en)?\s+back|\bfallback\b)[^.]{0,140}fence"
        ),
        # Deliberately NOT a bare "no": the false sentence itself contains
        # "no dispatch row reachable", and exempting on that would blind the row.
        # The last three markers disambiguate from the OTHER fallback in this
        # system -- contract_validator's tolerant json-tag fallback, which is a
        # parse-time mechanism and has nothing to do with the close. Two rows
        # sharing the word "fallback" is not a coincidence to paper over: each
        # has to say which fallback it means, or the true sentence describing
        # one gets flagged as the false claim about the other. It did.
        exempt_when=(
            r"not because|never|no longer|used to|in no case|in none of"
            r"|is not|are not|was retired|been retired|losing|removed"
            r"|tolerant|_RE_JSON_FALLBACK|mislabel"
        ),
        must_match=(
            "The fence decides only as a fallback, for a turn with no dispatch "
            "row reachable at all.",
            "The fence still stays required output every turn, now as the "
            "gate's fallback when no dispatch row is reachable at all.",
        ),
        must_not_match=(
            "Emit the fence because `parse_contract` still feeds it to the "
            "turn's descriptive readers, not because the gate ever falls back "
            "to it.",
            "Not because the gate ever falls back to the fence -- in none of "
            "its cases -- but because `parse_contract` feeds it to readers.",
            "Losing the fence as a fallback costs almost nothing in the other "
            "direction.",
            # The sentence this row wrongly flagged once: it describes the
            # PARSER's tolerant fallback, not the gate's retired one.
            "Still end the message with the envelope in a fenced block tagged "
            "`agent_contract_handoff`, the tag `parse_contract` looks for; a "
            "`json` fence whose body is already envelope-shaped is picked up "
            "too, by a deliberate tolerant fallback (`_RE_JSON_FALLBACK`), so "
            "the right tag is hygiene rather than a cliff.",
        ),
        why=(
            "commit ac721c2 removed the fence as an input to the SubagentStop "
            "gate; _resolve_subagent_stop_gate_full now decides in three "
            "row-only cases and reads nothing from the response text"
        ),
    ),
    Claim(
        claim_id="json-tagged-fence-is-not-recognized",
        symbol="_RE_JSON_FALLBACK",
        forbidden_while="present",
        # The assertion of NON-recognition: a FENCE/TAG word, the token json,
        # and a negated recognition verb, all in one sentence. The polarity is
        # inverted from the row above -- here the claim IS a negation, so a
        # negation marker is the claim itself and must never be an exemption.
        # The fence/tag word is load-bearing, not decoration: without it the row
        # matched "it is NOT parsed from `--output-format json`" in an unrelated
        # skill. A claim about a fence label has to be scoped to fence labels.
        pattern=(
            r"(?:fence|fenced|block|tag(?:ged)?|label)[^.]{0,80}?`?json`?"
            r"[^.]{0,110}?(?:does\s+not|doesn't|will\s+not|won't|is\s+not|isn't"
            r"|never)\s+(?:match|parse|regist|count|resolve|recogni)"
            r"|(?:does\s+not|doesn't|will\s+not|won't|is\s+not|isn't|never)\s+"
            r"(?:match|parse|regist|recogni)\w*[^.]{0,110}?"
            r"(?:fence|fenced|block|tag(?:ged)?|label)[^.]{0,40}?`?json`?"
        ),
        # The behaviour being denied: a sentence that names the fallback is
        # describing the real mechanism, not asserting its absence.
        exempt_when=(
            r"tolerant|fallback|picked\s+up|accepted|is\s+taken|migration"
        ),
        must_match=(
            "Still end the message with the envelope in a fenced block tagged "
            "literally `agent_contract_handoff`; a block tagged `json` does "
            "not match.",
            "the label matters literally; a block tagged `json` does not match "
            "what the extractor looks for.",
        ),
        must_not_match=(
            "Still end the message with the envelope in a fenced block tagged "
            "`agent_contract_handoff`, the tag `parse_contract` looks for; a "
            "`json` fence whose body is already envelope-shaped is picked up "
            "too, by a deliberate tolerant fallback (`_RE_JSON_FALLBACK`), so "
            "the right tag is hygiene rather than a cliff.",
            "The final message still ends with the envelope in a fenced block "
            "tagged `agent_contract_handoff` (not `json` -- the tag is how "
            "`parse_contract` finds it).",
            # Not about a fence label at all. This row matched it once.
            "The headless session reads its OWN id from its shell env var -- "
            "it is NOT parsed from `--output-format json`.",
        ),
        why=(
            "contract_validator._RE_JSON_FALLBACK, scanned by parse_contract, "
            "deliberately accepts a ```json``` fence whose body already has "
            "envelope shape (_looks_like_handoff_envelope); the module "
            "docstring calls it a tolerant fallback for the recurring case of "
            "an agent mislabeling the fence, and "
            "tests/contract/test_fence_fallback.py exercises it"
        ),
    ),
)

_SENTENCE = re.compile(r"[^.!?]*[.!?]|[^.!?]+$")


def _asserts(claim: Claim, text: str) -> "list[str]":
    """Sentences in ``text`` that make ``claim`` rather than describe around it."""
    shape = re.compile(claim.pattern, re.IGNORECASE)
    exempt = re.compile(claim.exempt_when, re.IGNORECASE)
    return [
        sentence.strip()
        for sentence in _SENTENCE.findall(text)
        if shape.search(sentence) and not exempt.search(sentence)
    ]


def _symbol_is_in_the_code(symbol: str) -> bool:
    for root in _CODE_ROOTS:
        base = _REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if symbol in path.read_text(encoding="utf-8", errors="ignore"):
                return True
    return False


def documents() -> "list[Path]":
    return sorted(
        path
        for root in _DOC_ROOTS
        for path in (_REPO_ROOT / root).rglob("*.md")
    )


@pytest.mark.parametrize("claim", CLAIM_LEDGER, ids=lambda c: c.claim_id)
def test_the_claims_anchor_is_where_the_ledger_says_it_is(claim):
    """The row's premise, rechecked against the code every run.

    A row is only meaningful while the symbol is really in the state it names.
    If that flips, the prose may be legal again -- and a guard that stayed green
    through the flip would be banning a true sentence.
    """
    actual = "present" if _symbol_is_in_the_code(claim.symbol) else "absent"
    assert actual == claim.forbidden_while, (
        f"ledger row {claim.claim_id!r} forbids its claim while {claim.symbol} "
        f"is {claim.forbidden_while}, and the symbol is now {actual} -- revisit "
        f"the row and the prose it governs before touching this assertion "
        f"({claim.why})"
    )


@pytest.mark.parametrize("claim", CLAIM_LEDGER, ids=lambda c: c.claim_id)
def test_the_claim_pattern_can_fire_and_knows_a_denial_from_an_assertion(claim):
    """Both controls, every run. A pattern that no longer matches its own
    positive control has gone blind; one that matches its negative control bans
    the correction, which is worse than not guarding at all."""
    for sentence in claim.must_match:
        assert _asserts(claim, sentence), (
            f"{claim.claim_id}: pattern no longer matches a sentence that DOES "
            f"make the claim -- {sentence!r}"
        )
    for sentence in claim.must_not_match:
        assert not _asserts(claim, sentence), (
            f"{claim.claim_id}: pattern matches a sentence that DENIES the "
            f"claim, so the correction itself would be rejected -- {sentence!r}"
        )


@pytest.mark.parametrize("claim", CLAIM_LEDGER, ids=lambda c: c.claim_id)
def test_no_document_asserts_a_claim_its_symbol_no_longer_licenses(claim):
    """THE property. Existence and completeness both compare name to name; only
    this direction asks whether a sentence is still true."""
    actual = "present" if _symbol_is_in_the_code(claim.symbol) else "absent"
    if actual != claim.forbidden_while:
        pytest.skip("the symbol state that kills this claim does not hold")
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}: {sentence}"
        for path in documents()
        for sentence in _asserts(claim, path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"{claim.symbol} is {claim.forbidden_while} in the code ({claim.why}), "
        "so no document may assert otherwise:\n  " + "\n  ".join(offenders)
    )


def test_the_claim_scan_reaches_the_documents_it_claims_to_scan():
    """If the doc walk returned nothing, every assertion above would pass on an
    empty set. This is the tripwire for that."""
    docs = documents()
    assert len(docs) >= 40, f"only {len(docs)} documents walked"
    stems = {p.parent.name for p in docs}
    assert "agents" in stems and "agent-protocol" in stems, sorted(stems)[:10]
