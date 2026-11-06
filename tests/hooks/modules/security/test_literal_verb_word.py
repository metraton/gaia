"""The literal-verb rule (D198 point 2): the word that decides what runs must be literal; an operand may be computed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.security.mutative_verbs import detect_mutative_command


@pytest.mark.parametrize("command, word", [
    ("$CMD status", "$CMD"),
    ("kubectl $VERB pods", "$VERB"),
    ("npm $SCRIPT", "$SCRIPT"),
    ("frobctl $VERB thing", "$VERB"),
    ("frobctl --verbose ${VERB}", "${VERB}"),
    ("zorbctl {get,delete} repos", "{get,delete}"),
    ("kubectl {get,delete} pods", "{get,delete}"),
    ("zorbctl x{1..3} repos", "x{1..3}"),
])
def test_an_expanded_program_or_verb_word_asks_naming_it(command, word, tmp_path):
    result = detect_mutative_command(command, cwd=str(tmp_path))
    assert result.is_mutative and result.rule == "unknown.dynamic-span", (command, result)
    assert word in result.reason


@pytest.mark.parametrize("command", [
    "ls $DIR",
    "frobctl status $NAME",
    "kubectl get pods $NAME",
])
def test_an_expanded_operand_keeps_its_verdict(command, tmp_path):
    assert not detect_mutative_command(command, cwd=str(tmp_path)).is_mutative


@pytest.mark.parametrize("command", [
    "python3 -m pytest tests/replay/test_validator_replay_diff.py -m replay -q",
    "pytest tests -m replay",
    "jq -c '{a: .b}' file.json",
    "jq -r '.items[] | select(.x)' file.json",
])
def test_an_option_value_or_filter_of_a_modelled_program_is_no_verb(command, tmp_path):
    result = detect_mutative_command(command, cwd=str(tmp_path))
    assert not result.is_mutative, (command, result)
