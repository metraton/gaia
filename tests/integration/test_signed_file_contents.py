"""A signature covers the files its command runs or reads, and says what it verifies.

The set is sealed by the real ``gaia approvals request-set`` subprocess and
approved through the question-lane bridge; execution is judged by the Claude
Code adapter's PreToolUse over a subagent Bash event, the match path a signed
item takes in production. A file the command runs (``bash deploy.sh``) or
reads (``--body-file``, ``-f``) that changes between signing and running makes
the item no longer match, so the grant stays unconsumed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from tests.integration.test_question_lane_consent_parity import (
    AGENT_ID,
    GAIA_CLI,
    SUBAGENT_SESSION,
    _approve_in_question_lane,
    _bind_child_session,
    _claude_code_verdict,
    _grant_row,
    substrate,  # noqa: F401 -- the fixture every case below runs on
)

SCRIPT = "kubectl delete namespace scratch\n"
BODY = "Seals the files a signed command reads.\n"
PHRASES = {
    "--what": "Aplicar el cambio planeado.",
    "--question": "¿Aplico el cambio?",
    "--rollback": "Recrear el namespace scratch.",
    "--verification": "kubectl get namespace scratch",
    "--shared-state": "Sí: borra un namespace del clúster compartido.",
}


def _request_set(cwd, commands, *, omit=()):
    """Run request-set in ``cwd`` with every phrase except those named in ``omit``."""
    argv = [sys.executable, str(GAIA_CLI), "approvals", "request-set"]
    for command in commands:
        argv += ["--command", command, "--does", "Aplica una parte.", "--impact", "Cambia el clúster."]
    for flag, value in PHRASES.items():
        if flag not in omit:
            argv += [flag, value]
    argv += ["--agent-id", AGENT_ID, "--session-id", SUBAGENT_SESSION, "--json"]
    return subprocess.run(
        argv, cwd=str(cwd), env=dict(os.environ), capture_output=True, text=True, timeout=180,
    )


def _signed(tmp_path, db_path, command):
    """Seal ``command``, approve it and bind the subagent that will run it."""
    result = _request_set(tmp_path, [command])
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    approval_id = json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]
    _approve_in_question_lane(approval_id)
    _bind_child_session(db_path)
    return approval_id


def _approval_count(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    finally:
        con.close()


def _payload(db_path, approval_id):
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT payload_json FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return json.loads(row[0])
    finally:
        con.close()


FILE_CASES = {
    "script the command runs": ("bash deploy.sh", "deploy.sh", SCRIPT),
    "body file the command reads": (
        "gh pr create --title seal --body-file body.md", "body.md", BODY,
    ),
    "manifest read through -f": ("kubectl apply -f app.yaml", "app.yaml", "kind: Namespace\n"),
}


@pytest.mark.parametrize("case", sorted(FILE_CASES))
def test_a_file_changed_after_signing_no_longer_matches(substrate, tmp_path, case):
    command, name, content = FILE_CASES[case]
    (tmp_path / name).write_text(content)
    approval_id = _signed(tmp_path, substrate, command)

    (tmp_path / name).write_text(content + "kubectl delete namespace production\n")

    assert _claude_code_verdict(command) is False
    row = _grant_row(substrate, approval_id)
    assert row["next_index"] == 0
    assert row["reservation_tool_use_id"] is None


@pytest.mark.parametrize("case", sorted(FILE_CASES))
def test_an_unchanged_file_still_matches(substrate, tmp_path, case):
    command, name, content = FILE_CASES[case]
    (tmp_path / name).write_text(content)
    approval_id = _signed(tmp_path, substrate, command)

    assert _claude_code_verdict(command) is True
    assert _grant_row(substrate, approval_id)["reservation_tool_use_id"] is not None


def test_a_file_absent_at_signing_that_appears_before_running_no_longer_matches(
    substrate, tmp_path,
):
    approval_id = _signed(tmp_path, substrate, "kubectl apply -f later.yaml")

    (tmp_path / "later.yaml").write_text("kind: Namespace\n")

    assert _claude_code_verdict("kubectl apply -f later.yaml") is False
    assert _grant_row(substrate, approval_id)["next_index"] == 0


@pytest.mark.parametrize(
    "command, prepare",
    [
        ("bash missing.sh", lambda root: None),
        ("gh pr create --title seal --body-file missing.md", lambda root: None),
        ("kubectl apply -f manifests", lambda root: (root / "manifests").mkdir()),
    ],
    ids=["script not written yet", "body file not written yet", "directory operand"],
)
def test_a_file_the_signature_cannot_seal_is_refused_before_minting(
    substrate, tmp_path, command, prepare,
):
    prepare(tmp_path)

    result = _request_set(tmp_path, [command])

    assert result.returncode != 0
    assert _approval_count(substrate) == 0


def test_a_file_over_the_seal_limit_is_refused_before_minting(substrate, tmp_path):
    from gaia.approvals.command_set import SEALED_FILE_MAX_BYTES

    with open(tmp_path / "huge.sh", "wb") as handle:
        handle.truncate(SEALED_FILE_MAX_BYTES + 1)

    result = _request_set(tmp_path, ["bash huge.sh"])

    assert result.returncode != 0
    assert _approval_count(substrate) == 0


@pytest.mark.parametrize("flag", ["--verification", "--shared-state"])
def test_a_set_without_verification_or_shared_state_is_refused_before_minting(
    substrate, tmp_path, flag,
):
    result = _request_set(tmp_path, ["git push origin main"], omit=(flag,))

    assert result.returncode != 0
    assert flag in (result.stdout + result.stderr)
    assert _approval_count(substrate) == 0


def test_details_show_the_sealed_verification_and_shared_state(substrate, tmp_path):
    result = _request_set(tmp_path, ["git push origin main"])
    assert result.returncode == 0, result.stderr
    approval_id = json.loads(result.stdout.strip().splitlines()[-1])["approval_id"]
    payload = _payload(substrate, approval_id)

    shown = subprocess.run(
        [sys.executable, str(GAIA_CLI), "approvals", "question", approval_id, "--details"],
        cwd=str(tmp_path), env=dict(os.environ), capture_output=True, text=True, timeout=180,
    )

    assert shown.returncode == 0, shown.stderr
    (details,) = [q["question"] for q in json.loads(shown.stdout)["questions"]]
    assert payload["verification"] and payload["verification"] in details
    assert payload["shared_state"] and payload["shared_state"] in details
