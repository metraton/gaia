"""`gaia open KIND:SCOPE/ID#FIELD` resolves any id spelling of a stored thing.

The fixture database reproduces the references that failed to open on
2026-10-28: a turn named by its agent_id and by its numeric handoff id, and a
memory row cited under the workspace it lived in before it moved. Every
reference the resolver prints must survive the shell guards unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
SCHEMA = _REPO_ROOT / "gaia" / "store" / "schema.sql"

HANDOFF_ID = 34522
CONTRACT_ID = "a173f63dee5740a6f.9301a33c2ddb"
AGENT_ID = "a173f63dee5740a6f"
HARNESS_ID = "a96b3329d5915478a"
MOVED_SLUG = "decision_gaia_vocabulario_workspace_grupo_proyecto_v3_20261028"
USER_SLUG = "user_pref_formato_estandar_descripcion_pr_20261022"
BRIEF = "information-lives-once-at-its-coordinate"
APPROVAL_ID = "P-39e3fa64e45d4198bd1028f369c81ce4"
SAFE_REF = re.compile(r"[A-Za-z0-9_.:/@-][A-Za-z0-9_.:/#@-]*\Z")


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "gaia-data" / "gaia.db"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    _seed(_db_path(tmp_path))


def _seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(SCHEMA.read_text())
        for name in ("me", "ws", "_gaia_user"):
            con.execute("INSERT INTO workspaces (name, identity) VALUES (?, ?)", (name, name))
        envelope = {
            "agent_status": {"agent_id": AGENT_ID, "agent_state": "COMPLETE",
                             "pending_steps": [], "next_action": "done"},
            "evidence_report": {"key_outputs": ["vocabulary proposal"]},
            "conclusion": "Workspace, group and project are three levels. The scan names groups.",
        }
        con.execute(
            "INSERT INTO agent_contract_handoffs (id, contract_id, agent_id, session_id, "
            "workspace, kind, agent_state, raw_handoff_json, created_at, harness_agent_id, "
            "dispatch_description, dispatch_prompt) "
            "VALUES (?, ?, ?, 'sess-1', 'me', 'investigation', 'COMPLETE', ?, "
            "'2026-10-28T19:55:03Z', ?, 'Vocabulario workspace-grupo-proyecto', 'project=gaia')",
            (HANDOFF_ID, CONTRACT_ID, AGENT_ID, json.dumps(envelope), HARNESS_ID),
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class) "
            "VALUES ('ws', ?, 'decision', 'Decision: workspace > group > project.', "
            "'The hierarchy body.', 'anchor')",
            (MOVED_SLUG,),
        )
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body) "
            "VALUES ('_gaia_user', ?, 'user', 'Preference: PR body format.', 'Seven headings.')",
            (USER_SLUG,),
        )
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status, title) "
            "VALUES (7, 'me', ?, 'in-progress', 'Information lives once')",
            (BRIEF,),
        )
        con.execute("INSERT INTO plans (id, brief_id, status, content) VALUES (94, 7, 'active', 'plan body')")
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal) VALUES (1044, 94, 16, 'Build the S1 resolver')"
        )
        con.execute(
            "INSERT INTO evidence (id, brief_id, ac_id, type, text) VALUES (42, 7, 'AC-4', 'text', 'measured')"
        )
        con.execute(
            "INSERT INTO approvals (id, agent_id, status, payload_json) VALUES (?, ?, 'pending', '{}')",
            (APPROVAL_ID, AGENT_ID),
        )
        con.execute(
            "INSERT INTO task_notifications (id, workspace, task_name, headline) "
            "VALUES (81, 'me', 'reminder', 'Load the timesheet')"
        )
        con.execute(
            "INSERT INTO project_context_contracts (workspace, contract_name, payload) "
            "VALUES ('ws', 'infrastructure', ?)",
            (json.dumps({"evidence_convention": "one file per gate"}),),
        )
        con.commit()
    finally:
        con.close()


def _open(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(GAIA_BIN), "open", *args],
        capture_output=True, text=True, env=env,
    )


def _open_json(ref: str) -> dict:
    result = _open(ref, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _printed_refs(text: str) -> list[str]:
    header = text.split("\n\n", 1)[0]
    return re.findall(r"gaia open (\S+)", header) + re.findall(r"^reference: (\S+)", header, re.M)


def test_agent_id_resolves_to_its_contract_and_labels_the_resume_id():
    shown = _open_json(f"contract:{AGENT_ID}")

    assert shown["reference"] == f"contract:{CONTRACT_ID}"
    assert shown["ids"]["handoff"] == str(HANDOFF_ID)
    assert shown["ids"]["agent_id"] == AGENT_ID
    assert shown["ids"]["resume"] == HARNESS_ID
    assert any(AGENT_ID in note and "agent_id" in note for note in shown["notes"])


def test_text_output_names_the_resume_handle():
    result = _open(f"contract:{AGENT_ID}")

    assert result.returncode == 0, result.stderr
    assert f"reference: contract:{CONTRACT_ID}" in result.stdout
    assert f"resume: SendMessage to {HARNESS_ID}" in result.stdout


@pytest.mark.parametrize("spelling", [str(HANDOFF_ID), CONTRACT_ID, HARNESS_ID])
def test_every_contract_id_spelling_lands_on_the_same_row(spelling):
    shown = _open_json(f"contract:{spelling}")

    assert shown["reference"] == f"contract:{CONTRACT_ID}"
    assert shown["ids"]["handoff"] == str(HANDOFF_ID)


def test_contract_field_prints_only_that_field():
    result = _open(f"contract:{HANDOFF_ID}#conclusion")

    assert result.returncode == 0, result.stderr
    assert "Workspace, group and project are three levels." in result.stdout
    assert "vocabulary proposal" not in result.stdout
    assert f"reference: contract:{CONTRACT_ID}#conclusion" in result.stdout


def test_moved_memory_row_resolves_and_says_where_it_lives_now():
    shown = _open_json(f"memory:me/{MOVED_SLUG}")

    assert shown["reference"] == f"memory:ws/{MOVED_SLUG}"
    assert shown["document"]["body"] == "The hierarchy body."
    assert any("moved" in note and "ws" in note for note in shown["notes"])


def test_user_scope_maps_to_the_user_sentinel_and_back():
    shown = _open_json(f"memory:user/{USER_SLUG}")

    assert shown["reference"] == f"memory:user/{USER_SLUG}"
    assert shown["document"]["body"] == "Seven headings."


def test_line_prints_only_the_why_line_generated_from_the_row():
    result = _open(f"memory:ws/{MOVED_SLUG}", "--line")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"Decision: workspace > group > project. -- gaia open memory:ws/{MOVED_SLUG}"
    )


@pytest.mark.parametrize("ref, canonical", [
    ("plan:94", "plan:94"),
    (f"plan:{BRIEF}", "plan:94"),
    ("task:1044", "task:1044"),
    (f"task:{BRIEF}/16", "task:1044"),
    (f"brief:{BRIEF}", f"brief:me/{BRIEF}"),
    ("evidence:42", "evidence:42"),
    (f"approval:{APPROVAL_ID}", f"approval:{APPROVAL_ID}"),
    ("approval:P-39e3fa64", f"approval:{APPROVAL_ID}"),
    ("notification:81", "notification:81"),
    ("context:ws/infrastructure", "context:ws/infrastructure"),
])
def test_each_kind_resolves_to_its_canonical_reference(ref, canonical):
    assert _open_json(ref)["reference"] == canonical


def test_task_names_its_order_and_brief():
    shown = _open_json("task:1044")

    assert shown["ids"]["order"] == "16"
    assert shown["ids"]["brief"] == f"brief:me/{BRIEF}"


def test_context_field_reads_inside_the_section():
    result = _open("context:ws/infrastructure#evidence_convention")

    assert result.returncode == 0, result.stderr
    assert "one file per gate" in result.stdout


def test_unresolvable_reference_exits_1_naming_the_nearest_match():
    result = _open("memory:ws/decision_gaia_vocabulario_workspace")

    assert result.returncode == 1
    assert f"memory:ws/{MOVED_SLUG}" in result.stderr


@pytest.mark.parametrize("bad", ["memory", "nonsense:12", "contract:", "memory:ws/x y"])
def test_malformed_reference_exits_1(bad):
    result = _open(bad)

    assert result.returncode == 1
    assert result.stderr.strip()


@pytest.mark.parametrize("ref", [
    f"contract:{AGENT_ID}",
    f"contract:{HANDOFF_ID}#conclusion",
    f"memory:me/{MOVED_SLUG}",
    "task:1044",
    "context:ws/infrastructure#evidence_convention",
])
def test_every_printed_reference_is_shell_safe(ref):
    result = _open(ref)

    assert result.returncode == 0, result.stderr
    printed = _printed_refs(result.stdout)
    assert printed
    for value in printed:
        assert SAFE_REF.match(value), value


def test_a_reference_outside_the_safe_alphabet_is_never_printed():
    sys.path.insert(0, str(_REPO_ROOT))
    from gaia.refs import Ref, RefError, format_ref

    assert format_ref(Ref("memory", "ws", "x")) == "memory:ws/x"
    with pytest.raises(RefError):
        format_ref(Ref("memory", "ws", "x$y"))
    with pytest.raises(RefError):
        format_ref(Ref("memory", "", "#x"))


def test_open_never_writes_the_database(tmp_path):
    db = _db_path(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    for ref in (f"contract:{AGENT_ID}", f"memory:me/{MOVED_SLUG}", "plan:94"):
        assert _open(ref).returncode == 0

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
