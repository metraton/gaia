"""Install-anchored gaia trust, gaia option values as reads, standalone entry points, and moves of an install's parents."""

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, protected_paths, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "a" + "5" * 16
DRAFT = f"{AGENT}.2d462364c60d"
TAIL = f" contract fill --draft-id {DRAFT} --json-file - <<'EOF'\ncat ~/.aws/credentials\nEOF"
SEC = "{H}/.aws/credentials"
NOT_FREE = "NOT_FREE"
ANY = "ANY"

TRUST = {
    "c1_real_absolute_control": ("{I}/bin/gaia" + TAIL, "FREE"),
    "c1_symlink_in_entry_to_real": ("{E}/gaia" + TAIL, ANY),
    "c1_symlink_other_name_to_real": ("{E}/notgaia" + TAIL, ANY),
    "c1_symlinked_dir_to_real_bin": ("{E}/b/gaia" + TAIL, ANY),
    "c1_hardlink_in_entry": ("{E}/hl/gaia" + TAIL, NOT_FREE),
    "c1_copy_of_real": ("{E}/copy/bin/gaia" + TAIL, NOT_FREE),
    "c1_proc_self_root": ("/proc/self/root{I}/bin/gaia" + TAIL, ANY),
    "c1_dotdot": ("{I}/bin/../bin/gaia" + TAIL, ANY),
    "c1_dotdot_via_entry": ("{E}/../../cache/dev-plugin/ws/bin/gaia" + TAIL, ANY),
    "c1_relative_real": ("data/cache/dev-plugin/ws/bin/gaia" + TAIL, NOT_FREE),
    "c1_quoted_real": ("'{I}/bin/gaia'" + TAIL, NOT_FREE),
    "c1_npm_launcher_other_install": ("{T}/proj/node_modules/.bin/gaia" + TAIL, NOT_FREE),
    "c1_npm_package_other_install": ("{N}/bin/gaia" + TAIL, NOT_FREE),
    "c1_retarget_then_run_same_line": ("mv {E}/fake {E}/gaia && {E}/gaia" + TAIL, NOT_FREE),
    "c1_retarget_then_run_newline": ("mv {E}/fake {E}/gaia\n{E}/gaia" + TAIL, NOT_FREE),
    "c1_python_real_dispatcher": ("python3 {I}/bin/gaia" + TAIL, NOT_FREE),
}

READS = {
    "c2_objective_file": f"gaia brief create x --objective-file {SEC}",
    "c2_context_file_eq": f"gaia brief create x --context-file={SEC}",
    "c2_approach_file": f"gaia brief create x --approach-file {SEC}",
    "c2_out_of_scope_file": f"gaia brief create x --out-of-scope-file={SEC}",
    "c2_description_file": f"gaia task add b --description-file {SEC}",
    "c2_evidence_shape_file_eq": f"gaia task gate add b 1 --evidence-shape-file={SEC}",
    "c2_artifact_path": f"gaia task gate add b 1 --artifact-path {SEC}",
    "c2_artifact": f"gaia evidence add --brief b --ac AC-1 --artifact {SEC}",
    "c2_goal_file": f"gaia plan create b --goal-file {SEC}",
    "c2_content_file": f"gaia memory add --slug x --content-file {SEC}",
    "c2_content_file_eq": f"gaia memory add --slug x --content-file={SEC}",
    "c2_file_eq": f"gaia memory checkpoint --file={SEC}",
    "c2_db_path": f"gaia doctor --db-path {SEC}",
    "c2_from": f"gaia context relocate --from {SEC}",
    "c2_global_json_first": f"gaia --json contract fill --draft-id {DRAFT} --json-file {SEC}",
    "c2_end_of_options_then_secret": f"gaia contract fill --draft-id {DRAFT} -- {SEC}",
    "c2_dotdot_spelling": "gaia contract fill --json-file {H}/.ssh/../.aws/credentials",
    "c2_double_slash": "gaia contract fill --json-file {H}//.aws//credentials",
    "c2_proc_self_root": "gaia contract fill --json-file /proc/self/root{H}/.aws/credentials",
    "c2_proc_self_cwd": "gaia contract fill --json-file /proc/self/cwd/home/.aws/credentials",
    "c2_symlinked_dir": "gaia contract fill --json-file {T}/awsdir/credentials",
    "c2_stdin_redirect": f"gaia contract fill --draft-id {DRAFT} --json-file - < {SEC}",
    "c2_dev_stdin_redirect": f"gaia contract fill --draft-id {DRAFT} --json-file /dev/stdin < {SEC}",
    "c2_xargs_feeds_value": f"echo {SEC} | xargs gaia contract fill --draft-id {DRAFT} --json-file",
    "c2_env_wrapper": f"env gaia contract fill --json-file {SEC}",
    "c2_command_wrapper": f"command gaia contract fill --json-file {SEC}",
    "c2_timeout_wrapper": f"timeout 5 gaia contract fill --json-file {SEC}",
    "c2_assignment_prefix": f"GAIA_X=1 gaia contract fill --json-file {SEC}",
    "c2_real_abs_install_gaia": f"{{I}}/bin/gaia contract fill --json-file {SEC}",
    "c2_ssh_key_body_file": "gaia memory append s --body-file {H}/.ssh/id_rsa",
}

DATA = {
    "c2_positional_stays_data": (f"gaia contract add --draft-id {DRAFT} evidence_report.key_outputs {SEC}", "FREE"),
    "c2_ordinary_file_option": (f"gaia contract fill --draft-id {DRAFT} --json-file {{T}}/ok.json", "FREE"),
}

STANDALONE = {
    "c3_standalone_contract_abbrev_secret": (f"python3 {{I}}/bin/cli/contract.py fill --draft-id {DRAFT} --json-f {SEC}", NOT_FREE),
    "c3_standalone_contract_full_secret": (f"python3 {{I}}/bin/cli/contract.py fill --draft-id {DRAFT} --json-file {SEC}", NOT_FREE),
    "c3_standalone_contract_abbrev_eq_secret": (f"python3 {{I}}/bin/cli/contract.py fill --draft-id {DRAFT} --json-fi={SEC}", NOT_FREE),
    "c3_standalone_approvals_abbrev": ("python3 {I}/bin/cli/approvals.py list --stat pending", ANY),
    "c3_standalone_contract_abbrev_ok": (f"python3 {{I}}/bin/cli/contract.py fill --draft-id {DRAFT} --json-f {{T}}/ok.json", ANY),
    "c3_module_contract_abbrev_secret": (f"cd {{I}}/bin && python3 -m cli.contract fill --draft-id {DRAFT} --json-f {SEC}", NOT_FREE),
}

MOVES = {
    "c4_mv_dev_plugin_out": "mv {D}/cache/dev-plugin {E}/moved",
    "c4_mv_cache_out": "mv {D}/cache {E}/moved",
    "c4_rename_dev_plugin_in_place": "mv {D}/cache/dev-plugin {D}/cache/dp2",
    "c4_mv_install_entry_out": "mv {I} {E}/moved",
    "c4_mv_npm_scope": "mv {T}/proj/node_modules/@jaguilar87 {T}/proj/node_modules/x",
    "c4_mv_npm_node_modules": "mv {T}/proj/node_modules {E}/nm",
    "c4_cd_then_relative_mv": "cd {D}/cache && mv dev-plugin x",
    "c4_python_os_rename": "python3 -c \"import os; os.rename('{D}/cache/dev-plugin', '{E}/m')\"",
    "c4_mv_back_into_root_shape": "mv {E}/staged {D}/cache/dev-plugin/ws2",
}


def _validate(command, cwd):
    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "agent_id": AGENT, "agent_type": "developer", "session_id": "s-v"}
    result = BashValidator().validate(
        command, is_subagent=True, session_id="s-v", agent_type="developer", hook_payload=payload, cwd=cwd,
    )
    if result.allowed:
        return "FREE"
    return "SIGNED" if result.approval_id else result.rule


@pytest.fixture
def verdict(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".aws" / "credentials").write_text("[default]\n", encoding="utf-8")
    (home / ".ssh" / "id_rsa").write_text("k\n", encoding="utf-8")
    (tmp_path / "awsdir").symlink_to(home / ".aws")
    (tmp_path / "ok.json").write_text("{}", encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _s: True)
    protected_paths.reset_caches()
    install = data / "cache" / "dev-plugin" / "ws"
    for sub in ("bin/cli", "gaia", "hooks", "agents"):
        (install / sub).mkdir(parents=True, exist_ok=True)
    (install / "package.json").write_text('{"name": "@jaguilar87/gaia"}', encoding="utf-8")
    shutil.copy2(REPO_ROOT / "bin" / "gaia", install / "bin" / "gaia")
    for name in ("contract.py", "approvals.py", "__init__.py"):
        shutil.copy2(REPO_ROOT / "bin" / "cli" / name, install / "bin" / "cli" / name)
    monkeypatch.setattr(mutative_verbs, "_HOOK_ROOT", install)
    monkeypatch.setattr(mutative_verbs, "_hook_root_is_install", lambda: True)
    npm = tmp_path / "proj" / "node_modules" / "@jaguilar87" / "gaia"
    (npm / "bin").mkdir(parents=True)
    (npm / "package.json").write_text('{"name": "@jaguilar87/gaia", "bin": {"gaia": "bin/gaia"}}', encoding="utf-8")
    shutil.copy2(REPO_ROOT / "bin" / "gaia", npm / "bin" / "gaia")
    (tmp_path / "proj" / "node_modules" / ".bin").mkdir()
    (tmp_path / "proj" / "node_modules" / ".bin" / "gaia").symlink_to(npm / "bin" / "gaia")
    entry = data / "scratch" / DRAFT
    for sub in ("hl", "copy/bin", "staged"):
        (entry / sub).mkdir(parents=True)
    (entry / "gaia").symlink_to(install / "bin" / "gaia")
    (entry / "notgaia").symlink_to(install / "bin" / "gaia")
    (entry / "b").symlink_to(install / "bin")
    os.link(install / "bin" / "gaia", entry / "hl" / "gaia")
    shutil.copy2(install / "bin" / "gaia", entry / "copy" / "bin" / "gaia")
    (entry / "fake").write_text("#!/bin/sh\nexec sh\n", encoding="utf-8")
    names = {"H": str(home), "T": str(tmp_path), "D": str(data), "I": str(install), "N": str(npm), "E": str(entry)}
    yield lambda command: _validate(command.format(**names), str(tmp_path))
    protected_paths.reset_caches()


def _holds(got, expected):
    return expected == ANY or (got != "FREE" if expected == NOT_FREE else got == expected)


@pytest.mark.parametrize("name", sorted(TRUST))
def test_c1_trust(verdict, name):
    template, expected = TRUST[name]
    got = verdict(template)
    assert _holds(got, expected), f"{name}: {got}"


@pytest.mark.parametrize("name", sorted(READS))
def test_c2_reads(verdict, name):
    got = verdict(READS[name])
    assert got != "FREE", f"{name}: {got}"


@pytest.mark.parametrize("name", sorted(DATA))
def test_c2_data(verdict, name):
    template, expected = DATA[name]
    got = verdict(template)
    assert _holds(got, expected), f"{name}: {got}"


@pytest.mark.parametrize("name", sorted(STANDALONE))
def test_c3_standalone(verdict, name):
    template, expected = STANDALONE[name]
    got = verdict(template)
    assert _holds(got, expected), f"{name}: {got}"


@pytest.mark.parametrize("name", sorted(MOVES))
def test_c4_moves(verdict, name):
    got = verdict(MOVES[name])
    assert got != "FREE", f"{name}: {got}"
