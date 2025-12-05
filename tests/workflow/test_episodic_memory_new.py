#!/usr/bin/env python3
"""
Test suite for the Episodic Memory System

Tests the complete functionality of the episodic memory module including:
- Episode storage and retrieval
- Search functionality
- Index management
- Auto-directory creation
- Cleanup operations
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# Import with correct module name (using hyphen in directory name)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "episodic",
    os.path.join(os.path.dirname(__file__), '../../tools/4-memory/episodic.py')
)
episodic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(episodic)

EpisodicMemory = episodic.EpisodicMemory
Episode = episodic.Episode
search_episodic_memory = episodic.search_episodic_memory


class TestEpisodicMemory:
    """Test suite for EpisodicMemory class"""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        # Cleanup after test
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def memory(self, temp_dir):
        """Create an EpisodicMemory instance with temp directory"""
        return EpisodicMemory(base_path=temp_dir / "episodic-memory")

    def test_auto_directory_creation(self, temp_dir):
        """Test that directories are created automatically"""
        memory_path = temp_dir / "episodic-memory"
        assert not memory_path.exists()

        memory = EpisodicMemory(base_path=memory_path)

        assert memory_path.exists()
        assert (memory_path / "episodes").exists()
        assert (memory_path / "index.json").exists()

    def test_store_episode(self, memory):
        """Test storing a basic episode"""
        episode_id = memory.store_episode(
            prompt="Deploy the API to production",
            clarifications={"environment": "prod"},
            enriched_prompt="Deploy tcm-api service to production environment",
            context={"user": "test", "project": "tcm"}
        )

        assert episode_id is not None
        assert episode_id.startswith("ep_")

        # Verify episode file was created
        episode_file = memory.episodes_dir / f"episode-{episode_id}.json"
        assert episode_file.exists()

        # Verify episode content
        with open(episode_file) as f:
            episode_data = json.load(f)

        assert episode_data["episode_id"] == episode_id
        assert episode_data["prompt"] == "Deploy the API to production"
        assert episode_data["type"] == "deployment"
        assert len(episode_data["keywords"]) > 0

    def test_get_episode(self, memory):
        """Test retrieving a specific episode"""
        # Store an episode
        episode_id = memory.store_episode(
            prompt="Check pod status",
            tags=["kubernetes", "monitoring"]
        )

        # Retrieve it
        episode = memory.get_episode(episode_id)

        assert episode is not None
        assert episode["episode_id"] == episode_id
        assert episode["prompt"] == "Check pod status"
        assert "kubernetes" in episode["tags"]

    def test_search_episodes_by_keywords(self, memory):
        """Test searching episodes by keywords"""
        # Store multiple episodes
        memory.store_episode(
            prompt="Deploy API to production",
            tags=["deployment", "api"]
        )

        memory.store_episode(
            prompt="Fix database connection error",
            tags=["troubleshooting", "database"]
        )

        memory.store_episode(
            prompt="Update API configuration",
            tags=["configuration", "api"]
        )

        # Search for API-related episodes
        results = memory.search_episodes("API", max_results=5)

        assert len(results) == 2
        # Results should be sorted by score
        assert all("api" in r.get("title", "").lower() or
                  "api" in r.get("prompt", "").lower()
                  for r in results)

    def test_search_episodes_by_tags(self, memory):
        """Test searching episodes by tags"""
        # Store episodes with specific tags
        ep1_id = memory.store_episode(
            prompt="Deploy service",
            tags=["kubernetes", "production"]
        )

        ep2_id = memory.store_episode(
            prompt="Check logs",
            tags=["monitoring", "debugging"]
        )

        # Search by tag
        results = memory.search_episodes("kubernetes", max_results=5)

        assert len(results) == 1
        assert results[0]["tags"] == ["kubernetes", "production"]

    def test_list_episodes(self, memory):
        """Test listing episodes with pagination"""
        # Store multiple episodes
        for i in range(15):
            memory.store_episode(prompt=f"Test episode {i}")

        # Test pagination
        first_page = memory.list_episodes(limit=10, offset=0)
        assert len(first_page) == 10

        second_page = memory.list_episodes(limit=10, offset=10)
        assert len(second_page) == 5

        # Episodes should be sorted by timestamp (newest first)
        timestamps = [ep["timestamp"] for ep in first_page]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_delete_episode(self, memory):
        """Test deleting an episode"""
        # Store an episode
        episode_id = memory.store_episode(prompt="To be deleted")

        # Verify it exists
        assert memory.get_episode(episode_id) is not None

        # Delete it
        deleted = memory.delete_episode(episode_id)
        assert deleted is True

        # Verify the file is gone (note: JSONL keeps audit trail)
        episode_file = memory.episodes_dir / f"episode-{episode_id}.json"
        assert not episode_file.exists()

        # Verify it's removed from index
        index = memory._load_index()
        assert not any(ep["id"] == episode_id for ep in index["episodes"])

    def test_get_statistics(self, memory):
        """Test getting memory statistics"""
        # Store various types of episodes
        memory.store_episode(prompt="Deploy service", tags=["deployment"])
        memory.store_episode(prompt="Fix error", tags=["troubleshooting"])
        memory.store_episode(prompt="Create resource", tags=["creation"])
        memory.store_episode(prompt="Check status", tags=["validation"])

        stats = memory.get_statistics()

        assert stats["total_episodes"] == 4
        assert "deployment" in stats["types"]
        assert "troubleshooting" in stats["types"]
        assert len(stats["recent_episodes"]) == 4
        assert stats["storage_size_mb"] >= 0
        assert stats["index_size_kb"] >= 0

    def test_keyword_extraction(self, memory):
        """Test keyword extraction from prompts"""
        keywords = memory._extract_keywords(
            "Deploy the tcm-api service to production environment with kubernetes"
        )

        assert "deploy" in keywords
        assert "service" in keywords
        assert "production" in keywords
        assert "environment" in keywords
        assert "kubernetes" in keywords
        # Stopwords should be filtered out
        assert "the" not in keywords
        assert "to" not in keywords
        assert "with" not in keywords

    def test_episode_type_determination(self, memory):
        """Test automatic episode type determination"""
        deploy_type = memory._determine_type("Deploy API to production", {})
        assert deploy_type == "deployment"

        fix_type = memory._determine_type("Fix connection error", {})
        assert fix_type == "troubleshooting"

        create_type = memory._determine_type("Create new resource", {})
        assert create_type == "creation"

        update_type = memory._determine_type("Update configuration", {})
        assert update_type == "modification"

        check_type = memory._determine_type("Check pod status", {})
        assert check_type == "validation"

        delete_type = memory._determine_type("Remove old files", {})
        assert delete_type == "deletion"

        general_type = memory._determine_type("List resources", {})
        assert general_type == "general"

    def test_empty_memory_operations(self, memory):
        """Test operations on empty memory"""
        # Search should return empty list
        results = memory.search_episodes("anything")
        assert results == []

        # List should return empty list
        episodes = memory.list_episodes()
        assert episodes == []

        # Stats should show zero episodes
        stats = memory.get_statistics()
        assert stats["total_episodes"] == 0
        assert stats["types"] == {}
        assert stats["recent_episodes"] == []

        # Delete non-existent episode should return False
        deleted = memory.delete_episode("non-existent")
        assert deleted is False

    def test_compatibility_function(self, temp_dir):
        """Test the standalone search_episodic_memory function"""
        # Create memory and store episodes
        memory = EpisodicMemory(base_path=temp_dir / "episodic-memory")
        memory.store_episode(
            prompt="Deploy kubernetes service",
            tags=["k8s", "deployment"]
        )

        # Mock the EpisodicMemory to use our temp directory
        original_init = EpisodicMemory.__init__

        def mock_init(self, base_path=None):
            original_init(self, base_path=temp_dir / "episodic-memory")

        EpisodicMemory.__init__ = mock_init

        try:
            # Use compatibility function
            results = search_episodic_memory("kubernetes", max_results=5)
            assert len(results) > 0
        finally:
            # Restore original init
            EpisodicMemory.__init__ = original_init


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])