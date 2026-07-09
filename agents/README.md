# Agents

Agents are the specialists of Gaia. Each one has a narrow domain, a set of allowed tools, and a list of skills that get injected at startup. The orchestrator never does domain work itself — it reads the user's intent, picks the right agent, and dispatches it. What comes back is a `agent_contract_handoff` block with findings, changes, and a verification result.

Every agent is defined as a Markdown file with YAML frontmatter at the top. That frontmatter is not decoration — Claude Code reads it to know which tools the agent may use, which model to run, and which skills to inject before the first turn. The body of the file is the agent's identity: its scope, its error handling, and the tone it uses when talking back to the orchestrator.

The orchestrator (`gaia-orchestrator.md`) is special: it has no `permissionMode` and no domain skills, and its only file tool is `Read` -- carried solely to triangulate evidence with the user (a document or an image next to a specialist's contract), never as a substitute for a specialist's investigation. It has no Bash, Edit, Write, Glob, or Grep. Its job is routing and governance, not execution. All other agents set `permissionMode: acceptEdits` so that file edits inside their domain flow without extra prompts, while the hook layer still enforces security tiers on every Bash call.

Adding a new agent is three steps: write the `.md` file here (including a `routing:` frontmatter block if the agent owns a surface), add it to `build/gaia.manifest.json` under `agents`, and re-run `gaia install` so `tools/scan/seed_surface_routing.py` seeds the agent's surface into the DB-backed `surface_routing` table. The agent becomes available on the next Claude Code restart. Surface routing is no longer a `config/surface-routing.json` file — each agent's `routing:` block is the source of truth.

## Cuándo se activa

```
User sends prompt
        |
[user_prompt_submit.py] injects orchestrator identity + routing recommendation
        |
Orchestrator evaluates intent against the DB-backed surface_routing table
        |
Orchestrator calls Agent/Task tool with agent name + focused objective
        |
[pre_tool_use.py] intercepts the Task/Agent tool call
        |  Reads agent .md frontmatter -> injects skills listed in skills:
        |  Injects project-context sections via context-contracts.json
        |  Validates permissionMode
        v
Claude Code spawns subagent with:
  - Identity from agents/<name>.md body
  - Skills injected from frontmatter skills: list
  - Project context filtered by context-contracts.json
        |
[subagent_start.py] fires -> can inject additional context (e.g. persisted memory)
        |
Agent executes, returns agent_contract_handoff to orchestrator
        |
[subagent_stop.py] fires -> validates contract, records metrics, updates episodic memory
```

## Qué hay aquí

```
agents/
├── gaia-orchestrator.md   # Routing + governance layer (no file tools, no domain)
├── gaia-operator.md       # Personal workspace: Gmail, calendar, operator tasks
├── gaia-system.md         # Meta-agent: Gaia internals, agents, skills, hooks
├── gaia-planner.md        # Feature planning: briefs, task decomposition
├── developer.md           # Application code: Node.js, Python, TypeScript
├── cloud-troubleshooter.md # Live cloud diagnostics: GCP, AWS, Azure
├── gitops-operator.md     # Kubernetes, Flux, HelmReleases, GitOps
└── platform-architect.md  # Infrastructure-as-code (tool-agnostic): Terraform, Pulumi, CloudFormation, OpenTofu, CDK
```

## Convenciones

**Frontmatter fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | Matches filename without `.md` |
| `description` | Yes | Routing label — the orchestrator uses this to pick the agent |
| `tools` | Yes | Comma-separated list of allowed Claude Code tools |
| `model` | Yes | Use `inherit` unless the agent needs a specific model |
| `permissionMode` | Most agents | Set `acceptEdits` for agents that write files |
| `routing` | Surface owners | Declares the agent's surface (`surface`, `adjacent_surfaces`, `keywords`/`commands`/`artifacts`, `required_checks`, optional `sub_surfaces`); seeded into the `surface_routing` table by `tools/scan/seed_surface_routing.py`. The surface's `intent` is the `description`; `contract_sections` derives from `project_context_contracts.read`. Omit for the orchestrator (it IS the router). |
| `contract_handoff_writer` | Fleet-seeded agents | `true` opts this agent into the handoff-writer fleet: the write-guard (`_assert_dispatch_can_write_handoff` in `gaia/store/writer.py`) allows `gaia contract finalize` to write this agent's `agent_contract_handoffs` row only when its `name:` carries this marker. Seeded by `gaia.state.permissions.handoff_writer_fleet()`, which enumerates `agents/*.md`; every agent under `agents/` is expected to carry it (drift-checked by `tests/contract/test_finalize_store.py`). |
| `skills` | Yes | First two are always `agent-protocol`, `security-tiers` |

**Skills order:** `agent-protocol` first, `security-tiers` second, then domain skills. The first two are non-negotiable — every agent needs the contract format and the tier classification.

**Description field:** This is the routing signal. Write it as a present-tense label: "Routes requests to specialist agents" or "Diagnoses live cloud infrastructure". The orchestrator matches user intent against these descriptions.

**Tool restriction:** Give each agent only the tools it actually needs. The orchestrator has only `Read` (to triangulate evidence with the user) and no Write/Edit/Bash/Glob/Grep. Read-only agents should not have Write or Edit.

## Ver también

- [`tools/scan/seed_surface_routing.py`](../tools/scan/seed_surface_routing.py) — seeds each agent's `routing:` block into the DB-backed `surface_routing` table (intent-to-agent mapping)
- [`build/gaia.manifest.json`](../build/gaia.manifest.json) — agent registration
- [`hooks/subagent_start.py`](../hooks/subagent_start.py) — context injection at spawn time
- [`hooks/subagent_stop.py`](../hooks/subagent_stop.py) — contract validation after agent completes
- [`skills/README.md`](../skills/README.md) — skill assignment matrix
