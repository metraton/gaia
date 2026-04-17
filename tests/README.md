# Tests

The test suite is built as a three-layer pyramid, and understanding the pyramid matters before running anything. Each layer exercises a different part of Gaia — and each layer costs more to run than the one below it. Layer 1 is fast, deterministic pytest that runs on every commit. Layer 2 is LLM evaluation that takes minutes and burns tokens. Layer 3 is end-to-end with real Claude Code sessions, reserved for pre-release verification.

The default CI path runs Layer 1 only. It covers hooks, modules, routing, context contracts, and the security pipeline — everything that can be verified without calling an LLM. Layer 2 and Layer 3 are opt-in: you run them when you are validating agent behavior, not code behavior.

The practical rule: if you changed a hook module, a config file, or a utility function, Layer 1 is sufficient. If you changed an agent's scope, an agent's skills, or a prompt injection path, Layer 2 catches regressions that Layer 1 cannot see. Layer 3 exists for the final check before publishing — it confirms the packaged plugin works when installed from the registry.

## Cuándo se activa

The test suite activates through explicit invocation — either by a developer at the terminal or by the CI pipeline. It is not triggered by Claude Code events.

```
Developer runs: pytest tests/
        |
    Layer 1 runs by default (fast, deterministic, no LLM calls)
        |
Developer opts into higher layers:
        |
    pytest tests/layer2_llm_evaluation/ -m llm    <- LLM behavior checks
    pytest tests/layer3_e2e/ -m e2e               <- Full Claude Code session
```

```
CI pipeline triggered on push/PR
        |
Runs Layer 1 only (--ignore=layer2 --ignore=layer3)
        |
Pre-release pipeline additionally runs Layer 3 against published artifact
```

**When to run each layer:**

| Layer | When to run | Cost |
|-------|-------------|------|
| Layer 1 | Every commit, every PR | Seconds, no external calls |
| Layer 2 | Changed agent behavior, skills, or prompt injection | Minutes, LLM tokens |
| Layer 3 | Before publishing a release (beta or stable) | Minutes, full session spawn |

## Qué hay aquí

```
tests/
├── conftest.py                      # Shared fixtures and markers
├── promptfoo.yaml                   # Promptfoo evaluation config (Layer 2)
├── test_cross_layer_consistency.py  # Cross-layer consistency validation
├── test_smoke_hook_pipeline.py      # Smoke check for hook pipeline
├── hooks/                           # Layer 1: hook and security module tests
│   └── modules/
│       ├── security/                # mutative_verbs, blocked_commands, tiers, gitops_validator
│       ├── tools/                   # bash_validator, shell_parser, task_validator
│       ├── core/                    # paths, state
│       └── context/                 # context_writer
├── integration/                     # Layer 1: E2E tests for context enrichment and subagent lifecycle
├── layer1_prompt_regression/        # Layer 1: prompt and skill regression tests
├── layer2_llm_evaluation/           # Layer 2: LLM behavior evaluation (manual, uses LLM tokens)
├── layer3_e2e/                      # Layer 3: end-to-end with real Claude Code session (pre-release)
├── performance/                     # Performance benchmarks
├── system/                          # Layer 1: structure, permissions, agents, configuration, schema
├── tools/                           # Layer 1: context_provider, episodic, pending_updates tests
├── cli/                             # CLI tool tests
├── bin/                             # bin/ script tests
├── e2e/                             # E2E installation and lifecycle tests
├── evidence/                        # Evidence/output captures for diagnostic review
└── fixtures/                        # JSON fixtures (project-context AWS/GCP/full)
```

## Convenciones

**Running the pyramid:**

```bash
# Layer 1 — default, fast, always runs in CI
python3 -m pytest tests/ -v --ignore=tests/layer2_llm_evaluation --ignore=tests/layer3_e2e

# Layer 1 by category
python3 -m pytest tests/system/ -v
python3 -m pytest tests/hooks/ -v
python3 -m pytest tests/tools/ -v

# Layer 2 — LLM evaluation (opt-in)
python3 -m pytest tests/layer2_llm_evaluation/ -v -m llm

# Layer 3 — end-to-end (pre-release)
python3 -m pytest tests/layer3_e2e/ -v -m e2e

# Coverage report (Layer 1)
python3 -m pytest tests/ --cov=hooks --cov=tools --cov-report=term
```

**Markers:** Layer 2 tests use `@pytest.mark.llm`, Layer 3 tests use `@pytest.mark.e2e`. The default pytest run ignores both — you must opt in with `-m` or by pointing pytest directly at the layer directory.

**Fixtures:** Shared fixtures live in `conftest.py`. JSON test data (project-context variants) lives in `fixtures/`.

**New tests:** Place in the directory matching the component under test (`hooks/modules/security/`, `tools/`, `system/`, etc.). If the test calls an LLM, it belongs in `layer2_llm_evaluation/`. If it spawns a Claude Code session, it belongs in `layer3_e2e/`.

**Dependencies:**

```bash
pip install pytest pytest-cov
```

## Ver también

- [`hooks/README.md`](../hooks/README.md) — code under test in Layer 1
- [`agents/README.md`](../agents/README.md) — behavior under test in Layer 2
- [`bin/gaia-doctor.js`](../bin/gaia-doctor.js) — separate health check invoked during Layer 3
- [`conftest.py`](./conftest.py) — shared pytest fixtures and markers
