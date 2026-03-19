---
name: gaia-patterns
description: Use when analyzing, designing, or modifying the gaia-ops orchestration system architecture
metadata:
  user-invocable: false
  type: domain
---

# Gaia-Ops Patterns

Domain knowledge for the gaia-ops meta-system. For the Component Map details, see `reference.md`.

## Prompt → Result Flow

```
1. User sends prompt
   ↓
2. Orchestrator (identity injected by submit hook) — routes to the correct agent
   ↓
3. Pre-Tool Hook (pre_tool_use.py)
   ├─ Inject project-context.json (relevant sections per agent)
   ├─ Load skills from frontmatter
   └─ Validate permissions
   ↓
4. Agent Executes — uses tools, follows skills, returns `json:contract` block
   ↓
5. Post-Tool Hook — audit + metrics
   ↓
6. Orchestrator processes `json:contract` block (plan_status)
   ├─ REVIEW → present plan, get feedback → resume
   ├─ AWAITING_APPROVAL → present plan + nonce relay → resume
   ├─ NEEDS_INPUT → ask user → resume
   └─ COMPLETE → respond to user
```

## Key Concepts

- **Binary Delegation:** The orchestrator always delegates. Its only tools are Agent and AskUserQuestion.
- **Agent Instantiation:** identity (.md) + skills (injected) + project-context (contracts) + orchestrator request.
- **Security Tiers:** T0 (read) → T1 (validate) → T2 (simulate) → T3 (realize, requires approval).
- **T3 Flow:** IN_PROGRESS → REVIEW → IN_PROGRESS → COMPLETE (plan-first) or IN_PROGRESS → AWAITING_APPROVAL → IN_PROGRESS → COMPLETE (hook-blocked).
- **Consolidation Loop:** for multi-surface work, Gaia may dispatch more than one round of agents, but only while gaps are actionable and evidence is still improving.
- **Principle:** Skills teach process. Agents teach identity and domain knowledge. Runtime enforces deterministic contracts. Never duplicate.

## Multi-Agent Consolidation

The orchestrator owns the consolidation loop. Agents return `json:contract` blocks with `consolidation` objects; the orchestrator merges, decides whether to dispatch another round, and stops when gaps are no longer actionable.

## Workflow Design Philosophy

1. **Flow naturally** — each step leads to the next without friction
2. **Be positive** — describe what to do, not what to avoid
3. **Allow discovery** — agent reaches conclusions empirically
4. **Be concise** — leave room for growth
5. **Be measurable** — goals with numbers, not subjective terms

## Line Budget

| Document | Target | Max |
|----------|--------|-----|
| Agent `.md` | 80 lines | 120 |
| `CLAUDE.md` | 60 lines | 100 |
| Skill (injected) | < 100 lines | 100 |

## Agent Creation Standards

1. **YAML Frontmatter** — `name`, `description` (routing label), `tools`, `model`, `skills` (canonical order)
2. **Identity** — 1-2 paragraphs: what domain, what output format
3. **Scope** — CAN DO / CANNOT DO → DELEGATE table with agent names
4. **Domain Errors** — domain-specific errors only

**Canonical injected skills order:** `agent-protocol` → `security-tiers` → `investigation` → `command-execution` → domain skill → `context-updater` → `fast-queries`

**On-demand workflow skills:** `approval`, `execution`, `git-conventions`

## Documentation Standards

**Required sections (in order):** What it does, Where it fits, How it works, Components, Usage, References.

**The zoom lens rule:** every README shows the complete system flow and bolds where this module participates.

**Writing rules:** every line earns its place — no duplication, discoverable over documented.

## Release Management

- **Package:** `@jaguilar87/gaia-ops` (npm public registry)
- **Symlinks:** `.claude/` symlinks to `node_modules/@jaguilar87/gaia-ops/`

| Change | Version |
|--------|---------|
| Bug fix in agent or skill | PATCH |
| New agent or skill | MINOR |
| Breaking change to `json:contract` format | MAJOR |
