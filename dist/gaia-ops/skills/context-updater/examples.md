# CONTEXT_UPDATE Examples

## cloud-troubleshooter

```
CONTEXT_UPDATE:
{
  "cluster_details": {
    "kubernetes_version": "1.29",
    "node_pools": [
      {"name": "default-pool", "machine_type": "e2-standard-4", "node_count": 3}
    ]
  }
}
```

## gitops-operator

```
CONTEXT_UPDATE:
{
  "gitops_configuration": {
    "flux_version": "v2.6.1",
    "reconciliation_interval": "1m"
  }
}
```

## terraform-architect

```
CONTEXT_UPDATE:
{
  "terraform_infrastructure": {
    "modules": ["vpc", "eks", "rds"],
    "backend": "s3"
  }
}
```

## devops-developer

```
CONTEXT_UPDATE:
{
  "application_services": {
    "services": [
      {"name": "graphql-server", "port": 3000, "namespace": "common"}
    ]
  }
}
```

## Fresh Install Enrichment

After investigating a new cluster, the gitops-operator discovers namespace structure:

```
CONTEXT_UPDATE:
{
  "cluster_details": {
    "namespaces": {
      "application": ["adm", "dev", "test"],
      "infrastructure": ["flux-system", "ingress-nginx"],
      "system": ["kube-system", "kube-public"]
    }
  }
}
```

This merges into existing `cluster_details`. Keys already present (like `kubernetes_version`) are preserved. The `namespaces` dict is added as a new key.
