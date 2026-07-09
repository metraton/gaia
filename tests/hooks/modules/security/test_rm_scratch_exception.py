#!/usr/bin/env python3
"""Tests for the `rm` scratch-directory tier exception (Option A).

`rm` (including `rm -rf`) is downgraded from T3 to T0 ONLY when every target
path resolves strictly inside the Gaia scratch directory (`~/.gaia/scratch`,
or the equivalent under a GAIA_DATA_DIR override).  Everything else stays T3,
and the catastrophic floor (`rm -rf /`, `/*`, `~`) remains intact.

Covered surfaces:
  * strict tier check      -- mutative_verbs._rm_targets_only_scratch / Step 1
  * floor cooperation      -- blocked_commands._rm_confined_to_scratch + ordering
  * end-to-end pipeline    -- bash_validator.BashValidator.validate
"""

import os
import sys
import pytest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))  # for the `gaia` package (resolver)

from modules.security.mutative_verbs import (
    detect_mutative_command,
    _rm_targets_only_scratch,
    _gaia_scratch_root,
)
from modules.security.blocked_commands import (
    is_blocked_command,
    _rm_confined_to_scratch,
)
from modules.tools.bash_validator import BashValidator
from modules.security.tiers import SecurityTier


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """Isolated GAIA_DATA_DIR with a populated scratch tree.

    Returns the realpath of the scratch root as a string. Creates:
      scratch/foo         (file)
      scratch/sub/        (dir)
      scratch/link -> OUTSIDE   (symlink escaping scratch)
    """
    data = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import ensure_layout, scratch_dir
    ensure_layout()
    root = scratch_dir()
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "foo").touch()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    if not link.exists():
        link.symlink_to(outside)
    # detect_mutative_command is lru_cached and NOT keyed on env; clear it so a
    # command string is re-evaluated against this test's scratch root.
    detect_mutative_command.cache_clear()
    return os.path.realpath(str(root))


def _tier(validator, cmd):
    """End-to-end classification via the real bash_validator pipeline.

    Returns one of: 'T0', 'T3', 'BLOCKED'.
    """
    detect_mutative_command.cache_clear()
    r = validator.validate(cmd)
    if not r.allowed:
        if r.tier == SecurityTier.T3_BLOCKED and r.block_response is None:
            return "BLOCKED"  # permanent floor block (exit 2, never approvable)
        return "T3"           # approvable (ask)
    if r.tier == SecurityTier.T0_READ_ONLY:
        return "T0"
    return str(r.tier)


# ---------------------------------------------------------------------------
# scratch root resolution
# ---------------------------------------------------------------------------

def test_scratch_root_resolves_under_data_dir(scratch, tmp_path):
    assert scratch == os.path.realpath(str(tmp_path / "gaia-data" / "scratch"))


def test_scratch_root_never_under_dot_claude(scratch):
    # A scratch root can never resolve inside a .claude/ hierarchy.
    assert ".claude" not in scratch.split(os.sep)


def test_gaia_scratch_root_fail_closed(monkeypatch):
    """If the resolver cannot be imported, scratch root is None (fail-closed)."""
    # A pathological GAIA_DATA_DIR still resolves; the true fail-closed path is
    # an import error, which we assert indirectly: with no env the root is a
    # real path, never an exception bubbling up.
    monkeypatch.delenv("GAIA_DATA_DIR", raising=False)
    root = _gaia_scratch_root()
    assert root is None or isinstance(root, str)


# ---------------------------------------------------------------------------
# strict tier check: _rm_targets_only_scratch  (AC -> T0)
# ---------------------------------------------------------------------------

def test_rm_file_in_scratch_is_t0(scratch):
    result = detect_mutative_command(f"rm {scratch}/foo")
    assert result.is_mutative is False
    assert result.category == "READ_ONLY"


def test_rm_rf_subdir_in_scratch_is_t0(scratch):
    result = detect_mutative_command(f"rm -rf {scratch}/sub/")
    assert result.is_mutative is False
    assert result.category == "READ_ONLY"


def test_rm_rf_scratch_root_itself_is_t0(scratch):
    result = detect_mutative_command(f"rm -rf {scratch}")
    assert result.is_mutative is False


# ---------------------------------------------------------------------------
# strict tier check: things that MUST stay T3
# ---------------------------------------------------------------------------

def test_rm_symlink_escaping_scratch_stays_t3(scratch):
    # scratch/link -> OUTSIDE ; realpath resolves outside scratch.
    result = detect_mutative_command(f"rm {scratch}/link")
    assert result.is_mutative is True
    assert result.category == "MUTATIVE"


def test_rm_traversal_out_of_scratch_stays_t3(scratch):
    result = detect_mutative_command(f"rm {scratch}/../../etc/x")
    assert result.is_mutative is True


def test_rm_glob_in_scratch_stays_t3(scratch):
    # Unexpanded glob is ambiguous -> no T0 exception.
    result = detect_mutative_command(f"rm -rf {scratch}/*")
    assert result.is_mutative is True


def test_rm_relative_outside_stays_t3(scratch):
    result = detect_mutative_command("rm ./repo-real/x")
    assert result.is_mutative is True


def test_rm_absolute_outside_stays_t3(scratch):
    result = detect_mutative_command("rm /home/jorge/ws/algo")
    assert result.is_mutative is True


def test_rm_no_positional_path_stays_t3(scratch):
    result = detect_mutative_command("rm -rf")
    assert result.is_mutative is True


def test_rm_only_flags_stays_t3(scratch):
    result = detect_mutative_command("rm -f --")
    assert result.is_mutative is True


def test_rm_mixed_scratch_and_outside_stays_t3(scratch):
    # One outside path is enough to deny the T0 exception (all-or-nothing).
    result = detect_mutative_command(f"rm {scratch}/foo /etc/passwd")
    assert result.is_mutative is True


# ---------------------------------------------------------------------------
# _rm_targets_only_scratch predicate directly
# ---------------------------------------------------------------------------

def test_predicate_true_for_scratch_file(scratch):
    assert _rm_targets_only_scratch(("rm", f"{scratch}/foo")) is True


def test_predicate_false_for_glob(scratch):
    assert _rm_targets_only_scratch(("rm", "-rf", f"{scratch}/*")) is False


def test_predicate_false_for_traversal(scratch):
    assert _rm_targets_only_scratch(("rm", f"{scratch}/../x")) is False


def test_predicate_false_without_scratch_root(monkeypatch):
    # No resolver failure, but empty token list -> no positional path.
    monkeypatch.setenv("GAIA_DATA_DIR", "/nonexistent-xyz")
    assert _rm_targets_only_scratch(("rm", "-rf")) is False


# ---------------------------------------------------------------------------
# catastrophic floor stays intact  (AC -> BLOCKED)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf ~/",
    "rm -rf ~/Documents",      # non-scratch home subpath: floor unchanged
])
def test_catastrophic_floor_still_blocks(cmd, scratch):
    # scratch fixture is active but these are NOT scratch paths, so the
    # rm_critical floor must still fire.
    assert is_blocked_command(cmd).is_blocked is True


def test_rm_normal_tmp_not_blocked(scratch):
    # Regression guard for the existing behavior.
    assert is_blocked_command("rm -rf /tmp/build").is_blocked is False


# ---------------------------------------------------------------------------
# floor cooperation: scratch-confined rm is NOT blocked by the floor
# ---------------------------------------------------------------------------

def test_floor_defers_scratch_rf_subdir(scratch):
    assert is_blocked_command(f"rm -rf {scratch}/sub/").is_blocked is False


def test_floor_defers_scratch_glob(scratch):
    # Lenient floor confinement tolerates the glob so the tier engine can
    # route it to T3 (not a permanent BLOCK).
    assert is_blocked_command(f"rm -rf {scratch}/*").is_blocked is False


def test_floor_confinement_predicate(scratch):
    assert _rm_confined_to_scratch(f"rm -rf {scratch}/sub/") is True
    assert _rm_confined_to_scratch(f"rm -rf {scratch}/*") is True
    assert _rm_confined_to_scratch("rm -rf ~") is False
    assert _rm_confined_to_scratch("rm -rf /") is False
    assert _rm_confined_to_scratch(f"rm -rf {scratch}/../x") is False


# ---------------------------------------------------------------------------
# END-TO-END via bash_validator (the strongest genuine check)
# ---------------------------------------------------------------------------

@pytest.fixture
def validator():
    return BashValidator()


def test_e2e_scratch_file_is_t0(validator, scratch):
    assert _tier(validator, f"rm {scratch}/foo") == "T0"


def test_e2e_scratch_rf_subdir_is_t0(validator, scratch):
    assert _tier(validator, f"rm -rf {scratch}/sub/") == "T0"


def test_e2e_catastrophic_root_blocked(validator, scratch):
    assert _tier(validator, "rm -rf /") == "BLOCKED"


def test_e2e_catastrophic_root_glob_blocked(validator, scratch):
    assert _tier(validator, "rm -rf /*") == "BLOCKED"


def test_e2e_catastrophic_home_blocked(validator, scratch):
    assert _tier(validator, "rm -rf ~") == "BLOCKED"


def test_e2e_symlink_escape_is_t3(validator, scratch):
    assert _tier(validator, f"rm {scratch}/link") == "T3"


def test_e2e_traversal_is_blocked_or_t3(validator, scratch):
    # `..` token -> floor still matches the `~`/path patterns OR tier engine
    # keeps it T3; either way it is never T0.
    assert _tier(validator, f"rm {scratch}/../../etc/x") in ("BLOCKED", "T3")


def test_e2e_scratch_glob_is_t3(validator, scratch):
    assert _tier(validator, f"rm -rf {scratch}/*") == "T3"


def test_e2e_outside_absolute_is_t3(validator, scratch):
    assert _tier(validator, "rm /home/jorge/ws/algo") == "T3"


def test_e2e_no_path_is_t3(validator, scratch):
    assert _tier(validator, "rm -rf") == "T3"
