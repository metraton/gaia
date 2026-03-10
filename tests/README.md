# Gaia-Ops Test Suite

Test suite to validate the Claude agent orchestration system.

## Metrics (2026-03-08)

| Metric | Value |
|--------|-------|
| **Total Tests** | 1462 |
| **Pass Rate** | 100% |
| **Time** | ~0.25s (collection) |
| **Test Files** | 46 |

## Structure

```
tests/
├── fixtures/              # JSON fixtures (project-context AWS/GCP/full)
├── hooks/                 # Hook and security module tests
│   └── modules/
│       ├── security/      # mutative_verbs, blocked_commands, tiers, gitops_validator
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
| `test_mutative_verbs.py` | ~50 | Security |
| `test_blocked_commands.py` | 126 | Security |
| `test_gitops_validator.py` | 80 | Security |
| `test_tiers.py` | 66 | Security |
| `test_task_validator.py` | 59 | Tools |
| `test_bash_validator.py` | 57 | Tools |
| `test_permissions_system.py` | 52 | System |
| `test_episodic.py` | 48 | Tools |
| `test_approval_grants.py` | 44 | Security |
| `test_pending_updates.py` | 42 | Tools |
| `test_shell_parser.py` | 39 | Tools |
| `test_routing_table.py` | 36 | Prompt Regression |
| `test_cross_layer_consistency.py` | 26 | Cross-Layer |
| `test_context_writer.py` | 26 | Context |
| `test_state.py` | 20 | Core |
| `test_skill_content_rules.py` | 19 | Prompt Regression |
| `test_cloud_pipe_validator.py` | 19 | Tools |
| `test_subagent_stop_e2e.py` | 18 | Integration |
| `test_subagent_lifecycle.py` | 17 | Integration |
| `test_paths.py` | 17 | Core |
| `test_agent_frontmatter.py` | 15 | Prompt Regression |
| `test_directory_structure.py` | 14 | System |
| `test_agent_prompt_content.py` | 14 | Prompt Regression |
| `test_security_tier_consistency.py` | 13 | Prompt Regression |
| `test_response_contract.py` | 13 | Agents |
| `test_deep_merge.py` | 11 | Tools |
| `test_context_provider.py` | 11 | Tools |
| `test_agent_definitions.py` | 11 | System |
| `test_agent_behavior.py` | 11 | LLM Evaluation |
| `test_subagent_stop_discovery.py` | 11 | Hooks |
| `test_review_engine.py` | 10 | Tools |
| `test_context_contracts.py` | 10 | Prompt Regression |
| `test_context_enrichment.py` | 10 | Integration |
| `test_schema_compatibility.py` | 9 | System |
| `test_configuration_files.py` | 9 | System |
| `test_context_performance.py` | 9 | Performance |
| `test_installation_smoke.py` | 8 | E2E |
| `test_skills_cross_reference.py` | 8 | Prompt Regression |
| `test_pre_tool_use_resume.py` | 6 | Hooks |
| `test_surface_router.py` | 5 | Tools |
| `test_hook_lifecycle.py` | 5 | E2E |
| `test_pre_tool_use_response_contract.py` | 5 | Hooks |
| `test_approval_scopes.py` | 5 | Security |
| `test_command_semantics.py` | 4 | Security |
| `test_nonce_approval_relay_e2e.py` | 3 | Integration |

## Pending Coverage

Modules without dedicated tests:
- `hooks/modules/audit/logger.py`, `metrics.py`

## Dependencies

```bash
pip install pytest pytest-cov
```

---

**Updated:** 2026-03-08 | **Tests:** 1462
