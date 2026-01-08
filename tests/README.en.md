# Claude Agent System - Test Suite

**[Version en espanol](README.md)**

Test suite to validate the Claude agent orchestration system.

## Metrics (2025-12-06)

| Metric | Value |
|--------|-------|
| **Total Tests** | 359 |
| **Pass Rate** | 100% |
| **Time** | ~2.2s |
| **Routing Accuracy** | 92.7% |

## Structure

```
tests/
├── system/           # Structure and integrity tests
├── tools/            # Routing and context tests
├── validators/       # Approval and commit tests
└── integration/      # End-to-end hook tests
```

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# By category
python3 -m pytest tests/system/ -v
python3 -m pytest tests/tools/ -v
python3 -m pytest tests/validators/ -v
python3 -m pytest tests/integration/ -v

# With coverage
python3 -m pytest tests/ --cov=.claude/tools --cov-report=term
```

## Test Categories

### system/ (~10 tests)
- Directory structure
- Agent definitions
- Configuration files

### tools/ (~15 tests)
- Agent router (semantic routing)
- Context provider (context generation)

### validators/ (~10 tests)
- Approval gate (approval workflow)
- Commit validator (Conventional Commits)

### integration/ (~74 tests)
- Pre/post hook validation
- PolicyEngine command classification
- GitOps security
- Settings permission matching

## Dependencies

```bash
pip install pytest pytest-cov
```

## Routing Golden Set

Accuracy test evaluates 26 semantic requests:

| Agent | Accuracy |
|-------|----------|
| terraform-architect | 95% |
| gitops-operator | 93% |
| cloud-troubleshooter | 90% |
| devops-developer | 92% |

---

**Updated:** 2025-12-06 | **Tests:** 359
