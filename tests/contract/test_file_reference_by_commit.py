"""The commit-qualified file reference in evidence_report.files_checked.

A cited file is evidence, and evidence with no lineage is a photograph: it
shows a state without saying which one. So a files_checked entry may now carry
the commit the file was read at, which turns the citation into a door -- to the
diff, to the message that says why, to the sibling files of the same move.

The rule under test attaches to the ARTEFACT, never to the turn:

    committed file    -> cited as {"path": ..., "commit": ...}
    uncommitted file  -> cited as a bare path, and that is NOT a fault

That conditional is the whole design. The condition is a fact the system can
check on its own rather than a discipline demanded of the agent, so nobody is
ever blocked for not having committed and nobody has a reason to half-commit
in order to be allowed to cite. A form that made the commit a REQUIREMENT
would re-open exactly that incentive.

Three properties carry the implementation, and each has its own section:

  1. ADDITIVE. Every bare path ever written stays valid -- including the ones
     carrying '@', ':', '#' and spaces, which is why the reference is an
     OBJECT and not a path@sha string. Measured over the 25,967 files_checked
     entries in the live population: a trailing-@ trigger would newly reject
     26 of them, any-@ 429, ' @ ' 7, trailing-# 22, and the object form 0.
     The strings in ``_REAL_BARE_PATHS_THAT_LOOK_LIKE_REFERENCES`` are copied
     verbatim from that census.

  2. FORM, NOT EXISTENCE. Nothing consults git. A reference to a commit that
     does not exist is well-formed and passes; that is the design, not a hole,
     and it is what keeps the validator pure and cheap enough to run on every
     incremental write.

  3. NO CELL. Every write validates the WHOLE envelope, so a new rejection can
     trap a draft whose only way out is subject to the same rejection. The
     rejection here cannot: a rejected write persists nothing (the entry never
     lands), and a malformed reference INHERITED from elsewhere is flattened
     to a bare path on the way in by ``sanitize_envelope``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.contract.validator import (
    COMMIT_TOKEN_PATTERN_TEXT,
    FILE_REFERENCE_KEYS,
    FormErrorCode,
    validate_form,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI = str(_REPO_ROOT / "bin" / "cli" / "contract.py")

# Verbatim from the census of the persisted population -- each one is a real
# bare-path entry that a string-separator form would have started rejecting.
_REAL_BARE_PATHS_THAT_LOOK_LIKE_REFERENCES = [
    "/home/jorge/ws/me/node_modules/@jaguilar87/gaia/tools/memory/episodic.py",
    "/home/jorge/ws/me/gaia/hooks/modules/core/state.py (get_session_id @25)",
    "bitbucket-pipelines.yml @ f6e40af (read via Bitbucket REST src endpoint)",
    ".github/workflows/foundation.yml @ century-inc/branchkinect-iac",
    "/tmp/runtime-plan.log (CI runtime plan log, build #3)",
    "Brief bodies: workspace-identity #21, retire-legacy-context-modules #31",
    "/home/jorge/ws/me/gaia/gaia/evidence/ (package: __init__.py, fs.py, store.py)",
    "~/.gaia/gaia.db (SQLite substrate)",
]


def _valid_envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c30f1e2d3c4b5",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _with_files_checked(entries: list) -> dict:
    env = _valid_envelope()
    env["evidence_report"]["files_checked"] = entries
    return env


def _sanitize(envelope: dict, removals=None) -> dict:
    """Imported inside the call so this module still collects on a tree whose
    validator has not grown the helper yet -- each case must fail on its OWN
    behaviour, never on a collection error."""
    from gaia.contract.validator import sanitize_envelope

    return sanitize_envelope(envelope, removals=removals)


def _canonicalize(envelope: dict, changes=None) -> dict:
    from gaia.contract.validator import canonicalize_envelope

    return canonicalize_envelope(envelope, changes=changes)


def _cli(args, env, expect=0):
    proc = subprocess.run(
        [sys.executable, _CLI] + args,
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    if expect is not None:
        assert proc.returncode == expect, (
            f"{args} -> rc={proc.returncode}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


@pytest.fixture
def cli_env(tmp_path):
    return dict(
        PATH="/usr/bin:/bin",
        HOME=str(tmp_path),
        GAIA_DATA_DIR=str(tmp_path / "gaia"),
    )


# ---------------------------------------------------------------------------
# 1. Additive -- the bare path is untouched
# ---------------------------------------------------------------------------
def test_a_bare_path_is_still_accepted():
    result = validate_form(_with_files_checked(["gaia/contract/validator.py"]))

    assert result.ok is True, result.error_summary()


@pytest.mark.parametrize("entry", _REAL_BARE_PATHS_THAT_LOOK_LIKE_REFERENCES)
def test_a_real_bare_path_that_resembles_a_reference_is_accepted(entry):
    """The measured false positives of every string-separator form.

    Each of these is a bare path an agent already wrote. Under a path@sha,
    path#sha or 'path @ sha' form the trigger would fire on them and, finding
    no valid commit, reject an entry that was never trying to be a reference.
    The object form cannot: a JSON string is not a JSON object.
    """
    result = validate_form(_with_files_checked([entry]))

    assert result.ok is True, result.error_summary()


def test_an_entry_that_is_neither_string_nor_object_keeps_its_old_verdict():
    """History is not re-judged.

    One nested-list element exists in the persisted population. It was
    accepted before this form existed and is accepted after: only an element
    that DECLARES itself a reference by being an object is inspected.
    """
    result = validate_form(_with_files_checked([["/tmp/a.txt", "/tmp/b.txt"]]))

    assert result.ok is True, result.error_summary()


def test_bare_paths_and_references_mix_in_one_list():
    """The conditional rule in one envelope: some files committed, some not."""
    result = validate_form(_with_files_checked([
        {"path": "gaia/contract/validator.py", "commit": "a76789a"},
        "/tmp/scratch-notes.md",
        {"path": "gaia/bin/cli/contract.py", "commit": "0249570"},
    ]))

    assert result.ok is True, result.error_summary()


# ---------------------------------------------------------------------------
# 2. The new form
# ---------------------------------------------------------------------------
def test_a_commit_qualified_reference_is_accepted():
    result = validate_form(_with_files_checked([
        {"path": "gaia/contract/validator.py", "commit": "a76789a"},
    ]))

    assert result.ok is True, result.error_summary()


@pytest.mark.parametrize("commit", [
    "a76789a",                                    # git's default abbreviation
    "a76789ab",
    "0249570cd3f1b2a4e5d6c7b8a9f0e1d2c3b4a5f6",   # full SHA-1
    "0" * 64,                                     # full SHA-256 object name
])
def test_every_accepted_commit_token_width(commit):
    result = validate_form(_with_files_checked([{"path": "x.py", "commit": commit}]))

    assert result.ok is True, result.error_summary()


def test_a_reference_needs_no_other_field():
    """Exactly two keys, so a reader never has to guess what a third meant."""
    assert FILE_REFERENCE_KEYS == ("path", "commit")


# ---------------------------------------------------------------------------
# 3. Form, not existence -- the validator never consults git
# ---------------------------------------------------------------------------
def test_a_commit_that_does_not_exist_passes():
    """Deliberate, and the property that keeps the validator pure.

    'deadbeefdeadbeef' is a well-formed object name that exists in no
    repository on earth. Validation is about whether the reference HAS THE
    FORM of a reference; resolving it belongs to whoever reads it. Coupling
    the form layer to git would make every incremental write pay a subprocess
    and would fail an agent whose evidence is real.
    """
    result = validate_form(_with_files_checked([
        {"path": "does/not/exist.py", "commit": "deadbeefdeadbeef"},
    ]))

    assert result.ok is True, result.error_summary()


def test_validating_a_reference_runs_no_subprocess(monkeypatch):
    """The stronger statement of the same property, enforced rather than
    asserted: if the form layer ever shells out to git, this fails."""
    import subprocess as _subprocess

    def _explode(*args, **kwargs):
        raise AssertionError(f"the form layer shelled out: {args!r}")

    monkeypatch.setattr(_subprocess, "run", _explode)
    monkeypatch.setattr(_subprocess, "check_output", _explode)
    monkeypatch.setattr(_subprocess, "Popen", _explode)

    result = validate_form(_with_files_checked([
        {"path": "gaia/contract/validator.py", "commit": "cafebabe"},
    ]))

    assert result.ok is True, result.error_summary()


# ---------------------------------------------------------------------------
# 4. A malformed reference is rejected, and told what to write
# ---------------------------------------------------------------------------
def test_a_reference_without_a_commit_is_rejected():
    result = validate_form(_with_files_checked([{"path": "gaia/x.py"}]))

    assert result.ok is False
    assert FormErrorCode.FILE_REFERENCE_SHAPE in result.codes
    assert result.errors[0].field == "evidence_report.files_checked[0].commit"


def test_a_reference_without_a_path_is_rejected():
    result = validate_form(_with_files_checked([{"commit": "a76789a"}]))

    assert result.ok is False
    assert FormErrorCode.FILE_REFERENCE_SHAPE in result.codes
    assert result.errors[0].field == "evidence_report.files_checked[0].path"


@pytest.mark.parametrize("moving_ref", ["HEAD", "main", "v5.1.0", "origin/main"])
def test_a_moving_reference_is_not_a_commit(moving_ref):
    """A branch, a tag and HEAD all move, and a reference that moves dates
    nothing -- which is the only reason to carry one."""
    result = validate_form(_with_files_checked([
        {"path": "gaia/x.py", "commit": moving_ref},
    ]))

    assert result.ok is False
    assert FormErrorCode.FILE_REFERENCE_SHAPE in result.codes


@pytest.mark.parametrize("bad", ["abc", "zzzzzzz", "a76789a!", "12 34567"])
def test_a_commit_that_is_not_a_hex_object_name_is_rejected(bad):
    result = validate_form(_with_files_checked([{"path": "x.py", "commit": bad}]))

    assert result.ok is False
    assert FormErrorCode.FILE_REFERENCE_SHAPE in result.codes


def test_a_misspelled_reference_key_is_rejected_with_the_nearest_key():
    result = validate_form(_with_files_checked([
        {"path": "gaia/x.py", "commmit": "a76789a"},
    ]))

    assert result.ok is False
    assert FormErrorCode.FILE_REFERENCE_SHAPE in result.codes
    details = " ".join(err.detail for err in result.errors)
    assert "'commit'" in details, details


def test_the_rejection_tells_the_agent_exactly_what_to_write():
    """A rejection that does not carry the repair is a dead end.

    The message must name BOTH forms -- including that the bare path is a
    valid answer and not a lesser one, which is what stops an agent from
    committing something merely to be allowed to cite it.
    """
    result = validate_form(_with_files_checked([
        {"path": "gaia/x.py", "commit": "HEAD"},
    ]))
    detail = result.errors[0].detail

    assert COMMIT_TOKEN_PATTERN_TEXT in detail
    assert "git rev-parse" in detail
    assert "does not exist passes" in detail


def test_the_missing_commit_rejection_offers_the_bare_path():
    result = validate_form(_with_files_checked([{"path": "gaia/x.py"}]))
    detail = result.errors[0].detail

    assert "bare path" in detail
    assert "never a requirement on the turn" in detail


def test_each_malformed_reference_is_reported_at_its_own_index():
    result = validate_form(_with_files_checked([
        "a/plain/path.py",
        {"path": "gaia/x.py", "commit": "a76789a"},
        {"path": "gaia/y.py", "commit": "HEAD"},
    ]))

    assert result.ok is False
    assert [err.field for err in result.errors] == [
        "evidence_report.files_checked[2].commit"
    ]


# ---------------------------------------------------------------------------
# 5. The commit is persisted in the form it was validated in
# ---------------------------------------------------------------------------
def test_an_upper_case_commit_is_canonicalized_to_lower():
    """Matched stripped and lower-cased, so it must persist that way: two
    spellings of one commit are two values to every reader that groups on it."""
    changes: list = []
    env = _with_files_checked([{"path": "gaia/x.py", "commit": "  A76789A "}])

    result = _canonicalize(env, changes=changes)

    assert result["evidence_report"]["files_checked"][0]["commit"] == "a76789a"
    assert changes == [
        "evidence_report.files_checked[0].commit: '  A76789A ' -> 'a76789a'"
    ]


def test_canonicalization_never_mutates_its_input():
    env = _with_files_checked([{"path": "gaia/x.py", "commit": "A76789A"}])

    _canonicalize(env)

    assert env["evidence_report"]["files_checked"][0]["commit"] == "A76789A"


def test_a_malformed_commit_is_left_verbatim_for_the_rejection_to_quote():
    env = _with_files_checked([{"path": "gaia/x.py", "commit": "HEAD"}])

    result = _canonicalize(env)

    assert result["evidence_report"]["files_checked"][0]["commit"] == "HEAD"


# ---------------------------------------------------------------------------
# 6. No cell -- every rejection has a way out
# ---------------------------------------------------------------------------
def test_an_inherited_malformed_reference_is_flattened_to_a_bare_path():
    """The handle on the inside of the door.

    An envelope read back from a row or a resumed draft may already carry a
    malformed reference. Since every write validates the WHOLE envelope, that
    entry would reject the very write that would fix it. It is flattened to a
    bare path -- always valid -- on the way in.
    """
    removals: list = []
    env = _with_files_checked([{"path": "gaia/x.py", "commit": "HEAD"}])
    assert validate_form(env).ok is False

    cleaned = _sanitize(env, removals=removals)

    assert validate_form(cleaned).ok is True, validate_form(cleaned).error_summary()
    assert removals and "files_checked[0]" in removals[0]


def test_flattening_loses_none_of_the_agents_evidence():
    """Repair keeps what was written, the way a wrapped scalar does -- the
    path stays a path and the unusable part rides along as text."""
    cleaned = _sanitize(_with_files_checked([
        {"path": "gaia/x.py", "commit": "HEAD"},
    ]))
    entry = cleaned["evidence_report"]["files_checked"][0]

    assert isinstance(entry, str)
    assert entry.startswith("gaia/x.py")
    assert "HEAD" in entry


def test_a_valid_reference_survives_sanitization_untouched():
    """Sanitization repairs what is broken and must not touch what is not."""
    ref = {"path": "gaia/x.py", "commit": "a76789a"}
    removals: list = []

    cleaned = _sanitize(_with_files_checked([ref]), removals=removals)

    assert cleaned["evidence_report"]["files_checked"] == [ref]
    assert removals == []


def test_a_rejected_reference_leaves_the_draft_writable(cli_env):
    """The other half of the no-cell property, end to end through the CLI.

    A rejected write persists NOTHING, so the malformed entry never lands in
    the draft and the next write is unaffected. If the entry were stored
    before validation, this draft could never be written to again.
    """
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    rejected = _cli(
        ["add", "evidence_report.files_checked",
         '{"path": "gaia/x.py", "commit": "HEAD"}',
         "--draft-id", draft_id, "--json"],
        cli_env, expect=1,
    )
    assert "FILE_REFERENCE_SHAPE" in rejected.stdout + rejected.stderr

    accepted = _cli(
        ["add", "evidence_report.files_checked", "gaia/x.py",
         "--draft-id", draft_id, "--json"],
        cli_env,
    )
    assert json.loads(accepted.stdout)["status"] == "ok"

    viewed = _cli(["view", "--draft-id", draft_id, "--json"], cli_env)
    envelope = json.loads(viewed.stdout)["envelope"]
    assert envelope["evidence_report"]["files_checked"] == ["gaia/x.py"]


def test_a_draft_inheriting_a_malformed_reference_can_still_be_written(cli_env):
    """The inherited case through the real CLI: a draft file on disk already
    carrying one, as a resumed turn would find it."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    draft_file = (
        Path(cli_env["GAIA_DATA_DIR"]) / "contract_drafts" / f"{draft_id}.json"
    )
    stored = json.loads(draft_file.read_text())
    stored["evidence_report"]["files_checked"] = [
        {"path": "gaia/x.py", "commit": "HEAD"}
    ]
    draft_file.write_text(json.dumps(stored))

    written = _cli(
        ["add", "evidence_report.open_gaps", "still open",
         "--draft-id", draft_id, "--json"],
        cli_env,
    )

    payload = json.loads(written.stdout)
    assert payload["status"] == "ok"
    # Announced on both paths -- a repair the writer cannot see is the same
    # defect as a rejection it cannot see.
    assert any("files_checked" in line for line in payload["sanitized"])
    assert "[SANITIZED]" in written.stderr


def test_the_cli_accepts_a_reference_and_stores_it_canonical(cli_env):
    """End to end: `gaia contract add` already JSON-parses its value, so the
    object form needs no new verb."""
    created = _cli(["init", "--json"], cli_env)
    draft_id = json.loads(created.stdout)["draft_id"]

    _cli(
        ["add", "evidence_report.files_checked",
         '{"path": "gaia/contract/validator.py", "commit": "A76789A"}',
         "--draft-id", draft_id, "--json"],
        cli_env,
    )

    viewed = _cli(["view", "--draft-id", draft_id, "--json"], cli_env)
    envelope = json.loads(viewed.stdout)["envelope"]
    assert envelope["evidence_report"]["files_checked"] == [
        {"path": "gaia/contract/validator.py", "commit": "a76789a"}
    ]


# ---------------------------------------------------------------------------
# 7. The reader resolves it
# ---------------------------------------------------------------------------
def test_the_evidence_reader_renders_a_reference_as_path_at_commit():
    """The object is the STORED form because no string form can be told apart
    from a bare path on the way in. That is not a constraint on the way out:
    a reader gets the git-pasteable spelling, not a Python repr."""
    sys.path.insert(0, str(_REPO_ROOT))
    from hooks.modules.agents.response_contract import parse_evidence_report

    block = parse_evidence_report("", {
        "evidence_report": {
            "files_checked": [
                {"path": "gaia/contract/validator.py", "commit": "a76789a"},
                "/tmp/uncommitted-scratch.md",
            ],
        },
    })

    assert block.fields["FILES_CHECKED"] == [
        "gaia/contract/validator.py@a76789a",
        "/tmp/uncommitted-scratch.md",
    ]
