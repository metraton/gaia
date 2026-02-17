# GitOps Patterns - Reference Examples

Full YAML examples for common operations. Read on-demand, not injected.

## HelmRelease Example

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
      chart: app-chart
      version: '>=1.0.0'
      sourceRef:
        kind: GitRepository
        name: helm-charts
        namespace: flux-system
      interval: 1m
  values:
    image:
      repository: ghcr.io/vtr/graphql-server
      tag: v1.0.176
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

## Namespace Example

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: common
  labels:
    name: common
    environment: production
```

## ConfigMap Example

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
```

## Sealed Secret Example

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

## Load Balancer (NLB) Annotations

```yaml
service:
  type: LoadBalancer
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
    service.beta.kubernetes.io/aws-load-balancer-scheme: "internal"
    service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled: "true"
```

## Ingress (Nginx) Example

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

## Kustomization Example

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

## Image Automation Policy

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

## Health Probes

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 3000
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /ready
    port: 3000
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
```

## Troubleshooting

| Issue | Check | Solution |
|-------|-------|----------|
| Pod not starting | `kubectl describe pod` | Check events, limits, image pull |
| HelmRelease failed | `flux get helmrelease` | Check chart version, values syntax |
| Image not found | `kubectl describe pod` | Verify image exists, check tag |
| LoadBalancer pending | `kubectl get svc` | Check AWS quotas, subnet tags |

## Debug Commands

```bash
flux get helmrelease graphql-server -n common --verbose
kubectl logs -n common deployment/graphql-server --tail=100
kubectl get events -n common --sort-by='.lastTimestamp'
kubectl top pods -n common
```
