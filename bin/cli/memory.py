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
                                          --project anchors memory.project_ref
                                          (N3, forward-only) by resolving a
                                          project name within --workspace to
                                          its stable project_identity; never
                                          guesses (clear error if the project
                                          does not exist or has no identity).

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
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    class_flag = getattr(args, "class_", None)
    status_flag = getattr(args, "status", None)
    project_flag = getattr(args, "project", None)
    project_ref_flag = getattr(args, "project_ref", None)

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

    try:
        from gaia.store.writer import (
            upsert_memory, reclassify_memory, resolve_project_ref,
            VALID_MEMORY_TYPES,
        )
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    if mem_type not in VALID_MEMORY_TYPES:
        return _err(
            f"invalid type '{mem_type}'; must be one of {list(VALID_MEMORY_TYPES)}",
            as_json,
        )

    # N3: forward-only project_ref anchor. --project resolves a project name
    # (within `workspace`) to its stable project_identity; --project-ref
    # passes an already-known identity directly. Neither guesses: an
    # unresolvable --project is a clear error, never a silent NULL.
    project_ref = None
    if project_flag is not None:
        try:
            project_ref = resolve_project_ref(workspace, project_flag)
        except ValueError as exc:
            return _err(str(exc), as_json)
    elif project_ref_flag is not None:
        project_ref = project_ref_flag

    try:
        res = upsert_memory(
            workspace,
            name,
            type=mem_type,
            body=body,
            description=description,
            project_ref=project_ref,
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
        print(f"  body: {snippet}")
        if reclassify_result is not None:
            print(
                f"  class={reclassify_result['class']}, "
                f"status={reclassify_result['memory_status']}"
            )
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
# without quota (user-explicit "carry me into the next session"); anchors and
# thread/open rows get bounded quotas; class=log never injects.
_RELEVANT_PER_CLASS_QUOTA = {
    "anchor": 4,
    "thread_open": 2,
}
_RELEVANT_CARRY_FORWARD_UNLIMITED = True

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


def _cmd_get_relevant(args) -> int:
    """Emit a compact memory block for SessionStart injection (v4).

    Selection model (T7, schema v4):
      * Section 1 (For this session): all rows with class=thread, status=
        carry_forward. Injected first, NO quota -- user-explicit hand-off.
      * Section 2 (About you / What I know): rows with class=anchor, ordered
        by updated_at DESC, quota 4.
      * Section 3 (Open threads): rows with class=thread, status=open,
        ordered by updated_at DESC, quota 2.
      * class=log rows are NEVER injected (closed-bitácora).
      * class=NULL rows (legacy, pre-v4): treated as anchor for backward
        compatibility -- the 36 me-workspace rows keep showing up until
        T10 reclassifies them. Trade-off: invisible drift vs broken UX.
      * Rows that are the destination of a `supersedes` edge are excluded
        across all sections -- a newer memory has replaced them.

    Char budget enforcement (T7):
      Trim order on overflow: thread_open → anchor. carry_forward rows
      are NEVER trimmed (user-explicit). If carry_forward alone exceeds
      the budget, emit warning and pass through.

    Legacy --types=... mode: when caller explicitly passes --types, the
    pre-v4 flow (atom/decision/negative by type with the old quota) is
    used unchanged. This preserves the existing test surface.

    Output is NEVER raised: a database error returns empty payload.
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    max_chars = int(getattr(args, "max_chars", None) or _RELEVANT_DEFAULT_MAX_CHARS)
    types_arg = getattr(args, "types", None)

    if types_arg:
        # Legacy type-based selection -- keep verbatim for back-compat.
        return _cmd_get_relevant_by_type(args, workspace, max_chars)

    # Optional section filter. When --sections is passed (comma-separated subset
    # of carry_forward,anchor,thread_open) only those sections are rendered. This
    # is how the subagent-dispatch path requests anchors-only ("About you / What
    # I know") while the orchestrator's SessionStart path omits the flag and keeps
    # all three sections. Unknown tokens are ignored; an empty/whitespace value
    # falls back to all sections (safe default).
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
                "SELECT name, type, description, updated_at, class, status "
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

            # Section 1: carry_forward -- no LIMIT.
            if "carry_forward" in active_sections:
                cur = con.execute(
                    base_select
                    + "  AND class = 'thread' AND status = 'carry_forward' "
                    + "ORDER BY COALESCE(updated_at, '') DESC",
                    (workspace, workspace),
                )
                rows_by_section["carry_forward"] = [dict(r) for r in cur.fetchall()]

            # Section 2: anchor.
            if "anchor" in active_sections:
                anchor_quota = _RELEVANT_PER_CLASS_QUOTA["anchor"]
                cur = con.execute(
                    base_select
                    + "  AND class = 'anchor' "
                    + "ORDER BY COALESCE(updated_at, '') DESC "
                    + f"LIMIT {anchor_quota}",
                    (workspace, workspace),
                )
                rows_by_section["anchor"] = [dict(r) for r in cur.fetchall()]

            # Section 3: thread/open (excluding carry_forward).
            if "thread_open" in active_sections:
                thread_quota = _RELEVANT_PER_CLASS_QUOTA["thread_open"]
                cur = con.execute(
                    base_select
                    + "  AND class = 'thread' AND status = 'open' "
                    + "ORDER BY COALESCE(updated_at, '') DESC "
                    + f"LIMIT {thread_quota}",
                    (workspace, workspace),
                )
                rows_by_section["thread_open"] = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
    except Exception:
        # Any DB error -> empty block, fail-safe SessionStart contract.
        if as_json:
            print(json.dumps({"workspace": workspace, "items": [], "block": ""}))
        return 0

    items_flat: list[dict] = []

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
                desc = body[:60] + ("..." if len(body) > 60 else "")
            line = f"- {name}: {desc}" if desc else f"- {name}"
            out.append(line)
            items_flat.append({
                "name": name,
                "type": r.get("type"),
                "class": r.get("class"),
                "memory_status": r.get("status"),
                "section": section_key,
                "description": desc,
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

    # Char budget: trim in order thread_open -> anchor. carry_forward is
    # never trimmed. Track overflow counter and section breakdown.
    overflow_count = 0
    overflow_warning = None
    carry_forward_lines = []
    if len(block) > max_chars:
        # Detect the "carry_forward alone exceeds budget" case by measuring
        # the carry_forward header + its '- ' lines from the current `lines`
        # list. We do NOT rebuild the section (that would double-count
        # items_flat).
        cf_header = _SECTION_HEADERS["carry_forward"]
        cf_lines: list[str] = []
        in_cf = False
        for ln in lines:
            if ln == cf_header:
                in_cf = True
                cf_lines.append(ln)
                continue
            if in_cf:
                if ln.startswith("## Memory"):
                    break
                cf_lines.append(ln)
        while cf_lines and cf_lines[-1] == "":
            cf_lines.pop()
        cf_block = "\n".join(cf_lines)
        if len(cf_block) > max_chars:
            overflow_warning = (
                "carry_forward block exceeds max_chars; passing through "
                "without trimming (user-explicit content)"
            )

        # Trim from the back: prefer to drop thread_open lines first,
        # then anchor lines. Never touch carry_forward.
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

        # Trim thread_open exhaustively, then anchor.
        for trim_target in ("thread_open", "anchor"):
            while len(block) > max_chars and _trim_one(trim_target):
                overflow_count += 1
                while lines and lines[-1] == "":
                    lines.pop()
                block = "\n".join(lines)
            if len(block) <= max_chars:
                break

        # If carry_forward still exceeds, we leave it as-is (warning above).
        if overflow_count > 0:
            footer = (
                f"\n... ({overflow_count} more items, use "
                f"'gaia memory search' to query)"
            )
            if len(block) + len(footer) <= max_chars:
                block = block + footer

    # Recoverable-pointer guidance (P2a). Appended AFTER budget trimming so the
    # pointer is never the line that gets dropped; its length was reserved from
    # max_chars above, so block + pointer still respects the caller's budget.
    block = block + "\n\n" + _MEMORY_POINTER

    if as_json:
        payload = {
            "workspace": workspace,
            "items": items_flat,
            "block": block,
            "overflow": overflow_count,
        }
        if overflow_warning:
            payload["overflow_warning"] = overflow_warning
        print(json.dumps(payload, indent=2, default=str))
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


def _cmd_curated_show(args) -> int:
    """Print a single curated memory row.

    Distinguishes from the legacy ``episode-show`` flow by looking up the
    ``memory`` table directly (PK = ``(project, name)``).
    """
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name

    try:
        from gaia.store.writer import get_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    row = get_memory(workspace, name)
    if row is None:
        return _err(
            f"memory '{name}' not found in workspace '{workspace}'",
            as_json,
        )

    if as_json:
        print(json.dumps(row, indent=2, default=str))
        return 0

    print(f"# {row['name']}  (type={row['type']})")
    if row.get("description"):
        print(f"# {row['description']}")
    print(f"# updated_at: {row.get('updated_at')}")
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

    if not has_field_patch and not has_reclassify:
        return _err(
            "--field/--content or --class/--status is required", as_json,
        )

    try:
        from gaia.store.writer import update_memory_field, reclassify_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

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
             "(carry_forward,anchor,thread_open). Default: all three. "
             "The subagent-dispatch path passes --sections=anchor to inject "
             "only 'About you / What I know'; the orchestrator omits the flag.",
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
