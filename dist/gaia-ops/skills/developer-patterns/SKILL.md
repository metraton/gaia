---
name: developer-patterns
description: Use when creating, modifying, or reviewing application code in Node.js/TypeScript or Python
metadata:
  user-invocable: false
  type: domain
---

# Developer Patterns

Reference conventions for Node.js/TypeScript and Python. The codebase is the authority -- these patterns help you find and interpret what's already there.

For config file templates (tsconfig.json, pyproject.toml, jest.config.ts), read `reference.md` in this directory.

## Discover the Project's Conventions

Before writing code, understand how THIS project is organized.

1. **Find the entry points.** Look for `src/`, `lib/`, `app/`, or the package.json `main`/`exports` field. The layout varies -- what matters is where this project puts its code.
2. **Read 2-3 existing modules.** How are tests organized -- co-located or in a `tests/` directory? What import style is used? What tooling does the config reflect?
3. **Check the existing toolchain.** Read `package.json` scripts, `tsconfig.json`, `pyproject.toml`, or equivalent. The project's configured tools are your tools.
4. **Follow the majority pattern.** If the project uses Vitest, don't introduce Jest. If tests live in `__tests__/`, put yours there too. Consistency with the project matters more than what you'd choose on a greenfield.

## Node.js / TypeScript (Reference)

Common conventions -- defer to the project's actual configuration.

- **Strict TypeScript** — `strict: true` catches entire categories of null/undefined bugs at compile time rather than runtime
- **Tests co-located** — `{file}.test.ts` next to `{file}.ts` keeps test and implementation in sync; but some projects prefer `__tests__/` directories, and that's fine
- **Absolute imports** — path aliases in tsconfig eliminate fragile `../../../` chains that break on refactors
- **Barrel exports with care** — re-export files (`index.ts`) create circular dependency risks in larger projects; use them intentionally
- **Lock file committed** — reproducible installs across environments; without it, CI and local can diverge silently

## Python (Reference)

Common conventions -- defer to the project's actual configuration.

- **src layout** — package under `src/` prevents accidental imports of the uninstalled package during development
- **pyproject.toml** — single source of truth for packaging; `setup.py` and `setup.cfg` are legacy unless the project already uses them
- **Type hints** — return types, parameter types; `Any` without a comment explaining why is a hole in the type safety net
- **Fixtures in conftest.py** — shared fixtures at directory level prevent duplication; pytest discovers them automatically
- **Lock file committed** — same reason as Node: reproducible installs

## Key Rules (Both Stacks)

1. **Tests with code** — untested code is unverified code; CI should enforce this, and if it doesn't, that's worth flagging
2. **Linter runs clean** — disabling a lint rule without a comment explaining why creates invisible technical debt
3. **No secrets in code** — environment variables only; `.env.example` documents what's needed so new developers don't have to guess
4. **Dependency pinning** — lock files committed; without them, "works on my machine" is the default state
5. **Security scanning** — `npm audit` / `pip-audit` catches known vulnerabilities before they reach production
