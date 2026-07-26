# Skill Creation -- Reference

Detailed guidance on writing style, and the mechanics of the teaching eval. Read on-demand when crafting, reviewing, or validating skill content.

## Tone by Type

**Reference** is where tone matters least and accuracy matters most: every field name, tag, and line reference must be checked against the source file, because a Reference skill is read as ground truth -- a wrong name propagates into envelopes that fail validation.

**Protocol** needs precision in its state machines and formats, but transitions should explain why they exist. An agent that understands why APPROVAL_REQUEST precedes IN_PROGRESS for T3 operations will handle edge cases the protocol didn't enumerate.

A skill full of prohibitions ("never do X", "do NOT do Y") trains avoidance, not understanding. A skill that describes the better path and explains why it's better trains judgment that generalizes -- this is the same test Step 3 of `SKILL.md` applies to every rule and trap.

## The teaching eval -- mechanics

The mechanics of Step 6 of `SKILL.md`. Run one eval per hypothesis; an eval that tests everything at once cannot tell you which part failed.

**Write the rubric first, as a file.** Each criterion states an observable outcome and the evidence that would satisfy it ("names the two entry cases", "produces a runnable artifact without being told the tool"). Commit it, or at least write it out, before dispatching -- a rubric still in your head is one you will edit while reading the answer.

**Build the prompt from the reader, not from the skill.** Take the sentence the real reader would actually type: their goal, their vocabulary, their level of vagueness. Strip every name the skill owns -- the skill's own name, its file paths, its tool and script names, its jargon. What remains tests two things at once: whether the description triggers a load, and whether the content carries the reader from vague intent to result. Dispatch it to a fresh agent with no prior context from your authoring session; a warm agent has already read the skill and can only confirm you.

**Establish the baseline where one exists.** For a rewrite, the comparison is the previous version's readers -- ideally the same prompt against the old skill, otherwise the artifacts real readers produced under it. Absolute quality is not the metric; the delta is. A skill that answers well but no better than its predecessor did not improve.

**Keep the rubric falsifiable in both directions.** A rubric that can only confirm is a ceremony. Two real payoffs, both worth having: an eval can find a genuine defect (a section every reader skipped -- the fix branched the first read by case), and it can refute the author (a "missing mode" a fresh agent derived unaided, which would otherwise have been documented as absent). Deciding in advance what a failure looks like is what buys the second one.

**The coverage test, for Domain skills describing a real system.** Enumerate the system's mechanics from the code, not from the skill or from memory -- the behaviors, flags, and special cases it genuinely has. Then walk the list and ask, per mechanic, which stated principle makes it a consequence. A mechanic with no such principle is the finding: the skill is missing a principle, and adding the mechanic as another table row hides the gap instead of closing it. One pass of this turned 7 principles into 9 and removed rows that the new principles subsumed.
