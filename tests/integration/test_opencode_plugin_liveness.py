"""Observable load and fail-loud checks for the real OpenCode plugin module."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests" / "opencode" / "liveness_driver.ts"
LIVENESS = re.compile(
    r"^\[gaia-opencode:liveness\] pid=(\d+) loaded_at=(\S+) export=GaiaOpenCodePlugin$"
)


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required")
def test_real_loader_emits_positive_liveness_and_explicit_negative_failure_scan():
    result = subprocess.run(
        ["bun", str(DRIVER)],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    matches = [match for line in lines if (match := LIVENESS.fullmatch(line))]
    assert len(matches) == 1
    assert int(matches[0].group(1)) > 0
    live = json.loads(result.stdout)
    assert live == {
        "event": "gaia-opencode-plugin-live",
        "pid": live["pid"],
        "export": "GaiaOpenCodePlugin",
        "hook": "tool.execute.before",
    }
    assert live["pid"] == int(matches[0].group(1))
    assert "[gaia-opencode:load-failed]" not in lines
    print(f"OPENCODE_LIVENESS pid={live['pid']} loaded_at={matches[0].group(2)}")
    print("OPENCODE_LOAD_FAILURES_IN_INTERVAL=0")


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required")
def test_plugin_initialization_failure_is_loud_and_distinguishable():
    result = subprocess.run(
        ["bun", str(DRIVER), "fail-log"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "[gaia-opencode:liveness] host log failed" in result.stderr
    assert "OPENCODE_LIVENESS" not in result.stdout


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required")
def test_host_logger_method_receives_its_app_as_this():
    result = subprocess.run(
        ["bun", str(DRIVER), "context-log"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"context": "preserved"}


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required")
def test_plugin_import_does_not_claim_liveness_before_factory_invocation():
    result = subprocess.run(
        ["bun", str(DRIVER), "import-only"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"imported": True}
    assert "[gaia-opencode:liveness]" not in result.stderr
