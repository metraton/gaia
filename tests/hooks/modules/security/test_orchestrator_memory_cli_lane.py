"""The orchestrator's Bash lane is a bounded Gaia coordination console."""

import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from modules.security.gaia_cli_only_guard import (
    ALLOWED_PHRASES,
    ALLOWED_READ_PHRASES,
    ALLOWED_WRITE_PHRASES,
    EXPLICITLY_DENIED_PHRASES,
    _READ_PHRASE_FORBIDDEN_FLAGS,
    _validate_orchestrator_write,
)
from modules.security import gaia_cli_only_guard as guard

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"


@pytest.fixture
def run_guard(monkeypatch):
    """Exercise the REAL guard path with a command string.

    Only binary provenance is stubbed (it depends on an on-disk npm package
    layout the test does not have); phrase matching, flag policy and the
    denial messages all run for real. Asserting that a tuple sits in a
    frozenset would prove nothing about reachability -- this repo has already
    shipped table entries that were dead code for every idiomatic spelling.
    """
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


def test_allowlist_contains_coordination_reads_and_owned_writes() -> None:
    assert ALLOWED_PHRASES
    for phrase in (
        ("contract", "view"), ("brief", "verify"), ("plan", "show"),
        ("task", "gate", "list"), ("approvals", "pending"),
        ("notifications", "show"), ("history",), ("metrics",),
        ("brief", "new"), ("plan", "set-status"),
        ("task", "set-status"), ("notifications", "ack"),
    ):
        assert phrase in ALLOWED_PHRASES


def test_primary_lifecycle_is_allowlisted_but_corrections_are_not() -> None:
    for operation in ("search", "show", "add", "append", "reclassify", "link"):
        assert ("memory", operation) in ALLOWED_PHRASES
    for operation in ("edit", "delete"):
        assert ("memory", operation) not in ALLOWED_PHRASES


def test_domain_and_verifier_owned_mutations_stay_denied() -> None:
    for phrase in (
        ("brief", "delete"), ("plan", "save"), ("plan", "delete"),
        ("task", "add"), ("task", "gate", "set-status"),
        ("contract", "finalize"), ("approvals", "approve"),
    ):
        assert phrase in EXPLICITLY_DENIED_PHRASES


def test_owned_write_shapes_are_validated() -> None:
    cases = [
        (("brief", "new", "--headless", "--title=Useful"), ("brief", "new"), True),
        (("brief", "new", "--title=Interactive"), ("brief", "new"), False),
        (("brief", "edit", "demo", "--headless", "--field=title", "--content=X"), ("brief", "edit"), True),
        (("brief", "edit", "demo"), ("brief", "edit"), False),
        (("brief", "set-status", "demo", "open"), ("brief", "set-status"), True),
        (("brief", "set-status", "demo", "destroyed"), ("brief", "set-status"), False),
        (("brief", "ac", "add", "demo", "--id=AC-1"), ("brief", "ac", "add"), True),
        (("plan", "set-status", "demo", "active"), ("plan", "set-status"), True),
        (("task", "set-status", "demo", "1", "done"), ("task", "set-status"), True),
        (("task", "set-status", "--json", "demo", "1", "done"), ("task", "set-status"), True),
        (("task", "set-status", "demo", "1", "done", "--workspace=me"), ("task", "set-status"), True),
        (("task", "set-status", "--workspace", "me", "demo", "1", "done"), ("task", "set-status"), True),
        (("task", "set-status", "demo", "1", "pass"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--override"), ("task", "set-status"), False),
        (("task", "set-status", "--override", "demo", "1", "done"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "--override", "1", "done"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--override=true"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--override", "--reason=sneak"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--reason", "sneak"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--reason=sneak"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--db=/tmp/other"), ("task", "set-status"), False),
        (("task", "set-status", "demo", "1", "done", "--workspace"), ("task", "set-status"), False),
        (("notifications", "ack", "12"), ("notifications", "ack"), True),
        (("notifications", "ack", "all"), ("notifications", "ack"), False),
    ]
    for candidate, phrase, expected in cases:
        assert (_validate_orchestrator_write(candidate, phrase) is None) is expected


@pytest.mark.parametrize("agent_type", [None, "gaia-orchestrator"])
def test_direct_orchestrator_task_status_allows_normal_and_denies_override(
    monkeypatch, agent_type
) -> None:
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)
    payload = {"session_id": "s"}
    if agent_type is not None:
        payload["agent_type"] = agent_type

    allowed, reason = guard.check(
        "/trusted/gaia task set-status demo 1 done --json", payload
    )
    assert allowed is True
    assert reason is None

    for suffix in (
        "--override",
        "--override=true",
        "--reason=sneak",
        "--override --reason=sneak",
    ):
        allowed, reason = guard.check(
            f"/trusted/gaia task set-status demo 1 done {suffix}", payload
        )
        assert allowed is False
        assert "bounded coordination shape" in reason


# ---------------------------------------------------------------------------
# Substrate and diagnostic read verbs
# ---------------------------------------------------------------------------

NEW_READ_COMMANDS = [
    "doctor",
    "doctor --json",
    "status",
    "status --json",
    "defects",
    "query 'SELECT 1'",
    "context show",
    "context show --json",
    "context get",
    "workspace current",
    "workspace info",
    "evidence show 12",
    "evidence list --brief demo",
    "schedule list",
    "schedule show nightly",
    "schedule status",
]


@pytest.mark.parametrize("argline", NEW_READ_COMMANDS)
def test_substrate_read_verbs_are_admitted(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is True, reason
    assert reason is None


@pytest.mark.parametrize(
    "argline",
    [
        "scan",
        "scan --workspace me",
        "scan --workspace me /tmp/repos",
        "scan --dry-run",
        # context._cmd_scan delegates in-process to the same cmd_scan.
        "context scan",
        "context scan --dry-run",
    ],
)
def test_scan_is_admitted_whole_including_its_writing_default(run_guard, argline) -> None:
    """`scan` refreshes the coordination substrate the orchestrator works from.

    Its default mode writes (classify_scan(..., apply=not dry_run)) and it is
    admitted anyway: the orchestrator's identity is what authorizes the CLI,
    and keeping its own workspace context current is its job. Both spellings
    are admitted, because gating one route to an effect while leaving the
    other open gates nothing.
    """
    allowed, reason = run_guard(argline)
    assert allowed is True, reason


@pytest.mark.parametrize("argline", ["paths", "paths data", "paths db"])
def test_paths_is_admitted_and_classified_as_a_write(run_guard, argline) -> None:
    """`paths` prints, but all three handlers call ensure_layout() first.

    ensure_layout os.makedirs() the ~/.gaia layout at 0700, so `paths` is not
    a read however much its name suggests one. It stays in the lane (the same
    root is created by store.writer._connect for every DB-touching verb here)
    and it is labelled honestly.
    """
    allowed, reason = run_guard(argline)
    assert allowed is True, reason
    assert ("paths",) in ALLOWED_WRITE_PHRASES
    assert ("paths",) not in ALLOWED_READ_PHRASES


# ---------------------------------------------------------------------------
# Flag policy: a read verb that a flag turns into a writer
# ---------------------------------------------------------------------------

DOCTOR_FIX_SPELLINGS = [
    "doctor --fix",
    "doctor --fix --json",
    "doctor --json --fix",
    # `=value` form: argparse rejects it for a store_true option, but the
    # guard must not be the layer that depends on that.
    "doctor --fix=true",
    "doctor --fix=1",
    # argparse abbreviates long options (allow_abbrev defaults True): both of
    # these really do parse to fix=True, so an equality check on "--fix"
    # would have failed open here.
    "doctor --fi",
    "doctor --f",
    # A flag placed before the subcommand is skipped when the phrase is
    # built; the scan must still see it.
    "--fix doctor",
    # After a bare `--` the token is a positional, and doctor takes none --
    # denying is the closed reading.
    "doctor -- --fix",
]


@pytest.mark.parametrize("argline", DOCTOR_FIX_SPELLINGS)
def test_doctor_fix_is_denied_in_every_reachable_spelling(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is False, f"{argline!r} reached --fix without a denial"
    assert "--fix" in reason
    assert "not approvable" in reason


def test_doctor_fix_denial_explains_why_and_keeps_the_verb(run_guard) -> None:
    _allowed, reason = run_guard("doctor --fix")
    assert "_apply_agent_fix" in reason
    assert "_apply_fts5_backfill" in reason
    assert "The verb itself stays allowed" in reason


@pytest.mark.parametrize(
    "argline",
    [
        # The value merely LOOKS like the forbidden flag; the option name is
        # --workspace, and fix stays False in argparse.
        "doctor --workspace=--fix",
        "doctor --json=--fix",
        # Not an abbreviation of anything forbidden.
        "doctor --workspace /tmp/ws",
        "doctor --fixture",
    ],
)
def test_flag_scan_does_not_false_positive_on_lookalikes(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is True, reason


def test_single_dash_fix_is_left_to_argparse(run_guard) -> None:
    """`-fix` is not an argparse abbreviation of `--fix`, so it is not matched.

    argparse resolves a single-dash token against options starting with that
    token, and no long option starts with a single dash -- `gaia doctor -fix`
    exits 2 as an unrecognized argument and never runs. Documented rather
    than silently relied upon: if a short `-f` is ever added to doctor, this
    test is where the assumption breaks.
    """
    allowed, _reason = run_guard("doctor -fix")
    assert allowed is True


def test_forbidden_flag_table_is_scoped_to_the_verb_that_needs_it(run_guard) -> None:
    assert set(_READ_PHRASE_FORBIDDEN_FLAGS) == {("doctor",)}
    # The same flag name on another admitted verb is not collateral damage.
    allowed, _reason = run_guard("status --fix")
    assert allowed is True


# ---------------------------------------------------------------------------
# New denials
# ---------------------------------------------------------------------------

NEW_DENIED_COMMANDS = [
    "context wipe --workspace me",
    "context prune-workspaces",
    "context move-contracts --from a --to b",
    "context move-memory --from a --to b",
    "context move-project --decision movido",
    "workspace merge a b",
    "evidence add --brief demo --ac AC-1",
    "schedule register nightly",
    "schedule remove nightly",
    "schedule sync",
    "release check",
    "release publish",
    "install",
    "update",
    "uninstall",
    "cleanup",
    "dev",
]


@pytest.mark.parametrize("argline", NEW_DENIED_COMMANDS)
def test_substrate_surgery_and_lifecycle_verbs_are_denied(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is False, f"{argline!r} was admitted"
    assert "explicitly excluded" in reason


def test_release_check_is_denied_despite_reading_like_a_verification(run_guard) -> None:
    allowed, reason = run_guard("release check --rc")
    assert allowed is False
    assert "explicitly excluded" in reason


def test_context_scan_tracks_top_level_scan(run_guard) -> None:
    """The two spellings of the scanner must not disagree.

    `context scan` was named in the deny table only as the back door to a
    `scan`-with-`--dry-run` restriction that no longer exists. Denying it now
    would gate a route while its twin stays open, which gates nothing.
    """
    assert ("context", "scan") not in EXPLICITLY_DENIED_PHRASES
    assert ("context", "scan") in ALLOWED_PHRASES
    for argline in ("scan", "context scan"):
        allowed, reason = run_guard(argline)
        assert allowed is True, f"{argline!r}: {reason}"


# ---------------------------------------------------------------------------
# Table reachability -- every entry must fire through the real code path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", sorted(ALLOWED_READ_PHRASES))
def test_every_allowed_read_phrase_is_reachable(run_guard, phrase) -> None:
    allowed, reason = run_guard(" ".join(phrase))
    assert allowed is True, f"{phrase!r} is in the table but denied: {reason}"


@pytest.mark.parametrize("phrase", sorted(EXPLICITLY_DENIED_PHRASES))
def test_every_denied_phrase_reaches_its_own_denial(run_guard, phrase) -> None:
    """A denied entry must produce the EXPLICIT denial, not the generic one.

    If some admitted phrase ever prefix-shadows one of these, the entry stops
    being reachable and this assertion is what says so.
    """
    allowed, reason = run_guard(" ".join(phrase))
    assert allowed is False, f"{phrase!r} is in the deny table but was admitted"
    assert "explicitly excluded" in reason, (
        f"{phrase!r} did not reach its own denial branch: {reason}"
    )


def test_no_phrase_prefix_shadows_another(run_guard) -> None:
    """Prefix matching is only unambiguous while no phrase prefixes another."""
    every = sorted(ALLOWED_PHRASES | EXPLICITLY_DENIED_PHRASES)
    for phrase in every:
        for other in every:
            if phrase is other or len(phrase) >= len(other):
                continue
            assert other[: len(phrase)] != phrase, (
                f"{phrase!r} prefix-shadows {other!r}"
            )


def test_read_and_write_sets_stay_disjoint() -> None:
    assert not (ALLOWED_READ_PHRASES & ALLOWED_WRITE_PHRASES)
    assert not (ALLOWED_PHRASES & EXPLICITLY_DENIED_PHRASES)


def test_scan_is_labelled_a_write_because_it_writes() -> None:
    assert ("scan",) in ALLOWED_WRITE_PHRASES
    assert ("scan",) not in ALLOWED_READ_PHRASES


# ---------------------------------------------------------------------------
# Non-regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argline",
    [
        "contract view a123",
        "contract list",
        "task gate list demo 1",
        "plan show demo",
        "brief search topic",
        "memory search thing",
        "history",
        "metrics",
        "approvals pending",
        "notifications list",
        "brief new --headless --title=Useful",
        "task set-status demo 1 done --json",
        "notifications ack 12",
    ],
)
def test_previously_admitted_commands_still_pass(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is True, reason


@pytest.mark.parametrize(
    "argline",
    [
        "contract finalize --draft-id d",
        "approvals approve x",
        "plan save demo",
        "brief delete demo",
        "task add demo",
        "memory delete 1",
    ],
)
def test_previously_denied_commands_stay_denied(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is False
    assert "explicitly excluded" in reason


@pytest.mark.parametrize(
    "argline",
    ["rm -rf /", "contract view a1; rm -rf /", "contract view $(whoami)", "frobnicate"],
)
def test_the_lane_is_still_closed_to_everything_else(run_guard, argline) -> None:
    allowed, reason = run_guard(argline)
    assert allowed is False
    assert reason


def test_unlisted_verb_denial_points_at_the_map(run_guard) -> None:
    """The wall names where the read lane is documented.

    The failure that motivated the wider allowlist was a capability that
    existed and was unknown to its own holder.
    """
    _allowed, reason = run_guard("frobnicate --wildly")
    assert "gaia --help" in reason
