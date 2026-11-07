"""Round-14 probes (task 1020): env's environment edits, chained exports and the prefix-class selectors."""

from __future__ import annotations

import pytest

from tests.hooks.modules.security.test_prod_target_probes_r6 import ASK, _mint, _validate, env, put, verdict

__all__ = ["env"]

CANON = "apiVersion: v1\nkind: Config\ncurrent-context: {}\n"


def _ask(command, cwd):
    got, reason = verdict(command, cwd)
    assert got in ASK, (got, reason[:160])


def _free(command, cwd):
    got, reason = verdict(command, cwd)
    assert got == "FREE", (got, reason[:160])


def _chain_refused(command, cwd):
    got, reason = verdict(command, cwd)
    assert got != "FREE", (got, reason[:160])
    assert "chain" in reason.lower() or "compound" in reason.lower(), (got, reason[:200])


def _kube_env_dev_home_prod(env, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(put(env["tmp"] / "kc-dev", CANON.format("dev-a"))))
    put(env["home"] / ".kube" / "config", CANON.format("prod-a"))


@pytest.mark.parametrize("command", [
    "env -u KUBECONFIG kubectl frobnicate x",
    "env --unset=KUBECONFIG kubectl frobnicate x",
    "env --unset KUBECONFIG kubectl frobnicate x",
    "env -uKUBECONFIG kubectl frobnicate x",
    "env -i kubectl frobnicate x",
    "env --ignore-environment kubectl frobnicate x",
    "env - kubectl frobnicate x",
    "env -i HOME=$HOME kubectl frobnicate x",
    "env -S '-u KUBECONFIG kubectl' frobnicate x",
    "env --frobnicate kubectl frobnicate x",
])
def test_r14_env_edit_lands_on_home_prod_asks(command, env, monkeypatch):
    _kube_env_dev_home_prod(env, monkeypatch)
    _free("kubectl frobnicate x", env["work"])
    _ask(command, env["work"])


def test_r14_env_keeps_dev_when_it_edits_nothing_that_selects(env, monkeypatch):
    _kube_env_dev_home_prod(env, monkeypatch)
    _free("env -u TF_DATA_DIR kubectl frobnicate x", env["work"])
    _free("env FOO=1 kubectl frobnicate x", env["work"])


def test_r14_shell_unset_kubeconfig_is_refused_by_the_chain_rule(env, monkeypatch):
    _kube_env_dev_home_prod(env, monkeypatch)
    _chain_refused("unset KUBECONFIG; kubectl frobnicate x", env["work"])


def test_r14_env_unset_tf_data_dir_lands_on_prod_asks(env, monkeypatch):
    put(env["work"] / ".terraform" / "environment", "prod")
    put(env["work"] / ".tf-dev" / "environment", "dev")
    monkeypatch.setenv("TF_DATA_DIR", ".tf-dev")
    _free("terraform frobnicate x", env["work"])
    _ask("env -u TF_DATA_DIR terraform frobnicate x", env["work"])


def test_grant_target_scope_r14_env_unset_grant_after_home_switch_asks(env, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(put(env["tmp"] / "kc-dev", CANON.format("dev-a"))))
    put(env["home"] / ".kube" / "config", CANON.format("dev-b"))
    command = "env -u KUBECONFIG kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    put(env["home"] / ".kube" / "config", CANON.format("prod-a"))
    retry = _validate(command, env["work"])
    assert not retry.allowed and retry.approval_id and retry.approval_id != minted, (retry.reason or "")[:200]


def test_grant_target_scope_r14_env_ignore_environment_grant_after_home_switch_asks(env, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(put(env["tmp"] / "kc-dev", CANON.format("dev-a"))))
    put(env["home"] / ".kube" / "config", CANON.format("dev-b"))
    command = "env -i kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    put(env["home"] / ".kube" / "config", CANON.format("prod-a"))
    retry = _validate(command, env["work"])
    assert not retry.allowed and retry.approval_id and retry.approval_id != minted, (retry.reason or "")[:200]


@pytest.mark.parametrize("command", [
    "export HELM_KUBECONTEXT=prod-a && helm frobnicate rel",
    "export HELM_DRIVER=sql && helm frobnicate rel",
    "export TF_WORKSPACE=prod-a && terraform frobnicate x",
    "export TF_LOG=debug && terraform plan",
])
def test_r14_chained_export_is_refused_by_the_chain_rule(command, env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    put(env["work"] / ".terraform" / "environment", "dev")
    _chain_refused(command, env["work"])


@pytest.mark.parametrize("command", [
    "helm --kube-context prod-a frobnicate rel",
    "TF_WORKSPACE=prod-a terraform frobnicate x",
    "DOCKER_TLS_VERIFY=1 docker frobnicate x",
    "HELM_KUBEASUSER=admin helm frobnicate rel",
    "HELM_PLUGINS=/tmp/p helm frobnicate rel",
    "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=sa@p.iam.gserviceaccount.com gcloud compute frobnicate x",
])
def test_r14_selector_or_unknown_prefix_asks(command, env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    put(env["work"] / ".terraform" / "environment", "dev")
    _ask(command, env["work"])


@pytest.mark.parametrize("command", [
    "TF_LOG=debug terraform plan",
    "HELM_DEBUG=1 helm list",
    "AWS_PAGER= aws s3 ls",
    "AWS_ENDPOINT_URL=http://x aws s3 ls",
    "CLOUDSDK_CORE_DISABLE_PROMPTS=1 gcloud compute instances list",
    "env -i kubectl get pods",
    "tf_workspace=prod-a terraform frobnicate x",
])
def test_r14_harmless_or_read_keeps_dev_verdict(command, env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    put(env["work"] / ".terraform" / "environment", "dev")
    _free(command, env["work"])


def test_r14_helm_namespace_flag_matches_variable(env):
    put(env["home"] / ".kube" / "config", CANON.format("dev-a"))
    a, _ = verdict("HELM_NAMESPACE=prod-a helm frobnicate rel", env["work"])
    b, _ = verdict("helm -n prod-a frobnicate rel", env["work"])
    assert a == b, (a, b)
