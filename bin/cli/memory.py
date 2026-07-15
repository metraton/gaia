"""
gaia memory -- Inspect, query, and curate Gaia memory.

Read-only subcommands operate on the episodic log (the activity history
mirrored into ``episodes`` / ``episodes_fts`` in ``~/.gaia/gaia.db``) and
on curated memory rows:

  search <query> [--scope=memory|episodes|both] [--limit N] [--json]
                                          FTS5 search with hybrid scoring
  stats [--json]                         Episode count, index count, scores, conflicts
  show <name> [--json]                  Curated memory row by (project, name)
  episode-show <episode_id> [--json]    Full episode with metadata and score
  list [--type=...] [--workspace=<ws>]  Enumerate curated memory rows
  get-relevant [--workspace=<ws>]       Compact SessionStart injection block
  conflicts [--threshold F] [--json]    Pairwise contradiction scan (curated)

Mutating subcommands operate on the curated ``memory`` table in
``~/.gaia/gaia.db`` (project / user / feedback / atom / decision / negative
notes). Memory is AGGREGATED and RECLASSIFIED, not overwritten -- reach for
these verbs in this order:

  append <name> --body="..." | --body-file=<path>
                                          PRIMARY additive verb: grows an
                                          existing row's body (separator
                                          "\\n\\n"), prior body kept in
                                          memory_history. Non-mutative (T0,
                                          no approval) -- appending only adds.

  add --name=<slug> --type=<project|user|feedback|atom|decision|negative>
      --body="..." [--description=...] [--class=...] [--status=...]
      [--workspace=<ws>] [--project=<name> | --project-ref=<identity>] [--json]
                                          Creates/UPSERTs a NEW row (distinct
                                          from append, which grows one).
                                          DB-only writer; no filesystem side
                                          effects (no .md under
                                          ~/.claude/projects/.../memory/).
                                          Requires >=1 explicit scope flag
                                          (--project preferred, or --workspace);
                                          refuses to write with both empty.
                                          --project anchors memory.project_ref
                                          (N3, forward-only) by resolving a
                                          project name within --workspace to
                                          its stable project_identity; never
                                          guesses. Unresolvable/mismatched
                                          scope is a structured error (JSON:
                                          {"error","code"}) and writes no row.
                                          --workspace only => project_ref NULL,
                                          exit 0 (explicit degraded lane).

  reclassify <name> [--class=...] [--status=...] [--workspace=<ws>]
                                          Lifecycle transitions (open ->
                                          carry_forward -> graduated ->
                                          closed) without touching the body.
                                          Non-mutative (T0).

  edit --name=<slug> --field=<description|body>
       --content="..." | --body-file=<path> [--append] [--json]
                                          CORRECTION verb: overwrite/supersede
                                          a field when existing content is
                                          WRONG. Non-destructive under the
                                          hood (memory_history keeps the prior
                                          value) but changes what reads see,
                                          so it stays T3 (needs approval).
                                          Prefer append to add text.

  link <src> <dst> --kind=<relates_to|supersedes|derived_from|graduated_to>
      [--delete] [--workspace=<ws>]      Create/delete a memory_links edge.

  delete <name> [--hard] [--yes] [--json]
                                          DISCOURAGED BY CONVENTION: prefer
                                          reclassify (retire without losing
                                          history) or append (correct
                                          forward). Soft-delete (tombstone)
                                          by default, recoverable; --hard is
                                          irreversible. Stays T3 either way.
"""

from __future__ import annotations

# Repo-root import bootstrap so ``from gaia.store.writer import ...`` resolves
# regardless of cwd (the CLI is launched from many places).
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Return the Gaia instance root: the highest ancestor with a .claude/ dir.

    Walks upward from cwd to Path.home(), collects every directory that
    contains a .claude/ subdirectory, and returns the one closest to HOME
    (the top-most one).  This prevents a nested .claude/ in a sub-repository
    or dev checkout from shadowing the real Gaia instance.

    Falls back to INIT_CWD if set and no .claude/ ancestor is found.
    """
    import sys as _sys
    import os

    # Resolve via the shared helper (tools/memory/paths.py).
    # Build the import path so this works regardless of sys.path state.
    _tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
    if str(_tools_dir) not in _sys.path:
        _sys.path.insert(0, str(_tools_dir))

    try:
        from memory.paths import find_highest_claude_root
        root = find_highest_claude_root()
        if root is not None:
            return root
    except ImportError:
        pass

    # Fallback: honour INIT_CWD if the helper was unavailable or found nothing.
    init_cwd = os.environ.get("INIT_CWD")
    if init_cwd and (Path(init_cwd) / ".claude").is_dir():
        return Path(init_cwd)

    return Path.cwd()


def _memory_base(project_root: Path) -> Path:
    return project_root / ".claude" / "project-context" / "episodic-memory"


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_episodic():
    try:
        from tools.memory.episodic import EpisodicMemory
        return EpisodicMemory
    except ImportError:
        return None


def _import_scoring():
    try:
        from tools.memory.scoring import score_memory
        return score_memory
    except ImportError:
        return None


def _import_conflict_detector():
    try:
        from tools.memory.conflict_detector import detect_conflicts
        return detect_conflicts
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_old(timestamp_str: str) -> float:
    """Compute age in days from an ISO-8601 timestamp string."""
    if not timestamp_str:
        return 0.0
    try:
        ts = timestamp_str
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        recorded = datetime.fromisoformat(ts)
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - recorded
        return max(0.0, delta.total_seconds() / 86400.0)
    except (ValueError, AttributeError):
        return 0.0


def _load_index(project_root: Path) -> dict:
    """Load index.json from episodic memory dir. Returns empty index on failure."""
    index_path = _memory_base(project_root) / "index.json"
    if not index_path.is_file():
        return {"episodes": []}
    try:
        return json.loads(index_path.read_text())
    except Exception:
        return {"episodes": []}


def _err(msg: str, as_json: bool) -> int:
    """Print an error and return exit code 1."""
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _err_structured(msg: str, as_json: bool, *, code: str, **extra) -> int:
    """Machine-parseable error: ``_err`` plus a stable ``code`` a caller can
    branch on (and optional structured ``extra`` fields).

    Designed so an LLM (the orchestrator) that runs the command and reads the
    output can *manage* the failure deterministically rather than guess:

      * ``--json`` emits ``{"error": msg, "code": code, ...extra}`` -- parse
        ``code`` and route (retry workspace-only, ask the user, ...).
      * text mode prints ``Error [code]: msg`` to stderr, then each extra
        field on its own indented line.

    Exit code stays ``1`` -- the CLI's existing non-zero error convention --
    so callers that only check the exit code still see failure; ``code`` is
    the added discriminator for callers that read the payload.
    """
    if as_json:
        payload = {"error": msg, "code": code}
        payload.update(extra)
        print(json.dumps(payload))
    else:
        print(f"Error [{code}]: {msg}", file=sys.stderr)
        for k, v in extra.items():
            print(f"  {k}: {v}", file=sys.stderr)
    return 1


def _read_body_file(path_str: str) -> str:
    """Read body text from a file path or stdin.

    Pass ``"-"`` to read from ``sys.stdin`` until EOF (utf-8).
    Pass any other path string to read that file (utf-8).
    Raises ``FileNotFoundError`` for missing paths (caller converts to _err).
    """
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


def _is_rich_body(body: str) -> bool:
    """Return True when *body* contains markdown structure that loses meaning
    when collapsed to a 60-char fallback at SessionStart injection.

    Detects any of:
      - Fenced code blocks (``` or ~~~)
      - Markdown headers (lines starting with ``#``)
      - 3+ consecutive blank lines (multi-paragraph / complex structure)
    """
    import re as _re
    if _re.search(r"^```|^~~~", body, _re.MULTILINE):
        return True
    if _re.search(r"^#{1,6} ", body, _re.MULTILINE):
        return True
    if _re.search(r"\n{3,}", body):
        return True
    return False


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _query_episodes_from_db(workspace: str | None = None) -> list[dict]:
    """Query episodes from gaia.db. Returns list of episode dicts.

    Used by _cmd_stats, _cmd_episode_show, and _cmd_search_scoped (episodes scope).
    Falls back to empty list on any import/DB error (non-blocking).
    """
    try:
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return []

    ws = workspace or _project_current()
    try:
        con = _store_connect()
        try:
            if ws:
                rows = con.execute(
                    "SELECT * FROM episodes WHERE workspace = ? ORDER BY timestamp DESC",
                    (ws,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM episodes ORDER BY timestamp DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()
    except Exception:
        return []


def _count_episodes_from_db(workspace: str | None = None) -> int:
    """Return COUNT(*) from episodes table for the given workspace.

    Returns 0 on any error (non-blocking).
    """
    try:
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return 0

    ws = workspace or _project_current()
    try:
        con = _store_connect()
        try:
            if ws:
                row = con.execute(
                    "SELECT COUNT(*) FROM episodes WHERE workspace = ?", (ws,)
                ).fetchone()
            else:
                row = con.execute("SELECT COUNT(*) FROM episodes").fetchone()
            return row[0] if row else 0
        finally:
            con.close()
    except Exception:
        return 0


def _count_episodes_fts_from_db() -> int:
    """Return row count from episodes_fts table in gaia.db.

    Returns -1 when the DB/table is unreachable (sentinel for broken state).
    """
    try:
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from gaia.store.writer import _connect as _store_connect
    except ImportError:
        return -1

    try:
        con = _store_connect()
        try:
            row = con.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()
            return row[0] if row else 0
        finally:
            con.close()
    except Exception:
        return -1


def _search_episodes_fts_from_db(
    query: str,
    workspace: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """FTS5 search over episodes_fts in gaia.db.

    Thin wrapper around the canonical reader
    ``gaia.store.reader.search_episodes_fts`` (the single implementation
    shared with the context injector). Resolves the default workspace here so
    the CLI keeps scoping results to the current project. Returns episode
    dicts enriched with an ``fts_rank`` field; empty list on any error.
    """
    try:
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from gaia.store.reader import search_episodes_fts as _search_fts
        from gaia.project import current as _project_current
    except ImportError:
        return []

    ws = workspace or _project_current()
    return _search_fts(query, workspace=ws, limit=limit)


def _cmd_stats(args) -> int:
    """Handle `gaia memory stats`.

    Reads episode count from episodes table in gaia.db (primary).
    Reads FTS5 indexed count from episodes_fts table in gaia.db.
    Legacy index.json is no longer consulted.
    """
    as_json = getattr(args, "json", False)

    score_memory = _import_scoring()
    detect_conflicts = _import_conflict_detector()

    warnings: list[str] = []

    # Episode count from DB (primary source -- T6 migration)
    total_episodes = _count_episodes_from_db()

    # FTS5 indexed count from episodes_fts in gaia.db
    indexed = _count_episodes_fts_from_db()
    if indexed < 0:
        warnings.append(
            "episodes_fts table not reachable in gaia.db — DB may be missing or corrupt. "
            "Run: gaia doctor"
        )

    # avg_score from a sample of episodes in DB
    avg_score = 0.0
    if score_memory is not None and total_episodes > 0:
        episodes_sample = _query_episodes_from_db()[:100]
        scores = []
        for ep in episodes_sample:
            try:
                days = _days_old(ep.get("timestamp", ""))
                rc = int(ep.get("retrieval_score", 0) or 0)
                scores.append(score_memory(days_old=days, retrieval_count=rc))
            except Exception:
                pass
        if scores:
            avg_score = round(sum(scores) / len(scores), 4)

    # Conflict count (curated memory conflicts -- unrelated to episodes)
    project_root = _find_project_root()
    conflicts_count = 0
    if detect_conflicts is not None:
        try:
            mem_dir = project_root / ".claude" / "projects" / "-home-jorge-ws-me" / "memory"
            if not mem_dir.is_dir():
                # Try the user memory default
                mem_dir = Path.home() / ".claude" / "projects" / "-home-jorge-ws-me" / "memory"
            raw_conflicts = detect_conflicts(memory_dir=mem_dir)
            conflicts_count = len(raw_conflicts)
        except Exception:
            conflicts_count = 0

    output = {
        "total_episodes": total_episodes,
        "indexed": indexed,
        "avg_score": avg_score,
        "conflicts": conflicts_count,
        "warnings": warnings,
    }

    if as_json:
        print(json.dumps(output, indent=2))
    else:
        indexed_display = "unknown" if indexed < 0 else str(indexed)
        print(f"\n  Memory Stats")
        print(f"  Total episodes : {total_episodes}")
        print(f"  FTS5 indexed   : {indexed_display}")
        print(f"  Avg score      : {avg_score:.4f}")
        print(f"  Conflicts      : {conflicts_count}")
        for w in warnings:
            print(f"  WARN: {w}", file=sys.stderr)
        print()

    return 0


def _get_episode_from_db(episode_id: str) -> dict | None:
    """Fetch a single episode row from episodes table in gaia.db by episode_id.

    Returns the row as a dict, or None if not found or on any error.
    """
    try:
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from gaia.store.writer import _connect as _store_connect
    except ImportError:
        return None

    try:
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            con.close()
    except Exception:
        return None


def _cmd_episode_show(args) -> int:
    """Handle `gaia memory episode-show <episode_id>`.

    Renamed from the legacy ``gaia memory show`` so that ``show`` can route
    to curated memory (``gaia memory show <name>``). The legacy episode
    inspector remains available under the explicit ``episode-show`` verb;
    pre-existing callers that import ``_cmd_show`` see an alias defined
    below for backward compatibility.

    T6 migration: reads from episodes table in gaia.db (no filesystem reads).
    """
    as_json = getattr(args, "json", False)
    episode_id = args.episode_id

    score_memory = _import_scoring()

    episode = _get_episode_from_db(episode_id)

    if episode is None:
        # Fallback to legacy EpisodicMemory for backward compatibility
        # (supports any episode_id that may not yet be in the DB)
        EpisodicMemory = _import_episodic()
        if EpisodicMemory is not None:
            try:
                mem = EpisodicMemory()
                episode = mem.get_episode(episode_id)
            except Exception as exc:
                return _err(f"Could not load episode: {exc}", as_json)

    if episode is None:
        return _err(f"Episode not found: {episode_id}", as_json)

    # Compute score
    days = _days_old(episode.get("timestamp", ""))
    retrieval_count = int(episode.get("retrieval_count", 0) or 0)
    if score_memory is not None:
        try:
            score = round(score_memory(days_old=days, retrieval_count=retrieval_count), 4)
        except Exception:
            score = 0.0
    else:
        score = 0.0

    # Parse tags from JSON string if stored as JSON
    raw_tags = episode.get("tags") or []
    if isinstance(raw_tags, str):
        try:
            raw_tags = json.loads(raw_tags)
        except Exception:
            raw_tags = []

    output = {
        "id": episode.get("episode_id") or episode.get("id") or episode_id,
        "title": episode.get("title") or "",
        "content": episode.get("enriched_prompt") or episode.get("prompt") or "",
        "score": score,
        "tags": raw_tags,
        "retrieval_count": retrieval_count,
        "age_days": round(days, 2),
    }

    if as_json:
        print(json.dumps(output, indent=2))
    else:
        print(f"\n  Episode: {output['id']}")
        print(f"  Title  : {output['title']}")
        print(f"  Score  : {output['score']}")
        print(f"  Age    : {output['age_days']} days")
        print(f"  Tags   : {', '.join(output['tags']) if output['tags'] else 'none'}")
        print(f"  Retrievals: {output['retrieval_count']}")
        print(f"\n  Content:\n  {output['content'][:500]}\n")

    return 0


def _cmd_conflicts(args) -> int:
    """Handle `gaia memory conflicts [--threshold F]`."""
    as_json = getattr(args, "json", False)
    threshold = getattr(args, "threshold", 0.3)

    detect_conflicts = _import_conflict_detector()

    if detect_conflicts is None:
        return _err("conflict_detector module not available", as_json)

    project_root = _find_project_root()

    try:
        # Use the default memory dir (same as detect_conflicts default)
        raw = detect_conflicts(threshold=threshold)
    except Exception as exc:
        return _err(f"Conflict detection failed: {exc}", as_json)

    # Normalize: similarity -> score, flatten conflicts list into reason string
    conflicts_out = []
    for item in raw:
        inner = item.get("conflicts", [])
        reason = "; ".join(c.get("reason", "") for c in inner) if inner else "high similarity"
        conflicts_out.append({
            "file_a": item.get("file_a", ""),
            "file_b": item.get("file_b", ""),
            "score": item.get("similarity", 0.0),  # similarity -> score
            "reason": reason,
        })

    output = {"conflicts": conflicts_out}

    if as_json:
        print(json.dumps(output, indent=2))
    else:
        if not conflicts_out:
            print("No conflicts detected.")
        else:
            print(f"\n  {len(conflicts_out)} conflict(s) found:\n")
            for c in conflicts_out:
                print(f"  [{c['score']:.4f}] {Path(c['file_a']).name} <-> {Path(c['file_b']).name}")
                print(f"    Reason: {c['reason']}\n")

    return 0


# ---------------------------------------------------------------------------
# Workspace resolution (shared with curated-memory writer below)
# ---------------------------------------------------------------------------

def _resolve_workspace(explicit: str | None) -> str:
    """Return the workspace identity, defaulting to ``gaia.project.current()``.

    Mirrors the resolver in ``bin/cli/brief.py`` so memory and brief subcommands
    behave identically. Falls back to ``"me"`` when no workspace can be
    inferred from the cwd.
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


# ---------------------------------------------------------------------------
# Subcommand handler: add (DB-only writer)
# ---------------------------------------------------------------------------
#
# T5 note: ``add`` and ``edit`` accept optional ``--class`` and ``--status``
# flags. After the primary upsert / edit completes, if either flag was
# supplied, the same ``reclassify_memory`` writer is invoked so the row's
# semantic role and lifecycle state land in a single CLI call. The CLI is
# the only surface that translates ``--status=null`` into the empty-string
# clear-sentinel that ``reclassify_memory`` expects.
# ---------------------------------------------------------------------------


def _normalize_status_flag(raw: str | None) -> tuple[bool, str | None]:
    """Translate a CLI --status value into the writer's contract.

    Returns ``(touches_column, value_for_writer)``:
      * raw is None        -> (False, None)   -- writer ignores the column.
      * raw == "null"      -> (True, "")      -- writer clears to NULL.
      * any other string   -> (True, raw)     -- writer enum-checks it.
    """
    if raw is None:
        return False, None
    if raw == "null":
        return True, ""
    return True, raw


def _cmd_add(args) -> int:
    """Handle ``gaia memory add --name=... --type=... --body=...``.

    DB-only: writes a row to the ``memory`` table in ``~/.gaia/gaia.db``.
    Does NOT create any file under ``~/.claude/projects/.../memory/`` -- the
    legacy filesystem layout is being retired and is read-only-for-humans.
    """
    as_json = getattr(args, "json", False)

    name = getattr(args, "name", None)
    mem_type = getattr(args, "type", None)
    body = getattr(args, "body", None)
    body_file = getattr(args, "body_file", None)
    description = getattr(args, "description", None)
    # Raw explicit flags (argparse default=None means "flag not provided").
    # The scope gate below inspects the RAW flag, not the resolved workspace,
    # so a defaulted workspace never satisfies the "at least one" requirement.
    workspace_flag = getattr(args, "workspace", None)
    class_flag = getattr(args, "class_", None)
    status_flag = getattr(args, "status", None)
    project_flag = getattr(args, "project", None)
    project_ref_flag = getattr(args, "project_ref", None)
    workspace = _resolve_workspace(workspace_flag)

    if not name:
        return _err("--name is required", as_json)
    if not mem_type:
        return _err("--type is required", as_json)
    if body_file is not None:
        try:
            body = _read_body_file(body_file)
        except FileNotFoundError:
            return _err(f"--body-file: file not found: {body_file}", as_json)
        except OSError as exc:
            return _err(f"--body-file: cannot read '{body_file}': {exc}", as_json)
    if not body:
        return _err("--body or --body-file is required", as_json)

    if _is_rich_body(body) and not description:
        return _err(
            "body contains markdown structure (code blocks/headers/multi-paragraph).\n"
            "--description is required for rich bodies -- it's what gets injected at SessionStart.\n"
            "Bodies without description fall back to body[:60] which destroys code-block semantics.",
            as_json,
        )

    # ------------------------------------------------------------------
    # Deterministic anchoring contract (no guessing, no silent fallback).
    # ------------------------------------------------------------------
    # At least ONE explicit scope flag must be given: --project (preferred),
    # --project-ref, or --workspace. Writing with none of them provided would
    # silently leave project_ref NULL purely for lack of input -- exactly the
    # "silent NULL by absence" this contract forbids. Scope inference from
    # natural language ("the century project") lives in the ORCHESTRATOR, not
    # in this function; the function only accepts explicit, resolvable scope.
    if project_flag is None and project_ref_flag is None and workspace_flag is None:
        return _err_structured(
            "no scope provided: pass at least one of --project (preferred) or "
            "--workspace. Refusing to write with project/workspace both empty "
            "(that would leave project_ref NULL by absence of input, not by "
            "intent). To anchor to a project use --project=<name>; for a "
            "workspace-scoped note use --workspace=<ws>.",
            as_json,
            code="missing_scope",
        )

    try:
        from gaia.store.writer import (
            upsert_memory, reclassify_memory, resolve_project_ref,
            project_workspaces, VALID_MEMORY_TYPES,
            normalize_initiative, initiative_from_project_ref,
        )
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    if mem_type not in VALID_MEMORY_TYPES:
        return _err(
            f"invalid type '{mem_type}'; must be one of {list(VALID_MEMORY_TYPES)}",
            as_json,
        )

    # N3: forward-only project_ref anchor.
    #   * --project resolves a project NAME within `workspace` to its stable
    #     projects.project_identity. Must resolve or it is a structured error
    #     -- never a silent NULL, never a guess.
    #   * --project-ref passes an already-known identity string directly.
    #   * --workspace only (no project flag) is the explicit degraded lane:
    #     a legitimate workspace-scoped note with project_ref = NULL, exit 0.
    #
    # When both --project and --workspace are given and the project does not
    # belong to that workspace, that is a MISMATCH -- reported with its own
    # structured code so the caller can tell it apart from a project that does
    # not exist at all.
    project_ref = None
    if project_flag is not None:
        try:
            project_ref = resolve_project_ref(workspace, project_flag)
        except ValueError as exc:
            msg = str(exc)
            if "project_identity" in msg:
                # Project exists in this workspace but has no identity yet.
                return _err_structured(
                    msg, as_json, code="project_no_identity",
                    project=project_flag, workspace=workspace,
                )
            # Not found in `workspace`. If --workspace was explicit and the
            # project exists under a DIFFERENT workspace, this is a mismatch;
            # otherwise the project simply does not exist anywhere.
            if workspace_flag is not None:
                other_ws = [w for w in project_workspaces(project_flag)
                            if w != workspace]
                if other_ws:
                    return _err_structured(
                        f"project {project_flag!r} does not belong to "
                        f"workspace {workspace!r}; --project and --workspace "
                        f"do not correspond. The project exists under "
                        f"{other_ws!r}.",
                        as_json, code="project_workspace_mismatch",
                        project=project_flag, workspace=workspace,
                        found_in=other_ws,
                    )
            return _err_structured(
                msg, as_json, code="project_unresolved",
                project=project_flag, workspace=workspace,
            )
    elif project_ref_flag is not None:
        project_ref = project_ref_flag
    # else: --workspace-only degraded lane -> project_ref stays None (exit 0).

    # v32: resolve the canonical initiative grouping key.
    #   * --initiative=<X> (explicit logical initiative) wins, normalized. It
    #     needs no git project -- this is the surface for initiatives that are
    #     NOT git repos (branchkinect, buildwiz, axisio, ...), which --project
    #     deliberately refuses (it never guesses an unknown project name).
    #   * else, when --project / --project-ref anchored a git project_ref, the
    #     key is the repo basename of that anchor (gaia, balance).
    #   * else None (workspace-only note): no initiative, never guessed.
    initiative_flag = getattr(args, "initiative", None)
    if initiative_flag is not None:
        initiative = normalize_initiative(initiative_flag)
    elif project_ref is not None:
        initiative = initiative_from_project_ref(project_ref)
    else:
        initiative = None

    try:
        res = upsert_memory(
            workspace,
            name,
            type=mem_type,
            body=body,
            description=description,
            project_ref=project_ref,
            initiative=initiative,
        )
    except ValueError as exc:
        return _err(str(exc), as_json)
    except PermissionError as exc:
        # Raised by writer._assert_dispatch_can_write_memory when the CLI is
        # invoked from a non-curator subagent dispatch. Propagate verbatim
        # so callers (and AC evidence) see the structural reason.
        return _err(str(exc), as_json)
    except Exception as exc:  # noqa: BLE001
        return _err(f"failed to upsert memory: {exc}", as_json)

    # T5: apply class/status if either flag was supplied. The reclassify
    # writer handles enum validation, the status-only-on-thread rule, and
    # the auto-clear-on-demotion semantics. If reclassify fails we surface
    # the message but the primary upsert has already landed -- not ideal
    # but acceptable for an interactive CLI surface; tests pin the
    # behaviour so callers know what to expect.
    reclassify_result = None
    status_touches, status_for_writer = _normalize_status_flag(status_flag)
    if class_flag is not None or status_touches:
        try:
            reclassify_result = reclassify_memory(
                workspace,
                name,
                class_=class_flag,
                status=status_for_writer,
            )
        except ValueError as exc:
            return _err(str(exc), as_json)
        except PermissionError as exc:
            return _err(str(exc), as_json)

    snippet = body.strip().replace("\n", " ")
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."

    if as_json:
        out = {
            "status": res.get("status"),
            "action": res.get("action"),
            "name": name,
            "type": mem_type,
            "description": description,
            "workspace": workspace,
            "body_preview": snippet,
            "updated_at": res.get("updated_at"),
        }
        if project_ref is not None:
            out["project_ref"] = project_ref
        if initiative is not None:
            out["initiative"] = initiative
        if reclassify_result is not None:
            out["class"] = reclassify_result["class"]
            out["memory_status"] = reclassify_result["memory_status"]
        print(json.dumps(out, indent=2))
    else:
        verb = "Updated" if res.get("action") == "updated" else "Created"
        print(f"{verb} memory '{name}' (type={mem_type}, workspace={workspace})")
        if description:
            print(f"  description: {description}")
        if project_ref is not None:
            print(f"  project_ref: {project_ref}")
        if initiative is not None:
            print(f"  initiative: {initiative}")
        print(f"  body: {snippet}")
        if reclassify_result is not None:
            print(
                f"  class={reclassify_result['class']}, "
                f"status={reclassify_result['memory_status']}"
            )
    return 0


# ---------------------------------------------------------------------------
# Subcommand handler: checkpoint (transactional session-close)
# ---------------------------------------------------------------------------
#
# `checkpoint` persists a whole session-reflection in ONE call and ONE DB
# transaction: the record anchor + N carry-forward threads + N derived_from
# links, all-or-nothing. It replaces the fragile `add`(anchor) + N x
# (`add`(thread) + `link`) sequence that session-reflection Step 6 used to
# prescribe (each of those was a separate connection/commit, so a mid-sequence
# failure left a half-written checkpoint). This handler is a THIN wrapper: it
# reads/parses the payload, applies the shared scope contract, then hands off
# to `writer.close_session_memory`, which owns the atomic transaction and the
# per-row validation. The verb is naturally non-mutative in NAME
# ("checkpoint"), so it never trips the mutative-verb classifier -- no entry in
# mutative_verbs.py, no approval prompt (T0), matching `add`/`append`/`reclassify`.
# ---------------------------------------------------------------------------


def _resolve_scope_contract(
    *,
    workspace: str,
    workspace_flag: str | None,
    project_flag: str | None,
    project_ref_flag: str | None,
    as_json: bool,
) -> tuple[str | None, int | None]:
    """Deterministic project/workspace anchoring contract (no guessing).

    Same contract and structured error codes as ``_cmd_add`` -- at least one
    explicit scope flag is required, ``--project`` resolves a name to its
    stable ``project_identity``, ``--workspace``-only is the degraded lane.

    Returns ``(project_ref, err)``:
      * ``err`` is ``None`` on success -- ``project_ref`` is the resolved
        identity, or ``None`` for the ``--workspace``-only degraded lane.
      * ``err`` is an already-emitted integer exit code (1) on failure, with
        the structured code (``missing_scope`` / ``project_unresolved`` /
        ``project_workspace_mismatch`` / ``project_no_identity``) already
        printed via ``_err_structured``.
    """
    if project_flag is None and project_ref_flag is None and workspace_flag is None:
        return None, _err_structured(
            "no scope provided: pass at least one of --project (preferred) or "
            "--workspace. Refusing to write with project/workspace both empty "
            "(that would leave project_ref NULL by absence of input, not by "
            "intent).",
            as_json,
            code="missing_scope",
        )

    from gaia.store.writer import resolve_project_ref, project_workspaces

    if project_flag is not None:
        try:
            return resolve_project_ref(workspace, project_flag), None
        except ValueError as exc:
            msg = str(exc)
            if "project_identity" in msg:
                return None, _err_structured(
                    msg, as_json, code="project_no_identity",
                    project=project_flag, workspace=workspace,
                )
            if workspace_flag is not None:
                other_ws = [w for w in project_workspaces(project_flag)
                            if w != workspace]
                if other_ws:
                    return None, _err_structured(
                        f"project {project_flag!r} does not belong to "
                        f"workspace {workspace!r}; --project and --workspace "
                        f"do not correspond. The project exists under "
                        f"{other_ws!r}.",
                        as_json, code="project_workspace_mismatch",
                        project=project_flag, workspace=workspace,
                        found_in=other_ws,
                    )
            return None, _err_structured(
                msg, as_json, code="project_unresolved",
                project=project_flag, workspace=workspace,
            )
    if project_ref_flag is not None:
        return project_ref_flag, None
    return None, None  # --workspace-only degraded lane


def _cmd_checkpoint(args) -> int:
    """Handle ``gaia memory checkpoint --file <payload.json|->``.

    Persists a session-close reflection (one record anchor + N carry-forward
    threads + N derived_from links) atomically via
    ``writer.close_session_memory``.
    """
    as_json = getattr(args, "json", False)

    file_arg = getattr(args, "file", None)
    if not file_arg:
        return _err("--file is required (path to a JSON payload, or '-' for stdin)", as_json)
    try:
        raw = _read_body_file(file_arg)
    except FileNotFoundError:
        return _err(f"--file: file not found: {file_arg}", as_json)
    except OSError as exc:
        return _err(f"--file: cannot read '{file_arg}': {exc}", as_json)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _err_structured(
            f"payload is not valid JSON: {exc}", as_json, code="bad_shape",
        )

    workspace_flag = getattr(args, "workspace", None)
    project_flag = getattr(args, "project", None)
    project_ref_flag = getattr(args, "project_ref", None)
    workspace = _resolve_workspace(workspace_flag)

    project_ref, scope_err = _resolve_scope_contract(
        workspace=workspace,
        workspace_flag=workspace_flag,
        project_flag=project_flag,
        project_ref_flag=project_ref_flag,
        as_json=as_json,
    )
    if scope_err is not None:
        return scope_err

    # Rich-body gate (same discipline as `add`): a record or pending whose body
    # carries markdown structure must ship a description, or SessionStart falls
    # back to body[:60] and destroys the structure. Checked here (CLI layer),
    # exactly where `_cmd_add` checks it -- before the writer is called, so a
    # rejection writes nothing.
    if isinstance(payload, dict):
        _resumen = payload.get("resumen")
        if isinstance(_resumen, dict):
            _rb = _resumen.get("body")
            if isinstance(_rb, str) and _is_rich_body(_rb) and not _resumen.get("description"):
                return _err(
                    "resumen body contains markdown structure "
                    "(code blocks/headers/multi-paragraph); a 'description' is "
                    "required for rich bodies.",
                    as_json,
                )
        _pend = payload.get("pendientes")
        if isinstance(_pend, list):
            for i, _p in enumerate(_pend):
                if not isinstance(_p, dict):
                    continue
                _pb = _p.get("body")
                if isinstance(_pb, str) and _is_rich_body(_pb) and not _p.get("description"):
                    return _err(
                        f"pendientes[{i}] body contains markdown structure; a "
                        f"'description' is required for rich bodies.",
                        as_json,
                    )

    try:
        from gaia.store.writer import (
            close_session_memory, MemorySessionPayloadError,
        )
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    try:
        res = close_session_memory(workspace, payload, project_ref=project_ref)
    except MemorySessionPayloadError as exc:
        return _err_structured(str(exc), as_json, code=exc.code)
    except PermissionError as exc:
        # writer._assert_dispatch_can_write_memory: non-curator dispatch.
        return _err(str(exc), as_json)
    except ValueError as exc:
        # Per-row semantic failure -- the checkpoint rolled back, no rows written.
        return _err(str(exc), as_json)
    except Exception as exc:  # noqa: BLE001
        return _err(f"failed to write checkpoint: {exc}", as_json)

    warnings = res.get("warnings") or []

    if as_json:
        out = {
            "status": res.get("status"),
            "workspace": workspace,
            "anchor": res.get("anchor"),
            "threads": res.get("threads"),
            "links": res.get("links"),
            "warnings": warnings,
            "updated_at": res.get("updated_at"),
        }
        if project_ref is not None:
            out["project_ref"] = project_ref
        print(json.dumps(out, indent=2))
    else:
        anchor = res.get("anchor") or {}
        threads = res.get("threads") or []
        print(
            f"Checkpoint saved to workspace={workspace}: "
            f"anchor '{anchor.get('name')}' ({anchor.get('action')}), "
            f"{len(threads)} carry-forward thread(s)"
        )
        if project_ref is not None:
            print(f"  project_ref: {project_ref}")
        for t in threads:
            print(f"  thread: {t.get('name')} ({t.get('action')})")
        for w in warnings:
            print(f"  WARNING: {w}", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# Subcommand handlers: curated memory list / show / delete / edit
# ---------------------------------------------------------------------------

def _cmd_list(args) -> int:
    """List curated memory rows (project / user / feedback)."""
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    type_filter = getattr(args, "type", None)
    fmt = getattr(args, "format", None) or "table"
    limit = getattr(args, "limit", None)
    if as_json:
        fmt = "json"

    try:
        from gaia.store.writer import list_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    rows = list_memory(workspace, type=type_filter)
    if limit is not None and limit > 0:
        rows = rows[:limit]

    if fmt == "count":
        print(len(rows))
        return 0
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
        return 0

    if not rows:
        print("(no curated memory)")
        return 0
    name_w = max(4, max(len(r["name"]) for r in rows))
    type_w = max(4, max(len(r["type"] or "") for r in rows))
    desc_w = max(
        11,
        max(min(len(r.get("description") or ""), 60) for r in rows),
    )
    print(f"{'NAME':<{name_w}}  {'TYPE':<{type_w}}  {'DESCRIPTION':<{desc_w}}")
    print("-" * (name_w + type_w + desc_w + 4))
    for r in rows:
        desc = (r.get("description") or "")[:desc_w]
        print(f"{r['name']:<{name_w}}  {(r['type'] or ''):<{type_w}}  "
              f"{desc:<{desc_w}}")
    return 0


# Backward-compat alias: existing tests / older callers reference
# ``memory_mod._cmd_show`` expecting the episode lookup behaviour. Newer
# CLI registration routes ``show`` to ``_cmd_curated_show`` instead.
_cmd_show = _cmd_episode_show


# ---------------------------------------------------------------------------
# Subcommand handler: get-relevant (curated memory for SessionStart injection)
# ---------------------------------------------------------------------------

# Default mix for the injected SessionStart block:
#   3 atoms + 3 decisions + 2 negatives = 8 items, bounded to ~800 chars.
# Tuned for orchestrator attention budget: each item is one short line, so
# eight lines is enough to anchor the session without dominating the prompt.
#
# Legacy (pre-v4) quota: kept for the `--types=...` opt-in mode where a caller
# explicitly asks for atom/decision/negative slicing. The default (v4) flow
# below selects by class/status instead.
_RELEVANT_DEFAULT_LIMIT = 8
_RELEVANT_DEFAULT_MAX_CHARS = 800
_RELEVANT_DEFAULT_TYPES = ("atom", "decision", "negative")
_RELEVANT_PER_TYPE_QUOTA = {
    "atom": 3,
    "decision": 3,
    "negative": 2,
}

# v4 class/status driven selection. carry_forward threads are injected first
# (user-explicit "carry me into the next session") but are now bounded by a
# recency sub-cap AND participate in char-budget trimming as the LAST resort,
# so max_chars is a genuinely HARD cap (see the char-budget block below).
# anchors and thread/open rows get bounded quotas; class=log never injects.
_RELEVANT_PER_CLASS_QUOTA = {
    "anchor": 4,
    # Raised from 2 -> 4 now that each item is length-capped (below): stale
    # open threads are the rows that need attention, so we can afford to show
    # more of them without blowing the budget (they trim first under pressure).
    "thread_open": 4,
}

# Recency sub-cap for the carry_forward section. Before this cap, carry_forward
# was unbounded and never trimmed, so a workspace with many carried threads blew
# the char budget by multiples (the "5.8x" audit finding). Rows beyond the cap
# are surfaced via the always-visible overflow footer (below), never dropped
# silently.
_RELEVANT_CARRY_FORWARD_CAP = 8

# Per-item description cap. Real descriptions run 140-700 chars; rendered
# verbatim they turn one section into the whole block. Truncate the rendered
# line to this many chars + an ellipsis; the full body stays recoverable via
# the pointer footer (`gaia memory show <slug>`).
_RELEVANT_ITEM_DESC_MAX = 150

# Overflow footer: emitted whenever any item was left out of the rendered block
# (dropped by the carry_forward sub-cap OR by char-budget trimming). Like the
# recoverable-pointer footer, its worst-case length is RESERVED from the budget
# before trimming so the footer is NEVER itself the line that gets dropped --
# overflow is never silent.
def _overflow_footer(n: int) -> str:
    return (
        f"\n... ({n} more item(s) not shown, use "
        f"`gaia memory search` to query)"
    )


# Reserve the worst-case footer width (allow up to a 4-digit count).
_OVERFLOW_FOOTER_RESERVE = len(_overflow_footer(9999)) + 1


def _project_tag(project_ref) -> str:
    """Derive a short project tag from a project_ref for per-bullet display.

    project_ref is stored as a filesystem path to the project's git dir
    (e.g. ``/home/jorge/ws/me/gaia/.git``) or an opaque identity
    (e.g. ``id/p1``). Reduce it to the trailing component: ``gaia``, ``p1``.
    Returns "" when there is nothing to tag.
    """
    if not project_ref:
        return ""
    ref = str(project_ref).strip().rstrip("/")
    if ref.endswith("/.git"):
        ref = ref[: -len("/.git")]
    elif ref.endswith(".git"):
        ref = ref[: -len(".git")].rstrip("/")
    tag = ref.rsplit("/", 1)[-1]
    return tag or ""

# P2a recoverable-pointer footer. Each injected line shows a slug + one-line
# description; the full body (the actionable detail) is recoverable on demand.
# State that explicitly so the orchestrator / a subagent fetches the depth
# instead of treating the one-liner as all there is. Its length is RESERVED
# from the char budget before trimming, so block + pointer respects max_chars.
_MEMORY_POINTER = (
    "> Detail of any item above is recoverable: "
    "`gaia memory show <slug>` (the slug is the name shown after `- `)."
)
_MEMORY_POINTER_RESERVE = len(_MEMORY_POINTER) + 2  # +2 for the "\n\n" join

# Section headers (T7). Coordinated with T6 (skills/memory/SKILL.md):
# the legacy "## Workspace Memory (<ws>)" block is retired in favor of
# three explicit user-facing sections. Empty sections drop their header;
# if all three are empty the whole block is empty.
_SECTION_HEADERS = {
    "carry_forward": "## Memory — For this session",
    "anchor":        "## Memory — About you / What I know",
    "thread_open":   "## Memory — Open threads",
}

# v32 transversal digest (initiative-grouped). The SessionStart injection no
# longer anchors to cwd: instead it emits a cross-project digest of LIVE
# PENDING work grouped by the canonical `memory.initiative` key, so the user
# sees "what is open, everywhere" the moment a session starts -- independent of
# which directory the session was launched from.
#
# "Pending vivo" is DELIBERATELY narrow: class='thread' AND status IN
# ('carry_forward','open'). Anchors (durable "about you" facts), logs, and
# resolved/snapshot threads are excluded by design -- the digest is a worklist,
# not a knowledge dump.
_DIGEST_HEADER = "## Memory — Pendientes vivos por proyecto"
# Top-K initiatives shown in the cross-project digest; the rest roll up into a
# single "+N proyectos más" overflow line.
_DIGEST_TOP_K = 10
# Per-item description cap inside the digest. Much tighter than the section
# renderer's 150: one short line per initiative keeps ~10 initiatives visible
# without any single project monopolising the block (the "5.8x monopoly" the
# old cwd-anchored, single-project block produced under the 800 cap).
_DIGEST_DESC_MAX = 60
# Budget for the digest. The old 800 cap truncated to a SINGLE project once a
# project carried several pending threads. With one short line per initiative
# (~90-110 chars) plus header + pointer, ~10 initiatives need ~1500 chars.
# session_manifest.build_workspace_memory_block passes --max-chars=1500 as the
# injection authority; this is the fallback when --max-chars is omitted.
_DIGEST_DEFAULT_MAX_CHARS = 1500
# Project-mode ("--initiative=X"): how many pending items of the one requested
# initiative to show before rolling the rest into an overflow footer.
_PROJECT_MODE_TOP_N = 5
# Bucket label for rows whose initiative IS NULL. Never re-derived from a slug
# -- a NULL initiative is its own explicit bucket, not a guess.
_OTHERS_BUCKET = "otros"


def _cmd_get_relevant(args) -> int:
    """Emit a compact Workspace Memory block for SessionStart injection.

    v32 dispatch (cwd-INDEPENDENT). The cwd no longer filters or prioritises
    anything -- "active project" anchoring was removed. Which renderer runs is
    decided purely by the flags:

      * ``--types=...``  -> legacy per-type flow (unchanged, back-compat).
      * ``--initiative=X`` -> PROJECT MODE: the top ``_PROJECT_MODE_TOP_N``
        live-pending threads of the ONE requested initiative, with overflow.
      * ``--sections=...`` -> SECTION renderer: the class/status sections
        (carry_forward / anchor / thread_open). This is the subagent-dispatch
        path (``--sections=anchor`` gives a dispatched subagent the durable
        "About you / What I know" anchors). cwd anchoring is gone here too.
      * (no flag) -> TRANSVERSAL DIGEST: a cross-project worklist grouped by
        the canonical ``memory.initiative`` key. This is the orchestrator's
        SessionStart view -- "what is open, everywhere", independent of the
        launch directory.

    Output is NEVER raised: a database error returns an empty payload.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    types_arg = getattr(args, "types", None)

    if types_arg:
        # Legacy type-based selection -- keep verbatim for back-compat.
        max_chars = int(
            getattr(args, "max_chars", None) or _RELEVANT_DEFAULT_MAX_CHARS
        )
        return _cmd_get_relevant_by_type(args, workspace, max_chars)

    initiative_arg = getattr(args, "initiative", None)
    sections_arg = getattr(args, "sections", None)

    if initiative_arg:
        return _render_project_mode(args, workspace, initiative_arg, as_json)
    if sections_arg:
        return _render_sections(args, workspace, as_json)
    return _render_digest(args, workspace, as_json)


def _render_sections(args, workspace: str, as_json: bool) -> int:
    """Class/status section renderer (carry_forward / anchor / thread_open).

    Reached only via an explicit ``--sections`` filter (the subagent-dispatch
    path). cwd anchoring has been removed: rows are workspace-scoped, never
    filtered or prioritised by the launch directory.

    Selection model:
      * carry_forward: class=thread, status=carry_forward, recency sub-cap.
      * anchor: class=anchor, identity anchors (type=user) pinned, quota 4.
      * thread_open: class=thread, status=open, STALEST first, quota 4.
      * class=log NEVER injects; supersedes-destination rows are excluded.
    """
    max_chars = int(getattr(args, "max_chars", None) or _RELEVANT_DEFAULT_MAX_CHARS)
    sections_arg = getattr(args, "sections", None)
    _all_sections = ("carry_forward", "anchor", "thread_open")
    if sections_arg:
        _requested = {
            s.strip() for s in str(sections_arg).split(",") if s.strip()
        }
        active_sections = tuple(s for s in _all_sections if s in _requested)
        if not active_sections:
            active_sections = _all_sections
    else:
        active_sections = _all_sections

    # Reserve room for the recoverable-pointer footer (appended after trimming)
    # so the final block + pointer respects the caller's char budget. Floor at
    # a small positive value so a pathologically tiny budget still renders.
    max_chars = max(80, max_chars - _MEMORY_POINTER_RESERVE)

    try:
        from gaia.store.writer import _connect, get_memory  # noqa: F401
    except ImportError:
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    # Pull class/status-aware rows via raw SQL (list_memory doesn't expose
    # those columns yet, and we need a supersedes-NOT-IN subquery).
    rows_by_section: dict[str, list[dict]] = {
        "carry_forward": [],
        "anchor": [],
        "thread_open": [],
    }
    try:
        con = _connect()
        try:
            # NOT IN subquery: exclude rows that are the destination of any
            # supersedes edge. A row A with an incoming `supersedes` from B
            # means "B replaces A" -- A drops out of the injection.
            base_select = (
                "SELECT name, type, description, updated_at, class, status, "
                "       project_ref "
                "FROM memory "
                "WHERE workspace = ? "
                # scan-v2 SV3: a soft-deleted (tombstoned) row must not be
                # injected into the SessionStart memory block.
                "  AND deleted_at IS NULL "
                "  AND name NOT IN ("
                "    SELECT dst_name FROM memory_links "
                "    WHERE workspace = ? AND kind = 'supersedes'"
                "  ) "
            )
            base_params: list = [workspace, workspace]

            # v32: cwd anchoring removed. Rows are workspace-scoped only; the
            # launch directory neither filters nor prioritises them. order_prefix
            # is kept as an empty string so the ORDER BY clauses below stay
            # unchanged in shape.
            order_prefix = ""

            def _section_params() -> list:
                return list(base_params)

            # Section 1: carry_forward -- fetch all by recency, cap applied
            # in Python below so the dropped count feeds the overflow footer.
            if "carry_forward" in active_sections:
                cur = con.execute(
                    base_select
                    + "  AND class = 'thread' AND status = 'carry_forward' "
                    + "ORDER BY " + order_prefix + "COALESCE(updated_at, '') DESC",
                    _section_params(),
                )
                rows_by_section["carry_forward"] = [dict(r) for r in cur.fetchall()]

            # Section 2: anchor.
            if "anchor" in active_sections:
                anchor_quota = _RELEVANT_PER_CLASS_QUOTA["anchor"]
                cur = con.execute(
                    base_select
                    + "  AND class = 'anchor' "
                    # Pin identity anchors (type=user) to the top so pure
                    # recency can never bury the user's own anchor, then most
                    # recent first within each group.
                    + "ORDER BY " + order_prefix
                    + "CASE WHEN type = 'user' THEN 0 ELSE 1 END, "
                    + "COALESCE(updated_at, '') DESC "
                    + f"LIMIT {anchor_quota}",
                    _section_params(),
                )
                rows_by_section["anchor"] = [dict(r) for r in cur.fetchall()]

            # Section 3: thread/open (excluding carry_forward).
            if "thread_open" in active_sections:
                thread_quota = _RELEVANT_PER_CLASS_QUOTA["thread_open"]
                cur = con.execute(
                    base_select
                    + "  AND class = 'thread' AND status = 'open' "
                    # Staleness first: oldest open thread ascends to the top --
                    # the one gone quiet longest is the one needing attention.
                    # NULL updated_at sorts last (treated as most recent) so a
                    # freshly-created row without a timestamp is not mistaken
                    # for the stalest.
                    + "ORDER BY " + order_prefix
                    + "CASE WHEN updated_at IS NULL THEN 1 ELSE 0 END, "
                    + "updated_at ASC "
                    + f"LIMIT {thread_quota}",
                    _section_params(),
                )
                rows_by_section["thread_open"] = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception:
        # Any DB error -> empty block, fail-safe SessionStart contract.
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    # carry_forward recency sub-cap: keep the newest N (rows arrive ordered by
    # updated_at DESC), remember how many were dropped so the overflow footer
    # can surface them. This is the first of two bounds on carry_forward (the
    # second is char-budget trimming, below).
    cf_dropped = 0
    _cf_rows = rows_by_section.get("carry_forward", [])
    if len(_cf_rows) > _RELEVANT_CARRY_FORWARD_CAP:
        cf_dropped = len(_cf_rows) - _RELEVANT_CARRY_FORWARD_CAP
        rows_by_section["carry_forward"] = _cf_rows[:_RELEVANT_CARRY_FORWARD_CAP]

    items_flat: list[dict] = []

    def _truncate_desc(text: str) -> str:
        """Cap a rendered description to _RELEVANT_ITEM_DESC_MAX + ellipsis."""
        text = text.replace("\n", " ").strip()
        if len(text) > _RELEVANT_ITEM_DESC_MAX:
            return text[:_RELEVANT_ITEM_DESC_MAX].rstrip() + "…"
        return text

    def _build_section(section_key: str) -> list[str]:
        sub = rows_by_section.get(section_key, [])
        if not sub:
            return []
        out = [_SECTION_HEADERS[section_key], ""]
        for r in sub:
            name = r.get("name") or ""
            desc = (r.get("description") or "").strip()
            if not desc:
                body = (r.get("body") or "").strip() if "body" in r else ""
                if not body:
                    try:
                        from gaia.store.writer import get_memory as _gm
                        full = _gm(workspace, name) or {}
                        body = (full.get("body") or "").strip().replace("\n", " ")
                    except Exception:
                        body = ""
                desc = body
            # Per-item cap: keep every rendered description to one bounded line.
            desc = _truncate_desc(desc)
            # Short project tag when the row is anchored to a project.
            tag = _project_tag(r.get("project_ref"))
            prefix = f"- {name} [{tag}]" if tag else f"- {name}"
            line = f"{prefix}: {desc}" if desc else prefix
            out.append(line)
            items_flat.append({
                "name": name,
                "type": r.get("type"),
                "class": r.get("class"),
                "memory_status": r.get("status"),
                "section": section_key,
                "description": desc,
                "project_ref": r.get("project_ref"),
            })
        out.append("")  # blank line between sections
        return out

    # Order is fixed: carry_forward, anchor, thread_open. Empty sections
    # contribute nothing (no header).
    lines: list[str] = []
    lines.extend(_build_section("carry_forward"))
    lines.extend(_build_section("anchor"))
    lines.extend(_build_section("thread_open"))

    # Drop trailing blanks.
    while lines and lines[-1] == "":
        lines.pop()

    if not lines:
        # All sections empty -> no block.
        if as_json:
            print(json.dumps({
                "workspace": workspace, "items": [], "block": "",
            }))
        return 0

    block = "\n".join(lines)

    # Char budget: max_chars is a HARD cap. Trim order thread_open -> anchor ->
    # carry_forward (carry_forward last, so under normal loads it survives
    # whole). Whenever ANY item is left out -- the carry_forward sub-cap above
    # OR trimming here -- an overflow footer is appended. Its width is RESERVED
    # from the trim budget so it is never itself the dropped line: overflow is
    # never silent, and the cap is genuinely hard.
    def _trim_one(target_section: str) -> bool:
        """Remove one '- ' line from the named section. Return True on success."""
        in_section = False
        header = _SECTION_HEADERS[target_section]
        section_start = -1
        section_end = len(lines)
        for i, ln in enumerate(lines):
            if ln == header:
                section_start = i
                in_section = True
                continue
            if in_section and ln.startswith("## Memory"):
                section_end = i
                break
        if section_start < 0:
            return False
        # Find the LAST "- " line within the section span.
        for j in range(section_end - 1, section_start, -1):
            if lines[j].startswith("- "):
                lines.pop(j)
                # If section is now empty (only header + blank), drop
                # header + blank line too.
                body_remains = any(
                    lines[k].startswith("- ")
                    for k in range(section_start, min(section_end - 1, len(lines)))
                )
                if not body_remains:
                    # Remove header and (possible) following blank.
                    # Defensive bounds checking.
                    nxt = section_start + 1
                    if nxt < len(lines) and lines[nxt] == "":
                        lines.pop(nxt)
                    lines.pop(section_start)
                return True
        return False

    # A footer is needed if the sub-cap already dropped carry_forward rows, or
    # if the rendered block overflows the content budget (forcing a trim).
    # Reserve the footer's width from the trim budget in either case so
    # block + footer + pointer never exceeds the caller's max_chars.
    needs_footer = cf_dropped > 0 or len(block) > max_chars
    trim_budget = max_chars - (_OVERFLOW_FOOTER_RESERVE if needs_footer else 0)
    trim_budget = max(1, trim_budget)

    overflow_count = 0
    if len(block) > trim_budget:
        for trim_target in ("thread_open", "anchor", "carry_forward"):
            while len(block) > trim_budget and _trim_one(trim_target):
                overflow_count += 1
                while lines and lines[-1] == "":
                    lines.pop()
                block = "\n".join(lines)
            if len(block) <= trim_budget:
                break

    total_dropped = cf_dropped + overflow_count
    if total_dropped > 0:
        # Space was reserved above, so this always fits under max_chars.
        block = block + _overflow_footer(total_dropped)

    # Recoverable-pointer guidance (P2a). Appended AFTER budget trimming so the
    # pointer is never the line that gets dropped; its length was reserved from
    # max_chars above, so block + pointer still respects the caller's budget.
    block = block + "\n\n" + _MEMORY_POINTER

    if as_json:
        payload = {
            "workspace": workspace,
            "items": items_flat,
            "block": block,
            "overflow": total_dropped,
            "carry_forward_dropped": cf_dropped,
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(block)
    return 0


# ---------------------------------------------------------------------------
# v32: initiative-grouped renderers (transversal digest + project mode)
# ---------------------------------------------------------------------------

# "Pending vivo" query: LIVE pending threads only. class='thread' AND status IN
# ('carry_forward','open) -- anchors, logs, and resolved/snapshot threads are
# excluded BY DESIGN (a worklist, not a knowledge dump). Soft-deleted and
# supersedes-destination rows are excluded exactly as the section renderer does.
_PENDING_VIVO_SELECT = (
    "SELECT name, type, description, updated_at, initiative, status "
    "FROM memory "
    "WHERE workspace = ? "
    "  AND deleted_at IS NULL "
    "  AND class = 'thread' "
    "  AND status IN ('carry_forward', 'open') "
    "  AND name NOT IN ("
    "    SELECT dst_name FROM memory_links "
    "    WHERE workspace = ? AND kind = 'supersedes'"
    "  ) "
)


def _digest_truncate(text: str, limit: int) -> str:
    """Collapse newlines and cap a description to ``limit`` chars + ellipsis."""
    text = (text or "").replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _bucket_key(initiative) -> str:
    """Map a raw ``initiative`` value to its digest bucket key.

    A NULL/empty initiative is its OWN explicit bucket ("otros"); it is NEVER
    re-derived from a slug or path. A present initiative is used verbatim (it
    was already normalised at write time by ``normalize_initiative``).
    """
    if initiative is None or str(initiative).strip() == "":
        return _OTHERS_BUCKET
    return str(initiative)


def _fetch_pending_vivo(workspace: str, extra_where: str = "",
                        extra_params=None) -> list:
    """Return live-pending thread rows for ``workspace``, freshest first.

    Never raises: any DB/import error yields an empty list so the SessionStart
    contract stays fail-safe.
    """
    try:
        from gaia.store.writer import _connect
    except ImportError:
        return []
    try:
        con = _connect()
        try:
            params = [workspace, workspace]
            if extra_params:
                params.extend(extra_params)
            cur = con.execute(
                _PENDING_VIVO_SELECT + extra_where
                + " ORDER BY COALESCE(updated_at, '') DESC",
                params,
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception:
        return []


def _render_digest(args, workspace: str, as_json: bool) -> int:
    """Transversal cross-project digest of live-pending work (v32 default).

    Groups every live-pending thread by its ``initiative`` key, shows the
    freshest pending item per initiative (top-1, title + short desc), orders
    initiatives by the recency of their freshest pending, and shows the top-K
    initiatives. Initiatives beyond K roll up into a single global overflow
    line; an initiative with more than one pending shows a per-initiative
    "+N más en <initiative>" hint. cwd is irrelevant -- the digest is the same
    from any launch directory.
    """
    max_chars = int(getattr(args, "max_chars", None) or _DIGEST_DEFAULT_MAX_CHARS)
    # Reserve the recoverable-pointer footer width up front.
    max_chars = max(80, max_chars - _MEMORY_POINTER_RESERVE)

    rows = _fetch_pending_vivo(workspace)

    # Group by initiative bucket. Rows arrive freshest-first, so bucket[0] is
    # always the freshest pending of that initiative.
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_bucket_key(r.get("initiative")), []).append(r)

    if not buckets:
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    # Order initiatives by recency of their freshest pending (DESC).
    ordered = sorted(
        buckets.items(),
        key=lambda kv: (kv[1][0].get("updated_at") or ""),
        reverse=True,
    )

    def _build(shown_buckets: list) -> tuple[str, list, int]:
        overflow_projects = len(ordered) - len(shown_buckets)
        lines = [_DIGEST_HEADER, ""]
        items: list[dict] = []
        for key, brows in shown_buckets:
            top = brows[0]
            name = top.get("name") or ""
            desc = _digest_truncate(top.get("description") or "", _DIGEST_DESC_MAX)
            line = f"- {name} [{key}]: {desc}" if desc else f"- {name} [{key}]"
            lines.append(line)
            items.append({
                "name": name,
                "type": top.get("type"),
                "initiative": key,
                "memory_status": top.get("status"),
                "section": "digest",
                "description": desc,
                "pending_count": len(brows),
            })
            extra = len(brows) - 1
            if extra > 0:
                lines.append(
                    f"  +{extra} más en {key} — pedime que profundice"
                )
        if overflow_projects > 0:
            lines.append("")
            lines.append(
                f"+{overflow_projects} proyectos más — pedime el detalle de alguno"
            )
        return "\n".join(lines), items, overflow_projects

    # Start with top-K; if the block overflows the budget, drop the LEAST fresh
    # shown initiative (they are already recency-ordered) and let it roll into
    # the global overflow line. This keeps the freshest work visible and stops
    # any single project from monopolising the block.
    shown = ordered[:_DIGEST_TOP_K]
    block, items_flat, overflow_projects = _build(shown)
    while len(block) > max_chars and len(shown) > 1:
        shown = shown[:-1]
        block, items_flat, overflow_projects = _build(shown)

    block = block + "\n\n" + _MEMORY_POINTER

    if as_json:
        print(json.dumps({
            "workspace": workspace,
            "items": items_flat,
            "block": block,
            "overflow_projects": overflow_projects,
        }, indent=2, default=str))
    else:
        print(block)
    return 0


def _render_project_mode(args, workspace: str, initiative_arg: str,
                         as_json: bool) -> int:
    """Project mode: live-pending work of ONE requested initiative.

    ``--initiative=X`` normalises X the SAME way the write side does
    (``normalize_initiative``), so the key matches what was stored. The
    special value "otros" targets the NULL-initiative bucket. Returns the
    top-N freshest pending items with an overflow footer.
    """
    max_chars = int(getattr(args, "max_chars", None) or _DIGEST_DEFAULT_MAX_CHARS)
    max_chars = max(80, max_chars - _MEMORY_POINTER_RESERVE)

    try:
        from gaia.store.writer import normalize_initiative
        key = normalize_initiative(initiative_arg)
    except Exception:
        key = (initiative_arg or "").strip().lower() or None

    if key == _OTHERS_BUCKET or key is None:
        # The NULL-initiative bucket.
        rows = _fetch_pending_vivo(workspace, "  AND initiative IS NULL ")
        label = _OTHERS_BUCKET
    else:
        rows = _fetch_pending_vivo(workspace, "  AND initiative = ? ", [key])
        label = key

    if not rows:
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    shown = rows[:_PROJECT_MODE_TOP_N]
    overflow = len(rows) - len(shown)

    header = f"## Memory — Pendientes de {label}"
    lines = [header, ""]
    items: list[dict] = []
    for r in shown:
        name = r.get("name") or ""
        desc = _digest_truncate(r.get("description") or "", _RELEVANT_ITEM_DESC_MAX)
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        items.append({
            "name": name,
            "type": r.get("type"),
            "initiative": label,
            "memory_status": r.get("status"),
            "section": "project",
            "description": desc,
        })
    if overflow > 0:
        lines.append("")
        lines.append(f"+{overflow} más en {label} — pedime que profundice")

    block = "\n".join(lines) + "\n\n" + _MEMORY_POINTER

    if as_json:
        print(json.dumps({
            "workspace": workspace,
            "items": items,
            "block": block,
            "overflow": overflow,
        }, indent=2, default=str))
    else:
        print(block)
    return 0


def _cmd_get_relevant_by_type(args, workspace: str, max_chars: int) -> int:
    """Legacy --types=... pathway (pre-v4 quota by atom/decision/negative).

    Kept verbatim from the original implementation so the existing test
    surface keeps passing. The v4 default path lives in _cmd_get_relevant.
    """
    as_json = getattr(args, "json", False)
    limit = int(getattr(args, "limit", None) or _RELEVANT_DEFAULT_LIMIT)
    types_arg = getattr(args, "types", None)
    types_list = tuple(
        t.strip() for t in types_arg.split(",") if t.strip()
    ) if types_arg else _RELEVANT_DEFAULT_TYPES

    try:
        from gaia.store.writer import list_memory, get_memory
    except ImportError:
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    grouped: dict[str, list[dict]] = {t: [] for t in types_list}
    for t in types_list:
        try:
            rows = list_memory(workspace, type=t)
        except Exception:
            rows = []
        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        grouped[t] = rows

    per_type_quota = dict(_RELEVANT_PER_TYPE_QUOTA)
    if set(types_list) != set(_RELEVANT_DEFAULT_TYPES):
        even = max(1, limit // max(1, len(types_list)))
        per_type_quota = {t: even for t in types_list}

    selected_per_type: dict[str, list[dict]] = {}
    total = 0
    for t in types_list:
        quota = per_type_quota.get(t, 0)
        take = grouped[t][:quota]
        selected_per_type[t] = take
        total += len(take)

    if total < limit:
        slack = limit - total
        leftovers: list[tuple[str, dict]] = []
        for t in types_list:
            quota = per_type_quota.get(t, 0)
            leftovers.extend((t, r) for r in grouped[t][quota:])
        leftovers.sort(key=lambda pair: pair[1].get("updated_at") or "",
                       reverse=True)
        for t, r in leftovers[:slack]:
            selected_per_type.setdefault(t, []).append(r)
            total += 1

    if total == 0:
        if as_json:
            print(json.dumps({
                "workspace": workspace, "items": [], "block": "",
            }))
        return 0

    type_label = {
        "atom": "Atoms",
        "decision": "Decisions",
        "negative": "Negative",
        "project": "Project",
        "user": "User",
        "feedback": "Feedback",
    }

    lines = [f"## Workspace Memory ({workspace})", ""]
    items_flat: list[dict] = []
    overflow_count = 0
    for t in types_list:
        sub = selected_per_type.get(t, [])
        if not sub:
            continue
        lines.append(f"{type_label.get(t, t.title())}:")
        for r in sub:
            name = r.get("name") or ""
            desc = (r.get("description") or "").strip()
            if not desc:
                full = get_memory(workspace, name) or {}
                body = (full.get("body") or "").strip().replace("\n", " ")
                desc = body[:60] + ("..." if len(body) > 60 else "")
            line = f"- {name}: {desc}" if desc else f"- {name}"
            lines.append(line)
            items_flat.append({
                "name": name, "type": t, "description": desc,
            })
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    block = "\n".join(lines)

    if len(block) > max_chars:
        rendered_items = sum(1 for ln in lines if ln.startswith("- "))
        kept = rendered_items
        while len(block) > max_chars and kept > 1:
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith("- "):
                    lines.pop(i)
                    overflow_count += 1
                    kept -= 1
                    if (i - 1 >= 0
                            and lines[i - 1].endswith(":")
                            and (i >= len(lines)
                                 or not lines[i].startswith("- "))):
                        lines.pop(i - 1)
                    break
            while lines and lines[-1] == "":
                lines.pop()
            block = "\n".join(lines)
        if overflow_count > 0:
            footer = (
                f"\n... ({overflow_count} more items, use "
                f"'gaia memory search' to query)"
            )
            if len(block) + len(footer) <= max_chars:
                block = block + footer

    if as_json:
        print(json.dumps({
            "workspace": workspace,
            "items": items_flat,
            "block": block,
            "overflow": overflow_count,
        }, indent=2, default=str))
    else:
        print(block)
    return 0


def _history_view(h: dict) -> dict:
    """Summarize one memory_history row for `show --history`.

    Reports WHICH fields changed and the body SIZE delta -- never the full
    bodies (those can be large; the timeline is a version index, not a dump).
    """
    fields: list[str] = []
    if (h.get("before_body") or "") != (h.get("after_body") or ""):
        fields.append("body")
    if (h.get("before_status") or None) != (h.get("after_status") or None):
        fields.append("status")
    if (h.get("before_type") or None) != (h.get("after_type") or None):
        fields.append("type")
    if (h.get("before_description") or None) != (h.get("after_description") or None):
        fields.append("description")
    if (h.get("before_workspace") or None) != (h.get("after_workspace") or None):
        fields.append("workspace")
    if (h.get("before_deleted_at") or None) != (h.get("after_deleted_at") or None):
        fields.append("deleted_at")
    body_delta = len(h.get("after_body") or "") - len(h.get("before_body") or "")
    return {
        "changed_at": h.get("changed_at"),
        "fields_changed": fields,
        "body_delta": body_delta,
        "status_from": h.get("before_status"),
        "status_to": h.get("after_status"),
    }


def _cmd_curated_show(args) -> int:
    """Print a single curated memory row.

    Distinguishes from the legacy ``episode-show`` flow by looking up the
    ``memory`` table directly (PK = ``(project, name)``).

    Primitives (read-only, T0):
      * default        -- the row, now including ``class`` and ``status``
                          (previously omitted; the writer's get_memory does not
                          project them, so we enrich here).
      * ``--links``    -- in/out ``memory_links`` edges (kind + created_at).
      * ``--history``  -- ``memory_history`` versions (changed_at, fields
                          changed, body size delta -- NOT full bodies).
    ``--links`` / ``--history`` are additive: either or both can be combined
    with ``--json`` to enrich the emitted payload.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    want_links = getattr(args, "links", False)
    want_history = getattr(args, "history", False)

    try:
        from gaia.store.writer import get_memory
        from gaia.store.reader import (
            get_memory_class_status, memory_links_for, memory_history_for,
        )
    except ImportError as exc:
        return _err(f"gaia.store not importable: {exc}", as_json)

    row = get_memory(workspace, name)
    if row is None:
        return _err(
            f"memory '{name}' not found in workspace '{workspace}'",
            as_json,
        )

    # Fill the class/status gap: get_memory projects neither column.
    cs = get_memory_class_status(workspace, name)
    row["class"] = cs["class"]
    row["status"] = cs["status"]

    links_payload = None
    if want_links:
        edges = memory_links_for(workspace, [name])
        links_payload = {
            "out": [e for e in edges if e["src_name"] == name],
            "in": [e for e in edges if e["dst_name"] == name],
        }

    history_payload = None
    if want_history:
        history_payload = [
            _history_view(h) for h in memory_history_for(workspace, [name])
        ]

    if as_json:
        payload = dict(row)
        if links_payload is not None:
            payload["links"] = links_payload
        if history_payload is not None:
            payload["history"] = history_payload
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(f"# {row['name']}  (type={row['type']})")
    if row.get("description"):
        print(f"# {row['description']}")
    print(f"# class: {row.get('class')}  status: {row.get('status')}")
    print(f"# updated_at: {row.get('updated_at')}")

    if links_payload is not None:
        print()
        print("## Links")
        if not links_payload["out"] and not links_payload["in"]:
            print("  (no links)")
        for e in links_payload["out"]:
            print(f"  out: {name} -[{e['kind']}]-> {e['dst_name']}"
                  f"  ({e.get('created_at')})")
        for e in links_payload["in"]:
            print(f"  in:  {e['src_name']} -[{e['kind']}]-> {name}"
                  f"  ({e.get('created_at')})")

    if history_payload is not None:
        print()
        print("## History")
        if not history_payload:
            print("  (no recorded history)")
        for hv in history_payload:
            fields = ", ".join(hv["fields_changed"]) or "(no tracked field)"
            delta = hv["body_delta"]
            sign = "+" if delta >= 0 else ""
            print(f"  {hv['changed_at']}  [{fields}]  body {sign}{delta} chars")

    if links_payload is None and history_payload is None:
        print()
        print(row["body"])
    return 0


def _cmd_delete(args) -> int:
    """Soft-delete (tombstone) a curated memory row -- scan-v2 SV3.

    DISCOURAGED BY CONVENTION: memory is meant to be AGGREGATED and
    RECLASSIFIED, not deleted. Prefer ``reclassify --status=graduated|closed``
    to retire a note without losing it, or ``append`` to correct-forward.

    By default this is a SOFT delete: the row's ``deleted_at`` is stamped so the
    row and its body survive (recoverable, invisible to reads). ``--hard``
    performs the irreversible physical DELETE, reserved for explicit human
    curation and strongly discouraged (it destroys history). Both paths keep
    the FTS5 mirror in sync via triggers. Stays T3 either way: delete reduces
    recoverability, the direction that needs consent.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    skip_confirm = getattr(args, "yes", False)
    hard = getattr(args, "hard", False)

    try:
        from gaia.store.writer import get_memory, delete_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    # For a hard delete we may target a row that is already tombstoned; reach it
    # via include_deleted so a soft-then-hard flow works.
    row = get_memory(workspace, name, include_deleted=hard)
    if row is None:
        return _err(
            f"memory '{name}' not found in workspace '{workspace}'",
            as_json,
        )

    if not skip_confirm:
        verb = "HARD-delete (irreversible)" if hard else "delete (tombstone)"
        prompt = f"{verb} memory '{name}' (type={row['type']})? [y/N] "
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            if as_json:
                print(json.dumps({"deleted": False, "name": name,
                                  "reason": "aborted by user"}))
            else:
                print(f"Aborted; memory '{name}' was not deleted.")
            return 0

    try:
        deleted = delete_memory(workspace, name, hard=hard)
    except PermissionError as exc:
        return _err(str(exc), as_json)
    if not deleted:
        return _err(
            f"memory '{name}' could not be deleted (already gone?)",
            as_json,
        )

    mode = "hard" if hard else "tombstone"
    if as_json:
        print(json.dumps({
            "deleted": True,
            "mode": mode,
            "name": name,
            "workspace": workspace,
            "previous_type": row["type"],
        }, indent=2, default=str))
    else:
        verb = "Hard-deleted" if hard else "Tombstoned"
        print(f"{verb} memory '{name}' (workspace={workspace!r}, "
              f"previous_type={row['type']!r})")
    return 0


def _cmd_edit(args) -> int:
    """CORRECT a single column of a curated memory row (supersede-with-history).

    ``edit`` is the CORRECTION verb: it overwrites a field to fix or reframe
    content that is already wrong. It is non-destructive under the hood -- the
    prior value is captured in ``memory_history`` by ``trg_memory_history`` --
    but the read surface then shows only the corrected value, which is why it
    is classified T3 (it changes what future reads see). To ADD text WITHOUT
    replacing the existing body, use ``gaia memory append`` instead: that is
    the primary additive verb and is non-mutative (T0). The ``--append`` flag
    here is retained for backward compatibility and delegates to the same
    writer path as ``append``.

    T5: also accepts ``--class`` and ``--status`` flags. When --field/--content
    are omitted but a class/status flag is supplied, the call functions as a
    pure reclassify -- useful for "I want to graduate this thread" style edits
    without re-typing the body.

    Also accepts ``--project`` / ``--project-ref`` to RE-ANCHOR an existing
    row's ``memory.project_ref`` without rewriting the body. This closes the
    gap where ``gaia memory add --project`` could only anchor at WRITE time:
    a row written with a NULL or wrong ``project_ref`` (e.g. because the cwd
    was a multi-project workspace root) can now be corrected in place. The
    resolution contract matches ``add`` -- ``--project`` resolves a name to a
    stable identity, ``--project-ref`` passes one directly, and an unknown
    project is a structured error, never a silent NULL.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = getattr(args, "name", None)
    field = getattr(args, "field", None)
    content = getattr(args, "content", None)
    body_file = getattr(args, "body_file", None)
    append = getattr(args, "append", False)
    class_flag = getattr(args, "class_", None)
    status_flag = getattr(args, "status", None)
    project_flag = getattr(args, "project", None)
    project_ref_flag = getattr(args, "project_ref", None)

    if not name:
        return _err("--name is required", as_json)

    if body_file is not None:
        try:
            content = _read_body_file(body_file)
        except FileNotFoundError:
            return _err(f"--body-file: file not found: {body_file}", as_json)
        except OSError as exc:
            return _err(f"--body-file: cannot read '{body_file}': {exc}", as_json)

    # Defensive gate: body edits with rich markdown require a prior description
    # to exist (or to be set via --field=description in the same call). Since
    # edit patches one field at a time, we only block when field=body + the
    # resolved content is rich. Callers that set --field=description first are
    # unaffected.
    if field == "body" and content and _is_rich_body(content):
        # Look up the existing row to check whether a description is already set.
        try:
            from gaia.store.writer import get_memory as _gm_check
            existing = _gm_check(_resolve_workspace(getattr(args, "workspace", None)), name)
            if existing and not (existing.get("description") or "").strip():
                return _err(
                    "body contains markdown structure (code blocks/headers/multi-paragraph).\n"
                    "--description is required for rich bodies -- it's what gets injected at SessionStart.\n"
                    "Bodies without description fall back to body[:60] which destroys code-block semantics.",
                    as_json,
                )
        except Exception:
            pass  # import failure -> skip gate rather than block the edit

    # On edit, --field/--content remain optional only when at least one
    # class/status flag is provided. The classic "patch a column" path still
    # requires both.
    status_touches, status_for_writer = _normalize_status_flag(status_flag)
    has_field_patch = field is not None and content not in (None, "")
    has_reclassify = class_flag is not None or status_touches
    has_reanchor = project_flag is not None or project_ref_flag is not None

    if not has_field_patch and not has_reclassify and not has_reanchor:
        return _err(
            "--field/--content, --class/--status, or --project/--project-ref "
            "is required", as_json,
        )

    try:
        from gaia.store.writer import update_memory_field, reclassify_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    # Re-anchor project_ref of an existing row. Resolve the project scope with
    # the SAME contract `gaia memory add` uses (`_resolve_scope_contract`),
    # scoped to the already-resolved workspace: --project resolves a name to
    # its stable identity, --project-ref passes an identity directly, and an
    # unresolvable project is a structured error (never a silent NULL).
    reanchor_result = None
    if has_reanchor:
        project_ref, scope_err = _resolve_scope_contract(
            workspace=workspace,
            workspace_flag=None,
            project_flag=project_flag,
            project_ref_flag=project_ref_flag,
            as_json=as_json,
        )
        if scope_err is not None:
            return scope_err
        try:
            from gaia.store.writer import reanchor_memory_project_ref
            reanchor_result = reanchor_memory_project_ref(
                workspace, name, project_ref,
            )
        except ValueError as exc:
            return _err(str(exc), as_json)
        except PermissionError as exc:
            return _err(str(exc), as_json)

    field_result = None
    if has_field_patch:
        try:
            field_result = update_memory_field(
                workspace, name, field, content, append=append,
            )
        except ValueError as exc:
            return _err(str(exc), as_json)
        except PermissionError as exc:
            return _err(str(exc), as_json)

    reclassify_result = None
    if has_reclassify:
        try:
            reclassify_result = reclassify_memory(
                workspace,
                name,
                class_=class_flag,
                status=status_for_writer,
            )
        except ValueError as exc:
            return _err(str(exc), as_json)
        except PermissionError as exc:
            return _err(str(exc), as_json)

    if as_json:
        payload = {
            "name": name,
            "workspace": workspace,
            "field_update": field_result,
            "reclassify": reclassify_result,
            "reanchor": reanchor_result,
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        if field_result is not None:
            print(
                f"Updated memory '{name}' field={field} "
                f"action={field_result['action']}"
            )
        if reclassify_result is not None:
            print(
                f"Reclassified '{name}': class={reclassify_result['class']}, "
                f"status={reclassify_result['memory_status']}"
            )
        if reanchor_result is not None:
            print(
                f"Re-anchored '{name}': project_ref "
                f"{reanchor_result['before_project_ref']!r} -> "
                f"{reanchor_result['after_project_ref']!r}"
            )
    return 0


# ---------------------------------------------------------------------------
# Subcommand handler: append (curated memory body growth -- the primary
# "add something" verb)
# ---------------------------------------------------------------------------
#
# Vocabulary decision (Option C): memory is AGGREGATED and RECLASSIFIED, not
# mutated. ``append`` is the primary verb for "add text to an existing note"
# (a carry-forward thread, a running log): it concatenates the new text onto
# the existing body (separator ``\n\n``) and NEVER overwrites. The prior body
# is preserved in ``memory_history`` by the ``trg_memory_history`` AFTER UPDATE
# trigger (fires ``WHEN OLD.body IS NOT NEW.body``), so no history is lost.
#
# SECURITY CLASSIFICATION -- append is NON-mutative (T0), by design:
#   ``append`` is deliberately ABSENT from ``MUTATIVE_VERBS`` in
#   hooks/modules/security/mutative_verbs.py, so ``gaia memory append`` is
#   classified READ_ONLY "by elimination" (the same mechanism that makes
#   ``gaia memory add`` and ``gaia memory reclassify`` non-T3). Growing a
#   record only ADDS capability/recoverability; per the security-tiers
#   direction principle, that never needs consent. No change to the classifier
#   was required -- the property falls out of the verb taxonomy. Contrast with
#   ``edit`` / ``delete``, which ARE in MUTATIVE_VERBS and stay T3.
# ---------------------------------------------------------------------------

def _cmd_append(args) -> int:
    """Append text to the ``body`` of an existing curated memory row.

    Additive and non-destructive: the new text is concatenated to the current
    body (separator ``\\n\\n``); the prior body survives in ``memory_history``
    via the ``trg_memory_history`` trigger. This is the primary verb for
    "sum something" to a carry-forward note or running thread. It routes
    through the SAME writer path as ``edit --append`` (update_memory_field with
    ``append=True``), so history preservation is identical.

    Classified NON-mutative (T0): ``append`` is not in MUTATIVE_VERBS, so it
    needs no T3 approval -- appending only grows the record.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    body = getattr(args, "body", None)
    body_file = getattr(args, "body_file", None)

    if body_file is not None:
        try:
            body = _read_body_file(body_file)
        except FileNotFoundError:
            return _err(f"--body-file: file not found: {body_file}", as_json)
        except OSError as exc:
            return _err(f"--body-file: cannot read '{body_file}': {exc}", as_json)

    if body is None or body == "":
        return _err(
            "--body or --body-file is required (the text to append)", as_json,
        )

    try:
        from gaia.store.writer import update_memory_field
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    try:
        result = update_memory_field(
            workspace, name, "body", body, append=True,
        )
    except ValueError as exc:
        return _err(str(exc), as_json)
    except PermissionError as exc:
        # MemoryWriteForbidden -- dispatch enforcement layer.
        return _err(str(exc), as_json)

    if as_json:
        print(json.dumps({
            "name": name,
            "workspace": workspace,
            "field": "body",
            "action": result["action"],
            "updated_at": result["updated_at"],
        }, indent=2, default=str))
    else:
        print(
            f"Appended to memory '{name}' body "
            f"(action={result['action']}, workspace={workspace})"
        )
    return 0


# ---------------------------------------------------------------------------
# Subcommand handler: reclassify (curated memory class/status update)
# ---------------------------------------------------------------------------

def _cmd_reclassify(args) -> int:
    """Handle ``gaia memory reclassify <slug> --class=... --status=...``.

    UX notes:
      * At least one of ``--class`` / ``--status`` must be supplied.
      * ``--status=null`` is the explicit-clear sentinel: it NULLs the
        status column. The writer auto-clears status when class moves from
        ``thread`` to ``anchor``/``log`` without an explicit status flag,
        so most callers will never need ``--status=null`` directly.
      * Status is enum-checked AND constrained to class=thread rows: if the
        resulting class is anchor or log and the resulting status is
        non-NULL, the call fails with a structural-reason message.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    class_flag = getattr(args, "class_", None)
    status_flag = getattr(args, "status", None)

    status_touches, status_for_writer = _normalize_status_flag(status_flag)

    if class_flag is None and not status_touches:
        return _err(
            "at least one of --class or --status is required",
            as_json,
        )

    try:
        from gaia.store.writer import reclassify_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    try:
        res = reclassify_memory(
            workspace,
            name,
            class_=class_flag,
            status=status_for_writer,
        )
    except ValueError as exc:
        return _err(str(exc), as_json)
    except PermissionError as exc:
        # MemoryWriteForbidden -- T3 enforcement layer.
        return _err(str(exc), as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(
            f"Reclassified {name}: class={res['class']}, "
            f"status={res['memory_status']} in workspace {workspace}"
        )
    return 0


# ---------------------------------------------------------------------------
# Subcommand handler: link (memory_links graph primitives)
# ---------------------------------------------------------------------------
#
# Brief: memory-model-refactor-class-status-links-structural-enforcement (T4).
#
# Duplicate-edge behavior: idempotent by default. Re-running
# ``gaia memory link a b --kind=relates_to`` does not error; it returns an
# action=noop so scripts can declaratively wire links without bookkeeping.
# Strict mode is reachable via the writer (`if_exists="error"`) but is not
# exposed on the CLI -- the CLI is the declarative surface.
# ---------------------------------------------------------------------------

def _cmd_link(args) -> int:
    """Handle ``gaia memory link <src> <dst> --kind=<k> [--delete]``."""
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    src_name = args.src_name
    dst_name = args.dst_name
    kind = args.kind
    do_delete = getattr(args, "delete", False)

    try:
        from gaia.store.writer import (
            insert_memory_link, delete_memory_link, VALID_MEMORY_LINK_KINDS,
        )
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    if kind not in VALID_MEMORY_LINK_KINDS:
        return _err(
            f"invalid --kind {kind!r}; must be one of "
            f"{list(VALID_MEMORY_LINK_KINDS)}",
            as_json,
        )

    try:
        if do_delete:
            res = delete_memory_link(workspace, src_name, dst_name, kind)
            verb = "Deleted" if res["action"] == "deleted" else "Skipped"
        else:
            res = insert_memory_link(workspace, src_name, dst_name, kind)
            verb = "Created" if res["action"] == "inserted" else "Skipped"
    except ValueError as exc:
        return _err(str(exc), as_json)
    except PermissionError as exc:
        # MemoryWriteForbidden -- structural enforcement layer (T3).
        return _err(str(exc), as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        # Arrow uses ASCII-friendly form; no unicode dependence.
        action_label = (
            "link" if res["action"] in ("inserted", "deleted") else "link (no-op)"
        )
        print(
            f"{verb} {action_label} {src_name} -[{kind}]-> {dst_name} "
            f"in workspace {workspace}"
        )
    return 0


# ---------------------------------------------------------------------------
# gaia memory search: --scope for memory/episodes/both
# ---------------------------------------------------------------------------

def _cmd_search_scoped(args) -> int:
    """Handle ``gaia memory search`` with a ``--scope`` selector.

    Valid scopes:
      * ``episodes`` -- FTS5 over ``episodes_fts`` in gaia.db (canonical
                        episodic index, via _search_episodes_fts_from_db).
      * ``memory``   -- FTS5 over the ``memory_fts`` mirror of the curated
                        ``memory`` table (preferred name).
      * ``both``     -- combined episodes + curated memory (default).

    Deprecated alias: ``curated`` is accepted as a synonym for ``memory``;
    a deprecation warning is emitted to stderr so callers can migrate.
    """
    scope = getattr(args, "scope", None) or "both"
    as_json = getattr(args, "json", False)
    query = args.query
    limit = getattr(args, "limit", 10)

    # Backward-compat: 'curated' was the original name for the curated-memory
    # scope. Accept it but warn -- the canonical name is 'memory' (mirrors the
    # surface / table name and aligns with the rest of the CLI).
    if scope == "curated":
        print(
            "Warning: --scope=curated is deprecated; use --scope=memory. "
            "Translating for this run.",
            file=sys.stderr,
        )
        scope = "memory"

    workspace = _resolve_workspace(getattr(args, "workspace", None))

    if scope == "episodes":
        # T6 migration: FTS5 over episodes_fts table in gaia.db
        hits = _search_episodes_fts_from_db(query, workspace=workspace, limit=limit)
        episodes_out = []
        for ep in hits:
            episodes_out.append({
                "id": ep.get("episode_id", ""),
                "title": ep.get("title") or "",
                "rank": float(ep.get("fts_rank", 0.0) or 0.0),
                "date": (ep.get("timestamp") or "")[:10],
                "snippet": (ep.get("enriched_prompt") or ep.get("prompt") or "")[:120],
            })
        if as_json:
            print(json.dumps({"scope": "episodes", "results": episodes_out},
                             indent=2, default=str))
        else:
            if not episodes_out:
                print("No episode results found.")
            else:
                for i, r in enumerate(episodes_out, 1):
                    print(f"\n{i}. [{r['rank']:.4f}] {r['title'] or r['id']}")
                    print(f"   Date: {r['date']}")
                    print(f"   {r['snippet']}")
        return 0

    # Curated-memory path (used by --scope=memory and --scope=both).
    try:
        from gaia.store.writer import search_memory_curated
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    curated = search_memory_curated(workspace, query, limit=limit)

    if scope == "memory":
        if as_json:
            print(json.dumps({"scope": "memory", "results": curated},
                             indent=2, default=str))
        else:
            if not curated:
                print("No curated matches.")
            else:
                for r in curated:
                    print(f"[{r['rank']:.4f}] {r['name']}  ({r['type']})")
                    if r.get("description"):
                        print(f"   {r['description']}")
                    if r.get("snippet"):
                        print(f"   {r['snippet']}")
        return 0

    # both: run episode FTS5 search (from gaia.db) + curated memory search
    episodes_out: list = []
    hits = _search_episodes_fts_from_db(query, workspace=workspace, limit=limit)
    for ep in hits:
        episodes_out.append({
            "id": ep.get("episode_id", ""),
            "title": ep.get("title") or "",
            "rank": float(ep.get("fts_rank", 0.0) or 0.0),
        })

    if as_json:
        print(json.dumps(
            {"scope": "both", "episodes": episodes_out, "curated": curated},
            indent=2, default=str,
        ))
    else:
        print(f"Episodes ({len(episodes_out)}):")
        for r in episodes_out:
            print(f"  [{r['rank']:.4f}] {r['title'] or r['id']}")
        print(f"Curated ({len(curated)}):")
        for r in curated:
            print(f"  [{r['rank']:.4f}] {r['name']}  ({r['type']})")
    return 0


# ---------------------------------------------------------------------------
# Dispatcher + registration
# ---------------------------------------------------------------------------

def cmd_memory(args) -> int:
    """Top-level dispatcher for `gaia memory <action>`."""
    func = getattr(args, "func", None)
    if func is None:
        # No subcommand given — print help via argparse
        if hasattr(args, "_memory_parser"):
            args._memory_parser.print_help()
        else:
            print("Usage: gaia memory <search|stats|show|conflicts>", file=sys.stderr)
        return 0
    return func(args) or 0


def register(subparsers):
    """Register the memory subcommand with nested sub-actions."""
    import argparse as _argparse

    mem_parser = subparsers.add_parser(
        "memory",
        help="Curated memory + episodic log",
        description=(
            "Inspect, search, and curate Gaia memory. Curated rows live in "
            "the DB; episodic memory is the activity log."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
    )
    mem_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON. bool.",
    )

    # Stash parser reference so dispatcher can print help when no subcommand given
    mem_parser.set_defaults(_memory_parser=mem_parser)

    actions = mem_parser.add_subparsers(dest="memory_action", metavar="<action>")

    # -- search -------------------------------------------------------------
    search_p = actions.add_parser(
        "search",
        help="FTS5 search across curated memory and/or episodes",
        description="Full-text search; returns ranked results.",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia memory search 'release' --scope=memory\n"
               "  gaia memory search 'tailscale' --limit=5 --json\n",
    )
    search_p.add_argument("query", help="FTS5 query string.")
    search_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="Max results per scope. int. Default: 10.",
    )
    search_p.add_argument(
        "--scope", default="both",
        choices=("memory", "episodes", "both", "curated"),
        help="Search scope. Default: both. 'curated' is a deprecated alias.",
    )
    search_p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity.",
    )
    search_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    search_p.set_defaults(func=_cmd_search_scoped)

    # -- stats --------------------------------------------------------------
    stats_p = actions.add_parser(
        "stats",
        help="Memory diagnostics",
        description="Episode count, FTS5 index size, avg score, conflict count.",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia memory stats --json\n",
    )
    stats_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    stats_p.set_defaults(func=_cmd_stats)

    # -- show (curated memory by name) --------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Print a curated memory row",
        description="Look up a curated memory row by (project, name).",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia memory show project_gaia_v5\n",
    )
    show_p.add_argument("name", help="Curated memory slug.")
    show_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    show_p.add_argument(
        "--links", action="store_true", default=False,
        help="List in/out memory_links edges (kind + created_at). bool.",
    )
    show_p.add_argument(
        "--history", action="store_true", default=False,
        help="List memory_history versions (changed_at, fields changed, "
             "body size delta -- not full bodies). bool.",
    )
    show_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    show_p.set_defaults(func=_cmd_curated_show)

    # -- episode-show -------------------------------------------------------
    episode_show_p = actions.add_parser(
        "episode-show",
        help="Print a full episode + score",
        description="Inspect an episodic memory entry by episode_id.",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia memory episode-show ep_20260420_152233_abc\n",
    )
    episode_show_p.add_argument("episode_id", help="Episode identifier.")
    episode_show_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    episode_show_p.set_defaults(func=_cmd_episode_show)

    # -- list ---------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List curated memory rows",
        description="Enumerate the curated memory table.",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia memory list --type=feedback\n"
            "  gaia memory list --limit=10\n"
        ),
    )
    list_p.add_argument(
        "--type", default=None,
        choices=("project", "user", "feedback", "atom", "decision", "negative"),
        help="Filter by type.",
    )
    list_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    list_p.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max rows to return. Default: all.",
    )
    list_p.add_argument(
        "--format", default="table",
        choices=("table", "json", "count"),
        help="Output shape. Default: table.",
    )
    list_p.add_argument(
        "--json", action="store_true", default=False,
        help="Alias for --format=json.",
    )
    list_p.set_defaults(func=_cmd_list)

    # -- delete -------------------------------------------------------------
    delete_p = actions.add_parser(
        "delete",
        help="DISCOURAGED: tombstone a curated memory row; --hard to purge",
        description=(
            "DISCOURAGED BY CONVENTION: memory is meant to be AGGREGATED and "
            "RECLASSIFIED, not deleted. Prefer `gaia memory reclassify "
            "--status=graduated|closed` to retire a note while keeping it, or "
            "`gaia memory append` to correct-forward. If you must remove: this "
            "tombstones the row by default (deleted_at stamped; row + body "
            "survive, invisible to reads, recoverable). --hard performs the "
            "irreversible physical DELETE (explicit human curation only, "
            "strongly discouraged -- it destroys history). T3: delete reduces "
            "recoverability, so it requires approval."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia memory delete project_old --yes\n"
            "  gaia memory delete project_old --hard --yes\n"
        ),
    )
    delete_p.add_argument("name", help="Curated memory slug.")
    delete_p.add_argument("--workspace", default=None, metavar="W",
                          help="Workspace identity.")
    delete_p.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip confirm prompt. bool. Default: false.",
    )
    delete_p.add_argument(
        "--hard", action="store_true", default=False,
        help="Physically DELETE the row (irreversible). Default: false "
             "(soft-delete/tombstone).",
    )
    delete_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    delete_p.set_defaults(func=_cmd_delete)

    # -- edit ---------------------------------------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="CORRECT a curated memory field (overwrite/supersede, with history)",
        description=(
            "Correction verb: overwrite a single column to fix or reframe what "
            "is already there. The prior value is preserved in memory_history "
            "(supersede-with-history, not a destructive mutation), but the read "
            "surface shows only the new value. To ADD text without replacing "
            "it, prefer `gaia memory append` -- that is the primary additive "
            "verb. Use `edit` when the existing content is WRONG and must be "
            "corrected. (T3: correction changes what reads see, so it needs "
            "approval; append does not.)"
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia memory edit --name=foo --field=body "
               "--append --content='...'\n"
               "  gaia memory edit --name=foo --field=body "
               "--body-file=/tmp/new_body.md\n"
               "  cat new_body.md | gaia memory edit --name=foo --field=body "
               "--body-file=-\n",
    )
    edit_p.add_argument("--name", required=True, help="Curated memory slug.")
    # --field / --content are no longer required: T5 lets edit operate as a
    # pure reclassify when only --class/--status are supplied. The handler
    # surfaces a clear error if neither pair is provided.
    edit_p.add_argument(
        "--field", default=None,
        choices=("description", "body"),
        help="Column to patch (optional; required only when --content or --body-file given).",
    )
    _edit_content_group = edit_p.add_mutually_exclusive_group()
    _edit_content_group.add_argument(
        "--content", default=None,
        help="New value for --field.",
    )
    _edit_content_group.add_argument(
        "--body-file", dest="body_file", default=None, metavar="PATH",
        help=(
            "Read new value for --field from PATH. Use '-' to read from stdin "
            "until EOF. Useful for bodies with angle brackets, shell variables, "
            "nested quotes, or markdown code blocks."
        ),
    )
    edit_p.add_argument("--append", action="store_true", default=False,
                        help="Append (separator '\\n\\n'). bool. Default: false.")
    edit_p.add_argument(
        "--class", dest="class_", default=None,
        choices=("anchor", "thread", "log"),
        help="T5: set memory.class. Writer-side enum.",
    )
    edit_p.add_argument(
        "--status", dest="status", default=None,
        help=(
            "T5: set memory.status (open|carry_forward|graduated|closed); "
            "use 'null' to clear. Only valid for class=thread."
        ),
    )
    edit_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    _edit_anchor_group = edit_p.add_mutually_exclusive_group()
    _edit_anchor_group.add_argument(
        "--project", default=None, metavar="NAME",
        help=(
            "RE-ANCHOR: change memory.project_ref of an EXISTING row. Resolves "
            "a project NAME within --workspace to its stable project_identity "
            "(same resolution as `gaia memory add --project`). Use this to fix "
            "a row that was written with project_ref NULL or anchored to the "
            "wrong project. Mutually exclusive with --project-ref."
        ),
    )
    _edit_anchor_group.add_argument(
        "--project-ref", dest="project_ref", default=None, metavar="IDENTITY",
        help=(
            "RE-ANCHOR: set memory.project_ref of an EXISTING row directly to a "
            "known project_identity string (no name resolution). Mutually "
            "exclusive with --project."
        ),
    )
    edit_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")
    edit_p.set_defaults(func=_cmd_edit)

    # -- append -------------------------------------------------------------
    # Primary "add text to an existing note" verb. Additive, history-preserving,
    # and NON-mutative (T0): 'append' is not in MUTATIVE_VERBS, so it needs no
    # T3 approval. Routes through the same writer path as `edit --append`.
    append_p = actions.add_parser(
        "append",
        help="Append text to an existing curated memory body (additive, T0)",
        description=(
            "Grow the body of an existing curated memory row by concatenating "
            "new text (separator '\\n\\n'). Additive and non-destructive -- the "
            "prior body is preserved in memory_history. This is the primary "
            "verb for 'add something' to a carry-forward note or running "
            "thread. Non-mutative (needs no approval). To CORRECT or replace "
            "existing text, use `gaia memory edit` instead."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia memory append thread_handoff --body='Next: verify T0.'\n"
            "  gaia memory append thread_handoff --body-file=/tmp/note.md\n"
            "  cat note.md | gaia memory append thread_handoff --body-file=-\n"
        ),
    )
    append_p.add_argument("name", help="Curated memory slug.")
    _append_body_group = append_p.add_mutually_exclusive_group(required=True)
    _append_body_group.add_argument(
        "--body", default=None,
        help="Text to append to the body (markdown string).",
    )
    _append_body_group.add_argument(
        "--body-file", dest="body_file", default=None, metavar="PATH",
        help=(
            "Read the text to append from PATH. Use '-' to read from stdin "
            "until EOF. Useful for text with angle brackets, shell variables, "
            "nested quotes, or markdown code blocks."
        ),
    )
    append_p.add_argument("--workspace", default=None, metavar="W",
                          help="Workspace identity.")
    append_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON. bool.")
    append_p.set_defaults(func=_cmd_append)

    # -- reclassify ---------------------------------------------------------
    reclass_p = actions.add_parser(
        "reclassify",
        help="Update memory.class and/or memory.status on a curated row",
        description=(
            "Set the semantic role (class) and/or lifecycle (status) of a "
            "curated memory row. At least one of --class / --status must be "
            "supplied. status is only valid for class=thread; the writer "
            "auto-clears status when class moves away from thread without "
            "an explicit status flag. Use --status=null to clear explicitly."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia memory reclassify atom_node_20 --class=anchor\n"
            "  gaia memory reclassify thread_handoff --class=thread "
            "--status=open\n"
            "  gaia memory reclassify thread_old --status=graduated\n"
            "  gaia memory reclassify thread_promoted --class=anchor "
            "--status=null   # explicit clear\n"
        ),
    )
    reclass_p.add_argument("name", help="Curated memory slug.")
    reclass_p.add_argument(
        "--class", dest="class_", default=None,
        choices=("anchor", "thread", "log"),
        help="Semantic role. Writer-side enum (no DB CHECK).",
    )
    reclass_p.add_argument(
        "--status", dest="status", default=None,
        help=(
            "Lifecycle for class=thread "
            "(open|carry_forward|graduated|closed). Use 'null' to clear."
        ),
    )
    reclass_p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity. Defaults to cwd-inferred.",
    )
    reclass_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    reclass_p.set_defaults(func=_cmd_reclassify)

    # -- link ---------------------------------------------------------------
    link_p = actions.add_parser(
        "link",
        help="Create or delete a graph edge between two curated memory rows",
        description=(
            "Create (default) or --delete a row in memory_links. Both src and "
            "dst must exist as curated memory rows. Idempotent: re-running the "
            "same link is a no-op."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia memory link atom_node_20 anchor_routing --kind=relates_to\n"
            "  gaia memory link decision_old decision_new --kind=supersedes\n"
            "  gaia memory link a b --kind=relates_to --delete\n"
        ),
    )
    link_p.add_argument("src_name", help="Source memory slug. Must exist.")
    link_p.add_argument("dst_name", help="Destination memory slug. Must exist.")
    link_p.add_argument(
        "--kind", required=True,
        choices=("relates_to", "supersedes", "derived_from", "graduated_to"),
        help="Edge kind (CHECK-enforced in schema).",
    )
    link_p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity. Defaults to cwd-inferred.",
    )
    link_p.add_argument(
        "--delete", action="store_true", default=False,
        help="Delete the link instead of creating it. bool.",
    )
    link_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    link_p.set_defaults(func=_cmd_link)

    # -- add ----------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Upsert a curated memory row (DB-only)",
        description="Insert or update by (project, name).",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia memory add --name=feedback_x --type=feedback "
               "--body='...'\n"
               "  gaia memory add --name=atom_x --type=atom "
               "--body-file=/tmp/body.md\n"
               "  cat body.md | gaia memory add --name=atom_x --type=atom "
               "--body-file=-\n",
    )
    add_p.add_argument("--name", required=True,
                       help="Slug. PK with project.")
    add_p.add_argument(
        "--type", required=True,
        choices=("project", "user", "feedback", "atom", "decision", "negative"),
        help="Memory type. Curated taxonomy (atom/decision/negative) "
             "requires slug prefix matching the type, e.g. 'atom_node_20'.",
    )
    _add_body_group = add_p.add_mutually_exclusive_group(required=True)
    _add_body_group.add_argument(
        "--body", default=None,
        help="Markdown body as a string.",
    )
    _add_body_group.add_argument(
        "--body-file", dest="body_file", default=None, metavar="PATH",
        help=(
            "Read body from PATH. Use '-' to read from stdin until EOF. "
            "Useful for bodies containing angle brackets, shell variables, "
            "nested quotes, or markdown code blocks."
        ),
    )
    add_p.add_argument("--description", default=None,
                       help="Short summary. Shown in list.")
    _add_project_group = add_p.add_mutually_exclusive_group()
    _add_project_group.add_argument(
        "--project", default=None,
        help=(
            "N3: anchor this memory to a project by NAME (resolved within "
            "--workspace to its stable projects.project_identity, persisted "
            "as memory.project_ref). Forward-only: errors clearly if the "
            "project does not exist or has no project_identity yet -- never "
            "guesses. Mutually exclusive with --project-ref."
        ),
    )
    _add_project_group.add_argument(
        "--project-ref", dest="project_ref", default=None,
        help=(
            "N3: anchor this memory directly to a known stable "
            "project_identity string, bypassing name resolution. Use "
            "--project instead unless you already hold the identity value."
        ),
    )
    add_p.add_argument(
        "--initiative", default=None, metavar="KEY",
        help=(
            "v32: canonical project/initiative grouping key (memory.initiative). "
            "Use for a LOGICAL initiative that is NOT a git repo (branchkinect, "
            "buildwiz, axisio, ...) -- normalized to lowercase_snake. When "
            "--project / --project-ref anchors a git project, initiative is "
            "auto-derived from the repo basename (gaia, balance); pass this only "
            "to set a key with no git anchor or to override the derived one."
        ),
    )
    add_p.add_argument(
        "--class", dest="class_", default=None,
        choices=("anchor", "thread", "log"),
        help="T5: set memory.class at insertion time. Writer-side enum.",
    )
    add_p.add_argument(
        "--status", dest="status", default=None,
        help=(
            "T5: set memory.status (open|carry_forward|graduated|closed); "
            "use 'null' to clear. Only valid for class=thread."
        ),
    )
    add_p.add_argument("--workspace", default=None, metavar="W",
                       help="Workspace identity.")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON. bool.")
    add_p.set_defaults(func=_cmd_add)

    # -- checkpoint ---------------------------------------------------------
    checkpoint_p = actions.add_parser(
        "checkpoint",
        help="Persist a session-close reflection atomically (record + threads)",
        description=(
            "Write one session-close reflection in a single transaction: the "
            "record anchor, one carry-forward thread per pending, and a "
            "derived_from edge from each thread back to the record. All-or-"
            "nothing -- a malformed or invalid payload writes zero rows. "
            "Replaces the N+1 add/link sequence session-reflection Step 6 used "
            "to prescribe."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Payload (JSON):\n"
               "  {\n"
               "    \"resumen\":   {\"name\",\"type\",\"description\",\"body\"},\n"
               "    \"pendientes\": [{\"name\",\"description\",\"body\"}, ...]\n"
               "  }\n"
               "Examples:\n"
               "  gaia memory checkpoint --file payload.json --project=gaia\n"
               "  cat payload.json | gaia memory checkpoint --file - --workspace=me\n",
    )
    checkpoint_p.add_argument(
        "--file", default=None, metavar="PATH",
        help=(
            "Path to the JSON payload. Use '-' to read from stdin until EOF. "
            "The payload carries the record ('resumen') and its pendings "
            "('pendientes'); pendings inherit the record's --type."
        ),
    )
    _cp_project_group = checkpoint_p.add_mutually_exclusive_group()
    _cp_project_group.add_argument(
        "--project", default=None,
        help="Anchor the checkpoint rows to a project by NAME (resolved within "
             "--workspace to its stable project_identity). Mutually exclusive "
             "with --project-ref.",
    )
    _cp_project_group.add_argument(
        "--project-ref", dest="project_ref", default=None,
        help="Anchor directly to a known project_identity string.",
    )
    checkpoint_p.add_argument("--workspace", default=None, metavar="W",
                              help="Workspace identity.")
    checkpoint_p.add_argument("--json", action="store_true", default=False,
                              help="Emit JSON. bool.")
    checkpoint_p.set_defaults(func=_cmd_checkpoint)

    # -- get-relevant -------------------------------------------------------
    rel_p = actions.add_parser(
        "get-relevant",
        help="Compact Workspace Memory block for SessionStart injection",
        description=(
            "Emit the top curated atoms/decisions/negatives for a workspace "
            "as a Markdown block, bounded by --max-chars. Designed to be "
            "consumed by the SessionStart hook; prints empty when there is "
            "nothing curated."
        ),
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia memory get-relevant --workspace=qxo\n"
               "  gaia memory get-relevant --types=atom,decision --limit=6\n",
    )
    rel_p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity. Defaults to cwd-inferred.",
    )
    rel_p.add_argument(
        "--limit", type=int, default=_RELEVANT_DEFAULT_LIMIT, metavar="N",
        help=f"Max items across all types. int. Default: {_RELEVANT_DEFAULT_LIMIT}.",
    )
    rel_p.add_argument(
        "--max-chars", dest="max_chars", type=int,
        default=_RELEVANT_DEFAULT_MAX_CHARS, metavar="C",
        help=f"Hard cap on rendered block length. int. Default: {_RELEVANT_DEFAULT_MAX_CHARS}.",
    )
    rel_p.add_argument(
        "--types", default=None, metavar="LIST",
        help="Comma-separated type filter (e.g. 'atom,decision'). "
             "Default: atom,decision,negative.",
    )
    rel_p.add_argument(
        "--sections", default=None, metavar="LIST",
        help="Comma-separated subset of curated sections to render "
             "(carry_forward,anchor,thread_open). When set, uses the class/"
             "status section renderer -- the subagent-dispatch path passes "
             "--sections=anchor to inject only 'About you / What I know'. "
             "When omitted (and no --initiative/--types), the transversal "
             "initiative digest is emitted instead.",
    )
    rel_p.add_argument(
        "--initiative", default=None, metavar="KEY",
        help="Project mode (v32): show the top live-pending threads of the ONE "
             "named initiative (normalised like the write side). The value "
             "'otros' targets the NULL-initiative bucket. When omitted, the "
             "cross-project transversal digest is emitted.",
    )
    rel_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON (with items list + block string). bool.",
    )
    rel_p.set_defaults(func=_cmd_get_relevant)

    # -- conflicts ----------------------------------------------------------
    conflicts_p = actions.add_parser(
        "conflicts",
        help="Contradiction scan across memory files",
        description="Pairwise jaccard similarity scan.",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia memory conflicts --threshold=0.5\n",
    )
    conflicts_p.add_argument(
        "--threshold", type=float, default=0.3, metavar="F",
        help="Jaccard threshold. float. Default: 0.3.",
    )
    conflicts_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )
    conflicts_p.set_defaults(func=_cmd_conflicts)

    # -- story (lineage narration; lives in memory_story.py to keep this
    #    module legible) ----------------------------------------------------
    from cli import memory_story as _memory_story
    _memory_story.add_story_subparser(
        actions, _argparse.RawDescriptionHelpFormatter
    )
