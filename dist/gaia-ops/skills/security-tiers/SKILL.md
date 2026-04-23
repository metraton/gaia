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

### Dispatch mode rule

Para Edit/Write sobre `.claude/skills/**`, `.claude/agents/**`, `.claude/commands/**` desde subagente, el orchestrator debe pasar `mode: acceptEdits` en la dispatch del Agent tool. Sin ese mode, CC native intercepta con prompt (foreground) o auto-deny (background). El mode se pasa en la dispatch de cada invocación del Agent tool y no se hereda del orchestrator al subagente.

<!-- T8-anchor: insert permissionMode comparison table after this line -->

### permissionMode comparison

| Modo | Qué hace | Cuándo usar | Cuándo NO usar |
|------|----------|-------------|----------------|
| `default` | Todas las operaciones requieren prompt nativo de CC | Operaciones destructivas irreversibles, read-only, o cuando explícitamente quieres prompt por operación | Dispatches headless o background -- CC no puede mostrar prompts, result: auto-deny |
| `acceptEdits` | Edit y Write pasan sin prompt nativo; Bash y herramientas destructivas siguen requiriendo aprobación | Edit/Write sobre `.claude/` o `gaia-ops-dev/` (briefs, plans, docs, skills, runtime code); Bash mutativo seguirá disparando grants file-scoped | Operaciones que requieren Bash mutativo sin supervisión; nunca como sustituto de `bypassPermissions` |
| `bypassPermissions` | Todos los permisos de CC skipeados -- Edit, Write, Bash, todo | Bash atómico single-command de housekeeping (mv dir, bulk cleanup) cuando el scope ya está aprobado conceptualmente por el usuario y hooks `PreToolUse` están hardened | Multi-file refactor -- bypass en background pre-aprueba el bundle entero, por lo que hooks `PreToolUse` no se re-invocan por operación; se pierde audit per-file |
| `plan` | El agente propone un plan y requiere aprobación explícita antes de ejecutar cualquier herramienta | Revisar plan antes de ejecutar sin side effects -- útil para validar goals ambiguos | Tareas operativas rutinarias donde la aprobación por cada herramienta crea fricción innecesaria |

> **Nota de precedencia:** `mode` en la dispatch del Agent tool aplica solo a esa invocación -- no se hereda del orchestrator al subagente, no se hereda entre dispatches distintas, y **no sobrevive a un SendMessage resume**. Si el subagente emite APPROVAL_REQUEST mid-task y el orchestrator resume vía SendMessage, el resume corre en `default` -- CC native vuelve a interceptar writes en `.claude/` aunque la dispatch original fuera `bypassPermissions`. Para tareas multi-step en paths protegidos, o se empaquetan todos los steps en un solo turno de la dispatch original, o el orchestrator re-dispatcha fresco con el mismo mode tras aprobar. Ver **Dispatch mode rule** arriba. `bypassPermissions` satisface CC native pero no bypassa Gaia `_is_protected()` ni el flujo nonce de `mutative_verbs.py`.

### Decision tree -- elegir mode por goal

Mapeo directo de goal a mode recomendado. Si el goal no coincide con ningún caso, el orchestrator pregunta al usuario antes del dispatch.

```
Goal → Mode recomendado:

- Read-only / investigación                           → default (o acceptEdits si escribirá evidence)
- Edit/Write archivos declarativos                    → acceptEdits
  (brief, plan, docs, skills, project-context, evidence)
- Edit/Write código runtime                           → acceptEdits
  (hooks/**, bin/**, tests/**)                          (aceptar fricción: Bash mutativo seguirá pidiendo grant file-scoped)
- Bash atómico housekeeping sobre .claude/            → bypassPermissions
  (mv dir, rmdir, mkdir, bulk CLI)                      IFF atómico + hooks PreToolUse hardened + scope ya aprobado conceptualmente
- Bash en multi-file refactor                         → acceptEdits
  (mv/rm/cp de varios archivos)                         NO bypass: pierde audit per-file porque background pre-aprueba el bundle
- Destructivo irreversible                            → default + approval explícito por paso, foreground obligatorio
  (rm -rf, git push --force, terraform destroy)
```

Regla del borde: si el goal no enumera archivos o patrón concreto, el orchestrator pregunta al usuario antes del dispatch. No adivinar un mode cuando el scope es vago.

Cross-reference: para el checklist pre-dispatch con ejemplos concretos, ver `skills/orchestrator-approval/SKILL.md` → "Dispatch mode decision".

### Foreground vs background

La elección foreground/background es ortogonal al mode, pero la combinación importa:

- **Foreground**: el agente puede recibir prompts nativos de CC y emitir `approval_request` mid-task. Cualquier T3 que requiera consentimiento del usuario funciona end-to-end.
- **Background**: no puede mostrar prompts ni esperar input. Requiere un mode que pre-satisfaga los permisos necesarios (`acceptEdits` para Edit/Write, `bypassPermissions` para Bash mutativo).

Regla de selección: si el agente puede descubrir algo inesperado mid-task y necesita emitir `approval_request` (ej: housekeeping que encuentra archivos no previstos), usa foreground. Si el scope está completamente definido y los permisos pre-satisfechos, background es viable.

**Nota sobre hooks y background:** Los hooks `PreToolUse` son ortogonales al mode -- se invocan independientemente. Pero `bypassPermissions` en background pre-aprueba el bundle de permisos de CC, lo que en la práctica significa que operaciones encadenadas no re-disparan el prompt nativo por operación. Los hooks de Gaia (`_is_protected()`, `mutative_verbs.py`) siguen activos.

**bypassPermissions + Gaia hook: comportamiento no determinístico.** En la práctica, el Gaia hook (`mutative_verbs.py`) no siempre dispara cuando `bypassPermissions` está activo en background. Causa observada: pre-approval del bundle por CC en background puede suprimir la cadena de invocación del hook en algunos pasos. No asumir que el hook disparará consistentemente bajo `bypassPermissions` + background -- diseñar el bundle asumiendo que podría no haber segunda validación por parte del hook.

**Double defense for `.claude/` paths.** For `rm`, `mv`, and other destructive commands targeting paths under `.claude/`, both layers fire independently: CC native prompts the user for any write in `.claude/` regardless of Gaia classification, AND Gaia T3 approval flows for the mutative verb itself. Neither layer bypasses the other. A subagent dispatched with `mode: bypassPermissions` satisfies CC native but still faces the Gaia hook; shell wrappers like `bash -c '...'` may trigger `_detect_indirect_execution` but CC native can still intercept writes inside `.claude/`.

## T3 Workflow

For T3 operations, follow the state flow in `agent-protocol`: IN_PROGRESS -- REVIEW -- IN_PROGRESS -- COMPLETE (plan-first or hook-blocked with approval_id).

On-demand workflow skills (read from disk when needed):
- `.claude/skills/request-approval/SKILL.md` -- informed-consent plan quality, approval-request presentation, and the dispatch mode + foreground/background combination table
- `.claude/skills/orchestrator-approval/SKILL.md` -- orchestrator checklist for when to pass `mode: acceptEdits` and when to restrict dispatches to foreground
- `.claude/skills/execution/SKILL.md` -- post-approval execution discipline and verification
