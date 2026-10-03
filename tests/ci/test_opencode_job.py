"""The CI bun job runs the OpenCode TypeScript tests and a red one reaches the verdict."""

import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
JOBS = CI["jobs"]


def _bun_pin(job):
    (step,) = [s for s in JOBS[job]["steps"] if s.get("uses", "").startswith("oven-sh/setup-bun@")]
    return step["with"]["bun-version"]


def _run_commands(job):
    return [s["run"].strip() for s in JOBS[job]["steps"] if "run" in s]


def test_bun_runner_is_invoked_on_a_directory_holding_typescript_tests():
    (command,) = _run_commands("test-opencode")
    argv = shlex.split(command)
    assert argv[:2] == ["bun", "test"]
    paths = [arg for arg in argv[2:] if not arg.startswith("-")]
    assert paths and all(arg.startswith("./") for arg in paths)
    for arg in paths:
        assert list((ROOT / arg).rglob("*.test.ts"))


def test_bun_job_is_skipped_exactly_when_the_shards_are():
    assert JOBS["test-opencode"]["if"] == JOBS["test-python"]["if"]
    assert "decide" in JOBS["test-opencode"]["needs"]


def test_a_failed_bun_job_fails_the_verdict():
    verdict = JOBS["ci-verdict"]
    assert "test-opencode" in verdict["needs"]
    (fail_step,) = [s for s in verdict["steps"] if s.get("run", "").strip() == "exit 1"]
    assert "contains(needs.*.result, 'failure')" in fail_step["if"]


def test_both_jobs_that_install_bun_pin_the_same_version():
    assert _bun_pin("test-opencode") == _bun_pin("test-python")
