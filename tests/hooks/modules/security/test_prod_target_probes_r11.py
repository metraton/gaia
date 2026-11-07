"""Verifier round-11 probes (task 1020): kubeconfig spellings and real-world shapes, with kubectl's readings as data."""

from __future__ import annotations

import pytest

from tests.hooks.modules.security.test_prod_target_probes_r6 import ASK, _mint, _validate, env, put, verdict
from modules.security import targets  # noqa: E402

__all__ = ["env"]
ERR = "ERR"

SMUGGLE = {
    "case-variant-after": ("apiVersion: v1\ncurrent-context: dev-a\nCurrent-Context: prod-a\n", "dev-a"),
    "case-variant-before": ("apiVersion: v1\nCurrent-Context: prod-a\ncurrent-context: dev-a\n", "dev-a"),
    "upper-after": ("apiVersion: v1\ncurrent-context: dev-a\nCURRENT-CONTEXT: prod-a\n", "dev-a"),
    "case-variant-only": ("apiVersion: v1\nCurrent-Context: prod-a\n", ERR),
    "json-case-variant-after": ('{"current-context": "dev-a", "Current-Context": "prod-a"}', "dev-a"),
    "json-case-variant-before": ('{"Current-Context": "prod-a", "current-context": "dev-a"}', "dev-a"),
    "camel-after": ("apiVersion: v1\ncurrent-context: dev-a\ncurrentContext: prod-a\n", "dev-a"),
    "wrap-then-nested-key": (
        "apiVersion: v1\nkind: Config\npreferences:\n  colors: a\n    current-context: prod-a\ncurrent-context: dev-a\n", ERR),
    "wrap-root-benign": ("apiVersion: v1\nkind: Con\n fig\ncurrent-context: dev-a\n", ERR),
    "wrap-item-shallow-key": ("apiVersion: v1\ncontexts:\n- name: dev-a\n current-context: prod-a\ncurrent-context: dev-a\n", ERR),
    "wrap-item-scalar-then-key": ("apiVersion: v1\ncontexts:\n- dev\n  current-context: prod-a\ncurrent-context: dev-a\n", ERR),
    "wrap-deep-plain-mentions-key": ("apiVersion: v1\nkind: Config\n  current-context prod-a\ncurrent-context: dev-a\n", ERR),
    "wrap-blank-then-deeper": ("apiVersion: v1\nkind: Config\n\n  prod-a\ncurrent-context: dev-a\n", ERR),
    "space-before-colon": ("apiVersion: v1\ncurrent-context : prod-a\n", "prod-a"),
    "space-before-colon-after-dev": ("apiVersion: v1\ncurrent-context: dev-a\ncurrent-context : prod-a\n", "prod-a"),
    "tab-after-colon": ("apiVersion: v1\ncurrent-context:\tprod-a\n", "prod-a"),
    "tab-indent-nested": ("apiVersion: v1\npreferences:\n\tcurrent-context: prod-a\ncurrent-context: dev-a\n", ERR),
    "spaces-then-tab": ("apiVersion: v1\npreferences:\n  \tx: y\ncurrent-context: dev-a\n", ERR),
    "tab-indent-root-key": ("apiVersion: v1\n\tcurrent-context: prod-a\n", ERR),
    "doc-sep-second": ("current-context: dev-a\n---\ncurrent-context: prod-a\n", "dev-a"),
    "doc-sep-first": ("---\ncurrent-context: prod-a\n", "prod-a"),
    "doc-end-then-doc": ("current-context: dev-a\n...\ncurrent-context: prod-a\n", "dev-a"),
    "doc-end-after": ("current-context: prod-a\n...\n", "prod-a"),
    "comment-in-value": ("apiVersion: v1\ncurrent-context: dev-a #prod-a\n", "dev-a"),
    "tab-comment-in-value": ("apiVersion: v1\ncurrent-context: dev-a\t#prod-a\n", "dev-a"),
    "hash-glued": ("apiVersion: v1\ncurrent-context: dev-a#prod\n", "dev-a#prod"),
    "quoted-colon": ('apiVersion: v1\ncurrent-context: "prod-a: x"\n', "prod-a: x"),
    "quoted-hash": ("apiVersion: v1\ncurrent-context: 'prod-a # x'\n", "prod-a # x"),
    "quoted-then-comment": ('apiVersion: v1\ncurrent-context: "dev-a" # prod-a\n', "dev-a"),
    "single-quote-escape": ("apiVersion: v1\ncurrent-context: 'prod''a'\n", "prod'a"),
    "nested-colon-key-lookalike": ("apiVersion: v1\npreferences:\n  current-context:x: prod-a\ncurrent-context: dev-a\n", "dev-a"),
    "nested-colon-key-root": ("apiVersion: v1\ncurrent-context:x: prod-a\ncurrent-context: dev-a\n", "dev-a"),
    "nested-colon-key-then-deeper": (
        "apiVersion: v1\npreferences:\n  a:b: c\n   current-context: prod-a\ncurrent-context: dev-a\n", ERR),
    "nested-double-colon": ("apiVersion: v1\npreferences:\n  current-context:: prod-a\ncurrent-context: dev-a\n", "dev-a"),
    "dup-same": ("apiVersion: v1\ncurrent-context: dev-a\ncurrent-context: dev-a\n", "dev-a"),
    "dup-dev-then-prod": ("apiVersion: v1\ncurrent-context: dev-a\ncurrent-context: prod-a\n", "prod-a"),
    "dup-prod-then-null": ("apiVersion: v1\ncurrent-context: prod-a\ncurrent-context:\n", ERR),
    "dup-prod-then-empty-quoted": ("apiVersion: v1\ncurrent-context: prod-a\ncurrent-context: ''\n", ERR),
    "tilde-null": ("apiVersion: v1\ncurrent-context: ~\n", ERR),
    "merge-key": ("base: &b\n  current-context: prod-a\n<<: *b\n", "prod-a"),
    "key-after-item-col0": ("contexts:\n- name: x\ncurrent-context: dev-a\n- current-context: prod-a\n", ERR),
    "nbsp-indent": ("preferences:\n current-context: prod-a\ncurrent-context: dev-a\n", "dev-a"),
    "crlf": ("apiVersion: v1\r\ncurrent-context: prod-a\r\n", "prod-a"),
    "empty-then-nested": ("apiVersion: v1\ncurrent-context:\n  prod-a\n", "prod-a"),
    "empty-then-item": ("apiVersion: v1\ncurrent-context:\n- prod-a\n", ERR),
    "explicit-value-indicator": ("apiVersion: v1\ncurrent-context:\n  : prod-a\n", ERR),
    "prod-key-after-nested-comment": ("apiVersion: v1\npreferences:\n  # c\n  x: y\ncurrent-context: prod-a\n", "prod-a"),
}

GKE_LEGACY = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUM=
    server: https://35.200.1.2
  name: gke_shop-{e}_europe-west1-b_main
contexts:
- context:
    cluster: gke_shop-{e}_europe-west1-b_main
    user: gke_shop-{e}_europe-west1-b_main
  name: gke_shop-{e}_europe-west1-b_main
current-context: gke_shop-{e}_europe-west1-b_main
kind: Config
preferences: {{}}
users:
- name: gke_shop-{e}_europe-west1-b_main
  user:
    auth-provider:
      config:
        access-token: ya29.a0AfB_byC-fake-token
        cmd-args: config config-helper --format=json
        cmd-path: /usr/lib/google-cloud-sdk/bin/gcloud
        expiry: "2026-10-27T12:00:00Z"
        expiry-key: '{{.credential.token_expiry}}'
        token-key: '{{.credential.access_token}}'
      name: gcp
"""
EKS = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    server: https://0123456789ABCDEF.gr7.eu-west-1.eks.amazonaws.com
  name: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
contexts:
- context:
    cluster: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
    user: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
  name: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
current-context: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
kind: Config
preferences: {{}}
users:
- name: arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - --region
      - eu-west-1
      - eks
      - get-token
      - --cluster-name
      - shop-{e}
      - --output
      - json
      command: aws
      env:
      - name: AWS_PROFILE
        value: shop-{e}
"""
AKS_CERT = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    server: https://shop-{e}-dns-1a2b3c4d.hcp.westeurope.azmk8s.io:443
  name: shop-{e}
contexts:
- context:
    cluster: shop-{e}
    user: clusterUser_rg-shop-{e}_shop-{e}
  name: shop-{e}
current-context: shop-{e}
kind: Config
preferences: {{}}
users:
- name: clusterUser_rg-shop-{e}_shop-{e}
  user:
    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQ==
    token: 0123456789abcdef0123456789abcdef
"""
AKS_AAD = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    server: https://shop-{e}-dns-1a2b3c4d.hcp.westeurope.azmk8s.io:443
  name: shop-{e}
contexts:
- context:
    cluster: shop-{e}
    user: clusterUser_rg-shop-{e}_shop-{e}
  name: shop-{e}
current-context: shop-{e}
kind: Config
preferences: {{}}
users:
- name: clusterUser_rg-shop-{e}_shop-{e}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - get-token
      - --environment
      - AzurePublicCloud
      - --server-id
      - 6dae42f8-4368-4678-94ff-3960e28e3630
      - --login
      - devicecode
      command: kubelogin
      env: null
      installHint: |2

        kubelogin is not installed which is required to connect to AAD enabled cluster.

        To learn more, please go to https://aka.ms/aks/kubelogin
      provideClusterInfo: false
"""
MINIKUBE = """apiVersion: v1
clusters:
- cluster:
    certificate-authority: /home/u/.minikube/ca.crt
    extensions:
    - extension:
        last-update: Mon, 27 Oct 2026 10:00:00 -03
        provider: minikube.sigs.k8s.io
        version: v1.34.0
      name: cluster_info
    server: https://192.168.49.2:8443
  name: minikube-{e}
contexts:
- context:
    cluster: minikube-{e}
    extensions:
    - extension:
        last-update: Mon, 27 Oct 2026 10:00:00 -03
        provider: minikube.sigs.k8s.io
        version: v1.34.0
      name: context_info
    namespace: default
    user: minikube-{e}
  name: minikube-{e}
current-context: minikube-{e}
kind: Config
preferences: {{}}
users:
- name: minikube-{e}
  user:
    client-certificate: /home/u/.minikube/profiles/minikube/client.crt
    client-key: /home/u/.minikube/profiles/minikube/client.key
"""
KIND = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    server: https://127.0.0.1:36443
  name: kind-shop-{e}
contexts:
- context:
    cluster: kind-shop-{e}
    user: kind-shop-{e}
  name: kind-shop-{e}
current-context: kind-shop-{e}
kind: Config
preferences: {{}}
users:
- name: kind-shop-{e}
  user:
    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t
    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQ==
"""
OPENSHIFT = """apiVersion: v1
clusters:
- cluster:
    server: https://api.crc-{e}.testing:6443
  name: api-crc-{e}-testing:6443
contexts:
- context:
    cluster: api-crc-{e}-testing:6443
    namespace: default
    user: kubeadmin/api-crc-{e}-testing:6443
  name: default/api-crc-{e}-testing:6443/kubeadmin
current-context: default/api-crc-{e}-testing:6443/kubeadmin
kind: Config
preferences: {{}}
users:
- name: kubeadmin/api-crc-{e}-testing:6443
  user:
    token: sha256~fakeTokenValue0123456789
"""
PYYAML_WRAPPED = """apiVersion: v1
clusters:
- cluster:
    server: https://k.example.com
  name: shop-{e}
contexts:
- context:
    cluster: shop-{e}
    user: shop-{e}
  name: shop-{e}
current-context: shop-{e}
kind: Config
preferences: {{}}
users:
- name: shop-{e}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: some-plugin
      installHint: this is a long plain hint that a python yaml dumper folds at eighty
        columns onto a deeper line when the value carries spaces
"""
REAL = {
    "gke-legacy": (GKE_LEGACY, "gke_shop-{e}_europe-west1-b_main"),
    "eks": (EKS, "arn:aws:eks:eu-west-1:111122223333:cluster/shop-{e}"),
    "aks-cert": (AKS_CERT, "shop-{e}"),
    "aks-aad": (AKS_AAD, "shop-{e}"),
    "minikube": (MINIKUBE, "minikube-{e}"),
    "kind": (KIND, "kind-shop-{e}"),
    "openshift": (OPENSHIFT, "default/api-crc-{e}-testing:6443/kubeadmin"),
    "pyyaml-wrapped": (PYYAML_WRAPPED, "shop-{e}"),
}
UNDETERMINABLE_SHAPES = {"aks-aad"}


def _names(e):
    return [t.name for t in targets.command_targets("kubectl frobnicate x", cwd=str(e["work"])) if t.kind == "kube-context"]


@pytest.mark.parametrize("case", sorted(SMUGGLE))
def test_r11_smuggle(case, env):
    text, truth = SMUGGLE[case]
    put(env["home"] / ".kube" / "config", text)
    got, reason = verdict("kubectl frobnicate x", env["work"])
    names = _names(env)
    if "prod" in truth:
        assert got in ASK, (truth, got, names, reason[:120])
    if names and names[0] not in (targets.UNREADABLE, targets.UNSET, "") and truth != ERR:
        assert names[0] == truth, ("certified a value kubectl does not read", truth, names)


@pytest.mark.parametrize("twin", ["dev", "prod"])
@pytest.mark.parametrize("shape", sorted(REAL))
def test_r11_real_world(shape, twin, env):
    text, truth = REAL[shape]
    put(env["home"] / ".kube" / "config", text.format(e=twin))
    got, reason = verdict("kubectl frobnicate x", env["work"])
    names = _names(env)
    if shape in UNDETERMINABLE_SHAPES:
        assert names == [targets.UNREADABLE] and got in ASK, (names, got, reason[:120])
        return
    assert names == [truth.format(e=twin)], (truth, names)
    assert got == "FREE" if twin == "dev" else got in ASK, (got, reason[:120])


def _absent(e):
    path = e["home"] / ".kube" / "config"
    if path.exists():
        path.unlink()


def _unset(e):
    put(e["home"] / ".kube" / "config", "apiVersion: v1\nkind: Config\npreferences: {}\n")


def _undet(e):
    put(e["home"] / ".kube" / "config", "apiVersion: v1\ncurrent-context: dev-a\nx: &a 1\n")


def _dev(e):
    put(e["home"] / ".kube" / "config", "apiVersion: v1\ncurrent-context: dev-a\n")


def _dev_other(e):
    put(e["home"] / ".kube" / "config", "apiVersion: v1\ncurrent-context: dev-b\n")


STATES = {"absent": _absent, "unset": _unset, "undet": _undet, "dev": _dev, "dev-b": _dev_other}


@pytest.mark.parametrize("lookup", sorted(STATES))
@pytest.mark.parametrize("mint", sorted(STATES))
def test_r11_scope_matrix(mint, lookup, env):
    command = "kubectl apply -f x.yaml"
    STATES[mint](env)
    minted = _mint(command, env["work"])
    STATES[lookup](env)
    after = _validate(command, env["work"])
    if mint == lookup:
        assert after.allowed, (after.reason or "")[:120]
    else:
        assert not after.allowed and after.approval_id and after.approval_id != minted, (after.reason or "")[:120]


def test_r11_multi_file_first_empty_then_prod(env, monkeypatch):
    first = put(env["home"] / "a.yaml", "apiVersion: v1\ncurrent-context: \"\"\n")
    second = put(env["home"] / "b.yaml", "apiVersion: v1\ncurrent-context: prod-a\n")
    monkeypatch.setenv("KUBECONFIG", f"{first}:{second}")
    got, reason = verdict("kubectl frobnicate x", env["work"])
    assert got in ASK, reason[:120]
