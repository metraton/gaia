---
name: claude-code-consent-adapter
description: Use when identifying the native consent mechanism implemented by Gaia's Claude Code adapter
---

# Claude Code Consent Adapter

Claude Code presents Gaia's already-rendered consent request through its native
question mechanism. The adapter translates that host shape at the boundary;
the consent fields, approval identifier, and activation semantics remain owned
by the host-neutral consent protocol.

The native mechanism is `AskUserQuestion`. Its result may arrive as an
`ElicitationResult`; both names are adapter vocabulary and must remain outside
the five host-agnostic consent skills.

Implementation details belong to the adapter, not here. The registration and
its documentation metadata live together in
`hooks/adapters/registry.py::register_adapter`; the concrete translation lives
in `hooks/adapters/claude_code.py::ClaudeCodeAdapter.request_consent`.
