"""The renderer asks D27's example in the D37 machine format (plan 76, task 16; D37 replaces D27/D29).

D27's literal example -- a push run in a sub-folder and a PR created from the
project -- stays the fixture; since D37 its surface is one one-line question
per command, identical in both hosts: the signature question names the agent
and the exact command, and Details adds what it does, its impact, the rollback
and, only when it matters, the folder. No block, no validity, no approval id.
"""

from __future__ import annotations

import json

from gaia.approvals import surface

APPROVAL_ID = "P-0123abcd"
PROJECT = "/home/jorge/ws/me"
PUSH = "git push origin feature/demo-login"
PR = (
    'gh pr create --repo metraton/demo --base main --head feature/demo-login '
    '--title "Login de prueba" --body-file /home/jorge/.gaia/scratch/demo-1234/pr.md'
)
PAYLOAD = {
    "requested_by": {"agent_id": "developer"},
    "requested_from": PROJECT,
    "what": "Publicar la rama de prueba y abrir su pull request.",
    "question": "¿Publico la rama y abro el PR?",
    "rollback_hint": "borrar la rama remota y cerrar el PR.",
    "items": [
        {"command": PUSH, "cwd": f"{PROJECT}/demo-repo",
         "does": "git push: sube la rama de prueba al remoto.",
         "impact": "La rama queda visible para todo el equipo."},
        {"command": PR, "cwd": PROJECT,
         "does": "gh pr create: abre el pull request de la rama.",
         "impact": "El equipo recibe un aviso del PR nuevo."},
    ],
}


def test_d27_block_surface_text_is_the_literal_example():
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert [q["question"] for q in rendered.questions] == [
        f"[GAIA-SECURITY] [ AGENT-REQUEST ] [ developer ] [ COMMAND ] [ {PUSH} ]",
        f"[GAIA-SECURITY] [ AGENT-REQUEST ] [ developer ] [ COMMAND ] [ {PR} ]",
    ]
    assert "Solicitud de aprobación" not in rendered.text and "```" not in rendered.text


def test_d27_block_surface_question_is_short_with_the_three_options():
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert [q["header"] for q in rendered.questions] == ["Firma 1/2", "Firma 2/2"]
    for question in (*rendered.questions, *rendered.details_questions):
        assert [option["label"] for option in question["options"]] == ["Approve", "Reject", "Details"]
        assert question["multiSelect"] is False
        surface.check_question(question)


def test_d27_block_surface_details_uses_the_block_format_without_the_command():
    """Details repeats the exact command (approving from Details is allowed, D37) and never the ID."""
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert [q["question"] for q in rendered.details_questions] == [
        f"[GAIA-SECURITY] [ DETAILS ] [ developer ] [ COMMAND: {PUSH} ] "
        "[ DOES: git push: sube la rama de prueba al remoto. ] "
        "[ IMPACT: La rama queda visible para todo el equipo. ] "
        "[ ROLLBACK: borrar la rama remota y cerrar el PR. ] "
        f"[ CWD: {PROJECT}/demo-repo ]",
        f"[GAIA-SECURITY] [ DETAILS ] [ developer ] [ COMMAND: {PR} ] "
        "[ DOES: gh pr create: abre el pull request de la rama. ] "
        "[ IMPACT: El equipo recibe un aviso del PR nuevo. ] "
        "[ ROLLBACK: borrar la rama remota y cerrar el PR. ]",
    ]
    shown = json.dumps([rendered.questions, rendered.details_questions], ensure_ascii=False)
    assert APPROVAL_ID not in shown and "30 min" not in shown


def test_d27_block_surface_command_in_the_requester_folder_shares_the_number_line():
    """A command run in the requester's own folder, reading no relative path, names no folder."""
    rendered = surface.render({**PAYLOAD, "items": PAYLOAD["items"][1:]}, APPROVAL_ID)

    assert "[ CWD:" not in rendered.details
    assert [q["header"] for q in rendered.questions] == ["Firma 1/1"]


def test_d27_block_surface_batch_headers_tell_the_details_re_ask_apart():
    other = {**PAYLOAD, "items": [{**PAYLOAD["items"][1], "command": "gh pr merge 7 --squash"}]}

    first, second = surface.render_batch([(PAYLOAD, APPROVAL_ID), (other, "P-4567cdef")])

    assert [q["header"] for q in (*first.questions, *second.questions)] == [
        "Firma A 1/2", "Firma A 2/2", "Firma B 1/1",
    ]
    assert [q["header"] for q in (*first.details_questions, *second.details_questions)] == [
        "Detalle A1/2", "Detalle A2/2", "Detalle B1/1",
    ]
    assert all(surface.is_signature_question(q) for q in second.details_questions)
