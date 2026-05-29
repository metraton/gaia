---
name: security-tiers
description: Use when classifying any operation before executing it, or deciding whether user approval is required
metadata:
  user-invocable: false
  type: reference
---

# Security Tiers

security-tiers classifies every operation into four tiers so an agent knows whether it can run freely or must request the user's consent.

## The four tiers

| Tier | What it is | Approval? | Example verbs |
|------|------------|:---:|---------------|
| **T0** | Read-only; observes state, changes nothing | No | get, list, describe, show, logs, status |
| **T1** | Local validation; no remote calls, no state | No | validate, lint, fmt, check |
| **T2** | Simulation / dry-run; may read remote, never writes | No | plan, diff, dry-run, template |
| **T3** | State-mutating; creates, updates, or destroys | **Yes** | apply, create, delete, commit, push, deploy |

## Classification heuristic

Ask, in order -- the first "yes" wins:

1. **Does it mutate live state?** (create, update, delete, apply, push, deploy) -- **T3**
2. **Does it only simulate?** (plan, diff, dry-run, template) -- **T2**
3. **Does it validate locally?** (validate, lint, fmt, check) -- **T1**
4. **Is it read-only?** (get, list, describe, show, logs) -- **T0**

This mirrors `_classify_command_tier_cached` in `hooks/modules/security/tiers.py`: blocked patterns and mutative verbs resolve to T3 first, then simulation to T2, then validation to T1, and everything left over defaults to T0 -- safe by elimination, never by an allow-list.

Conditional commands depend on flags: `git branch` is T0 for listing but T3 with `-D`, `-d`, `-m`. For cloud-specific verb patterns (kubectl, terraform, gcloud, helm, flux), see `reference.md`.

## Enforcement anchors

The runtime, not this skill, enforces tiers. Three modules layer the decision:

- `tiers.py` -- the `SecurityTier` enum (`T0_READ_ONLY`, `T1_VALIDATION`, `T2_DRY_RUN`, `T3_BLOCKED`) and `_classify_command_tier_cached` assign every command a tier.
- `blocked_commands.py` -- pattern-matches irreversible commands and permanently denies them (exit 2, never approvable).
- `mutative_verbs.py` -- CLI-agnostic detection of mutative verbs; drives the nonce / approval flow for T3.

Safe by elimination, with no allow-list: anything not blocked and not mutative is T0. Runtime is the single source of truth for nonce handling, grant scope, and approval enforcement -- this skill teaches how to think about the tier; it does not enforce it.

## The `.claude` rule

**Do not touch anything under `.claude/` -- ever, by any mechanism.** By Gaia core policy it is a hard security boundary. This is not a guideline to weigh against convenience; it is a precondition that must be satisfied before any operation begins.

**The rule applies to every execution path, not only deliberate edits.** A `sed -i`, `find -exec`, `xargs`, glob expansion, or any script that sweeps a directory tree is bound by exactly the same policy as a targeted `Edit` call. The mechanism does not change the obligation. If a bulk operation's scope *could* include a path whose components contain `.claude/` -- even deeply nested, such as `tests/fixtures/repo/.claude/settings.json` -- the correct sequence is:

1. Exclude those paths explicitly before running (e.g., `find . -path '*/.claude/*' -prune -o ...`), **or**
2. Do not run the operation at all, **or**
3. Ask the user first.

There is no fourth option. The policy is not "run it and let the hook decide" -- it is "do not attempt it." An agent that launches a bulk operation hoping the hook will catch `.claude/` paths has already violated the policy, regardless of whether the hook fires.

A second, deterministic layer backs the policy: even if attempted, the write cannot succeed. `_is_protected()` in `hooks/adapters/claude_code.py` hard-protects the most critical paths -- the Gaia hooks directory (which `.claude/hooks/` resolves into) and `settings.json` / `settings.local.json` anywhere under a `.claude/` path -- and it fires regardless of `permissionMode`. An agent running with `acceptEdits` is still blocked. The enforcement is unconditional and does not depend on the agent's intent or the operation's surface area.

Why state it here, at the top of tier classification: an agent that ignores the policy and tries anyway collides with the deterministic block. That surfaces as drift and confusing failures with no clear cause -- wasted cycles that do not produce a recoverable state. The policy is the prevention; the hook is the backstop. Knowing the rule before forming the intention spares that path entirely.

## T3 approval handoff

When a T3 command is blocked with an `approval_id`, emit `plan_status: APPROVAL_REQUEST` with the `approval_id` in `approval_request`, per the response envelope in `agent-protocol/SKILL.md`. See `subagent-request-approval/SKILL.md` for the full request schema.
