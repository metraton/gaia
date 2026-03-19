# Developer Patterns — Config Reference

Minimal config templates. Replace `{package-name}` and other placeholders with project values.

For project-specific examples, discover patterns from the existing codebase using the `investigation` skill.

---

## tsconfig.json (strict baseline)

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noUncheckedIndexedAccess": true,
    "outDir": "dist",
    "rootDir": "src",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src"],
  "exclude": ["node_modules", "dist"]
}
```

## pyproject.toml (Poetry baseline)

```toml
[tool.poetry]
name = "{package-name}"
version = "0.1.0"
description = ""
packages = [{include = "{package-name}", from = "src"}]

[tool.poetry.dependencies]
python = "^3.12"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
ruff = "^0.4"
mypy = "^1.10"

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "UP"]

[tool.mypy]
strict = true
python_version = "3.12"

[tool.pytest.ini_options]
testpaths = ["src"]
```

## jest.config.ts (TypeScript)

```typescript
import type { Config } from 'jest'

const config: Config = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  collectCoverageFrom: ['src/**/*.ts', '!src/**/*.test.ts'],
  coverageThreshold: {
    global: { lines: 80 }
  }
}

export default config
```

## pytest conftest.py (fixture baseline)

```python
import pytest

@pytest.fixture(scope="session")
def db_connection():
    """Session-scoped fixture for database connection."""
    # Setup
    conn = create_connection()
    yield conn
    # Teardown
    conn.close()

@pytest.fixture(autouse=True)
def reset_state():
    """Auto-use fixture to reset state between tests."""
    yield
    # cleanup after each test
```

## .env.example

```bash
# Required
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
API_KEY=your-api-key-here

# Optional
LOG_LEVEL=info
PORT=3000
```
