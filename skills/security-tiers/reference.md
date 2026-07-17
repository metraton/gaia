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

- `git branch` -- T0 for listing (no args or `--list`); T3 only with `-D` (force-delete), `-M` (force-rename), or the long-form `--delete`. The lowercase `-d` (delete -- git refuses on unmerged branches) and `-m` (plain rename) are intentionally left ungated: they are the safe counterparts of the same operations, and gating them would add a consent prompt for something git itself already refuses to do unsafely. `--move` (git's long form of `-m`, a plain rename) IS a recognized git flag but, like `-m`, is intentionally left ungated -- it is the safe counterpart of `-M`. Known asymmetry: `--delete` (long form of `-d`) IS gated even though it performs the identical safe deletion `-d` performs -- see `SKILL.md` for the full rationale and this documented (not fixed) inconsistency.
- Short force flag `-f` on git -- T3 across subcommands. `git` is in `F_FLAG_MEANS_FORCE` (`mutative_verbs.py`), mirroring the long-form `--force`, so `git mv -f` (force-overwrite the destination), `git checkout -f` (discard uncommitted changes), `git branch -f`, and `git add -f` all escalate to T3. Previously these slipped through as T0 because their subcommands are in `GIT_LOCAL_SAFE_SUBCOMMANDS` and `-f` was not collected by `_scan_dangerous_flags` for git.

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
- **`.claude/` writes via Bash:** the `.claude/` tree is protected on BOTH write surfaces. `_is_protected()` (`hooks/adapters/claude_code.py`) guards the Write/Edit `file_path`; `protected_path_guard.py` (wired into `bash_validator.validate()`) guards Bash `command` strings. The Bash guard CATEGORICALLY denies (exit 2, not approvable) any write-capable command whose target resolves into the protected `.claude/` tree (the hooks dir, or `settings.json`/`settings.local.json` anywhere under `.claude/`) -- git working-tree writers (`git mv`/`checkout`/`restore`/`stash`), filesystem writers (`mv`/`cp`/`tee`/`sed -i`), and redirects. This closes the hole where `git mv payload.py .claude/hooks/pre_tool_use.py` (short-circuited to T0 via `GIT_LOCAL_SAFE_SUBCOMMANDS`) could overwrite hook code with no consent. Reads of `.claude/` (`git diff`, `cat`, `grep`) are not write-capable and pass through.
