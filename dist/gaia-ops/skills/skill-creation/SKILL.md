---
name: skill-creation
description: Use when creating a new skill, improving an existing skill, or deciding what a skill should contain and how it should be structured
metadata:
  user-invocable: false
  type: technique
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
| **Domain** | Project-specific patterns for a technical area | terraform-patterns, gitops-patterns |
| **Protocol** | System operating contract -- state machines, mandatory formats | agent-protocol |

## Step 2: Apply the type structure

**Discipline:** Iron Law -> Mental Model -> Rules -> Traps -> Anti-patterns. Every trap you leave unnamed is a loophole.

**Technique:** Overview (core principle + when to use) -> Process (numbered steps) -> Anti-patterns.

**Reference:** Quick-scan table at top -> Examples -> Edge cases / special rules.

**Domain:** Conventions (naming, structure) -> Examples/snippets -> Key rules -> links to reference files.

**Protocol:** State machine / flow -> Mandatory format -> State transitions -> Error handling.

## Step 3: Write for judgment, not compliance

A rule without context ("ALWAYS do X") carries almost no weight in the LLM's reasoning -- the model has no reason to prioritize it over competing signals. An explanation with consequences carries enough weight to influence decisions even under pressure. Every line competes for attention; earn each one with reasoning the model can use.

The test: for each rule, ask -- if the agent saw enough examples of this going wrong, would it reach the same conclusion? If yes, you are capturing genuine wisdom. If no, it needs more context.

For detailed guidance on tone by type (discipline, technique, domain, reference, protocol) and connection to the gaia-patterns design philosophy, see `reference.md`.

## Step 4: Write the description field

The description determines when the agent reads the skill. It contains **triggering conditions only** -- describing the process causes the agent to follow the description and skip reading the content.

```yaml
# Bad -- summarizes process, agent skips content
description: Defensive command execution - timeout protection, pipe avoidance, safe shell patterns

# Good -- triggering conditions only
description: Use when executing any bash command, cloud CLI, or shell operation
```

## Step 5: Respect the line budget

| Injection method | Budget | Reason |
|-----------------|--------|--------|
| Frontmatter (always loaded) | < 100 lines | Loaded on every agent call |
| On-demand (read from disk) | < 500 lines | Loaded only when explicitly needed |

Heavy reference material -> `reference.md` (on-demand). Concrete examples -> `examples.md`. Executable tools -> `scripts/`.

```
skill-name/
├── SKILL.md          <- main content (always loaded)
├── reference.md      <- heavy docs (on-demand)
├── examples.md       <- concrete examples (on-demand)
└── scripts/          <- executable tools
```

## When to create vs update

**Create new skill:** Distinct behavioral concern not covered by existing skills. Domain knowledge inline in an agent that applies to multiple agents.

**Update existing skill:** Agent ignores a rule the skill already defines -> strengthen with traps. Skill is missing a type-appropriate section.

**Put elsewhere:** Project-specific config -> CLAUDE.md or agent inline. Single-agent-only behavior -> keep inline. Knowledge the LLM covers well from training -> not needed.

## Anti-Patterns

- **Description summarizes process** -- agent follows the description and skips reading the skill body.
- **Discipline without traps** -- agents rationalize around rules; every unnamed loophole gets used.
- **Too generic** -- "be careful with commands" teaches nothing; skills need specific, concrete rules.
- **Duplicates agent content** -- two sources of truth both become stale; pick one place.
- **Single responsibility violated** -- if a skill covers two distinct behaviors, split it.
