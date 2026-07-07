#!/usr/bin/env python3
"""
Context Provider for Claude Agent System

Generates structured context payloads for agents based on:
1. Agent contracts (agent_contract_permissions in ~/.gaia/gaia.db)
2. Project context (project_context_contracts in ~/.gaia/gaia.db)
3. Historical episodes (episodic memory)

Usage:
    python3 context_provider.py <agent_name> [user_task]
"""

import json
import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# Ensure the package root is on sys.path so that `tools.memory.scoring` and
# `gaia.store.reader` resolve when this module is imported in-process by hooks
# (e.g. context_injector) or invoked via the CLI entry point.
# Pattern: same as hooks/pre_tool_use.py line 22.
_PACKAGE_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

try:
    from .surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )
except ImportError:
    from surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )


# ============================================================================
# DB CONNECTION
# ============================================================================

def _get_db_path() -> Optional[Path]:
    """Resolve path to ~/.gaia/gaia.db via gaia.paths, or fallback default."""
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return Path.home() / ".gaia" / "gaia.db"


def _db_connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    resolved = db_path or _get_db_path()
    con = sqlite3.connect(str(resolved))
    con.row_factory = sqlite3.Row
    return con


# ============================================================================
# CLOUD PROVIDER DETECTION
# ============================================================================

def detect_cloud_provider(sections: Dict[str, Any]) -> str:
    """Detects the cloud provider from the infrastructure contract payload.

    Detection priority:
      1. infrastructure.cloud_providers[0].name (v2 scanner section)
      2. Fallback -> gcp
    """
    infra = sections.get("infrastructure", {})
    if isinstance(infra, dict):
        cloud_providers = infra.get("cloud_providers", [])
        if isinstance(cloud_providers, list) and cloud_providers:
            primary = cloud_providers[0]
            if isinstance(primary, dict):
                name = primary.get("name", "")
                if name:
                    provider = name.lower()
                    if provider == "multi-cloud":
                        return "gcp"
                    return provider

    print("Could not detect cloud provider from infrastructure section, defaulting to GCP", file=sys.stderr)
    return "gcp"


def load_project_context(workspace: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load project context for a workspace from project_context_contracts in gaia.db.

    Returns a dict shaped as ``{metadata: {}, sections: {contract_name: payload, ...}}``
    to remain compatible with the rest of the pipeline. Returns an empty context when the
    workspace has no rows rather than exiting.
    """
    try:
        con = _db_connect(db_path)
        rows = con.execute(
            "SELECT contract_name, payload, metadata, updated_at "
            "FROM project_context_contracts WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        print(f"Warning: DB error reading project context for '{workspace}': {exc}", file=sys.stderr)
        rows = []

    sections: Dict[str, Any] = {}
    last_updated = ""
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        sections[row["contract_name"]] = payload
        if row["updated_at"] and row["updated_at"] > last_updated:
            last_updated = row["updated_at"]

    if not sections:
        print(f"Warning: No project context found for workspace '{workspace}' in gaia.db", file=sys.stderr)

    return {
        "metadata": {"workspace": workspace, "last_updated": last_updated},
        "sections": sections,
    }


def load_provider_contracts(agent_name: str, cloud_provider: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load agent contract permissions from agent_contract_permissions in gaia.db.

    Returns a shape compatible with the rest of the pipeline:
    ``{version: str, provider: str, agents: {agent_name: {read: [...], write: [...]}}}``

    cloud_scope=NULL rows match all providers; cloud_scope=<provider> rows match only
    that provider. Both are included when querying for a specific provider.
    """
    try:
        con = _db_connect(db_path)
        rows = con.execute(
            """
            SELECT contract_name, can_read, can_write
            FROM agent_contract_permissions
            WHERE agent_name = ?
              AND (cloud_scope IS NULL OR cloud_scope = ?)
            ORDER BY contract_name
            """,
            (agent_name, cloud_provider),
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        print(f"Warning: DB error reading permissions for '{agent_name}': {exc}", file=sys.stderr)
        rows = []

    # Dedupe defensively; order is deterministic via the query's ORDER BY
    # contract_name. The row set can carry the same contract_name more than
    # once -- e.g. a NULL cloud_scope row plus a provider-scoped overlay, or
    # (observed in the field) accumulated duplicate NULL-scope rows because
    # SQLite treats NULL as distinct in the composite PRIMARY KEY, so
    # INSERT OR REPLACE never conflicts on NULL scope. Without this dedupe the
    # readable/writable lists fan out into thousands of repeated entries, which
    # the subagent Permissions block renders verbatim (~93% payload bloat
    # observed). See FIX (c).
    def _dedupe(names: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    readable = _dedupe([r["contract_name"] for r in rows if r["can_read"]])
    writable = _dedupe([r["contract_name"] for r in rows if r["can_write"]])

    return {
        "version": "db",
        "provider": cloud_provider,
        "agents": {
            agent_name: {"read": readable, "write": writable},
        },
    }


# ============================================================================
# CONTEXT EXTRACTION
# ============================================================================

def get_relevant_sections(
    sections: Dict[str, Any],
    contract_keys: List[str],
    surface_routing: Optional[Dict[str, Any]] = None,
    routing_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Filter sections by surface relevance, with fallback to all readable sections.

    Args:
        sections: All available sections from project_context_contracts (keyed by contract name).
        contract_keys: The agent's permitted read keys (from agent_contract_permissions).
        surface_routing: The routing result from classify_surfaces().
        routing_config: The full surface-routing.json config (has contract_sections per surface).

    Returns:
        Filtered dict of sections. Falls back to all readable sections when:
        - No surface_routing or routing_config provided
        - No active surfaces detected
        - Surface has no contract_sections defined
        - Intersection of surface sections and agent permissions is empty
    """
    all_readable = {k: sections[k] for k in contract_keys if k in sections}

    if not surface_routing or not routing_config:
        return all_readable

    active_surfaces = surface_routing.get("active_surfaces", [])
    if not active_surfaces:
        return all_readable

    surfaces_cfg = routing_config.get("surfaces", {})

    # Collect relevant sections from all active surfaces
    relevant: set = set()
    for surface in active_surfaces:
        surface_config = surfaces_cfg.get(surface, {})
        surface_sections = surface_config.get("contract_sections", [])
        relevant.update(surface_sections)

    if not relevant:
        # Surfaces have no contract_sections defined -- inject all (fallback)
        return all_readable

    # Filter: agent permissions AND surface relevance
    filtered = {k: sections[k] for k in contract_keys if k in sections and k in relevant}

    if not filtered:
        # Nothing matched -- inject all (fallback)
        return all_readable

    omitted = set(all_readable.keys()) - set(filtered.keys())
    if omitted:
        print(
            f"Surface gating: {len(filtered)} sections injected, "
            f"{len(omitted)} omitted ({', '.join(sorted(omitted))})",
            file=sys.stderr,
        )
    else:
        print(
            f"Surface gating: all {len(filtered)} readable sections match active surfaces",
            file=sys.stderr,
        )

    return filtered


def get_contract_context(
    project_context: Dict[str, Any],
    agent_name: str,
    provider_contracts: Dict[str, Any],
    surface_routing: Optional[Dict[str, Any]] = None,
    routing_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extracts the contract-defined context sections for a given agent.

    When surface_routing and routing_config are provided, sections are filtered
    to only those relevant to the active surface(s).  Falls back to returning
    all readable sections when routing is unavailable or yields an empty set.
    """
    agent_contract = provider_contracts.get("agents", {}).get(agent_name)
    if not agent_contract:
        print(f"Warning: No contract found for agent '{agent_name}' in DB; returning empty context.", file=sys.stderr)
        return {}

    contract_keys = agent_contract.get("read", [])

    sections = project_context.get("sections", {})

    return get_relevant_sections(
        sections, contract_keys,
        surface_routing=surface_routing,
        routing_config=routing_config,
    )


def get_context_update_contract(
    agent_name: str,
    provider_contracts: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the write/read permissions agents should use for update_contracts decisions."""
    agent_contract = provider_contracts.get("agents", {}).get(agent_name, {})

    return {
        "readable_sections": agent_contract.get("read", []),
        "writable_sections": agent_contract.get("write", []),
        "source": "agent_contract_permissions in ~/.gaia/gaia.db",
    }


# ============================================================================
# EPISODIC MEMORY
# ============================================================================

try:
    from tools.memory.scoring import rank_episodes as _rank_episodes
    _HAS_SCORING = True
except ImportError:
    try:
        import importlib, sys as _sys
        _scoring = importlib.import_module("tools.memory.scoring")
        _rank_episodes = _scoring.rank_episodes
        _HAS_SCORING = True
    except ImportError:
        _rank_episodes = None
        _HAS_SCORING = False

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


def _build_memory_index_table(index_episodes: List[Dict[str, Any]]) -> str:
    """Build a compact markdown table of all memory sources for Layer 1."""
    from datetime import datetime, timezone
    lines = ["## Memory Index", "", "| # | Title | Type | Score | Age |", "|----|-------|------|-------|-----|"]
    for i, ep in enumerate(index_episodes, 1):
        title = ep.get("title", "")[:40]
        ep_type = ep.get("type", "unknown")
        score = ep.get("relevance_score")
        if score is None:
            score = ep.get("_score", 0.0)
        # Calculate age from timestamp field
        ts = ep.get("timestamp", "")
        try:
            if ts:
                ep_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ep_time).days
                age_str = f"{age}d"
            else:
                age_str = "?d"
        except Exception:
            age_str = "?d"
        lines.append(f"| {i} | {title} | {ep_type} | {score:.2f} | {age_str} |")
    return "\n".join(lines)


def _fallback_keyword_score(episode: Dict[str, Any], user_task: str) -> float:
    """Keyword-based relevance scoring fallback when scoring module is unavailable."""
    task_lower = user_task.lower()
    task_words = set(task_lower.split())
    score = 0.0
    for tag in episode.get("tags", []):
        if tag.lower() in task_lower:
            score += 0.4
    title_words = set(episode.get("title", "").lower().split())
    common_words = task_words & title_words
    if common_words:
        score += 0.3 * (len(common_words) / max(len(title_words), 1))
    return score * episode.get("relevance_score", 0.5)


def load_relevant_episodes(
    user_task: str,
    max_episodes: int = 2,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Load relevant historical episodes using 2-layer progressive disclosure.

    Source of truth: the ``episodes`` / ``episodes_fts`` tables in
    ``~/.gaia/gaia.db`` (episodic memory is DB-canonical -- one row per agent
    turn, indexed by the schema's FTS5 triggers). The legacy filesystem layout
    (``.claude/project-context/episodic-memory/index.json`` + ``episodes.jsonl``)
    is no longer created on the canonical path and is not read here.

    Layer 1 (always): a compact markdown table of the FTS5-matched episodes
    (~200 tokens), returned under the ``memory_index`` key.

    Layer 2 (selective): the top-N episodes ranked by
    ``tools.memory.scoring.rank_episodes()``, included only within the
    remaining token budget.

    Parameters
    ----------
    user_task:
        Free-text description of the user's current task.
    max_episodes:
        Cap on the number of full episodes to include (Layer 2).
    max_tokens:
        Total token budget for the episodic memory block.  Reads from
        ``GAIA_MEMORY_TOKEN_BUDGET`` env var when not supplied explicitly.
        Defaults to 2000.
    """
    import os as _os

    if max_tokens is None:
        env_budget = _os.environ.get("GAIA_MEMORY_TOKEN_BUDGET")
        if env_budget:
            try:
                max_tokens = int(env_budget)
            except ValueError:
                max_tokens = 2000
        else:
            max_tokens = 2000

    try:
        # Resolve the current workspace so retrieval is project-scoped.
        try:
            from gaia.project import current as _project_current
            workspace = _project_current()
        except Exception:
            workspace = None

        # --- FTS5 over episodes_fts in gaia.db (canonical shared reader) ---
        try:
            from gaia.store.reader import (
                search_episodes_fts as _search_fts,
                sanitize_episodes_fts_query as _sanitize_fts,
            )
        except ImportError as _imp_err:
            print(
                f"Warning: gaia.store.reader unavailable, skipping memory: {_imp_err}",
                file=sys.stderr,
            )
            return {}

        fts_query = _sanitize_fts(user_task)
        rows = _search_fts(
            fts_query, workspace=workspace, limit=max_episodes * 3,
        ) if fts_query else []

        if not rows:
            return {}

        # Normalize DB rows into episode dicts with the 'id' key the table
        # builder and scorer expect.
        all_episodes: List[Dict[str, Any]] = []
        for r in rows:
            ep = dict(r)
            ep["id"] = ep.get("episode_id", "")
            all_episodes.append(ep)

        print(
            f"FTS5 search returned {len(all_episodes)} candidates for retrieval",
            file=sys.stderr,
        )

        # --- Layer 1: Memory Index -- compact markdown table (always included) ---
        layer1_text = _build_memory_index_table(all_episodes)
        layer1_tokens = _estimate_tokens(layer1_text)
        remaining_budget = max_tokens - layer1_tokens

        # --- Rank by keyword relevance x memory strength (decay) ---
        if _HAS_SCORING and _rank_episodes is not None:
            ranked = _rank_episodes(all_episodes, user_task)
        else:
            ranked = sorted(
                [dict(ep, _score=_fallback_keyword_score(ep, user_task)) for ep in all_episodes],
                key=lambda x: x["_score"],
                reverse=True,
            )

        # Prefer episodes with positive relevance; if scoring finds none
        # (matched only on tags/enriched text), fall back to FTS rank order so
        # a genuine match still surfaces.
        candidates = [ep for ep in ranked if ep.get("_score", 0.0) > 0.0]
        if not candidates:
            candidates = all_episodes

        # --- Layer 2: top episodes within remaining budget ---
        full_episodes = []
        tokens_used = 0
        for ep in candidates:
            if len(full_episodes) >= max_episodes:
                break
            if remaining_budget <= 0:
                break

            episode_entry = {
                "id": ep.get("id") or ep.get("episode_id", ""),
                "title": ep.get("title") or "",
                "type": ep.get("type") or "",
                "relevance": round(ep.get("_score", 0.0), 4),
                "outcome": ep.get("outcome") or "",
                "plan_status": ep.get("plan_status") or "",
            }
            entry_text = json.dumps(episode_entry)
            entry_tokens = _estimate_tokens(entry_text)

            if tokens_used + entry_tokens > remaining_budget:
                break

            full_episodes.append(episode_entry)
            tokens_used += entry_tokens

        result: Dict[str, Any] = {
            "memory_index": layer1_text,
        }

        if full_episodes:
            result["episodes"] = full_episodes
            result["summary"] = f"Found {len(full_episodes)} relevant historical episodes"
            print(
                f"Added {len(full_episodes)} historical episodes to context "
                f"(budget={max_tokens}, used~{layer1_tokens + tokens_used})",
                file=sys.stderr,
            )
        else:
            print(
                f"Memory index built ({len(all_episodes)} entries, "
                f"no full episodes within score/budget threshold)",
                file=sys.stderr,
            )

        return result

    except Exception as e:
        print(f"Warning: Could not load episodic memory: {e}", file=sys.stderr)
        return {}


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def build_context_payload(
    agent_name: str,
    user_task: str,
    workspace: Optional[str] = None,
    db_path: Optional[Path] = None,
    memory_token_budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Build and return a context payload dict for an agent.

    This is the programmatic entry point used by context_injector.py (in-process
    call, no subprocess). The ``main()`` function wraps this for CLI usage.

    Args:
        agent_name: The agent being dispatched (e.g. "cloud-troubleshooter").
        user_task: Free-text task description for surface routing and memory search.
        workspace: Workspace name to query project_context_contracts. Defaults to
            gaia.project.current() when None.
        db_path: Optional explicit path to gaia.db (used in tests).
        memory_token_budget: Token cap for episodic memory. Falls back to
            GAIA_MEMORY_TOKEN_BUDGET env var, then 2000.
    """
    import os as _os

    if memory_token_budget is None:
        env_budget = _os.environ.get("GAIA_MEMORY_TOKEN_BUDGET")
        if env_budget:
            try:
                memory_token_budget = int(env_budget)
            except ValueError:
                memory_token_budget = 2000
        else:
            memory_token_budget = 2000

    if workspace is None:
        try:
            from gaia.project import current as _project_current
            workspace = _project_current() or "global"
        except Exception:
            workspace = "global"

    project_context = load_project_context(workspace, db_path=db_path)
    cloud_provider = detect_cloud_provider(project_context.get("sections", {}))
    provider_contracts = load_provider_contracts(agent_name, cloud_provider, db_path=db_path)

    surface_routing_config = load_surface_routing_config()
    surface_routing = classify_surfaces(
        user_task,
        current_agent=agent_name,
        routing_config=surface_routing_config,
    )

    contract_context = get_contract_context(
        project_context, agent_name, provider_contracts,
        surface_routing=surface_routing,
        routing_config=surface_routing_config,
    )
    context_update_contract = get_context_update_contract(agent_name, provider_contracts)

    historical_context = load_relevant_episodes(user_task, max_tokens=memory_token_budget)

    investigation_brief = build_investigation_brief(
        user_task,
        agent_name,
        contract_context,
        routing_config=surface_routing_config,
        routing=surface_routing,
    )

    final_payload: Dict[str, Any] = {
        "project_knowledge": contract_context,
        "write_permissions": context_update_contract,
        "surface_routing": surface_routing,
        "investigation_brief": investigation_brief,
        "metadata": {
            "cloud_provider": cloud_provider,
            "contract_version": provider_contracts.get("version", "unknown"),
            "historical_episodes_count": len(historical_context.get("episodes", [])),
            "surface_routing_version": surface_routing_config.get("version", "unknown"),
            "active_surfaces_count": len(surface_routing.get("active_surfaces", [])),
            "surface_routing_confidence": surface_routing.get("confidence", 0.0),
        },
    }

    if historical_context:
        final_payload["historical_context"] = historical_context

    return final_payload


def main():
    """CLI entry point: generate and print the context payload as JSON."""
    parser = argparse.ArgumentParser(
        description="Generates a structured context payload for a Claude agent."
    )
    parser.add_argument("agent_name", help="The name of the agent being invoked.")
    parser.add_argument("user_task", nargs="?", default="General inquiry",
                        help="The user's task or query for the agent.")
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace name to query (default: resolved from gaia.project.current())",
    )
    parser.add_argument(
        "--memory-token-budget",
        type=int,
        default=None,
        help=(
            "Token budget for episodic memory injection. "
            "Overrides GAIA_MEMORY_TOKEN_BUDGET env var. Default: 2000."
        ),
    )

    args = parser.parse_args()

    payload = build_context_payload(
        agent_name=args.agent_name,
        user_task=args.user_task,
        workspace=args.workspace,
        memory_token_budget=args.memory_token_budget,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
