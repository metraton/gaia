#!/usr/bin/env python3
"""
Demonstration of the Episodic Memory System

This script shows how the episodic memory system integrates with the workflow.
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import using importlib to handle hyphen in directory name
import importlib.util
spec = importlib.util.spec_from_file_location("episodic", Path(__file__).parent / "episodic.py")
episodic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(episodic)

EpisodicMemory = episodic.EpisodicMemory


def main():
    print("=== Episodic Memory System Demo ===\n")

    # Create memory instance
    memory = EpisodicMemory()
    print(f"Memory location: {memory.base_path}")
    print(f"Episodes directory: {memory.episodes_dir}")
    print()

    # Store some episodes
    print("1. Storing episodes...")
    ep1 = memory.store_episode(
        prompt="Deploy the API to production",
        clarifications={"environment": "prod", "version": "1.2.3"},
        enriched_prompt="Deploy tcm-api v1.2.3 to production environment",
        context={"user": "demo", "timestamp": "2024-11-19"},
        tags=["deployment", "production", "api"]
    )
    print(f"   Stored: {ep1}")

    ep2 = memory.store_episode(
        prompt="Fix database connection issues",
        clarifications={"database": "postgresql", "issue": "timeout"},
        enriched_prompt="Fix PostgreSQL connection timeout in production",
        context={"severity": "high"},
        tags=["troubleshooting", "database", "production"]
    )
    print(f"   Stored: {ep2}")

    ep3 = memory.store_episode(
        prompt="Check Kubernetes pod status",
        enriched_prompt="Check pod status in namespace tcm-non-prod",
        tags=["kubernetes", "monitoring"]
    )
    print(f"   Stored: {ep3}")
    print()

    # Search episodes
    print("2. Searching episodes...")
    print("   Search: 'production'")
    results = memory.search_episodes("production", max_results=3)
    for i, episode in enumerate(results, 1):
        print(f"   {i}. [{episode['match_score']:.2f}] {episode.get('title', 'Untitled')}")

    print("\n   Search: 'kubernetes'")
    results = memory.search_episodes("kubernetes", max_results=3)
    for i, episode in enumerate(results, 1):
        print(f"   {i}. [{episode['match_score']:.2f}] {episode.get('title', 'Untitled')}")
    print()

    # Get statistics
    print("3. Memory statistics:")
    stats = memory.get_statistics()
    print(f"   Total episodes: {stats['total_episodes']}")
    print(f"   Storage size: {stats['storage_size_mb']:.3f} MB")
    print(f"   Episode types:")
    for ep_type, count in stats.get('types', {}).items():
        print(f"     - {ep_type}: {count}")
    print()

    # List recent episodes
    print("4. Recent episodes:")
    recent = memory.list_episodes(limit=5)
    for i, episode in enumerate(recent[:3], 1):
        print(f"   {i}. {episode.get('title', 'Untitled')} ({episode.get('type', 'unknown')})")
    print()

    print("Demo complete! The episodic memory system is ready for use.")


if __name__ == "__main__":
    main()