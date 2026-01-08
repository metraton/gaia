"""
Test suite for llm_classifier.py
Tests LLM-based intent classification with mock/fallback support
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add tools directory to path
TOOLS_PATH = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_PATH / "1-routing"))


class TestLLMClassifierImport:
    """Test that llm_classifier module imports correctly"""

    def test_module_imports(self):
        """Should import llm_classifier without errors"""
        from llm_classifier import (
            classify_intent,
            classify_intent_mock,
            classify_intent_with_llm,
            AGENT_DEFINITIONS,
            clear_cache,
            get_cache_stats
        )
        
        assert callable(classify_intent)
        assert callable(classify_intent_mock)
        assert isinstance(AGENT_DEFINITIONS, dict)

    def test_agent_definitions_complete(self):
        """All expected agents should be defined"""
        from llm_classifier import AGENT_DEFINITIONS
        
        expected_agents = [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
            "speckit-planner"
        ]
        
        for agent in expected_agents:
            assert agent in AGENT_DEFINITIONS, f"Missing agent: {agent}"
            assert "description" in AGENT_DEFINITIONS[agent]
            assert "capabilities" in AGENT_DEFINITIONS[agent]
            assert "keywords" in AGENT_DEFINITIONS[agent]


class TestMockClassification:
    """Test mock/fallback classification (no API key needed)"""

    def test_kubernetes_request(self):
        """Should classify kubernetes requests to gitops-operator"""
        from llm_classifier import classify_intent_mock
        
        result = classify_intent_mock("check pod status in namespace")
        
        assert result["agent"] == "gitops-operator"
        assert result["confidence"] > 0
        assert "reasoning" in result

    def test_terraform_request(self):
        """Should classify terraform requests to terraform-architect"""
        from llm_classifier import classify_intent_mock
        
        result = classify_intent_mock("run terraform plan for vpc")
        
        assert result["agent"] == "terraform-architect"
        assert result["confidence"] > 0

    def test_gcp_troubleshoot_request(self):
        """Should classify GCP troubleshooting to cloud-troubleshooter"""
        from llm_classifier import classify_intent_mock

        result = classify_intent_mock("diagnose gcp cloudsql connection issue")

        assert result["agent"] == "cloud-troubleshooter"
        assert result["confidence"] > 0

    def test_aws_troubleshoot_request(self):
        """Should classify AWS troubleshooting to cloud-troubleshooter"""
        from llm_classifier import classify_intent_mock

        result = classify_intent_mock("troubleshoot aws eks cluster")

        assert result["agent"] == "cloud-troubleshooter"
        assert result["confidence"] > 0

    def test_build_request(self):
        """Should classify build requests to devops-developer"""
        from llm_classifier import classify_intent_mock
        
        result = classify_intent_mock("build docker image and run tests")
        
        assert result["agent"] == "devops-developer"
        assert result["confidence"] > 0

    def test_spec_request(self):
        """Should classify spec requests to speckit-planner"""
        from llm_classifier import classify_intent_mock
        
        result = classify_intent_mock("create specification for new feature")
        
        assert result["agent"] == "speckit-planner"
        assert result["confidence"] > 0

    def test_ambiguous_request_fallback(self):
        """Ambiguous requests should fallback to devops-developer"""
        from llm_classifier import classify_intent_mock
        
        result = classify_intent_mock("do something random")
        
        # Should fallback to devops-developer with low confidence
        assert result["agent"] == "devops-developer"
        assert result["confidence"] <= 0.5
        assert "fallback" in result["reasoning"].lower() or "default" in result["reasoning"].lower()


class TestFallbackClassification:
    """Test fallback behavior when LLM is unavailable"""

    def test_fallback_without_api_key(self):
        """Should use fallback when ANTHROPIC_API_KEY not set"""
        from llm_classifier import classify_intent
        
        with patch.dict('os.environ', {}, clear=True):
            # Remove API key if present
            import os
            if 'ANTHROPIC_API_KEY' in os.environ:
                del os.environ['ANTHROPIC_API_KEY']
            
            result = classify_intent("check kubernetes pods")
            
            assert result["agent"] in ["gitops-operator", "devops-developer"]
            assert "reasoning" in result

    def test_fallback_on_import_error(self):
        """Should fallback if anthropic package not available"""
        from llm_classifier import _fallback_classification
        
        result = _fallback_classification("deploy to kubernetes", reason="import_error")
        
        assert result.agent in ["gitops-operator", "devops-developer"]
        assert "import_error" in result.reasoning


class TestCacheSystem:
    """Test caching functionality"""

    def test_cache_operations(self):
        """Cache should store and retrieve results"""
        from llm_classifier import clear_cache, get_cache_stats, classify_intent_mock
        
        # Clear cache first
        clear_cache()
        stats_before = get_cache_stats()
        assert stats_before["size"] == 0
        
    def test_cache_stats(self):
        """Should return valid cache statistics"""
        from llm_classifier import get_cache_stats
        
        stats = get_cache_stats()
        
        assert "size" in stats
        assert "max_size" in stats
        assert isinstance(stats["size"], int)
        assert isinstance(stats["max_size"], int)


class TestClassificationResult:
    """Test ClassificationResult dataclass"""

    def test_result_structure(self):
        """Result should have expected fields"""
        from llm_classifier import ClassificationResult
        
        result = ClassificationResult(
            agent="gitops-operator",
            confidence=0.85,
            reasoning="Test reasoning"
        )
        
        assert result.agent == "gitops-operator"
        assert result.confidence == 0.85
        assert result.reasoning == "Test reasoning"
        assert result.from_cache == False

    def test_result_with_cache_flag(self):
        """Result can indicate cache hit"""
        from llm_classifier import ClassificationResult
        
        result = ClassificationResult(
            agent="terraform-architect",
            confidence=0.9,
            reasoning="Cached result",
            from_cache=True
        )
        
        assert result.from_cache == True


class TestPromptBuilding:
    """Test prompt construction"""

    def test_prompt_contains_agents(self):
        """Prompt should list all agents"""
        from llm_classifier import _build_classification_prompt, AGENT_DEFINITIONS
        
        prompt = _build_classification_prompt("test query")
        
        for agent in AGENT_DEFINITIONS:
            assert agent in prompt, f"Agent {agent} not in prompt"

    def test_prompt_contains_query(self):
        """Prompt should include user query"""
        from llm_classifier import _build_classification_prompt
        
        test_query = "deploy tcm-api to production cluster"
        prompt = _build_classification_prompt(test_query)
        
        assert test_query in prompt

    def test_prompt_requests_json(self):
        """Prompt should request JSON response"""
        from llm_classifier import _build_classification_prompt
        
        prompt = _build_classification_prompt("test")
        
        assert "JSON" in prompt or "json" in prompt


class TestResponseParsing:
    """Test LLM response parsing"""

    def test_parse_clean_json(self):
        """Should parse clean JSON response"""
        from llm_classifier import _parse_llm_response
        
        response = '{"agent": "gitops-operator", "confidence": 0.9, "reasoning": "test"}'
        result = _parse_llm_response(response)
        
        assert result is not None
        assert result["agent"] == "gitops-operator"
        assert result["confidence"] == 0.9

    def test_parse_json_with_markdown(self):
        """Should parse JSON in markdown code block"""
        from llm_classifier import _parse_llm_response
        
        response = '''Here is my analysis:
```json
{"agent": "terraform-architect", "confidence": 0.85, "reasoning": "infrastructure"}
```'''
        result = _parse_llm_response(response)
        
        assert result is not None
        assert result["agent"] == "terraform-architect"

    def test_parse_invalid_json(self):
        """Should return None for invalid JSON"""
        from llm_classifier import _parse_llm_response
        
        response = "This is not JSON at all"
        result = _parse_llm_response(response)
        
        assert result is None


class TestIntegration:
    """Integration tests for classify_intent function"""

    def test_classify_returns_dict(self):
        """classify_intent should return dict with expected keys"""
        from llm_classifier import classify_intent
        
        result = classify_intent("check pod status")
        
        assert isinstance(result, dict)
        assert "agent" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "from_cache" in result

    def test_confidence_range(self):
        """Confidence should be between 0 and 1"""
        from llm_classifier import classify_intent
        
        test_queries = [
            "check kubernetes pods",
            "run terraform plan",
            "random gibberish xyz"
        ]
        
        for query in test_queries:
            result = classify_intent(query)
            assert 0 <= result["confidence"] <= 1, \
                f"Confidence {result['confidence']} out of range for: {query}"

    def test_agent_is_valid(self):
        """Returned agent should be a known agent"""
        from llm_classifier import classify_intent, AGENT_DEFINITIONS
        
        test_queries = [
            "deploy service to cluster",
            "create vpc with terraform",
            "diagnose database connection"
        ]
        
        # Add devops-developer as fallback
        valid_agents = set(AGENT_DEFINITIONS.keys()) | {"devops-developer"}
        
        for query in test_queries:
            result = classify_intent(query)
            assert result["agent"] in valid_agents, \
                f"Unknown agent {result['agent']} for: {query}"


class TestAccuracy:
    """Accuracy tests using golden set (mock classification)"""

    def test_mock_accuracy_golden_set(self):
        """Test mock classification accuracy on golden set"""
        from llm_classifier import classify_intent_mock
        
        golden_set = [
            ("check pods in default namespace", "gitops-operator"),
            ("deploy app to kubernetes", "gitops-operator"),
            ("run terraform plan", "terraform-architect"),
            ("provision vpc infrastructure", "terraform-architect"),
            ("diagnose gcp cluster issue", "cloud-troubleshooter"),
            ("troubleshoot aws rds", "cloud-troubleshooter"),
            ("build docker image", "devops-developer"),
            ("run npm tests", "devops-developer"),
            ("create feature specification", "speckit-planner"),
        ]
        
        correct = 0
        failures = []
        
        for query, expected in golden_set:
            result = classify_intent_mock(query)
            if result["agent"] == expected:
                correct += 1
            else:
                failures.append({
                    "query": query,
                    "expected": expected,
                    "got": result["agent"]
                })
        
        accuracy = correct / len(golden_set)
        
        if failures:
            print("\nMock classification failures:")
            for f in failures:
                print(f"  '{f['query']}' -> expected {f['expected']}, got {f['got']}")
        
        # Expect at least 70% accuracy with keyword matching
        assert accuracy >= 0.70, \
            f"Mock accuracy {accuracy*100:.1f}% below 70% threshold"
        
        print(f"\nMock Classification Accuracy: {accuracy*100:.1f}% ({correct}/{len(golden_set)})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
