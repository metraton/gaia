---
name: output-format
description: Global output contract and reporting standards for all agents
user-invocable: false
---

# Output Format Standards

## Report Structure

```
[STATUS ICON] [PHASE] COMPLETE

[Summary Section]
- Key finding 1
- Key finding 2

[Details Section - if needed]

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

## Error Reporting

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

**CRITICAL** — blocks the operation. Report immediately, stop execution.

**DEVIATION** — works but non-standard. Always mention in report.

**IMPROVEMENT** — minor optimization. Omit unless directly relevant to the task.

**PATTERN** — detected reusable pattern. Document for consistency, don't interrupt flow.
