"""The 5.7.2 validator defects, each on the exact PreToolUse payload Claude Code sends for a subagent Bash call."""

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

AGENT = "a59642996486723b3"
CONTRACT = f"{AGENT}.3d485ee1bddc"
OTHER_AGENT = "ab06c74151e2e2576"
OTHER = f"{OTHER_AGENT}.ce713a71b992"
HARNESS = "abb8688fb406175e2"
SESSION = "fcd612ec-f757-4a32-8f71-bf01428f22b7"
FREE, SIGN, REFUSE = "FREE", "SIGN", "REFUSE"
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _git(*args: str) -> None:
    subprocess.run([*_GIT, *args], check=True, capture_output=True)


def _worktree(repo: Path, path: Path, agent: str, contract: str) -> None:
    from gaia.worktree import lock_reason

    _git("-C", str(repo), "worktree", "add", "-q", "-b", path.name, str(path))
    _git("-C", str(repo), "worktree", "lock", "--reason", lock_reason(contract, agent), str(path))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = Path(os.path.realpath(tmp_path_factory.mktemp("v572")))
    patch = pytest.MonkeyPatch()
    patch.setenv("GAIA_DATA_DIR", str(root / "data"))
    patch.setenv("GAIA_DB", str(root / "data" / "gaia.db"))
    patch.setenv("HOME", str(root / "home"))
    patch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import cache_dir, ensure_layout, scratch_dir, worktrees_dir
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id

    ensure_layout()
    insert_dispatched_handoff(CONTRACT, AGENT, "me", session_id=SESSION, agent_name="gaia-system")
    assert stamp_harness_agent_id(CONTRACT, HARNESS)["status"] == "applied"
    scratch_root = Path(os.path.realpath(scratch_dir()))
    own = scratch_root / CONTRACT
    (scratch_root / OTHER).mkdir(parents=True)
    own.mkdir()
    for entry in (own, scratch_root / OTHER):
        (entry / "a.txt").write_text("a")
    (own / "run.sh").write_text("echo hi\n")
    outside = root / "outside"
    outside.mkdir()
    (own / "writes_own.py").write_text(
        f'import json\nwith open("{own}/out.json", "w") as handle:\n    json.dump({{"a": 1}}, handle)\n')
    (own / "writes_own_path.py").write_text(
        f'from pathlib import Path\nOUT = Path("{own}") / "out.txt"\nOUT.write_text("x")\n')
    (own / "writes_handle.py").write_text(
        f'handle = open("{own}/log.txt", "a")\nhandle.write("x")\nhandle.close()\n')
    (own / "writes_outside.py").write_text(f'with open("{outside}/x.txt", "w") as handle:\n    handle.write("x")\n')
    (own / "writes_other.py").write_text(f'open("{scratch_root / OTHER}/x.txt", "w").write("x")\n')

    repo = root / "home" / "ws" / "gaia"
    (repo / "gaia" / "store").mkdir(parents=True)
    (repo / "gaia" / "__init__.py").write_text("")
    (repo / "gaia" / "store" / "schema.sql").write_text("")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("import gaia\n\n\ndef test_x():\n    assert gaia\n")
    _git("-C", str(root / "home" / "ws"), "init", "-q", "-b", "main", str(repo))
    _git("-C", str(repo), "add", ".")
    _git("-C", str(repo), "commit", "-q", "-m", "init")
    managed = Path(os.path.realpath(worktrees_dir())) / "gaia"
    managed.mkdir(parents=True)
    mine, theirs = managed / "11111111111111111111111111111111", managed / "22222222222222222222222222222222"
    _worktree(repo, mine, AGENT, CONTRACT)
    _worktree(repo, theirs, OTHER_AGENT, OTHER)
    for tree in (mine, theirs):
        (tree / "x.sh").write_text("echo x\n")
        (tree / "gone.txt").write_text("g")
    (own / "writes_own_worktree.py").write_text(
        f'from pathlib import Path\n(Path("{mine}") / "gen.txt").write_text("x")\n')
    (own / "writes_other_worktree.py").write_text(
        f'from pathlib import Path\n(Path("{theirs}") / "gen.txt").write_text("x")\n')

    install = root / "home" / "ws" / "node_modules" / "@jaguilar87" / "gaia"
    install.mkdir(parents=True)
    (install / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia"}))
    dev_plugin = Path(cache_dir()) / "dev-plugin" / "ws"
    dev_plugin.mkdir(parents=True)
    (dev_plugin / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia"}))
    yield {
        "SR": str(scratch_root), "S": str(own), "W": str(mine), "OW": str(theirs), "REPO": str(repo),
        "WS": str(root / "home" / "ws"), "HOME": str(root / "home"), "DEV": str(dev_plugin),
        "OUT": str(outside), "DATA": str(root / "data"),
    }
    patch.undo()


@pytest.fixture(autouse=True)
def _world_environment(world, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", world["DATA"])
    monkeypatch.setenv("GAIA_DB", os.path.join(world["DATA"], "gaia.db"))
    monkeypatch.setenv("HOME", world["HOME"])
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def observe(command: str, cwd: str) -> str:
    """FREE, SIGN (a consent path exists) or REFUSE (categorical) for a subagent Bash call through the adapter."""
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {
        "session_id": SESSION,
        "transcript_path": f"/home/u/.claude/projects/-home-u-ws/{SESSION}.jsonl",
        "cwd": cwd,
        "permission_mode": "acceptEdits",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Probe"},
        "tool_use_id": "toolu_01Validator572Shape",
        "agent_id": HARNESS,
        "agent_type": "gaia:gaia-system",
    }
    adapter = ClaudeCodeAdapter()
    verdict = adapter.pre_tool_verdict(adapter.parse_event(json.dumps(payload)))
    if verdict.refusal is None and verdict.consent is None and verdict.decision in (None, "allow"):
        return FREE
    return SIGN if verdict.consent is not None or verdict.approval_id else REFUSE


TOOL_RESULTS = f"/home/u/.claude/projects/-home-u-ws/{SESSION}/tool-results/bd5twrof3.txt"
REQUEST_SET = (
    "gaia approvals request-set --command 'npm --prefix /home/u/ws install @jaguilar87/gaia@5.7.2-rc.1' "
    "--what 'Upgrade the npm install of Gaia' --question 'Upgrade Gaia?' "
    "--does 'Rewrites node_modules/@jaguilar87/gaia/package.json and the installed code' "
    "--impact 'The host runs the new hooks' --verification 'gaia doctor' --shared-state 'None' "
    "--rollback 'Install the previous version again'"
)

CASES = [
    ("chmod-own-scratch", "chmod +x {S}/run.sh", "W", FREE),
    ("chmod-recursive-own-scratch", "chmod -R u+w {S}", "W", FREE),
    ("chmod-own-worktree", "chmod +x {W}/x.sh", "W", FREE),
    ("chmod-other-agent-scratch", "chmod +x {SR}/" + OTHER + "/a.txt", "W", SIGN),
    ("chmod-other-agent-worktree", "chmod +x {OW}/x.sh", "W", SIGN),
    ("chmod-reference-option", "chmod --reference={S}/a.txt {S}/run.sh", "W", SIGN),
    ("rm-own-worktree", "rm -f {W}/gone.txt", "W", FREE),
    ("rm-other-agent-worktree", "rm -f {OW}/gone.txt", "W", SIGN),
    ("rm-own-worktree-root", "rm -rf {W}", "W", SIGN),
    ("script-open-writes-own-scratch", "python3 {S}/writes_own.py", "W", FREE),
    ("script-path-writes-own-scratch", "python3 {S}/writes_own_path.py", "W", FREE),
    ("script-handle-writes-own-scratch", "python3 {S}/writes_handle.py", "W", FREE),
    ("script-writes-own-worktree", "python3 {S}/writes_own_worktree.py", "W", FREE),
    ("script-writes-other-agent-worktree", "python3 {S}/writes_other_worktree.py", "W", SIGN),
    ("script-writes-outside", "python3 {S}/writes_outside.py", "W", SIGN),
    ("script-writes-other-agent-scratch", "python3 {S}/writes_other.py", "W", SIGN),
    ("uv-directory-pytest-gaia-test", "uv --directory {REPO} run pytest tests/test_x.py -q", "WS", FREE),
    ("pytest-rootdir-pythonpath-from-outside", "PYTHONPATH={REPO} pytest --rootdir {REPO} {REPO}/tests/test_x.py -q",
     "WS", FREE),
    ("pytest-pythonpath-outside-repo", "PYTHONPATH={OUT} pytest --rootdir {REPO} {REPO}/tests/test_x.py -q",
     "WS", SIGN),
    ("request-set-names-install-path", REQUEST_SET, "WS", FREE),
    ("rm-dev-plugin-install-root", "rm -rf {DEV}", "WS", SIGN),
    ("ls-dev-plugin-install-root", "ls {DEV}", "WS", FREE),
    ("cat-dev-plugin-install-root", "cat {DEV}/package.json", "WS", FREE),
    ("jq-reads-host-tool-output", "jq -c . " + TOOL_RESULTS, "WS", REFUSE),
    ("cp-out-of-host-tool-output", "cp " + TOOL_RESULTS + " {S}/data.txt", "WS", REFUSE),
    ("mkdir-stranded-in-workspace", "mkdir -p {WS}/scratch-t10-a6f6c4b3", "WS", SIGN),
    ("touch-stranded-in-workspace", "touch {WS}/tmp_dev_kc_probe.mjs", "WS", SIGN),
    ("mkdir-in-own-scratch", "mkdir -p {S}/sub", "WS", FREE),
    ("mkdir-in-own-worktree", "mkdir -p {W}/newdir", "WS", FREE),
]


@pytest.mark.parametrize("name,template,where,expected", CASES, ids=[c[0] for c in CASES])
def test_validator_572_verdicts_under_the_real_payload(name, template, where, expected, world):
    assert observe(template.format(**world), world[where]) == expected
