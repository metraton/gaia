"""Regression tests for the GCP quick-triage shell probe."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "fast-queries"
    / "cloud"
    / "gcp"
    / "quicktriage_gcp_troubleshooter.sh"
)


def _run_with_mock(tmp_path: Path, mock_body: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + mock_body,
        encoding="utf-8",
    )
    gcloud.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GCP_PROJECT"] = "test-project"
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_query_failure_is_not_reported_as_empty_or_healthy(tmp_path):
    result = _run_with_mock(
        tmp_path,
        'echo "permission denied" >&2\nexit 1\n',
    )

    assert result.returncode == 2
    assert "Query failed" in result.stdout
    assert "permission denied" in result.stdout
    assert "No clusters found" not in result.stdout
    assert "All quotas healthy" not in result.stdout


def test_quiet_inputs_do_not_turn_a_healthy_project_into_a_failure(tmp_path):
    """A project with no clusters, gcloud chatter on stderr and a payload larger
    than a pipe buffer is healthy: exit 0, never an alarm or an aborted triage."""
    result = _run_with_mock(
        tmp_path,
        """
echo "Your active configuration is: [default]" >&2
echo "WARNING: something advisory" >&2
case "$*" in
  "container clusters list"*) ;;
  "sql instances list"*) printf 'db-a RUNNABLE\\n' ;;
  "logging read"*) for i in $(seq 1 5000); do printf 'cluster-a error-line-%06d-padding-padding-padding\\n' "$i"; done ;;
  "compute project-info describe"*) printf '1 10\\n' ;;
esac
""",
    )

    assert result.returncode == 0
    assert "No clusters found" in result.stdout
    assert "1 instance(s) running" in result.stdout
    assert "Issues detected" not in result.stdout
    assert "errors in last hour" in result.stdout


def test_unhealthy_resources_return_health_failure(tmp_path):
    result = _run_with_mock(
        tmp_path,
        """
case "$*" in
  "container clusters list"*) printf 'cluster-a STOPPING\\n' ;;
  "sql instances list"*) printf 'db-a RUNNABLE\\n' ;;
  "logging read"*) ;;
  "compute project-info describe"*) printf '1 10\\n' ;;
esac
""",
    )

    assert result.returncode == 1
    assert "Issues detected" in result.stdout
    assert "cluster-a: STOPPING" in result.stdout
