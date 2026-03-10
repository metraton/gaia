# Destructive vs Mutative Command Reference

Comprehensive classification of CLI commands across all managed tool families.
Two categories:

- **DESTRUCTIVE (permanently blocked)**: Irreversible damage at scale. Cannot be approved via nonce.
- **MUTATIVE (approvable T3)**: Modifies state but is a normal operational command. Approved via nonce workflow.

---

## 1. Terraform / Terragrunt

### Verb vocabulary

Terraform uses `destroy`, `apply`, `plan`, `init`, `validate`, `show`, `output`, `fmt`, `import`, `state`, `taint`, `untaint`, `refresh`, `force-unlock`.

`destroy` is the only CLI in scope that uses the verb "destroy" as a first-class subcommand.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `terraform destroy` (without `-target`) | Destroys entire state -- all resources in the workspace |
| `terragrunt destroy-all` / `terragrunt run-all destroy` | Recursive destruction of all modules |
| `terraform state rm` (without specific resource) | Removes resources from state; orphans real infra |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `terraform apply` | Normal realization |
| `terraform destroy -target=<resource>` | Targeted single-resource destroy |
| `terragrunt apply` | Normal realization |
| `terraform import` | Imports existing infra into state |
| `terraform state mv` | Moves resources between state addresses |
| `terraform taint` / `terraform untaint` | Marks resource for recreation |
| `terraform force-unlock` | Unlocks stuck state lock |

### Edge cases

- `terraform apply` with a saved plan from `terraform plan -destroy` effectively runs a destroy -- but the hook sees `apply`, not `destroy`. This is acceptable because the plan was reviewed at plan time.
- `terragrunt run-all apply` is mutative (approvable), not destructive, because it applies existing plans.

---

## 2. kubectl

### Verb vocabulary

kubectl uses `get`, `describe`, `logs`, `apply`, `create`, `delete`, `patch`, `edit`, `scale`, `rollout`, `exec`, `port-forward`, `drain`, `cordon`, `uncordon`, `taint`, `label`, `annotate`, `top`, `auth`, `explain`, `diff`, `cp`.

kubectl uses `delete` -- never `destroy` or `remove`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `kubectl delete namespace <name>` | Cascading deletion of ALL resources in namespace |
| `kubectl delete ns <name>` | Short form of above |
| `kubectl delete node <name>` | Removes node from cluster |
| `kubectl delete cluster <name>` | Removes entire cluster |
| `kubectl delete pv <name>` / `persistentvolume` | Data loss -- underlying storage may be deleted |
| `kubectl delete pvc <name>` / `persistentvolumeclaim` | Triggers PV reclaim policy, potential data loss |
| `kubectl delete crd <name>` / `customresourcedefinition` | Destroys ALL custom resources of that type cluster-wide |
| `kubectl delete mutatingwebhookconfiguration <name>` | Breaks admission control |
| `kubectl delete validatingwebhookconfiguration <name>` | Breaks admission control |
| `kubectl drain <node>` | Evicts all pods, causes service disruption |
| `kubectl delete <type> --all --all-namespaces` | Deletes ALL resources of that type across the entire cluster |
| `kubectl delete all --all` | Deletes all standard resources in current namespace |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `kubectl apply -f <manifest>` | Normal desired-state application |
| `kubectl create <resource>` | Creates individual resource |
| `kubectl delete pod <name>` | Pod deletion -- pod is recreated by controller |
| `kubectl delete deployment <name>` | Removes single deployment |
| `kubectl delete service <name>` | Removes single service |
| `kubectl delete configmap <name>` | Removes single configmap |
| `kubectl delete secret <name>` | Removes single secret |
| `kubectl delete job <name>` | Removes single job |
| `kubectl delete clusterrole <name>` | RBAC modification, recoverable |
| `kubectl delete clusterrolebinding <name>` | RBAC modification, recoverable |
| `kubectl scale deployment <name> --replicas=N` | Scaling |
| `kubectl rollout restart deployment <name>` | Rolling restart |
| `kubectl rollout undo deployment <name>` | Rollback |
| `kubectl patch <resource>` | Partial update |
| `kubectl edit <resource>` | Interactive edit |
| `kubectl label <resource>` | Metadata modification |
| `kubectl annotate <resource>` | Metadata modification |
| `kubectl cordon <node>` | Marks node unschedulable (no eviction) |
| `kubectl uncordon <node>` | Marks node schedulable |
| `kubectl taint <node>` | Node scheduling preference |
| `kubectl exec <pod> -- <command>` | Runs command in pod |
| `kubectl cp <src> <dest>` | Copies files to/from pod |

### Dangerous flags that escalate context

| Flag | Effect |
|---|---|
| `--all` | With destructive verb: deletes all resources of that type |
| `--all-namespaces` / `-A` | Extends scope to entire cluster |
| `--force` | Bypasses graceful deletion |
| `--grace-period=0` | Immediate forced deletion |
| `--cascade=orphan` | Leaves dependent resources orphaned |

---

## 3. gcloud / gsutil

### Verb vocabulary

gcloud uses `create`, `delete`, `update`, `describe`, `list`, `set`, `get`, `deploy`, `disable`, `enable`, `reset`, `start`, `stop`, `resize`, `ssh`, `scp`.

gcloud uses `delete` -- never `destroy`, `remove`, or `terminate`.

gsutil uses `rm`, `rb`, `cp`, `mv`, `ls`, `cat`, `stat`, `rsync`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `gcloud projects delete <project>` | Entire project and ALL resources within it |
| `gcloud container clusters delete <cluster>` | Entire GKE cluster and workloads |
| `gcloud sql instances delete <instance>` | Database instance and all databases/data |
| `gcloud sql databases delete <db>` | Database and all data within it |
| `gcloud services disable <api>` | Can break all dependent resources silently |
| `gsutil rb gs://<bucket>` | Irreversible bucket removal |
| `gsutil rm -r gs://<bucket>/*` | Recursive deletion of all objects in bucket |
| `gcloud organizations delete <org>` | Entire organization (if exposed) |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `gcloud compute instances delete <instance>` | Single VM deletion |
| `gcloud compute instances create <instance>` | VM creation |
| `gcloud compute instances start/stop <instance>` | Instance lifecycle |
| `gcloud compute instances reset <instance>` | Instance reboot |
| `gcloud compute firewall-rules delete <rule>` | Single firewall rule |
| `gcloud compute firewall-rules create <rule>` | Firewall creation |
| `gcloud compute networks delete <network>` | Single network (fails if in use) |
| `gcloud compute disks delete <disk>` | Single disk |
| `gcloud compute images delete <image>` | Single image |
| `gcloud compute snapshots delete <snapshot>` | Single snapshot |
| `gcloud container node-pools delete <pool>` | Single node pool |
| `gcloud container node-pools create <pool>` | Node pool creation |
| `gcloud iam roles delete <role>` | IAM role deletion |
| `gcloud iam service-accounts delete <sa>` | Service account deletion |
| `gcloud storage rm gs://bucket/object` | Single object deletion |
| `gcloud functions delete <function>` | Single function deletion |
| `gcloud run services delete <service>` | Single Cloud Run service |
| `gcloud dns record-sets delete <record>` | DNS record modification |
| `gcloud deploy releases promote` | Deployment promotion |

---

## 4. AWS CLI

### Verb vocabulary

AWS uses hyphenated subcommands: `create-*`, `delete-*`, `describe-*`, `list-*`, `get-*`, `put-*`, `update-*`, `terminate-*`, `deregister-*`, `detach-*`, `remove-*`, `modify-*`, `start-*`, `stop-*`, `reboot-*`.

AWS uses `delete-*` and `terminate-*` -- never `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `aws ec2 delete-vpc` | VPC and all networking infra |
| `aws ec2 delete-subnet` | Subnet removal, network topology change |
| `aws ec2 delete-internet-gateway` | Breaks internet connectivity for VPC |
| `aws ec2 delete-route-table` | Breaks routing for VPC |
| `aws ec2 delete-route` | Breaks specific routing |
| `aws rds delete-db-instance` | Database instance and data |
| `aws rds delete-db-cluster` | Aurora cluster and all instances |
| `aws dynamodb delete-table` | Table and ALL data permanently |
| `aws s3 rb` | Bucket removal (irreversible) |
| `aws s3api delete-bucket` | Bucket removal (irreversible) |
| `aws elasticache delete-cache-cluster` | Cache cluster and data |
| `aws elasticache delete-replication-group` | Replication group and data |
| `aws eks delete-cluster` | Entire EKS cluster |
| `aws kms schedule-key-deletion` | KMS key -- all encrypted data becomes unrecoverable |
| `aws organizations delete-organization` | Entire AWS Organization |
| `aws route53 delete-hosted-zone` | DNS zone and all records |
| `aws s3 rm --recursive` (entire bucket) | All objects in bucket |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `aws ec2 terminate-instances` | Instance termination (recoverable via AMI/ASG) |
| `aws ec2 delete-key-pair` | SSH key removal |
| `aws ec2 delete-snapshot` | Single snapshot |
| `aws ec2 delete-volume` | Single EBS volume |
| `aws ec2 delete-security-group` | Single security group |
| `aws ec2 delete-network-interface` | Single ENI |
| `aws ec2 run-instances` | Launch instances |
| `aws ec2 create-*` | Resource creation |
| `aws iam delete-user` | Single IAM user |
| `aws iam delete-role` | Single IAM role |
| `aws iam delete-policy` | Single IAM policy |
| `aws iam delete-access-key` | Single access key |
| `aws iam detach-role-policy` | Detach policy from role |
| `aws iam attach-role-policy` | Attach policy to role |
| `aws iam create-*` | IAM creation |
| `aws lambda delete-function` | Single function |
| `aws lambda create-function` | Function creation |
| `aws cloudformation delete-stack` | Stack deletion (follows stack policy) |
| `aws cloudformation create-stack` | Stack creation |
| `aws cloudformation update-stack` | Stack update |
| `aws s3 cp` / `aws s3 sync` | Object operations |
| `aws s3api delete-objects` | Batch object delete (not bucket) |
| `aws s3api put-object` | Object creation |
| `aws sns delete-topic` | Single topic |
| `aws sqs delete-queue` | Single queue |
| `aws dynamodb delete-item` | Single item (not table) |
| `aws rds delete-db-parameter-group` | Parameter group |
| `aws eks delete-nodegroup` | Single node group |
| `aws eks delete-addon` | Single addon |
| `aws backup delete-recovery-point` | Single recovery point |

### Edge cases: should these be DESTRUCTIVE?

| Command | Current | Argument for DESTRUCTIVE |
|---|---|---|
| `aws kms schedule-key-deletion` | Not in blocked list | Renders all encrypted data unrecoverable. Strong candidate for permanent block. |
| `aws route53 delete-hosted-zone` | Not in blocked list | DNS zone loss can cause widespread outage. Strong candidate. |
| `aws s3 rm --recursive` on entire bucket | Caught by verb detector | Could wipe all data. Consider pattern-specific block. |

---

## 5. git

### Verb vocabulary

git uses `add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `cherry-pick`, `reset`, `revert`, `checkout`, `switch`, `restore`, `branch`, `tag`, `stash`, `clean`, `log`, `diff`, `show`, `status`, `blame`, `reflog`.

git does not use `delete` or `destroy` as subcommands -- it uses flags (`-D`, `-d`, `--delete`) and subcommands (`rm`, `clean`).

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `git push --force` / `git push -f` | Rewrites remote history, loses others' commits |
| `git push --force` to `main`/`master` | Especially catastrophic on default branch |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `git commit` | Normal commit |
| `git push` (without force) | Normal push |
| `git push --force-with-lease` | Safe force push (checks remote state) |
| `git merge` | Branch merge |
| `git rebase` | Rebasing |
| `git cherry-pick` | Cherry-picking |
| `git stash drop` / `git stash clear` | Stash management |
| `git branch -d` / `git branch -D` | Branch deletion |
| `git branch -m` / `git branch -M` | Branch rename |
| `git tag -d` | Tag deletion |
| `git reset --hard` | Discards uncommitted changes (dangerous but local-only) |
| `git reset --soft` / `git reset --mixed` | Safe resets |
| `git revert` | Creates new commit to undo |
| `git clean -f` / `git clean -fd` | Removes untracked files (local-only) |
| `git checkout -- <file>` | Discards working tree changes |
| `git restore --staged` / `git restore` | Unstage or discard changes |

### Dangerous flags

| Flag | Escalation |
|---|---|
| `--force` / `-f` (on push) | DESTRUCTIVE -- rewrites remote history |
| `--force-with-lease` (on push) | MUTATIVE -- checks remote state first |
| `-D` (on branch) | Force-deletes unmerged branch |
| `--hard` (on reset) | Discards all uncommitted work |
| `-f` / `-d` (on clean) | Removes untracked files/directories |

---

## 6. Docker

### Verb vocabulary

Docker uses `run`, `build`, `pull`, `push`, `create`, `start`, `stop`, `restart`, `kill`, `rm`, `rmi`, `exec`, `logs`, `inspect`, `ps`, `images`, `tag`, `login`, `logout`, `prune`, `volume`, `network`, `system`, `compose`.

Docker uses `rm`, `rmi`, `prune`, `kill` -- never `delete` or `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `docker system prune -a` / `docker system prune --all` | Removes ALL unused images, containers, networks, build cache |
| `docker system prune --volumes` | Removes ALL unused volumes (data loss) |
| `docker volume prune` | Removes ALL unused volumes |
| `docker image prune -a` | Removes ALL unused images |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `docker rm <container>` | Single container removal |
| `docker rm -f <container>` | Force-stop and remove container |
| `docker rmi <image>` | Single image removal |
| `docker kill <container>` | Stop container immediately |
| `docker stop <container>` | Graceful stop |
| `docker start <container>` | Start container |
| `docker restart <container>` | Restart container |
| `docker run <image>` | Create and start container |
| `docker build` | Build image |
| `docker push <image>` | Push image to registry |
| `docker pull <image>` | Pull image from registry |
| `docker exec <container> <cmd>` | Execute command in container |
| `docker volume rm <volume>` | Single volume removal |
| `docker volume create <volume>` | Volume creation |
| `docker network rm <network>` | Single network removal |
| `docker network create <network>` | Network creation |
| `docker system prune` (without -a/--volumes) | Removes only dangling resources |
| `docker compose up` / `docker compose down` | Compose lifecycle |
| `docker tag <image> <tag>` | Tag image |

### Dangerous flags

| Flag | Escalation |
|---|---|
| `-a` / `--all` (on prune commands) | Extends to ALL unused, not just dangling |
| `--volumes` (on system prune) | Includes volume deletion |
| `-f` / `--force` (on rm/rmi) | Skips confirmation, force-removes |

---

## 7. Helm

### Verb vocabulary

Helm uses `install`, `upgrade`, `uninstall`, `rollback`, `list`, `status`, `template`, `lint`, `show`, `search`, `repo`, `pull`, `push`, `package`, `create`, `env`, `version`, `get`, `history`, `plugin`, `test`.

Helm uses `uninstall` (Helm 3) -- `delete` was Helm 2 syntax. Neither uses `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `helm uninstall <release> --no-hooks` | Bypasses cleanup hooks, risks data loss |

Note: `helm uninstall` by itself is a borderline case. It removes a release and all its resources, but this is a normal operational action. The recommendation is to classify it as **MUTATIVE** because:
- It is the standard way to remove a Helm release
- It respects resource-policy annotations (`helm.sh/resource-policy: keep`)
- It does NOT remove CRDs, PVCs, or namespaces by default
- `--dry-run` is available for preview

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `helm install <release> <chart>` | Install release |
| `helm upgrade <release> <chart>` | Upgrade release |
| `helm uninstall <release>` | Remove release (respects hooks and annotations) |
| `helm rollback <release> <revision>` | Rollback to previous revision |
| `helm repo add <name> <url>` | Add chart repository |
| `helm repo remove <name>` | Remove chart repository |
| `helm repo update` | Update repository index |
| `helm plugin install <plugin>` | Install plugin |
| `helm plugin uninstall <plugin>` | Remove plugin |

---

## 8. Flux

### Verb vocabulary

Flux uses `bootstrap`, `check`, `create`, `delete`, `diff`, `export`, `get`, `install`, `logs`, `reconcile`, `resume`, `suspend`, `trace`, `tree`, `uninstall`, `version`.

Flux uses `delete` and `uninstall` -- never `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `flux uninstall` | Removes ALL Flux components from cluster |
| `flux uninstall --silent` | Same but skips confirmation |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `flux create source git <name>` | Create Git source |
| `flux create kustomization <name>` | Create kustomization |
| `flux create helmrelease <name>` | Create Helm release |
| `flux delete source git <name>` | Delete single source |
| `flux delete kustomization <name>` | Delete single kustomization |
| `flux delete helmrelease <name>` | Delete single Helm release |
| `flux reconcile source git <name>` | Trigger reconciliation |
| `flux reconcile kustomization <name>` | Trigger reconciliation |
| `flux suspend kustomization <name>` | Suspend reconciliation |
| `flux resume kustomization <name>` | Resume reconciliation |
| `flux bootstrap` | Bootstrap Flux to cluster |
| `flux install` | Install Flux components |

---

## 9. gh (GitHub CLI)

### Verb vocabulary

gh uses `create`, `view`, `list`, `close`, `reopen`, `merge`, `delete`, `edit`, `comment`, `review`, `run`, `watch`, `status`, `clone`, `fork`, `sync`, `api`.

gh uses `delete` -- never `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `gh repo delete <owner/repo>` | Entire repository and history |
| `gh repo delete --yes` | Same without confirmation |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `gh pr create` | Create pull request |
| `gh pr merge <number>` | Merge pull request |
| `gh pr close <number>` | Close pull request |
| `gh pr reopen <number>` | Reopen pull request |
| `gh pr comment <number>` | Add comment |
| `gh pr edit <number>` | Edit PR metadata |
| `gh pr review <number>` | Submit review |
| `gh issue create` | Create issue |
| `gh issue close <number>` | Close issue |
| `gh issue reopen <number>` | Reopen issue |
| `gh issue delete <number>` | Delete single issue |
| `gh issue comment <number>` | Add comment |
| `gh issue edit <number>` | Edit issue |
| `gh release create <tag>` | Create release |
| `gh release delete <tag>` | Delete single release |
| `gh release edit <tag>` | Edit release |
| `gh run cancel <run-id>` | Cancel workflow run |
| `gh run rerun <run-id>` | Rerun workflow |
| `gh repo create` | Create repository |
| `gh repo fork` | Fork repository |
| `gh repo clone` | Clone repository |
| `gh label create <name>` | Create label |
| `gh label delete <name>` | Delete label |

---

## 10. glab (GitLab CLI)

### Verb vocabulary

glab uses `create`, `view`, `list`, `close`, `reopen`, `merge`, `delete`, `update`, `note`, `subscribe`, `approve`.

glab uses `delete` -- never `destroy`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `glab repo delete <project>` | Entire repository deletion (if supported) |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `glab mr create` | Create merge request |
| `glab mr merge <number>` | Merge MR |
| `glab mr close <number>` | Close MR |
| `glab mr reopen <number>` | Reopen MR |
| `glab mr approve <number>` | Approve MR |
| `glab mr note <number>` | Add comment |
| `glab mr update <number>` | Update MR metadata |
| `glab issue create` | Create issue |
| `glab issue close <number>` | Close issue |
| `glab issue delete <number>` | Delete single issue |
| `glab issue note <number>` | Add comment |
| `glab ci delete <pipeline-id>` | Delete single pipeline |
| `glab ci run` | Trigger pipeline |
| `glab ci retry <job-id>` | Retry job |
| `glab release create <tag>` | Create release |
| `glab release delete <tag>` | Delete single release |
| `glab runner delete <runner-id>` | Delete single runner |
| `glab label create <name>` | Create label |

---

## 11. npm / pip

### Verb vocabulary

npm uses `install`, `uninstall`, `update`, `publish`, `unpublish`, `deprecate`, `init`, `run`, `test`, `build`, `ci`, `audit`, `pack`, `version`, `view`, `search`, `ls`, `outdated`.

pip uses `install`, `uninstall`, `freeze`, `list`, `show`, `search`, `download`, `check`.

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `npm unpublish <package>` (without version) | Removes entire package from public registry -- breaks all dependents |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `npm install <package>` | Install package |
| `npm uninstall <package>` | Uninstall local package |
| `npm update` | Update packages |
| `npm publish` | Publish to registry |
| `npm unpublish <package>@<version>` | Unpublish specific version (within 72h) |
| `npm deprecate <package>` | Mark package deprecated (preferred over unpublish) |
| `npm version <semver>` | Bump version |
| `npm run <script>` | Run script |
| `npm audit fix` | Fix vulnerabilities |
| `npm audit fix --force` | Force fix (may break dependencies) |
| `npm ci` | Clean install from lockfile |
| `pip install <package>` | Install package |
| `pip uninstall <package>` | Uninstall local package |
| `pip install --upgrade <package>` | Upgrade package |

---

## 12. System Commands

### DESTRUCTIVE (permanently blocked)

| Command / Pattern | Reason |
|---|---|
| `rm -rf /` | Destroys entire filesystem |
| `rm -rf /*` | Same effect |
| `dd if=<device> of=<device>` | Low-level disk overwrite |
| `fdisk <device>` | Disk repartitioning |
| `mkfs` / `mkfs.ext4` / `mkfs.fat` etc. | Formats device, destroys all data |

### MUTATIVE (approvable T3)

| Command / Pattern | Notes |
|---|---|
| `rm <file>` | Single file removal |
| `rm -r <directory>` | Directory removal (if not / or /*) |
| `mv <src> <dest>` | Move/rename |
| `cp <src> <dest>` | Copy |
| `chmod <mode> <file>` | Permission change |
| `chown <owner> <file>` | Ownership change |
| `ln -s <target> <link>` | Create symlink |
| `systemctl start/stop/restart <service>` | Service management |
| `systemctl enable/disable <service>` | Service autostart |

### Dangerous flags

| Flag | Escalation |
|---|---|
| `-rf` / `-fr` | ALWAYS dangerous on rm |
| `--no-preserve-root` | Explicitly bypasses root protection |
| `-f` (on rm) | Force, no confirmation |
| `-r` (on rm, cp, chmod) | Recursive operation |

---

## Cross-CLI Verb Mapping

This table answers "does `destroy` exist outside of terraform?"

| Verb | terraform | kubectl | gcloud | aws | git | docker | helm | flux | gh | glab | npm |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `destroy` | YES | no | no | no | no | no | no | no | no | no | no |
| `delete` | no | YES | YES | YES (`delete-*`) | no | no | no | YES | YES | YES | no |
| `remove` | no | no | no | YES (`remove-*`) | `rm` (file) | `rm` (container) | `repo remove` | no | no | no | no |
| `terminate` | no | no | no | YES (`terminate-*`) | no | no | no | no | no | no | no |
| `uninstall` | no | no | no | no | no | no | YES | YES | no | no | YES |
| `kill` | no | no | no | no | no | YES | no | no | no | no | no |
| `prune` | no | no | no | no | `prune` | YES | no | no | no | no | `prune` |
| `purge` | no | no | no | no | no | no | no | no | no | no | no |
| `unpublish` | no | no | no | no | no | no | no | no | no | no | YES |
| `drain` | no | YES | no | no | no | no | no | no | no | no | no |
| `push --force` | no | no | no | no | YES | no | no | no | no | no | no |
| `disable` | no | no | YES | no | no | no | no | no | no | no | no |
| `rb` | no | no | no | YES (s3) | no | no | no | no | no | no | no |

---

## Context-Dependent Classification

Commands where the same verb is DESTRUCTIVE in one context and MUTATIVE in another.

| Command | Context: DESTRUCTIVE | Context: MUTATIVE |
|---|---|---|
| `kubectl delete` | + `namespace`, `node`, `pv`, `pvc`, `crd`, `webhookconfig` | + `pod`, `deployment`, `service`, `configmap`, `job` |
| `kubectl delete` | + `--all --all-namespaces` | + single named resource |
| `docker prune` | + `system prune -a`, `volume prune` | + `system prune` (dangling only) |
| `docker rm` | N/A (always mutative per-container) | Single container |
| `terraform destroy` | Without `-target` (whole state) | With `-target=<resource>` |
| `git push` | + `--force` / `-f` | Without force, or with `--force-with-lease` |
| `git reset` | + `--hard` (loses uncommitted) | + `--soft` / `--mixed` |
| `aws s3 rm` | + `--recursive` on bucket root | Single object |
| `gsutil rm` | + `-r` (recursive) | Single object |
| `npm unpublish` | Entire package (no version) | Specific `@version` |
| `flux uninstall` | Always destructive (removes Flux) | N/A |
| `helm uninstall` | + `--no-hooks` (skip cleanup) | Normal (respects hooks/annotations) |

---

## Recommendations for blocked_commands.py

### Commands to ADD to permanent block list

Based on this research, the following commands are currently missing from `blocked_commands.py` but should be permanently blocked:

1. **AWS**
   - `aws kms schedule-key-deletion` -- encrypted data becomes permanently unrecoverable
   - `aws route53 delete-hosted-zone` -- DNS zone loss causes widespread outage
   - `aws organizations delete-organization` -- entire org destruction

2. **Docker**
   - `docker system prune -a` / `docker system prune --all` -- wipes all unused images/containers
   - `docker system prune --volumes` -- wipes all unused volumes
   - `docker volume prune` -- wipes all unused volumes

3. **Flux**
   - `flux uninstall` -- removes all Flux components from cluster

4. **GitHub CLI**
   - `gh repo delete` -- entire repository deletion

5. **Terraform**
   - `terraform destroy` (without `-target`) -- entire state destruction
   - `terragrunt run-all destroy` -- recursive destruction

6. **npm**
   - `npm unpublish` (without `@version`) -- removes entire package from registry

### Commands to KEEP as approvable T3 (no change needed)

All commands currently in `dangerous_verbs.py` DESTRUCTIVE and MUTATIVE categories are correctly classified. The verb taxonomy is sound.

### Dangerous flag combinations to consider

The `dangerous_verbs.py` flag scanning is solid. Consider adding:
- `--all-namespaces` / `-A` for kubectl as a DESTRUCTIVE escalator when combined with `delete`
- `--no-hooks` for helm as a DESTRUCTIVE escalator when combined with `uninstall`
- `--volumes` for docker as a DESTRUCTIVE escalator when combined with `prune`
- `--recursive` for `aws s3 rm` scope escalation
