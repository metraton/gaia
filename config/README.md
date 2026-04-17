# Config

Configuration lives here, separate from hooks, because these are data files — not code. Hooks are Python scripts that run at runtime; config files are JSON documents that those scripts read to make decisions. Keeping them apart means you can audit and change system behavior (which agents see which context sections, what git commit patterns are allowed, which surfaces route where) without touching executable code. It also makes the config files version-controllable and reviewable on their own terms.

The contracts are the most important piece in this directory. `context-contracts.json` defines, per agent, which sections of `project-context.json` the agent is allowed to read and which it is allowed to write. This is the access control layer for project knowledge — an agent that is not in the contracts file receives no context injection at all. The cloud extension files in `cloud/` extend these contracts for cloud-specific sections without modifying the base file, so adding a new cloud provider is a new file, not an edit to the core.

The other files — routing, git standards, universal rules — are each consumed by a specific module and do exactly what their names say. There is no magic here: the files are loaded, parsed, and applied by the module that reads them.

## Cuándo se activa

This component does not activate as a runtime process. Each file is read on-demand by the module that needs it. The table below shows the read point for each file.

**Cuándo se lee cada archivo:**

| File | Read by | When |
|------|---------|------|
| `surface-routing.json` | `hooks/user_prompt_submit.py` | Every prompt — determines routing recommendation injected into orchestrator context |
| `context-contracts.json` | `tools/context/context_provider.py` | Every agent dispatch — determines which project-context sections to inject |
| `git_standards.json` | `hooks/modules/validation/commit_validator.py` | Every `git commit` call intercepted by PreToolUse |
| `universal-rules.json` | `tools/context/context_provider.py` | Every agent dispatch — injected into all agents alongside project context |
| `cloud/gcp.json` | `tools/context/context_provider.py` | Agent dispatch when `cloud_provider = gcp` in project-context.json |
| `cloud/aws.json` | `tools/context/context_provider.py` | Agent dispatch when `cloud_provider = aws` in project-context.json |

**Base + cloud merge flow:**

```
Agent dispatch triggered
        |
context_provider.py reads context-contracts.json    <- cloud-agnostic base
        |
Detects cloud_provider from project-context.json
        |
Reads cloud/{provider}.json                         <- cloud extensions
        |
Merges: extends read/write lists per agent (no duplicates)
        |
Result: complete contract for this agent on this cloud
        |
Agent receives filtered project-context sections
```

## Qué hay aquí

```
config/
├── context-contracts.json   # Per-agent read/write access to project-context sections
├── surface-routing.json     # Intent classification and agent routing signals
├── git_standards.json       # Commit type allowlist, footer rules, Conventional Commits config
├── universal-rules.json     # Behavior rules injected into all agents at dispatch time
├── cloud/
│   ├── gcp.json             # GCP-specific context sections (extends base contracts)
│   └── aws.json             # AWS-specific context sections (extends base contracts)
└── README.md
```

## Convenciones

**context-contracts.json schema:** Each entry is keyed by agent name. Each agent has `read` (list of project-context section names the agent receives) and `write` (list of sections the agent can update via CONTEXT_UPDATE). `core_sections` is a top-level list of sections injected into every agent regardless of per-agent config.

**Adding a new cloud:** Create `cloud/azure.json` following the same schema as `cloud/gcp.json`. Define agent-specific sections for that cloud. No code changes needed — `context_provider.py` detects the file automatically by matching `cloud_provider` from project-context.

**surface-routing.json format:** Each surface entry has `intent`, `primary_agent`, `adjacent_surfaces`, and `signals` (with `high` and `medium` confidence keyword lists). High-confidence signals are checked first; medium signals act as tie-breakers.

**universal-rules.json:** Changes here affect every agent in every session. Add only rules that are truly universal — constraints that apply regardless of domain. Domain-specific rules belong in the relevant skill (`security-tiers`, `command-execution`, etc.).

## Ver también

- [`hooks/user_prompt_submit.py`](../hooks/user_prompt_submit.py) — reads `surface-routing.json` on every prompt
- [`hooks/modules/validation/`](../hooks/modules/validation/) — reads `git_standards.json` on commit validation
- [`tools/context/`](../tools/context/) — reads contracts and universal-rules at agent dispatch time
- [`agents/README.md`](../agents/README.md) — agent names that must match context-contracts.json keys
