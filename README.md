# Gaia

> **G**eneral **A**gentic **I**ntegration **A**rchitecture

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia.svg)](https://www.npmjs.com/package/@jaguilar87/gaia)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia.svg)](https://nodejs.org)

## How to read this repo

Gaia is event-driven. Every capability in the codebase is attached to a moment in the Claude Code lifecycle — a prompt arriving, a tool being called, an agent completing. Reading the folder structure without that lens makes it look like a collection of files. Reading it with that lens, everything clicks into place.

The flow is this: a user sends a prompt, the `UserPromptSubmit` hook fires and injects the orchestrator's identity and a routing recommendation. The orchestrator picks a specialist agent and dispatches it. Before that agent's first tool call lands, the `PreToolUse` hook intercepts it — injecting context, validating permissions, blocking dangerous commands. The agent does its work and returns a `agent_contract_handoff`. The `SubagentStop` hook fires, validates the contract, records metrics, and writes to episodic memory.

```
UserPromptSubmit  ->  routing  ->  PreToolUse  ->  agent  ->  PostToolUse  ->  SubagentStop
      |                  |               |              |             |               |
  identity           surface-        security       agent_contract_handoff  audit log     metrics +
  injection          routing.json    gate +                                      memory
                                     context
                                     injection
```

That pipeline is the spine. Everything else in this repo is either a component of that pipeline (`hooks/`, `agents/`, `skills/`, `config/`) or infrastructure that supports it (`build/`, `bin/`, `tests/`). Start with the folder that matches the behavior you want to understand, and its README will tell you where it fits in the flow.

## Overview

**Gaia** is a security-first multi-agent orchestration plugin for Claude Code. It classifies every command by risk, gates state-changing operations behind consent, injects project context, and routes work to specialist agents. It ships as a **single, unified plugin** named `gaia` — one artifact carrying the full orchestrator, all agents, all skills, all hooks, all tools, and all config.

### Features

- **Multi-cloud support** - GCP, AWS, Azure
- **8 agents** - platform-architect, gitops-operator, cloud-troubleshooter, developer, gaia-planner, gaia-operator, gaia-orchestrator, gaia-system (meta-agent)
- **Contracts as SSOT** - Cloud-agnostic base contracts with per-cloud extensions (GCP, AWS)
- **Dynamic identity** - Orchestrator identity defined in `agents/gaia-orchestrator.md`, activated via `settings.json` agent config; skills loaded on-demand
- **Dual-barrier security** - Settings deny rules (Claude Code native) + hook-level blocking (enforced from the installed plugin source, not user-editable)
- **Indirect execution detection** - Catches `bash -c`, `eval`, `python -c` wrappers that bypass regex patterns
- **Approval gates** for T3 operations via native `ask` dialog
- **Git commit validation** with Conventional Commits
- **32 skills** - Injected procedural knowledge modules for agents (protocol, domain, workflow)
- **Curated + episodic memory** - `gaia memory` CLI: FTS5 search, episode inspection, session context orientation, and curated-note curation (`append`/`add`/`edit`/`reclassify`/`delete`/`link`)
- **Context evals** - pytest-driven agent evaluation (5 graders, 3 backends, 10 scenarios, baseline + drift detection)
- **Plugin + npm** - Distributable as Claude Code native plugin or npm package
- **Enterprise ready** - Managed settings template for organization-wide deployment

## Installation

Gaia is one plugin reaching a workspace through two surfaces. Pick the one that matches how you run Claude Code.

### Via Claude Code Plugin (recommended)
```bash
# Add the marketplace
/plugin marketplace add metraton/gaia

# Install the unified plugin
/plugin install gaia
```

### Via npm / pnpm (advanced setup)
```bash
npm install @jaguilar87/gaia    # or: pnpm add @jaguilar87/gaia
gaia install                    # wires the workspace
```

**There is no `postinstall` hook.** The install is non-invasive and works identically under npm and pnpm. The DB is bootstrapped **lazily on the first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`); the workspace `.claude/` structure is written by running `gaia install` explicitly, or by the SessionStart hook. Run `gaia doctor` afterward to verify.

### Quick Start (npm / pnpm)

```bash
# Install the package
npm install @jaguilar87/gaia        # or: pnpm add @jaguilar87/gaia

# Wire the workspace (no postinstall does this for you)
gaia install
```

To scan your project stack after install:
```bash
gaia scan
```

`gaia install` will:
1. Bootstrap the DB (`~/.gaia/gaia.db`) with the current schema (lazy on first use, or here explicitly)
2. Create `.claude/` directory with 5 symlinks + a `CHANGELOG.md` link to this package
3. Merge hooks and permissions into `settings.local.json` (preserves existing user config)
4. Write `plugin-registry.json` with `installed[].name == "gaia"`

`gaia scan` (run separately, on-demand) will:
1. Auto-detect your project structure (GitOps, Terraform, AppServices, stack)
2. Write scan results to `~/.gaia/gaia.db` (DB is canonical; no `project-context.json` file is generated)

No `CLAUDE.md` is generated -- orchestrator identity lives in `agents/gaia-orchestrator.md` and is activated via `settings.json: { "agent": "gaia-orchestrator" }`.

### Settings Architecture

Gaia separates hooks from permissions:

| File | Content | Strategy |
|------|---------|----------|
| `settings.json` | Hooks only (12 hook types) | Overwritten from template on each update |
| `settings.local.json` | Permissions (allow + deny rules) | Union merge — never removes user config |

This ensures your personal customizations (MCP servers, extra permissions) survive updates.

### Manual Installation

`gaia install` writes these for you; the manual equivalent is:

```bash
npm install @jaguilar87/gaia
```

Then create the 5 directory symlinks plus the CHANGELOG file link:

```bash
mkdir -p .claude && cd .claude
ln -s ../node_modules/@jaguilar87/gaia/agents agents
ln -s ../node_modules/@jaguilar87/gaia/tools tools
ln -s ../node_modules/@jaguilar87/gaia/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia/config config
ln -s ../node_modules/@jaguilar87/gaia/skills skills
ln -s ../node_modules/@jaguilar87/gaia/CHANGELOG.md CHANGELOG.md
```

## Usage

Once installed, the agent system is ready:

```bash
claude
```

The orchestrator identity is defined in `agents/gaia-orchestrator.md` and activated via `settings.json` agent config. Skills are loaded on-demand.

Skill loading and overall installation health are checked via:

```bash
gaia doctor
```

## Security

Gaia enforces a 6-layer security pipeline:

| Layer | Mechanism | Bypassable? |
|-------|-----------|-------------|
| Indirect execution detection | `bash -c`, `eval`, `python -c` wrappers | No (hook-level) |
| Blocked commands (regex) | Regex patterns for irreversible commands | No (enforced from plugin source) |
| Blocked commands (semantic) | Ordered-token / mutative-verb rules | No (enforced from plugin source) |
| Cloud pipe validator | Credential piping detection | No (hook-level) |
| Mutative verb detection | `ask` dialog for state-changing ops | User approves via native dialog |
| Settings deny rules | 100+ deny rules generated at install into `settings.local.json` | Self-healing (restored each session) |

## Project Structure

```
gaia/
├── agents/              # Agent definitions (8 agents) — specialist identities + tool grants
├── skills/              # Skill modules (32 skills) — injected procedural knowledge
├── hooks/               # Claude Code hooks — the event-driven pipeline
├── config/              # Configuration — routing, contracts, rules, git standards
├── build/               # Plugin manifests — hook + agent registration for Claude Code
├── bin/                 # Single `gaia` CLI; subcommands discovered from bin/cli/
├── tests/               # Test suite — 3-layer pyramid (pytest, LLM eval, e2e)
└── tools/               # Context provisioning tools
```

## API

```javascript
import { getAgentPath, getToolPath, getConfigPath } from '@jaguilar87/gaia';

const agentPath = getAgentPath('gitops-operator');
const toolPath = getToolPath('context_provider.py');
```

## Versioning

This package follows [Semantic Versioning](https://semver.org/):

- **MAJOR:** Breaking changes
- **MINOR:** New features
- **PATCH:** Bug fixes

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## Documentation

- [INSTALL.md](./INSTALL.md) - Installation guide
- [agents/](./agents/) - Agent definitions and lifecycle
- [skills/](./skills/) - Skill modules and assignment matrix
- [hooks/](./hooks/) - Hook pipeline and security architecture
- [config/](./config/) - Configuration (contracts, git standards, surface routing)
- [build/](./build/) - Plugin manifests
- [bin/](./bin/) - CLI utilities
- [tests/](./tests/) - Test suite

## Requirements

- **Node.js:** >=18.0.0
- **Python:** >=3.9
- **Claude Code:** Latest version
- **Git:** >=2.30

## Project Context Management

Gaia uses `~/.gaia/gaia.db` (SQLite) as the canonical store for project context. Run `gaia scan` inside a workspace to detect and record the project stack, GitOps layout, Terraform layout, and other structural facts. Context is scoped per-workspace and survives reinstalls. View it with:

```bash
gaia context show
```

## Support

- **Issues:** [GitHub Issues](https://github.com/metraton/gaia/issues)
- **Repository:** [github.com/metraton/gaia](https://github.com/metraton/gaia)
- **Author:** Jorge Aguilar <jorge.aguilar88@gmail.com>

## License

MIT License - See [LICENSE](./LICENSE) for details.
