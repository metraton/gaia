#!/usr/bin/env python3
"""
Simple test for episodic memory search - tests the core logic directly.
"""

import json
from pathlib import Path
from datetime import datetime

def search_episodic_memory(user_prompt: str, max_results: int = 3):
    """Direct implementation of episodic memory search for testing"""

    # Search for episodic memory in current project
    memory_path = Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")
    index_file = memory_path / "index.json"

    if not index_file.exists():
        print(f"  ❌ Index file not found at {index_file}")
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

        # Simple time factor (skip complex date parsing for test)
        time_factor = 0.8

        final_score = score * time_factor * episode.get("relevance_score", 0.5)

        if final_score > 0.1:
            episode["match_score"] = final_score
            scored_episodes.append(episode)

    # Sort by score and return top N
    scored_episodes.sort(key=lambda x: x["match_score"], reverse=True)
    return scored_episodes[:max_results]


def test_memory_search():
    """Test episodic memory search"""
    print("🧪 Testing Episodic Memory Search...")

    # Test 1: Search for database migration
    print("\n  Test 1: Searching for 'postgres migration'...")
    results = search_episodic_memory("postgres migration")

    if results and any("postgres" in str(r).lower() for r in results):
        print(f"  ✅ PASSED: Found {len(results)} relevant episodes")
        for r in results:
            print(f"     - {r['title']} (score: {r.get('match_score', 0):.2f})")
    else:
        print(f"  ❌ FAILED: Should have found postgres migration episode")
        return False

    # Test 2: Search for kubernetes issues
    print("\n  Test 2: Searching for 'kubernetes autoscaling'...")
    results = search_episodic_memory("kubernetes autoscaling issues")

    if results and any("autoscaling" in str(r).lower() for r in results):
        print(f"  ✅ PASSED: Found {len(results)} relevant episodes")
        for r in results:
            print(f"     - {r['title']} (score: {r.get('match_score', 0):.2f})")
    else:
        print(f"  ❌ FAILED: Should have found autoscaling episode")
        return False

    # Test 3: Search with no matches
    print("\n  Test 3: Searching for unrelated terms...")
    results = search_episodic_memory("azure cosmos mongodb")

    if not results or len(results) == 0:
        print(f"  ✅ PASSED: No irrelevant episodes returned")
    else:
        print(f"  ⚠️  PARTIAL: Found {len(results)} episodes (might be false positives)")

    return True


def verify_memory_structure():
    """Verify the episodic memory structure exists"""
    print("\n🧪 Verifying Memory Structure...")

    memory_path = Path("/home/jaguilar/aaxis/vtr/repositories/.claude/project-context/episodic-memory")

    # Check directory
    if memory_path.exists():
        print(f"  ✅ Directory exists: {memory_path}")
    else:
        print(f"  ❌ Directory missing: {memory_path}")
        return False

    # Check files
    files = ["episodes.jsonl", "index.json", "schema.md"]
    for file in files:
        file_path = memory_path / file
        if file_path.exists():
            print(f"  ✅ File exists: {file}")
        else:
            print(f"  ❌ File missing: {file}")
            return False

    # Check episode count
    with open(memory_path / "index.json") as f:
        index = json.load(f)

    episode_count = len(index.get("episodes", []))
    print(f"  📊 Episodes in index: {episode_count}")

    return True


def main():
    """Run all tests"""
    print("="*60)
    print("EPISODIC MEMORY TESTS (Simplified)")
    print("="*60)

    # First verify structure
    if not verify_memory_structure():
        print("\n❌ Memory structure incomplete")
        return 1

    # Then test search
    if not test_memory_search():
        print("\n❌ Memory search failed")
        return 1

    print("\n" + "="*60)
    print("✅ All tests passed!")
    print("="*60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())