# Agents

Agents are the specialists of Gaia. Each one has a narrow domain, a set of allowed tools, and a list of skills that get injected at startup. The orchestrator never does domain work itself — it reads the user's intent, picks the right agent, and dispatches it. What comes back is a `json:contract` block with findings, changes, and a verification result.

Every agent is defined as a Markdown file with YAML frontmatter at the top. That frontmatter is not decoration — Claude Code reads it to know which tools the agent may use, which model to run, and which skills to inject before the first turn. The body of the file is the agent's identity: its scope, its error handling, and the tone it uses when talking back to the orchestrator.

The orchestrator (`gaia-orchestrator.md`) is special: it has no `permissionMode`, no file tools, and no domain skills. Its job is routing and governance, not execution. All other agents set `permissionMode: acceptEdits` so that file edits inside their domain flow without extra prompts, while the hook layer still enforces security tiers on every Bash call.

Adding a new agent is three steps: write the `.md` file here, add it to `build/gaia-ops.manifest.json` under `agents`, and add a routing entry in `config/surface-routing.json`. The agent becomes available on the next Claude Code restart.

## Cuándo se activa

```
User sends prompt
        |
[user_prompt_submit.py] injects orchestrator identity + routing recommendation
        |
Orchestrator evaluates intent against surface-routing.json
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
Agent executes, returns json:contract to orchestrator
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
└── terraform-architect.md # Terraform, Terragrunt, cloud infrastructure
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
| `skills` | Yes | First two are always `agent-protocol`, `security-tiers` |

**Skills order:** `agent-protocol` first, `security-tiers` second, then domain skills. The first two are non-negotiable — every agent needs the contract format and the tier classification.

**Description field:** This is the routing signal. Write it as a present-tense label: "Routes requests to specialist agents" or "Diagnoses live cloud infrastructure". The orchestrator matches user intent against these descriptions.

**Tool restriction:** Give each agent only the tools it actually needs. The orchestrator has no Read/Write/Bash. Read-only agents should not have Write or Edit.

## Ver también

- [`config/surface-routing.json`](../config/surface-routing.json) — intent-to-agent mapping
- [`build/gaia-ops.manifest.json`](../build/gaia-ops.manifest.json) — agent registration
- [`hooks/subagent_start.py`](../hooks/subagent_start.py) — context injection at spawn time
- [`hooks/subagent_stop.py`](../hooks/subagent_stop.py) — contract validation after agent completes
- [`skills/README.md`](../skills/README.md) — skill assignment matrix
