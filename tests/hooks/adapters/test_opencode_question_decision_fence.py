#!/usr/bin/env python3
"""A structured decision activates a grant only when Gaia verified where it
came from -- asserted over the OpenCode transport, end to end.

Two things are proven here and they are not the same thing.

The AFFIRMATIVE half proves the lane WORKS: the real ``GaiaOpenCodePlugin``
runs under bun, its own ``questionAnswers`` composes the answer mapping from
OpenCode's native ``metadata.answers``, the real Gaia-side bridge answers
issuance, and the emitted event is then parsed and delivered by the real
``OpenCodeAdapter.adapt_post_tool_use`` -- which hands it to the shared Claude
resolver. Nothing on that path is re-implemented here, because an activation
asserted over a hand-written dict proves a shape no adapter emits.

The NEGATIVE half proves the lane REFUSES. Its fixtures are synthetic on
purpose: the bridge reads stdin, so an arbitrary writer really can present a
``tool.execute.after`` naming the question tool and carrying a forged mapping,
and that is exactly what must activate nothing. The discriminator that decides
which results carry answers lives in ``plugin.ts`` -- the far side of that
stdin -- so it is not a fence at all, and these tests fail against an adapter
that trusts it.

EVERY case in the matrix runs twice, once through each container the resolver
reads: the ``tool_response`` the plugin builds, and the ``tool_input`` it falls
back to, which in this lane is the caller's own tool arguments. Covering only
the first leaves a second, fully caller-controlled activation route open --
measured, not assumed: with the fence absent, a forged event whose answers sit
only in ``args`` activates a real pending, which is what
``TestAttestedApproveActivates`` asserts as the positive control for that route.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_HOOKS_DIR = _REPO / "hooks"
for _p in (str(_HOOKS_DIR), str(_HOOKS_DIR / "adapters"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.opencode import OpenCodeAdapter

_DRIVER = _REPO / "tests" / "opencode" / "attestation_driver.ts"

SESSION = "ses-root"
CALL_ID = "call-question"

# The two containers the shared resolver reads a decision out of, in the order
# it reads them. Every case below is asserted through both.
_CONTAINERS = ("tool_response", "tool_input")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """Point issuance and resolution at a ledger this test owns."""
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_grants_dir(tmp_path, monkeypatch):
    """Filesystem grants land in tmp; the DB is already GAIA_DATA_DIR-isolated."""
    import modules.security.approval_grants as ag

    (tmp_path / ".claude" / "cache" / "approvals").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", SESSION)
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False


@pytest.fixture
def drive(ledger, monkeypatch):
    """Run the real plugin, then join the host run its bridge minted in.

    Issuance happens in a bridge process the bun driver spawned; these
    assertions resolve here, in the pytest process. The namespace is read back
    from the ledger the bridge chose to write, never named by this test, so a
    negative below fails on what it tampers with rather than on a namespace
    that never matched.
    """

    def run(scenario):
        result = subprocess.run(
            ["bun", str(_DRIVER), json.dumps(scenario)],
            text=True,
            capture_output=True,
            env=os.environ.copy(),
            cwd=str(_REPO),
        )
        assert result.returncode == 0, f"driver failed: {result.stderr}"
        requests = json.loads(result.stdout)
        written = sorted((ledger / "ledger").glob("*.json"))
        assert len(written) == 1, f"the bridge wrote no single ledger: {written}"
        monkeypatch.setattr(
            "modules.security.host_attestation.host_run_id", lambda: written[0].stem
        )
        return requests

    return run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sealed_payload(command: str, *, agent_type: str = "test-agent") -> dict:
    """Seal ``command`` with the REAL producer, fed the classifier's verdict."""
    from modules.security.mutative_verbs import detect_mutative_command
    from modules.tools.bash_validator import _build_sealed_payload

    verdict = detect_mutative_command(command)
    assert verdict.is_mutative, f"{command!r} is not intercepted as a mutative verb"
    return _build_sealed_payload(
        command=command,
        verb=verdict.verb,
        category=verdict.category,
        agent_type=agent_type,
    )


def _request(command: str, *, approval_id: str | None = None) -> str:
    import gaia.approvals.store as store

    return store.insert_requested(
        _sealed_payload(command), agent_id="test-agent", session_id=SESSION,
        approval_id=approval_id,
    )


def _status(approval_id: str) -> str:
    import gaia.approvals.store as store

    con = store._open_db()
    try:
        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    finally:
        con.close()
    return row["status"]


def _grant(command: str):
    from modules.security.approval_grants import check_approval_grant

    return check_approval_grant(command, session_id=SESSION)


def _approve_label(approval_id: str, command: str) -> str:
    return f"Approve -- {command} [{approval_id}]"


def _emitted(requests, event, session_id):
    for request in requests:
        if request.get("event") == event and request.get("sessionID") == session_id:
            return request
    raise AssertionError(f"plugin emitted no {event} for {session_id}: {requests}")


def _answered_turn(drive, labels, *, agent="gaia-orchestrator", args=None):
    """Drive one real answered question turn and return what the plugin sent."""
    requests = drive(
        {
            "steps": [
                {"kind": "message", "sessionID": SESSION, "agent": agent},
                {
                    "kind": "after",
                    "sessionID": SESSION,
                    "callID": CALL_ID,
                    "tool": "question",
                    "args": args or {"questions": [{"question": "Proceed?"}]},
                    "output": "User has answered your questions.",
                    "metadata": {"answers": [[label] for label in labels]},
                },
            ],
        }
    )
    return _emitted(requests, "tool.execute.after", SESSION)


def _through(emitted: dict, container: str) -> dict:
    """Move the decision into exactly one of the two containers the resolver reads.

    ``tool_input`` is the caller's own tool arguments, so relocating the mapping
    there is not a contrivance: it is the shape an arbitrary writer on the far
    side of the bridge controls outright, and the resolver falls back to it the
    moment the result carries no answers.
    """
    if container == "tool_response":
        return emitted
    return dict(
        emitted,
        result={"output": "answered"},
        args={"answers": emitted["result"]["answers"]},
    )


def _deliver(emitted: dict):
    adapter = OpenCodeAdapter()
    return adapter.adapt_post_tool_use(adapter.parse_event(json.dumps(emitted)))


def _unattested(emitted: dict) -> dict:
    """The exact claim a caller can mint on its own: a token nothing recorded."""
    return dict(
        emitted,
        roleContext=dict(emitted["roleContext"], attestation="ses-root:gaia-orchestrator"),
    )


def _not_activated_records() -> list[dict]:
    """Read the durable non-activation records back OUT of the store."""
    from gaia.approvals.decision_audit import DECISION_NOT_ACTIVATED_EVENT
    from gaia.store.reader import cross_surface_query

    rows = cross_surface_query(
        surface="harness_events", type=DECISION_NOT_ACTIVATED_EVENT, last=50
    )
    return [json.loads(r["raw"]["payload"] or "{}") for r in rows]


# ---------------------------------------------------------------------------
# The lane works: an approve the runtime attested activates end to end
# ---------------------------------------------------------------------------

class TestAttestedApproveActivates:

    def test_the_plugin_emits_the_native_answers_as_a_canonical_mapping(self, drive):
        """The transport half, asserted on the plugin's own emission.

        The answer mapping is built by the plugin from OpenCode's native
        ``metadata.answers``, not invented by this test.
        """
        command = "terraform apply"
        approval_id = _request(command)

        emitted = _answered_turn(drive, [_approve_label(approval_id, command)])

        assert emitted["tool"] == "AskUserQuestion"
        assert emitted["callID"] == CALL_ID
        assert emitted["result"]["answers"] == {
            "0:0": _approve_label(approval_id, command)
        }

    @pytest.mark.parametrize("container", _CONTAINERS)
    def test_an_attested_approve_activates_through_the_whole_transport(
        self, drive, container
    ):
        """plugin -> bridge shape -> OpenCodeAdapter -> resolver -> activation.

        Run for both containers, this is also the positive control the negative
        half needs: without it, a fence test could pass because the route does
        not exist rather than because the fence closed it.
        """
        command = "terraform apply"
        approval_id = _request(command)

        response = _deliver(
            _through(_answered_turn(drive, [_approve_label(approval_id, command)]), container)
        )

        assert response.exit_code == 0
        assert _status(approval_id) == "approved"
        assert _grant(command) is not None, (
            "the grant must be executable, not merely marked approved"
        )

    @pytest.mark.parametrize("container", _CONTAINERS)
    def test_two_signed_labels_in_one_answer_both_activate(self, drive, container):
        first_cmd = "terraform apply"
        second_cmd = "kubectl delete pod web-1"
        first = _request(first_cmd)
        second = _request(second_cmd)

        _deliver(
            _through(
                _answered_turn(
                    drive,
                    [_approve_label(first, first_cmd), _approve_label(second, second_cmd)],
                ),
                container,
            )
        )

        assert _status(first) == "approved"
        assert _status(second) == "approved"
        assert _grant(first_cmd) is not None
        assert _grant(second_cmd) is not None


# ---------------------------------------------------------------------------
# The fence: a decision whose provenance Gaia did not verify activates nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("container", _CONTAINERS)
class TestUnverifiedDecisionActivatesNothing:
    """The property under test, stated once: a structured decision may activate
    a grant only when Gaia verified its provenance, never because the payload
    said so."""

    def test_a_caller_minted_claim_cannot_sign(self, drive, container):
        command = "terraform apply"
        approval_id = _request(command)

        _deliver(
            _through(
                _unattested(_answered_turn(drive, [_approve_label(approval_id, command)])),
                container,
            )
        )

        assert _status(approval_id) == "pending", (
            "an unattested caller forged a signature: the plugin-side "
            "discriminator is on the far side of the bridge and fences nothing"
        )
        assert _grant(command) is None

    def test_an_event_with_no_role_context_at_all_cannot_sign(self, drive, container):
        command = "terraform apply"
        approval_id = _request(command)

        emitted = _through(
            _answered_turn(drive, [_approve_label(approval_id, command)]), container
        )
        emitted.pop("roleContext", None)

        _deliver(emitted)

        assert _status(approval_id) == "pending"
        assert _grant(command) is None

    def test_an_uncorrelated_event_cannot_sign(self, drive, container):
        """No ``callID`` means no tool call this decision belongs to."""
        command = "terraform apply"
        approval_id = _request(command)

        emitted = _through(
            _answered_turn(drive, [_approve_label(approval_id, command)]), container
        )
        emitted.pop("callID", None)

        _deliver(emitted)

        assert _status(approval_id) == "pending"
        assert _grant(command) is None

    def test_an_ordinary_agent_cannot_sign(self, drive, container):
        """The role is attested, and it is simply not the control plane."""
        command = "terraform apply"
        approval_id = _request(command)

        _deliver(
            _through(
                _answered_turn(
                    drive, [_approve_label(approval_id, command)], agent="developer",
                ),
                container,
            )
        )

        assert _status(approval_id) == "pending"
        assert _grant(command) is None

    def test_a_replayed_claim_from_another_session_cannot_sign(self, drive, container):
        command = "terraform apply"
        approval_id = _request(command)

        emitted = _through(
            _answered_turn(drive, [_approve_label(approval_id, command)]), container
        )

        _deliver(dict(emitted, sessionID="ses-other"))

        assert _status(approval_id) == "pending"
        assert _grant(command) is None


# ---------------------------------------------------------------------------
# Attested, and still not an approve: only the exact signed decision activates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("container", _CONTAINERS)
class TestOnlyAnExactApproveActivates:

    def test_a_reject_label_activates_nothing(self, drive, container):
        command = "git push origin main"
        approval_id = _request(command)

        _deliver(
            _through(
                _answered_turn(drive, [f"Do not approve -- {command} [{approval_id}]"]),
                container,
            )
        )

        assert _status(approval_id) == "pending", (
            "a label the user did not approve must yield no grant even though "
            "it carries the canonical id -- the ^Approve anchoring is the fence"
        )
        assert _grant(command) is None

    @pytest.mark.parametrize(
        "label",
        [
            "Sure, go ahead",
            "Approve",
            "Approve -- terraform apply [P-deadbeef]",
            "Approve -- terraform apply [P-not-hex-at-all-0000000000000000]",
        ],
        ids=["free-text", "bare-verb", "truncated-id", "unparseable-id"],
    )
    def test_a_malformed_or_free_text_answer_activates_nothing(
        self, drive, container, label
    ):
        command = "terraform apply"
        approval_id = _request(command)

        _deliver(_through(_answered_turn(drive, [label]), container))

        assert _status(approval_id) == "pending"
        assert _grant(command) is None

    def test_an_id_that_matches_no_pending_activates_nothing(self, drive, container):
        command = "terraform apply"
        approval_id = _request(command)
        unknown = "P-" + "a" * 32

        _deliver(_through(_answered_turn(drive, [_approve_label(unknown, command)]), container))

        assert _status(approval_id) == "pending"
        assert _grant(command) is None

    def test_a_shared_prefix_activates_only_the_exact_signed_id(self, drive, container):
        prefix = "deadbeef"
        signed_cmd = "git push origin signed"
        unsigned_cmd = "git push origin unsigned"
        signed = _request(signed_cmd, approval_id=f"P-{prefix}{'f' * 24}")
        unsigned = _request(unsigned_cmd, approval_id=f"P-{prefix}{'0' * 24}")

        _deliver(_through(_answered_turn(drive, [_approve_label(signed, signed_cmd)]), container))

        assert _status(signed) == "approved"
        assert _status(unsigned) == "pending"
        assert _grant(signed_cmd) is not None
        assert _grant(unsigned_cmd) is None

    def test_an_expired_pending_is_not_revived_by_a_late_answer(self, drive, container):
        import gaia.approvals.store as store

        command = "terraform apply"
        approval_id = _request(command)
        store.expire(approval_id, expirer_session=SESSION)

        _deliver(
            _through(_answered_turn(drive, [_approve_label(approval_id, command)]), container)
        )

        assert _status(approval_id) == "expired"
        assert _grant(command) is None

    def test_a_duplicate_reply_does_not_activate_a_second_time(self, drive, container):
        command = "terraform apply"
        approval_id = _request(command)
        emitted = _through(
            _answered_turn(drive, [_approve_label(approval_id, command)]), container
        )

        _deliver(emitted)
        assert _status(approval_id) == "approved"

        _deliver(emitted)

        assert _status(approval_id) == "approved"
        assert any(
            record.get("approval_id") == approval_id
            for record in _not_activated_records()
        ), (
            "the replayed answer must be recorded as not activated, so a "
            "dropped signature stays distinguishable from a replayed one"
        )
