"""v43 -- dispatch kernel rendering (modules/context/kernel_builder.py).

The kernel is the data-only context a claimed turn starts with. Pinned here:

  * the ``# Your Contract`` block renders EXACTLY the approved shape --
    identity, wrapped goal, role/surface, can_read/can_write -- with neither
    ``required_checks`` nor ``workspace`` nor any instruction text;
  * a plan-task-bound row additionally renders ``plan_task_id:`` and an
    ``acceptance:`` block read from ``task_gates``;
  * ``# Your CLI`` carries the base lines plus per-role frontmatter extras
    (``cli:`` key) when the agent declares them;
  * ``# How the user works`` inlines the BODY of every
    ``type='user' AND audience='executor'`` memory row for the workspace,
    complete and never truncated -- the row COUNT stays bounded, and a
    pathologically long body is dropped whole rather than sliced; omitted
    entirely when none match;
  * ``build_kernel_context`` joins the blocks and returns None without an
    identity (the CLI/memory blocks never ship without the contract).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.context.kernel_builder import (  # noqa: E402
    build_cli_block,
    build_dispatch_kernel,
    build_kernel_context,
    build_memory_block,
)

WORKSPACE = "me"


def _base_row(**overrides):
    row = {
        "contract_id": "a0123456789abcdef.beefcafe0123",
        "agent_id": "a0123456789abcdef",
        "workspace": WORKSPACE,
        "plan_task_id": None,
        "dispatch_prompt": "investigate why the build is flaky",
        "kernel_sections": (
            '{"role": "primary", "surface": "app_ci_tooling", '
            '"can_read": ["stack", "project_identity"], "can_write": ["apps"]}'
        ),
    }
    row.update(overrides)
    return row


def test_kernel_renders_the_approved_block_exactly():
    kernel = build_dispatch_kernel(_base_row())
    assert kernel == (
        "# Your Contract\n"
        "\n"
        "contract_id: a0123456789abcdef.beefcafe0123\n"
        "agent_id:    a0123456789abcdef\n"
        "\n"
        "goal: investigate why the build is flaky\n"
        "\n"
        "role: primary\n"
        "surface: app_ci_tooling\n"
        "\n"
        "can_read:  [stack, project_identity]\n"
        "can_write: [apps]"
    )


def test_kernel_never_carries_required_checks_or_workspace():
    kernel = build_dispatch_kernel(_base_row())
    assert "required_checks" not in kernel
    assert "workspace" not in kernel


def test_kernel_wraps_a_long_goal():
    long_goal = "word " * 60
    kernel = build_dispatch_kernel(_base_row(dispatch_prompt=long_goal.strip()))
    goal_lines = [l for l in kernel.splitlines()
                  if l.startswith(("goal:", "  word"))]
    assert len(goal_lines) > 1, "a long goal wraps onto indented lines"
    assert all(len(l) <= 102 for l in kernel.splitlines())


def test_kernel_degrades_without_identity():
    assert build_dispatch_kernel(_base_row(contract_id="")) is None
    assert build_dispatch_kernel(_base_row(agent_id=None)) is None


def test_kernel_tolerates_malformed_kernel_sections():
    kernel = build_dispatch_kernel(_base_row(kernel_sections="{not json"))
    assert kernel is not None
    assert "can_read:  []" in kernel
    assert "can_write: []" in kernel


def _seed_task_with_gates(db_path: Path) -> int:
    """Materialize the schema and a task carrying two gates; returns tasks.id."""
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
            (WORKSPACE, WORKSPACE),
        )
        con.execute(
            "INSERT INTO briefs (workspace, name, objective, status) "
            "VALUES (?, 'b', 'o', 'draft')",
            (WORKSPACE,),
        )
        brief_id = con.execute("SELECT MAX(id) FROM briefs").fetchone()[0]
        con.execute(
            "INSERT INTO plans (brief_id, status) VALUES (?, 'draft')",
            (brief_id,),
        )
        plan_id = con.execute("SELECT MAX(id) FROM plans").fetchone()[0]
        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) "
            "VALUES (?, 1, 'the increment', 'pending')",
            (plan_id,),
        )
        task_id = con.execute("SELECT MAX(id) FROM tasks").fetchone()[0]
        con.execute(
            "INSERT INTO task_gates (task_id, verification_type, evidence_shape) "
            "VALUES (?, 'command', 'pytest tests/x.py exits 0')",
            (task_id,),
        )
        con.execute(
            "INSERT INTO task_gates (task_id, verification_type, evidence_shape) "
            "VALUES (?, 'semantic', 'the report names the root cause')",
            (task_id,),
        )
        con.commit()
        return task_id
    finally:
        con.close()


def test_kernel_renders_plan_task_binding_and_acceptance(tmp_path):
    db = tmp_path / "gaia.db"
    task_id = _seed_task_with_gates(db)
    kernel = build_dispatch_kernel(
        _base_row(plan_task_id=task_id), db_path=db,
    )
    assert f"plan_task_id: {task_id}" in kernel
    assert "acceptance:" in kernel
    assert "  - (command) pytest tests/x.py exits 0" in kernel
    assert "  - (semantic) the report names the root cause" in kernel


def test_cli_block_base_lines_are_verbatim():
    block = build_cli_block()
    assert block.splitlines()[0] == "# Your CLI"
    assert "  gaia context get --section <s>   # forma del workspace (apps/services/stack/git/...), a demanda" in block
    assert "  gaia context get-contract --section <s>   # contratos de contexto (project_identity, stack, ...) -- namespace de can_read/can_write" in block
    assert "  gaia memory search '<término>'   # memoria curada y episodios" in block
    assert "  gaia memory list --type <t>      # t: project|user|feedback|atom|decision|negative" in block
    assert "  gaia memory show <slug>          # cuerpo completo de una fila curada" in block
    assert "  gaia memory get-relevant --initiative <k>   # pendientes vivos de UN proyecto" in block
    assert "  gaia memory get-relevant --sections <s>      # s: carry_forward|anchor|thread_open" in block
    assert "  gaia contract view / list / validate         # tu contrato: lectura" in block
    assert "  gaia contract set / add / fill / finalize    # tu contrato: llenado incremental y cierre" in block
    assert "  gaia --help                      # todo lo demás" in block


def test_cli_block_announces_every_verb_required_by_the_contract():
    """Every verb the goal requires announced, and each one runs for real."""
    import subprocess
    import sys

    gaia_bin = _REPO_ROOT / "bin" / "gaia"
    block = build_cli_block()
    contract_lines = "\n".join(
        l for l in block.splitlines() if l.strip().startswith("gaia contract")
    )
    required_substrings = [
        "gaia context get --section",
        "gaia context get-contract --section",
        "gaia memory search",
        "gaia memory list --type",
        "gaia memory show",
        "gaia memory get-relevant --initiative",
        "gaia memory get-relevant --sections",
        "gaia --help",
    ]
    for needle in required_substrings:
        assert needle in block, f"CLI block does not announce: {needle}"

    for verb in ("view", "list", "validate", "set", "add", "fill", "finalize"):
        assert verb in contract_lines, (
            f"CLI block does not announce contract verb: {verb}"
        )

    # And each announced verb is real -- --help must not error.
    for verb in (
        ["context", "get"], ["context", "get-contract"],
        ["memory", "search"], ["memory", "list"],
        ["memory", "show"], ["memory", "get-relevant"], ["contract", "view"],
        ["contract", "list"], ["contract", "validate"], ["contract", "set"],
        ["contract", "add"], ["contract", "fill"], ["contract", "finalize"],
    ):
        result = subprocess.run(
            [sys.executable, str(gaia_bin), *verb, "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"gaia {' '.join(verb)} --help failed: {result.stderr}"


def test_cli_block_renders_frontmatter_extras(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "gaia-system.md").write_text(
        "---\n"
        "name: gaia-system\n"
        "cli:\n"
        "  - \"gaia doctor            # valida la instalación\"\n"
        "---\n"
        "# body\n",
        encoding="utf-8",
    )
    block = build_cli_block("gaia-system", agents_dir=agents_dir)
    assert "  gaia doctor            # valida la instalación" in block


def test_cli_block_without_extras_is_just_the_base(tmp_path):
    block = build_cli_block("no-such-agent", agents_dir=tmp_path)
    assert len(block.splitlines()) == 12  # heading + blank + 10 base lines


def test_cli_block_workspace_line_points_at_get_contract_not_get():
    """The workspace-scoped can_read hint must name the verb that actually
    reaches project_context_contracts (get-contract), never `get` (which
    resolves --section against the workspace shape and never reaches it)."""
    block = build_cli_block(workspace=WORKSPACE)
    assert f"gaia context get-contract --section <s> --workspace {WORKSPACE}" in block
    assert f"gaia context get --section <s> --workspace {WORKSPACE}" not in block


def _seed_memory_row(con, *, name, type_="user", audience="executor",
                      body="b", class_="anchor", updated_at="2026-01-01T00:00:00Z"):
    con.execute(
        "INSERT INTO workspaces (name, identity) VALUES (?, ?) "
        "ON CONFLICT(name) DO NOTHING",
        (WORKSPACE, WORKSPACE),
    )
    con.execute(
        "INSERT INTO memory (workspace, name, type, body, class, "
        "updated_at, audience) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (WORKSPACE, name, type_, body, class_, updated_at, audience),
    )


def test_memory_block_selects_executor_user_rows_and_inlines_the_body(tmp_path):
    db = tmp_path / "gaia.db"
    from gaia.store.writer import _connect

    con = _connect(db)
    try:
        _seed_memory_row(
            con, name="user_prefers_live_verification",
            body="Live state and code outrank memory when they disagree.",
        )
        # Same type, wrong audience -- must NOT appear (the exact defect
        # being fixed: the old query had no audience filter at all).
        _seed_memory_row(
            con, name="orchestrator_only_note", audience="orchestrator",
            body="Orchestrator-facing routing note.",
        )
        # Right audience, wrong type -- must NOT appear.
        _seed_memory_row(
            con, name="atom_not_a_user_row", type_="atom", class_="log",
            body="An atom, not a user-preference row.",
        )
        # Right type and audience, but soft-deleted -- must NOT appear.
        _seed_memory_row(
            con, name="user_prefers_plain_reports",
            body="Prefers plain-language reports over structured blocks.",
        )
        con.execute(
            "UPDATE memory SET deleted_at = '2026-01-02T00:00:00Z' "
            "WHERE workspace = ? AND name = 'user_prefers_plain_reports'",
            (WORKSPACE,),
        )
        con.commit()
    finally:
        con.close()

    block = build_memory_block(WORKSPACE, db_path=db)
    assert block.splitlines()[0] == "# How the user works"
    assert "Live state and code outrank memory when they disagree." in block
    assert "orchestrator_only_note" not in block
    assert "Orchestrator-facing routing note." not in block
    assert "An atom, not a user-preference row." not in block
    assert "Prefers plain-language reports" not in block  # soft-deleted
    # No slug is injected -- only the body.
    assert "user_prefers_live_verification" not in block


def test_memory_block_injects_a_long_body_complete_never_truncated(tmp_path):
    """A body far past the old 600-char cut is injected whole: no truncation
    mark, and the full 5000-char payload is present verbatim."""
    db = tmp_path / "gaia.db"
    from gaia.store.writer import _connect

    con = _connect(db)
    try:
        _seed_memory_row(
            con, name="user_overlong_preference", body="x" * 5000,
        )
        con.commit()
    finally:
        con.close()

    block = build_memory_block(WORKSPACE, db_path=db)
    assert "[truncated]" not in block
    assert "x" * 5000 in block


def test_memory_block_drops_a_body_past_the_hard_ceiling_whole(tmp_path):
    """A pathologically long body (over the hard ceiling) is dropped from the
    block entirely -- never sliced mid-text."""
    db = tmp_path / "gaia.db"
    from gaia.store.writer import _connect
    from modules.context.kernel_builder import _MEMORY_BODY_HARD_CEILING

    con = _connect(db)
    try:
        _seed_memory_row(
            con, name="user_pathological_preference",
            body="y" * (_MEMORY_BODY_HARD_CEILING + 1),
        )
        con.commit()
    finally:
        con.close()

    block = build_memory_block(WORKSPACE, db_path=db)
    assert block == ""
    assert "y" not in block


def test_memory_block_empty_without_matching_rows(tmp_path):
    assert build_memory_block(WORKSPACE, db_path=tmp_path / "gaia.db") == ""


def test_memory_block_empty_when_only_non_matching_rows_exist(tmp_path):
    db = tmp_path / "gaia.db"
    from gaia.store.writer import _connect

    con = _connect(db)
    try:
        _seed_memory_row(
            con, name="orchestrator_only", audience="orchestrator", body="b",
        )
        con.commit()
    finally:
        con.close()

    assert build_memory_block(WORKSPACE, db_path=db) == ""


# ---------------------------------------------------------------------------
# Kernel-axis telemetry (usar-la-telemetria-de-memoria-edad-sesgo-y-pesaje,
# task 4; formerly P1 injection telemetry from
# telemetria-de-uso-en-memoria-curada, task 6): every row rendered into "How
# the user works" is a kernel-dispatch surface. Bumps kernel_count only,
# never injection_count, never deliberate_count, never updated_at, never a
# memory_history row -- and never breaks kernel assembly if the telemetry
# write itself fails, because this block ships on EVERY subagent dispatch.
# Split off injection_count precisely because that shared counter used to be
# dominated by this fixed, every-dispatch row set (measured: 37/37/26 against
# 17 or less for everything else) -- see task 4's contract for the measurement.
# ---------------------------------------------------------------------------

def _telemetry_row(db_path, name, workspace=WORKSPACE):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT injection_count, deliberate_count, kernel_count, "
            "       last_injected_at, last_kernel_at, updated_at "
            "FROM memory WHERE workspace=? AND name=?",
            (workspace, name),
        ).fetchone())
    finally:
        con.close()


def _history_count(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM memory_history").fetchone()[0]
    finally:
        con.close()


class TestMemoryBlockKernelTelemetry:
    def test_rendered_rows_bump_kernel_only(self, tmp_path):
        db = tmp_path / "gaia.db"
        from gaia.store.writer import _connect

        con = _connect(db)
        try:
            _seed_memory_row(
                con, name="user_prefers_live_verification",
                body="Live state and code outrank memory when they disagree.",
            )
            con.commit()
        finally:
            con.close()

        before = _telemetry_row(db, "user_prefers_live_verification")
        before_history = _history_count(db)

        block = build_memory_block(WORKSPACE, db_path=db)

        after = _telemetry_row(db, "user_prefers_live_verification")
        after_history = _history_count(db)

        assert "Live state and code outrank memory" in block
        assert after["kernel_count"] == before["kernel_count"] + 1
        assert after["last_kernel_at"] is not None
        assert after["injection_count"] == before["injection_count"] == 0
        assert after["deliberate_count"] == before["deliberate_count"] == 0
        assert after["updated_at"] == before["updated_at"]
        assert after_history == before_history

    def test_row_dropped_for_wrong_audience_never_bumps(self, tmp_path):
        """A row the query candidate-selected out entirely (wrong audience)
        is not the object under test -- this pins that the telemetry loop
        only ever iterates rows that made it into ``rows``/the block, never
        a row the SQL WHERE clause already excluded."""
        db = tmp_path / "gaia.db"
        from gaia.store.writer import _connect

        con = _connect(db)
        try:
            _seed_memory_row(
                con, name="orchestrator_only", audience="orchestrator", body="b",
            )
            con.commit()
        finally:
            con.close()

        before = _telemetry_row(db, "orchestrator_only")
        build_memory_block(WORKSPACE, db_path=db)
        after = _telemetry_row(db, "orchestrator_only")

        assert after == before

    def test_body_over_hard_ceiling_is_dropped_and_never_bumped(self, tmp_path):
        """A candidate SELECTed by the query but then dropped by this
        builder (body over the hard ceiling) must not bump kernel --
        selected is not emitted, the same property the get-relevant
        renderers are held to."""
        db = tmp_path / "gaia.db"
        from gaia.store.writer import _connect
        from modules.context.kernel_builder import _MEMORY_BODY_HARD_CEILING

        con = _connect(db)
        try:
            _seed_memory_row(
                con, name="user_pathological_preference",
                body="y" * (_MEMORY_BODY_HARD_CEILING + 1),
            )
            con.commit()
        finally:
            con.close()

        before = _telemetry_row(db, "user_pathological_preference")
        block = build_memory_block(WORKSPACE, db_path=db)
        after = _telemetry_row(db, "user_pathological_preference")

        assert block == ""
        assert after == before

    def test_second_call_renders_byte_identical_block(self, tmp_path):
        db = tmp_path / "gaia.db"
        from gaia.store.writer import _connect

        con = _connect(db)
        try:
            _seed_memory_row(
                con, name="user_prefers_live_verification",
                body="Live state and code outrank memory when they disagree.",
            )
            con.commit()
        finally:
            con.close()

        first = build_memory_block(WORKSPACE, db_path=db)
        second = build_memory_block(WORKSPACE, db_path=db)
        assert first == second

    def test_degrades_when_telemetry_raises(self, tmp_path):
        """Higher stakes than a single CLI call: this block renders on
        EVERY subagent dispatch, so a telemetry defect must never surface
        as a broken kernel."""
        db = tmp_path / "gaia.db"
        from gaia.store.writer import _connect

        con = _connect(db)
        try:
            _seed_memory_row(
                con, name="user_prefers_live_verification",
                body="Live state and code outrank memory when they disagree.",
            )
            con.commit()
        finally:
            con.close()

        with mock.patch(
            "gaia.store.writer.record_memory_access",
            side_effect=RuntimeError("boom"),
        ):
            block = build_memory_block(WORKSPACE, db_path=db)

        assert "Live state and code outrank memory" in block


def test_kernel_context_joins_and_requires_identity(tmp_path):
    db = tmp_path / "gaia.db"
    full = build_kernel_context(
        _base_row(), agent_name="gaia-system",
        agents_dir=tmp_path / "agents", db_path=db,
    )
    assert full.startswith("# Your Contract")
    assert "# Your CLI" in full
    assert build_kernel_context(
        _base_row(contract_id=None), agent_name="gaia-system",
        agents_dir=tmp_path / "agents", db_path=db,
    ) is None
