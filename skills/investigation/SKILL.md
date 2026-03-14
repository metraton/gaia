---
name: investigation
description: Use when starting an investigation, analyzing existing code or infrastructure, or building findings before proposing changes
metadata:
  user-invocable: false
  type: technique
---

# Investigation Skill

Investigation is about understanding a problem well enough to propose a correct solution.
This skill defines **how to think** -- the methodology for building evidence.
For the mandatory `json:contract` response format, see `agent-protocol`.

## Phase 1: Read Injected Context

Before your first tool call, list the key anchors from your injected Project Knowledge
that you will target: paths, service names, resource IDs. This is your investigation
starting point -- name them explicitly so your first tool calls go directly to them.

Extract what you already know from injected Project Context (PC):
- Paths: `infrastructure.paths`, `orchestration.gitops.config_path`, service directories
- Tech stack: languages, frameworks, CI tools, cloud provider
- Known issues or constraints noted in PC metadata
- Resource names, cluster names, project IDs

Define what you need to know that PC does NOT already answer. Those are your unknowns.

## Phase 2: Target Known Paths

Use context anchors for focused exploration. For each path or name PC gave you:
- Read the file or directory directly -- no Glob needed
- Read 2-3 similar existing resources to understand what is already implemented
- Extract: naming conventions, directory structure, dependencies, config patterns

If PC includes an `investigation_brief`, use it to prioritize:
- which surface you own
- which adjacent surface must still be checked
- which required checks must appear in your evidence
- whether you must return a `consolidation` object in your `json:contract` block for Gaia to merge findings

## Phase 3: Discover Unknowns

Only now search for things NOT covered by context. Use Glob and Grep for unknowns
that PC could not answer.

After gathering initial evidence, resist the pull to stop at the first answer.

- **Check adjacency:** Read neighboring files, sibling modules, and related configs.
  The file next to your target often explains constraints that the target itself does not.
- **Check depth:** If you found the resource, also find what references it and what it
  references. One level up and one level down reveals integration patterns.
- **Check breadth:** Search for 2-3 more instances of the same pattern. One example is
  an anecdote; three examples are a convention.

Stop exploring when new files confirm what you already know rather than adding new information.

## Phase 4: Live State

Only if drift is suspected or the task explicitly requires runtime data.
Use CLI tools for live verification.
If you have the `fast-queries` skill, run triage first.

## Phase 5: Pattern Analysis

Apply this hierarchy — in order, without skipping levels:

**Level 1 — Codebase (always first)**
Search for 2-3 existing resources of the same type. If found → FOLLOW them. Codebase pattern wins over your training and over your domain skill. Consistency beats preference.

**Level 2 — Domain skill (fallback when no codebase pattern exists)**
If no existing pattern is found in the codebase, use your injected domain skill
(the skill in your frontmatter that is specific to your technical area — e.g.,
terraform-patterns, gitops-patterns, developer-patterns, gaia-patterns,
speckit-workflow, or whichever domain skill you have).

**Level 3 — Training best practices (last resort)**
If neither codebase nor domain skill has a pattern for what you need, use your training knowledge. Always mark the result explicitly: *"No existing pattern found — applying best practices. Recommend reviewing before merging."*

**Pattern Authority (applies at all levels):**

**COPY** — Names, paths, IDs are contracts. Match existing schema exactly.
**ALERT** — Problematic pattern → DEVIATION or CRITICAL, propose alternative, let user decide. Never silently follow or fix.
**DOCUMENT** — New discovery not in project-context. If you have the `context-updater`
skill, emit `CONTEXT_UPDATE`. Otherwise, note the discovery in your report for the
orchestrator to route.

## Phase 6: Validate Your Hypothesis

Before treating findings as fact:
- Does local code agree with project-context? If not → investigate drift first
- Unfamiliar resource, API, or behavior? → search official documentation
- Uncertain about correctness? → run one more read-only validation step

Never plan on assumptions. If in doubt, validate.

## Phase 7: Surface Options

When multiple valid approaches exist:
- List them explicitly: **Option A** (trade-offs), **Option B** (trade-offs)
- Evaluate each against existing project patterns and constraints
- Do NOT pick silently — surface them and set status: `NEEDS_INPUT`

## Phase 8: Qualify Confidence Before Proposing

Before findings feed into a plan, explicitly state:
- What is **confirmed** (seen in code, validated by CLI or docs)
- What is **assumed** (inferred but not yet validated)

If critical gaps remain → run another validation round. Never propose on shaky ground.

## Phase 9: Report Evidence

Populate the `evidence_report` in your `json:contract` block using the schema
and field definitions from `agent-protocol`. Minimum expectations for investigations:

- Always populate `patterns_checked` and `files_checked` for local/code work
- Populate `commands_run` and `key_outputs` whenever you touched live state or ran diagnostics
- Populate `cross_layer_impacts` whenever the issue crosses surfaces
- Use `[]` when a field truly does not apply

The goal is not verbosity. The goal is evidence that another agent or the user can verify quickly.

## Phase 10: Consolidation

When consolidation is required (see `agent-protocol` for triggers and the full
`consolidation_report` schema), your response must help Gaia merge parallel work.
Separate confirmed findings from suspected ones, state surface ownership, identify
blockers, and name who should continue. Put this in the `consolidation_report`
object -- do not leave it for the orchestrator to infer from prose.

## Anti-Patterns

- Running Glob or Grep across the entire repo before checking if Project Context already has the path
- Searching before knowing what question you're trying to answer
- Planning before all critical unknowns are resolved
- Picking an approach without surfacing alternatives
- Treating your training's preference as the correct codebase pattern
- Assuming instead of validating with a read-only check
