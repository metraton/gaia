"""
Test suite for agent_router.py
Tests semantic routing, intent classification, and capability validation
"""

import pytest
import sys
import importlib
from pathlib import Path

# Add tools directory to path
TOOLS_PATH = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_PATH))

# Import from reorganized tools structure
routing = importlib.import_module("1-routing")
IntentClassifier = routing.IntentClassifier
CapabilityValidator = routing.CapabilityValidator
AgentRouter = routing.AgentRouter


class TestIntentClassifier:
    """Test suite for semantic intent classification"""

    @pytest.fixture
    def classifier(self):
        """Initialize intent classifier"""
        return IntentClassifier()

    def test_infrastructure_creation_intent(self, classifier):
        """Should classify 'create cluster' as infrastructure_creation"""
        request = "create a new gke cluster"
        intent, confidence = classifier.classify(request)

        assert intent == "infrastructure_creation", \
            f"Expected infrastructure_creation, got {intent}"
        assert confidence > 0.3, f"Confidence {confidence} should be > 0.3"
        assert 0 <= confidence <= 1, "Confidence should be normalized to 0-1"

    def test_infrastructure_diagnosis_intent(self, classifier):
        """Should classify 'diagnose connectivity' as infrastructure_diagnosis"""
        request = "diagnose cluster connectivity issues"
        intent, confidence = classifier.classify(request)

        assert intent == "infrastructure_diagnosis", \
            f"Expected infrastructure_diagnosis, got {intent}"
        assert confidence > 0.3, f"Confidence {confidence} should be > 0.3"

    def test_kubernetes_operations_intent(self, classifier):
        """Should classify 'check pod status' as kubernetes_operations"""
        request = "check pod status in tcm-non-prod namespace"
        intent, confidence = classifier.classify(request)

        assert intent == "kubernetes_operations", \
            f"Expected kubernetes_operations, got {intent}"
        assert confidence > 0.3, f"Confidence {confidence} should be > 0.3"

    def test_application_development_intent(self, classifier):
        """Should classify 'build docker image' as application_development"""
        request = "build docker image and run tests"
        intent, confidence = classifier.classify(request)

        assert intent == "application_development", \
            f"Expected application_development, got {intent}"
        assert confidence > 0.3, f"Confidence {confidence} should be > 0.3"

    def test_infrastructure_validation_intent(self, classifier):
        """Should classify 'validate terraform' as infrastructure_validation"""
        request = "validate terraform configuration"
        intent, confidence = classifier.classify(request)

        assert intent == "infrastructure_validation", \
            f"Expected infrastructure_validation, got {intent}"
        assert confidence > 0.3, f"Confidence {confidence} should be > 0.3"

    def test_ambiguous_request_low_confidence(self, classifier):
        """Ambiguous requests should return None intent or low confidence"""
        request = "what should i do?"
        intent, confidence = classifier.classify(request)

        if intent is None:
            assert confidence == 0.0
        else:
            assert confidence < 0.3, \
                "Ambiguous request should have low confidence"

    def test_classification_consistency(self, classifier):
        """Same request should always classify to same intent"""
        request = "create a new vpc network"

        results = []
        for _ in range(5):
            intent, _ = classifier.classify(request)
            results.append(intent)

        assert len(set(results)) == 1, \
            "Classification should be deterministic and consistent"


class TestCapabilityValidator:
    """Test suite for agent capability validation"""

    @pytest.fixture
    def validator(self):
        """Initialize capability validator"""
        return CapabilityValidator()

    def test_terraform_can_create_infrastructure(self, validator):
        """terraform-architect should handle infrastructure_creation"""
        is_valid = validator.validate("terraform-architect", "infrastructure_creation")
        assert is_valid is True, \
            "terraform-architect should handle infrastructure_creation"

    def test_terraform_cannot_do_kubernetes(self, validator):
        """terraform-architect should not handle kubernetes_operations"""
        is_valid = validator.validate("terraform-architect", "kubernetes_operations")
        assert is_valid is False, \
            "terraform-architect cannot handle kubernetes_operations"

    def test_unknown_agent_returns_false(self, validator):
        """Unknown agents should return False"""
        is_valid = validator.validate("unknown-agent", "infrastructure_creation")
        assert is_valid is False, "Unknown agents should be invalid"

    def test_find_fallback_agent_for_diagnosis(self, validator):
        """Should find valid fallback for infrastructure_diagnosis"""
        fallback = validator.find_fallback_agent("infrastructure_diagnosis")

        assert fallback is not None, "Should find a fallback agent"
        assert validator.validate(fallback, "infrastructure_diagnosis"), \
            "Fallback agent should be capable"

    def test_fallback_excludes_agent(self, validator):
        """Fallback should exclude specified agent"""
        primary = "terraform-architect"
        fallback = validator.find_fallback_agent(
            "infrastructure_validation", 
            exclude=primary
        )

        assert fallback != primary, \
            "Fallback should not be the same as excluded agent"

    def test_capability_matrix_consistency(self, validator):
        """Capability matrix should be well-defined"""
        agents = list(validator.agent_capabilities.keys())
        assert len(agents) >= 4, "Should have at least 4 agents"

        for agent, capabilities in validator.agent_capabilities.items():
            assert "can_do" in capabilities, \
                f"{agent} should have 'can_do' list"
            assert "cannot_do" in capabilities, \
                f"{agent} should have 'cannot_do' list"
            
            # No intent should be in both lists
            conflict = set(capabilities["can_do"]) & set(capabilities["cannot_do"])
            assert len(conflict) == 0, \
                f"{agent} has conflicting capabilities: {conflict}"


class TestAgentRouter:
    """Integration tests for AgentRouter"""

    @pytest.fixture
    def router(self):
        """Initialize agent router"""
        return AgentRouter()

    def test_router_has_semantic_routing(self, router):
        """Router should have semantic routing capability"""
        assert hasattr(router, '_route_semantic'), \
            "Router should have _route_semantic method"
        assert hasattr(router, 'intent_classifier'), \
            "Router should have intent_classifier"
        assert hasattr(router, 'capability_validator'), \
            "Router should have capability_validator"

    def test_semantic_routing_returns_proper_format(self, router):
        """_route_semantic should return (agent, confidence, reason)"""
        agent, confidence, reason = router._route_semantic("create a cluster")

        assert isinstance(agent, str), "Agent should be a string"
        assert isinstance(confidence, float), "Confidence should be a float"
        assert isinstance(reason, str), "Reason should be a string"
        assert 0 <= confidence <= 1, "Confidence should be normalized 0-1"

    def test_semantic_routing_selects_valid_agent(self, router):
        """_route_semantic should only select valid agents"""
        test_requests = [
            "create vpc",
            "diagnose connectivity",
            "check pod logs",
            "build docker image",
            "validate terraform"
        ]

        valid_agents = [
            "terraform-architect", 
            "gitops-operator", 
            "gcp-troubleshooter", 
            "devops-developer"
        ]

        for request in test_requests:
            agent, _, _ = router._route_semantic(request)
            assert agent in valid_agents, \
                f"Got invalid agent {agent} for request: {request}"


class TestRoutingAccuracy:
    """Accuracy tests for semantic routing"""

    @pytest.fixture
    def router(self):
        """Initialize agent router"""
        return AgentRouter()

    def test_semantic_routing_accuracy_golden_set(self, router):
        """Test accuracy on golden set of requests"""
        golden_set = [
            # infrastructure_creation -> terraform-architect
            ("create a new gke cluster", "terraform-architect"),
            ("provision vpc for prod", "terraform-architect"),
            ("deploy infrastructure changes", "terraform-architect"),
            
            # infrastructure_diagnosis -> gcp-troubleshooter
            ("diagnose connectivity issues", "gcp-troubleshooter"),
            ("troubleshoot cluster crash", "gcp-troubleshooter"),
            
            # kubernetes_operations -> gitops-operator
            ("check pod status in default", "gitops-operator"),
            ("verify flux reconciliation", "gitops-operator"),
            
            # application_development -> devops-developer
            ("build docker image", "devops-developer"),
            ("run unit tests", "devops-developer"),
            
            # infrastructure_validation -> terraform-architect
            ("validate terraform config", "terraform-architect"),
        ]

        correct = 0
        failures = []
        
        for request, expected_agent in golden_set:
            agent, _, _ = router._route_semantic(request)
            if agent == expected_agent:
                correct += 1
            else:
                failures.append({
                    "request": request,
                    "expected": expected_agent,
                    "got": agent
                })

        accuracy = correct / len(golden_set)
        
        if failures:
            print("\nRouting failures:")
            for failure in failures:
                print(f"  '{failure['request']}'")
                print(f"    Expected: {failure['expected']}, Got: {failure['got']}")
        
        assert accuracy >= 0.75, \
            f"Semantic routing accuracy should be >= 75%, got {accuracy*100:.1f}%"

        print(f"\nSemantic Routing Accuracy: {accuracy*100:.1f}% ({correct}/{len(golden_set)})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
