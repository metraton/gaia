---
name: security-tiers
description: Use when classifying any operation before executing it, or deciding whether user approval is required
metadata:
  user-invocable: false
  type: reference
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

## Hook Enforcement

The pre_tool_use hook is the primary security gate. With `Bash(*)` in the allow list, all commands reach the hook. Two modules, elimination logic:

1. **blocked_commands.py** -- pattern-matched irreversible commands, permanently denied (exit 2)
2. **mutative_verbs.py** -- CLI-agnostic verb detection, nonce-based approval flow

Everything that is not blocked and not mutative is safe by elimination. There is no separate safe-commands list.

Runtime is the single source of truth for nonce handling, grant scope, and
approval enforcement. This skill teaches classification and decision-making; it
does not replace the hook contract.

Conditional commands like `git branch` are safe for listing but T3 with mutative flags (`-D`, `-d`, `-m`). See `reference.md`.

### File Write Protection

The pre_tool_use hook also gates Edit and Write tools via `_is_protected()` in `adapters/claude_code.py`. This is a separate enforcement path from Bash command protection.

**Protected paths:**
- `.claude/hooks/` -- resolved via `Path.resolve().relative_to()` to catch symlinks
- `.claude/settings.json` and `.claude/settings.local.json` -- matched by filename within a `.claude/` path

**Why this matters:** `_is_protected()` fires regardless of `permissionMode`. An agent with `permissionMode: acceptEdits` can still be blocked from writing to hooks/ or settings files. In headless/cron mode where Claude Code native prompts cannot display, hooks remain the real security boundary.

**Permission model:**

| Path | Parent session | Subagents | Enforced by |
|------|:---:|:---:|---|
| Normal code (src/, etc.) | auto-accept | auto-accept | `permissionMode: acceptEdits` in agent frontmatter |
| `.claude/skills/`, `agents/`, `commands/` | auto-accept | CC native prompt | CC hardcoded `.claude/` protection |
| `.claude/hooks/` | T3 BLOCKED | T3 BLOCKED | Gaia `_is_protected()` hook |
| `settings.json`, `settings.local.json` | T3 BLOCKED | T3 BLOCKED | Gaia `_is_protected()` hook |

Note: `bypassPermissions` does not propagate to subagents (CC limitation). `acceptEdits` requires `permissionMode` in the agent frontmatter -- it does not flow through settings to subagents.

## T3 Workflow

For T3 operations, follow the state flow in `agent-protocol`: IN_PROGRESS -- REVIEW -- IN_PROGRESS -- COMPLETE (plan-first or hook-blocked with approval_id).

On-demand workflow skills (read from disk when needed):
- `.claude/skills/approval/SKILL.md` -- informed-consent plan quality and approval presentation
- `.claude/skills/execution/SKILL.md` -- post-approval execution discipline and verification
