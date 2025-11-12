"""
Semantic Module: Embedding-based semantic matching

This module provides semantic matching using embeddings for improved
intent classification and agent routing accuracy.
"""

from .semantic_matcher import SemanticMatcher
from .generate_embeddings import generate_embeddings

__all__ = ["SemanticMatcher", "generate_embeddings"]
