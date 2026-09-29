"""Which command shapes `gaia approvals request-set` accepts for signature.

Every case runs the real CLI as a subprocess against an isolated database: a
shape the runtime can execute once granted is accepted, and a shape that
could only fail after the user signed is refused at request time.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.integration import test_opencode_consent_retry_e2e as e2e

NODE_SCRIPT = 'require("child_process").execSync("kubectl delete namespace scratch");\n'
PYTHON_SCRIPT = 'import shutil\nshutil.rmtree("/tmp/gaia-request-set-scratch")\n'

ACCEPTED = {
    "node with a script": "node {scripts}/deploy.js",
    "python with a script": "python {scripts}/migrate.py",
    "python -c": "python -c \"import shutil; shutil.rmtree('/tmp/gaia-request-set-scratch')\"",
    "node -e": "node -e \"require('fs').rmSync('/tmp/gaia-request-set-scratch', {{recursive: true}})\"",
    "pipeline whose highest stage is T3": (
        "echo ready | curl -X POST --data-binary @- https://example.com/hook"
    ),
    "quoted title with parentheses": (
        'gh pr create --title "fix(approvals): accept scripts" --body "Scripts are signable."'
    ),
}

REJECTED = {
    "bare node REPL": "node",
    "bare python REPL": "python",
    "python -i on a script": "python -i {scripts}/migrate.py",
    "pipeline with no T3 stage": "git log --oneline -1 | head -1",
    "chain of two commands": "git status && git push origin main",
}


@pytest.fixture()
def request_set(tmp_path, bootstrapped_db_template):
    """Run request-set for one command in an isolated substrate; return the process."""
    env, _db_path = e2e._isolated_env(tmp_path / "state", bootstrapped_db_template)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "deploy.js").write_text(NODE_SCRIPT)
    (scripts / "migrate.py").write_text(PYTHON_SCRIPT)
    shell = {k: v for k, v in dict(env).items() if not k.startswith("CLAUDE")}

    def run(command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable, str(e2e.GAIA_CLI), "approvals", "request-set",
                "--command", command.format(scripts=scripts), "--cwd", env["WORKSPACE"],
                "--what", "Run the planned change", "--question", "Run it?",
                "--does", "Runs the planned change", "--impact", "Changes the scratch target",
                "--rollback", "Recreate the scratch target", "--session-id", "ses-773",
                "--agent-id", "developer", "--json",
            ],
            cwd=env["WORKSPACE"], env=shell, capture_output=True, text=True, timeout=180,
        )

    return run


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_request_set_accepts_executable_shape(request_set, case):
    result = request_set(ACCEPTED[case])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", sorted(REJECTED))
def test_request_set_rejects_shape_that_cannot_run_once_signed(request_set, case):
    result = request_set(REJECTED[case])
    assert result.returncode != 0, result.stdout + result.stderr


def test_request_set_rejects_unquoted_parentheses_naming_the_argument(request_set):
    result = request_set(
        'gh pr create --title fix(approvals): accept scripts --body "Scripts are signable."'
    )
    assert result.returncode != 0, result.stdout
    assert "--title" in result.stdout + result.stderr
