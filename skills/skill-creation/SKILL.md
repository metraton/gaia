---
name: skill-creation
description: Use when creating a new skill, improving an existing skill, or deciding what a skill should contain and how it should be structured
---

# Skill Creation

## What is a skill?

Injected procedural knowledge -- the "how" for agents. The agent brings identity and domain knowledge. The skill brings process and protocol. They never duplicate each other.

## Step 1: Choose the type

Type determines structure. Choose before writing anything.

| Type | Purpose | When it applies |
|------|---------|-----------------|
| **Discipline** | Enforces rules the agent will rationalize around under pressure | command-execution, execution |
| **Technique** | How to think about or approach a class of problem | investigation, approval |
| **Reference** | Lookup tables, classifications, format specifications | security-tiers, fast-queries, git-conventions |
| **Domain** | Project-specific patterns for a technical area | gaia-patterns |
| **Protocol** | System operating contract -- state machines, mandatory formats | agent-protocol |

## Step 1.5: Situate the skill in its flow

With the type chosen, place the skill before you structure it: where it lives in a flow, and what it can affect. Treat these as one gate with two coupled facets -- position is what sets blast radius.

- **Position.** Is the skill *standalone* (a self-contained process run from a clean start), or does it run *mid-flow* -- sometimes after many skills have already executed -- with an upstream that hands it state and a downstream that consumes what it emits?
- **Blast radius.** What does the skill's output touch or trigger? A standalone technique's reach ends at its own result; a mid-flow skill's is amplified, because its output becomes the next step's input.

The consequence is why the gate exists. A mid-flow skill inherits state and assumptions from upstream; write it as though it started clean and it either redoes work already settled or emits what the next step cannot consume -- and because that output is consumed downstream, the break propagates instead of staying local. A wrong position mis-scopes everything the skill's process assumes and produces.

The gate is **conditional on the type from Step 1**, not universal:

- **Protocol** -- load-bearing: position is the substrate, since a protocol cannot sequence its state machine without knowing where in the larger flow it stands, what the prior turn settled, and what the next needs.
- **Technique / Domain** -- relevant only when the skill runs inside a pipeline; skip it for a self-contained one.
- **Reference** -- irrelevant: a lookup table has no position in a flow. Forcing this gate on a pure Reference skill is itself the "generic without consequence" anti-pattern.

When position is load-bearing **and** the flow is not determinable from the context you were given, ask the user where the skill sits before writing -- do not guess; when the context fixes it or the type makes it irrelevant, do not ask.

This is *frame before action* applied to the skill you are placing: a unit of work has a past that set it up and a future that consumes it. See `agent-protocol` ("Frame before action") for that principle at the agent-turn level.

## Step 2: Apply the type structure

**Discipline:** Iron Law -> Mental Model -> Rules -> Traps -> Anti-patterns. Each trap and anti-pattern names a *principle of failure* -- one row per failure mode, not one row per concrete instance. If three rows are subcases of the same principle, one row that names the principle teaches more than three rows that enumerate.

**Technique:** Overview (core principle + when to use) -> Process (numbered steps) -> Anti-patterns.

**Reference:** Quick-scan table at top -> Examples -> Edge cases / special rules.

**Domain:** Conventions (naming, structure) -> Examples/snippets -> Key rules -> links to reference files.

**Protocol:** State machine / flow -> Mandatory format -> State transitions -> Error handling.

## Step 2.5: Open self-contained

The first sentence states what the skill IS and does, in its own terms. Never open by contrast ("this is NOT X"): that forces the reader to already know X, so the skill stops being self-contained. If you must disambiguate from a sibling skill, do it *after* the self-definition and frame it as a pointer for continuation ("for the universal envelope see agent-protocol"), not as the definition.

Cross-referencing another skill for *flow continuation* (handoff, "see X for the next step") is correct and avoids duplication. Defining your skill *by contrast* with another ("unlike X, this...") is not -- the first keeps you self-contained, the second couples your meaning to a skill the reader may not have loaded.

See `examples.md` for a before/after of each prose failure mode.

## Step 3: Write for judgment, not compliance

A rule without context ("ALWAYS do X") carries almost no weight in the LLM's reasoning -- the model has no reason to prioritize it over competing signals. An explanation with consequences carries enough weight to influence decisions even under pressure. Every line competes for attention; earn each one with reasoning the model can use.

The dual test: for each row in a Traps or Anti-patterns table, ask -- is this a separate principle, or is it the same principle as a row already there? If the latter, fold it in. Specificity competes with itself: five rows that share a principle each carry one-fifth the weight of one row that names the principle, because the model spreads attention across them.

The test: for each rule, ask -- if the agent saw enough examples of this going wrong, would it reach the same conclusion? If yes, you are capturing genuine wisdom. If no, it needs more context.

For detailed guidance on tone by type, see `reference.md`.

## Step 4: Write the description field

Triggering conditions only -- describing the process causes the agent to follow the description and skip reading the content. See `agent-creation/SKILL.md` Step 4 for the bad/good example pattern.

## Step 5: Respect the line budget

| Injection method | Budget | Reason |
|-----------------|--------|--------|
| Frontmatter (always loaded) | < 100 lines | Loaded on every agent call |
| On-demand (read from disk) | < 500 lines | Loaded only when explicitly needed |

Size is not the only criterion. The canonical contract/schema that IS the skill's purpose belongs in SKILL.md even when large; `reference.md` holds the *deep mechanics* (internals, walkthroughs, edge cases) that a reader needs only occasionally. Ask "is this the primary thing the skill exists to state?" before "is this big?".

The deciding criterion is the READER. What someone holding the idea needs in order to reason with it belongs in SKILL.md; what only an implementer needs -- field-by-field schemas, build cycles, invariant tables -- belongs in `reference.md`. Serving both readers from one file is what makes a skill fail to teach: one that mixed them produced authors using 10% of its vocabulary, and splitting them cut it from 404 to 274 lines while an eval showed it taught more.

Heavy reference material -> `reference.md` (on-demand). Concrete examples -> `examples.md`. Executable tools -> `scripts/`.

```
skill-name/
├── SKILL.md          <- main content (always loaded)
├── reference.md      <- heavy docs (on-demand)
├── examples.md       <- concrete examples (on-demand)
└── scripts/          <- executable tools
```

## Step 6: Verify it teaches

Every step above tests the writing; none tests whether a reader learns. Fix the rubric BEFORE seeing any answer -- written afterwards it only rationalizes what you got. Then hand a fresh agent a vague prompt in the real reader's voice, naming neither the skill nor any tool: if it finds the skill unprompted the trigger works, and if it reaches the result without being handed a tool, the skill taught. Where a previous version exists, measure against its readers -- the question is not "did it answer well?" but "does it beat the baseline?".

The rubric must be able to fail in both directions, and both directions pay: one eval exposed a section no reader ever opened (fixed by branching the first read), and another falsified the author's own hypothesis -- a "missing mode" a fresh agent derived unaided, which would otherwise have shipped as an invented section.

For a Domain skill describing a real system, add the coverage test: extract what the system actually does from the code, then check that each mechanic is a consequence of some stated principle. A mechanic no principle explains means a principle is missing, not a row -- that test took one skill from 7 principles to 9. See `reference.md` for how to build the prompt, the baseline, and the coverage extraction.

## When to create vs update

**Create new skill:** Distinct behavioral concern not covered by existing skills. Domain knowledge inline in an agent that applies to multiple agents.

**Update existing skill:** Agent ignores a rule the skill already defines -> strengthen with traps. Skill is missing a type-appropriate section.

**Put elsewhere:** Project-specific config -> CLAUDE.md or agent inline. Single-agent-only behavior -> keep inline. Knowledge the LLM covers well from training -> not needed.

**When creating a new skill:** Also update `skills/README.md` to add the new skill to the index. Load Skill('readme-writing') to do this correctly.

## Anti-Patterns

- **Description summarizes process** -- agent follows the description and skips reading the skill body.
- **Discipline without traps OR with too many traps** -- agents rationalize around bare rules; agents also dilute their attention across overlapping traps. A trap row earns its place by capturing a failure mode the existing rows do not. If you can describe a new trap by adjusting the wording of an existing trap, the existing row is what needed strengthening, not a new neighbor.
- **Generic without consequence** -- "be careful with commands" teaches nothing because it has no consequence attached. The fix is naming what goes wrong when you violate the rule, not enumerating instances of the rule. A principle with one consequence outweighs five examples without one.
- **Duplicates agent content** -- two sources of truth both become stale; pick one place.
- **Single responsibility violated** -- if a skill covers two distinct behaviors, split it.
- **Opening by negation** -- defining the skill by what it is not ("this is NOT agent-protocol"). The reader must already hold the other concept to parse yours. State what it IS first; disambiguate after.
- **Phantom / unanchored reference** -- naming a field, tag, or module that does not exist by that name in code, OR anchoring to line numbers that drift on every edit. Anchor to symbols (`activate_db_pending_by_prefix`), not line ranges (`approval_grants.py:1679-1955`) -- a symbol survives edits, a line number does not. For Reference skills especially, every named artifact must be verifiable -- cite the file and symbol. Verify before you assert.
- **Quoted implementation** -- pasting another file's body into a skill. The paste is not the implementation, it is a *claim* about it, and a claim with no invalidation trigger: the commit that falsifies it edits the other file and has no reason to visit the skill, so it rots in place while still reading as authoritative. Measured -- the consent adapter quoted a `break` loop that was removed the same day the skill was written, and the skill kept asserting the old semantics against a sibling that had been updated. Name the symbol and state the contract in prose; the symbol's docstring owns the detail, and the reader who needs the body opens it there. An interface example the skill itself OWNS is legitimate -- a call shape, a label format, an envelope the skill defines -- because the skill is that fact's source and nothing else can falsify it. A copy of another file's implementation never is. `skills/code-standards` already forbids this shape for code comments ("Comment that points outward"); this extends the same norm to the substrate agents actually load.
- **Single-case instruction presented as universal** -- an instruction written from one case reads as covering every case, so the reader of the other case obeys a wrong instruction correctly. One skill paid for this three times: "read `data/` first" (right when modifying, false when creating, where no `data/` exists yet), a cycle that started at "vague idea" with no door for the most frequent entry, and a crucial warning that lived in only one of its documents. If the process has more than one entry, the first instruction branches by case -- name the doors.
- **Inflated prose** -- restates the heading or hedges without adding a decision the reader can act on. If removing the sentence does not change what the agent does, cut it.
