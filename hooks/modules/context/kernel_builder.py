"""Dispatch kernel -- the minimal, data-only context a claimed turn starts with.

Renders the three blocks injected at SubagentStart once ``claim_dispatch_row``
has correlated the starting subagent to its born ``agent_contract_handoffs``
row:

  * ``# Your Contract``  -- identity, goal, role/surface, the project the
    dispatch ran from (v44, ``dispatch_project``) with the workflow that
    project declared in its ``project_identity`` entry, section scope, and
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
  * ``# How the user works`` -- the user's standing rows the session birth
    block carries, minus those whose ``audience`` is the orchestrator alone,
    in the same facts/preferences sections
    (``gaia.store.reader.user_anchor_rows`` rendered by
    ``modules.context.user_sections``), BODY inline, not slugs: a slug cost a
    further ``gaia memory show`` call the agent in practice never made.
    Omitted entirely when no row is selected -- never an empty heading.

``# Your skills`` is a fourth block, rendered only for a host that preloads
no skill body (OpenCode): each skill the agent's ``skills:`` frontmatter names,
with its description, so the turn knows at birth which bodies to load. Claude
Code preloads those bodies itself, so ``build_kernel_context`` never carries it.

Everything renders from the ROW (goal from ``dispatch_prompt``, scope from
``kernel_sections`` persisted at birth) plus three reads: ``task_gates`` for
the acceptance block, the dispatch project's ``project_identity`` entry for its
declared workflow, and ``memory`` for the user's rows. No other project context
is rebuilt here.

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
SKILLS_HEADING = "# Your skills"

# Claude Code caps hook-injected context at 10,000 chars, the budget a kernel is
# held to on either host; the skills block takes the share USER_ROWS_BUDGET
# takes. Inlined bodies cannot fit it: security-tiers alone is over 50,000.
SKILLS_BLOCK_BUDGET = 4_000
_SKILLS_PREAMBLE = (
    "Preloaded by your definition. Their bodies are not in this prompt: "
    "load each one with the `skill` tool before relying on it."
)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent

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
    "  gaia memory get-relevant --initiative <k> --sections anchor   # one project's standing notes, bodies included",
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


def _declared_workflow(workspace: str, dispatch_project: str, db_path=None) -> str:
    """The workflow the dispatch's project declared, as one data line, or "" when it declared none.

    A mapping renders its scalar values as ``key=value`` pairs in stored order,
    a string renders as written; whitespace runs collapse so the value stays one
    line, and anything else declares nothing renderable.
    """
    try:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from gaia.identity_shape import DECLARED_WORKFLOW_KEY
        from tools.context.context_provider import dispatch_project_entry

        entry = dispatch_project_entry(workspace, dispatch_project, db_path=db_path)
    except Exception:
        logger.debug("declared workflow read failed (non-fatal)", exc_info=True)
        return ""
    declared = (entry or {}).get(DECLARED_WORKFLOW_KEY)
    if isinstance(declared, str):
        return " ".join(declared.split())
    if isinstance(declared, dict):
        return ", ".join(
            f"{key}={' '.join(str(value).split())}" for key, value in declared.items()
            if isinstance(value, (str, int, float)) and str(value).strip()
        )
    return ""


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
        workflow = _declared_workflow(
            str(row.get("workspace") or ""), dispatch_project, db_path=db_path,
        )
        if workflow:
            parts.append(f"  workflow: {workflow}")

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


def _frontmatter(path: Path) -> dict:
    """The Markdown file's frontmatter mapping, or {} when the file or its parse is missing."""
    if not path.is_file():
        return {}
    try:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from tools.scan.seed_contract_permissions import _parse_frontmatter

        frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("frontmatter parse failed (non-fatal)", exc_info=True)
        return {}
    return frontmatter if isinstance(frontmatter, dict) else {}


def _agent_frontmatter(agent_name: str, agents_dir: "Path | None") -> dict:
    """The named agent's frontmatter, or {} for a name that is not a bare file stem."""
    if not agent_name or Path(agent_name).name != agent_name:
        return {}
    return _frontmatter((agents_dir or _PACKAGE_ROOT / "agents") / f"{agent_name}.md")


def _agent_cli_extras(agent_name: str, agents_dir: "Path | None") -> list:
    """Per-role CLI lines declared in the agent's frontmatter (``cli:`` key).

    Same declaration pattern as ``routing:``: the agent's ``.md`` frontmatter
    is the source of truth. Entries are plain strings rendered verbatim
    (indented). Missing file / key / parser -> no extras.
    """
    extras = _agent_frontmatter(agent_name, agents_dir).get("cli")
    if not isinstance(extras, list):
        return []
    return [f"  {str(e).strip()}" for e in extras if str(e).strip()]


def build_skills_block(
    agent_name: str, *, agents_dir: "Path | None" = None,
    skills_dir: "Path | None" = None,
) -> str:
    """Render ``# Your skills`` from the agent's ``skills:`` frontmatter, or "" when it names none.

    Each skill is listed with its description; when that would exceed
    ``SKILLS_BLOCK_BUDGET`` the names alone are listed, so none drops out.
    """
    declared = _agent_frontmatter(agent_name, agents_dir).get("skills")
    if not isinstance(declared, list):
        return ""
    names = [str(name).strip() for name in declared if str(name).strip()]
    if not names:
        return ""
    skills_dir = skills_dir or _PACKAGE_ROOT / "skills"
    head = [SKILLS_HEADING, "", _SKILLS_PREAMBLE, ""]
    described = []
    for name in names:
        description = " ".join(
            str(_frontmatter(skills_dir / name / "SKILL.md").get("description") or "").split()
        )
        described.append(f"- {name}: {description}" if description else f"- {name}")
    block = "\n".join(head + described)
    if len(block) <= SKILLS_BLOCK_BUDGET:
        return block
    return "\n".join(head + [f"- {name}" for name in names])


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


def _record_kernel_telemetry(
    rows: list, *, db_path=None,
) -> None:
    """Best-effort kernel-axis bump for the ``(workspace, name)`` rows rendered
    into the kernel's "How the user works" block. Reuses the same store-layer helper the
    get-relevant surfaces use (``gaia.store.writer.record_memory_access``);
    never a second implementation. Bumps the ``"kernel"`` axis
    (``kernel_count``/``last_kernel_at``), NOT ``"injection"``: this block
    fires on EVERY subagent dispatch over the same fixed rows (the user's
    standing rows), which used to dominate the
    injection axis by construction (measured: the kernel's rows led any
    injection ranking, 37/37/26 against 17 or less for everything else).
    Splitting it into its own axis is forward-only -- what it already added
    to ``injection_count`` before this split stays there, unmoved. Every
    failure mode is swallowed here, on top of ``record_memory_access``'s own
    internal best-effort contract -- this block ships on EVERY dispatch, so a
    telemetry defect must never surface as a broken kernel, unlike a single
    CLI invocation.
    """
    if not rows:
        return
    try:
        from gaia.store.writer import record_memory_access
    except ImportError:
        return
    for row_workspace, name in rows:
        try:
            record_memory_access(row_workspace, name, "kernel", db_path=db_path)
        except Exception:
            logger.debug(
                "memory kernel telemetry failed (non-fatal)", exc_info=True,
            )


def build_memory_block(*, db_path=None) -> str:
    """Render ``# How the user works`` from the user's standing rows, or "" when there are none.

    Whatever workspace the dispatch ran from: user memory belongs to no
    workspace. The rows are the session birth block's minus those addressed
    only to the orchestrator, which a specialist has no use for.
    """
    from gaia.store.reader import user_anchor_rows

    from .user_sections import render_user_sections

    rows = [
        r for r in user_anchor_rows(db_path, audience="executor")
        if (r.get("body") or "").strip()
    ]
    sections = render_user_sections(rows)
    if not sections:
        return ""
    _record_kernel_telemetry(
        [(row["workspace"], row["name"]) for row in rows], db_path=db_path,
    )
    return f"{MEMORY_HEADING}\n\n{sections}"


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
        build_memory_block(db_path=db_path),
    ]
    return "\n\n".join(b for b in blocks if b)
