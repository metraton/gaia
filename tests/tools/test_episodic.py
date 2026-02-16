#!/usr/bin/env python3
"""
Tests for Episodic Memory System.

PRIORITY: MEDIUM - Important for context persistence.

Validates:
1. Memory read/write operations (store, retrieve, search)
2. Path resolution and directory creation
3. Error handling (missing dirs, corrupted files)
4. Outcome tracking (P0)
5. Relationship management (P1)
6. Edge cases
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add tools to path
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from memory.episodic import (
    EpisodicMemory,
    Episode,
    search_episodic_memory,
    RELATIONSHIP_TYPES,
    OUTCOME_VALUES,
)


@pytest.fixture
def memory(tmp_path):
    """Create an EpisodicMemory instance with temporary directory."""
    return EpisodicMemory(base_path=tmp_path / "episodic-memory")


@pytest.fixture
def populated_memory(memory):
    """Create an EpisodicMemory with some stored episodes."""
    memory.store_episode(
        prompt="Deploy terraform infrastructure",
        enriched_prompt="Deploy terraform infrastructure to GCP us-central1",
        tags=["terraform", "deploy"],
        episode_id="ep_test_001",
        outcome="success",
        success=True,
        duration_seconds=120.5
    )
    memory.store_episode(
        prompt="Fix broken kubectl pods",
        enriched_prompt="Fix broken kubectl pods in production namespace",
        tags=["kubectl", "troubleshoot"],
        episode_id="ep_test_002",
        outcome="failed",
        success=False
    )
    memory.store_episode(
        prompt="Create new helm chart for API",
        enriched_prompt="Create new helm chart for API service",
        tags=["helm", "create"],
        episode_id="ep_test_003"
    )
    return memory


class TestDirectoryCreation:
    """Test automatic directory creation."""

    def test_creates_base_directory(self, tmp_path):
        """Test base directory is created automatically."""
        base = tmp_path / "new-memory"
        memory = EpisodicMemory(base_path=base)
        assert base.exists()

    def test_creates_episodes_directory(self, tmp_path):
        """Test episodes subdirectory is created."""
        base = tmp_path / "new-memory"
        memory = EpisodicMemory(base_path=base)
        assert (base / "episodes").exists()

    def test_creates_initial_index(self, tmp_path):
        """Test initial index.json is created."""
        base = tmp_path / "new-memory"
        memory = EpisodicMemory(base_path=base)
        assert (base / "index.json").exists()


class TestStoreEpisode:
    """Test episode storage."""

    def test_stores_episode_file(self, memory):
        """Test episode is saved as individual JSON file."""
        ep_id = memory.store_episode(
            prompt="Test prompt",
            episode_id="ep_store_test"
        )
        episode_file = memory.episodes_dir / f"episode-{ep_id}.json"
        assert episode_file.exists()

    def test_stores_with_auto_generated_id(self, memory):
        """Test episode ID is auto-generated when not provided."""
        ep_id = memory.store_episode(prompt="Auto ID test")
        assert ep_id.startswith("ep_")
        assert len(ep_id) > 10

    def test_stores_with_custom_id(self, memory):
        """Test episode stored with custom ID."""
        ep_id = memory.store_episode(prompt="Custom ID", episode_id="ep_custom_123")
        assert ep_id == "ep_custom_123"

    def test_appends_to_jsonl(self, memory):
        """Test episode is appended to episodes.jsonl."""
        memory.store_episode(prompt="JSONL test", episode_id="ep_jsonl_test")
        assert memory.episodes_jsonl.exists()
        with open(memory.episodes_jsonl) as f:
            lines = f.readlines()
        assert len(lines) >= 1

    def test_updates_index(self, memory):
        """Test index is updated with new episode metadata."""
        memory.store_episode(prompt="Index test", episode_id="ep_index_test")
        index = memory._load_index()
        assert len(index["episodes"]) == 1
        assert index["episodes"][0]["id"] == "ep_index_test"

    def test_stores_keywords(self, memory):
        """Test keywords are extracted and stored."""
        memory.store_episode(
            prompt="Deploy terraform infrastructure to production",
            episode_id="ep_kw_test"
        )
        episode = memory.get_episode("ep_kw_test")
        assert "keywords" in episode
        assert len(episode["keywords"]) > 0

    def test_stores_tags(self, memory):
        """Test tags are stored with episode."""
        memory.store_episode(
            prompt="Tagged episode",
            tags=["terraform", "deploy"],
            episode_id="ep_tags_test"
        )
        episode = memory.get_episode("ep_tags_test")
        assert "terraform" in episode.get("tags", [])

    def test_determines_episode_type(self, memory):
        """Test episode type is automatically determined."""
        memory.store_episode(prompt="Deploy application to production", episode_id="ep_type_test")
        episode = memory.get_episode("ep_type_test")
        assert episode.get("type") == "deployment"

    def test_validates_outcome(self, memory):
        """Test invalid outcome values are rejected."""
        ep_id = memory.store_episode(
            prompt="Bad outcome test",
            outcome="invalid_outcome",
            episode_id="ep_bad_outcome"
        )
        episode = memory.get_episode("ep_bad_outcome")
        # Invalid outcome should be set to None (stripped)
        assert episode.get("outcome") is None


class TestGetEpisode:
    """Test episode retrieval."""

    def test_get_existing_episode(self, populated_memory):
        """Test retrieving an existing episode by ID."""
        episode = populated_memory.get_episode("ep_test_001")
        assert episode is not None
        assert episode["episode_id"] == "ep_test_001"

    def test_get_nonexistent_episode(self, memory):
        """Test retrieving non-existent episode returns None."""
        episode = memory.get_episode("ep_nonexistent")
        assert episode is None

    def test_get_episode_from_jsonl_fallback(self, populated_memory):
        """Test episode retrieval falls back to JSONL when file is missing."""
        # Remove the individual file
        episode_file = populated_memory.episodes_dir / "episode-ep_test_001.json"
        episode_file.unlink()
        # Should still find it in JSONL
        episode = populated_memory.get_episode("ep_test_001")
        assert episode is not None


class TestSearchEpisodes:
    """Test episode search functionality."""

    def test_search_finds_matching_episodes(self, populated_memory):
        """Test search returns relevant episodes."""
        results = populated_memory.search_episodes("terraform deploy")
        assert len(results) > 0

    def test_search_respects_max_results(self, populated_memory):
        """Test search respects max_results limit."""
        results = populated_memory.search_episodes("test", max_results=1)
        assert len(results) <= 1

    def test_search_empty_query(self, populated_memory):
        """Test search with empty query."""
        results = populated_memory.search_episodes("")
        # Should return results (or empty) without crashing
        assert isinstance(results, list)

    def test_search_no_matches(self, populated_memory):
        """Test search with no matching keywords."""
        results = populated_memory.search_episodes("xyznonexistent")
        assert len(results) == 0

    def test_search_results_have_match_score(self, populated_memory):
        """Test search results include match_score."""
        results = populated_memory.search_episodes("terraform")
        if results:
            assert "match_score" in results[0]
            assert results[0]["match_score"] > 0

    def test_search_results_sorted_by_score(self, populated_memory):
        """Test search results are sorted by relevance score."""
        results = populated_memory.search_episodes("deploy helm terraform")
        if len(results) > 1:
            scores = [r["match_score"] for r in results]
            assert scores == sorted(scores, reverse=True)


class TestUpdateOutcome:
    """Test outcome update functionality."""

    def test_updates_outcome_successfully(self, populated_memory):
        """Test successful outcome update."""
        result = populated_memory.update_outcome(
            episode_id="ep_test_003",
            outcome="success",
            success=True,
            duration_seconds=45.0
        )
        assert result is True
        episode = populated_memory.get_episode("ep_test_003")
        assert episode["outcome"] == "success"
        assert episode["success"] is True

    def test_rejects_invalid_outcome(self, populated_memory):
        """Test invalid outcome is rejected."""
        result = populated_memory.update_outcome(
            episode_id="ep_test_003",
            outcome="invalid",
            success=False
        )
        assert result is False

    def test_handles_nonexistent_episode(self, populated_memory):
        """Test updating non-existent episode returns False."""
        result = populated_memory.update_outcome(
            episode_id="ep_nonexistent",
            outcome="success",
            success=True
        )
        assert result is False


class TestRelationships:
    """Test episode relationship management."""

    def test_add_relationship(self, populated_memory):
        """Test adding a relationship between episodes."""
        result = populated_memory.add_relationship(
            source_episode_id="ep_test_001",
            target_episode_id="ep_test_002",
            relationship_type="CAUSES"
        )
        assert result is True

    def test_rejects_invalid_relationship_type(self, populated_memory):
        """Test invalid relationship type is rejected."""
        result = populated_memory.add_relationship(
            source_episode_id="ep_test_001",
            target_episode_id="ep_test_002",
            relationship_type="INVALID_TYPE"
        )
        assert result is False

    def test_rejects_nonexistent_source(self, populated_memory):
        """Test relationship fails when source episode does not exist."""
        result = populated_memory.add_relationship(
            source_episode_id="ep_nonexistent",
            target_episode_id="ep_test_002",
            relationship_type="SOLVES"
        )
        assert result is False

    def test_duplicate_relationship_is_idempotent(self, populated_memory):
        """Test adding same relationship twice returns True (idempotent)."""
        populated_memory.add_relationship(
            "ep_test_001", "ep_test_002", "SOLVES"
        )
        result = populated_memory.add_relationship(
            "ep_test_001", "ep_test_002", "SOLVES"
        )
        assert result is True

    def test_get_related_episodes(self, populated_memory):
        """Test retrieving related episodes."""
        populated_memory.add_relationship(
            "ep_test_001", "ep_test_002", "CAUSES"
        )
        related = populated_memory.get_related_episodes("ep_test_001", direction="outgoing")
        assert len(related) > 0
        assert related[0]["relationship_type"] == "CAUSES"


class TestDeleteEpisode:
    """Test episode deletion."""

    def test_deletes_existing_episode(self, populated_memory):
        """Test deleting an existing episode removes file and index entry."""
        result = populated_memory.delete_episode("ep_test_001")
        assert result is True
        # Individual file should be removed
        episode_file = populated_memory.episodes_dir / "episode-ep_test_001.json"
        assert not episode_file.exists()
        # Note: JSONL is append-only (audit trail), so get_episode may still
        # find it via fallback. The canonical deletion is from file + index.

    def test_delete_nonexistent_returns_false(self, memory):
        """Test deleting non-existent episode returns False."""
        result = memory.delete_episode("ep_nonexistent")
        assert result is False

    def test_delete_removes_from_index(self, populated_memory):
        """Test deletion removes episode from index."""
        populated_memory.delete_episode("ep_test_001")
        index = populated_memory._load_index()
        ids = [ep["id"] for ep in index["episodes"]]
        assert "ep_test_001" not in ids


class TestListEpisodes:
    """Test episode listing."""

    def test_lists_episodes(self, populated_memory):
        """Test listing episodes returns results."""
        episodes = populated_memory.list_episodes()
        assert len(episodes) == 3

    def test_list_respects_limit(self, populated_memory):
        """Test listing respects limit parameter."""
        episodes = populated_memory.list_episodes(limit=2)
        assert len(episodes) == 2

    def test_list_respects_offset(self, populated_memory):
        """Test listing respects offset parameter."""
        episodes = populated_memory.list_episodes(limit=1, offset=2)
        assert len(episodes) == 1


class TestStatistics:
    """Test statistics generation."""

    def test_returns_statistics(self, populated_memory):
        """Test statistics are generated."""
        stats = populated_memory.get_statistics()
        assert stats["total_episodes"] == 3
        assert "types" in stats
        assert "outcomes" in stats

    def test_empty_memory_statistics(self, memory):
        """Test statistics for empty memory."""
        stats = memory.get_statistics()
        assert stats["total_episodes"] == 0


class TestEpisodeDataclass:
    """Test Episode dataclass."""

    def test_episode_to_dict(self):
        """Test Episode.to_dict() removes None values."""
        episode = Episode(
            episode_id="ep_test",
            timestamp="2026-01-01T00:00:00+00:00",
            keywords=["test"],
            prompt="Test",
            clarifications={},
            enriched_prompt="Test",
            context={},
            outcome=None,  # Should be removed
        )
        d = episode.to_dict()
        assert "outcome" not in d
        assert "episode_id" in d


class TestConstants:
    """Test module constants."""

    def test_relationship_types_defined(self):
        """Test all expected relationship types are defined."""
        assert "SOLVES" in RELATIONSHIP_TYPES
        assert "CAUSES" in RELATIONSHIP_TYPES
        assert "DEPENDS_ON" in RELATIONSHIP_TYPES

    def test_outcome_values_defined(self):
        """Test all expected outcome values are defined."""
        assert "success" in OUTCOME_VALUES
        assert "partial" in OUTCOME_VALUES
        assert "failed" in OUTCOME_VALUES
        assert "abandoned" in OUTCOME_VALUES


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_corrupted_index_file(self, tmp_path):
        """Test handling of corrupted index.json."""
        base = tmp_path / "corrupted-memory"
        base.mkdir(parents=True)
        (base / "episodes").mkdir()
        # Write corrupted index
        (base / "index.json").write_text("{invalid json!!")
        memory = EpisodicMemory(base_path=base)
        index = memory._load_index()
        assert index == {"episodes": [], "relationships": [], "metadata": {}}

    def test_extract_keywords(self, memory):
        """Test keyword extraction filters stopwords."""
        keywords = memory._extract_keywords("the quick brown fox is a test")
        assert "the" not in keywords
        assert "is" not in keywords
        assert "quick" in keywords

    def test_keyword_limit(self, memory):
        """Test keyword extraction limits to 20 keywords."""
        long_text = " ".join([f"word{i}" for i in range(50)])
        keywords = memory._extract_keywords(long_text)
        assert len(keywords) <= 20

    def test_generate_title_truncation(self, memory):
        """Test title generation truncates long prompts."""
        long_prompt = "A" * 100
        title = memory._generate_title(long_prompt)
        assert len(title) <= 63  # 60 + '...'

    def test_determine_type_deployment(self, memory):
        """Test type detection for deployment prompts."""
        assert memory._determine_type("deploy the app", {}) == "deployment"

    def test_determine_type_troubleshooting(self, memory):
        """Test type detection for troubleshooting prompts."""
        assert memory._determine_type("fix the error in pods", {}) == "troubleshooting"

    def test_determine_type_general(self, memory):
        """Test type detection falls back to general."""
        assert memory._determine_type("random unrelated text", {}) == "general"

    def test_search_episodic_memory_convenience(self, tmp_path, monkeypatch):
        """Test convenience function handles errors gracefully."""
        # This will likely fail to find memory but should not crash
        results = search_episodic_memory("test query")
        assert isinstance(results, list)
