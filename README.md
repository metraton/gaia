# Gaia

> **G**eneral **A**gentic **I**ntegration **A**rchitecture

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia.svg)](https://www.npmjs.com/package/@jaguilar87/gaia)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia.svg)](https://nodejs.org)

## Cómo leer este repo

Gaia is event-driven. Every capability in the codebase is attached to a moment in the Claude Code lifecycle — a prompt arriving, a tool being called, an agent completing. Reading the folder structure without that lens makes it look like a collection of files. Reading it with that lens, everything clicks into place.

The flow is this: a user sends a prompt, the `UserPromptSubmit` hook fires and injects the orchestrator's identity and a routing recommendation. The orchestrator picks a specialist agent and dispatches it. Before that agent's first tool call lands, the `PreToolUse` hook intercepts it — injecting context, validating permissions, blocking dangerous commands. The agent does its work and returns a `json:contract`. The `SubagentStop` hook fires, validates the contract, records metrics, and writes to episodic memory.

```
UserPromptSubmit  ->  routing  ->  PreToolUse  ->  agent  ->  PostToolUse  ->  SubagentStop
      |                  |               |              |             |               |
  identity           surface-        security       json:contract  audit log     metrics +
  injection          routing.json    gate +                                      memory
                                     context
                                     injection
```

That pipeline is the spine. Everything else in this repo is either a component of that pipeline (`hooks/`, `agents/`, `skills/`, `config/`) or infrastructure that supports it (`build/`, `bin/`, `tests/`, `templates/`). Start with the folder that matches the behavior you want to understand, and its README will tell you where it fits in the flow.

## Overview

**Gaia** is a multi-agent orchestration system for DevOps automation. It ships two sub-plugins — `gaia-ops` (full orchestrator) and `gaia-security` (security-only) — with security-first command classification, specialized AI agents, and plugin-based distribution. Currently integrates with Claude Code.

### Features

- **Multi-cloud support** - GCP, AWS, Azure
- **8 agents** - terraform-architect, gitops-operator, cloud-troubleshooter, developer, gaia-planner, gaia-operator, gaia-orchestrator, gaia-system (meta-agent)
- **Contracts as SSOT** - Cloud-agnostic base contracts with per-cloud extensions (GCP, AWS)
- **Dynamic identity** - Orchestrator identity defined in `agents/gaia-orchestrator.md`, activated via `settings.json` agent config; skills loaded on-demand
- **Dual-barrier security** - Settings deny rules (Claude Code native) + hook-level blocking (inalterable via symlink)
- **Indirect execution detection** - Catches `bash -c`, `eval`, `python -c` wrappers that bypass regex patterns
- **Approval gates** for T3 operations via native `ask` dialog
- **Git commit validation** with Conventional Commits
- **32 skills** - Injected procedural knowledge modules for agents (protocol, domain, workflow)
- **Episodic memory** - `gaia memory` CLI with FTS5 search, episode inspection, and session context orientation
- **Context evals** - pytest-driven agent evaluation (5 graders, 3 backends, 10 scenarios, baseline + drift detection)
- **Plugin + npm** - Distributable as Claude Code native plugin or npm package
- **Enterprise ready** - Managed settings template for organization-wide deployment

## Installation

### Via Claude Code Plugin (recommended)
```bash
# Add the marketplace
/plugin marketplace add metraton/gaia

# Install the full system (includes security)
/plugin install gaia-ops

# Or install security only
/plugin install gaia-security    # Security hooks only
```

### Via npm (advanced setup)
```bash
npm install @jaguilar87/gaia
npx gaia-scan
```

### Quick Start (npm)

```bash
# Run directly with npx
npx gaia-scan

# Or install globally
npm install -g @jaguilar87/gaia
gaia-scan
```

This will:
1. Auto-detect your project structure (GitOps, Terraform, AppServices)
2. Create `.claude/` directory with symlinks to this package
3. Generate `project-context.json`
4. Create `settings.json` with hooks only (no permissions in settings.json)
5. Merge deny rules + allow permissions into `settings.local.json` (preserves existing user config)

No `CLAUDE.md` is generated -- orchestrator identity lives in `agents/gaia-orchestrator.md` and is activated via `settings.json: { "agent": "gaia-orchestrator" }`.

### Settings Architecture

Gaia separates hooks from permissions:

| File | Content | Strategy |
|------|---------|----------|
| `settings.json` | Hooks only (9 hook types) | Overwritten from template on each update |
| `settings.local.json` | Permissions (allow + deny rules) | Union merge — never removes user config |

This ensures your personal customizations (MCP servers, extra permissions) survive updates.

### Manual Installation

```bash
npm install @jaguilar87/gaia
```

Then create symlinks:

```bash
mkdir -p .claude && cd .claude
ln -s ../node_modules/@jaguilar87/gaia/agents agents
ln -s ../node_modules/@jaguilar87/gaia/tools tools
ln -s ../node_modules/@jaguilar87/gaia/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia/commands commands
ln -s ../node_modules/@jaguilar87/gaia/config config
ln -s ../node_modules/@jaguilar87/gaia/templates templates
ln -s ../node_modules/@jaguilar87/gaia/skills skills
```

## Usage

Once installed, the agent system is ready:

```bash
claude
```

The orchestrator identity is defined in `agents/gaia-orchestrator.md` and activated via `settings.json` agent config. Skills are loaded on-demand.

Skills and injection diagnosis:

```bash
npx gaia-skills-diagnose
# or with test probe:
npx gaia-skills-diagnose --run-tests
```

## Security

Gaia enforces a 6-layer security pipeline:

| Layer | Mechanism | Bypassable? |
|-------|-----------|-------------|
| Indirect execution detection | `bash -c`, `eval`, `python -c` wrappers | No (hook-level) |
| Blocked commands (regex) | 85+ regex patterns | No (symlink to npm package) |
| Blocked commands (semantic) | 70+ ordered-token rules | No (symlink to npm package) |
| Cloud pipe validator | Credential piping detection | No (hook-level) |
| Mutative verb detection | `ask` dialog for state-changing ops | User approves via native dialog |
| Settings deny rules | 147 deny rules in `settings.local.json` | Self-healing (restored each session) |

### Enterprise Deployment

For organization-wide enforcement, deploy `templates/managed-settings.template.json` as a managed settings policy via Claude.ai Admin Console. Managed settings have the highest precedence and cannot be overridden.

## Project Structure

```
gaia-dev/
├── agents/              # Agent definitions (8 agents) — specialist identities + tool grants
├── skills/              # Skill modules (32 skills) — injected procedural knowledge
├── hooks/               # Claude Code hooks — the event-driven pipeline
├── config/              # Configuration — routing, contracts, rules, git standards
├── commands/            # Slash commands — /gaia, /scan-project
├── build/               # Plugin manifests — hook + agent registration for Claude Code
├── templates/           # Installation templates — managed-settings for enterprise
├── bin/                 # CLI utilities (11 scripts) — gaia-doctor, gaia-scan, etc.
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
- [config/](./config/) - Configuration (contracts, git standards, universal rules)
- [commands/](./commands/) - Slash commands
- [build/](./build/) - Plugin manifests
- [bin/](./bin/) - CLI utilities
- [tests/](./tests/) - Test suite

## Requirements

- **Node.js:** >=18.0.0
- **Python:** >=3.9
- **Claude Code:** Latest version
- **Git:** >=2.30

## Project Context Management

Gaia uses a versioned project context as SSOT:

```bash
cd .claude
git clone git@bitbucket.org:yourorg/your-project-context.git project-context
```

## Support

- **Issues:** [GitHub Issues](https://github.com/metraton/gaia/issues)
- **Repository:** [github.com/metraton/gaia](https://github.com/metraton/gaia)
- **Author:** Jorge Aguilar <jorge.aguilar88@gmail.com>

## License

MIT License - See [LICENSE](./LICENSE) for details.
