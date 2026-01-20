# Skills System

Skills are on-demand knowledge modules loaded based on context triggers. They reduce token duplication and improve maintainability.

## Architecture

```
.claude/skills/
├── workflow/          # How to work (process patterns)
│   ├── investigation/
│   ├── approval/
│   └── execution/
└── domain/            # What patterns to use (technical patterns)
    ├── terraform-patterns/
    ├── gitops-patterns/
    └── universal-protocol/
```

## Skill Categories

| Category | Purpose | When Loaded | Example |
|----------|---------|-------------|---------|
| **Workflow** | Process/methodology | By workflow phase | investigation-skill: how to investigate before acting |
| **Domain** | Technical patterns | By keywords in task | terraform-patterns: HCL patterns for this project |

## Trigger Mechanism

Skills are loaded when:
1. **Workflow phase changes** (automatic) - investigation → approval → execution
2. **Task contains trigger keywords** (see `skill-triggers.json`)

## Skill Structure

Each skill is a directory containing:

```
skill-name/
└── SKILL.md          # Core skill content
```

### SKILL.md Format

```markdown
---
name: skill-name
description: Brief description
triggers: [keyword1, keyword2]  # For domain skills
phase: start|investigation|approval|execution  # For workflow skills
---

# Skill Name

[Content that agents will read when skill is loaded]
```

## How Skills Work

1. **Hook intercepts Task tool call**
   ```python
   # pre_tool_use.py
   if is_project_agent:
       skills = skill_loader.load_skills(task_prompt, workflow_phase)
   ```

2. **skill_loader.py determines which skills to load**
   ```python
   # Load workflow skill based on phase
   if phase == "start":
       load("workflow/investigation")

   # Load domain skills based on keywords
   if "terraform" in prompt:
       load("domain/terraform-patterns")
   ```

3. **Skills are injected into prompt**
   ```
   # Project Context (Auto-Injected)
   {...context...}

   # Active Skills
   ## investigation-skill
   [content of investigation SKILL.md]

   ## terraform-patterns
   [content of terraform-patterns SKILL.md]

   ---
   # User Task
   {original prompt}
   ```

## Benefits

| Metric | Before Skills | After Skills |
|--------|---------------|--------------|
| Token duplication | ~6000 tokens repeated in 4 agents | ~1500 tokens in skills, loaded once |
| Agent size | ~280 lines each | ~180 lines each |
| Maintenance | Update 4 files | Update 1 skill |
| Consistency | Can drift | Guaranteed consistent |

## Usage Example

**User request:** "Create a new VPC in terraform"

**Skills loaded:**
1. `workflow/investigation` (phase: start)
2. `domain/terraform-patterns` (trigger: "terraform")
3. `domain/universal-protocol` (auto_load for project agents)

**Agent receives:**
- Full project context (~3000 tokens)
- Investigation skill (~500 tokens) - how to discover patterns first
- Terraform patterns skill (~600 tokens) - HCL patterns for this project
- Universal protocol skill (~400 tokens) - AGENT_STATUS format, Security Tiers

**Total:** ~4500 tokens vs ~6000 without skills

## Skill Development Guidelines

### Do's
- ✅ Keep skills focused and specific
- ✅ Use concrete examples
- ✅ Include decision trees when applicable
- ✅ Update skills when patterns change

### Don'ts
- ❌ Duplicate information across skills
- ❌ Make skills too generic (defeats the purpose)
- ❌ Include project-specific credentials/secrets
- ❌ Create skills for one-time operations

## Testing Skills

Test that skills load correctly:

```bash
# Test skill loader with agent and prompt
python3 .claude/hooks/modules/skills/skill_loader.py \
  --test \
  --prompt "terraform apply vpc" \
  --agent "terraform-architect"

# Expected output:
# Loaded skills:
# - workflow/investigation (phase: start)
# - domain/terraform-patterns (trigger: terraform)
# - domain/universal-protocol (auto_load)
```

## Version History

- v1.0 (2026-01-15): Initial skills system with workflow + domain categories
- v1.1 (2026-01-15): Added universal-protocol skill for all project agents
