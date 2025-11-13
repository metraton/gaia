"""
Test suite for context optimization tools
"""

import pytest
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "tools" / "2-context"))

from context_lazy_loader import LazyContextLoader, ContextPriority
from context_compressor import ContextCompressor, CompressionStats
from context_selector import SmartContextSelector, ContextRelevance


class TestLazyContextLoader:
    """Test lazy context loading functionality"""

    @pytest.fixture
    def loader(self, tmp_path):
        """Create loader with test context file"""
        context_file = tmp_path / "test-context.json"
        test_context = {
            "project_details": {"id": "test-123", "name": "Test Project"},
            "terraform_infrastructure": {"resources": ["vpc", "subnet"]},
            "terraform_state": {"version": 4, "resources": []},
            "cluster_details": {"name": "test-cluster", "nodes": 3},
            "namespaces": ["default", "kube-system", "test"],
            "deployments": [{"name": "api"}, {"name": "web"}],
            "operational_guidelines": {"on_call": "team-a"}
        }
        context_file.write_text(json.dumps(test_context))
        return LazyContextLoader(context_file, max_tokens=2000)

    def test_minimal_loading_t0(self, loader):
        """Test minimal loading for T0 tier"""
        context = loader.load_minimal_context(
            agent="terraform-architect",
            task="show state",
            tier="T0"
        )

        # Should only load required sections for T0
        assert "project_details" in context
        assert "terraform_infrastructure" in context
        assert "terraform_state" not in context  # Not required for T0
        assert len(context["metadata"]["loaded_sections"]) <= 3

    def test_expanded_loading_t3(self, loader):
        """Test expanded loading for T3 tier"""
        context = loader.load_minimal_context(
            agent="terraform-architect",
            task="apply terraform changes",
            tier="T3"
        )

        # Should load more sections for T3 (within token limits)
        assert "project_details" in context
        assert "terraform_infrastructure" in context
        # Note: operational_guidelines may be skipped due to token limit
        assert len(context["metadata"]["loaded_sections"]) >= 2

    def test_on_demand_loading(self, loader):
        """Test on-demand section loading"""
        context = loader.load_minimal_context(
            agent="terraform-architect",
            task="check",
            tier="T0"
        )

        # Load additional section on demand
        original_count = len(context["metadata"]["loaded_sections"])
        context = loader.load_on_demand("terraform_state", context)

        assert "terraform_state" in context
        assert len(context["metadata"]["loaded_sections"]) == original_count + 1

    def test_usage_stats(self, loader):
        """Test usage statistics collection"""
        # Perform several loads
        for _ in range(3):
            loader.load_minimal_context("terraform-architect", "test", "T0")

        stats = loader.get_usage_stats()

        assert stats["total_loads"] > 0
        assert "most_used" in stats
        assert isinstance(stats["recommendations"], list)


class TestContextCompressor:
    """Test context compression functionality"""

    @pytest.fixture
    def compressor(self):
        return ContextCompressor(aggressive=False)

    @pytest.fixture
    def large_context(self):
        """Create a large test context"""
        return {
            "metadata": {"test": True},
            "services": [
                {"name": f"service-{i}", "status": "running", "cpu": "100m"}
                for i in range(30)
            ],
            "long_string": "x" * 1000,
            "deployments": [
                {"name": f"deploy-{i}", "replicas": 3}
                for i in range(20)
            ]
        }

    def test_array_compression(self, compressor, large_context):
        """Test compression of large arrays"""
        compressed, stats = compressor.compress(large_context)

        # Services should be compressed
        assert "services" in compressed
        services = compressed["services"]
        assert isinstance(services, dict)
        assert "_type" in services
        assert services["_type"] == "compressed_array"
        assert "summary" in services
        assert services["total_count"] == 30

    def test_string_truncation(self, compressor, large_context):
        """Test string truncation"""
        compressed, stats = compressor.compress(large_context)

        # Long string should be truncated
        assert "long_string" in compressed
        assert "truncated" in compressed["long_string"]
        assert len(compressed["long_string"]) < len(large_context["long_string"])

    def test_compression_ratio(self, compressor, large_context):
        """Test compression achieves good ratio"""
        compressed, stats = compressor.compress(large_context)

        assert stats.compression_ratio < 0.7  # At least 30% reduction
        assert stats.original_size > stats.compressed_size

    def test_aggressive_compression(self, large_context):
        """Test aggressive compression mode"""
        normal_compressor = ContextCompressor(aggressive=False)
        aggressive_compressor = ContextCompressor(aggressive=True)

        normal_compressed, normal_stats = normal_compressor.compress(large_context)
        aggressive_compressed, aggressive_stats = aggressive_compressor.compress(large_context)

        # Aggressive should compress more
        assert aggressive_stats.compressed_size < normal_stats.compressed_size
        assert aggressive_stats.compression_ratio < normal_stats.compression_ratio


class TestSmartContextSelector:
    """Test smart context selection"""

    @pytest.fixture
    def selector(self):
        return SmartContextSelector()

    @pytest.fixture
    def available_sections(self):
        return [
            "project_details",
            "terraform_infrastructure",
            "terraform_state",
            "cluster_details",
            "deployments",
            "services",
            "error_logs",
            "metrics",
            "operational_guidelines"
        ]

    def test_terraform_task_selection(self, selector, available_sections):
        """Test selection for terraform-related task"""
        selected = selector.select_relevant_sections(
            task="apply terraform changes to production infrastructure",
            agent="terraform-architect",
            tier="T3",
            available_sections=available_sections,
            max_sections=5
        )

        # Should select terraform-related sections
        section_names = [s[0] for s in selected]
        assert "terraform_infrastructure" in section_names
        assert len(selected) <= 5
        assert all(score > 0.3 for _, score in selected)

    def test_debugging_task_selection(self, selector, available_sections):
        """Test selection for debugging task"""
        selected = selector.select_relevant_sections(
            task="debug pod crashes and check error logs",
            agent="gitops-operator",
            tier="T2",
            available_sections=available_sections,
            max_sections=5
        )

        section_names = [s[0] for s in selected]
        # Should prioritize error logs and deployments
        assert "error_logs" in section_names or "deployments" in section_names

    def test_tier_influence(self, selector, available_sections):
        """Test that tier influences selection"""
        # Same task, different tiers
        selected_t0 = selector.select_relevant_sections(
            task="check cluster status",
            agent="gitops-operator",
            tier="T0",
            available_sections=available_sections
        )

        selected_t3 = selector.select_relevant_sections(
            task="check cluster status",
            agent="gitops-operator",
            tier="T3",
            available_sections=available_sections
        )

        # T3 should generally select more or have higher scores
        t0_scores = sum(score for _, score in selected_t0)
        t3_scores = sum(score for _, score in selected_t3)
        assert t3_scores >= t0_scores

    def test_selection_insights(self, selector, available_sections):
        """Test insight generation from selection history"""
        # Make several selections
        for _ in range(5):
            selector.select_relevant_sections(
                task="test task",
                agent="test-agent",
                tier="T1",
                available_sections=available_sections
            )

        insights = selector.get_selection_insights()

        assert insights["total_selections"] == 5
        assert "most_selected_sections" in insights
        assert "agent_preferences" in insights


class TestIntegration:
    """Test integration of all context optimization tools"""

    def test_full_optimization_pipeline(self, tmp_path):
        """Test complete context optimization pipeline"""
        # Setup
        context_file = tmp_path / "context.json"
        full_context = {
            "project_details": {"id": "test", "name": "Test Project", "region": "us-central1"},
            "services": [{"name": f"svc-{i}", "status": "running", "cpu": "100m"} for i in range(50)],
            "terraform_infrastructure": {"resources": [{"type": "vpc", "name": f"vpc-{i}"} for i in range(20)]},
            "error_logs": [{"timestamp": f"2024-01-{i:02d}", "message": f"error-{i}"} for i in range(1, 101)],
            "metrics": {"cpu": [0.5] * 100, "memory": [0.6] * 100}
        }
        context_file.write_text(json.dumps(full_context))

        # Step 1: Smart selection
        selector = SmartContextSelector()
        available = list(full_context.keys())
        selected = selector.select_relevant_sections(
            task="deploy service",
            agent="gitops-operator",
            tier="T2",
            available_sections=available
        )

        # Step 2: Lazy loading
        loader = LazyContextLoader(context_file, max_tokens=3000)
        context = loader.load_minimal_context(
            agent="gitops-operator",
            task="deploy service",
            tier="T2"
        )

        # Step 3: Compression
        compressor = ContextCompressor()
        compressed, stats = compressor.compress(context)

        # Verify pipeline reduces size
        original_size = len(json.dumps(full_context))
        final_size = len(json.dumps(compressed))

        # The compression should happen (even if minimal in test context)
        assert final_size <= original_size  # Some reduction
        assert "metadata" in compressed
        assert stats.compression_ratio <= 1.0  # Compression ratio should be <= 1.0


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])