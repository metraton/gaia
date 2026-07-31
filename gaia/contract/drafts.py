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
      scopes resolution to that agent's own drafts. It resolves ONLY when that
      scope names exactly ONE live draft. Returns ``None`` when the agent has
      no draft at all, and raises ``AmbiguousDraftError`` when 2+ live drafts
      carry the handle -- an agent id is minted per turn with no uniqueness
      mechanism, so the handle is shared, and picking the most recent of
      several is a coin flip between unrelated turns (see "Why --agent-id
      refuses" below).
    * When BOTH are omitted, resolution falls back to the most-recently-
      modified LIVE draft SYSTEM-WIDE -- but only when that is unambiguous.
      If the live candidates all belong to the SAME agent (including the
      common case of exactly one), that fallback is returned. If they span 2+
      DISTINCT agents, this is the exact cross-agent guess that must never
      happen silently: ``resolve_draft_id`` raises ``AmbiguousDraftError``.

Liveness, and why it is the axis resolution turns on:
    Agent handles collide heavily in practice -- ``a7f3c1`` was observed on 64
    distinct drafts -- because the handle is minted per turn from a small hex
    space and agents reuse the same shapes. So "the most recent draft for this
    handle" was frequently a FINISHED draft from an unrelated turn, and every
    spent draft that was never swept counted as a rival candidate forever,
    which made bare resolution ambiguous permanently once a second agent had
    ever run. Both symptoms have one cause: history was being treated as
    candidacy. A draft whose contract id carries a TERMINAL
    ``agent_contract_handoffs`` row is SPENT -- its outcome is already durable
    in the DB -- and is therefore excluded from candidacy (``_prefer_live``).
    Identity minting is untouched; only what resolution considers changed.

Why ``--agent-id`` REFUSES rather than picks:
    Excluding spent drafts narrows candidacy but does not make an agent handle
    unique, and it never was: at the time, every agent minted its own id
    with no uniqueness mechanism anywhere, so collisions are structural rather
    than accidental (one handle observed on 44 files; ~147 agents against ~244
    live drafts). Two consecutive ``gaia contract fill --agent-id <handle>``
    calls therefore resolved to two DIFFERENT drafts -- the recency winner moved
    between the calls -- and the second wrote a COMPLETE plus a verification
    block onto a draft belonging to another agent's turn. Before liveness
    filtering ``--agent-id`` failed outright; after it, it succeeded by
    guessing. Making an unsafe operation usable is not an improvement, so
    resolution now refuses the guess: 2+ LIVE candidates under one handle raise
    ``AmbiguousDraftError`` naming ``--draft-id``, the only stable handle today.
    The refusal lives HERE, at the single resolution seam, so every caller that
    resolves by agent_id inherits it instead of each command re-deriving the
    rule. Deliberately NOT covered: minting (a separate surface), and the bare
    no-flags fallback, which still picks by recency among several live drafts
    of one agent.

Retention -- ``collectable_drafts(max_age_days, grace_hours)``:
    The single retention POLICY, returning a decision (with a per-draft
    ``reason``) rather than performing one, so the SessionStart GC hook and the
    ``gaia cleanup`` CLI share one criterion and a dry-run can show precisely
    what a real sweep would remove. Both consumers call THIS function; neither
    re-derives a criterion of its own, and both resolve the two thresholds
    through ``resolve_max_age_days`` / ``resolve_grace_hours`` here, so the
    preview and the sweep cannot disagree. Collectable iff SPENT past a grace
    window, or aged out entirely. A draft that is neither -- an in-flight turn,
    or one cut off mid-write -- is outside the selection, which keeps a
    recoverable draft safe by construction rather than by luck WITHIN the age
    window; the ``aged`` lane is DB-independent and does collect an unfinalized
    draft past ``max_age_days``. See ``collectable_drafts`` for why that bound
    is deliberate.

Public surface (stable for T6 resume-read, T7 finalize store-writer, T13
concurrency-isolation):
    drafts_dir() -> Path
    mint_agent_id() -> str                       # "a" + 16 hex, SSOT
    mint_draft_id(agent_id) -> str
    draft_path(draft_id) -> Path
    draft_exists(draft_id) -> bool
    save_draft(draft_id, envelope) -> None      # atomic
    load_draft(draft_id) -> dict | None
    list_draft_ids(agent_id=None) -> list[str]   # most-recent first
    resolve_draft_id(explicit=None, agent_id=None) -> str | None
        # raises AmbiguousDraftError when both are omitted and LIVE drafts
        # from 2+ distinct agents exist, OR when agent_id names 2+ LIVE drafts
    spent_draft_ids(candidates=None) -> set[str] # terminal-row drafts
    collectable_drafts(max_age_days=None, grace_hours=None) -> list[dict]
    resolve_max_age_days() -> int                # env-aware threshold, SSOT
    resolve_grace_hours() -> int                 # env-aware grace, SSOT
    AmbiguousDraftError                          # raised by resolve_draft_id
        # .candidates -- the FULL list; .agents -- distinct agent ids;
        # .agent_id -- the handle asked for (agent-scoped case) or None;
        # .code -- "ambiguous_agent_draft" vs "ambiguous_draft".
        # str() is BOUNDED (a short, copy-pasteable preview), never the full
        # enumeration: naming all 481 candidates produced a ~13 KB message.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

_DRAFT_SUFFIX = ".json"
# Random token width (hex chars). 12 hex chars = 48 bits of entropy -- far
# beyond any realistic number of concurrent drafts per agent, so two
# same-agent cycles minting in the same instant do not collide.
_TOKEN_HEX_BYTES = 6

# Agent-handle width (bytes -> 2x hex chars). 8 bytes = 16 hex digits, exactly
# the floor ``gaia.contract.validator.AGENT_ID_PATTERN_TEXT`` enforces and the
# shortest length measured to be collision-free across the observed corpus.
_AGENT_HEX_BYTES = 8

# Agent states that mean "this turn's outcome is already durably recorded".
# A draft whose contract_id carries a row in one of these states is SPENT: the
# authoritative agent_contract_handoffs row exists, so the file itself holds
# nothing that is not already in the DB. Deliberately EXCLUDES the non-terminal
# states (IN_PROGRESS, APPROVAL_REQUEST, NEEDS_VERIFICATION) -- those name a
# turn still in flight, whose draft is exactly what a resume or an orchestrator
# recovery reads.
_TERMINAL_ROW_STATES = frozenset({"COMPLETE", "BLOCKED", "NEEDS_INPUT"})

# Grace window applied on top of the spent check before a draft is collectable.
# A draft finalized moments ago is still the thing an orchestrator reads to
# relay the outcome, so being spent is necessary but NOT sufficient -- it must
# also have gone quiet. Hours, not days: the DB row is the durable copy.
DEFAULT_SPENT_GRACE_HOURS = 24

# Age after which a draft is collectable REGARDLESS of DB state -- the backstop
# for drafts that never finalized and never will (a turn cut before finalize,
# an abandoned init). Mirrors the historical age-only GC threshold.
DEFAULT_MAX_AGE_DAYS = 7

# Environment overrides for the two thresholds. They live HERE, next to the
# criterion they parameterize, because a threshold is part of the policy: a
# second reader of the same variable is a second policy the moment one of them
# drifts. Every consumer (the SessionStart GC hook, the `gaia cleanup` CLI)
# resolves them through the helpers below rather than reading os.environ itself.
MAX_AGE_DAYS_ENV = "GAIA_CONTRACT_DRAFTS_MAX_DAYS"
GRACE_HOURS_ENV = "GAIA_CONTRACT_DRAFTS_GRACE_HOURS"

# How many candidates an AmbiguousDraftError names in its human-readable
# message. The full list always remains on ``.candidates`` for programmatic
# callers; only the rendered text is bounded. Naming every candidate produced a
# ~13 KB error at 481 drafts, which no reader could act on.
_AMBIGUITY_PREVIEW_LIMIT = 5


class AmbiguousDraftError(Exception):
    """Raised by ``resolve_draft_id`` when the candidates it was given do not
    identify ONE draft, in either of two distinct situations.

    CROSS-AGENT (``agent_id`` is None) -- neither flag was given and live
    drafts from 2+ DISTINCT agents exist. ``list_draft_ids(agent_id=None)``
    globs EVERY agent's drafts and the most-recently-modified one wins, so a
    subcommand invoked bare could silently operate on a different agent's
    draft. When every candidate belongs to the same agent (including the
    single-draft-system-wide case), the latest-mtime fallback is preserved.

    AGENT-SCOPED (``agent_id`` is set) -- ``--agent-id`` was given and 2+ LIVE
    drafts carry that handle. Agent ids are minted per turn with no uniqueness
    mechanism, so one handle routinely spans unrelated turns; the recency
    winner moves between calls, which is how a COMPLETE was once written onto
    another agent's draft. The two situations get different messages because
    they have different remedies: the cross-agent one can be resolved by
    scoping with ``--agent-id``, the agent-scoped one only by ``--draft-id``.
    """

    def __init__(self, candidates: List[str], agent_id: Optional[str] = None):
        self.candidates = list(candidates)
        self.agents = sorted({_agent_of(c) for c in self.candidates})
        self.agent_id = agent_id
        self.code = "ambiguous_agent_draft" if agent_id else "ambiguous_draft"
        super().__init__(
            _render_ambiguity_message(self.candidates, self.agents, agent_id)
        )


def _draft_state(draft_id: str) -> str:
    """Best-effort ``agent_state`` of a draft, for the disambiguation preview.

    Purely cosmetic -- an unreadable or malformed draft yields "?" rather than
    failing the error path that is itself already reporting a problem.
    """
    envelope = load_draft(draft_id) or {}
    status = envelope.get("agent_status")
    if isinstance(status, dict):
        return str(status.get("agent_state") or "?")
    return "?"


def _draft_mtime(draft_id: str) -> float:
    try:
        return draft_path(draft_id).stat().st_mtime
    except OSError:
        return 0.0


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_ambiguity_message(
    candidates: List[str], agents: List[str], agent_id: Optional[str] = None
) -> str:
    """Render a BOUNDED, copy-pasteable disambiguation message.

    An error a reader cannot finish reading does not inform. Naming all 481
    candidates produced a ~13 KB wall; this names at most
    ``_AMBIGUITY_PREVIEW_LIMIT`` of them -- newest first, each rendered as the
    exact ``--draft-id`` argument that resolves it, with its timestamp and
    state (plus its agent in the cross-agent case) so the caller can recognize
    its own. The remainder is summarized as a count, and the complete list
    stays on ``.candidates`` for programmatic consumers, so bounding the text
    loses no data.

    ``agent_id`` selects the AGENT-SCOPED wording. The remedy differs by case
    and the message must not misdirect: telling a caller who already passed
    ``--agent-id`` to pass ``--agent-id`` sends them back through the flag that
    just failed, so that branch names ``--draft-id`` as the only handle that
    identifies one draft, and says why the handle they used cannot.
    """
    preview = candidates[:_AMBIGUITY_PREVIEW_LIMIT]
    if agent_id:
        lines = [
            f"Ambiguous contract draft: --agent-id {agent_id} matches "
            f"{len(candidates)} live drafts; refusing to guess which one is "
            f"yours.",
            "An agent_id is minted per turn and is NOT unique -- unrelated "
            "turns share the same handle, and the most-recent one changes "
            "between calls -- so --draft-id is the only stable handle today.",
            "Pass --draft-id <id>: the id 'gaia contract init' printed for "
            "THIS turn. Copy one below only if you recognize it as yours.",
        ]
    else:
        lines = [
            f"Ambiguous contract draft: {len(candidates)} live draft(s) across "
            f"{len(agents)} agents ({', '.join(agents[:6])}"
            f"{', ...' if len(agents) > 6 else ''}); refusing to guess which one "
            f"to operate on.",
            "Pass --draft-id <id> (copy one below), or --agent-id <agent_id> to "
            "scope to your own drafts.",
        ]
    for draft_id in preview:
        agent_col = "" if agent_id else f"agent={_agent_of(draft_id)}  "
        lines.append(
            f"  --draft-id {draft_id}   {agent_col}"
            f"{_iso(_draft_mtime(draft_id))}  {_draft_state(draft_id)}"
        )
    remaining = len(candidates) - len(preview)
    if remaining > 0:
        hint = (
            "Only the draft id you minted identifies yours."
            if agent_id
            else "Run 'gaia contract view --agent-id <agent_id>' to find yours."
        )
        lines.append(f"  ... and {remaining} more (newest first). {hint}")
    return "\n".join(lines)


def _resolve_env_int(name: str, default: int) -> int:
    """Read a non-negative integer threshold from the environment.

    Read on every call (never cached at import) so a monkeypatched env is
    honored. A missing, non-integer, or negative value falls back to ``default``
    -- a malformed override must never widen or disable retention silently.
    """
    raw = os.environ.get(name, "")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return default


def resolve_max_age_days() -> int:
    """The age threshold in effect, honoring ``GAIA_CONTRACT_DRAFTS_MAX_DAYS``."""
    return _resolve_env_int(MAX_AGE_DAYS_ENV, DEFAULT_MAX_AGE_DAYS)


def resolve_grace_hours() -> int:
    """The spent grace window in effect, honoring ``GAIA_CONTRACT_DRAFTS_GRACE_HOURS``."""
    return _resolve_env_int(GRACE_HOURS_ENV, DEFAULT_SPENT_GRACE_HOURS)


def _agent_of(draft_id: str) -> str:
    """Return the agent-id portion of a draft id (``{agent_id}.{token}``).

    ``agent_id`` itself never contains a ``.`` (format
    ``gaia.contract.validator.AGENT_ID_PATTERN_TEXT``), so splitting on the
    FIRST dot reliably recovers it regardless of the token's own shape.
    """
    return draft_id.split(".", 1)[0]


def _ro_db_connect():
    """Open a strictly read-only, NEVER-CREATE connection to gaia.db.

    Deliberately NOT ``gaia.store.reader._ro_connect``: that helper lazily
    BOOTSTRAPS the schema, which would materialize a database as a side effect
    of a resolver call or a GC dry-run. Both callers here must be able to run
    against a machine with no DB at all and simply learn nothing, so this uses
    sqlite's ``mode=ro`` URI, which fails cleanly when the file is absent.

    Returns None on ANY failure (absent DB, locked file, missing driver). Every
    caller treats None as "no evidence", which degrades to the pure mtime
    behavior that predates the DB-aware lane -- never to a deletion.
    """
    try:
        import sqlite3

        from gaia.paths import db_path

        path = db_path()
        if not Path(path).is_file():
            return None
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None


def spent_draft_ids(candidates: Optional[Iterable[str]] = None) -> Set[str]:
    """Return the subset of draft ids whose turn is already durably recorded.

    A draft is SPENT when ``agent_contract_handoffs`` holds a row for its
    contract id in a TERMINAL state (see ``_TERMINAL_ROW_STATES``). The row is
    the authoritative artifact; once it exists, the JSON file is a spent copy
    that no resume and no orchestrator recovery needs.

    Safety posture -- this function is only ever allowed to answer "yes, this
    one is already safe to consider": on ANY uncertainty (no DB, no table, a
    query error) it returns an EMPTY set, so callers conclude nothing is spent
    and fall back to age alone. Absence of evidence never becomes evidence of
    disposability.
    """
    con = _ro_db_connect()
    if con is None:
        return set()
    try:
        placeholders = ",".join("?" for _ in _TERMINAL_ROW_STATES)
        rows = con.execute(
            f"select contract_id from agent_contract_handoffs "  # noqa: S608 -- states are a fixed frozenset
            f"where contract_id is not null and agent_state in ({placeholders})",
            tuple(sorted(_TERMINAL_ROW_STATES)),
        ).fetchall()
    except Exception:
        return set()
    finally:
        try:
            con.close()
        except Exception:
            pass
    spent = {r[0] for r in rows if r and r[0]}
    if candidates is not None:
        spent &= set(candidates)
    return spent


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


def mint_agent_id() -> str:
    """Mint a fresh agent handle conforming to ``AGENT_ID_PATTERN_TEXT``.

    The SSOT for the handle's shape, shared by every minting site: the CLI's
    ``gaia contract init`` and the dispatch-side birth that stamps an identity
    onto the nascent handoff row before the agent runs. Keeping one function
    is what makes an id born at dispatch and an id minted by the CLI the SAME
    kind of value -- a dispatch-born row is adoptable by the CLI only while
    both agree on the format.
    """
    return "a" + secrets.token_hex(_AGENT_HEX_BYTES)


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

    ``explicit`` (an explicit ``--draft-id``) always wins.

    When ``agent_id`` is given, resolution is scoped to that agent's drafts and
    resolves ONLY if that scope is unambiguous. Three outcomes, deliberately
    distinct because they are different diagnoses: no draft at all -> ``None``
    (the caller reports "no draft, run init"); exactly one live draft -> that
    draft, the pre-existing behavior an uncolliding agent never sees change;
    2+ live drafts -> ``AmbiguousDraftError``, because an agent id is minted
    per turn without any uniqueness mechanism, so several unrelated turns share
    one handle and the recency winner moves between calls. Failing loudly is
    the safe direction here: the alternative is what actually happened once --
    a COMPLETE written onto another agent's draft.

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
        if not ids:
            return None
        live = _prefer_live(ids, drop_spent=True)
        if len(live) > 1:
            raise AmbiguousDraftError(live, agent_id=agent_id)
        # No live candidate left: every draft under this handle is spent, so
        # its outcome is already a terminal row and the DB writer refuses to
        # amend it. Preserving the latest-spent fallback keeps the read paths
        # that address a FINISHED draft (the M4 reconstruction of a lost fence)
        # working, and is the pool ``_prefer_live`` already ordered.
        return (live or ids)[0]

    ids = list_draft_ids(None)
    if not ids:
        return None

    # Narrow to LIVE drafts before judging ambiguity. A draft whose turn already
    # has a terminal row is a spent artifact that no caller means to address, so
    # counting it as a rival candidate manufactures ambiguity out of history --
    # which is precisely how an unswept directory made bare resolution fail
    # permanently. Only genuinely live drafts can conflict.
    live = _prefer_live(ids, drop_spent=True)
    pool = live or ids
    if len({_agent_of(i) for i in pool}) > 1:
        raise AmbiguousDraftError(pool)
    return pool[0]


def collectable_drafts(
    max_age_days: Optional[int] = None,
    grace_hours: Optional[int] = None,
    now: Optional[float] = None,
) -> List[Dict[str, object]]:
    """Select the drafts a GC sweep may delete, and say WHY for each.

    This is the retention POLICY in one place, returning a decision rather than
    performing one, so the SessionStart hook and the ``gaia cleanup`` CLI share
    a single criterion and a dry-run can show exactly what a real run would do.

    A draft is collectable under either of two independent rules:

    * ``spent``  -- its contract id carries a TERMINAL ``agent_contract_handoffs``
      row AND it has been untouched for ``grace_hours``. The DB row is the
      durable artifact; the file is a copy. The grace window matters because
      "finalized" and "no longer being read" are different moments: an
      orchestrator relaying a just-closed turn still reads the draft.
    * ``aged``   -- untouched for ``max_age_days`` regardless of DB state. The
      backstop for drafts that never finalized and never will.

    The rule that makes this safe by construction is the one that is ABSENT: a
    draft that is not spent and not aged is never returned, whatever its state.
    That is exactly the draft an agent was cut off mid-turn holding -- the case
    the orchestrator recovers from after a harness cut.

    The protection that grants is TIME-BOUNDED, not absolute, and the bound is
    deliberate. ``aged`` is evaluated FIRST and WITHOUT consulting the DB, so a
    draft with no terminal row -- an abandoned init, a turn cut before finalize
    -- IS collected once it passes ``max_age_days``. Read the two rules as one
    sentence: inside the age window, the absence of a terminal row is a full
    veto on collection; past it, nothing is exempt. The alternative (never
    collecting an unfinalized draft) is not a stronger guarantee but a leak with
    no reclaim path: nothing will ever finalize a draft whose turn is over, so
    those files accumulate forever -- 383 files were measured on one machine
    with 198 of them already unfinalized and past a day old. A 7-day floor sits
    orders of magnitude beyond any real recovery window, which is measured in
    minutes-to-hours (hence the 24h grace on the spent lane), so the backstop
    reclaims only drafts no reader was ever going to come back for.

    Because ``spent_draft_ids`` yields an empty set whenever the DB cannot be
    read, an unreadable substrate silently degrades this to the age-only rule.
    It can never widen the selection.

    Thresholds resolve, when not passed explicitly, through
    ``resolve_max_age_days`` / ``resolve_grace_hours`` -- so an env override
    reaches every consumer of this policy, not just the one that read it.
    """
    days = resolve_max_age_days() if max_age_days is None else max_age_days
    hours = resolve_grace_hours() if grace_hours is None else grace_hours
    current = time.time() if now is None else now

    ids = list_draft_ids(None)
    if not ids:
        return []

    age_cutoff = current - days * 86400
    grace_cutoff = current - hours * 3600
    spent = spent_draft_ids(ids)

    out: List[Dict[str, object]] = []
    for draft_id in ids:
        mtime = _draft_mtime(draft_id)
        if not mtime:
            continue
        if mtime < age_cutoff:
            reason = "aged"
        elif draft_id in spent and mtime < grace_cutoff:
            reason = "spent"
        else:
            continue
        out.append({
            "draft_id": draft_id,
            "agent_id": _agent_of(draft_id),
            "path": str(draft_path(draft_id)),
            "reason": reason,
            "mtime": mtime,
            "mtime_iso": _iso(mtime),
            "age_days": round((current - mtime) / 86400.0, 2),
        })
    return out


def _prefer_live(ids: List[str], drop_spent: bool = False) -> List[str]:
    """Order ``ids`` (already newest-first) so live drafts precede spent ones.

    Resolution by ``--agent-id`` is the case this exists for. Agent handles
    collide heavily in practice -- one handle was observed on 64 drafts -- so
    "the most recently modified draft for this handle" routinely named a
    FINISHED draft belonging to a different turn that merely reused the handle.
    Preferring a live draft makes the flag address the caller's own in-flight
    work instead of a stranger's history.

    With ``drop_spent`` the spent entries are removed rather than deprioritized;
    callers judging ambiguity use that form. Relative recency is preserved
    within each group, and when the DB yields nothing (``spent_draft_ids``
    returns an empty set) the input order is returned unchanged -- identical to
    the pure-mtime behavior this refines.
    """
    spent = spent_draft_ids(ids)
    if not spent:
        return list(ids)
    live = [i for i in ids if i not in spent]
    if drop_spent:
        return live
    return live + [i for i in ids if i in spent]
