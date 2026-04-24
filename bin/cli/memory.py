"""
gaia memory -- Inspect and query Gaia episodic memory (read-only).

Subcommands:
  search <query> [--limit N] [--json]   FTS5 search with hybrid scoring
  stats [--json]                         Episode count, index count, scores, conflicts
  show <episode_id> [--json]            Full episode with metadata and score
  conflicts [--threshold F] [--json]    Contradiction scan across memory files
"""

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


def _cmd_show(args) -> int:
    """Handle `gaia memory show <episode_id>`."""
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
    search_p = actions.add_parser("search", help="FTS5 search with hybrid scoring")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument(
        "--limit", type=int, default=10, metavar="N",
        help="Maximum number of results (default: 10)",
    )
    search_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    search_p.set_defaults(func=_cmd_search)

    # -- stats ----------------------------------------------------------------
    stats_p = actions.add_parser("stats", help="Episode count, index stats, conflict count")
    stats_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    stats_p.set_defaults(func=_cmd_stats)

    # -- show -----------------------------------------------------------------
    show_p = actions.add_parser("show", help="Full episode with metadata and score")
    show_p.add_argument("episode_id", help="Episode ID to display")
    show_p.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    show_p.set_defaults(func=_cmd_show)

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
