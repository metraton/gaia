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

After AskUserQuestion returns "Approve", check whether the pending file belongs to the current session.

### Same-session dispatch

When `pending.session_id == CLAUDE_SESSION_ID` — pass the nonce; the agent activates the grant via `APPROVE:{nonce}`:

```
Ejecuta este comando aprobado por el usuario. No requiere confirmación adicional.
Nonce: {nonce}
Comando: {command}
```

The agent runs the command. The hook finds the nonce, activates the grant, and allows the T3 operation through.

### Cross-session dispatch

When `pending.session_id != CLAUDE_SESSION_ID` — the nonce is stale and cannot be used:

1. Call `activate_cross_session_pending(pending_data)` — creates an active grant in the current session
2. Delete the old pending file
3. Dispatch a one-shot agent with the command only (no nonce):

```
Ejecuta este comando aprobado por el usuario. No requiere confirmación adicional.
Comando: {command}
```

The agent runs the command. The hook finds the pre-activated grant (by command signature) and allows the T3 operation through.

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
