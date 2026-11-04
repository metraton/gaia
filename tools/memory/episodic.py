#!/usr/bin/env python3
"""
Episodic Memory System for GAIA

Episodes capture user interactions, clarifications, and enriched prompts.
``store_episode`` writes to the gaia.db ``episodes`` table; the search, list,
relationship and cleanup methods operate on the JSON/JSONL file layout.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
import re
from dataclasses import dataclass, asdict, field


def _ensure_gaia_store_importable() -> None:
    """Ensure ``gaia.store.writer`` is importable from sys.path.

    A copy of this module outside the package (the hook subprocess runs
    without a PYTHONPATH naming it) has no relative path to
    ``<root>/gaia/store/writer.py``. Candidate roots, first match wins:
        1. ``GAIA_PACKAGE_ROOT`` env var.
        2. Three directories above this file.
        3. ``node_modules/@jaguilar87/gaia`` walking up from this file and
           from cwd.
        4. cwd / 'gaia'.

    The first whose ``gaia/store/writer.py`` exists is prepended to
    ``sys.path``. With none, ``sys.path`` is left alone and the import in
    ``store_episode`` raises.
    """
    try:
        import gaia.store.writer  # noqa: F401
        return
    except ImportError:
        pass

    candidates: List[Path] = []

    env_root = os.environ.get("GAIA_PACKAGE_ROOT")
    if env_root:
        candidates.append(Path(env_root))

    here = Path(__file__).resolve()
    candidates.append(here.parent.parent.parent)

    for anchor in (here, Path.cwd()):
        cursor = anchor
        for _ in range(6):
            nm = cursor / "node_modules" / "@jaguilar87" / "gaia"
            if (nm / "gaia" / "store" / "writer.py").exists():
                candidates.append(nm)
                break
            if cursor.parent == cursor:
                break
            cursor = cursor.parent

    candidates.append(Path.cwd() / "gaia")

    for root in candidates:
        if (root / "gaia" / "store" / "writer.py").is_file():
            root_str = str(root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
            return


_ensure_gaia_store_importable()


# SOLVES points problem -> solution, CAUSES action -> consequence, SUPERSEDES
# marks the episode that replaces another; RELATED_TO is the generic link.
RELATIONSHIP_TYPES = frozenset([
    "SOLVES",
    "CAUSES",
    "DEPENDS_ON",
    "VALIDATES",
    "SUPERSEDES",
    "RELATED_TO",
])

OUTCOME_VALUES = frozenset(["success", "partial", "failed", "abandoned"])

# Must equal gaia.state.VALID_PLAN_STATUSES and the ``episodes.plan_status``
# CHECK in gaia/store/schema.sql; copied so this module does not import gaia.state.
VALID_PLAN_STATUSES = frozenset([
    "IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE",
    "BLOCKED", "NEEDS_INPUT", "NEEDS_VERIFICATION",
])


@dataclass
class Episode:
    """Represents a single episodic memory entry."""
    episode_id: str
    timestamp: str
    keywords: List[str]
    prompt: str
    clarifications: Dict[str, Any]
    enriched_prompt: str
    context: Dict[str, Any]
    tags: Optional[List[str]] = None
    type: Optional[str] = None
    title: Optional[str] = None
    relevance_score: float = 1.0
    outcome: Optional[str] = None
    success: Optional[bool] = None
    duration_seconds: Optional[float] = None
    commands_executed: Optional[List[str]] = None
    related_episodes: Optional[List[Dict[str, str]]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert episode to dictionary."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


class EpisodicMemory:
    """
    Episodic memory over two stores.

    ``store_episode`` writes the gaia.db ``episodes`` table. Every other method
    reads or writes the ``index.json`` / ``episodes.jsonl`` / ``episodes/``
    files under ``base_path``, which nothing here creates.
    """

    def __init__(
        self,
        base_path: Optional[Union[str, Path]] = None,
        db_path: Optional[Union[str, Path]] = None,
    ):
        """
        Initialize EpisodicMemory with specified or default path.

        Args:
            base_path: Base directory for the filesystem episodic-memory
                layout. The constructor does not create this directory or any
                child path; the file-based methods resolve files relative to
                it when they exist.
            db_path: Optional override for the gaia.db file. When ``None``
                the writer resolves the canonical path via
                ``gaia.paths.db_path()``. Test fixtures pass an isolated
                ``tmp_path`` to avoid polluting the shared substrate.
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            candidates = [
                Path(".claude/project-context/episodic-memory"),
            ]

            for path in candidates:
                if path.parent.exists():
                    self.base_path = path
                    break
            else:
                self.base_path = candidates[0]

        self.episodes_dir = self.base_path / "episodes"
        self.index_file = self.base_path / "index.json"
        self.episodes_jsonl = self.base_path / "episodes.jsonl"
        self.db_path = Path(db_path) if db_path else None

    def _save_index(self, index_data: Dict[str, Any]):
        """Save index to JSON file."""
        with open(self.index_file, 'w') as f:
            json.dump(index_data, f, indent=2)

    def _load_index(self) -> Dict[str, Any]:
        """Load index from JSON file."""
        if not self.index_file.exists():
            return {"episodes": [], "relationships": [], "metadata": {}}

        try:
            with open(self.index_file, 'r') as f:
                index = json.load(f)
                # Ensure relationships key exists for backward compatibility
                if "relationships" not in index:
                    index["relationships"] = []
                return index
        except (json.JSONDecodeError, IOError):
            return {"episodes": [], "relationships": [], "metadata": {}}

    def _extract_keywords(self, text: str) -> List[str]:
        """
        Extract keywords from text for indexing.

        Lower-cased alphabetic words of three or more letters, minus a stopword
        list, deduplicated in order, capped at 20.

        Args:
            text: Text to extract keywords from

        Returns:
            List of keywords
        """
        words = re.findall(r'\b[a-z]+\b', text.lower())

        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'been', 'be',
                    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
                    'could', 'may', 'might', 'can', 'must', 'shall', 'need', 'dare'}

        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        return unique_keywords[:20]

    def _generate_title(self, prompt: str) -> str:
        """The prompt's first sentence, cut to 60 characters."""
        title = prompt.split('.')[0] if '.' in prompt else prompt
        return title[:60] + ('...' if len(title) > 60 else '')

    def _determine_type(self, prompt: str, context: Dict[str, Any]) -> str:
        """Determine episode type based on prompt and context."""
        prompt_lower = prompt.lower()

        if any(word in prompt_lower for word in ['deploy', 'apply', 'push', 'release']):
            return 'deployment'
        elif any(word in prompt_lower for word in ['fix', 'error', 'issue', 'problem', 'debug']):
            return 'troubleshooting'
        elif any(word in prompt_lower for word in ['create', 'add', 'new', 'setup', 'init']):
            return 'creation'
        elif any(word in prompt_lower for word in ['update', 'modify', 'change', 'edit']):
            return 'modification'
        elif any(word in prompt_lower for word in ['check', 'verify', 'test', 'validate']):
            return 'validation'
        elif any(word in prompt_lower for word in ['delete', 'remove', 'clean']):
            return 'deletion'
        else:
            return 'general'

    def store_episode(
        self,
        prompt: str,
        clarifications: Optional[Dict[str, Any]] = None,
        enriched_prompt: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        episode_id: Optional[str] = None,
        outcome: Optional[str] = None,
        success: Optional[bool] = None,
        duration_seconds: Optional[float] = None,
        commands_executed: Optional[List[str]] = None,
        related_episodes: Optional[List[Dict[str, str]]] = None,
        workflow_metrics: Optional[Dict] = None
    ) -> str:
        """
        Store a new episode in the gaia.db ``episodes`` table.

        One INSERT into ``episodes`` plus one into ``episode_anomalies`` per
        entry under ``context["anomalies"]``; the schema's INSERT trigger
        handles FTS5 indexing. It writes no JSON, JSONL or index files, and
        raises RuntimeError when the row is not persisted.

        Args:
            prompt: Original user prompt.
            clarifications: Ignored; kept on the signature for caller
                compatibility.
            enriched_prompt: Enriched version of the prompt.
            context: Additional context. ``context["anomalies"]`` (list[dict])
                is extracted into ``episode_anomalies`` rows; the remaining
                keys are flattened into the ``context_metrics`` JSON blob.
            tags: Optional tags for categorization.
            episode_id: Optional specific ID (auto-generated when omitted).
            outcome: Episode outcome ("success", "partial", "failed",
                "abandoned").
            success: Ignored; kept on the signature for compatibility.
            duration_seconds: How long the episode took to complete.
            commands_executed: List of commands executed during the episode.
            related_episodes: Ignored; kept on the signature for compatibility.
            workflow_metrics: Optional workflow metrics dict supplied by the
                SubagentStop hook. Recognized keys: agent, session_id,
                task_id, tier, exit_code, plan_status, output_length,
                output_tokens_approx, prompt (wf_prompt), and any of the
                context_metrics sub-blob (metrics, session_events,
                context_anchor_hits, compliance_score, token_costs,
                model_used, skills_injected, default_skills_snapshot).

        Returns:
            Episode ID.
        """
        if not episode_id:
            episode_id = f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        if outcome is not None and outcome not in OUTCOME_VALUES:
            print(f"Warning: Invalid outcome '{outcome}'. Must be one of {OUTCOME_VALUES}", file=sys.stderr)
            outcome = None

        context = context or {}
        workflow_metrics = workflow_metrics or {}

        episode_type = self._determine_type(prompt, context)
        title = self._generate_title(enriched_prompt or prompt)
        timestamp = datetime.now(timezone.utc).isoformat()

        tier = workflow_metrics.get("tier") or context.get("tier")

        # The CHECK on episodes.plan_status accepts NULL or a valid status,
        # never the empty string, so an invalid value is stored as NULL. An
        # absent key is silent; an explicit invalid one is warned and recorded
        # as an invalid_plan_status anomaly carrying the original value.
        raw_plan_status = workflow_metrics.get("plan_status")
        plan_status = raw_plan_status
        invalid_plan_status = None
        if plan_status is not None and plan_status not in VALID_PLAN_STATUSES:
            print(
                f"Warning: Invalid plan_status {raw_plan_status!r}. Must be "
                f"one of {VALID_PLAN_STATUSES} or None -- normalizing to "
                f"NULL and recording the original value as an anomaly.",
                file=sys.stderr,
            )
            invalid_plan_status = raw_plan_status
            plan_status = None

        anomalies = list(context.get("anomalies", []) or [])
        if invalid_plan_status is not None:
            anomalies.append({
                "type": "invalid_plan_status",
                "severity": "warning",
                "message": (
                    f"plan_status normalized to NULL; original value was "
                    f"{invalid_plan_status!r}"
                ),
                "original_value": invalid_plan_status,
            })
        context_for_blob = {k: v for k, v in context.items() if k != "anomalies"}

        fields = {
            "timestamp": timestamp,
            "session_id": workflow_metrics.get("session_id"),
            "task_id": workflow_metrics.get("task_id"),
            "agent": workflow_metrics.get("agent"),
            "tier": tier,
            "type": episode_type,
            "title": title,
            "prompt": prompt,
            "enriched_prompt": enriched_prompt or prompt,
            "wf_prompt": workflow_metrics.get("prompt"),
            "tags": tags or [],
            "commands_executed": commands_executed or [],
            "context_metrics": context_for_blob,
            "outcome": outcome,
            "duration_seconds": duration_seconds,
            "exit_code": workflow_metrics.get("exit_code"),
            "plan_status": plan_status,
            "output_length": workflow_metrics.get("output_length"),
            "output_tokens_approx": workflow_metrics.get("output_tokens_approx"),
        }

        workspace = self._resolve_workspace()

        try:
            from gaia.store.writer import insert_episode, insert_episode_anomaly
        except ImportError as exc:
            raise RuntimeError(
                f"gaia.store.writer not importable -- episode {episode_id} "
                f"not persisted: {exc}. Set GAIA_PACKAGE_ROOT or install "
                f"gaia as a Python package on sys.path."
            ) from exc

        result = insert_episode(workspace, episode_id, fields, db_path=self.db_path)
        if result.get("status") != "applied":
            raise RuntimeError(
                f"insert_episode rejected for {episode_id}: "
                f"{result.get('reason', 'unknown')}"
            )

        # An anomaly that fails to persist is logged and does not abort: the
        # parent row is already in the DB.
        for anomaly in anomalies:
            if not isinstance(anomaly, dict):
                continue
            anomaly_fields = {
                "timestamp": anomaly.get("timestamp") or timestamp,
                "type": anomaly.get("type", "unknown"),
                "severity": anomaly.get("severity"),
                "message": anomaly.get("message"),
                "payload": anomaly,
            }
            sub_result = insert_episode_anomaly(
                workspace, episode_id, anomaly_fields, db_path=self.db_path
            )
            if sub_result.get("status") != "applied":
                print(
                    f"Warning: insert_episode_anomaly failed for {episode_id}: "
                    f"{sub_result.get('reason', 'unknown')}",
                    file=sys.stderr,
                )

        print(
            f"Stored episode: {episode_id} (workspace={workspace}, "
            f"anomalies={len(anomalies)})",
            file=sys.stderr,
        )
        return episode_id

    def _resolve_workspace(self) -> str:
        """Resolve the workspace name to insert episodes under.

        Delegates to the canonical :func:`gaia.project.resolve_workspace`
        cascade (``GAIA_DISPATCH_WORKSPACE`` -> ``GAIA_WORKSPACE`` ->
        ``gaia.project.current()`` -> ``"global"``) so the resolution order
        stays in lockstep with every other writer that attributes a row
        (notably the harness event writer). Any failure falls back to
        ``"global"`` -- never an empty workspace.
        """
        try:
            from gaia.project import resolve_workspace
            return resolve_workspace()
        except Exception:
            return "global"

    def update_outcome(
        self,
        episode_id: str,
        outcome: str,
        success: bool,
        duration_seconds: Optional[float] = None,
        commands_executed: Optional[List[str]] = None
    ) -> bool:
        """
        Update the outcome of an existing episode.

        Args:
            episode_id: Episode ID to update
            outcome: New outcome ("success", "partial", "failed", "abandoned")
            success: Boolean indicating success
            duration_seconds: Optional duration in seconds
            commands_executed: Optional list of commands that were executed

        Returns:
            True if updated successfully, False if episode not found or invalid outcome
        """
        if outcome not in OUTCOME_VALUES:
            print(f"Error: Invalid outcome '{outcome}'. Must be one of {OUTCOME_VALUES}", file=sys.stderr)
            return False

        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if not episode_file.exists():
            print(f"Error: Episode {episode_id} not found", file=sys.stderr)
            return False

        try:
            with open(episode_file, 'r') as f:
                episode_data = json.load(f)

            episode_data["outcome"] = outcome
            episode_data["success"] = success
            if duration_seconds is not None:
                episode_data["duration_seconds"] = duration_seconds
            if commands_executed is not None:
                episode_data["commands_executed"] = commands_executed

            with open(episode_file, 'w') as f:
                json.dump(episode_data, f, indent=2)

            # Append outcome update to JSONL (as a separate event for audit trail)
            with open(self.episodes_jsonl, 'a') as f:
                outcome_event = {
                    "event_type": "outcome_update",
                    "episode_id": episode_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "outcome": outcome,
                    "success": success,
                    "duration_seconds": duration_seconds,
                    "commands_executed": commands_executed
                }
                f.write(json.dumps(outcome_event) + '\n')

            index = self._load_index()
            for ep in index["episodes"]:
                if ep.get("id") == episode_id:
                    ep["outcome"] = outcome
                    ep["success"] = success
                    break
            index["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save_index(index)

            print(f"Updated outcome for episode {episode_id}: {outcome} (success={success})", file=sys.stderr)
            return True

        except (json.JSONDecodeError, IOError) as e:
            print(f"Error updating episode {episode_id}: {e}", file=sys.stderr)
            return False

    def add_relationship(
        self,
        source_episode_id: str,
        target_episode_id: str,
        relationship_type: str
    ) -> bool:
        """
        Add a relationship between two episodes.

        Args:
            source_episode_id: The source episode ID
            target_episode_id: The target episode ID
            relationship_type: Type of relationship (SOLVES, CAUSES, DEPENDS_ON, etc.)

        Returns:
            True if relationship added successfully, False otherwise
        """
        if relationship_type not in RELATIONSHIP_TYPES:
            print(f"Error: Invalid relationship type '{relationship_type}'. Must be one of {RELATIONSHIP_TYPES}", file=sys.stderr)
            return False

        source_file = self.episodes_dir / f"episode-{source_episode_id}.json"
        if not source_file.exists():
            print(f"Error: Source episode {source_episode_id} not found", file=sys.stderr)
            return False

        # Check target episode exists (optional - might reference external or future episode)
        target_file = self.episodes_dir / f"episode-{target_episode_id}.json"
        target_exists = target_file.exists()

        try:
            with open(source_file, 'r') as f:
                source_data = json.load(f)

            if "related_episodes" not in source_data or source_data["related_episodes"] is None:
                source_data["related_episodes"] = []

            for rel in source_data["related_episodes"]:
                if rel.get("id") == target_episode_id and rel.get("type") == relationship_type:
                    print(f"Relationship already exists: {source_episode_id} --{relationship_type}--> {target_episode_id}", file=sys.stderr)
                    return True

            source_data["related_episodes"].append({
                "id": target_episode_id,
                "type": relationship_type
            })

            with open(source_file, 'w') as f:
                json.dump(source_data, f, indent=2)

            with open(self.episodes_jsonl, 'a') as f:
                rel_event = {
                    "event_type": "relationship_added",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": source_episode_id,
                    "target": target_episode_id,
                    "type": relationship_type,
                    "target_exists": target_exists
                }
                f.write(json.dumps(rel_event) + '\n')

            index = self._load_index()
            index["relationships"].append({
                "source": source_episode_id,
                "target": target_episode_id,
                "type": relationship_type,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            for ep in index["episodes"]:
                if ep.get("id") == source_episode_id:
                    ep["relationship_count"] = ep.get("relationship_count", 0) + 1
                    break

            index["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
            self._save_index(index)

            print(f"Added relationship: {source_episode_id} --{relationship_type}--> {target_episode_id}", file=sys.stderr)
            return True

        except (json.JSONDecodeError, IOError) as e:
            print(f"Error adding relationship: {e}", file=sys.stderr)
            return False

    def get_related_episodes(
        self,
        episode_id: str,
        relationship_type: Optional[str] = None,
        direction: str = "outgoing"
    ) -> List[Dict[str, Any]]:
        """
        Get episodes related to the given episode.

        Args:
            episode_id: The episode to find relationships for
            relationship_type: Optional filter by relationship type
            direction: "outgoing" (this episode points to), "incoming" (points to this), or "both"

        Returns:
            List of related episodes with relationship info
        """
        results = []
        index = self._load_index()

        for rel in index.get("relationships", []):
            match = False

            if direction in ("outgoing", "both") and rel.get("source") == episode_id:
                match = True
                related_id = rel.get("target")
                rel_direction = "outgoing"
            elif direction in ("incoming", "both") and rel.get("target") == episode_id:
                match = True
                related_id = rel.get("source")
                rel_direction = "incoming"

            if not match:
                continue

            if relationship_type and rel.get("type") != relationship_type:
                continue

            related_episode = self.get_episode(related_id)
            if related_episode:
                results.append({
                    "episode": related_episode,
                    "relationship_type": rel.get("type"),
                    "direction": rel_direction,
                    "relationship_timestamp": rel.get("timestamp")
                })

        return results

    def search_episodes(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.1,
        include_relationships: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant episodes based on query.

        Args:
            query: Search query
            max_results: Maximum number of results to return
            min_score: Minimum relevance score threshold
            include_relationships: If True, include related episode summaries in results

        Returns:
            List of relevant episodes with match scores
        """
        index = self._load_index()
        if not index.get("episodes"):
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored_episodes = []

        for episode_meta in index["episodes"]:
            score = 0.0

            for tag in episode_meta.get("tags", []):
                if tag.lower() in query_lower:
                    score += 0.4

            episode_keywords = set(episode_meta.get("keywords", []))
            common_keywords = query_words & episode_keywords
            if common_keywords:
                score += 0.3 * (len(common_keywords) / max(len(episode_keywords), 1))

            title_words = set(episode_meta.get("title", "").lower().split())
            common_title = query_words & title_words
            if common_title:
                score += 0.2 * (len(common_title) / max(len(title_words), 1))

            if episode_meta.get("type", "") in query_lower:
                score += 0.1

            if episode_meta.get("success") is True:
                score *= 1.1
            elif episode_meta.get("success") is False:
                # Don't penalize failed episodes - they're valuable for troubleshooting
                pass

            try:
                episode_date = datetime.fromisoformat(episode_meta["timestamp"])
                if episode_date.tzinfo is None:
                    episode_date = episode_date.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - episode_date).days

                if age_days < 7:
                    time_factor = 1.0
                elif age_days < 30:
                    time_factor = 0.9
                elif age_days < 90:
                    time_factor = 0.7
                elif age_days < 180:
                    time_factor = 0.5
                else:
                    time_factor = 0.3
            except:
                time_factor = 0.5

            final_score = score * time_factor * episode_meta.get("relevance_score", 1.0)

            if final_score >= min_score:
                full_episode = self.get_episode(episode_meta["id"])
                if full_episode:
                    full_episode["match_score"] = final_score

                    if include_relationships:
                        relationships = self.get_related_episodes(episode_meta["id"], direction="both")
                        if relationships:
                            full_episode["related_episodes_summary"] = [
                                {
                                    "id": r["episode"].get("episode_id", r["episode"].get("id")),
                                    "title": r["episode"].get("title", "Untitled"),
                                    "type": r["relationship_type"],
                                    "direction": r["direction"],
                                    "outcome": r["episode"].get("outcome")
                                }
                                for r in relationships[:5]
                            ]

                    scored_episodes.append(full_episode)

        scored_episodes.sort(key=lambda x: x["match_score"], reverse=True)
        top_episodes = scored_episodes[:max_results]

        if top_episodes:
            print(f"Found {len(top_episodes)} relevant episodes from {len(index['episodes'])} total", file=sys.stderr)

        return top_episodes

    def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific episode by ID.

        Args:
            episode_id: Episode ID to retrieve

        Returns:
            Episode dict or None if not found
        """
        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if episode_file.exists():
            try:
                with open(episode_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        if self.episodes_jsonl.exists():
            try:
                with open(self.episodes_jsonl, 'r') as f:
                    for line in f:
                        try:
                            episode = json.loads(line)
                            if episode.get("episode_id") == episode_id or episode.get("id") == episode_id:
                                return episode
                        except json.JSONDecodeError:
                            continue
            except IOError:
                pass

        return None

    def list_episodes(self, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List episodes with pagination.

        Args:
            limit: Maximum number of episodes to return
            offset: Number of episodes to skip

        Returns:
            List of episode metadata
        """
        index = self._load_index()
        episodes = index.get("episodes", [])

        episodes.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return episodes[offset:offset + limit]

    def delete_episode(self, episode_id: str) -> bool:
        """
        Delete an episode from memory.

        Args:
            episode_id: Episode ID to delete

        Returns:
            True if deleted, False if not found
        """
        deleted = False

        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if episode_file.exists():
            episode_file.unlink()
            deleted = True

        index = self._load_index()
        original_count = len(index.get("episodes", []))
        index["episodes"] = [ep for ep in index.get("episodes", [])
                           if ep.get("id") != episode_id]

        index["relationships"] = [
            rel for rel in index.get("relationships", [])
            if rel.get("source") != episode_id and rel.get("target") != episode_id
        ]

        if len(index["episodes"]) < original_count:
            self._save_index(index)
            deleted = True

        # episodes.jsonl is append-only, so it keeps the deleted episode.
        return deleted

    def cleanup_old_episodes(self, days: int = 180) -> int:
        """
        Remove episodes older than specified days.

        Args:
            days: Age threshold in days

        Returns:
            Number of episodes deleted
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        deleted_count = 0

        index = self._load_index()
        episodes_to_keep = []
        deleted_ids = set()

        for episode_meta in index.get("episodes", []):
            try:
                episode_date = datetime.fromisoformat(episode_meta["timestamp"])
                if episode_date.tzinfo is None:
                    episode_date = episode_date.replace(tzinfo=timezone.utc)

                if episode_date > cutoff_date:
                    episodes_to_keep.append(episode_meta)
                else:
                    episode_file = self.episodes_dir / f"episode-{episode_meta['id']}.json"
                    if episode_file.exists():
                        episode_file.unlink()
                    deleted_ids.add(episode_meta['id'])
                    deleted_count += 1
            except:
                # Keep episodes with invalid timestamps
                episodes_to_keep.append(episode_meta)

        if deleted_count > 0:
            index["episodes"] = episodes_to_keep
            index["relationships"] = [
                rel for rel in index.get("relationships", [])
                if rel.get("source") not in deleted_ids and rel.get("target") not in deleted_ids
            ]
            index["metadata"]["last_cleanup"] = datetime.now(timezone.utc).isoformat()
            self._save_index(index)

            print(f"Cleaned up {deleted_count} episodes older than {days} days", file=sys.stderr)

        return deleted_count

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the episodic memory.

        Returns:
            Dict with statistics including outcome and relationship stats
        """
        index = self._load_index()
        episodes = index.get("episodes", [])

        if not episodes:
            return {
                "total_episodes": 0,
                "types": {},
                "outcomes": {},
                "relationships": {},
                "recent_episodes": []
            }

        type_counts = {}
        for ep in episodes:
            ep_type = ep.get("type", "unknown")
            type_counts[ep_type] = type_counts.get(ep_type, 0) + 1

        outcome_counts = {"success": 0, "partial": 0, "failed": 0, "abandoned": 0, "unknown": 0}
        for ep in episodes:
            outcome = ep.get("outcome", "unknown")
            if outcome in outcome_counts:
                outcome_counts[outcome] += 1
            else:
                outcome_counts["unknown"] += 1

        relationship_counts = {}
        for rel in index.get("relationships", []):
            rel_type = rel.get("type", "unknown")
            relationship_counts[rel_type] = relationship_counts.get(rel_type, 0) + 1

        recent = sorted(episodes, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]

        ages = []
        now = datetime.now(timezone.utc)
        for ep in episodes:
            try:
                ep_date = datetime.fromisoformat(ep["timestamp"])
                if ep_date.tzinfo is None:
                    ep_date = ep_date.replace(tzinfo=timezone.utc)
                ages.append((now - ep_date).days)
            except:
                pass

        stats = {
            "total_episodes": len(episodes),
            "types": type_counts,
            "outcomes": outcome_counts,
            "total_relationships": len(index.get("relationships", [])),
            "relationship_types": relationship_counts,
            "recent_episodes": recent,
            "storage_size_mb": self._calculate_storage_size() / (1024 * 1024),
            "index_size_kb": self.index_file.stat().st_size / 1024 if self.index_file.exists() else 0
        }

        if ages:
            stats["age_stats"] = {
                "newest_days": min(ages),
                "oldest_days": max(ages),
                "average_days": sum(ages) / len(ages)
            }

        return stats

    def capture_git_state(self, repo_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """
        Capture current git state as part of episode context.

        Args:
            repo_path: Path to git repository. Defaults to current working directory.

        Returns:
            Dict with git state including:
            - branch: Current branch name
            - commit: Current commit hash
            - status: List of modified files
            - recent_commits: Last 5 commits (hash, message, timestamp)
        """
        import subprocess

        repo_path = Path(repo_path) if repo_path else Path.cwd()
        git_state = {
            "branch": None,
            "commit": None,
            "status": [],
            "recent_commits": [],
            "is_git_repo": False
        }

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return git_state

            git_state["is_git_repo"] = True

            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                git_state["branch"] = result.stdout.strip()

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                git_state["commit"] = result.stdout.strip()[:12]

            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                git_state["status"] = result.stdout.strip().split("\n")

            result = subprocess.run(
                ["git", "log", "--oneline", "-5", "--pretty=format:%H|%s|%ai"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    if line and "|" in line:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            git_state["recent_commits"].append({
                                "hash": parts[0][:12],
                                "message": parts[1],
                                "timestamp": parts[2]
                            })

        except subprocess.TimeoutExpired:
            print("Warning: Git command timed out", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not capture git state: {e}", file=sys.stderr)

        return git_state

    def _calculate_storage_size(self) -> float:
        """Calculate total storage size used by episodic memory."""
        total_size = 0

        if self.index_file.exists():
            total_size += self.index_file.stat().st_size

        if self.episodes_jsonl.exists():
            total_size += self.episodes_jsonl.stat().st_size

        if self.episodes_dir.exists():
            for episode_file in self.episodes_dir.glob("episode-*.json"):
                total_size += episode_file.stat().st_size

        return total_size


def search_episodic_memory(user_prompt: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Search the default episodic memory without instantiating EpisodicMemory.

    Returns an empty list when the search raises.

    Args:
        user_prompt: User's request to search for
        max_results: Maximum episodes to return

    Returns:
        List of relevant episodes with match scores
    """
    try:
        memory = EpisodicMemory()
        return memory.search_episodes(user_prompt, max_results)
    except Exception as e:
        print(f"Warning: Could not search episodic memory: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Episodic Memory Management")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    store_parser = subparsers.add_parser("store", help="Store a new episode")
    store_parser.add_argument("prompt", help="User prompt")
    store_parser.add_argument("--enriched", help="Enriched prompt")
    store_parser.add_argument("--tags", nargs="+", help="Tags")
    store_parser.add_argument("--outcome", choices=["success", "partial", "failed", "abandoned"], help="Episode outcome")
    store_parser.add_argument("--duration", type=float, help="Duration in seconds")

    search_parser = subparsers.add_parser("search", help="Search episodes")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Max results")
    search_parser.add_argument("--include-relationships", action="store_true", help="Include related episodes")

    list_parser = subparsers.add_parser("list", help="List recent episodes")
    list_parser.add_argument("--limit", type=int, default=10, help="Number to show")

    stats_parser = subparsers.add_parser("stats", help="Show statistics")

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean old episodes")
    cleanup_parser.add_argument("--days", type=int, default=180, help="Days to keep")

    outcome_parser = subparsers.add_parser("update-outcome", help="Update episode outcome")
    outcome_parser.add_argument("episode_id", help="Episode ID")
    outcome_parser.add_argument("outcome", choices=["success", "partial", "failed", "abandoned"], help="Outcome")
    outcome_parser.add_argument("--duration", type=float, help="Duration in seconds")

    rel_parser = subparsers.add_parser("add-relationship", help="Add relationship between episodes")
    rel_parser.add_argument("source", help="Source episode ID")
    rel_parser.add_argument("target", help="Target episode ID")
    rel_parser.add_argument("type", choices=list(RELATIONSHIP_TYPES), help="Relationship type")

    related_parser = subparsers.add_parser("get-related", help="Get related episodes")
    related_parser.add_argument("episode_id", help="Episode ID")
    related_parser.add_argument("--type", help="Filter by relationship type")
    related_parser.add_argument("--direction", choices=["outgoing", "incoming", "both"], default="both", help="Direction")

    args = parser.parse_args()

    memory = EpisodicMemory()

    if args.command == "store":
        episode_id = memory.store_episode(
            prompt=args.prompt,
            enriched_prompt=args.enriched,
            tags=args.tags,
            outcome=args.outcome,
            success=args.outcome == "success" if args.outcome else None,
            duration_seconds=args.duration
        )
        print(f"Stored episode: {episode_id}")

    elif args.command == "search":
        episodes = memory.search_episodes(
            args.query,
            max_results=args.limit,
            include_relationships=args.include_relationships
        )
        for i, ep in enumerate(episodes, 1):
            print(f"\n{i}. [{ep.get('match_score', 0):.2f}] {ep.get('title', 'Untitled')}")
            print(f"   ID: {ep.get('episode_id', ep.get('id'))}")
            print(f"   Type: {ep.get('type', 'unknown')}")
            print(f"   Outcome: {ep.get('outcome', 'unknown')}")
            print(f"   Timestamp: {ep.get('timestamp', 'unknown')}")
            if ep.get('related_episodes_summary'):
                print(f"   Related: {len(ep['related_episodes_summary'])} episodes")

    elif args.command == "list":
        episodes = memory.list_episodes(limit=args.limit)
        for i, ep in enumerate(episodes, 1):
            print(f"\n{i}. {ep.get('title', 'Untitled')}")
            print(f"   ID: {ep.get('id')}")
            print(f"   Type: {ep.get('type', 'unknown')}")
            print(f"   Outcome: {ep.get('outcome', 'unknown')}")
            print(f"   Timestamp: {ep.get('timestamp', 'unknown')}")

    elif args.command == "stats":
        stats = memory.get_statistics()
        print(f"\nEpisodic Memory Statistics:")
        print(f"  Total episodes: {stats['total_episodes']}")
        print(f"  Storage size: {stats['storage_size_mb']:.2f} MB")
        print(f"  Index size: {stats['index_size_kb']:.2f} KB")

        if stats.get("types"):
            print(f"\n  Episode types:")
            for ep_type, count in stats["types"].items():
                print(f"    {ep_type}: {count}")

        if stats.get("outcomes"):
            print(f"\n  Outcomes:")
            for outcome, count in stats["outcomes"].items():
                if count > 0:
                    print(f"    {outcome}: {count}")

        if stats.get("total_relationships"):
            print(f"\n  Relationships: {stats['total_relationships']} total")
            for rel_type, count in stats.get("relationship_types", {}).items():
                print(f"    {rel_type}: {count}")

        if stats.get("age_stats"):
            print(f"\n  Age statistics:")
            print(f"    Newest: {stats['age_stats']['newest_days']} days")
            print(f"    Oldest: {stats['age_stats']['oldest_days']} days")
            print(f"    Average: {stats['age_stats']['average_days']:.1f} days")

    elif args.command == "cleanup":
        count = memory.cleanup_old_episodes(days=args.days)
        print(f"Cleaned up {count} episodes older than {args.days} days")

    elif args.command == "update-outcome":
        success = memory.update_outcome(
            episode_id=args.episode_id,
            outcome=args.outcome,
            success=args.outcome == "success",
            duration_seconds=args.duration
        )
        if success:
            print(f"Updated outcome for {args.episode_id}")
        else:
            print(f"Failed to update outcome")

    elif args.command == "add-relationship":
        success = memory.add_relationship(
            source_episode_id=args.source,
            target_episode_id=args.target,
            relationship_type=args.type
        )
        if success:
            print(f"Added relationship: {args.source} --{args.type}--> {args.target}")
        else:
            print(f"Failed to add relationship")

    elif args.command == "get-related":
        related = memory.get_related_episodes(
            episode_id=args.episode_id,
            relationship_type=args.type,
            direction=args.direction
        )
        if related:
            print(f"\nRelated episodes for {args.episode_id}:")
            for rel in related:
                ep = rel["episode"]
                print(f"\n  --{rel['relationship_type']}--> ({rel['direction']})")
                print(f"    ID: {ep.get('episode_id', ep.get('id'))}")
                print(f"    Title: {ep.get('title', 'Untitled')}")
                print(f"    Outcome: {ep.get('outcome', 'unknown')}")
        else:
            print(f"No related episodes found for {args.episode_id}")

    else:
        parser.print_help()
