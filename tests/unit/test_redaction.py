"""Behavioral tests for structured secret redaction."""

from __future__ import annotations

import json

from gaia.redaction import REDACTED, redact_text, redact_tree


SYNTHETIC_PASSWORD = "synthetic-password-657"
SYNTHETIC_TOKEN = "synthetic-token-657"


def test_redact_text_preserves_secret_manifest_structure_without_values():
    source = f"""apiVersion: v1
kind: Secret
metadata:
  name: diagnostic-fixture
data:
  arbitrary-name: {SYNTHETIC_PASSWORD}
stringData:
  another-name: {SYNTHETIC_TOKEN}
type: Opaque
"""

    redacted = redact_text(source)

    assert SYNTHETIC_PASSWORD not in redacted
    assert SYNTHETIC_TOKEN not in redacted
    assert "name: diagnostic-fixture" in redacted
    assert "arbitrary-name: [REDACTED]" in redacted
    assert "another-name: [REDACTED]" in redacted
    assert "type: Opaque" in redacted


def test_redact_text_handles_json_assignments_flags_and_bearer_tokens():
    json_source = json.dumps(
        {
            "kind": "Secret",
            "metadata": {"name": "diagnostic-fixture"},
            "data": {"arbitrary-name": SYNTHETIC_PASSWORD},
        }
    )
    text_source = (
        f"PASSWORD={SYNTHETIC_PASSWORD} --token {SYNTHETIC_TOKEN} "
        f"Authorization: Bearer {SYNTHETIC_TOKEN}"
    )

    json_redacted = redact_text(json_source)
    text_redacted = redact_text(text_source)

    assert SYNTHETIC_PASSWORD not in json_redacted + text_redacted
    assert SYNTHETIC_TOKEN not in json_redacted + text_redacted
    assert "diagnostic-fixture" in json_redacted
    assert REDACTED in json_redacted
    assert text_redacted.count(REDACTED) == 3


def test_redact_tree_recurses_without_mutating_the_source():
    source = {
        "metadata": {"name": "diagnostic-fixture", "labels": ["safe"]},
        "nested": [{"api_key": SYNTHETIC_TOKEN}, f"token={SYNTHETIC_TOKEN}"],
        "secret_object": {
            "kind": "Secret",
            "data": {"arbitrary-name": SYNTHETIC_PASSWORD},
        },
    }

    redacted = redact_tree(source)

    assert source["nested"][0]["api_key"] == SYNTHETIC_TOKEN
    assert redacted["metadata"] == source["metadata"]
    assert redacted["nested"] == [{"api_key": REDACTED}, f"token={REDACTED}"]
    assert redacted["secret_object"]["data"] == {"arbitrary-name": REDACTED}
