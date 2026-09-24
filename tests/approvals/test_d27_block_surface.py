"""The renderer prints D27's literal example: one block, a short question, Details in the block format (plan 76, task 16).

D27/D29 fix the visible format in both hosts: a block of heading, short
description, a blank line and each command exact on one line, under
``en <carpeta>`` only when it runs outside the requester's folder, with no
validity; a question of at most 60 characters with Approve / Reject / Details;
and Details in the same block format, without the exact command, carrying what
each command does, its impact, the rollback, 30 minutes, the ID and the print.
"""

from __future__ import annotations

from gaia.approvals import surface
from gaia.approvals.command_set import command_fingerprint

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

D27_TEXT = (
    "Solicitud de aprobación · developer\n"
    "Publicar la rama de prueba y abrir su pull request.\n"
    "\n"
    "1  en /home/jorge/ws/me/demo-repo\n"
    "   git push origin feature/demo-login\n"
    "\n"
    "2  gh pr create --repo metraton/demo --base main --head feature/demo-login "
    '--title "Login de prueba" --body-file /home/jorge/.gaia/scratch/demo-1234/pr.md'
)


def test_d27_block_surface_text_is_the_literal_example():
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert rendered.text == D27_TEXT
    assert rendered.block == f"```\n{D27_TEXT}\n```"
    assert "Vale" not in rendered.text and "30 min" not in rendered.text


def test_d27_block_surface_question_is_short_with_the_three_options():
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert rendered.question == {
        "question": "¿Publico la rama y abro el PR?",
        "header": "Aprobación",
        "options": [
            {"label": "Approve", "description": "Autoriza exactamente lo que se muestra arriba"},
            {"label": "Reject", "description": "Rechaza la solicitud; no se ejecuta nada"},
            {"label": "Details", "description": "Ver qué hace cada paso y su impacto"},
        ],
        "multiSelect": False,
    }
    assert len(rendered.question["question"]) <= surface.QUESTION_MAX
    assert rendered.details_question == {**rendered.question, "header": "Detalles"}


def test_d27_block_surface_details_uses_the_block_format_without_the_command():
    rendered = surface.render(PAYLOAD, APPROVAL_ID)

    assert rendered.details == (
        "Solicitud de aprobación · developer\n"
        "Publicar la rama de prueba y abrir su pull request.\n"
        "\n"
        "1  git push: sube la rama de prueba al remoto.\n"
        "   Impacto: La rama queda visible para todo el equipo.\n"
        "\n"
        "2  gh pr create: abre el pull request de la rama.\n"
        "   Impacto: El equipo recibe un aviso del PR nuevo.\n"
        "\n"
        "Rollback: borrar la rama remota y cerrar el PR.\n"
        f"Vale 30 min · ID {APPROVAL_ID} · huellas 1 {command_fingerprint(PUSH)}"
        f" · 2 {command_fingerprint(PR)}"
    )
    assert PUSH not in rendered.details and PR not in rendered.details
    assert rendered.details_block == f"```\n{rendered.details}\n```"


def test_d27_block_surface_command_in_the_requester_folder_shares_the_number_line():
    rendered = surface.render({**PAYLOAD, "items": PAYLOAD["items"][1:]}, APPROVAL_ID)

    assert rendered.text.splitlines()[2:] == ["", f"1  {PR}"]


def test_d27_block_surface_batch_headers_tell_the_details_re_ask_apart():
    question, details = surface.batch_questions(PAYLOAD, APPROVAL_ID, 2, 4)

    assert (question["header"], details["header"]) == ("Aprob. 2/4", "Detalle 2/4")
    assert all(len(h) <= surface.HEADER_MAX for h in (question["header"], details["header"]))
    assert surface.is_signature_question(details)
