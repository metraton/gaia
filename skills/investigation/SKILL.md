---
name: investigation
description: How to diagnose problems, analyze patterns, and build reliable findings
user-invocable: false
type: core
---

# Investigation Skill

Investigation is about understanding a problem well enough to propose a correct solution.
**How to search** is defined in `agent-protocol`. This skill defines **how to think**.

## 1. Decompose First

Before searching anything, define what you need to know:
- What is the expected state vs what might be wrong or missing?
- What are the knowns (injected context) vs the unknowns?
- What evidence would confirm or disprove my hypothesis?

Starting a search without a clear question produces noise, not findings.

## 2. Gather Evidence

Follow the local-first order from `agent-protocol`. For each area of investigation:
- Read 2-3 similar existing resources to understand what is already implemented
- Extract: naming conventions, directory structure, dependencies, config patterns

## 3. Pattern Analysis

Compare what you find against your injected domain skill:
- `terraform-patterns` — infrastructure
- `gitops-patterns` — Kubernetes/Flux
- `developer-patterns` — application code (when available)

Apply **Pattern Authority**:

**FOLLOW** — Codebase pattern wins over your training. Consistency beats preference.
**COPY** — Names, paths, IDs are contracts. Match existing schema exactly.
**ALERT** — Problematic pattern → DEVIATION or CRITICAL, propose alternative, let
user decide. Never silently follow or fix.
**DOCUMENT** — New discovery not in project-context → CONTEXT_UPDATE per `context-updater`.

## 4. Validate Your Hypothesis

Before treating findings as fact:
- Does local code agree with project-context? If not → investigate drift first
- Unfamiliar resource, API, or behavior? → search official documentation
- Uncertain about correctness? → run one more read-only validation step

Never plan on assumptions. If in doubt, validate.

## 5. Surface Options

When multiple valid approaches exist:
- List them explicitly: **Option A** (trade-offs), **Option B** (trade-offs)
- Evaluate each against existing project patterns and constraints
- Do NOT pick silently — surface them and set status: `NEEDS_INPUT`

## 6. Qualify Confidence Before Proposing

Before findings feed into a plan, explicitly state:
- What is **confirmed** (seen in code, validated by CLI or docs)
- What is **assumed** (inferred but not yet validated)

If critical gaps remain → run another validation round. Never propose on shaky ground.

## Anti-Patterns

- Searching before knowing what question you're trying to answer
- Planning before all critical unknowns are resolved
- Picking an approach without surfacing alternatives
- Treating your training's preference as the correct codebase pattern
- Assuming instead of validating with a read-only check
