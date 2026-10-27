"""Adversarial probes against the effect lexicon: every write that asked before keeps asking, every read stays free."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

AGENT_ID = "a228a7dd1807336a7"

PROBES = [
    ("kubectl-apply-dry-run-none", "kubectl apply -f x.yaml --dry-run=none", "SIGNED"),
    ("kubectl-apply-dry-run-false", "kubectl apply -f x.yaml --dry-run=false", "SIGNED"),
    ("kubectl-delete-dry-run-none", "kubectl delete pod p1 --dry-run=none", "SIGNED"),
    ("kubectl-apply-dry-run-server", "kubectl apply -f x.yaml --dry-run=server", "FREE"),
    ("kubectl-apply-dry-run-client", "kubectl apply -f x.yaml --dry-run=client", "FREE"),
    ("helm-upgrade-dry-run-none", "helm upgrade rel1 ./chart --dry-run=none", "SIGNED"),
    ("npm-publish-dry-run-false", "npm publish --dry-run=false", "SIGNED"),
    ("pulumi-up-dry-run-false", "pulumi up --yes --dry-run=false", "SIGNED"),
    ("terraform-apply-planfile", "terraform apply plan.tfplan", "SIGNED"),
    ("terraform-apply-planfile-named-plan", "terraform apply plan", "SIGNED"),
    ("terraform-apply-target-plan", "terraform apply -target=module.plan", "SIGNED"),
    ("terraform-apply-refresh-only", "terraform apply -refresh-only -auto-approve", "SIGNED"),
    ("terraform-plan-out", "terraform plan -out=plan.tfplan", "FREE"),
    ("terraform-plan-destroy", "terraform plan -destroy", "FREE"),
    ("pulumi-up-diff", "pulumi up --diff --yes", "SIGNED"),
    ("pulumi-up-plan-file", "pulumi up --plan plan.json --yes", "SIGNED"),
    ("pulumi-up-stack-check", "pulumi up --yes --stack check", "SIGNED"),
    ("helm-upgrade-set-check", "helm upgrade rel1 ./chart --set mode=check", "SIGNED"),
    ("helm-upgrade-chart-named-check", "helm upgrade --install rel1 ./check", "SIGNED"),
    ("kubectl-apply-file-named-check", "kubectl apply -f check.yaml", "SIGNED"),
    ("kubectl-apply-validate", "kubectl apply -f x.yaml --validate=true", "SIGNED"),
    ("ansible-playbook-diff", "ansible-playbook site.yml --diff", "SIGNED"),
    ("ansible-playbook-tags-check", "ansible-playbook site.yml --tags check", "SIGNED"),
    ("ansible-playbook-file-check", "ansible-playbook check.yml", "SIGNED"),
    ("ansible-playbook-check", "ansible-playbook site.yml --check", "FREE"),
    ("curl-G-X-POST-before", "curl -G -X POST -d q=x https://example.invalid/x", "SIGNED"),
    ("curl-G-X-POST-after", "curl -G -d q=x -X POST https://example.invalid/x", "SIGNED"),
    ("curl-get-XPOST-attached", "curl --get -d q=x -XPOST https://example.invalid/x", "SIGNED"),
    ("curl-G-request-PUT", "curl -G -d q=x --request PUT https://example.invalid/x", "SIGNED"),
    ("curl-G-T", "curl -G -T file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-upload-file", "curl -G --upload-file file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-d-at-file", "curl -G -d @file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-d-at-file-after-literal", "curl -G -d q=x --data @file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-data-binary-at", "curl -G --data-binary @file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-urlencode-bare-at", "curl -G --data-urlencode @file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-d-attached-at", "curl -G -d@file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-F-form", "curl -G -F f=@file.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-json", "curl -G --json {} https://example.invalid/x", "SIGNED"),
    ("curl-G-method-override-header", "curl -G -d q=x -H X-HTTP-Method-Override:DELETE https://example.invalid/x", "SIGNED"),
    ("curl-G-d-stdin", "curl -G -d @- https://example.invalid/x", "SIGNED"),
    ("curl-G-literal-read", "curl -G -d q=x https://example.invalid/x", "FREE"),
    ("curl-G-urlencode-literal-at-value", "curl -G --data-urlencode q=a@b https://example.invalid/x", "FREE"),
    ("curl-G-next-post", "curl -G -d q=x https://a.example.invalid/r --next -d y=1 https://b.example.invalid/w", "SIGNED"),
    ("curl-G-config-file", "curl -G -d q=x -K cfg.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-config-long", "curl -G -d q=x --config cfg.txt https://example.invalid/x", "SIGNED"),
    ("curl-G-XPUT-attached", "curl -G -d q=x -XPUT https://example.invalid/x", "SIGNED"),
    ("curl-XPOST-attached-nodata", "curl -XPOST https://example.invalid/x", "SIGNED"),
    ("curl-G-urlencode-request-post", "curl -G --data-urlencode q=x --request POST https://example.invalid/x", "SIGNED"),
    ("helm-repo-update", "helm repo update", "FREE"),
    ("helm-repo-add", "helm repo add bitnami https://charts.example.invalid", "SIGNED"),
    ("helm-repo-remove", "helm repo remove bitnami", "SIGNED"),
    ("apt-update", "apt update", "FREE"),
    ("apt-get-update", "apt-get update", "FREE"),
    ("apt-upgrade", "apt upgrade", "SIGNED"),
    ("apt-get-upgrade-y", "apt-get upgrade -y", "SIGNED"),
    ("apt-get-dist-upgrade", "apt-get dist-upgrade", "SIGNED"),
    ("apt-install", "apt install curl", "SIGNED"),
    ("apt-update-pre-invoke-hook", "apt-get update -o APT::Update::Pre-Invoke::=touch-x", "SIGNED"),
    ("apt-update-config-file", "apt-get update -c ./evil.conf", "SIGNED"),
    ("apt-get-o-before-update", "apt-get -o APT::Update::Pre-Invoke::=x update", "SIGNED"),
    ("apt-update-dpkg-pre-invoke", "apt update -o Dpkg::Pre-Invoke::=x", "SIGNED"),
    ("sudo-apt-update", "sudo apt update", "SIGNED"),
    ("act-push", "act push", "FREE"),
    ("aws-report-instance-status", "aws ec2 report-instance-status --instances i-1 --status impaired", "SIGNED"),
    ("aws-put-bucket-policy", "aws s3api put-bucket-policy --bucket b --policy file://p.json", "SIGNED"),
    ("aws-associate-address", "aws ec2 associate-address --instance-id i-1 --allocation-id a-1", "SIGNED"),
    ("aws-schedule-key-deletion", "aws kms schedule-key-deletion --key-id k", "BLOCKED"),
    ("gcloud-slug-get-at-depth3", "gcloud compute ssh vm-get-1 --command=true", "FREE"),
    ("gcloud-instances-delete-slug", "gcloud compute instances delete list-vm-1", "SIGNED"),
    ("gcloud-get-iam-then-set", "gcloud projects set-iam-policy p1 policy.json", "SIGNED"),
    ("camel-list-and-delete", "mytool listAndDelete x", "SIGNED"),
    ("aws-cognito-admin-get-user", "aws cognito-idp admin-get-user --user-pool-id p --username u", "FREE"),
    ("aws-cognito-admin-delete-user", "aws cognito-idp admin-delete-user --user-pool-id p --username u", "SIGNED"),
    ("aws-dynamodb-batch-write", "aws dynamodb batch-write-item --request-items file://r.json", "SIGNED"),
    ("aws-timestream-query-delete", "aws timestream-query delete-scheduled-query --scheduled-query-arn a", "SIGNED"),
    ("aws-timestream-query-update", "aws timestream-query update-account-settings --max-query-tcu 4", "SIGNED"),
    ("gcloud-runtime-config-delete", "gcloud runtime-config configs delete c1", "SIGNED"),
    ("gcloud-runtime-config-var-set", "gcloud runtime-config configs variables set k v --config-name c1", "SIGNED"),
    ("gcloud-os-config-assignment-delete", "gcloud compute os-config os-policy-assignments delete a1 --location z", "SIGNED"),
    ("aws-logs-delete-log-group", "aws logs delete-log-group --log-group-name g", "SIGNED"),
    ("gcloud-logging-logs-delete", "gcloud logging logs delete my-log", "SIGNED"),
    ("az-webapp-config-appsettings-delete", "az webapp config appsettings delete --name a --resource-group r --setting-names K", "BLOCKED"),
    ("gcloud-os-login-describe", "gcloud compute os-login describe-profile", "FREE"),
    ("aws-cognito-sync-list", "aws cognito-sync list-datasets --identity-pool-id p --identity-id i", "FREE"),
    ("nvm-use", "nvm use 18", "FREE"),
    ("kubectl-set-context", "kubectl config set-context --current --namespace=x", "SIGNED"),
    ("git-push-force", "git push --force origin main", "SIGNED"),
    ("terraform-apply-auto-approve", "terraform apply -auto-approve", "SIGNED"),
    ("terraform-destroy-auto-approve-long", "terraform destroy --auto-approve", "SIGNED"),
    ("docker-build-push", "docker build --push -t img:1 .", "SIGNED"),
    ("skaffold-build-push", "skaffold build --push", "SIGNED"),
    ("compose-down-volumes-long", "docker compose down --volumes", "SIGNED"),
    ("compose-down-v", "docker compose down -v", "SIGNED"),
    ("compose-f-down-v", "docker compose -f prod.yml down -v", "SIGNED"),
    ("docker-compose-binary-down-v", "docker-compose down -v", "SIGNED"),
    ("gh-pr-merge-admin", "gh pr merge 1 --admin", "SIGNED"),
    ("docker-system-prune-all", "docker system prune --all", "BLOCKED"),
    ("terraform-apply-auto-approve-long", "terraform apply --auto-approve", "SIGNED"),
    ("terraform-destroy-auto-approve-true", "terraform destroy -auto-approve=true", "SIGNED"),
    ("buildx-output-registry", "docker buildx build --output type=registry -t img:1 .", "SIGNED"),
    ("buildx-o-push-true", "docker buildx build -o type=image,push=true -t img:1 .", "SIGNED"),
    ("kubectl-dry-run-client-then-none", "kubectl apply -f x.yaml --dry-run=client --dry-run=none", "SIGNED"),
    ("kubectl-dry-run-server-then-none", "kubectl delete pod p1 --dry-run=server --dry-run=none", "SIGNED"),
    ("npm-publish-dry-run-then-false", "npm publish --dry-run --dry-run=false", "SIGNED"),
    ("helm-dry-run-client-then-none", "helm upgrade rel1 ./chart --dry-run=client --dry-run=none", "SIGNED"),
    ("git-log-all", "git log --all", "FREE"),
    ("docker-ps-all", "docker ps --all", "FREE"),
    ("kubectl-get-all-ns", "kubectl get pods --all-namespaces", "FREE"),
    ("kubectl-config-view", "kubectl config view", "FREE"),
    ("kubectl-config-get-contexts", "kubectl config get-contexts", "FREE"),
    ("gcloud-config-list", "gcloud config list", "FREE"),
    ("gcloud-auth-list", "gcloud auth list", "FREE"),
    ("gh-auth-status", "gh auth status", "FREE"),
    ("aws-sts-identity", "aws sts get-caller-identity", "FREE"),
    ("aws-ecr-login-password", "aws ecr get-login-password --region us-east-1", "FREE"),
    ("helm-list", "helm list -A", "FREE"),
    ("apt-show", "apt show curl", "FREE"),
    ("apt-list-installed", "apt list --installed", "FREE"),
    ("terraform-plan", "terraform plan", "FREE"),
    ("curl-plain", "curl https://example.invalid/x", "FREE"),
    ("docker-compose-logs", "docker compose logs web", "FREE"),
    ("docker-compose-ps", "docker compose ps", "FREE"),
    ("kubectl-describe-select", "kubectl get pods --field-selector status.phase=Running", "FREE"),
    ("aws-dynamodb-transact-get", "aws dynamodb transact-get-items --transact-items file://t.json", "FREE"),
    ("gcloud-describe-slug-with-delete", "gcloud compute instances describe delete-me-1", "FREE"),
]

U = "https://example.invalid/x"
HOME = os.path.expanduser("~")
PROBES += [
    ("curl-G-d-json", f"curl -G -d q=x --json {{}} {U}", "SIGNED"),
    ("curl-G-form-string", f"curl -G --form-string a=b {U}", "SIGNED"),
    ("curl-G-d-F", f"curl -G -d q=x -F a=b {U}", "SIGNED"),
    ("curl-G-url-query", f"curl -G --url-query q=x {U}", "FREE"),
    ("curl-G-o-bashrc", f"curl -G -d q=x -o {HOME}/.bashrc {U}", "SIGNED"),
    ("curl-o-bashrc-no-G", f"curl -o {HOME}/.bashrc {U}", "SIGNED"),
    ("curl-G-output-ssh", f"curl -G -d q=x --output {HOME}/.ssh/authorized_keys {U}", "SIGNED"),
    ("curl-G-O-remote-name", f"curl -G -d q=x -O {U}", "SIGNED"),
    ("curl-G-proxy", f"curl -G -d q=x --proxy http://p.example.invalid:8080 {U}", "FREE"),
    ("curl-G-unknown-long", f"curl -G -d q=x --frobnicate {U}", "SIGNED"),
    ("curl-G-header-file", f"curl -G -d q=x -H @headers.txt {U}", "SIGNED"),
    ("curl-G-header-override-put", f"curl -G -d q=x -H X-HTTP-Method-Override:PUT {U}", "SIGNED"),
    ("curl-G-X-after-url", f"curl -G -d q=x {U} -X POST", "SIGNED"),
    ("curl-G-request-patch", f"curl -G -d q=x --request PATCH {U}", "SIGNED"),
    ("curl-G-T-stdin", f"curl -G -d q=x -T - {U}", "SIGNED"),
    ("curl-G-dq-attached", f"curl -G -dq=x {U}", "SIGNED"),
    ("curl-G-sSL-read", f"curl -G -sSL -d q=x {U}", "FREE"),
    ("curl-G-user-read", f"curl -G -d q=x -u user:pass {U}", "FREE"),
    ("curl-G-o-local-read", f"curl -G -d q=x -o out.json {U}", "FREE"),
    ("curl-G-write-out-file", f"curl -G -d q=x -w @fmt.txt {U}", "FREE"),
    ("curl-G-d-o-claude-settings", f"curl -G -d q=x -o {HOME}/.claude/settings.json {U}", "BLOCKED"),
    ("curl-G-o-claude-settings-nodata", f"curl -G -o {HOME}/.claude/settings.json {U}", "BLOCKED"),
    ("curl-o-claude-settings", f"curl -o {HOME}/.claude/settings.json {U}", "BLOCKED"),
    ("curl-G-d-output-claude-hooks", f"curl -G -d q=x --output {HOME}/.claude/hooks/x.py {U}", "BLOCKED"),
    ("curl-G-d-o-gaia-db", f"curl -G -d q=x -o {HOME}/.gaia/gaia.db {U}", "BLOCKED"),
    ("curl-G-d-o-profile", f"curl -G -d q=x -o {HOME}/.profile {U}", "SIGNED"),
    ("aptitude-update", "aptitude update", "SIGNED"),
    ("apt-config-env", "APT_CONFIG=./evil.conf apt-get update", "SIGNED"),
    ("apt-config-env-wrapper", "env APT_CONFIG=./evil.conf apt update", "SIGNED"),
    ("sudo-E-apt-get-update", "sudo -E apt-get update", "SIGNED"),
    ("env-apt-update", "env apt update", "SIGNED"),
    ("apt-get-update-qq", "apt-get update -qq", "FREE"),
    ("apt-get-y-update", "apt-get -y update", "FREE"),
    ("apt-get-o-attached", "apt-get -oAPT::Update::Pre-Invoke::=x update", "SIGNED"),
    ("apt-get-o-equals", "apt-get update -o=APT::Update::Pre-Invoke::=x", "SIGNED"),
    ("apt-get-insecure", "apt-get update --allow-insecure-repositories", "SIGNED"),
    ("helm-repo-update-config", "helm repo update --repository-config ./r.yaml", "SIGNED"),
    ("act-push-secret-file", "act push --secret-file .secrets", "SIGNED"),
    ("gcloud-alpha-logging-logs-delete", "gcloud alpha logging logs delete my-log", "SIGNED"),
    ("gcloud-beta-runtime-config-delete", "gcloud beta runtime-config configs delete c1", "SIGNED"),
    ("gcloud-alpha-os-config-delete", "gcloud alpha compute os-config os-policy-assignments delete a1 --location z", "SIGNED"),
    ("gcloud-alpha-instances-delete", "gcloud alpha compute instances delete vm1", "SIGNED"),
    ("az-log-analytics-workspace-delete", "az monitor log-analytics workspace delete -n w -g r", "SIGNED"),
    ("az-webapp-log-config", "az webapp log config --application-logging filesystem -n a -g r", "SIGNED"),
    ("az-activity-log-alert-delete", "az monitor activity-log alert delete -n x -g r", "SIGNED"),
    ("aws-s3api-put-bucket-logging", "aws s3api put-bucket-logging --bucket b --bucket-logging-status file://x.json", "SIGNED"),
    ("aws-s3api-delete-policy-value-list", "aws s3api delete-bucket-policy --bucket list", "SIGNED"),
    ("aws-ecr-batch-delete-value-list", "aws ecr batch-delete-image --repository-name list --image-ids imageTag=x", "SIGNED"),
    ("aws-ecr-batch-delete-value-describe", "aws ecr batch-delete-image --repository-name describe --image-ids imageTag=x", "SIGNED"),
    ("aws-dynamodb-batch-write-value-get", "aws dynamodb batch-write-item --request-items file://get.json --return-consumed-capacity list", "SIGNED"),
    ("aws-cognito-admin-delete-value-get", "aws cognito-idp admin-delete-user --user-pool-id p --username get", "SIGNED"),
    ("aws-iot-data-delete-shadow", "aws iot-data delete-thing-shadow --thing-name t out.json", "SIGNED"),
    ("aws-timestream-query-cancel", "aws timestream-query cancel-query --query-id q", "SIGNED"),
    ("aws-logs-put-retention", "aws logs put-retention-policy --log-group-name g --retention-in-days 1", "SIGNED"),
    ("aws-ec2-terminate-value-get", "aws ec2 terminate-instances --instance-ids get", "BLOCKED"),
    ("aws-resource-explorer-delete", "aws resource-explorer-2 delete-index --arn a", "SIGNED"),
    ("aws-ssm-incidents-delete", "aws ssm-incidents delete-response-plan --arn a", "SIGNED"),
    ("gcloud-instances-add-metadata-vm-list", "gcloud compute instances add-metadata list --metadata k=v", "SIGNED"),
    ("oci-logging-log-delete", "oci logging log delete --log-id x", "SIGNED"),
    ("az-show-connection-string", "az storage account show-connection-string -n a", "FREE"),
    ("aws-s3api-get-bucket-policy", "aws s3api get-bucket-policy --bucket b", "FREE"),
    ("aws-logs-describe-log-groups", "aws logs describe-log-groups", "FREE"),
    ("gcloud-logging-logs-list", "gcloud logging logs list", "FREE"),
    ("gcloud-runtime-config-list", "gcloud runtime-config configs list", "FREE"),
    ("aws-timestream-query-describe", "aws timestream-query describe-endpoints", "FREE"),
]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    from gaia.paths import ensure_layout

    ensure_layout()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert os.environ["GAIA_DB"].startswith(str(tmp_path))
    return cwd


def _observe(command: str, cwd: Path) -> tuple:
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": str(cwd), "tool_use_id": "t-probe", "session_id": "s-probe",
               "agent_id": AGENT_ID, "agent_type": "developer"}
    result = BashValidator(agent_names_scratch=True).validate(
        command, is_subagent=True, session_id="s-probe", agent_type="developer", hook_payload=payload)
    reason = result.reason or ""
    if result.allowed:
        return "FREE", reason
    if result.approval_id or reason.startswith("Compound T3 execution is disabled"):
        return ("UNKNOWN" if "could not be determined" in reason.lower() else "SIGNED"), reason
    return "BLOCKED", reason


def test_probe_ids_are_unique():
    ids = [p[0] for p in PROBES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("probe_id,command,expected", PROBES, ids=[p[0] for p in PROBES])
def test_probe(probe_id, command, expected, isolated):
    verdict, reason = _observe(command, isolated)
    assert verdict == expected, f"{verdict} :: {command} :: {reason[:120]}"
