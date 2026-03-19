# GitOps Patterns — YAML Reference

Structural patterns for Kubernetes and Flux. Use placeholders — replace with values from project-context.

For cloud-specific resource examples, discover patterns from the existing codebase using the `investigation` skill.

---

## HelmRelease

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2beta1
kind: HelmRelease
metadata:
  name: {service-name}
  namespace: {namespace}
spec:
  interval: 5m
  chart:
    spec:
      chart: {chart-name}
      version: '>=1.0.0'
      sourceRef:
        kind: GitRepository
        name: helm-charts
        namespace: flux-system
      interval: 1m
  values:
    image:
      repository: {registry}/{service-name}
      tag: v1.0.0
    resources:
      requests:
        memory: "256Mi"
        cpu: "100m"
      limits:
        memory: "512Mi"
        cpu: "500m"
```

## Namespace

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    name: {namespace}
    environment: {env}
```

## ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {service-name}-config
  namespace: {namespace}
data:
  KEY: "value"
```

## SealedSecret

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: {service-name}-secret
  namespace: {namespace}
spec:
  encryptedData:
    SECRET_KEY: AgB...  # Encrypted with kubeseal
```

## Kustomization

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: {scope}-{env}
  namespace: flux-system
spec:
  interval: 1m
  path: ./clusters/{cluster-name}
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
```

## ImagePolicy

```yaml
apiVersion: image.toolkit.fluxcd.io/v1beta1
kind: ImagePolicy
metadata:
  name: {service-name}
spec:
  imageRepositoryRef:
    name: {service-name}
  policy:
    semver:
      range: '>=1.0.0'
```

## Health Probes

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: {port}
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /ready
    port: {port}
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
```

## Troubleshooting

| Issue | Check | Solution |
|-------|-------|----------|
| Pod not starting | `kubectl describe pod {name} -n {ns}` | Check events, resource limits, image pull |
| HelmRelease failed | `flux get helmrelease {name} -n {ns}` | Check chart version, values syntax |
| Image not found | `kubectl describe pod {name} -n {ns}` | Verify image exists in registry, check tag |
| Service pending | `kubectl get svc -n {ns}` | Check cloud quotas, subnet/network config |
| Flux not reconciling | `flux get kustomizations` | Check source sync, path exists |

## Post-Push Verification

After pushing manifests to Git (T3), verify Flux reconciled successfully. Run each command separately:

```bash
flux reconcile helmrelease {name} -n {namespace} --timeout=30s
```

```bash
kubectl wait --for=condition=Ready helmrelease/{name} -n {namespace} --timeout=120s
```

```bash
kubectl get helmrelease {name} -n {namespace} -o jsonpath='{.status.conditions[?(@.type=="Ready")]}'
```

## Debug Commands

```bash
flux get helmrelease {service-name} -n {namespace} --verbose
kubectl logs -n {namespace} deployment/{service-name} --tail=100
kubectl get events -n {namespace} --sort-by='.lastTimestamp'
kubectl top pods -n {namespace}
```

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
