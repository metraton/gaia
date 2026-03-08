---
name: output-format
description: Use when reporting findings, errors, or results to the user — defines report structure, status icons, and finding classification
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

When a response is grounded in investigation, validation, review, or diagnostics, include the protocol-mandated `EVIDENCE_REPORT` block before `AGENT_STATUS`.

Use this compact style:

```html
<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- ...
FILES_CHECKED:
- ...
COMMANDS_RUN:
- `command` -> result
KEY_OUTPUTS:
- ...
CROSS_LAYER_IMPACTS:
- ...
OPEN_GAPS:
- none
<!-- /EVIDENCE_REPORT -->
```

Formatting rules:
- Prefer 1-3 bullets per field.
- Keep `KEY_OUTPUTS` summarized; do not dump full logs unless the user asked.
- If a command was run, show it exactly in `COMMANDS_RUN`.
- If no command was run, say `- not run`.

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

## Verification Commands

Every output that changes or validates state MUST include commands the user can run independently to verify the result.

```
## Verify yourself
- `[read-only command]` → expected: [what you should see]
```

Include after: Realization Packages, Diagnostic Reports, any analysis the user may need to evidence in a PR or ticket.
