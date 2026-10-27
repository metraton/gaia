"""Verifier probes round 4 (handoff 34241) and the cases that close them."""

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

from modules.security import targets  # noqa: E402
from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.approval_grants import activate_db_pending_by_id  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "aprobet1020x05"
CLEARED = ("GAIA_CLI_ALIASES", "KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
           "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE",
           "CONTAINER_HOST", "CONTAINER_CONNECTION", "PULUMI_HOME", "GIT_DIR", "GIT_WORK_TREE",
           "AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID")


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    from gaia.paths import ensure_layout

    ensure_layout()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return {"home": home, "work": work, "tmp": tmp_path}


def validate(command: str, cwd: Path, session: str = "s-probe4"):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": session, "tool_use_id": "t-probe4", "agent_id": AGENT, "agent_type": "developer"}
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


def azure(directory: Path, default: str = "", raw: str = "", bom: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if raw:
        body = raw
    else:
        names = {"dev-sub": "22222222-dev", "acme-prod": "11111111-aaaa", "Acme Production": "33333333-bbbb"}
        subs = [{"id": i, "name": n, "isDefault": n == default} for n, i in names.items()]
        body = json.dumps({"installationId": "x", "subscriptions": subs})
    (directory / "azureProfile.json").write_text(("﻿" if bom else "") + body, encoding="utf-8")


def gcloud(root: Path, configs: dict, active: str = "default") -> None:
    (root / "configurations").mkdir(parents=True, exist_ok=True)
    (root / "active_config").write_text(active, encoding="utf-8")
    for name, project in configs.items():
        (root / "configurations" / f"config_{name}").write_text(f"[core]\nproject = {project}\n", encoding="utf-8")


def kube(path: Path, context: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n", encoding="utf-8")
    return path


AZ_FETCH_DEV = "az aks get-credentials -n dev-aks -g rg"
# (id, command, setup, accepted, cwd key)
CASES = [
    ("az-sub-flag-eq-prod-unknown", "az frobnicate thing --subscription=acme-prod", {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-sub-flag-eq-dev-unknown", "az frobnicate thing --subscription=dev-sub", {"azure": "acme-prod"}, FREE, "work"),
    ("az-fetch-sub-flag-eq-prod-long-order", "az aks get-credentials --resource-group=rg --name=dev-aks --subscription=acme-prod",
     {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-fetch-sub-flag-before-verb", "az --subscription acme-prod aks get-credentials -g rg -n dev-aks", {}, NOT_FREE, "work"),
    ("az-fetch-sub-id-of-prod", f"{AZ_FETCH_DEV} --subscription 11111111-aaaa", {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-unknown-sub-id-of-prod", "az frobnicate thing --subscription 11111111-aaaa", {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-fetch-ambient-name-with-space", AZ_FETCH_DEV, {"azure": "Acme Production"}, NOT_FREE, "work"),
    ("az-fetch-ambient-no-bom", AZ_FETCH_DEV, {"azure": "acme-prod", "nobom": True}, NOT_FREE, "work"),
    ("az-fetch-ambient-subscriptions-int", AZ_FETCH_DEV, {"azure_raw": '{"subscriptions": 5}'}, NOT_FREE, "work"),
    ("az-fetch-ambient-subscriptions-bool", AZ_FETCH_DEV, {"azure_raw": '{"subscriptions": true}'}, NOT_FREE, "work"),
    ("az-fetch-ambient-top-list", AZ_FETCH_DEV, {"azure_raw": '[1, 2]'}, NOT_FREE, "work"),
    ("az-fetch-ambient-utf16", AZ_FETCH_DEV, {"azure_utf16": "acme-prod"}, NOT_FREE, "work"),
    ("az-fetch-config-dir-inline-prod", "AZURE_CONFIG_DIR={TMP}/azp " + AZ_FETCH_DEV, {"azure": "dev-sub", "azp": "acme-prod"},
     NOT_FREE, "work"),
    ("az-fetch-config-dir-env-wrapper-prod", "env AZURE_CONFIG_DIR={TMP}/azp " + AZ_FETCH_DEV, {"azure": "dev-sub", "azp": "acme-prod"},
     NOT_FREE, "work"),
    ("az-fetch-config-dir-relative-to-cwd", "AZURE_CONFIG_DIR=azp " + AZ_FETCH_DEV, {"azure": "dev-sub", "azp": "acme-prod"},
     NOT_FREE, "tmp"),
    ("az-fetch-config-dir-dynamic", "AZURE_CONFIG_DIR=$D " + AZ_FETCH_DEV, {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-fetch-subscription-variable-dynamic", "AZURE_SUBSCRIPTION_ID=$S " + AZ_FETCH_DEV, {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-fetch-name-dynamic", "az aks get-credentials -g rg -n $N", {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("az-fetch-admin-flag-after", "az aks get-credentials -g rg -n prod-aks --admin --overwrite-existing", {}, NOT_FREE, "work"),
    ("gc-fetch-project-flag-after-operand", "gcloud container clusters get-credentials main --zone z --project=acme-prod", {}, NOT_FREE, "work"),
    ("gc-fetch-configuration-after-operand", "gcloud container clusters get-credentials main --configuration=prodcfg",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE, "work"),
    ("gc-fetch-configuration-variable", "CLOUDSDK_ACTIVE_CONFIG_NAME=prodcfg gcloud container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE, "work"),
    ("gc-fetch-configuration-dynamic", "gcloud --configuration=$C container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE, "work"),
    ("gc-fetch-config-dir-inline", "CLOUDSDK_CONFIG={TMP}/gcp gcloud container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev"}, "gcp": {"default": "acme-prod"}}, NOT_FREE, "work"),
    ("gc-fetch-config-dir-dynamic", "CLOUDSDK_CONFIG=$G gcloud container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev"}}, NOT_FREE, "work"),
    ("gc-fetch-account-prod", "gcloud --account deployer@acme-prod.iam container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev"}}, NOT_FREE, "work"),
    ("gc-dev-fetch-configuration-after-operand", "gcloud container clusters get-credentials main --configuration=devcfg",
     {"gcloud": {"default": "acme-prod", "devcfg": "acme-dev"}}, FREE, "work"),
    ("aws-fetch-profile-after", "aws eks update-kubeconfig --name main --profile prod", {}, NOT_FREE, "work"),
    ("aws-fetch-profile-eq-before", "aws --profile=prod eks update-kubeconfig --name main", {}, NOT_FREE, "work"),
    ("sw-kubectl-use-context-unknown-opt", "kubectl config use-context --frobnicate prod-a", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("sw-kubectx-unknown-opt", "kubectx --frobnicate prod-a", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("sw-docker-context-use-unknown-opt", "docker context use --frobnicate prod-eu", {}, NOT_FREE, "work"),
    ("sw-gcloud-activate-unknown-opt", "gcloud config configurations activate --frobnicate prodcfg",
     {"gcloud": {"default": "acme-dev", "prodcfg": "acme-prod"}}, NOT_FREE, "work"),
    ("sw-pulumi-select-unknown-opt", "pulumi stack select --frobnicate prod", {}, NOT_FREE, "work"),
    ("sw-tf-select-unknown-opt-eq", "terraform workspace select -frobnicate=1 prod", {}, NOT_FREE, "work"),
    ("pulumi-up-ambient-prod", "pulumi up", {"pulumi": "prod"}, NOT_FREE, "work"),
    ("pulumi-up-flag-prod", "pulumi --stack prod up", {}, NOT_FREE, "work"),
    ("o-az-unknown-no-profile", "az frobnicate thing", {}, FREE, "work"),
    ("o-az-unknown-unreadable-profile", "az frobnicate thing", {"azure_raw": "{broken"}, NOT_FREE, "work"),
    ("o-az-read-unreadable-profile", "az vm list -g rg", {"azure_raw": "{broken"}, FREE, "work"),
    ("o-az-version-prod-profile", "az --version", {"azure": "acme-prod"}, FREE, "work"),
    ("o-az-account-list-prod-profile", "az account list", {"azure": "acme-prod"}, FREE, "work"),
    ("o-az-fetch-dev-everything", "az aks get-credentials -g rg -n dev-aks --subscription dev-sub", {"azure": "dev-sub"}, FREE, "work"),
    ("o-terraform-plan-azure-var", "AZURE_SUBSCRIPTION_ID=acme-prod terraform plan", {}, FREE, "work"),
    ("o-grep-subscription", "grep --subscription prod file.txt", {}, FREE, "work"),
    ("o-echo-az", "echo az aks get-credentials -n prod-aks -g rg", {}, FREE, "work"),
    ("o-kubectl-dev-get", "kubectl --context dev-a get pods", {}, FREE, "work"),
    ("o-gcloud-dev-fetch", "gcloud --project acme-dev container clusters get-credentials main", {}, FREE, "work"),
    # Closing cases beyond the verifier's file.
    ("r6-az-fetch-sub-id-not-in-profile", f"{AZ_FETCH_DEV} --subscription 99999999-cccc", {"azure": "dev-sub"}, NOT_FREE, "work"),
    ("r6-az-unknown-sub-id-not-in-profile", "az frobnicate thing --subscription 99999999-cccc", {"azure": "dev-sub"}, FREE, "work"),
    ("r6-az-fetch-sub-id-of-dev", f"{AZ_FETCH_DEV} --subscription 22222222-dev", {"azure": "acme-prod"}, FREE, "work"),
    ("r6-az-fetch-sub-variable-id-of-prod", "AZURE_SUBSCRIPTION_ID=11111111-aaaa " + AZ_FETCH_DEV, {"azure": "dev-sub"},
     NOT_FREE, "work"),
    ("r6-az-fetch-config-dir-env-chdir", "env -C {TMP} AZURE_CONFIG_DIR=azp " + AZ_FETCH_DEV, {"azure": "dev-sub", "azp": "acme-prod"},
     NOT_FREE, "work"),
    ("r6-az-fetch-config-dir-cd-chain", "cd {TMP} && AZURE_CONFIG_DIR=azp " + AZ_FETCH_DEV, {"azure": "dev-sub", "azp": "acme-prod"},
     NOT_FREE, "work"),
    ("r6-az-fetch-config-dir-relative-dev", "AZURE_CONFIG_DIR=azp " + AZ_FETCH_DEV, {"azure": "acme-prod", "azp": "dev-sub"},
     FREE, "tmp"),
    ("r6-gc-fetch-config-dir-relative-to-cwd", "CLOUDSDK_CONFIG=gcp gcloud container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev"}, "gcp": {"default": "acme-prod"}}, NOT_FREE, "tmp"),
    ("r6-gc-fetch-configuration-variable-dynamic", "CLOUDSDK_ACTIVE_CONFIG_NAME=$C gcloud container clusters get-credentials main",
     {"gcloud": {"default": "acme-dev"}}, NOT_FREE, "work"),
    ("r6-kubectl-relative-kubeconfig-prod", "KUBECONFIG=kc kubectl frobnicate x", {"kc": "prod-a"}, NOT_FREE, "tmp"),
    ("r6-kubectl-relative-kubeconfig-dev", "KUBECONFIG=kc kubectl frobnicate x", {"kc": "dev-a", "kube": "prod-a"}, FREE, "tmp"),
    ("r6-kubens-kubeconfig-dynamic", "KUBECONFIG=$K kubens app", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("r6-kubens-unknown-opt", "kubens --frobnicate app", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("r6-kubectx-dev", "kubectx dev-a", {"kube": "dev-a"}, FREE, "work"),
    ("r6-kubectx-unknown-opt-alone", "kubectx --frobnicate", {"kube": "dev-a"}, FREE, "work"),
]


def _arrange(setup: dict, e: dict, monkeypatch) -> None:
    home, tmp = e["home"], e["tmp"]
    if "azure" in setup:
        azure(home / ".azure", setup["azure"], bom=not setup.get("nobom"))
    if "azure_raw" in setup:
        azure(home / ".azure", raw=setup["azure_raw"], bom=False)
    if "azure_utf16" in setup:
        (home / ".azure").mkdir(parents=True, exist_ok=True)
        body = json.dumps({"subscriptions": [{"id": "x", "name": setup["azure_utf16"], "isDefault": True}]})
        (home / ".azure" / "azureProfile.json").write_text(body, encoding="utf-16")
    if "azp" in setup:
        azure(tmp / "azp", setup["azp"])
    if "gcloud" in setup:
        gcloud(home / ".config" / "gcloud", setup["gcloud"])
    if "gcp" in setup:
        gcloud(tmp / "gcp", setup["gcp"])
    if "kube" in setup:
        monkeypatch.setenv("KUBECONFIG", str(kube(tmp / "kubeconfig", setup["kube"])))
    if "kc" in setup:
        kube(tmp / "kc", setup["kc"])
    if "pulumi" in setup:
        project = e["work"] / "Pulumi.yaml"
        project.write_text("name: shop\nruntime: python\n", encoding="utf-8")
        digest = hashlib.sha1(str(project).encode()).hexdigest()
        workspaces = home / ".pulumi" / "workspaces"
        workspaces.mkdir(parents=True, exist_ok=True)
        (workspaces / f"shop-{digest}-workspace.json").write_text(json.dumps({"stack": setup["pulumi"]}), encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_r4_probe(case, env, monkeypatch):
    _, command, setup, accepted, where = case
    _arrange(setup, env, monkeypatch)
    got, reason = verdict(command.replace("{TMP}", str(env["tmp"])), env[where])
    assert got in accepted, (got, reason[:160])


EXTRA = [
    ("o-az-version-prod-profile", "az --version", {"azure": "acme-prod"}, FREE),
    ("o-az-help-prod-profile", "az --help", {"azure": "acme-prod"}, FREE),
    ("o-az-bare-prod-profile", "az", {"azure": "acme-prod"}, FREE),
    ("o-az-version-dev-profile", "az --version", {"azure": "dev-sub"}, FREE),
    ("o-gcloud-version-prod-project", "gcloud --version", {"gcloud": {"default": "acme-prod"}}, FREE),
    ("o-az-find-prod-profile", "az find vm", {"azure": "acme-prod"}, FREE),
    ("o-az-upgrade-prod-profile", "az upgrade", {"azure": "acme-prod"}, NOT_FREE),
]


@pytest.mark.parametrize("case", EXTRA, ids=[c[0] for c in EXTRA])
def test_r4_extra(case, env, monkeypatch):
    _, command, setup, accepted = case
    _arrange(setup, env, monkeypatch)
    got, reason = verdict(command, env["work"])
    assert got in accepted, (got, reason[:160])


PROD_AMBIENT = {"azure": "acme-prod", "gcloud": {"default": "acme-prod"}, "kube": "prod-a", "pulumi": "prod"}
NO_OPERATION = [
    "az", "az --version", "az --help", "az aks --help", "az --subscription acme-prod --version", "az help",
    "gcloud", "gcloud --version", "gcloud help container clusters", "gcloud container clusters --help",
    "kubectl", "kubectl --context prod-a", "kubectl --help", "kubectl help apply", "kubectl --context prod-a frobnicate --help",
    "helm --kube-context prod-a --help", "docker --context prod-eu --version", "aws --profile prod help", "aws --version",
    "pulumi --stack prod --version", "pulumi help up", "kubectx --help", "terraform --help",
]


@pytest.mark.parametrize("command", NO_OPERATION)
def test_a_command_that_performs_no_operation_keeps_its_verdict_whatever_the_ambient_target(command, env, monkeypatch):
    without = verdict(command, env["work"])[0]
    _arrange(PROD_AMBIENT, env, monkeypatch)
    assert verdict(command, env["work"])[0] == without == "FREE", command


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


def _declare(path: str, payload: dict) -> None:
    from gaia.store.writer import _connect

    con = _connect()
    con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('probe')")
    con.execute("INSERT OR REPLACE INTO projects (workspace, name, path, status, project_identity) VALUES ('probe', 'p', ?, 'active', 'probe/p')",
                (path,))
    con.execute("INSERT OR REPLACE INTO project_context (project_identity, contract_name, payload) VALUES ('probe/p', 'targets', ?)",
                (json.dumps(payload),))
    con.commit()
    con.close()


# (id, command, declared-at key, declared names, cwd key, accepted)
BRANCH = [
    ("p-dash-u", "git push -u origin release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-set-upstream", "git push --set-upstream origin release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-head-colon", "git push origin HEAD:release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-src-dst-same", "git push origin release/1.x:release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-plus-full", "git push origin +refs/heads/release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-declared-as-full-ref", "git push origin release/1.x", "repo", ["refs/heads/release/1.x"], "repo", NOT_FREE),
    ("p-two-refspecs", "git push origin feature-x release/1.x", "repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-cd-chain-from-outside", "cd {REPO} && git push origin release/1.x", "repo", ["release/1.x"], "work", NOT_FREE),
    ("p-cd-link-sub-chain", "cd {LINK}/sub && git push origin release/1.x", "repo", ["release/1.x"], "work", NOT_FREE),
    ("p-dash-C-link-declared-real", "git -C {LINK} push origin release/1.x", "repo", ["release/1.x"], "work", NOT_FREE),
    ("p-dash-C-real-declared-link", "git -C {REPO}/sub push origin release/1.x", "link", ["release/1.x"], "work", NOT_FREE),
    ("p-stored-trailing-slash", "git push origin release/1.x", "repo/", ["release/1.x"], "repo", NOT_FREE),
    ("p-stored-dotdot", "git push origin release/1.x", "repo/../repo", ["release/1.x"], "repo", NOT_FREE),
    ("p-sub-of-link", "git push origin release/1.x", "repo", ["release/1.x"], "linksub", NOT_FREE),
    ("p-twin-feature-dash-u", "git push -u origin feature-x", "repo", ["release/1.x"], "repo", FREE),
    ("p-twin-feature-link", "git -C {LINK} push origin feature-x", "repo", ["release/1.x"], "work", FREE),
    ("p-twin-feature-cd", "cd {REPO} && git push origin feature-x", "repo", ["release/1.x"], "work", FREE),
]


@pytest.mark.parametrize("case", BRANCH, ids=[c[0] for c in BRANCH])
def test_r4_branch_probe(case, env):
    _, command, declared_at, names, where, accepted = case
    repo = _repo(env["tmp"] / "repo")
    link = env["tmp"] / "link"
    link.symlink_to(repo)
    stored = {"repo": str(repo), "link": str(link), "repo/": str(repo) + "/", "repo/../repo": str(repo) + "/../repo"}[declared_at]
    _declare(stored, {"protected_branches": names})
    cwd = {"repo": repo, "work": env["work"], "linksub": link / "sub"}[where]
    got, reason = verdict(command.replace("{REPO}", str(repo)).replace("{LINK}", str(link)), cwd)
    assert got in accepted, (got, reason[:160])


def _mint(command: str, cwd) -> str:
    blocked = validate(command, cwd, session="s-grant-r4")
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    activation = activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver")
    assert activation.success, activation.reason
    return blocked.approval_id


def _az_id_switch(e, monkeypatch, name):
    azure(e["home"] / ".azure", "acme-prod" if name == "prod" else "dev-sub")


def _az_config_dir_switch(e, monkeypatch, name):
    azure(e["tmp"] / ("azprod" if name == "prod" else "azdev"), "acme-prod" if name == "prod" else "dev-sub")
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(e["tmp"] / ("azprod" if name == "prod" else "azdev")))


def _az_variable_switch(e, monkeypatch, name):
    azure(e["home"] / ".azure", "dev-sub")
    if name == "prod":
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "11111111-aaaa")


def _az_profile_breaks(e, monkeypatch, name):
    if name == "prod":
        azure(e["home"] / ".azure", raw='{"subscriptions": 5}', bom=False)
    else:
        azure(e["home"] / ".azure", "dev-sub")


GRANTS = [
    ("g-az-default-switch-vm-stop", "az vm stop -g rg -n vm", _az_id_switch),
    ("g-az-config-dir-switch", "az vm restart -g rg -n vm", _az_config_dir_switch),
    ("g-az-variable-added", "az vm restart -g rg -n vm", _az_variable_switch),
    ("g-az-profile-becomes-malformed", "az vm restart -g rg -n vm", _az_profile_breaks),
]


@pytest.mark.parametrize("case", GRANTS, ids=[c[0] for c in GRANTS])
def test_r4_grant_does_not_follow(case, env, monkeypatch):
    _, command, switch = case
    switch(env, monkeypatch, "dev")
    minted = _mint(command, env["work"])
    switch(env, monkeypatch, "prod")
    after = validate(command, env["work"], session="s-grant-r4")
    assert not after.allowed and after.approval_id != minted, (after.reason or "")[:160]


JUNK = {
    "number": "5", "string": '"x"', "null": "null", "list": "[1, 2]", "bool": "true", "deep": "[" * 50000,
    "broken": "{broken", "bad-encoding": b"\xff\xfe\x00\xd8\x00", "binary": b"\x00\x01\x02",
    "subscriptions-number": '{"subscriptions": 5}', "subscriptions-junk": '{"subscriptions": [5, "x", null, [1]]}',
    "default-typed-oddly": '{"subscriptions": [{"isDefault": true, "name": 5, "id": {"a": 1}}]}',
    "context-object": '{"current-context": {"a": 1}, "currentContext": [1], "stack": 7}',
}
JUNK_FILES = {
    "azure": ("home/.azure/azureProfile.json", "az aks get-credentials -g rg -n dev-aks"),
    "kube": ("home/.kube/config", "kubens app"),
    "docker": ("home/.docker/config.json", "docker frobnicate"),
    "gcloud-active": ("home/.config/gcloud/active_config", "gcloud container clusters get-credentials main"),
    "gcloud-config": ("home/.config/gcloud/configurations/config_default", "gcloud container clusters get-credentials main"),
    "pulumi": ("pulumi-workspace", "pulumi stack select dev"),
}


@pytest.mark.parametrize("junk", sorted(JUNK))
@pytest.mark.parametrize("source", sorted(JUNK_FILES))
def test_a_malformed_configuration_file_is_undeterminable_and_never_raises(source, junk, env):
    relative, command = JUNK_FILES[source]
    content = JUNK[junk]
    if source == "pulumi":
        project = env["work"] / "Pulumi.yaml"
        project.write_text("name: shop\nruntime: python\n", encoding="utf-8")
        relative = f"home/.pulumi/workspaces/shop-{hashlib.sha1(str(project).encode()).hexdigest()}-workspace.json"
    path = env["tmp"] / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    cwd = str(env["work"])
    found = targets.command_targets(command, cwd=cwd)
    assert all(isinstance(t.name, str) for t in found)
    targets.grant_target(command, cwd=cwd)
    detected = detect_mutative_command(command, cwd=cwd)
    escalated = targets.production_target_verdict(command, detected, cwd=cwd)
    well_formed_kubeconfig = source == "kube" and junk in ("subscriptions-number", "subscriptions-junk", "default-typed-oddly")
    gcloud_unreadable = source.startswith("gcloud") and junk in ("bad-encoding", "deep")
    if source == "azure" or (source == "kube" and not well_formed_kubeconfig) or gcloud_unreadable:
        assert targets.grant_target(command, cwd=cwd) == targets.UNDETERMINED
        assert escalated is not None and "could not be determined" in escalated.reason


def test_an_unexpected_failure_inside_the_targets_module_asks_instead_of_escaping(env, monkeypatch):
    def boom(*_args, **_kwargs):
        raise TypeError("unexpected")

    monkeypatch.setattr(targets, "_operation", boom)
    monkeypatch.setattr(targets, "command_targets", boom)
    command = "kubectl --context dev-a frobnicate x"
    escalated = targets.production_target_verdict(command, detect_mutative_command(command), cwd=str(env["work"]))
    assert escalated is not None and escalated.is_mutative and "could not be determined" in escalated.reason
    assert targets.grant_target(command, cwd=str(env["work"])) == targets.UNDETERMINED
    assert verdict(command, env["work"])[0] in ("SIGNED", "UNKNOWN")
