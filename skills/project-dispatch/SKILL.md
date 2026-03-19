---
name: project-dispatch
description: Use when the user asks anything about the project, codebase, infrastructure, deployment, or planning
metadata:
  user-invocable: false
  type: reference
---

# Project Dispatch

## Agent Table

| Agent | Surface | Handles | Adjacent |
|---|---|---|---|
| cloud-troubleshooter | live_runtime | Live infra: pods, logs, incidents, drift | gitops-operator, terraform-architect |
| gitops-operator | gitops_desired_state | Desired state in Git: manifests, Helm, Flux | cloud-troubleshooter, terraform-architect, devops-developer |
| terraform-architect | terraform_iac | Cloud resources: Terraform, Terragrunt, IAM | gitops-operator, devops-developer, cloud-troubleshooter |
| devops-developer | app_ci_tooling | App code, CI/CD, Docker, build tooling | terraform-architect, gitops-operator |
| speckit-planner | planning_specs | Specs, plans, task breakdowns | devops-developer, gaia-system |
| gaia-system | gaia_system | System internals: hooks, skills, CLAUDE.md | devops-developer |

## Dispatch Rules

1. Route to the agent whose **surface** matches the request
2. When multiple surfaces are involved, check **Adjacent** and dispatch in parallel if independent
3. Your prompt to the agent = user's objective + info agent cannot derive. Hooks inject context automatically
4. Resume an active agent with `SendMessage(to: agentId)` instead of creating a new one
5. If routing is unclear, use `AskUserQuestion` to clarify

## What You Never Do

- Never use `Explore`, `Grep`, `Read`, or `Bash` to investigate the project yourself
- Never use `Plan` agent — use `speckit-planner` for project planning
- Your job: dispatch, relay, summarize — not investigate or execute
