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

**Write tool creates parent directories implicitly.** Writing a file to a path whose parent does not yet exist creates the parent. Use this to avoid an explicit `mkdir` Bash step when creating new files under `.claude/` -- one Write tool call instead of mkdir (T3 Bash) + Write.

**Permission model:**

| Path | Parent session | Subagents | Enforced by |
|------|:---:|:---:|---|
| Normal code (src/, etc.) | auto-accept | auto-accept | `permissionMode: acceptEdits` in agent frontmatter |
| `.claude/skills/`, `agents/`, `commands/` | auto-accept | CC native prompt | CC hardcoded `.claude/` protection |
| `.claude/hooks/` | T3 BLOCKED | T3 BLOCKED | Gaia `_is_protected()` hook |
| `settings.json`, `settings.local.json` | T3 BLOCKED | T3 BLOCKED | Gaia `_is_protected()` hook |

Note: `bypassPermissions` does not propagate to subagents (CC limitation). `acceptEdits` requires `permissionMode` in the agent frontmatter -- it does not flow through settings to subagents.

## Mode runtime rules

Four rules govern how `mode` and `run_in_background` interact with the Gaia hooks. Classification rules (R1, R2) tell you what each layer covers; runtime rules (R3, R4) tell you how those layers behave across turn boundaries.

### R1 -- `acceptEdits` covers Edit/Write, not Bash mutativo

`mode: acceptEdits` in the Agent dispatch satisfies CC native for Edit and Write tools. It does NOT cover Bash mutativo (`rm`, `mv`, `cp`, `chmod`) even when the target lives under `.claude/`. Bundles that need both Edit/Write and mutative Bash on protected paths require either `mode: bypassPermissions` or per-command Gaia grants -- `acceptEdits` alone is insufficient.

### R2 -- Gaia bash_validator is orthogonal to `mode`

The `mutative_verbs.py` hook classifies Bash verbs as MUTATIVE and emits an `approval_id` regardless of which `mode` the dispatch carried. `bypassPermissions` covers the CC native side (Edit/Write/Bash without prompt) but does NOT disable the bash_validator. The two layers are independent and both must pass for a mutative Bash to execute. Design every bundle assuming the Gaia hook will classify and possibly block each mutative Bash, even under `bypassPermissions`.

### R3 -- `mode` does NOT survive a SendMessage resume

The `mode` parameter is per-dispatch of the Agent tool. If a subagent dispatched with `acceptEdits` or `bypassPermissions` emits APPROVAL_REQUEST mid-task, the SendMessage resume runs in `default` -- CC native re-blocks the next protected operation even after the Gaia grant has activated. For multi-step bundles on protected paths, either pack every step into the same turn the dispatch started, or accept that the orchestrator must re-dispatch fresh with the same mode. See `orchestrator-approval/SKILL.md` -> "Re-dispatch instead of resume".

### R4 -- `run_in_background` default is foreground

`run_in_background` is exposed by the Agent tool and is orthogonal to `mode`. **The default in interactive sessions is foreground and rarely needs to be set explicitly** -- almost every dispatch runs with `run_in_background=None` (foreground). Setting `run_in_background: false` explicitly is defensive, rarely necessary. The "background" case that actually matters is the SendMessage resume, which always runs in the background literal -- AskUserQuestion auto-denies and the original `mode` is gone (R3). The decision that shapes runtime behavior on protected-path bundles is dispatch-vs-resume, not foreground-vs-background.

### permissionMode comparison

| Modo | Qué hace | Cuándo usar | Cuándo NO usar |
|------|----------|-------------|----------------|
| `default` | Todas las operaciones requieren prompt nativo de CC | Operaciones destructivas irreversibles, read-only, o cuando explícitamente quieres prompt por operación | Dispatches headless o background -- CC no puede mostrar prompts, result: auto-deny |
| `acceptEdits` | Edit y Write pasan sin prompt nativo; Bash y herramientas destructivas siguen requiriendo aprobación | Edit/Write sobre `.claude/` o `gaia-ops-dev/` (briefs, plans, docs, skills, runtime code); Bash mutativo seguirá disparando grants file-scoped | Operaciones que requieren Bash mutativo sin supervisión; nunca como sustituto de `bypassPermissions` |
| `bypassPermissions` | Todos los permisos de CC skipeados -- Edit, Write, Bash, todo | Bash atómico single-command de housekeeping (mv dir, bulk cleanup) cuando el scope ya está aprobado conceptualmente por el usuario y hooks `PreToolUse` están hardened | Multi-file refactor -- bypass en background pre-aprueba el bundle entero, por lo que hooks `PreToolUse` no se re-invocan por operación; se pierde audit per-file |
| `plan` | El agente propone un plan y requiere aprobación explícita antes de ejecutar cualquier herramienta | Revisar plan antes de ejecutar sin side effects -- útil para validar goals ambiguos | Tareas operativas rutinarias donde la aprobación por cada herramienta crea fricción innecesaria |

For the goal->mode decision tree (Spanish), foreground/background examples, and notes on hooks under background, see `reference.md` -> "Mode decision tree" and "Foreground vs background detail".

For the dispatch-vs-resume operational rule, see `orchestrator-approval/SKILL.md` -> "Re-dispatch instead of resume".
