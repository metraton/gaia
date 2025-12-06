# Execution Standards

Shared standards extracted from agent prompts to reduce duplication and ensure consistency.

## Purpose

These standards are pre-loaded by `context_provider.py` using a **hybrid intelligence strategy**:

1. **Always Loaded** (~120 lines): Critical, short standards every agent needs
2. **On-Demand** (~300 lines): Loaded only when task keywords match

## Files

| File | Lines | Purpose | Pre-load Strategy |
|------|-------|---------|-------------------|
| `security-tiers.md` | ~50 | T0-T3 tier definitions | Always |
| `output-format.md` | ~70 | Report structure, icons | Always |
| `command-execution.md` | ~130 | Execution pillars, path handling | On-demand (kubectl, terraform, gcloud...) |
| `anti-patterns.md` | ~190 | Common mistakes by tool | On-demand (apply, create, deploy...) |

## Token Savings

| Scenario | Standards Loaded | Tokens Saved |
|----------|-----------------|--------------|
| Read-only query | 2 (security + output) | ~800 tokens |
| kubectl check | 3 (+ command) | ~400 tokens |
| terraform apply | 4 (all) | 0 (full load needed) |

**Average savings:** ~600 tokens per agent invocation

## How It Works

```python
# In context_provider.py

# Always loaded
ALWAYS_PRELOAD_STANDARDS = {
    "security_tiers": "security-tiers.md",
    "output_format": "output-format.md",
}

# Loaded based on task keywords
ON_DEMAND_STANDARDS = {
    "command_execution": {
        "file": "command-execution.md",
        "triggers": ["kubectl", "terraform", "gcloud", "apply", "plan", ...]
    },
    "anti_patterns": {
        "file": "anti-patterns.md",
        "triggers": ["create", "apply", "deploy", "delete", ...]
    }
}
```

## Adding New Standards

1. Create a new `.md` file in this directory
2. Keep it focused and concise (<100 lines ideal)
3. Add to `ALWAYS_PRELOAD_STANDARDS` or `ON_DEMAND_STANDARDS` in `context_provider.py`
4. Choose triggers wisely - too broad = wasted tokens, too narrow = missing context

## Maintenance

When updating agent prompts, extract any duplicated content here:

1. Identify repeated sections across 2+ agents
2. Extract to a new standard file
3. Update agents to reference standards (or rely on automatic pre-loading)
4. Test with `python3 tools/2-context/context_provider.py agent_name "test task"`
