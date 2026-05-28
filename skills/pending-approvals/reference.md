# Pending Approvals — Reference

Read on-demand when processing approval requests.

## Pending JSON Schema (filesystem legacy)

File: `.claude/cache/approvals/pending-{nonce}.json`

This schema applies to **filesystem-queue pendings** only (those operated on by
`reject`, `reject-all`, and `clean`). DB-backed approvals live in `gaia.db`
`approvals` + `approval_events` tables and are accessed exclusively through the
`gaia approvals` CLI. You do not read DB rows directly; use the CLI verbs.

```json
{
  "nonce": "8072af8044f0da0571c348041ad2cef6",
  "session_id": "abc123",
  "command": "kubectl apply -f manifest.yaml",
  "danger_verb": "apply",
  "danger_category": "MUTATIVE",
  "scope_type": "semantic_signature",
  "scope_signature": {
    "base_cmd": "kubectl",
    "cli_family": "k8s",
    "verb": "apply",
    "semantic_tokens": ["kubectl", "apply", "manifest.yaml"],
    "normalized_flags": ["-f"]
  },
  "timestamp": 1775843292.4328,
  "ttl_minutes": 5,
  "context": {
    "scope": "k8s cluster — dev namespace",
    "rollback": "kubectl delete -f manifest.yaml",
    "risk": "MEDIUM"
  }
}
```

The `context` field is optional. When absent, derive scope/rollback/risk from `scope_signature` and `danger_category`.

## Nonce Prefix Matching

User references "P-8072af8" → match against nonces starting with "8072af8".
Minimum 4 characters. If multiple nonces share the same prefix, ask the user to be more specific.

The hook function `activate_db_pending_by_prefix` (in
`hooks/modules/security/approval_grants.py`) implements this matching for DB
rows. The legacy filesystem path has its own prefix scan over the
`pending-*.json` glob. Both are activated by the same `gaia approvals show
P-XXXX` call — `cmd_show_v2` checks DB first, filesystem second.

## Summary Format (SessionStart injection)

`build_pending_approvals_block` in `hooks/modules/session/session_manifest.py`
scans both stores and formats each row identically:

```
Tienes N aprobaciones pendientes:

P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}
P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}

Di "ver P-XXXX" para detalles o "aprobar P-XXXX" para ejecutar.
```

Cross-session rows (where `session_id` differs from the current session) are
annotated with `[session anterior]`.

## Detail View Format

```
P-{nonce[0:8]} — Detalle

COMANDO:    {command}
OPERACION:  {danger_verb} en {base_cmd}
CATEGORIA:  {danger_category}
SCOPE:      {scope}
ROLLBACK:   {rollback}
CREADO:     {timestamp as readable datetime}
```

## AskUserQuestion Template

```python
AskUserQuestion(
    question=(
        "APPROVAL REQUIRED\n\n"
        f"OPERACION: {danger_verb} on {base_cmd}\n"
        f"COMANDO:   {command}\n"       # verbatim, never paraphrased
        f"SCOPE:     {scope}\n"
        f"RIESGO:    {danger_category}\n"
        f"ROLLBACK:  {rollback}"
    ),
    options=[f"Approve -- {danger_verb} {base_cmd} {target} [P-{nonce[:8]}]", "Reject"]
    # Option label MUST name the specific action, e.g.:
    # "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
    # NEVER: "Approve", "Approve -- proceed", "Approve -- aplicar cambios"
)
```

The PostToolUse hook checks `answer.lower().startswith("approve")` to activate the grant.
"Reject" (or any non-"Approve" answer) does NOT activate the grant.

## Post-Approval Dispatch Template

After AskUserQuestion returns "Approve", dispatch a one-shot agent with the
approved command. The prompt structure is the same regardless of store; the
only difference is whether to include the nonce (same-session) or omit it
(cross-session, where the grant was pre-activated by the ElicitationResult hook).

### Dispatch prompt structure

```
Ejecuta este comando aprobado por el usuario. No requiere confirmacion adicional.
{Nonce: {nonce}  -- only for same-session dispatch}
Comando: {command}
Directorio: {cwd}

PREFLIGHT: Before executing, verify preconditions still hold.
- For git push: fetch and check if the local branch is ahead of remote.
- For kubectl/helm apply: confirm the target resource exists and is not mid-rollout.
- For terraform apply: run a quick plan to confirm no unexpected drift.
- General: if the command depends on state that may have changed, check that state first.
If a precondition fails, report what changed and do NOT execute.

RECOVERY: If the command fails with a recoverable error, attempt ONE standard local recovery, then retry.
- git push (non-fast-forward): pull --rebase, then retry push.
- terraform apply (state conflict): refresh state, then retry apply.
- kubectl apply (conflict): re-fetch the resource, re-apply.
- General: if the error message suggests a local fix (rebase, refresh, retry), do that fix ONCE.
Do NOT attempt remote-mutating recovery (force push, remote delete, taint, import).
Do NOT retry more than once -- if recovery + retry fails, report the error.
```

The `cwd` field may be present in the pending JSON or DB row. When present, include it in the dispatch as `Directorio:`. When absent, omit the line.

### Dispatch `mode` for post-approval execution

The Gaia grant activates on the blocked command signature — that covers the Gaia hook, but CC native is a separate gate. Pick `mode` based on the command target:

| Approved command targets... | mode | session | Why |
|-----------------------------|------|---------|-----|
| Normal paths (src/, infra/, /tmp/) | `default` | foreground | CC native does not protect these; no mode needed |
| `.claude/` writes only (Edit/Write on skills, agents, briefs) | `acceptEdits` | foreground | CC native prompts on `.claude/` writes; `acceptEdits` satisfies it |
| Bash mutativo sobre `.claude/` (rm, mv, mkdir) | `bypassPermissions` | foreground | CC native intercepts `.claude/` destructive ops regardless of verb; bypass satisfies it |
| Bundle: Bash on `.claude/` + Edits on `.claude/` | `bypassPermissions` | foreground | The bundle needs one mode that covers both layers; pack all steps in one dispatch turn |

The dispatch is single-turn and cannot split: if the bundle emits APPROVAL_REQUEST mid-execution, the orchestrator must re-dispatch fresh with the same mode, not SendMessage resume — mode does not survive resume.

### Same-session dispatch

When the approval originates in the current session — pass the nonce:

1. Build the dispatch prompt with nonce, command, and cwd (if available)
2. Dispatch the one-shot agent
3. The hook finds the nonce, activates the grant via `activate_db_pending_by_prefix`, and allows the T3 operation through

### Cross-session dispatch

When the approval originates in a prior session — the original nonce is stale:

1. The PostToolUse ElicitationResult hook has already activated the grant under the current session (keyed by command signature, not nonce)
2. Build the dispatch prompt with command and cwd (if available), no nonce
3. Dispatch the one-shot agent
4. The hook finds the pre-activated grant by signature and allows the T3 operation through

### Recovery scope guardrail

Recovery actions must only modify LOCAL state. The agent should never attempt:
- `git push --force` or `git push --force-with-lease` (remote-mutating)
- `terraform state rm` or `terraform import` (state-mutating beyond refresh)
- `kubectl delete` followed by re-create (destructive recovery)
- Any action that would require its own T3 approval

If the only path forward requires remote mutation, the agent reports the failure and lets the user decide.

## Complete Flow Example

### Same-session path (DB-backed approval)

```
SessionStart
  → build_pending_approvals_block scans DB (store.list_pending) + filesystem
  → injects summary into additionalContext

User sees:
  "Tienes 1 aprobación pendiente:
   P-8072af8  kubectl apply -f manifest.yaml  [apply]  hace 2 min"

User: "ver P-8072af8"
  → orchestrator runs: gaia approvals show P-8072af8
  → cmd_show_v2 checks DB first, then filesystem; returns detail
  → orchestrator presents detail view

User: "aprobar P-8072af8"
  → orchestrator calls AskUserQuestion with all 5 fields visible
  → user selects "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
  → PostToolUse ElicitationResult hook activates grant via activate_db_pending_by_prefix
  → orchestrator dispatches one-shot agent with nonce + command
  → agent runs command; hook validates nonce and allows T3 through
  → agent returns COMPLETE
```

### Cross-session path (DB-backed approval, prior session)

```
SessionStart (new session)
  → build_pending_approvals_block scans DB (store.list_pending) + filesystem
  → DB row has session_id from prior session
  → scanner annotates entry with [session anterior]
  → injects summary into additionalContext

User sees:
  "Tienes 1 aprobación pendiente:
   P-8072af8  kubectl apply -f manifest.yaml  [apply]  hace 5 min  [session anterior]"

User: "aprobar P-8072af8"
  → orchestrator runs: gaia approvals show P-8072af8
  → cmd_show_v2 returns detail (DB row)
  → orchestrator calls AskUserQuestion with all 5 fields visible
  → user selects "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
  → PostToolUse ElicitationResult hook activates grant in current session (by command signature)
  → orchestrator dispatches one-shot agent with command only (no nonce)
  → agent runs command; hook finds pre-activated grant and allows T3 through
  → agent returns COMPLETE
```

### Filesystem-only path (legacy pending, no DB row)

This path applies when a `pending-{nonce}.json` exists in
`.claude/cache/approvals/` but the corresponding DB row was never created (e.g.
an approval generated before the DB-first migration).

`gaia approvals show P-XXXX` falls back to the filesystem automatically via
`cmd_show_v2` — the orchestrator flow is identical to the same-session path
above. The difference is rejection: use `gaia approvals reject P-XXXX` (not
`revoke`, which targets DB rows). Bulk cleanup uses `gaia approvals reject-all`
and `gaia approvals clean`.

## Filesystem Pending File Location (legacy)

All pending files: `.claude/cache/approvals/pending-{nonce}.json`
Index file (per-session): `.claude/cache/approvals/pending-index-{session_id}.json`

Use glob `pending-*.json` to find all pending files. Skip files starting with `pending-index-`.
These paths are relevant only for the `reject`, `reject-all`, and `clean` subcommands.
DB-backed approvals have no filesystem counterpart.
