"""The gaia-check approvals area (D40, which replaced D28; D30 kept) stays runnable as written.

Each check pins a skill claim to the code that makes it true: the states it
expects to the approvals reader, the verbs it tells agents to run to the CLI,
the orchestrator that must load it in both hosts to the OpenCode agent
derivation, and the fixed folder's survival to scratch retention. The file
keeps its D28 name so the gate selector ``d28_live_test_catalog`` still
matches.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from gaia.approvals import reading
from gaia.retention import fs_rules

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "gaia-check" / "SKILL.md"
ORCHESTRATOR = ROOT / "agents" / "gaia-orchestrator.md"
APPROVALS_CLI = ROOT / "bin" / "cli" / "approvals.py"
FOLDER = "prueba-firmas"
CHECKS = range(1, 10)

_BIN_DIR = ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _install_helpers  # noqa: E402


@pytest.fixture(scope="module")
def skill_text() -> str:
    """The skill with its line wrapping folded, so a phrase is found wherever it wraps."""
    return " ".join(SKILL.read_text(encoding="utf-8").split())


def _check_section(text: str, number: int) -> str:
    match = re.search(
        rf"\*\*Check {number} -- .*?(?=\*\*Check {number + 1} -- | Report per check|\Z)",
        text,
        re.S,
    )
    assert match, f"check {number} is missing from the approvals area"
    return match.group(0)


def test_d28_live_test_catalog_is_the_gaia_check_skill(skill_text):
    assert skill_text.startswith("--- name: gaia-check description:")
    assert "probemos las aprobaciones" in skill_text
    assert "## Area: approvals" in skill_text
    assert not (ROOT / "skills" / "signature-live-tests").exists()


def test_d28_live_test_catalog_lives_in_the_fixed_folder(skill_text):
    assert f"<scratch>/{FOLDER}/" in skill_text
    assert "`gaia paths`" in skill_text
    assert "sandbox" in skill_text and "no fixture" in skill_text


def test_d28_live_test_catalog_specialists_are_blind(skill_text):
    running = skill_text.split("## Area: approvals")[0]
    assert "do not know they are being checked" in running
    assert "never says test, check, signature or approval" in running


def test_d28_live_test_catalog_each_run_has_its_own_prefix(skill_text):
    assert "`<host>-<HHMM>-`" in skill_text
    for number in CHECKS:
        assert f"`<p>nota-{number}" in _check_section(skill_text, number) or (
            f"`otra/<p>nota-{number}" in _check_section(skill_text, number)
        ), f"check {number} names its file with the run's prefix"


@pytest.mark.parametrize("number", CHECKS)
def test_d28_live_test_catalog_each_check_is_complete(skill_text, number):
    section = _check_section(skill_text, number)
    assert "User:" in section, "each check says what the user does"
    assert "Expected:" in section and "State `" in section


_STATE = re.compile(r"State (?:is )?(?:still )?`(\w+)`")
_OUTCOME = re.compile(r"Outcome `(\w+)`")
# A check that runs its command is approved and then executed: two lines of show.
_RUNS = {1: True, 2: False, 3: True, 4: True, 5: False, 6: True, 7: True, 8: False, 9: False}


@pytest.mark.parametrize("number", CHECKS)
def test_d28_live_test_catalog_expects_states_the_reader_emits(skill_text, number):
    section = _check_section(skill_text, number)
    states = set(_STATE.findall(section))
    decisions = {reading.PENDING, reading.ORPHANED, reading.APPROVED, reading.REJECTED}
    assert states and states <= decisions, "State is the decision, never the outcome"
    outcomes = set(_OUTCOME.findall(section))
    if _RUNS[number]:
        assert reading.APPROVED in states and outcomes == {reading.EXECUTED}
    else:
        assert reading.APPROVED not in states and not outcomes


def test_d28_live_test_catalog_reads_show_as_two_lines(skill_text):
    display = (ROOT / "gaia" / "approvals" / "display.py").read_text(encoding="utf-8")
    assert '"  State       : {state}"' in display and "Outcome     :" in display
    assert "State and Outcome are separate lines" in skill_text


def test_d28_live_test_catalog_typed_answer_accepts_orphaned(skill_text):
    section = _check_section(skill_text, 8)
    assert {reading.PENDING, reading.ORPHANED} <= set(_STATE.findall(section))
    assert "30 minutes" in section


def test_d28_live_test_catalog_evidence_and_cleanup_apply_to_every_run(skill_text):
    running = skill_text.split("## Area: approvals")[0]
    assert "`gaia approvals show <approval_id>`" in running
    assert "Clean everything" in running and "`rm`" in running


def test_d28_live_test_catalog_covers_d30_and_d40_minimum(skill_text):
    for number, marker in {
        1: "Approve", 2: "not reusable", 3: "Details", 4: "one signature",
        5: "whole signature is rejected", 6: "[ CWD: <folder>/otra ]",
        7: "decides only its own", 8: "types an answer", 9: "[GAIA-SECURITY] [ AGENT-REQUEST ]",
    }.items():
        assert marker in _check_section(skill_text, number)


def test_d28_live_test_catalog_verbs_exist_in_the_cli(skill_text):
    cli = APPROVALS_CLI.read_text(encoding="utf-8")
    for verb in ("--cwd", "--details", "reject"):
        assert verb in skill_text
        assert f'"{verb}"' in cli


def test_d28_live_test_catalog_is_read_by_both_orchestrators():
    assert "Skill('gaia-check')" in ORCHESTRATOR.read_text(encoding="utf-8")
    sources = _install_helpers._opencode_agent_sources(ROOT)
    assert sources[0] == ORCHESTRATOR, "OpenCode's orchestrator is derived from the same file"


def test_d28_live_test_catalog_folder_survives_scratch_retention(tmp_path):
    contract_id = "a" + "1" * 16 + ".abcdef123456"
    assert fs_rules._entry_contract_id(contract_id) == contract_id
    assert fs_rules._entry_contract_id(FOLDER) is None

    folder = tmp_path / FOLDER
    (folder / "otra").mkdir(parents=True)
    (folder / f"{contract_id}.txt").write_text("x")
    old = time.time() - 30 * 24 * 3600
    os.utime(folder, (old, old))

    assert fs_rules.collectable_turn_scoped(tmp_path, grace_hours=0) == []
