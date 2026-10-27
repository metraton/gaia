"""Verifier probes (handoff 34153): a production target makes a non-read operation ask; a grant binds to its target."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
for entry in (str(ROOT), str(ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.approval_grants import activate_db_pending_by_id  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "aprobet1020x01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    for name in ("GAIA_CLI_ALIASES", "KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
                 "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    from gaia.paths import ensure_layout

    ensure_layout()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return {"home": home, "work": work, "tmp": tmp_path}


def kube(path: Path, context: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n", encoding="utf-8")
    return path


def gcloud(home: Path, configs: dict, active: str = "default") -> None:
    root = home / ".config" / "gcloud"
    (root / "configurations").mkdir(parents=True, exist_ok=True)
    (root / "active_config").write_text(active, encoding="utf-8")
    for name, project in configs.items():
        (root / "configurations" / f"config_{name}").write_text(f"[core]\nproject = {project}\n", encoding="utf-8")


def docker(home: Path, context: str) -> None:
    (home / ".docker").mkdir(parents=True, exist_ok=True)
    (home / ".docker" / "config.json").write_text(json.dumps({"currentContext": context}), encoding="utf-8")


def tf_workspace(directory: Path, name: str) -> None:
    (directory / ".terraform").mkdir(parents=True, exist_ok=True)
    (directory / ".terraform" / "environment").write_text(name, encoding="utf-8")


def declare(directory: Path, payload: dict) -> None:
    from gaia.store.writer import _connect

    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('probe')")
    con.execute("INSERT OR REPLACE INTO projects (workspace, name, path, status, project_identity) VALUES ('probe', 'p', ?, 'active', 'probe/p')",
                (str(directory),))
    con.execute("INSERT OR REPLACE INTO project_context (project_identity, contract_name, payload) VALUES ('probe/p', 'targets', ?)",
                (json.dumps(payload),))
    con.commit()
    con.close()


def validate(command: str, cwd: Path, tool_use: str = "t-probe"):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": "s-probe", "tool_use_id": tool_use, "agent_id": AGENT, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(command, is_subagent=True, session_id="s-probe",
                                                          agent_type="developer", hook_payload=payload)


def verdict(command: str, cwd: Path) -> tuple:
    result = validate(command, cwd)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith("Compound T3 execution is disabled"):
        return ("UNKNOWN" if "could not be determined" in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


NOT_FREE = ("SIGNED", "UNKNOWN")
REFUSED = NOT_FREE + ("BLOCKED",)

READ_EXEMPTION = [
    ("aws-timestream-delete", "aws --profile prod timestream-query delete-scheduled-query --scheduled-query-arn a", {}, NOT_FREE),
    ("gcloud-runtime-config-delete", "gcloud --project acme-prod runtime-config configs delete c1", {}, NOT_FREE),
    ("gcloud-os-config-delete", "gcloud --project acme-prod compute os-config os-policy-assignments delete a1", {}, NOT_FREE),
    ("unknown-verb-read-operand", "kubectl --context prod-a frobnicate list-api", {}, NOT_FREE),
    ("unknown-verb-ns-logs", "kubectl --context prod-a -n logs frobnicate thing", {}, NOT_FREE),
    ("gcloud-unknown-verb-status-vm", "gcloud --project acme-prod compute instances frobnicate vm-status", {}, NOT_FREE),
    ("access-approval-dismiss", "gcloud --project acme-prod access-approval requests dismiss r1", {}, NOT_FREE),
    ("access-ctx-dry-run-enforce", "gcloud --project acme-prod access-context-manager perimeters dry-run enforce p1", {}, NOT_FREE),
    ("aws-logs-disassociate", "aws --profile prod logs disassociate-kms-key --log-group-name g", {}, NOT_FREE),
    ("aws-ssm-send-command", "aws --profile prod ssm send-command --document-name AWS-RunShellScript --targets Key=x,Values=y", {}, NOT_FREE),
    ("kubectl-cordon", "kubectl --context prod-a cordon node-1", {}, NOT_FREE),
    ("kubectl-drain-status", "kubectl --context prod-a drain status-node", {}, REFUSED),
    ("control-describe", "aws --profile prod ec2 describe-instances", {}, ("FREE",)),
    ("control-get-pods", "kubectl --context prod-a get pods", {}, ("FREE",)),
    ("control-gcloud-list", "gcloud --project acme-prod compute instances list", {}, ("FREE",)),
    ("control-secret-access", "gcloud --project acme-prod secrets versions access latest --secret=s", {}, ("FREE",)),
]

EXTRACTION = [
    ("kubeconfig-flag", "kubectl --kubeconfig {TMP}/prod.yaml frobnicate thing", {"kube_file": "prod-a"}, NOT_FREE),
    ("kube-cluster-flag", "kubectl --cluster prod-cluster frobnicate thing", {}, NOT_FREE),
    ("kube-eks-arn", "kubectl frobnicate thing", {"kube": "arn:aws:eks:us-east-1:1:cluster/prod"}, NOT_FREE),
    ("kube-home-default", "kubectl frobnicate thing", {"home_kube": "prod-a"}, NOT_FREE),
    ("unset-kubeconfig", "unset KUBECONFIG; kubectl frobnicate thing", {"kube": "dev-a", "home_kube": "prod-a"}, REFUSED),
    ("docker-c", "docker -c prod-eu frobnicate c1", {}, NOT_FREE),
    ("docker-context-eq", "docker --context=prod-eu frobnicate c1", {}, NOT_FREE),
    ("docker-ambient", "docker frobnicate c1", {"docker": "prod-eu"}, NOT_FREE),
    ("unset-docker-host", "unset DOCKER_HOST; docker frobnicate c1", {"docker": "prod-eu"}, NOT_FREE),
    ("gcloud-configuration-flag", "gcloud --configuration prodcfg compute instances frobnicate vm-1",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE),
    ("gcloud-active-config-var", "CLOUDSDK_ACTIVE_CONFIG_NAME=prodcfg gcloud compute instances frobnicate vm-1",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE),
    ("gcloud-core-project-var", "CLOUDSDK_CORE_PROJECT=acme-prod gcloud compute instances frobnicate vm-1", {}, NOT_FREE),
    ("gcloud-core-account-var", "CLOUDSDK_CORE_ACCOUNT=ops@acme-prod.example gcloud compute instances frobnicate vm-1", {}, NOT_FREE),
    ("aws-profile-var", "AWS_PROFILE=prod aws ec2 frobnicate-instances", {}, NOT_FREE),
    ("aws-profile-export", "export AWS_PROFILE=prod; aws ec2 frobnicate-instances", {}, REFUSED),
    ("tf-chdir-taint", "terraform -chdir={TMP}/stack taint aws_instance.a", {"tf_dir": "prod"}, NOT_FREE),
    ("tf-var-taint", "TF_WORKSPACE=prod terraform taint aws_instance.a", {}, NOT_FREE),
    ("tf-cwd-taint", "terraform taint aws_instance.a", {"tf_cwd": "prod"}, NOT_FREE),
    ("switch-tf-workspace", "terraform workspace select prod", {}, NOT_FREE),
    ("switch-kube-context", "kubectl config use-context prod-a", {"kube": "dev-a"}, NOT_FREE),
    ("switch-docker-context", "docker context use prod-eu", {"docker": "dev-eu"}, NOT_FREE),
    ("switch-gcloud-config", "gcloud config configurations activate prodcfg", {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE),
    ("dev-twin-free", "kubectl --context dev-a frobnicate thing", {}, ("FREE",)),
]

DEV_SWITCHES = [
    ("kubectl config use-context dev-a", {"kube": "prod-a"}),
    ("terraform workspace select dev", {"tf_cwd": "prod"}),
    ("docker context use dev-eu", {"docker": "prod-eu"}),
    ("gcloud config configurations activate devcfg", {"gcloud": {"default": "acme-prod", "devcfg": "acme-dev"}}),
]


@pytest.mark.parametrize("command,setup", DEV_SWITCHES, ids=[c for c, _ in DEV_SWITCHES])
def test_prod_target_switch_to_development_keeps_the_verdict_targets_would_not_change(command, setup, env, monkeypatch):
    _arrange(setup, env, monkeypatch)
    with_targets = verdict(command, env["work"])[0]
    monkeypatch.setattr("modules.tools.bash_validator.production_target_verdict", lambda *a, **k: None)
    assert with_targets == verdict(command, env["work"])[0]

PROD_MAP = [
    ("declared-dev-name-has-prod", "kubectl --context acme-prod-sandbox frobnicate thing", {"declare": {"development": ["acme-prod-sandbox"]}}, ("FREE",)),
    ("undeclared-name-has-prod", "kubectl --context acme-prod-sandbox frobnicate thing", {}, NOT_FREE),
    ("declared-prod-case", "kubectl --context Staging-A frobnicate thing", {"declare": {"production": ["staging-a"]}}, NOT_FREE),
    ("declared-prod-project", "gcloud --project acme-main compute instances frobnicate vm-1", {"declare": {"production": ["acme-main"]}}, NOT_FREE),
    ("declared-dev-keeps-verb", "kubectl --context acme-prod-sandbox delete pod x", {"declare": {"development": ["acme-prod-sandbox"]}}, ("SIGNED",)),
]


def _arrange(setup: dict, env: dict, monkeypatch) -> None:
    home, tmp, work = env["home"], env["tmp"], env["work"]
    if "kube" in setup:
        monkeypatch.setenv("KUBECONFIG", str(kube(tmp / "kubeconfig", setup["kube"])))
    if "home_kube" in setup:
        kube(home / ".kube" / "config", setup["home_kube"])
    if "kube_file" in setup:
        kube(tmp / "prod.yaml", setup["kube_file"])
    if "gcloud" in setup:
        gcloud(home, setup["gcloud"])
    if "docker" in setup:
        docker(home, setup["docker"])
    if "tf_dir" in setup:
        (tmp / "stack").mkdir()
        tf_workspace(tmp / "stack", setup["tf_dir"])
    if "tf_cwd" in setup:
        tf_workspace(work, setup["tf_cwd"])
    if "declare" in setup:
        declare(work, setup["declare"])


@pytest.mark.parametrize("case", READ_EXEMPTION + EXTRACTION + PROD_MAP, ids=[c[0] for c in READ_EXEMPTION + EXTRACTION + PROD_MAP])
def test_prod_target_probe(case, env, monkeypatch):
    _, command, setup, accepted = case
    _arrange(setup, env, monkeypatch)
    got, reason = verdict(command.replace("{TMP}", str(env["tmp"])), env["work"])
    assert got in accepted, (got, reason[:200])


def _mint(command: str, cwd: Path) -> str:
    blocked = validate(command, cwd)
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    assert activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver").success
    return blocked.approval_id


def _switch_then_retry(command: str, env, before, after) -> tuple:
    before()
    minted = _mint(command, env["work"])
    after()
    return minted, validate(command, env["work"])


def test_grant_target_scope_gcloud_project_switch(env):
    home = env["home"]
    minted, retry = _switch_then_retry("gcloud compute instances delete vm-1", env,
                                       lambda: gcloud(home, {"default": "acme-dev"}), lambda: gcloud(home, {"default": "acme-prod"}))
    assert not retry.allowed and retry.approval_id != minted, retry.reason[:200]


def test_grant_target_scope_gcloud_active_config_switch(env):
    home = env["home"]
    _, retry = _switch_then_retry("gcloud compute instances delete vm-1", env,
                                  lambda: gcloud(home, {"default": "acme-dev", "p": "acme-prod"}, active="default"),
                                  lambda: gcloud(home, {"default": "acme-dev", "p": "acme-prod"}, active="p"))
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_docker_context_switch(env):
    home = env["home"]
    _, retry = _switch_then_retry("docker rm -f c1", env, lambda: docker(home, "dev-eu"), lambda: docker(home, "prod-eu"))
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_terraform_workspace_switch(env):
    work = env["work"]
    _, retry = _switch_then_retry("terraform apply -auto-approve", env, lambda: tf_workspace(work, "dev"), lambda: tf_workspace(work, "prod"))
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_terraform_workspace_switch_payload_cwd_differs_from_hook_cwd(env):
    stack = env["tmp"] / "stack"
    stack.mkdir()
    tf_workspace(stack, "dev")
    blocked = validate("terraform apply -auto-approve", stack)
    assert blocked.approval_id
    assert activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver").success
    tf_workspace(stack, "prod")
    retry = validate("terraform apply -auto-approve", stack)
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_payload_cwd_same_workspace_still_matches(env):
    stack = env["tmp"] / "stack"
    stack.mkdir()
    tf_workspace(stack, "dev")
    blocked = validate("terraform apply -auto-approve", stack)
    assert activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver").success
    assert validate("terraform apply -auto-approve", stack).allowed


def test_grant_target_scope_kube_home_default_after_unset(env, monkeypatch):
    kube(env["home"] / ".kube" / "config", "prod-a")
    monkeypatch.setenv("KUBECONFIG", str(kube(env["tmp"] / "kubeconfig", "dev-a")))
    _mint("kubectl delete pod x", env["work"])
    retry = validate("unset KUBECONFIG; kubectl delete pod x", env["work"])
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_unreadable_gcloud_config_at_lookup(env):
    home = env["home"]
    gcloud(home, {"default": "acme-dev"})
    _mint("gcloud compute instances delete vm-1", env["work"])
    target = home / ".config" / "gcloud" / "configurations" / "config_default"
    target.unlink()
    target.mkdir()
    retry = validate("gcloud compute instances delete vm-1", env["work"])
    assert not retry.allowed, retry.reason[:200]


def test_grant_target_scope_a6_unknown_verdict_is_not_a_family_grant(env, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(kube(env["tmp"] / "kubeconfig", "dev-a")))
    command = "kubectl $VERB pod x"
    first = validate(command, env["work"])
    assert not first.allowed and first.approval_id, first.reason[:200]
    assert activate_db_pending_by_id(first.approval_id, current_session_id="s-approver").success
    assert not validate("kubectl delete pod x", env["work"]).allowed
    used, again = validate(command, env["work"]), validate(command, env["work"])
    assert not (used.allowed and again.allowed)
