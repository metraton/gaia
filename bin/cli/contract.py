"""
gaia contract -- Contract-as-Managed-Data CLI (by-value, validate-on-write).

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli (M2).
Builds an ``agent_contract_handoff`` envelope BY-VALUE across several small
CLI calls instead of the agent re-emitting one large fenced JSON block every
turn. Every mutating verb validates the FULL resulting envelope through the
single combined entry point, ``gaia.contract.crosscheck.validate()`` (layer 1
form + layer 2 cross-check), before persisting anything -- so a rejected
write NEVER lands, NO false-pass.

Subcommands (the 6 draft verbs + the ``fill --json`` batch mode, plus
``reconcile``, which operates on a persisted ROW rather than a draft):
    init     [--agent-id ID]      [--draft-id ID]  Create a new draft; mints
                                                   and prints the agent_id
                                                   when --agent-id is omitted
    set      FIELD VALUE          [--draft-id ID]  Set a scalar field (dotted path)
    add      FIELD VALUE          [--draft-id ID]  Append a value to a list field
    view     [--field DOTTED_PATH][--draft-id ID]  Print the contract envelope (NEVER writes), or ONLY a
             [--harness-id ID] [--json]            dotted-path subtree named by the SAME schema keys the
                                                     envelope itself uses (e.g. evidence_report.open_gaps,
                                                     agent_status.agent_state, update_contracts) -- no
                                                     second taxonomy to learn. A path that EXISTS exits 0
                                                     printing the value verbatim, even an empty [] or null;
                                                     a path that does NOT exit 1 with a distinct stderr
                                                     error -- empty and absent are never the same response.
                                                     --harness-id resolves by the harness's per-run agentId
                                                     instead (cut-turn recovery). Both addressing modes, plus
                                                     --field, are safe for a historical/cut row found via
                                                     `contract list --cut --json` (use its contract_id for
                                                     --draft-id, harness_agent_id for --harness-id when the
                                                     row was stamped, v40+) -- see cmd_view's own docstring
                                                     for the write-on-read defect this used to have and no
                                                     longer does.
    validate                      [--draft-id ID]  Validate the draft WITHOUT mutating it
    finalize                      [--draft-id ID]  Validate and persist/converge the handoff row
    fill     --json JSON          [--draft-id ID]  Batch-merge a JSON patch (validate-on-write)
             --json-file PATH                      ... or read the patch from a file (avoids shell
                                                     quoting a payload that carries report prose)
    chain    --contract-id ID                      Print the whole continuation chain from ANY of
                                                     its links (see "Continuation" below)
    reconcile --contract-id ID | --harness-id ID   Clear the cut mark on a hook-written residue row
              [--superseded-by CONTRACT_ID]        (see the section header above cmd_reconcile for why
                                                     this is a separate door and not a looser finalize).
                                                     Reads no draft, validates no envelope, and NEVER
                                                     touches agent_state.

All subcommands exit 0 on success, 1 on a rejected write / validation
failure or a usage error (never a raw traceback).

Validate-on-write, no false-pass (AC-4):
    init / set / add / fill apply their mutation to an IN-MEMORY copy of the
    draft, call ``gaia.contract.crosscheck.validate()`` on that copy, and
    persist to disk ONLY when the verdict is ok. On rejection, the on-disk
    draft is left untouched at its last-known-good state, the concrete
    errors (including the enum text for an out-of-range agent_state) are
    printed to stderr, and the process exits non-zero -- never a crash.
    ``validate`` never mutates; it only reports the verdict. ``finalize`` does
    not mutate the draft file, but it DOES persist/converge the DB handoff row.
    It accepts any valid state; only ``COMPLETE`` is terminal.

Incremental fill is MIRRORED to the row, not only to disk:
    ``set``/``add``/``fill`` persist the draft to
    ``data_dir()/contract_drafts/`` AND, best-effort, reflect the same partial
    envelope onto this turn's already-born ``agent_contract_handoffs`` row via
    ``gaia.store.writer.mirror_partial_contract_handoff``. Without it a turn cut
    before ``finalize`` left every piece of evidence it had accumulated
    invisible to any DB reader, however much had been written to disk. The
    mirror is deliberately weaker than ``finalize``: it never CREATES a row (a
    draft with no born row mirrors to nothing and the disk write stands alone),
    never touches a row whose turn already declared a close, and never moves the
    row's ``agent_state`` or its born-at-dispatch binding -- only
    ``raw_handoff_json``. Every failure is swallowed: the mirror can never turn
    a successful draft write into a failed CLI call -- but it is never silent
    either. A mirror that did not land is announced on stderr and carried in
    ``--json`` as ``mirror_skipped_reason``, since a write that exits 0 having
    reached no row is exactly the failure worth being loud about. Read the
    mirrored row back with
    ``gaia contract list --contract-id <draft-id> --json``.

Continuation -- a turn is a contract, and closing it does not leave it open:
    An agent that already declared a close and writes again is a NEW turn, in
    ANY of the five states it can finalize under -- not only ``COMPLETE``. Its
    old row stays convergeable because the write-once rule guards a VERDICT and
    only ``COMPLETE`` is one; what that buys, and the verifier rationale that was
    written here and is measurably false, are recorded once in ``gaia.state``
    (section 1b). Note in particular that ``finalize`` below refuses any envelope
    whose ``agent_id`` disagrees with its draft id's prefix, so no OTHER agent can
    converge a row through this CLI at all. There
    is no birth event on a resumption to prepare a new row at, so the FIRST WRITE
    is the moment the fix lives at: ``set``/``add``/``fill`` addressed at a
    CLOSED contract mint a NEW one recording which it continues
    (``agent_contract_handoffs.continues_handoff_id``) and land the write there;
    the closed row is read and never written. The link is born by ONE criterion,
    what a column DOES: it carries the agent's identity, workspace, session and
    harness run, plus the binding columns that RESTRICT the agent and do not
    expire with the turn (``gaia.store.writer._CONTINUATION_CONSTRAINT_COLUMNS``
    -- dropping them made the resumption an escape hatch); it carries NONE of the
    dispatch columns that DESCRIBE the assignment that ended, which a new turn
    cannot fill legitimately and must not inherit falsely.
    ``finalize`` FOLLOWS an existing chain to its live link
    (so a resumed close lands on the contract its own writes went to) but never
    mints one -- its idempotent no-op on a repeated call is a guarantee, and
    minting there would make every retried close an empty link. ``validate`` and
    ``view`` address exactly the id given: a read verb shows the record the
    caller named. Nothing is required of the agent or the orchestrator, and
    nothing is silent: the mint is reported in ``--json`` output, on stderr, as a
    ``contract.continuation`` harness event, and durably on the row itself --
    read the whole chain back from ANY link with ``gaia contract chain``.

Attribution vs. harness-agnosticism (they are not in conflict):
    The purity rule below is about what this CLI READS, not about what it may
    RECORD. It never reads ``CLAUDE_SESSION_ID`` (or any other harness value)
    from the environment -- but ``finalize`` does accept ``--session-id`` and
    ``--plan-task-id`` as EXPLICIT flags, supplied by the caller from its own
    dispatch envelope, and stamps them on the row. The value arrives as an
    argument the caller is answerable for; the core still knows nothing about
    any harness. Without those flags every CLI-finalized contract landed with
    ``session_id`` and ``plan_task_id`` NULL and could not be attributed to the
    session or plan task that produced it -- purity was being paid for with
    unattributable history, which was never the point of the rule.

Implicit adoption:
    ``set``/``add``/``fill`` no longer require a prior ``gaia contract init``
    when the turn's identity was born at dispatch: their first call against an
    EXPLICIT, already-born ``--draft-id`` materializes the on-disk draft from
    the SAME identity the row was born under -- recovering the row's real
    evidence when any was already mirrored, never fabricating a blank over it.
    Guards, recovery, and the fallback role ``init`` keeps are documented on
    ``_maybe_adopt_draft``. ``validate`` and ``view`` deliberately do NOT
    auto-adopt -- neither may mutate anything on disk; ``cmd_view``'s docstring
    carries the write-on-read defect that rule closed.

Draft identity (T5 -- decisions #1, #3, #8):
    This CLI mints its OWN contract id and NEVER reads ``CLAUDE_SESSION_ID``
    or any other Claude-Code-specific environment variable -- decision #1
    ("el CLI y el validador-core no tocan Claude Code"), decision #3 ("el
    CLI acuna su PROPIO id de contrato"). ``init`` mints
    ``{agent_id}.{random-token}`` (see ``gaia.contract.drafts.mint_draft_id``);
    the random token makes concurrent drafts of the same agent collision-free,
    and encoding the agent id makes a draft locatable per agent. Drafts are
    JSON files under ``gaia.paths.data_dir()/contract_drafts/`` -- Gaia's own
    substrate, OUTSIDE the harness's ``.claude/`` tree (AC-5). Addressing:
    an explicit ``--draft-id`` always wins (the concurrency-safe primary key
    each concurrent cycle carries, and the seam the hook adapter (T6) uses to
    re-address a resumed agent's draft); otherwise a subcommand resolves the
    most-recently-modified draft, optionally scoped to a single agent via
    ``--agent-id``. Resolution refuses to guess in two situations, both raising
    ``gaia.contract.drafts.AmbiguousDraftError``, which the CLI catches to
    print a bounded candidate list and exit 1: when BOTH flags are omitted AND
    drafts from 2+ DISTINCT agents exist (the picked draft could belong to
    another agent), and when ``--agent-id`` is given but 2+ LIVE drafts carry
    that handle (agent ids are minted per turn with no uniqueness mechanism, so
    the handle is shared and the recency winner moves between calls -- that is
    how a COMPLETE once landed on another agent's draft). Only ``--draft-id``
    identifies one draft in that second case. A single draft system-wide, or
    several drafts all belonging to the SAME agent, still resolves via the
    latest-mtime fallback unchanged when no ``--agent-id`` is given. All
    addressing/persistence lives in
    ``gaia.contract.drafts`` (atomic writes, no shared mutable pointer), which
    T6 (resume-read), T7 (finalize store-writer), and T13 (concurrency) build
    on. Nothing here depends on a harness session.

Finalize (T7 -- the SOLE idempotent writer of ``agent_contract_handoffs``):
    confirms the draft passes the full verdict, then writes it via
    ``gaia.store.writer.finalize_agent_contract_handoff`` -- an idempotent
    UPSERT (``INSERT ... ON CONFLICT(contract_id) DO NOTHING``) keyed on the
    draft's OWN ``draft_id`` (the "contract id"). A full
    init->set/add->finalize cycle inserts EXACTLY ONE row; every subsequent
    ``finalize`` of the SAME draft is a genuine no-op that reports back the
    SAME ``handoff_id`` (AC-6). T8 (write-guard + fleet-seed permissions) and
    T9 (SubagentStop hook conditional backstop) build on this SAME writer and
    SAME idempotency key -- see the docstring on
    ``gaia.store.writer.finalize_agent_contract_handoff`` for the full
    contract.

Plugin auto-discovery: registered via ``register(subparsers)`` /
``cmd_contract(args)``, following the ``bin/gaia`` plugin pattern (see
``bin/gaia``'s ``_discover_plugins()``). Also runnable standalone:
``python3 bin/cli/contract.py <verb> ...`` (no ``bin/gaia`` dispatch, no DB
bootstrap side effect -- useful for isolated testing).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure the gaia package (repo root) is importable regardless of cwd,
# mirroring the sys.path setup used by every other bin/cli/*.py plugin
# (see bin/cli/task.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# SSOT for the agent-id shape a draft_id's prefix must satisfy to be a
# candidate for implicit adoption (see _maybe_adopt_draft). Imported from the
# portable validator rather than re-spelled, so this floor can never drift
# from the one the envelope itself is checked against.
from gaia.contract.validator import (  # noqa: E402
    AGENT_ID_PATTERN_TEXT,
    canonicalize_envelope,
    sanitize_envelope,
)

_AGENT_ID_RE = re.compile(AGENT_ID_PATTERN_TEXT)


# ---------------------------------------------------------------------------
# Draft storage (T5 -- delegated to gaia.contract.drafts; see module docstring
# "Draft identity"). This CLI holds NO draft-addressing state of its own: the
# per-agent keying, atomic persistence, and concurrency guarantees all live in
# that one harness-agnostic module so T6/T7/T13 build on a single surface.
# ---------------------------------------------------------------------------

def _mint_draft_id(agent_id: str) -> str:
    """Mint a fresh contract id for ``agent_id`` (harness-agnostic)."""
    from gaia.contract.drafts import mint_draft_id

    return mint_draft_id(agent_id)


def _resolve_draft_id(
    explicit: Optional[str], agent_id: Optional[str] = None
) -> Optional[str]:
    """Return the draft id to operate on, or None when nothing is resolvable."""
    from gaia.contract.drafts import resolve_draft_id

    return resolve_draft_id(explicit, agent_id)


def _load_draft(draft_id: str) -> Optional[dict]:
    from gaia.contract.drafts import load_draft

    return load_draft(draft_id)


def _save_draft(draft_id: str, envelope: dict) -> None:
    from gaia.contract.drafts import save_draft

    save_draft(draft_id, envelope)


def _draft_exists(draft_id: str) -> bool:
    from gaia.contract.drafts import draft_exists

    return draft_exists(draft_id)


# ---------------------------------------------------------------------------
# Envelope construction / mutation helpers
# ---------------------------------------------------------------------------

def _initial_envelope(agent_id: str) -> dict:
    """The starting shape for a freshly-init'd draft.

    Delegates to ``gaia.contract.drafts.initial_envelope`` -- the SSOT shared
    with the dispatch-side birth, which pre-creates the on-disk draft under
    the same shape (see that function's docstring for the shape rationale).
    """
    from gaia.contract.drafts import initial_envelope

    return initial_envelope(agent_id)


def _parse_value_arg(raw: str) -> Any:
    """Parse a CLI VALUE argument as JSON when possible, else keep it literal.

    Lets a caller pass ``true`` / ``42`` / ``["a","b"]`` / ``{"k":"v"}`` and
    get real JSON types, while a bare word like ``BOGUS`` or ``done`` still
    round-trips as a plain string.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _split_path(dotted_path: str) -> list:
    return [p for p in dotted_path.split(".") if p]


def _walk_to_parent(envelope: dict, parts: list) -> dict:
    """Walk (creating intermediate dicts as needed) to the parent of the
    final path segment, returning that parent dict."""
    cur = envelope
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    return cur


def _set_nested(envelope: dict, dotted_path: str, value: Any) -> None:
    parts = _split_path(dotted_path)
    if not parts:
        raise ValueError("FIELD must be a non-empty dotted path")
    parent = _walk_to_parent(envelope, parts)
    parent[parts[-1]] = value


def _append_nested(envelope: dict, dotted_path: str, value: Any) -> None:
    parts = _split_path(dotted_path)
    if not parts:
        raise ValueError("FIELD must be a non-empty dotted path")
    parent = _walk_to_parent(envelope, parts)
    key = parts[-1]
    existing = parent.get(key)
    if existing is None:
        existing = []
        parent[key] = existing
    if not isinstance(existing, list):
        raise ValueError(
            f"field {dotted_path!r} is not a list (got {type(existing).__name__})"
        )
    existing.append(value)


def _get_nested(envelope: dict, dotted_path: str) -> Any:
    """Return the value at ``dotted_path`` in ``envelope`` (read-only).

    The read counterpart of ``_set_nested`` -- it shares the SAME
    ``_split_path`` tokenizer that ``set``/``add``/``fill`` use to resolve a
    dotted path, so the addressing scheme is identical (no second, divergent
    parser). Unlike the write helpers, it NEVER creates missing intermediate
    dicts: a segment that is absent, or a non-dict encountered before the last
    segment, raises ``ValueError`` with a clean message naming the exact
    failing segment (never a raw ``KeyError``/traceback), which ``cmd_view``
    turns into a non-zero exit.
    """
    parts = _split_path(dotted_path)
    if not parts:
        raise ValueError("FIELD must be a non-empty dotted path")
    cur: Any = envelope
    walked: list = []
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            where = ".".join(walked) if walked else "(root)"
            raise ValueError(
                f"no such field {dotted_path!r}: no key {part!r} under {where}"
            )
        walked.append(part)
        cur = cur[part]
    return cur


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into ``base``. Dict values merge key-by-key;
    any other value (including a list) replaces the base value outright."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ---------------------------------------------------------------------------
# Validation + output helpers
# ---------------------------------------------------------------------------

def _validate_envelope(envelope: Any):
    """Single full-verdict entry point (layer 1 form + layer 2 cross-check).

    Per the T3 carry-forward: this CLI never re-implements shape checks or
    composes the two layers itself -- it calls the one combined entry point.
    """
    from gaia.contract.crosscheck import validate as _crosscheck_validate

    return _crosscheck_validate(envelope)


def _print_error(msg: str, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps({"status": "error", "error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)


def _print_rejection(result, as_json: bool = False) -> None:
    errors = result.errors
    repair = result.form.repair_message or getattr(result.crosscheck, "repair_message", "")
    if as_json:
        print(json.dumps({
            "status": "rejected",
            "codes": [err.code.value for err in errors],
            "errors": [str(err) for err in errors],
            "repair_message": repair,
        }))
        return
    print("Rejected: write failed validation -- no changes were persisted.", file=sys.stderr)
    for err in errors:
        print(f"  {err}", file=sys.stderr)
    if repair:
        print("", file=sys.stderr)
        print(repair, file=sys.stderr)


def _no_draft_error(as_json: bool, draft_id: Optional[str] = None) -> None:
    if draft_id:
        _print_error(
            f"No draft found for id {draft_id!r}. Run 'contract init' first.",
            as_json,
        )
    else:
        _print_error(
            "No draft found. Run 'gaia contract init' first (it mints and "
            "prints the agent_id and draft_id to reuse).",
            as_json,
        )


def _print_ambiguous_draft_error(exc, as_json: bool) -> None:
    """Report an ``AmbiguousDraftError`` -- resolution found several candidate
    drafts and refuses to guess (see gaia.contract.drafts.resolve_draft_id).

    Two cases share this reporter and are distinguished by the exception's own
    ``code``: ``ambiguous_draft`` (no flags given, 2+ agents have drafts) and
    ``ambiguous_agent_draft`` (``--agent-id`` given, but that handle is carried
    by 2+ live drafts). A machine consumer needs them apart because only the
    first is fixable by adding ``--agent-id``.
    """
    candidates = list(getattr(exc, "candidates", []) or [])
    if as_json:
        print(json.dumps({
            "status": "error",
            "error": getattr(exc, "code", "ambiguous_draft"),
            "message": str(exc),
            "candidates": candidates,
        }))
    else:
        print(f"Error: {exc}", file=sys.stderr)


def _mirror_partial_to_row(draft_id: str, envelope: dict) -> dict:
    """Mirror the partial envelope onto this turn's DB row. Best-effort.

    The disk draft is the primary record and is already written by the time
    this runs; the row is the SECOND place the same partial evidence lands, so
    a turn cut before ``finalize`` still leaves recoverable evidence somewhere
    a query can reach. Every failure mode is swallowed: no row born under this
    draft id, a DB that does not exist yet, an unseeded dispatch identity
    rejected by the write guard -- none of them may turn a successful draft
    write into a failed CLI call.

    The writer (``gaia.store.writer.mirror_partial_contract_handoff``) is what
    guarantees this can never create a row and never touch a row whose turn
    already closed; this seam only decides WHEN to offer the mirror, never what
    it is allowed to do.

    Returns the writer's outcome dict, so the caller can say WHY a mirror did
    not land instead of reporting a bare False. Swallowed is not the same as
    unreported: an exception degrades to ``{"status": "skipped", "reason":
    "error", ...}``, which :func:`_write_if_valid` still surfaces.
    """
    try:
        from gaia.store.writer import mirror_partial_contract_handoff

        outcome = mirror_partial_contract_handoff(draft_id, json.dumps(envelope))
    except Exception as exc:
        return {"status": "skipped", "reason": "error", "detail": str(exc)}
    return outcome if isinstance(outcome, dict) else {
        "status": "skipped", "reason": "unknown",
    }


# Why a mirror did not land, in the caller's terms. ``no_row`` is the one
# ordinary case -- a draft with no dispatch behind it (a plain CLI use, or a turn
# that minted its own identity) has nothing to mirror onto and never did; it is
# reported in --json and stays off stderr so it does not cry wolf on every write
# of a legitimately row-less draft. The others each mean evidence the caller
# believes is recorded is NOT on any row, which no write may leave unsaid.
_MIRROR_SKIP_EXPLANATIONS = {
    "no_row": (
        "no contract row exists for this draft, so the evidence lives only on "
        "disk. Expected when the draft was not born at dispatch."
    ),
    "closed": (
        "the contract row's turn is already closed and never accepts more "
        "evidence. A continuation should have been opened -- this write reached "
        "the row anyway, which is a defect worth reporting."
    ),
    "no_contract_id": "no contract id to key the mirror on.",
    "error": "the mirror raised and was swallowed to keep the draft write valid.",
    "unknown": "the mirror reported an unrecognized outcome.",
}


def _mirror_warning(outcome: dict) -> Optional[str]:
    """The stderr line for a mirror that did not land, or None when it did.

    ``no_row`` is deliberately silent here (see ``_MIRROR_SKIP_EXPLANATIONS``);
    every other non-landing outcome is announced, because a write that exits 0
    while its evidence reached no row is exactly the silent failure this seam
    exists to make loud.
    """
    reason = str(outcome.get("reason") or "unknown")
    if outcome.get("status") == "applied" or reason == "no_row":
        return None
    explanation = _MIRROR_SKIP_EXPLANATIONS.get(reason, reason)
    detail = outcome.get("detail")
    suffix = f" ({detail})" if detail else ""
    return f"[MIRROR SKIPPED: {reason}] {explanation}{suffix}"


def _write_if_valid(
    envelope: dict,
    draft_id: str,
    as_json: bool,
    extra_json: Optional[dict] = None,
    extra_lines: Optional[list] = None,
    mirror: bool = False,
    continuation: Optional[dict] = None,
) -> int:
    """Validate-on-write core: persist ONLY when the full verdict is ok.

    ``extra_json``/``extra_lines`` let a caller enrich the SUCCESS report
    without emitting a second record after this one -- a machine consumer
    reading stdout must still find exactly one JSON object.

    ``mirror`` additionally reflects the freshly-persisted partial envelope
    onto this turn's own row (see ``_mirror_partial_to_row``). It is
    opt-in per subcommand rather than automatic: the incremental verbs
    (``set``/``add``/``fill``) are the ones whose evidence would otherwise be
    lost to a cut, while ``init`` has nothing to preserve yet -- its envelope is
    the empty starting shape, and mirroring it would overwrite the birth
    envelope with no evidence gained. A mirror that did NOT land is announced
    rather than reduced to ``mirrored: false``: the caller believes its evidence
    is recorded, and a write that exits 0 having reached no row is precisely the
    silent failure this command must not produce (see :func:`_mirror_warning`,
    which stays quiet only for the one benign case, a draft with no row behind
    it).

    ``continuation`` carries the PENDING continuation plan from
    :func:`_plan_continuation`, and this is where it is committed -- AFTER the
    verdict, never before, so a rejected write leaves no link, no draft file and
    no event behind, exactly as it leaves no draft mutation behind. Once
    committed it is reported on BOTH output paths -- a structured block for a
    machine reader, a stderr notice for a human/agent one -- because a mechanism
    that requires no ceremony must still not be invisible: the caller whose write
    triggered it is exactly who needs to know its contract id changed.
    """
    result = _validate_envelope(envelope)
    if not result.ok:
        _print_rejection(result, as_json=as_json)
        return 1
    # What gets persisted is what got VALIDATED. Every enum in the envelope is
    # compared normalized (agent_state upper-cased, work_phase stripped and
    # lower-cased, verification.result lower-cased), and persisting the raw
    # spelling instead left two spellings of one value in the database for
    # every reader downstream to reconcile. Canonicalizing here, after the
    # verdict and before the write, is the one point where the validated value
    # and the stored value can be made the same value.
    canonical_changes: list = []
    envelope = canonicalize_envelope(envelope, changes=canonical_changes)
    if continuation is not None:
        continuation = _commit_continuation(continuation, as_json)
        if continuation is None:
            return 1
        draft_id = str(continuation["contract_id"])
    _save_draft(draft_id, envelope)
    mirror_outcome = _mirror_partial_to_row(draft_id, envelope) if mirror else None
    mirrored = None if mirror_outcome is None else (
        mirror_outcome.get("status") == "applied"
    )
    if continuation is not None:
        print(_continuation_notice(continuation), file=sys.stderr)
    # No silent conversion: a value the write changed on its way to disk is
    # announced on the same terms a rejection would be. The caller wrote one
    # spelling and the record now holds another; that is worth one line.
    for change in canonical_changes:
        print(f"[CANONICALIZED] {change}", file=sys.stderr)
    if mirror_outcome is not None:
        warning = _mirror_warning(mirror_outcome)
        if warning:
            print(warning, file=sys.stderr)
    if as_json:
        payload = {"status": "ok", "draft_id": draft_id}
        if mirrored is not None:
            payload["mirrored"] = mirrored
            if not mirrored:
                payload["mirror_skipped_reason"] = mirror_outcome.get("reason")
        if continuation is not None:
            payload["continuation"] = continuation
        if canonical_changes:
            payload["canonicalized"] = canonical_changes
        if _SANITIZE_REPORT:
            payload["sanitized"] = list(_SANITIZE_REPORT)
        payload.update(extra_json or {})
        print(json.dumps(payload))
    else:
        print(f"OK: draft {draft_id} updated and validated.")
        for line in extra_lines or []:
            print(line)
    return 0


def _maybe_adopt_draft(draft_id: str) -> Optional[dict]:
    """Materialize the on-disk draft for an EXPLICIT, already-BORN contract_id.

    A turn whose identity was born at dispatch (``insert_dispatched_handoff``
    in ``gaia.store.writer``) has a real ``agent_contract_handoffs`` row before
    it ever runs a CLI command, but that birth never writes the on-disk draft
    file -- ``init`` used to be the only thing that did. This lets the first
    ``set``/``add``/``fill`` against that SAME id recreate the file itself,
    from exactly the identity the row was born under, instead of requiring a
    separate ``gaia contract init`` call first.

    NOT called by ``cmd_view`` (nor ``cmd_validate``): a read verb may never
    materialize a write as a side effect. This function stays scoped to the
    three MUTATING verbs, whose first call is legitimately about to write
    something real; ``view``'s own read-only recovery lives in
    :func:`_freshest_envelope`.

    Two guards keep this from ever minting anything: the ``draft_id``'s
    agent-id prefix must satisfy ``AGENT_ID_PATTERN_TEXT`` (the same floor
    ``gaia contract init`` mints to), AND a row must already exist for this
    EXACT ``contract_id`` (``agent_contract_handoff_exists``) -- an id that is
    merely well-formed but never born still returns None, so the caller falls
    through to the same "No draft found... run init" error as before. This
    function converges an already-born identity; it never invents one.

    A blank ``_initial_envelope`` is correct ONLY for a genuinely fresh row --
    one that carries nothing but the birth marker
    (``{"agent_state": "DISPATCHED", "born_at_dispatch": True[, agent_name]}``,
    see ``insert_dispatched_handoff``). A row whose draft file was lost
    mid-turn AFTER real evidence was already mirrored onto it (via
    ``gaia.store.writer.mirror_partial_contract_handoff``) carries a full
    validated envelope instead -- and every validated envelope has an
    ``evidence_report`` key (``gaia.contract.validator`` requires it), while the
    bare birth marker never does. That is the same "fabricate blank over real
    evidence" defect ``cmd_view`` used to have, through the write door instead
    of the read one: fabricating blank here would not only lose the evidence on
    disk, it would MIRROR the blanked envelope straight back onto the row on
    the caller's next write, destroying it a second time. So this reuses
    :func:`_freshest_envelope` -- the SAME read-only recovery ``cmd_view``
    already relies on -- to recover the row's real envelope when one exists,
    and falls back to the blank starting shape only when nothing but the birth
    marker is there to recover.

    Returns the (recovered-and-saved, or freshly-initial-and-saved) envelope,
    or None when this is not an adoption case (malformed id, no born row, or a
    DB read failure -- any doubt degrades to None, never to a fabricated
    draft).

    Callers only reach this when :func:`_draft_exists` already read False, so
    a draft file that exists but failed to PARSE (corrupt, not merely absent)
    is never silently overwritten by this path -- that stays a distinct
    "unreadable draft" failure, not an adoption case.
    """
    agent_id = draft_id.split(".", 1)[0]
    if not _AGENT_ID_RE.match(agent_id):
        return None
    try:
        from gaia.store.writer import agent_contract_handoff_exists

        if not agent_contract_handoff_exists(draft_id):
            return None
    except Exception:
        return None

    try:
        row = _lookup_handoff_row_by_contract_id(draft_id)
    except Exception:
        row = None
    if row is not None:
        recovered, _source = _freshest_envelope(draft_id, row)
        if isinstance(recovered, dict) and "evidence_report" in recovered:
            _save_draft(draft_id, recovered)
            return recovered

    envelope = _initial_envelope(agent_id)
    _save_draft(draft_id, envelope)
    return envelope


# ---------------------------------------------------------------------------
# Continuation: a turn is a contract, and closing it does not leave it open.
#
# When the orchestrator resumes an agent that already closed its turn, the agent
# keeps working and its next write has to go somewhere. There is no birth event
# on a resumption to prepare a new row at (the nascent row is written only from
# the dispatching PreToolUse:Task; a resume arrives as SendMessage), so the FIRST
# WRITE is the only moment available, and that is here.
#
# THE TRIGGER IS THE TURN, NOT THE VERDICT. What counts as closed is
# `gaia.state.CLOSED_TURN_PLAN_STATUSES` -- every state an agent can finalize
# under. Reading it off the narrower TERMINAL_PLAN_STATUSES instead was the
# measured defect: a turn that closed declaring NEEDS_VERIFICATION left a row
# that was not terminal, so the same agent's NEXT assignment merged its evidence
# into the record an independent verifier was about to read, and its close
# replaced the producer's verdict -- all of it returning success. The verifier's
# own path is untouched by the widening: it converges through
# finalize_agent_contract_handoff, whose guard is still TERMINAL_PLAN_STATUSES.
# Both frontiers document each other at their definitions in gaia.state.
#
# NO CEREMONY is the binding constraint: the resumed agent passes the same
# --draft-id it was born with and does nothing different, and the orchestrator
# runs no preparatory verb. A fix that depended on someone remembering an extra
# step would be worthless, because the defect being fixed IS a silent omission.
# So the helpers below sit inside ordinary resolution:
#
#   * _continuation_tip_id -- FOLLOW an existing chain to its live link. Pure
#     read; used by finalize so a resumed turn's close lands on the contract its
#     own writes went to, not on the record it already closed.
#   * _plan_continuation   -- PLAN a new link when the addressed contract is
#     closed, without writing anything yet. Used only by the MUTATING verbs,
#     because only they carry content that would otherwise be lost.
#
# THE MINT IS DEFERRED UNTIL THE WRITE VALIDATES, and the split above is what
# buys that. Planning is pure: it mints an id STRING (`secrets`, no DB), builds
# the seed envelope, and returns them. Only when the mutated envelope passes the
# full verdict does _write_if_valid call the writer and INSERT the row. This
# keeps this CLI's oldest guarantee intact -- a rejected write NEVER lands -- for
# the row as well as the draft: a malformed `set` against a closed contract
# leaves no link, no draft file and no event behind, instead of stranding an
# empty row that nothing will ever finalize.
#
# finalize deliberately FOLLOWS and never OPENS. Its idempotency is a documented
# guarantee (a repeated finalize of the same draft is a genuine no-op reporting
# the same handoff_id), and minting on finalize would turn every retried close
# into an empty link. A resumption with nothing to write has nothing to continue;
# a resumption that produces work reaches a mutating verb first, and the link is
# already open by the time finalize follows the chain to it.
#
# NOT SILENT: opening a link is reported on every path a caller can see -- the
# `continuation` block in --json output, a stderr notice in plain output, a
# harness event, and the row's own continues_handoff_id, which `gaia contract
# chain` reads back.
# ---------------------------------------------------------------------------

_CONTINUATION_EVENT = "contract.continuation"


def _continuation_tip_id(draft_id: str) -> str:
    """The live link of ``draft_id``'s chain, or ``draft_id`` when there is none.

    Best-effort by design: a missing DB, an unknown id or a read failure all
    degrade to the id as addressed, which is exactly the pre-continuation
    behavior. Resolution must never turn a readable substrate into a failed CLI
    call.
    """
    try:
        from gaia.store.writer import continuation_tip

        tip = continuation_tip(draft_id)
    except Exception:
        return draft_id
    if not isinstance(tip, dict):
        return draft_id
    return str(tip.get("contract_id") or draft_id)


def _continuation_seed(agent_id: str, parent_contract_id: str, parent_row: dict) -> dict:
    """The envelope a fresh continuation link starts from.

    Deliberately the blank starting shape plus provenance, NOT a copy of the
    record it continues. Each link is ITS turn's contract: copying the parent's
    evidence forward would double-count it on every read of the chain and would
    reproduce, in a new row, the very symptom being fixed -- a record frozen at
    the previous close's content. What the parent holds stays readable where it
    already is, and ``gaia contract chain`` is what puts the links back together.

    The birth markers are carried across because they are dispatch metadata, not
    contract content: the SubagentStop closure's last-resort lane matches the
    dispatched agent's NAME inside a still-DISPATCHED row's envelope, and the
    link is exactly such a row.
    """
    envelope = _initial_envelope(agent_id)
    envelope["continues_contract_id"] = parent_contract_id
    try:
        from gaia.store.writer import BIRTH_AGENT_NAME_KEY

        parent_envelope = json.loads(parent_row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError, ImportError):
        return envelope
    if isinstance(parent_envelope, dict):
        for key in ("born_at_dispatch", BIRTH_AGENT_NAME_KEY):
            if key in parent_envelope:
                envelope[key] = parent_envelope[key]
    return envelope


def _record_continuation_event(outcome: dict) -> None:
    """Make the mint queryable after the fact. Best-effort, never load-bearing.

    The durable record is the row's own ``continues_handoff_id``; this event is
    the second surface, so the mint is findable in session history by an operator
    who was not watching the tool output when it happened.
    """
    try:
        from gaia.store.writer import write_harness_event

        write_harness_event(
            event_type=_CONTINUATION_EVENT,
            source="cli",
            result=(
                f"contract {outcome.get('continues_contract_id')} was already "
                f"closed; work continues in {outcome.get('contract_id')}"
            ),
            severity="info",
            meta={
                "contract_id": outcome.get("contract_id"),
                "continues_contract_id": outcome.get("continues_contract_id"),
                "continues_handoff_id": outcome.get("continues_handoff_id"),
                "handoff_id": outcome.get("handoff_id"),
                "created": outcome.get("created"),
            },
        )
    except Exception:
        pass


def _plan_continuation(
    draft_id: str,
) -> "tuple[str, Optional[dict], Optional[dict]]":
    """Decide a write's real target WITHOUT writing anything.

    CLOSED is the TURN ending, not the verdict freezing: any of the five states
    an agent can finalize under (``gaia.state.CLOSED_TURN_PLAN_STATUSES``). An
    agent that already declared a close and writes again is a new turn whatever
    it declared, and its evidence belongs to a contract of its own -- most
    sharply for the producer that closed ``NEEDS_VERIFICATION``, whose row is
    left convergeable for an INDEPENDENT VERIFIER and not for more of its own
    work. That row keeps converging through ``finalize_agent_contract_handoff``,
    which guards on the narrower ``TERMINAL_PLAN_STATUSES``; only this seam moved.

    Returns ``(effective_draft_id, seed_envelope, pending)``. On the ordinary
    path -- the addressed contract's turn is still running, unknown, or the
    substrate cannot be read -- this is ``(tip_id, None, None)`` and the caller
    loads the draft exactly as before. When the live link is CLOSED it is
    ``(new_contract_id, seed, pending)``: the id is minted from ``secrets``
    (:func:`_mint_draft_id`, a pure string), the seed is the envelope the link
    will be born with, and ``pending`` carries what :func:`_commit_continuation`
    needs to INSERT the row once the write validates.

    Nothing here touches the DB or the disk, which is the point -- see the
    "THE MINT IS DEFERRED" paragraph in the section header.
    """
    tip_id = _continuation_tip_id(draft_id)
    try:
        from gaia.state import CLOSED_TURN_PLAN_STATUSES

        row = _lookup_handoff_row_by_contract_id(tip_id)
    except Exception:
        return tip_id, None, None
    if row is None or row.get("agent_state") not in CLOSED_TURN_PLAN_STATUSES:
        return tip_id, None, None

    agent_id = str(row.get("agent_id") or "")
    if not _AGENT_ID_RE.match(agent_id):
        # A legacy row carries the agent NAME in this column, which is not a
        # draft key -- a link minted under it would be unaddressable by
        # --draft-id. Leave such a row alone rather than mint an orphan.
        return tip_id, None, None

    seed = _continuation_seed(agent_id, tip_id, row)
    new_contract_id = _mint_draft_id(agent_id)
    return new_contract_id, seed, {
        "parent_contract_id": tip_id,
        "new_contract_id": new_contract_id,
        "seed": seed,
    }


def _commit_continuation(pending: dict, as_json: bool) -> Optional[dict]:
    """INSERT the planned link now that the write has validated.

    Returns the writer's outcome dict, or None when the link could not be opened
    -- which the caller must treat as a failed write, NOT as a silent fallback.
    Falling back to writing at the parent would either edit a closed record or
    clobber a concurrent resumption's link with this call's blank-seeded
    envelope; both are worse than a clean refusal the caller can simply repeat,
    since the retry resolves the now-existing link through the ordinary path.
    """
    try:
        from gaia.store.writer import open_contract_continuation

        outcome = open_contract_continuation(
            pending["parent_contract_id"],
            pending["new_contract_id"],
            raw_handoff_json=json.dumps(pending["seed"]),
        )
    except Exception as exc:
        _print_error(f"could not open a continuation contract: {exc}", as_json)
        return None

    if outcome.get("status") != "opened":
        _print_error(
            f"contract {pending['parent_contract_id']!r} is closed, so this "
            f"write needs a continuation, but one could not be opened "
            f"(reason={outcome.get('reason')}). Nothing was written. Re-run the "
            f"same command.",
            as_json,
        )
        return None
    if outcome.get("contract_id") != pending["new_contract_id"]:
        _print_error(
            f"a continuation of {pending['parent_contract_id']!r} was opened "
            f"concurrently as {outcome.get('contract_id')!r}. Nothing was "
            f"written; re-run the same command and it will land there.",
            as_json,
        )
        return None
    _record_continuation_event(outcome)
    return outcome


def _continuation_notice(continuation: dict) -> str:
    verb = "opened" if continuation.get("created") else "already open"
    return (
        f"[CONTINUATION {verb}] contract "
        f"{continuation.get('continues_contract_id')} is already closed and is "
        f"never modified. This turn's work continues in a NEW contract, "
        f"{continuation.get('contract_id')}, which records where it came from. "
        f"Nothing changes for you -- keep using the draft id you were given. "
        f"Read the whole chain with 'gaia contract chain --contract-id "
        f"{continuation.get('contract_id')}'."
    )


def _resolve_target_draft_id(args, as_json: bool) -> Optional[str]:
    """Resolve --draft-id (or --agent-id scope) to one concrete draft id.

    The pure-resolution prefix of :func:`_load_target_draft`, split out so a
    caller that must decide FOR ITSELF what to do when no draft FILE exists
    (``cmd_view``'s read-only recovery, see :func:`_lookup_handoff_row_by_contract_id`)
    can resolve the id without also triggering adoption or the "no draft
    found" error for a case it is about to handle differently. Prints and
    returns None on the two errors that are unconditionally terminal
    regardless of what the caller does next: an ambiguous ``--draft-id``/
    ``--agent-id`` combination, or nothing resolvable at all.
    """
    from gaia.contract.drafts import AmbiguousDraftError

    try:
        draft_id = _resolve_draft_id(
            getattr(args, "draft_id", None),
            getattr(args, "agent_id", None),
        )
    except AmbiguousDraftError as exc:
        _print_ambiguous_draft_error(exc, as_json)
        return None
    if draft_id is None:
        _no_draft_error(as_json)
        return None
    return draft_id


def _load_target_draft(
    args,
    force_json: bool = False,
    allow_adopt: bool = True,
    chain: str = "none",
    sanitize: bool = False,
) -> "tuple[Optional[str], Optional[dict], bool, Optional[dict]]":
    """Resolve --draft-id and load it.

    Returns ``(draft_id, envelope, as_json, continuation)``.

    envelope is None (and an error already printed) when nothing is
    resolvable, resolution is ambiguous across agents, or the file is
    missing/corrupt -- callers should return 1.

    ``force_json`` lets a caller whose own ``--json`` flag means something
    else (``fill``'s ``--json`` is the PATCH payload, not an output-format
    toggle) still get JSON-shaped error reporting for THIS helper's own
    errors (no draft / ambiguous draft), matching that caller's documented
    "always speaks JSON" contract instead of silently falling back to
    plain text because ``args`` has no ``json`` attribute under that name.

    ``allow_adopt`` (default True) tries :func:`_maybe_adopt_draft` when NO
    draft file exists yet (checked via :func:`_draft_exists`, not merely a
    failed load, so a file that exists but is corrupt is never mistaken for
    "absent, adopt me") for an EXPLICIT id -- this is what makes a first
    ``set``/``add``/``fill`` against a born identity succeed with no prior
    ``init``. Both ``cmd_validate`` and ``cmd_view`` pass False: neither may
    mutate anything on disk. ``cmd_view`` does not even call this helper for
    its ``--draft-id`` addressing path any more -- see its own docstring for
    why materializing ``_initial_envelope`` was never safe for a READ verb,
    and how it recovers real evidence instead without writing.

    ``chain`` selects how the resolved id relates to its continuation chain
    (see the "Continuation" section above):

      * ``"none"``   -- address exactly the id given. ``validate`` uses this,
        and ``view`` resolves without this helper at all: a read verb must show
        the record the caller named, not a successor it never asked about.
      * ``"follow"`` -- resolve to the chain's live link, minting nothing.
        ``finalize`` uses this so a resumed turn's close lands on the contract
        its own writes went to.
      * ``"open"``   -- follow, and PLAN a link when the live one is already
        closed. The three mutating verbs use this; it is the whole mechanism.
        The returned fourth element is then the PENDING plan, which
        :func:`_write_if_valid` commits only after the write validates -- so a
        rejected write leaves no link behind, and the draft returned alongside
        it is the seed envelope the link will be born with, held in memory.
    """
    as_json = force_json or bool(getattr(args, "json", False))
    # One CLI process runs one verb, but the test suite calls these handlers
    # in-process and repeatedly; a stale report would be attributed to the
    # next write.
    _SANITIZE_REPORT.clear()
    draft_id = _resolve_target_draft_id(args, as_json)
    if draft_id is None:
        return None, None, as_json, None
    continuation = None
    if chain == "open":
        draft_id, seed, continuation = _plan_continuation(draft_id)
        if continuation is not None:
            # The link does not exist yet -- neither as a row nor as a file --
            # so there is nothing to adopt or load. The seed IS the draft.
            return draft_id, seed, as_json, continuation
    elif chain == "follow":
        draft_id = _continuation_tip_id(draft_id)
    if allow_adopt and not _draft_exists(draft_id):
        adopted = _maybe_adopt_draft(draft_id)
        if adopted is not None:
            return draft_id, _sanitize_inherited(adopted, sanitize), as_json, continuation
    envelope = _load_draft(draft_id)
    if envelope is None:
        _no_draft_error(as_json, draft_id)
        return draft_id, None, as_json, continuation
    return draft_id, _sanitize_inherited(envelope, sanitize), as_json, continuation


def _sanitize_inherited(envelope: dict, sanitize: bool) -> dict:
    """Repair an INHERITED envelope so the caller can actually write to it.

    A write validates the whole envelope and no verb removes a key, so one
    invalid key inherited from a historical row -- or from a draft file the
    CLI itself wrote before the vocabulary was closed -- rejects every `set`,
    `fill` and `finalize`, including the write that would fix it. 70 of the
    238 draft files on disk are in exactly that state. Repairing on the way IN
    is what keeps the door from having no handle on the inside.

    Announced, never silent, and on both output paths -- the caller is about
    to write a record that differs from the one it read, which is precisely
    what must not happen quietly. The read verbs pass ``sanitize=False``:
    ``validate`` and ``view`` must report the draft as it actually is.
    """
    if not sanitize:
        return envelope
    removals: list = []
    cleaned = sanitize_envelope(envelope, removals=removals)
    for line in removals:
        print(f"[SANITIZED] {line}", file=sys.stderr)
    if removals:
        _SANITIZE_REPORT.extend(removals)
    return cleaned


# Populated by :func:`_sanitize_inherited` and drained by
# :func:`_write_if_valid` into the --json payload. A module-level accumulator
# rather than a threaded return value because the sanitize happens during
# draft RESOLUTION, several frames above the write that reports it, and every
# mutating verb would otherwise have to carry it through by hand.
_SANITIZE_REPORT: list = []


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _mint_agent_id() -> str:
    """Mint a fresh, conforming agent handle.

    ``mint_draft_id`` has always minted the ``.{token}`` SUFFIX with
    ``secrets`` and that suffix has never collided across 6540 rows; the
    PREFIX -- the part resolution actually keys on -- was the one piece asked
    of the model, which is where every measured collision came from. Minting
    both here removes that asymmetry.

    Delegates to ``gaia.contract.drafts.mint_agent_id``, the SSOT the
    dispatch-side birth mints from too -- a row born at dispatch is adoptable
    by this CLI only while both sites agree on the handle's shape.
    """
    from gaia.contract.drafts import mint_agent_id

    return mint_agent_id()


def cmd_init(args) -> int:
    """Create a new draft envelope (validate-on-write).

    ``--agent-id`` is OPTIONAL: when omitted the substrate mints the handle
    and echoes it, so the agent reuses a value it never had to invent. An
    explicit ``--agent-id`` still works unchanged (subject to the same
    format floor), which is what keeps every existing caller valid.

    No longer the required first step for a turn whose identity was born at
    dispatch: ``set``/``add``/``fill`` now adopt that identity implicitly on
    their own first call (see ``_maybe_adopt_draft``). ``view`` recovers a
    born identity too, but read-only -- it never adopts/materializes a draft
    file (see ``cmd_view``'s docstring). This command remains the FALLBACK
    for the one case that still needs it -- a turn that received no injected
    ``# Your Contract`` block at all (a resumed session, or a dispatch
    outside this harness) -- where a bare ``gaia contract init`` (no
    ``--agent-id``) mints a fresh identity to reuse.
    """
    as_json = bool(getattr(args, "json", False))
    agent_id = getattr(args, "agent_id", None)
    minted = agent_id is None
    if minted:
        agent_id = _mint_agent_id()
    draft_id = getattr(args, "draft_id", None) or _mint_draft_id(agent_id)
    envelope = _initial_envelope(agent_id)
    # Echo the identity unambiguously. The draft_id embeds the agent_id, but
    # asking the agent to slice a prefix off a compound id is another chance
    # to retype it wrong -- report both, labelled, in both output modes.
    return _write_if_valid(
        envelope,
        draft_id,
        as_json,
        extra_json={"agent_id": agent_id, "agent_id_minted": minted},
        extra_lines=[
            f"agent_id: {agent_id}",
            f"draft_id: {draft_id}",
            "Reuse BOTH verbatim for the rest of this turn: agent_id in "
            "agent_status.agent_id, draft_id as --draft-id.",
        ],
    )


def cmd_set(args) -> int:
    """Set a scalar field by dotted path (validate-on-write)."""
    draft_id, envelope, as_json, continuation = _load_target_draft(
        args, chain="open", sanitize=True
    )
    if envelope is None:
        return 1
    value = _parse_value_arg(args.value)
    try:
        _set_nested(envelope, args.field, value)
    except ValueError as exc:
        _print_error(str(exc), as_json)
        return 1
    return _write_if_valid(
        envelope, draft_id, as_json, mirror=True, continuation=continuation
    )


def cmd_add(args) -> int:
    """Append a value to a list field (validate-on-write)."""
    draft_id, envelope, as_json, continuation = _load_target_draft(
        args, chain="open", sanitize=True
    )
    if envelope is None:
        return 1
    value = _parse_value_arg(args.value)
    try:
        _append_nested(envelope, args.field, value)
    except ValueError as exc:
        _print_error(str(exc), as_json)
        return 1
    return _write_if_valid(
        envelope, draft_id, as_json, mirror=True, continuation=continuation
    )


def _lookup_handoff_row_by_contract_id(contract_id: str) -> Optional[dict]:
    """The persisted ``agent_contract_handoffs`` row for this contract id, or
    None when no row was ever born under it. Plain SELECT, T0 -- no adoption,
    no write, shared by every recovery lane that needs the row itself rather
    than only its envelope.
    """
    from gaia.store.writer import list_agent_contract_handoffs

    rows = list_agent_contract_handoffs(contract_id=contract_id, limit=1)
    return rows[0] if rows else None


def _freshest_envelope(contract_id: Optional[str], row: dict) -> "tuple[Optional[Any], str]":
    """The freshest recoverable envelope for an ALREADY-RESOLVED row, without
    ever writing anything itself.

    Reads the SAME two sources in the SAME priority (on-disk draft first, it
    may carry writes newer than the last DB mirror; else the row's own
    ``raw_handoff_json``, populated by ``gaia contract set/add/fill``'s
    best-effort mirror -- see ``gaia.store.writer.mirror_partial_contract_handoff``)
    and returns whichever is real, or ``None`` when neither is. This function
    never touches disk or the DB either way -- it is a pure read used by two
    callers with different obligations once they have the result: ``cmd_view``
    only ever prints what this returns (a read verb may never materialize a
    write as a side effect); :func:`_maybe_adopt_draft` (a MUTATING verb's
    adoption path) uses it to recover real evidence when the row already
    carries any, and persists it, rather than fabricating a blank
    ``_initial_envelope`` over evidence that was already there.

    Returns ``(envelope, source)`` where ``source`` is ``"draft"`` or
    ``"db_row"`` -- labelled even when ``envelope`` comes back None, so a
    caller can still report WHICH source it looked at and found empty.
    """
    envelope = _load_draft(contract_id) if contract_id else None
    if envelope is not None:
        return envelope, "draft"
    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        envelope = None
    return envelope, "db_row"


def _view_by_harness_id(args, harness_id: str) -> int:
    """Resolve and print a turn's contract by the HARNESS's per-run agent id.

    The recovery lane for a cut turn: the parent's Task result reports an
    ``agentId`` in the harness's own identifier space, which resolves no draft
    (drafts key on the CLI-minted space). Since v40 SubagentStart stamps that
    id onto the born row (``agent_contract_handoffs.harness_agent_id``), so
    the row -- and through its ``contract_id``, the on-disk draft when one
    still exists -- is reachable directly, with no date search or content
    grep. The freshest source wins -- see :func:`_freshest_envelope`, which
    this and ``cmd_view``'s own ``--draft-id`` recovery lane both call, so the
    two addressing modes recover identically once a row is in hand.
    """
    from gaia.store.writer import list_agent_contract_handoffs

    as_json = bool(getattr(args, "json", False))
    rows = list_agent_contract_handoffs(harness_agent_id=harness_id, limit=1)
    if not rows:
        _print_error(
            f"no contract row carries harness_agent_id={harness_id!r}. Rows "
            f"are stamped at SubagentStart (v40); a turn dispatched before "
            f"that version, or one whose start never reached the stamping "
            f"seam, is only reachable by session/date via 'gaia contract "
            f"list'.",
            as_json=as_json,
        )
        return 1
    row = rows[0]
    contract_id = row.get("contract_id")
    envelope, source = _freshest_envelope(contract_id, row)

    field = getattr(args, "field", None)
    if field is not None:
        if not isinstance(envelope, dict):
            _print_error(
                f"row {row.get('id')} has no readable envelope to take "
                f"--field from.",
                as_json=as_json,
            )
            return 1
        try:
            subtree = _get_nested(envelope, field)
        except ValueError as exc:
            _print_error(str(exc), as_json=as_json)
            return 1
        print(json.dumps(subtree, indent=2))
        return 0

    print(json.dumps({
        "harness_agent_id": harness_id,
        "contract_id": contract_id,
        "handoff_id": row.get("id"),
        "agent_id": row.get("agent_id"),
        "agent_state": row.get("agent_state"),
        "cut_reason": row.get("cut_reason"),
        "session_id": row.get("session_id"),
        "created_at": row.get("created_at"),
        "envelope_source": source,
        "envelope": envelope,
    }, indent=2))
    return 0


def cmd_view(args) -> int:
    """Print a turn's contract envelope, or ONLY a dotted-path subtree of it
    (``--field``). NEVER writes -- not the draft file, not the DB row.

    ``--harness-id`` switches the lookup to the harness's per-run agent id
    (see ``_view_by_harness_id``) -- the id the parent holds for a turn that
    was cut before it could finalize. Both addressing modes are equally safe
    to point at a historical or cut row: neither ever adopts/materializes a
    draft file. ``gaia contract list --cut --json`` reports both coordinates
    on every row it returns -- ``contract_id`` (for ``--draft-id``) and
    ``harness_agent_id`` when the row was stamped at SubagentStart (v40) --
    so either is a safe next step from that list.

    FIXED (was a live defect): with no ``--field``, ``--draft-id`` addressing
    used to resolve through :func:`_load_target_draft`'s DEFAULT
    ``allow_adopt=True``. When no draft FILE existed yet for an EXPLICIT,
    already-born id -- exactly the shape of a historical or cut row reached
    via ``contract list`` -- that adoption path (:func:`_maybe_adopt_draft`)
    materialized a genuinely BLANK ``_initial_envelope`` (every
    ``evidence_report`` list empty) and PERSISTED it to disk, from a READ
    verb, discarding whatever real evidence the row's own
    ``raw_handoff_json`` still carried (populated by that turn's own
    ``set``/``add``/``fill`` calls before it was cut -- see
    ``gaia.store.writer.mirror_partial_contract_handoff``). The blank
    envelope then read back as "this turn recorded nothing", indistinguishable
    from an honestly empty turn, and the damage was PERMANENT: once the blank
    draft file existed, every later ``view`` of the SAME id loaded it instead
    of ever looking at ``raw_handoff_json`` again.

    This resolves ``--draft-id`` with ``allow_adopt=False`` (mirroring
    ``cmd_validate``, which already never mutates) and, only when no draft
    file exists, recovers via :func:`_freshest_envelope` -- the SAME
    freshest-source-wins recovery ``--harness-id`` addressing already used --
    instead of fabricating one. When NOTHING is recoverable at all (no draft
    file AND no row, or a row whose ``raw_handoff_json`` is itself
    missing/unparseable), that is reported as an explicit error naming the
    reason, never as a silent blank envelope: a turn that genuinely recorded
    nothing and a turn whose evidence could not be read back must never look
    the same.
    """
    harness_id = getattr(args, "harness_id", None)
    if harness_id:
        return _view_by_harness_id(args, harness_id)

    as_json = bool(getattr(args, "json", False))
    draft_id = _resolve_target_draft_id(args, as_json)
    if draft_id is None:
        return 1

    source = "draft"
    row = None
    draft_file_present = _draft_exists(draft_id)
    envelope = _load_draft(draft_id) if draft_file_present else None
    if envelope is None:
        if draft_file_present:
            # The draft file exists but failed to PARSE -- distinct from "no
            # file at all", and not the case this fix targets. Keep the
            # pre-existing behavior (a clean error, never a fabricated
            # envelope and never a fallback to raw_handoff_json for a file
            # that is sitting right there, just unreadable).
            _no_draft_error(as_json, draft_id)
            return 1
        row = _lookup_handoff_row_by_contract_id(draft_id)
        if row is None:
            _no_draft_error(as_json, draft_id)
            return 1
        envelope, source = _freshest_envelope(draft_id, row)
        if envelope is None:
            _print_error(
                f"contract row {row.get('id')} exists for draft_id "
                f"{draft_id!r} but nothing is recoverable: no on-disk draft "
                f"file remains, and the row's own raw_handoff_json is "
                f"missing or unparseable. This is NOT the same as a turn "
                f"that recorded no evidence -- it means the evidence itself "
                f"could not be read back.",
                as_json,
            )
            return 1

    field = getattr(args, "field", None)
    if field is not None:
        if not isinstance(envelope, dict):
            _print_error(
                f"draft_id {draft_id!r} has no readable envelope to take "
                f"--field from.",
                as_json,
            )
            return 1
        try:
            subtree = _get_nested(envelope, field)
        except ValueError as exc:
            _print_error(str(exc), as_json)
            return 1
        print(json.dumps(subtree, indent=2))
        return 0

    payload = {"draft_id": draft_id, "envelope": envelope}
    if source != "draft":
        # Recovered from the persisted row's raw_handoff_json, not the live
        # draft file -- labelled so a consumer can tell "the in-flight
        # draft" from "reconstructed evidence for a historical/cut row",
        # exactly as --harness-id addressing already labels its own recovery.
        payload["envelope_source"] = source
        if row is not None:
            payload["handoff_id"] = row.get("id")
            payload["agent_state"] = row.get("agent_state")
            payload["cut_reason"] = row.get("cut_reason")
    print(json.dumps(payload, indent=2))
    return 0


def cmd_validate(args) -> int:
    """Validate the draft WITHOUT mutating it.

    Passes ``allow_adopt=False``: implicit adoption (see
    ``_maybe_adopt_draft``) materializes a fresh draft file, which is exactly
    the mutation this verb promises never to perform.
    """
    draft_id, envelope, as_json, _continuation = _load_target_draft(
        args, allow_adopt=False
    )
    if envelope is None:
        return 1
    result = _validate_envelope(envelope)
    if not result.ok:
        _print_rejection(result, as_json=as_json)
        return 1
    if as_json:
        print(json.dumps({"status": "ok", "draft_id": draft_id}))
    else:
        print(f"OK: draft {draft_id} is valid.")
    return 0


# ---------------------------------------------------------------------------
# list: read-only query over the PERSISTED agent_contract_handoffs rows.
#
# `view` reads a DRAFT on disk; nothing in the CLI exposed the finalized rows,
# so recovering a handoff_id meant reaching for the internal writer API through
# an interpreter. This verb closes that gap with a plain SELECT: no draft
# resolution, no mutation, T0.
# ---------------------------------------------------------------------------

# Columns rendered by the default table view, in order. The full row is
# available via --json; the table keeps the coordinates needed to identify a
# turn and walk the plan-task -> producer -> verifier chain.
#
# `agent_name` is DERIVED, not a column of the table -- see _birth_agent_name.
# `cut_reason` (v39) is a real column and answers "why did this turn not close
# cleanly" without opening each row: a stuck row is legible from the list alone.
_LIST_TABLE_COLUMNS = (
    "id",
    "created_at",
    "agent_id",
    "agent_name",
    "agent_state",
    "cut_reason",
    "kind",
    "session_id",
    "plan_task_id",
    "parent_handoff_id",
)

# Sentinel for the valueless form of --cut ("any non-NULL cut_reason"), kept
# distinct from a reason a user could actually type.
_CUT_ANY = "*"


def _birth_agent_name(row: dict) -> "str | None":
    """The dispatched agent's NAME for this row, or None when it is not recorded.

    THERE IS NO agent-name COLUMN. ``agent_id`` holds the minted a<hex> handle,
    and the readable name is dispatch metadata recorded INSIDE the birth
    envelope under ``writer.BIRTH_AGENT_NAME_KEY`` by
    ``insert_dispatched_handoff``. This reads exactly that and nothing else --
    no inference from ``agent_id``, ``kind`` or the session.

    Deliberately, honestly empty for three populations, because for them the
    fact is NOT recorded anywhere the row can reach:

    * legacy rows born before the minted-identity change, whose ``agent_id``
      column happens to carry an agent name. Reading a name back out of that
      column would mean deciding by SHAPE which handles are names, and the live
      table mixes real names (``cloud-troubleshooter``) with fixture handles
      (``aworkspace1``) -- a guess, not a projection;
    * rows finalized cleanly, since ``finalize_agent_contract_handoff`` replaces
      ``raw_handoff_json`` wholesale with the contract envelope, which carries no
      agent name (only ``mirror_partial_contract_handoff`` preserves the marker,
      via ``_merge_birth_markers``);
    * rows written by a non-dispatch writer (the hook backstop's own row).

    That leaves it populated exactly where the recovery question is asked: rows
    still born-and-open (DISPATCHED) and rows carrying mirrored partial evidence.
    """
    raw = row.get("raw_handoff_json")
    if not raw:
        return None
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    from gaia.store.writer import BIRTH_AGENT_NAME_KEY

    name = envelope.get(BIRTH_AGENT_NAME_KEY)
    return str(name) if name else None


def _row_in_date_range(row: dict, since: Optional[str], until: Optional[str]) -> bool:
    """Whether ``created_at`` falls inside the requested range.

    The column stores ISO-8601 UTC (``strftime('%Y-%m-%dT%H:%M:%SZ')``), which
    sorts lexicographically, so a plain string comparison is a correct range
    test for any ISO prefix the user passes (``2026-07-26`` or a full stamp).
    ``--until`` is inclusive of the whole day when given as a bare date, hence
    the prefix-aware upper bound.
    """
    created = row.get("created_at") or ""
    if since and created < since:
        return False
    if until and created[: len(until)] > until:
        return False
    return True


def cmd_list(args) -> int:
    """List persisted agent_contract_handoffs rows (read-only, SELECT only)."""
    from gaia.store.writer import list_agent_contract_handoffs

    cut = getattr(args, "cut", None)
    rows = list_agent_contract_handoffs(
        workspace=args.workspace,
        agent_id=args.agent_id,
        session_id=args.session_id,
        agent_state=args.state,
        contract_id=args.contract_id,
        harness_agent_id=getattr(args, "harness_id", None),
        cut_reason=None if cut in (None, _CUT_ANY) else cut,
        any_cut=cut == _CUT_ANY,
        limit=args.limit,
    )
    if args.since or args.until:
        rows = [r for r in rows if _row_in_date_range(r, args.since, args.until)]

    # The derived name rides alongside the real columns in BOTH renderings, so a
    # --json consumer does not have to parse raw_handoff_json to learn which
    # specialist a stuck row belongs to.
    for row in rows:
        row["agent_name"] = _birth_agent_name(row)

    if args.json:
        print(json.dumps({"count": len(rows), "handoffs": rows}, indent=2, default=str))
        return 0

    if not rows:
        print("No handoffs matched.")
        return 0

    widths = {
        col: max(len(col), *(len(str(r.get(col) or "-")) for r in rows))
        for col in _LIST_TABLE_COLUMNS
    }
    header = "  ".join(col.ljust(widths[col]) for col in _LIST_TABLE_COLUMNS)
    print(header)
    print("  ".join("-" * widths[col] for col in _LIST_TABLE_COLUMNS))
    for row in rows:
        print(
            "  ".join(
                str(row.get(col) if row.get(col) is not None else "-").ljust(widths[col])
                for col in _LIST_TABLE_COLUMNS
            )
        )
    print(f"\n{len(rows)} handoff(s).")
    return 0


# ---------------------------------------------------------------------------
# chain: read a turn's whole continuation chain from ANY of its links.
#
# A chain that can only be reassembled by hand does not count as readable. Given
# one contract id -- whichever an operator happens to hold, first link or last --
# this walks back to the root and forward to the live link and prints every
# contract in order, so "where did this turn's later work go" and "what did this
# link come from" are one command instead of a sequence of joins.
# ---------------------------------------------------------------------------

_CHAIN_TABLE_COLUMNS = (
    "link",
    "id",
    "contract_id",
    "agent_state",
    "cut_reason",
    "created_at",
    "continues_handoff_id",
)


def cmd_chain(args) -> int:
    """Print the continuation chain that ``--contract-id`` belongs to (read-only).

    The two ways this returns nothing are reported as the DIFFERENT diagnoses
    they are: an id that names no row (the answer is known and is empty) versus
    a walk the substrate refused (the answer is unknown). Collapsing them told
    an operator holding a real contract id that no such row existed.
    """
    from gaia.store.writer import ContinuationChainUnreadable, continuation_chain

    as_json = bool(getattr(args, "json", False))
    contract_id = getattr(args, "contract_id", None)
    if not contract_id:
        _print_error(
            "chain addresses ONE contract explicitly: pass --contract-id "
            "(any link of the chain -- the walk recovers the rest).",
            as_json,
        )
        return 1

    try:
        chain = continuation_chain(contract_id)
    except ContinuationChainUnreadable as exc:
        _print_error(
            f"{exc} Whether a row exists for {contract_id!r} is a separate "
            f"question this call could not reach; the failure is recorded as a "
            f"'contract.chain_unreadable' harness event.",
            as_json,
        )
        return 1
    if not chain:
        _print_error(
            f"no agent_contract_handoffs row exists for contract_id="
            f"{contract_id!r}, so there is no chain to read.",
            as_json,
        )
        return 1

    for index, row in enumerate(chain, start=1):
        row["link"] = index
        row["agent_name"] = _birth_agent_name(row)

    if as_json:
        print(json.dumps({
            "contract_id": contract_id,
            "links": len(chain),
            "chain": chain,
        }, indent=2, default=str))
        return 0

    widths = {
        col: max(len(col), *(len(str(r.get(col) or "-")) for r in chain))
        for col in _CHAIN_TABLE_COLUMNS
    }
    print("  ".join(col.ljust(widths[col]) for col in _CHAIN_TABLE_COLUMNS))
    print("  ".join("-" * widths[col] for col in _CHAIN_TABLE_COLUMNS))
    for row in chain:
        print(
            "  ".join(
                str(row.get(col) if row.get(col) is not None else "-").ljust(widths[col])
                for col in _CHAIN_TABLE_COLUMNS
            )
        )
    print(
        f"\n{len(chain)} link(s); the live contract is "
        f"{chain[-1].get('contract_id')}."
    )
    return 0


def _resolve_finalize_workspace(explicit: Optional[str]) -> str:
    """Resolve the workspace to record this finalize's row under.

    Harness-agnostic (decision #1): an explicit ``--workspace`` always wins;
    otherwise this reads ``gaia.project.current()`` -- Gaia's OWN path-based
    workspace resolution, never a Claude-Code env var -- and falls back to
    ``"me"``, exactly mirroring every other bin/cli/*.py plugin's
    ``_resolve_workspace`` (see bin/cli/task.py).
    """
    if explicit:
        return explicit
    try:
        from gaia.project import current as _project_current

        ws = _project_current()
        if ws:
            return ws
    except Exception:
        pass
    return "me"


def cmd_finalize(args) -> int:
    """Validate the draft as final AND write it to the store (T7 -- the SOLE
    idempotent writer of the agent_contract_handoffs row).

    Confirms the draft passes the full verdict (form + cross-check), then
    calls ``gaia.store.writer.finalize_agent_contract_handoff`` -- an
    idempotent UPSERT keyed on the draft's OWN contract id (``draft_id``;
    see the module docstring's "Draft identity" section) -- so a full
    init->set/add->finalize cycle inserts EXACTLY ONE row, and every
    subsequent ``finalize`` of the SAME draft is a genuine no-op: no
    duplicate row, no error, the SAME ``handoff_id`` reported back (AC-6).
    finalize never mutates the on-disk draft itself -- only ``validate``'s
    read-only verdict plus one DB write; see gaia.store.writer for the exact
    idempotency-key contract T8 (write-guard/permissions) and T9 (hook
    backstop) build on.

    Attribution: ``--session-id`` and ``--plan-task-id`` are stamped on the row
    when supplied, making the finalized contract findable by that coordinate
    pair. They are arguments, never environment reads -- see the module
    docstring. When the turn ADOPTED the identity born for it at dispatch (the
    draft id came from the injected ``# Your Contract`` block), this write
    lands on that very row: the UPSERT keys on the SAME contract_id, so it
    converges the nascent row and closes it, binding intact -- no second row and
    nothing for SubagentStop to supersede. Only an UNADOPTED turn leaves the born
    row behind for the stop hook to close (see gaia.store.writer's
    born-at-dispatch module comment for both paths).

    Continuation (``chain="follow"``): when the addressed contract was already
    closed and this turn's writes opened a continuation, finalize resolves to
    that live link and closes IT -- the record it continues stays exactly as it
    was closed. It FOLLOWS and never OPENS: a repeated finalize of the same draft
    is a documented no-op reporting the same handoff_id, and minting here would
    turn every retried close into an empty link. A resumption that produced work
    reached a mutating verb first, so the link already exists by the time this
    follows the chain to it.
    """
    draft_id, envelope, as_json, _continuation = _load_target_draft(
        args, chain="follow", sanitize=True
    )
    if envelope is None:
        return 1
    result = _validate_envelope(envelope)
    if not result.ok:
        _print_rejection(result, as_json=as_json)
        return 1

    agent_status = envelope.get("agent_status") or {}
    agent_id = agent_status.get("agent_id")
    agent_state = agent_status.get("agent_state")
    workspace = _resolve_finalize_workspace(getattr(args, "workspace", None))

    # Identity coherence, made VISIBLE at the last seam before the row lands.
    # A draft id IS ``{agent_id}.{token}`` and resolution globs on that prefix
    # (gaia.contract.drafts._agent_of / list_draft_ids), so an envelope whose
    # agent_id disagrees with its own file name is already unaddressable by
    # --agent-id -- it just fails silently today. Since `gaia contract init`
    # now mints and prints the handle, the only way to reach this state is to
    # overwrite agent_status.agent_id after init with a different value, and a
    # terminal row is immutable once written: refuse now rather than persist a
    # row nothing can join back to its draft.
    draft_agent_id = draft_id.split(".", 1)[0]
    if agent_id and draft_agent_id and agent_id != draft_agent_id:
        _print_error(
            f"agent_id mismatch: the draft is keyed to {draft_agent_id!r} but "
            f"agent_status.agent_id is {agent_id!r}. The draft id is "
            f"'{{agent_id}}.{{token}}', so these must agree or the finalized "
            f"row cannot be joined back to its draft. Set "
            f"agent_status.agent_id to {draft_agent_id!r}, or run "
            f"'gaia contract init' for a fresh draft under the id you want.",
            as_json,
        )
        return 1

    # Closing-state floor (live finding: handoff row 10955). finalize is the
    # turn's CLEAN close -- it clears cut_reason and ends the row's life. A
    # draft still carrying IN_PROGRESS at that seam produces a limbo row:
    # closed clean (cut_reason NULL) yet declaring the turn never ended --
    # invisible to `contract list --cut` AND to every terminal-state read.
    # Every real end of a turn has a closing state (COMPLETE / NEEDS_
    # VERIFICATION / BLOCKED / NEEDS_INPUT / APPROVAL_REQUEST); IN_PROGRESS is
    # the one state that asserts the turn continues, so it is the one state a
    # close may not carry. Scoped to THIS CLI seam on purpose: the rescue
    # lanes (SubagentStop persister / reaper / salvage) write through
    # gaia.store.writer directly and record IN_PROGRESS deliberately, as the
    # honest verdict of a turn that was cut -- those stamp a cut lane, never a
    # clean close.
    if agent_state == "IN_PROGRESS":
        msg = (
            "agent_status.agent_state is IN_PROGRESS, but finalize is the "
            "turn's clean CLOSE: declare the state your turn actually ends in "
            "first (gaia contract set agent_status.agent_state "
            "COMPLETE|NEEDS_VERIFICATION|BLOCKED|NEEDS_INPUT|APPROVAL_REQUEST"
            "), then finalize. A row closed clean while declaring IN_PROGRESS "
            "is neither cut nor closed -- a limbo nothing can route."
        )
        if as_json:
            print(json.dumps({
                "status": "rejected",
                "reason": "closing_state_required",
                "error": msg,
            }))
        else:
            print(f"Rejected: {msg}", file=sys.stderr)
        return 1

    # Blind-verification anti-leak at the CLI seam (plan 34 task 8). The
    # SubagentStop gate already refuses a plan-task-bound self-COMPLETE
    # (hooks/adapters/claude_code.py::_blind_verification_required), but that gate
    # is a SEPARATE persistence path -- `gaia contract finalize` was role-blind
    # AND binding-blind, so a producer could bypass the gate by finalizing a bound
    # COMPLETE straight through the CLI (the hole that leaked the 31 COMPLETEs).
    # Close it here with the SAME binding-keyed decision, recovered by contract_id:
    # if this turn's dispatch binding carries a plan_task_id, it is a plan-task-
    # bound producer turn and may NOT self-COMPLETE -- refuse and tell it to set
    # NEEDS_VERIFICATION so an independent verifier confirms the increment. A turn
    # with NO plan_task_id (investigation / memory / a verifier turn, which binds
    # by parent_handoff_id) is unbound and may self-COMPLETE -- unchanged.
    #
    # BINDING SOURCE, in priority order. An EXPLICIT ``--plan-task-id`` flag wins.
    # The contract-keyed lookup below is the fallback and now genuinely resolves
    # for an ADOPTED turn -- the born row is keyed by the same draft id this
    # finalize carries -- but it still resolves to nothing for a turn that minted
    # its own identity, which is exactly how the leak this gate closes stayed open
    # when the two key spaces were disjoint. The flag is the coordinate the
    # caller's own dispatch envelope carries, so a producer that declares its
    # binding is held to it here, at the same seam the SubagentStop gate holds it.
    if agent_state == "COMPLETE":
        bound_plan_task_id = getattr(args, "plan_task_id", None)
        if bound_plan_task_id is None:
            try:
                from gaia.store.writer import (
                    dispatched_binding_plan_task_id_by_contract,
                )

                bound_plan_task_id = dispatched_binding_plan_task_id_by_contract(
                    draft_id
                )
            except Exception:
                # Binding unresolvable (no born-at-dispatch row / DB read error) ->
                # treat as UNBOUND, exactly like the live gate's best-effort resolver.
                bound_plan_task_id = None
        if bound_plan_task_id is not None:
            msg = (
                f"agent_status.agent_state is COMPLETE, but this turn is bound to "
                f"plan_task_id={bound_plan_task_id}: a plan-task-bound producer "
                f"turn may not self-COMPLETE via 'gaia contract finalize'. Set "
                f"agent_state to NEEDS_VERIFICATION and propose "
                f"evidence_report.verification.result for an independent verifier "
                f"to confirm, or stay IN_PROGRESS. (A turn with no plan_task_id -- "
                f"investigation / memory / a verifier turn -- may self-COMPLETE.)"
            )
            if as_json:
                print(json.dumps({
                    "status": "rejected",
                    "reason": "blind_verification_required",
                    "plan_task_id": bound_plan_task_id,
                    "error": msg,
                }))
            else:
                print(f"Rejected: {msg}", file=sys.stderr)
            return 1

    from gaia.store.writer import finalize_agent_contract_handoff

    try:
        outcome = finalize_agent_contract_handoff(
            contract_id=draft_id,
            agent_id=agent_id,
            workspace=workspace,
            agent_state=agent_state,
            raw_handoff_json=json.dumps(envelope),
            # Attribution, from the EXPLICIT flags only. The CLI still never
            # READS a harness value (decisions #1, #3) -- these arrive as
            # arguments the caller supplies from its own dispatch envelope. Both
            # default to None, and the writer merges them with COALESCE, so
            # omitting them records nothing and clears nothing.
            session_id=getattr(args, "session_id", None),
            plan_task_id=getattr(args, "plan_task_id", None),
        )
    except Exception as exc:
        _print_error(f"finalize store write failed: {exc}", as_json)
        return 1

    handoff_id = outcome.get("handoff_id")
    created = bool(outcome.get("created"))
    if as_json:
        print(json.dumps({
            "status": "finalized",
            "draft_id": draft_id,
            "handoff_id": handoff_id,
            "created": created,
        }))
    else:
        if created:
            print(f"OK: draft {draft_id} finalized (handoff_id={handoff_id}).")
        else:
            print(
                f"OK: draft {draft_id} was already finalized "
                f"(handoff_id={handoff_id}); no-op."
            )
    return 0


def cmd_fill(args) -> int:
    """Batch-merge a JSON patch into the draft (validate-on-write)."""
    # fill always speaks JSON on output (its own --json flag is the PATCH
    # payload, not an output-format toggle), so error/success reporting is
    # JSON-shaped regardless -- force_json=True makes THIS helper's own
    # errors (no draft / ambiguous draft) JSON-shaped too, not only the
    # write-path errors below.
    draft_id, envelope, as_json, continuation = _load_target_draft(
        args, force_json=True, chain="open", sanitize=True
    )
    if envelope is None:
        return 1
    # --json-file reads the patch from disk instead of a shell argument.
    # A patch built from report prose (open_gaps, key_outputs, verification
    # notes) routinely carries apostrophes and embedded quotes; surviving
    # that text through shell quoting is a hazard the caller should not have
    # to manage by hand -- an unescaped apostrophe inside a single-quoted
    # --json value breaks the shell's own quoting, and everything after the
    # break is re-tokenized as bare words, no longer recognizable as the
    # `gaia contract fill` invocation it was part of. Writing the patch to a
    # file with the Write tool sidesteps shell quoting entirely, the same
    # discipline already used for a long approval `exact_content`.
    if args.json_file is not None:
        try:
            with open(args.json_file, "r", encoding="utf-8") as fh:
                raw_patch = fh.read()
        except OSError as exc:
            _print_error(f"--json-file could not be read: {exc}", as_json)
            return 1
    else:
        raw_patch = args.json_patch
    try:
        patch = json.loads(raw_patch)
    except (json.JSONDecodeError, TypeError) as exc:
        _print_error(f"--json/--json-file must be valid JSON: {exc}", as_json)
        return 1
    if not isinstance(patch, dict):
        _print_error("--json must decode to a JSON object", as_json)
        return 1
    _deep_merge(envelope, patch)
    return _write_if_valid(
        envelope, draft_id, as_json, mirror=True, continuation=continuation
    )


# ---------------------------------------------------------------------------
# reconcile -- the closure path for a hook-written residue row
# ---------------------------------------------------------------------------
#
# WHY THIS IS A SEPARATE VERB AND NOT A RELAXATION OF `finalize`.
# `finalize` is the AGENT's clean close of ITS OWN turn: it loads a draft,
# validates the full envelope, and enforces identity coherence between
# `agent_status.agent_id` and the draft id's `{agent_id}.{token}` prefix. A
# residue row satisfies none of those premises -- it has no draft on disk, no
# agent authored it, and the SubagentStop backstop keys it
# `hook-backstop.{agent_id}.{session_id}`, whose first dot-segment is the
# literal `hook-backstop`. That made the row unclosable by construction, and by
# every route: `_maybe_adopt_draft` refuses the prefix (it fails
# AGENT_ID_PATTERN_TEXT) so no draft can even be materialized, and were one
# materialized anyway, `cmd_finalize`'s coherence check would demand
# `agent_status.agent_id == "hook-backstop"` while the validator forbids exactly
# that value. No value satisfies both.
#
# Widening either check to admit the synthetic shape would widen the agent's own
# clean-close door for every turn, to serve rows no agent ever wrote. The honest
# fix is a door of the row's own kind: this verb never loads a draft, never
# validates an envelope, and NEVER TOUCHES agent_state -- so it cannot promote
# anything to COMPLETE and cannot widen the SubagentStop gate or the state enum.
# What it changes is the CUT MARK, which is the only thing wrong with a residue
# row: the turn's real verdict already lives on another row, and leaving the
# duplicate marked cut turns `contract list --cut` -- the orchestrator's signal
# for degraded work -- into mostly false positives.
#
# The semantics are the ones `close_born_dispatch_row` already established for a
# SUPERSEDED scaffold (hooks/modules/agents/handoff_persister.py): record
# `superseded_by_contract_id` as a pure link to where the verdict is, and clear
# `cut_reason`, because nothing about that turn was degraded. This verb is that
# same closure, reachable by hand for the rows already accumulated.
# ---------------------------------------------------------------------------

# Envelope markers written ONLY by the hook-side capture paths
# (handoff_persister: `_capture` and the reaped branch of
# `close_born_dispatch_row`). Requiring one is what keeps this verb off a row an
# agent authored: a turn's own cut row (never_finalized on its real contract)
# carries no `auto_captured`, so it stays in `--cut` where it belongs.
_HOOK_WRITTEN_MARKERS = ("auto_captured", "reaped")


def _reconcilable_row(row: dict) -> "tuple[bool, str]":
    """Whether this row is hook-written residue this verb may close.

    Returns ``(ok, reason)``; ``reason`` is the refusal text when ok is False.
    """
    if not row.get("cut_reason"):
        return False, (
            "the row carries no cut_reason -- it is already closed clean and "
            "there is nothing to reconcile."
        )
    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        envelope = None
    if not isinstance(envelope, dict) or not any(
        envelope.get(marker) for marker in _HOOK_WRITTEN_MARKERS
    ):
        return False, (
            "the row carries no hook-capture marker "
            f"({'/'.join(_HOOK_WRITTEN_MARKERS)}), so it is a turn's OWN cut "
            "row, not backstop residue. A genuinely cut turn must stay visible "
            "in 'gaia contract list --cut'."
        )
    return True, ""


def cmd_reconcile(args) -> int:
    """Close a hook-written residue row against the row holding the real verdict.

    Addressed by ``--contract-id`` (the synthetic ``hook-backstop.*`` key such a
    row carries) or ``--harness-id`` (the same bridge ``view`` uses). Never
    changes ``agent_state``; it stamps ``superseded_by_contract_id`` into the
    envelope and clears ``cut_reason`` so the row leaves the ``--cut`` signal.
    """
    from gaia.store.writer import (
        collapse_continuation_chains,
        list_agent_contract_handoffs,
        reconcile_cut_row,
    )

    as_json = bool(getattr(args, "json", False))
    contract_id = getattr(args, "contract_id", None)
    harness_id = getattr(args, "harness_id", None)

    # Neither coordinate is NOT a resolution fallback here: a bare
    # `contract_id=None` lookup returns whatever row sorts first, which is the
    # class of accidental-target bug this whole change exists to remove.
    if not contract_id and not harness_id:
        _print_error(
            "reconcile addresses ONE row explicitly: pass --contract-id or "
            "--harness-id. There is no default target.",
            as_json,
        )
        return 1

    if harness_id and not contract_id:
        rows = list_agent_contract_handoffs(harness_agent_id=harness_id, limit=16)
        if not rows:
            _print_error(
                f"no contract row carries harness_agent_id={harness_id!r}.",
                as_json,
            )
            return 1
        # A continuation chain shares one harness id across all its links, so
        # the links are not rival candidates -- collapse to the live one before
        # judging ambiguity (the same reduction the SubagentStop bridge applies).
        rows = collapse_continuation_chains(rows)
        if len(rows) > 1:
            _print_error(
                f"harness_agent_id={harness_id!r} matches {len(rows)} rows; "
                f"address the one you mean with --contract-id "
                f"({', '.join(str(r.get('contract_id')) for r in rows)}).",
                as_json,
            )
            return 1
        row = rows[0]
        contract_id = row.get("contract_id")
    else:
        row = _lookup_handoff_row_by_contract_id(contract_id)
        if row is None:
            _print_error(
                f"no agent_contract_handoffs row exists for contract_id="
                f"{contract_id!r}.",
                as_json,
            )
            return 1

    ok, reason = _reconcilable_row(row)
    if not ok:
        _print_error(f"refusing to reconcile {contract_id!r}: {reason}", as_json)
        return 1

    superseded_by = getattr(args, "superseded_by", None)
    if superseded_by:
        if _lookup_handoff_row_by_contract_id(superseded_by) is None:
            _print_error(
                f"--superseded-by names {superseded_by!r}, which has no "
                f"agent_contract_handoffs row. The pointer must name the row "
                f"that actually holds this turn's verdict.",
                as_json,
            )
            return 1

    try:
        envelope = json.loads(row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        envelope = None
    if not isinstance(envelope, dict):
        envelope = {}
    envelope = dict(envelope)
    envelope["reconciled"] = True
    if superseded_by:
        envelope["superseded_by_contract_id"] = superseded_by

    outcome = reconcile_cut_row(
        contract_id, raw_handoff_json=json.dumps(envelope),
    )
    if outcome.get("status") != "applied":
        _print_error(
            f"reconcile did not apply to {contract_id!r} "
            f"(reason={outcome.get('reason')}).",
            as_json,
        )
        return 1

    result = {
        "status": "reconciled",
        "contract_id": contract_id,
        "handoff_id": outcome.get("handoff_id"),
        "agent_state": row.get("agent_state"),
        "cut_reason_before": row.get("cut_reason"),
        "cut_reason_after": None,
        "superseded_by_contract_id": superseded_by,
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"OK: reconciled {contract_id} (handoff_id={outcome.get('handoff_id')}); "
            f"cut_reason {row.get('cut_reason')} -> cleared, agent_state "
            f"{row.get('agent_state')} unchanged"
            + (f", superseded_by={superseded_by}" if superseded_by else "")
        )
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring (shared by register() and the standalone shim)
# ---------------------------------------------------------------------------

def _add_common_draft_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--draft-id",
        dest="draft_id",
        metavar="ID",
        default=None,
        help="Explicit draft id to operate on (default: the most recently touched draft)",
    )


def _add_agent_scope_arg(parser: argparse.ArgumentParser) -> None:
    """Optional per-agent resolution scope for the mutating verbs.

    When ``--draft-id`` is omitted, ``--agent-id`` narrows resolution to a
    single agent's own drafts -- the per-agent, resume-aware addressing
    (decision #8) that lets a resumed agent find its draft without any harness
    session concept. It resolves only when that scope names exactly one live
    draft: agent ids are not unique, so a handle shared by several live drafts
    is reported as ambiguous rather than decided by recency.
    """
    parser.add_argument(
        "--agent-id",
        dest="agent_id",
        metavar="AGENT_ID",
        default=None,
        help=(
            "Scope draft resolution to this agent's drafts (used only when "
            "--draft-id is omitted; errors if the handle has several live drafts)"
        ),
    )


def _build_subcommands(sub) -> None:
    p_init = sub.add_parser("init", help="Create a new draft envelope (validate-on-write)")
    p_init.add_argument(
        "--agent-id",
        dest="agent_id",
        default=None,
        metavar="AGENT_ID",
        help=(
            "agent_status.agent_id value. OPTIONAL -- omit it and the "
            "substrate mints a conforming handle and prints it. When given "
            "it must match ^a[0-9a-f]{16,}$"
        ),
    )
    _add_common_draft_arg(p_init)
    p_init.add_argument("--json", action="store_true", help="JSON output")
    p_init.set_defaults(func=cmd_init)

    p_set = sub.add_parser("set", help="Set a scalar field by dotted path (validate-on-write)")
    p_set.add_argument("field", metavar="FIELD", help="Dotted path, e.g. agent_status.agent_state")
    p_set.add_argument(
        "value",
        metavar="VALUE",
        help="New value (parsed as JSON when possible, else kept as a plain string)",
    )
    _add_common_draft_arg(p_set)
    _add_agent_scope_arg(p_set)
    p_set.add_argument("--json", action="store_true", help="JSON output")
    p_set.set_defaults(func=cmd_set)

    p_add = sub.add_parser("add", help="Append a value to a list field (validate-on-write)")
    p_add.add_argument(
        "field", metavar="FIELD", help="Dotted path to a list field, e.g. agent_status.pending_steps"
    )
    p_add.add_argument(
        "value",
        metavar="VALUE",
        help="Value to append (parsed as JSON when possible, else kept as a plain string)",
    )
    _add_common_draft_arg(p_add)
    _add_agent_scope_arg(p_add)
    p_add.add_argument("--json", action="store_true", help="JSON output")
    p_add.set_defaults(func=cmd_add)

    p_view = sub.add_parser(
        "view",
        help="Print a turn's contract envelope, or one --field subtree -- never writes",
        description=(
            "Print a turn's contract envelope. NEVER writes -- not the draft "
            "file, not the DB row -- so it is safe to point at ANY row, "
            "including a historical or cut one found via 'gaia contract list "
            "--cut --json': pass that row's contract_id as --draft-id, or its "
            "harness_agent_id (v40+) as --harness-id. Either addressing mode "
            "recovers real accumulated evidence from raw_handoff_json when no "
            "on-disk draft file remains, and reports explicitly (never as a "
            "silent blank envelope) when nothing is recoverable at all."
        ),
    )
    p_view.add_argument(
        "--field",
        dest="field",
        metavar="DOTTED_PATH",
        default=None,
        help=(
            "Print ONLY this dotted-path subtree instead of the full "
            "envelope -- the SAME schema names the envelope itself uses "
            "and the SAME dotted-path scheme 'set'/'add' use (e.g. "
            "agent_status.agent_state, evidence_report.open_gaps, "
            "evidence_report.verification, update_contracts). Combines "
            "with --draft-id, --agent-id (default resolution), or "
            "--harness-id exactly like a full view -- including a "
            "historical or cut row found via 'contract list --cut'. Exit "
            "0 and print the value verbatim (even an empty [] or null) "
            "when the path EXISTS; exit 1 with an explicit stderr message "
            "when it does NOT -- an existing-but-empty field and an "
            "absent one are never the same response."
        ),
    )
    _add_common_draft_arg(p_view)
    _add_agent_scope_arg(p_view)
    p_view.add_argument(
        "--harness-id",
        dest="harness_id",
        metavar="HARNESS_AGENT_ID",
        default=None,
        help=(
            "Resolve by the HARNESS's per-run agent id (the agentId the "
            "parent's Task result reports) instead of a draft id -- the "
            "recovery lane for a turn cut before it finalized. Prints the "
            "stamped row and its envelope (on-disk draft when present, else "
            "the row's own raw_handoff_json)."
        ),
    )
    p_view.add_argument(
        "--json",
        action="store_true",
        help=(
            "JSON-shaped error reporting (no-draft / ambiguous-draft / "
            "unreadable-envelope messages) for a machine consumer. Success "
            "output is already valid JSON either way -- the full envelope "
            "or a --field subtree -- so this only changes how an ERROR is "
            "printed, matching every other contract subcommand's --json."
        ),
    )
    p_view.set_defaults(func=cmd_view)

    p_list = sub.add_parser(
        "list",
        help="List persisted agent_contract_handoffs rows (read-only)",
        description=(
            "List persisted agent_contract_handoffs rows (read-only). Each "
            "row's --json output carries both 'contract_id' and (when "
            "stamped, v40+) 'harness_agent_id' -- feed either straight into "
            "'gaia contract view --draft-id' / '--harness-id' to recover a "
            "historical or cut row's real evidence; view never writes and "
            "recovers from raw_handoff_json when no draft file remains."
        ),
    )
    p_list.add_argument(
        "--agent-id", "--agent", dest="agent_id", metavar="AGENT_ID", default=None,
        help=(
            "Filter by the exact persisted agent_id (normally the minted "
            "a<hex> handle). Dispatch agent type/name is not stored on legacy "
            "rows and cannot be inferred by this filter; the agent_name column "
            "shows the dispatched name where the birth envelope recorded it."
        ),
    )
    p_list.add_argument(
        "--state", dest="state", metavar="AGENT_STATE", default=None,
        help="Filter by agent_state (COMPLETE, DISPATCHED, BLOCKED, ...)",
    )
    p_list.add_argument(
        "--cut", dest="cut", metavar="REASON", nargs="?", const=_CUT_ANY,
        default=None,
        help=(
            "Filter to turns that did NOT close cleanly. Bare --cut takes every "
            "cut reason; --cut REASON takes one (never_finalized, reaped, "
            "backstop_capture, salvaged_truncation)"
        ),
    )
    p_list.add_argument(
        "--session", dest="session_id", metavar="SESSION_ID", default=None,
        help="Filter by session_id",
    )
    p_list.add_argument(
        "--contract-id", dest="contract_id", metavar="CONTRACT_ID", default=None,
        help=(
            "Filter by contract_id (the draft/dispatch idempotency key). "
            "This is the SAME value 'gaia contract view --draft-id' expects "
            "-- not the numeric 'id' column shown in the default table view."
        ),
    )
    p_list.add_argument(
        "--harness-id", dest="harness_id", metavar="HARNESS_AGENT_ID",
        default=None,
        help=(
            "Filter by the harness's per-run agent id (stamped at "
            "SubagentStart, v40) -- recovers a cut turn's row from the "
            "agentId the parent's Task result reports"
        ),
    )
    p_list.add_argument(
        "--workspace", dest="workspace", metavar="NAME", default=None,
        help="Filter by workspace (default: all workspaces)",
    )
    p_list.add_argument(
        "--since", dest="since", metavar="ISO_DATE", default=None,
        help="Only rows created at or after this ISO date/timestamp",
    )
    p_list.add_argument(
        "--until", dest="until", metavar="ISO_DATE", default=None,
        help="Only rows created at or before this ISO date/timestamp (inclusive)",
    )
    p_list.add_argument(
        "--limit", dest="limit", type=int, default=20, metavar="N",
        help="Maximum rows to return (default: 20)",
    )
    p_list.add_argument("--json", action="store_true", help="JSON output")
    p_list.set_defaults(func=cmd_list)

    p_validate = sub.add_parser(
        "validate",
        help="Validate the draft WITHOUT mutating it",
        description="Validate the draft only; do not write or converge a handoff row.",
    )
    _add_common_draft_arg(p_validate)
    _add_agent_scope_arg(p_validate)
    p_validate.add_argument("--json", action="store_true", help="JSON output")
    p_validate.set_defaults(func=cmd_validate)

    p_finalize = sub.add_parser(
        "finalize",
        help=(
            "Validate and persist/converge the handoff row (idempotent); "
            "finalize does not imply terminal COMPLETE"
        ),
        description=(
            "Validate the draft and idempotently persist/converge its handoff row. "
            "Any CLOSING agent_state may be finalized (COMPLETE, "
            "NEEDS_VERIFICATION, BLOCKED, NEEDS_INPUT, APPROVAL_REQUEST); "
            "IN_PROGRESS is rejected -- declare the state the turn ends in "
            "before closing. Only COMPLETE is terminal."
        ),
    )
    _add_common_draft_arg(p_finalize)
    _add_agent_scope_arg(p_finalize)
    p_finalize.add_argument(
        "--workspace",
        dest="workspace",
        metavar="WORKSPACE",
        default=None,
        help="Workspace to record the row under (default: gaia.project.current() or 'me')",
    )
    # Attribution flags -- SUPPLIED by the caller from its dispatch envelope,
    # never read from the environment (see the module docstring's "Attribution
    # vs. harness-agnosticism"). Omitting them records nothing and clears
    # nothing; the writer merges both with COALESCE.
    p_finalize.add_argument(
        "--session-id",
        dest="session_id",
        metavar="SESSION_ID",
        default=None,
        help=(
            "Session this turn belongs to, so the finalized contract is "
            "attributable to it by query (supplied by the caller, not read "
            "from the environment)"
        ),
    )
    p_finalize.add_argument(
        "--plan-task-id",
        dest="plan_task_id",
        metavar="TASK_ID",
        type=int,
        default=None,
        help=(
            "Plan task (tasks.id) this turn executes, so the finalized contract "
            "is attributable to it by query. Declaring it also binds the turn: a "
            "plan-task-bound producer may not self-COMPLETE"
        ),
    )
    p_finalize.add_argument("--json", action="store_true", help="JSON output")
    p_finalize.set_defaults(func=cmd_finalize)

    p_fill = sub.add_parser("fill", help="Batch-merge a JSON patch into the draft (validate-on-write)")
    p_fill_json_source = p_fill.add_mutually_exclusive_group(required=True)
    p_fill_json_source.add_argument(
        "--json",
        dest="json_patch",
        metavar="JSON",
        help="JSON object to deep-merge into the draft envelope",
    )
    p_fill_json_source.add_argument(
        "--json-file",
        dest="json_file",
        metavar="PATH",
        help=(
            "Read the JSON patch from PATH instead of a shell argument -- "
            "write it with the Write tool first when the patch carries "
            "report prose (apostrophes, embedded quotes) that is fragile "
            "to pass as a single shell-quoted --json value"
        ),
    )
    _add_common_draft_arg(p_fill)
    _add_agent_scope_arg(p_fill)
    p_fill.set_defaults(func=cmd_fill)

    p_chain = sub.add_parser(
        "chain",
        help="Print the continuation chain a contract belongs to (read-only)",
        description=(
            "Print every contract in one turn's continuation chain, oldest link "
            "first. A turn is a contract, and resuming a closed turn does not "
            "reopen it -- the new work lands in a NEW contract that records "
            "which one it continues. Pass ANY link as --contract-id: the walk "
            "goes back to the root and forward to the live link, so the chain "
            "never has to be reassembled by hand. A contract that was never "
            "resumed prints as a single link."
        ),
    )
    p_chain.add_argument(
        "--contract-id",
        dest="contract_id",
        metavar="CONTRACT_ID",
        default=None,
        help="Any link of the chain (the same value --draft-id takes)",
    )
    p_chain.add_argument("--json", action="store_true", help="JSON output")
    p_chain.set_defaults(func=cmd_chain)

    p_reconcile = sub.add_parser(
        "reconcile",
        help="Clear the cut mark on a hook-written backstop/residue row",
        description=(
            "Close a residue row the SubagentStop backstop wrote for a turn "
            "whose real verdict lives on ANOTHER row. Addresses the row by "
            "--contract-id (the synthetic 'hook-backstop.*' key) or "
            "--harness-id. Clears cut_reason so the duplicate leaves "
            "'gaia contract list --cut', and records --superseded-by as the "
            "pointer to the row that holds the verdict. NEVER changes "
            "agent_state, and refuses any row an agent authored itself."
        ),
    )
    p_reconcile.add_argument(
        "--contract-id",
        dest="contract_id",
        metavar="CONTRACT_ID",
        default=None,
        help="The residue row's contract_id (as reported by 'contract list --cut')",
    )
    p_reconcile.add_argument(
        "--harness-id",
        dest="harness_id",
        metavar="HARNESS_AGENT_ID",
        default=None,
        help="Address the row by harness_agent_id instead (must match exactly one row)",
    )
    p_reconcile.add_argument(
        "--superseded-by",
        dest="superseded_by",
        metavar="CONTRACT_ID",
        default=None,
        help=(
            "contract_id of the row that holds this turn's real verdict; "
            "recorded on the residue row as superseded_by_contract_id"
        ),
    )
    p_reconcile.add_argument("--json", action="store_true", help="JSON output")
    p_reconcile.set_defaults(func=cmd_reconcile)


def _contract_default(args) -> int:
    print("Usage: gaia contract SUBCOMMAND [options]")
    print("")
    print("  init [--agent-id AGENT_ID]  -- create a new draft; mints and prints the agent_id when omitted")
    print("  set FIELD VALUE           -- set a scalar field by dotted path")
    print("  add FIELD VALUE           -- append a value to a list field")
    print("  view [--field PATH]       -- print a turn's contract envelope (never writes); --draft-id or")
    print("                               --harness-id both recover a historical/cut row found via 'list'.")
    print("                               --field PATH (schema names, e.g. evidence_report.open_gaps) reads")
    print("                               ONLY that subtree: exit 0 with the value (even empty) if it")
    print("                               exists, exit 1 with a clean error if it does not.")
    print("  validate                  -- validate the draft without mutating it")
    print("  finalize                  -- validate + persist/converge the row; only COMPLETE is terminal")
    print("  fill --json JSON          -- batch-merge a JSON patch into the draft")
    print("  chain --contract-id ID    -- print the whole continuation chain from any link")
    print("  reconcile --contract-id ID -- clear the cut mark on a hook-written residue row")
    print("                               (--superseded-by points at the row holding the verdict);")
    print("                               never changes agent_state, refuses an agent's own cut row")
    print("")
    print("Run 'gaia contract --help' for more information.")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration (called by bin/gaia dispatcher)
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the 'contract' subcommand group with the root parser."""
    p = subparsers.add_parser(
        "contract",
        help="Build and validate an agent_contract_handoff draft by-value",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="contract_cmd", metavar="SUBCOMMAND")
    sub.required = True
    _build_subcommands(sub)
    p.set_defaults(func=_contract_default)


def cmd_contract(args) -> int:
    """Top-level dispatcher for 'gaia contract'.

    Called by bin/gaia which invokes cmd_{subcommand}(args). For grouped
    subcommands, this delegates to the specific handler set via
    set_defaults(func=...) in register().
    """
    func = getattr(args, "func", None)
    if func is not None and func is not _contract_default:
        return func(args)
    return _contract_default(args)


# ---------------------------------------------------------------------------
# Standalone shim (for isolated testing without bin/gaia's DB bootstrap)
# ---------------------------------------------------------------------------

def _build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 bin/cli/contract.py",
        description="Gaia contract subcommand (standalone mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="contract_cmd", metavar="SUBCOMMAND")
    sub.required = True
    _build_subcommands(sub)
    return parser


if __name__ == "__main__":
    parser = _build_standalone_parser()
    parsed = parser.parse_args()
    sys.exit(parsed.func(parsed))
