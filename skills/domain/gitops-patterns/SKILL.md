---
name: gitops-patterns
description: GitOps, Kubernetes, and Flux CD patterns specific to this project
triggers: [kubectl, k8s, kubernetes, helm, flux, deploy, pod, service, manifest]
---

# GitOps Patterns for This Project

## Repository Structure

```
gitops/clusters/
├── prod-digital-eks/
│   ├── flux-system/
│   │   ├── gotk-components.yaml
│   │   └── gotk-sync.yaml
│   ├── common/
│   │   ├── namespace.yaml
│   │   ├── graphql-server.yaml
│   │   └── graphql-server-config.yaml
│   └── mobile-backend/
│       ├── namespace.yaml
│       └── mobile-backend-for-frontend.yaml
└── non-prod-digital-eks/
    └── ... (similar structure)
```

## Flux CD Configuration

### Version & Reconciliation
- **Flux version:** v2.6+
- **Reconciliation interval:** 1 minute
- **Image automation:** Enabled (semver >=1.0.0)

### Git Source
```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: flux-system
  namespace: flux-system
spec:
  interval: 1m0s
  ref:
    branch: main
  secretRef:
    name: flux-system
  url: ssh://git@github.com/[org]/[repo]
```

## HelmRelease Patterns

### Standard HelmRelease Structure

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: graphql-server
  namespace: common
spec:
  interval: 1m
  chart:
    spec:
      chart: app-chart  # Internal chart name
      version: '>=1.0.0'
      sourceRef:
        kind: GitRepository
        name: helm-charts
        namespace: flux-system
      interval: 1m
  values:
    image:
      repository: ghcr.io/vtr/graphql-server
      tag: v1.0.176  # Semver, never 'latest'
    service:
      type: LoadBalancer
      port: 3000
      annotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
        service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"
    env:
      NODE_ENV: "prod"
    resources:
      requests:
        memory: "256Mi"
        cpu: "100m"
      limits:
        memory: "512Mi"
        cpu: "500m"
```

## Naming Conventions

| Resource | Pattern | Example |
|----------|---------|---------|
| **Namespace** | `kebab-case` | `common`, `mobile-backend`, `ingress-nginx` |
| **Service** | `kebab-case` | `graphql-server`, `mobile-backend-for-frontend` |
| **ConfigMap** | `{service}-config` | `graphql-server-config` |
| **Secret** | `{service}-secret` | `graphql-server-secret` |
| **HelmRelease** | Same as service | `graphql-server` |

### Image Versioning (CRITICAL)
- **Pattern:** `v1.0.xxx` (semantic versioning)
- **NEVER use:** `latest`, `main`, `master`, `dev`
- **Automation:** Flux image automation updates tags automatically

## Namespace Pattern

### Standard Namespace

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: common
  labels:
    name: common
    environment: production
```

### Per-Namespace Resources
Each namespace typically contains:
- `namespace.yaml` - Namespace definition
- `{service}.yaml` - HelmRelease for the service
- `{service}-config.yaml` - ConfigMap (if needed)
- `{service}-secret.yaml` - Sealed Secret (if needed)

## Service Standards

### Node.js + Apollo GraphQL Services

**Port:** 3000 (standard)
**Health endpoint:** `/.well-known/apollo/server-health`

```yaml
values:
  service:
    port: 3000
  livenessProbe:
    httpGet:
      path: /.well-known/apollo/server-health
      port: 3000
    initialDelaySeconds: 30
    periodSeconds: 10
  readinessProbe:
    httpGet:
      path: /.well-known/apollo/server-health
      port: 3000
    initialDelaySeconds: 5
    periodSeconds: 5
```

## Resource Limits

### Standard Resource Patterns

| Service Type | CPU Request | CPU Limit | Memory Request | Memory Limit |
|--------------|-------------|-----------|----------------|--------------|
| **Small API** | 100m | 500m | 256Mi | 512Mi |
| **Medium API** | 250m | 1000m | 512Mi | 1Gi |
| **Large API** | 500m | 2000m | 1Gi | 2Gi |

**Always set both requests and limits** to ensure proper scheduling and QoS.

## Load Balancer Configuration

### Network Load Balancer (NLB) Pattern

For services that need external access:

```yaml
service:
  type: LoadBalancer
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
    service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"  # or "internet-facing"
    service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled: "true"
```

### Ingress Pattern (Nginx)

For HTTP/HTTPS services behind Ingress:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mobile-backend
  namespace: mobile-backend
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
spec:
  ingressClassName: nginx
  rules:
  - host: mobile-apps.prod.cloud.vtr.cl
    http:
      paths:
      - path: /bff(/|$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: mobile-backend-for-frontend
            port:
              number: 3000
```

## ConfigMap Pattern

### Environment-Specific Configuration

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: graphql-server-config
  namespace: common
data:
  NODE_ENV: "prod"
  LOG_LEVEL: "info"
  API_TIMEOUT: "30000"
  # Add service-specific configs
```

**Naming:** `{service}-config`
**Separation:** One ConfigMap per service
**Updates:** Changes trigger pod restart via Flux reconciliation

## Secrets Management

### Sealed Secrets (Preferred)

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: graphql-server-secret
  namespace: common
spec:
  encryptedData:
    DATABASE_URL: AgB...  # Encrypted
```

**Never commit plain secrets to Git**

## Kustomization Pattern

### Root Kustomization

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: apps
  namespace: flux-system
spec:
  interval: 1m
  path: ./clusters/prod-digital-eks
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
```

## Deployment Workflow (T3)

### Git-Based Workflow (MANDATORY)

**NEVER use direct kubectl apply**. All changes go through Git:

```bash
# 1. Edit manifest
vim gitops/clusters/prod-digital-eks/common/graphql-server.yaml

# 2. Validate
kubectl apply --dry-run=client -f graphql-server.yaml

# 3. Commit
git add graphql-server.yaml
git commit -m "chore(graphql): update to v1.0.180"

# 4. Push (T3 - requires approval)
git push origin main

# 5. Flux auto-reconciles in ~1 minute
# Or force:
flux reconcile helmrelease graphql-server -n common
```

## Verification Commands

### Check Deployment Status

```bash
# List HelmReleases
flux get helmreleases -n common

# Check specific release
flux get helmrelease graphql-server -n common

# Check pods
kubectl get pods -n common -l app=graphql-server

# Check service/load balancer
kubectl get svc graphql-server -n common

# View logs
kubectl logs -n common -l app=graphql-server --tail=50

# Describe pod (for events)
kubectl describe pod <pod-name> -n common
```

### Check Flux System

```bash
# Flux components status
flux check

# All Kustomizations
flux get kustomizations

# Force reconciliation
flux reconcile source git flux-system
```

## Image Automation

### Automatic Image Updates

Flux watches for new images matching semver pattern:

```yaml
spec:
  values:
    image:
      repository: ghcr.io/vtr/graphql-server
      tag: v1.0.176  # Will update to v1.0.177, v1.0.178, etc.
```

**Automation config:**
```yaml
apiVersion: image.toolkit.fluxcd.io/v1beta1
kind: ImagePolicy
metadata:
  name: graphql-server
spec:
  imageRepositoryRef:
    name: graphql-server
  policy:
    semver:
      range: '>=1.0.0'
```

## Context Switching

### Working with Multiple Clusters

```bash
# List available contexts
kubectl config get-contexts

# Switch to dev
kubectl config use-context aws-digital-eks-dev

# Switch to prod
kubectl config use-context aws-digital-eks-prod

# Verify current context
kubectl config current-context
```

**ALWAYS verify context before operations**

## Common Patterns to Replicate

### Pattern 1: Deploy New Service

When deploying a new service, replicate this structure:

1. **Create namespace (if new):**
   ```yaml
   # namespaces/my-service.yaml
   apiVersion: v1
   kind: Namespace
   metadata:
     name: my-service
   ```

2. **Create HelmRelease:**
   ```yaml
   # my-service/release.yaml
   apiVersion: helm.toolkit.fluxcd.io/v2beta1
   kind: HelmRelease
   metadata:
     name: my-service
     namespace: my-service
   spec:
     interval: 1m
     chart:
       spec:
         chart: app-chart
         version: '>=1.0.0'
         sourceRef:
           kind: GitRepository
           name: helm-charts
     values:
       image:
         repository: ghcr.io/vtr/my-service
         tag: v1.0.0
       service:
         port: 3000
       resources:
         requests:
           memory: "256Mi"
           cpu: "100m"
         limits:
           memory: "512Mi"
           cpu: "500m"
   ```

3. **Add ConfigMap (if needed):**
   ```yaml
   # my-service/config.yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: my-service-config
     namespace: my-service
   data:
     NODE_ENV: "prod"
   ```

### Pattern 2: Update Image Version

```yaml
# Before
tag: v1.0.176

# After
tag: v1.0.180
```

Commit message:
```
chore(service-name): update to v1.0.180

- Bug fixes
- Performance improvements

```

### Pattern 3: Scale Replicas

```yaml
# Add to values:
replicaCount: 3  # Scale from 2 to 3

# Or use HPA for auto-scaling
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80
```

## Troubleshooting Patterns

### Common Issues

| Issue | Check | Solution |
|-------|-------|----------|
| Pod not starting | `kubectl describe pod` | Check events, resource limits, image pull |
| HelmRelease failed | `flux get helmrelease` | Check chart version, values syntax |
| Image not found | `kubectl describe pod` | Verify image exists in registry, check tag |
| Config not applied | `flux reconcile hr` | Force reconciliation |
| LoadBalancer pending | `kubectl get svc` | Check AWS service quotas, subnet tags |

### Debug Commands

```bash
# HelmRelease status with details
flux get helmrelease graphql-server -n common --verbose

# Pod logs (last 100 lines)
kubectl logs -n common deployment/graphql-server --tail=100

# Events in namespace
kubectl get events -n common --sort-by='.lastTimestamp'

# Resource usage
kubectl top pods -n common
```

## Anti-Patterns (NEVER Do This)

❌ **Direct kubectl apply**
```bash
# Bad
kubectl apply -f manifest.yaml

# Good
git add manifest.yaml && git commit && git push
```

❌ **Using 'latest' image tag**
```yaml
# Bad
image:
  tag: latest

# Good
image:
  tag: v1.0.176
```

❌ **No resource limits**
```yaml
# Bad
resources: {}

# Good
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

❌ **Hardcoded values in manifests**
```yaml
# Bad
env:
  - name: DATABASE_URL
    value: "postgres://user:pass@host/db"  # Plain text!

# Good
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: my-service-secret
        key: DATABASE_URL
```

❌ **Manual flux reconcile without Git changes**
```bash
# Bad - bypasses GitOps
flux reconcile hr my-service -n my-namespace

# Good - Change in Git first, then Flux auto-reconciles
git commit && git push
```

## Label Standards

### Required Labels

```yaml
metadata:
  labels:
    app: graphql-server
    environment: production
    managed-by: flux
```

### Selector Labels

For Services to match Pods:
```yaml
selector:
  app: graphql-server
```

## Health Checks

### Liveness Probe (Restart unhealthy)

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 3000
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

### Readiness Probe (Remove from load balancer)

```yaml
readinessProbe:
  httpGet:
    path: /ready
    port: 3000
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
```

## Example: Full Deployment

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: my-app
---
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: my-app
  namespace: my-app
spec:
  interval: 1m
  chart:
    spec:
      chart: app-chart
      version: '>=1.0.0'
      sourceRef:
        kind: GitRepository
        name: helm-charts
        namespace: flux-system
  values:
    image:
      repository: ghcr.io/vtr/my-app
      tag: v1.0.0
    service:
      type: LoadBalancer
      port: 3000
      annotations:
        service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
        service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"
    env:
      NODE_ENV: "production"
    resources:
      requests:
        memory: "256Mi"
        cpu: "100m"
      limits:
        memory: "512Mi"
        cpu: "500m"
    livenessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 30
    readinessProbe:
      httpGet:
        path: /ready
        port: 3000
      initialDelaySeconds: 5
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-app-config
  namespace: my-app
data:
  LOG_LEVEL: "info"
  API_TIMEOUT: "30000"
```
