"""
gaia.state.gate_oracle -- Deterministic oracle re-execution of a command/code
task gate.

A task gate (task_gates row / planner-authored typed gate, harness R1-A) or a
proposed contract-envelope verification block (``agent_contract_handoff
evidence_report.verification``) whose ``verification_type`` (or ``type``) is
one of the two DETERMINISTIC entries in ``gaia.state.VALID_VERIFICATION_TYPES``
-- ``command`` and ``code`` -- carries a runnable check spec: ``evidence_shape``
on the gate shape, ``command`` on the envelope shape (both TEXT; see
``gaia.state.gate_validation`` and ``gaia.contract.validator``). This module
RE-EXECUTES that spec and returns an objective, evidence-carrying verdict --
it is the machinery a verifier-role agent calls when operating in oracle mode
(see ``skills/verification-oracle``); this module does not itself decide who
may call it (see ``gaia.state.permissions.verifier_fleet``/``is_verifier``).

Unlike ``gate_validation.validate_gate`` (pure, DB-free, LLM-free, no I/O),
this module DELIBERATELY performs real I/O: a subprocess execution of the
declared command. That is the entire point of an oracle -- it re-observes the
world instead of trusting a prior claim about it. It never touches the DB and
never calls an LLM, but it is not "pure" in the no-side-effects sense; it is
deterministic in the sense that, given the same check spec and environment,
the same objective result is produced independent of anyone's assertion.

``command`` and ``code`` are NOT two execution mechanisms -- the module
docstring in ``gaia.state.__init__`` calls them "synonyms for the two shapes
of a deterministic check": both resolve to the same runnable-string shape and
are re-executed identically here. The label only changes what a human reads
in a report (``command`` typically names a broader run -- a test suite, a
script; ``code`` typically names a narrower code-level check -- a linter, a
type-checker, an assertion), never how the oracle behaves.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field

# The two DETERMINISTIC verification types this oracle re-runs. Deliberately
# NOT "semantic" (needs a human/rubric) or "self_review" (trusts the agent's
# own statement) -- re-executing either of those is a category error, there
# is nothing to exec.
DETERMINISTIC_ORACLE_TYPES: tuple[str, ...] = ("command", "code")

_DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class OracleVerdict:
    """Objective result of re-running one command/code gate.

    ``ok`` is True only when the gate resolved to a runnable command AND its
    actual exit code matched ``expected_exit_code``. ``errors`` carries every
    reason ``ok`` is False (unresolvable type, empty spec, un-tokenizable
    string, command not found, timeout, or a plain exit-code mismatch) so a
    caller can report the SAME evidence a human would need, not just the bit.
    """

    ok: bool
    verification_type: str | None
    command: str
    exit_code: int | None
    expected_exit_code: int
    stdout: str
    stderr: str
    errors: list[str] = field(default_factory=list)


def _extract_type(gate: dict) -> str | None:
    """Resolve the verification type off either shape.

    Prefers the persisted task_gates column name (``verification_type``);
    falls back to the contract-envelope field name (``type``) so a caller can
    hand this function either shape without translating first.
    """
    vtype = gate.get("verification_type")
    if vtype:
        return vtype
    return gate.get("type")


def _extract_command(gate: dict) -> str | None:
    """Resolve the runnable check spec off either shape.

    ``evidence_shape`` (the persisted gate column) wins when both are
    present; ``command`` (the contract-envelope field) is the fallback. Both
    are documented, in ``gate_validation`` and ``gaia.contract.validator``
    respectively, as carrying the same thing: the command/oracle to run.
    """
    shape = gate.get("evidence_shape")
    if isinstance(shape, str) and shape.strip():
        return shape.strip()
    command = gate.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    return None


def _resolve_expected_exit_code(gate: dict) -> int:
    """Read a gate-declared ``expected_exit_code``; default to 0.

    Exit-code-0 is the common case (tests, most CLIs), NOT a universal
    constant -- a gate may legitimately expect a different code (e.g. a
    linter that exits non-zero on findings by design). A missing or
    non-integer value falls back to 0 rather than rejecting the gate: the
    expectation field is optional, unlike the check spec itself.
    """
    raw = gate.get("expected_exit_code", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def run_oracle_check(gate: dict, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> OracleVerdict:
    """Re-execute a command/code gate and return an objective verdict.

    Accepts a mapping in EITHER the task_gates shape (``verification_type``,
    ``evidence_shape``, optional ``expected_exit_code``) or the
    contract-envelope shape (``type``, ``command``). Rejects (``ok=False``,
    no execution attempted) when:

      * the resolved type is not in ``DETERMINISTIC_ORACLE_TYPES``;
      * the resolved check spec is absent/blank;
      * the check spec cannot be tokenized (``shlex.split`` raises).

    Otherwise runs the tokenized command as a subprocess (``shell=False`` --
    no shell injection surface, mirrors the ``command-execution`` discipline),
    with a bounded ``timeout``, and compares the actual exit code against the
    gate's ``expected_exit_code`` (default 0). A command that cannot be found,
    or that times out, is ``ok=False`` with a distinct ``errors`` entry -- it
    is never silently treated as a pass.
    """
    vtype = _extract_type(gate)

    if vtype not in DETERMINISTIC_ORACLE_TYPES:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command="",
            exit_code=None,
            expected_exit_code=0,
            stdout="",
            stderr="",
            errors=[
                f"verification_type {vtype!r} is not a deterministic oracle "
                f"type (expected one of {DETERMINISTIC_ORACLE_TYPES})"
            ],
        )

    expected_exit_code = _resolve_expected_exit_code(gate)
    command = _extract_command(gate)

    if not command:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command="",
            exit_code=None,
            expected_exit_code=expected_exit_code,
            stdout="",
            stderr="",
            errors=["gate has no runnable check spec (evidence_shape/command empty)"],
        )

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command=command,
            exit_code=None,
            expected_exit_code=expected_exit_code,
            stdout="",
            stderr="",
            errors=[f"check spec could not be tokenized: {exc}"],
        )

    if not argv:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command=command,
            exit_code=None,
            expected_exit_code=expected_exit_code,
            stdout="",
            stderr="",
            errors=["check spec tokenized to zero arguments"],
        )

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command=command,
            exit_code=None,
            expected_exit_code=expected_exit_code,
            stdout="",
            stderr=str(exc),
            errors=[f"command not found: {exc}"],
        )
    except subprocess.TimeoutExpired as exc:
        return OracleVerdict(
            ok=False,
            verification_type=vtype,
            command=command,
            exit_code=None,
            expected_exit_code=expected_exit_code,
            stdout=(exc.stdout if isinstance(exc.stdout, str) else "") or "",
            stderr=(exc.stderr if isinstance(exc.stderr, str) else "") or "",
            errors=[f"command timed out after {timeout}s"],
        )

    ok = proc.returncode == expected_exit_code
    errors: list[str] = []
    if not ok:
        errors.append(f"exit code {proc.returncode} != expected {expected_exit_code}")

    return OracleVerdict(
        ok=ok,
        verification_type=vtype,
        command=command,
        exit_code=proc.returncode,
        expected_exit_code=expected_exit_code,
        stdout=proc.stdout,
        stderr=proc.stderr,
        errors=errors,
    )


__all__ = [
    "DETERMINISTIC_ORACLE_TYPES",
    "OracleVerdict",
    "run_oracle_check",
]
