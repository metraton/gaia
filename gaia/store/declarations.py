"""Signed declarations of a tool's effect or a project's exact command form: recorded, not read by the validator yet
(no hook calls tool_effects or project_commands; pending thread_gaia_memory_declare_sin_lector_tras_1130_20261106)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

EFFECTS: Tuple[str, ...] = ("read", "local", "runs-project-code")

# Words the shell hands over unchanged: no quoting, expansion, glob, brace,
# tilde, redirect or operator character can appear in one.
_LITERAL_WORD = re.compile(r"^[A-Za-z0-9_./:@%+,=-]+$")


class DeclarationRefused(ValueError):
    """A declaration the channel does not accept, with the reason to show."""


def literal_form(command: str) -> Optional[Tuple[str, ...]]:
    """The words of *command* when each is a literal and the first names a program; None otherwise."""
    words = tuple((command or "").split())
    if not words or "=" in words[0] or words[0].startswith("-"):
        return None
    if not all(_LITERAL_WORD.match(word) for word in words):
        return None
    return words


def _refuse_dispatched_caller() -> None:
    if os.environ.get("GAIA_DISPATCH_AGENT"):
        raise DeclarationRefused(
            "a subagent never writes a declaration; propose it in your contract and the user "
            "signs `gaia memory declare` from the main session"
        )


def _effect(effect: Optional[str]) -> Optional[str]:
    if effect is not None and effect not in EFFECTS:
        raise DeclarationRefused(f"effect must be one of {', '.join(EFFECTS)}")
    return effect


def declare_tool_effect(binary: str, effect: Optional[str], db_path: Optional[Path] = None) -> int:
    """Set (or, with None, withdraw) the effect of the user's live tool rows for *binary*; returns the rows changed."""
    _refuse_dispatched_caller()
    effect = _effect(effect)
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        with con:
            changed = con.execute(
                "UPDATE memory SET effect = ? WHERE owner_level = 'user' AND kind = 'tool' "
                "AND binary = ? AND deleted_at IS NULL",
                (effect, binary),
            ).rowcount
    finally:
        con.close()
    if not changed:
        raise DeclarationRefused(
            f"no live tool row names {binary!r}; add it first with "
            f"`gaia memory add --type=user --kind=tool --binary={binary}`"
        )
    return changed


def declare_command(
    workspace: str, project: str, command: str, effect: Optional[str], db_path: Optional[Path] = None,
) -> str:
    """Declare (or, with None, withdraw) *command* as an exact form of *project* with *effect*; returns the stored form."""
    _refuse_dispatched_caller()
    effect = _effect(effect)
    words = literal_form(command)
    if words is None:
        raise DeclarationRefused(
            "a declared command is literal words only: no quote, variable, glob, brace, tilde, "
            "redirect or operator, and it starts with the program"
        )
    form = " ".join(words)
    from gaia.store.writer import _connect, _now_iso

    con = _connect(db_path)
    try:
        if not con.execute(
            "SELECT 1 FROM projects WHERE workspace = ? AND name = ? AND status = 'active'",
            (workspace, project),
        ).fetchone():
            raise DeclarationRefused(f"no active project {project!r} in workspace {workspace!r}")
        with con:
            if effect is None:
                con.execute(
                    "DELETE FROM command_declarations WHERE workspace = ? AND project = ? AND command = ?",
                    (workspace, project, form),
                )
            else:
                con.execute(
                    "INSERT INTO command_declarations (workspace, project, command, effect, declared_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(workspace, project, command) "
                    "DO UPDATE SET effect = excluded.effect, declared_at = excluded.declared_at",
                    (workspace, project, form, effect, _now_iso()),
                )
    finally:
        con.close()
    return form


def tool_effects(db_path: Optional[Path] = None) -> Dict[str, str]:
    """``{binary: effect}`` over the user's live declared tools that replace no CLI; ``{}`` when the read fails."""
    try:
        from gaia.store.reader import _ro_connect, not_superseded

        con = _ro_connect(db_path)
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT m.binary, m.effect FROM memory m WHERE m.owner_level = 'user' AND m.kind = 'tool' "
            "AND m.effect IS NOT NULL AND m.replaces IS NULL AND m.deleted_at IS NULL "
            f"AND {not_superseded('m')}"
        ).fetchall()
        return {binary: effect for binary, effect in rows}
    except Exception:
        return {}
    finally:
        con.close()


def project_commands(cwd: Optional[str], db_path: Optional[Path] = None) -> Dict[Tuple[str, ...], str]:
    """``{words: effect}`` declared by the active project holding *cwd*; ``{}`` with no such project or a failed read."""
    if not cwd:
        return {}
    try:
        from gaia.store.reader import _ro_connect

        here = Path(cwd).resolve()
        con = _ro_connect(db_path)
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT workspace, name, path FROM projects WHERE status = 'active' AND path IS NOT NULL"
        ).fetchall()
        holding = [(Path(r[2]).resolve(), r) for r in rows]
        holding = [(root, r) for root, r in holding if here == root or root in here.parents]
        if not holding:
            return {}
        workspace, name, _ = max(holding, key=lambda pair: len(pair[0].parts))[1]
        declared = con.execute(
            "SELECT command, effect FROM command_declarations WHERE workspace = ? AND project = ?",
            (workspace, name),
        ).fetchall()
        return {tuple(command.split()): effect for command, effect in declared}
    except Exception:
        return {}
    finally:
        con.close()
