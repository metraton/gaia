---
name: claude-code-consent-adapter
description: Use when the running host is Claude Code and a sealed T3 consent surface must be delivered to the user through AskUserQuestion -- the option label format that activates the grant, and the per-signed-label activation rule
---

# Claude Code Consent Adapter

The consent protocol itself is host-neutral: the sealed payload, the seven-field
surface, the field set and the render order are owned by
`orchestrator-present-approval` and its `template.md`, and they are identical on
every host. This skill owns only the Claude Code half -- the native mechanism
that carries that surface to the user, and the channel that turns the user's
choice into a grant. Nothing here decides what is shown; it decides how the
already-rendered surface travels and how the answer comes back.

Load it in addition to `orchestrator-present-approval` when the host is Claude
Code. Under another host, load that host's adapter skill instead: the surface is
the same, the mechanism below is not.

## The mechanism is `AskUserQuestion`, and the label is the activation channel

Claude Code has no separate consent primitive. The surface is delivered as the
`question` body of an `AskUserQuestion` call, and the user's selected option
`label` -- not the body, not the `description`, not a confirmation step -- is
what the hook layer parses to create the grant.

`extract_nonce_from_label` (`hooks/modules/security/approval_grants.py`) applies
`_APPROVE_NONCE_RE`, `^Approve\b.*\[P-([a-f0-9]+)\]`, to the chosen label. The
label MUST begin with the literal English word `Approve` and MUST carry the
approval id's leading hex characters in square brackets, `[P-{nonce8}]` by
convention (the first 8 hex characters after `P-`).
`activate_db_pending_by_prefix` then matches that captured prefix against
pending rows whose `id` starts with `P-{prefix}`.

A label that fails the regex extracts no nonce:
`activate_db_pending_by_prefix` is never called, no grant is inserted, and the
ledger stays `PENDING`. The user believes they consented, every retry of the
blocked command re-blocks on the same `approval_id`, and the failure is
indistinguishable from a decision never having been made. This is the incident
this instruction exists to prevent, and it is exactly as reachable on a single
command as on a COMMAND_SET.

BROKEN labels, each producing silent inertness: `Aprobar` or any translated
verb, `Si, ejecutar`, `Approve P-4bd2b170` (no brackets),
`Approve <full approval_id>` (no `[P-...]` tag), any paraphrase of `Approve`.

## Literal shape

`AskUserQuestion` takes `questions[]`, each with `options[]` of
`{label, description}` objects -- not a bare list of strings. The GOOD label
below activated live on 2026-08-03T06:52:34Z, confirmed by the ledger
transitioning to `approved` and the push executing.

```
AskUserQuestion(
  questions=[
    {
      "question": "<the rendered consent surface, verbatim -- template.md "
                   "states its exact shape: OPERATION, the indexed COMMANDS "
                   "block with a fingerprint per command, SCOPE, IMPACT, RISK, "
                   "ROLLBACK, VERIFICATION, CONSENT>",
      "header": "Approve push",
      "multiSelect": false,
      "options": [
        {
          "label": "Approve -- push rama flux-system [P-cf8eb08e]",
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

One Approve option covers a whole COMMAND_SET, never one per command.

## Activation is per signed label

**One `AskUserQuestion` call activates as many approvals as it carries signed
labels.** A host question event answers every question in the call
independently, so N well-formed labels are N signatures, each activating its own
approval. `_handle_ask_user_question_result` (`hooks/adapters/claude_code.py`)
collects one nonce prefix per distinct `[P-<hex>]` tag across every answered
label and calls `activate_db_pending_by_prefix` once per prefix; a single
failure is recorded and the loop continues, so a grant the user also signed is
not withheld because a sibling failed. That function's docstring is the living
contract for this behaviour -- read it at the symbol rather than trusting a
transcription of it here.

Activation has exactly one predicate: `extract_nonce_from_label` either returns
a prefix for a label or it does not, and nothing else reads the answer text. A
label that yields no nonce therefore activates nothing -- a `Reject` option, a
translated verb, any paraphrase of `Approve` -- so this path can lose a
signature but never manufacture one. The residual risk has a fixed direction: a
malformed label under-grants, silently; no label shape over-grants.

The one-decision-per-call rule survives, but as **presentation hygiene rather
than an activation limit**, and it is owned by `orchestrator-present-approval`,
not by this skill. What grouping costs is the user's ability to read what they
are signing -- several exact commands folded into one decision is one signature
over a surface nobody consented to field by field -- not a dropped grant.

Before dispatching execution, confirm with `gaia approvals show <approval_id>`
that each approval you intend to execute actually left `pending`. Presentation
and activation are separate events, and that read is the only thing that
distinguishes a grant that activated from one that is silently still pending.

## Prefix length

The regex captures `[a-f0-9]+`, one or more hex characters, not a fixed 8 -- the
8-character convention is presentation discipline, not an enforced minimum.
`activate_db_pending_by_prefix` matches the FIRST pending row (oldest first)
whose id starts with `P-{captured_prefix}` and does not detect or reject a
multi-row match. A shorter prefix therefore carries a real, if small, collision
risk: two pendings sharing a short prefix would silently activate the wrong one,
with no ambiguity error of the kind `gaia approvals show` raises on the CLI
side. Always carry the full 8-character nonce (or the id's genuinely
distinguishing prefix); never truncate further to save space in the label.
