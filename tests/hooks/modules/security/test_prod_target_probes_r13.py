"""Round-13 probes (task 1020): every variable under a target-bearing program's prefixes binds its grants and verdicts."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.hooks.modules.security.test_prod_target_probes_r6 import (
    ASK, _mint, _validate, env, pulumi_project, put, verdict,
)
from modules.security.targets import UNDETERMINED, UNREADABLE, grant_target

__all__ = ["env"]

LONE_CR = "apiVersion: v1\rkind: Config\rcurrent-context: {}\r"
CANON = "apiVersion: v1\nkind: Config\ncurrent-context: {}\n"
FOLDED = "name: shop\ndescription: >\n  long text\nruntime: python\n"
PLAIN = "name: shop\nruntime: python\n"


def _asks(command, cwd, minted, session="s-scope-r6"):
    retry = _validate(command, cwd, session=session)
    assert not retry.allowed and retry.approval_id and retry.approval_id != minted, (retry.reason or "")[:200]


def _runs(command, cwd):
    retry = _validate(command, cwd)
    assert retry.allowed, (retry.reason or "")[:200]


def _never_matches(command, cwd):
    assert grant_target(command, cwd=str(cwd)) == UNDETERMINED
    minted = _mint(command, cwd)
    _asks(command, cwd, minted)


def _free(command, cwd):
    got, reason = verdict(command, cwd)
    assert got == "FREE", (got, reason[:160])


def _ask(command, cwd):
    got, reason = verdict(command, cwd)
    assert got in ASK, (got, reason[:160])


def test_grant_target_scope_r13_kubeconfig_entry_is_a_directory_never_matches(env, monkeypatch):
    a = put(env["tmp"] / "kc-a", LONE_CR.format("dev-a"))
    d = env["tmp"] / "kc-dir"
    d.mkdir()
    monkeypatch.setenv("KUBECONFIG", f"{a}{os.pathsep}{d}")
    _never_matches("kubectl apply -f x.yaml", env["work"])


def test_grant_target_scope_r13_kubeconfig_flag_overrides_env_file_switched_asks(env, monkeypatch):
    flagged = put(env["tmp"] / "kc-flag", LONE_CR.format("dev-a"))
    monkeypatch.setenv("KUBECONFIG", str(put(env["tmp"] / "kc-env", CANON.format("dev-a"))))
    command = f"kubectl --kubeconfig {flagged} apply -f x.yaml"
    assert grant_target(command, cwd=str(env["work"])).startswith(f"{UNREADABLE}:")
    minted = _mint(command, env["work"])
    put(flagged, LONE_CR.format("prod-a"))
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_pulumi_workspace_switch_asks(env):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    command = "pulumi destroy --yes"
    assert grant_target(command, cwd=str(env["work"])).startswith(f"{UNREADABLE}:")
    minted = _mint(command, env["work"])
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "prod-a")
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_pulumi_workspace_file_added_asks(env):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    command = "pulumi destroy --yes"
    minted = _mint(command, env["work"])
    put(env["home"] / ".pulumi" / "workspaces" / "other-abc-workspace.json", json.dumps({"stack": "prod-a"}))
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_pulumi_project_file_added_nearer_asks(env):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    sub = env["work"] / "sub"
    sub.mkdir()
    command = "pulumi destroy --yes"
    minted = _mint(command, sub)
    put(sub / "Pulumi.yml", FOLDED)
    _asks(command, sub, minted)


def test_grant_target_scope_r13_pulumi_workspace_tree_over_limit_never_matches(env):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    for i in range(520):
        put(env["home"] / ".pulumi" / "workspaces" / f"pad-{i}-workspace.json", "{}")
    _never_matches("pulumi destroy --yes", env["work"])


def test_grant_target_scope_r13_symlinked_pulumi_workspaces_dir_never_matches(env):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    real = env["home"] / ".pulumi" / "workspaces"
    moved = env["tmp"] / "ws-real"
    real.rename(moved)
    real.symlink_to(moved)
    _never_matches("pulumi destroy --yes", env["work"])


def test_grant_target_scope_r13_symlinked_pulumi_home_repointed_asks(env, monkeypatch):
    pulumi_project(env, "Pulumi.yaml", FOLDED, "stack", "dev-a")
    real = env["home"] / ".pulumi"
    copy_a, copy_b = env["tmp"] / "ph-a", env["tmp"] / "ph-b"
    real.rename(copy_a)
    put(copy_b / "workspaces" / next(iter(os.listdir(copy_a / "workspaces"))), json.dumps({"stack": "dev-a"}))
    link = env["tmp"] / "ph-link"
    link.symlink_to(copy_a)
    monkeypatch.setenv("PULUMI_HOME", str(link))
    command = "pulumi destroy --yes"
    minted = _mint(command, env["work"])
    link.unlink()
    link.symlink_to(copy_b)
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_docker_context_meta_changed_asks(env, monkeypatch):
    root = env["tmp"] / "docker"
    put(root / "config.json", json.dumps({"currentContext": 5}))
    meta = root / "contexts" / "meta" / hashlib.sha256(b"build").hexdigest() / "meta.json"
    put(meta, json.dumps({"Name": "build", "Endpoints": {"docker": {"Host": "tcp://dev:2376"}}}))
    monkeypatch.setenv("DOCKER_CONFIG", str(root))
    command = "docker push registry.example/app:1"
    minted = _mint(command, env["work"])
    put(meta, json.dumps({"Name": "build", "Endpoints": {"docker": {"Host": "tcp://prod:2376"}}}))
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_docker_host_inline_assignment_changed_asks(env, monkeypatch):
    root = env["tmp"] / "docker"
    put(root / "config.json", json.dumps({"currentContext": 5}))
    monkeypatch.setenv("DOCKER_CONFIG", str(root))
    minted = _mint("DOCKER_HOST=tcp://dev:2376 docker push r/app:1", env["work"])
    _asks("DOCKER_HOST=tcp://prod:2376 docker push r/app:1", env["work"], minted)


def test_grant_target_scope_r13_docker_host_switch_on_determinate_grant_asks(env, monkeypatch):
    put(env["home"] / ".docker" / "config.json", json.dumps({"currentContext": "dev-a"}))
    monkeypatch.setenv("DOCKER_HOST", "tcp://dev:2376")
    command = "docker push registry.example/app:1"
    minted = _mint(command, env["work"])
    monkeypatch.setenv("DOCKER_HOST", "tcp://prod:2376")
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_terraform_environment_switch_asks(env):
    put(env["work"] / ".terraform" / "environment", "dev")
    command = "terraform apply -auto-approve"
    minted = _mint(command, env["work"])
    put(env["work"] / ".terraform" / "environment", "prod")
    _asks(command, env["work"], minted)


def _azure(env, monkeypatch, directory, name):
    root = env["tmp"] / directory
    put(root / "azureProfile.json", json.dumps({"subscriptions": [{"name": name, "id": 5, "isDefault": True}]}))
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(root))


def test_grant_target_scope_r13_azure_config_dir_switch_asks(env, monkeypatch):
    _azure(env, monkeypatch, "azure", "dev")
    command = "az vm start -n vm -g rg"
    assert grant_target(command, cwd=str(env["work"])).startswith(f"{UNREADABLE}:")
    minted = _mint(command, env["work"])
    _azure(env, monkeypatch, "azure2", "dev")
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_azure_profile_content_switch_asks(env, monkeypatch):
    _azure(env, monkeypatch, "azure", "dev")
    minted = _mint("az vm start -n vm -g rg", env["work"])
    _azure(env, monkeypatch, "azure", "prod")
    _asks("az vm start -n vm -g rg", env["work"], minted)


def test_r13_helm_kubecontext_prod_tightens_the_verdict(env, monkeypatch):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    monkeypatch.setenv("HELM_KUBECONTEXT", "prod-a")
    _ask("helm frobnicate rel", env["work"])
    monkeypatch.setenv("HELM_KUBECONTEXT", "dev-b")
    _free("helm frobnicate rel", env["work"])


def test_grant_target_scope_r13_helm_kubecontext_variable_switch_asks(env, monkeypatch):
    put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    monkeypatch.setenv("HELM_KUBECONTEXT", "dev-a")
    minted = _mint("helm upgrade rel chart", env["work"])
    monkeypatch.setenv("HELM_KUBECONTEXT", "prod-a")
    _asks("helm upgrade rel chart", env["work"], minted)


def test_grant_target_scope_r13_helm_kubecontext_switch_on_determinate_grant_asks(env, monkeypatch):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    monkeypatch.setenv("HELM_KUBECONTEXT", "dev-a")
    minted = _mint("helm upgrade rel chart", env["work"])
    monkeypatch.setenv("HELM_KUBECONTEXT", "prod-a")
    _asks("helm upgrade rel chart", env["work"], minted)


@pytest.mark.parametrize("variable", ["HELM_KUBEAPISERVER", "HELM_NAMESPACE"])
def test_r13_helm_selector_variables_are_targets(variable, env, monkeypatch):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    monkeypatch.setenv(variable, "prod-a")
    _ask("helm frobnicate rel", env["work"])


def test_r13_tf_data_dir_prod_tightens_the_verdict(env, monkeypatch):
    put(env["work"] / ".terraform" / "environment", "dev")
    put(env["work"] / ".tf-prod" / "environment", "prod")
    put(env["work"] / ".tf-dev" / "environment", "dev")
    monkeypatch.setenv("TF_DATA_DIR", ".tf-prod")
    _ask("terraform frobnicate x", env["work"])
    monkeypatch.setenv("TF_DATA_DIR", ".tf-dev")
    _free("terraform frobnicate x", env["work"])


def test_grant_target_scope_r13_tf_data_dir_switch_on_grant_asks(env, monkeypatch):
    put(env["work"] / ".terraform" / "environment", "dev")
    put(env["work"] / ".tf-prod" / "environment", "prod")
    minted = _mint("terraform apply -auto-approve", env["work"])
    monkeypatch.setenv("TF_DATA_DIR", ".tf-prod")
    _asks("terraform apply -auto-approve", env["work"], minted)


def test_r13_pulumi_backend_url_prod_tightens_the_verdict(env, monkeypatch):
    pulumi_project(env, "Pulumi.yaml", PLAIN, "stack", "dev-a")
    monkeypatch.setenv("PULUMI_BACKEND_URL", "s3://prod-state")
    _ask("pulumi frobnicate", env["work"])
    monkeypatch.setenv("PULUMI_BACKEND_URL", "s3://dev-state")
    _free("pulumi frobnicate", env["work"])


def test_r13_pulumi_credentials_current_backend_prod_tightens_the_verdict(env):
    pulumi_project(env, "Pulumi.yaml", PLAIN, "stack", "dev-a")
    credentials = env["home"] / ".pulumi" / "credentials.json"
    put(credentials, json.dumps({"current": "s3://prod-state", "accessTokens": {}}))
    _ask("pulumi frobnicate", env["work"])
    put(credentials, json.dumps({"current": "s3://dev-state", "accessTokens": {}}))
    _free("pulumi frobnicate", env["work"])


def test_r13_pulumi_project_backend_is_undeterminable(env):
    pulumi_project(env, "Pulumi.yaml", PLAIN + "backend:\n  url: s3://dev-state\n", "stack", "dev-a")
    _ask("pulumi frobnicate", env["work"])


def test_grant_target_scope_r13_pulumi_backend_url_switch_asks(env, monkeypatch):
    pulumi_project(env, "Pulumi.yaml", PLAIN, "stack", "dev-a")
    monkeypatch.setenv("PULUMI_BACKEND_URL", "s3://dev-state")
    minted = _mint("pulumi destroy --yes", env["work"])
    monkeypatch.setenv("PULUMI_BACKEND_URL", "s3://dev-other")
    _asks("pulumi destroy --yes", env["work"], minted)


def test_grant_target_scope_r13_pulumi_credentials_backend_switch_asks(env):
    pulumi_project(env, "Pulumi.yaml", PLAIN, "stack", "dev-a")
    credentials = env["home"] / ".pulumi" / "credentials.json"
    put(credentials, json.dumps({"current": "s3://dev-state"}))
    minted = _mint("pulumi destroy --yes", env["work"])
    put(credentials, json.dumps({"current": "s3://dev-other"}))
    _asks("pulumi destroy --yes", env["work"], minted)


def test_grant_target_scope_r13_pulumi_project_backend_switch_asks(env):
    pulumi_project(env, "Pulumi.yaml", PLAIN + "backend:\n  url: s3://dev-state\n", "stack", "dev-a")
    minted = _mint("pulumi destroy --yes", env["work"])
    put(env["work"] / "Pulumi.yaml", PLAIN + "backend:\n  url: s3://dev-other\n")
    _asks("pulumi destroy --yes", env["work"], minted)


@pytest.mark.parametrize("assignment", ["TF_CLI_ARGS=-lock=false", "TF_VAR_env=x", "HELM_DRIVER=sql",
                                        "AWS_ENDPOINT_URL=http://x", "KUBERC=/x", "CLOUDSDK_API_ENDPOINT_OVERRIDES_COMPUTE=x"])
def test_r13_inline_unknown_prefix_variable_asks(assignment, env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    program = {"TF": "terraform", "HE": "helm", "AW": "aws s3", "KU": "kubectl", "CL": "gcloud compute"}[assignment[:2]]
    _ask(f"{assignment} {program} frobnicate x", env["work"])


@pytest.mark.parametrize("command", ["TF_LOG=debug terraform frobnicate x", "HELM_DEBUG=1 helm frobnicate x",
                                     "TF_CLI_ARGS=-x terraform show", "AWS_REGION=us-east-1 aws s3 frobnicate x"])
def test_r13_inline_harmless_selector_or_read_stays_free(command, env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    _free(command, env["work"])


def test_r13_ambient_unknown_prefix_variable_does_not_ask(env, monkeypatch):
    put(env["work"] / ".terraform" / "environment", "dev")
    monkeypatch.setenv("TF_CLI_ARGS", "-lock=false")
    _free("terraform frobnicate x", env["work"])


def test_grant_target_scope_r13_ambient_unknown_prefix_variable_changed_asks(env, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIADEV")
    command = "aws ec2 stop-instances --instance-ids i-1"
    minted = _mint(command, env["work"])
    _runs(command, env["work"])
    minted = _mint(command, env["work"])
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPROD")
    _asks(command, env["work"], minted)


def test_grant_target_scope_r13_inline_unknown_prefix_variable_binds_its_value(env):
    put(env["work"] / ".terraform" / "environment", "dev")
    minted = _mint("TF_CLI_ARGS=-lock=false terraform apply -auto-approve", env["work"])
    _runs("TF_CLI_ARGS=-lock=false terraform apply -auto-approve", env["work"])
    minted = _mint("TF_CLI_ARGS=-lock=false terraform apply -auto-approve", env["work"])
    _asks("TF_CLI_ARGS=-lock=true terraform apply -auto-approve", env["work"], minted)
