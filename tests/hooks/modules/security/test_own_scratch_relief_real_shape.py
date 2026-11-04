"""A subagent's cp, rm, mv and git output into its own scratch entry run free under the PreToolUse payload Claude Code really sends."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

AGENT = "aa25e20f133a3d6f6"
CONTRACT = f"{AGENT}.effff18c865b"
HARNESS = "abb8688fb406175e2"
SESSION = "fcd612ec-f757-4a32-8f71-bf01428f22b7"
OTHER = "ab06c74151e2e2576.ce713a71b992"
FREE, ASK = "FREE", "ASK"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("own-scratch")
    patch = pytest.MonkeyPatch()
    patch.setenv("GAIA_DATA_DIR", str(root / "data"))
    patch.setenv("GAIA_DB", str(root / "data" / "gaia.db"))
    patch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import ensure_layout, scratch_dir, worktrees_dir
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id

    ensure_layout()
    insert_dispatched_handoff(CONTRACT, AGENT, "me", session_id=SESSION, agent_name="gaia-verifier")
    assert stamp_harness_agent_id(CONTRACT, HARNESS)["status"] == "applied"
    scratch_root = Path(os.path.realpath(scratch_dir()))
    own = scratch_root / CONTRACT
    own.mkdir()
    (own / "a.txt").write_text("a")
    (own / "gone.txt").write_text("g")
    outside = root / "outside"
    outside.mkdir()
    (own / "link_out").symlink_to(outside)
    for name in (OTHER, f"{HARNESS}.x"):
        (scratch_root / name).mkdir()
        (scratch_root / name / "a.txt").write_text("a")
    worktree = Path(os.path.realpath(worktrees_dir())) / "5132f32cfd0f5cf0b7f78dcdb6423167"
    worktree.mkdir(parents=True)
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "init")):
        subprocess.run(["git", "-C", str(worktree), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)
    (worktree / "x.txt").write_text("x")
    yield {"SR": str(scratch_root), "S": str(own), "W": str(worktree), "DATA": str(Path(os.path.realpath(root / "data")))}
    patch.undo()


@pytest.fixture(autouse=True)
def _world_environment(world, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", world["DATA"])
    monkeypatch.setenv("GAIA_DB", os.path.join(world["DATA"], "gaia.db"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def observe(command: str, world: dict, harness: str = HARNESS) -> str:
    """Run the PreToolUse event Claude Code sends for a subagent Bash call through the adapter."""
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {
        "session_id": SESSION,
        "transcript_path": f"/home/u/.claude/projects/-home-u-ws/{SESSION}.jsonl",
        "cwd": world["W"],
        "permission_mode": "acceptEdits",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Probe"},
        "tool_use_id": "toolu_01OwnScratchRealShape",
        "agent_id": harness,
        "agent_type": "gaia:gaia-verifier",
    }
    adapter = ClaudeCodeAdapter()
    verdict = adapter.pre_tool_verdict(adapter.parse_event(json.dumps(payload)))
    free = verdict.refusal is None and verdict.consent is None and verdict.decision in (None, "allow")
    return FREE if free else ASK


CASES = [
    ("archive-output-own-file", "git -C {W} archive --output {SR}/" + CONTRACT + ".tar HEAD", FREE),
    ("archive-o-own-entry", "git -C {W} archive --format=tar -o {S}/base.tar HEAD", FREE),
    ("cp-into-own-entry", "cp {W}/x.txt {S}/copy.txt", FREE),
    ("rm-in-own-entry", "rm -f {S}/gone.txt", FREE),
    ("mv-in-own-entry", "mv {S}/a.txt {S}/b.txt", FREE),
    ("archive-output-other-agent", "git -C {W} archive --output {SR}/" + OTHER + "/x.tar HEAD", ASK),
    ("cp-into-other-agent", "cp {W}/x.txt {SR}/" + OTHER + "/copy.txt", ASK),
    ("rm-in-other-agent", "rm -f {SR}/" + OTHER + "/a.txt", ASK),
    ("cp-into-harness-named-entry", "cp {W}/x.txt {SR}/" + HARNESS + ".x/copy.txt", ASK),
    ("cp-dotdot-out-of-own-entry", "cp {W}/x.txt {S}/../" + OTHER + "/copy2.txt", ASK),
    ("archive-dotdot-out-of-own-entry", "git -C {W} archive -o {S}/../escape.tar HEAD", ASK),
    ("cp-through-symlink-out", "cp {W}/x.txt {S}/link_out/copy.txt", ASK),
    ("archive-through-symlink-out", "git -C {W} archive -o {S}/link_out/x.tar HEAD", ASK),
    ("rm-glob-beyond-own-entry", "rm -f {SR}/" + AGENT + "*", ASK),
    ("rm-glob-whole-scratch", "rm -rf {SR}/*", ASK),
]


@pytest.mark.parametrize("name,template,expected", CASES, ids=[c[0] for c in CASES])
def test_own_scratch_relief_under_the_real_payload(name, template, expected, world):
    assert observe(template.format(**world), world) == expected


def test_an_unclaimed_harness_id_owns_no_contract_entry(world):
    assert observe(f"cp {world['W']}/x.txt {world['S']}/copy3.txt", world, harness="a0000000000000001") == ASK
