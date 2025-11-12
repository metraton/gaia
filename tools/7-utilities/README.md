# 7-Utilities Module

**Purpose:** Helper tools and audit logging

## Overview

This module provides utilities for agent invocation patterns and comprehensive audit logging of tool executions.

## Core Classes

### `AgentInvokerHelper`
Helper patterns for invoking agents correctly.

**Methods:**
```python
from tools.utilities import AgentInvokerHelper

helper = AgentInvokerHelper()

# Build agent invocation command
cmd = helper.build_invocation("terraform-architect", {
    "action": "plan",
    "target": "staging",
    "dry_run": True
})
# Returns: {"agent": "terraform-architect", "params": {...}}

# Get recommended context for agent
context = helper.get_agent_context("gitops-operator")
```

### `TaskAuditLogger`
Comprehensive audit logging for all tool executions.

**Methods:**
```python
from tools.utilities import TaskAuditLogger

logger = TaskAuditLogger()

# Log tool execution start
logger.log_tool_start(
    tool_name="kubectl",
    command="apply",
    args={"file": "deployment.yaml"},
    tier="T2"
)

# Log completion
logger.log_tool_complete(
    tool_name="kubectl",
    exit_code=0,
    duration_ms=1234,
    output_lines=42
)

# Log error
logger.log_tool_error(
    tool_name="terraform",
    error="Invalid configuration",
    error_code=-1
)
```

## Audit Logging Details

Each log entry includes:

```json
{
  "timestamp": "2025-11-12T00:00:00Z",
  "tool_name": "kubectl",
  "command": "apply",
  "tier": "T2",
  "user": "system",
  "context": {
    "namespace": "prod",
    "cluster": "gke-prod-1"
  },
  "result": {
    "exit_code": 0,
    "duration_ms": 1234,
    "output_lines": 42
  }
}
```

## Integration with Hooks

**post_tool_use.py** uses TaskAuditLogger:

```python
# In post_tool_use.py hook
from tools.utilities import TaskAuditLogger

logger = TaskAuditLogger()
logger.log_tool_complete(
    tool_name=tool_info.name,
    exit_code=result.exit_code,
    duration_ms=result.duration,
    output_lines=len(result.output.split('\n'))
)
```

## Files

- `agent_invoker_helper.py` - Agent invocation patterns
- `task_wrapper.py` - TaskAuditLogger implementation
- `README.md` - This file

## Audit Trail

All executions are logged to:
- `.claude/logs/audit.jsonl` - JSON Lines format
- Searchable by timestamp, tier, tool_name, user

**Query example:**
```bash
# Find all T3 operations
grep '"tier":"T3"' .claude/logs/audit.jsonl

# Find errors in terraform
grep '"tool_name":"terraform"' .claude/logs/audit.jsonl | grep '"exit_code":-'

# Timeline of operations
grep -o '"timestamp":"[^"]*"' .claude/logs/audit.jsonl | sort
```

## See Also

- `hooks/post_tool_use.py` - Post-execution hook
- `tools/__init__.py` - Package re-exports
- `.claude/logs/` - Audit log directory
