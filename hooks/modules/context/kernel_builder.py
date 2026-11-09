"""Dispatch kernel -- the minimal, data-only context a claimed turn starts with.

Renders the blocks injected at SubagentStart once ``claim_dispatch_row``
has correlated the starting subagent to its born ``agent_contract_handoffs``
row:

  * ``# Your Contract``  -- identity, goal, role/surface, the project the
    dispatch ran from (``dispatch_project``) with the workflow that
    project declared in its ``project_identity`` entry and the date that
    entry's row was last written, section scope, and
    (for a plan-task-bound turn) the task's acceptance gates. DATA ONLY: the
    block carries no instructions; the pedagogy lives in the agent-protocol
    skill, which documents every field.
  * ``# Your CLI``       -- the commands through which the turn pulls project
    context, memory (open to every subagent regardless of role) and fills its
    own contract, the contract verbs written with the turn's ``contract_id``.
    It is the index of what the turn can look up on its own and is never cut.
    Per-role extras are declared in the agent's frontmatter (``cli:`` key) and
    rendered verbatim after the base lines. Declaring is NOT permitting: tiers
    and guards still gate every execution.
  * ``# Scope preferences: <ws> > [<group> >] <project> (N of N)`` -- every
    live preference the dispatch's workspace, group and project hold, as a
    title and its ``ref=memory:...`` coordinate marked
    ``scope=workspace|group|project``; never cut. The user's own rows never
    reach a kernel: the preferences that touch this output travel in the goal.
  * ``# Scope memory: <chain>`` -- one line counting the chain's facts and
    lessons and the project's other standing notes, none of them preloaded,
    with the commands that read them. Both headings are omitted when they
    would be empty.
  * ``# Environment``    -- the session's environment line (OS, host, gaia CLI
    path, tools on PATH) and up to ``KERNEL_TOOL_LINES`` of the user's
    ``tool=`` lines.

``# Your skills`` is a further block, rendered only for a host that preloads
no skill body (OpenCode): each skill the agent's ``skills:`` frontmatter names,
with its description, so the turn knows at birth which bodies to load. Claude
Code preloads those bodies itself, so ``build_kernel_context`` carries it only
``with_skills``.

Everything renders from the ROW (goal from ``dispatch_prompt``, scope from
``kernel_sections`` persisted at birth) plus store reads: ``task_gates`` for
the acceptance block, the dispatch project's ``project_identity`` entry for its
declared workflow, its ``can_read`` sections for ``# Project context``,
``memory`` for the scope rows and standing notes, and the user's tool rows for
``# Environment``.

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
import shlex
import textwrap
from pathlib import Path
from typing import Any, Mapping, Optional

from .user_sections import CONTEXT_BUDGET, tool_lines

logger = logging.getLogger(__name__)

KERNEL_HEADING = "# Your Contract"
CLI_HEADING = "# Your CLI"
PREFERENCES_HEADING = "# Scope preferences"
MEMORY_HEADING = "# Scope memory"
SKILLS_HEADING = "# Your skills"
ENVIRONMENT_HEADING = "# Environment"

PROJECT_CONTEXT_HEADING = "# Project context"


def _section_pointer(section: str, workspace: str, project: str) -> str:
    args = " ".join(("--workspace", shlex.quote(workspace), "--project", shlex.quote(project)))
    return f"`gaia context get-contract --section {section} {args}`"


KERNEL_BUDGET = CONTEXT_BUDGET
# Inlined skill bodies cannot fit KERNEL_BUDGET: security-tiers alone is over 50,000.
SKILLS_BLOCK_BUDGET = 4_000
_SKILLS_PREAMBLE = (
    "Preloaded by your definition. Their bodies are not in this prompt: "
    "load each one with the `skill` tool before relying on it."
)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Wrap width for the goal text -- readability only, never truncation.
_GOAL_WRAP_WIDTH = 96
_GOAL_INDENT = "  "

# Every line must run as written once its <placeholders> are filled: this block
# is the index of what the turn can look up alone, and it is never cut.
_CLI_CONTEXT_LINE = (
    "  gaia context get-contract --section <s>   # one context section; <s> is a name from can_read"
)
_CLI_WORKSPACE_LINE = (
    "  gaia context get-contract --section <s> --workspace {workspace}"
    "   # the same section as your workspace holds it"
)
_CLI_LINES = (
    "  gaia memory search '<term>'      # curated memory and episodes",
    "  gaia memory show <slug>          # one curated row, body whole; there is no `memory get`",
    "  gaia memory list --type <t>      # t: project|user|feedback|atom|decision|negative",
    "  gaia memory get-relevant --initiative <k>   # one project's live pendings",
    "  gaia memory get-relevant --initiative <k> --sections anchor   # one project's standing notes, bodies included",
    "  gaia memory get-relevant --sections <s>      # s: carry_forward|anchor|thread_open",
    "  gaia contract view --draft-id {draft}   # your contract as it stands; also list, validate",
    "  gaia contract set --draft-id {draft} <dotted.field> <value>   # replace one field; <value> is read as JSON when it parses",
    "  gaia contract add --draft-id {draft} <dotted.list.field> <value>   # append one entry to a list",
    "  gaia contract fill --draft-id {draft} --json '<object>'   # deep-merge; writes a list only while it is empty",
    "  gaia contract fill --draft-id {draft} --json-file -   # the same, the object read from a quoted heredoc",
    "  gaia contract finalize --draft-id {draft}   # last, once agent_status.agent_state holds the closing state",
    "  gaia --help                      # everything else",
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


def unwrap_injected_prompt(prompt: str) -> str:
    """The instruction *prompt* carried before kernels were injected into it, every layer peeled.

    A prompt that already holds a rendered ``# Your Contract`` block (heading
    alone on its line, followed by ``contract_id:`` and ``agent_id:``) is one
    this builder wrapped; its ``goal:`` field is the instruction underneath.
    Prose that merely mentions the heading is returned unchanged.
    """
    current = prompt
    while True:
        lines = current.split("\n")
        start = next(
            (
                i for i, line in enumerate(lines[:-4])
                if line == KERNEL_HEADING
                and lines[i + 2].startswith("contract_id: ")
                and lines[i + 3].startswith("agent_id:")
                and lines[i + 5].startswith("goal: ")
            ),
            None,
        )
        if start is None:
            return current
        goal_at = start + 5
        end = next(
            (i for i in range(goal_at + 1, len(lines)) if lines[i].startswith("role:")),
            len(lines),
        )
        goal = [lines[goal_at][len("goal: "):]]
        goal.extend(
            line[len(_GOAL_INDENT):] if line.startswith(_GOAL_INDENT) else line
            for line in lines[goal_at + 1:end]
        )
        while goal and not goal[-1].strip():
            goal.pop()
        current = "\n".join(goal)


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
    """The ``workflow (as of <date>):`` line of the dispatch's project, or "" when it declared none.

    A mapping renders its scalar values as ``key=value`` pairs in stored order,
    a string renders as written; whitespace runs collapse so the value stays one
    line, and anything else declares nothing renderable. The date is the day the
    ``project_identity`` row holding the declaration was last written.
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
    if isinstance(declared, dict):
        declared = ", ".join(
            f"{key}={' '.join(str(value).split())}" for key, value in declared.items()
            if isinstance(value, (str, int, float)) and str(value).strip()
        )
    if not isinstance(declared, str) or not declared.strip():
        return ""
    written = _project_identity_written(workspace, dispatch_project, db_path) or "unknown date"
    return f"  workflow (as of {written}): {' '.join(declared.split())}"


def _project_identity_written(workspace: str, dispatch_project: str, db_path=None) -> str:
    """The day (``YYYY-MM-DD``) the dispatch project's ``project_identity`` row was last written, or ""."""
    try:
        from gaia.store.project_context import project_sections, resolve_project

        con = _connect(db_path)
        try:
            project = resolve_project(con, dispatch_project, workspace or None)
            stored = project_sections(con, project).get("project_identity") if project else None
        finally:
            con.close()
    except Exception:
        logger.debug("project_identity date read failed (non-fatal)", exc_info=True)
        return ""
    return str((stored or {}).get("updated_at") or "")[:10]


def build_dispatch_kernel(
    row: Mapping[str, Any], *, db_path=None, agent_name: str = "",
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

    # The project the dispatch ran from ("name (/abs/path)"), resolved at
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
            parts.append(workflow)

    plan_task_id = row.get("plan_task_id")
    if plan_task_id is not None:
        parts.append(f"plan_task_id: {plan_task_id}")

    from modules.agents.call_ceiling import effective_ceiling, notice_threshold

    ceiling = effective_ceiling(row, agent_name)
    if ceiling:
        parts.append(
            f"ceiling: {ceiling} tool calls (close notice at call {notice_threshold(ceiling)})"
        )

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


def agent_frontmatter(agent_name: str, agents_dir: "Path | None") -> dict:
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
    extras = agent_frontmatter(agent_name, agents_dir).get("cli")
    if not isinstance(extras, list):
        return []
    return [f"  {str(e).strip()}" for e in extras if str(e).strip()]


def build_skills_block(
    agent_name: str, *, agents_dir: "Path | None" = None,
    skills_dir: "Path | None" = None, names_only: bool = False,
) -> str:
    """Render ``# Your skills`` from the agent's ``skills:`` frontmatter, or "" when it names none.

    Each skill is listed with its description; when that would exceed
    ``SKILLS_BLOCK_BUDGET``, or ``names_only``, the names alone are listed.
    """
    declared = agent_frontmatter(agent_name, agents_dir).get("skills")
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
    if len(block) <= SKILLS_BLOCK_BUDGET and not names_only:
        return block
    return "\n".join(head + [f"- {name}" for name in names])


def build_cli_block(
    agent_name: str = "", *, agents_dir: "Path | None" = None,
    workspace: str = "", contract_id: str = "",
) -> str:
    """Render ``# Your CLI``: the base lines plus any per-role extras.

    A known ``workspace`` adds the workspace-scoped ``get-contract`` line, and a
    known ``contract_id`` is written into every ``gaia contract`` line in place
    of ``<contract_id>``.
    """
    lines = [CLI_HEADING, "", _CLI_CONTEXT_LINE]
    if workspace:
        lines.append(_CLI_WORKSPACE_LINE.format(workspace=workspace))
    lines.extend(line.format(draft=contract_id or "<contract_id>") for line in _CLI_LINES)
    lines.extend(_agent_cli_extras(agent_name, agents_dir))
    return "\n".join(lines)


def _record_kernel_telemetry(
    rows: list, *, db_path=None,
) -> None:
    """Best-effort kernel-axis bump for the ``(workspace, name)`` rows rendered
    into the kernel's "Scope memory" block. Reuses the same store-layer helper the
    get-relevant surfaces use (``gaia.store.writer.record_memory_access``);
    never a second implementation. Bumps the ``"kernel"`` axis
    (``kernel_count``/``last_kernel_at``), NOT ``"injection"``: this block
    fires on EVERY subagent dispatch over the same standing scope rows, which
    would dominate the injection axis by construction. Every
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


def _scope_chain(row: Mapping[str, Any], *, db_path=None) -> tuple:
    """``(label, workspace, group_key, project_key)`` of the dispatch's scope chain; the label reads ``ws > group > project``."""
    workspace = str(row.get("workspace") or "") or None
    dispatch_project = str(row.get("dispatch_project") or "")
    group_name = project_name = project_key = None
    if dispatch_project and dispatch_project != "none":
        try:
            from ..core.paths import ensure_package_root_importable

            ensure_package_root_importable()
            from gaia.store.project_context import resolve_project
            from tools.context.context_provider import dispatch_project_key

            con = _connect(db_path)
            try:
                project = resolve_project(con, dispatch_project, workspace)
                if project:
                    project_name, workspace = project["name"], project["workspace"]
                    found = con.execute(
                        "SELECT group_name FROM projects WHERE workspace = ? AND name = ?",
                        (workspace, project_name),
                    ).fetchone()
                    group_name = found["group_name"] if found else None
            finally:
                con.close()
            project_key = dispatch_project_key(workspace or "", dispatch_project, db_path=db_path)
        except Exception:
            logger.warning("scope chain read failed", exc_info=True)
            raise
    from gaia.store.writer import normalize_initiative

    group_key = normalize_initiative(group_name) if group_name else None
    label = " > ".join(p for p in (workspace, group_name, project_name or (dispatch_project or None)) if p)
    return label, workspace, group_key, project_key


def build_memory_block(row: Mapping[str, Any] = None, *, db_path=None) -> str:
    """Render the scope chain's memory: its preferences whole, the rest as one counted pointer line; "" for an empty scope.

    ``# Scope preferences: <chain> (N of N)`` lists every live preference of the
    dispatch's workspace, group and project and is never cut; only those rows
    count a kernel access. ``# Scope memory: <chain>`` counts the facts, lessons
    and other standing notes that are not preloaded and names the commands that
    read them. A read that fails is logged and rendered as the one line
    ``scope memory unavailable: <error class>``, never as an empty scope.
    """
    try:
        return _scope_memory_block(row, db_path=db_path)
    except Exception as exc:
        logger.warning("scope memory unavailable: %s", exc, exc_info=True)
        return f"scope memory unavailable: {type(exc).__name__}"


def _not_preloaded_line(counts: dict, workspace: Optional[str], project_key: Optional[str]) -> str:
    """``facts N, lessons N, other notes N — not preloaded:`` and the commands that read them; "" when every count is zero."""
    counted = ", ".join(f"{label} {count}" for label, count in counts.items() if count)
    if not counted:
        return ""
    scope = f" --workspace {shlex.quote(workspace)}" if workspace else ""
    verbs = [f"`gaia memory search '<term>'{scope}`", "`gaia memory show <slug>`"]
    if project_key:
        verbs.append(
            f"this project's, bodies whole: `gaia memory get-relevant --initiative {shlex.quote(project_key)} --sections anchor`"
        )
    return f"{counted} — not preloaded: {'; '.join(verbs)}"


def _scope_memory_block(row: Mapping[str, Any] = None, *, db_path=None) -> str:
    from gaia.store.reader import anchor_index_by_project, scope_memory_rows

    from .user_sections import memory_line

    label, workspace, group_key, project_key = _scope_chain(row or {}, db_path=db_path)
    rows = scope_memory_rows(workspace, group_key, project_key, db_path)
    preferences = [r for r in rows if r["kind"] == "preference"]
    in_scope = {(r["workspace"], r["name"]) for r in rows}
    notes = anchor_index_by_project([project_key], db_path) if project_key else []
    pointer = _not_preloaded_line(
        {
            "facts": sum(r["kind"] == "fact" for r in rows),
            "lessons": sum(r["kind"] == "lesson" for r in rows),
            "other notes": sum((n["workspace"], n["name"]) not in in_scope for n in notes),
        },
        workspace, project_key,
    )
    blocks = []
    if preferences:
        count = len(preferences)
        heading = f"{PREFERENCES_HEADING}: {label} ({count} of {count})"
        blocks.append("\n".join([heading, *(memory_line(r, scope=True) for r in preferences)]))
    if pointer:
        blocks.append(f"{MEMORY_HEADING}: {label}\n{pointer}")
    _record_kernel_telemetry(
        [(r["workspace"], r["name"]) for r in preferences], db_path=db_path,
    )
    return "\n\n".join(blocks)


def _readable_project_sections(row: Mapping[str, Any], *, db_path=None) -> tuple:
    """The dispatch project's ``{identity, workspace, name}`` and the non-empty ``(section, payload)`` pairs of its ``can_read``, in that order."""
    dispatch_project = row.get("dispatch_project")
    sections = _parse_json_field(row.get("kernel_sections"))
    can_read = sections.get("can_read") if isinstance(sections, dict) else None
    if not dispatch_project or not can_read:
        return {}, []
    try:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from gaia.store.project_context import project_sections, resolve_project

        con = _connect(db_path)
        try:
            project = resolve_project(con, dispatch_project, str(row.get("workspace") or "") or None)
            stored = project_sections(con, project) if project else {}
        finally:
            con.close()
    except Exception:
        logger.debug("project context read failed (non-fatal)", exc_info=True)
        return {}, []
    readable = [
        (str(name), stored[name]["payload"]) for name in can_read
        if name in stored and stored[name]["payload"]
    ]
    return (project if readable else {}), readable


def _readable_group_sections(row: Mapping[str, Any], *, db_path=None) -> list:
    """``[(member, [(section, chars)])]`` of each member of the group the dispatch names, over the ``can_read`` sections it holds itself."""
    group = str(row.get("dispatch_project") or "")
    sections = _parse_json_field(row.get("kernel_sections"))
    can_read = sections.get("can_read") if isinstance(sections, dict) else None
    if not group or not can_read:
        return []
    try:
        from ..core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from gaia.store.project_context import held_sections, resolve_group

        con = _connect(db_path)
        try:
            members = resolve_group(con, group, str(row.get("workspace") or "") or None)
            held = [(member, held_sections(con, member)) for member in members]
        finally:
            con.close()
    except Exception:
        logger.debug("group context read failed (non-fatal)", exc_info=True)
        return []
    readable = [
        (member, [(str(name), len(json.dumps(stored[name]["payload"], ensure_ascii=False)))
                  for name in can_read if name in stored and stored[name]["payload"]])
        for member, stored in held
    ]
    return [(member, pairs) for member, pairs in readable if pairs]


def build_group_context_block(group: str, members: list, *, max_chars: int) -> str:
    """Render ``# Project context`` for a group as one pointer per member section, else one line naming `gaia context project`."""
    if not members:
        return ""
    head = [PROJECT_CONTEXT_HEADING, "", f"{group} is a group; the sections its members hold:"]
    pointers = [
        f"- {member['name']} / {name}, {chars:,} chars: "
        + _section_pointer(shlex.quote(name), member["workspace"], member["identity"])
        for member, pairs in members for name, chars in pairs
    ]
    block = "\n".join(head + pointers)
    if len(block) <= max_chars:
        return block
    return "\n".join(head[:2] + [
        f"{group} is a group of {len(members)} projects with context — `gaia context project {shlex.quote(group)}`",
    ])


def build_project_context_block(workspace: str, project: str, sections: list, *, max_chars: int) -> str:
    """Render ``# Project context`` as whole sections, then pointers, then one line naming the sections that ignores ``max_chars``."""
    if not sections:
        return ""
    texts = [json.dumps(payload, ensure_ascii=False) for _, payload in sections]
    pointers = [
        f"- {name}, {len(text):,} chars: " + _section_pointer(shlex.quote(name), workspace, project)
        for (name, _), text in zip(sections, texts)
    ]
    head = [PROJECT_CONTEXT_HEADING, ""]
    spare = max_chars - len("\n".join(head + pointers))
    if spare < 0:
        count = f"{len(sections)} section{'s' if len(sections) > 1 else ''}"
        names = ", ".join(name for name, _ in sections)
        return "\n".join(
            head + [f"{count}: {names} — " + _section_pointer("<s>", workspace, project)]
        )
    lines = list(pointers)
    for index, ((name, _), text) in enumerate(zip(sections, texts)):
        whole = f"## {name}\n{text}"
        if len(whole) - len(pointers[index]) <= spare:
            spare -= len(whole) - len(pointers[index])
            lines[index] = whole
    return "\n".join(head + lines)


KERNEL_TOOL_LINES = 5


def build_environment_block(db_path=None) -> str:
    """Render ``# Environment``: the session's environment line, then up to ``KERNEL_TOOL_LINES`` detected user tools and the count of the rest; "" when empty."""
    try:
        from gaia.store.reader import user_tool_rows
        from gaia.store.writer import USER_WORKSPACE

        from ..session.session_manifest import build_environment_line

        line = build_environment_line(db_path=db_path)
        detected = tool_lines(user_tool_rows(db_path))
    except Exception:
        logger.debug("environment line failed (non-fatal)", exc_info=True)
        return ""
    tools = detected[:KERNEL_TOOL_LINES]
    if len(detected) > len(tools):
        tools.append(
            f"+{len(detected) - len(tools)} more — `gaia memory list --workspace {USER_WORKSPACE} --kind tool`"
        )
    body = "\n".join([line, *tools] if line else tools)
    return f"{ENVIRONMENT_HEADING}\n\n{body}" if body else ""


def _joined(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b)


def build_kernel_context(
    row: Mapping[str, Any],
    *,
    agent_name: str = "",
    agents_dir: "Path | None" = None,
    db_path=None,
    with_skills: bool = False,
) -> Optional[str]:
    """The kernel payload, held to ``KERNEL_BUDGET`` by its project context alone; None without a contract block.

    The contract, the scope preferences and pointer line, the environment and
    the CLI index are never cut. The project context takes the room they leave,
    and the skills lose their descriptions before it loses any. A goal too long
    for even that leaves the kernel over the budget, with one line after the
    contract saying how long the goal is.
    """
    kernel = build_dispatch_kernel(row, db_path=db_path, agent_name=agent_name)
    if not kernel:
        return None
    memory = build_memory_block(row, db_path=db_path)
    environment = build_environment_block(db_path=db_path)
    cli = build_cli_block(
        agent_name, agents_dir=agents_dir, workspace=str(row.get("workspace") or ""),
        contract_id=str(row.get("contract_id") or ""),
    )
    skills = build_skills_block(agent_name, agents_dir=agents_dir) if with_skills else ""
    project, sections = _readable_project_sections(row, db_path=db_path)

    def room() -> int:
        return KERNEL_BUDGET - len(_joined(kernel, memory, environment, cli, skills)) - 2

    if room() < 0 and skills:
        skills = build_skills_block(agent_name, agents_dir=agents_dir, names_only=True)
    context = build_project_context_block(
        project.get("workspace", ""), project.get("identity", ""), sections, max_chars=room(),
    ) or build_group_context_block(
        str(row.get("dispatch_project") or ""), _readable_group_sections(row, db_path=db_path),
        max_chars=room(),
    )
    over = ""
    if len(_joined(kernel, memory, context, environment, cli, skills)) > KERNEL_BUDGET:
        goal_chars = len(str(row.get("dispatch_prompt") or ""))
        over = f"over the {KERNEL_BUDGET:,}-char kernel budget: the goal alone is {goal_chars:,} chars"
    return _joined(kernel, over, memory, context, environment, cli, skills)
