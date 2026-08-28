# OpenCode Capability Authority

OpenCode and Gaia answer different questions. OpenCode owns whether a tool call can reach an external directory; Gaia owns whether the requested effect is allowed, denied, or requires informed consent. Host reachability is therefore a prerequisite, never a Gaia approval.

The precedence is strict:

```text
OpenCode external_directory policy
  deny -> HOST short-circuit; Gaia was not consulted; Gaia verdict is absent
  allow -> Gaia tool.execute.before -> Gaia consent/protected-path verdict
```

`opencode/agent-policy.json` is the durable policy source. `gaia install` resolves `gaia.paths.scratch_dir()` at install time and gives only `gaia-system` an `external_directory` rule for that canonical root and its descendants. The broad deny remains first and the two exact canonical-root allows follow it because OpenCode uses the last matching rule. No wildcard root, parent, sibling, alternate data root, or ordinary-agent grant is emitted. OpenCode resolves external targets before matching; a traversal or symlink that leaves scratch is consequently evaluated as its outside target and remains denied.

Every Gaia agent still reaches Gaia consent only after host reachability:

| Agent | Host external-directory reachability | Effect and protected-path authority |
|---|---|---|
| `gaia-system` | Canonical Gaia scratch only | Gaia |
| `gaia-orchestrator` | Denied | Gaia, if the host ever forwards a call |
| `gaia-operator` | Denied | Gaia, if the host ever forwards a call |
| `gaia-planner` | Denied | Gaia, if the host ever forwards a call |
| `gaia-verifier` | Denied | Gaia, if the host ever forwards a call |
| `developer` | Denied | Gaia, if the host ever forwards a call |
| `cloud-troubleshooter` | Denied | Gaia, if the host ever forwards a call |
| `gitops-operator` | Denied | Gaia, if the host ever forwards a call |
| `platform-architect` | Denied | Gaia, if the host ever forwards a call |

The live handoff 16805 failed before `tool.execute.before`: the generated `gaia-system` policy had the default wildcard deny and no scratch exception. That denial was HOST-owned and produced no Gaia verdict. This boundary cannot be observed by the plugin because the host does not invoke it. The policy records that limitation as `authority.host_short_circuit_gap` with owner `HOST`, `gaia_consulted=false`, and `gaia_verdict=null`; it must never be presented as working Gaia enforcement. The new reachability rule lets a scratch call arrive at `tool.execute.before` without pre-authorizing mutation: Gaia can still return T3 or protected-path consent, and OpenCode then presents that Gaia decision through its native permission surface.

OpenCode loads configuration once. After install or update changes `opencode.json`, quit and restart OpenCode before taking live-host evidence.
