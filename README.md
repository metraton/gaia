# @jaguilar87/gaia-ops

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia-ops.svg)](https://www.npmjs.com/package/@jaguilar87/gaia-ops)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia-ops.svg)](https://nodejs.org)

Multi-agent orchestration system for Claude Code - DevOps automation toolkit.

## Overview

**Gaia-Ops** provides a complete agent orchestration system for Claude Code, enabling intelligent automation of DevOps workflows through specialized AI agents.

### Features

- **Multi-cloud support** - GCP, AWS, Azure-ready
- **6 agents** - terraform-architect, gitops-operator, cloud-troubleshooter, devops-developer, speckit-planner, gaia (meta-agent)
- **Contracts as SSOT** - Cloud-agnostic base contracts with per-cloud extensions (GCP, AWS)
- **Episodic Memory** - Persistent memory system for operational patterns
- **Approval gates** for T3 operations
- **Git commit validation** with Conventional Commits
- **17 skills** - Injected procedural knowledge modules for agents
- **897 tests** at 100% passing

## Installation

### Quick Start

```bash
# Run directly with npx
npx gaia-init

# Or install globally
npm install -g @jaguilar87/gaia-ops
gaia-init
```

This will:
1. Auto-detect your project structure (GitOps, Terraform, AppServices)
2. Install Claude Code if not present
3. Create `.claude/` directory with symlinks to this package
4. Generate `CLAUDE.md` and `project-context.json`

### Manual Installation

```bash
npm install @jaguilar87/gaia-ops
```

Then create symlinks:

```bash
mkdir -p .claude && cd .claude
ln -s ../node_modules/@jaguilar87/gaia-ops/agents agents
ln -s ../node_modules/@jaguilar87/gaia-ops/tools tools
ln -s ../node_modules/@jaguilar87/gaia-ops/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia-ops/commands commands
ln -s ../node_modules/@jaguilar87/gaia-ops/config config
ln -s ../node_modules/@jaguilar87/gaia-ops/templates templates
ln -s ../node_modules/@jaguilar87/gaia-ops/skills skills
ln -s ../node_modules/@jaguilar87/gaia-ops/speckit speckit
```

## Usage

Once installed, the agent system is ready:

```bash
claude-code
```

Claude Code will automatically load `CLAUDE.md` and have access to all agents via `.claude/`.

Skills and injection diagnosis:

```bash
npx gaia-skills-diagnose
# or with test probe:
npx gaia-skills-diagnose --run-tests
```

## Project Structure

```
node_modules/@jaguilar87/gaia-ops/
├── agents/              # Agent definitions (6 agents)
├── skills/              # Skill modules (17 skills)
├── tools/               # Orchestration tools
├── hooks/               # Claude Code hooks (modular architecture)
├── commands/            # Slash commands (7 speckit commands)
├── config/              # Configuration (contracts, git standards, rules)
├── templates/           # Installation templates
├── speckit/             # Spec-Kit framework (scripts + templates)
├── bin/                 # CLI utilities (10 scripts)
└── tests/               # Test suite (897 tests)
```

## API

```javascript
import { getAgentPath, getToolPath, getConfigPath } from '@jaguilar87/gaia-ops';

const agentPath = getAgentPath('gitops-operator');
const toolPath = getToolPath('context_provider.py');
```

## Versioning

This package follows [Semantic Versioning](https://semver.org/):

- **MAJOR:** Breaking changes
- **MINOR:** New features
- **PATCH:** Bug fixes

Current version: **4.0.0**

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## Documentation

- [INSTALL.md](./INSTALL.md) - Installation guide
- [config/](./config/) - Configuration (contracts, git standards, universal rules)
- [agents/](./agents/) - Agent definitions
- [skills/](./skills/) - Skill modules
- [commands/](./commands/) - Slash commands (spec-kit)
- [hooks/](./hooks/) - Hook system (security, validation, audit)
- [speckit/](./speckit/) - Spec-Kit framework
- [bin/](./bin/) - CLI utilities
- [tests/](./tests/) - Test suite

## Requirements

- **Node.js:** >=18.0.0
- **Python:** >=3.9
- **Claude Code:** Latest version
- **Git:** >=2.30

## Project Context Management

Gaia-Ops uses a versioned project context as SSOT:

```bash
cd .claude
git clone git@bitbucket.org:yourorg/your-project-context.git project-context
```

## Support

- **Issues:** [GitHub Issues](https://github.com/metraton/gaia-ops/issues)
- **Repository:** [github.com/metraton/gaia-ops](https://github.com/metraton/gaia-ops)
- **Author:** Jorge Aguilar <jaguilar1897@gmail.com>

## License

MIT License - See [LICENSE](./LICENSE) for details.
