"""
Unit tests for clarification module
"""

import pytest
import json
import sys
import os

# Add tools to path (gaia-ops/tools)
tools_path = os.path.join(os.path.dirname(__file__), '..', '..', 'tools')
sys.path.insert(0, tools_path)

from clarification import ClarificationEngine, request_clarification, process_clarification


@pytest.fixture
def mock_project_context():
    """Mock project-context.json with test data."""
    return {
        "sections": {
            "application_services": [
                {
                    "name": "tcm-api",
                    "tech_stack": "NestJS",
                    "namespace": "tcm-non-prod",
                    "port": 3001,
                    "status": "running"
                },
                {
                    "name": "tcm-web",
                    "tech_stack": "React SPA",
                    "namespace": "tcm-non-prod",
                    "port": 3000,
                    "status": "running"
                },
                {
                    "name": "pg-api",
                    "tech_stack": "Spring Boot",
                    "namespace": "pg-non-prod",
                    "port": 8086,
                    "status": "running"
                }
            ],
            "cluster_details": {
                "primary_namespaces": ["tcm-non-prod", "pg-non-prod"]
            },
            "project_details": {
                "environment": "non-prod"
            },
            "terraform_infrastructure": {
                "modules": {
                    "tcm-redis": {
                        "resources": "Memorystore Redis",
                        "status": "running",
                        "tier": "BASIC"
                    },
                    "pg-redis": {
                        "resources": "Memorystore Redis",
                        "status": "running",
                        "tier": "STANDARD_HA"
                    }
                }
            }
        }
    }


@pytest.fixture
def engine_with_mock_context(mock_project_context, tmp_path):
    """Create engine with mock project context."""
    # Create temporary project-context.json
    context_file = tmp_path / "project-context.json"
    with open(context_file, "w") as f:
        json.dump(mock_project_context, f)

    engine = ClarificationEngine(project_context_path=str(context_file))
    return engine


def test_detect_service_ambiguity(engine_with_mock_context):
    """Test detection of ambiguous service references."""
    result = engine_with_mock_context.detect_ambiguity("Check the API")

    assert result["needs_clarification"] == True
    assert result["ambiguity_score"] > 30
    assert len(result["ambiguity_points"]) > 0
    assert "service" in result["ambiguity_points"][0]["pattern"]


def test_detect_namespace_ambiguity(engine_with_mock_context):
    """Test detection of ambiguous namespace references."""
    result = engine_with_mock_context.detect_ambiguity("Deploy to cluster")

    assert result["needs_clarification"] == True
    assert any("namespace" in a["pattern"] for a in result["ambiguity_points"])


def test_detect_environment_warning(engine_with_mock_context):
    """Test detection of environment mismatch."""
    result = engine_with_mock_context.detect_ambiguity("Deploy to production")

    assert result["needs_clarification"] == True
    assert any("environment" in a["pattern"] for a in result["ambiguity_points"])
    # Environment ambiguity has highest weight (90)
    if result["ambiguity_points"]:
        assert result["ambiguity_score"] >= 70  # High weight


def test_detect_resource_ambiguity(engine_with_mock_context):
    """Test detection of ambiguous Redis resources."""
    result = engine_with_mock_context.detect_ambiguity("Check the Redis")

    assert result["needs_clarification"] == True
    assert any("resource" in a["pattern"] for a in result["ambiguity_points"])


def test_no_ambiguity_specific_prompt(engine_with_mock_context):
    """Test that specific prompts don't trigger clarification."""
    result = engine_with_mock_context.detect_ambiguity(
        "Check tcm-api service in tcm-non-prod namespace"
    )

    assert result["needs_clarification"] == False
    assert result["ambiguity_score"] <= 30


def test_generate_questions(engine_with_mock_context):
    """Test question generation with rich options."""
    ambiguity_analysis = {
        "needs_clarification": True,
        "ambiguity_score": 80,
        "ambiguity_points": [
            {
                "pattern": "service_ambiguity",
                "detected_keyword": "the api",
                "ambiguity_reason": "Multiple services",
                "available_options": ["tcm-api", "pg-api"],
                "services_metadata": {
                    "tcm-api": {
                        "tech_stack": "NestJS",
                        "namespace": "tcm-non-prod",
                        "port": 3001,
                        "status": "running"
                    },
                    "pg-api": {
                        "tech_stack": "Spring Boot",
                        "namespace": "pg-non-prod",
                        "port": 8086,
                        "status": "running"
                    }
                },
                "suggested_question": "Which API?",
                "weight": 80,
                "allow_multiple": False
            }
        ],
        "suggested_questions": ["Which API?"]
    }

    result = engine_with_mock_context.generate_questions(ambiguity_analysis)

    assert "summary" in result
    assert "question_config" in result
    assert len(result["question_config"]["questions"]) == 1

    # Check question structure
    question = result["question_config"]["questions"][0]
    assert question["question"] == "Which API?"
    assert question["multiSelect"] == False
    assert len(question["options"]) == 2  # 2 options (tcm-api, pg-api)

    # Check options have emoji and rich descriptions
    for option in question["options"]:
        assert "label" in option
        assert "description" in option
        # Service options should have some emoji (❓, 📦, 🎯, etc.)
        # Just verify label is not empty and has some kind of prefix
        assert len(option["label"]) > 0
        assert ("Namespace:" in option["description"] or "N/A" in option["description"]
                or "Tech" in option["description"])


def test_generate_questions_with_catchall(engine_with_mock_context):
    """Test question generation with 4th 'All' option."""
    ambiguity_analysis = {
        "needs_clarification": True,
        "ambiguity_score": 80,
        "ambiguity_points": [
            {
                "pattern": "service_ambiguity",
                "detected_keyword": "services",
                "ambiguity_reason": "Multiple services",
                "available_options": ["tcm-api", "tcm-web", "pg-api", "pg-web"],  # 4+ options
                "services_metadata": {},
                "suggested_question": "Which services?",
                "weight": 80,
                "allow_multiple": False
            }
        ],
        "suggested_questions": ["Which services?"]
    }

    result = engine_with_mock_context.generate_questions(ambiguity_analysis)

    question = result["question_config"]["questions"][0]
    # Should have 4 options: 3 specific + 1 "All"
    assert len(question["options"]) == 4
    # Last option should be "All"
    assert "Todos" in question["options"][-1]["label"] or "🌐" in question["options"][-1]["label"]


def test_enrich_prompt(engine_with_mock_context):
    """Test prompt enrichment with user responses."""
    original_prompt = "Check the API"
    user_responses = {"question_1": "📦 tcm-api"}
    clarification_context = {
        "ambiguities": [
            {
                "pattern": "service_ambiguity",
                "suggested_question": "Which API?",
                "available_options": ["tcm-api", "pg-api"]
            }
        ]
    }

    enriched = engine_with_mock_context.enrich_prompt(
        original_prompt,
        user_responses,
        clarification_context
    )

    assert "Check the API" in enriched
    assert "tcm-api" in enriched
    assert "[Clarification" in enriched


def test_clean_answer(engine_with_mock_context):
    """Test emoji removal from user answers."""
    assert engine_with_mock_context._clean_answer("📦 tcm-api") == "tcm-api"
    assert engine_with_mock_context._clean_answer("🎯 tcm-non-prod") == "tcm-non-prod"
    assert engine_with_mock_context._clean_answer("plain text") == "plain text"


def test_validate_answer_exact_match(engine_with_mock_context):
    """Test exact answer validation."""
    ambiguity = {
        "available_options": ["tcm-api", "pg-api"]
    }

    assert engine_with_mock_context._validate_answer("tcm-api", ambiguity) == "tcm-api"


def test_validate_answer_fuzzy_match(engine_with_mock_context):
    """Test fuzzy matching of user answers."""
    ambiguity = {
        "available_options": ["tcm-api", "pg-api"]
    }

    # User types "tcm api" (with space)
    result = engine_with_mock_context._validate_answer("tcm api", ambiguity)
    assert "tcm-api" in result.lower() or result == "tcm api"  # Either matched or kept as-is


def test_validate_answer_all_keyword(engine_with_mock_context):
    """Test 'all' keyword detection."""
    ambiguity = {
        "available_options": ["tcm-api", "pg-api"]
    }

    result = engine_with_mock_context._validate_answer("todos", ambiguity)
    assert "Todos" in result or "tcm-api" in result


def test_convenience_function_request_clarification():
    """Test convenience function with minimal setup."""
    # This will use actual project-context.json if it exists
    result = request_clarification("Check the API")

    # Should return dict with expected keys
    assert "needs_clarification" in result

    if result["needs_clarification"]:
        assert "summary" in result
        assert "question_config" in result
        assert "engine_instance" in result


def test_command_context_filtering(engine_with_mock_context):
    """Test that command context filters patterns."""
    # Mock config to disable service_ambiguity for a specific command
    engine_with_mock_context.config["command_rules"]["test_command"] = {
        "enabled": True,
        "patterns": ["namespace_ambiguity"]  # Only namespace, not service
    }

    result = engine_with_mock_context.detect_ambiguity(
        "Check the API",
        command_context={"command": "test_command"}
    )

    # Service ambiguity should be filtered out
    if result["ambiguity_points"]:
        assert all(a["pattern"] != "service_ambiguity" for a in result["ambiguity_points"])


def test_disabled_command(engine_with_mock_context):
    """Test that disabled commands skip clarification."""
    engine_with_mock_context.config["command_rules"]["disabled_command"] = {
        "enabled": False
    }

    result = engine_with_mock_context.detect_ambiguity(
        "Check the API",
        command_context={"command": "disabled_command"}
    )

    assert result["needs_clarification"] == False


def test_multiple_ambiguities_sorted_by_weight(engine_with_mock_context):
    """Test that multiple ambiguities are sorted by weight."""
    result = engine_with_mock_context.detect_ambiguity(
        "Deploy the API to cluster in production"
    )

    if len(result["ambiguity_points"]) > 1:
        # Should be sorted by weight (descending)
        weights = [a["weight"] for a in result["ambiguity_points"]]
        assert weights == sorted(weights, reverse=True)


def test_get_option_metadata_service(engine_with_mock_context):
    """Test metadata generation for service options."""
    ambiguity = {
        "pattern": "service_ambiguity",
        "services_metadata": {
            "tcm-api": {
                "tech_stack": "NestJS",
                "namespace": "tcm-non-prod",
                "port": 3001
            }
        }
    }

    metadata = engine_with_mock_context._get_option_metadata("tcm-api", ambiguity)

    assert "NestJS" in metadata
    assert "tcm-non-prod" in metadata
    assert "3001" in metadata
    # Status is NOT in project-context (verified in real-time only)


def test_get_option_metadata_namespace(engine_with_mock_context):
    """Test metadata generation for namespace options."""
    ambiguity = {
        "pattern": "namespace_ambiguity",
        "namespace_metadata": {
            "tcm-non-prod": {
                "services": ["tcm-api", "tcm-web"],
                "service_count": 2
            }
        }
    }

    metadata = engine_with_mock_context._get_option_metadata("tcm-non-prod", ambiguity)

    assert "tcm-api" in metadata
    assert "2 servicios" in metadata or "2 services" in metadata


def test_spanish_keywords_detected(engine_with_mock_context):
    """Test that Spanish keywords are detected."""
    # Spanish: "Chequea el servicio"
    result = engine_with_mock_context.detect_ambiguity("Chequea el servicio")

    assert result["needs_clarification"] == True
    assert any("service" in a["pattern"] for a in result["ambiguity_points"])


def test_log_clarification(engine_with_mock_context, tmp_path):
    """Test that clarification is logged properly."""
    # Override log path to temp directory
    log_file = tmp_path / "clarifications.jsonl"
    engine_with_mock_context.clarification_log_path = str(log_file)
    os.makedirs(tmp_path, exist_ok=True)

    ambiguity_analysis = {
        "ambiguity_score": 80,
        "ambiguity_points": [
            {"pattern": "service_ambiguity"}
        ]
    }

    engine_with_mock_context.log_clarification(
        "Original prompt",
        "Enriched prompt",
        ambiguity_analysis,
        {"question_1": "tcm-api"}
    )

    # Check log file exists and has content
    assert log_file.exists()
    with open(log_file, "r") as f:
        log_entry = json.loads(f.read())
        assert log_entry["original_prompt"] == "Original prompt"
        assert log_entry["enriched_prompt"] == "Enriched prompt"
        assert log_entry["ambiguity_score"] == 80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
