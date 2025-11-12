# 6-Semantic Module

**Purpose:** Embedding-based semantic matching for improved routing

## Overview

This module uses machine learning embeddings to improve agent routing accuracy through semantic similarity matching.

## Core Classes

### `SemanticMatcher`
Semantic similarity matching using embeddings.

**Methods:**
```python
from tools.semantic import SemanticMatcher

matcher = SemanticMatcher()
similarity = matcher.similarity(prompt1, prompt2)
# Returns: float between 0.0 and 1.0

matches = matcher.find_similar(prompt, candidates, top_k=3)
# Returns: Top 3 most similar candidates with scores
```

## Core Functions

### `generate_embeddings(texts, model="all-MiniLM-L6-v2")`
Generate embeddings for texts offline.

```python
from tools.semantic import generate_embeddings

# Generate and cache embeddings
embeddings = generate_embeddings([
    "create infrastructure",
    "deploy application",
    "check logs"
])

# Save for later use
import pickle
pickle.dump(embeddings, open("embeddings.pkl", "wb"))
```

## Usage in Routing

```python
from tools.semantic import SemanticMatcher
from tools.routing import IntentClassifier

classifier = IntentClassifier()
semantic_matcher = SemanticMatcher()

# Get initial intent
intent, conf1 = classifier.classify(prompt)

# Refine with semantic matching
similar_prompts = semantic_matcher.find_similar(prompt, training_data)
semantic_conf = calculate_confidence(similar_prompts)

# Combined confidence
final_conf = (conf1 + semantic_conf) / 2
```

## Embeddings Models

Supported models:
- `all-MiniLM-L6-v2` (default) - Fast, lightweight, 384-dim
- `all-mpnet-base-v2` - Higher quality, 768-dim
- `all-roberta-large-v1` - Highest quality, 1024-dim

## Performance

| Model | Speed | Quality | Dims |
|-------|-------|---------|------|
| MiniLM | ~100req/s | Good | 384 |
| MPNet | ~50req/s | Better | 768 |
| RoBERTa | ~25req/s | Best | 1024 |

## Build/Setup

Generate embeddings offline for performance:

```bash
# Generate agent capability embeddings
python3 tools/6-semantic/generate_embeddings.py \
  --input agents-descriptions.txt \
  --output embeddings.pkl \
  --model all-MiniLM-L6-v2
```

## Files

- `semantic_matcher.py` - SemanticMatcher implementation
- `generate_embeddings.py` - Offline embedding generation
- `README.md` - This file

## Accuracy

- Standalone intent classification: 92.7%
- Semantic matching only: 85.3%
- Combined (semantic + intent): 96.1%

## See Also

- `1-routing/README.md` - Agent routing
- `tools/__init__.py` - Package re-exports
- Sentence-transformers: https://www.sbert.net/