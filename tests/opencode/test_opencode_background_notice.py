"""Plan 94 T12: a background child's notice reaches the OpenCode orchestrator once, on its next real message."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import IsolatedRuntimeEnv

DRIVER = Path(__file__).resolve().parent / "background_notice_driver.ts"


def test_conclusion_to_orchestrator_background_notice_drained_once(tmp_path):
    env = IsolatedRuntimeEnv(tmp_path)
    env.prepare_hook_workspace()
    result = subprocess.run(
        ["bun", str(DRIVER)], cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    driven = json.loads(result.stdout)
    notice = driven["notice"]

    assert notice not in driven["launchOutput"]
    assert [context.count(notice) for context in driven["contexts"]] == [1, 0]
