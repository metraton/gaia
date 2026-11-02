---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Gaia builds every signature. In Claude Code a signature is one English
question, headed `Signature N/M`: who asks and how many commands (and the
folder, when every command is `git -C` of the same one), each command
numbered on its own line, and a closing `What it does:` / `What they do:` line
with what the set does. In OpenCode each command is its own one-line question, marked
`[ GAIA-SECURITY ]`. Details adds what each command does, its impact, the
verification and the rollback. No model writes any of it.
You never print, copy, summarise or translate any part of it; you
only run `gaia approvals question` and open what it gives you, unchanged. You
can run it again at any time for the approvals still pending -- for example
when several specialists return signatures.

A signature is approved only when its question gets Approve (in OpenCode,
every one of its questions); a Reject rejects the whole signature. Every
signature in a call is decided on its own. One call asks at most 4 questions:
4 signatures in Claude Code, 4 commands in OpenCode. A signature carries at
most 4 commands either way, enforced when it is requested.

In Claude Code, any question you type that reads as a signature -- a
`Signature N/M`, `Firma N/M` or `Aprobación` header, Approve / Reject / Details
options, a `[ GAIA-SECURITY ]` opening or a `<agent> requests <k> commands`
opening (or the earlier `<agent> pide <k> comandos`) -- is refused unless it
is exactly what `gaia approvals question` printed.

## Presenting

The flow is the same in Claude Code and OpenCode. Only you open the question:
a specialist that asks for a signature, or is blocked on a command, asks the
user nothing. It ends its turn and returns `APPROVAL_REQUEST` with the
approval id in its contract. You decide when to ask.

A T3 command you run yourself is denied the same way, never sent to the
host's permission prompt. Run the request line its denial prints, which
carries `--agent-id` so the request is yours, then present the
`approval_id` it prints like any other. A protected write you attempt is
refused outright: dispatch a specialist for it.

1. Run `gaia approvals question <approval_id> [<approval_id> ...]` with the
   pending ids -- up to 4 questions in all: one per signature in Claude Code,
   one per command in OpenCode.
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

The user signs what the command does -- a push, an apply, a delete -- in their
language, seeing the whole route before the first signature; the tier
vocabulary never reaches them. Each grant is dispatched while it is still alive.

## What is not a signature

A denial that carries NO `approval_id` is categorical, not a tier decision:
there is nothing to present and nothing to approve, and calling it T3 invites
the user to sign a boundary no signature lifts. Name which boundary fired in
plain terms -- a blocked command, a protected path, a DB-write guard -- and
reroute the work through the governed surface.

## After the answer

Read the decision with `gaia approvals show <approval_id>` (its State); confirm
it left `pending` before resuming the requester. Approved:
resume the specialist that requested it -- the same agent, not a new one --
with `execution`; the signature is bound to that agent and session. When a
grant's TTL is running out mid-verification, re-mint it: verification after
the mutation is narration, and "execute now, nothing before" costs more than a
fresh signature. Rejected:
nothing runs; resume the same requester so it closes its contract and removes
what it created, instead of dispatching a new one. A typed answer is
not a decision. You may withdraw a pending approval (`pending-approvals`), but
the decision to approve is the user's alone.
