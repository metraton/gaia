# Producer approval reference

`gaia approvals request-set` (`bin/cli/approvals.py::cmd_request_set`, sealed by
`gaia/approvals/core.py::request_command_set`) accepts one or more exact,
atomic, non-interactive T3 commands. It refuses, naming the cause: a missing or
over-long phrase, a command that is shell composition, a protected path, a
permanently blocked operation, or a command that is not T3. The limits are the
renderer's (`gaia/approvals/surface.py`: `TITLE_MAX`, `QUESTION_MAX`,
`LINE_MAX`).

With `--json` it prints `status`, the canonical `approval_id`, and `command_set`
(each command with its fingerprint). Copy the id; never derive an id or a
fingerprint yourself.

`gaia approvals request-file-write --path <absolute path>` is the same request
for a Write/Edit on a protected path. A later Write/Edit of that path by the
same agent in the same session uses this request once it is approved.

The reactive denial's request line is `gaia/approvals/core.py::request_line`,
rendered into the denial by `hooks/modules/security/approval_messages.py`.
