"""A process environment carries tokens: /proc/<pid>/environ is refused to every reader, its siblings stay free."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import sensitive_paths, sensitive_read_guard  # noqa: E402

ENVIRON = [
    "/proc/self/environ",
    "/proc/thread-self/environ",
    "/proc/1/environ",
    "/proc/4242/environ",
    "/proc/self/task/7/environ",
    "/proc/1/task/1/environ",
    "/proc//self/./environ",
]
NOT_SECRET = [
    "/proc/self/status",
    "/proc/self/cmdline",
    "/proc/self/environ.txt",
    "/proc/self/task/7/status",
    "/proc/cpuinfo",
    "/proc/environ",
    "/srv/proc/self/environ",
]


@pytest.mark.parametrize("path", ENVIRON)
def test_the_process_environment_is_a_sensitive_path(path):
    assert sensitive_paths.sensitive_path_label(path) == "process environment"


@pytest.mark.parametrize("path", NOT_SECRET)
def test_the_rest_of_proc_is_not_sensitive(path):
    assert sensitive_paths.sensitive_path_label(path) == ""


@pytest.mark.parametrize("path", ENVIRON[:3])
def test_the_file_tools_refuse_the_process_environment(path):
    assert sensitive_read_guard.check_file_tool("Read", {"file_path": path})


def test_the_file_tools_leave_proc_status_free():
    assert sensitive_read_guard.check_file_tool("Read", {"file_path": "/proc/self/status"}) is None


def test_a_link_to_the_process_environment_is_refused(tmp_path):
    link = tmp_path / "env.txt"
    link.symlink_to("/proc/self/environ")
    allowed, reason = sensitive_read_guard.check(f"cat {link}", str(tmp_path))
    assert allowed is False
    assert reason.startswith("[SENSITIVE_PATH]")
    assert sensitive_read_guard.check_file_tool("Read", {"file_path": str(link)})


def test_a_link_to_proc_status_stays_free(tmp_path):
    link = tmp_path / "status.txt"
    link.symlink_to("/proc/self/status")
    assert sensitive_read_guard.check(f"cat {link}", str(tmp_path)) == (True, None)
