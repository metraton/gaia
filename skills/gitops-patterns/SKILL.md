---
name: gitops-patterns
description: Use when creating, modifying, or reviewing Kubernetes manifests, HelmReleases, or Flux configuration
user-invocable: false
---

# GitOps Patterns

Project-specific conventions. For YAML examples, read `reference.md` in this directory.
Use values from your injected project-context — never hardcode cluster names, registry URLs, or namespaces.

## Repository Structure

```
{gitops_repo_path}/
├── clusters/
│   └── {cluster-name}/          # from project-context cluster_name
│       ├── flux-system/         # Flux controllers + sync
│       ├── apps.yaml            # Kustomization → apps overlay
│       └── infrastructure.yaml  # Kustomization → infra overlay
├── infrastructure/
│   ├── base/                    # Shared: namespaces, sources, components
│   └── overlays/{env}/          # Per-environment patches
└── apps/
    ├── base/{service}/          # Per-service Kustomize base
    └── overlays/{env}/          # Per-environment patches
```

## Flux Configuration

- **Reconciliation interval:** 1 minute (Kustomization), 5 minutes (HelmRelease)
- **Source:** Git via SSH, branch `main`
- **Image automation:** semver `>=1.0.0` — Flux updates tags automatically
- **Pruning:** `prune: true` — resources removed from Git are deleted from cluster

## Naming Conventions

| Resource | Pattern | Example |
|----------|---------|---------|
| Namespace | `kebab-case` | `common`, `mobile-backend` |
| Service / HelmRelease | `kebab-case` | `products-service` |
| ConfigMap | `{service}-config` | `products-service-config` |
| Secret | `{service}-secret` | `products-service-secret` |
| Kustomization | `{scope}-{env}` | `apps-oci-dev` |

## Image Versioning (CRITICAL)

- **Pattern:** semantic versioning `v1.0.xxx`
- **NEVER:** `latest`, `main`, `master`, `dev`, `staging`
- Flux ImagePolicy uses `semver.range: '>=1.0.0'`

## Resource Limits

Always set both requests AND limits:

| Size | CPU Req | CPU Lim | Mem Req | Mem Lim |
|------|---------|---------|---------|---------|
| Small | 100m | 500m | 256Mi | 512Mi |
| Medium | 250m | 1000m | 512Mi | 1Gi |
| Large | 500m | 2000m | 1Gi | 2Gi |

## Secrets Management

```
Preference order:
1. SealedSecrets (Bitnami) — encrypted in Git, decrypted in cluster
2. External Secrets — from cloud secret store (Secret Manager, Vault)
3. NEVER plain Kubernetes Secrets in Git
```

## Per-Namespace Structure

Each namespace directory contains:
- `namespace.yaml` — Namespace definition with standard labels
- `{service}.yaml` — HelmRelease
- `{service}-config.yaml` — ConfigMap (if needed)
- `{service}-secret.yaml` — SealedSecret (if needed)

## Key Rules

1. **Git-first** — NEVER `kubectl apply` directly. All changes via git commit + push
2. **Semver tags** — Never `latest`, always `v1.0.xxx`
3. **Secrets via SealedSecrets** — Never plain secrets in Git
4. **Flux reconciles** — Auto in ~1m, or force: `flux reconcile kustomization {name}`
5. **Always set resource limits** — Both requests and limits required
6. **Verify cluster context** — `kubectl config current-context` before any operation
7. **Use project-context** — cluster_name, gitops_repo_path, environment from injected context
