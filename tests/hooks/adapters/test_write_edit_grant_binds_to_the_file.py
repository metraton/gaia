"""A write grant must bind to the FILE, and show the user the file it binds to.

The defect: ``_adapt_write_edit`` took ``file_path`` from the tool parameters
and handed it UNRESOLVED to every consumer that keys consent -- the grant
lookup, the pending lookup, the pending write and the consent surface -- while
the protection predicate beside them resolves. Protection was resolved and
wide; permission was raw and narrow, so one file reached through two spellings
was two different permissions.

These tests pin the three halves of the remedy that only hold together: the
consent surface NAMES the resolved file, the pending (and so the grant minted
from it) is KEYED to that same file, and a symlink retargeted afterwards
resolves elsewhere, fails to match, and is blocked again under a new request.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[3] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

SESSION = "sess-write-edit-resolve"
AGENT_ID = "a" + "c" * 16


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    yield


def _tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two protected files and one symlinked directory pointing at the first.

    ``settings.json`` under a ``.claude`` component is protected by shape, so
    the protection verdict does not depend on a registered workspace and the
    test measures the GRANT, which is what drifted.
    """
    first = tmp_path / "install" / ".claude" / "settings.json"
    first.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")

    second = tmp_path / "elsewhere" / ".claude" / "settings.json"
    second.parent.mkdir(parents=True)
    second.write_text("{}", encoding="utf-8")

    link_root = tmp_path / "current"
    link_root.symlink_to(tmp_path / "install", target_is_directory=True)
    return first, second, link_root / ".claude" / "settings.json"


def _attempt(path: Path):
    adapter = ClaudeCodeAdapter()
    return adapter._adapt_write_edit(
        "Edit",
        {"file_path": str(path)},
        session_id=SESSION,
        is_subagent=True,
        agent_id=AGENT_ID,
    )


def _reason(response) -> str:
    hook_output = response.output.get("hookSpecificOutput", {})
    return hook_output.get("permissionDecisionReason", "")


def test_the_consent_surface_names_the_file_the_grant_will_bind_to(tmp_path):
    real, _second, through_link = _tree(tmp_path)

    reason = _reason(_attempt(through_link))

    assert f"File: {real}" in reason, (
        "a permission that binds to one file while showing another is worse "
        f"than the raw defect; reason was:\n{reason}"
    )


def test_the_pending_is_keyed_to_the_resolved_file_not_to_the_spelling(tmp_path):
    from modules.security.approval_grants import find_pending_for_file

    real, _second, through_link = _tree(tmp_path)
    _attempt(through_link)

    assert find_pending_for_file(SESSION, str(real)), (
        "the pending -- and the grant minted from it -- must be keyed to the "
        "file, so the same file reached by another spelling is one permission"
    )
    assert not find_pending_for_file(SESSION, str(through_link)), (
        "keying the pending to the unresolved spelling is the defect itself"
    )


def test_a_retargeted_symlink_is_blocked_again_under_a_new_request(tmp_path):
    from modules.security.approval_grants import find_pending_for_file

    _real, second, through_link = _tree(tmp_path)
    first_attempt = _reason(_attempt(through_link))

    link_root = tmp_path / "current"
    link_root.unlink()
    link_root.symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    second_attempt = _reason(_attempt(through_link))

    assert f"File: {second}" in second_attempt, (
        "resolving at mint does not freeze the symlink: the retry must resolve "
        "again and consent to where the path lands NOW"
    )
    assert first_attempt != second_attempt, (
        "a retargeted link is a different file and must not ride the request "
        "raised for the old one"
    )
    assert find_pending_for_file(SESSION, str(second))


def test_the_persistence_fallback_consents_against_the_resolved_file(
    tmp_path, monkeypatch
):
    """The fallback branch names the same object as the three consumers above.

    When the pending cannot be persisted the adapter drops the Gaia approval
    id and asks the host inline instead. That request is built from its own
    local, and it kept the raw spelling while everything around it moved to
    the resolved one -- one branch consenting against the link while the rest
    bind to the file.

    Asserted on the ``ConsentRequest`` rather than on the rendered response
    because the native-ask shape renders ``reason`` alone; ``operation`` is
    the field this branch carries the path in, and the host is its reader.
    """
    real, _second, through_link = _tree(tmp_path)

    from modules.security import approval_grants

    monkeypatch.setattr(
        approval_grants,
        "write_pending_approval_for_file",
        lambda **_kwargs: None,
    )

    adapter = ClaudeCodeAdapter()
    seen = {}
    original_request_consent = adapter.request_consent

    def _capture(request):
        seen["operation"] = request.operation
        return original_request_consent(request)

    monkeypatch.setattr(adapter, "request_consent", _capture)
    adapter._adapt_write_edit(
        "Edit",
        {"file_path": str(through_link)},
        session_id=SESSION,
        is_subagent=True,
        agent_id=AGENT_ID,
    )

    assert seen.get("operation") == str(real), (
        "the fallback must consent against the file the write lands on, not "
        "the spelling that reached it"
    )


def test_the_resolver_keeps_the_form_the_symlink_actually_lands_on(tmp_path):
    from modules.security.protected_paths import resolved_write_target

    real, _second, through_link = _tree(tmp_path)

    assert resolved_write_target(str(through_link)) == str(real)
    assert resolved_write_target("") == ""
