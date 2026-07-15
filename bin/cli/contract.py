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
    init     --agent-id AGENT_ID [--draft-id ID]  Create a new draft
    set      FIELD VALUE          [--draft-id ID]  Set a scalar field (dotted path)
    add      FIELD VALUE          [--draft-id ID]  Append a value to a list field
    view                          [--draft-id ID]  Print the current draft envelope
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
    errors (including the enum text for an out-of-range plan_status) are
    printed to stderr, and the process exits non-zero -- never a crash.
    ``validate`` and ``finalize`` never mutate; they only report the verdict.

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
    ``--agent-id``. When BOTH are omitted AND drafts from 2+ DISTINCT agents
    currently exist, resolution refuses to guess: it raises
    ``gaia.contract.drafts.AmbiguousDraftError`` rather than silently picking
    the system-wide most-recently-modified draft (which could belong to a
    different agent than the one invoking the CLI). The CLI catches this and
    prints the candidate draft ids, exiting 1 -- the caller must then pass
    ``--draft-id`` or ``--agent-id``. A single draft system-wide, or several
    drafts all belonging to the SAME agent, still resolves via the
    latest-mtime fallback unchanged (no ambiguity, no cross-agent risk). All
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
    real check, not a smuggled-through no-op: plan_status defaults to
    IN_PROGRESS, pending_steps is present (empty list), next_action is a
    non-empty placeholder the agent overwrites via `set`/`add`, and
    evidence_report carries all seven required keys.
    """
    return {
        "agent_status": {
            "plan_status": "IN_PROGRESS",
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
            "No draft found. Run 'contract init --agent-id <id>' first.",
            as_json,
        )


def _print_ambiguous_draft_error(exc, as_json: bool) -> None:
    """Report an ``AmbiguousDraftError`` -- 2+ distinct agents have active
    drafts and neither ``--draft-id`` nor ``--agent-id`` was given, so
    resolution refuses to guess (see gaia.contract.drafts.resolve_draft_id).
    """
    candidates = list(getattr(exc, "candidates", []) or [])
    if as_json:
        print(json.dumps({
            "status": "error",
            "error": "ambiguous_draft",
            "message": str(exc),
            "candidates": candidates,
        }))
    else:
        print(f"Error: {exc}", file=sys.stderr)


def _write_if_valid(envelope: dict, draft_id: str, as_json: bool) -> int:
    """Validate-on-write core: persist ONLY when the full verdict is ok."""
    result = _validate_envelope(envelope)
    if not result.ok:
        _print_rejection(result, as_json=as_json)
        return 1
    _save_draft(draft_id, envelope)
    if as_json:
        print(json.dumps({"status": "ok", "draft_id": draft_id}))
    else:
        print(f"OK: draft {draft_id} updated and validated.")
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

def cmd_init(args) -> int:
    """Create a new draft envelope (validate-on-write)."""
    as_json = bool(getattr(args, "json", False))
    draft_id = getattr(args, "draft_id", None) or _mint_draft_id(args.agent_id)
    envelope = _initial_envelope(args.agent_id)
    return _write_if_valid(envelope, draft_id, as_json)


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
    return _write_if_valid(envelope, draft_id, as_json)


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
    return _write_if_valid(envelope, draft_id, as_json)


def cmd_view(args) -> int:
    """Print the current draft envelope (no mutation)."""
    draft_id, envelope, as_json = _load_target_draft(args)
    if envelope is None:
        return 1
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
    task_status = agent_status.get("plan_status")
    workspace = _resolve_finalize_workspace(getattr(args, "workspace", None))

    from gaia.store.writer import finalize_agent_contract_handoff

    try:
        outcome = finalize_agent_contract_handoff(
            contract_id=draft_id,
            agent_id=agent_id,
            workspace=workspace,
            task_status=task_status,
            raw_handoff_json=json.dumps(envelope),
            # session_id is deliberately omitted: the CLI/core never reads
            # CLAUDE_SESSION_ID or any harness-specific value (decisions #1,
            # #3) -- only the hook adapter (Claude-Code-specific) may supply
            # a session_id on its own write path.
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
    return _write_if_valid(envelope, draft_id, as_json)


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

    When ``--draft-id`` is omitted, ``--agent-id`` narrows the
    most-recently-touched fallback to a single agent's own drafts -- the
    per-agent, resume-aware addressing (decision #8) that lets a resumed
    agent find its latest draft without any harness session concept.
    """
    parser.add_argument(
        "--agent-id",
        dest="agent_id",
        metavar="AGENT_ID",
        default=None,
        help="Scope draft resolution to this agent's drafts (used only when --draft-id is omitted)",
    )


def _build_subcommands(sub) -> None:
    p_init = sub.add_parser("init", help="Create a new draft envelope (validate-on-write)")
    p_init.add_argument(
        "--agent-id",
        dest="agent_id",
        required=True,
        metavar="AGENT_ID",
        help="agent_status.agent_id value; must match ^a[0-9a-f]{5,}$",
    )
    _add_common_draft_arg(p_init)
    p_init.add_argument("--json", action="store_true", help="JSON output")
    p_init.set_defaults(func=cmd_init)

    p_set = sub.add_parser("set", help="Set a scalar field by dotted path (validate-on-write)")
    p_set.add_argument("field", metavar="FIELD", help="Dotted path, e.g. agent_status.plan_status")
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

    p_view = sub.add_parser("view", help="Print the current draft envelope")
    _add_common_draft_arg(p_view)
    _add_agent_scope_arg(p_view)
    p_view.set_defaults(func=cmd_view)

    p_validate = sub.add_parser("validate", help="Validate the draft WITHOUT mutating it")
    _add_common_draft_arg(p_validate)
    p_validate.add_argument("--json", action="store_true", help="JSON output")
    p_validate.set_defaults(func=cmd_validate)

    p_finalize = sub.add_parser(
        "finalize",
        help="Validate the draft as final and write it to the store (idempotent, exactly-once)",
    )
    _add_common_draft_arg(p_finalize)
    p_finalize.add_argument(
        "--workspace",
        dest="workspace",
        metavar="WORKSPACE",
        default=None,
        help="Workspace to record the row under (default: gaia.project.current() or 'me')",
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
    print("  init --agent-id AGENT_ID  -- create a new draft (validate-on-write)")
    print("  set FIELD VALUE           -- set a scalar field by dotted path")
    print("  add FIELD VALUE           -- append a value to a list field")
    print("  view                      -- print the current draft envelope")
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
