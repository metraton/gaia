"""Retention for what Gaia creates during a turn and never revisits: scratch,
tmp, cache, and preserved rejected-turn text (``gaia.retention.fs_rules``).

Mirrors ``tests/contract/test_draft_retention_and_resolution.py`` in shape,
because the criterion is deliberately the same shape: caducity by STATE
(owning turn closed) plus a grace window, never by age alone. The one
property that must NOT carry over is the draft policy's age-only fallback
lane -- these four rules have no equivalent, and
``test_unreadable_db_never_falls_back_to_age`` pins that directly.

Isolation: ``GAIA_DATA_DIR`` is redirected to a tmp path so every directory
under test (``scratch_dir()``, ``tmp_dir()``, ``cache_dir()``,
``rejected_turns_dir()``) and ``db_path()`` resolve under it.
"""

import importlib
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_A = valid_agent_id("fsr-a1")
AGENT_B = valid_agent_id("fsr-b2")
HOUR = 3600.0
DAY = 86400.0


@pytest.fixture()
def fs_rules(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate to tmp and return the policy module."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    import gaia.retention.fs_rules as mod

    importlib.reload(mod)
    return mod


def _seed_rows(tmp_path, rows):
    """Create the minimal agent_contract_handoffs shape the policy reads.

    ``rows`` is an iterable of ``(contract_id, session_id, agent_state)``.
    """
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, agent_state text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, session_id, agent_state) "
        "values (?, ?, ?)",
        list(rows),
    )
    con.commit()
    con.close()


def _touch(path: Path, age_seconds: float = 0.0, is_dir: bool = False):
    if is_dir:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    if age_seconds:
        when = time.time() - age_seconds
        os.utime(path, (when, when))
    return path


# ---------------------------------------------------------------------------
# collectable_turn_scoped -- scratch/tmp/cache all share this criterion.
# ---------------------------------------------------------------------------

def test_entry_with_no_recognizable_owner_is_never_touched(fs_rules, tmp_path):
    """No evidence of an owner is not evidence of disposability."""
    root = tmp_path / "scratch"
    _touch(root / "some-random-leftover-dir", age_seconds=30 * DAY, is_dir=True)

    selected = fs_rules.collectable_turn_scoped(root)

    assert selected == []


def test_open_contract_is_never_collected_regardless_of_age(fs_rules, tmp_path):
    """The turn is still running -- never collectable, however old."""
    root = tmp_path / "scratch"
    contract_id = f"{AGENT_A}.aaaaaa"
    _touch(root / contract_id, age_seconds=30 * DAY, is_dir=True)
    _seed_rows(tmp_path, [(contract_id, None, "IN_PROGRESS")])

    selected = fs_rules.collectable_turn_scoped(root)

    assert selected == []


def test_closed_contract_past_grace_is_collected_with_legible_reason(fs_rules, tmp_path):
    root = tmp_path / "scratch"
    contract_id = f"{AGENT_A}.bbbbbb"
    entry = _touch(root / contract_id, age_seconds=48 * HOUR, is_dir=True)
    _seed_rows(tmp_path, [(contract_id, None, "COMPLETE")])

    selected = fs_rules.collectable_turn_scoped(root, grace_hours=24)

    assert len(selected) == 1
    assert selected[0]["path"] == str(entry)
    assert selected[0]["action"] == "delete-dir"
    assert contract_id in selected[0]["reason"]
    assert selected[0]["reason"].strip() != ""


def test_closed_contract_inside_grace_is_kept(fs_rules, tmp_path):
    """Just-closed is not the same moment as no-longer-read."""
    root = tmp_path / "scratch"
    contract_id = f"{AGENT_A}.cccccc"
    _touch(root / contract_id, age_seconds=1 * HOUR, is_dir=True)
    _seed_rows(tmp_path, [(contract_id, None, "COMPLETE")])

    selected = fs_rules.collectable_turn_scoped(root, grace_hours=24)

    assert selected == []


def test_file_entry_with_extension_matches_by_stem(fs_rules, tmp_path):
    root = tmp_path / "tmp"
    contract_id = f"{AGENT_B}.dddddd"
    entry = _touch(root / f"{contract_id}.tgz", age_seconds=48 * HOUR)
    _seed_rows(tmp_path, [(contract_id, None, "COMPLETE")])

    selected = fs_rules.collectable_turn_scoped(root, grace_hours=24)

    assert [r["path"] for r in selected] == [str(entry)]
    assert selected[0]["action"] == "delete-file"


def test_unrelated_cache_entry_is_left_alone(fs_rules, tmp_path):
    """The real, currently-existing cache population (workspace-keyed
    dev-pack directories) never matches the contract-id shape and must be
    left completely untouched by this rule."""
    root = tmp_path / "cache"
    _touch(root / "dev-pack" / "github.com-owner-repo" / "pkg.tgz", age_seconds=90 * DAY)

    selected = fs_rules.collectable_turn_scoped(root, grace_hours=24)

    assert selected == []


def test_unreadable_db_never_falls_back_to_age(fs_rules, monkeypatch, tmp_path):
    """The deliberate divergence from gaia.contract.drafts: no age-only lane.

    An unreadable DB must degrade to "select nothing", never to "old enough,
    delete it anyway" -- unlike ``collectable_drafts``'s ``aged`` lane, which
    is safe only because a draft is a copy of a row that (if it exists) is
    durable elsewhere. None of these entries have that property.
    """
    monkeypatch.setattr(fs_rules, "_ro_db_connect", lambda: None)
    root = tmp_path / "scratch"
    contract_id = f"{AGENT_A}.eeeeee"
    _touch(root / contract_id, age_seconds=365 * DAY, is_dir=True)
    # Even if a row existed, the read-only connect is short-circuited above,
    # so this seed cannot be observed -- assert that directly too.
    _seed_rows(tmp_path, [(contract_id, None, "COMPLETE")])

    selected = fs_rules.collectable_turn_scoped(root, grace_hours=24)

    assert selected == []
    assert fs_rules._closed_contract_ids({contract_id}) == set()


# ---------------------------------------------------------------------------
# collectable_rejected_turns -- keyed by harness session, not contract id.
# ---------------------------------------------------------------------------

def test_rejected_turn_with_no_closed_session_row_is_kept(fs_rules, tmp_path):
    root = tmp_path / "rejected_turns"
    _touch(root / "session-open.a1.txt", age_seconds=48 * HOUR)

    selected = fs_rules.collectable_rejected_turns(root, grace_hours=24)

    assert selected == []


def test_rejected_turn_with_closed_session_past_grace_is_collected(fs_rules, tmp_path):
    root = tmp_path / "rejected_turns"
    session_id = "8f831722-9a04-4e8f-bf0c-a8636692442a"
    entry = _touch(root / f"{session_id}.{AGENT_A}.txt", age_seconds=48 * HOUR)
    _seed_rows(tmp_path, [(f"{AGENT_A}.tok", session_id, "COMPLETE")])

    selected = fs_rules.collectable_rejected_turns(root, grace_hours=24)

    assert [r["path"] for r in selected] == [str(entry)]
    assert session_id in selected[0]["reason"]
    assert selected[0]["reason"].strip() != ""


def test_rejected_turn_unreadable_db_never_falls_back_to_age(fs_rules, monkeypatch, tmp_path):
    monkeypatch.setattr(fs_rules, "_ro_db_connect", lambda: None)
    root = tmp_path / "rejected_turns"
    session_id = "e236f38b-77b8-45eb-9634-713f071061e0"
    _touch(root / f"{session_id}.{AGENT_A}.txt", age_seconds=365 * DAY)
    _seed_rows(tmp_path, [(f"{AGENT_A}.tok", session_id, "COMPLETE")])

    selected = fs_rules.collectable_rejected_turns(root, grace_hours=24)

    assert selected == []


# ---------------------------------------------------------------------------
# The CLI preview is a true preview: exact same population, dry-run deletes
# nothing, and threshold resolution is not duplicated locally.
# ---------------------------------------------------------------------------

def _cleanup_module():
    bin_dir = _REPO_ROOT / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    import cli.cleanup as cleanup_mod

    return cleanup_mod


def test_cli_dry_run_previews_exactly_the_policy_selection(fs_rules, tmp_path):
    root = tmp_path / "scratch"
    contract_id = f"{AGENT_A}.f00f00"
    entry = _touch(root / contract_id, age_seconds=48 * HOUR, is_dir=True)
    _seed_rows(tmp_path, [(contract_id, None, "COMPLETE")])

    policy_paths = {r["path"] for r in fs_rules.collectable_turn_scoped(root)}
    cli_actions = _cleanup_module()._prune_turn_scoped(root, "Scratch", dry_run=True)

    assert policy_paths == {str(entry)}
    assert {a["path"] for a in cli_actions} == policy_paths
    assert entry.exists(), "dry run must not delete"
    assert all(a.get("reason") for a in cli_actions)


def test_cli_sweep_deletes_exactly_what_the_dry_run_reported(fs_rules, tmp_path):
    root = tmp_path / "tmp"
    keep_id = f"{AGENT_A}.1a2b3c"
    drop_id = f"{AGENT_B}.4d5e6f"
    kept = _touch(root / keep_id, age_seconds=1 * HOUR, is_dir=True)
    dropped = _touch(root / drop_id, age_seconds=48 * HOUR, is_dir=True)
    _seed_rows(tmp_path, [(keep_id, None, "COMPLETE"), (drop_id, None, "COMPLETE")])
    cleanup_mod = _cleanup_module()

    would = cleanup_mod._prune_turn_scoped(root, "Tmp", dry_run=True)
    actual = cleanup_mod._prune_turn_scoped(root, "Tmp", dry_run=False)

    assert {a["path"] for a in would} == {a["path"] for a in actual} == {str(dropped)}
    assert not dropped.exists()
    assert kept.exists(), "the still-in-grace entry survives the sweep"


def test_threshold_is_not_duplicated_between_cli_and_policy(fs_rules, monkeypatch):
    """The single-site-of-truth property: an env override reaches both."""
    monkeypatch.setenv("GAIA_FS_RETENTION_GRACE_HOURS", "3")
    cleanup_mod = _cleanup_module()

    assert fs_rules.resolve_grace_hours() == 3
    assert cleanup_mod._fs_retention_grace_hours() == 3
