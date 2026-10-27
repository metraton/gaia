"""Adversarial probes against the effect lexicon: every write that asked before keeps asking, every read stays free."""
from __future__ import annotations

import os
import subprocess
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
    ("curl-G-o-local-read", f"curl -G -d q=x -o out.json {U}", "SIGNED"),
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
    ("aws-ecr-batch-delete-control", "aws ecr batch-delete-image --repository-name r --image-ids imageTag=x", "SIGNED"),
]

CS = f"{HOME}/.claude/settings.json"
CH = f"{HOME}/.claude/hooks"
DB = f"{HOME}/.gaia/gaia.db"
PROBES += [
    ("curl-bundled-sSLo-claude", f"curl -sSLo {CS} {U}", "BLOCKED"),
    ("curl-bundled-so-gaia-db", f"curl -so {DB} {U}", "BLOCKED"),
    ("curl-tilde-o-claude", f"curl -o ~/.claude/settings.json {U}", "BLOCKED"),
    ("curl-HOME-o-claude", f"curl -o $HOME/.claude/settings.json {U}", "BLOCKED"),
    ("curl-HOME-quoted-o-claude", f'curl -o "$HOME/.claude/settings.json" {U}', "BLOCKED"),
    ("curl-output-dir-o-claude", f"curl --output-dir {HOME}/.claude -o settings.json {U}", "BLOCKED"),
    ("curl-output-dir-O-hooks", f"curl --output-dir {CH} -O https://example.invalid/x.py", "BLOCKED"),
    ("curl-create-dirs-o-hooks", f"curl --create-dirs -o {CH}/new/x.py {U}", "BLOCKED"),
    ("curl-create-dirs-o-hooks-scheme-in-path", f"curl --create-dirs -o {CH}/x://y.py {U}", "BLOCKED"),
    ("curl-cd-claude-o-relative", f"cd {HOME}/.claude && curl -o settings.json {U}", "BLOCKED"),
    ("curl-cd-hooks-O-basename", f"cd {CH} && curl -O https://example.invalid/x.py", "BLOCKED"),
    ("curl-cd-hooks-remote-name-all", f"cd {CH} && curl --remote-name-all https://example.invalid/x.py", "BLOCKED"),
    ("curl-cd-gaia-O-db", f"cd {HOME}/.gaia && curl -O https://example.invalid/gaia.db", "BLOCKED"),
    ("curl-output-dir-gaia-O-db", f"curl --output-dir {HOME}/.gaia -O https://example.invalid/gaia.db", "BLOCKED"),
    ("curl-dump-header-claude", f"curl -D {CS} {U}", "BLOCKED"),
    ("curl-cookie-jar-claude", f"curl -c {CS} {U}", "BLOCKED"),
    ("curl-trace-hooks", f"curl --trace {CH}/x.py {U}", "BLOCKED"),
    ("curl-stderr-gaia-db", f"curl --stderr {DB} {U}", "BLOCKED"),
    ("curl-etag-save-claude", f"curl --etag-save {CS} {U}", "BLOCKED"),
    ("curl-hsts-eq-claude", f"curl --hsts={CS} {U}", "BLOCKED"),
    ("curl-alt-svc-claude", f"curl --alt-svc {CS} {U}", "BLOCKED"),
    ("curl-o-stdout-dash-attached", f"curl -o- {U}", "FREE"),
    ("curl-o-stdout-dash", f"curl -o - {U}", "FREE"),
    ("curl-o-local-file", f"curl -o out.json {U}", "FREE"),
    ("curl-cd-hooks-stdout", f"cd {CH} && curl -sS {U}", "FREE"),
    ("curl-header-unresolved-variable", f'curl -H "Authorization: Bearer $TOKEN" {U}', "FREE"),
    ("wget-O-claude", f"wget -O {CS} {U}", "BLOCKED"),
    ("wget-qO-bundled-claude", f"wget -qO {CS} {U}", "BLOCKED"),
    ("wget-output-document-eq-claude", f"wget --output-document={CS} {U}", "BLOCKED"),
    ("wget-output-doc-abbrev-claude", f"wget --output-doc={CS} {U}", "BLOCKED"),
    ("wget-P-hooks", f"wget -P {CH} https://example.invalid/x.py", "BLOCKED"),
    ("wget-directory-prefix-hooks", f"wget --directory-prefix={CH} https://example.invalid/x.py", "BLOCKED"),
    ("wget-cd-hooks-bare", f"cd {CH} && wget https://example.invalid/x.py", "BLOCKED"),
    ("wget-append-output-claude", f"wget -a {CS} {U}", "BLOCKED"),
    ("wget-o-logfile-claude", f"wget -o {CS} {U}", "BLOCKED"),
    ("wget-O-gaia-db", f"wget -O {DB} {U}", "BLOCKED"),
    ("wget-P-gaia-db", f"wget -P {HOME}/.gaia https://example.invalid/gaia.db", "BLOCKED"),
    ("wget-O-stdout", f"wget -O- {U}", "FREE"),
    ("wget-qO-stdout", f"wget -qO- {U}", "FREE"),
    ("httpie-download-cd-hooks", f"cd {CH} && http --download https://example.invalid/x.py", "BLOCKED"),
    ("httpie-o-claude", f"http -o {CS} {U}", "BLOCKED"),
    ("curl-G-H-at-before-d", f"curl -G -H @h.txt -d q=x {U}", "SIGNED"),
    ("curl-G-header-at-long", f"curl -G --header @h.txt -d q=x {U}", "SIGNED"),
    ("curl-G-H-at-attached", f"curl -G -H@h.txt -d q=x {U}", "SIGNED"),
    ("curl-G-H-at-after-url", f"curl -G -d q=x {U} -H @h.txt", "SIGNED"),
    ("curl-H-at-then-G", f"curl -H @h.txt -G -d q=x {U}", "SIGNED"),
    ("curl-G-H-plain-then-at", f"curl -G -H 'A: b' -H @h.txt -d q=x {U}", "SIGNED"),
    ("curl-G-H-plain-read", f"curl -G -H 'Accept: application/json' -d q=x {U}", "FREE"),
    ("curl-G-sSLo-claude", f"curl -G -d q=x -sSLo {CS} {U}", "BLOCKED"),
    ("curl-G-so-gaia-db", f"curl -G -d q=x -so {DB} {U}", "BLOCKED"),
    ("curl-G-tilde-o-claude", f"curl -G -d q=x -o ~/.claude/settings.json {U}", "BLOCKED"),
    ("curl-G-output-dir-o-claude", f"curl -G -d q=x --output-dir {HOME}/.claude -o settings.json {U}", "BLOCKED"),
    ("curl-G-cd-hooks-o-relative", f"cd {CH} && curl -G -d q=x -o x.py {U}", "BLOCKED"),
    ("curl-G-o-attached-claude", f"curl -G -d q=x -o{CS} {U}", "BLOCKED"),
    ("curl-G-dash-dash-output-eq", f"curl -G -d q=x --output={CS} {U}", "BLOCKED"),
    ("aws-get-policy-value-delete", "aws s3api get-bucket-policy --bucket delete", "FREE"),
    ("aws-describe-value-put", "aws logs describe-log-groups --log-group-name-prefix put", "FREE"),
    ("aws-profile-delete-s3-ls", "aws --profile delete s3 ls", "FREE"),
    ("aws-region-eq-create-ec2-describe", "aws --region=create ec2 describe-instances", "FREE"),
    ("aws-ec2-describe-filter-create", "aws ec2 describe-instances --filters Name=tag:x,Values=create", "FREE"),
    ("aws-debug-s3-ls", "aws --debug s3 ls", "FREE"),
    ("gcloud-project-delete-list", "gcloud --project delete compute instances list", "FREE"),
    ("gcloud-verbosity-delete-list", "gcloud --verbosity delete compute instances list", "FREE"),
    ("gcloud-list-filter-create", "gcloud compute instances list --filter create", "FREE"),
    ("gcloud-describe-zone-delete", "gcloud compute instances describe vm1 --zone delete", "FREE"),
    ("gcloud-describe-name-config", "gcloud compute instances describe config", "FREE"),
    ("gcloud-instances-list-config-positional", "gcloud compute instances list config", "FREE"),
    ("aws-s3-ls-config", "aws s3 ls s3://b/config", "FREE"),
    ("az-subscription-create-vm-list", "az --subscription create vm list", "FREE"),
    ("az-output-delete-vm-list", "az -o delete vm list", "FREE"),
    ("az-vm-list-query-put", "az vm list --query put", "FREE"),
    ("az-vm-show-name-create", "az vm show -n create -g r", "FREE"),
    ("az-show-name-delete", "az storage account show -n delete -g create", "BLOCKED"),
    ("az-vm-show-name-delete", "az vm show -n delete -g r", "BLOCKED"),
    ("aws-profile-list-s3-rm", "aws --profile list s3 rm s3://b/k", "SIGNED"),
    ("aws-profile-get-s3-cp", "aws --profile get s3 cp a s3://b/k", "SIGNED"),
    ("aws-unknown-option-list-s3-rm", "aws --frobnicate list s3 rm s3://b/k", "SIGNED"),
    ("aws-delete-sg-value-list", "aws ec2 delete-security-group --group-name list", "SIGNED"),
    ("aws-region-eq-get-sqs-purge", "aws --region=get sqs purge-queue --queue-url q", "SIGNED"),
    ("gcloud-project-list-delete", "gcloud --project list compute instances delete vm1", "SIGNED"),
    ("gcloud-account-list-run-deploy", "gcloud --account list run deploy svc --image i", "SIGNED"),
    ("gcloud-delete-zone-describe", "gcloud compute instances delete vm1 --zone describe", "SIGNED"),
    ("az-subscription-list-alert-delete", "az --subscription list monitor activity-log alert delete -n x -g r", "SIGNED"),
    ("az-group-delete-n-show", "az group delete -n show", "BLOCKED"),
    ("az-subscription-list-vm-delete", "az --subscription list vm delete -n v -g r", "BLOCKED"),
    ("aws-ec2-terminate-plain", "aws ec2 terminate-instances --instance-ids i-123", "BLOCKED"),
    ("gcloud-mid-short-unknown-set-password", "gcloud sql -x my-project users set-password postgres --instance=prod", "SIGNED"),
    ("gcloud-mid-long-unknown-instances-delete", "gcloud compute --frob list instances delete vm1", "SIGNED"),
    ("gcloud-mid-unknown-between-groups-deploy", "gcloud run -z get services delete svc", "SIGNED"),
    ("aws-mid-unknown-before-op", "aws s3api --frob get put-bucket-tagging --bucket b --tagging file://t.json", "SIGNED"),
    ("az-mid-unknown-between-groups", "az monitor --frob show activity-log alert delete -n x -g r", "SIGNED"),
    ("gcloud-mid-unknown-read-stays-free", "gcloud compute --frob x instances list", "FREE"),
    ("sudo-curl-o-claude", f"sudo curl -o {CS} {U}", "BLOCKED"),
    ("env-curl-o-claude", f"env FOO=1 curl -o {CS} {U}", "BLOCKED"),
    ("timeout-curl-o-gaia-db", f"timeout -k 5 10 curl -so {DB} {U}", "BLOCKED"),
    ("nice-wget-O-claude", f"nice -n 5 wget -qO {CS} {U}", "BLOCKED"),
    ("command-curl-o-hooks", f"command curl -o {CH}/x.py {U}", "BLOCKED"),
    ("var-o-claude", f"O={CS}; curl -o $O {U}", "BLOCKED"),
    ("var-quoted-o-claude", f'O={CS} && curl -o "$O" {U}', "BLOCKED"),
    ("var-braced-o-gaia-db", f"D={DB}; curl -o ${{D}} {U}", "BLOCKED"),
    ("curl-glob-hash-claude", f"curl -o {HOME}/.claude/settings.#1 'https://example.invalid/{{json}}'", "BLOCKED"),
    ("env-C-relative", f"env -C {HOME}/.claude curl -o settings.json {U}", "BLOCKED"),
    ("env-i-curl", f"env -i curl -o {CS} {U}", "BLOCKED"),
    ("sudo-n-curl", f"sudo -n curl -o {CS} {U}", "BLOCKED"),
    ("sudo-user-long-curl", f"sudo --user root curl -o {CS} {U}", "BLOCKED"),
    ("command-p-curl", f"command -p curl -o {CS} {U}", "BLOCKED"),
    ("exec-a-curl", f"exec -a x curl -o {CS} {U}", "BLOCKED"),
    ("timeout-signal-long-curl", f"timeout --signal KILL 10 curl -o {CS} {U}", "BLOCKED"),
    ("ionice-curl", f"ionice -c 3 curl -o {CS} {U}", "BLOCKED"),
    ("setsid-curl", f"setsid curl -o {CS} {U}", "BLOCKED"),
    ("stdbuf-oL-curl", f"stdbuf -oL curl -o {CS} {U}", "BLOCKED"),
    ("sudo-curl-G-read", f"sudo curl -G -d q=x {U}", "SIGNED"),
    ("curl-G-var-in-data", f"curl -G -d q=$Q {U}", "SIGNED"),
    ("curl-G-url-glob", f"curl -G -d q=x 'https://example.invalid/{{a,b}}'", "SIGNED"),
]

# The -G read relief needs a line it fully understands; any wrapper, output option,
# variable or glob keeps the signature the data option had before it.
G = "curl -G -d q=x"
LOWERING_ASKS = [
    ("g-plain-o-claude", f"{G} -o {CS} {U}"),
    ("g-env-i", f"env -i {G} -o {CS} {U}"),
    ("g-sudo-n", f"sudo -n {G} -o {CS} {U}"),
    ("g-sudo-user-long", f"sudo --user root {G} -o {CS} {U}"),
    ("g-command-p", f"command -p {G} -o {CS} {U}"),
    ("g-exec-a", f"exec -a x {G} -o {CS} {U}"),
    ("g-timeout-signal", f"timeout --signal KILL 10 {G} -o {CS} {U}"),
    ("g-ionice", f"ionice -c 3 {G} -o {CS} {U}"),
    ("g-setsid", f"setsid {G} -o {CS} {U}"),
    ("g-var-o", f"O={CS}; {G} -o $O {U}"),
    ("g-var-gaia-db", f"D={DB}; {G} -o ${{D}} {U}"),
    ("g-env-C-relative", f"env -C {HOME}/.claude {G} -o settings.json {U}"),
    ("g-cd-OJ", f"cd {HOME}/.claude && {G} -OJ {U}"),
    ("g-glob-hash", f"{G} -o {HOME}/.claude/settings.#1 'https://example.invalid/{{json}}'"),
    ("g-xargs", f"echo {CS} | xargs {G} {U} -o"),
    ("g-cd-hooks-schemeless-O", f"cd {CH} && {G} -O example.invalid/x.py"),
    ("g-git-tree", f"{G} -o GITTREE/src.py {U}"),
    ("g-git-hooks", f"{G} -o GITTREE/.git/hooks/pre-commit {U}"),
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


@pytest.mark.parametrize("probe_id,command", LOWERING_ASKS, ids=[r[0] for r in LOWERING_ASKS])
def test_lowering_needs_a_fully_understood_line(probe_id, command, isolated, tmp_path):
    tree = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    verdict, reason = _observe(command.replace("GITTREE", str(tree)), isolated)
    assert verdict in ("BLOCKED", "SIGNED"), f"{verdict} :: {reason[:120]}"


@pytest.mark.parametrize("form", ["o", "cd"])
def test_symlinked_dir_into_claude(form, isolated):
    link = isolated / "lnk"
    link.symlink_to(Path(HOME) / ".claude")
    command = f"curl -o {link}/settings.json {U}" if form == "o" else f"cd {link} && curl -o settings.json {U}"
    verdict, reason = _observe(command, isolated)
    assert verdict == "BLOCKED", f"{verdict} :: {reason[:120]}"
