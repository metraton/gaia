"""The consent verdict by effect and target, one data file driven through BashValidator.validate."""

from __future__ import annotations

import ast
import inspect
import json
import os
import shlex
import re
import subprocess
import sys
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
    patch.delenv("GAIA_CLI_ALIASES", raising=False)
    from gaia.paths import ensure_layout, scratch_dir, worktrees_dir

    ensure_layout()
    managed_root = Path(os.path.realpath(str(worktrees_dir())))
    dirs = {name: _build_context(spec, root, name, managed_root) for name, spec in HEADER["contexts"].items()}
    yield {"root": root, "dirs": dirs, "SCRATCH": os.path.realpath(str(scratch_dir())), "WORKTREES": str(managed_root)}
    patch.undo()


def _nested_shells(depth: int) -> str:
    command = "true"
    for _ in range(depth):
        command = "bash -c " + shlex.quote(command)
    return command


def observe(command: str, context: str, world: dict, role: str = "subagent", session: bool = True) -> tuple:
    for key, value in (("{CWD}", str(world["dirs"][context])), ("{SCRATCH}", world["SCRATCH"]), ("{WORKTREES}", world["WORKTREES"]),
                       ("{NEST5}", _nested_shells(5))):
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
    result = BashValidator().validate(command, is_subagent=subagent, session_id=session_id,
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
    return pytest.param(row, id=row["id"], marks=marks)


def _observe_isolated(row, world, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(world["root"] / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(world["root"] / "gaia-data" / "gaia.db"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    monkeypatch.chdir(world["root"])
    assert os.environ["GAIA_DB"].startswith(str(world["root"]))
    if row.get("seed_contracts"):
        seeded = row["seed_contracts"]

        def fake_list(*, harness_agent_id=None, contract_id=None, limit=100, **_):
            return [c for c in seeded if c.get("harness_agent_id") == harness_agent_id or c.get("contract_id") == contract_id][:limit]

        monkeypatch.setattr("gaia.store.writer.list_agent_contract_handoffs", fake_list)
    return observe(row["command"], row.get("context", "none"), world, row.get("role", "subagent"), not row.get("no_session"))


@pytest.mark.parametrize("row", [_case(r) for r in ROWS])
def test_verdict(row, world, monkeypatch):
    verdict, reason = _observe_isolated(row, world, monkeypatch)
    assert verdict == row["expected"], row["command"]
    if row.get("reason_contains"):
        assert row["reason_contains"].lower() in reason.lower(), reason


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
        if key == "git_destructive":
            continue
        for pattern in patterns:
            assert any(pattern.search(command) for command in floor), (key, pattern.pattern)


def test_force_push_is_the_only_blocked_case_expected_to_be_signed(world, monkeypatch):
    refused = [r for r in ROWS if r["expected"] == "SIGNED" and _observe_isolated(r, world, monkeypatch)[0] == "BLOCKED"]
    assert refused and all(re.search(r"\bpush\b.*(--force\b|\s-f\b)", r["command"]) for r in refused), [r["id"] for r in refused]


def test_each_change_request_shape_spans_every_binary():
    shapes = {"create", "edit", "view", "list", "comment", "ready", "approve", "close", "merge", "retarget"}
    for binary in ("gh", "ghwrap", "forgectl", "glab", "tea", "az-repos"):
        found = {r["id"].split("-")[-1] for r in ROWS if r["id"].startswith(f"forge-{binary}-")}
        assert found >= {"create", "edit", "view", "list", "approve", "close", "merge"}, binary
    assert {r["id"].split("-")[-1] for r in ROWS if r["id"].startswith("forge-gh-")} >= shapes


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
                            "target-context-unknown-verb-prod", "target-context-unknown-verb-dev", "target-context-unknown-verb-nonprod"],
        "cloud project": ["target-project-unknown-verb-prod", "target-project-unknown-verb-dev"],
        "account": ["target-account-unknown-verb-prod", "target-account-unknown-verb-dev", "target-account-aws-prod-delete", "target-account-aws-dev-delete"],
        "iac workspace": ["target-workspace-unknown-verb-prod", "target-workspace-unknown-verb-dev", "target-iac-terraform-workspace-select-prod"],
        "iac stack": ["target-stack-unknown-verb-prod", "target-stack-unknown-verb-dev", "target-iac-pulumi-up-prod-stack", "target-iac-pulumi-preview-prod-stack"],
        "name inference": ["target-unknown-verb-prod", "target-unknown-verb-nonprod", "target-unknown-verb-staging", "target-unknown-verb-dev"],
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
        "data piped into a network sender": ["egress-file-into-nc", "egress-echo-into-nc", "egress-log-into-curl-upload"],
        "secret piped stays refused": ["egress-secret-into-nc"],
        "coding-CLI credential stores": ["store-codex-cat", "store-claude-cat", "store-gemini-cat", "store-opencode-cat"],
    },
}


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
    "validate::secret_violation.reason": ["floor-kubectl-secret-output"],
    "validate::read_permission_reason(pipe_block)": ["floor-cloud-pipe"],
    "validate::sensitive_reason": ["floor-secret-read-ssh-key"],
    "_detect_indirect_execution::Indirect execution of blocked command de": ["floor-indirect-blocked-in-wrapper", "floor-indirect-blocked-in-inline"],
    "_detect_indirect_execution::deny_msg": ["floor-shell-wrapper-bash-c", "floor-shell-wrapper-eval"],
    "_phase4_check_composition::Dangerous pipe composition blocked: {res": ["seed-continue-curl-pipe-sh"],
    "_validate_commit_message::Commit message validation failed: {valid": ["floor-commit-message"],
    "decide_t3_outcome::Command blocked by security policy: {_bl": ["floor-aws-delete-vpc", "floor-rm-root", "floor-disk-dd"],
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
