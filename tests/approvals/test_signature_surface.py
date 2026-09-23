"""The one signature-surface renderer (plan 76, task 2; brief decisions D12, D14, D17).

Gaia composes the visible text, the question and Details of every signature
from the phrases a requester seals; hosts only show them. The D12 example,
read with the full orthography D17 requires, is the golden test. Length limits are enforced when the request is made, so an
over-long phrase never reaches a presentation.
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


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _seal(items, *, what=D12_TITLE, question=D12_QUESTION, rollback=D12_ROLLBACK):
    from gaia.approvals import core

    return core.seal_request(
        "command_set",
        [{"cwd": REPO, **item} for item in items],
        what=what, question=question, rollback=rollback,
        session_id=SESSION, agent_id=AGENT,
    )


def _d12_payload():
    return _seal([{"command": D12_COMMAND, "does": D12_DOES, "impact": D12_IMPACT}])


def _fingerprint(command):
    from gaia.approvals.command_set import command_fingerprint

    return command_fingerprint(command)


# --------------------------------------------------------------------------- #
# Golden: the D12 example, literally
# --------------------------------------------------------------------------- #

def test_signature_surface_golden_d12_example():
    from gaia.approvals import surface

    rendered = surface.render(_d12_payload(), APPROVAL_ID)

    assert rendered.text == "\n".join([
        "Solicitud de aprobación · gaia-system",
        "Reinstalar Gaia en tu espacio de trabajo y actualizar su base de datos.",
        "",
        "Comandos (1)",
        "  1  python3 .../0ac7481a.../bin/gaia dev \\",
        "         --workspace /home/jorge/ws/me \\",
        "         --ref bbc2f09 \\",
        "         --host all",
    ])
    assert rendered.question == {
        "question": "¿Reinstalo Gaia?",
        "header": "Aprobación",
        "options": [
            {"label": "Approve", "description": "Autoriza exactamente lo que se muestra arriba"},
            {"label": "Reject", "description": "Rechaza la solicitud; no se ejecuta nada"},
            {"label": "Details", "description": "Ver qué hace cada paso y su impacto"},
        ],
        "multiSelect": False,
    }
    assert rendered.details == "\n".join([
        "1  gaia dev: instala en tu espacio de trabajo la versión nueva de main.",
        "   Impacto: actualiza tu base de datos; ese cambio no se deshace.",
        "Rollback: volver al código anterior con --ref c1d8b89; la base queda actualizada.",
        "Comando exacto:",
        f"  1  {D12_COMMAND}",
        f"Vale 30 min · ID {APPROVAL_ID} · huella {_fingerprint(D12_COMMAND)}",
    ])


def test_signature_surface_options_carry_no_approval_id():
    from gaia.approvals import surface

    rendered = surface.render(_d12_payload(), APPROVAL_ID)

    assert [option["label"] for option in rendered.question["options"]] == [
        "Approve", "Reject", "Details",
    ]
    assert APPROVAL_ID not in json.dumps(rendered.question)
    assert APPROVAL_ID not in rendered.text


# --------------------------------------------------------------------------- #
# One template for 1 and N commands (D14)
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

    assert rendered.text == "\n".join([
        "Solicitud de aprobación · gaia-system",
        "Publicar la rama y abrir su PR.",
        "",
        "Comandos (2)",
        "  1  git push origin feat/x",
        "  2  gh pr create \\",
        "         --base main \\",
        "         --fill",
    ])
    assert rendered.details == "\n".join([
        "1  Sube la rama al remoto.",
        "   Impacto: La rama queda publicada.",
        "2  Abre el PR contra main.",
        "   Impacto: Queda un PR abierto.",
        "Rollback: no declarado; no supongas que se puede deshacer.",
        "Comandos exactos:",
        f"  1  {first}",
        f"  2  {second}",
        f"Vale 30 min · ID {APPROVAL_ID} · huellas 1 {_fingerprint(first)} · 2 {_fingerprint(second)}",
    ])


def test_signature_surface_undeclared_phrases_are_stated_not_invented():
    """A reactive block seals no question or per-command phrases; the surface says so."""
    from gaia.approvals import surface
    from modules.tools.bash_validator import _build_sealed_payload

    payload = _build_sealed_payload(
        "git push origin main", "push", "MUTATIVE", agent_type=AGENT, cwd=REPO, session_id=SESSION,
    )

    rendered = surface.render(payload, APPROVAL_ID)

    assert rendered.question["question"] == "¿Apruebo esta solicitud?"
    assert "1  (sin descripción declarada)" in rendered.details
    assert "   Impacto: no declarado." in rendered.details


# --------------------------------------------------------------------------- #
# Batches of up to 4 signatures with distinct questions (D14, PD4)
# --------------------------------------------------------------------------- #

def _batch_entry(index, question=None):
    payload = _seal(
        [{"command": f"git push origin feat/{index}"}],
        what=f"Publicar la rama {index}.", question=question or f"¿Publico la rama {index}?",
    )
    return payload, "P-" + f"{index:x}" * 32


def test_signature_surface_batch_of_four_keeps_each_signature_apart():
    from gaia.approvals import surface

    entries = [_batch_entry(index) for index in range(1, 5)]

    batch = surface.render_batch(entries)

    assert [item.approval_id for item in batch] == [approval_id for _, approval_id in entries]
    assert [item.question["question"] for item in batch] == [
        f"¿Publico la rama {index}?" for index in range(1, 5)
    ]


@pytest.mark.parametrize("count", [0, 5])
def test_signature_surface_batch_rejects_outside_one_to_four(count):
    from gaia.approvals import surface

    with pytest.raises(surface.SurfaceLimitError, match="1 to 4"):
        surface.render_batch([_batch_entry(index) for index in range(1, count + 1)])


def test_signature_surface_batch_rejects_repeated_question_text():
    from gaia.approvals import surface

    entries = [_batch_entry(1, "¿Publico la rama?"), _batch_entry(2, "¿Publico la rama?")]

    with pytest.raises(surface.SurfaceLimitError, match="distinct"):
        surface.render_batch(entries)


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


def _question(**overrides):
    from gaia.approvals import surface

    question = surface.render(_d12_payload(), APPROVAL_ID).question
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
        ({"question": "x" * 61}, "question.*60"),
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


def test_signature_surface_cli_presents_the_d12_surface(db):
    from bin.cli.approvals import cmd_request_set, cmd_show_v2
    from gaia.approvals import surface

    code, out = _run(cmd_request_set, _request_set_args())
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
        "question": expected.question,
        "details": expected.details,
    }
