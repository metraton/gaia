"""The consent verdict by effect and target, one data file driven through BashValidator.validate."""

from __future__ import annotations

import ast
import contextlib
import inspect
import json
import os
import shlex
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.blocked_commands import BLOCKED_PATTERNS  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

TABLE = json.loads((Path(__file__).parent / "data" / "effect_truth_table.json").read_text(encoding="utf-8"))
HEADER, ROWS = TABLE["header"], TABLE["rows"]
VERDICTS = {"FREE", "SIGNED", "BLOCKED", "UNKNOWN"}
OWNER = re.compile(r"^(T1|T[2-9]|T1[01]|out-of-brief)$")
EXTERNAL = {"codex": "Apache-2.0", "continue": "Apache-2.0", "roo": "Apache-2.0", "gptme": "MIT", "oh-sdk": "MIT"}
SOURCE_VERDICT = {"free": "FREE", "ask": "SIGNED", "deny": "BLOCKED", "unknown": "UNKNOWN"}
COMPOUND_DENIAL = "Compound T3 execution is disabled"
UNDETERMINED = "could not be determined"
AMBIENT_CONFIG = ("KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "AZURE_CONFIG_DIR", "PULUMI_HOME")
AMBIENT_NAMES = ("DOCKER_CONTEXT", "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME",
                 "AWS_PROFILE", "TF_WORKSPACE", "AZURE_SUBSCRIPTION_ID", "CONTAINER_CONNECTION")


def _isolate_targets(patch, root: Path) -> None:
    for name in AMBIENT_CONFIG:
        patch.setenv(name, str(root / "no-ambient-config" / name))
    for name in AMBIENT_NAMES:
        patch.delenv(name, raising=False)


@contextlib.contextmanager
def declared(directory: Path, payload: dict):
    """*payload* as the ``targets`` project-context section of a project rooted at *directory*, in the test's GAIA_DB."""
    from gaia.store.writer import _connect

    identity = f"truth-table/{directory.name}"
    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('truth-table')")
        con.execute("INSERT OR REPLACE INTO projects (workspace, name, path, status, project_identity) "
                    "VALUES ('truth-table', ?, ?, 'active', ?)", (directory.name, str(directory), identity))
        con.execute("INSERT OR REPLACE INTO project_context (project_identity, contract_name, payload) VALUES (?, 'targets', ?)",
                    (identity, json.dumps(payload)))
        con.commit()
        yield
    finally:
        con.execute("DELETE FROM project_context WHERE project_identity = ?", (identity,))
        con.execute("DELETE FROM projects WHERE project_identity = ?", (identity,))
        con.commit()
        con.close()


def _build_context(spec: dict, root: Path, name: str, managed_root: Path) -> Path:
    base = managed_root / "wt-a" if spec.get("git") == "managed" else root / "ctx" / name
    base.mkdir(parents=True, exist_ok=True)
    if spec.get("git"):
        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(base), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "init")
        git("branch", "feature-x")
        git("branch", "release/1.x")
        git("remote", "add", "origin", "https://example.invalid/o/r.git")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("update-ref", "refs/remotes/origin/feature-x", "feature-x")
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        for remote, url in spec.get("remotes", {}).items():
            git("remote", "add", remote, url)
    for branch, files in spec.get("branches", {}).items():
        git("checkout", "-q", "-b", branch)
        for rel, text in files.items():
            (base / rel).write_text(text, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", branch)
        git("checkout", "-q", "main")
    for rel, text in spec.get("files", {}).items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return base


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("truth-table")
    patch = pytest.MonkeyPatch()
    patch.setenv("GAIA_DATA_DIR", str(root / "gaia-data"))
    patch.setenv("GAIA_DB", str(root / "gaia-data" / "gaia.db"))
    _isolate_targets(patch, root)
    from gaia.paths import ensure_layout, scratch_dir, worktrees_dir

    ensure_layout()
    managed_root = Path(os.path.realpath(str(worktrees_dir())))
    dirs = {name: _build_context(spec, root, name, managed_root) for name, spec in HEADER["contexts"].items()}
    turn_entry = os.path.join(os.path.realpath(str(scratch_dir())), "atruthtable01.token")
    os.makedirs(turn_entry)
    with tarfile.open(os.path.join(turn_entry, "a.tar"), "w") as archive:
        archive.addfile(tarfile.TarInfo("pkg/empty.txt"))
    yield {"root": root, "dirs": dirs, "SCRATCH": turn_entry, "WORKTREES": str(managed_root)}
    patch.undo()


def _nested_shells(depth: int) -> str:
    command = "true"
    for _ in range(depth):
        command = "bash -c " + shlex.quote(command)
    return command


def observe(command: str, context: str, world: dict, role: str = "subagent", session: bool = True) -> tuple:
    for key, value in (("{CWD}", str(world["dirs"][context])), ("{SCRATCH}", world["SCRATCH"]), ("{WORKTREES}", world["WORKTREES"]),
                       ("{NEST5}", _nested_shells(5)), ("{GAIA}", str(_REPO_ROOT / "bin" / "gaia"))):
        command = command.replace(key, value)
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    subagent = role == "subagent"
    session_id = "s-truth-table" if session else ""
    payload = {"cwd": str(world["dirs"][context]), "tool_use_id": "t-truth-table"}
    if session:
        payload["session_id"] = session_id
    if subagent:
        payload.update(agent_id="atruthtable01", agent_type="developer")
    result = BashValidator(agent_names_scratch=True).validate(command, is_subagent=subagent, session_id=session_id,
                                      agent_type="developer" if subagent else "", hook_payload=payload)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith(COMPOUND_DENIAL):
        return ("UNKNOWN" if UNDETERMINED in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


def _case(row: dict):
    marks = []
    if row["owner"] == "out-of-brief":
        marks.append(pytest.mark.skip(reason=f"out-of-brief: the legs say {row['expected']}; listed, not asserted"))
    elif row["owner"] != "T1":
        marks.append(pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"owned by {row['owner']}: expected {row['expected']}"))
    return pytest.param(row, id=f"{row['category'].replace('-', '_')}-{row['id']}", marks=marks)


def _observe_isolated(row, world, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(world["root"] / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(world["root"] / "gaia-data" / "gaia.db"))
    _isolate_targets(monkeypatch, world["root"])
    monkeypatch.chdir(world["root"])
    assert os.environ["GAIA_DB"].startswith(str(world["root"]))
    if row.get("seed_contracts"):
        seeded = row["seed_contracts"]

        def fake_list(*, harness_agent_id=None, contract_id=None, limit=100, **_):
            return [c for c in seeded if c.get("harness_agent_id") == harness_agent_id or c.get("contract_id") == contract_id][:limit]

        monkeypatch.setattr("gaia.store.writer.list_agent_contract_handoffs", fake_list)
    context = row.get("context", "none")
    with declared(world["dirs"][context], row["declare"]) if row.get("declare") else contextlib.nullcontext():
        return observe(row["command"], context, world, row.get("role", "subagent"), not row.get("no_session"))


@pytest.mark.parametrize("row", [_case(r) for r in ROWS])
def test_verdict(row, world, monkeypatch):
    verdict, reason = _observe_isolated(row, world, monkeypatch)
    assert verdict == row["expected"], row["command"]
    if row.get("reason_contains"):
        assert row["reason_contains"].lower() in reason.lower(), reason


@pytest.mark.parametrize("row", [pytest.param(r, id=r["id"]) for r in ROWS if r["category"] in ("egress", "secret-store") and r["owner"] == "T1"])
def test_egress_and_credential_store_rows_hold_under_a_tmp_path_home(row, world, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert _observe_isolated(row, world, monkeypatch)[0] == row["expected"], row["command"]


@pytest.mark.parametrize("row", [pytest.param(r, id=r["id"]) for r in ROWS if r["owner"] == "T1" and r["expected"] != "FREE"])
def test_every_denial_names_its_rule_and_remediation(row, world, monkeypatch):
    reason = _observe_isolated(row, world, monkeypatch)[1]
    ruling = re.search(r"Rule: (\S+)\. Remediation: (\S.*\.)$", reason, re.M)
    assert ruling and ruling.group(1) == row["rule"], reason


@pytest.mark.parametrize("row", [pytest.param(r, id=r["id"]) for r in ROWS if r["expected"] == "UNKNOWN" and r["owner"] != "out-of-brief"])
def test_an_undeterminable_row_asks_today(row, world, monkeypatch):
    assert _observe_isolated(row, world, monkeypatch)[0] in {"SIGNED", "UNKNOWN"}, row["command"]


def test_every_row_carries_its_provenance():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids))
    for row in ROWS:
        for key in ("id", "command", "expected", "rule", "category", "owner", "src"):
            assert row.get(key), (row.get("id"), key)
        assert row["expected"] in VERDICTS and OWNER.match(row["owner"]), row["id"]
        assert row.get("context", "none") in HEADER["contexts"], row["id"]
        source = HEADER["sources"][row["src"]]
        assert source["license"] and re.fullmatch(r"[0-9a-f]{40}", source["commit"]), row["id"]


def test_seed_rows_come_only_from_the_permissive_corpora():
    used = {r["src"] for r in ROWS} - {"gaia-authored", "gaia-mined", "gaia-floor"}
    assert used <= set(EXTERNAL)
    for name, license_name in EXTERNAL.items():
        assert HEADER["sources"][name]["license"] == license_name
        assert HEADER["sources"][name]["copyright"]
    assert "deny-pack" in HEADER["attribution"] and not any("dcg" in json.dumps(r).lower() for r in ROWS)


def test_seed_rows_say_where_their_verdict_differs_from_the_source():
    for row in (r for r in ROWS if r["src"] in EXTERNAL):
        assert row["source_ref"] and row["source_verdict"] in SOURCE_VERDICT, row["id"]
        differs = SOURCE_VERDICT[row["source_verdict"]] != row["expected"]
        assert bool(row.get("why_differs")) == differs, row["id"]


def test_every_blocked_pattern_of_the_floor_has_a_row():
    floor = [r["command"] for r in ROWS if r["expected"] == "BLOCKED" and r["rule"].startswith("floor.")]
    for key, patterns in BLOCKED_PATTERNS.items():
        for pattern in patterns:
            assert any(pattern.search(command) for command in floor), (key, pattern.pattern)


def test_no_case_expected_to_be_signed_meets_the_floor(world, monkeypatch):
    refused = [r for r in ROWS if r["expected"] == "SIGNED" and _observe_isolated(r, world, monkeypatch)[0] == "BLOCKED"]
    assert not refused, [r["id"] for r in refused]


def test_each_change_request_shape_spans_every_binary():
    shapes = {"create", "edit", "view", "list", "comment", "ready", "approve", "close", "merge", "retarget"}
    for binary in ("gh", "ghwrap", "forgectl", "glab", "tea", "az-repos"):
        found = {r["id"].split("-")[-1] for r in ROWS if r["id"].startswith(f"forge-{binary}-")}
        assert found >= {"create", "edit", "view", "list", "approve", "close", "merge"}, binary
    assert {r["id"].split("-")[-1] for r in ROWS if r["id"].startswith("forge-gh-")} >= shapes


CHANGE_REQUEST_OPERATIONS = {
    "create --title t --body b": "FREE",
    "edit 42 --title t": "FREE",
    "update 42 --title t": "FREE",
    "view 42": "FREE",
    "list": "FREE",
    "comment 42 --body hi": "SIGNED",
    "note 42 --message hi": "SIGNED",
    "ready 42": "SIGNED",
    "review 42 --approve": "SIGNED",
    "close 42": "SIGNED",
    "merge 42 --squash": "SIGNED",
    "edit 42 --base main": "SIGNED",
    "create --title t --auto-merge": "SIGNED",
}

CHANGE_REQUEST_SPELLINGS = {
    "gh": "gh pr {op}",
    "ghx": "ghx pr {op}",
    "glab": "glab mr {op}",
    "tea": "tea pulls {op}",
    "env-wrapped": "env GH_HOST=example.invalid gh pr {op}",
    "timeout-wrapped": "timeout 30 ghx -C /work/tree pr {op}",
    "declared-wrapper": "mywrap pr {op}",
    "quoted": "'ghx' 'pr' \"{verb}\" {rest}",
}


@pytest.mark.parametrize("operation", list(CHANGE_REQUEST_OPERATIONS))
def test_a_change_request_operation_gets_one_verdict_whatever_the_binary(operation, world, monkeypatch, declare_alias):
    monkeypatch.setenv("GAIA_DATA_DIR", str(world["root"] / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(world["root"] / "gaia-data" / "gaia.db"))
    monkeypatch.chdir(world["root"])
    declare_alias("mywrap=gh")
    verb, _, rest = operation.partition(" ")
    verdicts = {}
    for name, template in CHANGE_REQUEST_SPELLINGS.items():
        command = template.format(op=operation, verb=verb, rest=rest)
        verdicts[command] = observe(command, "none", world)[0]
    assert set(verdicts.values()) == {CHANGE_REQUEST_OPERATIONS[operation]}, verdicts


def test_every_brief_criterion_has_rows_and_every_tightening_has_a_free_twin():
    for ac in ("AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6", "AC-7", "AC-8"):
        assert any(ac in r.get("ac", []) for r in ROWS), ac
    for category in ("lexicon", "target", "executor", "local-work", "inline-code", "egress", "secret-store"):
        verdicts = {r["expected"] for r in ROWS if r["category"] == category}
        assert "FREE" in verdicts and verdicts - {"FREE"}, category


TWINS = [
    ("ctl-push-feature", "mined-push-default-twin"),
    ("ctl-ff-own-upstream", "git-merge-ff-other-branch"),
    ("ctl-npm-ci-no-lifecycle", "ctl-npm-ci-postinstall"),
    ("mined-scratch-tar-x", "mined-tar-x-root-twin"),
    ("forge-gh-edit", "forge-gh-retarget"),
    ("forge-own-repo-gh-create", "forge-other-repo-gh-create"),
    ("ctl-gcloud-login-no-activate", "ctl-gcloud-login"),
]


@pytest.mark.parametrize("free_id,signed_id", TWINS)
def test_a_twin_flips_on_its_target_or_effect_alone(free_id, signed_id):
    by_id = {r["id"]: r for r in ROWS}
    free, signed = by_id[free_id], by_id[signed_id]
    assert free["owner"] == signed["owner"] == "T1"
    assert (free["expected"], signed["expected"]) == ("FREE", "SIGNED")
    left, right = free["command"].split(), signed["command"].split()
    changed = sum(a != b for a, b in zip(left, right)) + abs(len(left) - len(right))
    assert changed <= 2 or free["command"] == signed["command"]
    if free["command"] == signed["command"]:
        assert free.get("context") != signed.get("context")


AC_SHAPES = {
    "AC-1": {
        "create": ["forge-gh-create", "forge-ghwrap-create", "forge-glab-create", "forge-tea-create", "forge-az-repos-create", "forge-forgectl-create"],
        "edit": ["forge-gh-edit", "forge-ghwrap-edit", "forge-glab-edit", "forge-tea-edit", "forge-az-repos-edit", "forge-forgectl-edit"],
        "view and list": ["forge-gh-view", "forge-gh-list", "forge-glab-view", "forge-tea-list", "forge-az-repos-view", "forge-forgectl-list"],
        "comment": ["forge-gh-comment", "forge-ghwrap-comment", "forge-glab-comment", "forge-tea-comment", "forge-forgectl-comment"],
        "mark ready": ["forge-gh-ready", "forge-ghwrap-ready", "forge-glab-ready", "forge-az-repos-ready", "forge-forgectl-ready"],
        "review and approve": ["forge-gh-approve", "forge-ghwrap-approve", "forge-glab-approve", "forge-tea-approve", "forge-az-repos-approve", "forge-forgectl-approve"],
        "close": ["forge-gh-close", "forge-ghwrap-close", "forge-glab-close", "forge-tea-close", "forge-az-repos-close", "forge-forgectl-close"],
        "merge": ["forge-gh-merge", "forge-ghwrap-merge", "forge-glab-merge", "forge-tea-merge", "forge-az-repos-merge", "forge-forgectl-merge"],
        "retarget to main": ["forge-gh-retarget", "forge-ghwrap-retarget", "forge-glab-retarget", "forge-az-repos-retarget", "forge-forgectl-retarget"],
    },
    "AC-2": {
        "feature push free": ["ctl-push-feature", "mined-feature-push-u", "git-push-feature-colon"],
        "default branch push asks": ["git-push-default", "git-push-head-main", "git-push-branch-to-main", "git-push-head-refs-main"],
        "protected non-default branch": ["git-push-protected-release-branch", "git-push-protected-declared-release-branch"],
        "force push signed": ["git-push-force", "git-push-force-short", "git-push-force-trailing", "git-push-force-default", "git-push-force-dash-c"],
        "ff to own upstream free": ["ctl-ff-own-upstream", "git-merge-ff-other-branch"],
        "undeterminable target asks": ["git-push-unresolved-branch", "git-push-substituted-branch"],
    },
    "AC-3": {
        "kubectl exec": ["exec-kubectl-read-namespaced", "exec-kubectl-sh-c-write", "exec-kubectl-interactive-sh"],
        "docker exec": ["exec-docker-read", "exec-docker-write", "exec-docker-interactive"],
        "gcloud compute ssh --command": ["exec-gcloud-ssh-read", "exec-gcloud-ssh-write", "exec-gcloud-ssh-interactive"],
        "op run": ["exec-op-run-read", "exec-op-run-write"],
        "aws-vault exec": ["exec-aws-vault-read", "exec-aws-vault-write"],
        "similar executor (ssh)": ["exec-ssh-read", "exec-ssh-write"],
    },
    "AC-4": {
        "compound cloud operations": ["lex-aws-run-instances", "lex-aws-ecr-batch-delete", "lex-gcloud-add-metadata"],
        "iac and container verbs": ["lex-pulumi-up", "lex-compose-down-volumes", "lex-buildx-push", "lex-terraform-test", "lex-helm-test", "lex-vault-seal"],
        "auth and context switches": ["lex-gh-auth-login", "lex-gh-auth-refresh", "lex-kubectl-use-context", "lex-gcloud-get-credentials"],
        "remote copy": ["lex-rsync-remote", "lex-scp-remote"],
        "local-only run free": ["lex-helm-repo-update", "lex-apt-update", "lex-act-push"],
        "dry-run plan check free": ["lex-pulumi-up-dry-run", "lex-ansible-check", "lex-kubectl-dry-run-server", "lex-helm-upgrade-dry-run", "ctl-terraform-plan"],
    },
    "AC-5": {
        "cluster context": ["target-kubectl-prod-context-apply", "target-kubectl-dev-context-apply", "target-kubectl-prod-context-read",
                            "target-context-unlisted-verb-prod", "target-context-unlisted-verb-dev", "target-context-unlisted-verb-nonprod"],
        "cloud project": ["target-project-unlisted-verb-prod", "target-project-unlisted-verb-dev"],
        "account": ["target-account-unlisted-verb-prod", "target-account-unlisted-verb-dev", "target-account-aws-prod-delete", "target-account-aws-dev-delete"],
        "iac workspace": ["target-workspace-unlisted-verb-prod", "target-workspace-unlisted-verb-dev", "target-iac-terraform-workspace-select-prod"],
        "iac stack": ["target-stack-unlisted-verb-prod", "target-stack-unlisted-verb-dev", "target-iac-pulumi-up-prod-stack", "target-iac-pulumi-preview-prod-stack"],
        "name inference": ["target-unlisted-verb-prod", "target-unlisted-verb-nonprod", "target-unlisted-verb-staging", "target-unlisted-verb-dev"],
        "declared production": ["target-declared-production", "target-declared-not-production"],
    },
    "AC-6": {
        "blocked cases stay blocked": ["floor-aws-delete-vpc", "floor-azure-group", "floor-gcp-projects", "floor-k8s-namespace", "floor-rm-root", "floor-secret-read-ssh-key"],
        "force push moves to signed": ["git-push-force"],
    },
    "AC-7": {
        "long inline read-only code": ["inline-python-long-read", "seed-roo-node-e-long-read"],
        "subprocess helper that reads": ["inline-python-subprocess-read"],
        "tar -xf": ["mined-scratch-tar-x", "local-tar-x-managed-worktree", "mined-tar-x-root-twin"],
        "mv": ["mined-scratch-mv", "local-mv-managed-worktree", "mined-scratch-mv-out-twin"],
        "cp": ["mined-scratch-cp", "local-cp-managed-worktree", "local-cp-worktree-git-dir-twin"],
        "git merge-tree and git grep": ["local-git-merge-tree", "local-git-grep"],
        "similar local git reads": ["local-git-diff-stat", "local-git-log", "local-git-show", "local-git-blame", "local-git-ls-files"],
        "runs code the user did not write": ["inline-foreign-exec-fetched-code", "foreign-registry-runner-npx", "foreign-registry-runner-uvx"],
        "still asks when it reaches shared state": ["inline-python-subprocess-write-twin", "inline-python-os-system-twin", "local-git-merge-twin"],
    },
    "AC-8": {
        "data piped into a network sender": ["egress-file-into-nc", "egress-echo-into-nc", "egress-log-into-curl-upload", "egress-diff-into-curl-data-binary"],
        "secret piped stays refused": ["egress-secret-into-nc"],
        "coding-CLI credential stores": ["store-codex-cat", "store-claude-cat", "store-gemini-cat", "store-opencode-cat", "store-qwen-cat",
                                         "store-copilot-cat", "store-anthropic-profiles-ls"],
    },
}


INLINE_LANES = [
    "inline-lanes-python-long-json-read", "inline-lanes-python-long-rmtree-twin", "seed-roo-node-e-long-read",
    "inline-lanes-node-long-rename-twin", "inline-python-subprocess-read", "inline-python-subprocess-write-twin",
    "inline-lanes-cd-chain-script-read", "inline-lanes-cd-chain-script-write-twin", "inline-script-missing",
    "inline-foreign-exec-fetched-code", "inline-template-literal-read",
]


@pytest.mark.parametrize("row_id", INLINE_LANES)
def test_inline_lanes_judge_code_by_its_content(row_id, world, monkeypatch):
    row = {r["id"]: r for r in ROWS}[row_id]
    assert row["owner"] == "T1"
    if "long" in row_id:
        assert len(row["command"]) > 500, row_id
    verdict, reason = _observe_isolated(row, world, monkeypatch)
    assert verdict == row["expected"], (row["command"], reason)


def test_every_shape_named_in_every_brief_criterion_maps_to_rows():
    by_id = {r["id"]: r for r in ROWS}
    for ac, shapes in AC_SHAPES.items():
        for shape, ids in shapes.items():
            assert ids, (ac, shape)
            for row_id in ids:
                assert row_id in by_id, (ac, shape, row_id)


REFUSAL_SITES = {
    "validate::guard_reason": ["floor-cli-only-orchestrator"],
    "validate::Empty command not allowed": ["floor-empty-command"],
    "validate::consent_verb_reason": ["floor-consent-verb"],
    "validate::attribution_reason": ["floor-claude-attribution"],
    "validate::comment_push.reason": ["floor-comment-limit-push"],
    "validate::db_write_reason": ["floor-gaia-db-sqlite", "floor-gaia-db-cp"],
    "validate::mem_write_reason": ["floor-memory-write-subagent"],
    "validate::skill_reason": ["floor-memory-skill-orchestrator"],
    "validate::resume_reason": ["floor-resume-point-subagent"],
    "validate::ownership_reason": ["floor-contract-ownership-own-row"],
    "validate::pp_write_reason": ["floor-protected-install-cp", "floor-protected-install-sed"],
    "validate::shell_write_reason": ["floor-shell-write-redirect", "floor-shell-write-tee", "floor-shell-write-sed-i"],
    "validate::Obfuscated shell nesting detected: {unwr": ["floor-obfuscated-nesting"],
    "validate::Command blocked by security policy: {blo": ["floor-aws-delete-vpc", "floor-rm-root", "floor-disk-dd"],
    "validate::Command blocked by security policy: {com": ["floor-compound-component-blocked"],
    "_fail_closed_on_code_heredoc::Command blocked by security policy: {blo": ["floor-v11-xargs-heredoc-tail"],
    "validate::secret_violation.reason": ["floor-kubectl-secret-output"],
    "validate::read_permission_reason(pipe_block)": ["floor-cloud-pipe"],
    "validate::sensitive_reason": ["floor-secret-read-ssh-key"],
    "_detect_indirect_execution::Indirect execution of blocked command de": ["floor-indirect-blocked-in-wrapper", "floor-indirect-blocked-in-inline"],
    "_detect_indirect_execution::deny_msg": ["floor-shell-wrapper-bash-c", "floor-shell-wrapper-eval"],
    "_phase4_check_composition::Dangerous pipe composition blocked: {res": ["seed-continue-curl-pipe-sh"],
    "_validate_commit_message::Commit message validation failed: {valid": ["floor-commit-message"],
    "decide_t3_outcome::Command blocked by security policy: {_bl": ["floor-aws-delete-vpc", "floor-rm-root", "floor-disk-dd"],
    "_categorical_refusal::reason": ["floor-secret-into-account-write", "floor-account-write-then-blocked"],
    "decide_t3_outcome::reason": ["floor-clear-secret-in-request"],
    "decide_t3_outcome::reason#2": ["floor-no-requester"],
}
PASSTHROUGH_SITES = {
    "_validate_compound_command::Compound command blocked: component {i}/": (
        "wraps the refusal of whichever component failed, categorical or approvable alike; each categorical component has its own row"
    ),
}
UNCOVERED_SITES = {
    "_validate_single_command::Command blocked by flag classifier: {fla": "reachable only for a force push, which the earlier floor refuses today and AC-2 moves to signed",
    "_validate_single_command::_uncorrelated_set_denial(pending_set, se": "store failure path; needs an injected database failure",
    "decide_t3_outcome::T3 degraded-block: approval persistence ": "store failure path: approval persistence failed; needs an injected database failure",
    "_validate_single_command::COMMAND_SET persistence failed closed: {": "store failure path; needs an injected database failure",
    "_validate_single_command::COMMAND_SET persistence failed closed: {#2": "store failure path; needs an injected database failure",
    "_validate_single_command::COMMAND_SET persistence failed closed: {#3": "store failure path; needs an injected database failure",
    "_match_signed_pipeline::COMMAND_SET persistence failed closed: {": "store failure path; needs an injected database failure",
}


def _kind(response, function) -> str:
    if response is None:
        return "categorical"
    text = ast.unparse(response)
    if text.endswith(".block_response"):
        return "passthrough"
    if "'ask'" in text or "decision" in text:
        return "approvable"
    if isinstance(response, ast.Name) and f"{response.id} = build_hook_permission_response('ask'" in ast.unparse(function):
        return "approvable"
    return "categorical"


class _DenialSites(ast.NodeVisitor):
    def __init__(self):
        self.stack, self.sites, self.seen = [], {}, {}

    def visit_FunctionDef(self, node):
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):
        if getattr(node.func, "id", "") == "BashValidationResult" and self.stack:
            kw = {k.arg: k.value for k in node.keywords}
            allowed = kw.get("allowed")
            if isinstance(allowed, ast.Constant) and allowed.value is False and "approval_id" not in kw:
                reason = ast.unparse(kw["reason"]) if "reason" in kw else ""
                text = (reason[1:] if reason[:2] in ("f'", 'f"') else reason).strip("'\"")[:40]
                key = f"{self.stack[-1].name}::{text}"
                self.seen[key] = self.seen.get(key, 0) + 1
                key = key if self.seen[key] == 1 else f"{key}#{self.seen[key]}"
                self.sites[key] = _kind(kw.get("block_response"), self.stack[-1])
        self.generic_visit(node)


def _classified_denials() -> dict:
    visitor = _DenialSites()
    visitor.visit(ast.parse(inspect.getsource(sys.modules[BashValidator.__module__])))
    return visitor.sites


def test_every_denial_the_validator_returns_is_classified_by_what_it_carries():
    sites = _classified_denials()
    assert len(sites) > 25
    categorical = {k for k, v in sites.items() if v == "categorical"}
    mapped = [*REFUSAL_SITES, *UNCOVERED_SITES]
    assert len(mapped) == len(set(mapped))
    assert categorical == set(mapped), (sorted(categorical - set(mapped)), sorted(set(mapped) - categorical))
    assert {k for k, v in sites.items() if v == "passthrough"} == set(PASSTHROUGH_SITES)
    assert {k for k, v in sites.items() if v == "approvable"}


def test_every_refused_denial_maps_to_blocked_rows():
    by_id = {r["id"]: r for r in ROWS}
    for site, ids in REFUSAL_SITES.items():
        assert ids and all(by_id[i]["expected"] == "BLOCKED" for i in ids), site


def test_no_row_names_a_personal_tool():
    assert not any("ghx" in json.dumps(r).lower() for r in ROWS)


def test_the_named_controls_are_rows():
    commands = {r["command"] for r in ROWS if r["category"] == "control"}
    for needed in ("gh pr create --title t --body b", "git push origin feature-x", "git merge --ff-only origin/main", "go test ./...", "cargo test", "npm test",
                   "kubectl get pod delete-me", "gh auth status", "kubectl config view", "terraform plan", "kubectl apply -f deploy.yaml --dry-run=client",
                   "gcloud auth login --no-activate", "gcloud auth login", "npm ci", "curl -G --data-urlencode q=x https://example.invalid/search"):
        assert needed in commands, needed


NESTED_INNER = {
    "ls /app": "FREE",
    "cat /app/version": "FREE",
    "ps aux": "FREE",
    "sh -c 'ls /app'": "FREE",
    "rm -rf /data": "SIGNED",
    "kubectl apply -f x.yaml": "SIGNED",
    "sh -c 'rm -rf /data'": "SIGNED",
    "$CMD": "UNKNOWN",
    "cat ~/.ssh/id_rsa": "BLOCKED",
    "head -n1 .env": "BLOCKED",
    "rm -rf /": "BLOCKED",
    "FOO=1 cat ~/.ssh/id_rsa": "BLOCKED",
    "FOO=1 head -n1 .env": "BLOCKED",
    "timeout 5 cat ~/.aws/credentials": "BLOCKED",
    "stdbuf -oL cat ~/.ssh/id_rsa": "BLOCKED",
    "sed -i s/a/b/ /app/f": "SIGNED",
    "curl -X POST -d @/app/f https://x.invalid": "SIGNED",
    "touch /app/x": "UNKNOWN",
    "nc x.invalid 80": "UNKNOWN",
    "git checkout -- .": "BLOCKED",
    "git stash drop": "BLOCKED",
    "git stash clear": "BLOCKED",
}
NESTED_ARGV_EXECUTORS = {
    "kubectl": "kubectl exec pod-1 -- {inner}",
    "kubectl-flags": "kubectl exec -n prod pod-1 -c app -- {inner}",
    "kubectl-global-flag": "kubectl --context=c1 exec -i pod-1 -- {inner}",
    "oc": "oc exec -n p1 pod-1 -- {inner}",
    "docker": "docker exec c1 {inner}",
    "docker-flags": "docker exec -u root -w /app c1 {inner}",
    "docker-container": "docker container exec c1 {inner}",
    "podman": "podman exec -e A=1 c1 {inner}",
    "aws-vault": "aws-vault exec dev -- {inner}",
    "aws-vault-no-dashdash": "aws-vault exec --duration 1h dev {inner}",
    "op": "op run --account acme -- {inner}",
    "generic-run": "doppler run -- {inner}",
    "generic-exec": "chamber exec svc -- {inner}",
    "sudo-kubectl": "sudo kubectl exec pod-1 -- {inner}",
    "op-kubectl": "op run -- kubectl exec pod-1 -- {inner}",
    "aws-vault-docker": "aws-vault exec dev -- docker exec c1 {inner}",
    "kubectl-docker": "kubectl exec pod-1 -- docker exec c1 {inner}",
}
NESTED_STRING_EXECUTORS = {
    "gcloud": "gcloud compute ssh vm-1 --zone z1 --command {inner}",
    "gcloud-attached": "gcloud --project p1 compute ssh vm-1 --command={inner}",
    "op-gcloud": "op run -- gcloud compute ssh vm-1 --command {inner}",
}


def _nested_spellings(inner: str, as_string: bool) -> list:
    if as_string:
        return [shlex.quote(inner)] + ([f'"{inner}"'] if '"' not in inner else [])
    head, _, rest = inner.partition(" ")
    if not head.isalpha():
        return [inner]
    return [f"{word} {rest}".strip() for word in (head, f"'{head}'", f'"{head}"', "\\" + head)]


def _nested_batch(templates: dict, as_string: bool) -> list:
    return [pytest.param(name, template, as_string, id=name) for name, template in templates.items()]


@pytest.mark.parametrize("name,template,as_string", _nested_batch(NESTED_ARGV_EXECUTORS, False) + _nested_batch(NESTED_STRING_EXECUTORS, True))
def test_a_nested_executor_takes_the_verdict_of_its_inner_command(name, template, as_string, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    wrong = {}
    for inner, expected in NESTED_INNER.items():
        for spelling in _nested_spellings(inner, as_string):
            command = template.format(inner=spelling)
            verdict = observe(command, "none", world)[0]
            if verdict != expected:
                wrong[command] = (verdict, expected)
    assert not wrong, wrong


NESTED_PINS = [
    ("kubectl exec -it pod-1 -- sh", "repo", {"SIGNED"}, "sign.speaks"),
    ("kubectl exec -it pod-1 -- /bin/bash -l", "none", {"SIGNED"}, "sign.speaks"),
    ("oc exec -ti pod-1 -- bash", "none", {"SIGNED"}, "sign.speaks"),
    ("docker exec -it c1 sh", "none", {"SIGNED"}, "sign.speaks"),
    ("docker container exec -i c1 python3", "none", {"SIGNED"}, "sign.speaks"),
    ("gcloud compute ssh vm-1 --zone z1", "none", {"SIGNED"}, "sign.speaks"),
    ("aws-vault exec dev", "none", {"SIGNED"}, "sign.speaks"),
    ("aws-vault exec dev --", "none", {"SIGNED"}, "sign.speaks"),
    ("op run -- bash", "none", {"SIGNED"}, "sign.speaks"),
    ("doppler run -- zsh", "none", {"SIGNED"}, "sign.speaks"),
    ("op run -- kubectl exec -it pod-1 -- sh", "none", {"SIGNED"}, "sign.speaks"),
    ("sudo docker exec -it c1 bash", "none", {"SIGNED"}, "sign.speaks"),
    ("kubectl exec -it pod-1 -- ls /app", "none", {"FREE"}, ""),
    ("kubectl exec --bogus pod-1 -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec pod-1 extra -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec pod-1 ls", "none", {"FREE"}, ""),
    ("kubectl exec pod-1 -c app ls /app", "none", {"FREE"}, ""),
    ("kubectl exec pod-1 cat ~/.ssh/id_rsa", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 FOO=1 head -n1 .env", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 rm -rf ~", "none", {"BLOCKED"}, ""),
    ("kubectl exec pod-1 touch /app/x", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec pod-1 -c -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("FOO=1 cat ~/.ssh/id_rsa", "none", {"BLOCKED"}, "floor.secret-read"),
    ("op run -- FOO=1 cat ~/.aws/credentials", "none", {"BLOCKED"}, "floor.secret-read"),
    ("op run --env-file=.env -- ls", "none", {"BLOCKED"}, "floor.secret-read"),
    ("docker exec -e A=1 --env-file .env c1 ls", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 -- ssh host cat ~/.ssh/id_rsa", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 -- xargs cat ~/.ssh/id_rsa", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 -- -- cat ~/.ssh/id_rsa", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 --kubeconfig=/x -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("docker exec --privileged c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("docker -H tcp://h:2375 exec c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("docker exec --bogus c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("docker exec c1 -it ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("gcloud compute ssh vm-1 --ssh-flag=-oProxyCommand=x --command ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("gcloud compute ssh vm-1 --command ls -- -L 80:h:80", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("gcloud compute ssh vm-1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("op run --no-masking -- printenv", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("aws-vault exec --server dev -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("aws-vault exec --stdout dev", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec pod-1 -- git push origin feature-x", "repo", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("docker exec c1 ./run.sh", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("kubectl exec pod-1 -- sh setup", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("op run -- git push origin feature-x", "repo", {"FREE"}, ""),
    ("chamber exec --region r1 svc -- ls", "none", {"SIGNED"}, ""),
    ("doppler exec deploy -- ls", "none", {"SIGNED"}, ""),
    ("doppler run --config prd -- rm -rf /data", "none", {"SIGNED"}, "sign.destroys"),
    ("kubectl exec pod-1 -- kubectl delete ns prod", "none", {"BLOCKED"}, ""),
    ("kubectl exec pod-1 -- sh -c 'ls; rm -rf /data'", "none", {"SIGNED"}, ""),
    ("kubectl exec pod-1 -- sh -c 'ls && kubectl apply -f x.yaml'", "none", {"SIGNED"}, ""),
    ("gcloud compute ssh vm-1 --command 'ls && rm -rf /data'", "none", {"SIGNED"}, ""),
    ("gcloud compute ssh vm-1 --command 'ls | xargs rm'", "none", {"SIGNED"}, ""),
    ("gcloud compute ssh vm-1 --command 'ls\nrm -rf /data'", "none", {"SIGNED"}, ""),
    ("docker exec c1 sh -c 'ls || rm -rf /data'", "none", {"SIGNED"}, ""),
    ("op run -- sh -c 'ls; rm -rf /data'", "none", {"SIGNED"}, ""),
    ("ssh host1 'ls && kubectl apply -f x.yaml'", "none", {"SIGNED"}, ""),
    ("kubectl exec pod-1 -- bash -c 'cat ~/.ssh/id_rsa'", "none", {"BLOCKED"}, "floor.secret-read"),
    ("gcloud compute ssh vm-1 --command 'ls; cat ~/.aws/credentials'", "none", {"BLOCKED"}, "floor.secret-read"),
    ("kubectl exec pod-1 -- sh -c 'rm -rf /'", "none", {"BLOCKED"}, ""),
    ("gcloud compute ssh vm-1 --command 'echo hi; rm -rf /'", "none", {"BLOCKED"}, ""),
    ("kubectl exec pod-1 git checkout -- .", "none", {"BLOCKED"}, ""),
    ("kubectl exec pod-1 git stash drop", "none", {"BLOCKED"}, ""),
    ("frobctl exec -- git stash clear", "none", {"BLOCKED"}, ""),
    ("ssh host1 git checkout -- .", "none", {"BLOCKED"}, ""),
    ("kubectl exec pod-1 -- git push --force origin main", "none", {"SIGNED"}, ""),
    ("KUBECONFIG=/x kubectl exec pod-1 -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("env KUBECONFIG=/x kubectl exec pod-1 -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("KUBECONFIG=/x oc exec pod-1 -- ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("DOCKER_HOST=tcp://h:2375 docker exec c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("env DOCKER_HOST=tcp://h:2375 docker exec c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("DOCKER_CONFIG=/x docker exec c1 ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("FOO=1 kubectl exec pod-1 -- ls", "none", {"FREE"}, ""),
    ("gcloud compute ssh vm-1 --command 'l\\s /app'", "none", {"FREE"}, ""),
    ("gcloud compute ssh vm-1 --command 'e\\nv'", "none", {"FREE"}, ""),
    ("KUBECONFIG=/x kubectl exec pod-1 -- l\\s /app", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("gcloud compute ssh vm-1 --impersonate-service-account=sa@x --command ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
    ("gcloud --impersonate-service-account sa@x compute ssh vm-1 --command ls", "none", {"UNKNOWN"}, "unknown.dynamic-span"),
]


@pytest.mark.parametrize("command,context,verdicts,rule", NESTED_PINS, ids=[p[0] for p in NESTED_PINS])
def test_a_nested_executor_pin(command, context, verdicts, rule, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    verdict, reason = observe(command, context, world)
    assert verdict in verdicts, (verdict, reason)
    assert f"Rule: {rule}." in reason if rule else True, reason


_TARGET_TEMPLATES = {
    "kubectl": "kubectl {flag}exec pod-1 -- ls",
    "oc": "oc {flag}exec pod-1 -- ls",
    "docker": "docker {flag}exec c1 ls",
    "podman": "podman {flag}exec c1 ls",
    "gcloud": "gcloud {flag}compute ssh vm-1 --command ls",
}
_TARGET_CARRIERS = (
    "{var} {cmd}", "env {var} {cmd}", "{var} op run -- {cmd}", "env {var} op run -- {cmd}",
    "{var} aws-vault exec prof -- {cmd}", "op run -- env {var} {cmd}", "{var} op run -- env FOO=1 {cmd}",
    "{var} op run -- aws-vault exec prof -- {cmd}",
)


def _target_variable_cases(carriers=_TARGET_CARRIERS):
    from modules.security.nested_executors import TARGET_VARIABLES

    assert set(TARGET_VARIABLES) == set(_TARGET_TEMPLATES)
    return [
        pytest.param(program, variable, flag, carrier, id=f"{program}:{variable}:{carrier}")
        for program, variables in TARGET_VARIABLES.items()
        for variable, flag in variables.items()
        for carrier in carriers
    ]


@pytest.mark.parametrize("program,variable,flag,carrier", _target_variable_cases())
def test_a_target_variable_asks_wherever_in_the_chain_its_flag_asks(program, variable, flag, carrier, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    template = _TARGET_TEMPLATES[program]
    flag_verdict, flag_reason = observe(template.format(flag=f"{flag}=v "), "none", world)
    assert flag_verdict == "UNKNOWN", (flag_verdict, flag_reason)
    assert observe(template.format(flag=""), "none", world)[0] == "FREE"
    command = carrier.format(var=f"{variable}=v", cmd=template.format(flag=""))
    verdict, reason = observe(command, "none", world)
    assert verdict == flag_verdict, (command, verdict, reason)


_EXPORTING_CARRIERS = (
    "export {var} && {cmd}", "export {var}; {cmd}", "export {var}\n{cmd}", "export {var} || {cmd}",
    "export {var} && true | {cmd}", "declare -x {var} && {cmd}", "typeset -x {var} && {cmd}",
    "readonly -x {var} && {cmd}", "set -a; {var}; {cmd}", "{var}; export {name} && {cmd}",
    "export {var} && op run -- {cmd}", "export {var} && echo ok && aws-vault exec prof -- env FOO=1 {cmd}",
    "env -S '{var} {cmd}'", "env -S '{var} op run -- {cmd}'", "{var} env -S '{cmd}'",
    "export {var} && unset {name} && {cmd}", "export {var}; export -n {name}; {cmd}",
    "declare -x {var} && declare +x {name} && {cmd}", "export {var}; unset -v {name}; {cmd}",
    "export {var}; unset {name} | true; {cmd}", "export {var}; true | unset {name}; {cmd}",
    "export {var}; true && unset {name} | cat && {cmd}", "export {var}; false && unset {name}; {cmd}",
    "export {var}; declare +x {name}; export {name}; {cmd}", "builtin export {var}; {cmd}",
    "command export {var}; {cmd}", "f() {{ local -x {var}; {cmd}; }}; f", "f() {{ declare -gx {var}; }}; f; {cmd}",
    "f() {{ declare -x {var}; }}; f; {cmd}", "read {name} <<< v; export {name}; {cmd}",
    "export {name}; read {name} < f; {cmd}", "printf -v {name} v; export {name}; {cmd}",
    "export {name}; printf -v {name} %s v; {cmd}", "set -a; read {name} < f; {cmd}", "read -r {name} < f; {cmd}",
    "set -a; printf -v {name} v; {cmd}", "export {name}; mapfile -t {name} < f; {cmd}",
    "export {name}; readarray -t {name} < f; {cmd}", "(export {var}; {cmd})", "{{ export {var}; }}; {cmd}",
    "for {name} in v; do :; done; {cmd}", ": \"${{{name}:=v}}\"; {cmd}", "getopts a {name}; {cmd}",
    "( export {var}; ) && {cmd}", "( export {var} ); {cmd}", ". /dev/stdin <<< 'export {var}'; {cmd}",
)
_OPAQUE_SETTERS = (
    "source ./env.sh; {cmd}", ". ./env.sh && {cmd}", "source ~/.kube/env && {cmd}", "builtin . ./env.sh; {cmd}",
    "diff <(true) <(true); {cmd}", "cat <<< x; {cmd}", "(source ./env.sh; {cmd})",
    "export $(cat /tmp/f); {cmd}", "export \"$(cat /tmp/f)\"; {cmd}", "n=X; export \"${{n}}_Y=v\"; {cmd}",
    "declare -x \"$n=v\"; {cmd}", "typeset -x $n=v && {cmd}", "readonly `echo n`=v; {cmd}",
    "export -- \"$n\"=v; {cmd}", "eval \"export $n=v\"; {cmd}", "eval export X=v; {cmd}",
    "read \"$n\" < f; {cmd}", "read -r \"${{n}}\" < f; {cmd}", "read -ra \"$n\" < f; {cmd}",
    "read -p x -- $n < f; {cmd}", "printf -v \"$n\" v; {cmd}", "printf -v$n %s v; {cmd}",
    "mapfile -t \"$n\" < f; {cmd}", "readarray -d , $n < f; {cmd}",
    "n=X; declare -n r=${{n}}_Y; r=v; export r; {cmd}", "n=X; declare -n r=\"${{n}}_Y\"; export r=v; {cmd}",
    "declare -n r=$(echo X)_Y; export r=v; {cmd}", "declare -n r={{X,Z}}_Y; export r=v; {cmd}",
    "n=X; typeset -n r=${{n}}Y; export r=v; {cmd}", "f() {{ local -n r=$1; export r=v; }}; f Y; {cmd}",
    "declare -gn r=Y; {cmd}", "export X{{,}}_Y=v; {cmd}", "declare -x {{X,Z}}_Y=v; {cmd}", "export {{A,B}}=1; {cmd}",
    "read -r {{X,Z}}_Y <<< 'v w'; {cmd}", "printf -v X{{,}}_Y v; {cmd}", "export X_Y?=v; {cmd}", "eval ls; {cmd}",
)
_SPLIT_NAMES = ("{a}\"\"{b}", "{a}\\{b}", "\"{a}\"{b}", "{a}''{b}", "$'{a}'{b}", "'{a}'\"{b}\"")
_COMPUTED_NAMES = ("$'\\x{hex}'{rest}", "$'\\u00{hex}'{rest}", "{init}?", "{init}[{last}]", "{init}*",
                   "{{{name},X}}", "{a}{{,}}{b}", "{init}{{{last},}}", "{a}{{{b},X}}")
_LINE_READS = ("echo ${name}; {cmd}", "echo \"${{{name}}}\"; {cmd}", "echo \"${{{name}:-none}}\"; {cmd}",
               "read -rp \"$p\" line < f; {cmd}", "printf '%s' \"${name}\"; {cmd}", "mapfile -C \"$c\" lines < f; {cmd}",
               "grep -c {name} f; {cmd}", "export -- FOO=1; {cmd}", "typeset -gx FOO=1; read -r line < f; {cmd}",
               "for f in a b; do :; done; {cmd}")
_VERIFIER_ROWS = (
    "n=DOCKER; declare -n r=${n}_HOST; r=tcp://h:2375; export r; docker exec ctr ls",
    "n=DOCKER; declare -n r=\"${n}_HOST\"; export r=tcp://h:2375; docker exec ctr ls",
    "declare -n r=$(echo DOCKER)_HOST; export r=tcp://h:2375; docker exec ctr ls",
    "declare -n r={DOCKER,X}_HOST; export r=tcp://h:2375; docker exec ctr ls",
    "n=DOCKER; local -n r=${n}_HOST; r=tcp://h:2375; export r; docker exec ctr ls",
    "export DOCKER{,}_HOST=tcp://h:2375; docker exec ctr ls",
    "declare -x {DOCKER,X}_HOST=tcp://h:2375; docker exec ctr ls",
    "export $'\\u0044OCKER_HOST'=tcp://h:2375; docker exec ctr ls",
    "export $'\\x44OCKER_HOST'=tcp://h:2375; docker exec ctr ls",
    "export {DOCKER,X}_HOST=tcp://h:2375; docker exec ctr ls",
    "export DOCKER_HO{ST,}=tcp://h:2375; docker exec ctr ls",
    "export DOCKER_{HOST,X}=tcp://h:2375; docker exec ctr ls",
    "export {A,B}=1; docker exec ctr ls",
)


@pytest.mark.parametrize("program,variable,flag,carrier", _target_variable_cases(_EXPORTING_CARRIERS))
def test_a_nested_target_variable_assigned_anywhere_in_the_line_asks(
    program, variable, flag, carrier, world, monkeypatch,
):
    monkeypatch.chdir(world["root"])
    cmd = _TARGET_TEMPLATES[program].format(flag="")
    verdict, reason = observe(carrier.format(var=f"{variable}=v", name=variable, cmd=cmd), "none", world)
    assert verdict != "FREE", (carrier, verdict, reason)


@pytest.mark.parametrize("program", list(_TARGET_TEMPLATES))
@pytest.mark.parametrize("setter", _OPAQUE_SETTERS)
def test_a_nested_executor_after_a_source_or_substitution_asks(program, setter, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    command = setter.format(cmd=_TARGET_TEMPLATES[program].format(flag=""))
    verdict, reason = observe(command, "none", world)
    assert verdict != "FREE", (command, verdict, reason)


@pytest.mark.parametrize("setter", ("export {name}=v; {cmd}", "declare -x {name}=v && {cmd}"))
@pytest.mark.parametrize("split", _SPLIT_NAMES)
@pytest.mark.parametrize("program,variable,flag,carrier", _target_variable_cases(("",)))
def test_a_nested_target_variable_spelled_through_quotes_asks(
    program, variable, flag, carrier, split, setter, world, monkeypatch,
):
    monkeypatch.chdir(world["root"])
    middle = len(variable) // 2
    name = split.format(a=variable[:middle], b=variable[middle:])
    command = setter.format(name=name, cmd=_TARGET_TEMPLATES[program].format(flag=""))
    verdict, reason = observe(command, "none", world)
    assert verdict != "FREE", (command, verdict, reason)


@pytest.mark.parametrize("setter", ("export {name}=v; {cmd}", "declare -x {name}=v && {cmd}", "read -r {name} < f; {cmd}"))
@pytest.mark.parametrize("spelling", _COMPUTED_NAMES)
@pytest.mark.parametrize("program,variable,flag,carrier", _target_variable_cases(("",)))
def test_a_nested_target_variable_set_through_an_escape_glob_or_brace_asks(
    program, variable, flag, carrier, spelling, setter, world, monkeypatch,
):
    monkeypatch.chdir(world["root"])
    middle = len(variable) // 2
    name = spelling.format(hex=f"{ord(variable[0]):02x}", rest=variable[1:], init=variable[:-1], last=variable[-1],
                           name=variable, a=variable[:middle], b=variable[middle:])
    command = setter.format(name=name, cmd=_TARGET_TEMPLATES[program].format(flag=""))
    verdict, reason = observe(command, "none", world)
    assert verdict != "FREE", (command, verdict, reason)


@pytest.mark.parametrize("command", _VERIFIER_ROWS)
def test_a_computed_or_nameref_docker_host_asks(command, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    verdict, reason = observe(command, "none", world)
    assert verdict != "FREE", (command, verdict, reason)


@pytest.mark.parametrize("read", _LINE_READS)
@pytest.mark.parametrize("program,variable,flag,carrier", _target_variable_cases(("",)))
def test_a_nested_target_variable_only_read_on_the_line_changes_nothing(
    program, variable, flag, carrier, read, world, monkeypatch,
):
    monkeypatch.chdir(world["root"])
    cmd = _TARGET_TEMPLATES[program].format(flag="")
    verdict, reason = observe(read.format(name=variable, cmd=cmd), "none", world)
    plain, _ = observe(f"echo hi; {cmd}", "none", world)
    assert verdict == plain, (read, verdict, reason)


@pytest.mark.parametrize("command,expected", [
    ("gcloud compute ssh vm-1 --access-token-file=/x --command ls", "UNKNOWN"),
    ("podman --url ssh://h/run/podman.sock exec c1 ls", "UNKNOWN"),
    ("KUBECONFIG=/x op run -- kubectl get pods", "FREE"),
    ("FOO=1 op run -- kubectl exec pod-1 -- ls", "FREE"),
    ("KUBECONFIG=/x kubectl exec pod-1 -- kubectl get pods", "UNKNOWN"),
])
def test_a_credential_flag_asks_and_an_unrelated_or_read_carrier_stays_as_it_was(command, expected, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    verdict, reason = observe(command, "none", world)
    assert verdict == expected, (verdict, reason)


def _ambient_config(root: Path, monkeypatch, kube_context: str = "", project: str = "") -> None:
    kubeconfig, gcloud = root / "kubeconfig", root / "gcloud"
    kubeconfig.write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {kube_context}\n", encoding="utf-8")
    (gcloud / "configurations").mkdir(parents=True, exist_ok=True)
    (gcloud / "active_config").write_text("default", encoding="utf-8")
    (gcloud / "configurations" / "config_default").write_text(f"[core]\nproject = {project}\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(gcloud))


PROD_TARGETS = [
    ("kubectl --context prod-x frobnicate thing", {}, "SIGNED"),
    ("kubectl --context dev-x frobnicate thing", {}, "FREE"),
    ("kubectl --context=prod-x frobnicate thing", {"kube_context": "dev-a"}, "SIGNED"),
    ("kubectl --context dev-x frobnicate thing", {"kube_context": "prod-a"}, "FREE"),
    ("kubectl frobnicate thing", {"kube_context": "prod-a"}, "SIGNED"),
    ("kubectl frobnicate thing", {"kube_context": "dev-a"}, "FREE"),
    ("kubectl frobnicate thing", {"kube_context": "nonprod-a"}, "FREE"),
    ("kubectl --context prod-x get pods", {}, "FREE"),
    ("kubectl get pods", {"kube_context": "prod-a"}, "FREE"),
    ("kubectl --context dev-x apply -f x.yaml", {}, "SIGNED"),
    ("kubectl --context $CTX frobnicate thing", {"kube_context": "prod-a"}, "FREE"),
    ("docker --context prod-eu frobnicate c1", {}, "SIGNED"),
    ("docker -c dev-eu frobnicate c1", {}, "FREE"),
    ("DOCKER_CONTEXT=prod-eu docker frobnicate c1", {}, "SIGNED"),
    ("gcloud compute instances frobnicate vm-1", {"project": "acme-prod"}, "SIGNED"),
    ("gcloud compute instances frobnicate vm-1", {"project": "acme-dev"}, "FREE"),
    ("gcloud --project acme-dev compute instances frobnicate vm-1", {"project": "acme-prod"}, "FREE"),
    ("gcloud --account ops@acme-prod.example compute instances frobnicate vm-1", {}, "SIGNED"),
    ("gcloud compute instances list", {"project": "acme-prod"}, "FREE"),
    ("gcloud secrets versions access latest --secret=db-password --project prod", {}, "FREE"),
    ("gcloud --project prod secrets versions access 3 --secret=json-config", {}, "FREE"),
    ("gcloud secrets versions access latest --secret=db-password", {"project": "acme-prod"}, "FREE"),
    ("aws --profile prod ssm get-parameter --name /app/db --with-decryption", {}, "FREE"),
    ("aws --profile prod ec2 describe-instances", {}, "FREE"),
    ("shipit describe-release --env prod", {}, "FREE"),
]


@pytest.mark.parametrize("command,ambient,expected", PROD_TARGETS, ids=[f"{c}|{a}" for c, a, _ in PROD_TARGETS])
def test_prod_target_decides_a_verb_that_does_not_sign_by_itself(command, ambient, expected, world, monkeypatch, tmp_path):
    _isolate_targets(monkeypatch, world["root"])
    _ambient_config(tmp_path, monkeypatch, **ambient)
    monkeypatch.chdir(world["root"])
    verdict, reason = observe(command, "none", world)
    assert verdict == expected, (verdict, reason)
    if expected == "SIGNED":
        assert "Rule: sign.lands." in reason, reason


@pytest.mark.parametrize("command,declaration,expected", [
    ("kubectl --context nonprod-x frobnicate thing", {"production": ["nonprod-x"]}, "SIGNED"),
    ("kubectl --context nonprod-x frobnicate thing", {}, "FREE"),
    ("kubectl frobnicate thing", {"production": ["staging-a"]}, "SIGNED"),
    ("kubectl --context prod-preview frobnicate thing", {"development": ["prod-preview"]}, "FREE"),
    ("kubectl --context prod-preview frobnicate thing", {}, "SIGNED"),
    ("kubectl --context nonprod-x get pods", {"production": ["nonprod-x"]}, "FREE"),
])
def test_prod_target_declaration_overrides_the_name(command, declaration, expected, world, monkeypatch, tmp_path):
    _isolate_targets(monkeypatch, world["root"])
    _ambient_config(tmp_path, monkeypatch, kube_context="staging-a")
    monkeypatch.chdir(world["root"])
    with declared(world["dirs"]["none"], declaration):
        verdict, reason = observe(command, "none", world)
    assert verdict == expected, (verdict, reason)


TARGET_SWITCH_CLAIMS = [
    ("export -- FOO=1; docker exec ctr ls", "FREE"),
    ("typeset -gx FOO=1; docker exec ctr ls", "FREE"),
    ("echo $DOCKER_HOST; docker exec ctr ls", "FREE"),
    ('echo "${DOCKER_HOST}"; docker exec ctr ls', "FREE"),
    ('echo "${DOCKER_HOST:-none}"; docker exec ctr ls', "FREE"),
    ('echo "DOCKER_HOST is set"; docker exec ctr ls', "FREE"),
    ("docker exec ctr ls # DOCKER_HOST=x", "FREE"),
    ("export {A,B}=1; docker exec ctr ls", "UNKNOWN"),
    ("unset DOCKER_HOST; docker exec ctr ls", "UNKNOWN"),
    ("unset -v CONTAINER_HOST; podman exec c1 ls", "UNKNOWN"),
    ("unset FOO; docker exec ctr ls", "FREE"),
]


@pytest.mark.parametrize("command,expected", TARGET_SWITCH_CLAIMS)
def test_a_target_switch_claim_keeps_its_decided_verdict(command, expected, world, monkeypatch):
    monkeypatch.chdir(world["root"])
    verdict, reason = observe(command, "none", world)
    assert verdict == expected, (verdict, reason)


def test_a_target_name_inside_a_read_heredoc_changes_nothing(world, monkeypatch):
    monkeypatch.chdir(world["root"])
    named = observe("read -r FOO <<EOF\nDOCKER_HOST\nEOF\nls", "none", world)[0]
    assert named == observe("read -r FOO <<EOF\nX\nEOF\nls", "none", world)[0]
