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
this skill. Redundancy is the threshold, never length. That refusal is the state
of the evidence, not caution: every circulating target traces to a source that
cannot be checked, the one maintainability metric that scored comment density
was dropped by its own adopters, and the strongest replicated result in the
field runs the other way — comment-code inconsistencies, hunted automatically,
turned out to be confirmed defects. A density target measures the harmless
dimension and misses the harmful one. And "the code documents itself" is not a
neutral reading of that dispute: it is the first of the four rationalizations
one school names and calls a myth.

## Is it contract? The caller test

**Would a caller who cannot see this implementation still get it right?** If the
information is needed by someone reading only the interface, it is contract and
the second obligation applies. If it is needed only by someone reading the body,
it is implementation commentary and the first one does. Without this test the
second obligation certifies itself — an agent simply declares contract whatever
it already wrote — and confident compliance is worse than an absent rule.

## Fix the code first

Renaming, extracting and typing come before commenting. A comment explaining a
confusing name is worse than the rename that dissolves it, because it leaves the
confusion in place for whoever reads only the code. Both schools agree here, so
it is shared floor — invoking "comments help" against a rename invokes nothing.
Commenting is the second move, reached when the first cannot carry the meaning.

## The why-not-what test

Before writing a comment, ask whether the next line already says it. One that
narrates the code is deleted; one that states a constraint the code cannot show
earns its line. Worked pairs are in `examples.md`.

A separator or banner that carries no information is not a comment at all — it
is layout. Neither obligation reaches it, so leave it as the repository has it:
matching an existing pattern outranks a preference about decoration.

## The seven protected categories

Seven kinds of information no code carries, however well written. These are
never removed as noise: deleting one deletes a contract, and the loss does not
show in the diff — it shows months later, in the reader who guessed wrong.

| Protected | What is lost with it |
|-----------|----------------------|
| Why, and which alternatives were rejected | The next reader re-litigates a settled decision, or reverts to an option already ruled out |
| The caller's contract, including the precision the type does not carry — units, bounds, invariants, the meaning of empty or absent | The caller infers the contract from one implementation, couples to an accident, and writes a call that type-checks and is still wrong |
| The high-level intuition | Every part is legible and the whole is not |
| External cause — a dependency defect, a regulatory duty, a provider quota, a standard dictating the shape | The workaround reads as arbitrary, gets "cleaned up", and restores the defect |
| Why THIS value and not another | A constant nobody dares change and nobody can justify |
| Legal and licensing notices | A compliance obligation silently dropped |
| Provenance — the source a fact was verified against | The only record that the claim was ever checked; without it the fact can be trusted or re-derived, never re-verified |

## When commenting beats restructuring

| Case | Why restructuring loses |
|------|-------------------------|
| Extraction produces tangle | The reader reconstructs the original anyway, jumping between units |
| Restructuring degrades something real | A witness case introduced a concurrency defect and a performance regression |
| The idea is intrinsically hard to explain | The difficulty is in the problem; no arrangement of code removes it |

**The burden of proof sits on whoever invokes the exception**: state which
restructuring you tried and why it did not serve. An exception claimed without
that account is indistinguishable from one never tested — which is why this is
the easiest section to reach for as an excuse, and why an untested "extraction
would tangle" is exactly the rationalization the first obligation exists to stop.

## Audit in both directions

A review that only hunts comments to delete will always find some and will never
find the interface nobody documented. Sweep for redundancy AND for missing
contract, or the audit reports half its subject as though it were the whole.

## One rationale, one place

Said once it is documentation; said twice it is drift waiting, because the
copies will not update together. Before adding one, check the module header, the
entry's own documentation, and the comments just above. A second site references
the first — it never repeats it.

## Cache what the reader cannot look up

The environment is a source of truth in its own right: configuration files,
scripts, the directory layout, a tool's own `--help`. A sentence restating what
one of those already answers is a cache — a copy of a lookup — and a cache is
justified only when the lookup is expensive. What earns the sentence is what no
lookup returns: the unwritten convention, the reason behind a choice, the gotcha
no config confesses. Asked while writing, this converts staleness from a
maintenance problem someone inherits into a question settled at the moment of
authorship. (Formulation adapted from `writing-for-agents` in mattpocock/skills
— cited per the seventh protected category.)

## The four durabilities of a written claim

Whether a prose claim about code can silently become false is decided by what
the claim is ABOUT, and the question takes a second to ask:

| The claim is about | Durability |
|--------------------|------------|
| The language, the platform, the provider's semantics | Cannot rot |
| A decision and the alternative it rejected | Cannot rot — it is past tense, and the past stays true |
| The current state of another file or system | Rots silently: the target moves, and nothing at the claim's site changes |
| What the code below it already shows | Rots on the next edit, and carried nothing while it lasted |

The last row is what the why-not-what test already deletes, and the first two
are where the protected categories live — a rationale is durable precisely
because it asserts history, not state. The third row is the one that needs its
own treatment: in a real audit of a 33-file repository, every false claim found
— six — asserted state outside the file it lived in, and none was about the
language, a decision, or the code below. Falsehood accumulates exactly there,
because nothing that edits the target ever re-reads the claim. The more of a
document is made of that row, the faster it rots — a README is the limiting
case, and `readme-writing` carries that consequence.

So a claim about elsewhere is a cost accepted knowingly, not a default: prefer
the durable forms — the decision, the constraint, the why. When the state of
another file or system genuinely must be asserted, give the claim the
coordinate that makes it checkable: the file and the symbol. A symbol survives
edits; a line number drifts on the first insertion above it. And be honest
about what the coordinate buys: pointers rot too — of the millions of links
found in published source comments, roughly one in ten is dead. The coordinate
lowers the cost of checking the claim; it does not make the claim durable.

## Reading: a claim, never evidence

A comment or a document is a claim about code, never evidence of it. Reading
code to decide something, verify the claim against the implementation it
describes, or state that you did not — those are the two honest outcomes, and
both are cheap next to what absorbing a falsehood costs. Measured: models
handed deliberately false comments described the program the prose claimed
rather than the one the code implemented, invented details to reconcile the
two, and flagged no contradiction — in the experimenters' own words, "LLMs
believe that all provided input is true and correct". The same models,
explicitly asked to hunt for inaccuracies, found most of them: the capacity to
verify exists, and it engages when the reading posture demands it. The posture
matters more for an agent than it ever did for a human — a human skims past
comments; a model ingests them with the same weight as the code and reasons
more fluently over prose than over configuration syntax. And the prose being
read is increasingly agent-written, at a measured error rate near one claim in
five, so a reader trusting by default is trusting a previous session of
itself. Accurate comments measurably help comprehension — the enemy is
falsehood absorbed unverified, never prose itself, which is why this rule asks
for verification, not for suspicion of volume.

## Where documentation belongs

Documentation of an entry lives in the language's native mechanism when one
exists; a comment is its destination only when none does. Where there is no
native slot the comment stops being optional — it is the only place the contract
can live, and deleting it deletes the contract. The bar inverts by stack: with a
doc mechanism, a comment restating it is redundant; without one, that same
comment IS the contract.

A slot can also be present and insufficient — capped in length, or rendered to
an audience the rationale is not for. Treat the overflow as having no slot: the
native field carries the summary, the comment carries what does not fit, and for
that remainder the comment is mandatory again.

The inversion has a volume consequence. A slot steers contract out of comments,
so a comment-heavy file there signals contract sitting in the wrong place; no
slot steers every contract into them — the standards that govern slot-less
formats set floors ("document every property") and none, anywhere, sets a
ceiling or a ratio, in either direction, and canonical shipped configurations
are majority comment by explicit design. So in a slot-less format a high
comment-to-content proportion is not itself a defect, and cannot be cut down to
one: the cut runs comment by comment against what the file already shows. A
comment restating a value visible in the lines below it is redundant, and it is
the first thing to go stale when the data changes. One carrying a constraint, a
boundary, or a consequence that appears nowhere in the file has no other home:
deleting it does not deduplicate, it destroys.

## Cleanup is bounded by the radius of what you touch

Dead code inside what you are modifying: delete it. Outside it: report, do not
touch — even when it is wrong, because matching a repository's existing patterns
is worth more than correcting them in passing. The counterpart holds too: inside
the module you improve what you touch, rather than leaving it as found because it
was not the assignment. What you see and do not touch gets REPORTED: a defect
seen and recorded nowhere is indistinguishable from one never seen, and the
boundary exists to protect the repository, not to lose the finding.

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

**Provenance is not a process trace — it is the seventh protected category.** A
ticket identifier points at a process; a source citation points at a fact that
can be re-checked. The second survives: deleting "verified against \<source\>"
from a security-sensitive list destroys the only record of how that list was
validated.

For the per-stack tables — where documentation natively lives, which stacks have
no native slot, and which checker verifies which rule — see `reference.md`.
Worked before/after pairs are in `examples.md`.
