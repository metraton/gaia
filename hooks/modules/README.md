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
│   ├── __init__.py
│   ├── paths.py          # find_claude_dir() - single source of truth
│   └── state.py          # Pre/post hook state sharing
│
├── security/             # Security classification & approval
│   ├── __init__.py
│   ├── tiers.py          # SecurityTier enum (T0-T3)
│   ├── blocked_commands.py # Blocked patterns by category
│   ├── mutative_verbs.py   # CLI-agnostic verb detector, nonce-based deny
│   ├── approval_grants.py  # Nonce-based approval grant management
│   ├── approval_constants.py # Approval system constants
│   ├── approval_messages.py  # Approval denial message formatting
│   ├── approval_scopes.py   # Approval scope definitions
│   ├── command_semantics.py  # Command semantic analysis
│   ├── interactive_handler.py # Auto-append non-interactive flags
│   └── gitops_validator.py # kubectl/helm/flux validation
│
├── tools/                # Tool-specific validators
│   ├── __init__.py
│   ├── shell_parser.py   # Parse compound commands
│   ├── bash_validator.py # Bash command validation (orchestrates pipeline)
│   ├── task_validator.py # Task tool validation with context enforcement
│   ├── cloud_pipe_validator.py # Cloud pipe/redirect/chain check
│   └── hook_response.py  # Standardized hook response formatting
│
├── context/              # Context management
│   ├── __init__.py
│   └── context_writer.py # Write context updates
│
├── validation/           # Commit validation
│   ├── __init__.py
│   └── commit_validator.py # Conventional Commits enforcement
│
├── workflow/             # Workflow support (reserved)
│   └── __init__.py
│
├── audit/                # Logging and metrics
│   ├── __init__.py
│   ├── logger.py         # AuditLogger
│   ├── metrics.py        # MetricsCollector + FUNCTIONAL generate_summary
│   └── event_detector.py # CriticalEventDetector
│
└── agents/               # Subagent support
    ├── __init__.py
    └── response_contract.py # Agent response contract validation
```

## Key Features

### Orchestrator Gate
The orchestrator is restricted to two tools only:
- `Agent` - Delegating to agents
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
from modules.security import SecurityTier, classify_command_tier, is_blocked_command
from modules.security import CATEGORY_MUTATIVE, CATEGORY_READ_ONLY
from modules.tools import BashValidator
from modules.audit import generate_summary

# Check if command is permanently blocked
result = is_blocked_command("kubectl delete namespace production")
print(f"Blocked: {result.blocked}, Reason: {result.reason}")

# Classify command tier
tier = classify_command_tier("kubectl get pods")
print(f"Tier: {tier}")  # SecurityTier.T0

# Validate Bash command (full pipeline: blocked -> mutative verbs -> safe by elimination)
validator = BashValidator()
result = validator.validate("kubectl get pods")
print(f"Allowed: {result.allowed}, Tier: {result.tier}")

# Get metrics summary
summary = generate_summary(days=7)
print(f"Success rate: {summary['success_rate']:.1%}")
```

## Architecture Notes

The modular architecture maintains full backward compatibility with Claude Code's hook interface (stdin JSON format).

All security rules (blocked patterns, mutative verbs, tiers) are hardcoded in the Python modules for performance and simplicity - no external JSON config files needed.

### Validation Order (Defense-in-Depth)
bash_validator checks commands in this order (short-circuit on first match):
1. **Blocked commands** (blocked_commands.py) — permanently denied patterns, exit 2
2. **Claude footer stripping** — transparent via updatedInput
3. **Commit message validation** — conventional commits enforcement
4. **Cloud pipe/redirect/chain check** (cloud_pipe_validator.py) — corrective deny
5. **Mutative verbs** (mutative_verbs.py) — CLI-agnostic verb detector, nonce-based deny
6. **GitOps validation** (gitops_validator.py) — kubectl/helm/flux policy enforcement
7. **Everything else** — SAFE by elimination (auto-approved)

### Tier Classification
- **T0**: Read-only (get, list, describe, show)
- **T1**: Local validation (validate, lint, fmt, check)
- **T2**: Simulation (plan, template, diff, --dry-run)
- **T3**: State-modifying (apply, delete, push, commit)
