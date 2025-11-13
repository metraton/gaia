#!/usr/bin/env python3
"""
Context Compressor
Comprime contexto para reducir tokens manteniendo información esencial.
Técnicas: Summarization, deduplicación, referencias, truncado inteligente.
"""

import json
import logging
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from collections import Counter

logger = logging.getLogger(__name__)


@dataclass
class CompressionStats:
    """Estadísticas de compresión"""
    original_size: int
    compressed_size: int
    compression_ratio: float
    techniques_used: List[str]
    items_summarized: int
    items_deduplicated: int


class ContextCompressor:
    """
    Comprime contexto usando múltiples técnicas para reducir tokens.

    Técnicas implementadas:
    1. Summarization: Arrays grandes → resumen + items relevantes
    2. Deduplication: Eliminar información repetida
    3. Reference IDs: Reemplazar objetos completos por IDs
    4. Smart Truncation: Cortar campos largos preservando inicio/fin
    5. Field Filtering: Eliminar campos no esenciales
    """

    # Campos que típicamente se pueden omitir
    NON_ESSENTIAL_FIELDS = {
        "created_at", "updated_at", "created_by", "modified_by",
        "etag", "generation", "resource_version", "uid",
        "self_link", "annotations", "labels", "owner_references",
        "finalizers", "managed_fields"
    }

    # Límites para triggering compression
    COMPRESSION_THRESHOLDS = {
        "array_items": 10,      # Comprimir arrays con >10 items
        "string_length": 500,   # Truncar strings >500 chars
        "object_fields": 20,    # Comprimir objetos con >20 campos
        "total_size": 1000      # Comprimir si sección >1000 tokens estimados
    }

    def __init__(self, aggressive: bool = False):
        """
        Initialize compressor.

        Args:
            aggressive: If True, apply more aggressive compression
        """
        self.aggressive = aggressive
        self.dedup_cache: Dict[str, str] = {}  # For deduplication
        self.stats = None

    def compress(self, context: Dict[str, Any]) -> Tuple[Dict[str, Any], CompressionStats]:
        """
        Compress context dictionary.

        Args:
            context: Full context to compress

        Returns:
            Tuple of (compressed_context, stats)
        """
        logger.info("Starting context compression...")

        original_size = self._estimate_size(context)
        compressed = {}
        techniques_used = []

        # Process each top-level section
        for section_name, section_content in context.items():
            if section_name == "metadata":
                # Keep metadata as-is
                compressed[section_name] = section_content
                continue

            # Compress based on content type
            if isinstance(section_content, list):
                compressed[section_name] = self._compress_array(
                    section_content, section_name
                )
                techniques_used.append(f"array_compression:{section_name}")

            elif isinstance(section_content, dict):
                compressed[section_name] = self._compress_object(
                    section_content, section_name
                )
                techniques_used.append(f"object_compression:{section_name}")

            elif isinstance(section_content, str):
                compressed[section_name] = self._compress_string(section_content)
                if len(section_content) != len(compressed[section_name]):
                    techniques_used.append(f"string_truncation:{section_name}")

            else:
                # Keep primitives as-is
                compressed[section_name] = section_content

        # Apply deduplication across entire context
        compressed = self._apply_deduplication(compressed)
        if self.dedup_cache:
            techniques_used.append("cross_section_deduplication")

        compressed_size = self._estimate_size(compressed)

        self.stats = CompressionStats(
            original_size=original_size,
            compressed_size=compressed_size,
            compression_ratio=compressed_size / original_size if original_size > 0 else 1.0,
            techniques_used=techniques_used,
            items_summarized=0,  # TODO: Track this
            items_deduplicated=len(self.dedup_cache)
        )

        logger.info(
            f"Compression complete: {original_size} → {compressed_size} tokens "
            f"({self.stats.compression_ratio:.1%} of original)"
        )

        return compressed, self.stats

    def _compress_array(self, array: List[Any], context_name: str) -> Any:
        """
        Compress large arrays.

        Strategy:
        - If ≤ threshold items: keep as-is
        - If > threshold: summarize + keep most relevant items
        """
        threshold = self.COMPRESSION_THRESHOLDS["array_items"]

        if len(array) <= threshold:
            return array

        # Create summary
        summary = {
            "_type": "compressed_array",
            "total_count": len(array),
            "summary": self._create_array_summary(array, context_name),
            "relevant_items": []
        }

        # Identify and keep relevant items
        relevant_items = self._select_relevant_items(array, context_name)
        summary["relevant_items"] = relevant_items[:5]  # Keep top 5

        # Add preview of other items if space allows
        if not self.aggressive:
            preview_items = [item for item in array if item not in relevant_items][:3]
            if preview_items:
                summary["preview_items"] = preview_items

        return summary

    def _compress_object(self, obj: Dict[str, Any], context_name: str) -> Dict[str, Any]:
        """
        Compress large objects.

        Strategy:
        - Remove non-essential fields
        - Compress nested arrays/objects
        - Keep only most important fields
        """
        compressed = {}

        # First pass: remove non-essential fields
        for key, value in obj.items():
            if key.lower() in self.NON_ESSENTIAL_FIELDS:
                continue

            # Recursively compress nested structures
            if isinstance(value, list) and len(value) > self.COMPRESSION_THRESHOLDS["array_items"]:
                compressed[key] = self._compress_array(value, f"{context_name}.{key}")
            elif isinstance(value, dict) and len(value) > self.COMPRESSION_THRESHOLDS["object_fields"]:
                compressed[key] = self._compress_object(value, f"{context_name}.{key}")
            elif isinstance(value, str) and len(value) > self.COMPRESSION_THRESHOLDS["string_length"]:
                compressed[key] = self._compress_string(value)
            else:
                compressed[key] = value

        # Second pass: if still too large, keep only essential fields
        if self.aggressive and len(compressed) > self.COMPRESSION_THRESHOLDS["object_fields"]:
            essential_fields = self._identify_essential_fields(compressed, context_name)
            compressed = {k: v for k, v in compressed.items() if k in essential_fields}
            compressed["_truncated"] = True
            compressed["_original_fields"] = len(obj)

        return compressed

    def _compress_string(self, text: str) -> str:
        """
        Compress long strings.

        Strategy:
        - If ≤ threshold: keep as-is
        - If > threshold: keep beginning and end, indicate truncation
        """
        threshold = self.COMPRESSION_THRESHOLDS["string_length"]

        if len(text) <= threshold:
            return text

        # Smart truncation: keep beginning and end
        keep_start = threshold // 2
        keep_end = threshold // 4

        return (
            f"{text[:keep_start]}\n"
            f"... [truncated {len(text) - threshold} chars] ...\n"
            f"{text[-keep_end:]}"
        )

    def _create_array_summary(self, array: List[Any], context_name: str) -> str:
        """Create a text summary of an array."""
        # Special handling for known types
        if "services" in context_name.lower():
            return self._summarize_services(array)
        elif "deployments" in context_name.lower():
            return self._summarize_deployments(array)
        elif "namespaces" in context_name.lower():
            return self._summarize_namespaces(array)
        elif "errors" in context_name.lower() or "logs" in context_name.lower():
            return self._summarize_logs(array)

        # Generic summary
        if array and isinstance(array[0], dict):
            # Count by status if available
            status_counts = Counter(item.get("status", "unknown") for item in array)
            return f"{len(array)} items. Status: {dict(status_counts)}"
        else:
            # Simple type summary
            types = Counter(type(item).__name__ for item in array)
            return f"{len(array)} items. Types: {dict(types)}"

    def _summarize_services(self, services: List[Dict[str, Any]]) -> str:
        """Create summary for services array."""
        total = len(services)
        running = sum(1 for s in services if s.get("status") == "running")
        pending = sum(1 for s in services if s.get("status") == "pending")
        failed = sum(1 for s in services if s.get("status") in ["failed", "error"])

        return (
            f"{total} services total: {running} running, "
            f"{pending} pending, {failed} failed"
        )

    def _summarize_deployments(self, deployments: List[Dict[str, Any]]) -> str:
        """Create summary for deployments array."""
        total = len(deployments)
        ready = sum(1 for d in deployments if d.get("ready_replicas") == d.get("replicas"))
        updating = sum(1 for d in deployments if d.get("updated_replicas", 0) < d.get("replicas", 0))

        return f"{total} deployments: {ready} ready, {updating} updating"

    def _summarize_namespaces(self, namespaces: List[Dict[str, Any]]) -> str:
        """Create summary for namespaces array."""
        total = len(namespaces)
        active = sum(1 for n in namespaces if n.get("status") == "Active")

        return f"{total} namespaces: {active} active"

    def _summarize_logs(self, logs: List[Any]) -> str:
        """Create summary for logs array."""
        total = len(logs)
        if logs and isinstance(logs[0], dict):
            error_count = sum(1 for log in logs if "error" in str(log).lower())
            warning_count = sum(1 for log in logs if "warning" in str(log).lower())
            return f"{total} log entries: {error_count} errors, {warning_count} warnings"
        return f"{total} log entries"

    def _select_relevant_items(self, array: List[Any], context_name: str) -> List[Any]:
        """
        Select most relevant items from array to keep.

        Relevance criteria:
        - Items with errors/failures
        - Items with recent changes
        - Items with high resource usage
        - Items explicitly named in common tasks
        """
        if not array:
            return []

        relevant = []

        for item in array:
            if not isinstance(item, dict):
                continue

            # Priority 1: Errors/failures
            if any(key in ["error", "failed", "unhealthy"] for key in str(item).lower().split()):
                relevant.append(item)
                continue

            # Priority 2: Recent changes (if timestamp available)
            if item.get("updated_at") or item.get("last_modified"):
                relevant.append(item)
                continue

            # Priority 3: High resource usage
            if "cpu" in item or "memory" in item:
                # Check if high usage (simplified check)
                relevant.append(item)
                continue

            # Priority 4: Common service names
            name = item.get("name", "")
            if any(keyword in name.lower() for keyword in ["api", "database", "redis", "kafka"]):
                relevant.append(item)

        # Sort by priority and return top items
        return relevant[:10]

    def _identify_essential_fields(self, obj: Dict[str, Any], context_name: str) -> List[str]:
        """Identify which fields are essential to keep."""
        essential = ["name", "id", "status", "error", "message", "type", "kind"]

        # Add context-specific essentials
        if "terraform" in context_name.lower():
            essential.extend(["resource", "provider", "state"])
        elif "kubernetes" in context_name.lower() or "k8s" in context_name.lower():
            essential.extend(["namespace", "replicas", "image"])

        # Keep fields that exist in object
        return [field for field in essential if field in obj]

    def _apply_deduplication(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply cross-section deduplication.

        Replace duplicate objects with references.
        """
        def _process_value(value: Any, path: str = "") -> Any:
            if isinstance(value, dict):
                # Create hash of object
                obj_hash = hashlib.md5(
                    json.dumps(value, sort_keys=True).encode()
                ).hexdigest()[:8]

                # Check if we've seen this before
                if obj_hash in self.dedup_cache:
                    # Replace with reference
                    return {"_ref": self.dedup_cache[obj_hash]}
                else:
                    # Store and process recursively
                    self.dedup_cache[obj_hash] = path
                    return {k: _process_value(v, f"{path}.{k}") for k, v in value.items()}

            elif isinstance(value, list):
                return [_process_value(item, f"{path}[{i}]") for i, item in enumerate(value)]
            else:
                return value

        return _process_value(context)

    def _estimate_size(self, obj: Any) -> int:
        """
        Estimate size in tokens (rough approximation).

        Assumes ~4 characters per token.
        """
        json_str = json.dumps(obj, default=str)
        return len(json_str) // 4


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test compressor
    compressor = ContextCompressor(aggressive=False)

    # Create test context
    test_context = {
        "metadata": {"agent": "test", "tier": "T2"},
        "services": [
            {"name": f"service-{i}", "status": "running" if i < 8 else "failed",
             "cpu": "100m", "memory": "256Mi", "created_at": "2024-01-01T00:00:00Z",
             "labels": {"app": "test", "env": "prod"}, "annotations": {"version": "1.0"}}
            for i in range(30)
        ],
        "deployments": [
            {"name": f"deployment-{i}", "replicas": 3, "ready_replicas": 3 if i < 10 else 2,
             "image": "app:latest", "namespace": "default"}
            for i in range(15)
        ],
        "long_config": "x" * 1000,  # Long string to test truncation
        "project_details": {
            "id": "project-123",
            "name": "Test Project",
            "region": "us-central1"
        }
    }

    print("🧪 Testing Context Compression...\n")

    # Compress
    compressed, stats = compressor.compress(test_context)

    # Show results
    print(f"Original size: {stats.original_size} tokens (estimated)")
    print(f"Compressed size: {stats.compressed_size} tokens (estimated)")
    print(f"Compression ratio: {stats.compression_ratio:.1%}")
    print(f"Techniques used: {', '.join(stats.techniques_used)}")
    print()

    # Show compressed structure
    print("Compressed structure:")
    for key in compressed:
        if key == "metadata":
            continue
        value = compressed[key]
        if isinstance(value, dict) and "_type" in value:
            print(f"  {key}: {value.get('summary', 'compressed')}")
        elif isinstance(value, str) and "truncated" in value:
            print(f"  {key}: [truncated string]")
        else:
            print(f"  {key}: {type(value).__name__}")

    # Test aggressive compression
    print("\n🔥 Testing Aggressive Compression...")
    aggressive_compressor = ContextCompressor(aggressive=True)
    compressed_aggressive, stats_aggressive = aggressive_compressor.compress(test_context)

    print(f"Aggressive compression ratio: {stats_aggressive.compression_ratio:.1%}")
    print(f"Size reduction: {stats.compressed_size - stats_aggressive.compressed_size} tokens")