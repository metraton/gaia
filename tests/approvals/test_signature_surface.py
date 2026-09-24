"""The one signature-surface renderer (plan 76, tasks 2 and 16; brief decisions D12, D14, D17, D37, D38).

Gaia composes every question of a signature from the phrases a requester
seals; hosts only show them. Since D37 each command is its own one-line
question in the machine format, the same text in both hosts: the signature
question names the agent and the exact command, the Details question adds what
it does, its impact and the rollback, with no validity and no approval id. The
D12 example is the golden test. Length limits are enforced when the request is
made, so an over-long phrase never reaches a presentation.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout

import pytest

from gaia.store import writer

APPROVAL_ID = "P-" + "a" * 32
AGENT = "gaia-system"
SESSION = "ses-requester"
REPO = "/home/jorge/ws/me"
WORKTREE_GAIA = (
    "/home/jorge/ws/me/.project-worktrees/gaia/0ac7481a9c2e4f6b8d0a1c3e5f7b9d2e/bin/gaia"
)
D12_COMMAND = (
    f"python3 {WORKTREE_GAIA} dev --workspace /home/jorge/ws/me --ref bbc2f09 --host all"
)
D12_TITLE = "Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos."
D12_QUESTION = "¿Reinstalo Gaia?"
D12_DOES = "gaia dev: instala en tu espacio de trabajo la versión nueva de main."
D12_IMPACT = "actualiza tu base de datos; ese cambio no se deshace."
D12_ROLLBACK = "volver al código anterior con --ref c1d8b89; la base queda actualizada."
OPTIONS = [
    {"label": "Approve", "description": "Autoriza exactamente este comando"},
    {"label": "Reject", "description": "Rechaza la firma; no se ejecuta nada"},
    {"label": "Details", "description": "Qué hace, impacto y cómo deshacerlo"},
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _seal(items, *, what=D12_TITLE, question=D12_QUESTION, rollback=D12_ROLLBACK, requested_from=None):
    from gaia.approvals import core

    return core.seal_request(
        "command_set",
        [{"cwd": REPO, **item} for item in items],
        what=what, question=question, rollback=rollback,
        session_id=SESSION, agent_id=AGENT, requested_from=requested_from,
    )


def _d12_payload():
    return _seal([{"command": D12_COMMAND, "does": D12_DOES, "impact": D12_IMPACT}])


def _asks(command):
    return f"[GAIA-SECURITY] [ AGENT-REQUEST ] [ gaia-system ] [ COMMAND ] [ {command} ]"


# --------------------------------------------------------------------------- #
# Golden: the D12 example, literally (D37 machine format)
# --------------------------------------------------------------------------- #

def test_signature_surface_golden_d12_example():
    from gaia.approvals import surface

    rendered = surface.render(_d12_payload(), APPROVAL_ID)

    details = (
        f"[GAIA-SECURITY] [ DETAILS ] [ gaia-system ] [ COMMAND: {D12_COMMAND} ] "
        f"[ DOES: {D12_DOES} ] [ IMPACT: {D12_IMPACT} ] [ ROLLBACK: {D12_ROLLBACK} ]"
    )
    assert rendered.questions == (
        {"question": _asks(D12_COMMAND), "header": "Firma 1/1", "options": OPTIONS, "multiSelect": False},
    )
    assert rendered.details_questions == (
        {"question": details, "header": "Detalle 1/1", "options": OPTIONS, "multiSelect": False},
    )
    assert (rendered.text, rendered.details) == (_asks(D12_COMMAND), details)
    assert "..." not in rendered.text


def test_signature_surface_shows_no_validity_and_no_approval_id():
    """The ID and the validity stay in the approval, never in what the user reads (D37)."""
    from gaia.approvals import surface

    rendered = surface.render(_d12_payload(), APPROVAL_ID)

    shown = json.dumps([rendered.questions, rendered.details_questions], ensure_ascii=False)
    assert APPROVAL_ID not in shown
    assert "30 min" not in shown and "Vale" not in shown


def test_signature_surface_question_phrase_is_held_to_its_limit():
    from gaia.approvals import surface

    _seal([{"command": "git push origin main"}], question="x" * 60)
    with pytest.raises(surface.SurfaceLimitError, match="60"):
        _seal([{"command": "git push origin main"}], question="x" * 61)


# --------------------------------------------------------------------------- #
# One question per command, one template for 1 and N commands (D14, D33)
# --------------------------------------------------------------------------- #

def test_signature_surface_same_template_for_n_commands():
    from gaia.approvals import surface

    first, second = "git push origin feat/x", "gh pr create --base main --fill"
    payload = _seal(
        [
            {"command": first, "does": "Sube la rama al remoto.", "impact": "La rama queda publicada."},
            {"command": second, "does": "Abre el PR contra main.", "impact": "Queda un PR abierto."},
        ],
        what="Publicar la rama y abrir su PR.", question="¿Publico la rama?", rollback=None,
    )

    rendered = surface.render(payload, APPROVAL_ID)

    assert [q["question"] for q in rendered.questions] == [_asks(first), _asks(second)]
    assert [q["question"] for q in rendered.details_questions] == [
        f"[GAIA-SECURITY] [ DETAILS ] [ gaia-system ] [ COMMAND: {first} ] "
        "[ DOES: Sube la rama al remoto. ] [ IMPACT: La rama queda publicada. ] "
        "[ ROLLBACK: no declarado; no supongas que se puede deshacer ]",
        f"[GAIA-SECURITY] [ DETAILS ] [ gaia-system ] [ COMMAND: {second} ] "
        "[ DOES: Abre el PR contra main. ] [ IMPACT: Queda un PR abierto. ] "
        "[ ROLLBACK: no declarado; no supongas que se puede deshacer ]",
    ]
    assert [q["header"] for q in rendered.questions] == ["Firma 1/2", "Firma 2/2"]
    assert rendered.text.splitlines() == [q["question"] for q in rendered.questions]


def test_signature_surface_undeclared_phrases_are_stated_not_invented():
    """A reactive block seals no per-command phrases; its Details says so."""
    from gaia.approvals import surface
    from modules.tools.bash_validator import _build_sealed_payload

    payload = _build_sealed_payload(
        "git push origin main", "push", "MUTATIVE", agent_type=AGENT, cwd=REPO, session_id=SESSION,
    )

    rendered = surface.render(payload, APPROVAL_ID)

    assert "[ DOES: (sin descripción declarada) ]" in rendered.details
    assert "[ IMPACT: no declarado ]" in rendered.details


def test_signature_surface_a_relative_path_brings_its_folder_into_details():
    """The folder shows only when a command reads a relative path or runs outside the requester's (D37)."""
    from gaia.approvals import surface

    payload = _seal(
        [{"command": "mv muestra.txt movido.txt", "does": "Renombra.", "impact": "Cambia el nombre."},
         {"command": "git push origin feature/demo-login", "does": "Sube.", "impact": "Publica."}],
        requested_from=REPO,
    )

    relative, branch = (q["question"] for q in surface.render(payload, APPROVAL_ID).details_questions)

    assert relative.endswith(f"[ CWD: {REPO} ]")
    assert "[ CWD:" not in branch


# --------------------------------------------------------------------------- #
# Batches of up to 4 questions (D14, PD4, D38)
# --------------------------------------------------------------------------- #

def _batch_entry(index, commands=1):
    payload = _seal(
        [{"command": f"git push origin feat/{index}-{n}"} for n in range(commands)],
        what=f"Publicar la rama {index}.", question=f"¿Publico la rama {index}?",
    )
    return payload, "P-" + f"{index:x}" * 32


def test_signature_surface_batch_of_four_keeps_each_signature_apart():
    from gaia.approvals import surface

    entries = [_batch_entry(index) for index in range(1, 5)]

    batch = surface.render_batch(entries)

    assert [item.approval_id for item in batch] == [approval_id for _, approval_id in entries]
    assert [item.questions[0]["question"] for item in batch] == [
        _asks(f"git push origin feat/{index}-0") for index in range(1, 5)
    ]


@pytest.mark.parametrize(
    "shape, headers, details_headers",
    [
        ([1], ["Firma 1/1"], ["Detalle 1/1"]),
        ([4], ["Firma 1/4", "Firma 2/4", "Firma 3/4", "Firma 4/4"],
         ["Detalle 1/4", "Detalle 2/4", "Detalle 3/4", "Detalle 4/4"]),
        ([1, 2], ["Firma A 1/1", "Firma B 1/2", "Firma B 2/2"],
         ["Detalle A1/1", "Detalle B1/2", "Detalle B2/2"]),
        ([1, 1, 1, 1], ["Firma A 1/1", "Firma B 1/1", "Firma C 1/1", "Firma D 1/1"],
         ["Detalle A1/1", "Detalle B1/1", "Detalle C1/1", "Detalle D1/1"]),
    ],
)
def test_signature_surface_batch_header_names_each_position(shape, headers, details_headers):
    """One signature counts the call; a call mixing signatures letters each one (D38)."""
    from gaia.approvals import surface

    batch = surface.render_batch(
        [_batch_entry(index, commands) for index, commands in enumerate(shape, start=1)]
    )

    assert [q["header"] for item in batch for q in item.questions] == headers
    assert [q["header"] for item in batch for q in item.details_questions] == details_headers
    for item in batch:
        for question in (*item.questions, *item.details_questions):
            assert len(question["header"]) <= surface.HEADER_MAX
            assert surface.is_signature_question(question)


@pytest.mark.parametrize("count", [0, 5])
def test_signature_surface_batch_rejects_outside_one_to_four(count):
    from gaia.approvals import surface

    with pytest.raises(surface.SurfaceLimitError, match="1 to 4"):
        surface.render_batch([_batch_entry(index) for index in range(1, count + 1)])


def test_signature_surface_batch_rejects_repeated_question_text():
    """The host indexes each answer by its question text, so one call never asks one command twice."""
    from gaia.approvals import surface

    same = _seal([{"command": "git push origin main"}], what="Publicar.", question="¿Publico?")

    with pytest.raises(surface.SurfaceLimitError, match="read the same"):
        surface.render_batch([(same, "P-" + "1" * 32), (same, "P-" + "2" * 32)])


# --------------------------------------------------------------------------- #
# Limits, enforced when the request is made (PD5)
# --------------------------------------------------------------------------- #

def _seal_with(field, value):
    item = {"command": "git push origin main", "does": "Sube la rama.", "impact": "Publica la rama."}
    phrases = {"what": "Publicar la rama.", "question": "¿Publico la rama?"}
    if field in item:
        item[field] = value
    else:
        phrases[field] = value
    return _seal([item], **phrases)


@pytest.mark.parametrize(
    "field, limit",
    [("what", 120), ("question", 60), ("does", 100), ("impact", 100)],
)
def test_signature_surface_request_rejects_each_phrase_over_its_limit(field, limit):
    from gaia.approvals import surface

    _seal_with(field, "x" * limit)
    with pytest.raises(surface.SurfaceLimitError, match=f"{limit}"):
        _seal_with(field, "x" * (limit + 1))


@pytest.mark.parametrize("field", ["what", "question", "does", "impact"])
def test_signature_surface_request_rejects_a_phrase_on_two_lines(field):
    from gaia.approvals import surface

    with pytest.raises(surface.SurfaceLimitError, match="one line"):
        _seal_with(field, "primera linea\nsegunda linea")


def test_signature_surface_protected_write_on_a_deep_path_still_seals(db):
    """Gaia's own default title names the file, so a long path never trips the title limit."""
    from gaia.approvals import core
    from gaia.approvals.store import get_by_id
    from modules.security.approval_grants import generate_nonce, write_pending_approval_for_file

    deep = "/" + "/".join(["directorio-profundo"] * 8) + "/.claude/settings.json"
    title = "Modificar el archivo protegido settings.json."

    requested = core.protected_write_verdict(deep, session_id=SESSION, agent_id=AGENT)["approval_id"]
    nonce = generate_nonce()
    assert write_pending_approval_for_file(nonce=nonce, file_path=deep, session_id=SESSION) is not None

    for approval_id in (requested, f"P-{nonce}"):
        assert json.loads(get_by_id(approval_id)["payload_json"])["what"] == title


def _question(**overrides):
    from gaia.approvals import surface

    question = surface.render(_d12_payload(), APPROVAL_ID).questions[0]
    question = {**question, "options": [dict(option) for option in question["options"]]}
    for key, value in overrides.items():
        if key == "description":
            question["options"][0]["description"] = value
        else:
            question[key] = value
    return question


def test_signature_surface_question_within_host_limits_passes():
    from gaia.approvals import surface

    surface.check_question(_question(header="x" * 12, description=" ".join(["palabra"] * 8)))


@pytest.mark.parametrize(
    "override, match",
    [
        ({"header": "x" * 13}, "header.*12"),
        ({"description": " ".join(["palabra"] * 9)}, "8 words"),
    ],
)
def test_signature_surface_question_rejects_each_host_limit(override, match):
    from gaia.approvals import surface

    with pytest.raises(surface.SurfaceLimitError, match=match):
        surface.check_question(_question(**override))


# --------------------------------------------------------------------------- #
# CLI: rejected at request, rendered at presentation
# --------------------------------------------------------------------------- #

def _request_set_args(**overrides):
    values = dict(
        command=[D12_COMMAND], cwd=[REPO], expect_exit=None, what=D12_TITLE,
        question=D12_QUESTION, does=[D12_DOES], impact=[D12_IMPACT],
        rationale=None, verification=None, rollback=D12_ROLLBACK,
        agent_id=AGENT, session_id=SESSION, json=True,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _run(handler, args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = handler(args)
    return code, out.getvalue()


def test_signature_surface_request_set_rejects_before_persisting(db):
    from bin.cli.approvals import cmd_request_set

    code, _ = _run(cmd_request_set, _request_set_args(question="x" * 61))

    assert code == 1
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        con.close()


def test_signature_surface_request_set_without_rollback_is_refused_with_what_to_write(db):
    """D38: a request names how to undo it, or says plainly it cannot be undone."""
    from bin.cli.approvals import cmd_request_set

    code, out = _run(cmd_request_set, _request_set_args(rollback=None))

    assert code == 1
    assert "--rollback is required" in out, out
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    finally:
        con.close()


def test_signature_surface_cli_presents_the_d12_surface(db, tmp_path, monkeypatch):
    from bin.cli.approvals import cmd_request_set, cmd_show_v2
    from gaia.approvals import surface

    # Requested from the folder it runs in: the D12 surface names no folder.
    monkeypatch.chdir(tmp_path)
    code, out = _run(cmd_request_set, _request_set_args(cwd=[str(tmp_path)]))
    assert code == 0, out
    approval_id = json.loads(out)["approval_id"]

    code, out = _run(
        cmd_show_v2,
        argparse.Namespace(approval_id=approval_id, json=False, consent_surface=True),
    )
    assert code == 0, out
    shown = json.loads(out)

    expected = surface.render(_d12_payload(), approval_id)
    assert shown == {
        "approval_id": approval_id,
        "text": expected.text,
        "questions": list(expected.questions),
        "details": expected.details,
        "details_questions": list(expected.details_questions),
    }
