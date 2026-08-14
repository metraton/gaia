---
name: code-standards
description: Use when writing, modifying, reviewing, or refactoring code — any language or stack, application, infrastructure, or configuration. Also when asked to apply, audit, or clean up the code standards of a file or module.
---

# Code Standards

Code Standards governs how code is expressed — clarity, simplicity, safety, and
maintainability — not which architectural pattern should exist. Use the applicable
domain or pattern guidance to determine the design; use these standards to implement
that design clearly and safely.

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
Refactoring outside that surface should have a concrete reason connected to the
change.

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

When the code changes or the file is audited, no comment is passed over: each one
earns its line again, is corrected, or goes. Existing volume is not precedent — every
comment already in the file faces the same justification as one written today. A pass
that leaves a commented file carrying as much comment as it found owes an account of
why each line survived, whatever route it took to get there.

### 7. Comments are context, not evidence

When reading existing code, use comments as navigation and historical context. Verify
claims about behavior against the implementation, tests, configuration, or other
executable source of truth before relying on them. A stale comment should not
override what the system actually does: treating it as truth makes an agent reason
about behavior that no longer exists.

### 8. Route each fact to the declaration that owns it

Who reads a fact decides where it lives. A fact the caller needs goes in the slot that
renders at the call site. A fact spanning implementation units is not a comment:
concepts, workflows, architecture, and usage belong in README and documentation, where
one statement serves every file instead of drifting per-file copies.

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

### Comment that points outward

A comment that names anything outside the item it annotates: "see above", "handled
below", any pointer that rots when the file is reordered, or an assertion about the
current state of another file or module. Nothing invalidates it at the point of
change — the reorder or the edit elsewhere has no reason to visit this line — so it
goes silently wrong while still reading as authoritative. State what the item itself
holds, or say nothing.
