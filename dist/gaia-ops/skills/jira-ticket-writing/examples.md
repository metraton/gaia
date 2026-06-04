# Jira Ticket Writing — Examples

Real tickets from the AOS migration project (AOS-181..AOS-204, 2026-05-18).
These are the patterns the formula was validated against.

---

## Story example

**Title:** Migrate AOS repositories from GitHub to Bitbucket

**Objective:** Move the AOS repositories to Bitbucket so the team works in
one platform, keeping the full project history.

**What it covers:**
- Repos migrated: aos, aos-keycloak
- Branches and tags preserved
- Default branch renamed master → main
- Team access configured in Bitbucket

**Acceptance criteria:**
- [x] Repos visible in Bitbucket under aaxisdigital
- [x] Full history and tags intact
- [x] Default branch is main
- [x] Original GitHub repos archived

**Links:** Brief: aos-migration-github-to-bitbucket

**First comment (evidence):**
```
git log --oneline | head -1  -> 3a231fa (2026-05-18) — first commit present
git tag -> v1.0.0, v1.1.0 — all tags present
GitHub repos marked archived: github.com/aaxisdigital/aos, ...keycloak
Brief aos-migration-github-to-bitbucket closed.
```

**Subtasks:**
- Migrate aos repo
- Migrate aos-keycloak repo
- Verify refs, tags and branches

---

## Subtask examples

**Title:** Migrate aos repo
**Description:** Push full history and all tags from GitHub to Bitbucket.
Done when `git log` shows continuous history and `git tag` lists all original tags.

**Title:** Verify refs, tags and branches
**Description:** Confirm all branches, tags, and default branch (main) match
the GitHub source. Done when diff of `git tag` outputs is empty.

---

## Story example — infrastructure

**Title:** Provision Cloud Run service for AOS backend

**Objective:** Deploy the NestJS backend to Cloud Run on aaxis-os-project so
the team has a stable staging environment for integration testing.

**What it covers:**
- Cloud Run service created in us-central1
- Environment variables injected from Secret Manager
- Service account with minimal IAM permissions
- Health check at /health returns 200

**Acceptance criteria:**
- [x] Service deployed: `gcloud run services describe aos-server --region us-central1`
- [x] Health endpoint responds: `curl https://<url>/health` returns 200
- [x] No secrets in plaintext env vars (Secret Manager refs only)

**Links:** IaC: aos-iac / Brief: aos-cloud-run-backend

**First comment (evidence):**
```
gcloud run services describe aos-server --region us-central1 --format=json | jq .status.url
-> https://aos-server-xxxx-uc.a.run.app
curl https://aos-server-xxxx-uc.a.run.app/health -> {"status":"ok"}
terraform show | grep secret -> all refs are secretmanager.googleapis.com paths
```

---

## What NOT to do

### Bad title (mechanism, not outcome)
> Run git remote set-url and git push --mirror for both repos

### Good title (outcome)
> Migrate AOS repositories from GitHub to Bitbucket

### Bad Acceptance Criteria (vague)
> - [ ] Repos work correctly in Bitbucket
> - [ ] History is fine

### Good Acceptance Criteria (verifiable)
> - [x] `git log --oneline | wc -l` matches count from GitHub (382 commits)
> - [x] `git tag | sort` output matches GitHub tag list verbatim

### Bad evidence placement (in description)
> *What it covers:*
> - git push --mirror ran with exit 0 on 2026-05-18 at 14:32
> - commit 3a231fa is the oldest commit

### Good evidence placement (in first comment)
Description stays clean; verbatim commands and hashes go in the comment posted
immediately after ticket creation.
