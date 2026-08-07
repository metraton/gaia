---
name: orchestrator-present-approval
description: Use when presenting a returned APPROVAL_REQUEST for informed user consent
---

# Present Approval — Orchestrator Branch

Present trusted contract data exactly. Do not execute, derive, shorten, reorder,
or silently expand it.

## The Approve label format is mandatory -- for every request, singular or set

**The activation channel is the label text itself, not the question body, and
this applies identically whether you are presenting one command or a
COMMAND_SET.** The Approve option's `label` MUST match `^Approve\b.*\[P-{hex}]`
-- it must begin with the literal English word `Approve` and contain the
approval id's leading hex characters in square brackets, `[P-{nonce8}]` by
convention (first 8 hex chars after `P-`). This is what the hook parses
(`extract_nonce_from_label`, `hooks/modules/security/approval_grants.py`) to
find the pending row and create the grant (`activate_db_pending_by_prefix`);
the question body's full id, a `description` field, or a translated verb do
not substitute for it. A label without that tag produces no grant, the ledger
stays `pending`, and the user's consent is silently inert -- this is what
happened in the incident this skill exists to prevent, and it is exactly as
possible on a single-command approval as on a COMMAND_SET.

**Literal example -- GOOD vs BROKEN, side by side, in the tool's real shape**
(`AskUserQuestion` takes `questions[]`, each with `options[]` of `{label,
description}` objects -- not a bare list of strings). The GOOD label activated
live on 2026-08-03T06:52:34Z, confirmed by the ledger transitioning to
`approved` and the push executing:

```
AskUserQuestion(
  questions=[
    {
      "question": "APPROVAL REQUIRED -- P-cf8eb08e...\n\n"
                   "OPERACION: push rama flux-system\n"
                   "COMANDO:   git push origin flux-system\n"
                   "SCOPE:     remote origin, branch flux-system\n"
                   "RIESGO:    MEDIUM -- pushes to a shared branch\n"
                   "ROLLBACK:  git push origin --force-with-lease "
                   "origin/flux-system@{1}:flux-system",
      "header": "Approve push",
      "multiSelect": false,
      "options": [
        {
          "label": "Approve -- push rama flux-system [P-cf8eb08e]",  # GOOD -- activates
          "description": "git push origin flux-system"
        },
        {
          "label": "Reject",
          "description": "Do not push; leave the local branch as is."
        }
      ]
    }
  ]
)
```

A label of `"Aprobar"` (or any translated verb, paraphrase, or the bare
approval id with no brackets) fails the regex outright regardless of what the
`description` or question body says -- this exact shape is what produced the
original incident: two legitimate user approvals that never created a grant,
silently.

## Singular vs COMMAND_SET presentation

For a single request show the exact command, affected scope, risk, rollback (or
explicitly `none supplied`), verification, and full approval id.

For COMMAND_SET show:

1. the full approval id and bounded goal;
2. total command count;
3. every command verbatim, indexed from `0` through `N-1`, in execution order;
4. scope/effect for the set and any item-specific scope;
5. risks, including partial completion if a later command fails;
6. rollback for completed items, or explicitly that no rollback was supplied;
7. the desired-state verification to run after execution; and
8. Approve and Reject options following the label format above -- one Approve
   option covers the whole set, never one per command.

Do not call a COMMAND_SET atomic: consent is grouped, execution is separate,
ordered, and fail-fast. Do not claim verification has happened; this is the
pre-execution consent point.

Approval activation verifies the REQUESTED fingerprint. Presentation must still
be exact because informed consent depends on what the human sees. If the
contract is incomplete, reordered, mismatched, or ambiguous, do not repair it;
route back to the producer.

## Who activates, who executes

If the user approves, the orchestrator dispatches a fresh owning specialist
with the grant context and `execution` skill; the orchestrator never runs the
commands itself. `gaia approvals approve` is a separate, CLI-only admin verb
that writes the DB directly and does **not** create a hook-side grant -- it is
not the activation path, and it is not available to the orchestrator: the
trusted-CLI role guard (`hooks/modules/security/gaia_cli_only_guard.py`,
`EXPLICITLY_DENIED_PHRASES`) categorically denies `approvals approve` /
`revoke` / `reject` / `reject-all` / `clean` / `replay` for the orchestrator
role, non-approvable -- the orchestrator may only *read* approval state
(`approvals list` / `show` / `pending` / `history` / `stats`, in
`ALLOWED_READ_PHRASES`). Reads are the orchestrator's; approval decisions are
not -- they happen exclusively through the label the user selects.

**Prefix length note:** the regex captures `[a-f0-9]+` (one or more hex
characters), not a fixed 8 -- the 8-char convention is presentation discipline,
not an enforced minimum. `activate_db_pending_by_prefix` matches the FIRST
pending row (oldest first) whose id starts with `P-{captured_prefix}` and does
not detect or reject a multi-row match. A shorter prefix in the label therefore
carries a real, if small, collision risk: two pending approvals sharing a short
prefix would silently activate the wrong one, with no ambiguity error the way
`gaia approvals show` gives on the CLI side. Always use the full 8-char nonce
(or the id's genuinely distinguishing prefix) in the label -- never truncate it
further to save space.
