#!/usr/bin/env python3
"""
Packaging oracle for the `gaia defects` verb.

A CLI verb that is not distributed fails SILENTLY: the installed copy simply
does not have the subcommand, and nothing in the repo notices. `build/
gaia.manifest.json` declares `bin` as an EXPLICIT list, so an entry can cover
a module by directory while the artifact a user installs still lacks it --
which is exactly the failure this file exists to catch.

The check therefore does not read the manifest text or an enumeration written
here. It resolves the manifest through the real packer
(`scripts/build-plugin.py::resolve_file_list`), materializes THAT EXACT file
set into a temporary tree, and executes `gaia defects` from it against an
isolated `GAIA_DATA_DIR`. Whatever the packer resolves is what the test runs;
if the module stops being resolved, the run stops working.

`test_verb_is_unreachable_when_the_module_is_not_packed` is the falsifiability
half: it materializes the same resolved set MINUS the CLI module and asserts
the invocation fails. Without it, a green result would be compatible with a
check that never measured anything.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VERB = "defects"
CLI_MODULE_REL = Path("bin/cli/defects.py")
DISPATCHER_REL = Path("bin/gaia")

WORKSPACE = "packaging-oracle-ws"
EPISODE_ID = "ep_packaging_oracle"
SUBAGENT_DEFECT_TYPE = "agent_reported_defect"
ORCHESTRATOR_DEFECT_TYPE = "agent.cut"


def _load_build_plugin_module():
    """Import scripts/build-plugin.py (hyphenated filename, not import-able directly)."""
    spec = importlib.util.spec_from_file_location(
        "_gaia_build_plugin", PROJECT_ROOT / "scripts" / "build-plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def packed_relative_paths() -> list[Path]:
    """Every file the real packer resolves from the manifest, repo-relative.

    Resolved, never enumerated: a hardcoded list here would measure the list
    instead of the packaging.
    """
    build_plugin = _load_build_plugin_module()
    manifest = build_plugin.load_manifest("gaia")
    return [
        path.relative_to(PROJECT_ROOT)
        for path in build_plugin.resolve_file_list(manifest)
    ]


def _materialize(relative_paths, dest: Path, omit: Path | None = None) -> int:
    """Copy the resolved file set into ``dest``, optionally omitting one path.

    ``omit`` models "the manifest did not resolve this file" without editing
    the manifest, which is what makes the negative case a real absence rather
    than a simulated one.
    """
    written = 0
    for rel in relative_paths:
        if omit is not None and rel == omit:
            continue
        source = PROJECT_ROOT / rel
        if not source.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        written += 1
    return written


def _seed_defects(db_path: Path) -> None:
    """Create an isolated substrate holding one defect of each origin.

    Uses the repo's own writer so the schema is materialized the same way the
    packed CLI will materialize it, then inserts through plain SQL because
    these two rows are normally written by hooks, not by a public writer API.
    """
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (WORKSPACE,)
        )
        con.execute(
            "INSERT OR REPLACE INTO episodes (episode_id, workspace, timestamp, agent) "
            "VALUES (?, ?, ?, ?)",
            (EPISODE_ID, WORKSPACE, "2026-07-27T04:00:00+00:00", "gaia-system"),
        )
        con.execute(
            "INSERT INTO episode_anomalies "
            "(episode_id, workspace, timestamp, type, severity, message, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                EPISODE_ID,
                WORKSPACE,
                "2026-07-27T04:00:00+00:00",
                SUBAGENT_DEFECT_TYPE,
                "info",
                "a subagent stated a defect",
                json.dumps({"agent": "gaia-system"}),
            ),
        )
        con.execute(
            "INSERT INTO harness_events "
            "(workspace, ts, type, source, agent, result, severity, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                WORKSPACE,
                "2026-07-27T03:00:00+00:00",
                ORCHESTRATOR_DEFECT_TYPE,
                "hook",
                "platform-architect",
                "subagent cut mid-turn (no_contract_fence)",
                "warning",
                json.dumps({"reason": "no_contract_fence"}),
            ),
        )
        con.commit()
    finally:
        con.close()


def _run_packed_verb(tree: Path, data_dir: Path, *args: str):
    """Invoke the packed dispatcher, isolated from the developer's own substrate."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["GAIA_DATA_DIR"] = str(data_dir)
    env["GAIA_DB"] = str(data_dir / "gaia.db")
    return subprocess.run(
        [sys.executable, str(tree / DISPATCHER_REL), VERB, *args],
        cwd=str(tree),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_manifest_resolves_the_cli_module(packed_relative_paths):
    """The packer must resolve the verb's module and the dispatcher that finds it."""
    resolved = set(packed_relative_paths)
    assert CLI_MODULE_REL in resolved, (
        f"{CLI_MODULE_REL} is not in the file set resolved from "
        "build/gaia.manifest.json -- the verb would be absent from an install"
    )
    assert DISPATCHER_REL in resolved


def test_verb_runs_from_the_packed_tree(packed_relative_paths, tmp_path):
    """The verb must execute, and list both defect origins, from the packed set alone."""
    tree = tmp_path / "packed"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _materialize(packed_relative_paths, tree)
    _seed_defects(data_dir / "gaia.db")

    result = _run_packed_verb(tree, data_dir, "--workspace", WORKSPACE, "--json")

    assert result.returncode == 0, (
        f"packed `gaia {VERB}` failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    rows = json.loads(result.stdout)
    assert {row["origin"] for row in rows} == {"subagent", "orchestrator"}
    assert {row["type"] for row in rows} == {
        SUBAGENT_DEFECT_TYPE,
        ORCHESTRATOR_DEFECT_TYPE,
    }


def test_verb_is_unreachable_when_the_module_is_not_packed(
    packed_relative_paths, tmp_path
):
    """Falsifiability: with the module unresolved, the same invocation must fail.

    If this passed, the positive test above would be measuring nothing.
    """
    tree = tmp_path / "packed-without-verb"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _materialize(packed_relative_paths, tree, omit=CLI_MODULE_REL)
    _seed_defects(data_dir / "gaia.db")
    assert not (tree / CLI_MODULE_REL).exists()

    result = _run_packed_verb(tree, data_dir, "--workspace", WORKSPACE, "--json")

    assert result.returncode != 0, (
        "the verb ran from a tree that does not contain its module -- the "
        "packaging check cannot detect an undeclared verb"
    )
    assert f"invalid choice: '{VERB}'" in result.stderr


def test_materialized_tree_is_only_what_the_manifest_resolved(
    packed_relative_paths, tmp_path
):
    """The tree under test carries no file the packer did not resolve.

    Counts are compared against the resolved LIST, not its deduplicated set:
    `resolve_file_list` returns repeats, because the manifest lists several
    `bin/cli/*.py` files explicitly on top of the `bin/cli/` directory entry
    that already collects them.
    """
    tree = tmp_path / "packed"
    written = _materialize(packed_relative_paths, tree)
    on_disk = {
        path.relative_to(tree) for path in tree.rglob("*") if path.is_file()
    }
    assert on_disk == set(packed_relative_paths)
    assert written == len(packed_relative_paths)
