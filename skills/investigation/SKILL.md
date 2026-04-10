---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
metadata:
  user-invocable: false
  type: technique
---

# Investigation

Every codebase is a record of accumulated decisions. Investigation
is not a prerequisite you rush through — it is the most important part.
The first 2-3 files you read define whether your solution fits or
fights the project.

## Phase 1: Start From Injected Context

Before your first tool call, extract anchors from your injected
Project Context: paths, service names, resource IDs. These are
your starting point — go directly to them.

Define what you need to know that the context does NOT answer.
Those are your unknowns.

## Phase 2: Explore Known Paths

For each path or name from context:
- Read the file or directory directly — no Glob needed
- Read 2-3 similar existing resources to understand conventions
- Extract: naming patterns, directory structure, dependencies

If context includes an `investigation_brief`, use it to prioritize
your surface, adjacent surfaces, and required checks.

## Phase 3: Discover Unknowns

Search only for things NOT covered by context. Use Glob and Grep.

After initial evidence, check adjacency:
- **Neighbors:** Files next to your target often explain constraints
- **References:** What references this resource? What does it reference?
- **Breadth:** Find 2-3 instances of the same pattern. One example is
  anecdote; three are convention.

Stop when new files confirm what you already know.

## Phase 4: Live State

Only if drift is suspected or the task requires runtime data.
If you have the `fast-queries` skill, run triage first.

## Phase 5: Pattern Hierarchy

Apply in order — do not skip levels:

1. **Codebase first** — Find 2-3 existing resources of the same type.
   If found, follow them. Consistency beats preference.
2. **Domain skill** — If no codebase pattern, use your domain skill
   (terraform-patterns, gitops-patterns, etc.)
3. **Training knowledge** — Last resort. Mark explicitly:
   *"No existing pattern found — applying best practices."*

When following patterns: **COPY** names/paths exactly.
When a pattern is problematic: **ALERT** as DEVIATION, propose alternative.

## Phase 6: Validate Before Proposing

- Does code agree with project-context? If not → investigate drift
- Uncertain about correctness? → one more read-only validation
- Multiple valid approaches? → list options, set status `NEEDS_INPUT`

Separate what is **confirmed** (seen in code, validated) from what
is **assumed** (inferred). Never propose on assumptions.

## Anti-Patterns

- **Searching before reading context.** Your injected context already
  has paths and names. Searching for what you have wastes tool calls
  and produces noise that obscures real unknowns.
- **Planning before resolving unknowns.** A plan built on assumptions
  collapses when reality disagrees. Find contradictions early.
- **Treating training knowledge as codebase convention.** The codebase
  says "we do Y" -- consistency within the project matters more than
  abstract best practice from your training.
- **Skipping investigation because the prompt is specific.** The
  orchestrator doesn't see the codebase. You do. Instructions are
  intent; the codebase is truth. When they contradict, the codebase wins.
- **Creating files before reading existing examples.** Without seeing
  how the project structures similar resources, you'll produce
  something that works but looks foreign in every review.
