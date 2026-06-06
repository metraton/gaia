# Security Tiers -- Reference

Read on-demand by infrastructure agents. Not injected automatically.

## Cloud-Specific Classification Examples

### T0 -- Read-Only

- `kubectl get pods`, `kubectl get svc`, `kubectl describe node`
- `terraform show`, `terraform output`
- `gcloud describe`, `gcloud sql instances describe`, `gcloud container clusters list`
- `helm status`, `helm list`
- `flux get kustomizations`, `flux get sources`

### T1 -- Validation

- `terraform validate`
- `helm lint`
- `tflint`
- `kustomize build`

### T2 -- Simulation

- `terraform plan` / `terragrunt plan`
- `kubectl diff -f manifest.yaml`
- `helm upgrade --dry-run`
- `kubectl apply --dry-run=server`

### Conditional (T0 or T3 depending on flags)

- `git branch` -- T0 for listing (no args or `--list`), T3 with `-D`, `-d`, `-m`, `-M`, `--delete`, `--move`

### T3 -- Realization

- `terraform apply` / `terragrunt apply`
- `kubectl apply -f manifest.yaml`
- `helm upgrade` (without `--dry-run`)
- `flux reconcile` (write operations)
- `git push` (any branch) -- mutates remote state

Note: `git commit` and `git add` are **not** T3. They are local-only (working tree + local refs, never remote), classified safe by elimination via `GIT_LOCAL_SAFE_SUBCOMMANDS` in `mutative_verbs.py`. Only `git push` reaches remote state.

## Edge Cases

- **Compound subcommands that look mutative:** verbs like `merge-base` split on the hyphen to `merge`, which is a mutative verb -- but `git merge-base` is read-only. The detector in `mutative_verbs.py` carries an allow-list of read-only compound subcommands so they are not falsely flagged T3.
- **Message bodies after `-m`:** text after a `-m` flag (commit messages, descriptions) can contain mutative-looking words; the detector stops scanning verbs once it reaches the message body so the content does not leak a false T3.
- **`git reset --hard`:** routed through the T3 approval flow (mutative, approvable), not permanently blocked -- the user can confirm or decline interactively. See `destructive-commands-reference.md` for the full destructive-vs-mutative matrix per CLI.
