"""Every PowerShell command position gets the verdict of its POSIX twin.

Windows-shaped commands judged on Linux through the Claude Code PreToolUse
adapter, on the Bash tool and on the PowerShell tool alike. An irreversible
Windows disk command is permanently blocked like ``mkfs`` in every spelling; a
mutative stage hidden behind a read cmdlet, an alias or a script block asks for
the same consent as ``rm``; a read-only pipeline stays free like ``ls``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

SESSION = "ses-powershell-command-positions"
AGENT_ID = "a" + "9" * 16


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()


def verdict(tool_name: str, command: str) -> str:
    """Return ``free``, ``approval``, ``blocked`` (the permanent floor) or ``denied`` (any other refusal)."""
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "tool_use_id": f"toolu-{tool_name}",
        "cwd": "/",
        "agent_id": AGENT_ID,
        "agent_type": "developer",
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if response.approval_id:
        return "approval"
    if isinstance(response.output, str):
        reason = response.output
    else:
        specific = response.output.get("hookSpecificOutput", {})
        decision = specific.get("permissionDecision")
        if decision == "allow" or (decision is None and response.exit_code == 0):
            return "free"
        reason = specific.get("permissionDecisionReason", "")
    return "blocked" if "blocked by security policy" in reason else "denied"


IRREVERSIBLE = [
    "format C:",
    "diskpart /s wipe.txt",
    "vssadmin delete shadows /all /quiet",
    "Format-Volume -DriveLetter D",
    "Clear-Disk -Number 1 -RemoveData",
]

BLOCKED_SPELLINGS = [
    *(("Bash", command) for command in IRREVERSIBLE),
    *(("Bash", f"cmd /c {command}") for command in IRREVERSIBLE),
    *(("Bash", f'cmd /c "{command}"') for command in IRREVERSIBLE),
    *(("Bash", f'powershell -Command "{command}"') for command in IRREVERSIBLE),
    *(("Bash", f"pwsh -c '{command}'") for command in IRREVERSIBLE),
    *(("PowerShell", command) for command in IRREVERSIBLE),
    ("Bash", "FORMAT.COM D: /q"),
    ("Bash", "DISKPART.EXE"),
    ("PowerShell", "Get-Disk 1 | Clear-Disk -RemoveData"),
    ("PowerShell", "if ($true) { vssadmin delete shadows /all }"),
    ("Bash", 'pwsh -NoProfile -c "& { Format-Volume -DriveLetter D }"'),
]


@pytest.mark.parametrize("tool_name,command", BLOCKED_SPELLINGS)
def test_irreversible_windows_command_is_blocked_like_mkfs(tool_name, command):
    assert verdict(tool_name, command) == verdict("Bash", "mkfs.ext4 /dev/sdb") == "blocked"
    assert verdict("Bash", "dd if=/dev/zero of=/dev/sda") == "blocked"


RM = "rm -r -f /proj"
PIPED_RM = "ls /proj | rm -r -f /proj"
MISSING_SCRIPT = "bash /proj/missing.sh"

HIDDEN_MUTATIVE_STAGES = [
    ("Bash", 'powershell -Command "Get-ChildItem | foreach { rm $_ }"', RM),
    ("Bash", 'powershell -Command "Get-ChildItem; rm -r -fo C:/proj"', RM),
    ("Bash", "pwsh -c 'Get-Item x | ri'", RM),
    ("Bash", 'powershell -Command "Get-ChildItem | Where-Object { Remove-Item $_ }"', RM),
    ("Bash", 'pwsh -NoProfile -c "Get-ChildItem | % { del $_ }"', RM),
    ("Bash", 'powershell -Command "(Get-Item x).Delete()"', RM),
    ("Bash", "pwsh -c '$f = Get-Item x; $f.Delete()'", RM),
    ("Bash", 'pwsh -c "Get-ChildItem; [IO.File]::Delete(\'x\')"', RM),
    pytest.param(
        "Bash", 'powershell -Command "Get-ChildItem; .\\deploy.ps1"', MISSING_SCRIPT,
        id="guard-already-gated-on-base-dot-slash-script",
    ),
    ("Bash", 'pwsh -c "Get-ChildItem; &.\\deploy.ps1"', MISSING_SCRIPT),
    ("Bash", 'pwsh -c "Get-ChildItem; powershell -File deploy.ps1 -Force"', MISSING_SCRIPT),
    pytest.param(
        "PowerShell", "Get-ChildItem | foreach { rm $_ }", PIPED_RM,
        id="guard-already-gated-on-base-tool-foreach-rm",
    ),
    ("PowerShell", "Get-Item x | ri", PIPED_RM),
    ("PowerShell", "Get-Item x | % { $_.Delete() }", PIPED_RM),
]


@pytest.mark.parametrize("tool_name,command,twin", HIDDEN_MUTATIVE_STAGES)
def test_hidden_mutative_stage_gets_the_verdict_of_its_posix_twin(tool_name, command, twin):
    assert verdict(tool_name, command) == verdict("Bash", twin) != "free"


@pytest.mark.parametrize("tool_name,command", [
    ("Bash", 'powershell -Command "Get-ChildItem | ForEach-Object { $_.Name }"'),
    ("PowerShell", "Get-ChildItem | ForEach-Object { $_.Name }"),
])
def test_foreach_with_a_read_only_body_is_free_like_ls(tool_name, command):
    assert verdict(tool_name, command) == verdict("Bash", "ls /proj") == "free"


@pytest.mark.parametrize("tool_name,command", [
    ("Bash", 'powershell -Command "Get-ChildItem | Select-Object Name"'),
    ("PowerShell", "Get-ChildItem | Select-Object Name"),
    ("PowerShell", "Get-ChildItem C:/my-folder | Sort-Object Length"),
])
def test_read_only_pipeline_stays_free(tool_name, command):
    assert verdict(tool_name, command) == "free"


@pytest.mark.parametrize("tool_name,command,twin", [
    ("Bash", "pwsh -c 'Get-ChildItem | % Delete'", RM),
    pytest.param(
        "PowerShell", "Get-ChildItem | ForEach-Object -MemberName Delete", PIPED_RM,
        id="guard-already-gated-on-base-tool-member-name",
    ),
])
def test_foreach_invoking_a_member_by_name_asks_like_rm(tool_name, command, twin):
    assert verdict(tool_name, command) == verdict("Bash", twin) != "free"
