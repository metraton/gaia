"""`gaia migrate diagnose` runs free by its exact verb through the Bash validator, while every action that writes still signs (D252)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

CASES = {
    "migrate diagnose": "FREE",
    "migrate diagnose --json": "FREE",
    "migrate plan": "FREE",
    "migrate backup": "SIGNED",
    "migrate apply": "SIGNED",
    "migrate restore --from x": "SIGNED",
    "migrate frobnicate": "SIGNED",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    from gaia.paths import ensure_layout

    ensure_layout()
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    shutil.copy(_REPO_ROOT / "bin" / "gaia", install / "bin" / "gaia")
    monkeypatch.setattr(mutative_verbs, "_HOOK_ROOT", install)
    monkeypatch.setattr(mutative_verbs, "_hook_root_is_install", lambda: True)
    monkeypatch.chdir(tmp_path)
    return {"cwd": str(tmp_path), "gaia": str(install / "bin" / "gaia")}


def _verdict(command: str, cwd: str) -> tuple[str, str]:
    mutative_verbs.detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "tool_use_id": "t-diagnose", "session_id": "s-diagnose",
               "agent_id": "adiagnose00000001", "agent_type": "gaia-system"}
    result = BashValidator().validate(command, is_subagent=True, session_id="s-diagnose", agent_type="gaia-system",
                                      hook_payload=payload)
    if result.allowed:
        return "FREE", result.reason or ""
    return ("SIGNED" if result.approval_id else "BLOCKED"), result.reason or ""


@pytest.mark.parametrize("spelling", ["bare", "installed-path"])
@pytest.mark.parametrize("args,expected", CASES.items())
def test_only_diagnose_and_plan_run_free_under_gaia_migrate(args, expected, spelling, world):
    program = "gaia" if spelling == "bare" else world["gaia"]
    verdict, reason = _verdict(f"{program} {args}", world["cwd"])
    assert verdict == expected, reason
    if expected == "FREE":
        assert tiers.classify_command_tier(f"{program} {args}") != tiers.SecurityTier.T3_BLOCKED
