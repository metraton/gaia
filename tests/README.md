# Gaia-Ops Test Suite

Test suite to validate the Claude agent orchestration system.

## Metrics (2026-03-03)

| Metric | Value |
|--------|-------|
| **Total Tests** | 897 |
| **Pass Rate** | 100% |
| **Time** | ~0.6s (collection) |
| **Test Files** | 36 |

## Structure

```
tests/
├── fixtures/              # JSON fixtures (project-context AWS/GCP/full)
├── hooks/                 # Hook and security module tests
│   └── modules/
│       ├── security/      # safe_commands, blocked_commands, tiers, gitops_validator
│       ├── tools/         # bash_validator, shell_parser, task_validator
│       ├── core/          # paths, state
│       └── context/       # context_writer
├── integration/           # E2E tests for context enrichment and subagent lifecycle
├── layer1_prompt_regression/ # Prompt and skill regression tests
├── layer2_llm_evaluation/ # LLM evaluation tests (run separately)
├── layer3_e2e/            # End-to-end tests (run separately)
├── performance/           # Performance benchmarks
├── system/                # Structure, permissions, agents, configuration, schema compat
├── tools/                 # context_provider, episodic, pending_updates tests
├── test_cross_layer_consistency.py  # Cross-layer consistency validation
├── conftest.py            # Shared fixtures and markers
└── promptfoo.yaml         # Promptfoo evaluation config
```

## Running Tests

```bash
# Layer 1 tests (default, fast)
python3 -m pytest tests/ -v --ignore=tests/layer2_llm_evaluation --ignore=tests/layer3_e2e

# By category
python3 -m pytest tests/system/ -v
python3 -m pytest tests/hooks/ -v
python3 -m pytest tests/tools/ -v

# Layer 2 (LLM evaluation)
python3 -m pytest tests/layer2_llm_evaluation/ -v -m llm

# Layer 3 (end-to-end)
python3 -m pytest tests/layer3_e2e/ -v -m e2e

# With coverage
python3 -m pytest tests/ --cov=hooks --cov=tools --cov-report=term
```

## Tests by File

| File | Tests | Category |
|------|-------|----------|
| `test_safe_commands.py` | 111 | Security |
| `test_blocked_commands.py` | 67 | Security |
| `test_tiers.py` | 54 | Security |
| `test_permissions_system.py` | 52 | System |
| `test_episodic.py` | 46 | Tools |
| `test_gitops_validator.py` | 42 | Security |
| `test_task_validator.py` | 41 | Tools |
| `test_shell_parser.py` | 39 | Tools |
| `test_bash_validator.py` | 37 | Tools |
| `test_skill_loader.py` | 36 | Skills |
| `test_cross_layer_consistency.py` | 24 | Cross-Layer |
| `test_state.py` | 20 | Core |
| `test_paths.py` | 17 | Core |
| `test_directory_structure.py` | 14 | System |
| `test_context_provider.py` | 11 | Tools |
| `test_agent_definitions.py` | 11 | System |
| `test_configuration_files.py` | 9 | System |
| `test_schema_compatibility.py` | 7 | System |

## Pending Coverage

Modules without dedicated tests:
- `hooks/modules/audit/logger.py`, `metrics.py`

## Dependencies

```bash
pip install pytest pytest-cov
```

---

**Updated:** 2026-03-03 | **Tests:** 897
