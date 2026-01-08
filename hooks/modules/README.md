# Hooks Modular Architecture

This directory contains the modular hook system for gaia-ops.

## What is this?

A refactored, maintainable architecture for Claude Code hooks. Instead of monolithic 1000+ line files, the logic is split into focused modules that can be tested, maintained, and extended independently.

## Where does this fit?

```
Claude Code invokes hook
        |
        v
[pre_tool_use.py] ---------> [modules/security/*] -> Tier classification
        |                     [modules/tools/*]   -> Bash/Task validation
        v
    Tool executes
        |
        v
[post_tool_use.py] --------> [modules/audit/*]   -> Logging & metrics
                              [modules/agents/*]  -> Anomaly detection
```

## Module Structure

```
modules/
├── __init__.py           # Package marker
├── core/                 # Shared utilities
│   ├── paths.py          # find_claude_dir() - single source of truth
│   ├── state.py          # Pre/post hook state sharing
│   └── config_loader.py  # JSON config loading
│
├── security/             # Security classification
│   ├── tiers.py          # SecurityTier enum (T0-T3)
│   ├── safe_commands.py  # SAFE_COMMANDS_CONFIG + is_read_only_command()
│   ├── blocked_commands.py # Blocked patterns by category
│   └── gitops_validator.py # kubectl/helm/flux validation
│
├── tools/                # Tool-specific validators
│   ├── shell_parser.py   # Parse compound commands
│   ├── bash_validator.py # Bash command validation
│   └── task_validator.py # Task tool validation with context enforcement
│
├── workflow/             # Workflow phase management
│   ├── phase_validator.py # Pre/post phase validation
│   └── state_tracker.py   # Track current workflow phase
│
├── audit/                # Logging and metrics
│   ├── logger.py         # AuditLogger
│   ├── metrics.py        # MetricsCollector + FUNCTIONAL generate_summary
│   └── event_detector.py # CriticalEventDetector
│
└── agents/               # Subagent support
    ├── subagent_metrics.py # Capture workflow metrics
    └── anomaly_detector.py # Detect anomalies + signal Gaia
```

## Key Features

### Orchestrator Gate
The orchestrator is restricted to specific tools:
- `Read` - Reading context files
- `Task` - Delegating to agents
- `TodoWrite` - Managing task lists
- `AskUserQuestion` - Getting user input

This enforces the principle: "Orchestrator delegates, agents execute."

### Context Enforcement
Task invocations for project agents must include "# Project Context" in the prompt, ensuring the orchestrator properly provisions context via `context_provider.py`.

### State Sharing
Pre-hook saves state to `.claude/.hooks_state.json`, which post-hook reads to get:
- Security tier assigned
- Command executed
- Timestamp for duration calculation

### Functional Metrics
`generate_summary()` now actually works - reads JSONL metrics files and aggregates:
- Total executions
- Success rate
- Average duration
- Top command types
- Tier distribution

## Usage

### Entry Points

```bash
# Pre-hook (validation)
python3 pre_tool_use.py --test

# Post-hook (audit)
python3 post_tool_use.py --test
python3 post_tool_use.py --metrics
```

### Importing Modules

```python
from modules.security import SecurityTier, is_read_only_command
from modules.tools import BashValidator
from modules.audit import generate_summary

# Check if command is safe
is_safe, reason = is_read_only_command("ls -la")

# Validate Bash command
validator = BashValidator()
result = validator.validate("kubectl get pods")
print(f"Allowed: {result.allowed}, Tier: {result.tier}")

# Get metrics summary
summary = generate_summary(days=7)
print(f"Success rate: {summary['success_rate']:.1%}")
```

## Architecture Notes

The modular architecture maintains full backward compatibility with Claude Code's hook interface (stdin JSON format).

All security rules (safe commands, blocked patterns, tiers) are hardcoded in the Python modules for performance and simplicity - no external JSON config files needed.
