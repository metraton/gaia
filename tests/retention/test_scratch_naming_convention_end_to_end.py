"""End-to-end proof that the scratch/tmp/cache naming convention taught in
``skills/command-execution/SKILL.md``, ``skills/investigation/SKILL.md``,
``skills/agent-contract-handoff/SKILL.md``, and ``agents/developer.md`` is a
working property, not merely prose.

Each of those four sources tells an agent WHERE to write a working file that
is not itself the deliverable (the canonical Gaia scratch directory) but,
without a taught naming convention, never said HOW to name it. Without a
taught name, ``gaia.retention.fs_rules`` can never recognize an entry as
attributable to a turn, so its sweep always selects nothing -- a state
indistinguishable from "no garbage exists."

Confirming the four sources contain the instruction is necessary but not
sufficient: a source could carry the literal string and the taught shape
could still not match what the retention rule actually recognizes. The
second test below closes that gap -- it derives a real filename from the
literal template string read out of the documentation, writes a probe file
under the canonical scratch directory using that derived name, closes a
contract row for it, ages the file past the retention grace window, and then
runs the retention selection path behind ``gaia cleanup --prune --dry-run
--json`` to confirm the file is actually recognized and reported as
collectible.

The derived filename is built from the template text extracted from the
docs (``<agent_id>.<token>``), not from ``gaia.retention.fs_rules``'s own
``_CONTRACT_ID_RE`` -- importing the retention module's internal pattern to
build the name would test the implementation against itself instead of
testing the taught contract.
"""

import importlib
import json
import os
import sqlite3
import sys
import time
from argparse import Namespace
from pathlib import Path

from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The literal shape the four sources now state. Used to both confirm
# coverage and to derive a real filename -- never imported from the
# retention module itself.
_TEMPLATE = "<agent_id>.<token>"

_SOURCES = {
    "command-execution": _REPO_ROOT / "skills" / "command-execution" / "SKILL.md",
    "investigation": _REPO_ROOT / "skills" / "investigation" / "SKILL.md",
    "agent-contract-handoff": _REPO_ROOT / "skills" / "agent-contract-handoff" / "SKILL.md",
    "agents/developer.md": _REPO_ROOT / "agents" / "developer.md",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_four_sources_teach_the_naming_template():
    """Coverage check -- necessary, but explicitly NOT sufficient on its own.

    A source could contain this literal string and still leave the sweep
    finding nothing, if the taught shape did not match what the retention
    rule actually recognizes -- that is what the second test below proves.
    """
    missing = [name for name, path in _SOURCES.items() if _TEMPLATE not in _read(path)]
    assert missing == [], f"sources missing the naming template: {missing}"


def _derive_filename_from_doc(doc_text: str, agent_id: str, token: str) -> str:
    """Build a real filename by substituting into the doc's own template text.

    Reads the instruction rather than the module: the template positions
    (``<agent_id>``, ``<token>``) come verbatim from the documentation string,
    not from ``gaia.retention.fs_rules._CONTRACT_ID_RE``.
    """
    assert _TEMPLATE in doc_text, "documentation no longer states the taught template"
    return _TEMPLATE.replace("<agent_id>", agent_id).replace("<token>", token)


def _cleanup_module():
    bin_dir = _REPO_ROOT / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    import cli.cleanup as cleanup_mod

    return cleanup_mod


def test_named_scratch_entry_is_recognized_and_reclaimed_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INIT_CWD", str(tmp_path))

    # Derive the filename from the documentation text itself, not from the
    # retention module's internal pattern.
    doc_text = _read(_SOURCES["command-execution"])
    agent_id = valid_agent_id("scratch-naming-e2e")
    token = "0a1b2c"
    filename = _derive_filename_from_doc(doc_text, agent_id, token)
    contract_id = filename  # the bare-id form the docs describe

    # Write a probe file under the canonical scratch directory with that name.
    import gaia.paths as paths_mod

    importlib.reload(paths_mod)
    scratch = paths_mod.scratch_dir()
    scratch.mkdir(parents=True, exist_ok=True)
    entry = scratch / filename
    entry.write_text("scratch retention naming probe", encoding="utf-8")

    # Insert (or reuse) a row for this contract id in a terminal plan_status.
    db_path = paths_mod.db_path()
    con = sqlite3.connect(str(db_path))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, agent_state text)"
    )
    existing = con.execute(
        "select 1 from agent_contract_handoffs where contract_id = ?", (contract_id,)
    ).fetchone()
    if not existing:
        con.execute(
            "insert into agent_contract_handoffs (contract_id, session_id, agent_state) "
            "values (?, ?, ?)",
            (contract_id, None, "COMPLETE"),
        )
        con.commit()
    con.close()

    # Age the file's mtime past the grace window the retention rule applies.
    import gaia.retention.fs_rules as fs_rules

    importlib.reload(fs_rules)
    grace_hours = fs_rules.resolve_grace_hours()
    stale = time.time() - (grace_hours + 1) * 3600
    os.utime(entry, (stale, stale))

    # Run the real retention selection behind `gaia cleanup --prune --dry-run --json`.
    cleanup_mod = _cleanup_module()
    importlib.reload(cleanup_mod)
    args = Namespace(prune=True, retain=False, dry_run=True, json=True)
    exit_code = cleanup_mod.cmd_cleanup(args)
    stdout = capsys.readouterr().out
    result = json.loads(stdout)

    assert exit_code == 0
    scratch_actions = [
        a for a in result["retention_actions"] if a["path"] == str(entry)
    ]
    assert len(scratch_actions) == 1, (
        f"derived-name entry {entry} not reported collectible; "
        f"retention_actions={result['retention_actions']}"
    )
    reason = scratch_actions[0].get("reason") or ""
    assert reason.strip() != ""
    assert contract_id in reason
    assert entry.exists(), "dry-run must not delete anything"
