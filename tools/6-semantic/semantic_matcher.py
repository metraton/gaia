"""
Semantic matching using pre-computed embeddings

CRITICAL: This module does NOT require torch at runtime!
- Embeddings are pre-computed offline
- At runtime, we only load numpy + json
- Similarity is calculated with scipy/sklearn

Week 2 Addition
"""

import json
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SemanticMatcher:
    """
    Match requests to intents using pre-computed embeddings

    - Loads pre-computed embeddings from .npy/.json
    - Calculates similarity using numpy only
    - No torch/transformers needed at runtime
    - Provides fallback to keyword scores
    """

    def __init__(self, embeddings_dir: Optional[Path] = None):
        """
        Initialize semantic matcher

        Args:
            embeddings_dir: Directory containing embeddings (defaults to .claude/config/)
        """
        if embeddings_dir is None:
            embeddings_dir = Path(__file__).parent.parent / "config"

        self.embeddings_dir = embeddings_dir
        self.embeddings: Dict[str, Any] = {}
        self.metadata: Dict[str, Any] = {}
        self.available = False

        self._load_embeddings()

    def _load_embeddings(self):
        """Load pre-computed embeddings from JSON"""
        json_path = self.embeddings_dir / "intent_embeddings.json"

        if not json_path.exists():
            logger.warning(
                f"⚠️  Embeddings not found at {json_path}. "
                "Run: python3 .claude/tools/generate_embeddings.py"
            )
            self.available = False
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Convert lists back to numpy arrays
            import numpy as np

            for intent, info in data.items():
                self.embeddings[intent] = {
                    "embedding": np.array(info["embedding"]),
                    "examples": info["examples"],
                    "dimension": info.get("dimension", 384)
                }

            self.available = True
            logger.info(f"✅ Loaded {len(self.embeddings)} intent embeddings")

        except Exception as e:
            logger.error(f"Error loading embeddings: {e}")
            self.available = False

    def find_similar_intent(
        self,
        text: str,
        keyword_scores: Dict[str, float]
    ) -> Tuple[Optional[str], float]:
        """
        Find most similar intent combining keywords + embeddings

        Args:
            text: User request
            keyword_scores: Scores from keyword matching {intent: score}

        Returns:
            (best_intent, confidence)

        Strategy:
        1. If embeddings available, calculate text embedding (via TF-IDF approximation)
        2. Find similarity to each intent embedding
        3. Combine with keyword scores
        4. Return best match
        """
        if not keyword_scores:
            return None, 0.0

        # If embeddings not available, use keyword scores as primary
        if not self.available:
            logger.debug("Embeddings not available, using keyword scores only")
            best_intent = max(keyword_scores, key=keyword_scores.get)
            confidence = keyword_scores[best_intent]
            return best_intent, confidence

        # If embeddings available, enhance keyword scores with embedding similarity
        try:
            import numpy as np
            from sklearn.feature_extraction.text import TfidfVectorizer

            # Create TF-IDF approximation of text embedding
            # (lightweight alternative to transformer embeddings)
            all_examples = []
            intent_map = []

            for intent, info in self.embeddings.items():
                all_examples.extend(info["examples"])
                intent_map.extend([intent] * len(info["examples"]))

            all_examples.append(text)  # Add query at end

            # Vectorize
            vectorizer = TfidfVectorizer(
                analyzer='char',
                ngram_range=(2, 3),
                max_features=100
            )
            tfidf_matrix = vectorizer.fit_transform(all_examples)

            # Get query vector (last row)
            query_vector = tfidf_matrix[-1].toarray().flatten()

            # Calculate similarities to each intent
            embedding_scores = {}

            for intent in self.embeddings.keys():
                # Get example vectors for this intent
                example_indices = [i for i, x in enumerate(intent_map) if x == intent]
                example_vectors = tfidf_matrix[example_indices].toarray()

                # Calculate mean similarity to examples
                similarities = []
                for example_vec in example_vectors:
                    # Cosine similarity
                    dot = np.dot(query_vector, example_vec)
                    norm1 = np.linalg.norm(query_vector)
                    norm2 = np.linalg.norm(example_vec)
                    if norm1 > 0 and norm2 > 0:
                        similarity = dot / (norm1 * norm2)
                        similarities.append(similarity)

                # Mean similarity for this intent
                if similarities:
                    embedding_scores[intent] = np.mean(similarities)
                else:
                    embedding_scores[intent] = 0.0

            # Combine keyword scores (70%) + embedding scores (30%)
            combined_scores = {}
            for intent in keyword_scores.keys():
                kw_score = keyword_scores[intent]
                emb_score = embedding_scores.get(intent, 0.0)

                # Normalize both to 0-1
                kw_norm = min(kw_score / 5.0, 1.0)  # keyword scores are ~0-5
                emb_norm = max(0.0, min(emb_score, 1.0))  # embedding scores already 0-1

                # Weighted combination
                combined = (kw_norm * 0.7) + (emb_norm * 0.3)
                combined_scores[intent] = combined

            # Select best
            best_intent = max(combined_scores, key=combined_scores.get)
            confidence = combined_scores[best_intent]

            logger.debug(
                f"Combined scores: {best_intent} = {confidence:.3f} "
                f"(kw={keyword_scores.get(best_intent, 0):.2f}, "
                f"emb={embedding_scores.get(best_intent, 0):.3f})"
            )

            return best_intent, confidence

        except Exception as e:
            logger.warning(f"Error in embedding similarity: {e}")
            # Fallback to keyword scores
            best_intent = max(keyword_scores, key=keyword_scores.get)
            confidence = keyword_scores[best_intent]
            return best_intent, confidence

    def get_intent_examples(self, intent: str) -> List[str]:
        """Get example requests for an intent"""
        if intent in self.embeddings:
            return self.embeddings[intent]["examples"]
        return []

    def list_intents(self) -> List[str]:
        """List all available intents"""
        return list(self.embeddings.keys())

    def is_available(self) -> bool:
        """Check if embeddings are loaded"""
        return self.available

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded embeddings"""
        return {
            "available": self.available,
            "intents": len(self.embeddings),
            "total_examples": sum(
                len(info["examples"]) for info in self.embeddings.values()
            ),
            "embedding_dimension": next(
                (info["dimension"] for info in self.embeddings.values()),
                None
            )
        }
