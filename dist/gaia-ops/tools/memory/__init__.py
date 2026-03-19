"""
Episodic Memory Module for GAIA-OPS

This module provides episodic memory functionality for storing and retrieving
historical context from user interactions.
"""

from .episodic import (
    EpisodicMemory,
    Episode,
    search_episodic_memory
)

__all__ = [
    'EpisodicMemory',
    'Episode',
    'search_episodic_memory'
]

__version__ = '1.0.0'