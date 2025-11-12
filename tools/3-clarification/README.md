# 3-Clarification Module

**Purpose:** Phase 0 ambiguity detection and resolution

## Overview

This module detects ambiguous user prompts and generates targeted clarification questions before routing to agents. It's the first phase of the orchestration workflow.

## Core Concepts

### Phase 0 - Clarification
Detects and resolves ambiguous requests:
- Service ambiguity: "check the API" → Multiple APIs available
- Namespace ambiguity: "deploy to prod" → Multiple prod namespaces
- Environment ambiguity: "the cluster" → Multiple clusters exist

## Core Functions

### `clarify(user_prompt, max_iterations=1)`
Simple one-liner clarification function.

```python
from tools.clarification import clarify

enriched = clarify("Check the API")
# If ambiguous: Asks user to select
# If specific: Returns immediately
# Performance: ~200ms before user interaction
```

### `execute_workflow(user_prompt)`
Complete clarification workflow with all steps.

```python
from tools.clarification import execute_workflow

result = execute_workflow("Deploy to production")
# Returns: {
#   "enriched_prompt": "Deploy to production [service: tcm-api]",
#   "clarification_occurred": True,
#   "user_selections": {"service": "tcm-api"}
# }
```

### `request_clarification(prompt, project_context)`
Detect and ask clarification questions.

### `process_clarification(original_prompt, user_responses)`
Enrich prompt with user's clarification selections.

## Core Classes

### `ClarificationEngine`
Complex clarification detection and question generation.

**Methods:**
```python
engine = ClarificationEngine()
is_ambiguous = engine.detect_ambiguity(prompt)
questions = engine.generate_questions(prompt, options)
```

## Ambiguity Patterns

The module detects:

| Pattern | Example | Detection |
|---------|---------|-----------|
| ServiceAmbiguity | "check the API" | Multiple services matched |
| NamespaceAmbiguity | "prod namespace" | Multiple namespaces exist |
| EnvironmentAmbiguity | "production" | Multiple environments mapped |
| ClusterAmbiguity | "the cluster" | Multiple clusters available |

## Configuration

**Ambiguity threshold:** 30 (configurable in `.claude/config/clarification_rules.json`)

If ambiguity score ≥ 30, user is asked for clarification.

## Usage Examples

```python
# Simple clarification
from tools.clarification import clarify
enriched = clarify("Check the service")

# Complex workflow
from tools.clarification import execute_workflow, ClarificationEngine
workflow_result = execute_workflow("Deploy to the cluster")

# Low-level detection
from tools.clarification import detect_all_ambiguities
ambiguities = detect_all_ambiguities("Check API in prod")
```

## Command Line

```bash
# Test clarification engine
python3 -c "from tools.clarification import clarify; print(clarify('Check the API'))"
```

## Files

- `engine.py` - Core ClarificationEngine class (complex)
- `generic_engine.py` - Simple clarify() function (recommended)
- `patterns.py` - Ambiguity pattern definitions
- `workflow.py` - Orchestration workflow
- `user_interaction.py` - UI prompts and questions
- `README.md` - This file

## Performance

- Ambiguity detection: ~100ms
- Question generation: ~50ms
- Total (before user): ~150ms

## See Also

- `.claude/config/clarification_rules.json` - Threshold configuration
- `.claude/config/orchestration-workflow.md` - Phase 0 protocol
- `tests/integration/test_phase_0_regression.py` - Regression tests
- `tests/tools/test_clarify_engine.py` - Unit tests
