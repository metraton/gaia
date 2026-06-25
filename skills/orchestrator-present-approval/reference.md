# Orchestrator Present Approval -- Reference

Detailed templates, examples, grant mechanics, and the dispatch mode checklist.
Read on-demand when presenting approvals. All mechanics here are anchored to the
DB-backed approval flow (`gaia/approvals/`, `hooks/modules/security/approval_grants.py`).

## GOOD vs BAD Examples

### Example 1: Git push

**BAD -- vague label, missing fields:**
```
AskUserQuestion(
  question="Shall I push the changes?",
  options=["Approve", "Reject"]
)
```
Missing: OPERATION, COMMAND, SCOPE, RISK, ROLLBACK. Label "Approve" does not name the action.

**BAD -- paraphrased command, generic label:**
```
AskUserQuestion(
  question="APPROVAL REQUIRED\n\nOPERACION: Push changes\nCOMANDO: push the 2 commits\nSCOPE: main branch\nRIESGO: MEDIUM\nROLLBACK: git revert",
  options=["Approve -- aplicar cambios", "Reject"]
)
```
COMMAND is paraphrased ("push the 2 commits" instead of the literal `git push origin main`). Label is a vague paraphrase.

**GOOD -- verbatim command, specific label:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Push 2 commits to origin/main\n"
    "COMANDO:   git push origin main\n"
    "SCOPE:     remote origin, branch main -- 2 commits (a1b2c3, d4e5f6)\n"
    "RIESGO:    MEDIUM -- modifies shared branch history\n"
    "ROLLBACK:  git revert a1b2c3..d4e5f6"
  ),
  options=["Approve -- push 2 commits to origin/main [P-a1b2c3d4]", "Reject"]
)
```

### Example 2: Terraform apply

**BAD:**
```
options=["Approve -- los 3 recursos", "Reject"]
```
"los 3 recursos" -- what 3? The user cannot tell from the label alone.

**GOOD:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Apply Terraform changes to dev VPC\n"
    "COMANDO:   terraform -chdir=/infra/dev apply -auto-approve\n"
    "SCOPE:     3 resources: google_compute_network.dev, google_compute_subnetwork.dev-a, google_compute_subnetwork.dev-b\n"
    "RIESGO:    MEDIUM -- creates new cloud resources in dev\n"
    "ROLLBACK:  terraform -chdir=/infra/dev destroy -auto-approve"
  ),
  options=["Approve -- terraform apply (3 resources in dev) [P-9c4e1f2a]", "Reject"]
)
```

### Example 3: Multiple file edits

**GOOD:**
```
AskUserQuestion(
  question=(
    "APPROVAL REQUIRED\n\n"
    "OPERACION: Edit 3 config files to update API endpoint\n"
    "COMANDO:\n"
    "  1. Edit /app/config/prod.yaml -- api_url: https://old.api.com -> https://new.api.com\n"
    "  2. Edit /app/config/staging.yaml -- api_url: https://old.api.com -> https://new.api.com\n"
    "  3. Edit /app/.env.production -- API_BASE=https://old.api.com -> API_BASE=https://new.api.com\n"
    "SCOPE:     3 config files in /app/config/ and /app/.env.production\n"
    "RIESGO:    HIGH -- production config, affects live API routing\n"
    "ROLLBACK:  git checkout HEAD -- /app/config/prod.yaml /app/config/staging.yaml /app/.env.production"
  ),
  options=["Approve -- update API endpoint in 3 config files [P-d7f3a09b]", "Reject"]
)
```

## Option Label Patterns

| Pattern | Verdict | Why |
|---------|---------|-----|
| `"Approve -- push 2 commits to origin/main [P-a1b2c3d4]"` | GOOD | Names exact action, includes nonce suffix |
| `"Approve -- terraform apply (3 resources in dev) [P-9c4e1f2a]"` | GOOD | Names tool, count, environment, includes nonce suffix |
| `"Approve -- delete branch feature/old-login [P-f5b0e871]"` | GOOD | Names the destructive action and target, includes nonce suffix |
| `"Approve -- push 2 commits to origin/main"` | BAD | Missing `[P-{8hex}]` suffix -- hook cannot do targeted activation |
| `"Approve"` | BAD | No action description |
| `"Approve -- aplicar cambios"` | BAD | Vague paraphrase |
| `"Approve -- los 3"` | BAD | What 3? |
| `"Approve -- the plan above"` | BAD | References context, not action |
| `"Si, ejecutar"` | BROKEN | Missing "Approve" -- the label regex requires it |

The nonce extractor is `extract_nonce_from_label()` in
`hooks/modules/security/approval_grants.py`. Its regex `_APPROVE_NONCE_RE` is
`^Approve\b.*\[P-([a-f0-9]+)\]` -- the label MUST start with "Approve" and
contain `[P-<hex>]`. Reject labels never carry a nonce. The captured hex is the
**first 8 chars after `P-`** (the `[P-xxxxxxxx]` prefix tag);
`activate_db_pending_by_prefix()` loads all pending rows via
`get_pending(all_sessions=True)` and selects the one whose `id` starts with
`P-{prefix}`.

## On batch intents -- the COMMAND_SET grant (one consent, N commands)

The old `verb_family` design (one approval covering many commands of the same
`base_cmd + verb`) **was removed**. The module docstring in
`hooks/modules/security/approval_grants.py` is explicit: "The legacy verb_family
path has been removed."

Its replacement is the `COMMAND_SET` grant: an explicit list of
`{command, rationale}` items, each matched **byte-for-byte** (D10: no whitespace
normalization, no quote canonicalization, no shell expansion) and consumed
individually (`create_command_set_grant` and `match_command_set_grant` in
`approval_grants.py`).

**Current state of the code: all three sides are wired -- intake, activation,
consume.** It is a **plan-first** flow: the subagent declares the batch up-front
by emitting an `APPROVAL_REQUEST` whose `approval_request` carries a
`command_set` list and **no** `approval_id`.

- **Intake.** The SubagentStop processor
  `hooks/modules/agents/handoff_persister.py` ->
  `_intake_command_set_pending()` reads the `command_set`; when it holds **>= 2**
  items it calls `gaia.approvals.store.insert_requested()` with a payload that
  contains the `command_set` key, minting **exactly ONE** pending `COMMAND_SET`
  approval with one `approval_id`. A set of `<= 1` item is declined (no
  COMMAND_SET is minted for one command).
- **Activation.** When the user approves, `activate_db_pending_by_prefix()`
  (`hooks/modules/security/approval_grants.py`) reads `payload["command_set"]`,
  and because it has > 1 item branches at **Step 3b** into
  `create_command_set_grant()`, inserting ONE `COMMAND_SET` grant row (status
  `PENDING`, `command_set_json` holding the whole set, 60-min TTL via
  `DEFAULT_COMMAND_SET_TTL_MINUTES`) instead of a singular
  `SCOPE_SEMANTIC_SIGNATURE` grant.
- **Consume.** On each retry, `bash_validator` calls `match_command_set_grant()`
  (byte-for-byte index match), then `mark_command_set_item_consumed()`; a
  consumed index never matches again (replay protection), and when every index
  is consumed the grant flips to `CONSUMED`.

**Practical consequence:** a `batch_scope` field still does nothing -- the signal
is `command_set`. To approve a sweep of N related commands under one consent,
present the single `COMMAND_SET` approval the intake minted: show **all N
commands** in the question body, with **one** Approve label carrying **one**
`[P-{nonce8}]` suffix. The user gives one consent; each command then runs on its
own retry within the 60-minute window. You do NOT issue N separate approvals.

**Reading the batch id and commands -- from the block, not by dispatch.** Once
the minted `COMMAND_SET` pending has survived a turn, it appears in the injected
`[PENDING-APPROVALS-VERIFIED]` block with its content-derived `approval_id` and
all N commands attached (`build_verified_pending_approvals` in
`hooks/modules/session/session_manifest.py`). Read the id and the commands
straight from that block -- the orchestrator has no shell and must NOT dispatch
`gaia approvals derive-id` or any verify command. For a command_set emitted in
the CURRENT turn (not yet in the block), present from the subagent's relayed
`approval_request`, which carries the same `command_set`.

## Grant Activation Mechanics

When the hook blocks a T3 Bash command in subagent context,
`_build_sealed_payload()` (`hooks/modules/tools/bash_validator.py`) constructs
the 7-field payload and `store.insert_requested()` (`gaia/approvals/store.py`)
generates a `P-{uuid4_hex}` `approval_id`, fingerprints the payload, inserts an
`approvals` row (`status='pending'`), and writes the `REQUESTED` event. The deny
message ends with `approval_id: P-{...}` (`build_t3_blocked_denial_message` in
`hooks/modules/security/approval_messages.py`).

The orchestrator presents via AskUserQuestion with the `[P-xxxxxxxx]` label,
reading the `approval_id` and fields from the injected
`[PENDING-APPROVALS-VERIFIED]` block (primary) or, for a same-turn pending not
yet in the block, from the subagent's relayed `approval_request` (fallback). It
does not dispatch to verify or derive. When the user selects the Approve label,
the **ElicitationResult hook**
(`hooks/elicitation_result.py`) fires and calls
`activate_db_pending_by_prefix()`, which:

1. finds the pending `approvals` row by prefix (`get_pending(all_sessions=True)`),
2. writes `SHOWN` then `APPROVED` events and flips `approvals.status` to `approved`,
3. inserts a `SCOPE_SEMANTIC_SIGNATURE` row into `approval_grants` (status `PENDING`),
4. also writes a legacy filesystem grant file as a deprecated fallback (the DB
   path is primary; filesystem remains as a fallback consumer-side path).

On the subagent's retry, `check_approval_grant()` (DB-primary path in
`hooks/modules/security/approval_grants.py`) calls `check_db_semantic_grant()`
(`gaia/store/writer.py`); on a match, `bash_validator` immediately calls
`consume_db_semantic_grant()` to set the grant `status='CONSUMED'`. The grant
is single-use -- a second attempt within the TTL window will not match.

No nonce or `approval_id` is relayed through SendMessage; activation is entirely
hook-driven by the label the user selected.

## Re-dispatch instead of resume

`mode` is per-dispatch of the Agent tool and does **not** survive a SendMessage
resume. A subagent dispatched with
`acceptEdits` / `bypassPermissions` that emits APPROVAL_REQUEST ends its turn; a
SendMessage resume runs in `default`, so CC native re-blocks the next protected
operation even after the Gaia grant activated.

Prefer a **fresh re-dispatch** carrying the same `mode` and the verbatim
`exact_content` of the approved command, over a SendMessage resume. The DB grant
lives in the session and is found by the re-dispatched subagent on its retry.

## Scope Mismatch -- The Common Re-block Trap

Semantic grants match by **semantic signature** per shell statement:
`base_cmd + verb + normalized arguments`, where each statement separated by `;`,
`&&`, or `||` is classified independently. Two statements with the same verb but
different path arguments are different signatures and do NOT share a grant.

**Example of the trap:**

1. Agent is blocked trying to run: `rm /path/to/file-A`
2. Orchestrator approves it. The grant matches that command's signature.
3. Orchestrator resumes: "Delete the stale file and then do the git operations"
4. Agent runs: `rm /path/to/file-B` (different path)
5. **Blocked again** -- the signature does not match.

**Why it happens:** the orchestrator paraphrased the operation instead of quoting
the approved command verbatim, giving the agent latitude to choose a different
target.

**Correct resume:** quote the exact approved command.

```
# BAD resume
"Proceed. Delete the stale file and then do the git operations."

# GOOD resume
"Proceed. Run exactly: rm /path/to/file-A"
"Then continue with the git operations."
```

If the correct target changed since approval, present a NEW approval for the new
command -- do not resume with modified instructions.

## Cosmetic drift -- the wrapper trap

Rule 3 says copy `exact_content` byte-for-byte. The most common breakage is not
changing the command itself but adding **wrapping** the orchestrator considers
harmless. The matcher sees a different statement and re-blocks.

| Approved | What the orchestrator typed | Why it missed |
|---|---|---|
| `gaia brief set-status X closed` | `gaia brief set-status X closed 2>&1` | No longer misses -- shell redirects (`2>&1`, `> file`, `2> file`) are normalized OUT of the signature, so a bare redirect reuses the grant. Pipes, `cd` prefixes, wrappers, and added flags still miss. |
| `rm /path/to/file` | `cd /path && rm to/file` | `cd && ` prefix turned one statement into a chain |
| `terraform apply` | `bash -c "terraform apply"` | Wrapper turned the verb from `terraform` to `bash` |
| `git push origin main` | `git push origin main --verbose` | Flag added that was not in the approved scope |
| `npm install` | `time npm install` | Time prefix changed the leading verb |
| `kubectl apply -f x.yaml` | `kubectl apply -f x.yaml && echo done` | Chained statement -- second statement has no grant |

The user approved a specific command. The orchestrator's job is to put that
command, unchanged, in front of the agent. Any stderr capture, cwd change, or
other effect needs a fresh approval -- not a retrofit. This applies symmetrically
to SendMessage resume and to fresh Agent re-dispatch.

## Dispatch mode checklist

**When to pass `mode: acceptEdits`:**
- Dispatch edits briefs, plans, or evidence files (`.claude/project-context/**`)
- Dispatch edits skills, agents, or commands (`.claude/skills/**`, `agents/**`, `commands/**`)
- Dispatch writes any file under `.claude/` that is NOT hooks/ or settings files

**When NOT to use `acceptEdits`:**
- Dispatch requires mutative Bash (acceptEdits does not cover Bash -- the Gaia T3 flow still fires regardless of mode)
- Dispatch is exploratory/read-only (use `default` or omit mode)
- Dispatch touches `.claude/hooks/` or `settings.json` -- Gaia blocks these regardless of mode

**foreground vs background:** the Agent tool exposes this as `run_in_background`.
Default is foreground and rarely needs to be set explicitly. The decision that
shapes runtime behavior is dispatch-vs-resume (see "Re-dispatch instead of
resume"), because SendMessage resumes always run in the background literal where
AskUserQuestion auto-denies.

**The mode is not inherited.** Set `mode` per dispatch -- subagents receive
`default` unless you pass `mode` explicitly.

| Dispatch type | mode to pass | session |
|--------------|-------------|---------|
| Reads only (investigate, report) | omit (default) | foreground (default) |
| Edits `.claude/skills/`, briefs, evidence | `acceptEdits` | foreground (default) |
| T3 where approval may be needed mid-task | `default` or `acceptEdits` | **foreground** |
| T3 with bounded scope, pre-satisfied permissions | `acceptEdits` or `bypassPermissions` | foreground or background |
| Edits `.claude/hooks/` or settings | never dispatch directly | n/a -- requires Gaia approval flow |

Cross-reference: for how `mode` is chosen at dispatch time and why it does not
survive a resume, see the `gaia-orchestrator` agent identity ->
"Re-dispatch vs SendMessage".
