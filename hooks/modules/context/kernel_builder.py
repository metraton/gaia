"""Dispatch kernel -- the minimal, data-only context a claimed turn starts with.

Renders the three blocks injected at SubagentStart once ``claim_dispatch_row``
has correlated the starting subagent to its born ``agent_contract_handoffs``
row:

  * ``# Your Contract``  -- identity, goal, role/surface, the project the
    dispatch ran from (v44, ``dispatch_project``), section scope, and
    (for a plan-task-bound turn) the task's acceptance gates. DATA ONLY: the
    block carries no instructions; the pedagogy lives in the agent-protocol
    skill, which documents every field.
  * ``# Your CLI``       -- the commands through which the turn pulls project
    context, memory (search/list/show/get-relevant -- open to every subagent
    regardless of role), and its own contract (both the read verbs --
    view/list/validate -- and the incremental-fill ones --
    set/add/fill/finalize) ON DEMAND (nothing is precargado beyond these
    blocks in the target architecture). This is the INDEX of what the turn
    can look up on its own -- a capability the turn already has but that is
    not announced here does not exist for the turn in practice. Per-role
    extras are declared in the agent's frontmatter (``cli:`` key, same
    declaration pattern as ``routing:``) and rendered verbatim when present,
    additive to the base lines. Declaring is NOT permitting: tiers and
    guards still gate every execution.
  * ``# How the user works`` -- the durable, executor-facing user-preference
    rows (``memory.type='user' AND memory.audience='executor'``), BODY
    inline, not slugs: a slug cost a further ``gaia memory show`` call the
    agent in practice never made. Omitted entirely when the query returns no
    rows -- never an empty heading.

Everything renders from the ROW (goal from ``dispatch_prompt``, scope from
``kernel_sections`` persisted at birth) plus two scoped reads: ``task_gates``
for the acceptance block and ``memory`` for the executor-facing user rows. No
project context is rebuilt here.

Gotchas:
  * ``kernel_sections`` arrives as a JSON string on the row; this module
    parses it defensively (a malformed value degrades to an empty scope,
    never a crash).
  * Every builder is fail-safe by contract: a DB read or frontmatter parse
    failure degrades to a smaller block, because SubagentStart must never
    fail on account of kernel rendering.
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

KERNEL_HEADING = "# Your Contract"
CLI_HEADING = "# Your CLI"
MEMORY_HEADING = "# How the user works"

# Wrap width for the goal text -- readability only, never truncation.
_GOAL_WRAP_WIDTH = 96
_GOAL_INDENT = "  "

# The base CLI lines every turn receives, verbatim (user-approved text).
# Every verb/flag here is executable as written -- checked against the real
# CLI, not just its --help text -- because this block is the index of what
# the turn can look up on its own: what it does not announce, for the turn
# does not exist. Memory reads are open to any subagent regardless of role;
# nothing here is orchestrator-only.
#
# Line 0 (`get`) and line 1 (`get-contract`) resolve --section against two
# DIFFERENT namespaces -- the fixed workspace shape (apps/services/stack/
# git/...) vs project_context_contracts.contract_name -- and can_read/
# can_write (in `# Your Contract`) names the SECOND one. Do not collapse
# them back into one line: that collapse is exactly the bug this pair fixes
# (see bin/cli/context.py's module docstring).
_CLI_BASE_LINES = (
    "  gaia context get --section <s>   # forma del workspace (apps/services/stack/git/...), a demanda",
    "  gaia context get-contract --section <s>   # contratos de contexto (project_identity, stack, ...) -- namespace de can_read/can_write",
    "  gaia memory search '<término>'   # memoria curada y episodios",
    "  gaia memory list --type <t>      # t: project|user|feedback|atom|decision|negative",
    "  gaia memory show <slug>          # cuerpo completo de una fila curada",
    "  gaia memory get-relevant --initiative <k>   # pendientes vivos de UN proyecto",
    "  gaia memory get-relevant --sections <s>      # s: carry_forward|anchor|thread_open",
    "  gaia contract view / list / validate         # tu contrato: lectura",
    "  gaia contract set / add / fill / finalize    # tu contrato: llenado incremental y cierre",
    "  gaia --help                      # todo lo demás",
)

# Rendered right after the get-contract base line when the dispatch workspace
# is known: the workspace-scoped, concrete form of `gaia context get-contract`
# -- the actual command that reaches can_read/can_write, not `get` (which
# never resolves a contract name; see the base-lines comment above). NOTE
# deliberately --workspace, not --project: the CLI has no --project flag --
# contracts live per WORKSPACE in project_context_contracts, the default
# workspace resolves from the caller's current directory
# (gaia.project.current()), and projects are entries inside the workspace's
# project_identity contract. can_read (in # Your Contract) is the menu of
# contract names the turn may pull with this exact command.
_CLI_WORKSPACE_LINE = (
    "  gaia context get-contract --section <s> --workspace {workspace}"
    "   # tus secciones legibles: can_read"
)

# Defensive ceilings for the "How the user works" block. Two different
# risks, two different guards: an unwatched SET growing (many rows) is
# bounded by _MEMORY_ROW_LIMIT; a single pathological ROW is bounded by
# _MEMORY_BODY_HARD_CEILING. The row limit still slices the set (a row over
# the limit simply never gets read). The body ceiling is deliberately NOT a
# mid-text cut: a body is injected whole or not at all, never truncated --
# truncating pays the token cost of the row and still forces a follow-up
# `gaia memory show` for what got cut, which is strictly worse than either
# extreme. A body over the ceiling is dropped from the block entirely rather
# than sliced, because a silent truncation was measured eating INSTRUCTION,
# not just context, in a row that legitimately needed every character.
# 3 rows exist today totalling ~2500 chars; the ceiling is sized for a
# runaway row, not the expected case.
_MEMORY_ROW_LIMIT = 20
_MEMORY_BODY_HARD_CEILING = 20_000


def _connect(db_path):
    """Open a read connection through the store's own connect helper (same
    schema-materialization and FK contract as every other hook-side reader)."""
    try:
        from gaia.store.reader import _connect as _reader_connect
    except ImportError:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from gaia.store.reader import _connect as _reader_connect
    return _reader_connect(db_path)


def _parse_json_field(value: Any) -> Any:
    """Parse a row's JSON column defensively; anything unparseable -> None."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _wrap_goal(prompt: str) -> str:
    """Render ``goal:`` with the dispatch prompt wrapped, newlines preserved.

    The first physical line rides after the ``goal: `` label; every subsequent
    physical (and wrapped) line is indented so the goal reads as one visually
    contiguous field.
    """
    lines: list[str] = []
    for raw_line in prompt.splitlines() or [""]:
        if not raw_line.strip():
            lines.append("")
            continue
        lines.extend(
            textwrap.wrap(raw_line, width=_GOAL_WRAP_WIDTH) or [raw_line]
        )
    if not lines:
        lines = [""]
    first, rest = lines[0], lines[1:]
    rendered = [f"goal: {first}"]
    rendered.extend(f"{_GOAL_INDENT}{line}" if line else "" for line in rest)
    return "\n".join(rendered)


def _acceptance_lines(plan_task_id: int, db_path=None) -> list:
    """One line per task gate, read via the writer's own gate SELECT (SSOT)."""
    try:
        from gaia.store.writer import _read_task_gate_rows
    except ImportError:
        return []
    try:
        con = _connect(db_path)
        try:
            gates = _read_task_gate_rows(con, plan_task_id)
        finally:
            con.close()
    except Exception:
        logger.debug("acceptance gate read failed (non-fatal)", exc_info=True)
        return []
    lines = []
    for gate in gates:
        shape = (
            gate.get("evidence_shape")
            or gate.get("evidence_type")
            or gate.get("artifact_path")
            or ""
        )
        lines.append(f"  - ({gate.get('verification_type')}) {shape}".rstrip())
    return lines


def build_dispatch_kernel(
    row: Mapping[str, Any], *, db_path=None,
) -> Optional[str]:
    """Render the ``# Your Contract`` block from a claimed dispatch row.

    ``row`` is the dict :func:`gaia.store.writer.claim_dispatch_row` returns
    (``kernel_sections`` still a JSON string). Returns None when the row lacks
    either identity half -- a caller can splice the result unconditionally and
    a broken row degrades to no kernel rather than a malformed one.

    Deliberately ABSENT, by user decision: ``required_checks`` (method belongs
    to the role skills; exact commands are validated on the OUTPUT contract by
    the gate) and ``workspace`` (kept as a row COLUMN for scoping and audit,
    never injected). And zero instructions: data only.
    """
    contract_id = row.get("contract_id") or ""
    agent_id = row.get("agent_id") or ""
    if not contract_id or not agent_id:
        return None

    sections = _parse_json_field(row.get("kernel_sections")) or {}
    can_read = sections.get("can_read") or []
    can_write = sections.get("can_write") or []

    parts = [
        KERNEL_HEADING,
        "",
        f"contract_id: {contract_id}",
        f"agent_id:    {agent_id}",
        "",
        _wrap_goal(str(row.get("dispatch_prompt") or "")),
        "",
        f"role: {sections.get('role') or ''}".rstrip(),
        f"surface: {sections.get('surface') or ''}".rstrip(),
    ]

    # v44: the project the dispatch ran from ("name (/abs/path)"), resolved at
    # birth. A datum of the assignment -- which project to pull context for --
    # not the orchestrator's routing reasoning. Omitted when the birth resolved
    # no project.
    dispatch_project = row.get("dispatch_project")
    if dispatch_project:
        parts.append(f"project: {dispatch_project}")

    plan_task_id = row.get("plan_task_id")
    if plan_task_id is not None:
        parts.append(f"plan_task_id: {plan_task_id}")

    parts.extend([
        "",
        f"can_read:  [{', '.join(str(s) for s in can_read)}]",
        f"can_write: [{', '.join(str(s) for s in can_write)}]",
    ])

    if plan_task_id is not None:
        acceptance = _acceptance_lines(plan_task_id, db_path=db_path)
        if acceptance:
            parts.extend(["", "acceptance:"])
            parts.extend(acceptance)

    return "\n".join(parts)


def _agent_cli_extras(agent_name: str, agents_dir: "Path | None") -> list:
    """Per-role CLI lines declared in the agent's frontmatter (``cli:`` key).

    Same declaration pattern as ``routing:``: the agent's ``.md`` frontmatter
    is the source of truth. Entries are plain strings rendered verbatim
    (indented). Missing file / key / parser -> no extras.
    """
    if not agent_name:
        return []
    if agents_dir is None:
        agents_dir = Path(__file__).resolve().parent.parent.parent.parent / "agents"
    agent_file = agents_dir / f"{agent_name}.md"
    if not agent_file.is_file():
        return []
    try:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from tools.scan.seed_contract_permissions import _parse_frontmatter

        frontmatter = _parse_frontmatter(agent_file.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("agent frontmatter parse failed (non-fatal)", exc_info=True)
        return []
    extras = frontmatter.get("cli") if isinstance(frontmatter, dict) else None
    if not isinstance(extras, list):
        return []
    return [f"  {str(e).strip()}" for e in extras if str(e).strip()]


def build_cli_block(
    agent_name: str = "", *, agents_dir: "Path | None" = None,
    workspace: str = "",
) -> str:
    """Render ``# Your CLI``: the base lines plus any per-role extras.

    When ``workspace`` is known (the row's own column), the workspace-scoped
    ``get-contract`` example is rendered concrete right after the generic
    ``get``/``get-contract`` pair -- the REAL syntax for scoping a pull,
    since the CLI has no ``--project`` flag (see ``_CLI_WORKSPACE_LINE``).
    """
    lines = [CLI_HEADING, "", _CLI_BASE_LINES[0], _CLI_BASE_LINES[1]]
    if workspace:
        lines.append(_CLI_WORKSPACE_LINE.format(workspace=workspace))
    lines.extend(_CLI_BASE_LINES[2:])
    lines.extend(_agent_cli_extras(agent_name, agents_dir))
    return "\n".join(lines)


def _executor_user_bodies(workspace: str, db_path=None) -> list:
    """Durable executor-facing user-preference rows, freshest first, bounded.

    Selects exactly ``type='user' AND audience='executor'`` for the
    workspace -- not ``class='anchor'`` (which mixed in anchors from
    unrelated projects sharing the same workspace). Returns ``name`` plus
    ``body`` -- the name never renders in the block (that would cost a
    further ``gaia memory show`` call the agent in practice never made) but
    is needed so the kernel-axis telemetry in ``build_memory_block`` can
    bump exactly the rows that make it into the block, never a candidate
    filtered out below. A body is injected whole; one over
    ``_MEMORY_BODY_HARD_CEILING`` is dropped entirely instead of sliced --
    see the ceiling's own comment for why.
    """
    if not workspace:
        return []
    try:
        con = _connect(db_path)
        try:
            rows = con.execute(
                "SELECT name, body FROM memory "
                "WHERE workspace = ? AND type = 'user' AND audience = 'executor' "
                "AND deleted_at IS NULL "
                "ORDER BY updated_at DESC LIMIT ?",
                (workspace, _MEMORY_ROW_LIMIT),
            ).fetchall()
        finally:
            con.close()
    except Exception:
        logger.debug("executor-user memory read failed (non-fatal)", exc_info=True)
        return []

    kept = []
    for row in rows:
        body = (row["body"] or "").strip()
        if not body:
            continue
        if len(body) > _MEMORY_BODY_HARD_CEILING:
            logger.warning(
                "executor-user memory body exceeds hard ceiling "
                "(%d > %d chars); dropped from the kernel rather than "
                "truncated",
                len(body), _MEMORY_BODY_HARD_CEILING,
            )
            continue
        kept.append({"name": row["name"], "body": body})
    return kept


def _record_kernel_telemetry(
    workspace: str, names: list, *, db_path=None,
) -> None:
    """Best-effort kernel-axis bump for rows rendered into the kernel's
    "How the user works" block. Reuses the same store-layer helper the
    get-relevant surfaces use (``gaia.store.writer.record_memory_access``);
    never a second implementation. Bumps the ``"kernel"`` axis
    (``kernel_count``/``last_kernel_at``), NOT ``"injection"``: this block
    fires on EVERY subagent dispatch over the same fixed rows
    (``type=user AND audience=executor``), which used to dominate the
    injection axis by construction (measured: the kernel's rows led any
    injection ranking, 37/37/26 against 17 or less for everything else).
    Splitting it into its own axis is forward-only -- what it already added
    to ``injection_count`` before this split stays there, unmoved. Every
    failure mode is swallowed here, on top of ``record_memory_access``'s own
    internal best-effort contract -- this block ships on EVERY dispatch, so a
    telemetry defect must never surface as a broken kernel, unlike a single
    CLI invocation.
    """
    if not names:
        return
    try:
        from gaia.store.writer import record_memory_access
    except ImportError:
        return
    for name in names:
        try:
            record_memory_access(workspace, name, "kernel", db_path=db_path)
        except Exception:
            logger.debug(
                "memory kernel telemetry failed (non-fatal)", exc_info=True,
            )


def build_memory_block(workspace: str, *, db_path=None) -> str:
    """Render ``# How the user works``, or "" when no matching rows exist."""
    rows = _executor_user_bodies(workspace, db_path=db_path)
    if not rows:
        return ""
    lines = [MEMORY_HEADING, ""]
    for index, row in enumerate(rows):
        if index:
            lines.append("")
        body_lines = row["body"].splitlines() or [""]
        lines.append(f"- {body_lines[0]}")
        lines.extend(f"  {line}" if line else "" for line in body_lines[1:])
    block = "\n".join(lines)
    _record_kernel_telemetry(
        workspace, [row["name"] for row in rows], db_path=db_path,
    )
    return block


def build_kernel_context(
    row: Mapping[str, Any],
    *,
    agent_name: str = "",
    agents_dir: "Path | None" = None,
    db_path=None,
) -> Optional[str]:
    """The full kernel payload: contract + CLI + memory, blank-line joined.

    Returns None when the contract block itself cannot render (no identity on
    the row) -- the CLI/memory blocks never ship without it.
    """
    kernel = build_dispatch_kernel(row, db_path=db_path)
    if not kernel:
        return None
    workspace = str(row.get("workspace") or "")
    blocks = [
        kernel,
        build_cli_block(agent_name, agents_dir=agents_dir, workspace=workspace),
        build_memory_block(workspace, db_path=db_path),
    ]
    return "\n\n".join(b for b in blocks if b)
