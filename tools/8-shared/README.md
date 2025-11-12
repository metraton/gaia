# 8-Shared Module

**Purpose:** Common utilities and shared schemas

## Overview

This module provides shared utilities, common dataclass schemas, exceptions, and helper functions used across multiple tool modules. It serves as the foundation layer for cross-cutting concerns.

## Current State

This module is prepared for future extensions but currently serves as a placeholder for shared utilities.

## Planned Extensions

### `paths.py`
Path resolution and manipulation utilities:
```python
# Future API
from tools.shared import resolve_project_root, get_claude_dir

project_root = resolve_project_root()
claude_dir = get_claude_dir()
```

### `models.py`
Common dataclass schemas and types:
```python
# Future API
from tools.shared import AgentResponse, ToolResult, ValidationStatus

response = AgentResponse(
    agent="terraform-architect",
    status=ValidationStatus.SUCCESS,
    result=ToolResult(...)
)
```

### `exceptions.py`
Custom exception types:
```python
# Future API
from tools.shared import (
    ToolValidationError,
    ContextLoadError,
    AgentRoutingError
)

raise ToolValidationError("Invalid configuration")
```

### `logging.py`
Centralized logging utilities:
```python
# Future API
from tools.shared import get_logger

logger = get_logger("agent_router")
logger.info("Routing request", extra={"confidence": 0.95})
```

### `constants.py`
System-wide constants:
```python
# Future API
from tools.shared import (
    SECURITY_TIERS,
    AGENT_TYPES,
    DEFAULT_TIMEOUT,
    MAX_RETRIES
)
```

## Design Principles

**Shared utilities should:**
1. Have zero dependencies on other tool modules
2. Be truly generic (used by 3+ modules)
3. Have comprehensive tests
4. Follow single responsibility principle
5. Be backward compatible

**Don't put here:**
- Domain-specific logic (belongs in respective modules)
- One-off utilities (keep in using module)
- Experimental code (use dedicated module first)

## Migration Path

When moving utilities here:

1. **Identify duplication** across modules
2. **Extract to shared/** with tests
3. **Update dependent modules** to import from shared
4. **Verify no circular dependencies**
5. **Document in this README**

## Files

- `__init__.py` - Module marker (currently empty)
- `README.md` - This file

## Future Structure

```
8-shared/
├── __init__.py          # Re-exports
├── paths.py             # Path utilities
├── models.py            # Common schemas
├── exceptions.py        # Custom exceptions
├── logging.py           # Logging utilities
├── constants.py         # System constants
└── README.md            # This file
```

## See Also

- `tools/__init__.py` - Package organization
- All other tool modules - Potential consumers
