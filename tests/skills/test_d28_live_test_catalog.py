"""The live signature catalog (D28, refined by D30) stays runnable as written.

Each check pins a catalog claim to the code that makes it true: the states it
expects to the approvals reader, the verbs it tells agents to run to the CLI,
the orchestrator that must load it in both hosts to the OpenCode agent
derivation, and the fixed folder's survival to scratch retention.
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
SKILL = ROOT / "skills" / "signature-live-tests" / "SKILL.md"
ORCHESTRATOR = ROOT / "agents" / "gaia-orchestrator.md"
APPROVALS_CLI = ROOT / "bin" / "cli" / "approvals.py"
FOLDER = "prueba-firmas"
TESTS = range(1, 9)

_BIN_DIR = ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _install_helpers  # noqa: E402


@pytest.fixture(scope="module")
def skill_text() -> str:
    """The skill with its line wrapping folded, so a phrase is found wherever it wraps."""
    return " ".join(SKILL.read_text(encoding="utf-8").split())


def _test_section(text: str, number: int) -> str:
    match = re.search(
        rf"\*\*Test {number} -- .*?(?=\*\*Test {number + 1} -- | Running the catalog|\Z)",
        text,
        re.S,
    )
    assert match, f"test {number} is missing from the catalog"
    return match.group(0)


def test_d28_live_test_catalog_is_a_named_skill(skill_text):
    assert skill_text.startswith("--- name: signature-live-tests description:")
    assert "ejecuta la prueba N" in skill_text


def test_d28_live_test_catalog_lives_in_the_fixed_folder(skill_text):
    assert f"<scratch>/{FOLDER}/" in skill_text
    assert "`gaia paths`" in skill_text
    assert "sandbox" in skill_text and "no fixture" in skill_text


@pytest.mark.parametrize("number", TESTS)
def test_d28_live_test_catalog_each_test_is_complete(skill_text, number):
    section = _test_section(skill_text, number)
    assert f"`nota-{number}" in section, "each test owns its own file"
    assert "User:" in section, "each test says what the user does"
    assert "Expected:" in section and "State `" in section


@pytest.mark.parametrize("number", TESTS)
def test_d28_live_test_catalog_expects_states_the_reader_emits(skill_text, number):
    states = set(re.findall(r"State (?:is )?(?:still )?`(\w+)`", _test_section(skill_text, number)))
    real = {reading.PENDING, reading.EXECUTED, reading.REJECTED}
    assert states and states <= real


def test_d28_live_test_catalog_evidence_and_cleanup_apply_to_every_test(skill_text):
    running = skill_text.split("## The catalog")[0]
    assert "`gaia approvals show <approval_id>`" in running
    assert "removes its own files" in running and "`rm`" in running


def test_d28_live_test_catalog_covers_d30_minimum(skill_text):
    for number, marker in {
        1: "Approve", 2: "not reusable", 3: "Details", 4: "one signature",
        5: "en <folder>/otra", 6: "decides only its own", 7: "types an answer",
        8: "Solicitud de aprobación",
    }.items():
        assert marker in _test_section(skill_text, number)


def test_d28_live_test_catalog_verbs_exist_in_the_cli(skill_text):
    cli = APPROVALS_CLI.read_text(encoding="utf-8")
    for verb in ("request-set", "--cwd", "--details", "reject"):
        assert verb in skill_text
        assert f'"{verb}"' in cli


def test_d28_live_test_catalog_is_read_by_both_orchestrators():
    assert "Skill('signature-live-tests')" in ORCHESTRATOR.read_text(encoding="utf-8")
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
