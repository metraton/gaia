---
name: orchestrator-approval
description: DEPRECATED — renamed to orchestrator-present-approval; load that skill instead
deprecated: true
metadata:
  user-invocable: false
---

# DEPRECATED — orchestrator-approval

This skill has been renamed to `orchestrator-present-approval`.

Load `Skill('orchestrator-present-approval')` instead of this one.

## Why this stub exists

The stub is preserved so that any agent frontmatter still referencing the old
name does not cause a missing-skill error during skill injection. The runtime
will load this stub, which immediately redirects to the canonical skill.

When updating agent frontmatter, replace `orchestrator-approval` with
`orchestrator-present-approval` in the `skills:` list.
