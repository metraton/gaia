# Approval presentation template

This is the documentation half of one renderer. The consent surface is produced
from the sealed payload by `adapters/consent_presentation.py`
(`render_native_text` over the `VISIBLE_FIELDS` table); every presenter renders
there, and both the reconstructed audit record and the completeness tripwire
read the same table. Present the produced text; do not compose it, summarise it,
reorder it, or translate a label.

## The shape

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
identity of this consent attempt.

The command block is the same for one command as for many -- `COMMANDS (1)` with
a single `[1]` entry -- so a set can never be presented in the shape of a
singular request. Each command is followed by the fingerprint of its own exact
bytes, on its own line: the fingerprint is what makes "these bytes and no
others" checkable by the reader rather than a promise.

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
- Do not call the set atomic. Consent is grouped; execution stays one command
  per call, ordered and fail-fast.
- Do not claim verification has happened. This is the pre-execution consent
  point; `VERIFICATION` states what to check afterwards.

If the sealed payload is incomplete, mismatched, or ambiguous, do not repair it
here -- route it back to the producer.
