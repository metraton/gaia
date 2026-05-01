"""
test_install_detector_topic_key.py -- AC-5 verification.

build_topic_key(kind, target) produces the expected topic_key string
in the format "{kind}/{family}/{target}". The same target always produces
the same topic_key (idempotent upsert guarantee).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure hooks/ is on the path
_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.install_detector import build_topic_key


@pytest.mark.parametrize("kind,target,expected", [
    # Known family mappings
    ("cli", "acli",   "cli/atlassian/acli"),
    ("cli", "gcloud", "cli/google/gcloud"),
    ("cli", "gaia",   "cli/gaia/gaia"),
    ("cli", "kubectl","cli/kubernetes/kubectl"),
    ("cli", "helm",   "cli/kubernetes/helm"),
    ("cli", "terraform", "cli/hashicorp/terraform"),
    # pkg kind
    ("pkg", "requests", "pkg/generic/requests"),
    ("pkg", "pytest",   "pkg/generic/pytest"),
    # Generic fallback
    ("cli", "some-custom-tool", "cli/generic/some-custom-tool"),
])
def test_integration_row_topic_key(kind, target, expected):
    """AC-5: build_topic_key produces the correct topic_key for known and unknown targets."""
    result = build_topic_key(kind, target)
    assert result == expected, (
        f"build_topic_key({kind!r}, {target!r}) = {result!r}, expected {expected!r}"
    )


def test_topic_key_is_idempotent():
    """Same kind+target always produces the same topic_key (reinstall idempotency)."""
    first = build_topic_key("cli", "acli")
    second = build_topic_key("cli", "acli")
    assert first == second == "cli/atlassian/acli"


def test_npm_install_acli_full_flow():
    """Integration sub-test: npm install -g acli -> topic_key='cli/atlassian/acli'."""
    from modules.install_detector import detect
    output = "npm install -g acli → added 1 package"
    match = detect(output)
    assert match["matched"] is True
    assert match["target"] == "acli"
    assert match["kind"] == "cli"
    tk = build_topic_key(match["kind"], match["target"])
    assert tk == "cli/atlassian/acli"
