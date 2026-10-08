#!/usr/bin/env python3
"""Truth table for read-only commands the classifier used to mistake for mutations.

Each family pairs the READ form that must run without a signature with the
MUTATING form of the same command family that must still ask for one, so a
rule loosened for the read cannot silently loosen the write.

Rows whose id ends in ``regression`` were already correct on the base and are
recorded so a later change cannot break them.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "hooks"))
sys.path.insert(0, str(REPO_ROOT))

from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier
from modules.tools.bash_validator import BashValidator

FREE = "free"
GATED = "gated"

# (case_id, kind, command)
READONLY_FALSE_T3_TABLE = [
    # ---- git: -M is rename detection on show/log/diff, a forced rename on branch
    ("git-show-find-renames", FREE, "git -C /r show -M --stat HEAD"),
    ("git-log-find-renames", FREE, "git -C /r log -M --stat -1"),
    ("git-diff-find-renames", FREE, "git -C /r diff -M --stat"),
    ("git-branch-force-rename", GATED, "git -C /r branch -M newname"),
    # ---- gh: `deploy-key` is a noun; its verb is the next token ----
    ("gh-deploy-key-list", FREE, "gh repo deploy-key list"),
    ("gh-deploy-key-add", GATED, "gh repo deploy-key add key.pub"),
    ("gh-deploy-key-delete", GATED, "gh repo deploy-key delete 1"),
    ("gh-workflow-run-regression", GATED, "gh workflow run build.yml"),
    # ---- node -e: reading is free, writing is not ----
    ("node-e-read", FREE, "node -e \"console.log(require('fs').readFileSync('a.yml','utf8'))\""),
    ("node-e-write", GATED, "node -e \"require('fs').writeFileSync('a.txt','x')\""),
]


def _verdict(command):
    detect_mutative_command.cache_clear()
    return detect_mutative_command(command).is_mutative, classify_command_tier(command)


@pytest.mark.parametrize(
    "case_id,kind,command",
    READONLY_FALSE_T3_TABLE,
    ids=[row[0] for row in READONLY_FALSE_T3_TABLE],
)
def test_readonly_false_t3_verdict(case_id, kind, command):
    is_mutative, tier = _verdict(command)
    if kind == FREE:
        assert not is_mutative and tier != SecurityTier.T3_BLOCKED, (
            f"{case_id}: a read-only command asks for a signature: {command!r} "
            f"-> mutative={is_mutative}, {tier}"
        )
    else:
        assert is_mutative and tier == SecurityTier.T3_BLOCKED, (
            f"{case_id}: a real mutation stopped asking for a signature: "
            f"{command!r} -> mutative={is_mutative}, {tier}"
        )


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    from gaia.paths import ensure_layout, scratch_dir

    ensure_layout()
    turn_entry = scratch_dir() / "agent.token"
    (turn_entry / "empty").mkdir(parents=True)
    detect_mutative_command.cache_clear()
    return turn_entry, tmp_path


def test_rmdir_inside_the_turn_scratch_entry_is_free(scratch):
    turn_entry, _ = scratch
    assert _verdict(f"rmdir {turn_entry}/empty") == (False, SecurityTier.T0_READ_ONLY)


def test_removal_outside_the_scratch_still_asks(scratch):
    _, tmp_path = scratch
    for command in (f"rmdir {tmp_path}/proj", f"rm -rf {tmp_path}/proj"):
        is_mutative, tier = _verdict(command)
        assert is_mutative and tier == SecurityTier.T3_BLOCKED, command


def test_npm_prefix_run_reads_the_script_body(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    is_mutative, tier = _verdict(f"npm --prefix {tmp_path} run build")
    assert not is_mutative and tier != SecurityTier.T3_BLOCKED


def test_tar_extract_inside_the_turn_scratch_is_free(scratch):
    turn_entry, _ = scratch
    for command in (
        f"tar -xf {turn_entry}/a.tar -C {turn_entry}/empty",
        f"tar -xzf {turn_entry}/a.tgz -C {turn_entry}/empty",
    ):
        assert BashValidator().validate(command).allowed, command


def test_tar_extract_that_leaves_the_scratch_still_asks(scratch):
    turn_entry, tmp_path = scratch
    for command in (
        f"tar -xf {tmp_path}/a.tar -C {tmp_path}/proj",
        f"tar -xf {turn_entry}/a.tar -C {tmp_path}/proj",
        f"tar -xf {tmp_path}/a.tar -C {turn_entry}/empty",
        f"tar -xf {turn_entry}/a.tar",
        f"tar -xf {turn_entry}/a.tar -C {turn_entry}/../proj",
        f"tar -xf {turn_entry}/a.tar -C {turn_entry}/empty --to-command=sh",
        f"tar -cf {turn_entry}/a.tar -C {turn_entry}/empty .",
    ):
        assert not BashValidator().validate(command).allowed, command
