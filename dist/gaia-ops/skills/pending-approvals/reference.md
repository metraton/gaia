# Pending Approvals — Reference

Read on-demand when processing approval requests.

## Pending JSON Schema

File: `.claude/cache/approvals/pending-{nonce}.json`

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
Minimum 4 characters. If multiple nonces share the same prefix, ask user to be more specific.

## Summary Format (SessionStart injection)

```
Tienes N aprobaciones pendientes:

P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}
P-{nonce[0:8]}  {command}  [{danger_verb}]  hace {age}

Di "ver P-XXXX" para detalles o "aprobar P-XXXX" para ejecutar.
```

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

After AskUserQuestion returns "Approve", check whether the pending file belongs to the current session. Both dispatch paths use the same smart prompt structure -- the only difference is whether the nonce is included.

### Dispatch prompt structure

The dispatch prompt tells the agent three things: what to run, where to run it, and how to handle failure. This replaces fire-and-forget dispatch, which reports failure without attempting recovery.

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

The `cwd` field may be present in the pending JSON. When present, include it in the dispatch as `Directorio:`. When absent, omit the line.

### Same-session dispatch

When `pending.session_id == CLAUDE_SESSION_ID` -- pass the nonce:

1. Build the dispatch prompt with nonce, command, and cwd (if available)
2. Dispatch the one-shot agent
3. The hook finds the nonce, activates the grant, and allows the T3 operation through

### Cross-session dispatch

When `pending.session_id != CLAUDE_SESSION_ID` -- the nonce is stale:

1. The PostToolUse hook will have already activated the grant under the current session
2. Build the dispatch prompt with command and cwd (if available), no nonce
3. Dispatch the one-shot agent
4. The hook finds the pre-activated grant (by command signature) and allows the T3 operation through

### Recovery scope guardrail

Recovery actions must only modify LOCAL state. The agent should never attempt:
- `git push --force` or `git push --force-with-lease` (remote-mutating)
- `terraform state rm` or `terraform import` (state-mutating beyond refresh)
- `kubectl delete` followed by re-create (destructive recovery)
- Any action that would require its own T3 approval

If the only path forward requires remote mutation, the agent reports the failure and lets the user decide.

## Complete Flow Example

### Same-session path

```
SessionStart
  → scans .claude/cache/approvals/pending-*.json
  → injects summary into additionalContext

User sees:
  "Tienes 1 aprobación pendiente:
   P-8072af8  kubectl apply -f manifest.yaml  [apply]  hace 2 min"

User: "ver P-8072af8"
  → orchestrator reads pending-8072af8044f0da0571c348041ad2cef6.json
  → presents detail view

User: "aprobar P-8072af8"
  → orchestrator calls AskUserQuestion with all 5 fields visible
  → user selects "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
  → PostToolUse hook extracts nonce from label, activates grant for nonce 8072af8044f0da0571c348041ad2cef6
  → orchestrator dispatches one-shot agent with nonce + command
  → agent runs command; hook validates nonce and allows T3 through
  → agent returns COMPLETE; pending file deleted
```

### Cross-session path

```
SessionStart (new session)
  → scans .claude/cache/approvals/pending-*.json
  → pending-8072af8044f0da0571c348041ad2cef6.json has session_id = "prior-session"
  → scanner annotates entry with [session anterior]
  → injects summary into additionalContext

User sees:
  "Tienes 1 aprobación pendiente:
   P-8072af8  kubectl apply -f manifest.yaml  [apply]  hace 5 min  [session anterior]"

User: "aprobar P-8072af8"
  → orchestrator calls AskUserQuestion with all 5 fields visible
  → user selects "Approve -- kubectl apply -f manifest.yaml [P-8072af80]"
  → orchestrator detects pending.session_id != CLAUDE_SESSION_ID
  → calls activate_cross_session_pending(pending_data) — grant created in current session
  → deletes old pending file
  → dispatches one-shot agent with command only (no nonce)
  → agent runs command; hook finds pre-activated grant and allows T3 through
  → agent returns COMPLETE
```

## Pending File Location

All pending files: `.claude/cache/approvals/pending-{nonce}.json`
Index file (per-session): `.claude/cache/approvals/pending-index-{session_id}.json`

Use glob `pending-*.json` to find all pending files. Skip files starting with `pending-index-`.
