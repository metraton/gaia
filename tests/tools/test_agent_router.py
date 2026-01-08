"""
Test suite for agent_router.py
Tests unified routing with LLM classifier and legacy compatibility
"""

import pytest
import sys
import importlib
from pathlib import Path

# Add tools directory to path
TOOLS_PATH = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_PATH / "1-routing"))

# Import router components
from agent_router import (
    AgentRouter,
    IntentClassifier,
    CapabilityValidator,
    ROUTING_RULES
)


class TestAgentRouterImport:
    """Test that agent_router module imports correctly"""

    def test_module_imports(self):
        """Should import agent_router without errors"""
        from agent_router import AgentRouter, should_delegate
        
        assert callable(AgentRouter)
        assert callable(should_delegate)

    def test_routing_rules_defined(self):
        """Routing rules should be defined for all agents"""
        expected_agents = [
            "gitops-operator",
            "cloud-troubleshooter",
            "terraform-architect",
            "devops-developer",
            "speckit-planner"
        ]
        
        for agent in expected_agents:
            assert agent in ROUTING_RULES, f"Missing routing rule for: {agent}"


class TestIntentClassifier:
    """Test suite for legacy IntentClassifier (backward compatibility)"""

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
        assert confidence > 0.1, f"Confidence {confidence} should be > 0.1"

    def test_infrastructure_diagnosis_intent(self, classifier):
        """Should classify 'diagnose connectivity' as infrastructure_diagnosis"""
        request = "diagnose cluster connectivity issues"
        intent, confidence = classifier.classify(request)

        assert intent == "infrastructure_diagnosis", \
            f"Expected infrastructure_diagnosis, got {intent}"

    def test_kubernetes_operations_intent(self, classifier):
        """Should classify 'check pod status' as kubernetes_operations"""
        request = "check pod status in tcm-non-prod namespace"
        intent, confidence = classifier.classify(request)

        assert intent == "kubernetes_operations", \
            f"Expected kubernetes_operations, got {intent}"

    def test_application_development_intent(self, classifier):
        """Should classify 'build docker image' as application_development"""
        request = "build docker image and run tests"
        intent, confidence = classifier.classify(request)

        assert intent == "application_development", \
            f"Expected application_development, got {intent}"

    def test_ambiguous_request_low_confidence(self, classifier):
        """Ambiguous requests should return None intent or low confidence"""
        request = "what should i do?"
        intent, confidence = classifier.classify(request)

        if intent is None:
            assert confidence == 0.0
        else:
            assert confidence < 0.5, "Ambiguous request should have low confidence"

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
    """Test suite for legacy CapabilityValidator (backward compatibility)"""

    @pytest.fixture
    def validator(self):
        """Initialize capability validator"""
        return CapabilityValidator()

    def test_terraform_can_create_infrastructure(self, validator):
        """terraform-architect should handle infrastructure_creation"""
        is_valid = validator.validate("terraform-architect", "infrastructure_creation")
        assert is_valid is True

    def test_terraform_cannot_do_kubernetes(self, validator):
        """terraform-architect should not handle kubernetes_operations"""
        is_valid = validator.validate("terraform-architect", "kubernetes_operations")
        assert is_valid is False

    def test_unknown_agent_returns_false(self, validator):
        """Unknown agents should return False"""
        is_valid = validator.validate("unknown-agent", "infrastructure_creation")
        assert is_valid is False

    def test_find_fallback_agent_for_diagnosis(self, validator):
        """Should find valid fallback for infrastructure_diagnosis"""
        fallback = validator.find_fallback_agent("infrastructure_diagnosis")
        assert fallback is not None
        assert validator.validate(fallback, "infrastructure_diagnosis")

    def test_fallback_excludes_agent(self, validator):
        """Fallback should exclude specified agent"""
        primary = "terraform-architect"
        fallback = validator.find_fallback_agent(
            "infrastructure_validation", 
            exclude=primary
        )
        assert fallback != primary

    def test_capability_matrix_consistency(self, validator):
        """Capability matrix should be well-defined"""
        agents = list(validator.agent_capabilities.keys())
        assert len(agents) >= 4, "Should have at least 4 agents"

        for agent, capabilities in validator.agent_capabilities.items():
            assert "can_do" in capabilities
            assert "cannot_do" in capabilities
            
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

    def test_router_initialization(self, router):
        """Router should initialize correctly"""
        assert router is not None
        assert hasattr(router, 'suggest_agent')
        assert hasattr(router, 'routing_rules')

    def test_suggest_agent_returns_tuple(self, router):
        """suggest_agent should return (agent, confidence, reason)"""
        agent, confidence, reason = router.suggest_agent("check pods")
        
        assert isinstance(agent, str)
        assert isinstance(confidence, int)
        assert isinstance(reason, str)
        assert 0 <= confidence <= 100

    def test_kubernetes_routing(self, router):
        """Should route kubernetes requests to gitops-operator"""
        test_requests = [
            "check pod status",
            "deploy to kubernetes",
            "kubectl get pods",
            "check flux reconciliation"
        ]
        
        for request in test_requests:
            agent, confidence, reason = router.suggest_agent(request)
            assert agent == "gitops-operator", \
                f"Expected gitops-operator for '{request}', got {agent}"

    def test_terraform_routing(self, router):
        """Should route terraform requests to terraform-architect"""
        test_requests = [
            "run terraform plan",
            "apply terragrunt changes",
            "validate terraform configuration"
        ]
        
        for request in test_requests:
            agent, confidence, reason = router.suggest_agent(request)
            assert agent == "terraform-architect", \
                f"Expected terraform-architect for '{request}', got {agent}"

    def test_cloud_troubleshoot_routing(self, router):
        """Should route cloud troubleshooting to cloud-troubleshooter"""
        test_requests = [
            "diagnose gke cluster",
            "troubleshoot cloudsql connection",
            "debug gcp iam issue",
            "diagnose eks cluster",
            "troubleshoot aws rds"
        ]

        for request in test_requests:
            agent, confidence, reason = router.suggest_agent(request)
            assert agent == "cloud-troubleshooter", \
                f"Expected cloud-troubleshooter for '{request}', got {agent}"

    def test_fallback_agent(self, router):
        """Should fallback to devops-developer for unknown requests"""
        agent, confidence, reason = router.suggest_agent("xyz abc random")
        
        assert agent == "devops-developer"
        assert confidence <= 50

    def test_explain_routing(self, router):
        """explain_routing should return informative string"""
        explanation = router.explain_routing("check kubernetes pods")
        
        assert isinstance(explanation, str)
        assert "Request:" in explanation
        assert "Agent:" in explanation

    def test_list_agents(self, router):
        """Should list all available agents"""
        agents = router.list_agents()
        
        assert len(agents) >= 5
        assert "gitops-operator" in agents
        assert "terraform-architect" in agents

    def test_get_agent_description(self, router):
        """Should return description for valid agent"""
        desc = router.get_agent_description("gitops-operator")
        
        assert desc is not None
        assert isinstance(desc, str)
        assert len(desc) > 0


class TestRoutingAccuracy:
    """Accuracy tests for routing"""

    @pytest.fixture
    def router(self):
        """Initialize agent router"""
        return AgentRouter()

    def test_routing_accuracy_golden_set(self, router):
        """Test accuracy on golden set of requests"""
        golden_set = [
            # kubernetes -> gitops-operator
            ("check pod status in default", "gitops-operator"),
            ("verify flux reconciliation", "gitops-operator"),
            ("deploy service to cluster", "gitops-operator"),

            # terraform -> terraform-architect
            ("validate terraform config", "terraform-architect"),
            ("run terragrunt plan", "terraform-architect"),

            # cloud troubleshooting -> cloud-troubleshooter
            ("diagnose gke connectivity", "cloud-troubleshooter"),
            ("troubleshoot cloudsql", "cloud-troubleshooter"),
            ("diagnose eks cluster", "cloud-troubleshooter"),
            ("troubleshoot aws rds", "cloud-troubleshooter"),

            # dev -> devops-developer
            ("build docker image", "devops-developer"),
            ("run unit tests", "devops-developer"),
        ]

        correct = 0
        failures = []
        
        for request, expected_agent in golden_set:
            agent, _, _ = router.suggest_agent(request)
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
        
        # Expect at least 75% accuracy
        assert accuracy >= 0.75, \
            f"Routing accuracy should be >= 75%, got {accuracy*100:.1f}%"

        print(f"\nRouting Accuracy: {accuracy*100:.1f}% ({correct}/{len(golden_set)})")


class TestDelegationMatrix:
    """Test should_delegate function"""

    def test_should_delegate_returns_dict(self):
        """should_delegate should return dict with expected keys"""
        from agent_router import should_delegate
        
        result = should_delegate("check pod status")
        
        assert isinstance(result, dict)
        assert "delegate" in result
        assert "decision" in result
        assert "reason" in result

    def test_should_delegate_suggests_agent(self):
        """When delegating, should suggest an agent"""
        from agent_router import should_delegate
        
        result = should_delegate("deploy to kubernetes cluster")
        
        if result["delegate"]:
            assert "suggested_agent" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
