"""Verifier probes round 2 (handoff 34212): per-program targets, switches, operation words, protected-branch repository."""

from __future__ import annotations

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
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT = "aprobet1020x03"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    for name in ("KUBECONFIG", "CLOUDSDK_CONFIG", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST",
                 "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "AWS_PROFILE", "TF_WORKSPACE",
                 "CONTAINER_HOST", "CONTAINER_CONNECTION", "PULUMI_HOME"):
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
FREE = ("FREE",)

ARITY = [
    ("k-unknown-eq-before-read", "kubectl --context prod-a --frob=1 get pods", {}, NOT_FREE),
    ("k-unknown-valued-before-read", "kubectl --context prod-a --frob get get pods", {}, NOT_FREE),
    ("k-unknown-short-before-read", "kubectl --context prod-a -x get pods", {}, NOT_FREE),
    ("k-bool-known-with-value-word", "kubectl --context prod-a --insecure-skip-tls-verify true delete-ish", {}, NOT_FREE),
    ("aws-unknown-bool-before-read", "aws --profile prod --frob ec2 describe-instances", {}, NOT_FREE),
    ("aws-unknown-eq-before-read", "aws --profile=prod --frob=x ec2 describe-instances", {}, NOT_FREE),
    ("gcloud-unknown-eq-before-read", "gcloud --project=acme-prod --frob=1 compute instances list", {}, NOT_FREE),
    ("az-unknown-before-read", "az --subscription prod-sub --frob vm list", {}, NOT_FREE),
    ("docker-unknown-before-read", "docker --context prod-eu --frob ps", {}, NOT_FREE),
    ("helm-unknown-before-read", "helm --kube-context prod-a --frob list", {}, NOT_FREE),
    ("flux-unknown-before-read", "flux --context prod-a --frob get ks", {}, NOT_FREE),
    ("tf-unknown-before-read", "TF_WORKSPACE=prod terraform -frob show", {}, NOT_FREE),
    ("pulumi-unknown-verb", "pulumi --stack prod-x frobnicate", {}, NOT_FREE),
    ("ok-kubectl-context-eq-ns-eq", "kubectl --context=prod-a -n=app get pods", {}, FREE),
    ("ok-kubectl-ns-attached", "kubectl --context prod-a -napp get pods", {}, FREE),
    ("ok-aws-profile-eq-output-eq", "aws --profile=prod --output=json ec2 describe-instances", {}, FREE),
    ("ok-aws-debug-first", "aws --debug --profile prod ec2 describe-instances", {}, FREE),
    ("ok-gcloud-eq-format", "gcloud --project=acme-prod --format=json compute instances list", {}, FREE),
    ("ok-az-read-output", "az vm list --subscription prod-sub -o table", {}, FREE),
    ("ok-docker-ps", "docker --context prod-eu ps", {}, FREE),
    ("ok-flux-get", "flux --context prod-a get ks", {}, FREE),
    ("ok-pulumi-preview", "pulumi --stack prod-x preview", {}, FREE),
    ("ok-tf-chdir-show", "TF_WORKSPACE=prod terraform -chdir=. show", {}, FREE),
]

SWITCHES = [
    ("k-switch-flag-after", "kubectl config use-context prod-a --namespace x", {"kube": "dev-a"}, NOT_FREE),
    ("k-switch-flag-then-dashdash", "kubectl config use-context --kubeconfig={TMP}/kubeconfig -- prod-a", {"kube": "dev-a"}, NOT_FREE),
    ("docker-switch-unknown-then-dashdash", "docker context use --frob -- prod-eu", {"docker": "dev-eu"}, NOT_FREE),
    ("tf-select-or-create-dashdash", "terraform workspace select -or-create -- prod", {"tf_cwd": "dev"}, NOT_FREE),
    ("tf-new-lock-flag", "terraform -chdir=. workspace new -lock=false prod", {"tf_cwd": "dev"}, NOT_FREE),
    ("pulumi-select-create", "pulumi stack select --create prod", {}, NOT_FREE),
    ("pulumi-cwd-select", "pulumi --cwd . stack select prod", {}, NOT_FREE),
    ("pulumi-select-stack-flag", "pulumi stack select --stack prod", {}, NOT_FREE),
    ("pulumi-select-s-flag", "pulumi stack select -s prod", {}, NOT_FREE),
    ("pulumi-select-s-attached", "pulumi stack select -sprod", {}, NOT_FREE),
    ("gcloud-get-credentials-prod", "gcloud --project acme-prod container clusters get-credentials prod-gke", {}, NOT_FREE),
    ("gcloud-get-credentials-prod-project", "gcloud --project acme-prod container clusters get-credentials main", {}, NOT_FREE),
    ("az-get-credentials-prod", "az aks get-credentials --subscription prod-sub -g rg -n prod-aks", {}, NOT_FREE),
    ("aws-update-kubeconfig-prod", "aws eks update-kubeconfig --name prod-eks", {}, NOT_FREE),
    ("dev-k-switch-dashdash", "kubectl config use-context -- dev-a", {"kube": "prod-a"}, FREE),
    ("dev-tf-select-or-create-dashdash", "terraform workspace select -or-create -- dev", {"tf_cwd": "prod"}, FREE),
    ("dev-gcloud-activate-dashdash", "gcloud config configurations activate -- devcfg",
     {"gcloud": {"default": "acme-prod", "devcfg": "acme-dev"}}, FREE),
    ("dev-pulumi-select", "pulumi stack select dev", {}, FREE),
    ("dev-pulumi-select-stack-flag", "pulumi stack select --stack dev", {}, FREE),
    ("dev-gcloud-get-credentials", "gcloud --project acme-dev container clusters get-credentials dev-gke", {}, FREE),
    ("dev-az-get-credentials", "az aks get-credentials --subscription dev-sub -g rg -n dev-aks", {}, FREE),
    ("local-git-switch-dashdash", "git switch -- prod-hotfix", {}, FREE),
]

AZ = [
    ("az-search-group-unknown-verb", "az search service frobnicate --subscription prod-sub -n x", {}, NOT_FREE),
    ("az-search-admin-key-renew", "az search admin-key renew --subscription prod-sub --service-name s -g rg --key-kind primary", {}, NOT_FREE),
    ("az-find-read", "az find vm --subscription prod-sub", {}, FREE),
    ("az-search-service-list", "az search service list --subscription prod-sub -g rg", {}, FREE),
]

OTHER = [
    ("podman-connection-unknown-verb", "podman --connection prod-a frobnicate c1", {}, NOT_FREE),
    ("podman-connection-eq-unknown-verb", "podman --connection=prod-a frobnicate c1", {}, NOT_FREE),
    ("podman-connection-variable", "CONTAINER_CONNECTION=prod-a podman frobnicate c1", {}, NOT_FREE),
    ("podman-connection-dev", "podman --connection dev-a frobnicate c1", {}, FREE),
    ("kubectx-prod", "kubectx prod-a", {"kube": "dev-a"}, NOT_FREE),
    ("kubectx-dev", "kubectx dev-a", {"kube": "prod-a"}, FREE),
    ("kubectx-list", "kubectx", {"kube": "prod-a"}, FREE),
    ("kubens-on-prod-context", "kubens app", {"kube": "prod-a"}, NOT_FREE),
    ("kubens-on-dev-context", "kubens app", {"kube": "dev-a"}, FREE),
    ("nontarget-ls-context", "ls --context prod-notes.txt", {}, FREE),
    ("nontarget-diff-context", "diff --context prod.yaml dev.yaml", {}, FREE),
    ("nontarget-pytest-env", "pytest --env prod tests", {}, FREE),
    ("nontarget-cargo-profile", "cargo test --profile prod", {}, FREE),
    ("unmodelled-conda-stack", "conda activate --stack prod-env", {}, NOT_FREE),
    ("unmodelled-shipit-stack", "shipit release --stack prod", {}, NOT_FREE),
]

CASES = ARITY + SWITCHES + AZ + OTHER


def _arrange(setup: dict, env: dict, monkeypatch) -> None:
    home, tmp, work = env["home"], env["tmp"], env["work"]
    if "kube" in setup:
        monkeypatch.setenv("KUBECONFIG", str(kube(tmp / "kubeconfig", setup["kube"])))
    if "gcloud" in setup:
        gcloud(home, setup["gcloud"])
    if "docker" in setup:
        docker(home, setup["docker"])
    if "tf_cwd" in setup:
        tf_workspace(work, setup["tf_cwd"])


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_r2_probe(case, env, monkeypatch):
    name, command, setup, accepted = case
    if name in {"dev-pulumi-select", "dev-pulumi-select-stack-flag", "dev-gcloud-get-credentials",
                "dev-az-get-credentials", "dev-k-switch-dashdash", "dev-tf-select-or-create-dashdash"}:
        accepted = ("SIGNED",)
    _arrange(setup, env, monkeypatch)
    got, reason = verdict(command.replace("{TMP}", str(env["tmp"])), env["work"])
    assert got in accepted, (got, reason[:160])


def test_kubectl_token_row_is_categorical(env):
    got, reason = verdict("kubectl --context prod-a --token describe frobnicate thing", env["work"])
    assert got == "BLOCKED", (got, reason[:300])


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
    ("control-declared-release", "git push origin release/1.x", "repo", NOT_FREE),
    ("u-flag-declared-release", "git push -u origin release/1.x", "repo", NOT_FREE),
    ("set-upstream-declared-release", "git push --set-upstream origin release/1.x", "repo", NOT_FREE),
    ("dash-C-from-outside", "git -C {REPO} push origin release/1.x", "outside", NOT_FREE),
    ("git-dir-from-outside", "git --git-dir={REPO}/.git push origin release/1.x", "outside", NOT_FREE),
    ("work-tree-from-outside", "git --git-dir {REPO}/.git --work-tree {REPO} push origin release/1.x", "outside", NOT_FREE),
    ("cd-chain-from-outside", "cd {REPO} && git push origin release/1.x", "outside", NOT_FREE),
    ("twin-feature-free", "git push origin feature-x", "repo", FREE),
    ("twin-dash-C-feature-free", "git -C {REPO} push origin feature-x", "outside", FREE),
]


@pytest.mark.parametrize("case", BRANCH, ids=[c[0] for c in BRANCH])
def test_declared_release_branch_probe(case, env):
    _, command, where, accepted = case
    repo = _repo(env["tmp"] / "repo")
    _declare(repo, {"protected_branches": ["release/1.x"]})
    cwd = repo if where == "repo" else env["work"]
    got, reason = verdict(command.replace("{REPO}", str(repo)), cwd)
    assert got in accepted, (got, reason[:160])
