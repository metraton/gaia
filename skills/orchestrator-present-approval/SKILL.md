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
   about the signature, before or after. The hook shows each signature's text
   to the user, and its Details when the user chooses Details.
3. When the hook asks for it (Details was chosen, or it did not recognise the
   question), run `gaia approvals question` again for the ids it names and pass
   the new output unchanged.

## OpenCode

The question opens in the specialist's own session, at its attempt. Resume the
requesting specialist (`task_id`) with `execution` so it attempts the
requested command; its turn ends there and the user answers in that session, one signature after
another. When the user approves, Gaia posts a notice in your session naming
the specialist session: resume that session (`task_id`) with `execution`.

## After the answer

Read the decision with `gaia approvals show <approval_id>` (its State). Approved:
resume the specialist that requested it -- the same agent, not a new one --
with `execution`; the signature is bound to that agent and session. Rejected:
nothing runs; tell the requester if its work depends on it. A typed answer is
not a decision. You may withdraw a pending approval (`pending-approvals`), but
the decision to approve is the user's alone.
