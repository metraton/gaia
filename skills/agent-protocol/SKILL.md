---
name: agent-protocol
description: Use when starting any agent task, reporting status, or handling errors -- defines json:contract format, state machine, and response contract
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

## Injected Context

The runtime hook injects structured project knowledge before your first tool call.
The injected prompt includes natural-language sections with JSON data:

- **Your Project Knowledge** -- confirmed project data (paths, configs, resource names).
  Use these as direct targets in your first tool calls instead of searching.
- **Your Investigation Brief** -- your role, primary surface, adjacent surfaces,
  consolidation requirements.
- **Your Write Permissions** -- which project-context sections you may update.
- **Rules** -- universal and agent-specific operational rules.
- **Surface Routing** -- active surfaces and dispatch mode.

Trust project knowledge values as search anchors. If a resource is not where the
data says, that is drift -- report it in `open_gaps` and, if you have the
`context-updater` skill, emit a `CONTEXT_UPDATE` block (see that skill for format
and permissions).

**Runtime-enforced** (not optional guidance):
- `write_permissions.writable_sections` -- the hook rejects writes to sections not listed here
- `investigation_brief` -- your role, ownership, required checks, and consolidation requirements
- `surface_routing` -- active surfaces and dispatch mode

For investigation methodology (how to search, pattern hierarchy, live state),
follow the `investigation` skill. For human-facing output structure, follow
`output-format`. This skill is the authority for the `json:contract` block only.

## Mandatory Response Contract

Every response MUST end with a single fenced `json:contract` block.

### Schema

```json:contract
{
  "agent_status": {
    "plan_status": "<STATUS>",
    "agent_id": "<hex id>",
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
    "open_gaps": []
  },
  "consolidation_report": null
}
```

**`agent_status`** (always required):
- `plan_status` -- one of the valid states below
- `agent_id` -- format: `a` + 5 or more hex characters (e.g. `a1f2c3d4`). Generate once per task, reuse across responses.
- `pending_steps` -- remaining work (empty array `[]` when done)
- `next_action` -- `"done"` or a description of what happens next

**`evidence_report`** (required for all states except `APPROVED_EXECUTING`):
All 7 fields are validated by runtime. Use `[]` when a field does not apply.
- `patterns_checked` -- existing repo patterns you compared against
- `files_checked` -- files or paths you inspected
- `commands_run` -- exact commands with terse results (`"cmd -> result"`)
- `key_outputs` -- 1-2 sentence evidence summaries
- `verbatim_outputs` -- literal command output backing your conclusions; truncate at ~100 lines with `[truncated]`
- `cross_layer_impacts` -- adjacent surfaces or contracts affected (required for multi-surface tasks)
- `open_gaps` -- what remains unverified; never imply certainty when uncertainty exists

Keep each field to 1-3 items unless the task genuinely needs more.

**`consolidation_report`** (required when ANY of these is true, otherwise `null`):
- `investigation_brief.consolidation_required` is true
- `investigation_brief.cross_check_required` is true
- `surface_routing.multi_surface` is true

Fields:
- `ownership_assessment` -- one of: `owned_here`, `cross_surface_dependency`, `not_my_surface`
- `confirmed_findings` -- evidence-backed facts only
- `suspected_findings` -- hypotheses belong here, not in confirmed
- `conflicts` -- disagreements with prior agent findings
- `open_gaps` -- consistent with evidence_report.open_gaps (may overlap, must not contradict)
- `next_best_agent` -- name of agent to continue, or `null`

For status-specific examples, read `examples.md` in this skill directory.

## State Machine

| Status | Meaning |
|--------|---------|
| `INVESTIGATING` | Gathering evidence |
| `PLANNING` | Building execution plan |
| `PENDING_APPROVAL` | T3 plan ready, awaiting user approval |
| `APPROVED_EXECUTING` | Running approved T3 actions |
| `FIXING` | Retrying after failure (max 2 cycles) |
| `COMPLETE` | Task finished |
| `BLOCKED` | Cannot proceed -- escalated |
| `NEEDS_INPUT` | Missing information from user |

### Transitions

```
INVESTIGATING -> PLANNING -> PENDING_APPROVAL -> APPROVED_EXECUTING -> COMPLETE  (T3)
INVESTIGATING -> COMPLETE                                                        (T0/T1/T2)
APPROVED_EXECUTING -> FIXING -> APPROVED_EXECUTING  (retry, max 2)
FIXING -> BLOCKED                                   (after 2 cycles)
INVESTIGATING -> BLOCKED | NEEDS_INPUT
PLANNING -> NEEDS_INPUT
PENDING_APPROVAL -> PLANNING                        (user requests modifications)
```

## T3 and Git (On-Demand)

If your identity restricts you to T0/T1/T2, investigation leads directly to `COMPLETE`.

For T3 operations, read on-demand workflow skills when needed:
- `.claude/skills/approval/SKILL.md` -- plan quality and approval presentation
- `.claude/skills/execution/SKILL.md` -- post-approval execution and verification
- `.claude/skills/git-conventions/SKILL.md` -- commit and PR conventions

## Self-Review Gate

Before emitting `COMPLETE`:
1. Does your output answer the original request?
2. Are all requested items addressed?
3. Do paths, names, and commands match what you found?
4. Is the `json:contract` block present and structurally correct?

Fix silently before emitting COMPLETE.

## Contract Repair

If runtime resumes you with repair instructions:
- Reissue a complete response with the required `json:contract` block
- Do not rerun the full investigation unless evidence truly requires it
- Preserve the real task status -- repair is not a license to fabricate evidence
- Retries are capped at 2; after that, the orchestrator escalates

## Agent Handoff

When receiving prior agent findings: consume confirmed facts directly -- no
re-investigation. Investigate only gaps or contradictions. Emit your own
independent `json:contract` block.

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (max 2 cycles) | `FIXING` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
