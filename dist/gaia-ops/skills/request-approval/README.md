# Approvals -- Operating Rules

This README is the cross-skill index for the Gaia approval system across the agent side (`request-approval/`) and the orchestrator side (`orchestrator-approval/`). Read this when you need to locate which file owns a specific rule and where to find the full text.

The 5 rules below are validated empirically -- each one was demonstrated in a live session against the runtime, not inferred from documentation. They are stated as operative rules, not doctrine, because the runtime is what enforces them; the agent's job is to model the runtime correctly.

## Rule 1 -- `acceptEdits` covers Edit/Write, not Bash mutativo

`mode: acceptEdits` satisfies CC native for Edit and Write tools. It does NOT cover Bash mutativo (`rm`, `mv`, `cp`, `chmod`) even on `.claude/` paths. Bundles that need both require `bypassPermissions` or per-command Gaia grants.

Full text and permissionMode comparison table: `security-tiers/SKILL.md` -> "Mode runtime rules" -> R1.

## Rule 2 -- Gaia bash_validator is orthogonal to `mode`

`mutative_verbs.py` classifies Bash verbs as MUTATIVE and emits an `approval_id` regardless of which `mode` the dispatch carried. `bypassPermissions` covers the CC native side but does NOT disable the bash_validator. Both layers must pass independently.

Full text: `security-tiers/SKILL.md` -> "Mode runtime rules" -> R2.

## Rule 3 -- `mode` does NOT survive a SendMessage resume

SendMessage resumes always run in `default` -- CC native re-blocks the next protected operation even after a Gaia grant has activated. Cure: re-dispatch fresh with the same `mode`, not a SendMessage resume.

Full text and dispatch-vs-resume operational procedure: `security-tiers/SKILL.md` -> "Mode runtime rules" -> R3. Orchestrator procedure: `orchestrator-approval/SKILL.md` -> "Re-dispatch instead of resume".

## Rule 4 -- `run_in_background` default is foreground; explicit setting is rare

The default in interactive sessions is foreground. `run_in_background: false` is defensive and rarely necessary. The "background" case that shapes runtime behavior is the SendMessage resume (see R3), not an explicit `run_in_background` flag.

Full text: `security-tiers/SKILL.md` -> "Mode runtime rules" -> R4.

## Rule 5 -- `batch_scope` activation requires literal "batch" in the Approve label

The agent-side request: `batch_scope: "verb_family"` is the ONLY valid value. No other string activates a batch grant.

The orchestrator-side activation flow:

1. The subagent emits `approval_request` with `batch_scope: "verb_family"`.
2. The orchestrator formats the AskUserQuestion option label as `"Approve batch -- ... [P-{nonce_prefix8}]"`. The literal word "batch" is load-bearing.
3. The PostToolUse hook for AskUserQuestion detects the literal "batch" in the answer string and creates a verb-family grant (multi-use, 10-minute TTL, scoped to `base_cmd + verb`).
4. Without "batch" in the label, the hook creates a single-use grant; commands 2..N get blocked again.

Use batch ONLY when one user intent expands into ≥3 commands sharing the same base CLI and verb. Two `mkdir` calls do not need batch; three or more do. Mixed verbs require one batch per verb in sequence -- a `modify` grant does not cover `delete` even on the same CLI.

Agent-side schema and examples: `request-approval/SKILL.md` -> "Batch Approval". Orchestrator presentation examples: `orchestrator-approval/reference.md`.

## Files in this module

| Path | Role |
|------|------|
| `.claude/skills/request-approval/SKILL.md` | Agent-side: when and how to emit APPROVAL_REQUEST |
| `.claude/skills/request-approval/reference.md` | Detailed schema, plan template, batch semantics |
| `.claude/skills/request-approval/examples.md` | Concrete examples (terraform, gitops, batch) |
| `.claude/skills/orchestrator-approval/SKILL.md` | Orchestrator-side: presentation discipline, dispatch checklist |
| `.claude/skills/orchestrator-approval/reference.md` | Good-vs-bad examples, batch flow, scope mismatch trap |
| `.claude/skills/security-tiers/SKILL.md` | T0-T3 classification, hook enforcement model, `permissionMode` matrix |
| `.claude/skills/execution/SKILL.md` | Post-approval execution discipline |
| `.claude/skills/pending-approvals/SKILL.md` | Present and resolve pending approvals |
| `.claude/hooks/modules/security/mutative_verbs.py` | Runtime classifier and grant store |
| `.claude/hooks/modules/security/blocked_commands.py` | Hard-deny patterns (irreversible) |
| `.claude/hooks/adapters/claude_code.py` | `_is_protected()` for Edit/Write gating |

## See also

- `.claude/hooks/README.md` -- pipeline overview, hook lifecycle
- `.claude/skills/README.md` -- skill catalogue and assignment matrix
