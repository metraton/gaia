"""gaia-verifier may write its probes in its own scratch entry and nowhere else.

The verifier's frontmatter grants Write so it can author probe tests, and the
shared tool policy refuses any verifier Write/Edit whose resolved target is
not under ``<scratch>/<its contract_id>``. The contract_id is resolved from the
host agent id; every path is a stand-in under a temporary data root.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

SESSION = "ses-verifier-scratch"
HARNESS_ID = "a" + "7" * 16
CONTRACT_ID = "a" + "8" * 16 + ".0123456789ab"


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr(
        "gaia.store.writer.find_dispatch_row_by_harness_agent_id",
        lambda harness_id, **_: {"contract_id": CONTRACT_ID} if harness_id == HARNESS_ID else None,
    )
    root = tmp_path / "gaia_data" / "scratch"
    root.mkdir(parents=True)
    return root


def _write(tool_name: str, file_path: Path, *, agent_type: str = "gaia-verifier",
           agent_id: str = HARNESS_ID) -> dict:
    tool_input = {"file_path": str(file_path), "content": "def test_probe():\n    pass\n"}
    if tool_name == "Edit":
        tool_input = {"file_path": str(file_path), "old_string": "a", "new_string": "b"}
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": "/",
        "agent_id": agent_id,
        "agent_type": agent_type,
    }
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps(payload)))
    if isinstance(response.output, str):
        return {"decision": "deny", "reason": response.output}
    specific = response.output.get("hookSpecificOutput", {})
    return {
        "decision": specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny"),
        "reason": specific.get("permissionDecisionReason", ""),
    }


def _frontmatter_list(field: str) -> list[str]:
    text = (REPO_ROOT / "agents" / "gaia-verifier.md").read_text(encoding="utf-8")
    header = text.split("---", 2)[1]
    match = re.search(rf"^{field}:\s*\[?([^\]\n]*)\]?\s*$", header, re.MULTILINE)
    return [item.strip() for item in match.group(1).split(",")] if match else []


def _refused(verdict: dict) -> bool:
    return verdict["decision"] == "deny" and "[VERIFIER_SCRATCH_ONLY]" in verdict["reason"]


def test_verifier_can_write_a_probe_in_its_own_scratch_entry(scratch):
    assert "Write" in _frontmatter_list("tools")
    assert "Write" not in _frontmatter_list("disallowedTools")
    for probe in (scratch / CONTRACT_ID / "test_probe.py", scratch / f"{CONTRACT_ID}.probe.py"):
        verdict = _write("Write", probe)
        assert verdict["decision"] != "deny", verdict


def test_verifier_write_into_the_worktree_under_test_is_refused(scratch, tmp_path):
    worktree = tmp_path / "worktree"
    (worktree / "src").mkdir(parents=True)
    assert _refused(_write("Write", worktree / "src" / "app.py"))
    assert _refused(_write("Write", REPO_ROOT / "agents" / "gaia-verifier.md"))


def test_verifier_cannot_write_another_entry_or_escape_its_own(scratch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (scratch / CONTRACT_ID).mkdir()
    (scratch / CONTRACT_ID / "link").symlink_to(outside)
    for target in (
        scratch / "a999999999999999.000000000000.py",
        scratch / f"{CONTRACT_ID}x.py",
        scratch / CONTRACT_ID / ".." / "escape.py",
        scratch / CONTRACT_ID / "link" / "planted.py",
        scratch,
    ):
        assert _refused(_write("Write", target)), target


def test_verifier_write_is_refused_when_its_contract_does_not_resolve(scratch):
    assert _refused(_write("Write", scratch / f"{CONTRACT_ID}.py", agent_id="a" + "0" * 16))


def test_verifier_edit_outside_its_scratch_entry_is_refused(scratch, tmp_path):
    assert _refused(_write("Edit", tmp_path / "worktree" / "README.md"))
