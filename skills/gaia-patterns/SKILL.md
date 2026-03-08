---
name: gaia-patterns
description: Use when analyzing, designing, or modifying the gaia-ops orchestration system architecture
user-invocable: false
---

# Gaia-Ops Patterns

Domain knowledge for the gaia-ops meta-system. For the Component Map details, see `reference.md`.

## Prompt → Result Flow

```
1. User sends prompt
   ↓
2. Orchestrator (CLAUDE.md) — routes or answers directly (<200 tokens → direct)
   ↓
3. Pre-Tool Hook (pre_tool_use.py)
   ├─ Inject project-context.json (relevant sections per agent)
   ├─ Load skills from frontmatter
   └─ Validate permissions
   ↓
4. Agent Executes — uses tools, follows skills, returns AGENT_STATUS
   ↓
5. Post-Tool Hook — audit + metrics
   ↓
6. Orchestrator processes AGENT_STATUS
   ├─ PENDING_APPROVAL → get approval → resume
   ├─ NEEDS_INPUT → ask user → resume
   └─ COMPLETE → respond to user
```

## Key Concepts

- **Binary Delegation:** <200 tokens + only Read needed → answer directly. Otherwise → delegate.
- **Agent Instantiation:** identity (.md) + skills (injected) + project-context (contracts) + orchestrator request.
- **Security Tiers:** T0 (read) → T1 (validate) → T2 (simulate) → T3 (realize, requires approval).
- **Two-Phase T3:** PLANNING → PENDING_APPROVAL → APPROVED_EXECUTING → COMPLETE.
- **Consolidation Loop:** for multi-surface work, Gaia may dispatch more than one round of agents, but only while gaps are actionable and evidence is still improving.
- **Principle:** Skills teach process. Agents teach identity and domain knowledge. Runtime enforces deterministic contracts. Never duplicate.

## Multi-Agent Consolidation

When Gaia coordinates multi-surface work:
1. dispatch the primary owners for the active surfaces
2. collect `EVIDENCE_REPORT` and `CONSOLIDATION_REPORT`
3. merge:
   - confirmed findings
   - conflicts
   - open gaps
   - next best agent
   - recommended action
4. continue only if the next gap has a clear owner and another round is likely to reduce uncertainty
5. stop after at most **2 consolidation rounds after the initial pass**

Do not keep dispatching agents just because a gap exists. A gap must be:
- owned by a clear next agent
- resolvable without new user input
- likely to produce new evidence rather than rephrase existing uncertainty

## Workflow Design Philosophy

1. **Flow naturally** — each step leads to the next without friction
2. **Be positive** — describe what to do, not what to avoid
3. **Allow discovery** — agent reaches conclusions empirically
4. **Be concise** — leave room for growth
5. **Be measurable** — goals with numbers, not subjective terms

## Token Budget

| Document | Target | Max |
|----------|--------|-----|
| Agent `.md` | 2,000 tokens | 3,000 |
| `CLAUDE.md` | 1,500 tokens | 2,500 |
| Skill (injected) | 500 tokens | 1,000 |

## Agent Creation Standards

1. **YAML Frontmatter** — `name`, `description` (routing label), `tools`, `model`, `skills` (canonical order)
2. **Identity** — 1-2 paragraphs: what domain, what output format
3. **Scope** — CAN DO / CANNOT DO → DELEGATE table with agent names
4. **Domain Errors** — domain-specific errors only

**Canonical injected skills order:** `agent-protocol` → `security-tiers` → `output-format` → `investigation` → `command-execution` → domain skill → `context-updater` → `fast-queries`

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
| Breaking change to AGENT_STATUS format | MAJOR |
