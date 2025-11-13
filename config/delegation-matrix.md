# Binary Delegation Matrix

**Version:** 1.0.0
**Purpose:** Deterministic decision of when to delegate vs execute locally

## Decision Rules (Priority Order)

| # | Condition | Decision | Confidence | Reason |
|---|-----------|----------|------------|---------|
| 1 | `has_task_id AND task_agent != None` | DELEGATE | 1.0 | Task metadata routing |
| 2 | `security_tier == "T3"` | DELEGATE | 1.0 | T3 requires agent + approval |
| 3 | `file_count >= 3` | DELEGATE | 0.9 | Multi-file threshold |
| 4 | `file_span_multiple_dirs == True` | DELEGATE | 0.9 | Multiple directories |
| 5 | `has_infrastructure_keywords AND requires_context` | DELEGATE | 0.85 | Infrastructure + context |
| 6 | `has_chained_commands == True` | DELEGATE | 0.8 | Chained commands safety |
| 7 | `tier == "T0" AND file_count <= 1 AND !has_approval_keywords` | LOCAL | 0.9 | Atomic T0 operation |
| 8 | `tier == "T1" AND file_count <= 1 AND !requires_credentials` | LOCAL | 0.85 | Simple T1 validation |
| 9 | DEFAULT (fallback) | DELEGATE | 0.5 | Safety default |

## Binary Conditions Extracted

```python
@dataclass
class DelegationConditions:
    file_count: int                    # Count of files to modify
    file_span_multiple_dirs: bool      # Files in multiple directories
    has_chained_commands: bool         # Uses && or pipes
    has_infrastructure_keywords: bool  # terraform/kubectl/etc
    has_approval_keywords: bool        # apply/deploy/push/delete
    security_tier: str                 # T0, T1, T2, T3
    requires_context: bool             # Needs project-context.json
    requires_credentials: bool         # Needs GCP/AWS/K8s creds
    has_task_id: bool                  # Mentions task ID
    task_agent: str                    # Agent from task metadata
```

## Examples

```python
# Example 1: Simple git status (LOCAL)
conditions = DelegationConditions(
    file_count=0,
    file_span_multiple_dirs=False,
    has_infrastructure_keywords=False,
    security_tier="T0"
)
→ Decision: LOCAL (Rule 7: Atomic T0 operation)

# Example 2: Terraform apply (DELEGATE)
conditions = DelegationConditions(
    has_infrastructure_keywords=True,
    has_approval_keywords=True,
    security_tier="T3"
)
→ Decision: DELEGATE (Rule 2: T3 requires agent)

# Example 3: Multi-file edit (DELEGATE)
conditions = DelegationConditions(
    file_count=5,
    file_span_multiple_dirs=True,
    security_tier="T1"
)
→ Decision: DELEGATE (Rule 3: Multi-file threshold)
```

## Integration with Orchestrator

```python
from agent_router import should_delegate

# At orchestrator entry point
result = should_delegate(user_request, context={
    "file_count": 3,
    "multiple_directories": True
})

if result["delegate"]:
    agent = result["suggested_agent"]
    # Proceed with agent invocation
else:
    # Execute locally (orchestrator)
    pass
```

## Confidence Levels

- **1.0**: Absolute confidence (deterministic rules)
- **0.9**: High confidence (clear patterns)
- **0.85**: Medium-high confidence (strong indicators)
- **0.8**: Medium confidence (good heuristics)
- **0.5**: Low confidence (fallback/safety)

## Security Considerations

1. **T3 operations ALWAYS delegate** - No exceptions
2. **Chained commands prefer delegation** - Safety over convenience
3. **Default to delegation when uncertain** - Better safe than sorry
4. **Infrastructure operations require context** - Never execute without proper context

## Testing the Matrix

```bash
# Run standalone test
python3 .claude/tools/0-guards/delegation_matrix.py

# Test integration with router
python3 -c "from tools.1-routing.agent_router import should_delegate; \
    print(should_delegate('terraform apply', {'file_count': 1}))"
```

## Monitoring

Delegation decisions are logged to:
- `.claude/logs/delegation.jsonl` (if logging enabled)
- Included in metrics collection for KPI tracking

## Future Improvements

1. **Machine Learning Enhancement**: Train on actual delegation outcomes
2. **Custom Rules**: Allow project-specific delegation rules
3. **Context Awareness**: Consider recent operations for better decisions
4. **Performance Metrics**: Track decision accuracy and adjust thresholds