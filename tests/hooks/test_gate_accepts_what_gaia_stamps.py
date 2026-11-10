"""The stop gate never rejects a row for a field Gaia wrote, and finalize refuses a clause the close would drop."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.contract.validator import TOP_LEVEL_ENVELOPE_KEYS, validate_form  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402

GAIA = _REPO_ROOT / "bin" / "gaia"
SESSION_ID = "sess-gaia-stamps"
HARNESS_ID = "a7777777777777777"
AGENT = "a" + "6" * 16
CONTRACT_ID = f"{AGENT}.0000000000dd"
MALFORMED = [{"section": "stack", "payload": {"k": 1}}]
STAMP_SOURCES = ("gaia", "hooks", "bin")
_STAMP = re.compile(r"""\b(?:envelope|capture_notes)\[["']([a-z_]+)["']\]\s*=[^=]""")


def _envelope(**extra) -> dict:
    lists = ("patterns_checked", "files_checked", "commands_run", "key_outputs",
             "verbatim_outputs", "cross_layer_impacts", "open_gaps")
    return {
        "agent_status": {"agent_state": "COMPLETE", "agent_id": AGENT,
                         "pending_steps": [], "next_action": "done"},
        "evidence_report": dict({key: [] for key in lists},
                                verification={"type": "none", "result": "pass", "details": "none"}),
        "consolidation_report": None,
        "approval_request": None,
        "conclusion": "closed",
        **extra,
    }


@pytest.fixture()
def substrate(tmp_path, monkeypatch):
    data = tmp_path / "data"
    env = {k: v for k, v in os.environ.items()
           if k not in ("GAIA_DISPATCH_AGENT", "GAIA_WORKSPACE", "GAIA_SESSION_ID")}
    env.update(GAIA_DATA_DIR=str(data), GAIA_DB=str(data / "gaia.db"), HOME=str(tmp_path / "home"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr(ClaudeCodeAdapter, "_get_gaia_agent_names", lambda self: {"gaia-system"})
    clear_path_cache()
    from gaia.store.writer import _connect, insert_dispatched_handoff, stamp_harness_agent_id

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.commit()
    finally:
        con.close()
    insert_dispatched_handoff(CONTRACT_ID, AGENT, "me", session_id=SESSION_ID, agent_name="gaia-system")
    stamp_harness_agent_id(CONTRACT_ID, HARNESS_ID)

    def run(verb: str, *rest: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(GAIA), "contract", verb, "--draft-id", CONTRACT_ID, *rest],
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)

    yield run
    clear_path_cache()


def _stop():
    adapter = ClaudeCodeAdapter()
    payload = {"hook_event_name": "SubagentStop", "session_id": SESSION_ID, "agent_id": HARNESS_ID,
               "agent_type": "gaia-system", "agent_transcript_path": "",
               "last_assistant_message": "COMPLETE", "stop_reason": "end_turn", "cwd": "/tmp"}
    return adapter.adapt_subagent_stop(adapter.parse_event(json.dumps(payload)))


def _row_envelope() -> dict:
    from gaia.store.writer import list_agent_contract_handoffs

    return json.loads(list_agent_contract_handoffs(contract_id=CONTRACT_ID, limit=1)[0]["raw_handoff_json"])


def test_a_row_the_close_stamped_with_its_refusals_passes_the_gate_again(substrate):
    from gaia.store.writer import finalize_agent_contract_handoff

    finalize_agent_contract_handoff(
        contract_id=CONTRACT_ID, agent_id=AGENT, workspace="me", agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_envelope(update_contracts=MALFORMED)), session_id=SESSION_ID)

    first, second = _stop(), _stop()

    assert _row_envelope().get("update_contracts_refused")
    assert (first.exit_code, second.exit_code) == (0, 0), second.output.get("contract_rejection_reason")


def test_finalize_refuses_a_clause_the_close_would_drop_while_the_draft_is_editable(substrate):
    from gaia.contract.drafts import save_draft

    save_draft(CONTRACT_ID, _envelope(update_contracts=MALFORMED))

    refused = substrate("finalize")

    message = refused.stdout + refused.stderr
    assert refused.returncode != 0, message
    assert '"contract"' in message and '"section"' in message and "gaia contract set update_contracts" in message
    assert "update_contracts_refused" not in _row_envelope()

    assert substrate("set", "update_contracts", '[{"contract":"stack","payload":{"k":1}}]').returncode == 0
    closed = substrate("finalize")

    assert closed.returncode == 0, closed.stdout + closed.stderr
    assert validate_form(_row_envelope()).ok


def test_every_key_gaia_stamps_into_an_envelope_is_one_the_gate_accepts():
    stamped = {}
    for source in STAMP_SOURCES:
        for path in (_REPO_ROOT / source).rglob("*.py"):
            for key in _STAMP.findall(path.read_text(encoding="utf-8")):
                stamped.setdefault(key, str(path.relative_to(_REPO_ROOT)))

    unknown = {key: where for key, where in stamped.items() if key not in TOP_LEVEL_ENVELOPE_KEYS}

    assert not unknown, f"stamped by Gaia but rejected by validate_form as UNKNOWN_FIELD: {unknown}"
