"""Verifier probes round 5 (handoff 34254) with its triage, kept as permanent cases."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import resource
import signal
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
for entry in (str(ROOT), str(ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.approval_grants import activate_db_pending_by_id  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.targets import _READ_LIMIT  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "aprobet1020x05"
CLEARED = ("KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
           "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE",
           "CONTAINER_HOST", "CONTAINER_CONNECTION", "PULUMI_HOME", "GIT_DIR", "GIT_WORK_TREE",
           "AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID")
NOT_FREE = ("SIGNED", "UNKNOWN")
DENIED = ("SIGNED", "UNKNOWN", "BLOCKED")
FREE = ("FREE",)


class Hung(BaseException):
    pass


@contextlib.contextmanager
def deadline(seconds, label):
    def alarm(*_):
        raise Hung(f"{label} hung past {seconds}s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


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


def validate(command, cwd, session="s-probe5"):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": session, "tool_use_id": "t-probe5", "agent_id": AGENT, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(command, is_subagent=True, session_id=session,
                                                          agent_type="developer", hook_payload=payload)


def verdict(command, cwd):
    result = validate(command, cwd)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith("Compound T3 execution is disabled"):
        return ("UNKNOWN" if "could not be determined" in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


def azure(directory, default="", raw=None):
    directory.mkdir(parents=True, exist_ok=True)
    if raw is None:
        names = {"dev-sub": "22222222-dev", "acme-prod": "11111111-aaaa", "Acme Production": "33333333-bbbb", "ACME-PROD2": "44444444-cccc"}
        raw = json.dumps({"subscriptions": [{"id": i, "name": n, "isDefault": n == default} for n, i in names.items()]})
    (directory / "azureProfile.json").write_text(raw, encoding="utf-8")


def gcloud(root, configs, active="default"):
    (root / "configurations").mkdir(parents=True, exist_ok=True)
    (root / "active_config").write_text(active, encoding="utf-8")
    for name, project in configs.items():
        (root / "configurations" / f"config_{name}").write_text(f"[core]\nproject = {project}\n", encoding="utf-8")


def kube(path, context=None, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n", encoding="utf-8")
    return path


def docker(root, context):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"currentContext": context}), encoding="utf-8")


def pulumi(e, stack, home=None):
    project = e["work"] / "Pulumi.yaml"
    project.write_text("name: shop\nruntime: python\n", encoding="utf-8")
    digest = hashlib.sha1(str(project).encode()).hexdigest()
    workspaces = (home or e["home"] / ".pulumi") / "workspaces"
    workspaces.mkdir(parents=True, exist_ok=True)
    (workspaces / f"shop-{digest}-workspace.json").write_text(json.dumps({"stack": stack}), encoding="utf-8")


def arrange(setup, e):
    home, tmp = e["home"], e["tmp"]
    if "azure" in setup:
        azure(home / ".azure", setup["azure"])
    for rel in ("azp", "azd"):
        if rel in setup:
            azure(tmp / rel, setup[rel])
    if "gcloud" in setup:
        gcloud(home / ".config" / "gcloud", setup["gcloud"])
    if "gcp" in setup:
        gcloud(tmp / "gcp", setup["gcp"])
    if "kube" in setup:
        kube(home / ".kube" / "config", setup["kube"])
    if "kc" in setup:
        kube(tmp / "kc", setup["kc"])
    if "kraw" in setup:
        kube(home / ".kube" / "config", raw=setup["kraw"])
    if "docker" in setup:
        docker(home / ".docker", setup["docker"])
    if "dc" in setup:
        docker(tmp / "dc", setup["dc"])
    if "pulumi" in setup:
        pulumi(e, setup["pulumi"])
    if "ph" in setup:
        pulumi(e, setup["ph"], home=tmp / "ph")


FETCH = "az aks get-credentials -n dev-aks -g rg"
GFETCH = "gcloud container clusters get-credentials main"
DEV = {"azure": "dev-sub", "gcloud": {"default": "acme-dev"}, "kube": "dev-a", "docker": "dev-eu"}
PROBES = [
    ("var-kubeconfig-unknown", "KUBECONFIG=$K kubectl frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-braced-quoted", 'KUBECONFIG="${K}" kubectl frobnicate x', DEV, NOT_FREE, "work"),
    ("var-kubeconfig-list-part", "KUBECONFIG=/nonexistent:$K kubectl frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-substitution", "KUBECONFIG=$(cat f) kubectl frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-export-chain", "export KUBECONFIG=$K && kubectl frobnicate x", DEV, NOT_FREE, "work"),
    ("lit-kubeconfig-export-chain-prod", "export KUBECONFIG={TMP}/kc && kubectl frobnicate x", {**DEV, "kc": "prod-a"}, NOT_FREE, "work"),
    ("var-kubeconfig-flag", "kubectl --kubeconfig $K frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-flag-eq", "kubectl --kubeconfig=$K frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-helm", "helm --kubeconfig=$K frobnicate x", DEV, NOT_FREE, "work"),
    ("var-kubeconfig-kubectx", "KUBECONFIG=$K kubectx dev-a", DEV, NOT_FREE, "work"),
    ("var-pulumi-home-up", "PULUMI_HOME=$P pulumi up", {**DEV, "pulumi": "dev"}, NOT_FREE, "work"),
    ("lit-pulumi-home-up-prod", "PULUMI_HOME={TMP}/ph pulumi up", {**DEV, "pulumi": "dev", "ph": "prod"}, NOT_FREE, "work"),
    ("lit-pulumi-home-up-dev", "PULUMI_HOME={TMP}/ph pulumi up", {**DEV, "pulumi": "prod", "ph": "dev"}, FREE, "work"),
    ("var-docker-config-unknown", "DOCKER_CONFIG=$D docker frobnicate x", DEV, NOT_FREE, "work"),
    ("var-docker-config-flag", "docker --config $D frobnicate x", DEV, NOT_FREE, "work"),
    ("lit-docker-config-flag-prod", "docker --config {TMP}/dc frobnicate x", {**DEV, "dc": "prod-eu"}, NOT_FREE, "work"),
    ("lit-docker-config-flag-eq-prod", "docker --config={TMP}/dc frobnicate x", {**DEV, "dc": "prod-eu"}, NOT_FREE, "work"),
    ("lit-docker-config-flag-dev-twin", "docker --config {TMP}/dc frobnicate x", {"docker": "prod-eu", "dc": "dev-eu"}, FREE, "work"),
    ("var-docker-context-unknown", "DOCKER_CONTEXT=$X docker frobnicate x", DEV, NOT_FREE, "work"),
    ("var-docker-host-unknown", "DOCKER_HOST=$H docker frobnicate x", DEV, NOT_FREE, "work"),
    ("o-var-kubeconfig-read", "KUBECONFIG=$K kubectl get pods", DEV, FREE, "work"),
    ("o-var-azure-dir-read", "AZURE_CONFIG_DIR=$D az vm list -g rg", DEV, FREE, "work"),
    ("o-var-gcloud-dir-read", "CLOUDSDK_CONFIG=$G gcloud compute instances list", DEV, FREE, "work"),
    ("o-var-docker-config-read", "DOCKER_CONFIG=$D docker ps", DEV, FREE, "work"),
    ("o-var-pulumi-home-read", "PULUMI_HOME=$P pulumi stack ls", {**DEV, "pulumi": "dev"}, FREE, "work"),
    ("rel-az-cd-dotdot", "cd .. && AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-cd-semicolon", "cd {TMP}; AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-env-chdir-long", "env --chdir={TMP} AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-env-C-attached", "env -C{TMP} AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-env-C-dotdot", "env -C .. AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-pushd", "pushd {TMP} && AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-subshell", "(cd {TMP} && AZURE_CONFIG_DIR=azp " + FETCH + ")", {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-az-cd-env-wrapper", "cd {TMP} && env AZURE_CONFIG_DIR=azp " + FETCH, {**DEV, "azp": "acme-prod"}, NOT_FREE, "work"),
    ("rel-gc-cd-chain", "cd {TMP} && CLOUDSDK_CONFIG=gcp " + GFETCH, {**DEV, "gcp": {"default": "acme-prod"}}, NOT_FREE, "work"),
    ("rel-gc-env-C", "env -C {TMP} CLOUDSDK_CONFIG=gcp " + GFETCH, {**DEV, "gcp": {"default": "acme-prod"}}, NOT_FREE, "work"),
    ("rel-kube-cd-chain", "cd {TMP} && KUBECONFIG=kc kubectl frobnicate x", {**DEV, "kc": "prod-a"}, NOT_FREE, "work"),
    ("rel-kube-env-C", "env -C {TMP} KUBECONFIG=kc kubectl frobnicate x", {**DEV, "kc": "prod-a"}, NOT_FREE, "work"),
    ("rel-kube-flag-cd-chain", "cd {TMP} && kubectl --kubeconfig=kc frobnicate x", {**DEV, "kc": "prod-a"}, NOT_FREE, "work"),
    ("rel-docker-config-cwd", "DOCKER_CONFIG=dc docker frobnicate x", {**DEV, "dc": "prod-eu"}, NOT_FREE, "tmp"),
    ("rel-docker-config-cd", "cd {TMP} && DOCKER_CONFIG=dc docker frobnicate x", {**DEV, "dc": "prod-eu"}, NOT_FREE, "work"),
    ("rel-az-cd-dev-twin", "cd {TMP} && AZURE_CONFIG_DIR=azd " + FETCH, {"azure": "acme-prod", "azd": "dev-sub"}, FREE, "work"),
    ("rel-kube-env-C-dev-twin", "env -C {TMP} KUBECONFIG=kc kubectl frobnicate x", {"kube": "prod-a", "kc": "dev-a"}, FREE, "work"),
    ("sub-name-upper", FETCH + " --subscription ACME-PROD", DEV, NOT_FREE, "work"),
    ("sub-id-upper", FETCH + " --subscription 11111111-AAAA", DEV, NOT_FREE, "work"),
    ("sub-id-upper-eq", FETCH + " --subscription=33333333-BBBB", DEV, NOT_FREE, "work"),
    ("sub-name-lower-spaced", FETCH + " --subscription 'acme production'", DEV, NOT_FREE, "work"),
    ("sub-unknown-verb-id-upper", "az frobnicate thing --subscription 11111111-AAAA", DEV, NOT_FREE, "work"),
    ("sub-variable-id-upper", "AZURE_SUBSCRIPTION_ID=11111111-AAAA " + FETCH, DEV, NOT_FREE, "work"),
    ("sub-default-upper-name", FETCH, {"azure": "ACME-PROD2"}, NOT_FREE, "work"),
    ("sub-id-of-upper-name-lower", FETCH + " --subscription 44444444-CCCC", DEV, NOT_FREE, "work"),
    ("sub-dev-name-upper-twin", FETCH + " --subscription DEV-SUB", {"azure": "acme-prod"}, FREE, "work"),
    ("sub-dev-id-upper-twin", FETCH + " --subscription 22222222-DEV", {"azure": "acme-prod"}, FREE, "work"),
    ("kubens-prod-switch", "kubens app", {"kube": "prod-a"}, NOT_FREE, "work"),
    ("kubens-prod-back", "kubens -", {"kube": "prod-a"}, NOT_FREE, "work"),
    ("kubens-dev-switch", "kubens app", {"kube": "dev-a"}, FREE, "work"),
    ("kubens-prod-current", "kubens -c", {"kube": "prod-a"}, FREE, "work"),
    ("kubens-prod-list", "kubens", {"kube": "prod-a"}, FREE, "work"),
    ("kubens-prod-help", "kubens --help", {"kube": "prod-a"}, FREE, "work"),
    ("kubens-context-flag-prod", "KUBECONFIG={TMP}/kc kubens app", {"kube": "dev-a", "kc": "prod-a"}, NOT_FREE, "work"),
    ("kubectx-back-undetermined", "kubectx -", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("kubectx-prod-current", "kubectx -c", {"kube": "prod-a"}, FREE, "work"),
    ("kubectx-prod-list", "kubectx", {"kube": "prod-a"}, FREE, "work"),
    ("kubectx-to-prod-trailing-opt", "kubectx prod-a --frobnicate", {"kube": "dev-a"}, NOT_FREE, "work"),
    ("kubectx-from-prod-to-dev", "kubectx dev-a", {"kube": "prod-a"}, FREE, "work"),
    ("kraw-quoted-key", "kubectl frobnicate x", {"kraw": 'apiVersion: v1\n"current-context": prod-a\n'}, NOT_FREE, "work"),
    ("kraw-single-quoted-key", "kubectl frobnicate x", {"kraw": "apiVersion: v1\n'current-context': prod-a\n"}, NOT_FREE, "work"),
    ("kraw-space-before-colon", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context : prod-a\n"}, NOT_FREE, "work"),
    ("kraw-str-tag", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context: !!str prod-a\n"}, NOT_FREE, "work"),
    ("kraw-block-scalar", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context: >-\n  prod-a\n"}, NOT_FREE, "work"),
    ("kraw-alias", "kubectl frobnicate x", {"kraw": "apiVersion: v1\npreferences: {x: &c prod-a}\ncurrent-context: *c\n"}, NOT_FREE, "work"),
    ("kraw-next-line-value", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context:\n  prod-a\n"}, NOT_FREE, "work"),
    ("kraw-folded-plain", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context: dev\n  prod-a\n"}, NOT_FREE, "work"),
    ("kraw-flow-mapping", "kubectl frobnicate x", {"kraw": "{apiVersion: v1, current-context: prod-a}\n"}, NOT_FREE, "work"),
    ("kraw-explicit-key", "kubectl frobnicate x", {"kraw": "apiVersion: v1\n? current-context\n: prod-a\n"}, NOT_FREE, "work"),
    ("kraw-escaped-double", "kubectl frobnicate x", {"kraw": 'apiVersion: v1\ncurrent-context: "\\x70rod-a"\n'}, NOT_FREE, "work"),
    ("kraw-duplicate-key", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context: dev-a\ncurrent-context: prod-a\n"}, NOT_FREE, "work"),
    ("kraw-canonical-dev-twin", "kubectl frobnicate x", {"kraw": "apiVersion: v1\ncurrent-context: dev-a # note\nkind: Config\n"}, FREE, "work"),
    ("kraw-quoted-value-dev-twin", "kubectl frobnicate x", {"kraw": 'apiVersion: v1\ncurrent-context: "dev-a"\n'}, FREE, "work"),
    ("kraw-empty-value-twin", "kubectl frobnicate x", {"kraw": 'apiVersion: v1\ncurrent-context: ""\nkind: Config\n'}, FREE, "work"),
]
KEEPS_VERDICT = {
    "var-kubeconfig-unknown", "var-kubeconfig-braced-quoted", "var-kubeconfig-list-part", "var-kubeconfig-substitution",
    "var-kubeconfig-flag", "var-kubeconfig-flag-eq", "var-kubeconfig-helm", "var-kubeconfig-kubectx",
    "var-pulumi-home-up", "var-docker-config-unknown", "var-docker-context-unknown", "var-docker-host-unknown",
}
CHAINED = {"var-kubeconfig-export-chain", "lit-kubeconfig-export-chain-prod", "rel-kube-flag-cd-chain"}
RENAMED = {"sub-default-upper-name": (FETCH, {"azure": "ACME-PROD"}),
           "sub-id-of-upper-name-lower": (FETCH + " --subscription 55555555-DDDD", {"azure": "dev-sub"})}


def _triaged(row):
    name, command, setup, accepted, where = row
    if name in KEEPS_VERDICT:
        accepted = FREE
    if name in CHAINED:
        accepted = DENIED
    if name in RENAMED:
        command, setup = RENAMED[name]
    return name, command, setup, accepted, where


CASES = [_triaged(row) for row in PROBES]


def _upper_profile(e):
    subs = [{"id": "22222222-dev", "name": "dev-sub", "isDefault": False},
            {"id": "55555555-dddd", "name": "ACME-PROD", "isDefault": False}]
    azure(e["home"] / ".azure", raw=json.dumps({"subscriptions": subs}))


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_r5_probe(case, env):
    name, command, setup, accepted, where = case
    if name in {"var-pulumi-home-up", "lit-pulumi-home-up-dev", "rel-az-cd-dev-twin", "sub-dev-name-upper-twin",
                "sub-dev-id-upper-twin"}:
        accepted = ("SIGNED",)
    arrange(setup, env)
    if name == "sub-id-of-upper-name-lower":
        _upper_profile(env)
    if name == "sub-default-upper-name":
        azure(env["home"] / ".azure", raw='{"subscriptions": [{"id": "55555555-dddd", "name": "ACME-PROD", "isDefault": true}]}')
    with deadline(10, name):
        got, reason = verdict(command.replace("{TMP}", str(env["tmp"])), env[where])
    assert got in accepted, (got, reason[:160])


PROD = {"azure": "acme-prod", "gcloud": {"default": "acme-prod"}, "kube": "prod-a", "docker": "prod-eu", "pulumi": "prod"}
HELP = {
    "az vm delete --help": "BLOCKED", "az vm delete -h": "BLOCKED", "az vm --help": "FREE",
    "az aks get-credentials --help": "FREE", "az frobnicate --help": "FREE", "gcloud help compute instances delete": "SIGNED",
    "gcloud compute instances delete --help": "FREE", "gcloud container clusters get-credentials --help": "FREE",
    "kubectl delete --help": "FREE", "kubectl delete pod x --help": "SIGNED", "kubectl help delete": "FREE",
    "kubectl frobnicate x --help": "FREE", "kubectl version": "FREE", "az version": "FREE", "gcloud version": "FREE",
    "gcloud info": "FREE", "docker push --help": "FREE", "docker version": "FREE", "pulumi destroy --help": "FREE",
    "pulumi help destroy": "FREE", "pulumi up --help": "FREE", "helm install --help": "FREE", "helm help install": "FREE",
    "aws ec2 terminate-instances help": "BLOCKED", "terraform destroy -help": "SIGNED", "kubectx --help": "FREE",
    "kubens --help": "FREE",
}


@pytest.mark.parametrize("command", sorted(HELP))
def test_help_keeps_base_verdict_under_prod(command, env):
    clean = verdict(command, env["work"])[0]
    arrange(PROD, env)
    prod = verdict(command, env["work"])[0]
    assert (clean, prod) == (HELP[command], HELP[command]), (clean, prod)


def _fifo(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(p)


def _loop(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.parent / "loop-b").symlink_to(p)
    p.symlink_to(p.parent / "loop-b")


def _unreadable(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"subscriptions": []}', encoding="utf-8")
    p.chmod(0)


def _directory(p):
    p.mkdir(parents=True)


def _huge(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    pad = " " * (64 * 1024 * 1024)
    p.write_text('{"pad": "' + pad + '", "current-context": "prod-a", "currentContext": "prod-a", "subscriptions": '
                 '[{"id": "1", "name": "acme-prod", "isDefault": true}]}', encoding="utf-8")


def _over_cap(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"pad": "' + " " * (1100 * 1024) + '","current-context": "dev-a", "currentContext": "dev-eu", '
                 '"subscriptions": [{"id": "1", "name": "dev-sub", "isDefault": true}]}', encoding="utf-8")


def _dev_zero(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to("/dev/zero")


JUNK = {
    "null": lambda p: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text("null")),
    "nested-wrong-types": lambda p: (p.parent.mkdir(parents=True, exist_ok=True),
                                     p.write_text('{"subscriptions": [{"name": ["acme-prod"], "id": null, "isDefault": "yes"}],'
                                                  ' "currentContext": {"n": 1}, "current-context": [1]}')),
    "subscriptions-null": lambda p: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text('{"subscriptions": null}')),
    "unreadable": _unreadable, "directory": _directory, "symlink-loop": _loop, "huge": _huge, "fifo": _fifo,
    "over-cap": _over_cap, "dev-zero": _dev_zero,
}
SOURCES = {
    "azure": ("home/.azure/azureProfile.json", FETCH, "az vm list -g rg"),
    "kube": ("home/.kube/config", "kubectl frobnicate x", "kubectl get pods"),
    "docker": ("home/.docker/config.json", "docker frobnicate x", "docker ps"),
    "gcloud": ("home/.config/gcloud/configurations/config_default", GFETCH, "gcloud compute instances list"),
    "kubeconfig-env": ("kc", "KUBECONFIG={TMP}/kc kubectl frobnicate x", "KUBECONFIG={TMP}/kc kubectl get pods"),
    "docker-config-flag": ("dc/config.json", "docker --config {TMP}/dc frobnicate x", "docker --config {TMP}/dc ps"),
}


AMBIENT = {
    "az": ("home/.azure/azureProfile.json", "az frobnicate thing"),
    "gcloud": ("home/.config/gcloud/configurations/config_default", "gcloud frobnicate thing"),
    "kubeconfig": ("home/.kube/config", "kubectl frobnicate x"),
    "docker-config": ("home/.docker/config.json", "docker frobnicate x"),
}


@pytest.mark.parametrize("state", ["absent", "unreadable"])
@pytest.mark.parametrize("source", sorted(AMBIENT))
def test_absent_configuration_is_no_target(source, state, env):
    relative, command = AMBIENT[source]
    if state == "unreadable":
        _unreadable(env["tmp"] / relative)
    else:
        assert not (env["home"] / relative.split("/")[1]).exists()
    got, reason = verdict(command, env["work"])
    assert got in (FREE if state == "absent" else NOT_FREE), (got, reason[:160])


@contextlib.contextmanager
def memory_ceiling(extra_mb):
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    size = int(next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmSize")).split()[1])
    ceiling = size * 1024 + extra_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (ceiling if hard == resource.RLIM_INFINITY else min(ceiling, hard), hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _counted_reads(monkeypatch):
    sizes, real = [], os.read

    def read(descriptor, length):
        data = real(descriptor, length)
        sizes.append(len(data))
        return data

    monkeypatch.setattr(os, "read", read)
    return sizes


@pytest.mark.parametrize("junk", sorted(JUNK))
@pytest.mark.parametrize("source", sorted(SOURCES))
def test_junk_configuration(source, junk, env, monkeypatch):
    """Junk asks for a mutation and stays free for a read, with bounded bytes and CPU; a blocking open hits the deadline."""
    relative, mutating, read = (part.replace("{TMP}", str(env["tmp"])) for part in SOURCES[source])
    if source == "gcloud":
        gcloud(env["home"] / ".config" / "gcloud", {})
    JUNK[junk](env["tmp"] / relative)
    read_sizes = _counted_reads(monkeypatch)
    with deadline(60, f"{source}/{junk}"), memory_ceiling(1536):
        start = time.thread_time()
        got_mut, reason_mut = verdict(mutating, env["work"])
        got_read, reason_read = verdict(read, env["work"])
        elapsed = time.thread_time() - start
    assert got_mut != "BLOCKED" and got_read != "BLOCKED", (got_mut, reason_mut[:120], got_read, reason_read[:120])
    assert got_read == "FREE", (got_read, reason_read[:120])
    if (junk, source.split("-")[0]) not in (("subscriptions-null", "kube"), ("subscriptions-null", "kubeconfig"),
                                            ("subscriptions-null", "docker")):
        assert got_mut in NOT_FREE, (got_mut, reason_mut[:120])
    assert sum(read_sizes) <= _READ_LIMIT, sum(read_sizes)
    assert elapsed < 3.0, elapsed


def test_kubeconfig_pointing_at_dev_zero_is_bounded(env):
    _dev_zero(env["home"] / ".kube" / "config")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with deadline(10, "dev-zero"), memory_ceiling(1536):
        got, reason = verdict("kubectl frobnicate x", env["work"])
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert got in NOT_FREE, (got, reason[:120])
    assert grown_mb < 256, f"read grew rss by {grown_mb:.0f} MB"


def _mint(command, cwd):
    blocked = validate(command, cwd, session="s-grant-r5")
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    activation = activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver")
    assert activation.success, activation.reason
    return blocked.approval_id


def _gc(e, mp, n):
    gcloud(e["home"] / ".config" / "gcloud", {"default": "acme-dev", "prodcfg": "acme-prod"}, active="prodcfg" if n == "prod" else "default")


def _dk(e, mp, n):
    docker(e["home"] / ".docker", "prod-eu" if n == "prod" else "dev-eu")


def _pl(e, mp, n):
    pulumi(e, n)


def _azrel(e, mp, n):
    azure(e["tmp"] / "azd", "acme-prod" if n == "prod" else "dev-sub")


def _kenv(e, mp, n):
    kube(e["home"] / ".kube" / "config", "dev-a")
    if n == "prod":
        mp.setenv("KUBECONFIG", str(kube(e["tmp"] / "kc", "prod-a")))


def _dcflag(e, mp, n):
    docker(e["tmp"] / "dc", "prod-eu" if n == "prod" else "dev-eu")


GRANTS = [
    ("g-gcloud-active-config", "gcloud compute instances stop vm --zone z", _gc, "work"),
    ("g-docker-current-context", "docker push registry.example/app:1", _dk, "work"),
    ("g-pulumi-stack", "pulumi destroy --yes", _pl, "work"),
    ("g-az-relative-config-dir", "AZURE_CONFIG_DIR=azd az vm stop -g rg -n vm", _azrel, "tmp"),
    ("g-kubeconfig-env-added", "kubectl apply -f x.yaml", _kenv, "work"),
    ("g-docker-config-flag", "docker --config ../dc push registry.example/app:1", _dcflag, "work"),
]


@pytest.mark.parametrize("case", GRANTS, ids=[c[0] for c in GRANTS])
def test_r5_grant_does_not_follow(case, env, monkeypatch):
    _, command, switch, where = case
    switch(env, monkeypatch, "dev")
    used = _mint(command, env[where])
    again = validate(command, env[where], session="s-grant-r5")
    assert again.allowed, (again.reason or "")[:160]
    unused = _mint(command, env[where])
    switch(env, monkeypatch, "prod")
    after = validate(command, env[where], session="s-grant-r5")
    assert not after.allowed and after.approval_id and after.approval_id not in (used, unused), (after.reason or "")[:160]
