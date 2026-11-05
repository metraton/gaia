"""What a signed declaration says a command does, in the shadow lattice: it replaces only what no model asserted."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from .effect_lattice import (
    CODE_EXEC, DIRECT, FS_READ, FS_WRITE, PROC_SPAWN, READ_KINDS, UNMODELLED, WORKTREE, Effect, EffectSet,
)

DECLARED_EFFECTS: Dict[str, Effect] = {
    "read": Effect(FS_READ),
    "local": Effect(FS_WRITE, DIRECT, WORKTREE),
    "runs-project-code": Effect(CODE_EXEC, DIRECT, WORKTREE),
}

_TOOL_EFFECTS: Optional[Dict[str, str]] = None


def _tool_effects() -> Dict[str, str]:
    global _TOOL_EFFECTS
    if _TOOL_EFFECTS is None:
        try:
            from gaia.store.declarations import tool_effects

            _TOOL_EFFECTS = tool_effects()
        except Exception:
            _TOOL_EFFECTS = {}
    return _TOOL_EFFECTS


def reset_cache() -> None:
    """Forget the tool effects read so far; the next lookup reads them again."""
    global _TOOL_EFFECTS
    _TOOL_EFFECTS = None


def _declarable(words, *, exact: bool) -> bool:
    from .blocked_commands import is_blocked_command
    from .cli_aliases import aliased_cli
    from .mutative_verbs import _is_unmodelled_cli, _names_a_program

    program = words[0]
    if "/" in program or aliased_cli(program) is not None or is_blocked_command(" ".join(words)).is_blocked:
        return False
    # A tool covers every argument list, so only a program and words no rule models; an exact form is signed word for word.
    return exact or (_is_unmodelled_cli(program) and not any(_names_a_program(word) for word in words[1:]))


def declared_effect(command: str, cwd: Optional[str]) -> Optional[EffectSet]:
    """The effect a declaration gives *command* run from *cwd*: the project's exact form first, then the tool's; else None."""
    try:
        from gaia.store.declarations import literal_form, project_commands
    except Exception:
        return None
    words = literal_form(command)
    if words is None:
        return None
    effect = project_commands(cwd).get(words)
    if not (effect and _declarable(words, exact=True)):
        effect = _tool_effects().get(words[0]) if _declarable(words, exact=False) else None
    return frozenset({DECLARED_EFFECTS[effect]}) if effect in DECLARED_EFFECTS else None


def _a_guess(effect: Effect) -> bool:
    return effect == UNMODELLED or effect.kind in READ_KINDS or (effect.kind == PROC_SPAWN and effect.mode == DIRECT)


def apply_declarations(effects: Iterable[Effect], command: str, cwd: Optional[str]) -> EffectSet:
    """*effects* with a declaration for *command* in place of what no model asserted; every modelled change stays."""
    effects = frozenset(effects)
    declared = declared_effect(command, cwd)
    if declared is None:
        return effects
    return frozenset(effect for effect in effects if not _a_guess(effect)) | declared
