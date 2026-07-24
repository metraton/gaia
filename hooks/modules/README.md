# Hooks Modular Architecture

This directory contains the modular hook system for Gaia.

## What is this?

A refactored, maintainable architecture for Claude Code hooks. Instead of monolithic 1000+ line files, the logic is split into focused modules that can be tested, maintained, and extended independently.

## Where does this fit?

```
Claude Code invokes hook
        |
        v
[session_start.py] --------> [modules/context/context_freshness] -> Staleness check
        |                     [modules/scanning/scan_trigger]     -> Auto-refresh
        v
[pre_tool_use.py] ---------> [modules/security/*] -> Tier classification
        |                     [modules/tools/*]   -> Bash/Task validation
        v
    Tool executes
        |
        v
[post_tool_use.py] --------> [modules/audit/*]    -> Logging & metrics
                              [modules/session/*]  -> Session context updates
                              [modules/agents/*]   -> Anomaly detection
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
│   ├── subagent_memory_write_guard.py # Blocks `gaia memory` writes from subagents (non-operator)
│   ├── gaia_db_write_guard.py # Blocks direct sqlite3 writes to gaia.db (categorical)
│   ├── protected_path_guard.py # Blocks Bash writes into the .claude/ tree (hooks/settings) (categorical)
│   ├── source_lexer.py     # Per-language comment/string lexer for the JS script lane
│   ├── approval_grants.py  # Nonce-based approval grant management
│   ├── approval_constants.py # Approval system constants
│   ├── approval_messages.py  # Approval denial message formatting
│   ├── approval_scopes.py   # Approval scope definitions
│   └── command_semantics.py  # Command semantic analysis
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
│   ├── context_writer.py # Write context updates
│   └── context_freshness.py     # Check staleness for SessionStart
│
├── scanning/             # Scan triggering
│   ├── __init__.py
│   └── scan_trigger.py   # Lightweight scan invocation for SessionStart
│
├── session/              # Session context management
│   ├── __init__.py
│   └── session_context_writer.py # Write critical events to session context
│
├── validation/           # Commit validation
│   ├── __init__.py
│   └── commit_validator.py # Conventional Commits enforcement
│
├── audit/                # Logging and metrics
│   ├── __init__.py
│   ├── logger.py         # AuditLogger
│   ├── metrics.py        # MetricsCollector + FUNCTIONAL generate_summary
│   └── event_detector.py # CriticalEventDetector
│
└── agents/               # Subagent support
    ├── __init__.py
    ├── response_contract.py # Agent response contract validation
    ├── contract_validator.py # Parse + resolve the agent_contract_handoff fence
    ├── dispatch_binding.py   # Born-at-dispatch row: extract + validate + birth the nascent handoff row from dispatch metadata
    ├── handoff_persister.py  # Persist/finalize the terminal agent_contract_handoffs row
    ├── skill_injection_verifier.py # Verify required skills were injected
    ├── state_tracker.py      # Legal agent_state transitions (_LEGAL_TRANSITIONS)
    ├── task_info_builder.py  # Build task_info from SubagentStop hook data
    ├── transcript_analyzer.py # Analyze subagent transcript on stop
    └── transcript_reader.py  # Read the subagent transcript file
```

### `dispatch_binding.py` (born-at-dispatch, plan 34)

At dispatch time (PreToolUse:Agent / SubagentStart), the hook BIRTHS the
nascent `agent_contract_handoffs` row from the dispatch metadata, stamping the
four binding coordinates (`plan_task_id`, `plan_id`, `parent_handoff_id`,
`kind`) and validating their REFERENTIAL INTEGRITY before the row is born:

- `extract_dispatch_binding(metadata)` — best-effort parse of the dispatch
  prompt: `plan_id=`, `task_id=` (dropped for a verifier turn, which binds by
  parent), `parent_handoff_id=`, and `turn_role`/`kind` inferred from the
  target agent name.
- `validate_dispatch_binding(...)` — a `task_execution` kind requires a
  resolvable, dispatchable (`status='pending'`) `plan_task_id`; a verifier turn
  (`turn_role='verifier'`) requires a resolvable `parent_handoff_id` (the
  producer handoff it verifies). `kind` is a pure label — never rejected for its
  value. Raises `DispatchBindingError` (with a machine-readable `reason`) when a
  coordinate does not resolve; the dispatch is never blocked (the row simply is
  not born).
- `birth_dispatched_row(...)` — validates the binding, then writes one
  `agent_state='DISPATCHED'` row via `gaia.store.writer.insert_dispatched_handoff`.
  Idempotent: a re-dispatch of the same `contract_id` never births a second row.

The finalize gate keys on this binding's `plan_task_id`
(`hooks/adapters/claude_code.py::_blind_verification_required`), not on the
emitting agent's role.

## Key Features

### Orchestrator Gate
The orchestrator is restricted to four tools:
- `Agent` -- dispatch work to specialist agents
- `SendMessage` -- resume a previously spawned agent
- `AskUserQuestion` -- get clarification or approval from the user
- `Skill` -- load on-demand procedures

This enforces the principle: "Orchestrator delegates, agents execute."

### SendMessage Validation (PreToolUse matcher)
SendMessage is validated as a PreToolUse event (not a separate hook event):
- Agent ID format check (must match `/^a[0-9a-f]{5,}$/`)
- Non-empty message required
- Grant activation is handled by ElicitationResult hook (user approval via AskUserQuestion)

### Context Enforcement
Task invocations for project agents inject project-context via `context_provider.py`.

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

# Metrics
gaia metrics
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
0. **Indirect execution detection** — `bash -c`, `eval`, `python -c` etc. → ask or block
0b. **Categorical write guards** — gaia_db_write_guard.py (direct sqlite3 writes to gaia.db), subagent_memory_write_guard.py (`gaia memory` writes from non-operator subagents), protected_path_guard.py (Bash writes into the `.claude/` hooks/settings tree) → exit 2, not approvable
1. **Blocked commands** (blocked_commands.py) — permanently denied patterns, exit 2
2. **Claude footer stripping** — transparent via updatedInput
3. **Commit message validation** — conventional commits enforcement
4. **Cloud pipe/redirect/chain check** (cloud_pipe_validator.py) — corrective deny
5. **Mutative verbs** (mutative_verbs.py) — CLI-agnostic verb detector, native `ask` dialog
6. **Everything else** — SAFE by elimination (auto-approved)

### Tier Classification
- **T0**: Read-only (get, list, describe, show)
- **T1**: Local validation (validate, lint, fmt, check)
- **T2**: Simulation (plan, template, diff, --dry-run)
- **T3**: State-modifying (apply, delete, push, commit)
