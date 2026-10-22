"""A code gate whose selection was narrowed after its red run (skip, xfail, -k, a dropped file) fails the oracle."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaia.state.gate_oracle import (  # noqa: E402
    gate_selection,
    run_oracle_check,
    selection_narrowing,
)

TEST_FILE = "test_claim.py"
RED_BODY = "def test_claim():\n    assert False\n"
GATE_COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider {TEST_FILE}"


def _gate(command: str = GATE_COMMAND) -> dict:
    return {"verification_type": "code", "evidence_shape": command}


@pytest.fixture
def red_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / TEST_FILE).write_text(RED_BODY)
    verdict = run_oracle_check(_gate())
    assert not verdict.ok and verdict.exit_code == 1
    return tmp_path, gate_selection(GATE_COMMAND)


@pytest.mark.parametrize(
    "narrowed_body",
    [
        "import pytest\n\n@pytest.mark.xfail\n" + RED_BODY,
        "import pytest\n\n@pytest.mark.skip\n" + RED_BODY,
        "import pytest\n\ndef test_claim():\n    pytest.skip('later')\n",
    ],
    ids=["xfail", "skip-marker", "skip-call"],
)
def test_marker_added_after_red_run_fails_the_oracle(red_run, narrowed_body):
    workdir, red = red_run
    (workdir / TEST_FILE).write_text(narrowed_body)

    verdict = run_oracle_check(_gate(), red_selection=red)

    assert verdict.exit_code == 0
    assert not verdict.ok
    assert any("skip/xfail" in error for error in verdict.errors)


def test_keyword_added_after_red_run_fails_the_oracle(red_run):
    workdir, red = red_run
    (workdir / TEST_FILE).write_text(RED_BODY + "\ndef test_other():\n    pass\n")
    narrowed = f"{GATE_COMMAND} -k other"

    verdict = run_oracle_check(_gate(narrowed), red_selection=red)

    assert verdict.exit_code == 0
    assert not verdict.ok
    assert any("-k other" in error for error in verdict.errors)


def test_dropped_test_file_fails_the_oracle(red_run):
    workdir, red = red_run
    (workdir / "test_elsewhere.py").write_text("def test_x():\n    pass\n")

    verdict = run_oracle_check(
        _gate(GATE_COMMAND.replace(TEST_FILE, "test_elsewhere.py")), red_selection=red
    )

    assert not verdict.ok
    assert any("no longer selected" in error for error in verdict.errors)


def test_skip_inside_a_selected_directory_fails_the_oracle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command = f"{sys.executable} -m pytest -q -p no:cacheprovider suite/"
    (tmp_path / "suite").mkdir()
    (tmp_path / "suite" / TEST_FILE).write_text(RED_BODY)
    assert not run_oracle_check(_gate(command)).ok
    red = gate_selection(command)
    (tmp_path / "suite" / TEST_FILE).write_text("import pytest\n\n@pytest.mark.skip\n" + RED_BODY)

    verdict = run_oracle_check(_gate(command), red_selection=red)

    assert verdict.exit_code == 0
    assert not verdict.ok


def test_aliased_mark_skip_fails_the_oracle(red_run):
    workdir, red = red_run
    (workdir / TEST_FILE).write_text("from pytest import mark\n\n@mark.skip\n" + RED_BODY)

    verdict = run_oracle_check(_gate(), red_selection=red)

    assert verdict.exit_code == 0
    assert not verdict.ok


def test_unittest_skiptest_call_fails_the_oracle(red_run):
    workdir, red = red_run
    (workdir / TEST_FILE).write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_claim(self):\n        self.skipTest('later')\n"
    )

    verdict = run_oracle_check(_gate(), red_selection=red)

    assert verdict.exit_code == 0
    assert not verdict.ok


def test_interpreter_dash_m_is_not_a_marker_filter():
    assert gate_selection(f"{sys.executable} -m pytest -q x.py").filters == ()


def test_runner_spelling_change_is_not_narrowing():
    red = gate_selection("pytest -q x.py")
    now = gate_selection("python3 -m pytest -q x.py")

    assert selection_narrowing(red, now) == []


def test_fix_and_new_tests_pass_the_oracle(red_run):
    workdir, red = red_run
    (workdir / TEST_FILE).write_text(
        "def test_claim():\n    assert True\n\ndef test_added():\n    assert 1 == 1\n"
    )

    verdict = run_oracle_check(_gate(), red_selection=red)

    assert verdict.ok, verdict.errors
