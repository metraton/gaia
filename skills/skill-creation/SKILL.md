---
name: skill-creation
description: Use when creating a new skill, improving an existing skill, or deciding what a skill should contain and how it should be structured
user-invocable: false
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
Iron Law → Mental Model → Rules → Red Flags → Rationalization table → Anti-patterns

Discipline skills enforce behavior agents will try to avoid. Every rationalization you don't name explicitly is a loophole.

**Checklist:**
- [ ] Iron Law in a code block at the top — the rule, stated bluntly
- [ ] Mental model explains WHY (not just what not to do)
- [ ] Red Flags section — specific thoughts that mean STOP
- [ ] Rationalization table — every known excuse + the reality
- [ ] Anti-patterns with real examples

### Technique
Overview (core principle + when to use) → Process (numbered steps) → Anti-patterns

### Reference
Quick-scan table at top → Examples → Edge cases / special rules

### Domain
Conventions (naming, structure) → Examples/snippets → Key rules → links to reference files

### Protocol
State machine / flow → Mandatory format → State transitions → Error handling

---

## Step 3: Write the description field (CSO)

The description determines when the agent reads the skill. It must contain **triggering conditions only** — never summarize the process.

If the description says what the skill does step by step, the agent follows the description and skips reading the content.

```yaml
# ❌ BAD — summarizes process, agent skips content
description: Defensive command execution - timeout protection, pipe avoidance, safe shell patterns

# ✅ GOOD — triggering conditions only
description: Use when executing any bash command, cloud CLI, or shell operation
```

---

## Step 4: Respect the token budget

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
