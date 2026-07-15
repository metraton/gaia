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
    * ``agent_id`` (an explicit ``--agent-id``), when ``explicit`` is absent,
      scopes resolution to that agent's own drafts and returns its
      most-recently-modified one -- the per-agent "resume to my latest
      draft" convenience. Returns ``None`` when that agent has no draft.
    * When BOTH are omitted, resolution falls back to the most-recently-
      modified draft SYSTEM-WIDE -- but only when that is unambiguous. If
      every candidate belongs to the SAME agent (including the common case
      of exactly one draft total), the latest-mtime fallback is returned, as
      before. If candidates span 2+ DISTINCT agents, this is the exact
      cross-agent guess that must never happen silently (a caller with no
      ``--draft-id``/``--agent-id`` could otherwise land on another agent's
      draft): ``resolve_draft_id`` raises ``AmbiguousDraftError`` instead of
      picking one, naming every candidate so the caller can disambiguate.

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
        # raises AmbiguousDraftError when both are omitted and drafts from
        # 2+ distinct agents exist
    AmbiguousDraftError                          # raised by resolve_draft_id
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


class AmbiguousDraftError(Exception):
    """Raised by ``resolve_draft_id`` when neither ``explicit`` nor
    ``agent_id`` is given AND drafts from 2+ DISTINCT agents currently exist.

    This guards the exact security/UX bug the fallback used to have:
    ``list_draft_ids(agent_id=None)`` globs EVERY agent's drafts and the
    most-recently-modified one wins -- so a subcommand invoked with no
    ``--draft-id``/``--agent-id`` could silently operate on a DIFFERENT
    agent's draft, with no warning. When every candidate belongs to the SAME
    agent (including the common single-draft-system-wide case), resolution
    stays unambiguous and the latest-mtime fallback is preserved unchanged --
    this only fires on a genuine multi-agent tie, and it refuses to guess.
    """

    def __init__(self, candidates: List[str]):
        self.candidates = list(candidates)
        agents = sorted({_agent_of(c) for c in self.candidates})
        super().__init__(
            "Multiple agents have active contract drafts "
            f"({', '.join(agents)}); refusing to guess which one to "
            "operate on. Pass --draft-id <id> or --agent-id <agent_id> to "
            f"disambiguate. Candidate drafts: {', '.join(self.candidates)}"
        )


def _agent_of(draft_id: str) -> str:
    """Return the agent-id portion of a draft id (``{agent_id}.{token}``).

    ``agent_id`` itself never contains a ``.`` (format ``^a[0-9a-f]{5,}$``),
    so splitting on the FIRST dot reliably recovers it regardless of the
    token's own shape.
    """
    return draft_id.split(".", 1)[0]


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

    ``explicit`` (an explicit ``--draft-id``) always wins. Otherwise, when
    ``agent_id`` is given, resolution is scoped to that agent's own drafts
    (the per-agent "resume to my latest draft" convenience) and returns its
    most-recently-modified one, or ``None`` if it has none.

    When BOTH are omitted, resolution falls back to the most-recently-
    modified draft SYSTEM-WIDE -- but only when unambiguous. If every
    candidate belongs to the SAME agent (including the common case of
    exactly one draft total), that fallback is safe and is returned as
    before. If candidates span 2+ DISTINCT agents, guessing would risk
    silently operating on another agent's draft, so this raises
    ``AmbiguousDraftError`` (naming every candidate) instead of picking one.

    Returns ``None`` when nothing resolvable exists at all.
    """
    if explicit:
        return explicit
    if agent_id:
        ids = list_draft_ids(agent_id)
        return ids[0] if ids else None
    ids = list_draft_ids(None)
    if not ids:
        return None
    if len({_agent_of(i) for i in ids}) > 1:
        raise AmbiguousDraftError(ids)
    return ids[0]
