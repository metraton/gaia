# @jaguilar87/gaia-ops

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia-ops.svg)](https://www.npmjs.com/package/@jaguilar87/gaia-ops)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia-ops.svg)](https://nodejs.org)

**[Version en espanol](README.md)**

Multi-agent orchestration system for Claude Code - DevOps automation toolkit.

## Overview

**Gaia-Ops** provides a complete agent orchestration system for Claude Code, enabling intelligent automation of DevOps workflows through specialized AI agents.

### Features

- **Multi-cloud support** - GCP, AWS, Azure-ready
- **6 specialist agents** (terraform-architect, gitops-operator, cloud-troubleshooter, cloud-troubleshooter, devops-developer, Gaia)
- **3 meta-agents** (Explore, Plan, Gaia)
- **Episodic Memory** - Memory system for operational patterns
- **Hybrid standards pre-loading** - 78% token reduction per invocation
- **Approval gates** for T3 operations
- **Git commit validation** with Conventional Commits
- **359 tests** at 100% passing

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
```

## Usage

Once installed, the agent system is ready:

```bash
claude-code
```

Claude Code will automatically load `CLAUDE.md` and have access to all agents via `.claude/`.

## Project Structure

```
node_modules/@jaguilar87/gaia-ops/
├── agents/              # Agent definitions
├── tools/               # Orchestration tools
├── hooks/               # Claude Code hooks
├── commands/            # Slash commands
├── config/              # Configuration and documentation
├── templates/           # Installation templates
├── speckit/             # Spec-Kit methodology
└── tests/               # Test suite (359 tests)
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

Current version: **3.0.0**

See [CHANGELOG.md](./CHANGELOG.md) for version history.

## Documentation

- [config/AGENTS.md](./config/AGENTS.md) - System overview
- [config/orchestration-workflow.md](./config/orchestration-workflow.md) - Orchestration workflow
- [config/git-standards.md](./config/git-standards.md) - Git standards
- [config/context-contracts.md](./config/context-contracts.md) - Context contracts

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
