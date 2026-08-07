# Gaia Compact — Examples

## Chained after reflection and curation

Memory saved `feedback_compact_duplication`; plan 7/task 42 already owns the
remaining work. Compact builds the handoff itself, from the resume point the
reflection stated and from the session's own state: those identifiers, the
exact next action, the active files. It does not repeat the feedback body or
the task description, and `UNSAVED TRANSIENT CONTEXT` is empty — every item the
reflection surfaced left with a home, so anything in that field would mean the
reflection stopped early.

## Standalone by user insistence

The user asks to compact immediately while an unsaved decision remains. Offer
reflection and curation once. If the user declines and repeats the instruction,
place the decision under `UNSAVED TRANSIENT CONTEXT`, label it as not durable,
and compact. Do not silently write memory and do not omit the decision.
