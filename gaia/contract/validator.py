"""
Form-layer validator (layer 1) -- pure, portable, harness-agnostic.

This module is the SINGLE SOURCE OF TRUTH for the *shape* of an
``agent_contract_handoff`` envelope. It validates a parsed envelope (a plain
``dict``) by SHAPE ONLY, rejecting each malformed case with a NAMED error code
drawn from a small, stable enum, and always exposing the canonical rich repair
message so the runtime can hand the agent an actionable fix.

It unifies the shape logic of the two pre-existing validators
(``hooks/modules/agents/contract_validator.py`` and
``hooks/modules/agents/response_contract.py``) into one portable core that the
CLI (M2), the hook gate (M4), the fence fallback (M6) and the packaging smoke
test (M6) all import. Downstream tasks (T2-T17) treat the public surface below
as a stable interface -- the four error codes, the ``validate_form`` signature,
the ``FormValidationResult`` shape, and ``CANONICAL_REPAIR_MESSAGE`` -- so it
must not change without a plan-level decision.

PORTABILITY CONTRACT (enforced by tests/contract/test_validator_portable.py):
    - Imports ONLY the Python standard library plus ``gaia.state`` (itself
      stdlib-pure), which is the SSOT for ``VALID_PLAN_STATUSES``.
    - NEVER imports from ``hooks/`` and NEVER pulls in a third-party package.
    - The ``gaia.state`` import degrades to an inline stdlib fallback when the
      package is not on the path, so the module remains importable in a bare
      stdlib subprocess.

The named codes (AC-1; VERIFICATION_SHAPE added additively in R3 per a
plan-level decision -- brief contract-type-conditional-validation-harness-r3;
APPROVAL_REQUEST_SHAPE and COMPLETE_SHAPE added additively in R4, closing the
two pure-shape cross-field conditionals the form layer previously missed):
    AGENT_ID_FORMAT     -- agent_id is present but does not match
                           ``AGENT_ID_PATTERN_TEXT`` (^a[0-9a-f]{16,}$)
    PLAN_STATUS         -- agent_state is present but outside the canonical enum
                           (the error CODE keeps the name PLAN_STATUS -- a stable
                           public-surface identifier -- while the FIELD it guards
                           is now agent_state)
    VERIFICATION_RESULT -- agent_state is COMPLETE but verification.result != "pass"
                           (including a missing/malformed verification block)
    MISSING_FIELD       -- a required field (agent_status, an agent_status
                           sub-field, evidence_report, or a required
                           evidence_report key) is absent
    VERIFICATION_SHAPE  -- verification.type declares a type but the evidence
                           that type requires is missing/empty (a by-TYPE SHAPE
                           check, independent of agent_state; DISTINCT from
                           VERIFICATION_RESULT). Applies to EVERY declared type:
                           a known one owes its named field, an out-of-enum one
                           owes at least one of command/reviewed/requires_human.
                           Absent or blank type == no check.
    APPROVAL_REQUEST_SHAPE -- agent_state is APPROVAL_REQUEST but the top-level
                           approval_request object is absent/null, or present
                           without a non-empty exact_content (the verbatim
                           content the user must see for informed consent).
                           approval_id is deliberately NOT required here --
                           agent-response documents a legitimate
                           approval_request with no approval_id yet (a plan
                           presented before the hook has blocked anything and
                           minted a grant).
    COMPLETE_SHAPE      -- agent_state is COMPLETE but next_action != "done"
                           (when next_action is present) or pending_steps is
                           non-empty (when pending_steps is present). Pure
                           cross-field coherence, independent of
                           VERIFICATION_RESULT; the MISSING_FIELD checks above
                           already own the case where either sub-field is
                           absent, so this never stacks with MISSING_FIELD on
                           the same field.
    FAILURE_REPORT_SHAPE -- the OPTIONAL top-level failure_report block is
                           present but malformed (not an object, a required
                           sub-field missing/blank, an empty evidence list, or
                           a severity outside the enum). Absent or null ==
                           no check, on every agent_state.
    WORK_PHASE_SHAPE    -- the OPTIONAL top-level work_phase field is present
                           but not one of VALID_WORK_PHASES. Absent or null ==
                           no check, on every agent_state -- work_phase is the
                           observable WORK cycle (framing/investigating/
                           planning/executing/verifying), orthogonal to the
                           agent_state communication states above; see the
                           VALID_WORK_PHASES comment for why the two are kept
                           as separate enums.

Design notes:
    - SHAPE ONLY: the form layer takes the already-parsed envelope dict. Fence
      extraction (the ```agent_contract_handoff``` regex) and any DB cross-check
      (approval_id / nonce) live in other layers, not here.
    - NO TASK CONTEXT: consolidation_report is context-dependent (needs
      task_info / multi-surface signals) and is therefore NOT a form-layer
      concern -- it belongs to a higher layer that has that context.
    - ONE CODE PER INVALIDITY: an out-of-enum agent_state yields exactly
      PLAN_STATUS and suppresses the downstream evidence requirement (an invalid
      status cannot be classified as evidence-requiring), so a single defect
      does not fan out into multiple codes. This matches AC-9's "one anomaly per
      invalidity".
    - ORDER-AWARE DETAIL, not relaxed validation: the CLI (``bin/cli/contract.py``)
      lets a caller build the envelope incrementally across several small
      ``gaia contract set``/``add``/``fill`` calls, and validates the FULL
      resulting envelope on every write. A cross-field code (VERIFICATION_RESULT,
      COMPLETE_SHAPE, APPROVAL_REQUEST_SHAPE, and the per-type branches of
      VERIFICATION_SHAPE) can therefore fire not because the caller misunderstood
      the requirement, but because it set the terminal ``agent_state`` before
      filling the field that state depends on -- a build-ORDER mistake, not a
      content mistake. Because a terminal row is immutable once
      ``gaia contract finalize`` persists it (the writer's guard refuses to
      rewrite it), there is no correcting the order after the fact -- the
      ``detail`` on these four codes therefore names not just what is missing,
      but the concrete order to build in (dependency field first, agent_state
      last), so a single corrected write can succeed. This teaches the caller
      the ordering rule; it does not loosen what the resulting envelope must
      satisfy -- an invalid envelope is rejected exactly as before.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Tuple

# ---------------------------------------------------------------------------
# Canonical plan_status enum -- SSOT is gaia.state.VALID_PLAN_STATUSES.
#
# Imported with a stdlib-only fallback so the module stays importable in a bare
# stdlib subprocess (AC-2). gaia.state is itself stdlib-pure (only
# ``from __future__ import annotations``), so importing it never violates the
# portability contract; the fallback exists solely for a path on which the gaia
# package root is absent. The fallback is kept byte-identical to the canonical
# tuple so behaviour cannot drift between the two paths.
# ---------------------------------------------------------------------------
try:
    from gaia.state import VALID_PLAN_STATUSES as _CANONICAL_PLAN_STATUSES

    VALID_PLAN_STATUSES: Tuple[str, ...] = tuple(_CANONICAL_PLAN_STATUSES)
except ImportError:  # pragma: no cover -- exercised only on a bare stdlib path
    VALID_PLAN_STATUSES = (
        "IN_PROGRESS",
        "APPROVAL_REQUEST",
        "COMPLETE",
        "BLOCKED",
        "NEEDS_INPUT",
        "NEEDS_VERIFICATION",
    )

# ---------------------------------------------------------------------------
# Canonical verification_type enum -- SSOT is gaia.state.VALID_VERIFICATION_TYPES.
#
# Imported with the SAME stdlib-only fallback idiom as VALID_PLAN_STATUSES above
# so the module stays importable in a bare stdlib subprocess (portability
# contract). Unlike plan statuses this maps to no DB column -- it is the SSOT for
# the ``type`` field of a contract-envelope verification block. The fallback is
# kept byte-identical to gaia.state.VALID_VERIFICATION_TYPES so behaviour cannot
# drift between the two paths.
# ---------------------------------------------------------------------------
try:
    from gaia.state import VALID_VERIFICATION_TYPES as _CANONICAL_VERIFICATION_TYPES

    VALID_VERIFICATION_TYPES: Tuple[str, ...] = tuple(_CANONICAL_VERIFICATION_TYPES)
except ImportError:  # pragma: no cover -- exercised only on a bare stdlib path
    VALID_VERIFICATION_TYPES = (
        "command",
        "code",
        "semantic",
        "self_review",
    )

# ---------------------------------------------------------------------------
# Envelope-only verification.type enum (plan 34 task 7).
#
# The CONTRACT ENVELOPE additionally accepts ``verification.type == "none"`` for
# a turn that performed NO plan-task-bound verification -- an investigation or
# memory turn that carries no ``plan_task_id`` and is therefore free to
# self-COMPLETE (the finalize gate keys on plan_task_id, not role). "none" names
# "no external oracle was required"; it demands no additional field.
#
# This extension is DELIBERATELY scoped to the envelope and MUST NOT widen
# VALID_VERIFICATION_TYPES -- that tuple is the shared SSOT (gaia.state) backing
# the persisted CHECK on task_gates.verification_type, which must stay exactly
# command / code / semantic / self_review. Extending the envelope enum here can
# never contaminate the task_gates CHECK: the two vocabularies are now distinct
# on purpose (a gate's verification_type is a promise to run a real oracle; the
# envelope's "none" is the explicit absence of one).
# ---------------------------------------------------------------------------
_ENVELOPE_ONLY_VERIFICATION_TYPES: Tuple[str, ...] = ("none",)
ENVELOPE_VERIFICATION_TYPES: Tuple[str, ...] = (
    VALID_VERIFICATION_TYPES + _ENVELOPE_ONLY_VERIFICATION_TYPES
)

# ---------------------------------------------------------------------------
# verification.type -- an OPEN vocabulary that grants no discount.
#
# The shape check below used to fire only when the declared type was a MEMBER
# of ENVELOPE_VERIFICATION_TYPES, so a word outside the list switched the
# requirement off. That is backwards: naming a type the enum happens to know
# cost the agent its companion field, and naming one it did not cost nothing.
# Measured on the live population, 127 of 522 typed rows (24.3%) sat outside
# the enum, and 123 of those carried no companion evidence at all -- not by
# malice but by economics, because inventing a plausible word was simply
# cheaper than producing the evidence. The values are ordinary and often
# accurate: test, self-review, dry_run, command_execution, manual,
# observation, oracle, and a tail of some thirty more.
#
# The fix removes the DISCOUNT rather than the VOCABULARY. Two rules, applied
# in order, and they answer two different problems:
#
#   1. CANONICALIZATION (_canonical_verification_type) folds pure orthography.
#      A hyphen and an underscore spell one concept -- self-review/self_review,
#      dry-run/dry_run, command-execution/command_execution all occur in both
#      forms -- so they are folded to one token before anything is decided.
#      This is lossless by construction: it rewrites separators, never words.
#      It deliberately does NOT map a semantic type onto a known one. "manual",
#      "observation" and "oracle" survive verbatim, because choosing a known
#      type on their behalf would be inventing a claim the agent never made.
#
#   2. THE COMPANION FIELD IS REQUIRED FOR EVERY TYPE, known or not
#      (_verification_type_shape_error). Declaring a type is a claim that a
#      verification happened; the price of the claim is naming the evidence.
#      For a known type the enum says WHICH field (command / requires_human /
#      reviewed). For a word the enum does not know, no such mapping exists, so
#      the demand is stated at the only altitude available: at least ONE of the
#      three. That is not a weaker rule -- there is no fourth kind of evidence
#      the envelope recognizes, and no branch of the disjunction is free.
#
# "none" stays exempt, and that exemption is what proves the discount is gone
# rather than relocated: it is reachable at exactly ONE spelling, a word that
# says out loud "no oracle was required" and can be audited as such. An
# invented word no longer lands there -- it lands in rule 2.
#
# The rejection is NOT a cell (see the sanitization note further down for what
# a cell is and why this file is careful about them). The remedy for it is an
# ADDITION -- write the command, the reviewed statement, or requires_human --
# and addition is exactly what every mutating verb does: `fill --json` and
# `set` deep-merge into the existing verification object, so the corrective
# write produces a merged envelope that validates and lands. That is the
# structural difference from the traps this file has hit before, where the
# offending state was an undeclared key or a malformed value and NO write
# could remove it.
# ---------------------------------------------------------------------------
_VERIFICATION_TYPE_SEPARATOR_RE = re.compile(r"[\s_-]+")

# The three fields the envelope recognizes as verification evidence, in the
# order a writer is offered them. Ordered so the deterministic one comes first:
# a command another party can re-run is the strongest of the three.
_VERIFICATION_EVIDENCE_FIELDS: Tuple[str, ...] = (
    "command",
    "reviewed",
    "requires_human",
)


def _canonical_verification_type(raw: Any) -> str:
    """Fold a declared ``verification.type`` to its canonical spelling.

    Strips, lower-cases, and collapses every run of separator characters
    (whitespace, hyphen, underscore) to a single underscore -- so
    ``"Self-Review"``, ``"self review"`` and ``"self_review"`` are one token.
    Only separators move; no word is ever substituted for another, which is
    what keeps this safe to apply to types outside the enum.

    Returns ``""`` for a value that carries no declaration at all (``None`` or
    a blank string). An empty result means "no type was declared" and fires no
    requirement -- an empty string is not a claim, and treating it as one would
    invent a rejection class over a value that has always been a no-op.
    """
    if raw is None:
        return ""
    text = str(raw).strip().lower()
    if not text:
        return ""
    return _VERIFICATION_TYPE_SEPARATOR_RE.sub("_", text).strip("_")

# ---------------------------------------------------------------------------
# work_phase -- the observable WORK cycle (agent-protocol, work-cycle-observability
# design). Orthogonal to agent_state on purpose:
#
#   agent_state (VALID_PLAN_STATUSES above) is the COMMUNICATION state machine --
#   how THIS TURN currently reports back (IN_PROGRESS/BLOCKED/NEEDS_INPUT/
#   APPROVAL_REQUEST/NEEDS_VERIFICATION/COMPLETE). It feeds routing and the
#   finalize/verification gate, a pure function of (agent_state, plan_task_id),
#   and that gate must not grow a second axis.
#
#   work_phase is the WORK state machine -- WHERE the producer is in framing ->
#   investigating -> planning -> executing -> verifying. Two turns can carry the
#   identical agent_state (IN_PROGRESS) while being in entirely different work
#   phases; collapsing the two into one enum would force agent_state to grow
#   phase-shaped values and break the routing/verification gate's purity. Kept
#   separate instead.
#
# Like ENVELOPE_VERIFICATION_TYPES's "none" above, this enum backs NO DB CHECK
# column -- agent_contract_handoffs persists the whole envelope in
# raw_handoff_json, so a new top-level key needs no migration -- and therefore
# does NOT belong in gaia.state.STATE_MACHINE_REGISTRY (that registry is
# reserved for tuples paired with a real SQL CHECK). It is defined here, local
# to the form layer, exactly where the DB-free envelope-only enum precedent
# already lives.
#
# work_phase is OPTIONAL on every agent_state (mirrors failure_report): a
# turn with no investigating/planning/executing/verifying phase -- a single
# read-only lookup, say -- never sets it, and its absence is never an error.
# Presence is validated in full: an out-of-enum value is a WORK_PHASE_SHAPE
# rejection, so a typo'd phase does not silently pass as a null-equivalent.
# ---------------------------------------------------------------------------
VALID_WORK_PHASES: Tuple[str, ...] = (
    "framing",
    "investigating",
    "planning",
    "executing",
    "verifying",
)

# Evidence is required for every valid status (no exclusions), matching
# EVIDENCE_REQUIRED_PLAN_STATUSES in response_contract.py.
_EVIDENCE_REQUIRING_STATUSES = frozenset(VALID_PLAN_STATUSES)

# Canonical agent_id shape -- the SINGLE source of truth for every executable
# copy of this rule. ``hooks.modules.agents.response_contract`` re-exports it
# (with a stdlib fallback for a bare subprocess), and the two SendMessage
# validators import it from there rather than re-spelling the literal: four
# independent copies of this regex are exactly what let the floor drift.
#
# The 16-hex floor is measured, not conventional. Cross-session handle
# collisions fall off a cliff with length, because a biased model only
# collides where it can compress the digits it has to invent:
#   6 hex  -> 27 of 82 handles collided   (32.9%)
#   7 hex  -> 12 of 103 handles collided  (11.7%)
#   17 hex -> 0 of 2658 handles collided  ( 0.0%)
# 16 is the smallest floor comfortably inside the zero-collision regime and is
# exactly ``secrets.token_hex(8)``, which is what ``gaia contract init`` mints
# when no --agent-id is supplied.
#
# Raising the floor is deliberately NOT retroactive: this pattern gates what an
# agent may MINT for a new turn. Historical rows and drafts keyed by a shorter
# handle are read back by exact string, never re-validated against this regex,
# so no grandfathering window is required for them.
AGENT_ID_MIN_HEX = 16
AGENT_ID_PATTERN_TEXT = r"^a[0-9a-f]{%d,}$" % AGENT_ID_MIN_HEX
_AGENT_ID_PATTERN = re.compile(AGENT_ID_PATTERN_TEXT)

# Required evidence_report keys (canonical lower-case JSON form). Upper-case
# variants are also accepted for backward compatibility, matching both existing
# validators. Presence is checked, not truthiness: an explicit empty list [] is
# valid.
REQUIRED_EVIDENCE_FIELDS: Tuple[str, ...] = (
    "patterns_checked",
    "files_checked",
    "commands_run",
    "key_outputs",
    "verbatim_outputs",
    "cross_layer_impacts",
    "open_gaps",
)

# Required agent_status sub-fields. agent_state and agent_id have dedicated
# codes for the "present-but-malformed" case; all four are subject to
# MISSING_FIELD when absent. pending_steps accepts an empty list (presence
# check); next_action must be a non-empty value.
#
# agent_state is the canonical name of the TURN-status field (renamed from the
# former ``plan_status`` envelope key, plan 34 task 4). It carries a
# VALID_PLAN_STATUSES value and matches the persisted
# ``agent_contract_handoffs.agent_state`` column. The enum constant and the
# PLAN_STATUS error code keep their names -- the enum is still the SSOT shared
# with the ``episodes.plan_status`` lifecycle column, and the error code is a
# stable public-surface identifier.
REQUIRED_AGENT_STATUS_FIELDS: Tuple[str, ...] = (
    "agent_state",
    "agent_id",
    "pending_steps",
    "next_action",
)

# ---------------------------------------------------------------------------
# failure_report -- the OPTIONAL advisory failure axis (AC-1).
#
# A turn can end in a valid contract and still have suffered a concrete defect
# along the way: something it attempted broke. The envelope had no place to say
# so -- open_gaps is free prose about what is UNKNOWN, and rollback_executed is
# about a rollback, not a defect -- so those failures were only ever recoverable
# by reading a transcript. This block is that place, and it is ADVISORY in the
# strict sense: its ABSENCE is never an error on any agent_state, and its
# PRESENCE is never a substitute for any other requirement. Thousands of
# terminal rows persisted before it existed keep the exact verdict they had.
#
# The shape is a single object, not an array. One defect per turn is the one
# worth curating; a list invites a dump, and the incremental CLI build
# (`gaia contract set failure_report.symptom ...`) only reads naturally on an
# object. A turn that genuinely hit several failures reports the one that
# mattered and cites the rest in ``evidence``.
#
# The three required sub-fields ARE the axis -- what was attempted, what broke,
# and the observed proof -- and the shape exists to be consumed by a writer, not
# only read by a human:
#
#     attempted  (str, non-empty)   the operation tried, stated concretely
#     symptom    (str, non-empty)   what broke, as observed rather than inferred
#     evidence   (list, non-empty)  verbatim excerpts: the error text, the exit
#                                   status, the command output that shows it
#     component  (str, optional)    the file/module/surface involved, when known
#     severity   (str, optional)    one of VALID_FAILURE_SEVERITIES
#
# ``evidence`` being REQUIRED and non-empty is what keeps this from becoming a
# prose field: a defect report that cannot cite what was observed is an opinion,
# and an opinion is not worth a row in the defect floor.
#
# Because presence triggers the whole shape at once, a partial first write is
# rejected -- ``set failure_report.attempted X`` alone leaves the block
# incomplete. That is the same order-aware trap the terminal statuses have, and
# the detail messages below say the same thing they do: build the block in ONE
# write (`gaia contract fill --json`), not field by field.
# ---------------------------------------------------------------------------
REQUIRED_FAILURE_REPORT_FIELDS: Tuple[str, ...] = (
    "attempted",
    "symptom",
    "evidence",
)

VALID_FAILURE_SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")

# ---------------------------------------------------------------------------
# files_checked -- the commit-qualified file reference.
#
# A cited file is evidence, and evidence with no lineage is a photograph: it
# shows a state without saying which one. When the file IS committed, the
# commit turns the citation into a door -- to the diff, to the message that
# says why, to the sibling files of the same move, to the PR discussion. So a
# files_checked entry may now carry the commit the file was read at, and a
# read-only investigation that produces nothing and commits nothing can still
# date every finding it reports.
#
# The rule is CONDITIONAL and attaches to the ARTEFACT, never to the turn: a
# committed file is cited with its commit, an uncommitted one is cited as a
# bare path and that is not a fault. The condition is a fact the system can
# check on its own rather than a discipline demanded of the agent, which is
# what keeps a half-commit from ever being the price of citing something.
#
# SHAPE, NOT EXISTENCE -- deliberate and load-bearing. Nothing here consults
# git. The validator stays pure and cheap (it runs on every incremental write,
# measured at 0.008 ms inside a 60 ms call), and that margin is exactly what
# lets it be strict for free. A reference to a commit that does not exist is
# well-FORMED and passes; resolving it belongs to whoever reads it.
#
# WHY AN OBJECT AND NOT A path@sha STRING. The form had to satisfy one hard
# constraint: a legitimate bare path must NEVER be mistaken for a malformed
# reference. That was settled by measurement over the 25,967 files_checked
# entries already persisted (9,680 rows) and on disk (212 drafts), not by
# taste -- each candidate scored by how many EXISTING entries its trigger
# would newly reject:
#
#     trailing @<token>   26  ("...approval_grants.py (match_command_set_grant @2181)")
#     any @              429  ("node_modules/@jaguilar87/gaia/tools/memory/episodic.py")
#     " @ "                7  (".github/workflows/foundation.yml @ century-inc/branchkinect-iac")
#     trailing #<token>   22  ("/tmp/runtime-plan.log (CI runtime plan log, build #3)")
#     OBJECT {path,commit} 0
#
# Every string separator collides with prose an agent already writes inside a
# path entry. A JSON object cannot collide with a JSON string at all -- the
# distinction is carried by the type, so the trigger is exact BY CONSTRUCTION
# rather than by a regex that has to out-guess free text. The census confirms
# it from the other side too: zero object elements exist in the whole
# population, so nothing already written can be caught by the new check.
#
# The check therefore fires ONLY on an element that DECLARES itself a
# reference by being an object. A string element is never inspected -- every
# bare path stays valid exactly as written, which is what makes this purely
# additive. A non-string, non-object element (one nested list exists in
# history) is left alone as well: it was accepted before and rejecting it now
# would re-open a verdict on a row nobody can rewrite.
FILE_REFERENCE_KEYS: Tuple[str, ...] = ("path", "commit")

# The commit token: hex only. 7 is git's own default abbreviation length
# (``core.abbrev``) and the floor below which an abbreviation stops being
# unambiguous in a real repository; 64 admits a SHA-256 object name as well as
# SHA-1's 40, so a repository that has migrated does not need a validator
# change. Matched against the stripped, lower-cased token, and canonicalized to
# that same form on write (see ``canonicalize_envelope``).
#
# A branch name, a tag, or HEAD is deliberately NOT a commit here: those move,
# and a reference that moves dates nothing -- which is the entire point of
# carrying one. The rejection says so and names the command that resolves it.
COMMIT_TOKEN_PATTERN_TEXT = r"^[0-9a-f]{7,64}$"
_COMMIT_TOKEN_PATTERN = re.compile(COMMIT_TOKEN_PATTERN_TEXT)

# ---------------------------------------------------------------------------
# The declared schema -- what a key IS, not merely that it is there.
#
# The form layer used to check PRESENCE only, and the live population shows
# exactly what that bought: the seven evidence lists accepted a string, a
# number or an object and answered ok (mistyped rows in six of the seven), and
# ``pending_steps`` carried a bare string on 166 rows. A required key holding
# the wrong type is not a filled field -- every reader downstream iterates it
# expecting a list and gets characters, or a dict and gets nothing.
#
# Each entry below is anchored to a MEASURED observation over the persisted
# population, never to a guess about what the field ought to hold:
#
#   dict-valued     agent_status / evidence_report (7201 / 7133 dicts, no
#                   counterexample), consolidation_report, approval_request,
#                   failure_report, context_consumption, loop_state
#   memory_delta    dict -- anchored to its consumer,
#                   ``modules.agents.response_contract._extract_memory_delta``,
#                   which requires an object carrying ``version`` +
#                   ``proposals``; the population holds only nulls
#   list-valued     update_contracts, memory_suggestions,
#                   memorialize_suggestions (lists, with a single dict
#                   counterexample that is itself the defect this closes)
#   str-valued      work_phase, user_facing_summary
#
# ``rollback_executed`` is the one field admitting two spellings, and that is
# its consumer's doing rather than a hedge: ``parse_rollback_executed`` returns
# ``str(val)``, so a boolean and a sentence are both real, already-supported
# inputs. Narrowing it to ``bool`` here would reject a value the reader
# explicitly accepts.
#
# A type is enforced only on a value that is PRESENT and not null: an explicit
# null is the seeded convention for every optional block (see
# ``gaia.contract.drafts.initial_envelope``), and absence/nullity of a REQUIRED
# field is already owned by MISSING_FIELD. That split is what keeps one code
# per invalidity.
# ---------------------------------------------------------------------------
TOP_LEVEL_FIELD_TYPES = {
    "agent_status": (dict,),
    "evidence_report": (dict,),
    "consolidation_report": (dict,),
    "approval_request": (dict,),
}

# ``failure_report`` and ``work_phase`` are declared fields whose type IS
# checked -- by the dedicated code that already owned them,
# FAILURE_REPORT_SHAPE and WORK_PHASE_SHAPE, each of which reports a
# wrong-typed value with a message written for that field (the required
# sub-fields, the five phase names). Adding them to the table above would
# rename an existing rejection for no gain and stack two codes on one defect.
# They are listed here so the schema is complete where it is read.
_FIELDS_TYPED_BY_A_DEDICATED_CODE: Tuple[str, ...] = (
    "failure_report",
    "work_phase",
)

# ---------------------------------------------------------------------------
# The advisory optional fields -- declared, allowlisted, deliberately UNTYPED.
#
# These carry an EXPLICIT pre-existing contract, written three times in
# ``modules.agents.contract_validator``: "The return value is purely
# informational; the validator never rejects based on this field"
# (``parse_rollback_executed``, ``parse_context_consumption``,
# ``parse_user_facing_summary``). Their parsers degrade to ``None`` on a
# malformed value and the turn still closes, and a test asserts exactly that:
# ``test_non_string_summary_is_none`` -- "a malformed optional field never
# blocks".
#
# Type-checking them would reverse that contract: a turn that emits a
# malformed advisory field closes today and would be REJECTED instead. That is
# a runtime behaviour change for a field nothing load-bearing reads, so it is
# NOT taken here on the validator's own initiative. They stay in the
# allowlists (a typo among them is still caught by UNKNOWN_FIELD) and out of
# the type table.
#
# Flipping this is one edit -- move a name from this tuple into
# TOP_LEVEL_FIELD_TYPES with its type -- and the observed types are recorded
# here so the decision needs no re-measurement: user_facing_summary str (236,
# with 1 null), update_contracts list (282, 1 dict, 5 null),
# memorialize_suggestions list (27, 1 null), memory_suggestions list (12, 1
# null), context_consumption dict (4, 2 null), loop_state dict (3),
# memory_delta null only (its consumer ``_extract_memory_delta`` requires an
# object carrying version + proposals), rollback_executed null only (its
# consumer returns ``str(val)``, so a boolean and a sentence are both real).
# ---------------------------------------------------------------------------
ADVISORY_UNTYPED_FIELDS: Tuple[str, ...] = (
    "context_consumption",
    "loop_state",
    "memorialize_suggestions",
    "memory_delta",
    "memory_suggestions",
    "rollback_executed",
    "update_contracts",
    "user_facing_summary",
)

AGENT_STATUS_FIELD_TYPES = {
    "agent_state": (str,),
    "agent_id": (str,),
    "pending_steps": (list,),
    "next_action": (str,),
}

EVIDENCE_FIELD_TYPES = dict(
    {key: (list,) for key in REQUIRED_EVIDENCE_FIELDS},
    verification=(dict,),
)

# ---------------------------------------------------------------------------
# Keys the SYSTEM writes into an envelope -- never an agent.
#
# This is the allowlist's delicate half, and it was built by sweeping the 9646
# persisted envelopes rather than by reading the source: three of these
# (``_contract_tag`` on 3806 rows, ``fallback`` on 1486, ``salvaged`` on 172)
# have no grep-visible assignment at the top level and would have been missed
# by source inspection alone. Rejecting any one of them does not tighten
# validation -- it breaks the mechanism that writes it:
#
#   _contract_tag       stamped onto EVERY fence-parsed envelope by
#                       ``modules.agents.contract_validator.parse_contract``,
#                       whose result is handed straight to ``validate_form``.
#                       Rejecting it fails every fence-path validation there
#                       is. Two values in history: "agent_contract_handoff"
#                       and the legacy "json:contract".
#   continues_contract_id
#                       the continuation link, written by
#                       ``bin/cli/contract.py::_continuation_seed``. Rejecting
#                       it stops a resumed turn from minting its new contract,
#                       which is the whole resumed-turn mechanism.
#   born_at_dispatch / agent_name
#                       birth markers (``gaia.store.writer``), carried across
#                       into a continuation seed on purpose -- the SubagentStop
#                       last-resort lane matches the dispatched agent's name
#                       inside a still-DISPATCHED row's envelope.
#   agent_state         the BIRTH envelope's own top-level state
#                       ("DISPATCHED"). Deliberately NOT treated as a misplaced
#                       agent_status.agent_state: the system writes it here, and
#                       an agent that puts its state at the root instead of in
#                       agent_status is already caught by MISSING_FIELD on
#                       agent_status.agent_state.
#   degraded / auto_captured / backstop / reaped / salvaged /
#   dispatch_closed_at_subagent_stop / superseded_by_contract_id /
#   agent_output_preview / reconstructed_from_finalized_draft / fallback
#                       the rescue lanes -- what the hook-side capture,
#                       reaper, salvage and reconstruction paths leave on a row
#                       whose turn never wrote an envelope of its own.
#                       ``fallback`` is retired (last written 2026-07-09) and is
#                       kept because history is still revalidated, not because
#                       anything writes it today.
# ---------------------------------------------------------------------------
#   binding_rejection   written into the BIRTH envelope by
#                       ``modules.agents.dispatch_binding`` when a dispatch
#                       names a plan task it may not bind to: the reason and
#                       the rejected token are recorded on the row instead of
#                       vanishing with an unborn one.
#   reconciled          written by ``gaia contract reconcile``
#                       (``bin/cli/contract.py::cmd_reconcile``) when a
#                       hook-written residue row has its cut mark cleared.
#
# The last two are the reason this list is built from a CODE sweep and not
# only from the persisted population. ``reconciled`` appears in ZERO rows --
# it is written by a verb whose output nothing had yet re-read -- so a sweep
# of the database, however careful, is structurally incapable of finding it.
# ``binding_rejection`` is written as a key inside a dict LITERAL, which a
# sweep looking for ``envelope["key"] = ...`` cannot see either. A sweep is
# only as complete as the shapes it knows to look for.
SYSTEM_WRITTEN_ENVELOPE_KEYS: Tuple[str, ...] = (
    "_contract_tag",
    "agent_name",
    "agent_output_preview",
    "agent_state",
    "auto_captured",
    "backstop",
    "binding_rejection",
    "born_at_dispatch",
    "continues_contract_id",
    "degraded",
    "dispatch_closed_at_subagent_stop",
    "fallback",
    "reaped",
    "reconciled",
    "reconstructed_from_finalized_draft",
    "salvaged",
    "superseded_by_contract_id",
)

# Agent-authored top-level keys, per the agent-contract-handoff envelope: the
# two required blocks, the conditional objects, and the documented optional
# fields -- which is exactly the set carrying a declared type above.
#
# Kept SEPARATE from the system keys, and that separation is load-bearing for
# the error messages rather than decorative. An UNKNOWN_FIELD rejection that
# offers the caller a list of "declared keys" must offer the keys the CALLER
# may write: listing `backstop`, `reaped`, `salvaged`, `_contract_tag` or
# `born_at_dispatch` as available options invites an agent to write a key only
# the rescue lanes may write, which is worse than the typo the message was
# answering.
AGENT_WRITABLE_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    tuple(TOP_LEVEL_FIELD_TYPES)
    + _FIELDS_TYPED_BY_A_DEDICATED_CODE
    + ADVISORY_UNTYPED_FIELDS
)

TOP_LEVEL_ENVELOPE_KEYS: Tuple[str, ...] = (
    AGENT_WRITABLE_TOP_LEVEL_KEYS + SYSTEM_WRITTEN_ENVELOPE_KEYS
)

# evidence_report additionally accepts the UPPER-CASE spelling of each required
# key, matching the long-standing backward compatibility in
# ``_evidence_has_key``: 20 rows in history carry the upper form, and the
# presence check has always honoured it.
EVIDENCE_REPORT_KEYS: Tuple[str, ...] = (
    tuple(EVIDENCE_FIELD_TYPES)
    + tuple(key.upper() for key in REQUIRED_EVIDENCE_FIELDS)
)

AGENT_STATUS_KEYS: Tuple[str, ...] = tuple(AGENT_STATUS_FIELD_TYPES)

# Every declared path, keyed by its LAST segment, so a key found at the wrong
# level can be told where it belongs. Only levels the unknown-key door closes
# on appear here -- ``verification`` and the conditional objects stay open (see
# ``_unknown_key_errors``), so no path inside them is claimed.
def _declared_paths() -> dict:
    """Map each declared key to its one canonical dotted path.

    Built root-first so a key the SYSTEM writes at the root (``agent_state``)
    keeps the root as its home and is never reported as a misplaced
    ``agent_status.agent_state``.
    """
    paths = {}
    for key in TOP_LEVEL_ENVELOPE_KEYS:
        paths.setdefault(key, key)
    for key in AGENT_STATUS_KEYS:
        paths.setdefault(key, "agent_status." + key)
    for key in EVIDENCE_FIELD_TYPES:
        paths.setdefault(key, "evidence_report." + key)
    return paths


_DECLARED_PATH_BY_KEY = _declared_paths()


class FormErrorCode(str, Enum):
    """Named, stable error codes emitted by the form layer (AC-1).

    ``str`` mixin: members compare equal to and serialize as their string
    value, so a code round-trips cleanly through JSON and CLI output without a
    custom encoder.
    """

    AGENT_ID_FORMAT = "AGENT_ID_FORMAT"
    PLAN_STATUS = "PLAN_STATUS"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    MISSING_FIELD = "MISSING_FIELD"
    # Additive (R3): a verification.type was declared but the evidence that type
    # requires is missing/empty. DISTINCT from VERIFICATION_RESULT (which is the
    # by-VALUE "COMPLETE but result != pass" check); this is a by-TYPE SHAPE
    # check, independent of agent_state. Fires on every declared type, in or out
    # of the enum; absent or blank verification.type == no check.
    VERIFICATION_SHAPE = "VERIFICATION_SHAPE"
    # Additive (R4): APPROVAL_REQUEST without a usable approval_request block
    # (absent, or present but missing a non-empty exact_content). A pure-shape
    # cross-field check, independent of the evidence_report/verification
    # checks above.
    APPROVAL_REQUEST_SHAPE = "APPROVAL_REQUEST_SHAPE"
    # Additive (R4): COMPLETE without next_action == "done" or with a
    # non-empty pending_steps. A pure-shape cross-field coherence check,
    # independent of VERIFICATION_RESULT.
    COMPLETE_SHAPE = "COMPLETE_SHAPE"
    # Additive (AC-1, plan 38): the OPTIONAL failure_report block is present
    # but malformed. Never fires on an envelope that omits the block, which is
    # what makes the field additive over already-persisted history.
    FAILURE_REPORT_SHAPE = "FAILURE_REPORT_SHAPE"
    # Additive (work-cycle observability): the OPTIONAL top-level work_phase
    # field is present but outside VALID_WORK_PHASES. Never fires on an
    # envelope that omits the field, so every already-persisted contract
    # keeps its verdict.
    WORK_PHASE_SHAPE = "WORK_PHASE_SHAPE"
    # Additive: a DECLARED field is present with the wrong JSON type -- a
    # string where a list is declared, an object where a string is. Presence
    # was all the form layer used to check, which is how the seven evidence
    # lists came to hold strings and objects in the live population. Fires only
    # on a present, non-null value; absence stays MISSING_FIELD's.
    FIELD_TYPE = "FIELD_TYPE"
    # Additive: a declared key written at the WRONG LEVEL -- most often an
    # evidence key at the root (``commands_run`` instead of
    # ``evidence_report.commands_run``), which used to create a silent orphan.
    # Deliberately a rejection naming the correct path, never a silent move:
    # inferring the intent would hide the very class of error this closes.
    MISPLACED_KEY = "MISPLACED_KEY"
    # Additive: a files_checked entry DECLARED itself a commit-qualified file
    # reference (it is an object) but is malformed -- no usable path, no
    # commit, a commit that is not a commit, or a key that is neither. Never
    # fires on a string entry, so every bare path ever written stays valid.
    FILE_REFERENCE_SHAPE = "FILE_REFERENCE_SHAPE"
    # Additive: a key belonging to no declared path at any level. This is what
    # catches a mistyped field name (``files_checkd``), which previously
    # created a brand-new field without a word. The detail names the nearest
    # declared key when there is one.
    UNKNOWN_FIELD = "UNKNOWN_FIELD"


@dataclass(frozen=True)
class FormError:
    """A single shape violation.

    Attributes:
        code: the named FormErrorCode.
        field: dotted path of the offending field (e.g. "agent_status.agent_id",
            "evidence_report.commands_run"). Empty when not field-specific.
        detail: human-readable specifics (the bad value, the expected enum, ...).
    """

    code: FormErrorCode
    field: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover -- convenience only
        loc = f" [{self.field}]" if self.field else ""
        return f"{self.code.value}{loc}: {self.detail}"


@dataclass(frozen=True)
class FormValidationResult:
    """Outcome of form-layer validation.

    Attributes:
        ok: True when the envelope is shape-valid (no errors).
        errors: tuple of FormError, one per distinct invalidity.
        repair_message: the canonical rich repair message for the envelope's
            ``source`` (see ``validate_form``) -- ``CANONICAL_REPAIR_MESSAGE``
            when the envelope came from the agent's own final declaration (the
            default, and the only value before the row-first gate existed),
            ``ROW_ENVELOPE_REPAIR_MESSAGE`` when it came from the turn's
            persisted dispatch row. Byte-stable per source regardless of which
            errors fired, so a caller that injects it (hook gate, CLI) keeps a
            cache-stable surface; the specific defects live in ``errors``.
    """

    ok: bool
    errors: Tuple[FormError, ...] = ()
    repair_message: str = ""

    @property
    def codes(self) -> List[FormErrorCode]:
        """The distinct error codes present, in first-seen order."""
        seen: List[FormErrorCode] = []
        for err in self.errors:
            if err.code not in seen:
                seen.append(err.code)
        return seen

    def error_summary(self) -> str:
        """One-line summary of the specific defects (for stderr / logs).

        Empty string when valid. Callers that want the full guidance combine
        this with ``repair_message``.
        """
        return "; ".join(str(err) for err in self.errors)


# ---------------------------------------------------------------------------
# Canonical rich repair message
#
# Unified from the two prior validators' repair blocks. Always returned (see
# FormValidationResult.repair_message). Kept as a module constant so it is
# byte-stable across calls.
#
# Two variants share one body (``_REPAIR_MESSAGE_BODY``) and differ only in
# their opening sentence, because a validated envelope now has one of two
# distinct sources (the row-first SubagentStop gate,
# ``hooks/adapters/claude_code.py::resolve_subagent_stop_gate``): the agent's
# own final ```agent_contract_handoff``` declaration, or the persisted
# ``agent_contract_handoffs`` row it built incrementally via `gaia contract
# set/add/fill`. Telling an agent to fix "your response" when the row -- not
# the response text -- is what actually failed to parse sends the repair to
# the wrong place; ``validate_form``'s ``source`` argument selects the
# matching variant. The JSON template and build-order guidance below apply
# identically either way, so only the opening clause forks.
# ---------------------------------------------------------------------------
_REPAIR_MESSAGE_BODY = (
    "\n"
    "\n"
    "```agent_contract_handoff\n"
    "{\n"
    '  "agent_status": {\n'
    '    "agent_state": "<IN_PROGRESS|APPROVAL_REQUEST|COMPLETE|BLOCKED|NEEDS_INPUT|NEEDS_VERIFICATION>",\n'
    '    "agent_id": "<the id `gaia contract init` printed for THIS turn>",\n'
    '    "pending_steps": [],\n'
    '    "next_action": "<done or the next concrete step>"\n'
    "  },\n"
    '  "evidence_report": {\n'
    '    "patterns_checked": [],\n'
    '    "files_checked": [],\n'
    '    "commands_run": [],\n'
    '    "key_outputs": [],\n'
    '    "verbatim_outputs": [],\n'
    '    "cross_layer_impacts": [],\n'
    '    "open_gaps": [],\n'
    '    "verification": { "method": "<method>", "result": "pass", "details": "<...>" }\n'
    "  },\n"
    '  "consolidation_report": null,\n'
    '  "approval_request": null\n'
    "}\n"
    "```\n"
    "\n"
    "Required: agent_status (agent_state in the enum above; agent_id matching "
    + AGENT_ID_PATTERN_TEXT + " -- run `gaia contract init` with no --agent-id "
    "and reuse the id it prints, do not invent one; pending_steps; next_action) "
    "and evidence_report with keys "
    "patterns_checked, files_checked, commands_run, key_outputs, "
    "verbatim_outputs, cross_layer_impacts, open_gaps. "
    "When agent_state is COMPLETE, evidence_report.verification.result must be "
    '"pass".\n'
    "\n"
    "Build order for a terminal state (when building the draft incrementally "
    "via `gaia contract set`/`add`/`fill`): fill the fields the terminal state "
    "depends on FIRST -- evidence_report.verification (result == 'pass') and "
    "agent_status.next_action/pending_steps for COMPLETE; approval_request."
    "exact_content for APPROVAL_REQUEST -- and set agent_status.agent_state to "
    "the terminal value LAST, only once those dependencies already hold. A "
    "rejected write leaves the draft at its last-known-good state, but a "
    "terminal row is immutable once `gaia contract finalize` persists it, so "
    "there is no fixing the order after that point -- get it right on this "
    "write, not the next one."
)

CANONICAL_REPAIR_MESSAGE = (
    "Repair: your response must carry an agent_contract_handoff envelope whose "
    "body is valid JSON (parsed with json.loads -- NOT YAML: comments, trailing "
    "commas, or unquoted keys will fail to parse and the block is treated as "
    "missing)." + _REPAIR_MESSAGE_BODY
)

ROW_ENVELOPE_REPAIR_MESSAGE = (
    "Repair: this turn's OWN persisted dispatch row "
    "(agent_contract_handoffs.raw_handoff_json) is what the gate read and is "
    "what needs fixing -- a fenced agent_contract_handoff block in your "
    "response text is not consulted once that row is reachable, however well "
    "formed. Rebuild the draft with `gaia contract set`/`add`/`fill --json` "
    "(valid JSON, parsed with json.loads -- NOT YAML) and close it with `gaia "
    "contract finalize`; that write is what repairs the row." + _REPAIR_MESSAGE_BODY
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_status(raw: Any) -> str:
    """Uppercase and strip trailing punctuation, matching the legacy resolvers."""
    return str(raw or "").strip().upper().rstrip(".,;")


def _evidence_has_key(evidence: dict, key_lower: str) -> bool:
    """Presence check accepting both lower-case (JSON) and UPPER-CASE keys."""
    return key_lower in evidence or key_lower.upper() in evidence


def _is_nonempty_str(value: Any) -> bool:
    """True when ``value`` is a non-empty (after strip) string.

    Used for the per-type required fields that must carry a declared value (a
    command/oracle to run, or a statement of what was reviewed).
    """
    return isinstance(value, str) and value.strip() != ""


def _has_verification_evidence(verification: dict) -> bool:
    """True when the block carries at least one of the three evidence fields.

    The disjunction an out-of-enum type is held to. ``requires_human`` is read
    for truthiness (it is a marker) while the other two must be non-empty
    strings, matching exactly what each known type is held to individually.
    """
    return (
        _is_nonempty_str(verification.get("command"))
        or _is_nonempty_str(verification.get("reviewed"))
        or bool(verification.get("requires_human"))
    )


def _verification_type_shape_error(vtype: str, verification: dict) -> Tuple[Any, str]:
    """Return ``(field, detail)`` for a missing type-required field, else ``(None, "")``.

    ``vtype`` is the CANONICAL form (``_canonical_verification_type``), and the
    requirement applies to EVERY non-empty type, not only the ones the enum
    knows -- see the open-vocabulary rationale where that helper is defined.

      * "command"/"code" (DETERMINISTIC) -- a non-empty ``command`` naming the
        command/oracle a third-party verifier would run.
      * "semantic" -- a truthy ``requires_human`` marker: the contract declares
        it needs human/rubric validation and stays open pending that judgement.
      * "self_review" -- a non-empty ``reviewed`` statement of what was checked
        and observed.
      * "none" (envelope-only, plan 34 task 7) -- no plan-task-bound verification
        was performed; demands NO field (falls through to the ``(None, "")``
        return below).
      * anything else -- an agent's own word for what it did, which stays
        sayable. The enum cannot say WHICH field such a type implies, so it
        demands at least one of the three.

    A ``(None, "")`` return means the type-required field is satisfied. Every
    non-empty detail below is order-aware (per the same rationale as
    VERIFICATION_RESULT/COMPLETE_SHAPE/APPROVAL_REQUEST_SHAPE below): declaring
    ``verification.type`` before the field that type requires is the identical
    build-order trap, just scoped to one sub-field instead of the whole
    envelope.
    """
    if vtype == "none":
        # No external oracle was required (a turn with no plan_task_id). Nothing
        # to enforce -- the explicit absence of a check is itself well-formed.
        return (None, "")
    if vtype in ("command", "code"):
        if not _is_nonempty_str(verification.get("command")):
            return (
                "evidence_report.verification.command",
                (
                    f"verification.type {vtype!r} (deterministic) requires a "
                    "non-empty 'command' naming the command/oracle to run, "
                    "but it is missing. Order matters: set "
                    "evidence_report.verification.command together with (or "
                    "before) verification.type -- declaring the type alone is "
                    "not enough."
                ),
            )
    elif vtype == "semantic":
        if not bool(verification.get("requires_human")):
            return (
                "evidence_report.verification.requires_human",
                (
                    "verification.type 'semantic' requires a truthy "
                    "'requires_human' marker (needs human/rubric validation; "
                    "contract stays open), but it is missing/falsy. Order "
                    "matters: set requires_human together with (or before) "
                    "verification.type."
                ),
            )
    elif vtype == "self_review":
        if not _is_nonempty_str(verification.get("reviewed")):
            return (
                "evidence_report.verification.reviewed",
                (
                    "verification.type 'self_review' requires a non-empty "
                    "'reviewed' statement of what was checked, but it is "
                    "missing. Order matters: set "
                    "evidence_report.verification.reviewed together with (or "
                    "before) verification.type."
                ),
            )
    elif not _has_verification_evidence(verification):
        # An out-of-enum type. The word is kept -- what is refused is declaring
        # a verification and naming nothing that backs it.
        return (
            "evidence_report.verification",
            (
                f"verification.type {vtype!r} is not one of "
                f"{', '.join(ENVELOPE_VERIFICATION_TYPES)}, which is allowed -- "
                "the vocabulary is open and your own word for what you did is "
                "kept. What is not allowed is declaring a type and naming no "
                "evidence for it: an unrecognized type must carry at least one "
                "of 'command' (the command/oracle a third party would re-run), "
                "'reviewed' (what you checked and observed), or "
                "'requires_human' (this needs human/rubric judgement), and "
                "carries none. Order matters: set the evidence field together "
                "with (or before) verification.type. If no verification was "
                "performed at all, the honest declaration is "
                "verification.type 'none', which requires no evidence."
            ),
        )
    return (None, "")


_JSON_TYPE_NAMES = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    type(None): "null",
}


def _json_type_name(value: Any) -> str:
    """The JSON type name for a value, so a rejection speaks the envelope's
    vocabulary rather than Python's (``array``, not ``list``)."""
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


def _type_error(path: str, value: Any, expected: tuple) -> Tuple[str, str]:
    """Return ``(field, detail)`` naming the field, what arrived, what was
    expected -- the three things a caller needs to fix the write in one go."""
    wanted = " or ".join(
        _JSON_TYPE_NAMES.get(kind, kind.__name__) for kind in expected
    )
    return (
        path,
        (
            f"{path} must be {'an' if wanted[0] in 'aeiou' else 'a'} {wanted}, "
            f"got {_json_type_name(value)} ({value!r}). The form layer checks "
            f"the TYPE of a declared field, not only that the key is there: a "
            f"required key holding the wrong type is not a filled field, it is "
            f"an unreadable one -- every consumer downstream iterates it "
            f"expecting {wanted}."
        ),
    )


def _typed_field_errors(container: Any, types: dict, prefix: str) -> List[Tuple[str, str]]:
    """Type-check every declared key PRESENT in ``container``.

    A null is skipped on purpose: an explicit null is the seeded convention for
    every optional block, and a required field's absence or nullity is already
    MISSING_FIELD's to report. Checking it here too would stack two codes on
    one defect.
    """
    problems: List[Tuple[str, str]] = []
    if not isinstance(container, dict):
        return problems
    for key, expected in types.items():
        if key not in container:
            continue
        value = container[key]
        if value is None:
            continue
        # bool is a subclass of int in Python; no declared field wants a
        # number, so the only risk is a boolean satisfying a numeric slot.
        # Guard it explicitly rather than relying on isinstance semantics.
        if isinstance(value, bool) and bool not in expected:
            problems.append(_type_error(prefix + key, value, expected))
            continue
        if not isinstance(value, expected):
            problems.append(_type_error(prefix + key, value, expected))
    return problems


def _unknown_key_errors(
    container: Any,
    allowed: Tuple[str, ...],
    prefix: str,
    suggestable: Optional[Tuple[str, ...]] = None,
) -> List[Tuple[FormErrorCode, str, str]]:
    """Report each key in ``container`` that is not declared at this level.

    ``allowed`` is what passes; ``suggestable`` is what the message may OFFER,
    and at the root the two differ. Every key is accepted there, including the
    fifteen only the rescue lanes and the birth path write, but a message that
    lists those as available options teaches an agent to write `backstop` or
    `reaped` -- a worse outcome than the typo the message was answering.
    Defaults to ``allowed`` for the levels where the distinction is empty.

    Two distinct verdicts, because they are two distinct mistakes and the fix
    differs:

      * the key IS declared, but somewhere else -> MISPLACED_KEY, naming the
        path it belongs at. The value is never moved there: guessing the intent
        is what let a root ``commands_run`` sit unnoticed on 62 rows.
      * the key is declared nowhere -> UNKNOWN_FIELD, naming the nearest
        declared key when one is close enough to be a plausible typo.

    Applied at the three levels whose vocabulary is closed -- the root,
    ``agent_status`` and ``evidence_report``. It is deliberately NOT applied
    inside ``verification`` (47 distinct keys in history: ``checks``,
    ``observed``, ``expected``, ``actual`` -- a genuinely open evidence
    object), nor inside ``consolidation_report`` / ``approval_request``, whose
    fields are extended by the approval protocol. Closing those would reject
    the shapes their own protocols specify.
    """
    problems: List[Tuple[FormErrorCode, str, str]] = []
    if not isinstance(container, dict):
        return problems
    suggestable = allowed if suggestable is None else suggestable
    for key in container:
        if key in allowed:
            continue
        path = prefix + str(key)
        declared_at = _DECLARED_PATH_BY_KEY.get(key)
        if declared_at is not None:
            problems.append((
                FormErrorCode.MISPLACED_KEY,
                path,
                (
                    f"{path} is a declared field written at the wrong level -- "
                    f"it belongs at {declared_at}. It was NOT moved there: "
                    f"inferring the intent would hide exactly the error this "
                    f"reports. Two things follow, and the second is the one "
                    f"that usually matters. (1) Re-run this write addressing "
                    f"{declared_at}. (2) This rejection persisted NOTHING, so "
                    f"the draft still reads as it did before -- and if "
                    f"{path} was already sitting IN the draft, you do not "
                    f"need a way to delete it: there is no delete verb, and "
                    f"none is needed, because the next write strips an "
                    f"inherited key like this one and tells you it did."
                ),
            ))
            continue
        suggestion = _closest_declared_key(str(key), suggestable)
        hint = (
            f" Did you mean {suggestion!r}?" if suggestion
            else f" Fields you can write here: {sorted(suggestable)}."
        )
        problems.append((
            FormErrorCode.UNKNOWN_FIELD,
            path,
            (
                f"{path} is not a field of the contract envelope, so it was "
                f"rejected rather than created.{hint}"
            ),
        ))
    return problems


def _closest_declared_key(key: str, allowed: Tuple[str, ...]) -> str:
    """The nearest declared key to ``key``, or "" when none is close.

    ``difflib`` at a 0.7 cutoff is what turns "unknown key" into an actionable
    message for the case that motivated the check: ``files_checkd`` is one
    deletion from ``files_checked`` and scores well above the cutoff, while an
    unrelated word matches nothing and falls through to the declared-key list.
    """
    matches = difflib.get_close_matches(key, allowed, n=1, cutoff=0.7)
    return matches[0] if matches else ""


_FAILURE_REPORT_ORDER_HINT = (
    "The whole block is validated the moment it appears, so build it in ONE "
    "write -- e.g. `gaia contract fill --json '{\"failure_report\": "
    "{\"attempted\": \"<what you tried>\", \"symptom\": \"<what broke>\", "
    "\"evidence\": [\"<verbatim output>\"]}}'` -- rather than one `set` per "
    "sub-field, where the first write is rejected for the fields not written "
    "yet. Omitting failure_report entirely is always valid: it reports a "
    "defect, it does not certify the turn."
)


def _failure_report_shape_errors(block: Any) -> List[Tuple[str, str]]:
    """Return ``(field, detail)`` pairs for a malformed failure_report block.

    The caller has already established that the block is PRESENT and not null;
    absence is handled there and is never an error. An empty list return means
    the block is well-formed. One pair per offending sub-field, so several
    defects in one block report as several errors under the single
    FAILURE_REPORT_SHAPE code -- the same fan-out the required evidence keys
    use, never several codes for one block.
    """
    if not isinstance(block, dict):
        return [(
            "failure_report",
            (
                f"failure_report must be an object, got "
                f"{type(block).__name__}. Expected keys: "
                f"{list(REQUIRED_FAILURE_REPORT_FIELDS)} (plus the optional "
                f"'component' and 'severity'). " + _FAILURE_REPORT_ORDER_HINT
            ),
        )]

    problems: List[Tuple[str, str]] = []

    for key in ("attempted", "symptom"):
        if not _is_nonempty_str(block.get(key)):
            problems.append((
                f"failure_report.{key}",
                (
                    f"failure_report requires a non-empty '{key}' "
                    + (
                        "naming the operation that was attempted"
                        if key == "attempted"
                        else "stating what broke, as observed"
                    )
                    + f", got {block.get(key)!r}. " + _FAILURE_REPORT_ORDER_HINT
                ),
            ))

    evidence = block.get("evidence")
    if not isinstance(evidence, list):
        problems.append((
            "failure_report.evidence",
            (
                f"failure_report requires 'evidence' to be a list of verbatim "
                f"excerpts (the error text, exit status, or output that shows "
                f"the failure), got {type(evidence).__name__}. "
                + _FAILURE_REPORT_ORDER_HINT
            ),
        ))
    elif not [item for item in evidence if _is_nonempty_str(item)]:
        problems.append((
            "failure_report.evidence",
            (
                "failure_report requires at least one non-empty entry in "
                "'evidence'. A reported defect that cites nothing observed is "
                "an opinion, and the block exists to carry proof, not a "
                "claim. " + _FAILURE_REPORT_ORDER_HINT
            ),
        ))

    severity = block.get("severity")
    if severity is not None:
        if str(severity).strip().lower() not in VALID_FAILURE_SEVERITIES:
            problems.append((
                "failure_report.severity",
                (
                    f"failure_report.severity is optional, but when present it "
                    f"must be one of {list(VALID_FAILURE_SEVERITIES)}, got "
                    f"{severity!r}. Omit it to let the consumer classify."
                ),
            ))

    return problems


_FILE_REFERENCE_FORM_HINT = (
    "A files_checked entry is EITHER a bare path string -- "
    '"gaia/contract/validator.py" -- OR, when the file is committed, an object '
    'naming the commit it was read at: {"path": "gaia/contract/validator.py", '
    '"commit": "a76789a"}, e.g. `gaia contract add evidence_report.files_checked '
    '\'{"path": "<path>", "commit": "<sha>"}\'`. The bare path is always valid '
    "and is the RIGHT answer for a file that is not committed: the commit rides "
    "along when the ARTEFACT has one, it is never a requirement on the turn, so "
    "nothing here is a reason to commit anything."
)


def _file_reference_defects(ref: dict) -> List[Tuple[str, str]]:
    """Return ``(suffix, detail)`` pairs for a malformed reference object.

    The caller has already established that the entry IS an object, which is
    the element's own declaration that it means to be a commit-qualified
    reference; a string entry never reaches here. An empty list means the
    reference is well-formed.

    One pair per offending sub-field, so several defects in one reference
    report as several errors under the single FILE_REFERENCE_SHAPE code --
    the same fan-out ``_failure_report_shape_errors`` uses.
    """
    problems: List[Tuple[str, str]] = []

    if not _is_nonempty_str(ref.get("path")):
        problems.append((
            ".path",
            (
                f"a commit-qualified file reference requires a non-empty "
                f"'path', got {ref.get('path')!r}. " + _FILE_REFERENCE_FORM_HINT
            ),
        ))

    raw_commit = ref.get("commit")
    if not _is_nonempty_str(raw_commit):
        problems.append((
            ".commit",
            (
                f"a commit-qualified file reference requires a non-empty "
                f"'commit', got {raw_commit!r}. Drop the whole object and write "
                f"the bare path string instead if the file is not committed -- "
                f"that is valid, not a lesser answer. " + _FILE_REFERENCE_FORM_HINT
            ),
        ))
    elif not _COMMIT_TOKEN_PATTERN.match(str(raw_commit).strip().lower()):
        problems.append((
            ".commit",
            (
                f"{raw_commit!r} is not a commit: expected a hex object name "
                f"matching {COMMIT_TOKEN_PATTERN_TEXT} (7 to 64 hex digits -- "
                f"git's own abbreviation floor up to a full SHA-256 name). A "
                f"branch, a tag or 'HEAD' is not accepted here because it MOVES, "
                f"and a reference that moves dates nothing -- resolve it first "
                f"(`git rev-parse --short HEAD`) and write the result. Nothing "
                f"is checked against git: this is the SHAPE of the reference, "
                f"and a commit that does not exist passes."
            ),
        ))

    for key in ref:
        if key in FILE_REFERENCE_KEYS:
            continue
        suggestion = _closest_declared_key(str(key), FILE_REFERENCE_KEYS)
        hint = (
            f" Did you mean {suggestion!r}?" if suggestion
            else f" A reference carries exactly {list(FILE_REFERENCE_KEYS)}."
        )
        problems.append((
            f".{key}",
            (
                f"{key!r} is not part of a commit-qualified file reference, so "
                f"the entry was rejected rather than half-read.{hint} Anything "
                f"else you want to say about the file belongs in the path "
                f"string itself or in key_outputs. " + _FILE_REFERENCE_FORM_HINT
            ),
        ))

    return problems


def _file_reference_errors(entries: Any, key: str) -> List[Tuple[str, str]]:
    """Return ``(field, detail)`` pairs for every malformed reference in a
    ``files_checked`` list.

    Only OBJECT elements are inspected. A string element -- every bare path in
    the persisted population -- is never looked at, and neither is any other
    scalar or container, which is what keeps this check additive over history
    rather than a re-verdict on it.
    """
    problems: List[Tuple[str, str]] = []
    if not isinstance(entries, list):
        return problems
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        for suffix, detail in _file_reference_defects(item):
            problems.append((f"evidence_report.{key}[{index}]{suffix}", detail))
    return problems


def _flatten_broken_reference(ref: dict) -> str:
    """Render a malformed reference object as a bare-path STRING.

    The repair half of the reference rule (see ``sanitize_envelope``). A bare
    string is unconditionally valid, so flattening is guaranteed to lift the
    rejection; nothing the agent wrote is discarded, because whatever cannot
    be read as a path rides along as text.
    """
    path = ref.get("path")
    if _is_nonempty_str(path):
        extras = {k: v for k, v in ref.items() if k != "path"}
        if not extras:
            return path.strip()
        return (
            path.strip() + " "
            + json.dumps(extras, sort_keys=True, ensure_ascii=False)
        )
    return json.dumps(ref, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_form(envelope: Any, *, source: str = "declaration") -> FormValidationResult:
    """Validate an ``agent_contract_handoff`` envelope by SHAPE ONLY.

    Args:
        envelope: the already-parsed contract dict. A non-dict (including None,
            e.g. an unparseable / missing block) is reported as a single
            MISSING_FIELD on ``agent_contract_handoff``.
        source: where ``envelope`` was read from, purely for the wording of
            the MISSING_FIELD detail and the choice of repair message --
            never a validation input. ``"declaration"`` (the default, and the
            only value that existed before the row-first SubagentStop gate)
            means the agent's own final fenced text. ``"row"`` means the
            turn's persisted ``agent_contract_handoffs`` row, read because
            that row -- not the fence -- was the gate's authoritative source
            for this turn (see ``resolve_subagent_stop_gate``); a non-dict
            envelope under this source is the row's ``raw_handoff_json``
            having failed to parse as JSON, a distinct and real failure from
            "the agent wrote no fence at all."

    Returns:
        FormValidationResult. ``ok`` is True only when there are no errors.
        ``repair_message`` is ``CANONICAL_REPAIR_MESSAGE`` for
        ``source="declaration"``, ``ROW_ENVELOPE_REPAIR_MESSAGE`` for
        ``source="row"``.
    """
    errors: List[FormError] = []
    from_row = source == "row"
    repair_message = ROW_ENVELOPE_REPAIR_MESSAGE if from_row else CANONICAL_REPAIR_MESSAGE

    if not isinstance(envelope, dict):
        if from_row:
            detail = (
                "this turn's own dispatch row was reachable, but its "
                f"raw_handoff_json could not be read as a JSON object (got "
                f"{type(envelope).__name__}). The row is authoritative once "
                "reachable, so this is not 'no fence in the response' -- it is "
                "the persisted draft itself failing to parse."
            )
        else:
            detail = (
                "no parseable agent_contract_handoff envelope (expected a "
                f"JSON object, got {type(envelope).__name__})"
            )
        errors.append(
            FormError(
                code=FormErrorCode.MISSING_FIELD,
                field="agent_contract_handoff",
                detail=detail,
            )
        )
        return FormValidationResult(
            ok=False, errors=tuple(errors), repair_message=repair_message
        )

    # --- vocabulary: misplaced and unknown keys, at the three closed levels --
    # Run before the per-field checks so a key at the wrong level is reported
    # as what it is (misplaced) rather than as the required field it is not.
    for level, allowed, prefix, suggestable in (
        (envelope, TOP_LEVEL_ENVELOPE_KEYS, "", AGENT_WRITABLE_TOP_LEVEL_KEYS),
        (envelope.get("agent_status"), AGENT_STATUS_KEYS, "agent_status.", None),
        (
            envelope.get("evidence_report"),
            EVIDENCE_REPORT_KEYS,
            "evidence_report.",
            tuple(EVIDENCE_FIELD_TYPES),
        ),
    ):
        for code, field, detail in _unknown_key_errors(
            level, allowed, prefix, suggestable
        ):
            errors.append(FormError(code=code, field=field, detail=detail))

    # --- declared types -----------------------------------------------------
    for container, types, prefix in (
        (envelope, TOP_LEVEL_FIELD_TYPES, ""),
        (envelope.get("agent_status"), AGENT_STATUS_FIELD_TYPES, "agent_status."),
        (
            envelope.get("evidence_report"),
            EVIDENCE_FIELD_TYPES,
            "evidence_report.",
        ),
    ):
        for field, detail in _typed_field_errors(container, types, prefix):
            errors.append(
                FormError(code=FormErrorCode.FIELD_TYPE, field=field, detail=detail)
            )

    # --- agent_status -------------------------------------------------------
    agent_status = envelope.get("agent_status")
    normalized_status = ""
    if not isinstance(agent_status, dict) or not agent_status:
        # A present-but-wrong-type agent_status already reported FIELD_TYPE
        # above; adding MISSING_FIELD on top would stack two codes on one
        # defect. Only a genuinely absent (or empty) block is missing.
        if agent_status is None or isinstance(agent_status, dict):
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status",
                    detail="agent_status object is missing",
                )
            )
    else:
        # agent_state: absent -> MISSING_FIELD; present-but-invalid -> PLAN_STATUS
        raw_status = agent_status.get("agent_state")
        if raw_status is None or str(raw_status).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.agent_state",
                    detail="agent_state is missing",
                )
            )
        elif not isinstance(raw_status, str):
            # FIELD_TYPE already named this defect. Reading an enum out of a
            # non-string would add PLAN_STATUS for the same one value.
            pass
        else:
            normalized_status = _normalize_status(raw_status)
            if normalized_status not in VALID_PLAN_STATUSES:
                errors.append(
                    FormError(
                        code=FormErrorCode.PLAN_STATUS,
                        field="agent_status.agent_state",
                        detail=(
                            f"{raw_status!r} is not one of "
                            f"{list(VALID_PLAN_STATUSES)}"
                        ),
                    )
                )
                # Suppress evidence classification for an unknown status
                # (one code per invalidity).
                normalized_status = ""

        # agent_id: absent -> MISSING_FIELD; present-but-malformed -> AGENT_ID_FORMAT
        raw_agent_id = agent_status.get("agent_id")
        if raw_agent_id is None or str(raw_agent_id).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.agent_id",
                    detail="agent_id is missing",
                )
            )
        elif not _AGENT_ID_PATTERN.match(str(raw_agent_id)):
            errors.append(
                FormError(
                    code=FormErrorCode.AGENT_ID_FORMAT,
                    field="agent_status.agent_id",
                    detail=(
                        f"{raw_agent_id!r} does not match "
                        f"{AGENT_ID_PATTERN_TEXT} -- run `gaia contract init` "
                        f"with no --agent-id and reuse the id it prints"
                    ),
                )
            )

        # pending_steps: presence only (empty list [] is valid).
        if "pending_steps" not in agent_status:
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.pending_steps",
                    detail="pending_steps is missing",
                )
            )

        # next_action: must be present and non-empty.
        raw_next = agent_status.get("next_action")
        if raw_next is None or str(raw_next).strip() == "":
            errors.append(
                FormError(
                    code=FormErrorCode.MISSING_FIELD,
                    field="agent_status.next_action",
                    detail="next_action is missing",
                )
            )

        # --- COMPLETE cross-field coherence (pure SHAPE, R4) -----------------
        # A COMPLETE turn must actually be done: next_action == "done" and
        # pending_steps == [] are pure-shape cross-field checks, independent
        # of evidence/verification. Each only fires when the field in question
        # is itself PRESENT -- the MISSING_FIELD checks above already own the
        # absent case, so this never stacks a second code on the same field
        # (one code per invalidity).
        if normalized_status == "COMPLETE":
            if raw_next is not None and str(raw_next).strip() != "":
                if str(raw_next).strip().lower() != "done":
                    errors.append(
                        FormError(
                            code=FormErrorCode.COMPLETE_SHAPE,
                            field="agent_status.next_action",
                            detail=(
                                "COMPLETE requires next_action == 'done', got "
                                f"{raw_next!r}. Order matters: set "
                                "agent_status.next_action to 'done' together "
                                "with (or before) setting agent_state to "
                                "COMPLETE -- setting agent_state last, once "
                                "next_action already reads 'done', avoids this "
                                "rejection."
                            ),
                        )
                    )
            if isinstance(agent_status.get("pending_steps"), list):
                raw_pending = agent_status.get("pending_steps")
                if raw_pending:
                    errors.append(
                        FormError(
                            code=FormErrorCode.COMPLETE_SHAPE,
                            field="agent_status.pending_steps",
                            detail=(
                                "COMPLETE requires pending_steps == [], got "
                                f"{raw_pending!r}. Order matters: clear "
                                "agent_status.pending_steps to [] (a leftover "
                                "entry from an earlier IN_PROGRESS turn) "
                                "before -- or together with -- setting "
                                "agent_state to COMPLETE; agent_state is the "
                                "last field to set, not the first."
                            ),
                        )
                    )

    # --- evidence_report ----------------------------------------------------
    # Required for every valid status. An unknown/absent status leaves
    # normalized_status == "" and skips this block (already flagged above).
    if normalized_status in _EVIDENCE_REQUIRING_STATUSES:
        evidence = envelope.get("evidence_report")
        if not isinstance(evidence, dict) or not evidence:
            # Same split as agent_status above: a present-but-wrong-type block
            # is FIELD_TYPE's to report, not MISSING_FIELD's.
            if evidence is None or isinstance(evidence, dict):
                errors.append(
                    FormError(
                        code=FormErrorCode.MISSING_FIELD,
                        field="evidence_report",
                        detail="evidence_report object is missing",
                    )
                )
        else:
            for key in REQUIRED_EVIDENCE_FIELDS:
                if not _evidence_has_key(evidence, key):
                    errors.append(
                        FormError(
                            code=FormErrorCode.MISSING_FIELD,
                            field=f"evidence_report.{key}",
                            detail=f"required evidence_report key {key!r} is missing",
                        )
                    )

        # --- verification.type (type-conditional SHAPE, any status) ---------
        # Mirrors the conditional-by-VALUE pattern below (VERIFICATION_RESULT):
        # if verification declares a type, require the evidence that type
        # demands and reject an omission with VERIFICATION_SHAPE. This is a
        # SHAPE check independent of agent_state; it is DISTINCT from the
        # by-VALUE COMPLETE/result==pass check and may co-occur with it (two
        # different invalidities -> two codes).
        #
        # The trigger is the PRESENCE of a declared type, not its membership in
        # any enum. Gating on membership was the escape: a word the enum did not
        # know switched the requirement off, which priced an invented type below
        # a real one. _verification_type_shape_error now answers for every type;
        # membership only decides WHICH evidence is demanded, and "none"
        # (envelope-only, plan 34 task 7) remains the one type demanding none.
        # An ABSENT or blank type still fires no requirement, which is what
        # keeps every contract that never declared one valid.
        verification = evidence.get("verification") if isinstance(evidence, dict) else None
        if isinstance(verification, dict):
            vtype = _canonical_verification_type(verification.get("type"))
            if vtype:
                shape_field, shape_detail = _verification_type_shape_error(vtype, verification)
                if shape_field is not None:
                    errors.append(
                        FormError(
                            code=FormErrorCode.VERIFICATION_SHAPE,
                            field=shape_field,
                            detail=shape_detail,
                        )
                    )

        # --- verification (COMPLETE only) -----------------------------------
        # COMPLETE without verification.result == "pass" -> VERIFICATION_RESULT
        # (covers a missing or malformed verification block too).
        if normalized_status == "COMPLETE":
            verification = evidence.get("verification") if isinstance(evidence, dict) else None
            if not isinstance(verification, dict):
                errors.append(
                    FormError(
                        code=FormErrorCode.VERIFICATION_RESULT,
                        field="evidence_report.verification",
                        detail=(
                            "COMPLETE requires evidence_report.verification to "
                            "be an object with result == 'pass', but it is "
                            "missing. This is a BUILD-ORDER defect, not a "
                            "content defect: fill "
                            "evidence_report.verification (e.g. `gaia contract "
                            "fill --json '{\"evidence_report\": {\"verification\": "
                            "{\"method\": \"<how you checked>\", \"result\": "
                            "\"pass\", \"details\": \"<what you observed>\"}}}'`) "
                            "BEFORE -- or in the same write as -- setting "
                            "agent_status.agent_state to COMPLETE. Set "
                            "agent_state last, once verification.result already "
                            "reads 'pass': a terminal COMPLETE row is immutable "
                            "once finalized, so this order cannot be fixed "
                            "after the fact."
                        ),
                    )
                )
            else:
                result_val = str(verification.get("result", "")).strip().lower()
                if result_val != "pass":
                    errors.append(
                        FormError(
                            code=FormErrorCode.VERIFICATION_RESULT,
                            field="evidence_report.verification.result",
                            detail=(
                                "COMPLETE requires verification.result == "
                                f"'pass', got {verification.get('result')!r}. "
                                "Order matters: only set agent_state to "
                                "COMPLETE once verification has genuinely "
                                "concluded with result == 'pass' -- if the "
                                "check has not run yet, keep agent_state at "
                                "IN_PROGRESS (or NEEDS_VERIFICATION) and set "
                                "verification.result = 'pass' first, "
                                "agent_state last."
                            ),
                        )
                    )

    # --- approval_request (APPROVAL_REQUEST only, pure SHAPE, R4) -----------
    # A pure-shape cross-field check: when agent_state is APPROVAL_REQUEST the
    # top-level approval_request object must itself be present, and its
    # exact_content -- the verbatim content the user must see to give
    # informed consent (the orchestrator-present-approval iron law) -- must
    # be non-empty. approval_id is deliberately NOT required here:
    # agent-response documents a legitimate approval_request with no
    # approval_id yet (an agent presenting a T3 plan before the hook has
    # blocked anything and minted a grant) -- requiring it would reject that
    # documented, in-use protocol state.
    if normalized_status == "APPROVAL_REQUEST":
        approval_request = envelope.get("approval_request")
        if not isinstance(approval_request, dict) or not approval_request:
            errors.append(
                FormError(
                    code=FormErrorCode.APPROVAL_REQUEST_SHAPE,
                    field="approval_request",
                    detail=(
                        "APPROVAL_REQUEST requires a non-null approval_request "
                        "object, but it is missing. Order matters: fill "
                        "approval_request (exact_content at minimum, e.g. "
                        "`gaia contract fill --json '{\"approval_request\": "
                        "{\"exact_content\": \"<verbatim command>\"}}'`) BEFORE "
                        "-- or in the same write as -- setting agent_state to "
                        "APPROVAL_REQUEST; agent_state is the last field to "
                        "set, not the first."
                    ),
                )
            )
        elif not _is_nonempty_str(approval_request.get("exact_content")):
            errors.append(
                FormError(
                    code=FormErrorCode.APPROVAL_REQUEST_SHAPE,
                    field="approval_request.exact_content",
                    detail=(
                        "APPROVAL_REQUEST requires a non-empty 'exact_content' "
                        "(the verbatim command/content the user must see), but "
                        "it is blank/missing. Order matters: set "
                        "approval_request.exact_content before -- or in the "
                        "same write as -- setting agent_state to "
                        "APPROVAL_REQUEST."
                    ),
                )
            )

    # --- failure_report (OPTIONAL advisory axis, pure SHAPE, AC-1) ----------
    # Checked independently of agent_state: a defect is a defect whether the
    # turn ended COMPLETE, BLOCKED or IN_PROGRESS, so this is not gated on the
    # status the way the COMPLETE/APPROVAL_REQUEST checks above are. It is
    # gated only on PRESENCE -- an envelope that omits the key, or sets it to
    # null the way consolidation_report/approval_request are habitually
    # nulled, reaches no check at all. That guard is the whole reason a shape
    # change can be made here without disturbing already-persisted history.
    if envelope.get("failure_report") is not None:
        for field, detail in _failure_report_shape_errors(envelope["failure_report"]):
            errors.append(
                FormError(
                    code=FormErrorCode.FAILURE_REPORT_SHAPE,
                    field=field,
                    detail=detail,
                )
            )

    # --- work_phase (OPTIONAL, pure SHAPE, orthogonal to agent_state) -------
    # Same presence-gating idiom as failure_report above: checked independently
    # of agent_state (a turn's WORK phase is meaningful whatever it reports
    # back), and gated only on presence -- absence or an explicit null (the
    # seeded default in bin/cli/contract.py's _initial_envelope) reaches no
    # check at all, so no already-persisted contract is affected.
    raw_work_phase = envelope.get("work_phase")
    if raw_work_phase is not None:
        normalized_phase = str(raw_work_phase).strip().lower()
        if normalized_phase not in VALID_WORK_PHASES:
            errors.append(
                FormError(
                    code=FormErrorCode.WORK_PHASE_SHAPE,
                    field="work_phase",
                    detail=(
                        f"{raw_work_phase!r} is not one of "
                        f"{list(VALID_WORK_PHASES)}. work_phase is optional "
                        "and orthogonal to agent_state -- omit it entirely "
                        "for a turn with no distinguishable work phase, or "
                        "set it to one of the enum values at each phase "
                        "transition."
                    ),
                )
            )

    # --- files_checked commit references (OPTIONAL form, pure SHAPE) --------
    # Same presence-gating idiom as failure_report and work_phase above, and
    # gated one level deeper: only an ENTRY that declares itself a reference by
    # being an object is checked at all. An envelope whose files_checked holds
    # nothing but strings -- which is every envelope written before this form
    # existed -- reaches no check, so no already-persisted contract changes its
    # verdict. Checked independently of agent_state: a citation is a citation
    # whatever the turn reports back.
    evidence_block = envelope.get("evidence_report")
    if isinstance(evidence_block, dict):
        for evidence_key in ("files_checked", "FILES_CHECKED"):
            for field, detail in _file_reference_errors(
                evidence_block.get(evidence_key), evidence_key
            ):
                errors.append(
                    FormError(
                        code=FormErrorCode.FILE_REFERENCE_SHAPE,
                        field=field,
                        detail=detail,
                    )
                )

    return FormValidationResult(
        ok=not errors,
        errors=tuple(errors),
        repair_message=repair_message,
    )


# ---------------------------------------------------------------------------
# Canonicalization -- persist the value that was VALIDATED, not the raw one.
#
# Every enum in this module is compared NORMALIZED and was persisted RAW, so a
# lower-case agent_state, a work_phase with surrounding spaces and capitals,
# and an upper-case verification result all passed validation and then sat in
# the database in whatever spelling they arrived with, forever. Two spellings
# of one value are two values to every reader downstream -- a GROUP BY, a
# filter, a metric -- and no amount of care at the read end recovers what the
# write end threw away.
#
# The rule this applies is narrow and mechanical: a value is canonicalized
# EXACTLY where the validator compared a normalized form of it, and nowhere
# else. Free prose (``details``, ``next_action`` when it is a real next step)
# is never touched, because nothing normalized it to decide anything.
#
# ``verification.type`` is canonicalized only when it names a KNOWN type. An
# unrecognized type is left verbatim on purpose: it is not validated against
# anything today (a deliberately open escape, out of scope here), and
# rewriting a value no check consulted would be a conversion with no
# validation behind it.
#
# Never in place, and never silent: a copy is returned, and every substitution
# is appended to ``changes`` so the caller can report it. A conversion the
# writer cannot see is the same defect as a rejection it cannot see.
# ---------------------------------------------------------------------------

def canonicalize_envelope(envelope: Any, *, changes: Optional[list] = None) -> Any:
    """Return a copy of ``envelope`` with every validated value canonical.

    Args:
        envelope: a parsed envelope. A non-dict is returned unchanged, so a
            caller can apply this unconditionally after a verdict.
        changes: optional list; each substitution is appended as a
            ``"<path>: <raw!r> -> <canonical!r>"`` line, in the order applied.

    Returns:
        A deep copy carrying the canonical spellings. The input is never
        mutated -- a caller that validated one dict and persists another must
        be able to compare the two.
    """
    if not isinstance(envelope, dict):
        return envelope

    log = changes if changes is not None else []
    result = copy.deepcopy(envelope)

    def _replace(container: dict, key: str, canonical: Any, path: str) -> None:
        raw = container[key]
        if raw == canonical:
            return
        container[key] = canonical
        log.append(f"{path}: {raw!r} -> {canonical!r}")

    agent_status = result.get("agent_status")
    if isinstance(agent_status, dict):
        raw_state = agent_status.get("agent_state")
        if isinstance(raw_state, str):
            normalized = _normalize_status(raw_state)
            if normalized in VALID_PLAN_STATUSES:
                _replace(
                    agent_status, "agent_state", normalized,
                    "agent_status.agent_state",
                )
        raw_next = agent_status.get("next_action")
        # Only the one value an enum comparison is made against: COMPLETE
        # requires next_action to read "done", so " Done " validated and must
        # persist as "done". Any other next_action is prose and stays verbatim.
        if isinstance(raw_next, str) and raw_next.strip().lower() == "done":
            _replace(agent_status, "next_action", "done", "agent_status.next_action")

    raw_phase = result.get("work_phase")
    if isinstance(raw_phase, str):
        normalized = raw_phase.strip().lower()
        if normalized in VALID_WORK_PHASES:
            _replace(result, "work_phase", normalized, "work_phase")

    evidence = result.get("evidence_report")
    if isinstance(evidence, dict):
        verification = evidence.get("verification")
        if isinstance(verification, dict):
            raw_result = verification.get("result")
            if isinstance(raw_result, str):
                _replace(
                    verification, "result", raw_result.strip().lower(),
                    "evidence_report.verification.result",
                )
            raw_type = verification.get("type")
            if isinstance(raw_type, str):
                # Folded whether or not the result is a member of the enum --
                # the SAME helper validate_form decided with, so the stored
                # value is the value that was judged. Convergence is the point:
                # 'self-review' and 'self_review', 'dry-run' and 'dry_run' are
                # one concept each, and leaving both spellings in the column
                # left every reader that groups on it to reconcile them. Only
                # separators fold, so an out-of-enum word survives as itself.
                normalized = _canonical_verification_type(raw_type)
                if normalized:
                    _replace(
                        verification, "type", normalized,
                        "evidence_report.verification.type",
                    )
        # A commit token is matched stripped and lower-cased, so it persists
        # that way -- the same narrow rule the enums above follow. 'A76789A '
        # and 'a76789a' are one commit to git and would otherwise be two
        # distinct strings to every reader that groups or joins on them. Only
        # a token that MATCHED is rewritten; a malformed one is left verbatim
        # for the rejection to quote back.
        for evidence_key in ("files_checked", "FILES_CHECKED"):
            entries = evidence.get(evidence_key)
            if not isinstance(entries, list):
                continue
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                raw_commit = item.get("commit")
                if not isinstance(raw_commit, str):
                    continue
                normalized = raw_commit.strip().lower()
                if _COMMIT_TOKEN_PATTERN.match(normalized):
                    _replace(
                        item, "commit", normalized,
                        f"evidence_report.{evidence_key}[{index}].commit",
                    )

    failure_report = result.get("failure_report")
    if isinstance(failure_report, dict):
        raw_severity = failure_report.get("severity")
        if isinstance(raw_severity, str):
            normalized = raw_severity.strip().lower()
            if normalized in VALID_FAILURE_SEVERITIES:
                _replace(
                    failure_report, "severity", normalized,
                    "failure_report.severity",
                )

    return result


# ---------------------------------------------------------------------------
# Sanitization -- no draft may be born impossible to close.
#
# Closing the envelope's vocabulary created a trap with no handle on the
# inside. Every write validates the WHOLE envelope, and there is no verb that
# removes a key; so a draft carrying one invalid key rejects every `set`,
# `fill` and `finalize` alike, and the agent holding it cannot even write the
# correction. The draft is stuck forever.
#
# That is not a hypothetical inherited from the distant past: 70 of the 238
# draft files already on disk carry a root orphan or an undeclared key, put
# there by the CLI itself back when it accepted them silently. An agent that
# resumes one of those inherits a contract it can never close, for a defect it
# did not commit.
#
# So the vocabulary is enforced on what an agent WRITES and repaired on what
# it INHERITS. This function is the repair half: it takes an envelope read
# back from disk or from a row and returns one that validates, recording every
# change. Nothing is silent -- the caller announces each line, for the same
# reason the rest of this work exists.
#
# Two repair strategies, chosen so the agent's own evidence survives wherever
# it can:
#
#   * an undeclared key is REMOVED. There is nowhere for it to go and no way
#     to guess what it meant; a misplaced one names where it belonged so the
#     caller can write it back deliberately.
#   * a DECLARED key holding the wrong type is repaired in place rather than
#     removed, because removing a REQUIRED key just trades one unclosable
#     draft for another (MISSING_FIELD blocks writes exactly as FIELD_TYPE
#     does). A scalar where a list belongs is wrapped -- ``"ran pytest"``
#     becomes ``["ran pytest"]``, which is lossless. A required string holding
#     a non-string falls back to the same placeholder a fresh draft is seeded
#     with, since no lossless reading exists.
#
# ``agent_id`` is deliberately NOT repaired: it must match AGENT_ID_PATTERN
# and this layer cannot invent a conforming handle. It has zero non-string
# observations across the persisted population, so the case is theoretical;
# were it to occur, the residual MISSING_FIELD is reported honestly rather
# than papered over with a fabricated identity.
# ---------------------------------------------------------------------------

_REQUIRED_STR_PLACEHOLDERS = {
    "agent_state": "IN_PROGRESS",
    "next_action": "pending",
}


def _sanitize_level(
    container: dict, allowed: Tuple[str, ...], types: dict, prefix: str, log: list
) -> None:
    """Drop undeclared keys and repair wrong-typed declared ones, in place."""
    for key in [k for k in container if k not in allowed]:
        path = prefix + str(key)
        declared_at = _DECLARED_PATH_BY_KEY.get(key)
        where = (
            f" (a declared field that belongs at {declared_at})"
            if declared_at else " (not a field of the contract envelope)"
        )
        del container[key]
        log.append(f"removed {path}{where}")

    for key, expected in types.items():
        if key not in container:
            continue
        value = container[key]
        if value is None or isinstance(value, expected):
            if not (isinstance(value, bool) and bool not in expected):
                continue
        path = prefix + key
        if list in expected:
            container[key] = [value]
            log.append(
                f"repaired {path}: {_json_type_name(value)} wrapped into an "
                f"array, so the value itself is kept"
            )
        elif key in _REQUIRED_STR_PLACEHOLDERS:
            replacement = _REQUIRED_STR_PLACEHOLDERS[key]
            container[key] = replacement
            log.append(
                f"repaired {path}: {_json_type_name(value)} replaced with "
                f"{replacement!r}, the value a fresh draft is seeded with -- "
                f"no lossless reading of the original exists"
            )
        else:
            del container[key]
            log.append(
                f"removed {path}: {_json_type_name(value)} where "
                f"{_JSON_TYPE_NAMES.get(expected[0], expected[0].__name__)} "
                f"is declared, and no lossless repair exists"
            )


def sanitize_envelope(envelope: Any, *, removals: Optional[list] = None) -> Any:
    """Return a copy of ``envelope`` that the form layer will accept.

    Args:
        envelope: a parsed envelope, typically read back from a draft file or
            a persisted row. A non-dict is returned unchanged.
        removals: optional list; one human-readable line is appended per
            change, in the order applied. An empty list afterwards means the
            envelope needed nothing.

    Returns:
        A deep copy with undeclared keys removed and wrong-typed declared keys
        repaired. The input is never mutated.

    This repairs the vocabulary and the declared types. It does not
    manufacture a missing required block, so an envelope with no
    ``agent_status`` at all comes back still failing MISSING_FIELD -- which is
    the honest answer, not a defect: that is a draft with no contract in it,
    and inventing one would fabricate a turn's identity.
    """
    if not isinstance(envelope, dict):
        return envelope

    log = removals if removals is not None else []
    result = copy.deepcopy(envelope)

    _sanitize_level(
        result, TOP_LEVEL_ENVELOPE_KEYS, TOP_LEVEL_FIELD_TYPES, "", log
    )
    agent_status = result.get("agent_status")
    if isinstance(agent_status, dict):
        _sanitize_level(
            agent_status, AGENT_STATUS_KEYS, AGENT_STATUS_FIELD_TYPES,
            "agent_status.", log,
        )
    evidence = result.get("evidence_report")
    if isinstance(evidence, dict):
        _sanitize_level(
            evidence, EVIDENCE_REPORT_KEYS, EVIDENCE_FIELD_TYPES,
            "evidence_report.", log,
        )
        _sanitize_file_references(evidence, log)
    return result


def _sanitize_file_references(evidence: dict, log: list) -> None:
    """Flatten every malformed commit reference in files_checked, in place.

    This is the handle on the inside of the door FILE_REFERENCE_SHAPE closes.
    A rejected write persists nothing, so an agent cannot trap ITSELF with a
    malformed reference -- the entry never lands. What it does not cover is an
    envelope INHERITED from elsewhere (a row read back, a resumed draft, a
    hook-captured envelope): a malformed reference already sitting in one
    would reject every subsequent write, including the write that would fix
    it. Repairing on the way in is what keeps the new rejection from being a
    cell rather than a validation.

    Repair is by FLATTENING, not removal, for the reason ``_sanitize_level``
    wraps a scalar instead of deleting it: the entry is evidence the agent
    gathered, and a bare string keeps all of it while being unconditionally
    valid.
    """
    for key in ("files_checked", "FILES_CHECKED"):
        entries = evidence.get(key)
        if not isinstance(entries, list):
            continue
        for index, item in enumerate(entries):
            if not isinstance(item, dict) or not _file_reference_defects(item):
                continue
            replacement = _flatten_broken_reference(item)
            entries[index] = replacement
            log.append(
                f"repaired evidence_report.{key}[{index}]: a malformed commit "
                f"reference was flattened to the bare-path form "
                f"{replacement!r}, which is always valid -- rewrite it as "
                f'{{"path": ..., "commit": ...}} if the file is committed'
            )


__all__ = [
    "FormErrorCode",
    "FormError",
    "FormValidationResult",
    "validate_form",
    "canonicalize_envelope",
    "sanitize_envelope",
    "AGENT_WRITABLE_TOP_LEVEL_KEYS",
    "TOP_LEVEL_ENVELOPE_KEYS",
    "TOP_LEVEL_FIELD_TYPES",
    "AGENT_STATUS_FIELD_TYPES",
    "EVIDENCE_FIELD_TYPES",
    "SYSTEM_WRITTEN_ENVELOPE_KEYS",
    "CANONICAL_REPAIR_MESSAGE",
    "ROW_ENVELOPE_REPAIR_MESSAGE",
    "VALID_PLAN_STATUSES",
    "VALID_VERIFICATION_TYPES",
    "ENVELOPE_VERIFICATION_TYPES",
    "REQUIRED_EVIDENCE_FIELDS",
    "REQUIRED_AGENT_STATUS_FIELDS",
    "REQUIRED_FAILURE_REPORT_FIELDS",
    "VALID_FAILURE_SEVERITIES",
    "VALID_WORK_PHASES",
    "FILE_REFERENCE_KEYS",
    "COMMIT_TOKEN_PATTERN_TEXT",
]
