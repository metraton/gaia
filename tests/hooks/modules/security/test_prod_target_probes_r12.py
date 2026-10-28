"""Verifier round-12 probes (task 1020): every input that decides an undeterminable target, changed, asks again."""

from __future__ import annotations

import json
import os

import pytest

from tests.hooks.modules.security.test_prod_target_probes_r6 import _mint, _validate, env, pulumi_project, put
from modules.security.targets import UNREADABLE, grant_target

__all__ = ["env"]

LONE_CR = "apiVersion: v1\rkind: Config\rcurrent-context: {}\r"
CANON = "apiVersion: v1\nkind: Config\ncurrent-context: {}\n"


def _undeterminable(command, cwd):
    bound = grant_target(command, cwd=str(cwd))
    assert bound.startswith(f"{UNREADABLE}:"), bound


def _asks(command, cwd, minted, session="s-scope-r6"):
    retry = _validate(command, cwd, session=session)
    assert not retry.allowed and retry.approval_id and retry.approval_id != minted, (retry.reason or "")[:200]


@pytest.mark.parametrize("first", [
    "apiVersion: v1\nkind: Config\nusers: [{name: u}]\n",
    "apiVersion: v1\nkind: Config\n# current-context lives in the second file\n",
], ids=["flow-style-no-context", "comment-mentions-key"])
def test_r12_kubeconfig_list_second_file_switched_asks(first, env, monkeypatch):
    a = put(env["tmp"] / "kc-a", first)
    b = put(env["tmp"] / "kc-b", CANON.format("dev-a"))
    monkeypatch.setenv("KUBECONFIG", f"{a}{os.pathsep}{b}")
    command = "kubectl apply -f x.yaml"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    put(b, CANON.format("prod-a"))
    _asks(command, env["work"], minted)


def test_r12_kubeconfig_list_first_file_switched_asks(env, monkeypatch):
    a = put(env["tmp"] / "kc-a", "apiVersion: v1\nkind: Config\n")
    b = put(env["tmp"] / "kc-b", LONE_CR.format("dev-a"))
    monkeypatch.setenv("KUBECONFIG", f"{a}{os.pathsep}{b}")
    command = "kubectl apply -f x.yaml"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    put(a, CANON.format("prod-a"))
    _asks(command, env["work"], minted)


def test_r12_kubeconfig_list_missing_entry_created_asks(env, monkeypatch):
    a = put(env["tmp"] / "kc-a", LONE_CR.format("dev-a"))
    missing = env["tmp"] / "kc-later"
    monkeypatch.setenv("KUBECONFIG", f"{a}{os.pathsep}{missing}")
    command = "kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    put(missing, CANON.format("prod-a"))
    _asks(command, env["work"], minted)


def test_r12_pulumi_unparsed_project_stack_selected_asks(env):
    text = "name: shop\ndescription: >\n  long text\nruntime: python\n"
    pulumi_project(env, "Pulumi.yaml", text, "stack", "dev-a")
    command = "pulumi destroy --yes"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    pulumi_project(env, "Pulumi.yaml", text, "stack", "prod-a")
    _asks(command, env["work"], minted)


def _gcloud(e, active, root):
    put(root / "active_config", active)
    for name in ("dev", "prod"):
        put(root / "configurations" / f"config_{name}", f"[core\nproject = {name}\n")


def test_r12_gcloud_active_config_pointer_switched_asks(env, monkeypatch):
    root = env["tmp"] / "gcloud"
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(root))
    _gcloud(env, "dev", root)
    command = "gcloud compute instances delete vm --quiet"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    put(root / "active_config", "prod")
    _asks(command, env["work"], minted)


def test_r12_gcloud_other_configuration_changed_asks(env, monkeypatch):
    root = env["tmp"] / "gcloud"
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(root))
    _gcloud(env, "dev", root)
    command = "gcloud compute instances delete vm --quiet"
    minted = _mint(command, env["work"])
    put(root / "configurations" / "config_prod", "[core]\nproject = prod\n")
    _asks(command, env["work"], minted)


@pytest.mark.parametrize("change", ["content", "directory"])
def test_r12_docker_config_changed_asks(change, env, monkeypatch):
    root = env["tmp"] / "docker"
    put(root / "config.json", json.dumps({"currentContext": 5}))
    monkeypatch.setenv("DOCKER_CONFIG", str(root))
    command = "docker push registry.example/app:1"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    if change == "content":
        put(root / "config.json", json.dumps({"currentContext": 6}))
    else:
        put(env["tmp"] / "docker2" / "config.json", json.dumps({"currentContext": 5}))
        monkeypatch.setenv("DOCKER_CONFIG", str(env["tmp"] / "docker2"))
    _asks(command, env["work"], minted)


@pytest.mark.parametrize("variable", ["DOCKER_HOST", "CONTAINER_HOST"])
def test_r12_engine_host_variable_changed_asks(variable, env, monkeypatch):
    root = env["tmp"] / "docker"
    put(root / "config.json", json.dumps({"currentContext": 5}))
    monkeypatch.setenv("DOCKER_CONFIG", str(root))
    monkeypatch.setenv(variable, "tcp://build-a:2376")
    command = "docker push registry.example/app:1"
    minted = _mint(command, env["work"])
    monkeypatch.setenv(variable, "tcp://build-b:2376")
    _asks(command, env["work"], minted)


@pytest.mark.parametrize("change", ["content", "directory", "config"])
def test_r12_azure_config_changed_asks(change, env, monkeypatch):
    root = env["tmp"] / "azure"
    profile = {"subscriptions": [{"name": "dev", "id": 5, "isDefault": True}]}
    put(root / "azureProfile.json", json.dumps(profile))
    monkeypatch.setenv("AZURE_CONFIG_DIR", str(root))
    command = "az webapp restart --name app --resource-group rg"
    _undeterminable(command, env["work"])
    minted = _mint(command, env["work"])
    if change == "content":
        put(root / "azureProfile.json", json.dumps({"subscriptions": [{"name": "prod", "id": 5, "isDefault": True}]}))
    elif change == "config":
        put(root / "config", "[defaults]\ngroup = prod\n")
    else:
        put(env["tmp"] / "azure2" / "azureProfile.json", json.dumps(profile))
        monkeypatch.setenv("AZURE_CONFIG_DIR", str(env["tmp"] / "azure2"))
    _asks(command, env["work"], minted)


def test_r12_cwd_changed_asks(env):
    put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    command = "kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    other = env["tmp"] / "work2"
    other.mkdir()
    _asks(command, other, minted)


def test_r12_symlink_repointed_to_identical_content_asks(env):
    a = put(env["tmp"] / "real-a", LONE_CR.format("dev-a"))
    b = put(env["tmp"] / "real-b", LONE_CR.format("dev-a"))
    link = env["home"] / ".kube" / "config"
    link.parent.mkdir(parents=True)
    link.symlink_to(a)
    command = "kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    link.unlink()
    link.symlink_to(b)
    _asks(command, env["work"], minted)


def test_r12_mtime_only_touch_still_matches(env):
    config = put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    command = "kubectl apply -f x.yaml"
    _mint(command, env["work"])
    os.utime(config, (1, 1))
    assert _validate(command, env["work"]).allowed


def test_r12_other_command_same_config_asks(env):
    put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    minted = _mint("kubectl apply -f x.yaml", env["work"])
    _asks("kubectl apply -f y.yaml", env["work"], minted)


def test_r12_undeterminable_single_use(env):
    put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    command = "kubectl apply -f x.yaml"
    _mint(command, env["work"])
    assert _validate(command, env["work"]).allowed
    assert not _validate(command, env["work"]).allowed


def test_r12_other_session_follows_the_same_scope(env):
    seen = {}
    for text in (LONE_CR, CANON):
        put(env["home"] / ".kube" / "config", text.format("dev-a"))
        command = f"kubectl apply -f {len(seen)}.yaml"
        _mint(command, env["work"])
        seen[text] = _validate(command, env["work"], session="s-another-session").allowed
    assert seen[LONE_CR] == seen[CANON], seen


def test_r12_shell_computed_context_never_matches(env):
    put(env["home"] / ".kube" / "config", LONE_CR.format("dev-a"))
    command = "kubectl --context $CTX apply -f x.yaml"
    assert grant_target(command, cwd=str(env["work"])) == "?"
    assert not _validate(command, env["work"]).allowed
