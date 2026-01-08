# Gaia-Ops Configuration

**[Versión en español](README.md)**

This directory contains all system configuration: operational JSON files, architectural documentation, development guides, and standards.

## Purpose

Centralizes all configuration and documentation files consumed programmatically or referentially by gaia-ops components.

## Configuration Files (JSON)

| File | Purpose | Consumed by |
|------|---------|-------------|
| `clarification_rules.json` | Clarification engine rules (Phase 0) | `tools/3-clarification/engine.py` |
| `context-contracts.aws.json` | Context schema for AWS agents | `tools/2-context/context_provider.py` |
| `context-contracts.gcp.json` | Context schema for GCP agents | `tools/2-context/context_provider.py` |
| `git_standards.json` | Programmatic Git standards | `tools/4-validation/commit_validator.py` |
| `metrics_targets.json` | System performance targets | `bin/gaia-metrics.js` |
| `universal-rules.json` | Universal orchestration rules | `index.js` |

## Documentation (Markdown)

### Architecture

| File | Description |
|------|-------------|
| `agent-catalog.md` | Complete agent catalog with capabilities and examples |
| `delegation-matrix.md` | Orchestrator delegation matrix |
| `orchestration-workflow.md` | Complete Phase 0-6 orchestrator flow |

### Development Guides

| File | Description |
|------|-------------|
| `documentation-principles.md` | Principles for writing documentation |
| `git-standards.md` | Git standards for commits and PRs |

### Standards (`standards/`)

| File | Description |
|------|-------------|
| `standards/security-tiers.md` | T0-T3 tier definitions |
| `standards/output-format.md` | Output format for agents |
| `standards/command-execution.md` | Command execution standards |
| `standards/anti-patterns.md` | Anti-patterns to avoid |

## Usage

### For Agents

```python
import json
from pathlib import Path

# Load configuration
config_path = Path('.claude/config/git_standards.json')
with open(config_path) as f:
    standards = json.load(f)
```

### For Developers

```bash
# View configurations
cat .claude/config/git_standards.json | jq .

# Validate JSON
jq empty .claude/config/*.json

# View documentation
cat .claude/config/orchestration-workflow.md
```

## Structure

```
config/
├── *.json                          # 6 configuration files
├── *.md                            # 8 documentation files
└── standards/                      # System standards
    ├── README.md
    ├── security-tiers.md
    ├── output-format.md
    ├── command-execution.md
    └── anti-patterns.md
```

## References

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Commands](../commands/README.md)

---

**Updated:** 2026-01-08 | **JSON Files:** 6 | **MD Files:** 8
