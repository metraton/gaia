---
name: brief-spec
description: Use when the user wants to create a brief or spec for a feature before planning
metadata:
  user-invocable: false
  type: technique
---

# Brief Spec

Conversational brief creation. The orchestrator loads this inline to
co-create a brief with the user before dispatching to gaia-planner.

## Process

1. **Size the work**

   | Size | Signal | Questions |
   |------|--------|-----------|
   | S | Bug fix, config tweak, single-file | 0-1 |
   | M | Feature, endpoint, integration | 2-3 |
   | L | Project, multi-agent, cross-surface | 4-6 |

   For S: skip brief, tell the user to describe what they want and
   dispatch directly to the appropriate agent.

3. **Ask questions** (M/L) -- Target gaps, not completeness:
   - What problem does this solve?
   - What constraints matter? (cloud, performance, security, timeline)
   - How will we know it works? (acceptance criteria)
   - What is explicitly NOT in scope?

   One question per round via AskUserQuestion. Stop when you can write
   a problem statement without guessing.

4. **Write brief.md** -- Use the structure below. Write to:
   `.claude/project-context/briefs/{feature-name}/brief.md`
   where `{feature-name}` is a kebab-case slug.

## Brief Structure

```markdown
---
status: draft
---

# [Feature Name]

## Objective
[1-3 sentences: what problem, why now, who benefits]

## Context
[Project constraints relevant to this feature]

## Approach
[High-level strategy, not implementation details. 3-5 sentences max]

## Acceptance Criteria
- AC-1: [human description] | `verify command`
- AC-2: [human description] | `verify command`

## Milestones (M/L features only)
- M1: [name] -- [what is shippable after this]
- M2: [name] -- [what is shippable after this]

## Out of Scope
[Explicit boundaries -- what this feature does NOT include]
```

## Acceptance Criteria Rules

- Every AC has a human description AND a verify command
- Verify commands are binary: pass (exit 0) or fail (non-zero)
- Vague ACs get pushed back: "Fast means what? Under 200ms p95?"

## After Brief

Present the full brief. Ask: "Does this capture what you want?"
When confirmed, suggest dispatching to gaia-planner to create a plan.
