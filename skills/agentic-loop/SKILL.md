---
name: agentic-loop
description: Use when working on a multi-step implementation task that requires iterative build, test, and fix cycles before presenting results
metadata:
  user-invocable: false
  type: discipline
---

# Agentic Loop

```
EVERY INCREMENT ENDS WITH A GREEN TEST.
No increment is complete until its tests pass.
No task is complete until the user has seen the branch.
```

## Mental Model

Inspired by Karpathy's AutoResearch: run experiments in a loop,
keep only what beats the baseline, discard the rest. Applied to
implementation: break into increments, test each one, commit only
passing work, present the branch with evidence.

Large tasks fail when you implement everything and test at the end.
Small increments with immediate testing catch failures when they
are cheap to fix.

## Rules

1. **Break first** — decompose into 2-5 increments before writing code
2. **One at a time** — implement one increment, then test, then next
3. **Test every increment** — run the right tests for what changed
4. **Fix before moving on** — red test blocks the next increment
5. **Branch early** — create a descriptive branch before the first increment
6. **Commit per increment** — each passing increment gets its own commit
7. **Present at the end** — user sees: branch, changes, test evidence

## The Loop

```
1. Decompose task → list of increments
   ↓
2. Create branch: feat/<task-description>
   ↓
3. For each increment:
   ├─ Implement
   ├─ Run tests
   ├─ Pass? → Commit → next increment
   └─ Fail? → Fix → re-test (max 3 retries)
       └─ Still failing? → Stop, report BLOCKED with evidence
   ↓
4. All increments done → present to user
```

## Presenting Results

When all increments pass:

> Branch `feat/<name>` is ready.
> - N files changed across M increments
> - All tests pass: [command + summary]
> - Key changes: [2-3 bullets]

## Handling Feedback

| User says | Action |
|-----------|--------|
| "Looks good" | Merge if needed, COMPLETE |
| "Change X" | New increment on same branch, same loop |
| "Different approach" | New branch, restart loop |
| "Not what I meant" | NEEDS_INPUT to clarify before new work |

## Traps

| Thinking... | Reality... |
|---|---|
| "I'll implement everything, then test" | You'll spend more time debugging than you saved |
| "This is trivial, skip testing" | Trivial increments have trivial tests — run them |
| "Tests failed but I know the fix, keep going" | Fix now. Compounding failures is exponential |
| "One big commit is cleaner" | One per increment gives better bisect and rollback |
| "I'll present code, user can test later" | The branch with passing tests IS the deliverable |

## Anti-Patterns

- Implementing all increments before running any tests
- Moving to next increment with a failing test
- Presenting work without test evidence
- Creating branch after the work is done (no rollback point)
- More than 5 increments (overhead exceeds value — regroup)
