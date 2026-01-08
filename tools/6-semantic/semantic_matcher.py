"""
DEPRECATED: Semantic matching using pre-computed embeddings

This module has been superseded by llm_classifier.py in tools/1-routing/.
The LLM-based approach provides better accuracy with simpler implementation.

Kept for backward compatibility - will be removed in future version.

Week 2 Addition (Now Deprecated)
"""

import warnings
import json
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Deprecation warning
warnings.warn(
    "semantic_matcher.py is deprecated. Use llm_classifier.py instead.",
    DeprecationWarning,
    stacklevel=2
)


class SemanticMatcher:
    """
    DEPRECATED: Match requests to intents using pre-computed embeddings
    
    This class is deprecated and will be removed in a future version.
    Use llm_classifier.classify_intent() instead for better accuracy.
    """

    def __init__(self, embeddings_dir: Optional[Path] = None):
        """Initialize semantic matcher (DEPRECATED)."""
        warnings.warn(
            "SemanticMatcher is deprecated. Use llm_classifier.classify_intent() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        if embeddings_dir is None:
            embeddings_dir = Path(__file__).parent.parent / "config"

        self.embeddings_dir = embeddings_dir
        self.embeddings: Dict[str, Any] = {}
        self.metadata: Dict[str, Any] = {}
        self.available = False

        self._load_embeddings()

    def _load_embeddings(self):
        """Load pre-computed embeddings from JSON (DEPRECATED)."""
        json_path = self.embeddings_dir / "intent_embeddings.json"

        if not json_path.exists():
            logger.warning(
                f"Embeddings not found at {json_path}. "
                "SemanticMatcher is deprecated - consider using llm_classifier.py instead."
            )
            self.available = False
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            import numpy as np

            for intent, info in data.items():
                self.embeddings[intent] = {
                    "embedding": np.array(info["embedding"]),
                    "examples": info["examples"],
                    "dimension": info.get("dimension", 384)
                }

            self.available = True
            logger.info(f"Loaded {len(self.embeddings)} intent embeddings (DEPRECATED)")

        except Exception as e:
            logger.error(f"Error loading embeddings: {e}")
            self.available = False

    def find_similar_intent(
        self,
        text: str,
        keyword_scores: Dict[str, float]
    ) -> Tuple[Optional[str], float]:
        """
        DEPRECATED: Find most similar intent combining keywords + embeddings.
        
        Consider using llm_classifier.classify_intent() instead.
        """
        if not keyword_scores:
            return None, 0.0

        if not self.available:
            logger.debug("Embeddings not available, using keyword scores only")
            best_intent = max(keyword_scores, key=keyword_scores.get)
            confidence = keyword_scores[best_intent]
            return best_intent, confidence

        # Simplified: just use keyword scores (embedding logic removed)
        best_intent = max(keyword_scores, key=keyword_scores.get)
        confidence = min(keyword_scores[best_intent] / 100.0, 1.0)
        
        return best_intent, confidence

    def get_intent_examples(self, intent: str) -> List[str]:
        """Get example requests for an intent."""
        if intent in self.embeddings:
            return self.embeddings[intent]["examples"]
        return []

    def list_intents(self) -> List[str]:
        """List all available intents."""
        return list(self.embeddings.keys())

    def is_available(self) -> bool:
        """Check if embeddings are loaded."""
        return self.available

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded embeddings."""
        return {
            "available": self.available,
            "deprecated": True,
            "replacement": "llm_classifier.classify_intent()",
            "intents": len(self.embeddings),
            "total_examples": sum(
                len(info["examples"]) for info in self.embeddings.values()
            )
        }
