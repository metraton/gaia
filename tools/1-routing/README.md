# 1-Routing Module

**Purpose:** Agent semantic routing and intent classification

## Overview

This module determines which Gaia-Ops agent is best suited for a given user request using semantic matching and intent classification.

## Core Classes

### `IntentClassifier`
Analyzes user prompts and classifies them into intents:
- `infrastructure_creation` - Creating infrastructure
- `infrastructure_destruction` - Destroying resources
- `gitops_deployment` - GitOps deployments
- `debugging` - Problem diagnosis
- `general_inquiry` - Generic questions

**Methods:**
```python
classifier = IntentClassifier()
intent, confidence = classifier.classify("create a new gke cluster")
# Returns: ("infrastructure_creation", 0.95)
```

### `CapabilityValidator`
Validates agent capabilities against requested operations.

**Methods:**
```python
validator = CapabilityValidator()
agent = validator.find_fallback_agent("invalid_operation")
```

### `AgentRouter`
Main router that combines intent classification and capability validation.

**Methods:**
```python
router = AgentRouter()
agent, confidence = router.route(user_prompt)
# Returns: ("terraform-architect", 0.92)
```

## Usage

```python
from tools.routing import AgentRouter

router = AgentRouter()
agent, confidence = router.route("deploy frontend to production")
print(f"Route to: {agent} (confidence: {confidence})")
```

## Performance

- **Intent classification:** ~50ms per request
- **Agent routing:** ~100ms per request
- **Accuracy target:** 92.7% on standard test set

## Files

- `agent_router.py` - Main router implementation and related classes
- `README.md` - This file

## See Also

- `tools/__init__.py` - Package re-exports
- `tests/tools/test_agent_router.py` - Test suite
