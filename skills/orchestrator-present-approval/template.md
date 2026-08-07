# Approval presentation template

Approval: `<full approval_id>`

Goal: `<bounded operation>`

Ordered commands (`N` total):

| Index | Exact command | Scope/effect | Risk |
|---:|---|---|---|
| 0 | `<verbatim>` | `<scope>` | `<risk>` |
| 1 | `<verbatim>` | `<scope>` | `<risk>` |

Overall risk: `<including partial completion>`

Rollback: `<per completed item, or none supplied>`

Verification after execution: `<desired-state check>`

`AskUserQuestion` takes `questions[].options[]` as `{label, description}`
objects, not bare strings. The Approve `label` is the activation surface -- it
MUST start with the literal word `Approve` and end with the bracketed nonce tag
`[P-{nonce8}]`, the first 8 hex chars of `<full approval_id>` after `P-`; a
label missing either part extracts no nonce and creates no grant, regardless of
what the `description` says. This applies identically to a singular approval
and a COMMAND_SET:

```
options=[
  {"label": "Approve -- <bounded operation or specific action> [P-{nonce8}]",
   "description": "<first command, or a one-line summary of the set>"},
  {"label": "Reject",
   "description": "Do not run any of these commands."},
]
```
