#!/usr/bin/env python3
"""
Generate intent embeddings offline using sentence-transformers

This script is run ONCE to generate pre-computed embeddings.
At runtime, we only need numpy for similarity calculations.

Usage:
    python3 generate_embeddings.py

Output:
    - .claude/config/intent_embeddings.npy (binary, ~5MB)
    - .claude/config/intent_embeddings.json (readable metadata)
"""

import json
from pathlib import Path
import sys

# Define intent examples (from agent_router.py IntentClassifier)
INTENT_EXAMPLES = {
    "infrastructure_creation": [
        "create gke cluster with autopilot",
        "provision new vpc and subnets",
        "deploy terraform infrastructure",
        "setup kubernetes cluster",
        "build cloud resources",
        "create new database instance",
        "provision redis cache",
        "deploy load balancer"
    ],
    "infrastructure_diagnosis": [
        "diagnose cluster connectivity",
        "troubleshoot gke pod crashes",
        "debug network latency problems",
        "check kubernetes node status",
        "analyze infrastructure errors",
        "troubleshoot cloud sql connectivity",
        "debug workload identity issues",
        "diagnose firewall rule problems"
    ],
    "kubernetes_operations": [
        "check pod status in namespace",
        "view deployment logs",
        "verify flux reconciliation",
        "monitor helm release status",
        "inspect kubernetes resources",
        "check service endpoints",
        "scale deployment replicas",
        "update configmap values"
    ],
    "application_development": [
        "build docker image for api",
        "run unit tests for application",
        "validate application configuration",
        "compile typescript code",
        "execute npm build command",
        "lint code with eslint",
        "run integration tests",
        "package application for deployment"
    ],
    "infrastructure_validation": [
        "validate terraform configuration",
        "check hcl syntax errors",
        "run terraform plan",
        "scan infrastructure security",
        "verify module dependencies",
        "check terraform state integrity",
        "scan for policy violations",
        "validate cloudformation template"
    ]
}


def generate_embeddings():
    """Generate and save embeddings using sentence-transformers"""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        print("❌ Error: sentence-transformers not installed")
        print("   Run: pip install sentence-transformers torch")
        return False

    print("\n" + "="*70)
    print("🔧 Generating Intent Embeddings (Offline)")
    print("="*70 + "\n")

    # Load model
    print("📥 Loading sentence-transformers model: all-MiniLM-L6-v2")
    model = SentenceTransformer('all-MiniLM-L6-v2')
    print(f"   ✅ Model loaded (embedding dimension: 384)")

    embeddings_data = {}
    total_examples = 0

    print("\n📊 Generating embeddings for intents:\n")

    for intent_name, examples in INTENT_EXAMPLES.items():
        print(f"   🎯 {intent_name}")

        # Generate embeddings for all examples
        embeddings = model.encode(examples, convert_to_numpy=True)
        mean_embedding = embeddings.mean(axis=0)

        # Store metadata and mean embedding
        embeddings_data[intent_name] = {
            "embedding": mean_embedding.tolist(),  # Convert to list for JSON
            "examples": examples,
            "dimension": len(mean_embedding),
            "count": len(examples)
        }

        print(f"      ✅ {len(examples):2d} examples → 384-dim embedding")
        total_examples += len(examples)

    print(f"\n   Total examples processed: {total_examples}")

    # Save as JSON (readable metadata)
    output_dir = Path(__file__).parent.parent / "config"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "intent_embeddings.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(embeddings_data, f, indent=2)

    print(f"\n✅ Saved metadata: {json_path}")
    print(f"   Size: {json_path.stat().st_size / 1024:.1f} KB")

    # Save as numpy array (binary, optimized)
    npy_path = output_dir / "intent_embeddings.npy"
    import numpy as np
    np.save(npy_path, embeddings_data)

    print(f"✅ Saved embeddings: {npy_path}")
    print(f"   Size: {npy_path.stat().st_size / (1024*1024):.1f} MB")

    # Create runtime loader info
    info_path = output_dir / "embeddings_info.json"
    info = {
        "model": "all-MiniLM-L6-v2",
        "dimension": 384,
        "intents": list(embeddings_data.keys()),
        "total_examples": total_examples,
        "timestamp": str(Path(__file__).stat().st_mtime),
        "note": "Pre-computed offline. At runtime, load with numpy (no torch needed)."
    }

    with open(info_path, 'w') as f:
        json.dump(info, f, indent=2)

    print(f"✅ Saved info: {info_path}")

    print("\n" + "="*70)
    print("🎉 Embeddings Generated Successfully!")
    print("="*70)
    print("\nUsage:")
    print("   from semantic_matcher import SemanticMatcher")
    print("   matcher = SemanticMatcher()  # Loads embeddings automatically")
    print("   intent, conf = matcher.find_similar_intent(request, keyword_scores)")
    print()

    return True


if __name__ == "__main__":
    success = generate_embeddings()
    sys.exit(0 if success else 1)
