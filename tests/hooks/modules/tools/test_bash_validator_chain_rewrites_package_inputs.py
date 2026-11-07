#!/usr/bin/env python3
"""A script run after a chain stage that rewrites its inputs is not judged on the old file.

``npm run`` is classified by the ``scripts`` body in package.json, read when
the whole command is classified, before the chain executes. In ``git checkout
<ref> -- package.json && bun run build`` the body that runs is the one the
checkout writes, which the classifier never saw.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

HARMLESS_SCRIPTS = {"build": "vite build", "test": "vitest run"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": HARMLESS_SCRIPTS}))
    return tmp_path


def _validate(command, cwd):
    from modules.tools.bash_validator import BashValidator

    payload = {"cwd": str(cwd), "agent_id": "a0123456789abcdef", "agent_type": "developer"}
    return BashValidator().validate(
        command, is_subagent=True, session_id="t", agent_type="developer",
        hook_payload=payload,
    )


REWRITING_STAGES = [
    "git checkout feature -- package.json",
    "git restore --source=feature package.json",
    "cp /elsewhere/package.json package.json",
    "mv /elsewhere/package.json ./package.json",
    "curl -sSo package.json https://example.test/package.json",
    "git checkout feature -- node_modules/.bin/vite",
    "ln -sf /elsewhere/vite node_modules/.bin/vite",
]

LATER_STAGES = ["bun run build", "npm run build", "npm test", "npx vite build"]


@pytest.mark.parametrize("later", LATER_STAGES)
@pytest.mark.parametrize("earlier", REWRITING_STAGES)
def test_script_run_after_a_stage_that_rewrites_its_inputs_asks_a_signature(
    project, earlier, later,
):
    result = _validate(f"{earlier} && {later}", project)
    assert result.allowed is False, result.reason
    assert "Compound T3" in result.reason, result.reason


@pytest.mark.parametrize("later", ["bun run build", "npm run build", "npm test"])
@pytest.mark.parametrize("earlier", [
    "cat package.json",
    "git diff package.json",
    "git log -1 -- package.json",
    "jq .scripts package.json",
    "grep build package.json",
    "ls node_modules/.bin",
    "git status",
    "git checkout feature",
])
def test_script_run_after_a_reading_stage_runs_free(project, earlier, later):
    result = _validate(f"{earlier} && {later}", project)
    assert result.allowed is True, result.reason


@pytest.mark.parametrize("command", ["bun run build", "npm run build", "npm test"])
def test_script_run_alone_is_still_judged_by_its_body(project, command):
    assert _validate(command, project).allowed is True


def test_rewriting_stage_after_the_run_does_not_taint_it(project):
    result = _validate("npm run build && git restore --source=feature package.json", project)
    assert result.allowed is True, result.reason
