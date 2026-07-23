# Skill Creation -- Examples

Before/after pairs for the three prose failure modes documented in SKILL.md Anti-Patterns. Each pair uses a real or realistic case from the Gaia skill library.

## Contents

1. [Opening by negation](#opening-by-negation)
2. [Phantom / unanchored reference](#phantom--unanchored-reference)
3. [Inflated prose](#inflated-prose)

---

## Opening by negation

**Before** (from `agent-approval-protocol/SKILL.md` -- actual text):

> This skill is NOT `agent-protocol`. That skill documents the universal response contract. This skill documents only the approval-specific handoff.

The reader must already know what `agent-protocol` covers to understand what this skill covers. An agent that has not loaded `agent-protocol` cannot parse this as a definition.

**After** (self-contained opening):

> `agent-approval-protocol` documents the data contract that flows between a subagent and the orchestrator when a T3 command is blocked: the `sealed_payload` fields, the `approval_id` format, the `APPROVAL_REQUEST` contract shape, and how to confirm a grant is active before proceeding. For the universal response envelope (agent_state states, evidence_report), see `agent-protocol`.

The skill now opens by stating what it IS. The disambiguation pointer comes after, framed as a continuation handoff rather than a defining contrast.

---

## Phantom / unanchored reference

**Before** (a Reference skill asserting a field name without checking):

> Set the `grant_token` field in the approval payload. The hook reads this field to activate the grant.

If the actual field in code is `approval_id` (not `grant_token`), the reading agent builds an invalid payload the hook rejects.

**After** (verified against source):

> Set the `approval_id` field in the approval payload (see `hooks/modules/security/approval_grants.py`). The hook reads this field to activate the grant.

The field name is anchored to the file where it is defined. A reader who doubts the name can verify it in one step.

---

## Inflated prose

**Before**:

> It is important to note that when writing skills, you should always consider the type of skill you are creating, as different types have different structural requirements that are important to follow correctly.

This sentence restates the heading "Step 1: Choose the type" without adding any decision the agent can act on. Removing it changes nothing.

**After**: deleted entirely. The heading plus the type table carry the message.
