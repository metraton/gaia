---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Gaia builds every signature, and each of its commands is one question on one
line: who asks and the exact command, written as a request for review in the
user's language. Details turns each question into one line with what that
command does, the folder it runs in when that is not the project, its impact,
the rollback, how long the approval lasts and its ID. Only the Approve / Reject / Details labels stay in English. No model writes
any of it. You never print, copy, summarise or translate any part of it; you
only run `gaia approvals question` and open what it gives you, unchanged. You
can run it again at any time for the approvals still pending -- for example
when several specialists return signatures.

A signature is approved only when every one of its questions gets Approve; a
Reject on any of them rejects the whole signature. One call asks at most 4
questions, so a signature carries at most 4 commands.

## Claude Code

1. Run `gaia approvals question <approval_id> [<approval_id> ...]` with the
   pending ids -- up to 4 questions in all, one per command.
2. Call AskUserQuestion with that output unchanged. Gaia's hook checks it is
   exactly what Gaia wrote before the questions open. Print nothing about the
   signature.
3. Details: the hook names the command to run,
   `gaia approvals question --details <approval_id> ...`. Pass its output to
   AskUserQuestion the same way. If the hook refuses a question, run
   `gaia approvals question` again for the ids it names.

## OpenCode

Gaia also opens the question in the requesting specialist's session as soon as
the request is made; the user may answer it there, and on approval
Gaia posts a notice in your session naming the specialist session to resume
(`task_id`) with `execution`. To present a pending signature yourself, without resuming
anyone:

1. Run `gaia approvals question <approval_id>`, one id per call.
2. Call the question tool with that output unchanged, and print nothing about
   the signature. Gaia's plugin fills your call with that signature's
   questions, one per command, and binds the answers to that approval. Only
   approvals your own specialists requested are accepted; ask several one
   after another.
3. Your call's result says what Gaia did with the answers. Details: run
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
