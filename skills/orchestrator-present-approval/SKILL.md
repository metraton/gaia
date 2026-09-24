---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Gaia builds and shows every signature: a block with who asks, what for, and
the exact commands, and on Details a block with what each command does, its
impact, the rollback, the validity, the ID and the fingerprint. No model writes
it. You never print, copy, summarise or translate any part of it; you only run
`gaia approvals question` and open the short question it gives you, unchanged.
You can run it again at any time for the approvals still pending -- for
example when several specialists return signatures.

## Claude Code

1. Run `gaia approvals question <approval_id> [<approval_id> ...]` with the
   pending ids -- up to 4 in one question, one signature each.
2. Call AskUserQuestion with that output unchanged. It holds only each short
   question with Approve / Reject / Details; Gaia's hook shows each
   signature's block as the question opens. Print nothing about the
   signature.
3. Details: the hook names the command to run,
   `gaia approvals question --details <approval_id> ...`. Pass its output to
   AskUserQuestion the same way; the hook shows the Details block. If the hook
   refuses a question, run `gaia approvals question` again for the ids it
   names.

## OpenCode

Gaia also opens the question in the requesting specialist's session as soon as
the request is made; the user may answer it there, and on approval
Gaia posts a notice in your session naming the specialist session to resume
(`task_id`) with `execution`. To present a pending signature yourself, without resuming
anyone:

1. Run `gaia approvals question <approval_id>`, one id per call.
2. Call the question tool with that output unchanged, and print nothing about
   the signature. Gaia's plugin posts the signature's block in your session
   just before the question, fills your call with the short question, and
   binds the answer to that approval. Only approvals your own specialists
   requested are accepted; ask several one after another.
3. Your call's result says what Gaia did with the answer. Details: run
   `gaia approvals question --details <approval_id>` and ask again the same
   way; the plugin posts the Details block. Approved: resume the specialist
   session it names (`task_id`) with `execution`.

## After the answer

Read the decision with `gaia approvals show <approval_id>` (its State). Approved:
resume the specialist that requested it -- the same agent, not a new one --
with `execution`; the signature is bound to that agent and session. Rejected:
nothing runs; tell the requester if its work depends on it. A typed answer is
not a decision. You may withdraw a pending approval (`pending-approvals`), but
the decision to approve is the user's alone.
