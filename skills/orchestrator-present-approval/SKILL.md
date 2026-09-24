---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Gaia builds every signature, and each of its commands is one question on one
line, marked `[GAIA-SECURITY]` so the user reads it as Gaia's check and not as
your question: who asks and the exact command. Details turns each question
into one line with the command, what it does, its impact and the rollback,
plus the folder when it matters. The field labels are English; what the
requester wrote is in the user's language. No model writes any of it. You never print, copy, summarise or translate any part of it; you
only run `gaia approvals question` and open what it gives you, unchanged. You
can run it again at any time for the approvals still pending -- for example
when several specialists return signatures.

A signature is approved only when every one of its questions gets Approve; a
Reject on any of them rejects the whole signature. One call asks at most 4
questions, so a signature carries at most 4 commands.

## Presenting

The flow is the same in Claude Code and OpenCode. Only you open the question:
a specialist that asks for a signature, or is blocked on a command, asks the
user nothing. It ends its turn and returns `APPROVAL_REQUEST` with the
approval id in its contract. You decide when to ask.

1. Run `gaia approvals question <approval_id> [<approval_id> ...]` with the
   pending ids -- up to 4 questions in all, one per command.
2. Call your question tool (AskUserQuestion in Claude Code, question in
   OpenCode) with that output unchanged, and print nothing about the
   signature. Gaia checks the call before the questions open -- the hook in
   Claude Code, the plugin in OpenCode, which fills the call with every
   signature's questions -- and ties each answer to its own signature. Only
   approvals your own specialists requested are accepted. In OpenCode, ask
   two signatures of the same specialist in separate calls: that specialist
   runs one approval at a time.
3. Details: Gaia names the command to run -- the hook's message in Claude
   Code, your call's result in OpenCode --
   `gaia approvals question --details <approval_id> ...`. Pass its output to
   the question tool the same way. If Gaia refuses a question, run
   `gaia approvals question` again for the ids it names.

## After the answer

Read the decision with `gaia approvals show <approval_id>` (its State). Approved:
resume the specialist that requested it -- the same agent, not a new one --
with `execution`; the signature is bound to that agent and session. Rejected:
nothing runs; tell the requester if its work depends on it. A typed answer is
not a decision. You may withdraw a pending approval (`pending-approvals`), but
the decision to approve is the user's alone.
