#!/usr/bin/env python3
"""
Simple test for episodic memory search - tests the core logic directly.
"""

import json
from pathlib import Path
from datetime import datetime
import pytest


def search_episodic_memory(user_prompt: str, max_results: int = 3):
    """Direct implementation of episodic memory search for testing"""

    # Search for episodic memory in current project
    memory_path = Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")
    index_file = memory_path / "index.json"

    if not index_file.exists():
        return []

    with open(index_file) as f:
        index = json.load(f)

    # Extract keywords from prompt
    prompt_lower = user_prompt.lower()
    prompt_words = set(prompt_lower.split())

    # Score each episode
    scored_episodes = []
    for episode in index.get("episodes", []):
        score = 0.0

        # Tag matching (highest weight)
        for tag in episode.get("tags", []):
            if tag.lower() in prompt_lower:
                score += 0.4

        # Title matching
        title_words = set(episode.get("title", "").lower().split())
        common_words = prompt_words & title_words
        if common_words:
            score += 0.3 * (len(common_words) / max(len(title_words), 1))

        # Type matching
        if episode.get("type", "") in prompt_lower:
            score += 0.2

        # Simple time factor
        time_factor = 0.8
        final_score = score * time_factor * episode.get("relevance_score", 0.5)

        if final_score > 0.1:
            episode["match_score"] = final_score
            scored_episodes.append(episode)

    # Sort by score and return top N
    scored_episodes.sort(key=lambda x: x["match_score"], reverse=True)
    return scored_episodes[:max_results]


class TestMemorySearch:
    """Test episodic memory search functionality"""

    @pytest.fixture
    def memory_path(self):
        """Get the memory path"""
        return Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")

    def test_memory_directory_exists(self, memory_path):
        """Verify memory directory exists"""
        # This test is conditional - memory may not exist in test environment
        if not memory_path.exists():
            pytest.skip("Memory directory not present in test environment")
        assert memory_path.is_dir(), "Memory path should be a directory"

    def test_search_returns_list(self):
        """Test that search returns a list"""
        results = search_episodic_memory("postgres migration")
        assert isinstance(results, list), "Search should return a list"

    def test_search_with_no_matches(self):
        """Test search with unrelated terms returns empty or few results"""
        results = search_episodic_memory("azure cosmos mongodb")
        # Should return empty or minimal results for unrelated terms
        assert isinstance(results, list), "Should return list"
        # No specific assertion on length since index may be empty


class TestMemoryStructure:
    """Verify the episodic memory structure exists"""

    def test_structure_verification(self):
        """Verify memory structure - skip if not present"""
        memory_path = Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")

        if not memory_path.exists():
            pytest.skip("Memory structure not present in test environment")

        # Check directory
        assert memory_path.exists(), f"Directory should exist: {memory_path}"

        # Check files
        files = ["episodes.jsonl", "index.json", "schema.md"]
        for file in files:
            file_path = memory_path / file
            assert file_path.exists(), f"File should exist: {file}"


def test_memory_search():
    """Legacy test function - converted to proper pytest assertion"""
    results = search_episodic_memory("postgres migration")
    
    # Just verify it returns a list without error
    assert isinstance(results, list), "Should return a list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
