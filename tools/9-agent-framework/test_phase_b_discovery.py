#!/usr/bin/env python3
"""
Tests for Phase B: Local Discovery
"""

import pytest
from pathlib import Path
from local_discoverer import LocalDiscoverer, DiscoveryResult


class TestLocalDiscovery:
    """Test local discovery functionality"""

    @pytest.fixture
    def discoverer(self):
        """Create LocalDiscoverer instance"""
        return LocalDiscoverer()

    @pytest.fixture
    def sample_payload(self):
        """Sample payload for testing"""
        return {
            "operation": "validate",
            "paths": [str(Path(__file__).parent)]
        }

    def test_discoverer_initialization(self, discoverer):
        """Test that discoverer initializes correctly"""
        assert discoverer is not None
        assert hasattr(discoverer, 'discover')

    def test_discover_returns_result(self, discoverer, sample_payload):
        """Test that discover method returns DiscoveryResult"""
        result = discoverer.discover(sample_payload)
        assert isinstance(result, DiscoveryResult)

    def test_discovery_result_has_required_fields(self, discoverer, sample_payload):
        """Test that discovery result contains required fields"""
        result = discoverer.discover(sample_payload)

        assert hasattr(result, 'files_scanned')
        assert hasattr(result, 'resources_found')
        assert isinstance(result.files_scanned, int)
        assert result.files_scanned >= 0

    def test_discover_with_empty_payload(self, discoverer):
        """Test discovery with empty payload"""
        result = discoverer.discover({})
        assert isinstance(result, DiscoveryResult)
        # Should handle gracefully

    def test_discover_with_nonexistent_path(self, discoverer):
        """Test discovery with path that doesn't exist"""
        payload = {
            "paths": ["/nonexistent/path/that/does/not/exist"]
        }
        result = discoverer.discover(payload)
        assert isinstance(result, DiscoveryResult)
        # Should handle errors gracefully

    def test_discover_finds_python_files(self, discoverer):
        """Test that discoverer finds Python files in test directory"""
        payload = {
            "paths": [str(Path(__file__).parent)]
        }
        result = discoverer.discover(payload)

        # Should find at least this test file
        assert result.files_scanned > 0

    def test_discovery_result_serializable(self, discoverer, sample_payload):
        """Test that discovery result can be serialized"""
        result = discoverer.discover(sample_payload)

        # Should be able to convert to dict (for JSON serialization)
        try:
            from dataclasses import asdict
            result_dict = asdict(result)
            assert isinstance(result_dict, dict)
        except:
            # If not a dataclass, should have __dict__
            assert hasattr(result, '__dict__')


class TestDiscoveryPerformance:
    """Test discovery performance characteristics"""

    @pytest.fixture
    def discoverer(self):
        return LocalDiscoverer()

    def test_discovery_completes_in_reasonable_time(self, discoverer):
        """Test that discovery completes within acceptable time"""
        import time

        payload = {
            "paths": [str(Path(__file__).parent)]
        }

        start_time = time.time()
        result = discoverer.discover(payload)
        duration = time.time() - start_time

        # Discovery should complete in under 5 seconds for local directory
        assert duration < 5.0
        assert isinstance(result, DiscoveryResult)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
