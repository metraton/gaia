---
name: skill-creation
description: Use when creating a new skill, improving an existing skill, or deciding what a skill should contain and how it should be structured
metadata:
  user-invocable: false
  type: technique
---

# Skill Creation

## What is a skill?

Injected procedural knowledge — the "how" for agents. The agent brings identity and domain knowledge. The skill brings process and protocol. They never duplicate each other.

## Step 1: Choose the type

Type determines structure. Choose before writing anything.

| Type | Purpose | When it applies |
|------|---------|-----------------|
| **Discipline** | Enforces rules the agent will rationalize around under pressure | command-execution, execution |
| **Technique** | How to think about or approach a class of problem | investigation, approval |
| **Reference** | Lookup tables, classifications, format specifications | security-tiers, output-format, git-conventions |
| **Domain** | Project-specific patterns for a technical area | terraform-patterns, gitops-patterns |
| **Protocol** | System operating contract — state machines, mandatory formats | agent-protocol |

## Step 2: Apply the type structure

### Discipline
Iron Law → Mental Model → Rules → Traps → Anti-patterns

Discipline skills enforce behavior agents will try to avoid. Every trap you don't name explicitly is a loophole.

**Checklist:**
- [ ] Iron Law in a code block at the top — the rule, stated bluntly
- [ ] Mental model explains WHY (not just what not to do)
- [ ] Rules — the concrete constraints
- [ ] Traps table — "If you're thinking X → the reality is Y" (fuses red flags + rationalizations)
- [ ] Anti-patterns with real code examples

### Technique
Overview (core principle + when to use) → Process (numbered steps) → Anti-patterns

### Reference
Quick-scan table at top → Examples → Edge cases / special rules

### Domain
Conventions (naming, structure) → Examples/snippets → Key rules → links to reference files

### Protocol
State machine / flow → Mandatory format → State transitions → Error handling

---

## Step 3: Write for judgment, not compliance

A skill that says "ALWAYS do X" is a rule. Rules get skipped the moment the agent encounters a situation where X seems unnecessary. A skill that explains *what goes wrong when you skip X* forms judgment. Judgment holds even in situations the skill never anticipated.

The test: for each rule or step you write, ask — if the agent saw enough real examples of this going wrong, would it reach the same conclusion on its own? If yes, you're capturing genuine wisdom. If no, it's probably an arbitrary preference that needs more context before it can guide decisions.

This is why the investigation skill doesn't say "INVESTIGATE FIRST. ALWAYS. NO EXCEPTIONS." It says: *"Every codebase is a record of accumulated decisions... The first 2-3 files you read define whether your solution fits or fights the project."* The agent understands the stakes. The behavior follows.

Every line in a skill competes for weight in the LLM's reasoning. A rule without context carries almost no weight — the model has no reason to prioritize it over competing signals. An explanation with consequences carries enough weight to influence decisions even under pressure. This is why conciseness matters: a verbose skill dilutes its own weight. Every line should earn its place by adding reasoning the model can use.

### Tone by type

**Discipline** works best when the Iron Law is blunt and a reasoned paragraph follows explaining what breaks when you violate it. Command-execution's mental model ("When you reach for a pipe, you have not looked for the flag yet") does more work than a dozen capitalized warnings because it reframes the decision point itself.

**Technique** should read like a mentor sharing experience. Not "do step 1, step 2, step 3" but "when you encounter X, the thing that matters most is Y, because Z." The agent needs to internalize the priority, not memorize the sequence.

**Domain** skills guide discovery of the project's conventions, not dictate a generic structure. The codebase is the source of truth; the skill is a reference that helps the agent find and interpret what's already there.

**Reference** is where tone matters least and accuracy matters most. Tables, classifications, format specs. Get the content right.

**Protocol** needs precision in its state machines and formats, but transitions should explain why they exist. An agent that understands why REVIEW precedes IN_PROGRESS for T3 operations will handle edge cases the protocol didn't enumerate.

### Connecting to design philosophy

The gaia-patterns Workflow Design Philosophy already captures this: *"Be positive -- describe what to do, not what to avoid"* and *"Allow discovery -- agent reaches conclusions empirically."* These principles apply directly to skill writing. A skill full of prohibitions ("never do X", "do NOT do Y") trains avoidance, not understanding. A skill that describes the better path and explains why it's better trains judgment that generalizes.

---

## Step 4: Write the description field (CSO)

The description determines when the agent reads the skill. It must contain **triggering conditions only** — never summarize the process.

If the description says what the skill does step by step, the agent follows the description and skips reading the content.

```yaml
# ❌ BAD — summarizes process, agent skips content
description: Defensive command execution - timeout protection, pipe avoidance, safe shell patterns

# ✅ GOOD — triggering conditions only
description: Use when executing any bash command, cloud CLI, or shell operation
```

---

## Step 5: Respect the line budget

| Injection method | Budget | Reason |
|-----------------|--------|--------|
| Frontmatter (always loaded) | < 100 lines | Loaded on every agent call |
| On-demand (read from disk) | < 500 lines | Loaded only when explicitly needed |

Heavy reference material → move to `reference.md` (read on-demand).
Concrete examples → move to `examples.md` (read on-demand).
Executable tools → `scripts/` directory.

```
skill-name/
├── SKILL.md          ← main content (always loaded)
├── reference.md      ← heavy docs (on-demand)
├── examples.md       ← concrete examples (on-demand)
└── scripts/          ← executable tools
```

---

## When to create vs update

**Create new skill:**
- Distinct behavioral concern not covered by any existing skill
- Domain knowledge living inline in an agent that applies to multiple agents

**Update existing skill:**
- Agent ignores a rule the skill already defines → strengthen with Red Flags
- Skill is missing a type-appropriate section

**Do NOT create a skill — put elsewhere:**
- Project-specific config → CLAUDE.md or agent inline
- Single-agent-only behavior → keep inline in that agent
- Knowledge the LLM covers well from training → not needed

---

## Anti-Patterns

**❌ Description summarizes process** — agent follows the description and skips reading the skill body

**❌ Discipline skill without Red Flags** — agents are smart; they rationalize. Every unnamed loophole gets used.

**❌ Too generic** — "be careful with commands" teaches nothing. Skills need specific, concrete rules.

**❌ Duplicates agent content** — two sources of truth both become stale. Pick one place.

**❌ Single responsibility violated** — if a skill needs to cover two distinct behaviors, split it into two skills.
