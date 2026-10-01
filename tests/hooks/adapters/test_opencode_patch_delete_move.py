"""An OpenCode patch cannot delete or move a file; the Bash command it names is judged instead.

``apply_patch`` reaches policy as an Edit of each path, and an Edit verdict
cannot see a deletion or a rename. The adapter therefore refuses the two
markers and names the ``rm``/``mv`` command that carries the same effect, so
the only route to it is the Bash tier: free inside Gaia scratch, a signature
anywhere else. Every path is a stand-in under a temporary HOME.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.opencode import OpenCodeAdapter  # noqa: E402

SESSION = "ses-patch-delete-move"
AGENT_ID = "a" + "6" * 16
AGENT_TYPE = "developer"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    (fake / "project").mkdir(parents=True)
    (fake / "project" / "kept.txt").write_text("kept\n", encoding="utf-8")
    data_dir = tmp_path / "gaia_data"
    (data_dir / "scratch").mkdir(parents=True)
    (data_dir / "scratch" / "probe.txt").write_text("probe\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)
    from modules.security import mutative_verbs, tiers

    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    return fake


def _opencode(tool: str, args: dict, call_id: str) -> dict:
    adapter = OpenCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": SESSION,
        "callID": call_id,
        "agentID": AGENT_ID,
        "roleContext": {
            "role": AGENT_TYPE,
            "issuer": "opencode-runtime",
            "attestation": f"{SESSION}:{AGENT_TYPE}",
            "verified": True,
        },
        "tool": tool,
        "args": args,
    }))
    output = adapter.adapt_pre_tool_use(event).output
    return {
        "decision": output.get("action"),
        "reason": output.get("reason", ""),
        "approval_id": output.get("approval_id"),
    }


def _patch(*body: str) -> dict:
    return {"patchText": "\n".join(["*** Begin Patch", *body, "*** End Patch"])}


def _refused_toward(verdict: dict, command: str) -> bool:
    return (
        verdict["decision"] == "deny"
        and not verdict["approval_id"]
        and f"`{command}`" in verdict["reason"]
    )


def _signature_request(verdict: dict) -> bool:
    return verdict["decision"] == "deny" and str(verdict["approval_id"] or "").startswith("P-")


def test_patch_delete_of_a_repo_file_is_refused_and_its_rm_asks_the_signature(home):
    target = str(home / "project" / "kept.txt")
    command = f"rm -- {target}"

    patched = _opencode("apply_patch", _patch(f"*** Delete File: {target}"), "call-del-repo")
    assert _refused_toward(patched, command), patched

    bash = _opencode("bash", {"command": command}, "call-rm-repo")
    assert _signature_request(bash), bash


def test_patch_delete_inside_scratch_is_refused_and_its_rm_runs_free(home):
    target = str(home.parent / "gaia_data" / "scratch" / "probe.txt")
    command = f"rm -- {target}"

    patched = _opencode("apply_patch", _patch(f"*** Delete File: {target}"), "call-del-scratch")
    assert _refused_toward(patched, command), patched

    bash = _opencode("bash", {"command": command}, "call-rm-scratch")
    assert bash["decision"] == "allow", bash


def test_patch_move_of_a_repo_file_is_refused_and_its_mv_asks_the_signature(home):
    source = str(home / "project" / "kept.txt")
    destination = str(home / "project" / "renamed.txt")
    command = f"mv -- {source} {destination}"

    patched = _opencode(
        "apply_patch",
        _patch(f"*** Update File: {source}", f"*** Move to: {destination}", "@@", "-kept", "+kept"),
        "call-move-repo",
    )
    assert _refused_toward(patched, command), patched

    bash = _opencode("bash", {"command": command}, "call-mv-repo")
    assert _signature_request(bash), bash


def test_patch_move_inside_scratch_is_refused_too(home):
    scratch = home.parent / "gaia_data" / "scratch"
    command = f"mv -- {scratch / 'probe.txt'} {scratch / 'moved.txt'}"

    patched = _opencode(
        "apply_patch",
        _patch(
            f"*** Update File: {scratch / 'probe.txt'}",
            f"*** Move to: {scratch / 'moved.txt'}",
            "@@", "-probe", "+probe",
        ),
        "call-move-scratch",
    )
    assert _refused_toward(patched, command), patched


def test_patch_add_and_update_keep_the_edit_verdict(home):
    target = str(home / "project" / "kept.txt")
    added = str(home / "project" / "added.txt")

    patched = _opencode(
        "apply_patch",
        _patch(f"*** Add File: {added}", "+new", f"*** Update File: {target}", "@@", "-kept", "+edited"),
        "call-add-update",
    )
    assert patched["decision"] == "allow", patched


def test_path_with_spaces_is_quoted_in_the_named_command(home):
    target = home / "project" / "two words.txt"
    target.write_text("x\n", encoding="utf-8")

    patched = _opencode("apply_patch", _patch(f"*** Delete File: {target}"), "call-del-spaces")
    assert _refused_toward(patched, f"rm -- '{target}'"), patched
