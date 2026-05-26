---
name: request-approval
description: DEPRECATED — renamed to subagent-request-approval; load that skill instead
deprecated: true
metadata:
  user-invocable: false
---

# DEPRECATED — request-approval

This skill has been renamed to `subagent-request-approval`.

Load `Skill('subagent-request-approval')` instead of this one.

## Why this stub exists

The stub is preserved so that any agent frontmatter still referencing the old
name does not cause a missing-skill error during skill injection. The runtime
will load this stub, which immediately redirects to the canonical skill.

When updating agent frontmatter, replace `request-approval` with
`subagent-request-approval` in the `skills:` list.
