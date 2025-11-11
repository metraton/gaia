"""
Phase 0 Regression Tests

Regression tests for specific cases that should trigger clarification.
These tests validate real-world scenarios where ambiguity detection is critical.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))

from clarification import request_clarification, execute_workflow


def test_regression_valida_servicio_tcm():
    """
    Regression test for: "valida el servicio de tcm"

    This SHOULD trigger clarification because:
    1. "el servicio" is ambiguous (ServiceAmbiguityPattern)
    2. "tcm" is not a specific service name (could be tcm-api, tcm-web, tcm-bot, tcm-jobs)
    3. "validar" is generic (no specific action)

    Expected behavior:
    - Detect service ambiguity
    - Ambiguity score >= 30 (above threshold)
    - Offer options: tcm-api, tcm-web, tcm-bot, tcm-jobs
    """

    user_request = "valida el servicio de tcm"

    # Phase 0: Should detect ambiguity
    clarification = request_clarification(user_request)

    # Assertions
    assert clarification["needs_clarification"] == True, \
        "Should need clarification for ambiguous service reference"

    assert clarification["ambiguity_score"] >= 30, \
        f"Ambiguity score {clarification['ambiguity_score']} should be >= 30"

    # Should detect service ambiguity pattern
    service_ambiguity = next(
        (a for a in clarification.get("ambiguity_points", [])
         if "service" in a["pattern"]),
        None
    )

    assert service_ambiguity is not None, \
        "Should detect service ambiguity pattern"

    # Should offer service options containing "tcm"
    available_options = service_ambiguity.get("available_options", [])
    tcm_services = [opt for opt in available_options if "tcm" in opt.lower()]

    assert len(tcm_services) > 0, \
        f"Should offer TCM services, got: {available_options}"


def test_regression_check_the_api():
    """
    Regression test for: "Check the API"

    Generic reference to "the API" when multiple APIs exist should trigger clarification.
    """

    clarification = request_clarification("Check the API")

    assert clarification["needs_clarification"] == True
    assert clarification["ambiguity_score"] > 30

    # Should detect service ambiguity
    patterns = [a["pattern"] for a in clarification.get("ambiguity_points", [])]
    assert "service_ambiguity" in patterns


def test_regression_deploy_to_cluster():
    """
    Regression test for: "Deploy to cluster"

    Missing namespace specification should trigger clarification.
    """

    clarification = request_clarification("Deploy to cluster")

    assert clarification["needs_clarification"] == True

    # Should detect namespace ambiguity
    patterns = [a["pattern"] for a in clarification.get("ambiguity_points", [])]
    assert "namespace_ambiguity" in patterns


def test_regression_deploy_to_production():
    """
    Regression test for: "Deploy to production"

    When project-context says "non-prod" but user mentions "production",
    should trigger environment warning.
    """

    clarification = request_clarification("Deploy to production")

    assert clarification["needs_clarification"] == True

    # Should detect environment mismatch
    patterns = [a["pattern"] for a in clarification.get("ambiguity_points", [])]
    assert "environment_ambiguity" in patterns

    # Should have high weight (90)
    env_ambiguity = next(
        (a for a in clarification.get("ambiguity_points", [])
         if "environment" in a["pattern"]),
        None
    )
    assert env_ambiguity["weight"] >= 80, \
        "Environment mismatch should have high weight"


def test_no_clarification_for_specific_prompt():
    """
    Test that specific prompts do NOT trigger clarification.

    "Check tcm-api service in tcm-non-prod namespace" is fully qualified
    and should not need clarification.
    """

    clarification = request_clarification(
        "Check tcm-api service in tcm-non-prod namespace"
    )

    assert clarification["needs_clarification"] == False, \
        "Specific prompt should not need clarification"

    assert clarification.get("ambiguity_score", 0) <= 30, \
        "Specific prompt should have low ambiguity score"


def test_spanish_keywords_detection():
    """
    Test that Spanish keywords are properly detected.

    "Chequea el servicio" should trigger same clarification as "Check the service"
    """

    clarification = request_clarification("Chequea el servicio")

    assert clarification["needs_clarification"] == True

    # Should detect service ambiguity
    patterns = [a["pattern"] for a in clarification.get("ambiguity_points", [])]
    assert "service_ambiguity" in patterns


def test_execute_workflow_without_ask_function():
    """
    Test execute_workflow in manual mode (no ask_user_question_func).

    Should return questions for manual handling.
    """

    result = execute_workflow("Check the API")

    assert result.get("needs_manual_questioning") == True
    assert "questions" in result
    assert len(result["questions"]) > 0
    assert "summary" in result

    # Original prompt not enriched yet
    assert result["enriched_prompt"] == "Check the API"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
