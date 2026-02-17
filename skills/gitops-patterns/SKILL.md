---
name: gitops-patterns
description: GitOps, Kubernetes, and Flux CD patterns specific to this project
user-invocable: false
---

# GitOps Patterns

Project-specific conventions. For YAML examples, read `reference.md` in this directory.

## Repository Structure

```
gitops/clusters/
├── prod-digital-eks/
│   ├── flux-system/       (gotk-components, gotk-sync)
│   ├── common/            (graphql-server, configs)
│   └── mobile-backend/    (mobile-backend-for-frontend)
└── non-prod-digital-eks/  (same structure)
```

## Flux Configuration

- **Version:** v2.6+
- **Reconciliation:** 1 minute interval
- **Image automation:** Enabled, semver >=1.0.0
- **Source:** Git via SSH, branch `main`

## Naming Conventions

| Resource | Pattern | Example |
|----------|---------|---------|
| Namespace | `kebab-case` | `common`, `mobile-backend` |
| Service | `kebab-case` | `graphql-server` |
| ConfigMap | `{service}-config` | `graphql-server-config` |
| Secret | `{service}-secret` | `graphql-server-secret` |
| HelmRelease | Same as service | `graphql-server` |

## Image Versioning (CRITICAL)

- **Pattern:** `v1.0.xxx` (semantic versioning)
- **NEVER:** `latest`, `main`, `master`, `dev`
- Flux image automation updates tags automatically

## Service Standards

- **Port:** 3000 (Node.js/Apollo standard)
- **Health:** `/.well-known/apollo/server-health`
- **Chart:** `app-chart` (internal Helm chart)

## Resource Limits

| Size | CPU Req | CPU Lim | Mem Req | Mem Lim |
|------|---------|---------|---------|---------|
| Small | 100m | 500m | 256Mi | 512Mi |
| Medium | 250m | 1000m | 512Mi | 1Gi |
| Large | 500m | 2000m | 1Gi | 2Gi |

Always set both requests and limits.

## Required Labels

```yaml
labels:
  app: {service-name}
  environment: production
  managed-by: flux
```

## Cluster Contexts

```
aws-digital-eks-dev     (non-prod)
aws-digital-eks-prod    (production)
```

**Always verify context before operations:** `kubectl config current-context`

## Per-Namespace Structure

Each namespace contains:
- `namespace.yaml` — Namespace definition
- `{service}.yaml` — HelmRelease
- `{service}-config.yaml` — ConfigMap (if needed)
- `{service}-secret.yaml` — SealedSecret (if needed)

## Key Rules

1. **Git-first** — NEVER `kubectl apply` directly. All changes via git commit + push
2. **Semver tags** — Never `latest`, always `v1.0.xxx`
3. **Secrets via SealedSecrets** — Never plain secrets in Git
4. **Flux reconciles** — Auto in ~1m, or force with `flux reconcile`
5. **Always set resource limits** — Both requests and limits required
6. **ConfigMap per service** — One ConfigMap, one service
