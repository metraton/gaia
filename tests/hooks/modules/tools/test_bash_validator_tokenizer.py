#!/usr/bin/env python3
"""
Tests for fundamental bash command tokenization.

These tests pin down the contract for distinguishing CLI subcommand
identifiers from argument values (paths, test selectors, query strings).
The principle: a real CLI subcommand identifier is a single word made of
[A-Za-z0-9_-]. Tokens containing characters like ``/``, ``::``, ``.``,
``:``, ``=`` are argument values, not subcommands, so camelCase /
hyphen splitting must NOT be applied to them.

Two classes of behaviour are exercised:

1. **No false positives** -- commands whose argument values contain
   substrings that look like mutative verbs after splitting (e.g.
   ``pytest tests/test_install.py::TestStop`` would naive-split ``Stop``
   out of ``TestStop`` and classify the whole command as MUTATIVE).

2. **No regressions** -- commands whose CLI subcommand really is a
   mutative verb continue to be detected (e.g. ``gh pr comment``,
   ``rm -rf``, ``git push``).

These tests fail against pre-fix behaviour and pass after the fix.
"""

import sys
import pytest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command


# ---------------------------------------------------------------------------
# False-positive corpus
#
# Each entry is (command, why). The expected behaviour is that
# detect_mutative_command() returns is_mutative=False.  These commands
# carry tokens that look like CLI subcommand identifiers only after a
# naive camelCase or hyphen split of an argument value, but the argument
# itself is a path / test selector / query string / KV pair, not a real
# CLI subcommand.
# ---------------------------------------------------------------------------
NO_FALSE_POSITIVE_CASES = [
    # pytest test selector with camelCase class name -- "TestStop"
    # would split to "Test", "Stop" and "stop" is in MUTATIVE_VERBS.
    ("pytest tests/test_install.py::TestStop",
     "path::CamelCase test selector"),
    ("pytest path/to/test_x.py::TestRemove",
     "path::CamelCase test selector with 'Remove'"),
    ("pytest tests/test_install.py::TestInstall",
     "path::CamelCase test selector with 'Install'"),
    ("pytest tests/test_install.py::TestInstall::test_stop",
     "path::CamelCase::snake_case nested test selector"),
    ("python -m pytest tests/test_remove_install.py::TestCreateThings",
     "module-form pytest with camelCase selector"),

    # File paths with embedded mutative-looking words.
    ("pytest tests/install/test_x.py",
     "path containing 'install' as directory"),
    ("pytest tests/remove/test_y.py",
     "path containing 'remove' as directory"),

    # Query-string-like arguments.
    ("curl https://example.com/api?action=createUser",
     "URL query string containing 'createUser' camelCase value"),

    # KV-pair arguments.
    ("foo bar=createSomething",
     "key=value with camelCase value"),

    # Module path identifiers (Java/Python) with mutative-looking
    # camelCase fragments.
    ("foo com.example.DeleteService",
     "Java-style module path with camelCase class"),
]


# ---------------------------------------------------------------------------
# True-positive corpus
#
# Commands whose CLI subcommand really is a mutative verb (the verb is the
# whole first non-flag token, a clean identifier).  Detection MUST continue
# to flag these.
# ---------------------------------------------------------------------------
TRUE_POSITIVE_CASES = [
    "rm -rf /tmp/foo",
    "git push origin main",
    "gh pr comment 42 --body lgtm",
    "kubectl delete pod mypod",
    "docker exec container ls",
    "gaia install --postinstall",
    "helm install myrelease ./chart",
]


# ---------------------------------------------------------------------------
# Safe commands (negative controls).
# ---------------------------------------------------------------------------
SAFE_CASES = [
    "ls -la",
    "cat file.txt",
    "echo 'test it with rm and mv inside string'",
    "grep -r pattern src/",
    "git status",
    "kubectl get pods",
    "pytest -v tests/",
]


class TestNoFalsePositivesOnArgumentValues:
    """Tokens containing path/selector/KV characters are never subcommands."""

    @pytest.mark.parametrize("command,why", NO_FALSE_POSITIVE_CASES)
    def test_argument_value_not_treated_as_subcommand(self, command, why):
        result = detect_mutative_command(command)
        assert not result.is_mutative, (
            f"False positive ({why}): {command!r} -> "
            f"verb={result.verb!r} reason={result.reason!r}"
        )


class TestTruePositivesStillDetected:
    """Real mutative CLI subcommands continue to be detected after the fix."""

    @pytest.mark.parametrize("command", TRUE_POSITIVE_CASES)
    def test_mutative_subcommand_still_flagged(self, command):
        result = detect_mutative_command(command)
        assert result.is_mutative, (
            f"Regression -- mutative command not detected: {command!r} -> "
            f"verb={result.verb!r} reason={result.reason!r}"
        )


class TestSafeCommandsRemainSafe:
    """Commands that were always safe must remain safe."""

    @pytest.mark.parametrize("command", SAFE_CASES)
    def test_safe_command_not_flagged(self, command):
        result = detect_mutative_command(command)
        assert not result.is_mutative, (
            f"Regression -- safe command flagged: {command!r} -> "
            f"verb={result.verb!r} reason={result.reason!r}"
        )
