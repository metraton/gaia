"""
Capture-before-recycle for agentic worktrees (AC-9).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

The property under test, stated once: a worktree carrying uncommitted
changes or commits no remote holds is NEVER recycled before its full diff
is durably deposited through the evidence producer lane opened in task 3 --
and, per the corrected design (see gaia/retention/worktree_reclaim.py's
module docstring), such a worktree is never auto-force-removed at all,
because no unforgeable, content-bound exemption for that currently exists.
Only an ALREADY-CLEAN worktree gets removed, through the existing,
unconditionally safe task-11 exemption. The adversarial half is what turns
"we tried to be careful" into a guarantee: if the deposit fails for any
reason, the worktree must come out of the attempt byte-for-byte as it went
in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _seed_brief(workspace: str = "me", name: str = "wt-reclaim-test") -> int:
    from gaia.briefs.store import upsert_brief

    return upsert_brief(workspace, name, {"title": "seed", "objective": "seed"})["brief_id"]


def _dirty_agentic_worktree(repo_path: Path, branch: str) -> Path:
    """A locked agentic worktree (mirrors real creation, task 12) made dirty
    with one tracked-file edit and one untracked file."""
    from gaia.worktree import create_agentic_worktree

    worktree = create_agentic_worktree(
        repo_path, f"a{branch}deadbeefdeadbeef.cafef00d", f"a{branch}deadbeefdeadbeef",
        branch=branch,
    )
    (worktree / "README.md").write_text("hello\nedited by the agent\n", encoding="utf-8")
    (worktree / "scratch-note.txt").write_text("untracked note content\n", encoding="utf-8")
    return worktree


def _snapshot(worktree: Path) -> dict:
    return {
        str(p.relative_to(worktree)): p.read_bytes()
        for p in worktree.rglob("*")
        if p.is_file()
    }


@pytest.fixture()
def repo(tmp_path):
    return _init_repo(tmp_path)


# ---------------------------------------------------------------------------
# (a)+(b): capture happens and lands under the canonical root. (c), the
# corrected reading: a dirty worktree is captured and then deliberately
# left in place rather than force-removed -- see the module docstring for
# why an automatic force-remove is not offered.
# ---------------------------------------------------------------------------

def test_dirty_worktree_is_captured_and_left_in_place(repo):
    import gaia.retention.worktree_reclaim as wr
    from gaia.evidence.store import get_evidence
    from gaia.paths import evidence_dir

    _seed_brief()
    worktree = _dirty_agentic_worktree(repo, "wt1")

    result = wr.reclaim_worktree(
        repo, worktree,
        workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
        created_by_agent="gaia-system",
    )

    assert result["status"] == "captured_pending_removal"
    assert result["recycled"] is False
    assert result["captured"] is True
    assert result["reason"]

    # (a): a new evidence row exists whose blob carries the full diff.
    row = get_evidence(result["evidence_id"])
    assert row is not None
    artifact_path = Path(row["artifact_path"])
    assert artifact_path.exists()
    diff_text = artifact_path.read_text(encoding="utf-8")
    assert "edited by the agent" in diff_text, "tracked-file edit missing from the diff"
    assert "untracked note content" in diff_text, "untracked file content missing from the diff"

    # (b): the blob lives under the canonical evidence root.
    root = evidence_dir().resolve()
    resolved = artifact_path.resolve()
    assert resolved == root or root in resolved.parents

    # The corrected (c): captured work is left in place, not force-removed.
    assert worktree.exists()
    assert (worktree / "README.md").read_text(encoding="utf-8") == "hello\nedited by the agent\n"
    assert (worktree / "scratch-note.txt").exists()


def test_committed_but_unpushed_work_is_captured_and_left_in_place(repo):
    """Uncommitted changes are only half of AC-9 -- a commit no remote holds
    is exactly as much at risk, and must be captured (then left in place)
    the same way."""
    from gaia.worktree import create_agentic_worktree
    import gaia.retention.worktree_reclaim as wr
    from gaia.evidence.store import get_evidence

    _seed_brief()
    worktree = create_agentic_worktree(
        repo, "aunpushedbeefdeadbeefdead.f00dcafe", "aunpushedbeefdeadbeefdead",
        branch="wt-unpushed",
    )
    (worktree / "new_file.txt").write_text("brand new committed content\n", encoding="utf-8")
    _git(worktree, "add", "new_file.txt")
    _git(worktree, "commit", "-q", "-m", "unpushed work")

    result = wr.reclaim_worktree(
        repo, worktree, workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
    )

    assert result["status"] == "captured_pending_removal"
    assert result["captured"] is True
    row = get_evidence(result["evidence_id"])
    diff_text = Path(row["artifact_path"]).read_text(encoding="utf-8")
    assert "brand new committed content" in diff_text
    assert worktree.exists()


def test_clean_worktree_recycles_without_capturing_anything(repo):
    """The negative case: a worktree with nothing to capture uses the
    existing, unconditionally safe exemption and is actually removed."""
    from gaia.worktree import create_agentic_worktree
    import gaia.retention.worktree_reclaim as wr

    _seed_brief()
    worktree = create_agentic_worktree(
        repo, "aclean0011223344deadbeef.abc123", "aclean0011223344deadbeef",
        branch="wt-clean",
    )

    result = wr.reclaim_worktree(
        repo, worktree, workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
    )

    assert result == {
        "status": "recycled",
        "recycled": True, "captured": False, "evidence_id": None, "reason": None,
    }
    assert not worktree.exists()


def test_worktree_needs_capture_reflects_dirtiness(repo):
    from gaia.worktree import create_agentic_worktree
    import gaia.retention.worktree_reclaim as wr

    worktree = create_agentic_worktree(
        repo, "acheck0011223344deadbeef.aa11bb", "acheck0011223344deadbeef",
        branch="wt-check",
    )
    assert wr.worktree_needs_capture(worktree) is False

    (worktree / "README.md").write_text("hello\nchanged\n", encoding="utf-8")
    assert wr.worktree_needs_capture(worktree) is True


# ---------------------------------------------------------------------------
# (d) ADVERSARIAL: a failed capture must leave the worktree untouched.
# ---------------------------------------------------------------------------

def test_evidence_insert_failure_leaves_worktree_untouched(repo, monkeypatch):
    """The deposit's row-insert half fails -- the worktree must not move."""
    import gaia.retention.worktree_reclaim as wr
    from gaia.paths import evidence_dir
    from gaia.store.writer import _connect

    _seed_brief()
    worktree = _dirty_agentic_worktree(repo, "wt2")
    before = _snapshot(worktree)

    def failing_insert(*args, **kwargs):
        raise RuntimeError("simulated evidence-store outage")

    # The lazy `from gaia.evidence.store import insert_evidence` inside
    # _deposit_diff_evidence resolves the name at call time, so patching the
    # attribute on gaia.evidence.store (not a name already bound in
    # worktree_reclaim's own namespace) is what that import actually sees.
    monkeypatch.setattr("gaia.evidence.store.insert_evidence", failing_insert)

    result = wr.reclaim_worktree(
        repo, worktree, workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
    )

    assert result["status"] == "deposit_failed"
    assert result["recycled"] is False
    assert result["captured"] is False
    assert result["evidence_id"] is None
    assert "evidence deposit failed" in result["reason"]

    # The hard property: directory and contents are exactly as found.
    assert worktree.exists()
    assert _snapshot(worktree) == before

    # No orphan blob -- the write-then-insert-then-cleanup-on-reject
    # guarantee from the producer lane (task 3) must hold here too.
    assert list(evidence_dir().rglob("*.diff")) == []

    con = _connect()
    try:
        count = con.execute("select count(*) from evidence").fetchone()[0]
    finally:
        con.close()
    assert count == 0


def test_blob_write_failure_leaves_worktree_untouched(repo, monkeypatch):
    """The deposit's blob-write half fails instead -- same guarantee."""
    import gaia.retention.worktree_reclaim as wr

    _seed_brief()
    worktree = _dirty_agentic_worktree(repo, "wt3")
    before = _snapshot(worktree)

    def failing_write_blob(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr("gaia.evidence.fs.write_blob", failing_write_blob)

    result = wr.reclaim_worktree(
        repo, worktree, workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
    )

    assert result["status"] == "deposit_failed"
    assert result["captured"] is False
    assert result["evidence_id"] is None
    assert "evidence deposit failed" in result["reason"]
    assert worktree.exists()
    assert _snapshot(worktree) == before


def test_diff_capture_failure_leaves_worktree_untouched(repo, monkeypatch):
    """The capture step itself fails (before any evidence-store call at
    all) -- the worktree must still be completely untouched."""
    import gaia.retention.worktree_reclaim as wr

    _seed_brief()
    worktree = _dirty_agentic_worktree(repo, "wt4")
    before = _snapshot(worktree)

    def failing_capture(_worktree_path):
        raise subprocess.CalledProcessError(1, ["git", "diff"])

    monkeypatch.setattr(wr, "capture_worktree_diff", failing_capture)

    result = wr.reclaim_worktree(
        repo, worktree, workspace="me", brief_slug="wt-reclaim-test", ac_id="AC-9",
    )

    assert result["status"] == "capture_failed"
    assert result["captured"] is False
    assert result["evidence_id"] is None
    assert "diff capture failed" in result["reason"]
    assert worktree.exists()
    assert _snapshot(worktree) == before
