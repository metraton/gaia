"""The Windows smoke's grant cycle holds on every platform the suite runs on.

``windows_smoke.py`` only executes on the Windows runner, so a change to the
approval core that its grant cycle no longer matches surfaces there alone. Running
the same check here puts that drift in front of the ordinary suite.
"""

import importlib.util
from pathlib import Path

SMOKE = Path(__file__).resolve().parent / "windows_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("windows_smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grant_cycle_blocks_activates_and_admits_only_the_requester(tmp_path):
    """Block -> DB activation -> retry passes for the requester, not a foreign session."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)

    ok, detail = _load_smoke()._run_grant_cycle(workspace)

    assert ok, detail
