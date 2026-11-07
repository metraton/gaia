"""Who counts as the gaia CLI for the data exemptions, and which gaia arguments are files it reads."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, protected_paths, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "a" + "7" * 16
DRAFT = f"{AGENT}.4df5e309bb03"
FILL_TAIL = f" contract fill --draft-id {DRAFT} --json-file - <<'EOF'\ncat ~/.aws/credentials\nEOF"
SECRET = "{H}/.aws/credentials"

NOT_THE_GAIA_CLI = {
    "symlink_named_gaia_to_fake": "{L}/gaia" + FILL_TAIL,
    "fake_package_declaring_gaia_bin": "{P}/bin/gaia" + FILL_TAIL,
    "relative_fake_gaia": "scratch/gaia" + FILL_TAIL,
    "path_prefix_expansion": "PATH={S}:$PATH gaia" + FILL_TAIL,
    "path_prefix_literal": "PATH={S} gaia" + FILL_TAIL,
    "env_path_prefix": "env PATH={S} gaia" + FILL_TAIL,
    "function_same_line": "gaia() {{ sh; }}; gaia" + FILL_TAIL,
    "alias_same_line": "alias gaia=sh; gaia" + FILL_TAIL,
    "function_previous_line": "gaia() {{ sh; }}\ngaia" + FILL_TAIL,
}

CREDENTIAL_CONTENT_FILES = {
    "json_file_equals": f"gaia contract fill --draft-id {DRAFT} --json-file={SECRET}",
    "json_file_equals_tilde": f"gaia contract fill --draft-id {DRAFT} --json-file=~/.aws/credentials",
    "json_file_single_quoted": f"gaia contract fill --draft-id {DRAFT} --json-file '{SECRET}'",
    "json_file_tilde": f"gaia contract fill --draft-id {DRAFT} --json-file ~/.aws/credentials",
    "json_file_dollar_home": f'gaia contract fill --draft-id {DRAFT} --json-file "$HOME/.aws/credentials"',
    "json_file_glob": f"gaia contract fill --draft-id {DRAFT} --json-file {{H}}/.aws/cred*",
    "json_file_symlink_to_secret": f"gaia contract fill --draft-id {DRAFT} --json-file {{T}}/notes.txt",
    "json_file_abbrev_json_f": f"gaia contract fill --draft-id {DRAFT} --json-f {SECRET}",
    "json_file_abbrev_json_fi_equals": f"gaia contract fill --draft-id {DRAFT} --json-fi={SECRET}",
    "gcloud_adc_abbrev": f"gaia contract fill --draft-id {DRAFT} --json-f ~/.config/gcloud/application_default_credentials.json",
    "memory_append_body_file": f"gaia memory append some-slug --body-file {SECRET}",
    "memory_append_body_abbrev": f"gaia memory append some-slug --body-fi {SECRET}",
    "evidence_artifact_file": f"gaia evidence add --brief b --ac AC-1 --type file --artifact-file {SECRET}",
    "evidence_artifact_abbrev": f"gaia evidence add --brief b --ac AC-1 --type file --artifact-f {SECRET}",
    "evidence_artifact_abbrev_short": f"gaia evidence add --brief b --ac AC-1 --type file --artifact {SECRET}",
    "memory_checkpoint_file": f"gaia memory checkpoint --file {SECRET}",
    "ssh_key_artifact_file": "gaia evidence add --brief b --ac AC-1 --type file --artifact-file ~/.ssh/id_rsa",
    "fake_gaia_reads_positional": f"{{S}}/gaia {SECRET}",
}

INSTALL_ROOT_WRITES = {
    "fake_package_contract_names_root": f'{{P}}/bin/gaia contract add --draft-id {DRAFT} evidence_report.key_outputs "{{I}}/bin/evil.py"',
    "symlink_gaia_contract_names_root": f'{{L}}/gaia contract add --draft-id {DRAFT} evidence_report.key_outputs "{{I}}/bin/evil.py"',
}


@pytest.fixture
def probe(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    protected_paths.reset_caches()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = scratch / "gaia"
    fake.write_text("#!/bin/sh\nexec sh\n", encoding="utf-8")
    fake.chmod(0o755)
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    (link_dir / "gaia").symlink_to(fake)
    package = tmp_path / "fakepkg"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"name": "@jaguilar87/gaia", "bin": {"gaia": "bin/gaia"}}', encoding="utf-8")
    (package / "bin" / "gaia").write_text("#!/bin/sh\nexec sh\n", encoding="utf-8")
    (package / "bin" / "gaia").chmod(0o755)
    (tmp_path / "notes.txt").symlink_to(home / ".aws" / "credentials")
    install = tmp_path / "data" / "cache" / "dev-plugin" / "ws"
    for sub in ("bin/__pycache__", "gaia", "hooks", "agents"):
        (install / sub).mkdir(parents=True, exist_ok=True)
    (install / "package.json").write_text('{"name": "@jaguilar87/gaia"}', encoding="utf-8")
    (install / "bin" / "gaia").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def verdict(command: str) -> str:
        mutative_verbs.detect_mutative_command.cache_clear()
        mutative_verbs._detect_mutative_command.cache_clear()
        tiers._classify_command_tier_cached.cache_clear()
        payload = {"cwd": str(tmp_path), "agent_id": AGENT, "agent_type": "developer", "session_id": "s-probe"}
        result = BashValidator().validate(
            command, is_subagent=True, session_id="s-probe", agent_type="developer",
            hook_payload=payload, cwd=str(tmp_path),
        )
        if result.allowed:
            return "FREE"
        return "SIGNED" if result.approval_id else result.rule

    names = {"H": str(home), "S": str(scratch), "L": str(link_dir), "P": str(package), "T": str(tmp_path), "I": str(install)}
    yield verdict, names
    protected_paths.reset_caches()


def test_the_bare_gaia_word_keeps_its_data_heredoc(probe):
    verdict, names = probe
    assert verdict(("gaia" + FILL_TAIL).format(**names)) == "FREE"


@pytest.mark.parametrize("name", sorted(NOT_THE_GAIA_CLI))
def test_a_receiver_that_is_not_the_gaia_cli_gets_no_data_exemption(probe, name):
    verdict, names = probe
    assert verdict(NOT_THE_GAIA_CLI[name].format(**names)) != "FREE"


@pytest.mark.parametrize("name", sorted(CREDENTIAL_CONTENT_FILES))
def test_a_credential_a_gaia_call_reads_is_a_secret_read(probe, name):
    verdict, names = probe
    assert verdict(CREDENTIAL_CONTENT_FILES[name].format(**names)) != "FREE"


@pytest.mark.parametrize("name", sorted(INSTALL_ROOT_WRITES))
def test_an_untrusted_gaia_naming_an_install_root_is_still_a_write(probe, name):
    verdict, names = probe
    assert verdict(INSTALL_ROOT_WRITES[name].format(**names)) == "floor.protected-path"
