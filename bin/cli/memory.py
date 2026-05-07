"""
gaia memory -- Inspect, query, and curate Gaia memory.

Read-only subcommands operate on episodic memory (the activity log under
``.claude/project-context/episodic-memory/``):

  search <query> [--limit N] [--json]   FTS5 search with hybrid scoring
  stats [--json]                         Episode count, index count, scores, conflicts
  show <episode_id> [--json]            Full episode with metadata and score
  conflicts [--threshold F] [--json]    Contradiction scan across memory files

Mutating subcommands operate on the curated ``memory`` table in
``~/.gaia/gaia.db`` (the project-level / user-level / feedback notes):

  add --name=<slug> --type=<project|user|feedback> --body="..."
      [--description=...] [--workspace=<ws>] [--json]
                                          DB-only writer; no filesystem side
                                          effects (no .md under
                                          ~/.claude/projects/.../memory/).
"""

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

def _import_search_store():
    try:
        from tools.memory import search_store
        return search_store
    except ImportError:
        return None


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


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_search(args) -> int:
    """Handle `gaia memory search <query> [--limit N]`."""
    as_json = getattr(args, "json", False)
    query = args.query
    limit = getattr(args, "limit", 10)

    search_store = _import_search_store()
    EpisodicMemory = _import_episodic()
    score_memory = _import_scoring()

    if search_store is None:
        return _err("search_store module not available", as_json)

    raw_results = search_store.search(query, max_results=limit)

    results = []
    for hit in raw_results:
        episode_id = hit.get("episode_id", "")
        rank = hit.get("rank", 0.0)

        # Enrich via episodic module
        episode = None
        if EpisodicMemory is not None:
            try:
                mem = EpisodicMemory()
                episode = mem.get_episode(episode_id)
            except Exception:
                episode = None

        if episode is None:
            # Minimal record from rank alone
            results.append({
                "id": episode_id,
                "title": "",
                "score": 0.0,
                "date": "",
                "snippet": "",
            })
            continue

        # Compute hybrid score
        days = _days_old(episode.get("timestamp", ""))
        retrieval_count = int(episode.get("retrieval_count", 0))
        if score_memory is not None:
            try:
                hybrid_score = score_memory(days_old=days, retrieval_count=retrieval_count)
            except Exception:
                hybrid_score = 0.0
        else:
            hybrid_score = 0.0

        # Snippet: first 120 chars of enriched_prompt or prompt
        text = episode.get("enriched_prompt") or episode.get("prompt") or ""
        snippet = text[:120] + ("..." if len(text) > 120 else "")

        results.append({
            "id": episode_id,
            "title": episode.get("title") or "",
            "score": round(hybrid_score, 4),
            "date": episode.get("timestamp", "")[:10],  # YYYY-MM-DD
            "snippet": snippet,
        })

    output = {"results": results}
    if as_json:
        print(json.dumps(output, indent=2))
    else:
        if not results:
            print("No results found.")
        else:
            for i, r in enumerate(results, 1):
                print(f"\n{i}. [{r['score']:.4f}] {r['title'] or r['id']}")
                print(f"   Date: {r['date']}")
                print(f"   {r['snippet']}")
    return 0


def _cmd_stats(args) -> int:
    """Handle `gaia memory stats`."""
    as_json = getattr(args, "json", False)
    project_root = _find_project_root()

    index = _load_index(project_root)
    episodes = index.get("episodes", [])
    total_episodes = len(episodes)

    search_store = _import_search_store()
    score_memory = _import_scoring()
    detect_conflicts = _import_conflict_detector()

    warnings: list[str] = []

    # Indexed count. The FTS5 backend returns -1 as a sentinel when path
    # resolution / connection fails (e.g. broken `.claude/*` symlinks).
    # Previously we silently coerced that to 0, which hid drift: `doctor`
    # would report 102/102 while `memory stats` reported 0/0 with no
    # indication anything was wrong. Now we surface it explicitly.
    indexed: int = 0
    if search_store is not None:
        try:
            raw = search_store.count()
        except Exception:
            raw = -1
        if isinstance(raw, int) and raw < 0:
            warnings.append(
                "FTS5 index path not resolved — symlinks may be broken. "
                "Run: gaia doctor"
            )
            indexed = raw  # keep the sentinel visible in JSON output
        else:
            indexed = int(raw) if isinstance(raw, int) else 0

    # avg_score from a sample of episodes
    avg_score = 0.0
    if score_memory is not None and episodes:
        sample = episodes[:100]  # cap to avoid slow computation on huge indexes
        scores = []
        for ep in sample:
            try:
                days = _days_old(ep.get("timestamp", ""))
                rc = int(ep.get("retrieval_count", 0))
                scores.append(score_memory(days_old=days, retrieval_count=rc))
            except Exception:
                pass
        if scores:
            avg_score = round(sum(scores) / len(scores), 4)

    # Conflict count
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


def _cmd_episode_show(args) -> int:
    """Handle `gaia memory episode-show <episode_id>`.

    Renamed from the legacy ``gaia memory show`` so that ``show`` can route
    to curated memory (``gaia memory show <name>``). The legacy episode
    inspector remains available under the explicit ``episode-show`` verb;
    pre-existing callers that import ``_cmd_show`` see an alias defined
    below for backward compatibility.
    """
    as_json = getattr(args, "json", False)
    episode_id = args.episode_id

    EpisodicMemory = _import_episodic()
    score_memory = _import_scoring()

    if EpisodicMemory is None:
        return _err("episodic module not available", as_json)

    try:
        mem = EpisodicMemory()
        episode = mem.get_episode(episode_id)
    except Exception as exc:
        return _err(f"Could not load episode: {exc}", as_json)

    if episode is None:
        return _err(f"Episode not found: {episode_id}", as_json)

    # Compute score
    days = _days_old(episode.get("timestamp", ""))
    retrieval_count = int(episode.get("retrieval_count", 0))
    if score_memory is not None:
        try:
            score = round(score_memory(days_old=days, retrieval_count=retrieval_count), 4)
        except Exception:
            score = 0.0
    else:
        score = 0.0

    output = {
        "id": episode.get("episode_id") or episode.get("id") or episode_id,
        "title": episode.get("title") or "",
        "content": episode.get("enriched_prompt") or episode.get("prompt") or "",
        "score": score,
        "tags": episode.get("tags") or [],
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
    description = getattr(args, "description", None)
    workspace = _resolve_workspace(getattr(args, "workspace", None))

    if not name:
        return _err("--name is required", as_json)
    if not mem_type:
        return _err("--type is required", as_json)
    if not body:
        return _err("--body is required", as_json)

    try:
        from gaia.store.writer import upsert_memory, VALID_MEMORY_TYPES
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    if mem_type not in VALID_MEMORY_TYPES:
        return _err(
            f"invalid type '{mem_type}'; must be one of {list(VALID_MEMORY_TYPES)}",
            as_json,
        )

    try:
        res = upsert_memory(
            workspace,
            name,
            type=mem_type,
            body=body,
            description=description,
        )
    except ValueError as exc:
        return _err(str(exc), as_json)
    except Exception as exc:  # noqa: BLE001
        return _err(f"failed to upsert memory: {exc}", as_json)

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
        print(json.dumps(out, indent=2))
    else:
        verb = "Updated" if res.get("action") == "updated" else "Created"
        print(f"{verb} memory '{name}' (type={mem_type}, workspace={workspace})")
        if description:
            print(f"  description: {description}")
        print(f"  body: {snippet}")
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
    if as_json:
        fmt = "json"

    try:
        from gaia.store.writer import list_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    rows = list_memory(workspace, type=type_filter)

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
    """Hard-delete a curated memory row (FTS5 mirror cleared via trigger)."""
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    skip_confirm = getattr(args, "yes", False)

    try:
        from gaia.store.writer import get_memory, delete_memory
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    row = get_memory(workspace, name)
    if row is None:
        return _err(
            f"memory '{name}' not found in workspace '{workspace}'",
            as_json,
        )

    if not skip_confirm:
        prompt = f"Delete memory '{name}' (type={row['type']})? [y/N] "
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

    deleted = delete_memory(workspace, name)
    if not deleted:
        return _err(
            f"memory '{name}' could not be deleted (already gone?)",
            as_json,
        )

    if as_json:
        print(json.dumps({
            "deleted": True,
            "name": name,
            "workspace": workspace,
            "previous_type": row["type"],
        }, indent=2, default=str))
    else:
        print(f"Deleted memory '{name}' (workspace={workspace!r}, "
              f"previous_type={row['type']!r})")
    return 0


def _cmd_edit(args) -> int:
    """Patch a single column of a curated memory row."""
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = getattr(args, "name", None)
    field = getattr(args, "field", None)
    content = getattr(args, "content", None)
    append = getattr(args, "append", False)

    if not name:
        return _err("--name is required", as_json)
    if not field:
        return _err("--field is required", as_json)
    if content is None or content == "":
        return _err("--content is required", as_json)

    try:
        from gaia.store.writer import update_memory_field
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    try:
        res = update_memory_field(workspace, name, field, content,
                                  append=append)
    except ValueError as exc:
        return _err(str(exc), as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Updated memory '{name}' field={field} action={res['action']}")
    return 0


# ---------------------------------------------------------------------------
# Override _cmd_search: add --scope for curated/episodes/both
# ---------------------------------------------------------------------------

def _cmd_search_scoped(args) -> int:
    """Wrapper around the legacy ``_cmd_search`` that honours ``--scope``.

    ``--scope=episodes`` (default for backwards compatibility) routes to the
    legacy episodic FTS5 backend.
    ``--scope=curated`` runs FTS5 over the ``memory_fts`` mirror in
    ``~/.gaia/gaia.db``.
    ``--scope=both`` returns combined results, episodes-first (sorted by
    rank within each scope).
    """
    scope = getattr(args, "scope", None) or "both"
    as_json = getattr(args, "json", False)
    query = args.query
    limit = getattr(args, "limit", 10)

    if scope == "episodes":
        return _cmd_search(args)

    # Curated path (used by curated-only and both).
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    try:
        from gaia.store.writer import search_memory_curated
    except ImportError as exc:
        return _err(f"gaia.store.writer not importable: {exc}", as_json)

    curated = search_memory_curated(workspace, query, limit=limit)

    if scope == "curated":
        if as_json:
            print(json.dumps({"scope": "curated", "results": curated},
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

    # both: run episode search and combine
    search_store = _import_search_store()
    EpisodicMemory = _import_episodic()
    episodes_out: list = []
    if search_store is not None:
        raw = search_store.search(query, max_results=limit)
        for hit in raw:
            episode_id = hit.get("episode_id", "")
            episode = None
            if EpisodicMemory is not None:
                try:
                    episode = EpisodicMemory().get_episode(episode_id)
                except Exception:
                    episode = None
            if episode is None:
                episodes_out.append({"id": episode_id, "title": "",
                                     "rank": hit.get("rank", 0.0)})
                continue
            episodes_out.append({
                "id": episode_id,
                "title": episode.get("title") or "",
                "rank": hit.get("rank", 0.0),
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
    mem_parser = subparsers.add_parser(
        "memory",
        help="Inspect and query Gaia episodic memory (read-only)",
    )
    mem_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON (machine-readable)",
    )

    # Stash parser reference so dispatcher can print help when no subcommand given
    mem_parser.set_defaults(_memory_parser=mem_parser)

    actions = mem_parser.add_subparsers(dest="memory_action", metavar="<action>")

    # -- search ---------------------------------------------------------------
    search_p = actions.add_parser(
        "search",
        help="FTS5 search across episodes and/or curated memory",
    )
    search_p.add_argument("query", help="Search query")
    search_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="Maximum number of results (default: 10)",
    )
    search_p.add_argument(
        "--scope", default="both",
        choices=("curated", "episodes", "both"),
        help="Where to search: curated memory rows, episodic memory, "
             "or both (default: both)",
    )
    search_p.add_argument(
        "--workspace", default=None,
        help="Workspace identity for curated scope "
             "(default: gaia.project.current() or 'me')",
    )
    search_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    search_p.set_defaults(func=_cmd_search_scoped)

    # -- stats ----------------------------------------------------------------
    stats_p = actions.add_parser("stats", help="Episode count, index stats, conflict count")
    stats_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    stats_p.set_defaults(func=_cmd_stats)

    # -- show (curated memory by name) ---------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Print a curated memory row by name (use episode-show for episodes)",
    )
    show_p.add_argument("name",
                        help="Curated memory slug (e.g. project_gaia_v5)")
    show_p.add_argument("--workspace", default=None)
    show_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    show_p.set_defaults(func=_cmd_curated_show)

    # -- episode-show (legacy, renamed from old `show`) ----------------------
    episode_show_p = actions.add_parser(
        "episode-show",
        help="Full episode with metadata and score (legacy episodic memory)",
    )
    episode_show_p.add_argument("episode_id", help="Episode ID to display")
    episode_show_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    episode_show_p.set_defaults(func=_cmd_episode_show)

    # -- list (curated memory) ------------------------------------------------
    list_p = actions.add_parser(
        "list", help="List curated memory rows in the workspace",
    )
    list_p.add_argument(
        "--type", default=None,
        choices=("project", "user", "feedback"),
        help="Filter by memory type",
    )
    list_p.add_argument("--workspace", default=None)
    list_p.add_argument(
        "--format", default="table",
        choices=("table", "json", "count"),
    )
    list_p.add_argument(
        "--json", action="store_true", default=False,
        help="Alias for --format=json",
    )
    list_p.set_defaults(func=_cmd_list)

    # -- delete (curated memory) ---------------------------------------------
    delete_p = actions.add_parser(
        "delete",
        help="Hard-delete a curated memory row (FTS5 mirror cleared via trigger)",
    )
    delete_p.add_argument("name", help="Curated memory slug")
    delete_p.add_argument("--workspace", default=None)
    delete_p.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip the interactive confirmation prompt",
    )
    delete_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    delete_p.set_defaults(func=_cmd_delete)

    # -- edit (curated memory; patch one field) ------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="Patch a curated memory field (description / body) by flags",
    )
    edit_p.add_argument("--name", required=True,
                        help="Curated memory slug to patch")
    edit_p.add_argument(
        "--field", required=True,
        choices=("description", "body"),
        help="Column to patch (type changes go through delete + add)",
    )
    edit_p.add_argument("--content", required=True,
                        help="New value for the field")
    edit_p.add_argument("--append", action="store_true", default=False,
                        help="Concatenate with existing field using '\\n\\n' "
                             "separator instead of overwriting")
    edit_p.add_argument("--workspace", default=None)
    edit_p.add_argument("--json", action="store_true", default=False)
    edit_p.set_defaults(func=_cmd_edit)

    # -- add (DB-only writer; curated memory) --------------------------------
    add_p = actions.add_parser(
        "add",
        help="Add (or upsert) a curated memory row in the DB (no filesystem)",
    )
    add_p.add_argument("--name", required=True,
                       help="Memory slug (e.g. project_gaia_v5). Acts as PK with workspace.")
    add_p.add_argument(
        "--type", required=True,
        choices=("project", "user", "feedback"),
        help="Canonical memory type (matches schema CHECK constraint)",
    )
    add_p.add_argument("--body", required=True,
                       help="Markdown body (without frontmatter)")
    add_p.add_argument("--description", default=None,
                       help="Optional one-line summary")
    add_p.add_argument("--workspace", default=None,
                       help="Workspace identity (default: gaia.project.current() or 'me')")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit the result as JSON")
    add_p.set_defaults(func=_cmd_add)

    # -- conflicts ------------------------------------------------------------
    conflicts_p = actions.add_parser(
        "conflicts", help="Contradiction scan across memory files"
    )
    conflicts_p.add_argument(
        "--threshold", type=float, default=0.3, metavar="F",
        help="Jaccard similarity threshold (default: 0.3)",
    )
    conflicts_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    conflicts_p.set_defaults(func=_cmd_conflicts)
