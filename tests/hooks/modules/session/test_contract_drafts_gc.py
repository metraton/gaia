#!/usr/bin/env python3
"""
Tests for the SessionStart contract-drafts GC: modules.session.contract_drafts_gc.

gc_contract_drafts() deletes exactly what gaia.contract.drafts.collectable_drafts
selects, best-effort: a per-file failure never aborts the sweep nor blocks
session start. The thresholds and the criterion both live in that policy module
-- this hook owns no retention constant, so the tests below assert its behavior
against the policy's own values rather than a copy of them.

These cases exercise the AGE lane: the sandbox has no gaia.db, so
spent_draft_ids() yields an empty set and the DB-aware lane is inert by design.
The spent lane and hook/CLI agreement are covered in
tests/contract/test_draft_retention_and_resolution.py.

Isolation: GAIA_DATA_DIR is redirected to a tmp path so drafts_dir() resolves
inside the test sandbox and the real ~/.gaia is never touched.
"""

import os
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Add hooks to path so `from modules.session...` resolves correctly.
HOOKS_DIR = _REPO_ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from gaia.contract.drafts import (  # noqa: E402
    DEFAULT_MAX_AGE_DAYS,
    resolve_max_age_days,
)
from modules.session.contract_drafts_gc import gc_contract_drafts  # noqa: E402


@pytest.fixture
def drafts_sandbox(tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to tmp; yield the contract_drafts directory.

    The directory is created eagerly (drafts_dir() would create it anyway) so
    tests can drop files into it directly.
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_CONTRACT_DRAFTS_MAX_DAYS", raising=False)
    drafts = tmp_path / "contract_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    yield drafts


def _write_draft(drafts: Path, name: str, age_days: float) -> Path:
    """Create a draft JSON file with mtime aged `age_days` into the past."""
    path = drafts / name
    path.write_text('{"agent_status": {}}', encoding="utf-8")
    when = time.time() - age_days * 86400
    os.utime(path, (when, when))
    return path


class TestGcDeletesOld:
    def test_prunes_draft_older_than_threshold(self, drafts_sandbox):
        old = _write_draft(drafts_sandbox, "a111111.old.json", age_days=30)
        deleted = gc_contract_drafts()
        assert deleted == 1
        assert not old.exists()

    def test_prunes_multiple_old_drafts(self, drafts_sandbox):
        for i in range(5):
            _write_draft(drafts_sandbox, f"a11111{i}.old.json", age_days=10)
        deleted = gc_contract_drafts()
        assert deleted == 5
        assert list(drafts_sandbox.glob("*.json")) == []


class TestGcPreservesRecent:
    def test_preserves_draft_within_threshold(self, drafts_sandbox):
        """A recent draft -- e.g. one an in-progress turn is still writing -- is kept."""
        recent = _write_draft(drafts_sandbox, "a222222.recent.json", age_days=1)
        deleted = gc_contract_drafts()
        assert deleted == 0
        assert recent.exists()

    def test_mixed_old_and_recent(self, drafts_sandbox):
        old = _write_draft(drafts_sandbox, "a333333.old.json", age_days=14)
        recent = _write_draft(drafts_sandbox, "a333333.recent.json", age_days=2)
        deleted = gc_contract_drafts()
        assert deleted == 1
        assert not old.exists()
        assert recent.exists()

    def test_boundary_just_under_threshold_is_kept(self, drafts_sandbox):
        """A draft just younger than the threshold survives (cutoff is strict <)."""
        near = _write_draft(
            drafts_sandbox, "a444444.near.json", age_days=DEFAULT_MAX_AGE_DAYS - 0.5
        )
        deleted = gc_contract_drafts()
        assert deleted == 0
        assert near.exists()


class TestGcBestEffort:
    def test_per_file_error_does_not_abort_sweep(self, drafts_sandbox, monkeypatch):
        """An unlink failure on one file must not stop the others being pruned."""
        _write_draft(drafts_sandbox, "a555555.boom.json", age_days=20)
        survivor_target = _write_draft(drafts_sandbox, "a555555.ok.json", age_days=20)

        real_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self.name == "a555555.boom.json":
                raise OSError("simulated locked file")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        # Must not raise, and must still delete the healthy old file.
        deleted = gc_contract_drafts()
        assert deleted == 1
        assert not survivor_target.exists()
        assert (drafts_sandbox / "a555555.boom.json").exists()

    def test_missing_dir_returns_zero_without_error(self, tmp_path, monkeypatch):
        """No data dir yet -> nothing to prune, no crash (drafts_dir creates it empty)."""
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "does_not_exist_yet"))
        monkeypatch.delenv("GAIA_CONTRACT_DRAFTS_MAX_DAYS", raising=False)
        assert gc_contract_drafts() == 0

    def test_ignores_non_json_files(self, drafts_sandbox):
        _write_draft(drafts_sandbox, "a666666.old.json", age_days=20)
        other = drafts_sandbox / "note.txt"
        other.write_text("keep me", encoding="utf-8")
        when = time.time() - 20 * 86400
        os.utime(other, (when, when))
        deleted = gc_contract_drafts()
        assert deleted == 1
        assert other.exists()


class TestThresholdResolution:
    """The hook delegates threshold resolution; these assert the delegation holds.

    The env var is read by the policy, so the observable claim is that the SWEEP
    honors it -- not that this module parses it. A hook that re-read the env
    itself would pass a parse test and still be free to diverge from the policy,
    which is the failure this delegation removes.
    """

    def test_default_when_env_absent(self, drafts_sandbox):
        assert resolve_max_age_days() == DEFAULT_MAX_AGE_DAYS

    def test_env_override_reaches_the_sweep(self, drafts_sandbox, monkeypatch):
        monkeypatch.setenv("GAIA_CONTRACT_DRAFTS_MAX_DAYS", "1")
        # A 3-day-old draft is prunable under the 1-day threshold, and the hook
        # passes no threshold of its own for the env to be overridden by.
        old = _write_draft(drafts_sandbox, "a777777.json", age_days=3)
        assert gc_contract_drafts() == 1
        assert not old.exists()

    def test_invalid_env_falls_back_to_default(self, drafts_sandbox, monkeypatch):
        monkeypatch.setenv("GAIA_CONTRACT_DRAFTS_MAX_DAYS", "not-a-number")
        assert resolve_max_age_days() == DEFAULT_MAX_AGE_DAYS
        # And the sweep behaves as if unset: a 3-day draft survives the default.
        recent = _write_draft(drafts_sandbox, "a777778.json", age_days=3)
        assert gc_contract_drafts() == 0
        assert recent.exists()

    def test_explicit_arg_overrides_env(self, drafts_sandbox):
        recent = _write_draft(drafts_sandbox, "a888888.json", age_days=5)
        # Under an explicit 10-day threshold, a 5-day draft is preserved.
        assert gc_contract_drafts(max_days=10) == 0
        assert recent.exists()
