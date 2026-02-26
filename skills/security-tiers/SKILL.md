---
name: security-tiers
description: Use when classifying any operation before executing it, or deciding whether user approval is required
user-invocable: false
---

# Security Tiers

## Classification Principle

Before executing any command, classify it by asking:

1. **Does it modify live state?** (create, update, delete, apply, push) -- **T3**
2. **Does it simulate changes?** (plan, diff, dry-run) -- **T2**
3. **Does it validate locally?** (validate, lint, fmt, check) -- **T1**
4. **Is it read-only?** (get, list, describe, show, logs) -- **T0**

## Tier Definitions

| Tier | Name | Side Effects | Approval |
|------|------|-------------|----------|
| **T0** | Read-Only | None | No |
| **T1** | Validation | None (local only) | No |
| **T2** | Simulation | None (dry-run) | No |
| **T3** | Realization | **Modifies state** | **Yes** |

## Examples (anchors, not exhaustive)

Classify using your own tools. Common verb patterns:

- **T0**: read, get, list, describe, show, logs, status
- **T1**: validate, lint, fmt, check, build (local)
- **T2**: plan, diff, dry-run
- **T3**: apply, create, delete, commit, push, deploy

For cloud-specific command examples (kubectl, terraform, gcloud, helm, flux), see `reference.md` in this skill directory.

## T3 Workflow

For T3 operations, follow the state flow in `agent-protocol`: PLANNING -- PENDING_APPROVAL -- APPROVED_EXECUTING -- COMPLETE.

On-demand workflow skills (read from disk when needed):
- `.claude/skills/approval/SKILL.md` -- plan format and presentation
- `.claude/skills/execution/SKILL.md` -- post-approval execution protocol
