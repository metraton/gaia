# Contributing to gaia-ops

Thank you for your interest in contributing to gaia-ops. This guide covers how to set up your development environment, run tests, and submit changes.

## Development Setup

### Prerequisites

- **Node.js** >= 18.0.0
- **Python** >= 3.9
- **Git** >= 2.30
- **Claude Code** (latest version, for end-to-end testing)

### Clone and Install

```bash
git clone https://github.com/metraton/gaia-ops.git
cd gaia-ops
npm install
```

Python test dependencies:

```bash
pip install pytest
```

## Running Tests

The test suite is organized in layers:

```bash
# Layer 1 (fast, deterministic) - run these before every PR
npm test

# Equivalent:
npm run test:layer1

# Layer 2 (LLM evaluation) - requires Claude Code access
npm run test:layer2

# Layer 3 (end-to-end)
npm run test:layer3

# All layers
npm run test:all

# Run pytest directly with stop-on-first-failure
python -m pytest tests/ -x

# Linting
npm run lint
```

Always ensure Layer 1 tests pass before submitting a PR.

## Project Structure

See [README.md](./README.md) for the full directory tree. Key areas for contributors:

| Directory | What it contains |
|-----------|-----------------|
| `agents/` | Agent definition files (`.md`) - identity, scope, routing |
| `skills/` | Skill modules (`SKILL.md` files) - injected procedural knowledge |
| `hooks/` | Runtime validators (`pre_tool_use.py`, `post_tool_use.py`, `subagent_stop.py`) |
| `hooks/modules/` | Modular hook components (blocked commands, safe commands, dangerous verbs) |
| `tools/` | Orchestration tools (context provider, memory, validation) |
| `config/` | Configuration files (contracts, git standards, rules) |
| `tests/` | Test suite organized by layer |
| `bin/` | CLI utilities (`gaia-init`, `gaia-doctor`, etc.) |

## Coding Standards

### Python

- Follow the existing code style in the repository.
- Use [ruff](https://github.com/astral-sh/ruff) for linting and formatting.
- Type hints are encouraged but not strictly required.
- Keep functions focused and testable.

### JavaScript / Node.js

- ES modules (`import`/`export`), not CommonJS.
- Follow the existing patterns in `bin/` and `index.js`.

### Commit Messages

All commits must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): short description
```

Allowed types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`, `perf`, `style`, `build`

Examples:
- `feat(hooks): add timeout protection to bash validator`
- `fix(skills): correct token budget in agent-protocol`
- `docs(readme): update installation instructions`

## PR Process

1. **Fork** the repository and create a feature branch from `main`.
2. **Make your changes** following the coding standards above.
3. **Write tests** for new functionality. Changes to `hooks/` always need tests.
4. **Run the test suite**: `npm test` must pass.
5. **Commit** using Conventional Commits format.
6. **Open a PR** against `main` with a clear description of what changed and why.

PRs are reviewed for correctness, test coverage, and consistency with existing patterns.

## Hooks Development

The `hooks/` directory contains runtime validators that enforce security and workflow policies in Claude Code. These are critical-path code.

- `pre_tool_use.py` - Main entry point; validates every tool call before execution.
- `post_tool_use.py` - Audit and metrics after tool execution.
- `hooks/modules/` - Individual validation modules (e.g., `blocked_commands.py`, `safe_commands.py`, `dangerous_verbs.py`).

**Key rules for hook changes:**
- Every change to a hook module must have a corresponding test in `tests/`.
- Hook modules must be deterministic -- no network calls, no randomness.
- Test both the allow and deny paths for any new validation rule.

## Skills Development

Skills live in `skills/` as directories, each containing a `SKILL.md` file:

```
skills/
  skill-name/
    SKILL.md          # Main content (injected into agents)
    reference.md      # Heavy reference material (read on-demand)
    examples.md       # Concrete examples (optional)
    scripts/          # Executable tools (optional)
```

- `SKILL.md` must stay under 100 lines (it is injected on every agent call).
- Heavy content goes in `reference.md` (loaded on-demand).
- Skills define process; agents define identity. Do not duplicate between them.

For detailed guidance, see `skills/skill-creation/SKILL.md`.

## Questions?

Open an issue on [GitHub](https://github.com/metraton/gaia-ops/issues) or contact the maintainer at jaguilar1897@gmail.com.
