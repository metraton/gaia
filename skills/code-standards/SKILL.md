---
name: code-standards
description: Use when writing, modifying, reviewing, or refactoring code — any language or stack, application, infrastructure, or configuration. Also when asked to apply, audit, or clean up the code standards of a file or module.
---

# Code Standards

Code Standards governs how code is expressed — clarity, simplicity, safety, and
maintainability — not which architectural pattern should exist. Use the applicable
domain or pattern guidance to determine the design; use these standards to implement
that design clearly and safely.

Load and apply this discipline before generating a change, then check the result
against it as well as the behavioral checks before calling the change done. Ordinary
coding includes coherent local improvement and verification, not an automatic
multi-reviewer audit. For an explicitly requested review, use `code-review` to
organize the examination; this skill remains the owner of the quality criteria.

## Iron Law

Write code so that its behavior, responsibility, and intent can be understood from
the implementation itself. Prefer code that explains itself over explanations
surrounding unclear code.

## Mental Model

A good implementation lets another engineer — or another agent — determine what
assumptions it relies on and where to look when behavior must change. Clarity
reduces the amount of inference required to safely modify the system.

## Rules

### 1. Make responsibility visible

Before adding code, identify the responsibility being implemented and the existing
pattern it belongs to; names, boundaries, and structure should reveal that
responsibility. Follow the local pattern when one exists — it settles the form of what
you write, never whether it was warranted, and matching what surrounds you is no
evidence that what surrounds you earned its place. A new abstraction or pattern should
exist because the problem requires it, not because the current implementation can be
made more elaborate.

### 2. Prefer the simplest complete implementation

Implement the behavior required by the current problem. Additional abstractions,
configuration, indirection, branches, or extensibility added for hypothetical future
cases increase the number of assumptions a reader must understand and the number of
places a future change can fail. Safety comes from making current assumptions and
boundaries explicit, not from anticipating every possible implementation.

### 3. Make behavior explicit

Important behavior should be visible in code rather than hidden behind unexpected
side effects, implicit state, or unrelated abstractions. Inputs, transformations,
state changes, and failure paths should be traceable from the implementation.

### 4. Keep changes local

A change should touch the smallest coherent surface that correctly implements the
behavior; a small blast radius makes it easier to reason about, verify, and revert.
Include coupled declarations, callers, schemas, and tests when they must change
together to preserve an invariant; the smallest diff is not necessarily coherent.
Identify those dependencies before editing and verify the relationship afterwards.

A normal modification is not permission to restructure its neighbors. Reassess the
touched file without treating existing debt as approved; fix within the agreed
coherent surface and report unrelated debt separately. If correctness requires a
wider scope, explain the dependency and obtain agreement before expanding. An
explicit review examines its declared scope without editing; a transformation or
refactor beyond the modification requires a separate assignment.

### 5. Protect boundaries

Treat external input, configuration, network responses, persisted data, and other
trust boundaries explicitly. Validate where data enters a trusted part of the system
rather than spreading defensive assumptions throughout the implementation. Sensitive
values should not become source code, logs, error messages, or other persistent
output.

### 6. A comment is the exception, not the default

Default to no comment. Each one is optional and justified on its own: it earns its line
only by carrying a fact the code cannot state — why a non-obvious decision exists, an
invariant that must hold, an external constraint, a compatibility requirement, a
surprising consequence of changing the code. A comment that narrates the implementation
carries no such fact and leaves a second description to drift from the first. One
comment is not optional: the contract the caller reads, description or docstring, one
sentence saying what the unit promises.

Size a comment to its facts, never to the size of what it heads: one fact rarely needs
more than about two lines, and four chained facts are four entries in a dry list of
about a line each. Neither figure is a count to satisfy — padding one fact to two lines
and truncating a real chain to look short fail the same way.

When a file changes or is explicitly reviewed, reassess every comment in that file:
each one must earn its line again, warrant correction, or warrant removal. Apply
corrections only within the authorized modification; a read-only review reports
them, and unrelated cleanup remains declared debt rather than a silent scope increase.
Existing volume is not precedent — every comment already in the file faces the same
justification as one written today. A pass
that leaves a commented file carrying as much comment as it found owes an account of
why each line survived, whatever route it took to get there.

No comment-density ratio proves quality. Zero is valid where code carries the facts;
necessary knowledge must survive where it does not. A 5–10% band is only a proposal
for experimentation, not a quota, cap, gate, or chosen threshold. Keep interface
contracts, licenses, and tool directives distinct from explanatory comments; do not
delete their obligations to improve a count.

### 7. Comments are context, not evidence

When reading existing code, use comments as navigation and historical context. Verify
claims about behavior against the implementation, tests, configuration, or other
executable source of truth before relying on them. A stale comment should not
override what the system actually does: treating it as truth makes an agent reason
about behavior that no longer exists.

This is descriptive precedence: executable evidence establishes what happens, not
what ought to happen. Requirements, safety constraints, and applicable standards
govern the latter. Existing code or a passing test does not excuse a defect or poor
practice; name the disagreement instead of turning observed behavior into a norm.

### 8. Route each fact to the declaration that owns it

Who reads a fact decides where it lives. A fact the caller needs goes in the slot that
renders at the call site. Concepts, workflows, architecture, and usage spanning units
belong in shared documentation, where one statement serves every file instead of
drifting per-file copies. The turn's reasoning belongs in its report, not automatically
in the artifact. Do not repeat a fact already expressed by an interface or data field.

A synchronization invariant can belong at the declaration that must preserve it,
even when it names another unit. State what must remain aligned, why divergence is
harmful, and the stable counterpart that must be checked when changing it; verify
both sides and protect the relationship with a meaningful check where feasible.
That is a maintenance obligation, not a claim that the other file currently behaves
a certain way. A bare "keep in sync" or "see elsewhere" leaves the reader to recover
the constraint and does not earn a line. Keep the rule in one owning place rather
than copying implementations or scattering reciprocal explanations.

A fact only a maintainer needs belongs to the declaration whose constraint produces it,
not to the line where the value happens to sit. Ask whose change would make it false:
if changing how the resource iterates falsifies the comment, the fact is the
resource's, and the map it consumes is merely where the value lives. The fact travels
up to that declaration's header, and bodies — argument lists, value maps, local blocks
— stay data a reader can scan with no prose in the way.

Never write a comment beside code on the same line. Whatever the column limit, the
margin left over fits a fragment, and a fragment of a why is a description: the form
manufactures the narration every other rule here exists to prevent.

When a declaration is long enough that its header leaves the target ambiguous, name the
field — "retention_policy is deliberately unlocked: locking is irreversible." Naming a
field of the declaration being read is a coordinate, not a pointer outward; reordering
cannot break what does not depend on position.

### 9. Verify behavioral changes

When behavior changes, verify the observable behavior rather than only the shape of
the implementation. Tests should protect meaningful behavior and invariants,
especially where a future refactor could accidentally change them.
Exercise relevant failure paths and boundaries, and assert externally meaningful
outcomes or cross-file relationships. A test should fail for the regression it
claims to prevent, not merely confirm an implementation string or echo its own
fixture. For non-behavioral changes, show preservation of behavior rather than
manufacturing tests for prose. State what ran, what it establishes, and what remains
unverified; loading this skill or passing a linter is not evidence of its application.

## Traps

| Trap | Why it fails |
|------|--------------|
| Copying a nearby pattern without understanding its responsibility | Structural similarity can hide different assumptions and failure modes. |

## Anti-Patterns

### Clever compression

Code minimizes lines at the cost of making state changes, conditions, or data
transformations harder to see; behavior hidden behind convenience helpers makes the
code shorter while the actual control flow becomes harder to trace. Optimize for
understanding rather than line count.

### Pointer without an obligation

"See above", "handled below", or a copied assertion about another module's current
state can go stale without any reason to revisit the comment. Naming another unit
is not itself the defect: making the reader reconstruct the relationship, or copying
a fact owned elsewhere, is. Preserve a verified synchronization obligation under
Rule 8; remove redundant navigation rather than deleting the invariant it obscures.
