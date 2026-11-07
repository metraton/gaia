"""The refusal of a literal credential steers to a form request-set accepts and the hook executes."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.approvals import core, secret_audit  # noqa: E402
from gaia.redaction import REDACTED  # noqa: E402
from gaia.store import writer  # noqa: E402

SESSION = "ses-orchestrator"
AGENT_ID = "a0f1e2d3c4b5a6978"
AGENT_TYPE = "gaia-system"
LITERAL = "gho_" + "aB3dE5gH7jK9mN1pQ3sT5vW7"
STEERED = "GH_TOKEN=$(gh auth token) gh pr merge 41 --squash"


@pytest.fixture
def host(tmp_path, monkeypatch):
    from modules.core.paths import clear_path_cache

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    clear_path_cache()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    monkeypatch.setattr("modules.core.state.find_claude_dir", lambda: claude_dir)
    repo = tmp_path / "repo"
    repo.mkdir()
    return str(repo)


def _request(command: str, repo: str) -> str:
    items = [{"command": command, "cwd": repo, "expect_exit": [],
              "does": "Merges the pull request.", "impact": "The change lands on main."}]
    return core.request_command_set(
        items, what="Merge the PR", question="Merge the PR?", session_id=SESSION, agent_id=AGENT_TYPE,
        rollback="Revert the merge commit.", verification="gh pr view 41", shared_state="Yes: main.",
    )


def _pre(command: str, tool_use_id: str, repo: str) -> str | None:
    from adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "session_id": SESSION, "transcript_path": "/nonexistent/transcript.jsonl", "cwd": repo,
        "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse", "agent_id": AGENT_ID,
        "agent_type": AGENT_TYPE, "tool_name": "Bash", "tool_input": {"command": command},
        "tool_use_id": tool_use_id,
    }))
    response = adapter.adapt_pre_tool_use(event)
    if response.exit_code != 0 or not isinstance(response.output, dict):
        return None
    decision = response.output.get("hookSpecificOutput", {})
    if decision and decision.get("permissionDecision") != "allow":
        return None
    return command


def test_literal_token_is_refused_with_a_steering_request_set_accepts(host):
    with pytest.raises(core.SealError) as refusal:
        _request(f"GH_TOKEN={LITERAL} gh pr merge 41 --squash", host)
    message = str(refusal.value)
    assert LITERAL not in message
    assert "$(gh auth token)" in message
    assert "not a chain" not in message
    _request(STEERED, host)


def test_steered_request_grant_matches_the_identical_command_at_execution(host):
    approval_id = _request(STEERED, host)
    core.record_presentation(approval_id, native_ref="toolu_q", session_id=SESSION, agent_id="gaia-orchestrator")
    assert core.decide(native_ref="toolu_q", option_key="approve", session_id=SESSION).status == "activated"

    assert _pre(STEERED.replace("41", "42"), "tu-other", host) is None
    assert _pre(STEERED, "tu-1", host) == STEERED


@pytest.mark.parametrize("command", [
    "GH_TOKEN=$(rm -rf /tmp/x) gh pr merge 41 --squash",
    "GH_TOKEN=$(gh pr merge 3) gh pr merge 41 --squash",
    "GH_TOKEN=$(gh auth token; gh pr close 3) gh pr merge 41 --squash",
    "GH_TOKEN=$(gh auth token | tee out) gh pr merge 41 --squash",
    "GH_TOKEN=$(gh auth token $(rm x)) gh pr merge 41 --squash",
    "GH_TOKEN=`gh auth token` gh pr merge 41 --squash",
    "gh pr merge $(gh auth token) --squash",
    "gh pr merge 41 --body $(gh auth token)",
    "GH_TOKEN=$(gh auth token) && gh pr merge 41",
    "A=1\nrm -rf /tmp/x",
    "GH_TOKEN=$(gh auth token)\nrm -rf /tmp/x",
    "A=1 \ngh pr merge 41",
    "GH_TOKEN=$(cat ~/.config/gh/hosts.yml) gh pr merge 41 --squash",
    "GH_TOKEN=$(cat .env) gh pr merge 41 --squash",
    "GH_TOKEN=$(gh auth token",
])
def test_a_substitution_beyond_one_read_only_assignment_value_stays_refused(host, command):
    with pytest.raises(core.SealError):
        _request(command, host)


BEARER = "Zx9Cv8Bn7Mm6Ll5Kk4Jj3Hh2"
PASSWORD = "Pw9Lk8Jh7Gf6Ds5Aq4"
PHRASES = ("what", "question", "rationale", "does", "impact", "rollback", "verification", "shared_state")


@pytest.mark.parametrize("field", PHRASES)
@pytest.mark.parametrize(
    "text, secret", [(f"Bearer {BEARER}", BEARER), (f"PASSWORD={PASSWORD}", PASSWORD)], ids=["bearer", "password"],
)
def test_a_secret_in_a_phrase_field_is_refused_whole(host, field, text, secret):
    phrases = {"what": "Merge the PR", "question": "Merge the PR?", "rationale": "Ready.",
               "rollback": "Revert the merge commit.", "verification": "gh pr view 41", "shared_state": "Yes: main."}
    item = {"command": STEERED, "cwd": host, "expect_exit": [],
            "does": "Merges the pull request.", "impact": "The change lands on main."}
    if field in ("does", "impact"):
        item[field] = text
    else:
        phrases[field] = text
    with pytest.raises(core.SealError) as refusal:
        core.request_command_set([item], session_id=SESSION, agent_id=AGENT_TYPE, **phrases)
    assert secret not in str(refusal.value)


@pytest.mark.parametrize("template, secret", [
    ("GH_TOKEN={} gh pr merge 41", LITERAL),
    ("curl -H 'Authorization: Bearer {}' é", BEARER),
    ("PASSWORD={} kubectl delete pod web-0", PASSWORD),
], ids=["gho", "bearer", "password"])
def test_scrub_masks_spans_in_the_raw_json_and_keeps_every_other_byte(template, secret):
    text = json.dumps([{"command": template.format(secret), "key": "k"}], indent=2, ensure_ascii=False)
    scrubbed = secret_audit.scrub_text(text)
    assert secret not in scrubbed
    assert scrubbed == text.replace(secret, REDACTED)
