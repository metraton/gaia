"""A secret-bearing read is redacted the same whatever wrapper runs it.

``sudo``, ``timeout``, ``env`` and ``nice`` run the command after them with the
effect it has alone, and ``git -C <repo>`` only moves where git runs, so none
of them may carry a read past the redaction its bare form gets. Each case runs
the real PreToolUse boundary of both hosts, then executes the command the hook
hands back against stand-in binaries that print known sentinel values.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.hooks.test_secret_output_redaction import (  # noqa: F401  (world is a fixture)
    HOSTS,
    SENTINELS,
    _execute,
    _stored_sentinels,
    world,
)

FAKE_SUDO = """#!/usr/bin/env bash
exec "$@"
"""


@pytest.fixture()
def wrapped_world(world):
    sudo = world["bin"] / "sudo"
    sudo.write_text(FAKE_SUDO, encoding="utf-8")
    sudo.chmod(0o755)
    repo = str(world["repo"])
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    return world


WRAPPED_READS = [
    ("sudo kubectl get deployment api -n prod -o yaml", None),
    ("timeout 30 vault kv get secret/app", None),
    ("env LC_ALL=C bao read secret/app", None),
    ("nice -n 5 kubectl describe deployment api", None),
    ("nice -n 5 grep -rn TOKEN {repo}", 0),
    ("sudo grep -rn TOKEN {repo}", 0),
    ("git -C {repo} grep -rn TOKEN", 0),
    ("timeout 30 git -C {repo} grep -n TOKEN", 0),
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("fake_exit", [0, 3])
@pytest.mark.parametrize("read,fixed_exit", WRAPPED_READS)
def test_a_wrapped_secret_read_is_redacted_and_keeps_its_exit_code(
    wrapped_world, host, read, fixed_exit, fake_exit
):
    command = read.format(repo=wrapped_world["repo"])
    verdict = HOSTS[host](command)
    assert verdict["decision"] == "allow", verdict

    result = _execute(wrapped_world, verdict["run"], fake_exit)

    shown = result.stdout + result.stderr
    assert shown.strip(), "the redacted read printed nothing"
    assert not [s for s in SENTINELS if s in shown], shown
    assert result.returncode == (fake_exit if fixed_exit is None else fixed_exit)
    assert _stored_sentinels(wrapped_world) == []


EMPTY_READS = [
    "nice -n 5 grep -rn ABSENT_WORD {repo}",
    "sudo grep -rn ABSENT_WORD {repo}",
    "git -C {repo} grep -rn ABSENT_WORD",
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("read", EMPTY_READS)
def test_a_wrapped_recursive_grep_that_finds_nothing_is_rewritten_and_still_exits_1(
    wrapped_world, host, read
):
    command = read.format(repo=wrapped_world["repo"])
    verdict = HOSTS[host](command)
    assert verdict["decision"] == "allow", verdict
    assert "secret_reads" in verdict["run"], "the recursive read was not routed through the redactor"

    result = _execute(wrapped_world, verdict["run"], 0)

    assert result.returncode == 1, result
    assert result.stdout == ""


LOOK_ALIKES = [
    "timeout 30 kubectl get pods -n prod",
    "nice -n 5 vault status",
    "sudo grep -n TOKEN {repo}/app/settings.py",
    "git -C {repo} log --oneline",
]


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("read", LOOK_ALIKES)
def test_a_wrapped_read_that_carries_no_secret_runs_unchanged(wrapped_world, host, read):
    command = read.format(repo=wrapped_world["repo"])

    verdict = HOSTS[host](command)

    assert verdict["decision"] in {"allow", None}, verdict
    assert verdict["run"] is None or command in verdict["run"]
    assert "secret_reads" not in (verdict["run"] or "")
