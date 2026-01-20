---
name: output-format
description: Global output contract and reporting standards for all agents
auto_load: true
---

# Output Format Standards

## Report Structure

All agent outputs follow a consistent structure:

```
[STATUS ICON] [PHASE] COMPLETE

[Summary Section]
- Key finding 1
- Key finding 2

[Details Section - if needed]
Specific information relevant to the task

[Next Steps]
- Recommended action 1
- Recommended action 2
```

## Status Icons

| Icon | Meaning |
|------|---------|
| `✅` | Success / All healthy |
| `❌` | Error / Critical issue |
| `⚠️` | Warning / Deviation |
| `✓` | Step completed |
| `✗` | Step failed |

## Escalation Protocol

When an issue is outside your scope, use this format:

```
⚠️ DELEGATION REQUIRED

Issue: [Brief description]
Recommended Agent: [agent-name]
Reason: [Why this agent should handle it]

Suggested Action:
[Specific command or task for the other agent]
```

## Error Reporting

When reporting errors:

```
❌ ERROR: [Brief title]

Details:
- Resource: [affected resource]
- Type: [error type]
- Message: [error message]

Suggested Fix:
[Actionable recommendation]
```

## Finding Classification

Report findings by severity tier:

- **Tier 1 (CRITICAL):** Blocks operation - report immediately
- **Tier 2 (DEVIATION):** Works but non-standard - mention in report
- **Tier 3 (IMPROVEMENT):** Minor optimization - omit from report
- **Tier 4 (PATTERN):** Detected pattern to replicate - auto-apply
