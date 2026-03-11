# Marketplace Registration

How to add and install gaia-ops plugins via the Claude Code marketplace.

## Add the gaia-ops marketplace

```
/plugin marketplace add metraton/gaia-ops
```

This makes all published plugins available for installation.

## Install plugins

```
/plugin install gaia-ops            # Complete system: security + agents + skills + orchestrator
/plugin install gaia-security      # Security hooks only: approval system, audit, metrics, anomaly detection
```

### Plugin descriptions

| Plugin | Includes | Use case |
|--------|----------|----------|
| `gaia-ops` | Security hooks, agents, skills, context injection, episodic memory, scanning, speckit, CLI tools | Full orchestration system (includes gaia-security) |
| `gaia-security` | Security hooks, approval system, audit logging, metrics, anomaly detection | Security-only overlay |

### Dependencies

- `gaia-ops` is self-contained (includes all security components).
- `gaia-security` is standalone (no dependencies).

## Verify installation

```
/plugin list
```

Expected output includes the installed plugin(s) with their version and status.

## Marketplace manifest

The marketplace is defined in `.claude-plugin/marketplace.json`. This file must contain:

- `name` -- marketplace identifier
- `owner.name` -- owner display name
- `owner.email` -- owner contact email
- `plugins[]` -- array of available plugins, each with `name`, `description`, `version`, `source`

## Troubleshooting

| Issue | Resolution |
|-------|-----------|
| Plugin not found | Verify marketplace is added: `/plugin marketplace list` |
| Version mismatch | All plugin versions must match `package.json` version |
| Missing dependencies | `gaia-ops` is self-contained; `gaia-security` has no dependencies |
