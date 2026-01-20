#!/usr/bin/env python3
"""
Episodic Memory System for GAIA-OPS

This module provides functionality to store, index, and search episodic memory
for the workflow system. Episodes capture user interactions, clarifications,
and enriched prompts for future reference and context enhancement.

Architecture:
- Episodes stored as individual JSON files with metadata
- JSONL index for fast keyword-based search
- Automatic directory creation and management
- Integration with workflow.py for context enhancement

P0 Enhancement: Outcome tracking (success/failure/partial, duration, commands)
P1 Enhancement: Simple relationships between episodes (SOLVES, CAUSES, etc.)
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
import hashlib


# Valid relationship types for episode connections
RELATIONSHIP_TYPES = frozenset([
    "SOLVES",       # This episode solves another (problem -> solution)
    "CAUSES",       # This episode caused another (action -> consequence)
    "DEPENDS_ON",   # This episode depends on another
    "VALIDATES",    # This episode validates another
    "SUPERSEDES",   # This episode replaces another
    "RELATED_TO",   # Generic relation
])

# Valid outcome values
OUTCOME_VALUES = frozenset(["success", "partial", "failed", "abandoned"])


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
    # P0: Outcome tracking fields
    outcome: Optional[str] = None  # "success", "partial", "failed", "abandoned"
    success: Optional[bool] = None
    duration_seconds: Optional[float] = None
    commands_executed: Optional[List[str]] = None
    # P1: Simple relationships
    related_episodes: Optional[List[Dict[str, str]]] = None  # [{"id": "ep_xxx", "type": "SOLVES"}]

    def to_dict(self) -> Dict[str, Any]:
        """Convert episode to dictionary."""
        data = asdict(self)
        # Remove None values to keep storage clean
        return {k: v for k, v in data.items() if v is not None}


class EpisodicMemory:
    """
    Manages episodic memory storage and retrieval.

    This class provides methods to:
    - Store new episodes with automatic indexing
    - Search episodes by keywords and context
    - Maintain an efficient index for fast retrieval
    - Auto-create required directory structures
    - Track outcomes and relationships between episodes (P0/P1)
    """

    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        """
        Initialize EpisodicMemory with specified or default path.

        Args:
            base_path: Base directory for episodic memory storage.
                      Defaults to .claude/project-context/episodic-memory/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Try to find the best location
            candidates = [
                Path(".claude/project-context/episodic-memory"),
                Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")
            ]

            # Use first existing or first candidate
            for path in candidates:
                if path.parent.exists():
                    self.base_path = path
                    break
            else:
                self.base_path = candidates[0]

        self.episodes_dir = self.base_path / "episodes"
        self.index_file = self.base_path / "index.json"
        self.episodes_jsonl = self.base_path / "episodes.jsonl"

        # Auto-create directories
        self._ensure_directories()

    def _ensure_directories(self):
        """Create required directories if they don't exist."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(parents=True, exist_ok=True)

        # Create empty index if it doesn't exist
        if not self.index_file.exists():
            self._save_index({
                "episodes": [],
                "relationships": [],  # P1: Track relationships in index
                "metadata": {"created": datetime.now(timezone.utc).isoformat()}
            })

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
            # Return empty index if file is corrupted
            return {"episodes": [], "relationships": [], "metadata": {}}

    def _extract_keywords(self, text: str) -> List[str]:
        """
        Extract keywords from text for indexing.

        Uses simple tokenization and filtering. Can be enhanced with NLP.

        Args:
            text: Text to extract keywords from

        Returns:
            List of keywords
        """
        # Convert to lowercase and split
        words = re.findall(r'\b[a-z]+\b', text.lower())

        # Filter common words (basic stopwords)
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'been', 'be',
                    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
                    'could', 'may', 'might', 'can', 'must', 'shall', 'need', 'dare'}

        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        # Return unique keywords, preserving order
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        return unique_keywords[:20]  # Limit to 20 keywords

    def _generate_title(self, prompt: str) -> str:
        """Generate a short title from the prompt."""
        # Take first 60 characters or first sentence
        title = prompt.split('.')[0] if '.' in prompt else prompt
        return title[:60] + ('...' if len(title) > 60 else '')

    def _determine_type(self, prompt: str, context: Dict[str, Any]) -> str:
        """Determine episode type based on prompt and context."""
        prompt_lower = prompt.lower()

        # Check for common operation types
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
        # P0: Outcome tracking parameters
        outcome: Optional[str] = None,
        success: Optional[bool] = None,
        duration_seconds: Optional[float] = None,
        commands_executed: Optional[List[str]] = None,
        # P1: Relationship parameters
        related_episodes: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Store a new episode in memory.

        Args:
            prompt: Original user prompt
            clarifications: Any clarifications made during processing
            enriched_prompt: Enriched version of the prompt
            context: Additional context information
            tags: Optional tags for categorization
            episode_id: Optional specific ID (auto-generated if not provided)
            outcome: Episode outcome ("success", "partial", "failed", "abandoned")
            success: Boolean indicating if episode was successful
            duration_seconds: How long the episode took to complete
            commands_executed: List of commands executed during episode
            related_episodes: List of related episode references [{"id": "ep_xxx", "type": "SOLVES"}]

        Returns:
            Episode ID
        """
        # Generate episode ID if not provided
        if not episode_id:
            episode_id = f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # Validate outcome if provided
        if outcome is not None and outcome not in OUTCOME_VALUES:
            print(f"Warning: Invalid outcome '{outcome}'. Must be one of {OUTCOME_VALUES}", file=sys.stderr)
            outcome = None

        # Validate relationships if provided
        validated_relationships = None
        if related_episodes:
            validated_relationships = []
            for rel in related_episodes:
                if isinstance(rel, dict) and "id" in rel and "type" in rel:
                    if rel["type"] in RELATIONSHIP_TYPES:
                        validated_relationships.append({"id": rel["id"], "type": rel["type"]})
                    else:
                        print(f"Warning: Invalid relationship type '{rel['type']}'. Skipping.", file=sys.stderr)
            if not validated_relationships:
                validated_relationships = None

        # Extract keywords from prompt and enriched prompt
        all_text = prompt
        if enriched_prompt:
            all_text += " " + enriched_prompt
        keywords = self._extract_keywords(all_text)

        # Add tags to keywords if provided
        if tags:
            keywords = list(set(keywords + [t.lower() for t in tags]))

        # Determine episode type and title
        episode_type = self._determine_type(prompt, context or {})
        title = self._generate_title(enriched_prompt or prompt)

        # Create episode
        episode = Episode(
            episode_id=episode_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            keywords=keywords,
            prompt=prompt,
            clarifications=clarifications or {},
            enriched_prompt=enriched_prompt or prompt,
            context=context or {},
            tags=tags,
            type=episode_type,
            title=title,
            relevance_score=1.0,
            # P0: Outcome fields
            outcome=outcome,
            success=success,
            duration_seconds=duration_seconds,
            commands_executed=commands_executed,
            # P1: Relationships
            related_episodes=validated_relationships
        )

        # Save full episode to file
        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        with open(episode_file, 'w') as f:
            json.dump(episode.to_dict(), f, indent=2)

        # Append to JSONL file
        with open(self.episodes_jsonl, 'a') as f:
            f.write(json.dumps(episode.to_dict()) + '\n')

        # Update index
        index = self._load_index()
        index_entry = {
            "id": episode_id,
            "timestamp": episode.timestamp,
            "keywords": keywords[:10],  # Store limited keywords in index
            "tags": tags or [],
            "type": episode_type,
            "title": title,
            "relevance_score": 1.0,
            # P0: Include outcome summary in index
            "outcome": outcome,
            "success": success,
            # P1: Include relationship count in index
            "relationship_count": len(validated_relationships) if validated_relationships else 0
        }
        index["episodes"].append(index_entry)

        # P1: Add relationships to index for fast lookup
        if validated_relationships:
            for rel in validated_relationships:
                index["relationships"].append({
                    "source": episode_id,
                    "target": rel["id"],
                    "type": rel["type"],
                    "timestamp": episode.timestamp
                })

        # Keep only last 1000 episodes in index (for performance)
        if len(index["episodes"]) > 1000:
            index["episodes"] = index["episodes"][-1000:]

        # Keep only last 5000 relationships in index
        if len(index["relationships"]) > 5000:
            index["relationships"] = index["relationships"][-5000:]

        # Ensure metadata exists
        if "metadata" not in index:
            index["metadata"] = {}
        index["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._save_index(index)

        print(f"Stored episode: {episode_id} with {len(keywords)} keywords", file=sys.stderr)

        return episode_id

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
        # Validate outcome
        if outcome not in OUTCOME_VALUES:
            print(f"Error: Invalid outcome '{outcome}'. Must be one of {OUTCOME_VALUES}", file=sys.stderr)
            return False

        # Load the episode
        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if not episode_file.exists():
            print(f"Error: Episode {episode_id} not found", file=sys.stderr)
            return False

        try:
            with open(episode_file, 'r') as f:
                episode_data = json.load(f)

            # Update outcome fields
            episode_data["outcome"] = outcome
            episode_data["success"] = success
            if duration_seconds is not None:
                episode_data["duration_seconds"] = duration_seconds
            if commands_executed is not None:
                episode_data["commands_executed"] = commands_executed

            # Save updated episode
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

            # Update index
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
        # Validate relationship type
        if relationship_type not in RELATIONSHIP_TYPES:
            print(f"Error: Invalid relationship type '{relationship_type}'. Must be one of {RELATIONSHIP_TYPES}", file=sys.stderr)
            return False

        # Check source episode exists
        source_file = self.episodes_dir / f"episode-{source_episode_id}.json"
        if not source_file.exists():
            print(f"Error: Source episode {source_episode_id} not found", file=sys.stderr)
            return False

        # Check target episode exists (optional - might reference external or future episode)
        target_file = self.episodes_dir / f"episode-{target_episode_id}.json"
        target_exists = target_file.exists()

        try:
            # Load source episode
            with open(source_file, 'r') as f:
                source_data = json.load(f)

            # Initialize or get existing relationships
            if "related_episodes" not in source_data or source_data["related_episodes"] is None:
                source_data["related_episodes"] = []

            # Check if relationship already exists
            for rel in source_data["related_episodes"]:
                if rel.get("id") == target_episode_id and rel.get("type") == relationship_type:
                    print(f"Relationship already exists: {source_episode_id} --{relationship_type}--> {target_episode_id}", file=sys.stderr)
                    return True  # Not an error, just already exists

            # Add new relationship
            source_data["related_episodes"].append({
                "id": target_episode_id,
                "type": relationship_type
            })

            # Save updated episode
            with open(source_file, 'w') as f:
                json.dump(source_data, f, indent=2)

            # Append relationship event to JSONL
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

            # Update index
            index = self._load_index()
            index["relationships"].append({
                "source": source_episode_id,
                "target": target_episode_id,
                "type": relationship_type,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Update relationship count in episode index entry
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

        # Find relationships in index
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

            # Filter by type if specified
            if relationship_type and rel.get("type") != relationship_type:
                continue

            # Load the related episode
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

            # Tag matching (highest weight)
            for tag in episode_meta.get("tags", []):
                if tag.lower() in query_lower:
                    score += 0.4

            # Keyword matching
            episode_keywords = set(episode_meta.get("keywords", []))
            common_keywords = query_words & episode_keywords
            if common_keywords:
                score += 0.3 * (len(common_keywords) / max(len(episode_keywords), 1))

            # Title matching
            title_words = set(episode_meta.get("title", "").lower().split())
            common_title = query_words & title_words
            if common_title:
                score += 0.2 * (len(common_title) / max(len(title_words), 1))

            # Type matching
            if episode_meta.get("type", "") in query_lower:
                score += 0.1

            # P0: Boost successful episodes slightly
            if episode_meta.get("success") is True:
                score *= 1.1
            elif episode_meta.get("success") is False:
                # Don't penalize failed episodes - they're valuable for troubleshooting
                pass

            # Apply time decay
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
                # Load full episode if score meets threshold
                full_episode = self.get_episode(episode_meta["id"])
                if full_episode:
                    full_episode["match_score"] = final_score

                    # P1: Include relationship summaries if requested
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
                                for r in relationships[:5]  # Limit to 5 related episodes
                            ]

                    scored_episodes.append(full_episode)

        # Sort by score and return top N
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
        # First try individual file
        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if episode_file.exists():
            try:
                with open(episode_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # Fall back to JSONL file
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

        # Sort by timestamp (newest first)
        episodes.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        # Apply pagination
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

        # Delete from individual file
        episode_file = self.episodes_dir / f"episode-{episode_id}.json"
        if episode_file.exists():
            episode_file.unlink()
            deleted = True

        # Update index
        index = self._load_index()
        original_count = len(index.get("episodes", []))
        index["episodes"] = [ep for ep in index.get("episodes", [])
                           if ep.get("id") != episode_id]

        # Also remove relationships involving this episode
        index["relationships"] = [
            rel for rel in index.get("relationships", [])
            if rel.get("source") != episode_id and rel.get("target") != episode_id
        ]

        if len(index["episodes"]) < original_count:
            self._save_index(index)
            deleted = True

        # Note: We don't remove from JSONL as it's append-only for audit trail

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
                    # Delete old episode file
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
            # Also clean up relationships involving deleted episodes
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

        # Count by type
        type_counts = {}
        for ep in episodes:
            ep_type = ep.get("type", "unknown")
            type_counts[ep_type] = type_counts.get(ep_type, 0) + 1

        # P0: Count by outcome
        outcome_counts = {"success": 0, "partial": 0, "failed": 0, "abandoned": 0, "unknown": 0}
        for ep in episodes:
            outcome = ep.get("outcome", "unknown")
            if outcome in outcome_counts:
                outcome_counts[outcome] += 1
            else:
                outcome_counts["unknown"] += 1

        # P1: Count relationships by type
        relationship_counts = {}
        for rel in index.get("relationships", []):
            rel_type = rel.get("type", "unknown")
            relationship_counts[rel_type] = relationship_counts.get(rel_type, 0) + 1

        # Get recent episodes
        recent = sorted(episodes, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]

        # Calculate age statistics
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

        Migrated from session system to provide git context for episodes.

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
            # Check if it is a git repo
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

            # Get current branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                git_state["branch"] = result.stdout.strip()

            # Get current commit
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                git_state["commit"] = result.stdout.strip()[:12]

            # Get status (modified files)
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                git_state["status"] = result.stdout.strip().split("\n")

            # Get recent commits
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

        # Add index file size
        if self.index_file.exists():
            total_size += self.index_file.stat().st_size

        # Add JSONL file size
        if self.episodes_jsonl.exists():
            total_size += self.episodes_jsonl.stat().st_size

        # Add episode files sizes
        if self.episodes_dir.exists():
            for episode_file in self.episodes_dir.glob("episode-*.json"):
                total_size += episode_file.stat().st_size

        return total_size


# Compatibility function for direct use in workflow.py
def search_episodic_memory(user_prompt: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Compatibility function for workflow.py integration.

    This function can be imported and used directly without instantiating EpisodicMemory.

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


# CLI interface for testing and management
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Episodic Memory Management")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Store command
    store_parser = subparsers.add_parser("store", help="Store a new episode")
    store_parser.add_argument("prompt", help="User prompt")
    store_parser.add_argument("--enriched", help="Enriched prompt")
    store_parser.add_argument("--tags", nargs="+", help="Tags")
    store_parser.add_argument("--outcome", choices=["success", "partial", "failed", "abandoned"], help="Episode outcome")
    store_parser.add_argument("--duration", type=float, help="Duration in seconds")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search episodes")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Max results")
    search_parser.add_argument("--include-relationships", action="store_true", help="Include related episodes")

    # List command
    list_parser = subparsers.add_parser("list", help="List recent episodes")
    list_parser.add_argument("--limit", type=int, default=10, help="Number to show")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")

    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean old episodes")
    cleanup_parser.add_argument("--days", type=int, default=180, help="Days to keep")

    # Update outcome command
    outcome_parser = subparsers.add_parser("update-outcome", help="Update episode outcome")
    outcome_parser.add_argument("episode_id", help="Episode ID")
    outcome_parser.add_argument("outcome", choices=["success", "partial", "failed", "abandoned"], help="Outcome")
    outcome_parser.add_argument("--duration", type=float, help="Duration in seconds")

    # Add relationship command
    rel_parser = subparsers.add_parser("add-relationship", help="Add relationship between episodes")
    rel_parser.add_argument("source", help="Source episode ID")
    rel_parser.add_argument("target", help="Target episode ID")
    rel_parser.add_argument("type", choices=list(RELATIONSHIP_TYPES), help="Relationship type")

    # Get related command
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
