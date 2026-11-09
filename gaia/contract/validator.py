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
CLI, the hook gate, the fence fallback and the packaging smoke test all import.
The public surface below is a stable interface -- the error codes, the
``validate_form`` signature, the ``FormValidationResult`` shape, and
``CANONICAL_REPAIR_MESSAGE``.

PORTABILITY CONTRACT (enforced by tests/contract/test_validator_portable.py):
    - Imports ONLY the Python standard library plus ``gaia.state`` (itself
      stdlib-pure), which is the SSOT for ``VALID_PLAN_STATUSES``.
    - NEVER imports from ``hooks/`` and NEVER pulls in a third-party package.
    - The ``gaia.state`` import degrades to an inline stdlib fallback when the
      package is not on the path, so the module remains importable in a bare
      stdlib subprocess.

The named codes:
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
      does not fan out into multiple codes.
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
# stdlib subprocess. gaia.state is itself stdlib-pure (only
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
# Envelope-only verification.type enum.
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
# A declared type outside ENVELOPE_VERIFICATION_TYPES gets no discount: the
# companion evidence is required for every declared type, known or not. Two
# rules, applied in order:
#
#   1. CANONICALIZATION (_canonical_verification_type) folds pure orthography:
#      a hyphen and an underscore spell one concept (self-review/self_review),
#      so separators are folded to one token before anything is decided. It
#      rewrites separators, never words, and does NOT map a type outside the
#      enum onto a known one: choosing a known type on the agent's behalf would
#      invent a claim it never made.
#
#   2. THE COMPANION FIELD IS REQUIRED FOR EVERY TYPE
#      (_verification_type_shape_error). For a known type the enum says WHICH
#      field (command / requires_human / reviewed). For a type the enum does not
#      know, at least ONE of the three is required; the envelope recognizes no
#      other kind of evidence.
#
# "none" stays exempt: it is the one spelling that says "no oracle was
# required", so an invented word lands in rule 2 instead.
#
# The remedy for a rejection is an ADDITION -- write the command, the reviewed
# statement, or requires_human -- and `fill --json` and `set` deep-merge into
# the existing verification object, so the corrective write produces a merged
# envelope that validates.
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
# agent_state is the TURN-status field. It carries a
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

# failure_report is optional: its absence is never an error on any agent_state.
# It is one object, not a list. When present it must carry all three required
# sub-fields, so a partial first write (``set failure_report.attempted X``) is
# rejected; build the block in one write (`gaia contract fill --json`).
#     attempted  (str, non-empty)   the operation tried
#     symptom    (str, non-empty)   what broke, as observed
#     evidence   (list, non-empty)  verbatim excerpts showing it
#     component  (str, optional)    the file, module or surface involved
#     severity   (str, optional)    one of VALID_FAILURE_SEVERITIES
REQUIRED_FAILURE_REPORT_FIELDS: Tuple[str, ...] = (
    "attempted",
    "symptom",
    "evidence",
)

VALID_FAILURE_SEVERITIES: Tuple[str, ...] = ("info", "warning", "error")

# A files_checked entry is a bare path string or a {path, commit} object. Only
# an object is checked, and only for shape: nothing here consults git, so a
# well-formed reference to a commit that does not exist passes. A path@sha
# string is not used because "@" and "#" already occur in bare-path prose.
FILE_REFERENCE_KEYS: Tuple[str, ...] = ("path", "commit")

# Commit token: lower-case hex, 7 (git's default abbreviation) to 64 (a SHA-256
# name) characters, matched against the stripped, lower-cased token. A branch,
# tag or HEAD is not a commit here: it moves, so it would date nothing.
COMMIT_TOKEN_PATTERN_TEXT = r"^[0-9a-f]{7,64}$"
_COMMIT_TOKEN_PATTERN = re.compile(COMMIT_TOKEN_PATTERN_TEXT)

# Declared JSON types of the typed fields. A type is enforced only on a value
# that is present and not null: an explicit null is how drafts seed every
# optional block (``gaia.contract.drafts.initial_envelope``), and the absence
# of a required field is MISSING_FIELD's, so one invalidity carries one code.
TOP_LEVEL_FIELD_TYPES = {
    "agent_status": (dict,),
    "evidence_report": (dict,),
    "consolidation_report": (dict,),
    "approval_request": (dict,),
    "report_prose": (str,),
    "conclusion": (str,),
}

LEGACY_CONCLUSION_FIELD = "user_facing_summary"


def envelope_as_read(envelope: Any) -> Any:
    """The envelope a reader sees: a row closed before the rename reads its user_facing_summary as its conclusion."""
    if not isinstance(envelope, dict) or envelope.get("conclusion") is not None:
        return envelope
    if LEGACY_CONCLUSION_FIELD not in envelope:
        return envelope
    read = {key: value for key, value in envelope.items() if key != LEGACY_CONCLUSION_FIELD}
    read["conclusion"] = envelope[LEGACY_CONCLUSION_FIELD]
    return read


def conclusion_of(envelope: Any) -> "str | None":
    """The trimmed conclusion a row carries for the orchestrator, or None when it has none."""
    read = envelope_as_read(envelope)
    text = read.get("conclusion") if isinstance(read, dict) else None
    if not isinstance(text, str):
        return None
    return text.strip() or None

# A key in the table above is not thereby required: a type is enforced only
# where the key is present, and validate_form enumerates the required fields
# by hand.

# failure_report is type-checked by FAILURE_REPORT_SHAPE, not by the table
# above, so a wrong type is not reported under two codes.
_FIELDS_TYPED_BY_A_DEDICATED_CODE: Tuple[str, ...] = (
    "failure_report",
)

# Declared and allowed but deliberately untyped: their parsers degrade to
# nothing on a malformed value and the turn still closes. work_phase,
# rollback_executed, context_consumption and memory_suggestions are not
# declared, so a write carrying one is UNKNOWN_FIELD.
ADVISORY_UNTYPED_FIELDS: Tuple[str, ...] = (
    "memorialize_suggestions",
    "memory_delta",
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

# Keys the system writes into an envelope, never an agent; rejecting one would
# break the writer.
#   _contract_tag         stamped by modules.agents.contract_validator.parse_contract
#                         on every fence-parsed envelope it hands to validate_form
#   continues_contract_id continuation link, bin/cli/contract.py::_continuation_seed
#   born_at_dispatch, agent_name
#                         birth markers (gaia.store.writer)
#   agent_state           the birth envelope's own state; not a misplaced
#                         agent_status.agent_state
#   degraded, auto_captured, backstop, reaped, salvaged, fallback,
#   dispatch_closed_at_subagent_stop, superseded_by_contract_id,
#   agent_output_preview, reconstructed_from_finalized_draft
#                         left by the hook-side capture, reaper, salvage and
#                         reconstruction lanes on a row whose turn wrote no envelope
#   binding_rejection     modules.agents.dispatch_binding records a rejected
#                         plan-task binding in the birth envelope
#   reconciled            set by `gaia contract reconcile` (cmd_reconcile)
#   worktree_scope        "managed" | "shared" | "unknown", stamped by cmd_finalize
#                         (_finalize_worktree_scope); detection only, never gates a close
SYSTEM_WRITTEN_ENVELOPE_KEYS: Tuple[str, ...] = (
    "_contract_tag",
    "agent_name",
    "agent_output_preview",
    "agent_state",
    "auto_captured",
    "backstop",
    "binding_rejection",
    "born_at_dispatch",
    "capture_key_space",
    "capture_source",
    "conclusion_alias",
    "content_checks",
    "continues_contract_id",
    "degraded",
    "dispatch_closed_at_subagent_stop",
    "draft_ambiguity",
    "fallback",
    "harness_record",
    "reaped",
    "reconciled",
    "reconstructed_from_finalized_draft",
    "salvaged",
    "superseded_by_contract_id",
    "update_contracts_refused",
    "worktree_scope",
)

# Agent-authored top-level keys: the typed fields, the one typed by a dedicated
# code, and the advisory untyped ones. Kept apart from the system keys so an
# UNKNOWN_FIELD message never offers an agent a key only the rescue lanes write.
AGENT_WRITABLE_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    tuple(TOP_LEVEL_FIELD_TYPES)
    + _FIELDS_TYPED_BY_A_DEDICATED_CODE
    + ADVISORY_UNTYPED_FIELDS
)

TOP_LEVEL_ENVELOPE_KEYS: Tuple[str, ...] = (
    AGENT_WRITABLE_TOP_LEVEL_KEYS + SYSTEM_WRITTEN_ENVELOPE_KEYS
)

# validate_form accepts these because birth, finalize and the rescue lanes put
# them in envelopes it judges; the agent write verbs refuse to change them.
HARNESS_STAMPED_PATHS: Tuple[str, ...] = SYSTEM_WRITTEN_ENVELOPE_KEYS + ("agent_status.agent_id",)

# memory_delta is the one memory channel an UNKNOWN_FIELD message may offer.
_SUGGESTABLE_TOP_LEVEL_KEYS: Tuple[str, ...] = tuple(
    key for key in AGENT_WRITABLE_TOP_LEVEL_KEYS if key != "memorialize_suggestions"
)
_RETIRED_FIELD_SUCCESSORS = {"memory_suggestions": "memory_delta"}
_ABSENT = object()


def _path_value(envelope: Any, path: str) -> Any:
    node = envelope
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def harness_written_changes(before: Any, after: Any) -> List[str]:
    """The harness-stamped paths whose value differs between two envelopes."""
    return [
        path for path in HARNESS_STAMPED_PATHS
        if _path_value(before, path) != _path_value(after, path)
    ]

# evidence_report also accepts the UPPER-CASE spelling of each required key,
# as ``_evidence_has_key`` does.
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
    """Named, stable error codes emitted by the form layer.

    ``str`` mixin: members compare equal to and serialize as their string
    value, so a code round-trips cleanly through JSON and CLI output without a
    custom encoder.
    """

    AGENT_ID_FORMAT = "AGENT_ID_FORMAT"
    PLAN_STATUS = "PLAN_STATUS"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    MISSING_FIELD = "MISSING_FIELD"
    # A verification.type was declared but the evidence it requires is missing
    # or empty; fires for every declared type, in or out of the enum.
    VERIFICATION_SHAPE = "VERIFICATION_SHAPE"
    # APPROVAL_REQUEST without an approval_request block carrying a non-empty
    # exact_content.
    APPROVAL_REQUEST_SHAPE = "APPROVAL_REQUEST_SHAPE"
    # COMPLETE without next_action == "done", or with a non-empty pending_steps.
    COMPLETE_SHAPE = "COMPLETE_SHAPE"
    # The failure_report block is present but malformed.
    FAILURE_REPORT_SHAPE = "FAILURE_REPORT_SHAPE"
    # A declared field is present with the wrong JSON type; absence stays
    # MISSING_FIELD's.
    FIELD_TYPE = "FIELD_TYPE"
    # A declared key written at the wrong level, e.g. ``commands_run`` at the
    # root. The rejection names the correct path; the value is never moved.
    MISPLACED_KEY = "MISPLACED_KEY"
    # A files_checked entry that is an object but malformed: no usable path, no
    # commit, a commit that is not one, or an unknown key. Never fires on a
    # string entry.
    FILE_REFERENCE_SHAPE = "FILE_REFERENCE_SHAPE"
    # A key declared at no level, e.g. a mistyped ``files_checkd``; the detail
    # names the nearest declared key when there is one.
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
            default), ``ROW_ENVELOPE_REPAIR_MESSAGE`` when it came from the turn's
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


# Repair message returned with every result, a byte-stable module constant.
# Two variants share one body and differ in the opening sentence, chosen by
# validate_form's ``source``: the agent's own declaration, or the persisted
# ``agent_contract_handoffs`` row the SubagentStop gate read
# (``hooks/adapters/subagent_stop_core.py::resolve_subagent_stop_gate``).
#
# The verification line shows the concrete type "command", not a
# ``<a|b|c>`` placeholder: verification.type is an open vocabulary, so a
# literal placeholder would validate and persist as the stored type.
_REPAIR_MESSAGE_BODY = (
    "\n"
    "\n"
    "The envelope's shape (what the ROW must hold -- write it with `gaia "
    "contract set`/`add`/`fill --json`, never as a block in your message):\n"
    "\n"
    "```json\n"
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
    '    "verification": { "type": "command", "command": "<the exact command a '
    'verifier can re-run>", "method": "<prose: how you checked>", "result": '
    '"pass", "details": "<what you observed>" }\n'
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
    "verification.type and verification.method are NOT the same field. 'type' "
    "is the classifier the validator reads, and its vocabulary is OPEN: "
    "command, code, semantic, self_review and none are the names it knows, and "
    "any other word you find accurate is accepted. Declaring a type is a claim "
    "that a check ran, and the claim is priced in a companion field -- "
    "'command'/'code' owe a non-empty 'command' (the oracle a third party can "
    "re-run), 'semantic' owes a truthy 'requires_human', 'self_review' owes a "
    "non-empty 'reviewed', any other word owes at least ONE of those three, "
    "and 'none' (no oracle was required) owes nothing. Spelling folds on "
    "separators only, so 'self-review' and 'self_review' are one type. "
    "'method' is a different thing: free prose naming HOW you checked, stored "
    "verbatim and never read as a classifier -- a verification block carrying "
    "only 'method' declares no type at all and is asked for no evidence. "
    "Write both: the type that can be checked, and the prose that can be "
    "read.\n"
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
    "Repair: this turn's contract envelope must be valid JSON (parsed with "
    "json.loads -- NOT YAML: comments, trailing commas, or unquoted keys will "
    "fail to parse and the envelope is treated as missing)." + _REPAIR_MESSAGE_BODY
)

ROW_ENVELOPE_REPAIR_MESSAGE = (
    "Repair: this turn's OWN persisted dispatch row "
    "(agent_contract_handoffs.raw_handoff_json) is what the gate read and is "
    "the only thing that needs fixing -- your response text is not consulted, "
    "however well formed. Rebuild the draft with `gaia contract set`/`add`/`fill --json` "
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
      * "none" -- no plan-task-bound verification was performed; demands NO field (falls through to the ``(None, "")``
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
        suggestion = _RETIRED_FIELD_SUCCESSORS.get(str(key)) or _closest_declared_key(
            str(key), suggestable
        )
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
            never a validation input. ``"declaration"`` (the default)
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
        (envelope, TOP_LEVEL_ENVELOPE_KEYS, "", _SUGGESTABLE_TOP_LEVEL_KEYS),
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

        # COMPLETE cross-field coherence.
        # A COMPLETE turn needs next_action == "done" and pending_steps == [].
        # Each check fires only when its field is present; an absent field is
        # MISSING_FIELD's, so one invalidity carries one code.
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
        # A declared type must carry the evidence it demands, else
        # VERIFICATION_SHAPE. Independent of agent_state, and distinct from the
        # COMPLETE/result == pass check below, so the two codes may co-occur.
        #
        # The trigger is the presence of a declared type, not its membership in
        # the enum: membership only decides which evidence is demanded, and
        # "none" is the one type demanding none. An absent or blank type
        # fires no requirement.
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
                            "{\"type\": \"command\", \"command\": \"<the exact "
                            "command a verifier can re-run>\", \"method\": "
                            "\"<prose: how you checked>\", \"result\": \"pass\", "
                            "\"details\": \"<what you observed>\"}}}'`) "
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

    # approval_request (APPROVAL_REQUEST only).
    # The top-level approval_request object must be present and its
    # exact_content, the verbatim content the user must see to consent, must
    # be non-empty. approval_id is not required: a plan presented before the
    # hook has blocked anything has no approval_id yet.
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

    # failure_report (optional).
    # Checked whatever the agent_state; gated only on presence, so an absent
    # or null block reaches no check.
    if envelope.get("failure_report") is not None:
        for field, detail in _failure_report_shape_errors(envelope["failure_report"]):
            errors.append(
                FormError(
                    code=FormErrorCode.FAILURE_REPORT_SHAPE,
                    field=field,
                    detail=detail,
                )
            )

    # --- files_checked commit references (optional) -------------------------
    # Gated one level deeper than failure_report: only an entry that is an
    # object is checked, so a files_checked of strings reaches no check.
    # Checked whatever the agent_state.
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


# Canonicalization: persist the value that was validated, not the raw one.
# A value is rewritten only where validate_form compared a normalized form of
# it; free prose is never touched. A copy is returned and every substitution is
# appended to ``changes``.
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
        # COMPLETE requires next_action to read "done", so " Done " persists
        # as "done"; any other next_action is prose and stays verbatim.
        if isinstance(raw_next, str) and raw_next.strip().lower() == "done":
            _replace(agent_status, "next_action", "done", "agent_status.next_action")

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
                # Folded for any type, in the enum or not, with the helper
                # validate_form decided with: 'self-review' and 'self_review'
                # become one value. Only separators fold, so an out-of-enum
                # word survives as itself.
                normalized = _canonical_verification_type(raw_type)
                if normalized:
                    _replace(
                        verification, "type", normalized,
                        "evidence_report.verification.type",
                    )
        # A commit token is matched stripped and lower-cased, so it persists
        # that way. Only a token that matched is rewritten; a malformed one is
        # left verbatim for the rejection to quote back.
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


# Sanitization: the vocabulary is enforced on what an agent writes and
# repaired on what it inherits, because every write validates the whole
# envelope and no verb removes a key, so an inherited invalid key would reject
# every later write. Each change is recorded in ``removals``.
#   * an undeclared key is removed; a misplaced one names where it belonged.
#   * a declared key of the wrong type is repaired in place when that is
#     lossless: a value where a list is declared is wrapped in one, and
#     agent_state / next_action fall back to the draft seed. Any other
#     wrong-typed key is removed.
# agent_id is not repaired: this layer cannot invent a conforming handle, so
# the residual MISSING_FIELD is reported.

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

    A rejected write persists nothing, so an agent cannot trap itself with a
    malformed reference; an inherited envelope (a row read back, a resumed
    draft, a hook-captured envelope) can carry one that rejects every later
    write. Repair flattens instead of removing: a bare string keeps all the
    entry's content and is always valid.
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
    "LEGACY_CONCLUSION_FIELD",
    "envelope_as_read",
    "conclusion_of",
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
    "FILE_REFERENCE_KEYS",
    "COMMIT_TOKEN_PATTERN_TEXT",
]
