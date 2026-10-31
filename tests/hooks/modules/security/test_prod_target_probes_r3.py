"""Verifier probes round 3 (handoff 34223): attached options, undeterminable switches, podman ambient, branch refnames,
symlinked project paths, and grants that must not follow a silent target switch."""

from __future__ import annotations

import hashlib
import json
import subprocess
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

AGENT = "aprobet1020x04"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    for name in ("KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
                 "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE",
                 "CONTAINER_HOST", "CONTAINER_CONNECTION", "PULUMI_HOME", "GIT_DIR", "GIT_WORK_TREE",
                 "AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID"):
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


def validate(command: str, cwd: Path, session: str = "s-probe"):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": session, "tool_use_id": "t-probe", "agent_id": AGENT, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(command, is_subagent=True, session_id=session,
                                                          agent_type="developer", hook_payload=payload)


def verdict(command: str, cwd: Path) -> tuple:
    result = validate(command, cwd)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith("Compound T3 execution is disabled"):
        return ("UNKNOWN" if "could not be determined" in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


NOT_FREE = ("SIGNED", "UNKNOWN", "BLOCKED")
FREE = ("FREE",)

ATTACHED = [
    ("b-docker-c-attached-unknown", "docker -cprod-eu frobnicate x", {}, NOT_FREE),
    ("b-podman-c-attached-unknown", "podman -cprod-a frobnicate c1", {}, NOT_FREE),
    ("b-pulumi-s-attached-unknown", "pulumi -sprod-x frobnicate", {}, NOT_FREE),
    ("b-kubectl-n-attached-then-unknown", "kubectl --context prod-a -napp frobnicate x", {}, NOT_FREE),
    ("b-ok-kubectl-l-attached", "kubectl --context prod-a -lapp=x get pods", {}, FREE),
    ("b-ok-kubectl-v-attached", "kubectl --context prod-a -v9 get pods", {}, FREE),
    ("b-ok-helm-n-attached", "helm --kube-context prod-a -napp list", {}, FREE),
    ("b-ok-dev-namespace-named-prod", "kubectl -nprod frobnicate x", {"kube": "dev-a"}, FREE),
]

SWITCH = [
    ("c-pulumi-select-stack-eq", "pulumi stack select --stack=prod", {}, NOT_FREE),
    ("c-pulumi-C-select-s", "pulumi -C . stack select -s prod-x", {}, NOT_FREE),
    ("c-gcloud-get-credentials-variable-project", "CLOUDSDK_CORE_PROJECT=acme-prod gcloud container clusters get-credentials main", {}, NOT_FREE),
    ("c-gcloud-get-credentials-ambient-project", "gcloud container clusters get-credentials main", {"gcloud": {"default": "acme-prod"}}, NOT_FREE),
    ("c-gcloud-get-credentials-configuration-flag", "gcloud --configuration prodcfg container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE),
    ("c-aws-update-kubeconfig-variable-profile", "AWS_PROFILE=prod aws eks update-kubeconfig --name main", {}, NOT_FREE),
    ("c-aws-update-kubeconfig-profile-flag", "aws --profile prod eks update-kubeconfig --name main", {}, NOT_FREE),
    ("c-aws-update-kubeconfig-name-eq", "aws eks update-kubeconfig --name=prod-eks", {}, NOT_FREE),
    ("c-az-get-credentials-name-eq", "az aks get-credentials -g rg --name=prod-aks", {}, NOT_FREE),
    ("c-az-get-credentials-canonical", "az aks get-credentials -g rg -n prod-aks", {}, NOT_FREE),
    ("c-az-get-credentials-long", "az aks get-credentials --resource-group rg --name prod-aks", {}, NOT_FREE),
    ("c-az-get-credentials-name-first", "az aks get-credentials -n prod-aks -g rg", {}, NOT_FREE),
    ("c-kubens-kubeconfig-assignment", "KUBECONFIG={TMP}/prodkube kubens app", {"kube": "dev-a", "kube2": "prod-a"}, NOT_FREE),
    ("c-kubectl-use-context-variable", "kubectl config use-context $CTX", {"kube": "dev-a"}, NOT_FREE),
    ("c-tf-select-behind-unknown-option", "terraform workspace select -frobnicate dev", {}, NOT_FREE),
    ("c-kubectx-previous", "kubectx -", {"kube": "dev-a"}, NOT_FREE),
    ("c-dev-tf-select-or-create", "terraform workspace select -or-create dev", {}, FREE),
    ("c-dev-tf-select-behind-unknown-option-dashdash", "terraform workspace select -frobnicate -- dev", {}, FREE),
    ("c-dev-gcloud-get-credentials-variable", "CLOUDSDK_CORE_PROJECT=acme-dev gcloud container clusters get-credentials main", {}, FREE),
    ("c-dev-aws-update-kubeconfig-variable", "AWS_PROFILE=dev aws eks update-kubeconfig --name main", {}, NOT_FREE),
    ("c-dev-pulumi-select-stack-eq", "pulumi stack select --stack=dev", {}, FREE),
    ("c-dev-az-get-credentials-canonical", "az aks get-credentials -g rg -n dev-aks", {}, FREE),
    ("c-dev-gcloud-get-credentials-configuration-flag", "gcloud --configuration devcfg container clusters get-credentials main",
     {"gcloud": {"default": "acme-prod", "devcfg": "acme-dev"}}, FREE),
]

OPERATION = [
    ("d-gcloud-pubsub-pull-auto-ack", "gcloud --project acme-prod pubsub subscriptions pull my-sub --auto-ack", {}, NOT_FREE),
    ("d-gcloud-sql-connect", "gcloud --project acme-prod sql connect main-db", {}, NOT_FREE),
    ("d-gcloud-functions-call", "gcloud --project acme-prod functions call my-fn", {}, NOT_FREE),
    ("d-gcloud-sql-export", "gcloud --project acme-prod sql export sql main-db gs://b/x.sql", {}, NOT_FREE),
    ("d-gcloud-add-tags", "gcloud --project acme-prod compute instances add-tags vm --tags x", {}, NOT_FREE),
    ("d-gcloud-topics-publish", "gcloud --project acme-prod pubsub topics publish my-topic --message x", {}, NOT_FREE),
    ("d-gcloud-scheduler-run", "gcloud --project acme-prod scheduler jobs run my-job", {}, NOT_FREE),
    ("d-gcloud-queues-pause", "gcloud --project acme-prod tasks queues pause q", {}, NOT_FREE),
    ("d-gcloud-sql-failover", "gcloud --project acme-prod sql instances failover main-db", {}, NOT_FREE),
    ("d-gcloud-detach-disk", "gcloud --project acme-prod compute instances detach-disk vm --disk d", {}, NOT_FREE),
    ("d-gcloud-secrets-access", "gcloud --project acme-prod secrets versions access latest --secret s", {}, FREE),
    ("d-az-keyvault-recover", "az --subscription prod-sub keyvault secret recover --name x --vault-name v", {}, NOT_FREE),
    ("d-az-pipelines-tag-add", "az --subscription prod-sub pipelines runs tag add 12 --tags x", {}, NOT_FREE),
    ("d-ok-gcloud-describe-one", "gcloud --project acme-prod compute instances describe vm", {}, FREE),
    ("d-ok-az-show", "az --subscription prod-sub vm show -g rg -n vm", {}, FREE),
    ("d-ok-gcloud-logging-read", "gcloud --project acme-prod logging read x", {}, FREE),
]

PODMAN = [
    ("e-podman-c-short", "podman -c prod-a frobnicate c1", {}, NOT_FREE),
    ("e-podman-env-wrapper", "env CONTAINER_CONNECTION=prod-a podman frobnicate c1", {}, NOT_FREE),
    ("e-podman-ambient-variable", "podman frobnicate c1", {"setenv": {"CONTAINER_CONNECTION": "prod-a"}}, NOT_FREE),
    ("e-docker-ambient-variable-control", "docker frobnicate c1", {"setenv": {"DOCKER_CONTEXT": "prod-eu"}}, NOT_FREE),
    ("e-ok-podman-ps-prod", "podman --connection prod-a ps", {}, FREE),
    ("e-ok-podman-dev-variable", "CONTAINER_CONNECTION=dev-a podman frobnicate c1", {}, FREE),
    ("e-ok-podman-ambient-dev-variable", "podman frobnicate c1", {"setenv": {"CONTAINER_CONNECTION": "dev-a"}}, FREE),
]

OVERREACH = [
    ("o-kubectl-dev-unknown", "kubectl --context dev-a frobnicate x", {}, FREE),
    ("o-gcloud-dev-unknown", "gcloud --project acme-dev compute instances frobnicate vm", {}, FREE),
    ("o-shipit-dev", "shipit release --env dev", {}, FREE),
    ("o-shipit-status-prod", "shipit status --env prod", {}, FREE),
    ("o-grep-context", "grep --context 3 prod file.txt", {}, FREE),
    ("o-kubectl-logs-prod", "kubectl --context prod-a logs pod-1", {}, FREE),
    ("o-kubectl-selector-eq-prod", "kubectl --context prod-a -l=app get pods", {}, FREE),
    ("o-docker-inspect-prod", "docker --context prod-eu inspect c1", {}, FREE),
    ("o-aws-s3-ls-prod", "aws --profile prod s3 ls", {}, FREE),
    ("o-az-account-show-prod", "az --subscription prod-sub account show", {}, FREE),
    ("o-gcloud-config-list-prod", "gcloud --project acme-prod config list", {}, FREE),
    ("o-helm-status-prod", "helm --kube-context prod-a status rel", {}, FREE),
    ("o-helm-get-values-prod", "helm --kube-context prod-a get values rel", {}, FREE),
]

AZ_FETCH_DEV = "az aks get-credentials -n dev-aks -g rg"
AZURE = [
    ("az-fetch-subscription-flag-prod", f"{AZ_FETCH_DEV} --subscription prod-sub", {"azure": "dev-sub"}, NOT_FREE),
    ("az-fetch-subscription-flag-dev", f"{AZ_FETCH_DEV} --subscription dev-sub", {"azure": "prod-sub"}, FREE),
    ("az-fetch-ambient-prod", AZ_FETCH_DEV, {"azure": "prod-sub"}, NOT_FREE),
    ("az-fetch-ambient-dev", AZ_FETCH_DEV, {"azure": "dev-sub"}, FREE),
    ("az-fetch-ambient-none", AZ_FETCH_DEV, {}, FREE),
    ("az-fetch-ambient-unreadable", AZ_FETCH_DEV, {"azure": "{broken"}, NOT_FREE),
    ("az-fetch-config-dir-prod", AZ_FETCH_DEV, {"azure_dir": "prod-sub", "azure": "dev-sub"}, NOT_FREE),
    ("az-fetch-variable-prod", f"AZURE_SUBSCRIPTION_ID=prod-sub {AZ_FETCH_DEV}", {"azure": "dev-sub"}, NOT_FREE),
    ("az-fetch-variable-dev-keeps-ambient-prod", f"AZURE_SUBSCRIPTION_ID=dev-sub {AZ_FETCH_DEV}", {"azure": "prod-sub"}, NOT_FREE),
    ("az-unknown-ambient-prod", "az frobnicate thing", {"azure": "prod-sub"}, NOT_FREE),
    ("az-unknown-ambient-dev", "az frobnicate thing", {"azure": "dev-sub"}, FREE),
    ("az-read-ambient-prod", "az vm show -g rg -n vm", {"azure": "prod-sub"}, FREE),
]

CASES = ATTACHED + SWITCH + OPERATION + PODMAN + OVERREACH + AZURE


def azure(directory: Path, default: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if default.startswith("{"):
        body = default
    else:
        subscriptions = [{"id": f"id-{name}", "name": name, "isDefault": name == default} for name in ("dev-sub", "prod-sub")]
        body = json.dumps({"installationId": "x", "subscriptions": subscriptions})
    (directory / "azureProfile.json").write_text("﻿" + body, encoding="utf-8")


def _arrange(setup: dict, env: dict, monkeypatch) -> None:
    home, tmp = env["home"], env["tmp"]
    if "kube" in setup:
        monkeypatch.setenv("KUBECONFIG", str(kube(tmp / "kubeconfig", setup["kube"])))
    if "kube2" in setup:
        kube(tmp / "prodkube", setup["kube2"])
    if "gcloud" in setup:
        gcloud(home, setup["gcloud"])
    if "azure" in setup:
        azure(home / ".azure", setup["azure"])
    if "azure_dir" in setup:
        azure(tmp / "azcfg", setup["azure_dir"])
        monkeypatch.setenv("AZURE_CONFIG_DIR", str(tmp / "azcfg"))
    for name, value in setup.get("setenv", {}).items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_r3_probe(case, env, monkeypatch):
    name, command, setup, accepted = case
    if name in {"c-dev-gcloud-get-credentials-variable", "c-dev-pulumi-select-stack-eq",
                "c-dev-az-get-credentials-canonical", "c-dev-gcloud-get-credentials-configuration-flag",
                "c-dev-tf-select-or-create", "c-dev-tf-select-behind-unknown-option-dashdash",
                "az-fetch-subscription-flag-dev", "az-fetch-ambient-dev", "az-fetch-ambient-none"}:
        accepted = ("SIGNED",)
    _arrange(setup, env, monkeypatch)
    got, reason = verdict(command.replace("{TMP}", str(env["tmp"])), env["work"])
    assert got in accepted, (got, reason[:160])


def _repo(base: Path) -> Path:
    base.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(base), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "init")
    git("branch", "feature-x")
    git("branch", "release/1.x")
    git("remote", "add", "origin", "https://example.invalid/o/r.git")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (base / "sub").mkdir()
    return base


def _declare(directory: Path, payload: dict) -> None:
    from gaia.store.writer import _connect

    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('probe')")
    con.execute("INSERT OR REPLACE INTO projects (workspace, name, path, status, project_identity) VALUES ('probe', 'p', ?, 'active', 'probe/p')",
                (str(directory),))
    con.execute("INSERT OR REPLACE INTO project_context (project_identity, contract_name, payload) VALUES ('probe/p', 'targets', ?)",
                (json.dumps(payload),))
    con.commit()
    con.close()


BRANCH = [
    ("f-full-refname", "git push origin refs/heads/release/1.x", "repo", NOT_FREE),
    ("f-heads-refname", "git push origin heads/release/1.x", "repo", NOT_FREE),
    ("f-forced-refspec", "git push origin +release/1.x", "repo", NOT_FREE),
    ("f-src-dst-refspec", "git push origin feature-x:refs/heads/release/1.x", "repo", NOT_FREE),
    ("f-dash-C-subdir-from-outside", "git -C {REPO}/sub push origin release/1.x", "outside", NOT_FREE),
    ("f-dash-C-relative", "git -C repo push origin release/1.x", "tmp", NOT_FREE),
    ("f-git-dir-variable-from-outside", "GIT_DIR={REPO}/.git git push origin release/1.x", "outside", NOT_FREE),
    ("f-symlinked-project-path", "git push origin release/1.x", "link", NOT_FREE),
    ("f-symlinked-project-from-real-path", "git push origin release/1.x", "repo-link-declared", NOT_FREE),
    ("f-twin-full-refname-feature", "git push origin refs/heads/feature-x", "repo", FREE),
    ("f-twin-dash-C-subdir-feature", "git -C {REPO}/sub push origin feature-x", "outside", FREE),
    ("f-twin-symlinked-feature", "git push origin feature-x", "link", FREE),
]


@pytest.mark.parametrize("case", BRANCH, ids=[c[0] for c in BRANCH])
def test_r3_branch_probe(case, env):
    _, command, where, accepted = case
    repo = _repo(env["tmp"] / "repo")
    link = env["tmp"] / "link"
    link.symlink_to(repo)
    _declare(link if where in ("link", "repo-link-declared") else repo, {"protected_branches": ["release/1.x"]})
    cwd = {"repo": repo, "outside": env["work"], "tmp": env["tmp"], "link": link, "repo-link-declared": repo}[where]
    got, reason = verdict(command.replace("{REPO}", str(repo)), cwd)
    assert got in accepted, (got, reason[:160])


DECLARED = [
    ("decl-symlinked-production", "kubectl --context staging-x frobnicate y", NOT_FREE),
    ("decl-symlinked-development-override", "kubectl --context prod-sandbox frobnicate y", FREE),
]


@pytest.mark.parametrize("case", DECLARED, ids=[c[0] for c in DECLARED])
def test_r3_declared_symlink(case, env):
    _, command, accepted = case
    real = env["tmp"] / "proj"
    real.mkdir()
    link = env["tmp"] / "projlink"
    link.symlink_to(real)
    _declare(link, {"production": ["staging-x"], "development": ["prod-sandbox"]})
    got, reason = verdict(command, link)
    assert got in accepted, (got, reason[:160])


def _mint(command: str, cwd) -> str:
    blocked = validate(command, cwd, session="s-grant-r3")
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    activation = activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver")
    assert activation.success, activation.reason
    return blocked.approval_id


def _gcloud_switch(e, monkeypatch, name):
    gcloud(e["home"], {"default": "acme-dev", "prodcfg": "acme-prod"}, active=name)


def _gcloud_variable(e, monkeypatch, name):
    gcloud(e["home"], {"default": "acme-dev"})
    if name == "prodcfg":
        monkeypatch.setenv("CLOUDSDK_CORE_PROJECT", "acme-prod")


def _docker_switch(e, monkeypatch, name):
    (e["home"] / ".docker").mkdir(exist_ok=True)
    context = "prod-eu" if name == "prodcfg" else "dev-eu"
    (e["home"] / ".docker" / "config.json").write_text(json.dumps({"currentContext": context}), encoding="utf-8")


def _podman_variable(e, monkeypatch, name):
    monkeypatch.setenv("CONTAINER_CONNECTION", "prod-a" if name == "prodcfg" else "dev-a")


def _azure_switch(e, monkeypatch, name):
    azure(e["home"] / ".azure", "prod-sub" if name == "prodcfg" else "dev-sub")


def _kube_home_switch(e, monkeypatch, name):
    (e["home"] / ".kube").mkdir(exist_ok=True)
    context = "prod-a" if name == "prodcfg" else "dev-a"
    (e["home"] / ".kube" / "config").write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n", encoding="utf-8")


def _tf_switch(e, monkeypatch, name):
    (e["work"] / ".terraform").mkdir(exist_ok=True)
    (e["work"] / ".terraform" / "environment").write_text("prod" if name == "prodcfg" else "dev", encoding="utf-8")


def _aws_variable(e, monkeypatch, name):
    monkeypatch.setenv("AWS_PROFILE", "prod" if name == "prodcfg" else "dev")


def _pulumi_switch(e, monkeypatch, name):
    project = e["work"] / "Pulumi.yaml"
    project.write_text("name: shop\nruntime: python\n", encoding="utf-8")
    digest = hashlib.sha1(str(project).encode()).hexdigest()
    workspaces = e["home"] / ".pulumi" / "workspaces"
    workspaces.mkdir(parents=True, exist_ok=True)
    stack = "prod" if name == "prodcfg" else "dev"
    (workspaces / f"shop-{digest}-workspace.json").write_text(json.dumps({"stack": stack}), encoding="utf-8")


SWITCHES = [
    ("grant-gcloud-active-config", "gcloud compute instances delete vm", _gcloud_switch),
    ("grant-gcloud-project-variable", "gcloud compute instances delete vm", _gcloud_variable),
    ("grant-docker-current-context", "docker rm c1", _docker_switch),
    ("grant-podman-connection-variable", "podman rm c1", _podman_variable),
    ("grant-kube-home-config", "kubectl apply -f x", _kube_home_switch),
    ("grant-terraform-workspace", "terraform apply", _tf_switch),
    ("grant-aws-profile-variable", "aws s3 rm s3://b/x", _aws_variable),
    ("grant-pulumi-current-stack", "pulumi destroy", _pulumi_switch),
    ("grant-azure-default-subscription", "az vm restart -g rg -n vm", _azure_switch),
]


@pytest.mark.parametrize("case", SWITCHES, ids=[c[0] for c in SWITCHES])
def test_r3_grant_does_not_follow_a_silent_switch(case, env, monkeypatch):
    _, command, switch = case
    switch(env, monkeypatch, "default")
    minted = _mint(command, env["work"])
    switch(env, monkeypatch, "prodcfg")
    after = validate(command, env["work"], session="s-grant-r3")
    assert not after.allowed and after.approval_id != minted, after.reason[:160]
