"""Layer 3 conftest - adds helpers to import path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
