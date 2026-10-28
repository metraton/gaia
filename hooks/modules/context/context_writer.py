"""
ContextWriter: validates update_contracts clauses from agent output and applies them.

A specialist's clause is a proposal: :func:`process_update_contracts` checks its
shape, routes it to the project it is about, and writes nothing;
:func:`apply_proposal` is the orchestrator's write into project_context.

update_contracts format (array inside agent_contract_handoff)::

    "update_contracts": [
      { "contract": "<contract_name>", "payload": { ... } },
      ...
    ]

A payload is a DELTA, not a snapshot: :func:`apply_update` deep-merges it into
the stored section instead of replacing it, so keys the payload does not mention
survive the write (see :func:`_merge_section_payload` for the exact rules and
why). This is the semantics agents are instructed to expect
(``skills/agent-contract-handoff``) and the one the section's other writer
already honours: ``tools/scan/promote.py`` refreshes only its scan-owned keys
and preserves agent-owned enrichment. Both actors write disjoint keys of the
same section, so a full-section replace from either side destroys the other's
contribution. That is also why a payload must BE an object: any other type
carries no keys to merge and could only express the wholesale replace, so it is
refused at both seams -- skipped by
``contract_validator.parse_update_contracts`` on the way in, and refused by
:func:`apply_update` at the write itself.

Public API:
    - apply_update(update, agent_name, workspace, db_path, project, proposed_by) -> dict
    - process_update_contracts(contract_dict, task_info) -> dict
    - apply_proposal(clause, workspace, db_path, dry_run, project, proposed_by, proposer_agent) -> dict
    - proposed_entries(clause) -> (entries, note)
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 1. apply_update
# ============================================================================

def _get_db_path() -> Optional[Path]:
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return None


def _derive_workspace() -> str:
    try:
        from gaia.project import current as _project_current
        identity = _project_current()
        return identity if identity else "global"
    except Exception:
        return "global"


def _merge_section_payload(stored: Any, delta: dict) -> dict:
    """Deep-merge ``delta`` into ``stored`` and return the merged section.

    The rules, which ``skills/agent-contract-handoff`` states as the contract
    agents write against:

      * ADD       -- a key absent from ``stored`` is inserted.
      * MERGE     -- two dicts merge recursively, key by key.
      * OVERWRITE -- any other value type replaces the stored one.
      * NO-DELETE -- a key ``delta`` does not mention is left untouched.

    A LIST replaces the stored list wholesale rather than being unioned into it,
    matching ``bin/cli/contract.py::_deep_merge``. Unioning is tempting and
    wrong: project-context lists are lists of records (helm releases, apps), so
    a union leaves two contradictory entries for the same resource and makes the
    drift correction that motivated the write unexpressible. A writer restates
    the whole list; a writer that only knows one element puts it under a keyed
    dict instead.

    NO-DELETE is total, and the omission of a tombstone/sentinel is deliberate:
    every value in ``delta`` is a value to STORE, so nothing here spells "remove
    this key", and no other seam removes one either. A delta is computed from
    partial knowledge, so a sentinel would put a writer that merely did not
    OBSERVE a key one keystroke from erasing another actor's contribution -- the
    loss this merge exists to prevent, re-entered by the front door. The posture
    on a fact that stopped being true is to MARK it: ``promote._mark_missing``
    stamps ``MISSING_MARK_KEY`` and keeps the entry, and a mis-keyed row is
    re-keyed with ``superseded_by`` rather than hard-deleted. A targeted delete
    verb did exist once and was removed on exactly these grounds -- see the note
    in ``tests/test_writer_surgical_reconciliation.py``.
    """
    from gaia.store.project_context import merge_section_payload

    return merge_section_payload(stored, delta)


def merge_into_section(
    con: sqlite3.Connection,
    owner: dict,
    contract_name: str,
    delta: dict,
    now: str,
    agent_name: str,
    proposed_by: Optional[str],
) -> str:
    """Deep-merge ``delta`` into ``owner``'s section inside the caller's transaction; return the write mode."""
    from gaia.store.project_context import project_sections, write_section

    seen = project_sections(con, owner).get(contract_name)
    stored = None
    write_mode = "insert"
    if seen is not None:
        stored = seen["payload"] if isinstance(seen["payload"], dict) else None
        if stored is not None and contract_name == "project_identity":
            stored = {k: v for k, v in stored.items() if k in delta}
        write_mode = "merge" if stored is not None else "replace"
    merged_payload = _merge_section_payload(stored, delta)
    write_section(
        con, owner["identity"], contract_name, json.dumps(merged_payload), now,
        agent_name, proposed_by,
    )
    return write_mode


def apply_update(
    update: dict,
    agent_name: str,
    workspace: Optional[str] = None,
    db_path: Optional[Path] = None,
    project: Optional[str] = None,
    proposed_by: Optional[str] = None,
) -> dict:
    """Merge a validated update_contracts entry into the ``project_context`` row of its project.

    The project is the one the entry names, else ``project``, else -- for
    ``project_identity`` -- the single project its keys name (:func:`_route`);
    an entry that resolves to no project, or to several, is refused. The
    section a reader of that project saw is the merge base, so a first write
    never hides keys still read from ``project_context_contracts``.

    The payload is a DELTA: the stored section is read, deep-merged with the
    payload (:func:`_merge_section_payload`), and written back, so a partial
    write preserves the sibling keys it does not mention. Read and write happen
    inside one ``BEGIN IMMEDIATE`` transaction, since agents run concurrently and
    an interleaved read-modify-write would lose one of the two deltas.

    A payload that is not an object is REFUSED (``success`` False, no write),
    not persisted: it has no keys to merge, so the only thing it could express
    is a wholesale replacement of the section, discarding every entry it does
    not mention. ``contract_validator.parse_update_contracts`` already skips
    such an entry on the envelope path; this check is the one adjacent to the
    write, so a direct caller of this function cannot reach the destructive
    path either. Note the asymmetry with a non-object payload already IN the
    row: that one is replaced (``write_mode`` ``replace``), since
    a corrupt or non-object stored value has nothing worth preserving and
    ``project_context_history`` retains the prior value regardless.

    ``workspace`` defaults to gaia.project.current() and only disambiguates a
    project name several workspaces hold. ``agent_name`` and ``proposed_by``
    are recorded in ``project_context_history``.

    Returns an audit dict:
    ``{timestamp, agent, contract, workspace, project, success, error, write_mode}``,
    where ``write_mode`` is ``insert`` (no prior row), ``merge`` (deep-merged
    into the prior payload), or ``replace`` (the prior payload was corrupt or
    not an object, so it had no keys to preserve).
    """
    contract_name = update["contract"]
    payload = update["payload"]
    ws = workspace or _derive_workspace()
    now = datetime.now(timezone.utc).isoformat()

    audit = {
        "timestamp": now,
        "agent": agent_name,
        "contract": contract_name,
        "workspace": ws,
        "project": None,
        "success": False,
        "error": None,
        "write_mode": None,
    }

    resolved = db_path or _get_db_path()
    if resolved is None or not resolved.exists():
        audit["error"] = f"gaia.db not found (path={resolved})"
        logger.error("apply_update: gaia.db unavailable: %s", resolved)
        return audit

    if not isinstance(payload, dict):
        audit["error"] = (
            f"payload must be a JSON object, got {type(payload).__name__}; "
            "refusing to replace the stored section"
        )
        logger.error(
            "apply_update: refused a %s payload for contract '%s' from %s; "
            "only an object has keys to merge, so writing it would drop every "
            "entry the delta does not mention",
            type(payload).__name__, contract_name, agent_name,
        )
        return audit

    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        audit["error"] = f"payload is not JSON-serializable: {exc}"
        return audit

    con = None
    try:
        from gaia.store.writer import assert_schema_writable
        assert_schema_writable(resolved)
        con = sqlite3.connect(str(resolved), isolation_level=None)
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN IMMEDIATE")

        targets, refusals = _route(update, ws, project, con)
        if refusals or len(targets) != 1:
            con.execute("ROLLBACK")
            audit["error"] = "; ".join(refusals) or (
                f"update_contracts '{contract_name}' names {len(targets)} projects; "
                "write each project's keys separately"
            )
            return audit
        owner, entry = targets[0]
        audit["workspace"], audit["project"] = owner["workspace"], owner["identity"]

        write_mode = merge_into_section(
            con, owner, contract_name, entry["payload"], now, agent_name, proposed_by,
        )
        con.execute("COMMIT")

        audit["success"] = True
        audit["write_mode"] = write_mode
        logger.info(
            "Context updated by %s: project=%s contract=%s mode=%s",
            agent_name, owner["identity"], contract_name, write_mode,
        )
        return audit

    except sqlite3.Error as exc:
        audit["error"] = str(exc)
        logger.error(
            "Failed to merge contract '%s' for agent '%s': %s",
            contract_name, agent_name, exc,
        )
        if con is not None:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        return audit

    finally:
        if con is not None:
            con.close()


# ============================================================================
# 2. process_update_contracts (envelope path)
# ============================================================================

def _section_keys(contract_name: str, workspace: str, db_path: Optional[Path]) -> set:
    """Top-level keys of the section as ``workspace`` reads it, or an empty set when it holds none."""
    resolved = Path(db_path) if db_path else _get_db_path()
    if resolved is None or not resolved.exists():
        return set()
    try:
        from gaia.store.project_context import workspace_sections

        con = sqlite3.connect(str(resolved))
        try:
            section = workspace_sections(con, workspace).get(contract_name)
        finally:
            con.close()
    except sqlite3.Error:
        return set()
    stored = section["payload"] if section else None
    return set(stored) if isinstance(stored, dict) else set()


def _identity_key(con: sqlite3.Connection, owner: dict, key: str) -> str:
    """The spelling of ``key`` the project's ``project_identity`` already holds, else ``key``.

    Keeps a hyphenated proposal from forking a second entry beside the
    underscore slug ``tools/scan/promote.py`` writes.
    """
    from gaia.store.project_context import name_variants, project_sections

    held = (project_sections(con, owner).get("project_identity") or {}).get("payload")
    held = held if isinstance(held, dict) else {}
    return next((variant for variant in name_variants(key) if variant in held), key)


def _route(
    entry: dict,
    workspace: str,
    dispatch_project: Optional[str],
    con: sqlite3.Connection,
) -> tuple:
    """``([(project, entry)], [refusal])``: each part of ``entry`` under the project it is about.

    The project is the one the entry names (``project``), else, for
    ``project_identity``, the one each key names (a key's value may also name
    it by ``local_path``), else the dispatch project. Nothing is ever
    defaulted to ``workspace``: a section about a project that lands in the
    session's workspace is invisible to every reader of that project.
    """
    from gaia.store.project_context import resolve_project

    contract_name = entry["contract"]
    if entry.get("project"):
        owner = resolve_project(con, entry["project"], workspace)
        if owner is None:
            return [], [_unowned_error(contract_name, [str(entry["project"])])]
        return [(owner, entry)], []
    if contract_name != "project_identity":
        owner = resolve_project(con, dispatch_project, workspace)
        if owner is None:
            return [], [_no_project_error(contract_name, dispatch_project)]
        return [(owner, entry)], []
    by_owner: dict = {}
    unowned: list = []
    for key, value in entry["payload"].items():
        path = value.get("local_path") if isinstance(value, dict) else None
        owner = resolve_project(con, key, workspace) or resolve_project(con, path, workspace)
        if owner is None:
            unowned.append(key)
            continue
        slot = by_owner.setdefault(owner["identity"], (owner, {}))
        slot[1][_identity_key(con, owner, key)] = value
    routed = [
        (owner, {"contract": contract_name, "payload": payload})
        for owner, payload in by_owner.values()
    ]
    return routed, [_unowned_error(contract_name, unowned)] if unowned else []


def _route_proposal(entry: dict, workspace: str, dispatch_project: Optional[str], db_path) -> tuple:
    """:func:`_route` on a read-only connection; ``([], [refusal])`` when the DB is unreadable."""
    resolved = Path(db_path) if db_path else _get_db_path()
    try:
        con = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        try:
            return _route(entry, workspace, dispatch_project, con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return [], [f"update_contracts '{entry['contract']}' not routed: {exc}"]


def _unowned_error(contract_name: str, unowned: list) -> str:
    return (
        f"update_contracts '{contract_name}': no workspace owns {', '.join(unowned)}, "
        "so nothing was routed for it. Repair: key the entry by a project the "
        "substrate knows (see `gaia context project <name>`; underscore and "
        "hyphen spellings are both tried) and propose it again."
    )


def _no_project_error(contract_name: str, dispatch_project: Optional[str]) -> str:
    named = f"the dispatch project {dispatch_project!r} is unknown" if dispatch_project else (
        "the dispatch named no project"
    )
    return (
        f"update_contracts '{contract_name}': the entry names no project and {named}, "
        "so it was written nowhere. Repair: add \"project\": \"<name or identity>\" "
        "to the entry (see `gaia context project <name>`) and propose it again."
    )


def _normalize_clause(raw: Any) -> tuple:
    """``(array, note, error)`` for the raw ``update_contracts`` value.

    The clause is an array of ``{contract, payload}``. Two single-object
    spellings of the same thing are read as such rather than dropped: one
    ``{contract, payload}`` entry, and ``{section: payload}`` where every key is
    a section name and every value an object. Both are unambiguous (a payload
    is always an object, a section name always a string, and ``contract`` and
    ``payload`` are not section names), and normalizing loses nothing because a
    proposal is only recorded, not written. Anything else is an ``error``.
    """
    if not raw:
        return [], None, None
    if isinstance(raw, list):
        return raw, None, None
    if isinstance(raw, dict):
        if "contract" in raw and "payload" in raw:
            return [raw], "a single {contract, payload} object was read as a one-entry array", None
        if all(isinstance(k, str) and k and isinstance(v, dict) for k, v in raw.items()):
            return (
                [{"contract": k, "payload": v} for k, v in raw.items()],
                "a {section: payload} object was read as an array of {contract, payload}",
                None,
            )
    return [], None, (
        f"update_contracts not recorded: expected an array of {{contract, payload}}, got "
        f"{type(raw).__name__}. Repair: send [{{\"contract\": \"<section>\", "
        "\"payload\": {...}}]."
    )


def _parse_clause(raw: Any) -> tuple:
    """``(entries, note, errors)``: the well-formed entries of a raw clause and why others failed."""
    clause, note, error = _normalize_clause(raw)
    if error:
        return [], None, [error]
    try:
        from ..agents.contract_validator import parse_update_contracts

        entries = parse_update_contracts({"update_contracts": clause})
    except Exception as exc:
        logger.warning("parse_update_contracts failed: %s", exc)
        return [], note, [f"update_contracts not recorded: the clause could not be parsed ({exc})"]
    errors = []
    if len(entries) < len(clause):
        errors.append(
            f"update_contracts: {len(clause) - len(entries)} of {len(clause)} entries not "
            "recorded: each must be an object with 'contract' and an object 'payload'"
        )
    return entries, note, errors


def apply_proposal(
    clause: Any,
    workspace: str,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
    project: Optional[str] = None,
    proposed_by: Optional[str] = None,
    proposer_agent: Optional[str] = None,
) -> dict:
    """Write a recorded ``update_contracts`` proposal under the projects it is about.

    This is the orchestrator's write path: SubagentStop only records and
    announces a specialist's proposal (:func:`process_update_contracts`).
    ``project`` is the proposing turn's dispatch project; :func:`_route` says
    which project each part lands in, and a part with none is refused, never
    written to ``workspace``. ``proposed_by`` (the proposing contract id) and
    ``proposer_agent`` are recorded in history. ``evidence`` entries are
    skipped: they are inserted at SubagentStop. With ``dry_run`` nothing is
    written and ``applied`` lists the targets that would be.

    Returns ``{applied: [{contract, workspace, project}], errors: [str], note}``.
    """
    entries, note, errors = _parse_clause(clause)
    applied: list = []
    for entry in entries:
        if entry["contract"] == "evidence":
            continue
        routed, refusals = _route_proposal(entry, workspace, project, db_path)
        errors.extend(refusals)
        for owner, routed_entry in routed:
            target = {"contract": entry["contract"], "workspace": owner["workspace"],
                      "project": owner["identity"]}
            if dry_run:
                applied.append(target)
                continue
            audit = apply_update(
                dict(routed_entry, project=owner["identity"]),
                proposer_agent or "orchestrator", workspace=owner["workspace"],
                db_path=db_path, proposed_by=proposed_by,
            )
            if audit["success"]:
                applied.append(target)
            else:
                errors.append(
                    f"update_contracts entry '{audit['contract']}' for project "
                    f"'{owner['identity']}' not applied: {audit.get('error') or 'unknown error'}"
                )
    return {"applied": applied, "errors": errors, "note": note}


def proposed_entries(clause: Any) -> tuple:
    """``(entries, note)``: the well-formed project-context entries of a raw clause.

    Shape only, with no owner lookup and no write, so a reader of a stored
    envelope can count what awaits the orchestrator. ``evidence`` entries are
    excluded because SubagentStop inserts them rather than proposing them.
    """
    entries, note, _errors = _parse_clause(clause)
    return [entry for entry in entries if entry["contract"] != "evidence"], note


def process_update_contracts(
    contract_dict: dict,
    task_info: dict,
) -> dict:
    """Record the ``update_contracts`` clause of a parsed contract dict as proposals.

    A specialist only notifies: project-context entries are validated, routed
    to the workspace that owns their project, and listed under ``proposed``
    with nothing written; the orchestrator applies them (:func:`apply_proposal`).
    ``evidence`` entries are the exception and are still inserted here.
    Any specialist may propose any section; whether to accept is the
    orchestrator's decision, so ``can_write`` does not gate a proposal.

    Args:
        contract_dict: Parsed JSON contract dict (from parse_contract()).
        task_info: Task metadata (agent, db_path, cloud_scope, workspace, and
            project -- the born row's ``dispatch_project``).

    Returns:
        dict with keys:
            updated (bool)         -- True when an evidence entry was inserted
            contracts (list[str])  -- Names of inserted evidence rows
            proposed (list[dict])  -- ``{contract, workspace, project, keys}`` awaiting the orchestrator
            rejected (list[str])   -- Names of rejected contracts
            errors (list[str])     -- Why an entry or key was not recorded
            normalized (str)       -- Present when a single-object clause was reshaped
    """
    result = {
        "updated": False,
        "contracts": [],
        "proposed": [],
        "rejected": [],
        "errors": [],
    }

    if not isinstance(contract_dict, dict):
        return result

    entries, note, errors = _parse_clause(contract_dict.get("update_contracts"))
    result["errors"].extend(errors)
    if note:
        result["normalized"] = note

    if not entries:
        return result

    agent_name = task_info.get("agent", task_info.get("agent_type", "unknown"))
    db_path = task_info.get("db_path")
    workspace = task_info.get("workspace") or _derive_workspace()

    evidence_entries = []
    context_entries = []
    for entry in entries:
        if entry.get("contract") == "evidence":
            evidence_entries.append(entry)
        else:
            context_entries.append(entry)

    for entry in context_entries:
        routed, refusals = _route_proposal(entry, workspace, task_info.get("project"), db_path)
        result["errors"].extend(refusals)
        for owner, routed_entry in routed:
            result["proposed"].append({
                "contract": entry["contract"],
                "workspace": owner["workspace"],
                "project": owner["identity"],
                "keys": sorted(routed_entry["payload"]),
            })

    # Process evidence entries (fail-together per D8 -- hook-trusted path)
    if evidence_entries:
        _apply_evidence_entries(evidence_entries, workspace, db_path, agent_name, result)

    return result


def _validate_evidence_payload(payload: dict) -> list:
    """Validate an evidence clause payload at write time.

    Returns a list of error strings (empty = valid).  Mirrors the parse-time
    check in contract_validator.validate_evidence_update_contract_payload but
    lives in the writer layer so write-time rejections are reported consistently.
    """
    errors = []
    if not isinstance(payload, dict):
        errors.append("evidence payload must be an object/dict")
        return errors

    raw_brief_id = payload.get("brief_id")
    if raw_brief_id is None:
        errors.append("evidence payload missing required field: brief_id")
    else:
        try:
            int(raw_brief_id)
        except (TypeError, ValueError):
            errors.append(
                f"evidence payload brief_id must be an integer, got {type(raw_brief_id).__name__!r}"
            )

    ac_id = payload.get("ac_id")
    if not ac_id or not str(ac_id).strip():
        errors.append("evidence payload missing or empty required field: ac_id")

    _valid_types = frozenset({"text", "file", "command_output", "url", "screenshot"})
    ev_type = payload.get("type")
    if not ev_type:
        errors.append(
            f"evidence payload missing required field: type (must be one of {sorted(_valid_types)})"
        )
    elif ev_type not in _valid_types:
        errors.append(
            f"evidence payload type {ev_type!r} is invalid; must be one of {sorted(_valid_types)}"
        )

    has_text = payload.get("text") is not None
    has_artifact = payload.get("artifact_path") is not None
    if has_text and has_artifact:
        errors.append(
            "evidence payload fields 'text' and 'artifact_path' are mutually exclusive"
        )
    elif not has_text and not has_artifact:
        errors.append("evidence payload requires exactly one of 'text' or 'artifact_path'")

    # Reject an artifact_path that is not already under the canonical evidence
    # root (e.g. a repo working-tree file) instead of inserting it verbatim --
    # same guard bin/cli/ac.py and bin/cli/brief.py already apply.
    artifact_path = payload.get("artifact_path")
    if artifact_path is not None:
        import sys as _sys
        import pathlib as _pl
        _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        try:
            from gaia.evidence.fs import require_canonical_artifact_path
            require_canonical_artifact_path(str(artifact_path))
        except ValueError as exc:
            errors.append(
                f"evidence payload artifact_path {artifact_path!r} rejected: {exc}"
            )

    return errors


def _apply_evidence_entries(
    entries: list,
    workspace,
    db_path,
    agent_name: str,
    result: dict,
) -> None:
    """Insert evidence rows from update_contracts evidence clauses.

    Implements fail-together semantics (D8): if ANY entry has a validation
    error, NO rows are inserted for the batch.  Other contract types in the
    same update_contracts array are unaffected.

    Successful inserts are appended to result["contracts"] as "evidence:<id>"
    strings.  Failures are appended to result["rejected"] and result["errors"].
    """
    # Phase 1: validate ALL entries before touching the DB (fail-together)
    all_errors = []
    for i, entry in enumerate(entries):
        payload = entry.get("payload", {})
        errs = _validate_evidence_payload(payload)
        for err in errs:
            all_errors.append(f"evidence[{i}]: {err}")

    if all_errors:
        for entry in entries:
            result["rejected"].append(entry.get("contract", "evidence"))
        result["errors"].extend(all_errors)
        logger.warning(
            "_apply_evidence_entries: rejecting %d evidence entries due to validation errors: %s",
            len(entries),
            "; ".join(all_errors),
        )
        return

    # Phase 2: all valid -- insert each entry
    import sys as _sys
    import pathlib as _pl
    _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    try:
        from gaia.evidence.store import insert_evidence
    except ImportError as exc:
        err = f"gaia.evidence.store unavailable: {exc}"
        result["errors"].append(err)
        logger.error("_apply_evidence_entries: %s", err)
        return

    ws = workspace or _derive_workspace()

    for i, entry in enumerate(entries):
        payload = entry.get("payload", {})
        try:
            row = insert_evidence(
                ws,
                int(payload["brief_id"]),
                str(payload["ac_id"]),
                type=payload["type"],
                text=payload.get("text"),
                artifact_path=payload.get("artifact_path"),
                size_bytes=payload.get("size_bytes"),
                task_id=payload.get("task_id"),
                created_by_agent=payload.get("created_by_agent") or agent_name,
                db_path=db_path,
                bypass_dispatch_guard=True,
            )
            ev_ref = f"evidence:{row['id']}"
            result["contracts"].append(ev_ref)
            result["updated"] = True
            logger.info(
                "_apply_evidence_entries: inserted %s by %s (brief_id=%s ac_id=%s)",
                ev_ref, agent_name, payload["brief_id"], payload["ac_id"],
            )
        except Exception as exc:
            err = f"evidence[{i}] insert failed: {exc}"
            result["errors"].append(err)
            logger.error("_apply_evidence_entries: %s", err)
