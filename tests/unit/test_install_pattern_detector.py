"""
test_install_pattern_detector.py -- AC-2 verification.

install_detector.detect(output) returns the correct result for install
patterns (positive cases) and returns {matched: False} for non-install
output (negative cases).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure hooks/ is on the path so install_detector imports cleanly
_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.install_detector import detect


# ---------------------------------------------------------------------------
# Test matrix: (output_fixture, expected_matched, expected_pattern, expected_target)
# ---------------------------------------------------------------------------

_MATRIX = [
    # --- Positive: npm install -g (global) ---
    pytest.param(
        "npm install -g acli → added 1 package",
        True, "npm install", "acli",
        id="npm_global_install_acli",
    ),
    pytest.param(
        "Running: npm install -g @atlassian/acli\nadded 1 package",
        True, "npm install", "acli",
        id="npm_global_scoped_atlassian_acli",
    ),
    pytest.param(
        "$ npm install -g gaia\nadded 1 package, audited 42 packages in 3s",
        True, "npm install", "gaia",
        id="npm_global_gaia",
    ),
    # --- Positive: pip install ---
    pytest.param(
        "Successfully installed requests-2.31.0",
        False, "", "",  # "Successfully installed" alone doesn't match command regex
        id="pip_success_message_no_command",
    ),
    pytest.param(
        "pip install requests\nSuccessfully installed requests-2.31.0",
        True, "pip install", "requests",
        id="pip_install_requests",
    ),
    pytest.param(
        "pip3 install pytest\nSuccessfully installed pytest-7.4.0",
        True, "pip install", "pytest",
        id="pip3_install_pytest",
    ),
    # --- Positive: gaia install ---
    pytest.param(
        "gaia install acli",
        True, "gaia install", "acli",
        id="gaia_install_acli",
    ),
    # --- Positive: auth configure ---
    pytest.param(
        "gcloud auth configure\nCredentials saved.",
        True, "auth configure", "gcloud",
        id="auth_configure_gcloud",
    ),
    pytest.param(
        "acli auth configure --token abc123",
        True, "auth configure", "acli",
        id="auth_configure_acli",
    ),
    # --- Negative: read-only commands ---
    pytest.param(
        "npm list -g --depth=0",
        False, "", "",
        id="negative_npm_list",
    ),
    pytest.param(
        "pip list",
        False, "", "",
        id="negative_pip_list",
    ),
    pytest.param(
        "kubectl get pods -n default",
        False, "", "",
        id="negative_kubectl_get",
    ),
    pytest.param(
        "gcloud describe --help",
        False, "", "",
        id="negative_gcloud_describe",
    ),
    pytest.param(
        "",
        False, "", "",
        id="negative_empty_string",
    ),
    pytest.param(
        "Some random output with no install commands",
        False, "", "",
        id="negative_random_output",
    ),
]


@pytest.mark.parametrize("output,exp_matched,exp_pattern,exp_target", _MATRIX)
def test_detect_matrix(output, exp_matched, exp_pattern, exp_target):
    """AC-2: detect() returns correct matched/pattern/target for each fixture."""
    result = detect(output)

    assert result["matched"] == exp_matched, (
        f"Expected matched={exp_matched} for output={output!r}, got {result}"
    )

    if exp_matched:
        assert result.get("pattern") == exp_pattern, (
            f"Expected pattern={exp_pattern!r}, got {result.get('pattern')!r}"
        )
        assert result.get("target") == exp_target, (
            f"Expected target={exp_target!r}, got {result.get('target')!r}"
        )
    else:
        # No match: no pattern/target keys required (but if present they should be empty/falsy)
        assert not result.get("pattern"), f"Unexpected pattern in no-match result: {result}"
