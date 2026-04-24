# Skill Creation -- Reference

Detailed guidance on writing style, tone by type, and design philosophy. Read on-demand when crafting or reviewing skill content.

## Write for Judgment, Not Compliance

A skill that says "ALWAYS do X" is a rule. Rules get skipped the moment the agent encounters a situation where X seems unnecessary. A skill that explains *what goes wrong when you skip X* forms judgment. Judgment holds even in situations the skill never anticipated.

The test: for each rule or step you write, ask -- if the agent saw enough real examples of this going wrong, would it reach the same conclusion on its own? If yes, you're capturing genuine wisdom. If no, it's probably an arbitrary preference that needs more context before it can guide decisions.

This is why the investigation skill doesn't say "INVESTIGATE FIRST. ALWAYS. NO EXCEPTIONS." It says: *"Every codebase is a record of accumulated decisions... The first 2-3 files you read define whether your solution fits or fights the project."* The agent understands the stakes. The behavior follows.

Every line in a skill competes for weight in the LLM's reasoning. A rule without context carries almost no weight -- the model has no reason to prioritize it over competing signals. An explanation with consequences carries enough weight to influence decisions even under pressure. This is why conciseness matters: a verbose skill dilutes its own weight. Every line should earn its place by adding reasoning the model can use.

## Tone by Type

**Discipline** works best when the Iron Law is blunt and a reasoned paragraph follows explaining what breaks when you violate it. Command-execution's mental model ("When you reach for a pipe, you have not looked for the flag yet") does more work than a dozen capitalized warnings because it reframes the decision point itself.

**Technique** should read like a mentor sharing experience. Not "do step 1, step 2, step 3" but "when you encounter X, the thing that matters most is Y, because Z." The agent needs to internalize the priority, not memorize the sequence.

**Domain** skills guide discovery of the project's conventions, not dictate a generic structure. The codebase is the source of truth; the skill is a reference that helps the agent find and interpret what's already there.

**Reference** is where tone matters least and accuracy matters most. Tables, classifications, format specs. Get the content right.

**Protocol** needs precision in its state machines and formats, but transitions should explain why they exist. An agent that understands why APPROVAL_REQUEST precedes IN_PROGRESS for T3 operations will handle edge cases the protocol didn't enumerate.

## Connection to Design Philosophy

The gaia-patterns Workflow Design Philosophy captures this directly: *"Be positive -- describe what to do, not what to avoid"* and *"Allow discovery -- agent reaches conclusions empirically."* These principles apply directly to skill writing. A skill full of prohibitions ("never do X", "do NOT do Y") trains avoidance, not understanding. A skill that describes the better path and explains why it's better trains judgment that generalizes.
