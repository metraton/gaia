"""A signed set whose earlier step reinstalls the program a later step runs, judged by BashValidator.validate()."""

from __future__ import annotations

import json
import os
import sqlite3

from tests.approvals.test_approval_live_fixes import (  # noqa: F401 -- host is the fixture below
    AGENT,
    SESSION,
    _approve,
    host,
)


def _install_copy(workspace, version):
    package = workspace / "node_modules" / "@jaguilar87" / "gaia"
    (package / "bin").mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": version}))
    launcher = package / "bin" / "gaia.new"
    launcher.write_text(f"#!/usr/bin/env python3\nVERSION = {version!r}\n")
    launcher.chmod(0o755)
    os.replace(launcher, package / "bin" / "gaia")
    link = workspace / "node_modules" / ".bin" / "gaia"
    link.parent.mkdir(exist_ok=True)
    if not os.path.lexists(link):
        link.symlink_to("../@jaguilar87/gaia/bin/gaia")


def _request(items):
    from gaia.approvals import core

    return core.request_command_set(
        [{**item, "does": "Actualiza Gaia.", "impact": "Cambia la copia instalada."} for item in items],
        what="Actualizar Gaia en el workspace.", question="¿Actualizo Gaia?", session_id=SESSION,
        agent_id=AGENT, rollback="Reinstalar la versión anterior.",
        verification="npm ls", shared_state="No: solo este workspace.",
    )


def _validate(command, tool_use_id, cwd):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id=SESSION, agent_type=AGENT,
        hook_payload={
            "cwd": cwd, "tool_use_id": tool_use_id, "session_id": SESSION, "agent_type": AGENT,
        },
    )


def _close(approval_id, command, tool_use_id):
    from gaia.approvals import core

    return core.close_call(
        approval_id, command=command, session_id=SESSION, tool_use_id=tool_use_id,
        exit_code=0, reserved=True, terminal_event="PostToolUse",
    )


def _resealed(approval_id):
    from gaia.approvals.store import get_history

    metadata = [json.loads(event["metadata_json"] or "{}") for event in get_history(approval_id)]
    return [
        {key: entry[key] for key in ("settled_index", "items")}
        for entry in metadata if entry.get("kind") == "files_resealed"
    ]


def _grant(db, approval_id):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM approval_grants WHERE approval_id = ?", (approval_id,),
        ).fetchone())
    finally:
        con.close()


def test_the_program_an_earlier_step_reinstalled_runs_under_the_same_signature(host, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _install_copy(workspace, "5.7.2-rc.2")
    install = f"npm --prefix {workspace} install @jaguilar87/gaia@5.8.0-rc.1 --save-exact"
    update = f"{workspace}/node_modules/.bin/gaia update --workspace {workspace} --channel opencode"
    approval_id = _request([
        {"command": install, "cwd": str(workspace)},
        {"command": update, "cwd": str(workspace)},
    ])
    _approve(approval_id)

    assert _validate(install, "toolu_install", str(workspace)).allowed
    _install_copy(workspace, "5.8.0-rc.1")
    assert _close(approval_id, install, "toolu_install") == "executed"

    result = _validate(update, "toolu_update", str(workspace))

    assert result.allowed, result.reason
    assert result.consumed_approval_id == approval_id
    assert _resealed(approval_id) == [{"settled_index": 0, "items": {"1": [f"{workspace}/node_modules/.bin/gaia"]}}]
    assert _close(approval_id, update, "toolu_update") == "executed"
    assert _grant(host["db"], approval_id)["status"] == "CONSUMED"


def test_a_script_changed_while_an_earlier_step_ran_stays_refused_and_is_named(host, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    script = workspace / "deploy.sh"
    script.write_text("kubectl apply -f app.yaml\n")
    push = "git push origin main"
    deploy = f"bash {script}"
    approval_id = _request([
        {"command": push, "cwd": str(workspace)},
        {"command": deploy, "cwd": str(workspace)},
    ])
    _approve(approval_id)

    assert _validate(push, "toolu_push", str(workspace)).allowed
    script.write_text("kubectl delete namespace production\n")
    assert _close(approval_id, push, "toolu_push") == "executed"
    result = _validate(deploy, "toolu_deploy", str(workspace))

    assert not result.allowed
    assert approval_id in result.reason
    assert f"{script} changed since it was signed" in result.reason
    assert _grant(host["db"], approval_id)["next_index"] == 1


def test_a_held_reservation_is_named_in_the_denial(host, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    command = "kubectl apply -f app.yaml"
    (workspace / "app.yaml").write_text("kind: Namespace\n")
    approval_id = _request([{"command": command, "cwd": str(workspace)}])
    _approve(approval_id)
    assert _validate(command, "toolu_first", str(workspace)).allowed

    result = _validate(command, "toolu_second", str(workspace))

    assert not result.allowed
    assert "call toolu_first holds item [0]" in result.reason
