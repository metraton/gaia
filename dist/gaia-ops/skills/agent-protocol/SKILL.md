---
name: agent-protocol
description: Use when producing any agent response
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

This protocol governs REPORTING FORMAT, not tool access. All agents may use their declared tools during any phase.

## Response Contract

Every response MUST end with a single fenced `json:contract` block.

```json:contract
{
  "agent_status": {
    "plan_status": "<STATUS>",
    "agent_id": "<a + 5+ hex chars>",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": [],
    "commands_run": [],
    "key_outputs": [],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": null
}
```

**agent_status** -- `plan_status` (one of 5 states below), `agent_id` (generate once, reuse), `pending_steps` (`[]` when done), `next_action` (`"done"` or what's next).

**evidence_report** -- Use `[]` when not applicable, 1-3 items each. `key_outputs`: what changed. `verbatim_outputs`: literal output, truncate ~100 lines. `cross_layer_impacts`: adjacent surfaces. `open_gaps`: what remains unverified. `verification`: **required when COMPLETE** (see Verification Gate), `null` otherwise.

**consolidation_report** -- Required when `consolidation_required` or `multi_surface` is true. Otherwise `null`. Fields: `ownership_assessment`, `confirmed_findings`, `suspected_findings`, `conflicts`, `next_best_agent`. See `examples.md`.

**approval_request** -- Required when REVIEW. Fields: `operation`, `exact_content`, `scope`, `risk_level`, `rollback`, `verification`. On `[T3_BLOCKED]` with `approval_id`: set REVIEW, include `approval_id`, wait. See `examples.md`.

## Universal Execution Loop

Each increment: **INVESTIGATE** (read, search) -> **PLAN** (propose; REVIEW if T3) -> **EXECUTE** (write, run) -> **VERIFY** (confirm results) -> **COMPLETE** or loop back on failure. Decompose large tasks into 2-5 increments; each is one action paired with one verification. Every increment ends verified. Fix before moving on -- compounding failures is exponential.

## Verification Gate

An agent cannot set `plan_status: "COMPLETE"` without a `verification` object in `evidence_report`. When verification fails, loop back to EXECUTE -- do not complete.

```json
"verification": {
  "method": "test | dry-run | metric | self-review",
  "checks": ["what was checked"],
  "result": "pass | fail",
  "details": "concrete evidence"
}
```

Choose the method that fits your domain. Infrastructure: `dry-run` (terraform plan). Code: `test` (pytest, lint). Gaia skills: `self-review` (line count, frontmatter). Email: `metric` (count match). Git/file ops: `test` or `self-review`. When no automated check exists, `self-review` is the minimum: state what you checked and what you observed. For full examples see `examples.md`.

## State Machine

| Status | Meaning |
|--------|---------|
| `IN_PROGRESS` | Investigating, planning, or executing work |
| `REVIEW` | Presenting plan with evidence for approval |
| `COMPLETE` | Verified -- `verification.result` is `"pass"` |
| `BLOCKED` | Cannot proceed -- escalated |
| `NEEDS_INPUT` | Missing information from user |

### Transitions

```
IN_PROGRESS -> COMPLETE                  (requires verification evidence)
IN_PROGRESS -> REVIEW -> IN_PROGRESS -> COMPLETE
IN_PROGRESS -> BLOCKED | NEEDS_INPUT     (any point)
IN_PROGRESS -> IN_PROGRESS               (retry or verify-fail loop, max 2)
```

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (max 2) | `IN_PROGRESS` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options | `NEEDS_INPUT` |
| Contract repair | Reissue `json:contract`, skip re-investigation (max 2) | `IN_PROGRESS` |
