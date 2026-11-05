"""The effect lattice every classifier lane reports into: kind, mode and scope of each effect, with a top for what no lane models."""

from __future__ import annotations

from typing import FrozenSet, Iterable, NamedTuple, Optional

from .verdict import Verdict

FS_READ = "fs.read"
FS_WRITE = "fs.write"
FS_META = "fs.meta"
ENV_READ = "env.read"
ENV_WRITE = "env.write"
NET_EGRESS = "net.egress"
NET_INGRESS = "net.ingress"
PROC_SPAWN = "proc.spawn"
PROC_SIGNAL = "proc.signal"
PRIV = "priv"
PERSIST = "persist"
CRED = "cred"
CODE_EXEC = "code.exec"
TOP_KIND = "top"
KINDS: FrozenSet[str] = frozenset({
    FS_READ, FS_WRITE, FS_META, ENV_READ, ENV_WRITE, NET_EGRESS, NET_INGRESS,
    PROC_SPAWN, PROC_SIGNAL, PRIV, PERSIST, CRED, CODE_EXEC, TOP_KIND,
})
READ_KINDS: FrozenSet[str] = frozenset({FS_READ, ENV_READ, NET_INGRESS})

DIRECT = "direct"
TRANSITIVE = "transitive"
AMBIENT = "ambient"
CONDITIONAL = "conditional"
MODES: FrozenSet[str] = frozenset({DIRECT, TRANSITIVE, AMBIENT, CONDITIONAL})

OWN_SCRATCH = "own-scratch"
WORKTREE = "worktree"
REPO = "repo"
HOME = "home"
PROTECTED = "protected"
STORE = "store"
REMOTE = "remote"
UNKNOWN_SCOPE = "unknown"
SCOPES: FrozenSet[str] = frozenset({OWN_SCRATCH, WORKTREE, REPO, HOME, PROTECTED, STORE, REMOTE, UNKNOWN_SCOPE})
OWN_SCOPES: FrozenSet[str] = frozenset({OWN_SCRATCH, WORKTREE})
FLOOR_SCOPES: FrozenSet[str] = frozenset({PROTECTED, STORE})


class Effect(NamedTuple):
    """One effect: what it does, how the command reaches it, and where it lands."""

    kind: str
    mode: str = DIRECT
    scope: str = UNKNOWN_SCOPE


EffectSet = FrozenSet[Effect]

UNMODELLED = Effect(TOP_KIND, AMBIENT, UNKNOWN_SCOPE)
UNDETERMINED = Effect(TOP_KIND, CONDITIONAL, UNKNOWN_SCOPE)
TOP: EffectSet = frozenset({UNMODELLED})
ABSTAINS: EffectSet = frozenset()

VERB_CLASS_KINDS = {
    "create": FS_WRITE,
    "update": FS_WRITE,
    "delete": FS_WRITE,
    "publish": NET_EGRESS,
    "exec": PROC_SPAWN,
}

CATALOGUE_KINDS = {
    "PROCESS_EXECUTION": PROC_SPAWN,
    "FILE_DELETION": FS_WRITE,
    "FILE_WRITE": FS_WRITE,
    "FILE_MUTATION": FS_WRITE,
    "PERMISSION_MOD": FS_META,
    "NETWORK": NET_EGRESS,
    "CLOUD_SDK": NET_EGRESS,
    "DYNAMIC_CODE": CODE_EXEC,
}

REMOTE_FAMILIES: FrozenSet[str] = frozenset({"k8s", "cloud", "iac", "gitops"})

_LANE_VERBS = {
    "registry-fetch": Effect(CODE_EXEC, TRANSITIVE, REMOTE),
    "foreign-code": Effect(CODE_EXEC, TRANSITIVE, REMOTE),
    "git-config-code": Effect(CODE_EXEC, TRANSITIVE, REPO),
    "script-stdin-payload": Effect(CODE_EXEC, CONDITIONAL, UNKNOWN_SCOPE),
    "account-path-write": Effect(PERSIST, DIRECT, HOME),
    "egress": Effect(NET_EGRESS, DIRECT, REMOTE),
    "install": Effect(CODE_EXEC, TRANSITIVE, REMOTE),
}
_UNDETERMINED_VERBS: FrozenSet[str] = frozenset({"script-file-unreadable", "executor-unparsed-flag", "unproven-python"})

LEG_EFFECTS = {
    "sign.destroys": Effect(FS_WRITE, DIRECT, UNKNOWN_SCOPE),
    "sign.speaks": Effect(NET_EGRESS, DIRECT, REMOTE),
    "sign.lands": Effect(FS_WRITE, DIRECT, REMOTE),
    "sign.lands-on-protected": Effect(PERSIST, DIRECT, HOME),
    "sign.foreign-code": Effect(CODE_EXEC, TRANSITIVE, REMOTE),
    "sign.lifecycle-body-verb": Effect(CODE_EXEC, TRANSITIVE, REPO),
}

FLOOR_EFFECTS = {
    "floor.gaia-db-write": Effect(FS_WRITE, DIRECT, STORE),
    "floor.secret-read": Effect(CRED, DIRECT, PROTECTED),
    "floor.protected-path": Effect(FS_WRITE, DIRECT, PROTECTED),
    "floor.shell-write": Effect(FS_WRITE, DIRECT, PROTECTED),
    "floor.subagent-memory-write": Effect(FS_WRITE, DIRECT, STORE),
    "floor.subagent-resume-point": Effect(FS_WRITE, DIRECT, STORE),
    "floor.kubectl-secret-output": Effect(CRED, DIRECT, PROTECTED),
    "floor.clear-secret": Effect(CRED, DIRECT, PROTECTED),
}
_FLOOR_DEFAULT = Effect(CODE_EXEC, DIRECT, PROTECTED)


def is_top(effects: Iterable[Effect]) -> bool:
    """True when the set holds the top: some effect of the command is outside every model."""
    return any(effect.kind == TOP_KIND for effect in effects)


def join(*sets: Iterable[Effect]) -> EffectSet:
    """Union the lanes' sets; when every lane abstains the command is unmodelled, never effect-free."""
    joined = frozenset().union(*sets)
    return joined or TOP


def floor_effect(rule: str) -> EffectSet:
    """The effect a categorical refusal under *rule* stands for, always in a floor scope."""
    return frozenset({FLOOR_EFFECTS.get(rule, _FLOOR_DEFAULT)})


def signed_effect(rule: str) -> EffectSet:
    """The effect a signature request under *rule* stands for."""
    return frozenset({LEG_EFFECTS.get(rule, Effect(FS_WRITE, DIRECT, UNKNOWN_SCOPE))})


def classification_effects(
    *, is_mutative: bool, reads: bool, changes: bool, verb: str, cli_family: str, verb_class: Optional[str],
) -> EffectSet:
    """The set a verb-scan classification reports: a read, a change by its verb class, or TOP when nothing modelled it."""
    remote = cli_family in REMOTE_FAMILIES
    if is_mutative:
        if verb in _UNDETERMINED_VERBS:
            return frozenset({UNDETERMINED})
        if verb in _LANE_VERBS:
            return frozenset({_LANE_VERBS[verb]})
        kind = VERB_CLASS_KINDS.get(verb_class or "", FS_WRITE)
        if remote and kind == FS_WRITE:
            kind = NET_EGRESS
        return frozenset({Effect(kind, DIRECT, REMOTE if remote else UNKNOWN_SCOPE)})
    if changes:
        return frozenset({Effect(FS_WRITE, DIRECT, WORKTREE)})
    if reads:
        return frozenset({Effect(NET_INGRESS, DIRECT, REMOTE) if remote else Effect(FS_READ)})
    return TOP


def catalogue_effects(*, is_dangerous: bool, category: str, parse_failed: bool) -> EffectSet:
    """The set the Python call catalogue reports: the class of the call it found, or TOP when it proves nothing."""
    if parse_failed:
        return frozenset({UNDETERMINED})
    if is_dangerous:
        return frozenset({Effect(CATALOGUE_KINDS.get(category, CODE_EXEC), DIRECT, UNKNOWN_SCOPE)})
    return TOP


def within_own_work(effects: Iterable[Effect]) -> EffectSet:
    """The set of a command the validator ran free: what would ask stayed inside the caller's own work."""
    relieved = set()
    for effect in effects:
        if effect.kind == TOP_KIND:
            relieved.add(UNMODELLED)
        elif _asks(effect):
            relieved.add(Effect(effect.kind, DIRECT, WORKTREE))
        else:
            relieved.add(effect)
    return frozenset(relieved) or TOP


def _asks(effect: Effect) -> bool:
    if effect.kind == TOP_KIND or effect.kind in READ_KINDS:
        return False
    return not (effect.scope in OWN_SCOPES and effect.mode == DIRECT)


def lattice_verdict(effects: Iterable[Effect], unmodelled: Verdict = Verdict.FREE) -> Verdict:
    """The verdict the set alone implies; *unmodelled* is what TOP costs, FREE by elimination today."""
    effects = frozenset(effects) or TOP
    if any(effect.scope in FLOOR_SCOPES for effect in effects):
        return Verdict.BLOCKED
    if UNDETERMINED in effects:
        return Verdict.UNKNOWN
    if any(_asks(effect) for effect in effects):
        return Verdict.SIGNED
    if is_top(effects):
        return unmodelled
    return Verdict.FREE
