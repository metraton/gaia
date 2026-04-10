---
name: gitops-patterns
description: Use when creating, modifying, or reviewing Kubernetes manifests, HelmReleases, or Flux configuration
metadata:
  user-invocable: false
  type: domain
---

# GitOps Patterns

Reference conventions for Kubernetes, HelmRelease, and Flux. The codebase is the authority -- these patterns help you find and interpret what's already there.

For YAML examples, troubleshooting, and resource limit defaults, read `reference.md` in this directory.

## Discover the Project's GitOps Layout

Before creating any manifest, understand how THIS project organizes its GitOps repo.

1. **Find the repo root.** Check project-context for `gitops_repo_path`. If absent, look for a directory containing `clusters/`, `flux-system/`, or Kustomization files.
2. **Read 2-3 existing HelmReleases.** How are values structured? What chart sources are used? What reconciliation intervals are set?
3. **Check namespace organization.** Some projects use one directory per namespace; others group by service or environment. Follow what exists.
4. **Follow the majority pattern.** If existing services use `kebab-case` names and `{service}-config` ConfigMaps, yours should too.

## Repository Structure (Reference)

Common layout -- defer to what the project actually uses.

```
{gitops_repo_path}/
├── clusters/{cluster-name}/     # Flux entrypoint per cluster
├── infrastructure/
│   ├── base/                    # Shared: namespaces, sources
│   └── overlays/{env}/          # Per-environment patches
└── apps/
    ├── base/{service}/          # Per-service Kustomize base
    └── overlays/{env}/          # Per-environment patches
```

## Naming Conventions

| Resource | Pattern | Example |
|----------|---------|---------|
| Namespace | `kebab-case` | `common`, `mobile-backend` |
| Service / HelmRelease | `kebab-case` | `products-service` |
| ConfigMap | `{service}-config` | `products-service-config` |
| Secret | `{service}-secret` | `products-service-secret` |
| Kustomization | `{scope}-{env}` | `apps-oci-dev` |

## Image Versioning

Flux ImagePolicy uses semver ranges (e.g., `>=1.0.0`) to auto-promote tags. Mutable tags like `latest`, `main`, or `dev` break this -- Flux cannot determine which is newer, so reconciliation either picks the wrong image or loops indefinitely. Always use semantic versioning: `v1.0.xxx`.

## Key Rules

1. **Git is the single source of truth** — `kubectl apply` directly bypasses reconciliation, creating drift that Flux will either revert (losing your change) or conflict with (breaking the next deploy)
2. **Semver tags only** — mutable tags break image automation (see above)
3. **Secrets via SealedSecrets** — plain secrets in Git are readable by anyone with repo access; SealedSecrets encrypt at rest and decrypt only in-cluster
4. **Resource limits on every workload** — without limits, a single pod can starve the node; without requests, the scheduler cannot bin-pack efficiently
5. **Verify cluster context first** — `kubectl config current-context` before any operation; applying to the wrong cluster is the most common and most damaging mistake
6. **Post-push verification** — after pushing manifests, verify Flux reconciled successfully; a merged manifest that fails to apply is worse than no change at all. See `reference.md` for the exact command sequence
