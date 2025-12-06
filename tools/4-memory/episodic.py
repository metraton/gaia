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
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
import re
from dataclasses import dataclass, asdict
import hashlib


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
            self._save_index({"episodes": [], "metadata": {"created": datetime.now(timezone.utc).isoformat()}})

    def _save_index(self, index_data: Dict[str, Any]):
        """Save index to JSON file."""
        with open(self.index_file, 'w') as f:
            json.dump(index_data, f, indent=2)

    def _load_index(self) -> Dict[str, Any]:
        """Load index from JSON file."""
        if not self.index_file.exists():
            return {"episodes": [], "metadata": {}}

        try:
            with open(self.index_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # Return empty index if file is corrupted
            return {"episodes": [], "metadata": {}}

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
        episode_id: Optional[str] = None
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

        Returns:
            Episode ID
        """
        # Generate episode ID if not provided
        if not episode_id:
            episode_id = f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

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
            relevance_score=1.0
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
            "relevance_score": 1.0
        }
        index["episodes"].append(index_entry)

        # Keep only last 1000 episodes in index (for performance)
        if len(index["episodes"]) > 1000:
            index["episodes"] = index["episodes"][-1000:]

        # Ensure metadata exists
        if "metadata" not in index:
            index["metadata"] = {}
        index["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._save_index(index)

        print(f"💾 Stored episode: {episode_id} with {len(keywords)} keywords", file=sys.stderr)

        return episode_id

    def search_episodes(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.1
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant episodes based on query.

        Args:
            query: Search query
            max_results: Maximum number of results to return
            min_score: Minimum relevance score threshold

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
                    scored_episodes.append(full_episode)

        # Sort by score and return top N
        scored_episodes.sort(key=lambda x: x["match_score"], reverse=True)
        top_episodes = scored_episodes[:max_results]

        if top_episodes:
            print(f"📚 Found {len(top_episodes)} relevant episodes from {len(index['episodes'])} total", file=sys.stderr)

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
                    deleted_count += 1
            except:
                # Keep episodes with invalid timestamps
                episodes_to_keep.append(episode_meta)

        if deleted_count > 0:
            index["episodes"] = episodes_to_keep
            index["metadata"]["last_cleanup"] = datetime.now(timezone.utc).isoformat()
            self._save_index(index)

            print(f">� Cleaned up {deleted_count} episodes older than {days} days", file=sys.stderr)

        return deleted_count

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the episodic memory.

        Returns:
            Dict with statistics
        """
        index = self._load_index()
        episodes = index.get("episodes", [])

        if not episodes:
            return {
                "total_episodes": 0,
                "types": {},
                "recent_episodes": []
            }

        # Count by type
        type_counts = {}
        for ep in episodes:
            ep_type = ep.get("type", "unknown")
            type_counts[ep_type] = type_counts.get(ep_type, 0) + 1

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

    # Search command
    search_parser = subparsers.add_parser("search", help="Search episodes")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Max results")

    # List command
    list_parser = subparsers.add_parser("list", help="List recent episodes")
    list_parser.add_argument("--limit", type=int, default=10, help="Number to show")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")

    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean old episodes")
    cleanup_parser.add_argument("--days", type=int, default=180, help="Days to keep")

    args = parser.parse_args()

    memory = EpisodicMemory()

    if args.command == "store":
        episode_id = memory.store_episode(
            prompt=args.prompt,
            enriched_prompt=args.enriched,
            tags=args.tags
        )
        print(f"Stored episode: {episode_id}")

    elif args.command == "search":
        episodes = memory.search_episodes(args.query, max_results=args.limit)
        for i, ep in enumerate(episodes, 1):
            print(f"\n{i}. [{ep.get('match_score', 0):.2f}] {ep.get('title', 'Untitled')}")
            print(f"   ID: {ep.get('episode_id', ep.get('id'))}")
            print(f"   Type: {ep.get('type', 'unknown')}")
            print(f"   Timestamp: {ep.get('timestamp', 'unknown')}")

    elif args.command == "list":
        episodes = memory.list_episodes(limit=args.limit)
        for i, ep in enumerate(episodes, 1):
            print(f"\n{i}. {ep.get('title', 'Untitled')}")
            print(f"   ID: {ep.get('id')}")
            print(f"   Type: {ep.get('type', 'unknown')}")
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

        if stats.get("age_stats"):
            print(f"\n  Age statistics:")
            print(f"    Newest: {stats['age_stats']['newest_days']} days")
            print(f"    Oldest: {stats['age_stats']['oldest_days']} days")
            print(f"    Average: {stats['age_stats']['average_days']:.1f} days")

    elif args.command == "cleanup":
        count = memory.cleanup_old_episodes(days=args.days)
        print(f"Cleaned up {count} episodes older than {args.days} days")

    else:
        parser.print_help()