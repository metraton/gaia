# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 4.0.x   | Yes       |
| < 4.0   | No        |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

To report a vulnerability, please use one of the following methods:

1. **Email:** Send details to jaguilar1897@gmail.com with the subject line `[SECURITY] gaia-ops vulnerability report`.
2. **GitHub Private Vulnerability Reporting:** Use the [Security Advisories](https://github.com/metraton/gaia-ops/security/advisories) tab to report privately.

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 5 business days
- **Fix or mitigation:** Depends on severity, targeting 30 days for critical issues

## What Constitutes a Security Issue

The following are considered security vulnerabilities in gaia-ops:

- **Hook bypass:** Any method to execute commands without passing through the pre_tool_use validation hook
- **Approval flow bypass:** Circumventing the nonce-based approval flow for T3 (state-modifying) operations
- **Nonce forgery:** Fabricating, reusing, or predicting approval nonces
- **Command injection:** Injecting arbitrary commands through validators (bash_validator, mutative_verbs, blocked_commands)
- **Privilege escalation:** Agents executing operations above their declared security tier
- **Context injection:** Manipulating project-context.json or skill injection to alter agent behavior maliciously

## Out of Scope

- Vulnerabilities in Claude Code itself (report to Anthropic)
- Issues in upstream dependencies (report to the respective maintainer)
- Denial of service through large inputs (this is a local development tool)
