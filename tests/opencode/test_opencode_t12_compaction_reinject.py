"""Gate 1015 (task 540, T12, plan 65): synthetic reinjection over the REAL
compaction-hook shape, hardened AC-7 (auditoria 15857, enmienda E3).

VALIDEZ CONDICIONADA per the gate's own text: this synthetic scenario does
not close AC-7 alone -- it requires the live probe (gate 1023, evidenced in
contract aa78226d7de9837d7.e3501e70a3c4 / handoff 16251: liveness line +
identity.attest ledger row cited literal, three forcing attempts, disparo
not observed, escalated NEEDS_INPUT; the user's option-B decision accepts
static existence as sufficient and defers live disparo). This file exercises
only the LOGIC: given a claimed dispatch row bound to a session, and the
EXACT {sessionID} / {context, prompt} shapes the installed OpenCode 1.18.23
binary was decompiled to call "experimental.session.compacting" with, the
child's contract kernel (T9's build_dispatch_kernel, unmodified) reaches
output.context, and the row's claimed_at is untouched by the whole path.

Driven through the REAL, already-landed plugin.ts hook (opencode/plugin.ts,
unprotected -- edited this task) under bun, with a stub gaiaBridge standing
in for the sealed hooks/adapters/opencode.py::adapt_pre_compact (protected,
pending approval -- see this task's contract for the design). The stub
returns exactly the updated_input.context shape that design produces; this
test proves the plugin's OWN in-place-mutation logic against it, and that
nothing in the path touches the DB row.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from modules.context.kernel_builder import build_dispatch_kernel
from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    find_dispatch_row_by_harness_agent_id,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"
DRIVER = _ROOT / "tests" / "opencode" / "t12_compaction_reinject_driver.ts"
SESSION_ID = "ses-child-t12-1"


@pytest.fixture(autouse=True)
def _isolated_gaia_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path


def _db(tmp_path) -> Path:
    return tmp_path / "gaia.db"


def _birth_claim_bind(db, token: str, harness_agent_id: str) -> str:
    """Mirrors T9's Task-dispatch claim + T10's message.part.updated bind --
    the exact state find_dispatch_row_by_harness_agent_id resolves a
    compaction event's session against."""
    agent_id = valid_agent_id(f"a{token}")
    result = insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE,
        session_id=None, db_path=db,
        agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id=f"call-{token}",
    )
    contract_id = result["contract_id"]
    claim_dispatch_row(dispatch_tool_use_id=f"call-{token}", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=f"call-{token}", harness_agent_id=harness_agent_id,
        db_path=db,
    )
    return contract_id


def _drive(kernel: str) -> dict:
    result = subprocess.run(
        ["bun", str(DRIVER)],
        capture_output=True, text=True, timeout=60,
        env={
            **__import__("os").environ,
            "GAIA_T12_SESSION_ID": SESSION_ID,
            "GAIA_T12_KERNEL": kernel,
        },
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_post_compact_context_carries_the_real_dispatch_kernel(tmp_path):
    db = _db(tmp_path)
    contract_id = _birth_claim_bind(db, "t12one", SESSION_ID)

    row = find_dispatch_row_by_harness_agent_id(SESSION_ID, db_path=db)
    assert row is not None
    assert row["contract_id"] == contract_id
    claimed_at_before = row["claimed_at"]
    assert claimed_at_before

    kernel = build_dispatch_kernel(row)
    assert kernel
    assert contract_id in kernel

    driven = _drive(kernel)

    assert driven["output"]["context"] == [kernel]
    # JSON.stringify drops an `undefined` value entirely -- "prompt" absent
    # from the driver's JSON output IS the untouched-prompt assertion.
    assert "prompt" not in driven["output"]

    request = driven["requests"][0]
    assert request["event"] == "session.compacting"
    assert request["sessionID"] == SESSION_ID


def test_claimed_at_stays_intact_across_the_reinjection_path(tmp_path):
    db = _db(tmp_path)
    contract_id = _birth_claim_bind(db, "t12two", SESSION_ID)

    row_before = find_dispatch_row_by_harness_agent_id(SESSION_ID, db_path=db)
    claimed_at_before = row_before["claimed_at"]
    kernel = build_dispatch_kernel(row_before)

    _drive(kernel)

    row_after = find_dispatch_row_by_harness_agent_id(SESSION_ID, db_path=db)
    assert row_after["contract_id"] == contract_id
    assert row_after["claimed_at"] == claimed_at_before


def test_no_bound_row_leaves_context_untouched():
    """A session with nothing claimed against it (the primary session's own
    compaction, or a stale/unbound sessionID) must not fabricate a kernel --
    _adapt_task_with_kernel's own degrade-to-plain rule, mirrored here."""
    driven_result = subprocess.run(
        ["bun", str(DRIVER)],
        capture_output=True, text=True, timeout=60,
        env={
            **__import__("os").environ,
            "GAIA_T12_SESSION_ID": "ses-unbound",
            "GAIA_T12_KERNEL": "",
        },
    )
    assert driven_result.returncode == 0, driven_result.stderr
    driven = json.loads(driven_result.stdout)
    assert driven["output"]["context"] == []
