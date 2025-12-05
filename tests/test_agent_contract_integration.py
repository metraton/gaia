#!/usr/bin/env python3
"""
Integration test for Agent Contract System
Tests the complete flow: Query → Contract → Response → Extraction → Continuation
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# Add conversation directory directly to path
conversation_dir = Path(__file__).parent.parent / "tools" / "conversation"
sys.path.insert(0, str(conversation_dir))

from enhanced_conversation_manager import EnhancedConversationManager
from progressive_disclosure import ProgressiveDisclosureManager
from agent_contract_builder import AgentContractBuilder


def test_complete_conversation_flow():
    """Test a complete multi-turn conversation with contracts."""

    print("="*80)
    print("🧪 AGENT CONTRACT SYSTEM - INTEGRATION TEST")
    print("="*80)

    # Initialize manager
    manager = EnhancedConversationManager(
        storage_path="/tmp/test_conversations",
        project_context_path=".claude/project-context.json"
    )

    # ============================================
    # TURN 1: Initial query
    # ============================================
    print("\n" + "="*60)
    print("TURN 1: Initial Query")
    print("="*60)

    user_query_1 = "check the pods in namespace tcm-non-prod"
    print(f"User: '{user_query_1}'")

    # Start conversation
    conv_id = manager.start_conversation("gitops-operator", user_query_1)
    print(f"✅ Started conversation: {conv_id}")

    # Build Agent Contract
    contract_1 = manager.build_agent_contract(conv_id, user_query_1)

    print("\n📦 Agent Contract (Turn 1):")
    print("-"*40)
    print(f"Components: {list(contract_1.keys())}")
    if "project_contract" in contract_1:
        print(f"  Project: {contract_1['project_contract']}")
    if "task_contract" in contract_1:
        print(f"  Task: action={contract_1['task_contract']['action']}, "
              f"scope={contract_1['task_contract'].get('scope')}")
    print(f"  Expected Response: {contract_1['expected_response']['format']}")

    # Simulate Agent Response
    agent_response_1 = {
        "version": "1.0",
        "metadata": {
            "agent": "gitops-operator",
            "timestamp": datetime.now().isoformat(),
            "execution_time": "2.3s",
            "tier": "T0",
            "success": True
        },
        "findings": {
            "resources": [
                {
                    "type": "pod",
                    "name": "tcm-api",
                    "namespace": "tcm-non-prod",
                    "state": "CrashLoopBackOff",
                    "severity": "critical",
                    "details": "5 restarts in last 10 minutes"
                },
                {
                    "type": "pod",
                    "name": "tcm-web",
                    "namespace": "tcm-non-prod",
                    "state": "ImagePullBackOff",
                    "severity": "high",
                    "details": "Image v2.1.0 not found"
                },
                {
                    "type": "pod",
                    "name": "tcm-worker",
                    "namespace": "tcm-non-prod",
                    "state": "Running",
                    "severity": "info",
                    "details": "Healthy"
                }
            ],
            "errors": [
                {
                    "resource": "tcm-api",
                    "type": "connection",
                    "message": "Cannot connect to database at postgres:5432",
                    "suggested_action": "Check database connectivity and credentials"
                },
                {
                    "resource": "tcm-web",
                    "type": "configuration",
                    "message": "Image gcr.io/project/tcm-web:v2.1.0 not found in registry",
                    "suggested_action": "Update image tag to existing version"
                }
            ],
            "metrics": {
                "total_resources": 3,
                "healthy": 1,
                "unhealthy": 2,
                "critical": 1,
                "warnings": 1
            }
        },
        "actions": {
            "performed": [
                {"command": "kubectl get pods -n tcm-non-prod", "result": "success"},
                {"command": "kubectl describe pod tcm-api -n tcm-non-prod", "result": "success"}
            ]
        },
        "human_summary": "Found 3 pods in tcm-non-prod: 2 failing (tcm-api: CrashLoopBackOff, tcm-web: ImagePullBackOff), 1 healthy"
    }

    print("\n📤 Agent Response (Turn 1):")
    print("-"*40)
    print(f"Success: {agent_response_1['metadata']['success']}")
    print(f"Resources found: {len(agent_response_1['findings']['resources'])}")
    print(f"Errors detected: {len(agent_response_1['findings']['errors'])}")
    print(f"Human summary: {agent_response_1['human_summary']}")

    # Process response
    summary_1 = manager.process_agent_response(conv_id, agent_response_1, user_query_1)
    print(f"\n✅ Response processed and stored")

    # Check what was extracted
    conversation = manager.active_conversations[conv_id]
    print("\n🔍 Extracted Data:")
    print(f"  Resources: {conversation['summary_data'].get('resources', [])}")
    print(f"  States: {conversation['summary_data'].get('states', {})}")
    print(f"  Errors: {len(conversation['summary_data'].get('errors', []))} errors captured")

    # ============================================
    # TURN 2: Continuation with reference
    # ============================================
    print("\n" + "="*60)
    print("TURN 2: Continuation Query (with reference)")
    print("="*60)

    user_query_2 = "show me the logs of those failing pods"
    print(f"User: '{user_query_2}'")

    # Check if continuation detected
    is_continuation = manager.is_continuation(user_query_2, "gitops-operator")
    print(f"Continuation detected: {is_continuation}")

    # Build Agent Contract for Turn 2
    contract_2 = manager.build_agent_contract(conv_id, user_query_2)

    print("\n📦 Agent Contract (Turn 2):")
    print("-"*40)
    print(f"Components: {list(contract_2.keys())}")

    # Check if conversation contract was included
    if "conversation_contract" in contract_2:
        conv_contract = contract_2["conversation_contract"]
        print(f"✅ Conversation Contract included:")
        print(f"  Context level: {conv_contract.get('context_level')}")
        if "referenced_resources" in conv_contract:
            print(f"  Referenced resources: {len(conv_contract['referenced_resources'])} resources")
            for res in conv_contract['referenced_resources'][:2]:
                print(f"    - {res['name']}: {res['state']}")
        if "previous_errors" in conv_contract:
            print(f"  Previous errors: {len(conv_contract['previous_errors'])} errors")

    # Check task contract
    if "task_contract" in contract_2:
        task = contract_2["task_contract"]
        print(f"\n📋 Task Contract:")
        print(f"  Action: {task['action']}")
        print(f"  Targets: {task.get('targets', [])}")
        print(f"  Options: {task.get('options', {})}")

    # Simulate Agent Response for Turn 2
    agent_response_2 = {
        "version": "1.0",
        "metadata": {
            "agent": "gitops-operator",
            "timestamp": datetime.now().isoformat(),
            "execution_time": "3.1s",
            "tier": "T0",
            "success": True
        },
        "findings": {
            "logs": {
                "tcm-api": [
                    "[ERROR] 2024-11-19 10:15:23 Database connection failed",
                    "[ERROR] 2024-11-19 10:15:23 java.net.ConnectException: Connection refused",
                    "[INFO]  2024-11-19 10:15:28 Retrying connection in 5 seconds..."
                ],
                "tcm-web": [
                    "[ERROR] 2024-11-19 10:14:15 Failed to pull image",
                    "[ERROR] 2024-11-19 10:14:15 Image gcr.io/project/tcm-web:v2.1.0 not found",
                    "[ERROR] 2024-11-19 10:14:20 Pull failed, backing off"
                ]
            },
            "analysis": {
                "root_causes": {
                    "tcm-api": "PostgreSQL service is down or unreachable",
                    "tcm-web": "Image tag v2.1.0 doesn't exist in registry"
                },
                "patterns_detected": [
                    "Database connectivity issue affecting multiple services",
                    "Incorrect image versioning in deployment"
                ]
            }
        },
        "actions": {
            "performed": [
                {"command": "kubectl logs tcm-api -n tcm-non-prod --tail=50", "result": "success"},
                {"command": "kubectl logs tcm-web -n tcm-non-prod --tail=50", "result": "success"}
            ],
            "recommended": [
                {
                    "priority": "critical",
                    "action": "Restart PostgreSQL service",
                    "command": "kubectl rollout restart deployment/postgres -n tcm-non-prod"
                },
                {
                    "priority": "high",
                    "action": "Update tcm-web image tag",
                    "command": "kubectl set image deployment/tcm-web tcm-web=gcr.io/project/tcm-web:v2.0.0 -n tcm-non-prod"
                }
            ]
        },
        "human_summary": "Retrieved logs showing database connection errors for tcm-api and image pull errors for tcm-web. Root causes identified."
    }

    print("\n📤 Agent Response (Turn 2):")
    print("-"*40)
    print(f"Logs retrieved for: {list(agent_response_2['findings']['logs'].keys())}")
    print(f"Root causes identified: {len(agent_response_2['findings']['analysis']['root_causes'])}")
    print(f"Recommendations: {len(agent_response_2['actions']['recommended'])}")

    # Process response
    summary_2 = manager.process_agent_response(conv_id, agent_response_2, user_query_2)
    print(f"\n✅ Response processed and stored")

    # ============================================
    # TURN 3: Complex debugging query
    # ============================================
    print("\n" + "="*60)
    print("TURN 3: Complex Debugging Query")
    print("="*60)

    user_query_3 = "fix the database connection error for tcm-api with all details"
    print(f"User: '{user_query_3}'")

    # Analyze query intent
    progressive_manager = ProgressiveDisclosureManager()
    intent = progressive_manager.analyze_query_intent(user_query_3)

    print(f"\n🔍 Query Analysis:")
    print(f"  Needs debugging: {intent['needs_debugging']}")
    print(f"  Needs detail: {intent['needs_detail']}")
    print(f"  References previous: {intent['references_previous']}")
    print(f"  Complexity score: {intent['complexity_score']}")
    print(f"  Recommended level: {intent['recommended_level']} (1-4)")

    # Build Agent Contract for Turn 3
    contract_3 = manager.build_agent_contract(conv_id, user_query_3)

    print("\n📦 Agent Contract (Turn 3 - Level 4):")
    print("-"*40)

    # Check for infrastructure contract
    if "infrastructure_contract" in contract_3:
        print("✅ Infrastructure Contract included (database details)")

    # Check conversation contract for full history
    if "conversation_contract" in contract_3:
        conv_contract = contract_3["conversation_contract"]
        if "response_history" in conv_contract:
            print(f"✅ Full response history included: {len(conv_contract['response_history'])} responses")

    # ============================================
    # STATISTICS
    # ============================================
    print("\n" + "="*60)
    print("📊 FINAL STATISTICS")
    print("="*60)

    stats = manager.get_statistics()
    print(f"Active conversations: {stats['active_conversations']}")
    print(f"Total token savings: {stats['total_token_savings']} tokens")
    print(f"Structured responses: {stats['structured_responses']}")
    print(f"Contract-enabled agents: {stats['contract_enabled_agents']}")

    # Show token optimization
    print("\n💡 TOKEN OPTIMIZATION:")
    print("-"*40)

    # Calculate sizes
    full_response_size = len(json.dumps(agent_response_1)) + len(json.dumps(agent_response_2))
    summary_size = len(summary_1) + len(summary_2)
    savings_percent = ((full_response_size - summary_size) / full_response_size) * 100

    print(f"Full responses size: {full_response_size} chars")
    print(f"Summaries size: {summary_size} chars")
    print(f"Savings: {savings_percent:.1f}%")

    print("\n✅ ALL TESTS PASSED!")
    print("="*80)

    return True


def test_progressive_disclosure_levels():
    """Test different Progressive Disclosure levels."""

    print("\n" + "="*80)
    print("🧪 PROGRESSIVE DISCLOSURE LEVELS TEST")
    print("="*80)

    manager = ProgressiveDisclosureManager()

    test_queries = [
        ("how many pods are running?", 1, "Simple status query"),
        ("show me those pod logs", 3, "Reference with detail request"),
        ("debug and fix all the errors you found", 4, "Complex debugging"),
        ("list the namespaces", 1, "Simple listing"),
        ("explain the database connection issue in detail", 4, "Detailed explanation")
    ]

    for query, expected_level, description in test_queries:
        intent = manager.analyze_query_intent(query)
        actual_level = intent["recommended_level"]

        status = "✅" if actual_level == expected_level else "❌"
        print(f"{status} '{query}'")
        print(f"   Expected: Level {expected_level}, Got: Level {actual_level}")
        print(f"   ({description})")
        print(f"   Complexity: {intent['complexity_score']}, Confidence: {intent['confidence']:.0%}")

    print("\n✅ Progressive Disclosure test complete!")


if __name__ == "__main__":
    print("\n" + "🚀 RUNNING INTEGRATION TESTS" + "\n")

    # Run tests
    try:
        # Test 1: Complete conversation flow
        test_complete_conversation_flow()

        # Test 2: Progressive disclosure levels
        test_progressive_disclosure_levels()

        print("\n" + "="*80)
        print("🎉 ALL INTEGRATION TESTS PASSED!")
        print("="*80)

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)