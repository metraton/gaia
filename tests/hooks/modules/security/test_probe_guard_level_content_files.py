"""The secret-read guard's own verdict on gaia content files, which holds where no role floor masks it."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import sensitive_read_guard  # noqa: E402

CASES = {
    "memory_append_body_file": "gaia memory append some-slug --body-file {H}/.aws/credentials",
    "memory_append_body_abbrev": "gaia memory append some-slug --body-fi {H}/.aws/credentials",
    "memory_checkpoint_file": "gaia memory checkpoint --file {H}/.aws/credentials",
    "contract_fill_json_abbrev": "gaia contract fill --draft-id x --json-f {H}/.aws/credentials",
    "evidence_artifact_abbrev": "gaia evidence add --artifact-f {H}/.aws/credentials",
}


@pytest.fixture
def home(monkeypatch, tmp_path):
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    return str(tmp_path)


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_credential_gaia_reads_as_content_is_refused_by_the_guard(home, name):
    assert sensitive_read_guard.check(CASES[name].format(H=home))[0] is False


@pytest.mark.parametrize("command", [
    "gaia contract add --draft-id x evidence_report.key_outputs {H}/.aws/credentials",
    "gaia contract fill --draft-id x --json-file {H}/payload.json",
])
def test_a_stored_value_or_an_ordinary_file_is_not_a_secret_read(home, command):
    assert sensitive_read_guard.check(command.format(H=home))[0] is True
