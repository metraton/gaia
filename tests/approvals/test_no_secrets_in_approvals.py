"""No secret value is persisted or shown by the approval flow (audit handoff 32196, S3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.approvals import core, secret_audit
from gaia.approvals.chain import canonical_payload, fingerprint_payload, insert_event, validate_chain
from gaia.redaction import has_clear_secret, redact_text
from gaia.store import writer

REPO = str(Path(__file__).resolve().parent)
FILLER = "aB3dE5gH7jK9mN1pQ3sT5vW7"
SHAPES = {
    "gho": "gho_" + FILLER,
    "ghp": "ghp_" + FILLER,
    "ghs": "ghs_" + FILLER,
    "github_pat": "github_pat_" + FILLER,
    "ya29": "ya29." + FILLER,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "slack": "xoxb-" + "1234567890-abcdefghij",
    "gitlab": "glpat-" + FILLER,
}


LEGIT_COMMANDS = [
    "gcloud run deploy x --set-secrets=DB_PASSWORD=PROJ_DB_PASSWORD:latest,API_TOKEN=OTHER_TOKEN:latest",
    "GH_TOKEN=$(gh auth token) gh pr list",
    "curl -H \"Authorization: Bearer $(gcloud auth print-access-token)\" https://example.test",
    "python3 -c \"token=a[0]; print(token)\"",
    "curl -d client_secret=langfuse-secret https://example.test",
    "export PASSWORD=password99",
    "echo $GH_TOKEN",
]


def _items(command: str) -> list[dict]:
    return [{"command": command, "cwd": REPO, "expect_exit": [], "does": "Runs it.", "impact": "None."}]


def _seal(command: str, kind: str = "command_set", **extra) -> dict:
    kwargs = {"what": "Run it", "session_id": "ses", "agent_id": "gaia-system", "operation": "MUTATIVE command intercepted: run"}
    kwargs.update(extra)
    return core.seal_request(kind, _items(command), **kwargs)


@pytest.mark.parametrize("token", SHAPES.values(), ids=SHAPES.keys())
def test_bare_token_shape_is_a_clear_secret_and_is_redacted(token):
    command = f"curl -H 'X-Auth: {token}' https://example.test"
    assert has_clear_secret(command)
    assert token not in redact_text(command)


@pytest.mark.parametrize(
    "command",
    ["GH_TOKEN=$(gh auth token) gh pr list", "curl -H \"Authorization: Bearer $(gcloud auth print-access-token)\" x", "echo $GH_TOKEN"],
)
def test_reference_forms_are_not_secrets(command):
    assert not has_clear_secret(command)


@pytest.mark.parametrize("kind", ["command", "command_set"])
@pytest.mark.parametrize("command", [f"GH_TOKEN={SHAPES['gho']} gh pr list", f"GOOGLE_OAUTH_ACCESS_TOKEN={SHAPES['ya29']} terragrunt apply",
                                     f"curl -H \"Authorization: Bearer {SHAPES['ya29']}\" x", f"echo {SHAPES['aws']}"])
def test_request_with_a_literal_token_is_refused_with_the_steering_message(kind, command):
    with pytest.raises(core.SealError) as refused:
        _seal(command, kind)
    message = str(refused.value)
    assert "$(" in message and "$GH_TOKEN" in message
    assert not any(token in message for token in SHAPES.values())


def test_request_whose_phrase_carries_a_token_is_refused():
    with pytest.raises(core.SealError, match="credential in clear"):
        _seal("gh pr merge 41 --squash", rationale=f"rotated to {SHAPES['ghp']}")


def test_request_with_a_reference_is_sealed_with_its_raw_fingerprint():
    command = "GH_TOKEN=$(gh auth token) gh pr merge 41 --squash"
    payload = _seal(command)
    assert payload["commands"] == [command]
    assert payload["items"][0]["command"] == command


def test_displays_of_a_legacy_row_hide_the_token(capsys):
    from gaia.approvals import display, surface

    command = f"GH_TOKEN={SHAPES['gho']} gh workflow run x.yml"
    payload = {"exact_content": command, "commands": [command], "operation": "run"}
    display.print_approval_detail({"id": "P-1", "status": "approved", "payload_json": json.dumps(payload)}, [])
    shown = capsys.readouterr().out + display._command_summary(json.dumps(payload)) + surface._target({"command": command})
    assert SHAPES["gho"] not in shown and "gh workflow run" in shown


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "copy.db"
    con = sqlite3.connect(path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    secret = f"GH_TOKEN={SHAPES['gho']} gh workflow run x.yml"
    clean = {"commands": ["gh pr list"], "exact_content": "gh pr list", "items": [{"key": "0:ab", "command": "gh pr list"}]}
    leaky = {"commands": [secret], "exact_content": secret, "items": [{"key": "0:cd", "command": secret}]}
    legit = {"commands": LEGIT_COMMANDS, "exact_content": LEGIT_COMMANDS[0], "items": [{"key": "0:ef", "command": LEGIT_COMMANDS[0]}]}
    for approval_id, payload in (("P-clean", clean), ("P-leaky", leaky), ("P-legit", legit)):
        fingerprint = fingerprint_payload(payload)
        con.execute(
            "INSERT INTO approvals (id, status, fingerprint, payload_json) VALUES (?, 'approved', ?, ?)",
            (approval_id, fingerprint, canonical_payload(payload)),
        )
        insert_event(con, approval_id, "REQUESTED", payload_json=canonical_payload(payload), fingerprint=fingerprint)
    con.execute(
        "INSERT INTO approval_grants (approval_id, command_set_json, failure_reason) VALUES ('P-leaky', ?, ?)",
        (json.dumps([{"command": secret}]), f"exit 1 with {SHAPES['ya29']}"),
    )
    con.commit()
    con.close()
    return path


def _stored_text(path) -> str:
    con = sqlite3.connect(path)
    try:
        return "".join(str(value) for table in ("approvals", "approval_events", "approval_grants")
                       for row in con.execute(f"SELECT * FROM {table}") for value in row)
    finally:
        con.close()


@pytest.mark.parametrize("command", LEGIT_COMMANDS)
def test_audit_leaves_legitimate_commands_byte_identical(command):
    assert secret_audit.scrub_text(command) == command
    assert secret_audit.scrub_text(json.dumps({"command": command})) == json.dumps({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        f"curl -d 'grant_type=password&password={FILLER}&client_id=x' https://example.test",
        f"curl -H 'Authorization: Bearer {FILLER}' https://example.test",
        f"DB_PASSWORD={FILLER} psql",
    ],
)
def test_audit_masks_a_literal_credential_that_has_no_token_shape(command):
    scrubbed = secret_audit.scrub_text(command)
    assert FILLER not in scrubbed
    assert scrubbed.count("[REDACTED]") == 1
    assert secret_audit.scrub_text(scrubbed) == scrubbed


def test_audit_finds_every_leaky_column_and_never_touches_the_database(legacy_db):
    before = legacy_db.read_bytes()
    con = secret_audit.open_read_only(str(legacy_db))
    try:
        findings = list(secret_audit.scan(con))
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM approvals")
    finally:
        con.close()
    assert {(f.table, f.column, f.key) for f in findings} == {
        ("approvals", "payload_json", "P-leaky"),
        ("approval_events", "payload_json", 2),
        ("approval_grants", "command_set_json", "P-leaky"),
        ("approval_grants", "failure_reason", "P-leaky"),
    }
    assert legacy_db.read_bytes() == before


def test_emitted_scrub_removes_every_token_and_keeps_chain_and_fingerprints(legacy_db):
    con = secret_audit.open_read_only(str(legacy_db))
    try:
        sql = secret_audit.scrub_sql(con, list(secret_audit.scan(con)))
    finally:
        con.close()
    assert not any(token in sql for token in SHAPES.values())
    con = sqlite3.connect(legacy_db)
    fingerprints = con.execute("SELECT id, fingerprint FROM approvals ORDER BY id").fetchall()
    con.executescript(sql)
    assert con.execute("SELECT fingerprint FROM approvals ORDER BY id").fetchall() == [(f,) for _, f in fingerprints]
    assert validate_chain("P-leaky", con)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE approval_events SET payload_json = 'x'")
    con.close()
    assert not any(token in _stored_text(legacy_db) for token in SHAPES.values())
    con = secret_audit.open_read_only(str(legacy_db))
    assert list(secret_audit.scan(con)) == []
    con.close()


def test_audit_verb_reports_counts_without_values(legacy_db, capsys):
    from bin.cli.approvals import cmd_audit_secrets

    assert cmd_audit_secrets(SimpleNamespace(db=str(legacy_db), emit_sql=False, json=True)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["count"] == 4
    assert not any(token in json.dumps(report) for token in SHAPES.values())
