"""
gaia contract -- Contract-as-Managed-Data CLI (by-value, validate-on-write).

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli (M2).
Builds an ``agent_contract_handoff`` envelope BY-VALUE across several small
CLI calls instead of the agent re-emitting one large fenced JSON block every
turn. Every mutating verb validates the FULL resulting envelope through the
single combined entry point, ``gaia.contract.crosscheck.validate()`` (layer 1
form + layer 2 cross-check), before persisting anything -- so a rejected
write NEVER lands, NO false-pass.

Subcommands (the 6 verbs + the ``fill --json`` batch mode):
    init     [--agent-id ID]      [--draft-id ID]  Create a new draft; mints
                                                   and prints the agent_id
                                                   when --agent-id is omitted
    set      FIELD VALUE          [--draft-id ID]  Set a scalar field (dotted path)
    add      FIELD VALUE          [--draft-id ID]  Append a value to a list field
    view     [--field DOTTED_PATH][--draft-id ID]  Print the draft envelope, or ONLY a dotted-path subtree
             [--harness-id ID]                     ... or resolve by the harness's per-run agentId (cut-turn recovery)
    validate                      [--draft-id ID]  Validate the draft WITHOUT mutating it
    finalize                      [--draft-id ID]  Validate the draft as final
    fill     --json JSON          [--draft-id ID]  Batch-merge a JSON patch (validate-on-write)

All subcommands exit 0 on success, 1 on a rejected write / validation
failure or a usage error (never a raw traceback).

Validate-on-write, no false-pass (AC-4):
    init / set / add / fill apply their mutation to an IN-MEMORY copy of the
    draft, call ``gaia.contract.crosscheck.validate()`` on that copy, and
    persist to disk ONLY when the verdict is ok. On rejection, the on-disk
    draft is left untouched at its last-known-good state, the concrete
    errors (including the enum text for an out-of-range agent_state) are
    printed to stderr, and the process exits non-zero -- never a crash.
    ``validate`` and ``finalize`` never mutate; they only report the verdict.

Incremental fill is MIRRORED to the row, not only to disk:
    ``set``/``add``/``fill`` persist the draft to
    ``data_dir()/contract_drafts/`` AND, best-effort, reflect the same partial
    envelope onto this turn's already-born ``agent_contract_handoffs`` row via
    ``gaia.store.writer.mirror_partial_contract_handoff``. Without it a turn cut
    before ``finalize`` left every piece of evidence it had accumulated
    invisible to any DB reader, however much had been written to disk. The
    mirror is deliberately weaker than ``finalize``: it never CREATES a row (a
    draft with no born row mirrors to nothing and the disk write stands alone),
    never touches a row already in a terminal state, and never moves the row's
    ``agent_state`` or its born-at-dispatch binding -- only
    ``raw_handoff_json``. Every failure is swallowed: the mirror can never turn
    a successful draft write into a failed CLI call. Read the mirrored row back
    with ``gaia contract list --contract-id <draft-id> --json``.

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
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure the gaia package (repo root) is importable regardless of cwd,
# mirroring the sys.path setup used by every other bin/cli/*.py plugin
# (see bin/cli/task.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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


# ---------------------------------------------------------------------------
# Envelope construction / mutation helpers
# ---------------------------------------------------------------------------

def _initial_envelope(agent_id: str) -> dict:
    """The starting shape for a freshly-init'd draft.

    Deliberately a genuinely SHAPE-VALID envelope (not a stub that would
    later need a special-cased pass) so init's own validate-on-write is a
    real check, not a smuggled-through no-op: agent_state defaults to
    IN_PROGRESS, pending_steps is present (empty list), next_action is a
    non-empty placeholder the agent overwrites via `set`/`add`, and
    evidence_report carries all seven required keys.

    ``failure_report`` is seeded ``None`` for the same reason
    ``consolidation_report``/``approval_request`` already are: seeding the
    slot makes it discoverable in `gaia contract view` without making it
    required -- ``gaia.contract.validator.validate_form`` only runs the
    FAILURE_REPORT_SHAPE check when the block is present and non-null, so a
    seeded ``None`` reaches no check at all, exactly like an omitted key.
    """
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "pending",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
        "failure_report": None,
    }


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


def _mirror_partial_to_row(draft_id: str, envelope: dict) -> bool:
    """Mirror the partial envelope onto this turn's DB row. Best-effort.

    The disk draft is the primary record and is already written by the time
    this runs; the row is the SECOND place the same partial evidence lands, so
    a turn cut before ``finalize`` still leaves recoverable evidence somewhere
    a query can reach. Every failure mode is swallowed: no row born under this
    draft id, a DB that does not exist yet, an unseeded dispatch identity
    rejected by the write guard -- none of them may turn a successful draft
    write into a failed CLI call.

    The writer (``gaia.store.writer.mirror_partial_contract_handoff``) is what
    guarantees this can never create a row and never touch a terminal one; this
    seam only decides WHEN to offer the mirror, never what it is allowed to do.

    Returns True only when a row was actually updated.
    """
    try:
        from gaia.store.writer import mirror_partial_contract_handoff

        outcome = mirror_partial_contract_handoff(draft_id, json.dumps(envelope))
        return bool(outcome.get("status") == "applied")
    except Exception:
        return False


def _write_if_valid(
    envelope: dict,
    draft_id: str,
    as_json: bool,
    extra_json: Optional[dict] = None,
    extra_lines: Optional[list] = None,
    mirror: bool = False,
) -> int:
    """Validate-on-write core: persist ONLY when the full verdict is ok.

    ``extra_json``/``extra_lines`` let a caller enrich the SUCCESS report
    without emitting a second record after this one -- a machine consumer
    reading stdout must still find exactly one JSON object.

    ``mirror`` additionally reflects the freshly-persisted partial envelope
    onto this turn's non-terminal row (see ``_mirror_partial_to_row``). It is
    opt-in per subcommand rather than automatic: the incremental verbs
    (``set``/``add``/``fill``) are the ones whose evidence would otherwise be
    lost to a cut, while ``init`` has nothing to preserve yet -- its envelope is
    the empty starting shape, and mirroring it would overwrite the birth
    envelope with no evidence gained.
    """
    result = _validate_envelope(envelope)
    if not result.ok:
        _print_rejection(result, as_json=as_json)
        return 1
    _save_draft(draft_id, envelope)
    mirrored = _mirror_partial_to_row(draft_id, envelope) if mirror else None
    if as_json:
        payload = {"status": "ok", "draft_id": draft_id}
        if mirrored is not None:
            payload["mirrored"] = mirrored
        payload.update(extra_json or {})
        print(json.dumps(payload))
    else:
        print(f"OK: draft {draft_id} updated and validated.")
        for line in extra_lines or []:
            print(line)
    return 0


def _load_target_draft(
    args, force_json: bool = False
) -> "tuple[Optional[str], Optional[dict], bool]":
    """Resolve --draft-id and load it. Returns (draft_id, envelope, as_json).

    envelope is None (and an error already printed) when nothing is
    resolvable, resolution is ambiguous across agents, or the file is
    missing/corrupt -- callers should return 1.

    ``force_json`` lets a caller whose own ``--json`` flag means something
    else (``fill``'s ``--json`` is the PATCH payload, not an output-format
    toggle) still get JSON-shaped error reporting for THIS helper's own
    errors (no draft / ambiguous draft), matching that caller's documented
    "always speaks JSON" contract instead of silently falling back to
    plain text because ``args`` has no ``json`` attribute under that name.
    """
    from gaia.contract.drafts import AmbiguousDraftError

    as_json = force_json or bool(getattr(args, "json", False))
    try:
        draft_id = _resolve_draft_id(
            getattr(args, "draft_id", None),
            getattr(args, "agent_id", None),
        )
    except AmbiguousDraftError as exc:
        _print_ambiguous_draft_error(exc, as_json)
        return None, None, as_json
    if draft_id is None:
        _no_draft_error(as_json)
        return None, None, as_json
    envelope = _load_draft(draft_id)
    if envelope is None:
        _no_draft_error(as_json, draft_id)
        return draft_id, None, as_json
    return draft_id, envelope, as_json


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
    draft_id, envelope, as_json = _load_target_draft(args)
    if envelope is None:
        return 1
    value = _parse_value_arg(args.value)
    try:
        _set_nested(envelope, args.field, value)
    except ValueError as exc:
        _print_error(str(exc), as_json)
        return 1
    return _write_if_valid(envelope, draft_id, as_json, mirror=True)


def cmd_add(args) -> int:
    """Append a value to a list field (validate-on-write)."""
    draft_id, envelope, as_json = _load_target_draft(args)
    if envelope is None:
        return 1
    value = _parse_value_arg(args.value)
    try:
        _append_nested(envelope, args.field, value)
    except ValueError as exc:
        _print_error(str(exc), as_json)
        return 1
    return _write_if_valid(envelope, draft_id, as_json, mirror=True)


def _view_by_harness_id(args, harness_id: str) -> int:
    """Resolve and print a turn's contract by the HARNESS's per-run agent id.

    The recovery lane for a cut turn: the parent's Task result reports an
    ``agentId`` in the harness's own identifier space, which resolves no draft
    (drafts key on the CLI-minted space). Since v40 SubagentStart stamps that
    id onto the born row (``agent_contract_handoffs.harness_agent_id``), so
    the row -- and through its ``contract_id``, the on-disk draft when one
    still exists -- is reachable directly, with no date search or content
    grep. The freshest source wins: the draft on disk when present (it may
    carry writes newer than the last DB mirror), else the row's own
    ``raw_handoff_json``.
    """
    from gaia.store.writer import list_agent_contract_handoffs

    rows = list_agent_contract_handoffs(harness_agent_id=harness_id, limit=1)
    if not rows:
        _print_error(
            f"no contract row carries harness_agent_id={harness_id!r}. Rows "
            f"are stamped at SubagentStart (v40); a turn dispatched before "
            f"that version, or one whose start never reached the stamping "
            f"seam, is only reachable by session/date via 'gaia contract "
            f"list'.",
            as_json=False,
        )
        return 1
    row = rows[0]
    contract_id = row.get("contract_id")

    envelope = _load_draft(contract_id) if contract_id else None
    source = "draft"
    if envelope is None:
        source = "db_row"
        try:
            envelope = json.loads(row.get("raw_handoff_json") or "null")
        except (TypeError, ValueError):
            envelope = None

    field = getattr(args, "field", None)
    if field is not None:
        if not isinstance(envelope, dict):
            _print_error(
                f"row {row.get('id')} has no readable envelope to take "
                f"--field from.",
                as_json=False,
            )
            return 1
        try:
            subtree = _get_nested(envelope, field)
        except ValueError as exc:
            _print_error(str(exc), as_json=False)
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
    """Print the current draft envelope, or ONLY a dotted-path subtree of it
    (``--field``), without mutating anything.

    With no ``--field`` this prints the FULL envelope exactly as before
    (``{"draft_id": ..., "envelope": ...}``). With ``--field <dotted-path>``
    it resolves that subtree via the same ``_split_path`` addressing ``set``
    uses and prints ONLY that subtree as JSON -- an invalid/absent path is a
    clean error to stderr with a non-zero exit, never a raw traceback.

    ``--harness-id`` switches the lookup to the harness's per-run agent id
    (see ``_view_by_harness_id``) -- the id the parent holds for a turn that
    was cut before it could finalize.
    """
    harness_id = getattr(args, "harness_id", None)
    if harness_id:
        return _view_by_harness_id(args, harness_id)
    draft_id, envelope, as_json = _load_target_draft(args)
    if envelope is None:
        return 1
    field = getattr(args, "field", None)
    if field is not None:
        try:
            subtree = _get_nested(envelope, field)
        except ValueError as exc:
            _print_error(str(exc), as_json)
            return 1
        print(json.dumps(subtree, indent=2))
        return 0
    print(json.dumps({"draft_id": draft_id, "envelope": envelope}, indent=2))
    return 0


def cmd_validate(args) -> int:
    """Validate the draft WITHOUT mutating it."""
    draft_id, envelope, as_json = _load_target_draft(args)
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
_LIST_TABLE_COLUMNS = (
    "id",
    "created_at",
    "agent_id",
    "agent_state",
    "kind",
    "session_id",
    "plan_task_id",
    "parent_handoff_id",
)


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

    rows = list_agent_contract_handoffs(
        workspace=args.workspace,
        agent_id=args.agent_id,
        session_id=args.session_id,
        agent_state=args.state,
        contract_id=args.contract_id,
        harness_agent_id=getattr(args, "harness_id", None),
        limit=args.limit,
    )
    if args.since or args.until:
        rows = [r for r in rows if _row_in_date_range(r, args.since, args.until)]

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
    draft id came from the injected ``# Contract Identity`` block), this write
    lands on that very row: the UPSERT keys on the SAME contract_id, so it
    converges the nascent row and closes it, binding intact -- no second row and
    nothing for SubagentStop to supersede. Only an UNADOPTED turn leaves the born
    row behind for the stop hook to close (see gaia.store.writer's
    born-at-dispatch module comment for both paths).
    """
    draft_id, envelope, as_json = _load_target_draft(args)
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
    draft_id, envelope, as_json = _load_target_draft(args, force_json=True)
    if envelope is None:
        return 1
    try:
        patch = json.loads(args.json_patch)
    except (json.JSONDecodeError, TypeError) as exc:
        _print_error(f"--json must be valid JSON: {exc}", as_json)
        return 1
    if not isinstance(patch, dict):
        _print_error("--json must decode to a JSON object", as_json)
        return 1
    _deep_merge(envelope, patch)
    return _write_if_valid(envelope, draft_id, as_json, mirror=True)


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

    p_view = sub.add_parser("view", help="Print the current draft envelope (or one --field subtree)")
    p_view.add_argument(
        "--field",
        dest="field",
        metavar="DOTTED_PATH",
        default=None,
        help=(
            "Print ONLY this dotted-path subtree of the draft "
            "(e.g. evidence_report.files_checked) instead of the full "
            "envelope; same dotted-path scheme as 'set'"
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
    p_view.set_defaults(func=cmd_view)

    p_list = sub.add_parser(
        "list",
        help="List persisted agent_contract_handoffs rows (read-only)",
    )
    p_list.add_argument(
        "--agent-id", "--agent", dest="agent_id", metavar="AGENT_ID", default=None,
        help=(
            "Filter by the exact persisted agent_id (normally the minted "
            "a<hex> handle). Dispatch agent type/name is not stored on legacy "
            "rows and cannot be inferred by this filter."
        ),
    )
    p_list.add_argument(
        "--state", dest="state", metavar="AGENT_STATE", default=None,
        help="Filter by agent_state (COMPLETE, DISPATCHED, BLOCKED, ...)",
    )
    p_list.add_argument(
        "--session", dest="session_id", metavar="SESSION_ID", default=None,
        help="Filter by session_id",
    )
    p_list.add_argument(
        "--contract-id", dest="contract_id", metavar="CONTRACT_ID", default=None,
        help="Filter by contract_id (the draft/dispatch idempotency key)",
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

    p_validate = sub.add_parser("validate", help="Validate the draft WITHOUT mutating it")
    _add_common_draft_arg(p_validate)
    _add_agent_scope_arg(p_validate)
    p_validate.add_argument("--json", action="store_true", help="JSON output")
    p_validate.set_defaults(func=cmd_validate)

    p_finalize = sub.add_parser(
        "finalize",
        help="Validate the draft as final and write it to the store (idempotent, exactly-once)",
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
    p_fill.add_argument(
        "--json",
        dest="json_patch",
        required=True,
        metavar="JSON",
        help="JSON object to deep-merge into the draft envelope",
    )
    _add_common_draft_arg(p_fill)
    _add_agent_scope_arg(p_fill)
    p_fill.set_defaults(func=cmd_fill)


def _contract_default(args) -> int:
    print("Usage: gaia contract SUBCOMMAND [options]")
    print("")
    print("  init [--agent-id AGENT_ID]  -- create a new draft; mints and prints the agent_id when omitted")
    print("  set FIELD VALUE           -- set a scalar field by dotted path")
    print("  add FIELD VALUE           -- append a value to a list field")
    print("  view [--field PATH]       -- print the draft envelope, or only a dotted-path subtree")
    print("  validate                  -- validate the draft without mutating it")
    print("  finalize                  -- validate as final + write the row (idempotent, exactly-once)")
    print("  fill --json JSON          -- batch-merge a JSON patch into the draft")
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
