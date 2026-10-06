# Producer approval reference

`gaia approvals request-set` (`bin/cli/approvals.py::cmd_request_set`, sealed by
`gaia/approvals/core.py::request_command_set`) accepts one or more exact,
atomic, non-interactive steps, at least one of them T3; a step that is not T3
is sealed unsigned (`gaia/approvals/command_set.py::validate_request_set`). It
refuses, naming the cause: a missing or over-long phrase, a command that is
shell composition, a protected path, a permanently blocked operation, or a set
with no T3 step. The limits are the renderer's (`gaia/approvals/surface.py`:
`TITLE_MAX`, `QUESTION_MAX`, `LINE_MAX`, and `BATCH_MAX` counting signed steps).

With `--json` it prints `status`, the canonical `approval_id`, `command_set`
(the signed commands with their fingerprints, the only ones the grant reserves)
and `steps` (every step in order, an unsigned one with `"signed": false`). Copy
the id; never derive an id or a fingerprint yourself.

`gaia approvals request-file-write --path <absolute path>` is the same request
for a Write/Edit on a protected path. A later Write/Edit of that path by the
same agent in the same session uses this request once it is approved.

The reactive denial's request line is `gaia/approvals/core.py::request_line`,
rendered into the denial by `hooks/modules/security/approval_messages.py`.
