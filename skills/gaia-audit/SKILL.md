---
name: gaia-audit
description: Use when the user asks to audit, check, or review a Gaia component (an agent or a skill) against its standard and its live implementation -- "audita este skill", "chequea este agente", "¿está bien esta skill con la implementación?", "¿cómo mejoro este agente?"
---

# Gaia Audit

The discipline for auditing one Gaia component -- an agent or a skill --
against its type standard AND the live implementation it describes, and
returning an enumerated proposal to discuss. The user reaches for this
when they point at a single component and ask "how do I improve this?
is it still right with the implementation?". The audit reads; it
proposes; it does not mutate. The orchestrator enumerates the proposal
back to the user, who decides what lands.

## Core principle

An audit is worthless if it grades the component against its own claims.
The component is a declaration; the value of the audit is anchoring every
claim to the **implementation** -- the actual files and symbols the
component describes -- and surfacing where the two have drifted apart.
Three forces shape it:

- **The standard is owned by the creator, not by this skill.** What a
  *good* agent or skill looks like lives in `agent-creation` (for agents)
  and `skill-creation` (for skills). Load the creator for the component's
  type before judging it; this skill carries the audit cycle, not the
  type standard.
- **Claims anchor to symbols, or they are unverified.** A reference to a
  field, module, table, or function is real only if it exists by that
  name in code (`activate_db_pending_by_prefix`, `_is_protected`), not by
  a line number that drifts on every edit. Hunt phantom references, dead
  instructions, and schema/contract drift by opening the implementation
  and matching symbol-for-symbol. Cover both single refs (the component
  to its own implementation) and cross refs (the component to its
  siblings).
- **Factual drift is a fix; judgment trim is a discussion.** When the
  code rejects what the component claims, that is factual drift -- propose
  the correction. When the component is merely verbose, redundant, or
  could be tighter, that is a judgment call -- propose it as a discussion
  item, not a foregone fix. Keeping the two apart lets the user approve
  fast on facts and deliberate on taste.

Use this on a single named component. For validating the whole install
pipeline (npm, dry-run, RC), that is `gaia-verify`. For deterministic
structural checks -- name-vs-dir, dangling cross-ref -- defer to
`gaia doctor` (see "Reference, do not duplicate").

## The audit cycle

1. **Identify the component and load its creator.** Resolve which file
   is the target (an agent `.md` or a skill `SKILL.md` + `reference.md`).
   Load `agent-creation` if it is an agent, `skill-creation` if it is a
   skill -- that is the standard for its type and the source of the
   judgment you will apply.
2. **Validate the declaration against the implementation.** Open the
   files and symbols the component names. Anchor each claim to a
   `file + symbol`. Hunt phantom references (named but absent), dead
   instructions (steps the code path no longer supports), and
   schema/contract drift (fields, tables, or envelope keys that have
   moved or renamed). Check single refs and cross refs to sibling
   components.
3. **Enumerate what the component does TODAY.** State, with key verbatim
   extracts, what the component currently declares and instructs. This is
   the baseline the proposal edits against -- ground it in the text, not
   in memory.
4. **Enumerate the drift.** List every gap between the declaration and
   the implementation: what the code rejects, what is stale, what is
   missing. Each item names the claim, the symbol that contradicts it,
   and the direction of the drift.
5. **Propose changes.** For each item, write the concrete change. Tag it
   **factual-drift** (the code disagrees -- a fix) or **judgment-trim**
   (tighter or leaner -- a discussion). The tag tells the user how much
   deliberation each item needs.
6. **Return for discussion.** Hand the enumerated proposal back; the
   orchestrator presents it to the user. The audit ends here -- it does
   not apply edits.

## Reference, do not duplicate

- **Creators own the type standard and the judgment.** `agent-creation`
  and `skill-creation` define what good looks like per type. This skill
  invokes them; it does not restate their rules.
- **`gaia doctor` owns the deterministic checks.** Mechanical,
  non-judgment checks -- name-vs-directory mismatch (`check_component_naming`,
  order 52) and dangling skill cross-ref (`check_skill_cross_refs`, order 53)
  -- live in `gaia doctor`, which runs them the same way every time. Do not
  re-run them inline here; run `gaia doctor` (or `gaia doctor --json`) for the
  structural pass and spend this skill's cycle on the judgment work the
  deterministic checks cannot do.

## Output

An enumerated audit proposal, read-only: numbered drift items each tagged
factual-drift or judgment-trim, with the anchoring `file + symbol` and the
concrete proposed change. No file is mutated -- the audit proposes, the
user disposes.

## Anti-patterns

- **Auditing the component against its own claims.** Grading the
  declaration by what it says about itself finds nothing. The whole value
  is anchoring to the implementation; an audit that never opens the code
  is a proofread, not an audit.
- **Anchoring to line numbers instead of symbols.** A line range is stale
  the next edit; a symbol survives. A reference that cannot name the
  symbol it points to has not been verified.
- **Collapsing factual drift and judgment trim.** Presenting a taste
  preference as a required fix erodes trust; presenting real drift as
  optional lets a broken claim survive. The tag is what lets the user
  approve fast and deliberate slow.
- **Applying the fix.** This skill returns a proposal. The moment it
  edits the component, it has skipped the discussion the user asked for
  and taken a decision that was theirs.
- **Re-teaching the type standard.** If the audit explains what a good
  skill or agent is, it is duplicating the creator. Load the creator and
  point at it; do not copy it.
