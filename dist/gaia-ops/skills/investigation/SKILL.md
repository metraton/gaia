---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
metadata:
  user-invocable: false
  type: technique
---

# Investigation

Investigation is about understanding a problem well enough to propose
a correct solution. For the `json:contract` response format, see `agent-protocol`.

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

- Searching before checking if context already has the path
- Planning before resolving critical unknowns
- Treating your training preference as codebase convention
