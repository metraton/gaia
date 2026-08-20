# Approval presentation template

This is the documentation half of one renderer. The consent surface is produced
from the sealed payload by
`hooks/adapters/consent_presentation.py::render_native_text` (over that module's
`VISIBLE_FIELDS` table); every presenter renders
there, and both the reconstructed audit record and the completeness tripwire
read the same table. Present the produced text; do not compose it, summarise it,
reorder it, or translate a label.

## A presentation is two pieces: a MESSAGE, then a QUESTION

The surface does not travel inside the decision. It is printed as text -- the
MESSAGE -- and the decision is asked separately as a minimal QUESTION. Both
pieces are specified below, and neither is optional: a question without its
message asks for a signature over nothing, and a message without its question
states a surface nobody was asked about.

## Piece 1 -- the MESSAGE

```
GAIA T3 APPROVAL REQUEST  <approval_id>
OPERATION:    <bounded operation>
COMMANDS (N) -- exact bytes, in order:
  [1] <verbatim command>
      sha256 <fingerprint of those exact bytes>
  [2] <verbatim command>
      sha256 <fingerprint of those exact bytes>
SCOPE:        <what the commands reach>
IMPACT:       <what changes outside this session>
RISK:         <level> -- <the rationale that produced it>
ROLLBACK:     <how the effect is reversed>
VERIFICATION: <the desired-state check to run after execution>
CONSENT:      protocol <version>  correlation <correlation_id>
```

Seven fields, one render order, one label set: `OPERATION`, the indexed
`COMMANDS` block, `SCOPE`, `IMPACT`, `RISK`, `ROLLBACK`, `VERIFICATION`. The
`CONSENT` line closes the surface with the protocol version and the correlation
identity of this consent attempt. The header line carries the `approval_id`;
that is not decoration, it is one of the two ends Rule 1 makes the reader check.

The command block is the same for one command as for many -- `COMMANDS (1)` with
a single `[1]` entry -- so a set can never be presented in the shape of a
singular request. Each command is followed by the fingerprint of its own exact
bytes, on its own line: the fingerprint is what makes "these bytes and no
others" checkable by the reader rather than a promise.

The presenter composes nothing in this block. Its own prose -- a sentence of
framing, why the operation came up, what it is part of -- goes BEFORE the
block, never inside it. A line the renderer did not produce, sitting between
two lines it did, is indistinguishable to the reader from a field of the
surface, and a reader who cannot tell rendered text from presenter text cannot
tell what they are consenting to.

## Piece 2 -- the QUESTION

```
<one-line question carrying the approval id>
<one-line operation> — N comando(s).
The full surface, with the exact bytes and their fingerprints, is in the message immediately above.

  [ Approve -- <one-line operation> [P-<8 hex>] ]
  [ Reject ]
```

The prose lines are the presenter's, in the user's language. The control text is
not: the approve control's leading word is the literal English `Approve`,
because that literal is what the resolver matches
(`hooks/modules/security/approval_grants.py::extract_nonce_from_label`, and
`hooks/modules/security/approval_grants.py::render_approve_label` for the label
Gaia itself emits). Translating the control does not translate the surface; it
silently unbinds the decision.

## The four rules that make the split safe

### Rule 1 -- the id appears on both ends

The surface's header line carries the `approval_id` and the approve control
carries it. This is what replaces the structural binding the nested shape had:
the reader is not asked to trust that the question refers to the message, they
are given the same identifier twice and can compare it. **If the two do not
match, do not sign.** That instruction is the rule -- an id printed twice that
nobody is told to compare buys nothing.

### Rule 2 -- adjacency is a requirement, not an accident

The message must sit immediately above the question, in the same exchange. If
anything intervened -- other output, another turn, an intervening tool result --
**reprint the surface before asking.**

Without this rule the split trades a structural guarantee for a coincidence.
Nesting made containment automatic: the text could not be absent from the thing
being answered. Adjacency is not automatic, and a user can answer a question
whose subject has scrolled out of view -- which is consent over a surface they
did not read, the exact failure the split was made to prevent.

Nothing in the runtime observes what was printed before the question, so this
rule is not machine-enforced, and it is stated that way rather than implied to
be checked. Its backstop is the reader, through Rule 1: a user who cannot see
the surface cannot compare the two ids, and an id they cannot compare is a
signature they must withhold. The rule binds the presenter; Rule 1 is what
makes a violation of it visible to the person being asked.

### Rule 3 -- the question carries what makes the decision identifiable, not a summary of the content

One line of operation, the command COUNT, and the id. The question does not
restate the seven fields -- restating them is what the split removed, and a
second rendering of a field is a second thing to drift from the first.

The count is not optional. It is not content of the commands; it is how many of
them the signature covers, and *"1 comando"* against *"4 comandos"* is the
difference between what a user believes they are signing and what they sign.
An operation line alone reads identically over one command and over forty.

### Rule 4 -- binary, always

`Approve` and `Reject`, and nothing else. In particular no third control for
`always`: the decision vocabulary has a standing-grant value
(`hooks/adapters/types.py::ConsentDecision`, normalized by
`hooks/adapters/consent_events.py::normalize_decision`) and this protocol
version refuses it rather than honoring it, because it issues only single-use
grants. Offering it would present a door that does not exist -- the user would
answer for a standing grant and receive, at best, something narrower than what
they answered for.

## Absence is stated, never filled

A field the sealed payload does not declare renders the statement that nothing
was declared, identical on every surface. It is never replaced by a plausible
sentence, and above all never by a substantive claim -- a payload that declares
no rollback does not license the surface to assert the operation cannot be
undone. The absence statements live beside the field table, so a reader meets
the same words about the same missing field wherever they meet it.

## What must not be done to this surface

- Do not translate a label or the absence statements.
- Do not drop a field because its value is the absence statement; a field the
  reader cannot see is a field they did not consent to.
- Do not shorten, wrap-away, or elide a command or its fingerprint.
- Do not reorder the commands: consent to an ordered set presented in another
  order is consent to something else.
- Do not move any part of the surface into the question, and do not move
  presenter prose into the surface.
- Do not call the set atomic. Consent is grouped; execution stays one command
  per call, ordered and fail-fast.
- Do not claim verification has happened. This is the pre-execution consent
  point; `VERIFICATION` states what to check afterwards.

If the sealed payload is incomplete, mismatched, or ambiguous, do not repair it
here -- route it back to the producer.
