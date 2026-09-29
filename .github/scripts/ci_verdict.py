#!/usr/bin/env python3
"""Find an earlier green "CI verdict" that a commit can reuse instead of testing its tree again.

A completed CI run whose "CI verdict" job succeeded is reusable for SHA when its
head commit is, in order of preference:

  1. SHA itself;
  2. another commit with SHA's tree -- the PR head a squash merge reproduces;
  3. SHA's only parent, when parent..SHA touches version sources only
     (the chore(release) commit).

Everything is read through the GitHub API with `gh api`, so the answer does not
depend on how much history the caller's checkout holds.

Exit status: 0 a verdict is reusable, 1 none is, 2 the API could not answer.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass

VERDICT_JOB = "CI verdict"

# The files a release rewrites and nothing else does; a diff confined to them
# changes no code a test could observe.
VERSION_FILES = frozenset({"package.json", "pyproject.toml", "CHANGELOG.md", "hooks/hooks.json"})
VERSION_DIRS = (".claude-plugin/",)


class ApiError(RuntimeError):
    """`gh api` failed or returned something that is not JSON."""


@dataclass(frozen=True)
class Verdict:
    run_id: int
    url: str
    head_sha: str
    reason: str


def gh_api(path):
    """Return the decoded JSON body of `gh api <path>`, raising ApiError on failure."""
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if result.returncode != 0:
        raise ApiError(f"gh api {path} failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ApiError(f"gh api {path} returned invalid JSON: {exc}") from exc


def runs_path(repo, workflow):
    """API path listing the workflow's 100 most recent successful runs."""
    return f"repos/{repo}/actions/workflows/{workflow}/runs?status=success&per_page=100"


def is_version_only(filenames):
    """True when a non-empty diff touches version sources and nothing else."""
    return bool(filenames) and all(
        name in VERSION_FILES or name.startswith(VERSION_DIRS) for name in filenames
    )


def _verdict_is_green(repo, run_id, api):
    jobs = api(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")["jobs"]
    return any(job["name"] == VERDICT_JOB and job["conclusion"] == "success" for job in jobs)


def find_reusable_verdict(repo, sha, workflow="ci.yml", api=gh_api):
    """Return the Verdict SHA can reuse, or None when its tree still needs testing."""
    commit = api(f"repos/{repo}/commits/{sha}")
    tree = commit["commit"]["tree"]["sha"]

    rules = [
        (lambda run: run["head_sha"] == sha, "same commit"),
        (lambda run: run["head_commit"]["tree_id"] == tree, "same tree"),
    ]
    parents = commit["parents"]
    changed = [entry["filename"] for entry in commit.get("files", [])]
    if len(parents) == 1 and is_version_only(changed):
        parent_sha = parents[0]["sha"]
        parent_tree = api(f"repos/{repo}/commits/{parent_sha}")["commit"]["tree"]["sha"]
        rules.append(
            (
                lambda run: run["head_commit"]["tree_id"] == parent_tree,
                f"parent {parent_sha} differs only in version sources ({', '.join(changed)})",
            )
        )

    runs = [
        run
        for run in api(runs_path(repo, workflow))["workflow_runs"]
        if run["status"] == "completed" and run["conclusion"] == "success"
    ]
    for matches, reason in rules:
        for run in runs:
            if matches(run) and _verdict_is_green(repo, run["id"], api):
                return Verdict(run["id"], run["html_url"], run["head_sha"], reason)
    return None


def _write_outputs(path, verdict):
    fields = {"reusable": "true" if verdict else "false"}
    if verdict:
        fields.update(source_run=verdict.run_id, source_url=verdict.url, reason=verdict.reason)
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in fields.items():
            handle.write(f"{key}={value}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sha", help="commit whose tree is about to be tested")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="OWNER/NAME (default: $GITHUB_REPOSITORY)",
    )
    parser.add_argument("--workflow", default="ci.yml", help="workflow file whose runs count")
    parser.add_argument(
        "--github-output",
        metavar="PATH",
        help="also append reusable/source_run/source_url/reason as step outputs",
    )
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo is required outside GitHub Actions")

    try:
        verdict = find_reusable_verdict(args.repo, args.sha, args.workflow, api=gh_api)
    except (ApiError, KeyError) as exc:
        print(f"No CI verdict lookup for {args.sha}: {exc}. The suite runs.", file=sys.stderr)
        verdict, status = None, 2
    else:
        status = 0 if verdict else 1
        if verdict:
            print(
                f"Reusable CI verdict for {args.sha}: run {verdict.run_id} "
                f"on {verdict.head_sha} ({verdict.reason}) {verdict.url}"
            )
        else:
            print(f"No reusable CI verdict for {args.sha}: its tree has not passed. The suite runs.")

    if args.github_output:
        _write_outputs(args.github_output, verdict)
    return status


if __name__ == "__main__":
    sys.exit(main())
