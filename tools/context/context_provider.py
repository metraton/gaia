#!/usr/bin/env python3
"""
Context Provider for Claude Agent System

Generates structured context payloads for agents based on:
1. Agent contracts (context-contracts.json + cloud overlays)
2. Universal rules (universal-rules.json)
3. Historical episodes (episodic memory)

Usage:
    python3 context_provider.py <agent_name> [user_task] [--context-file PATH]
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    from ._paths import resolve_config_dir
    from .surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )
except ImportError:
    from _paths import resolve_config_dir
    from surface_router import (
        build_investigation_brief,
        classify_surfaces,
        load_surface_routing_config,
    )

# Default paths
DEFAULT_CONTEXT_PATH = Path(".claude/project-context/project-context.json")


# ============================================================================
# CONTRACTS DIRECTORY RESOLUTION
# ============================================================================

def get_contracts_dir():
    """Determines the correct contracts directory based on execution context."""
    return resolve_config_dir()


DEFAULT_CONTRACTS_DIR = get_contracts_dir()


# ============================================================================
# UNIVERSAL RULES SYSTEM
# ============================================================================

DEFAULT_RULES_FILE = "universal-rules.json"


def load_universal_rules(agent_name: str, rules_file: Optional[Path] = None) -> Dict[str, Any]:
    """Load universal rules and agent-specific rules from JSON file."""
    if rules_file is None:
        rules_file = get_contracts_dir() / DEFAULT_RULES_FILE

    if not rules_file.is_file():
        print(f"Warning: Rules file not found: {rules_file}", file=sys.stderr)
        return {"universal": [], "agent_specific": []}

    try:
        with open(rules_file, 'r', encoding='utf-8') as f:
            rules_data = json.load(f)

        universal = [r["rule"] for r in rules_data.get("rules", {}).get("universal", [])]
        agent_specific = [
            r["rule"]
            for r in rules_data.get("rules", {}).get("agent_specific", {}).get(agent_name, [])
        ]

        total_rules = len(universal) + len(agent_specific)
        if total_rules > 0:
            print(f"Loaded {len(universal)} universal rules, {len(agent_specific)} agent-specific", file=sys.stderr)

        return {
            "universal": universal,
            "agent_specific": agent_specific
        }
    except Exception as e:
        print(f"Warning: Could not load rules: {e}", file=sys.stderr)
        return {"universal": [], "agent_specific": []}


# ============================================================================
# CLOUD PROVIDER DETECTION
# ============================================================================

def detect_cloud_provider(project_context: Dict[str, Any]) -> str:
    """Detects the cloud provider from project-context.json.

    Detection priority:
      1. metadata.cloud_provider (explicit user/scanner setting)
      2. infrastructure.cloud_providers[0].name (v2 scanner section)
      3. metadata.project_id presence -> gcp
      4. Fallback -> gcp
    """
    metadata = project_context.get("metadata", {})
    if "cloud_provider" in metadata:
        provider = metadata["cloud_provider"].lower()
        if provider == "multi-cloud":
            print("Multi-cloud detected, using GCP contracts as primary", file=sys.stderr)
            return "gcp"
        return provider

    sections = project_context.get("sections", {})

    # v2: read from infrastructure.cloud_providers
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

    if "project_id" in metadata:
        return "gcp"

    print("Could not detect cloud provider, defaulting to GCP", file=sys.stderr)
    return "gcp"


def load_provider_contracts(cloud_provider: str, contracts_dir: Path = DEFAULT_CONTRACTS_DIR) -> Dict[str, Any]:
    """
    Loads context contracts using the base+cloud merge strategy.

    Strategy:
    1. Load base contracts from context-contracts.json (cloud-agnostic)
    2. Load cloud overrides from cloud/{provider}.json and merge (extend) read/write lists
    3. If base contracts missing → error (contracts are the single source of truth)
    """
    base_file = contracts_dir / "context-contracts.json"
    cloud_file = contracts_dir / "cloud" / f"{cloud_provider}.json"

    # --- Step 1: Load base contracts ---
    if not base_file.is_file():
        print(f"Error: Contract file not found at {base_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(base_file, 'r', encoding='utf-8') as f:
            base_contracts = json.load(f)
        print(f"Loaded base contracts from {base_file}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {base_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Step 2: Merge cloud-specific overrides ---
    if cloud_file.is_file():
        try:
            with open(cloud_file, 'r', encoding='utf-8') as f:
                cloud_overrides = json.load(f)
            print(f"Loaded {cloud_provider.upper()} cloud overrides from {cloud_file}", file=sys.stderr)

            for agent_name, agent_overrides in cloud_overrides.get("agents", {}).items():
                if agent_name in base_contracts.get("agents", {}):
                    existing_read = base_contracts["agents"][agent_name].get("read", [])
                    existing_write = base_contracts["agents"][agent_name].get("write", [])
                    extra_read = [s for s in agent_overrides.get("read", []) if s not in existing_read]
                    extra_write = [s for s in agent_overrides.get("write", []) if s not in existing_write]
                    base_contracts["agents"][agent_name]["read"] = existing_read + extra_read
                    base_contracts["agents"][agent_name]["write"] = existing_write + extra_write
                else:
                    base_contracts["agents"][agent_name] = agent_overrides

        except json.JSONDecodeError as e:
            print(f"Warning: Invalid JSON in {cloud_file}: {e} — skipping cloud overrides", file=sys.stderr)
    else:
        print(f"No cloud overrides found at {cloud_file}, using base contracts only", file=sys.stderr)

    return {
        "version": base_contracts.get("version", "unknown"),
        "provider": cloud_provider,
        "agents": base_contracts.get("agents", {})
    }


def load_project_context(context_path: Path) -> Dict[str, Any]:
    """Loads the project context from the specified JSON file."""
    if not context_path.is_file():
        print(f"Error: Context file not found at {context_path}", file=sys.stderr)
        sys.exit(1)
    with open(context_path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
        sections: All available sections from project-context.json.
        contract_keys: The agent's permitted read keys (from context-contracts).
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
        print(f"ERROR: Invalid agent '{agent_name}'. Available: {list(provider_contracts.get('agents', {}).keys())}", file=sys.stderr)
        sys.exit(1)

    contract_keys = agent_contract.get("read", [])

    sections = project_context.get("sections", {})
    if not sections:
        raise KeyError("project-context.json must contain a 'sections' object.")

    return get_relevant_sections(
        sections, contract_keys,
        surface_routing=surface_routing,
        routing_config=routing_config,
    )


def get_context_update_contract(
    agent_name: str,
    provider_contracts: Dict[str, Any]
) -> Dict[str, Any]:
    """Return the SSOT contract agents should use for CONTEXT_UPDATE decisions."""
    agent_contract = provider_contracts.get("agents", {}).get(agent_name)
    if not agent_contract:
        print(f"ERROR: Invalid agent '{agent_name}'. Available: {list(provider_contracts.get('agents', {}).keys())}", file=sys.stderr)
        sys.exit(1)

    return {
        "readable_sections": agent_contract.get("read", []),
        "writable_sections": agent_contract.get("write", []),
        "source": "config/context-contracts.json + config/cloud/{provider}.json",
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

try:
    from tools.memory.search_store import search as fts5_search
except ImportError:
    fts5_search = None


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
        score = ep.get("relevance_score", ep.get("_score", 0.0))
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

    Layer 1 (always): A compact markdown table of all scored memory sources
    (~200 tokens), returned under the ``memory_index`` key.

    Layer 2 (selective): Full content of top-N episodes ranked by
    ``tools.memory.scoring.rank_episodes()``, loaded only within the
    remaining token budget.

    Parameters
    ----------
    user_task:
        Free-text description of the user's current task.
    max_episodes:
        Legacy cap on the number of full episodes to include (Layer 2).
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
        index_file = Path(".claude/project-context/episodic-memory/index.json")
        if not index_file.exists():
            return {}

        with open(index_file) as f:
            index = json.load(f)

        all_index_episodes = index.get("episodes", [])
        if not all_index_episodes:
            return {}

        # --- Layer 1: Memory Index -- compact markdown table (~200 tokens, always included) ---
        layer1_text = _build_memory_index_table(all_index_episodes)
        layer1_tokens = _estimate_tokens(layer1_text)
        remaining_budget = max_tokens - layer1_tokens

        # --- Score and rank episodes: hybrid FTS5 + keyword fallback ---
        # Build a lookup map from episode id to index entry for fast access
        ep_by_id = {ep["id"]: ep for ep in all_index_episodes if "id" in ep}

        # Try FTS5 first; results are BM25-ranked (better quality)
        fts5_ids: List[str] = []
        if fts5_search is not None:
            try:
                fts5_results = fts5_search(user_task, max_results=max_episodes * 3)
                fts5_ids = [r["episode_id"] for r in fts5_results if "episode_id" in r]
                print(
                    f"FTS5 search returned {len(fts5_ids)} candidates for retrieval",
                    file=sys.stderr,
                )
            except Exception as _fts_err:
                print(
                    f"Warning: FTS5 search failed (non-fatal): {_fts_err}",
                    file=sys.stderr,
                )
                fts5_ids = []

        # Build ranked list: FTS5 hits first, then fill with keyword/scoring results
        fts5_id_set = set(fts5_ids)

        # Keyword/scoring baseline (used to fill gaps and as full fallback)
        if _HAS_SCORING and _rank_episodes is not None:
            keyword_ranked = _rank_episodes(all_index_episodes, user_task)
        else:
            keyword_ranked = sorted(
                [dict(ep, _score=_fallback_keyword_score(ep, user_task)) for ep in all_index_episodes],
                key=lambda x: x["_score"],
                reverse=True,
            )

        # If FTS5 found candidates, prepend them (with decay scoring if available)
        if fts5_ids:
            fts5_episodes = [ep_by_id[eid] for eid in fts5_ids if eid in ep_by_id]
            if _HAS_SCORING and _rank_episodes is not None:
                fts5_episodes = _rank_episodes(fts5_episodes, user_task)
            else:
                # Assign a generous score so they sort above keyword results
                fts5_episodes = [dict(ep, _score=max(ep.get("_score", 0.0), 0.5)) for ep in fts5_episodes]
            # Fill remaining slots with keyword results not already in FTS5 set
            keyword_fill = [ep for ep in keyword_ranked if ep.get("id") not in fts5_id_set]
            ranked = fts5_episodes + keyword_fill
        else:
            # FTS5 not available or returned nothing — fall back entirely to keyword
            ranked = keyword_ranked

        # --- Layer 2: full content of top episodes within remaining budget ---
        full_episodes = []
        tokens_used = 0
        for ep in ranked:
            if len(full_episodes) >= max_episodes:
                break
            score = ep.get("_score", 0.0)
            if score <= 0.05:
                continue
            if remaining_budget <= 0:
                break

            full_ep = load_full_episode(ep["id"], index_file.parent)
            if not full_ep:
                continue

            episode_entry = {
                "id": full_ep["id"],
                "title": full_ep["title"],
                "type": full_ep["type"],
                "relevance": round(score, 4),
                "lessons_learned": full_ep.get("lessons_learned", [])[:2],
                "resolution": full_ep.get("resolution", "")[:200],
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
                f"(budget={max_tokens}, used≈{layer1_tokens + tokens_used})",
                file=sys.stderr,
            )
        else:
            print(
                f"Memory index built ({len(all_index_episodes)} entries, "
                f"no full episodes within score/budget threshold)",
                file=sys.stderr,
            )

        # --- Retrieval strengthening: update retrieval_count + last_retrieved ---
        # Failure here must never block context injection.
        try:
            if full_episodes:
                import tempfile as _tempfile
                from datetime import datetime as _datetime

                selected_ids = {ep["id"] for ep in full_episodes}
                now_iso = _datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

                updated = False
                for entry in index.get("episodes", []):
                    if entry.get("id") in selected_ids:
                        entry["retrieval_count"] = entry.get("retrieval_count", 0) + 1
                        entry["last_retrieved"] = now_iso
                        updated = True

                if updated:
                    index_path = index_file.resolve()
                    index_dir = index_path.parent
                    fd, tmp_path = _tempfile.mkstemp(
                        dir=str(index_dir), suffix=".tmp", prefix="index_"
                    )
                    try:
                        with _os.fdopen(fd, "w", encoding="utf-8") as tf:
                            json.dump(index, tf, indent=2)
                        _os.rename(tmp_path, str(index_path))
                        print(
                            f"Retrieval strengthening: updated {len(selected_ids)} episode(s)",
                            file=sys.stderr,
                        )
                    except Exception:
                        try:
                            _os.unlink(tmp_path)
                        except Exception:
                            pass
                        raise
        except Exception as _rs_err:
            print(
                f"Warning: retrieval_count update failed (non-fatal): {_rs_err}",
                file=sys.stderr,
            )

        return result

    except Exception as e:
        print(f"Warning: Could not load episodic memory: {e}", file=sys.stderr)
        return {}


def load_full_episode(episode_id: str, memory_dir: Path) -> Optional[Dict[str, Any]]:
    """Load full episode details from JSONL file."""
    try:
        episodes_file = memory_dir / "episodes.jsonl"
        if episodes_file.exists():
            with open(episodes_file) as f:
                for line in f:
                    try:
                        episode = json.loads(line)
                        if episode.get("id") == episode_id:
                            return episode
                    except Exception:
                        continue
    except Exception:
        pass
    return None


# ============================================================================
# MAIN FUNCTION
# ============================================================================

def main():
    """Main function to generate and print the context payload."""
    import os as _os

    parser = argparse.ArgumentParser(
        description="Generates a structured context payload for a Claude agent."
    )
    parser.add_argument("agent_name", help="The name of the agent being invoked.")
    parser.add_argument("user_task", nargs="?", default="General inquiry",
                        help="The user's task or query for the agent.")
    parser.add_argument(
        "--context-file",
        type=Path,
        default=DEFAULT_CONTEXT_PATH,
        help=f"Path to the project-context.json file. Defaults to '{DEFAULT_CONTEXT_PATH}'"
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

    # Resolve memory token budget: CLI arg > env var > default
    memory_token_budget: Optional[int] = args.memory_token_budget
    if memory_token_budget is None:
        env_budget = _os.environ.get("GAIA_MEMORY_TOKEN_BUDGET")
        if env_budget:
            try:
                memory_token_budget = int(env_budget)
            except ValueError:
                memory_token_budget = 2000
        else:
            memory_token_budget = 2000

    # Load project context
    project_context = load_project_context(args.context_file)

    # Detect cloud provider and load contracts
    cloud_provider = detect_cloud_provider(project_context)
    provider_contracts = load_provider_contracts(cloud_provider)

    # Compute surface routing BEFORE extracting sections so we can gate by surface
    surface_routing_config = load_surface_routing_config()
    surface_routing = classify_surfaces(
        args.user_task,
        current_agent=args.agent_name,
        routing_config=surface_routing_config,
    )

    # Extract contracted sections (surface-gated when routing is available)
    contract_context = get_contract_context(
        project_context, args.agent_name, provider_contracts,
        surface_routing=surface_routing,
        routing_config=surface_routing_config,
    )
    context_update_contract = get_context_update_contract(args.agent_name, provider_contracts)

    # Load historical episodes (2-layer progressive disclosure)
    historical_context = load_relevant_episodes(args.user_task, max_tokens=memory_token_budget)

    # Load universal rules
    rules_context = load_universal_rules(args.agent_name)
    investigation_brief = build_investigation_brief(
        args.user_task,
        args.agent_name,
        contract_context,
        routing_config=surface_routing_config,
        routing=surface_routing,
    )

    # Build final payload
    final_payload = {
        "project_knowledge": contract_context,
        "write_permissions": context_update_contract,
        "rules": rules_context,
        "surface_routing": surface_routing,
        "investigation_brief": investigation_brief,
        "metadata": {
            "cloud_provider": cloud_provider,
            "contract_version": provider_contracts.get("version", "unknown"),
            "historical_episodes_count": len(historical_context.get("episodes", [])),
            "rules_count": len(rules_context.get("universal", [])) + len(rules_context.get("agent_specific", [])),
            "surface_routing_version": surface_routing_config.get("version", "unknown"),
            "active_surfaces_count": len(surface_routing.get("active_surfaces", [])),
            "surface_routing_confidence": surface_routing.get("confidence", 0.0),
        }
    }

    # Add historical context if episodes found
    if historical_context:
        final_payload["historical_context"] = historical_context

    print(json.dumps(final_payload, indent=2))


if __name__ == "__main__":
    main()
