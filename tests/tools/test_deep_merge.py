"""
TDD tests for deep_merge utility.

Module under test: tools/context/deep_merge.py (does not exist yet)
Function: deep_merge(current: dict, update: dict) -> tuple[dict, dict]
Returns (merged_result, diff) where diff records changes made.

Merge semantics:
- Dicts: recursive merge, preserving keys not in update (no-delete policy)
- Lists of primitives: set union, sorted, deduplicated
- Lists of objects with "name" key: merge by name, add new entries, preserve missing
- Lists of objects without "name" key: concatenate, deduplicate by JSON equality
- Scalars: overwrite with new value
- Type mismatch: new value wins
"""

import pytest
import sys
from pathlib import Path

# Add tools directory to path (matches test_context_provider.py pattern)
TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if TOOLS_DIR.is_symlink():
    TOOLS_DIR = TOOLS_DIR.resolve()

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

from deep_merge import deep_merge


class TestDeepMerge:
    """Tests for the deep_merge(current, update) -> (merged, diff) function."""

    def test_merge_dict_into_dict(self):
        """Recursive merge preserving existing keys."""
        current = {"a": {"x": 1, "y": 2}, "b": 3}
        update = {"a": {"y": 5, "z": 6}}
        merged, diff = deep_merge(current, update)
        assert merged == {"a": {"x": 1, "y": 5, "z": 6}, "b": 3}
        assert "a" in diff  # Should record the y change

    def test_merge_list_primitives(self):
        """Set union, sorted, deduplicated (e.g., namespace arrays)."""
        current = {"ns": ["adm", "dev", "test"]}
        update = {"ns": ["dev", "test", "nova-auth-dev"]}
        merged, diff = deep_merge(current, update)
        assert merged["ns"] == sorted(["adm", "dev", "test", "nova-auth-dev"])

    def test_merge_list_objects_by_name(self):
        """Merge by name key, add new entries (e.g., helm releases)."""
        current = {"releases": [
            {"name": "orders", "chart_version": "0.53.0"},
            {"name": "payments", "chart_version": "1.0.0"}
        ]}
        update = {"releases": [
            {"name": "orders", "chart_version": "0.54.0", "status": "healthy"},
            {"name": "auth", "chart_version": "2.0.0"}
        ]}
        merged, diff = deep_merge(current, update)
        names = [r["name"] for r in merged["releases"]]
        assert "orders" in names
        assert "payments" in names  # preserved (no-delete)
        assert "auth" in names  # added
        orders = next(r for r in merged["releases"] if r["name"] == "orders")
        assert orders["chart_version"] == "0.54.0"  # updated

    def test_merge_list_objects_without_name(self):
        """Concatenate, deduplicate by JSON equality."""
        current = {"items": [{"port": 80}, {"port": 443}]}
        update = {"items": [{"port": 443}, {"port": 8080}]}
        merged, diff = deep_merge(current, update)
        assert len(merged["items"]) == 3  # 80, 443, 8080

    def test_merge_scalar_overwrite(self):
        """New value replaces old (e.g., chart_version)."""
        current = {"version": "0.53.0", "name": "test"}
        update = {"version": "0.54.0"}
        merged, diff = deep_merge(current, update)
        assert merged["version"] == "0.54.0"
        assert merged["name"] == "test"  # preserved
        assert diff["version"]["old"] == "0.53.0"
        assert diff["version"]["new"] == "0.54.0"

    def test_merge_add_missing_key(self):
        """Key in update but not in current -> add."""
        current = {"a": 1}
        update = {"b": 2}
        merged, diff = deep_merge(current, update)
        assert merged == {"a": 1, "b": 2}

    def test_no_delete_policy(self):
        """Key in current but not in update -> preserved."""
        current = {"a": 1, "b": 2, "c": 3}
        update = {"a": 10}
        merged, diff = deep_merge(current, update)
        assert merged["b"] == 2
        assert merged["c"] == 3

    def test_type_mismatch_overwrite(self):
        """Dict vs list -> new value wins with warning."""
        current = {"data": {"key": "value"}}
        update = {"data": ["item1", "item2"]}
        merged, diff = deep_merge(current, update)
        assert merged["data"] == ["item1", "item2"]

    def test_nested_deep_merge(self):
        """3-4 levels of nesting (realistic project-context structure)."""
        current = {
            "cluster_details": {
                "namespaces": {
                    "application": ["adm", "dev"],
                    "infrastructure": ["flux-system"]
                },
                "status": "RUNNING"
            }
        }
        update = {
            "cluster_details": {
                "namespaces": {
                    "application": ["adm", "dev", "nova-auth-dev"],
                    "system": ["kube-system"]
                }
            }
        }
        merged, diff = deep_merge(current, update)
        assert "nova-auth-dev" in merged["cluster_details"]["namespaces"]["application"]
        assert "flux-system" in merged["cluster_details"]["namespaces"]["infrastructure"]  # preserved
        assert "kube-system" in merged["cluster_details"]["namespaces"]["system"]  # added
        assert merged["cluster_details"]["status"] == "RUNNING"  # preserved

    def test_empty_inputs(self):
        """Empty dict/list/None handling."""
        merged1, _ = deep_merge({}, {"a": 1})
        assert merged1 == {"a": 1}

        merged2, _ = deep_merge({"a": 1}, {})
        assert merged2 == {"a": 1}

    def test_diff_generation(self):
        """Returns diff of old -> new values for audit trail."""
        current = {"version": "1.0", "count": 5}
        update = {"version": "2.0", "count": 10, "new_key": "value"}
        merged, diff = deep_merge(current, update)
        assert diff["version"] == {"old": "1.0", "new": "2.0"}
        assert diff["count"] == {"old": 5, "new": 10}
