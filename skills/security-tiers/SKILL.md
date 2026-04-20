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
- `.claude/hooks/` -- resolved via `Path.resolve().relative_to()` to catch symlinks (exception: `.md` files are exempt since documentation does not execute code; see `_is_protected()` in `hooks/adapters/claude_code.py`)
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

### Dispatch mode rule

Para Edit/Write sobre `.claude/skills/**`, `.claude/agents/**`, `.claude/commands/**` desde subagente, el orchestrator debe pasar `mode: acceptEdits` en la dispatch del Agent tool. Sin ese mode, CC native intercepta con prompt (foreground) o auto-deny (background). El mode se pasa en la dispatch de cada invocación del Agent tool y no se hereda del orchestrator al subagente.

<!-- T8-anchor: insert permissionMode comparison table after this line -->

### permissionMode comparison

| Modo | Qué hace | uso recomendado | Cuándo NO usar |
|------|----------|-----------------|----------------|
| `default` | Todas las operaciones requieren prompt nativo de CC | Sesiones interactivas normales, trabajo exploratorio donde el usuario quiere control granular | Dispatches headless o background -- CC no puede mostrar prompts, result: auto-deny |
| `acceptEdits` | Edit y Write pasan sin prompt nativo; Bash y herramientas destructivas siguen requiriendo aprobación | Dispatches que editan `.claude/skills/**`, `.claude/agents/**`, `.claude/commands/**`, briefs, plans, evidence | Operaciones que requieren Bash mutativo sin supervisión; nunca como sustituto de `bypassPermissions` |
| `bypassPermissions` | Todos los permisos de CC skipeados -- Edit, Write, Bash, todo | Solo uso interno del CLI (`gaia-doctor`, scripts de instalación), test pipelines controlados | **Nunca** en dispatches normales de agentes; bypassa CC native pero no bypassa Gaia hooks |
| `plan` | El agente propone un plan y requiere aprobación explícita antes de ejecutar cualquier herramienta | Cuando la propuesta del agente debe revisarse antes de actuar -- alto riesgo, cambios estructurales | Tareas operativas rutinarias donde la aprobación por cada herramienta crea fricción innecesaria |

> **Nota de precedencia:** `mode` en la dispatch del Agent tool aplica solo a esa invocación -- no se hereda del orchestrator al subagente ni de una dispatch a otra. Ver **Dispatch mode rule** arriba. `bypassPermissions` satisface CC native pero no bypassa Gaia `_is_protected()` ni el flujo nonce de `mutative_verbs.py`.

**Double defense for `.claude/` paths.** For `rm`, `mv`, and other destructive commands targeting paths under `.claude/`, both layers fire independently: CC native prompts the user for any write in `.claude/` regardless of Gaia classification, AND Gaia T3 approval flows for the mutative verb itself. Neither layer bypasses the other. A subagent dispatched with `mode: bypassPermissions` satisfies CC native but still faces the Gaia hook; shell wrappers like `bash -c '...'` may trigger `_detect_indirect_execution` but CC native can still intercept writes inside `.claude/`.

## T3 Workflow

For T3 operations, follow the state flow in `agent-protocol`: IN_PROGRESS -- REVIEW -- IN_PROGRESS -- COMPLETE (plan-first or hook-blocked with approval_id).

On-demand workflow skills (read from disk when needed):
- `.claude/skills/request-approval/SKILL.md` -- informed-consent plan quality, approval-request presentation, and the dispatch mode + foreground/background combination table
- `.claude/skills/orchestrator-approval/SKILL.md` -- orchestrator checklist for when to pass `mode: acceptEdits` and when to restrict dispatches to foreground
- `.claude/skills/execution/SKILL.md` -- post-approval execution discipline and verification
