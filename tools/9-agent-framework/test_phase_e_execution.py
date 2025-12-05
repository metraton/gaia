#!/usr/bin/env python3
"""
Tests for Phase E: Execution Manager
"""

import pytest
from execution_manager import ExecutionManager, ExecutionMetrics, ExecutionProfile


class TestExecutionManager:
    """Test execution manager functionality"""

    @pytest.fixture
    def manager(self):
        """Create ExecutionManager instance"""
        return ExecutionManager()

    def test_manager_initialization(self, manager):
        """Test that execution manager initializes correctly"""
        assert manager is not None
        assert hasattr(manager, 'execute')

    def test_execution_profiles_exist(self):
        """Test that execution profiles are defined"""
        assert ExecutionProfile.DRY_RUN
        assert ExecutionProfile.APPLY
        assert ExecutionProfile.RECONCILE

    def test_execute_with_dry_run_profile(self, manager):
        """Test execution with DRY_RUN profile"""
        payload = {
            "operation": "validate",
            "profile": ExecutionProfile.DRY_RUN.value
        }

        result = manager.execute(payload)
        assert isinstance(result, ExecutionMetrics)
        # Dry run should complete without errors
        assert result.success is True

    def test_execution_metrics_structure(self, manager):
        """Test that execution metrics have required fields"""
        payload = {"operation": "test"}
        metrics = manager.execute(payload)

        assert hasattr(metrics, 'success')
        assert hasattr(metrics, 'duration_ms')
        assert isinstance(metrics.success, bool)
        assert isinstance(metrics.duration_ms, (int, float))

    def test_execute_with_empty_payload(self, manager):
        """Test execution with empty payload"""
        result = manager.execute({})
        assert isinstance(result, ExecutionMetrics)
        # Should handle gracefully

    def test_execution_completes_in_reasonable_time(self, manager):
        """Test that execution completes within acceptable time"""
        import time

        payload = {"operation": "test"}

        start_time = time.time()
        result = manager.execute(payload)
        duration = time.time() - start_time

        # Execution should complete quickly for simple operations
        assert duration < 5.0
        assert isinstance(result, ExecutionMetrics)


class TestExecutionMetrics:
    """Test ExecutionMetrics dataclass"""

    def test_metrics_creation(self):
        """Test creating execution metrics"""
        metrics = ExecutionMetrics(
            success=True,
            duration_ms=100,
            operations_executed=5
        )

        assert metrics.success is True
        assert metrics.duration_ms == 100
        assert metrics.operations_executed == 5

    def test_metrics_with_failure(self):
        """Test metrics for failed execution"""
        metrics = ExecutionMetrics(
            success=False,
            duration_ms=50,
            operations_executed=2,
            error_message="Operation failed"
        )

        assert metrics.success is False
        assert metrics.error_message == "Operation failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
