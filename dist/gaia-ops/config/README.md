# Config

Configuration lives here, separate from hooks, because these are data files — not code. Hooks are Python scripts that run at runtime; config files are JSON documents that those scripts read to make decisions. Keeping them apart means you can audit and change system behavior (which agents see which context sections, what git commit patterns are allowed, which surfaces route where) without touching executable code. It also makes the config files version-controllable and reviewable on their own terms.

`context-contracts.json` is the seeding source for agent contracts. During `gaia install`, its contents are loaded into the `project_context_contracts` and `agent_contract_permissions` tables in `~/.gaia/gaia.db`. At runtime, the DB is the SSOT — the hook layer reads contracts from the DB, not from this file. Editing `context-contracts.json` without re-running `gaia install` (or manually applying the SQL) has no effect. The cloud extension files in `cloud/` extend these contracts for cloud-specific sections without modifying the base file, so adding a new cloud provider is a new file, not an edit to the core.

The other files — routing and git standards — are each consumed by a specific module and do exactly what their names say. There is no magic here: the files are loaded, parsed, and applied by the module that reads them.

## Cuándo se activa

This component does not activate as a runtime process. Each file is read on-demand by the module that needs it. The table below shows the read point for each file.

**Cuándo se lee cada archivo:**

| File | Read by | When |
|------|---------|------|
| `surface-routing.json` | `hooks/user_prompt_submit.py` | Every prompt — determines routing recommendation injected into orchestrator context |
| `context-contracts.json` | `gaia install` / `gaia update` | One-time at install; populates `~/.gaia/gaia.db` tables. Runtime reads come from DB. |
| `git_standards.json` | `hooks/modules/validation/commit_validator.py` | Every `git commit` call intercepted by PreToolUse |
| `cloud/gcp.json` | `tools/context/context_provider.py` | Agent dispatch when `cloud_provider = gcp` in workspace DB record |
| `cloud/aws.json` | `tools/context/context_provider.py` | Agent dispatch when `cloud_provider = aws` in workspace DB record |

**Base + cloud merge flow:**

```
Agent dispatch triggered
        |
hooks/modules/context/contracts_loader.py reads project_context_contracts from DB
        |
Detects cloud_provider from workspace record in ~/.gaia/gaia.db
        |
Reads cloud/{provider}.json                         <- cloud extensions (still file-based)
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
├── context-contracts.json   # Seeding source for per-agent read/write contracts (applied on install to gaia.db)
├── surface-routing.json     # Intent classification and agent routing signals
├── git_standards.json       # Commit type allowlist, footer rules, Conventional Commits config
├── cloud/
│   ├── gcp.json             # GCP-specific context sections (extends base contracts)
│   └── aws.json             # AWS-specific context sections (extends base contracts)
└── README.md
```

## Convenciones

**context-contracts.json schema:** Each entry is keyed by agent name. Each agent has `read` (list of project-context section names the agent receives) and `write` (list of sections the agent can update via an `update_contracts` clause). `core_sections` is a top-level list of sections injected into every agent regardless of per-agent config. This schema is mirrored in the DB tables `project_context_contracts` (one row per agent) and `agent_contract_permissions` (permission grants).

**Adding a new cloud:** Create `cloud/azure.json` following the same schema as `cloud/gcp.json`. Define agent-specific sections for that cloud. No code changes needed — `context_provider.py` detects the file automatically by matching `cloud_provider` from project-context.

**surface-routing.json format:** Each surface entry has `intent`, `primary_agent`, `adjacent_surfaces`, and `signals` (with `high` and `medium` confidence keyword lists). High-confidence signals are checked first; medium signals act as tie-breakers.

## Ver también

- [`~/.gaia/gaia.db`](../gaia/store/schema.sql) — `project_context_contracts` + `agent_contract_permissions` tables (runtime SSOT for contracts)
- [`hooks/user_prompt_submit.py`](../hooks/user_prompt_submit.py) — reads `surface-routing.json` on every prompt
- [`hooks/modules/validation/`](../hooks/modules/validation/) — reads `git_standards.json` on commit validation
- [`tools/context/`](../tools/context/) — reads contracts (from DB) at agent dispatch time
- [`agents/README.md`](../agents/README.md) — agent names that must match context-contracts.json keys
