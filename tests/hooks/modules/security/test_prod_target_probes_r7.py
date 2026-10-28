"""Round-7 probes for task 1020: YAML target configs outside the closed subset ask, and empty targets match only when absent."""

from __future__ import annotations

import codecs
import pytest

from tests.hooks.modules.security.test_prod_target_probes_r6 import (
    ASK, _mint, _validate, env, pulumi_project, put, verdict,
)
from modules.security import targets  # noqa: E402

__all__ = ["env"]


def u16le(text, bom=True):
    return (codecs.BOM_UTF16_LE if bom else b"") + text.encode("utf-16-le")


KC = "apiVersion: v1\ncurrent-context: prod-a\n"
KUBE = {
    "k-esc-x-indented": '  apiVersion: v1\n  "current\\x2dcontext": prod-a\n',
    "k-esc-u-indented": '  apiVersion: v1\n  "current\\u002dcontext": prod-a\n',
    "k-esc-x-top": 'apiVersion: v1\n"current\\x2dcontext": prod-a\n',
    "k-continued-key-indented": '  apiVersion: v1\n  ? "current-\\\n    context"\n  : prod-a\n',
    "k-continued-key-top": 'apiVersion: v1\n? "current-\\\n  context"\n: prod-a\n',
    "k-flow-json-indented": '  {"apiVersion": "v1", "current\\u002dcontext": "prod-a"}\n',
    "k-json-escaped-key": '{"apiVersion": "v1", "current\\u002dcontext": "prod-a"}',
    "k-alias-key-indented": '  x: &k "current\\x2dcontext"\n  *k : prod-a\n',
    "k-split-plain-key": "apiVersion: v1\n? current-\n  context\n: prod-a\n",
    "k-comment-mention": "apiVersion: v1\n# current-context: prod-a\n",
    "k-value-mention": "apiVersion: v1\npreferences:\n  note: current-context prod-a\n",
    "k-cyrillic-key": "apiVersion: v1\nсurrent-context: prod-a\n",
    "k-fullwidth-key": "apiVersion: v1\nｃurrent-context: prod-a\n",
    "k-zwsp-key": "apiVersion: v1\ncurrent​-context: prod-a\n",
    "k-zwsp-indented": "  apiVersion: v1\n  current​-context: prod-a\n",
    "k-json-dup-case-null-last": '{"current-context": "prod-a", "Current-Context": null}',
    "k-utf16le-bom": u16le(KC),
    "k-utf16be-bom": codecs.BOM_UTF16_BE + KC.encode("utf-16-be"),
    "k-utf16le-nobom": u16le(KC, bom=False),
    "k-utf32le-bom": codecs.BOM_UTF32_LE + KC.encode("utf-32-le"),
}
KUBECTL_READS_PROD = {
    "k-alias-key-indented", "k-continued-key-indented", "k-continued-key-top", "k-esc-u-indented", "k-esc-x-indented",
    "k-esc-x-top", "k-flow-json-indented", "k-json-dup-case-null-last", "k-json-escaped-key", "k-utf16be-bom",
    "k-utf16le-bom",
}
JSON = {
    "d-dup-case-null-last": ("docker", '{"currentContext": "prod-a", "CURRENTCONTEXT": null}'),
    "d-escaped-key": ("docker", '{"current\\u0043ontext": "prod-a"}'),
    "d-lower-key": ("docker", '{"currentcontext": "prod-a"}'),
    "d-escaped-lower-mixed": ("docker", '{"\\u0063urrentcontext": "prod-a"}'),
    "d-zwsp-key": ("docker", '{"current​Context": "prod-a"}'),
    "d-cyrillic-key": ("docker", '{"сurrentContext": "prod-a"}'),
    "d-value-mention": ("docker", '{"auths": {}, "note": "currentContext prod-a"}'),
    "d-dev-then-null": ("docker", '{"currentContext": "dev-a", "CurrentContext": null}'),
    "d-utf8-bom": ("docker", '﻿{"currentContext": "prod-a"}'),
    "d-utf16le-bom": ("docker", u16le('{"currentContext": "prod-a"}')),
    "p-longs-kelvin": ("pulumi", '{"ſtacK": "prod-a"}'),
    "p-longs-kelvin-escaped": ("pulumi", '{"\\u017ftac\\u212a": "prod-a"}'),
    "p-escaped-key": ("pulumi", '{"st\\u0061ck": "prod-a"}'),
    "p-dup-null-last": ("pulumi", '{"stack": "prod-a", "STACK": null}'),
    "p-zwsp-key": ("pulumi", '{"st​ack": "prod-a"}'),
}
GO_READS_PROD = {
    "d-dup-case-null-last", "d-escaped-key", "d-escaped-lower-mixed", "d-lower-key",
    "p-dup-null-last", "p-escaped-key", "p-longs-kelvin", "p-longs-kelvin-escaped",
}


@pytest.mark.parametrize("case", sorted(KUBE))
def test_r7_kubeconfig_kubectl_reads_as_prod_asks(case, env):
    """Every kubeconfig spelling kubectl reads as prod-a asks; the rest may ask or keep the verdict."""
    put(env["home"] / ".kube" / "config", KUBE[case])
    got, reason = verdict("kubectl frobnicate x", env["work"])
    if case in KUBECTL_READS_PROD:
        assert got in ASK, (got, reason[:160])


@pytest.mark.parametrize("case", sorted(JSON))
def test_r7_go_json_reads_as_prod_asks(case, env):
    """Every docker or pulumi JSON spelling Go's encoding/json reads as prod-a asks."""
    kind, raw = JSON[case]
    if kind == "docker":
        put(env["home"] / ".docker" / "config.json", raw)
        command = "docker frobnicate x"
    else:
        pulumi_project(env, "Pulumi.yaml", "name: shop\nruntime: python\n", stack="placeholder")
        put(next((env["home"] / ".pulumi" / "workspaces").iterdir()), raw)
        command = "pulumi frobnicate x"
    got, reason = verdict(command, env["work"])
    if case in GO_READS_PROD:
        assert got in ASK, (got, reason[:160])


def _kube_escaped(e, name):
    put(e["home"] / ".kube" / "config", f'  apiVersion: v1\n  "current\\x2dcontext": {name}\n')


def _kube_unset(e, _name=None):
    put(e["home"] / ".kube" / "config", "apiVersion: v1\nkind: Config\npreferences: {}\n")


def _kube_anchor_elsewhere(e, name):
    put(e["home"] / ".kube" / "config", f"apiVersion: v1\ncurrent-context: {name}\nx: &a 1\n")


def _kube_canonical(e, name):
    put(e["home"] / ".kube" / "config", f"apiVersion: v1\ncurrent-context: {name}\n")


def _assert_denied_anew(command, cwd, minted):
    after = _validate(command, cwd)
    assert not after.allowed and after.approval_id and after.approval_id != minted, (after.reason or "")[:160]


def test_r7_grant_minted_without_config_follows_escaped_prod(env):
    """Minted while no kubeconfig exists, then a config kubectl reads as prod-a appears: the grant must not match."""
    command = "kubectl apply -f x.yaml"
    minted = _mint(command, env["work"])
    _kube_escaped(env, "prod-a")
    _assert_denied_anew(command, env["work"], minted)


def test_r7_grant_minted_on_escaped_dev_follows_escaped_prod(env):
    """Minted on an escaped-key dev config, looked up on the escaped-key prod twin: the grant must not match."""
    command = "kubectl apply -f x.yaml"
    _kube_escaped(env, "dev-a")
    minted = _mint(command, env["work"])
    _kube_escaped(env, "prod-a")
    _assert_denied_anew(command, env["work"], minted)


def _absent(_e, _name=None):
    return None


SCOPE_DENIED = [
    ("absent-then-unset", _absent, _kube_unset),
    ("absent-then-escaped-dev", _absent, _kube_escaped),
    ("absent-then-anchor-elsewhere", _absent, _kube_anchor_elsewhere),
    ("unset-then-absent", _kube_unset, None),
]


@pytest.mark.parametrize("state", [_kube_escaped, _kube_anchor_elsewhere], ids=["escaped-dev", "anchor-elsewhere"])
def test_r7_undeterminable_target_matches_only_the_same_content(state, env):
    command = "kubectl apply -f x.yaml"
    state(env, "dev-a")
    _mint(command, env["work"])
    state(env, "dev-a")
    assert _validate(command, env["work"]).allowed


@pytest.mark.parametrize("case", SCOPE_DENIED, ids=[c[0] for c in SCOPE_DENIED])
def test_r7_empty_target_matches_only_absent(case, env):
    """Absent never matches present or undeterminable."""
    _, at_mint, at_lookup = case
    command = "kubectl apply -f x.yaml"
    at_mint(env, "dev-a")
    minted = _mint(command, env["work"])
    if at_lookup is None:
        (env["home"] / ".kube" / "config").unlink()
    else:
        at_lookup(env, "dev-a")
    _assert_denied_anew(command, env["work"], minted)


@pytest.mark.parametrize("state", [_absent, _kube_unset, _kube_canonical], ids=["absent", "unset", "certified-dev"])
def test_r7_determinate_target_matches_itself(state, env):
    """Absent against absent, and a certified file against the same certified file, still match."""
    command = "kubectl apply -f x.yaml"
    state(env, "dev-a")
    _mint(command, env["work"])
    assert _validate(command, env["work"]).allowed


def _kube_names(e):
    return [t.name for t in targets.command_targets("kubectl frobnicate x", cwd=str(e["work"])) if t.kind == "kube-context"]


def test_r7_line_outside_subset_without_the_key_is_undeterminable(env):
    """A dev kubeconfig with one anchor on an unrelated line is undeterminable and asks."""
    _kube_anchor_elsewhere(env, "dev-a")
    got, reason = verdict("kubectl frobnicate x", env["work"])
    assert got in ASK and _kube_names(env) == [targets.UNREADABLE], (got, reason[:160])


KUBECTL_WRITTEN = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ZmFrZS1jbHVzdGVyLWNhLWZvci10ZXN0cy0wMTIzNDU2Nzg5YWJjZGVmZ2hpams=
    server: https://34.118.0.10
  name: gke_shop-{env}_us-central1_main
- cluster:
    certificate-authority-data: ZmFrZS1la3MtY2EtZm9yLXRlc3Rz
    server: https://ABCDEF0123456789.gr7.us-east-1.eks.amazonaws.com
  name: arn:aws:eks:us-east-1:123456789012:cluster/shop-{env}
- cluster:
    certificate-authority-data: ZmFrZS1raW5kLWNhLWZvci10ZXN0cw==
    server: https://127.0.0.1:40123
  name: kind-local
contexts:
- context:
    cluster: gke_shop-{env}_us-central1_main
    namespace: default
    user: gke_shop-{env}_us-central1_main
  name: gke_shop-{env}_us-central1_main
- context:
    cluster: arn:aws:eks:us-east-1:123456789012:cluster/shop-{env}
    user: arn:aws:eks:us-east-1:123456789012:cluster/shop-{env}
  name: arn:aws:eks:us-east-1:123456789012:cluster/shop-{env}
- context:
    cluster: kind-local
    user: kind-local
  name: kind-local
current-context: gke_shop-{env}_us-central1_main
kind: Config
preferences: {{}}
users:
- name: gke_shop-{env}_us-central1_main
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args: null
      command: gke-gcloud-auth-plugin
      env: null
      installHint: Install gke-gcloud-auth-plugin for use with kubectl by following
        https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin
      interactiveMode: IfAvailable
      provideClusterInfo: true
- name: arn:aws:eks:us-east-1:123456789012:cluster/shop-{env}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - --region
      - us-east-1
      - eks
      - get-token
      - --cluster-name
      - shop-{env}
      command: aws
      env:
      - name: AWS_PROFILE
        value: "shop-{env}"
      interactiveMode: IfAvailable
      provideClusterInfo: false
- name: kind-local
  user:
    client-certificate-data: ZmFrZS1jbGllbnQtY2VydC1mb3ItdGVzdHM=
    client-key-data: ZmFrZS1jbGllbnQta2V5LWZvci10ZXN0cw==
"""


KUBECTL_READS_WRITTEN = {"dev": "gke_shop-dev_us-central1_main", "prod": "gke_shop-prod_us-central1_main"}


@pytest.mark.parametrize("twin, expected", [("dev", ("FREE",)), ("prod", ASK)])
def test_r7_kubectl_written_kubeconfig_is_certified(twin, expected, env):
    """A kubectl-written kubeconfig is read: the dev twin keeps its verdict, the prod twin asks naming the context."""
    put(env["home"] / ".kube" / "config", KUBECTL_WRITTEN.format(env=twin))
    got, reason = verdict("kubectl frobnicate x", env["work"])
    assert got in expected, (got, reason[:160])
    assert _kube_names(env) == [KUBECTL_READS_WRITTEN[twin]]


def test_r7_kubectl_written_kubeconfig_grant_follows_the_twin(env):
    """A grant minted on the dev twin matches the dev twin once and never the prod twin."""
    command = "kubectl apply -f x.yaml"
    path = env["home"] / ".kube" / "config"
    put(path, KUBECTL_WRITTEN.format(env="dev"))
    _mint(command, env["work"])
    assert _validate(command, env["work"]).allowed
    minted = _mint(command, env["work"])
    put(path, KUBECTL_WRITTEN.format(env="prod"))
    _assert_denied_anew(command, env["work"], minted)


def test_r7_pulumi_project_with_namespaced_config_keys_is_certified(env):
    """A pulumi-new Pulumi.yaml, whose nested config keys carry a namespace colon, still reads its stack."""
    project = "name: shop\nruntime: python\ndescription: A shop\nconfig:\n  pulumi:tags:\n    value:\n" \
              "      pulumi:template: python\n"
    pulumi_project(env, "Pulumi.yaml", project, stack="dev-a")
    assert verdict("pulumi frobnicate x", env["work"])[0] == "FREE"
    pulumi_project(env, "Pulumi.yaml", project, stack="prod-a")
    assert verdict("pulumi frobnicate x", env["work"])[0] in ASK
