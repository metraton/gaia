---
name: developer-patterns
description: Use when creating, modifying, or reviewing application code in Node.js/TypeScript or Python
user-invocable: false
---

# Developer Patterns

Project-agnostic conventions for application development. Use values from your injected project-context — never hardcode environment-specific configuration.

For config file templates (tsconfig.json, pyproject.toml, jest.config.ts), read `reference.md` in this directory.

## Node.js / TypeScript

### Project Structure

```
src/
├── index.ts              # Entry point / public API
├── {module}/
│   ├── index.ts          # Module public API
│   ├── {module}.ts       # Implementation
│   └── {module}.test.ts  # Co-located tests
├── types/                # Shared type definitions
└── utils/                # Shared utilities
```

### Toolchain

| Concern | Tool |
|---------|------|
| Type checking | TypeScript (`strict: true`) |
| Linting | ESLint |
| Formatting | Prettier |
| Testing | Jest or Vitest |
| Pre-commit | Husky + lint-staged |
| Security | `npm audit` |

### Key Conventions

- **Strict TypeScript** — `strict: true`, `noImplicitAny: true`, `strictNullChecks: true`
- **Tests co-located** — `{file}.test.ts` next to `{file}.ts`, not in a separate `/tests` folder
- **Absolute imports** — configure `paths` in tsconfig, never `../../../`
- **No barrel exports** unless intentional — they create circular dependency risks
- **Lock file committed** — `package-lock.json` or `pnpm-lock.yaml` always in Git

---

## Python

### Project Structure

```
src/
└── {package}/
    ├── __init__.py
    ├── {module}.py
    └── tests/
        ├── conftest.py       # Shared fixtures at directory level
        └── test_{module}.py
pyproject.toml                # Single source of truth
```

### Toolchain

| Concern | Tool |
|---------|------|
| Packaging + deps | Poetry or pip-tools |
| Linting + formatting | ruff (replaces black + isort + flake8) |
| Type checking | mypy |
| Testing | pytest |
| Security | `pip-audit` |

### Key Conventions

- **src layout** — package under `src/`, not at root — prevents import confusion during development
- **pyproject.toml only** — no `setup.py`, no `setup.cfg`, no bare `requirements.txt` for packaged code
- **ruff over black + flake8** — one tool, faster, same behavior
- **Type hints everywhere** — return types, parameter types; no `Any` without an inline comment explaining why
- **Fixtures in conftest.py** — shared fixtures at directory level, not duplicated across test files
- **Lock file committed** — `poetry.lock` always in Git

---

## Key Rules (Both Stacks)

1. **Tests before merge** — no code without tests; CI must enforce
2. **Linter is non-negotiable** — CI must fail on lint errors; never disable rules without comment
3. **No secrets in code** — environment variables only; `.env.example` documents what's needed
4. **Dependency pinning** — lock files always committed
5. **Security scanning** — `npm audit` / `pip-audit` before release, not just on CI failures
