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

## List Format (explicit "ver pendientes" only)

There is no automatic injection of this list anymore -- render it only in
response to the user explicitly asking to see pending approvals (`gaia
approvals pending` for the current session, `--all-sessions` when the user
asks across sessions):

```
Tienes N aprobaciones pendientes:

P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}
P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}

Di "ver P-XXXX" para detalles o "aprobar P-XXXX" para ejecutar.
```

When rendering an `--all-sessions` result, annotate rows whose `session_id`
differs from the current session with `[otra sesion]` so the user knows which
ones are not from the current in-loop flow.

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
"Reject" (or any non-"Approve" answer) does NOT activate the grant. Activation
is not the end of the turn -- selecting "Approve" both activates the grant
(single-use, 5-minute TTL, consumed at match) AND is the signal to
immediately re-dispatch the verbatim command. There is no intermediate step
where the orchestrator asks whether to proceed.

## Post-Approval Dispatch Template

The moment AskUserQuestion returns "Approve", dispatch a one-shot agent with
the approved command -- approving IS the execute order, so this dispatch is
automatic, not a separate decision. Pass the nonce: approvals are in-loop and
single-session, so the pending being approved always belongs to the current
session and the grant is always activated fresh in it.

### Dispatch prompt structure

```
Ejecuta este comando aprobado por el usuario. No requiere confirmacion adicional.
Nonce: {nonce}
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

### Dispatch on approval

Approvals are in-loop and single-session, so there is one dispatch shape, not
a same-session/cross-session split:

1. Build the dispatch prompt with nonce, command, and cwd (if available)
2. Dispatch the one-shot agent immediately -- approving already is the order
   to execute, this is not a step the orchestrator deliberates on
3. The hook finds the nonce, activates the grant via `activate_db_pending_by_prefix`, and allows the T3 operation through
4. If this dispatch dies before reaching the command, a fresh re-dispatch
   within the 5-minute grant TTL reuses the same activated grant. If the
   command reached and executed but failed, the grant was already consumed at
   match -- do not re-dispatch expecting the same grant to work; a new
   approval is required.

### Recovery scope guardrail

Recovery actions must only modify LOCAL state. The agent should never attempt:
- `git push --force` or `git push --force-with-lease` (remote-mutating)
- `terraform state rm` or `terraform import` (state-mutating beyond refresh)
- `kubectl delete` followed by re-create (destructive recovery)
- Any action that would require its own T3 approval

If the only path forward requires remote mutation, the agent reports the failure and lets the user decide.

## Complete Flow Example

### In-loop, single-session path (DB-backed approval)

```
Subagent attempts T3 command -> hook blocks it, mints P-8072af8

User: "ver P-8072af8"
  → orchestrator runs: gaia approvals show P-8072af8
  → cmd_show_v2 checks DB first, then filesystem; returns detail
  → orchestrator presents detail view

User: "aprobar P-8072af8"
  → orchestrator calls AskUserQuestion with all 5 fields visible
  → user selects "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
  → PostToolUse ElicitationResult hook activates a single-use, 5-minute-TTL
    grant via activate_db_pending_by_prefix
  → approving is the execute order: orchestrator immediately dispatches a
    one-shot agent with nonce + command, no separate confirmation
  → agent runs command; hook matches the grant (consuming it at match, before
    execution) and allows T3 through
  → agent returns COMPLETE
```

If the dispatched agent dies before reaching the command, a fresh re-dispatch
within the 5-minute window reuses the still-alive grant. If the command
executed and failed, the grant was already consumed -- report the failure and
request a new approval rather than retrying.

There is no SessionStart or per-turn surfacing step in this flow anymore: the
pending is raised and resolved within the same session, driven entirely by
the user directly asking ("ver P-XXXX", "aprobar P-XXXX").

### Filesystem-only path (legacy pending, no DB row)

This path applies when a `pending-{nonce}.json` exists in
`.claude/cache/approvals/` but the corresponding DB row was never created (e.g.
an approval generated before the DB-first migration).

`gaia approvals show P-XXXX` falls back to the filesystem automatically via
`cmd_show_v2` — the orchestrator flow is otherwise identical to the in-loop
path above. The difference is rejection: use `gaia approvals reject P-XXXX`
(not `revoke`, which targets DB rows). Bulk cleanup uses `gaia approvals
reject-all` and `gaia approvals clean`.

## Filesystem Pending File Location (legacy)

All pending files: `.claude/cache/approvals/pending-{nonce}.json`
Index file (per-session): `.claude/cache/approvals/pending-index-{session_id}.json`

Use glob `pending-*.json` to find all pending files. Skip files starting with `pending-index-`.
These paths are relevant only for the `reject`, `reject-all`, and `clean` subcommands.
DB-backed approvals have no filesystem counterpart.
