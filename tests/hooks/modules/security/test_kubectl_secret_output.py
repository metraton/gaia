"""kubectl Secret reads must expose metadata, never value-bearing output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[5] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.kubectl_secret_output import check_kubectl_secret_output
from modules.tools.bash_validator import BashValidator


@pytest.mark.parametrize(
    "command",
    [
        "kubectl get secret fixture -o yaml",
        "kubectl get secrets -o json",
        "kubectl get secret/fixture -o jsonpath={.data.password}",
        "kubectl get secret fixture --output=go-template={{.data.token}}",
        "kubectl get secret fixture -o custom-columns=NAME:.metadata.name,VALUE:.data.token",
    ],
)
def test_value_bearing_secret_outputs_are_rejected(command):
    violation = check_kubectl_secret_output(command)

    assert violation is not None
    assert "can expose secret values" in violation.reason


@pytest.mark.parametrize(
    "command",
    [
        "kubectl get secret fixture",
        "kubectl get secret fixture -o name",
        "kubectl get secret fixture -o custom-columns=NAME:.metadata.name,TYPE:.type",
        "kubectl describe secret fixture",
        "kubectl get configmap fixture -o yaml",
    ],
)
def test_metadata_only_and_non_secret_reads_remain_available(command):
    assert check_kubectl_secret_output(command) is None


def test_bash_validator_blocks_value_output_before_execution():
    result = BashValidator().validate("kubectl get secret fixture -o json")

    assert not result.allowed
    assert "can expose secret values" in result.reason
