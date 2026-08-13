#!/usr/bin/env python3
"""Executable truth table for the live command classifier.

Every case is a LITERAL command string fed to the live classifier
(``detect_mutative_command`` + ``classify_command_tier``), asserting the PAIR
``(is_mutative, tier)`` rather than either half alone: the two are decided by
different code paths, and a change that flips one without the other is exactly
the kind of drift a single-value assertion hides.

Two families share ONE table on purpose:

- ``OPEN`` -- forms that the classifier does NOT gate today. They are recorded
  with their CURRENT verdict, not the desired one. The table is a baseline, so
  closing one of these gaps must show up here as a deliberate edit to the
  expected verdict; a gap that closes silently is indistinguishable from a
  regression.
- ``GATED`` / ``FREE`` -- controls. Commands that already resolve to T3, and
  read-only commands that already resolve free. They live in the same table so
  that OVERCORRECTING breaks it just as loudly as undercorrecting: widening a
  rule until a read form starts demanding consent turns a control red.

The table is the shared harness: work that changes a verdict extends this
table instead of standing up its own assertions somewhere else.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

OPEN = "open"
GATED = "gated"
FREE = "free"

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# (case_id, family, command, expected_is_mutative, expected_tier)
CLASSIFIER_TRUTH_TABLE = [
    # ---- CLOSED: granting capability is now gated exactly like removing it --
    # These four were recorded OPEN (False, T0): granting was gated on no
    # surface, because `add-iam-policy-binding` hyphen-splits onto `add`, which
    # is kept out of MUTATIVE_VERBS so `git add` stays free. They are anchored
    # per surface in COMMAND_PATH_MUTATIVE_UPGRADES and are now controls that
    # must stay T3. Closing the gap is recorded HERE, as a deliberate edit to
    # the expected verdict, because a gap that closes silently reads exactly
    # like a regression.
    (
        "iam-grant-project",
        GATED,
        "gcloud projects add-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
        True,
        T3,
    ),
    (
        "iam-grant-bucket",
        GATED,
        "gcloud storage buckets add-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
        True,
        T3,
    ),
    (
        "iam-grant-secret",
        GATED,
        "gcloud secrets add-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
        True,
        T3,
    ),
    (
        "iam-grant-service-account",
        GATED,
        "gcloud iam service-accounts add-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountTokenCreator",
        True,
        T3,
    ),
    # ---- CLOSED: removal on a three-token path was open too ----
    # Measured while closing the grants, and not in the corpus before: the
    # hyphen split that gates `remove-iam-policy-binding` only runs at
    # semantic_index <= 2, so on `storage buckets` and `iam service-accounts`
    # the token sits too deep and never reached `remove`. Removal was gated on
    # the two-token surfaces alone -- which is why the sibling control below
    # (`control-iam-revoke`, a `projects` form) passed while these did not
    # exist to fail.
    (
        "iam-revoke-bucket",
        GATED,
        "gcloud storage buckets remove-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
        True,
        T3,
    ),
    (
        "iam-revoke-service-account",
        GATED,
        "gcloud iam service-accounts remove-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountTokenCreator",
        True,
        T3,
    ),
    # ---- FREE: reads of the four IAM surfaces must not start paying a toll --
    ("read-iam-project-policy", FREE, "gcloud projects get-iam-policy my-proj", False, T0),
    ("read-iam-bucket", FREE, "gcloud storage buckets describe gs://my-bucket", False, T0),
    ("read-iam-secret", FREE, "gcloud secrets describe my-secret", False, T0),
    (
        "read-iam-service-account",
        FREE,
        "gcloud iam service-accounts describe sa@proj.iam.gserviceaccount.com",
        False,
        T0,
    ),
    # ---- CLOSED: a read-only noun no longer decides before the verb is read --
    # These three were recorded OPEN (False, T0). `config` is a READ_ONLY_VERBS
    # entry and the verb scan returned on it, so `set` was never read; the two
    # gcloud forms redirect every later command onto another project and another
    # identity. Anchored per command path -- the noun stays in the read-only
    # table, and its read forms below stay free.
    #
    # `git config` reached T0 by a different route and took a different repair:
    # it carries no verb at all, so withdrawing the noun leaves it READ_ONLY by
    # elimination. Its write form is the absence of a read flag plus a key AND a
    # value, which an anchor cannot express, so a discriminator decides it the
    # way `git tag` is decided.
    (
        "redirect-project",
        GATED,
        "gcloud config set project other-project",
        True,
        T3,
    ),
    (
        "redirect-account",
        GATED,
        "gcloud config set account someone@else.com",
        True,
        T3,
    ),
    ("git-config-write", GATED, "git config user.email someone@else.com", True, T3),
    # ---- FREE: the reads of that same noun are the volume, and stay free ----
    ("read-gcloud-config-list", FREE, "gcloud config list", False, T0),
    ("read-git-config-list", FREE, "git config --list", False, T0),
    ("read-git-config-get", FREE, "git config --get user.email", False, T0),
    # ---- OPEN: redirection the read-only noun does NOT shadow ----
    # Recorded rather than closed: `activate` and `use` are absent from the verb
    # taxonomy, so the short-circuit is not what holds these at T0 and removing
    # it would not move them. Both redirect as hard as the three forms above --
    # one switches project and account together, the other switches the cluster
    # every later kubectl reaches -- and each needs a decision of its own.
    (
        "configurations-activate",
        OPEN,
        "gcloud config configurations activate other",
        False,
        T0,
    ),
    ("context-switch", OPEN, "kubectl config use-context prod", False, T0),
    # ---- CLOSED: indirect trigger and live workload ----
    # These four were recorded OPEN (False, T0). None carries a verb in
    # MUTATIVE_VERBS -- `run` is deliberately excluded ("safe by elimination"),
    # and `rerun`/`cancel` were never in the taxonomy -- so all four fell
    # through to Step 4 and classified READ_ONLY by elimination despite
    # provoking a remote execution or bringing a live workload to life.
    # `workflow-cancel` was not in the original brief; it surfaced while
    # closing the other three as the same gap reached from the opposite
    # direction (reaching INTO a running execution instead of starting one).
    # Anchored per (family, subcommand)/(family, flag) in
    # COMMAND_PATH_MUTATIVE_UPGRADES, never by widening `run` globally.
    ("workflow-trigger", GATED, "gh workflow run deploy.yml --ref main", True, T3),
    ("workflow-retrigger", GATED, "gh run rerun 123456", True, T3),
    ("workflow-cancel", GATED, "gh run cancel 123456", True, T3),
    (
        "workload-create",
        GATED,
        "kubectl run debug-pod --image=alpine:3.20 -- sleep 3600",
        True,
        T3,
    ),
    # ---- FREE: reads of the same flows/runs/cluster stay free ----
    ("read-gh-workflow-list", FREE, "gh workflow list", False, T0),
    ("read-gh-workflow-view", FREE, "gh workflow view deploy.yml", False, T0),
    ("read-gh-run-view", FREE, "gh run view 123456", False, T0),
    # ---- CLOSED: state, destination and direct write ----
    # These four were recorded OPEN (False, T0). None of the three verbs
    # behind them sits in MUTATIVE_VERBS -- `init` names no lifecycle action
    # the taxonomy tracks, `add` is deliberately excluded (git add stays
    # free), and `tee` carries no verb at all -- so every one fell through to
    # Step 4 (or, for tee, every step) and classified READ_ONLY by
    # elimination. `state-reconfigure` and the three `terragrunt` rows below
    # were not in the original OPEN pair: surfaced while anchoring `-upgrade`
    # /`-migrate-state` as the third flag sharing the same mutating shape, and
    # as the sibling CLI this repository observed alongside terraform.
    # Anchored per (family, subcommand)/(family, flag) in
    # COMMAND_PATH_MUTATIVE_UPGRADES; the direct write is anchored by a
    # sensitive-path predicate on `tee` itself, never by widening a verb or
    # prohibiting the tool.
    ("state-upgrade", GATED, "terraform init -upgrade", True, T3),
    ("state-migrate", GATED, "terraform init -migrate-state", True, T3),
    ("state-reconfigure", GATED, "terraform init -reconfigure", True, T3),
    ("state-upgrade-terragrunt", GATED, "terragrunt init -upgrade", True, T3),
    (
        "state-migrate-terragrunt",
        GATED,
        "terragrunt init -migrate-state",
        True,
        T3,
    ),
    (
        "state-reconfigure-terragrunt",
        GATED,
        "terragrunt init -reconfigure",
        True,
        T3,
    ),
    (
        "remote-add",
        GATED,
        "git remote add upstream git@github.com:other/repo.git",
        True,
        T3,
    ),
    (
        "sensitive-write",
        GATED,
        "tee /home/jorge/ws/me/gaia/hooks/pre_tool_use.py",
        True,
        T3,
    ),
    # ---- FREE: bare init on the sibling CLI stays free too ----
    ("read-terragrunt-init", FREE, "terragrunt init", False, T0),
    # ---- FREE: --prune on `git fetch` no longer taxes local ref cleanup ----
    # Previously GATED (True, T3): `--prune` sat in DANGEROUS_FLAGS as an
    # ALWAYS entry, so it escalated `fetch` -- a read-only verb -- to T3 on
    # EVERY CLI that carried the exact flag, regardless of what the flag
    # actually does there. On `git fetch` it only removes LOCAL
    # remote-tracking refs whose branch is already gone on the remote --
    # bookkeeping the caller has already lost, not a destruction of anything
    # they own. Measured: 15 approvals. Recorded here as the deliberate edit
    # the table's own convention calls for -- moving a row's expected verdict
    # is how a closed gap stays visible instead of reading as though nobody
    # measured it. Anchored to the exact (git, fetch, --prune) path in
    # COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS -- see the GATED controls
    # immediately below, which the SAME table leaves untouched on purpose.
    ("prune-git-fetch", FREE, "git fetch --prune", False, T0),
    # ---- GATED: the identical flag, where it destroys real state ----
    # These did not move. They pin the property this fix exists to protect:
    # the flag scales where it actually destroys, not where the ALWAYS entry
    # merely says so. `kubectl apply --prune` deletes live cluster resources
    # not present in the applied set -- gated independently of the ALWAYS
    # mechanism, since `apply` is itself a MUTATIVE_VERBS verb. The two
    # `state list --prune` forms are read-only verbs relying on the SAME
    # ALWAYS mechanism the fetch exemption narrows, on the two infrastructure
    # CLIs this repository observes -- narrowing the flag's reach to one exact
    # git path must not narrow it here. The compound git form proves the
    # narrowing is scoped to the FLAG TOKEN, not to the command: a second,
    # non-exempted ALWAYS flag on the identical exempted path still escalates.
    (
        "control-prune-kubectl-apply",
        GATED,
        "kubectl apply -f k8s/ --prune -l app=guestbook",
        True,
        T3,
    ),
    (
        "control-prune-terraform-state",
        GATED,
        "terraform state list --prune",
        True,
        T3,
    ),
    (
        "control-prune-terragrunt-state",
        GATED,
        "terragrunt state list --prune",
        True,
        T3,
    ),
    (
        "control-prune-fetch-plus-force",
        GATED,
        "git fetch --prune --force",
        True,
        T3,
    ),
    # ---- FREE: the tee anchor's sibling reads, which had no row at all ----
    # The sensitive-write row above landed without a single free counterpart,
    # so nothing in this table could tell a path predicate that discriminates
    # from one that simply gates the tool. These are the forms that must stay
    # free for `tee` to be anchored by WHERE it writes rather than by its name.
    (
        "write-tee-working-tree",
        FREE,
        "tee /home/jorge/ws/me/gaia/notes.txt",
        False,
        T0,
    ),
    (
        "write-tee-append-working-tree",
        FREE,
        "tee -a /home/jorge/ws/me/gaia/README.md",
        False,
        T0,
    ),
    (
        "write-tee-scratch",
        FREE,
        "tee /home/jorge/.gaia/scratch/out.txt",
        False,
        T0,
    ),
    # ---- FREE: the passthrough that writes nothing costs nothing again ----
    # Recorded OPEN (True, T3) as a measured regression, now closed -- the edit
    # to this row is the deliberate record the OPEN convention asks for.
    #
    # `tee` had been anchored by adding it to COMMAND_ALIASES -- a GLOBAL base
    # command table -- and subtracting the safe cases back out with a path
    # predicate. That subtraction was skipped when there was NO file argument,
    # so the bare form fell through to T3. But bare `tee` is not an incomplete
    # write; it is the complete stdin-to-stdout passthrough of `cmd | tee`,
    # which writes nothing at all, and the validator splits a pipeline on its
    # operators and classifies each component -- so this exact string denied
    # the whole pipeline.
    #
    # The repair withdraws the global-table entry entirely and decides `tee`
    # with a discriminator that stands aside by default and escalates only on
    # the destination, the way `git config` and `git tag` are decided. The
    # sensitive-write row above is unchanged, which is what makes this a
    # narrowing rather than a retreat.
    ("write-tee-passthrough-no-file", FREE, "tee", False, T0),
    ("write-tee-passthrough-append-flag", FREE, "tee -a", False, T0),
    ("write-tee-privileged-path", GATED, "tee /etc/hosts", True, T3),
    # ---- GATED controls: already T3, must stay T3 ----
    ("control-pr-merge", GATED, "gh pr merge 42 --squash", True, T3),
    (
        "control-api-write",
        GATED,
        "gh api -X POST /repos/o/r/issues -f title=x",
        True,
        T3,
    ),
    ("control-release-create", GATED, "gh release create v1.2.3", True, T3),
    (
        "control-secret-create",
        GATED,
        "gcloud secrets create my-secret --data-file=-",
        True,
        T3,
    ),
    (
        "control-rollout-restart",
        GATED,
        "kubectl rollout restart deployment/api",
        True,
        T3,
    ),
    (
        "control-iam-revoke",
        GATED,
        "gcloud projects remove-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
        True,
        T3,
    ),
    ("control-push-force", GATED, "git push --force origin main", True, T3),
    ("control-kubectl-delete", GATED, "kubectl delete pod my-pod", True, T3),
    ("control-terraform-apply", GATED, "terraform apply -auto-approve", True, T3),
    ("control-rm-recursive", GATED, "rm -rf /home/jorge/ws/me/gaia/hooks", True, T3),
    # ---- FREE controls: reads that must not start paying a toll ----
    ("read-gcloud-config", FREE, "gcloud config get-value project", False, T0),
    ("read-gh-run-list", FREE, "gh run list --limit 5", False, T0),
    ("read-kubectl-get", FREE, "kubectl get pods -o json", False, T0),
    ("read-terraform-init", FREE, "terraform init", False, T0),
    ("read-git-remote", FREE, "git remote -v", False, T0),
    # ---- M4 (task 12): two of the four remaining friction groups measured
    # by most volume, plus the one required sibling that only these two
    # naturally pair with here (the npm-run-body and script-file-content
    # groups are cwd-dependent and live in their own BashValidator classes in
    # test_mutative_verbs.py -- see the classes carrying "friction_residual"
    # in their method names). Case ids are prefixed "friction_residual_" on
    # purpose: this file's own oracle test has no marker in its name, so
    # `pytest -k friction_residual` selects these PARAMETRIZED INSTANCES by
    # id rather than the whole table, while the rows themselves still live in
    # the SAME shared table task 1 built -- extending it, not forking it.
    #
    # `terraform`/`terragrunt init` was already free for a bare invocation
    # (see "read-terraform-init" above) and for the flag-bearing form that
    # merely disables the backend: `-backend=false`/`-input=false` carry no
    # verb MUTATIVE_VERBS tracks and match none of the three flags anchored
    # in COMMAND_PATH_MUTATIVE_UPGRADES (`-upgrade`/`-migrate-state`/
    # `-reconfigure`, landed in task 8) -- so this form was ALREADY T0 by the
    # same "safe by elimination + flag-scoped anchor" mechanism task 8 built,
    # with no further code change needed here. Recorded as a deliberate row
    # (not left silently free) so the SAME selected run also carries its
    # sibling: a state-migrating init on the exact same command shape stays
    # T3, proving the anchor discriminates the flag rather than "init" as a
    # whole.
    (
        "friction_residual_terraform_init_no_backend_free",
        FREE,
        "terraform init -backend=false -input=false",
        False,
        T0,
    ),
    (
        "friction_residual_terraform_init_migrate_state_sibling",
        GATED,
        "terraform init -input=false -migrate-state",
        True,
        T3,
    ),
    # `pytest -rf` (pytest's OWN report-selection flag: show extra summary
    # for Failed tests) used to read exactly like `rm -rf` -- the packed
    # short-flag scan treated any cli carrying both letters in one bundle as
    # dangerous, agnostic of what those letters mean on THAT cli. Fixed by
    # scoping the compound r+f branch (and dropping the unconditional exact
    # "-rf"/"-fr" ALWAYS entries) to cli membership in BOTH
    # R_FLAG_MEANS_RECURSIVE_DELETE and F_FLAG_MEANS_FORCE -- `rm -rf` and
    # `cp -rf` (below, as controls) are unaffected; `pytest` is in neither
    # set. This is the exact command this project's own testing discipline
    # requires (security-tiers: no ad-hoc inline probes to verify the
    # classifier -- pytest is the sanctioned method), so gating it taxed the
    # one tool mandated to check it.
    (
        "friction_residual_pytest_packed_report_flag_free",
        FREE,
        "python3 -m pytest tests/hooks/modules/security/ -q -rf",
        False,
        T0,
    ),
    # A simulation-shaped flag on the invocation line cannot, BY ITSELF,
    # absolve a script whose content mutates. `scripts/release-prepare.mjs` is
    # this repository's own release-notes writer: an unconditional
    # `fs.writeFileSync` that never reads `process.argv`, so no flag changes
    # what it does. It was freed by appending `--dry-run`, and freed again by
    # `--report-duplicates` -- a flag that belongs to `gaia workspace merge`
    # and means nothing here -- because the simulation override ran before the
    # content was read. Both spellings are rows so the property is pinned
    # against the plausible flag AND the unrelated one; the exemption is now
    # granted inside the script-file lane only when the script's own
    # executable text names the flag (see the friction_residual class in
    # test_mutative_verbs.py, where the freed sibling and its counterfactual
    # live with hermetic fixtures).
    (
        "friction_residual_sim_flag_alone_cannot_absolve_mutating_script",
        GATED,
        "node /home/jorge/ws/me/gaia/scripts/release-prepare.mjs --dry-run",
        True,
        T3,
    ),
    (
        "friction_residual_unrelated_sim_flag_cannot_absolve_mutating_script",
        GATED,
        "node /home/jorge/ws/me/gaia/scripts/release-prepare.mjs "
        "--report-duplicates",
        True,
        T3,
    ),
    # ---- Controls: the packed r+f bundle still fires where it is genuinely
    # destructive -- unaffected by scoping the heuristic to cli membership.
    (
        "friction_residual_packed_rf_control_rm",
        GATED,
        "rm -rf /home/jorge/.gaia/scratch/friction-residual-probe",
        True,
        T3,
    ),
    (
        "friction_residual_packed_rf_control_cp",
        GATED,
        "cp -rf /home/jorge/.gaia/scratch/src /home/jorge/.gaia/scratch/dst",
        True,
        T3,
    ),
    # ---- CLOSED: a grouping character glued to the base command -------------
    # Every one of these was recorded FREE (False, T0): the classifier
    # identifies a command by its FIRST WORD, and `(rm` / `$(cp` / `` `rm ``
    # match nothing in any table, so the whole family passed as safe by
    # elimination -- including the ``^``-anchored permanent-deny floor, which
    # has no approval path to fall back on. They are gated now by ONE shared
    # normalization (`shell_grouping.strip_grouping_wrappers`) applied before
    # first-token extraction in command_semantics, blocked_commands,
    # protected_path_guard and the tier classifier's own deny-pattern scan.
    #
    # The standard each row is held to is PARITY with its naked spelling, not
    # merely "gated": a wrapped form must classify exactly as the same command
    # written without the wrapper. `mkfs` is the row that shows why the tier
    # value and is_mutative are asserted separately -- it reaches T3 through
    # the deny floor with is_mutative False, exactly as bare `mkfs.ext4
    # /dev/sda1` does, because the disk-operation regexes have no
    # mutative-verb backup.
    ("grouping_paren_glued_rm_root", GATED, "(rm -rf /)", True, T3),
    ("grouping_paren_glued_rm_tmp", GATED, "(rm -rf /tmp/test)", True, T3),
    (
        "grouping_paren_glued_dd_device",
        GATED,
        "(dd if=/dev/zero of=/dev/sda)",
        True,
        T3,
    ),
    ("grouping_paren_glued_mkfs", GATED, "(mkfs.ext4 /dev/sda1)", False, T3),
    ("grouping_paren_glued_chmod_root", GATED, "(chmod -R 777 /)", True, T3),
    ("grouping_paren_glued_terraform_apply", GATED, "(terraform apply)", True, T3),
    (
        "grouping_paren_glued_terraform_destroy",
        GATED,
        "(terraform destroy)",
        True,
        T3,
    ),
    ("grouping_paren_glued_kubectl_delete", GATED, "(kubectl delete)", True, T3),
    ("grouping_paren_glued_git_push", GATED, "(git push)", True, T3),
    ("grouping_cmdsub_rm_tmp", GATED, "$(rm -rf /tmp/test)", True, T3),
    ("grouping_backtick_rm_tmp", GATED, "`rm -rf /tmp/test`", True, T3),
    ("grouping_paren_spaced_rm_tmp", GATED, "( rm -rf /tmp/test )", True, T3),
    ("grouping_paren_nested_rm_root", GATED, "((rm -rf /))", True, T3),
    (
        "grouping_cmdsub_cp_into_hooks",
        GATED,
        "$(cp payload.py .claude/hooks/pre_tool_use.py)",
        True,
        T3,
    ),
    # ---- Controls: the SAME characters, used legitimately, stay free --------
    # Grouping and substitution are ordinary, frequent shell syntax. A
    # normalization that closed the family above by taxing these would have
    # traded the hole for the friction that costs more: consent demanded for
    # reading. A read-only subshell and a read-only substitution used as
    # another read's argument are the two shapes that appear constantly.
    ("grouping_free_readonly_subshell", FREE, "(cd /tmp && ls)", False, T0),
    (
        "grouping_free_readonly_subshell_repo",
        FREE,
        "(cd /home/jorge/ws/me/gaia && ls -la)",
        False,
        T0,
    ),
    ("grouping_free_cmdsub_as_arg", FREE, "ls -la $(pwd)", False, T0),
    (
        "grouping_free_cmdsub_git_rev_parse",
        FREE,
        "echo $(git rev-parse HEAD)",
        False,
        T0,
    ),
    ("grouping_free_backtick_as_arg", FREE, "ls -la `pwd`", False, T0),
    (
        "grouping_free_find_escaped_parens",
        FREE,
        r"find /tmp -type f \( -name a -o -name b \)",
        False,
        T0,
    ),
]

# ---------------------------------------------------------------------------
# No-overcorrection census
# ---------------------------------------------------------------------------
# Closing gaps is paid for in overcorrection: a rule widened until reading
# costs consent. This census is the control on the anchors landed for M2, and
# it is deliberately built out of the two directions that can each hide the
# other's failure.
#
# CONTROL_GATED -- the six operations that were already gated correctly before
# any of this work and must still be gated after it. If an anchor is rewritten
# in a way that drops one of these, the repair has traded a false negative for
# a worse one.
#
# MENTION_FREE -- the subtle half. A mutative command QUOTED INSIDE another
# command's argument is being written down, not run, and must not escalate.
# One spelling proves nothing here, because each quoting shape reaches the
# classifier differently, so the same mutation is mentioned several ways.
#
# MENTION_OPEN -- recorded, not desired. A mention that DOES escalate today.
# It is kept with its current (wrong) verdict for the reason the OPEN family
# exists at the top of this file: a gap recorded as a passing expectation stays
# visible, and closing it costs a deliberate edit here, whereas a gap left out
# of the table is indistinguishable from one nobody found.

CONTROL_GATED = "control_gated"
MENTION_FREE = "mention_free"
MENTION_OPEN = "mention_open"

# (case_id, kind, command, expected_gated)
NO_OVERCORRECTION_CENSUS = [
    # ---- The six that must keep costing consent ----
    ("census-pr-merge", CONTROL_GATED, "gh pr merge 42 --squash", True),
    (
        "census-api-write-method",
        CONTROL_GATED,
        "gh api -X POST /repos/o/r/issues -f title=x",
        True,
    ),
    ("census-release-create", CONTROL_GATED, "gh release create v1.2.3", True),
    (
        "census-secret-add-gh",
        CONTROL_GATED,
        "gh secret set MY_SECRET --body hunter2",
        True,
    ),
    (
        "census-secret-add-gcloud",
        CONTROL_GATED,
        "gcloud secrets create my-secret --data-file=-",
        True,
    ),
    (
        "census-rollout-restart",
        CONTROL_GATED,
        "kubectl rollout restart deployment/api",
        True,
    ),
    (
        "census-iam-capacity-removal",
        CONTROL_GATED,
        "gcloud projects remove-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
        True,
    ),
    # ---- Mention, not use: quoted into another command's argument ----
    # Each row quotes a command that IS gated on its own (every one of them
    # appears above or in the truth table), so a row turning red means the
    # quoting shape stopped protecting it, not that the inner command is safe.
    (
        "mention-double-quoted",
        MENTION_FREE,
        'gaia contract add evidence_report.key_outputs "ran gh pr merge 42 --squash"',
        False,
    ),
    (
        "mention-single-quoted",
        MENTION_FREE,
        "gaia contract add evidence_report.key_outputs 'ran gh pr merge 42 --squash'",
        False,
    ),
    (
        "mention-kubectl-delete",
        MENTION_FREE,
        'gaia contract add evidence_report.open_gaps '
        '"kubectl delete pod my-pod was never run"',
        False,
    ),
    (
        "mention-terraform-apply",
        MENTION_FREE,
        'gaia contract set evidence_report.verification.command '
        '"terraform apply -auto-approve"',
        False,
    ),
    (
        "mention-iam-grant",
        MENTION_FREE,
        'gaia contract add evidence_report.key_outputs '
        '"gcloud projects add-iam-policy-binding my-proj '
        '--member=user:a@b.c --role=roles/owner"',
        False,
    ),
    (
        "mention-rm-recursive",
        MENTION_FREE,
        'gaia contract add evidence_report.open_gaps "never ran rm -rf /etc"',
        False,
    ),
    (
        "mention-in-memory-body",
        MENTION_FREE,
        'gaia memory save --slug x --body "gh release create v1.2.3 was the trigger"',
        False,
    ),
    (
        "mention-prose-carrying-verbs",
        MENTION_FREE,
        'gaia contract add evidence_report.key_outputs '
        '"the apply step creates the secret and restarts the deployment"',
        False,
    ),
    (
        "mention-workflow-run",
        MENTION_FREE,
        'gaia contract add evidence_report.key_outputs '
        '"gh workflow run deploy.yml --ref main is the trigger"',
        False,
    ),
    # ---- Mention, not use: the mentioned command carries a GROUPING wrapper --
    # The sharpest edge of the wrapper normalization. It exists to make a
    # grouping character glued to a command stop hiding that command -- and a
    # normalization applied without care about POSITION would do exactly that
    # to prose, turning every quoted `(rm -rf ...)` in a report into a real
    # delete. Position is what separates them: the normalization only ever
    # looks at the first and last position of a component, and a mention is
    # neither.
    (
        "mention-grouped-rm",
        MENTION_FREE,
        "gaia contract add evidence_report.open_gaps "
        '"the form (rm -rf /tmp/test) passed free"',
        False,
    ),
    (
        "mention-grouped-kubectl-delete",
        MENTION_FREE,
        "gaia contract add evidence_report.key_outputs "
        '"(kubectl delete pod my-pod) was never run"',
        False,
    ),
    (
        "mention-grouped-hooks-overwrite",
        MENTION_FREE,
        "gaia contract add evidence_report.key_outputs "
        '"(cp payload.py .claude/hooks/pre_tool_use.py) was never run"',
        False,
    ),
    # The measured false positive this repository already carries, kept as a
    # control on the wrapper work: a quoted search term read as syntax.
    (
        "mention-grep-quoted-search-term",
        MENTION_FREE,
        'grep -rn "SessionStart" /home/jorge/ws/me/.claude/settings.local.json',
        False,
    ),
    (
        "mention-grep-quoted-dangerous-form",
        MENTION_FREE,
        'grep -rn "(rm -rf /)" /home/jorge/ws/me/gaia/README.md',
        False,
    ),
    # ---- OPEN: a mention that escalates today ----
    # Measured, not desired. The escalation does not come from any anchor this
    # plan added -- it comes from the permanently-blocked pattern table
    # (`blocked_commands.py`, category `git_destructive`), which this plan's
    # diff does not touch, and the same verdict is produced by the tree that
    # predates the M2 anchors. It is recorded here so the census carries it
    # rather than looking clean by omission.
    #
    # The asymmetry that makes it a false positive rather than a policy: the
    # SAME quoted text behind `echo` classifies free, because `echo` is in
    # READ_ONLY_BASE_CMDS and the Gaia CLI is not. The block therefore depends
    # on which read-only command is carrying the prose, not on what runs.
    (
        "mention-force-push-escalates",
        MENTION_OPEN,
        'gaia contract add evidence_report.key_outputs '
        '"git push --force origin main is forbidden"',
        True,
    ),
]


@pytest.mark.parametrize(
    "case_id,kind,command,expected_gated",
    NO_OVERCORRECTION_CENSUS,
    ids=[row[0] for row in NO_OVERCORRECTION_CENSUS],
)
def test_no_overcorrection_census(case_id, kind, command, expected_gated):
    """Gated stays gated, and a quoted mention does not become a use."""
    tier = classify_command_tier(command)
    gated = tier == T3

    if kind == CONTROL_GATED:
        assert gated, (
            f"[{kind}] {case_id}: an operation that was correctly gated before "
            f"this work no longer costs consent -- {command!r} classified {tier}"
        )
    elif kind == MENTION_FREE:
        assert not gated, (
            f"[{kind}] {case_id}: OVERCORRECTION -- a mutative command merely "
            f"QUOTED inside another command's argument escalated to {tier}. "
            f"Writing a command down is not running it: {command!r}"
        )
    else:
        assert gated is expected_gated, (
            f"[{kind}] {case_id}: recorded verdict drifted for {command!r} -- "
            f"expected gated={expected_gated}, got {tier}. If this is a "
            f"deliberate fix, move the row to {MENTION_FREE!r}."
        )


def test_no_overcorrection_census_carries_both_directions():
    """The census keeps the six controls and more than one quoting shape.

    A census of controls alone cannot see overcorrection, and a census of
    mentions alone cannot see the trade that causes it.
    """
    kinds = [row[1] for row in NO_OVERCORRECTION_CENSUS]
    assert kinds.count(CONTROL_GATED) >= 6
    assert kinds.count(MENTION_FREE) >= 5

    ids = [row[0] for row in NO_OVERCORRECTION_CENSUS]
    assert len(ids) == len(set(ids)), "duplicate case id in the census"


# The floor exists so a row cannot leave the table quietly, which only works
# while it EQUALS the number of rows. It had drifted to ten below: the six IAM
# forms closed most recently, and four others, could all have been deleted with
# the guard still green -- a guard that permits exactly the loss it was put
# there to catch. It is a literal, not ``len(CLASSIFIER_TRUTH_TABLE)``, because
# deriving it from the table would assert nothing; adding a row is meant to
# cost one deliberate edit here.
_MINIMUM_MEASURED_CASES = 87


@pytest.mark.parametrize(
    "case_id,family,command,expected_mutative,expected_tier",
    CLASSIFIER_TRUTH_TABLE,
    ids=[row[0] for row in CLASSIFIER_TRUTH_TABLE],
)
def test_classifier_truth_table_verdict(
    case_id, family, command, expected_mutative, expected_tier
):
    """The live classifier returns the recorded (is_mutative, tier) pair."""
    result = detect_mutative_command(command)
    tier = classify_command_tier(command)

    assert result.is_mutative is expected_mutative, (
        f"[{family}] {case_id}: is_mutative drifted for {command!r} -- "
        f"expected {expected_mutative}, got {result.is_mutative} "
        f"(verb={result.verb!r}, category={result.category!r})"
    )
    assert tier == expected_tier, (
        f"[{family}] {case_id}: tier drifted for {command!r} -- "
        f"expected {expected_tier}, got {tier}"
    )


def test_classifier_truth_table_covers_the_measured_corpus():
    """The table keeps at least the measured corpus, with unique ids and commands."""
    assert len(CLASSIFIER_TRUTH_TABLE) >= _MINIMUM_MEASURED_CASES

    ids = [row[0] for row in CLASSIFIER_TRUTH_TABLE]
    assert len(ids) == len(set(ids)), "duplicate case id in the truth table"

    commands = [row[2] for row in CLASSIFIER_TRUTH_TABLE]
    assert len(commands) == len(set(commands)), "duplicate command in the truth table"


def test_classifier_truth_table_carries_both_directions():
    """Both an open form and a gated control are present.

    A table of only-open or only-gated cases cannot detect the failure mode it
    exists for: undercorrecting is invisible without controls, overcorrecting is
    invisible without open forms.
    """
    families = {row[1] for row in CLASSIFIER_TRUTH_TABLE}
    assert {OPEN, GATED, FREE} <= families
