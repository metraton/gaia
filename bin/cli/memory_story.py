"""
gaia memory story -- narrate the lineage of a curated memory row.

``story`` resolves the graph of ``memory_links`` around a slug (BFS in both
directions, cycle-safe, depth-bounded) and fuses the ``memory_history`` of
every node in that lineage into ONE chronological timeline: approximate birth
(the first observable trace -- the `memory` table has no created_at), body
appends (char delta), body edits, status transitions, link creation, and
tombstones. It closes with a final-state table (name / class / status / role in
the tree).

Read-only (T0): all data comes from ``gaia.store.reader`` read helpers, which
open the substrate with ``PRAGMA query_only = ON``. This module owns only the
CLI surface (argument wiring + narration render); the queries, the BFS, and the
timeline fusion live in ``gaia.store.reader`` so they are unit-testable without
the CLI.

Kept in its own module (imported by ``bin/cli/memory.py``) so ``memory.py``
stays legible.
"""

from __future__ import annotations

# Repo-root import bootstrap so ``from gaia.store.reader import ...`` resolves
# regardless of cwd (the CLI is launched from many places).
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

import json
import sys


def _resolve_workspace(explicit: str | None) -> str:
    """Resolve the workspace, delegating to memory.py's shared resolver.

    Imported lazily to avoid a circular import at registration time
    (memory.py imports this module to wire the subparser).
    """
    from cli.memory import _resolve_workspace as _rw
    return _rw(explicit)


def _err(msg: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Narration render (episode-show style: two-space indented, ASCII-only)
# ---------------------------------------------------------------------------

def _render_story(story: dict, workspace: str) -> str:
    seed = story["seed"]
    lines: list[str] = []
    lines.append("")
    lines.append(f"  Story of memory '{seed}'  (workspace={workspace})")
    lines.append(
        f"  Lineage: {len(story['nodes'])} node(s), "
        f"{len(story['edges'])} edge(s)"
    )
    lines.append("")

    # Lineage nodes
    lines.append("  Lineage")
    for n in story["nodes"]:
        role = n.get("role") or "-"
        depth = n.get("depth")
        depth_s = f"depth {depth}" if depth is not None else "depth ?"
        lines.append(f"    - {n['name']}  [{role}, {depth_s}]")
    lines.append("")

    # Timeline
    lines.append("  Timeline")
    if not story["timeline"]:
        lines.append("    (no recorded history or links)")
    else:
        for ev in story["timeline"]:
            ts = ev.get("ts") or "(undated)"
            node = ev.get("node") or ""
            approx = " ~" if ev.get("approximate") else ""
            lines.append(f"    {ts}{approx}  {node}: {ev.get('detail', '')}")
    lines.append("")

    # Final states
    lines.append("  Final state")
    name_w = max(4, max((len(f["name"]) for f in story["final_states"]),
                        default=4))
    header = (
        f"    {'NAME':<{name_w}}  {'CLASS':<7}  {'STATUS':<13}  ROLE"
    )
    lines.append(header)
    lines.append("    " + "-" * (name_w + 7 + 13 + 8 + 6))
    for f in story["final_states"]:
        cls = f.get("class") or "-"
        status = f.get("status") or "-"
        role = f.get("role") or "-"
        marker = "" if f.get("present", True) else "  (row gone)"
        lines.append(
            f"    {f['name']:<{name_w}}  {cls:<7}  {status:<13}  "
            f"{role}{marker}"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand handler
# ---------------------------------------------------------------------------

def _cmd_story(args) -> int:
    """Handle ``gaia memory story <slug>``."""
    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    slug = args.name
    max_depth = int(getattr(args, "max_depth", None) or 5)

    try:
        from gaia.store.reader import build_memory_story
        from gaia.store.writer import get_memory
    except ImportError as exc:
        return _err(f"gaia.store not importable: {exc}", as_json)

    # Existence gate: a slug with neither a live row NOR any lineage trace is a
    # genuine miss. A slug whose row was hard-deleted but still appears as a
    # link endpoint remains reachable through the lineage, so we do not require
    # a live row -- we require SOME trace.
    story = build_memory_story(workspace, slug, max_depth=max_depth)
    live = get_memory(workspace, slug)
    has_trace = (
        len(story["nodes"]) > 1
        or bool(story["timeline"])
        or any(f["present"] for f in story["final_states"])
    )
    if live is None and not has_trace:
        return _err(
            f"memory '{slug}' not found in workspace '{workspace}' "
            f"(no live row, no lineage, no history)",
            as_json,
        )

    if as_json:
        print(json.dumps(story, indent=2, default=str))
        return 0

    print(_render_story(story, workspace))
    return 0


# ---------------------------------------------------------------------------
# Registration (called by cli.memory.register)
# ---------------------------------------------------------------------------

def add_story_subparser(actions, raw_formatter) -> None:
    """Register the ``story`` sub-action on the ``gaia memory`` subparsers."""
    story_p = actions.add_parser(
        "story",
        help="Narrate a curated memory row's lineage as a fused timeline",
        description=(
            "Resolve the graph of memory_links around a slug (BFS both "
            "directions, cycle-safe, depth-bounded) and fuse the memory_history "
            "of every node into one chronological timeline: approximate birth, "
            "body appends/edits, status transitions, link creation, tombstones. "
            "Closes with a final-state table. Read-only (T0)."
        ),
        formatter_class=raw_formatter,
        epilog=(
            "Examples:\n"
            "  gaia memory story thread_handoff\n"
            "  gaia memory story decision_new --json\n"
            "  gaia memory story atom_x --max-depth=3\n"
        ),
    )
    story_p.add_argument("name", help="Curated memory slug (the lineage seed).")
    story_p.add_argument(
        "--max-depth", dest="max_depth", type=int, default=5, metavar="N",
        help="Max BFS hops over memory_links. int. Default: 5.",
    )
    story_p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity. Defaults to cwd-inferred.",
    )
    story_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON {nodes, edges, timeline, final_states}. bool.",
    )
    story_p.set_defaults(func=_cmd_story)
