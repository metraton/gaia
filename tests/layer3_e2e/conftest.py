"""Layer 3 conftest - E2E tests with real claude CLI sessions.

SERIAL EXECUTION REQUIRED: These tests spawn real `claude` CLI sessions in
headless mode and perform actual installation steps into temporary project
directories. Although each test uses pytest's tmp_path fixture (which
isolates the project directory per test), the Claude CLI accesses shared
global state (e.g., ~/.gaia/gaia.db for storing session contracts, approval
grants, and memory). Twelve parallel xdist workers attempting simultaneous
writes to the same database and reading/writing the same shared resources
can cause contention, lock timeouts, and test failures.

ENFORCEMENT: The package.json script `test:layer3` includes `-n0` (or
`-p no:xdist`) to disable pytest-xdist parallelization. This is NOT
a limitation of the tests' own design (they are truly isolated via tmp_path);
it is a protection against Gaia's global shared state that the CLI legitimately
accesses. If you run layer3 tests directly with `pytest`, pass `-n0` or
`-p no:xdist` to avoid spurious failures.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
