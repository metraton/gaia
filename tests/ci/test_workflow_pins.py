"""Workflows name their runner image and use artifact actions on a supported Node."""

import re
from pathlib import Path

import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))
FLOATING_LABEL = re.compile(r"-latest$")
RETIRED_ARTIFACT_MAJOR = re.compile(r"^actions/(upload|download)-artifact@v[1-4]$")


def _runner_labels(workflow):
    for job in workflow["jobs"].values():
        runs_on = job["runs-on"]
        if "${{" in str(runs_on):
            runs_on = job["strategy"]["matrix"]["os"]
        yield from [runs_on] if isinstance(runs_on, str) else runs_on


def test_linux_jobs_pin_the_runner_image():
    floating = [
        (path.name, label)
        for path in WORKFLOWS
        for label in _runner_labels(yaml.safe_load(path.read_text(encoding="utf-8")))
        if FLOATING_LABEL.search(label) and label.startswith("ubuntu")
    ]
    assert not floating, floating


def test_artifact_actions_are_past_the_node20_majors():
    retired = [
        (path.name, step["uses"])
        for path in WORKFLOWS
        for job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values()
        for step in job.get("steps", [])
        if RETIRED_ARTIFACT_MAJOR.match(step.get("uses", ""))
    ]
    assert not retired, retired
