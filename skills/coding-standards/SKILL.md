---
name: coding-standards
description: Use when writing, editing, or reviewing code and its inline documentation — module headers, inline comments, docstrings, doc comments — in any language or stack
---

# Coding Standards

Code is read far more often than it is written, and it outlives the session,
ticket, or conversation that produced it. These are the language-agnostic rules
for writing code so a future reader — human or agent, holding none of today's
context — can trust what they see without a second source.

## The two obligations

**Zero redundant comments. One hundred percent of contract comments.** Both at
once, or neither is met: cutting narration while leaving an interface
undocumented trades one defect for another, and documenting every interface
while restating the code buries the contract in noise.

What NOT to comment has been settled for fifty years and is not in dispute — a
comment restating the code is a second thing to keep in sync, and it drops the
first time it drifts. HOW MUCH to comment is an open disagreement between two
schools and will stay open, so no line count, ratio or density target appears in
this skill. Redundancy is the threshold, never length. And "the code documents
itself" is not a neutral reading of that dispute: it is the first of the four
rationalizations one school names and calls a myth.

## Fix the code first

Renaming, extracting and typing come before commenting. A comment explaining a
confusing name is worse than the rename that dissolves it, because it leaves the
confusion in place for whoever reads only the code. Both schools agree here, so
it is shared floor — invoking "comments help" against a rename invokes nothing.
Commenting is the second move, reached when the first cannot carry the meaning.

## The why-not-what test

Before writing a comment, ask whether the next line already says it.

```
# narrates — delete
# loop over the items
for item in items:

# earns its line — states the non-obvious constraint
# insertion order matters: downstream dedup relies on last-write-wins
for item in items:
```

## The seven protected categories

Seven kinds of information no code carries, however well written. These are
never removed as noise: deleting one deletes a contract, and the loss does not
show in the diff — it shows months later, in the reader who guessed wrong.

| Protected | What is lost with it |
|-----------|----------------------|
| Why, and which alternatives were rejected | The next reader re-litigates a settled decision, or reverts to an option already ruled out |
| The interface contract | The caller infers it from one implementation and couples to an accident |
| Precision the type does not carry — units, bounds, invariants, the meaning of empty or absent | A call that type-checks and is still wrong |
| The high-level intuition | Every part is legible and the whole is not |
| External cause — a dependency defect, a regulatory duty, a provider quota, a standard dictating the shape | The workaround reads as arbitrary, gets "cleaned up", and restores the defect |
| Why THIS value and not another | A constant nobody dares change and nobody can justify |
| Legal and licensing notices | A compliance obligation silently dropped |

## When commenting beats restructuring

| Case | Why restructuring loses |
|------|-------------------------|
| Extraction produces tangle | The reader reconstructs the original anyway, jumping between units |
| Restructuring degrades something real | A witness case introduced a concurrency defect and a performance regression |
| The idea is intrinsically hard to explain | The difficulty is in the problem; no arrangement of code removes it |

**The burden of proof sits on whoever invokes the exception**: state which
restructuring you tried and why it did not serve. This is the easiest section to
reach for as an excuse, and an untested "extraction would tangle" is exactly the
rationalization the first obligation exists to stop.

## Audit in both directions

A review that only hunts comments to delete will always find some and will never
find the interface nobody documented. Sweep for redundancy AND for missing
contract, or the audit reports half its subject as though it were the whole.

## One rationale, one place

Said once it is documentation; said twice it is drift waiting, because the
copies will not update together. Before adding one, check the module header, the
entry's own documentation, and the comments just above. A second site references
the first — it never repeats it.

## Where documentation belongs

Documentation of an entry lives in the language's native mechanism when one
exists; a comment is its destination only when none does. Where there is no
native slot the comment stops being optional — it is the only place the contract
can live, and deleting it deletes the contract. The bar inverts by stack: with a
doc mechanism, a comment restating it is redundant; without one, that same
comment IS the contract.

## Cleanup is bounded by the radius of what you touch

Dead code inside what you are modifying: delete it. Outside it: report, do not
touch — even when it is wrong, because matching a repository's existing patterns
is worth more than correcting them in passing. The counterpart holds too: inside
the module you improve what you touch, rather than leaving it as found because it
was not the assignment. What you see and do not touch gets REPORTED — through
whatever channel the work already reports through — so the defect is not lost to
respecting the boundary.

## Anchor the rule to what a tool can verify

A rule a tool can check outweighs one only a human can judge; where a checker
exists for the stack, bind the rule to it. Note the asymmetry: these tools detect
MISSING documentation and none detects surplus. The contract obligation is
therefore mechanically enforceable and the redundancy obligation is not — which
is why redundancy needs the discipline, since nothing else will catch it.

## No temporal, attribution, or process traces

No dates, authors, task identifiers, acceptance-criterion numbers, Finding or
Risk labels, "generated by", or changelog in a comment. Version history and the
change description already own that, and none of it means anything to a reader
without the originating ticket open. Strip the pointer; keep the durable
rationale, if any survives once it is gone.

## Not in scope here

Project-specific numeric knobs — a line-length limit, a timeout constant, a lint
threshold — are that project's configuration, not a rule of this skill. The
principle is taught here; the number lives in the project's own config.

For the per-stack tables — where documentation natively lives, which stacks have
no native slot, and which checker verifies which rule — see `reference.md`.
