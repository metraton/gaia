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


def test_a_password_in_a_url_is_redacted_and_the_rest_of_the_url_kept():
    command = f"psql postgres://app:{SYNTHETIC_PASSWORD}@db.internal:5432/app -c 'select 1'"
    heredoc = f"cat > app.env <<'EOF'\nDATABASE_URL=redis://:{SYNTHETIC_PASSWORD}@cache:6379/0\nEOF"
    output = f"connected to https://deploy:{SYNTHETIC_PASSWORD}@git.example/repo.git\n"

    for source in (command, heredoc, output):
        redacted = redact_text(source)
        assert SYNTHETIC_PASSWORD not in redacted, redacted
        assert f":{REDACTED}@" in redacted

    assert redact_text(command).startswith(f"psql postgres://app:{REDACTED}@db.internal:5432/app")
    assert redact_text("see https://example.com:8443/path and user@host") == (
        "see https://example.com:8443/path and user@host"
    )


def test_a_private_key_block_is_redacted_whole_terminated_or_not():
    body = "MIIEowIBAAKCAQEA" + "Zk9" * 40
    closed = f"before\n-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\nafter\n"
    heredoc = f"cat > id <<'EOF'\n-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----\nEOF"
    unterminated = f"-----BEGIN PRIVATE KEY-----\n{body}"

    for source in (closed, heredoc, unterminated):
        redacted = redact_text(source)
        assert body[:16] not in redacted, redacted
        assert REDACTED in redacted

    assert redact_text(closed).startswith("before\n-----BEGIN RSA PRIVATE KEY-----")
    assert redact_text(closed).endswith("-----END RSA PRIVATE KEY-----\nafter\n")
    assert redact_text(heredoc).endswith("EOF")
    public = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQ\n-----END PUBLIC KEY-----"
    assert redact_text(public) == public
