# Security Tiers -- Reference

Read on-demand by infrastructure agents. Not injected automatically.

## Cloud-Specific Classification Examples

### T0 -- Read-Only

- `kubectl get pods`, `kubectl get svc`, `kubectl describe node`
- `terraform show`, `terraform output`
- `gcloud describe`, `gcloud sql instances describe`, `gcloud container clusters list`
- `helm status`, `helm list`
- `flux get kustomizations`, `flux get sources`

### T1 -- Validation

- `terraform validate`
- `helm lint`
- `tflint`
- `kustomize build`

### T2 -- Simulation

- `terraform plan` / `terragrunt plan`
- `kubectl diff -f manifest.yaml`
- `helm upgrade --dry-run`
- `kubectl apply --dry-run=server`

### Conditional (T0 or T3 depending on flags)

- `git branch` -- T0 for listing (no args or `--list`), T3 with `-D`, `-d`, `-m`, `-M`, `--delete`, `--move`

### T3 -- Realization

- `terraform apply` / `terragrunt apply`
- `kubectl apply -f manifest.yaml`
- `helm upgrade` (without `--dry-run`)
- `flux reconcile` (write operations)
- `git commit`, `git push` (any branch)

## Mode decision tree -- elegir mode por goal

Mapeo directo de goal a mode recomendado. Si el goal no coincide con ningún caso, el orchestrator pregunta al usuario antes del dispatch.

```
Goal -> Mode recomendado:

- Read-only / investigación                           -> default (o acceptEdits si escribirá evidence)
- Edit/Write archivos declarativos                    -> acceptEdits
  (brief, plan, docs, skills, project-context, evidence)
- Edit/Write código runtime                           -> acceptEdits
  (hooks/**, bin/**, tests/**)                          (aceptar fricción: Bash mutativo seguirá pidiendo grant file-scoped)
- Bash atómico housekeeping sobre .claude/            -> bypassPermissions
  (mv dir, rmdir, mkdir, bulk CLI)                      IFF atómico + hooks PreToolUse hardened + scope ya aprobado conceptualmente
- Bash en multi-file refactor                         -> acceptEdits
  (mv/rm/cp de varios archivos)                         NO bypass: pierde audit per-file porque background pre-aprueba el bundle
- Destructivo irreversible                            -> default + approval explícito por paso, foreground obligatorio
  (rm -rf, git push --force, terraform destroy)
```

Regla del borde: si el goal no enumera archivos o patrón concreto, el orchestrator pregunta al usuario antes del dispatch. No adivinar un mode cuando el scope es vago.

Cross-reference: para el checklist pre-dispatch con ejemplos concretos, ver `orchestrator-approval/reference.md` -> "Dispatch mode checklist".

## Foreground vs background detail

R4 (en SKILL.md) cubre la regla; aquí el detalle operativo.

- **Foreground (default)**: el agente puede recibir prompts nativos de CC y emitir `approval_request` mid-task. Cualquier T3 que requiera consentimiento del usuario funciona end-to-end.
- **Background**: no puede mostrar prompts ni esperar input. Requiere un mode que pre-satisfaga los permisos necesarios (`acceptEdits` para Edit/Write, `bypassPermissions` para Bash mutativo).

Regla de selección: la decisión que realmente moldea el runtime es **dispatch-vs-resume** (ver `orchestrator-approval/SKILL.md` -> "Re-dispatch instead of resume"), porque los SendMessage resumes corren en background literal independientemente de cómo se haya despachado el original.

**Nota sobre hooks y background:** Los hooks `PreToolUse` son ortogonales al mode -- se invocan independientemente. `bypassPermissions` en background pre-aprueba el bundle de permisos de CC, lo que en la práctica significa que operaciones encadenadas no re-disparan el prompt nativo por operación. Los hooks de Gaia (`_is_protected()`, `mutative_verbs.py`) siguen activos en ambos casos. Ver R2 en SKILL.md.

**Double defense for `.claude/` paths.** For `rm`, `mv`, and other destructive commands targeting paths under `.claude/`, both layers fire independently: CC native prompts the user for any write in `.claude/` regardless of Gaia classification, AND Gaia T3 approval flows for the mutative verb itself. Neither layer bypasses the other. A subagent dispatched with `mode: bypassPermissions` satisfies CC native but still faces the Gaia hook; shell wrappers like `bash -c '...'` may trigger `_detect_indirect_execution` but CC native can still intercept writes inside `.claude/`.
