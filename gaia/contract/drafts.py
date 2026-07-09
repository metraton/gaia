"""
gaia.contract.drafts -- per-agent, resume-aware contract-draft storage.

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli
(M2, task T5). This module owns the ADDRESSING and PERSISTENCE of the
by-value ``agent_contract_handoff`` drafts that ``bin/cli/contract.py``
builds up across several small CLI calls. It replaces T4's single-slot
``.current``-pointer scaffold with real per-agent keying that supports
multiple concurrent drafts.

Harness-agnostic by construction (decisions #1 and #3):
    The CLI mints its OWN contract id. NOTHING in this module reads
    ``CLAUDE_SESSION_ID`` or any other Claude-Code-specific environment
    variable, and it imports nothing under ``hooks/``. The only external
    dependency is ``gaia.paths`` (Gaia's OWN storage substrate) -- exactly
    the same harness-free dependency the layer-2 cross-check already relies
    on. The mapping "harness session -> contract id" is NOT this module's
    concern; it lives in the hook adapter (T6).

Contract id (draft id) minting -- ``mint_draft_id(agent_id)``:
    ``f"{agent_id}.{token}"`` where ``token`` is a fresh random hex string
    from ``secrets`` (never time-, pid-, or session-derived). Encoding the
    agent id makes a draft locatable per agent (glob ``{agent_id}.*.json``)
    while the random token guarantees two concurrent cycles of the SAME
    agent never collide on a filename -- the per-agent multi-draft property
    T13's concurrency AC (AC-14) depends on.

Storage layout:
    ``gaia.paths.data_dir()/contract_drafts/<draft_id>.json`` -- under
    Gaia's own data substrate, OUTSIDE the harness's ``.claude/`` tree
    (AC-5). One file per draft; each draft is fully self-contained (the
    agent_id lives inside the envelope AND is encoded in the id), so there
    is NO shared mutable index or ``.current`` pointer that concurrent
    writers could clobber.

Concurrency / atomicity guarantees (for T13 / AC-14):
    * ``save_draft`` writes to a unique temp file in the same directory
      (``os.replace`` requires same-filesystem) and atomically renames it
      over the target. A reader therefore observes either the previous
      complete draft or the new complete draft -- never a half-written
      file, and never bytes from a different draft.
    * Distinct drafts occupy distinct paths keyed by a unique id, so two
      concurrent init/set/finalize cycles never contaminate each other:
      there is no last-writer-wins shared slot.
    * Resolution when ``--draft-id`` is omitted reads the directory listing
      at call time (no cached pointer), so it can never dangle to a draft
      another cycle deleted or superseded.

Resolution / addressing -- ``resolve_draft_id(explicit, agent_id)``:
    * ``explicit`` (an explicit ``--draft-id``) always wins -- this is the
      concurrency-safe primary key each concurrent cycle carries, and the
      seam the hook adapter (T6) uses to re-address a resumed agent's draft.
    * Otherwise the most-recently-modified draft is returned, optionally
      scoped to a single agent via ``agent_id`` (the per-agent "resume to my
      latest draft" convenience). Returns ``None`` when nothing matches.

Public surface (stable for T6 resume-read, T7 finalize store-writer, T13
concurrency-isolation):
    drafts_dir() -> Path
    mint_draft_id(agent_id) -> str
    draft_path(draft_id) -> Path
    draft_exists(draft_id) -> bool
    save_draft(draft_id, envelope) -> None      # atomic
    load_draft(draft_id) -> dict | None
    list_draft_ids(agent_id=None) -> list[str]   # most-recent first
    resolve_draft_id(explicit=None, agent_id=None) -> str | None
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import List, Optional

_DRAFT_SUFFIX = ".json"
# Random token width (hex chars). 12 hex chars = 48 bits of entropy -- far
# beyond any realistic number of concurrent drafts per agent, so two
# same-agent cycles minting in the same instant do not collide.
_TOKEN_HEX_BYTES = 6


def drafts_dir() -> Path:
    """Directory holding contract drafts, under Gaia's own data substrate.

    Resolved lazily on every call (not cached at import) so tests that set
    ``GAIA_DATA_DIR`` via env/monkeypatch are honored -- matching
    ``gaia.paths.resolver``'s own no-caching contract.
    """
    from gaia.paths import data_dir

    d = data_dir() / "contract_drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mint_draft_id(agent_id: str) -> str:
    """Mint a fresh, harness-agnostic contract id for ``agent_id``.

    The id encodes the agent (for per-agent locatability) plus a random
    token (for concurrent-draft uniqueness). It is NEVER derived from any
    harness session identifier or environment variable.
    """
    return f"{agent_id}.{secrets.token_hex(_TOKEN_HEX_BYTES)}"


def draft_path(draft_id: str) -> Path:
    return drafts_dir() / f"{draft_id}{_DRAFT_SUFFIX}"


def draft_exists(draft_id: str) -> bool:
    return draft_path(draft_id).is_file()


def save_draft(draft_id: str, envelope: dict) -> None:
    """Atomically persist ``envelope`` as the draft ``draft_id``.

    Writes to a unique temp file in the drafts directory, flushes+fsyncs it,
    then ``os.replace``s it over the target -- an atomic rename on POSIX so a
    concurrent reader never sees a partially-written or cross-contaminated
    file. The temp name carries the pid and a random suffix so two writers
    (even for the same draft id) never share a temp path.
    """
    directory = drafts_dir()
    target = directory / f"{draft_id}{_DRAFT_SUFFIX}"
    tmp = directory / f".{draft_id}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    data = json.dumps(envelope, indent=2, sort_keys=False)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        # Best-effort cleanup if the replace never happened (e.g. an error
        # between write and rename); the target is untouched in that case.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def load_draft(draft_id: str) -> Optional[dict]:
    """Return the persisted envelope for ``draft_id``, or None if missing /
    unreadable / corrupt."""
    path = draft_path(draft_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_draft_ids(agent_id: Optional[str] = None) -> List[str]:
    """Return draft ids, most-recently-modified first.

    When ``agent_id`` is given, only that agent's drafts (id prefix
    ``{agent_id}.``) are returned -- the per-agent scoping that lets a
    resumed agent find its own latest draft without a session concept.
    """
    directory = drafts_dir()
    pattern = f"{agent_id}.*{_DRAFT_SUFFIX}" if agent_id else f"*{_DRAFT_SUFFIX}"
    files = [p for p in directory.glob(pattern) if p.is_file()]
    # Sort by mtime descending; break ties by name for determinism.
    files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return [p.name[: -len(_DRAFT_SUFFIX)] for p in files]


def resolve_draft_id(
    explicit: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve which draft a subcommand should operate on.

    ``explicit`` (an explicit ``--draft-id``) always wins. Otherwise the
    most-recently-modified draft is returned, optionally scoped to
    ``agent_id``. Returns None when nothing resolvable exists.
    """
    if explicit:
        return explicit
    ids = list_draft_ids(agent_id)
    return ids[0] if ids else None
