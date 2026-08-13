# Skills

Skills are the procedural knowledge layer of Gaia. Where agents carry identity — their scope, their tone, their domain — skills carry process: how to classify a command, how to format a response contract, how to approach an investigation. An agent without skills knows who it is but not how to operate. Skills bridge that gap by injecting step-by-step protocols that the agent follows during its session.

Each skill lives in its own directory under `skills/<name>/` and contains at minimum a `SKILL.md` file. That file is what gets injected. Supporting material (`reference.md`, `examples.md`) lives in the same directory but is read on-demand — the agent pulls it from disk when needed rather than receiving it at startup. This keeps startup context lean while making full documentation accessible.

Skills are not shared via inheritance or imports — they are text injected verbatim into the agent's context window. The size limit for injected skills is roughly 100 lines. If a skill grows beyond that, the detailed content moves to `reference.md` and the main `SKILL.md` becomes a compact index pointing there.

The assignment matrix below shows which skills each agent receives. The first two — `agent-protocol` and `security-tiers` — appear on every agent. They are the non-negotiables: every agent must understand the response contract and the tier system.

## Cuándo se activa

Skills reach an agent through two distinct routes, and understanding both matters when troubleshooting why a skill is or is not present in a session.

**Route 1 — Preload from frontmatter (dispatched subagents only):**

```
1. The agent's .md declares `skills: [agent-protocol, security-tiers]`
     -> the HOST (Claude Code) reads that frontmatter when it dispatches the subagent
2. The host preloads each SKILL.md into the subagent's context
     -> the agent holds the process before its first tool call
3. At SubagentStop, hooks/adapters/claude_code.py::adapt_subagent_stop calls
   verify_skill_injection with the frontmatter list
     -> the transcript is searched for that skill's SKILL_FINGERPRINTS, and a
        declared skill that never appeared is reported as an advisory anomaly
```

No Gaia hook reads frontmatter to inject a skill, and none opens a `SKILL.md` at
all. `pre_tool_use.py` validates the dispatch and births the contract row; step 3
only verifies, after the fact, that what should have been preloaded actually
turned up. So a skill that fails to load produces an anomaly, never a repair.

The primary agent has no equivalent: `gaia-orchestrator.md` carries no `skills:`
field, because on the main thread the host inherits only system prompt, tool
restrictions, and model.

**Route 2 — On-demand via Skill tool:**

```
1. The agent hits a situation needing a workflow skill (approval, execution, git-conventions)
     -> it calls Skill("subagent-request-approval")
2. Claude Code reads skills/subagent-request-approval/SKILL.md from disk
     -> the content enters the agent's active context window
3. The agent follows the newly loaded protocol
```

`hooks/hooks.json` carries no `Skill` matcher, so a `Skill(...)` call never
reaches PreToolUse. This route is entirely host-side too.

Orchestrator-level skills (`agent-response`, `orchestrator-present-approval`) are always Route 2 — they are never in a frontmatter list, only loaded when the orchestrator needs to interpret a specific situation.

## Qué hay aquí

```
skills/
├── agent-contract-handoff/ # Reference: full field dictionary for the agent_contract_handoff envelope (input + output)
├── agent-creation/        # Coach skill: structure, tone, and component inventory for new specialist agents
├── agent-protocol/        # Protocol: the eleven principles that govern a turn as it happens, plus what the gate rejects
│   ├── reference.md       # the two state machines, phase-to-section map, kernel fields, storage/recovery, edge cases
│   ├── examples.md        # filled envelopes, one per agent_state
│   └── read-map.md        # THE read vocabulary: what a turn can read, with which verb, and what comes back (every other skill points here)
├── agent-response/        # Orchestrator: interpret agent agent_contract_handoff responses
├── blog-writing/          # Blog article writing and publishing for metraton.github.io
├── brief-spec/            # Brief and spec creation for features before planning
├── coding-standards/      # Language-agnostic code + inline documentation conventions
│   ├── reference.md       # per-stack tables: where docs natively live, stacks with no native slot, which checker verifies which rule
│   └── examples.md        # worked before/after pairs, each showing the cut (the survival test, placement, repeated rationale, provenance vs process trace)
├── command-execution/     # Defensive Bash execution, no-pipes discipline
│   └── reference.md
├── diagram-builder/       # Domain: turn any idea into a creative, pedagogical, data-driven diagram deck (thinking method + section/component dialect + authoring modes)
│   ├── GLOSSARY.md        # canonical dialect terms (section + component types) + status/variant enums
│   ├── reference.md       # field schema, engine behaviors, authoring modes, build/verify loop
│   └── assets/            # vendored portable engine: index.html, engine/, package.json, tools/verify.mjs, seed data/ (see assets/README.md)
├── execution/             # Post-approval execution discipline
├── fast-queries/          # Project Context-first scoped diagnostics
├── gaia-compact/          # Preserve transient continuity without duplicating durable state
├── gaia-patterns/         # Gaia component patterns: hooks, agents, routing, CLI
│   └── reference.md
├── gaia-planner/          # Feature planning, briefs, task decomposition
├── gaia-release/          # Gaia release pipeline: install local, dry-run, release
├── gaia-research/         # Technique: mine bookmarked GitHub repos for ideas Gaia can take, judged on their code rather than their README
├── gaia-audit/            # Audit one component (agent or skill) against its standard + live implementation
├── gaia-verify/           # Verify a Gaia installation across delivery surfaces
├── git-conventions/       # Conventional Commits (on-demand workflow skill)
├── gmail-policy/          # Gmail domain policy (label-only, no delete)
├── gmail-triage/          # Interactive Gmail inbox triage
├── gws-setup/             # Google Workspace CLI (gws) installation and configuration
├── investigation/         # Diagnosis methodology and pattern analysis
├── memory/                # Curate durable knowledge, live threads, and historical logs
│   └── reference.md        # project_ref anchoring internals, curate-flow mechanics, knowledge-graph roadmap
├── orchestrator-present-approval/ # T3 approval presentation for orchestrator
├── pending-approvals/     # Present and manage pending approval requests
├── readme-writing/        # How to write a README, branching by gate: repository root, component folder, or shipped template
│   └── reference.md       # per gate (repo root / component folder / shipped template): a filled example + a blank skeleton
├── subagent-request-approval/ # Plan-first T3 set / blocked-single producer branch
│   ├── reference.md
│   └── examples.md
├── agent-approval-protocol/ # Approval and COMMAND_SET data reference
├── scheduled-task/        # Headless recurring task: crontab + claude -p, reports via notifications
│   ├── reference.md
│   └── scripts/           # run-scheduled-task.sh wrapper + crontab.template
├── security-tiers/        # T0-T3 classification + hook enforcement model
│   └── reference.md
├── session-reflection/    # Recover, reconcile, curate, and hand off session continuity
├── skill-creation/        # How to design and write new skills
├── verification-oracle/   # Deterministically re-execute a command/code task_gates entry and compare actual vs expected exit code (loaded by gaia-verifier, the seeded verifier-role agent)
├── verification-rubric/   # Judge a semantic/self_review task_gates entry against its rubric, emit a justified pass/fail verdict (loaded by gaia-verifier, the seeded verifier-role agent)
│   └── scripts/           # rubric_verdict.py -- pure criteria-parse + verdict-assembly reference implementation
├── visual-verify/         # Technique: screenshot a UI/HTML with cached Chromium and read the result (invocable directly via the Skill tool)
│   └── scripts/           # screenshot.cjs -- zero-install Playwright capture
```

## Convenciones

**Skill assignment matrix:**

The two columns are structurally different, not just two lists: **Frontmatter**
is the literal `skills:` array in the agent's `.md` file — injected at dispatch
(Route 1 above), present in every session regardless of what the task turns out
to need. **On-demand** is a skill the agent's own text names loading via
`Skill('name')` when the matching moment arrives (Route 2) — it is never in
that agent's frontmatter, and because on-demand loading is discretionary, this
column lists only what the agent's file documents itself as loading, not every
skill that could theoretically apply.

| Agent | Frontmatter (always loaded) | On-demand (loaded via `Skill(...)`) |
|-------|------------------------------|--------------------------------------|
| cloud-troubleshooter | agent-protocol, security-tiers, command-execution, investigation, fast-queries | — |
| platform-architect | agent-protocol, security-tiers, investigation, command-execution, git-conventions, coding-standards | — |
| gitops-operator | agent-protocol, security-tiers, investigation, command-execution, git-conventions, coding-standards | — |
| developer | agent-protocol, security-tiers, investigation, command-execution, git-conventions, coding-standards | — |
| gaia-system | agent-protocol, security-tiers, command-execution, gaia-patterns, investigation, gaia-audit, coding-standards | agent-creation, skill-creation, gaia-release, gaia-verify |
| gaia-verifier | agent-protocol, security-tiers, command-execution, verification-oracle, verification-rubric | — |
| gaia-planner | agent-protocol, security-tiers, investigation, command-execution, gaia-planner | — |
| gaia-orchestrator | agent-protocol, security-tiers, command-execution, memory | agent-response and flow-specific skills |
| gaia-operator | agent-protocol, security-tiers, investigation, command-execution | memory, gmail-triage, gmail-policy, gws-setup, blog-writing, brief-spec |

Orchestrator skills (loaded on-demand via Skill tool, not assigned in frontmatter):
- `agent-response` — contract status interpretation and presentation
- `orchestrator-present-approval` — T3 approval presentation and grant activation
- `gaia-compact` — compact transient continuity after durable state is persisted

Workflow skills (on-demand injection, not in any agent frontmatter):
- `agent-contract-handoff` — reference field dictionary for the contract envelope (input + output); loaded on demand by producers and the orchestrator when field/trigger precision is needed
- `agent-approval-protocol` — approval and COMMAND_SET data reference
- `agent-creation` — coach skill for creating specialist agents; loaded on demand by gaia-system
- `brief-spec` — brief and spec creation; loaded on demand by orchestrator
- `execution` — post-approval execution discipline
- `git-conventions` — Conventional Commits format
- `pending-approvals` — present and resolve pending approval requests
- `subagent-request-approval` — T3 approval-request workflow (replaces `request-approval`)
- `scheduled-task` — headless recurring task framework: crontab + `claude -p` headless run that accumulates T3 approvals and reports back via `gaia notifications`; loaded on demand by description match
- `gaia-research` — technique for mining one or more bookmarked GitHub repos (or, in the inverse direction, finding who solves a capability the user wants) for ideas Gaia can take: burden of proof set by the claim type, code read instead of README, evidential status marked on every idea. Ends at digested ideas and deliberately produces no brief or plan — `brief-spec` picks up downstream. Loaded on demand by description match, invocable directly via the Skill tool
- `session-reflection` — session-arc recovery, two-way reconciliation against the live corpus, and memory curation proposal
- `ticket-writing` — formula for human-readable Stories and Subtasks, tracker-agnostic; invocable directly via the Skill tool
- `visual-verify` — technique for screenshotting a UI/HTML with a cached Chromium (no browser install) and reading the result; loaded on demand by description match when an agent produces visual output, invocable directly via the Skill tool
- `diagram-builder` — domain skill for turning an idea into a portable, data-driven diagram deck (architecture, timeline, planner, flow); carries the dialect vocabulary so the orchestrator can propose a decomposition and the agent can author it; delegates the visual check to `visual-verify`; loaded on demand by description match, invocable directly via the Skill tool
- `verification-oracle` — deterministically re-executes a `task_gates` entry of `verification_type` `command`/`code` (or a proposed contract `evidence_report.verification` block of the same types), comparing the actual exit code against the gate's expected value; the judgment-based `verification-rubric` skill's deterministic counterpart for `semantic`/`self_review` gates. Loaded by `gaia-verifier` (`agents/gaia-verifier.md`, `verifier: true`), the seeded verifier-role agent (Gaia harness B3, milestone M1)
- `verification-rubric` — judges a `task_gates` entry of `verification_type` `semantic`/`self_review` against its rubric (`evidence_shape`) and emits a justified pass/fail verdict; the deterministic-oracle skill's judgment-based counterpart for `command`/`code` gates. Loaded by `gaia-verifier` (`agents/gaia-verifier.md`, `verifier: true`), the seeded verifier-role agent (Gaia harness B3, milestone M1)

**Skill types:**

| Type | Injection | Examples |
|------|-----------|---------|
| Core | Always via `skills:` frontmatter | agent-protocol, security-tiers |
| Common | Most agents via `skills:` frontmatter | command-execution, investigation |
| Domain | Per-agent via `skills:` frontmatter | gaia-patterns |
| Workflow | On-demand (agent reads from disk) | subagent-request-approval, execution, git-conventions |
| Orchestrator | On-demand via Skill tool | agent-response, orchestrator-present-approval |

**SKILL.md format:**

```yaml
---
name: skill-name
description: When Claude should load and follow this skill
---

# Skill Content
```

Frontmatter carries only `name` + `description`. Whether a skill is reachable
directly via the Skill tool (as opposed to only via frontmatter injection) is
not tracked as a frontmatter field -- it is a fact noted in prose where
relevant (see the skill list above), not a machine-read property.

**Line budget:** Keep injected `SKILL.md` under 100 lines. Move details to `reference.md` (read on-demand). Supporting examples go in `examples.md`.

## Ver también

- [`agents/README.md`](../agents/README.md) — agent frontmatter and skills: field
- [`hooks/modules/agents/skill_injection_verifier.py`](../hooks/modules/agents/skill_injection_verifier.py) — checks at SubagentStop that expected skills reached the transcript; it verifies, it does not inject
- [`skills/skill-creation/SKILL.md`](./skill-creation/SKILL.md) — how to design a new skill
- [`skills/gaia-patterns/reference.md`](./gaia-patterns/reference.md) — full component inventory
