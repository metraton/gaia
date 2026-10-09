"""A signature that force-removes a dirty worktree shows what was captured and where.

The user signs ``git worktree remove --force`` over a directory whose
uncommitted work ``gaia worktree release`` already deposited as a diff; the
Details of that signature must name the deposit and summarise it, and must say
so plainly when nothing was deposited.
"""

from __future__ import annotations

from types import SimpleNamespace

from gaia.approvals.surface import render

WORKTREE = "/ws/.project-worktrees/gaia/abc123"
APPROVAL_ID = "P-" + "ab" * 16
DIFF = (
    "diff --git a/notes.txt b/notes.txt\n"
    "--- a/notes.txt\n"
    "+++ b/notes.txt\n"
    "@@ -1 +1,2 @@\n"
    "-old\n"
    "+new\n"
    "+extra\n"
)


def _payload(command: str) -> dict:
    return {
        "requested_by": {"agent_id": "a1"},
        "items": [{"command": command, "does": "Removes the worktree.", "impact": "Its files are gone."}],
    }


def _details(command: str) -> str:
    return render(_payload(command), APPROVAL_ID).details


def _captured(monkeypatch, tmp_path, *, capture):
    blob = tmp_path / "capture.diff"
    blob.write_text(DIFF)
    monkeypatch.setattr(
        "gaia.worktree.read_worktree_metadata",
        lambda path: SimpleNamespace(contract_id="c1.abc") if str(path) == WORKTREE else None,
    )
    monkeypatch.setattr(
        "gaia.store.writer.get_contract_worktree_capture",
        lambda contract_id: (
            {"artifact_path": str(blob), "size_bytes": len(DIFF), "worktree_path": WORKTREE}
            if capture and contract_id == "c1.abc" else None
        ),
    )
    return blob


def test_force_remove_shows_where_the_work_was_captured_and_what_it_holds(monkeypatch, tmp_path):
    blob = _captured(monkeypatch, tmp_path, capture=True)

    details = _details(f"git -C /repo worktree remove --force {WORKTREE}")

    assert f"[ CAPTURED: {blob} " in details
    assert f"{len(DIFF)} bytes" in details
    assert "1 file, +2 -1" in details


def test_force_remove_with_no_capture_says_nothing_was_preserved(monkeypatch, tmp_path):
    _captured(monkeypatch, tmp_path, capture=False)

    details = _details(f"git worktree remove -f {WORKTREE}")

    assert "[ CAPTURED: none recorded" in details


def test_a_remove_without_force_or_another_command_shows_no_capture_field(monkeypatch, tmp_path):
    _captured(monkeypatch, tmp_path, capture=True)

    assert "CAPTURED" not in _details(f"git worktree remove {WORKTREE}")
    assert "CAPTURED" not in _details("git push origin main")
