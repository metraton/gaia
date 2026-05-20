"""Tests for `session_start._detect_headless()`.

The detector decides whether to register a session as headless. Three
signals, in priority order:
  1. Explicit env vars (CLAUDE_HEADLESS=1, CI=true, NONINTERACTIVE=1).
  2. Parent process probe -- `/proc/$ppid/cmdline` contains `claude -p`
     or `claude --print` (the SDK CLI invocation pattern).
  3. Tertiary: both stdin and stdout detached from a TTY.

Why this matters
----------------
Before this fix the detector relied solely on CLAUDE_HEADLESS / CI env
vars. The SDK CLI (`claude -p ...`) does not set either, so every
headless SDK invocation registered as interactive and polluted the
session registry's liveness tracking.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks/ to sys.path so `import session_start` resolves the same
# way the production entry point does.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import session_start  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc_root(tmp_path: Path, ppid: int, argv: list[str]) -> Path:
    """Build a fake /proc/<ppid>/cmdline mock root.

    Layout:
        <tmp>/proc/<ppid>/cmdline   -- NUL-separated, NUL-terminated
    """
    proc_root = tmp_path / "proc"
    pid_dir = proc_root / str(ppid)
    pid_dir.mkdir(parents=True)
    cmdline = pid_dir / "cmdline"
    cmdline.write_bytes(b"\x00".join(a.encode("utf-8") for a in argv) + b"\x00")
    return proc_root


# ---------------------------------------------------------------------------
# Env-var signals (Priority 1)
# ---------------------------------------------------------------------------

class TestEnvSignals:
    """Explicit env vars must trigger headless=True without further probing."""

    def test_claude_headless_true(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_HEADLESS", "1")
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NONINTERACTIVE", raising=False)
        assert session_start._detect_headless() is True

    def test_ci_true(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.setenv("CI", "true")
        monkeypatch.delenv("NONINTERACTIVE", raising=False)
        assert session_start._detect_headless() is True

    def test_ci_true_case_insensitive(self, monkeypatch):
        """`CI=TRUE` (any case) must trigger headless."""
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.setenv("CI", "TRUE")
        monkeypatch.delenv("NONINTERACTIVE", raising=False)
        assert session_start._detect_headless() is True

    def test_noninteractive_set(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("NONINTERACTIVE", "1")
        assert session_start._detect_headless() is True

    def test_no_env_signal_falls_through(self, monkeypatch, tmp_path):
        """With no env signal, an empty /proc and TTY-on stdio = not headless."""
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NONINTERACTIVE", raising=False)
        # Empty proc root: no parent probe match.
        empty_proc = tmp_path / "empty_proc"
        empty_proc.mkdir()
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=empty_proc) is False


# ---------------------------------------------------------------------------
# SDK CLI probe (Priority 2)
# ---------------------------------------------------------------------------

class TestSdkCliProbe:
    """Parent process `claude -p` / `claude --print` triggers headless."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        # Strip env signals so the probe is the only positive path.
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NONINTERACTIVE", raising=False)

    def test_parent_claude_with_p_flag(self, tmp_path, monkeypatch):
        proc_root = _make_proc_root(
            tmp_path, ppid=99999, argv=["/usr/bin/claude", "-p", "do thing"]
        )
        monkeypatch.setattr(os, "getppid", lambda: 99999)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is True

    def test_parent_claude_with_print_flag(self, tmp_path, monkeypatch):
        proc_root = _make_proc_root(
            tmp_path, ppid=12345, argv=["claude", "--print", "do thing"]
        )
        monkeypatch.setattr(os, "getppid", lambda: 12345)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is True

    def test_parent_claude_with_output_format(self, tmp_path, monkeypatch):
        proc_root = _make_proc_root(
            tmp_path,
            ppid=12345,
            argv=["claude", "--output-format", "json", "run"],
        )
        monkeypatch.setattr(os, "getppid", lambda: 12345)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is True

    def test_parent_claude_interactive_no_print(self, tmp_path, monkeypatch):
        """Interactive `claude` (no -p/--print) must NOT register as headless."""
        proc_root = _make_proc_root(
            tmp_path, ppid=12345, argv=["claude"]
        )
        monkeypatch.setattr(os, "getppid", lambda: 12345)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is False

    def test_parent_not_claude(self, tmp_path, monkeypatch):
        """A non-claude parent with `-p` must NOT trigger headless."""
        proc_root = _make_proc_root(
            tmp_path, ppid=12345, argv=["/usr/bin/bash", "-p", "anything"]
        )
        monkeypatch.setattr(os, "getppid", lambda: 12345)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is False

    def test_missing_proc_dir_no_crash(self, tmp_path, monkeypatch):
        """/proc absent (macOS/Windows): falls through cleanly, no exception."""
        nonexistent = tmp_path / "no_proc"
        # Do NOT create the directory.
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=nonexistent) is False

    def test_missing_cmdline_no_crash(self, tmp_path, monkeypatch):
        """/proc/<ppid>/ exists but cmdline gone (race): no exception."""
        proc_root = tmp_path / "proc"
        (proc_root / "99999").mkdir(parents=True)
        # NO cmdline file.
        monkeypatch.setattr(os, "getppid", lambda: 99999)
        with patch.object(sys.stdout, "isatty", return_value=True), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=proc_root) is False


# ---------------------------------------------------------------------------
# TTY tertiary signal (Priority 3)
# ---------------------------------------------------------------------------

class TestTtyTertiary:
    """Both stdio detached from TTY = headless, as a last-resort fallback."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("NONINTERACTIVE", raising=False)

    def test_both_stdio_not_tty(self, tmp_path):
        empty_proc = tmp_path / "empty_proc"
        empty_proc.mkdir()
        with patch.object(sys.stdout, "isatty", return_value=False), \
             patch.object(sys.stdin, "isatty", return_value=False):
            assert session_start._detect_headless(proc_root=empty_proc) is True

    def test_only_stdout_not_tty_is_not_enough(self, tmp_path):
        """Piping stdout only is common in interactive use -- not headless."""
        empty_proc = tmp_path / "empty_proc"
        empty_proc.mkdir()
        with patch.object(sys.stdout, "isatty", return_value=False), \
             patch.object(sys.stdin, "isatty", return_value=True):
            assert session_start._detect_headless(proc_root=empty_proc) is False
