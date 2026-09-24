---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Gaia builds and shows every signature. You never print, summarise, translate
or rebuild any part of it; you only open the question.

## Claude Code

1. Run `gaia approvals question <approval_id> [<approval_id> ...]` with the
   pending ids the specialists returned -- up to 4 in one question, one
   signature each.
2. Call AskUserQuestion with that output exactly as printed, and print nothing
   about the signature, before or after: each question text already carries
   its signature, and the hook refuses any other text.
3. When the user chooses Details, the hook names the command to run:
   `gaia approvals question --details <approval_id> ...`, whose questions carry
   the Details. Pass that output unchanged the same way. If the hook did not
   recognise a question, run `gaia approvals question` again for the ids it
   names.

## OpenCode

Gaia also opens the question in the requesting specialist's session as soon as
the request is made; the user may answer it there, and on approval
Gaia posts a notice in your session naming the specialist session to resume
(`task_id`) with `execution`. To present a pending signature yourself -- the same approvals,
without resuming anyone:

1. Run `gaia approvals question <approval_id>`, one id per call: in OpenCode
   it prints a question that carries only that id.
2. Call the question tool with that output unchanged. Gaia writes the signature
   into your call before the user sees it and binds the answer to that
   approval. Only approvals your own specialists requested are accepted; ask
   several one after another. A signature you type or copy is refused.
3. Your call's result says what Gaia did with the answer. Details: run
   `gaia approvals question --details <approval_id>` and ask again the same
   way. Approved: resume the specialist session it names (`task_id`) with
   `execution`.

## After the answer

Read the decision with `gaia approvals show <approval_id>` (its State). Approved:
resume the specialist that requested it -- the same agent, not a new one --
with `execution`; the signature is bound to that agent and session. Rejected:
nothing runs; tell the requester if its work depends on it. A typed answer is
not a decision. You may withdraw a pending approval (`pending-approvals`), but
the decision to approve is the user's alone.
