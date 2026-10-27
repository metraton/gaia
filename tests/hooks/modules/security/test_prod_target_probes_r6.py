"""Verifier round-6 probes (task 1020): each EXPECT is what the tool the command runs reads from the same file."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
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
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "aprobet1020x06"
CLEARED = ("GAIA_CLI_ALIASES", "KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
           "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE",
           "CONTAINER_HOST", "CONTAINER_CONNECTION", "PULUMI_HOME", "GIT_DIR", "GIT_WORK_TREE",
           "AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID")
ASK = ("SIGNED", "UNKNOWN")
KUBECTL_READS = json.loads((Path(__file__).parent / "data" / "kubeconfig_r6_kubectl.json").read_text(encoding="utf-8"))


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


def verdict(command, cwd):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": "s-probe6", "tool_use_id": "t-probe6", "agent_id": AGENT,
               "agent_type": "developer"}
    result = BashValidator(agent_names_scratch=True).validate(command, is_subagent=True, session_id="s-probe6",
                                                              agent_type="developer", hook_payload=payload)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith("Compound T3 execution is disabled"):
        return ("UNKNOWN" if "could not be determined" in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


def put(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


KUBE = "home/.kube/config"
DOCKER = "home/.docker/config.json"

CASES = {
    "k-lone-cr": (KUBE, "apiVersion: v1\rkind: Config\rcurrent-context: prod-a\r", "kubectl frobnicate x", "ask", None),
    "k-nel": (KUBE, "apiVersion: v1\x85current-context: prod-a\n", "kubectl frobnicate x", "ask", None),
    "k-line-separator": (KUBE, "apiVersion: v1 current-context: prod-a\n", "kubectl frobnicate x", "ask", None),
    "k-indented-root": (KUBE, "  apiVersion: v1\n  kind: Config\n  current-context: prod-a\n", "kubectl frobnicate x",
                        "ask", None),
    "k-crlf": (KUBE, "apiVersion: v1\r\ncurrent-context: prod-a\r\n", "kubectl frobnicate x", "ask", None),
    "k-bom": (KUBE, "﻿current-context: prod-a\n", "kubectl frobnicate x", "ask", None),
    "k-tab-separator": (KUBE, "current-context:\tprod-a\n", "kubectl frobnicate x", "ask", None),
    "k-comment-after": (KUBE, "current-context: prod-a   # the live one\n", "kubectl frobnicate x", "ask", None),
    "k-comment-glued": (KUBE, "current-context: prod-a#x\n", "kubectl frobnicate x", "ask", None),
    "k-canon-dev-then-spaced-prod": (KUBE, "current-context: dev-a\ncurrent-context : prod-a\n", "kubectl frobnicate x",
                                     "ask", None),
    "k-quoted-prod-then-canon-dev": (KUBE, "\"current-context\": prod-a\ncurrent-context: dev-a\n",
                                     "kubectl frobnicate x", "ask", None),
    "k-canon-dev-then-single-quoted-prod": (KUBE, "current-context: dev-a\n'current-context': prod-a\n",
                                            "kubectl frobnicate x", "ask", None),
    "k-two-docs-prod-second": (KUBE, "kind: Config\n---\ncurrent-context: prod-a\n", "kubectl frobnicate x", "ask", "free"),
    "k-two-docs-dev-then-prod": (KUBE, "current-context: dev-a\n---\ncurrent-context: prod-a\n", "kubectl frobnicate x",
                                 "free", "ask"),
    "k-doc-end-marker": (KUBE, "current-context: prod-a\n...\n", "kubectl frobnicate x", "ask", None),
    "k-empty-value": (KUBE, "current-context:\n", "kubectl frobnicate x", "free", None),
    "k-empty-quoted": (KUBE, "current-context: \"\"\n", "kubectl frobnicate x", "free", None),
    "k-dev-generated": (KUBE, "apiVersion: v1\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n  name: kind-dev\n"
                              "contexts:\n- context:\n    cluster: kind-dev\n    user: kind-dev\n  name: kind-dev\n"
                              "current-context: kind-dev\nkind: Config\npreferences: {}\nusers:\n- name: kind-dev\n"
                              "  user:\n    token: x\n", "kubectl frobnicate x", "free", None),
    "k-dev-eks-arn": (KUBE, "current-context: arn:aws:eks:us-east-1:1:cluster/dev-a\n", "kubectl frobnicate x", "free", None),
    "k-dev-quoted-space": (KUBE, "current-context: \"my dev\"\n", "kubectl frobnicate x", "free", None),
    "k-dev-crlf": (KUBE, "apiVersion: v1\r\ncurrent-context: dev-a\r\n", "kubectl frobnicate x", "free", None),
    "k-prod-read-stays-free": (KUBE, "current-context: prod-a\n", "kubectl get pods", "free", None),
    "d-key-capitalised": (DOCKER, '{"CurrentContext": "prod-a"}', "docker frobnicate x", "ask", None),
    "d-key-dev-then-upper-prod": (DOCKER, '{"currentContext": "dev-a", "CURRENTCONTEXT": "prod-a"}',
                                  "docker frobnicate x", "ask", None),
    "d-dev-twin": (DOCKER, '{"currentContext": "dev-a"}', "docker frobnicate x", "free", None),
    "d-later-null-keeps-prod": (DOCKER, '{"currentContext": "prod-a", "CurrentContext": null}', "docker frobnicate x",
                                "ask", None),
    "d-upper-dev-after-prod": (DOCKER, '{"currentContext": "prod-a", "CURRENTCONTEXT": "dev-a"}', "docker frobnicate x",
                               "free", None),
    "k-present-without-key": (KUBE, "apiVersion: v1\nkind: Config\nclusters: []\n", "kubectl frobnicate x", "free", None),
    "k-dev-lone-cr-is-undeterminable": (KUBE, "kind: Config\rcurrent-context: dev-a\r", "kubectl frobnicate x", "ask",
                                        None),
    "k-json-key-other-case": (KUBE, '{"Current-Context": "prod-a"}', "kubectl frobnicate x", "ask", None),
    "d-present-without-key": (DOCKER, '{"auths": {}}', "docker frobnicate x", "free", None),
    "d-config-eq": ("dc/config.json", '{"currentContext": "prod-a"}', "docker --config={TMP}/dc frobnicate x", "ask", None),
    "d-config-after-tls": ("dc/config.json", '{"currentContext": "prod-a"}',
                           "docker --tlscacert ca.pem -l debug --config {TMP}/dc frobnicate x", "ask", None),
    "d-config-after-subcommand": ("dc/config.json", '{"currentContext": "prod-a"}', "docker ps --config {TMP}/dc",
                                  "free", None),
    "d-env-dev-flag-prod": ("dc/config.json", '{"currentContext": "prod-a"}',
                            "DOCKER_CONFIG={TMP}/devdc docker --config {TMP}/dc frobnicate x", "ask", None),
    "d-env-prod-flag-dev": ("devdc/config.json", '{"currentContext": "dev-a"}',
                            "DOCKER_CONFIG={TMP}/dc docker --config {TMP}/devdc frobnicate x", "free", None),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_r6_probe(case, env):
    relative, raw, command, expected, also = CASES[case]
    tmp = env["tmp"]
    put(tmp / relative, raw)
    if case == "d-env-dev-flag-prod":
        put(tmp / "devdc" / "config.json", '{"currentContext": "dev-a"}')
    if case == "d-env-prod-flag-dev":
        put(tmp / "dc" / "config.json", '{"currentContext": "prod-a"}')
    command = command.replace("{TMP}", str(tmp))
    with deadline(10, case):
        got, reason = verdict(command, env["work"])
    accepted = {"ask": ASK, "free": ("FREE",)}
    allowed = accepted[expected] + (accepted[also] if also else ())
    assert got in allowed, (got, reason[:160])


@pytest.mark.parametrize("case", sorted(KUBECTL_READS))
def test_r6_kubectl_fixture_is_the_probed_file(case, env):
    """The kubeconfig kubectl was asked about is byte-for-byte the one probed, and asks whenever kubectl reads prod."""
    observed = KUBECTL_READS[case]
    assert observed["content"] == CASES[case][1]
    put(env["home"] / ".kube" / "config", observed["content"])
    got, reason = verdict("kubectl frobnicate x", env["work"])
    assert got in (ASK if "prod" in observed["kubectl_current_context"] else ASK + ("FREE",)), (got, reason[:160])


def pulumi_project(e, filename, project_text, stack_key="stack", stack="prod-a", name="shop"):
    project = put(e["work"] / filename, project_text)
    digest = hashlib.sha1(str(project).encode()).hexdigest()
    put(e["home"] / ".pulumi" / "workspaces" / f"{name}-{digest}-workspace.json", json.dumps({stack_key: stack}))


PULUMI = {
    "p-name-double-quoted": ("Pulumi.yaml", 'name: "shop"\nruntime: python\n', "stack", "prod-a", "ask"),
    "p-name-single-quoted": ("Pulumi.yaml", "name: 'shop'\nruntime: python\n", "stack", "prod-a", "ask"),
    "p-name-comment": ("Pulumi.yaml", "name: shop # c\nruntime: python\n", "stack", "prod-a", "ask"),
    "p-name-crlf": ("Pulumi.yaml", "name: shop\r\nruntime: python\r\n", "stack", "prod-a", "ask"),
    "p-name-tab": ("Pulumi.yaml", "name:\tshop\nruntime: python\n", "stack", "prod-a", "ask"),
    "p-name-dup-noncanonical": ("Pulumi.yaml", "name: shop\nname : other\n", "stack", "prod-a", "ask"),
    "p-name-lone-cr": ("Pulumi.yaml", "name: shop\rruntime: python\r", "stack", "prod-a", "ask"),
    "p-nested-name-ignored": ("Pulumi.yaml", "name: shop\nruntime:\n  name: python\n", "stack", "prod-a", "ask"),
    "p-dev-quoted-name": ("Pulumi.yaml", 'name: "shop"\nruntime: python\n', "stack", "dev-a", "free"),
    "p-dev-description-folded": ("Pulumi.yaml", "name: shop\ndescription: >\n  long text\nruntime: python\n", "stack",
                                 "dev-a", "free"),
    "p-project-file-yml": ("Pulumi.yml", "name: shop\nruntime: python\n", "stack", "prod-a", "ask"),
    "p-stack-key-capitalised": ("Pulumi.yaml", "name: shop\nruntime: python\n", "Stack", "prod-a", "ask"),
}


@pytest.mark.parametrize("case", sorted(PULUMI))
def test_r6_pulumi(case, env):
    filename, text, key, stack, expected = PULUMI[case]
    pulumi_project(env, filename, text, key, stack)
    with deadline(10, case):
        got, reason = verdict("pulumi frobnicate x", env["work"])
    assert got in ({"ask": ASK, "free": ("FREE",)}[expected]), (got, reason[:160])


@pytest.mark.parametrize("yml_stack, expected", [("dev-a", ("FREE",)), ("prod-a", ASK), ("dev-b", ASK)])
def test_r6_pulumi_yaml_and_yml_together(yml_stack, expected, env):
    pulumi_project(env, "Pulumi.yaml", "name: shop\nruntime: python\n", stack="dev-a")
    pulumi_project(env, "Pulumi.yml", "name: shop\nruntime: python\n", stack=yml_stack)
    got, reason = verdict("pulumi frobnicate x", env["work"])
    assert got in expected, (got, reason[:160])


def _fifo_by_proc(e):
    fifo = e["tmp"] / "pipe"
    os.mkfifo(fifo)
    descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    return f"/proc/self/fd/{descriptor}", descriptor


def _anon_pipe(e):
    read_end, write_end = os.pipe()
    return f"/proc/self/fd/{read_end}", (read_end, write_end)


PSEUDO = ["/proc/self/pagemap", "/proc/self/mem", "/proc/self/environ", "/proc/kmsg", "/proc/kcore",
          "/sys/devices/system/cpu/online", "/proc/self/task", "/dev/stdin", "fifo-by-proc", "anon-pipe-by-proc"]


@pytest.mark.parametrize("target", PSEUDO)
def test_r6_pseudo_file_config_is_bounded(target, env):
    held = None
    if target == "fifo-by-proc":
        target, held = _fifo_by_proc(env)
    elif target == "anon-pipe-by-proc":
        target, held = _anon_pipe(env)
    config = env["home"] / ".kube" / "config"
    config.parent.mkdir(parents=True)
    config.symlink_to(target)
    try:
        with deadline(10, target):
            start = time.monotonic()
            got_mut, reason = verdict("kubectl frobnicate x", env["work"])
            got_read, _ = verdict("kubectl get pods", env["work"])
            elapsed = time.monotonic() - start
    finally:
        for fd in (held if isinstance(held, tuple) else (held,) if held is not None else ()):
            os.close(fd)
    assert got_read == "FREE" and got_mut in ASK, (got_mut, got_read, reason[:120])
    assert elapsed < 3.0, elapsed


def test_r6_hardlinked_prod_config(env):
    source = put(env["tmp"] / "elsewhere" / "kc", "current-context: prod-a\n")
    (env["home"] / ".kube").mkdir()
    os.link(source, env["home"] / ".kube" / "config")
    got, reason = verdict("kubectl frobnicate x", env["work"])
    assert got in ASK, (got, reason[:120])


def test_r6_absent_configs_keep_todays_verdict(env):
    got = [verdict(c, env["work"])[0] for c in ("kubectl frobnicate x", "docker frobnicate x", "pulumi frobnicate x")]
    assert got == ["FREE", "FREE", "FREE"], got


def _validate(command, cwd, session="s-scope-r6"):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "session_id": session, "tool_use_id": "t-probe6", "agent_id": AGENT, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(command, is_subagent=True, session_id=session,
                                                          agent_type="developer", hook_payload=payload)


def _mint(command, cwd):
    from modules.security.approval_grants import activate_db_pending_by_id

    blocked = _validate(command, cwd)
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    assert activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver").success
    return blocked.approval_id


def _kube_lone_cr(e, name):
    put(e["home"] / ".kube" / "config", f"apiVersion: v1\rkind: Config\rcurrent-context: {name}\r")


def _kube_indented(e, name):
    put(e["home"] / ".kube" / "config", f"  apiVersion: v1\n  current-context: {name}\n")


def _docker_upper(e, name):
    put(e["home"] / ".docker" / "config.json", json.dumps({"CurrentContext": name}))


def _pulumi_yml(e, name):
    pulumi_project(e, "Pulumi.yml", "name: shop\nruntime: python\n", "stack", name)


def _kube_canonical(e, name):
    put(e["home"] / ".kube" / "config", f"apiVersion: v1\ncurrent-context: {name}\n")


CERTIFIED = [
    ("s-docker-key-capitalised", "docker push registry.example/app:1", _docker_upper),
    ("s-pulumi-yml", "pulumi destroy --yes", _pulumi_yml),
    ("s-kube-canonical-control", "kubectl apply -f x.yaml", _kube_canonical),
]
UNDETERMINABLE = [
    ("s-kube-lone-cr", "kubectl apply -f x.yaml", _kube_lone_cr),
    ("s-kube-indented-root", "kubectl apply -f x.yaml", _kube_indented),
]
SWITCHES = CERTIFIED + UNDETERMINABLE


@pytest.mark.parametrize("rewrite_same", [False, True], ids=["untouched", "rewritten-same-dev"])
@pytest.mark.parametrize("case", CERTIFIED, ids=[c[0] for c in CERTIFIED])
def test_r6_activated_grant_is_single_use(case, rewrite_same, env):
    """A third byte-identical call asks again with no switch, so a test that consumes before switching proves nothing."""
    _, command, switch = case
    switch(env, "dev-a")
    _mint(command, env["work"])
    assert _validate(command, env["work"]).allowed
    if rewrite_same:
        switch(env, "dev-a")
    third = _validate(command, env["work"])
    assert not third.allowed, (third.reason or "")[:160]


@pytest.mark.parametrize("case", UNDETERMINABLE, ids=[c[0] for c in UNDETERMINABLE])
def test_r6_undeterminable_target_matches_no_grant(case, env):
    _, command, switch = case
    switch(env, "dev-a")
    minted = _mint(command, env["work"])
    retry = _validate(command, env["work"])
    assert not retry.allowed and retry.approval_id and retry.approval_id != minted, (retry.reason or "")[:160]


@pytest.mark.parametrize("case", SWITCHES, ids=[c[0] for c in SWITCHES])
def test_r6_switch_without_consuming(case, env):
    """Minted and activated under dev, switched to prod before first use: the unused approval must not match."""
    _, command, switch = case
    switch(env, "dev-a")
    minted = _mint(command, env["work"])
    switch(env, "prod-a")
    after = _validate(command, env["work"])
    assert not after.allowed and after.approval_id != minted, (after.reason or "")[:160]
