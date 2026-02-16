# Claude Agent System - Test Suite

**[Version en espanol](README.md)**

Test suite to validate the Claude agent orchestration system.

## Metrics (2026-02-13)

| Metric | Value |
|--------|-------|
| **Total Tests** | 505 |
| **Pass Rate** | 100% |
| **Time** | ~0.9s |
| **Test Files** | 15 |

## Structure

```
tests/
├── fixtures/         # JSON fixtures (project-context AWS/GCP/full)
├── hooks/            # Hook and security module tests
│   └── modules/
│       ├── security/ # safe_commands, blocked_commands, tiers
│       ├── tools/    # bash_validator, shell_parser, task_validator
│       ├── core/     # config_loader, paths, state
│       └── skills/   # (pending: skill_loader)
├── system/           # Structure, permissions, agents, configuration, schema compat
└── tools/            # context_provider tests
```

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# By category
python3 -m pytest tests/system/ -v
python3 -m pytest tests/hooks/ -v
python3 -m pytest tests/tools/ -v

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
| `test_task_validator.py` | 41 | Tools |
| `test_shell_parser.py` | 39 | Tools |
| `test_bash_validator.py` | 37 | Tools |
| `test_state.py` | 20 | Core |
| `test_config_loader.py` | 18 | Core |
| `test_paths.py` | 17 | Core |
| `test_directory_structure.py` | 14 | System |
| `test_context_provider.py` | 11 | Tools |
| `test_agent_definitions.py` | 11 | System |
| `test_configuration_files.py` | 9 | System |
| `test_schema_compatibility.py` | 4 | System |

## Pending Coverage

Modules without dedicated tests:
- `hooks/modules/skills/skill_loader.py` (313 lines)
- `hooks/modules/security/gitops_validator.py`
- `tools/memory/episodic.py`
- `hooks/modules/audit/event_detector.py`, `logger.py`, `metrics.py`

## Dependencies

```bash
pip install pytest pytest-cov
```

---

**Updated:** 2026-02-13 | **Tests:** 505
