# Coding Standards Reference

## Where input documentation lives, per stack

Input documentation — what a variable, parameter, or output means and
expects — belongs in the language's own native mechanism, not in a comment
duplicating it beside the declaration. The mechanism differs by stack; the
principle (one place, native to the tool) does not.

| Stack | Native mechanism | Notes |
|-------|------------------|-------|
| Terraform / OpenTofu | `variable "x" { description = "..." }`, `output "x" { description = "..." }` | The `description` field IS the documentation; `terraform-docs` and the registry render it directly. A comment above the block repeating it is redundant. |
| TypeScript / JavaScript | JSDoc block (`/** ... */`) with `@param`, `@returns`, `@throws` above the function, class, or exported symbol | Editors, type-checkers, and doc generators read JSDoc directly. A plain `//` comment restating a `@param` line duplicates it. |
| Python | Docstring (`"""..."""`) as the first statement of the module, function, or class | Follow whichever docstring convention (Google, NumPy, reST) the surrounding file already uses — consistency with the file wins over any one style's preference. |
| Go | Doc comment immediately above the declaration, starting with the identifier's own name (`// Foo does ...`) | `go doc`/godoc specifically expects this form to extract documentation. |
| Bash / shell scripts | A header comment block at the top of the script: purpose, usage, required environment variables | Shell has no native docstring construct; the header comment block is the native mechanism for this stack. |
| YAML / Helm values | A comment directly above the key, or the `# --` convention if the project already uses `helm-docs` | Match whatever the repo's existing `values.yaml` already does — do not introduce a new convention mid-file. |

## Examples: tooling and plan-system traces to strip

Never leave in code:

```
# TASK-142: implement retry per AC-3
# Finding 7 remediation
# as discussed with the user, added on 2026-07-21
```

None of these carry meaning for a reader six months later without the
originating ticket or conversation open beside them, and `git log`/`git
blame` already carries authorship and timing. Remove the process pointer
entirely; keep only the durable rationale, if any remains once the pointer
is stripped — usually nothing does, and the comment should simply go.

## Dead code is not preservation

Commenting out a block does not preserve it; it leaves clutter that a future
reader must re-verify is truly unused before they can safely delete it. If
the code was worth keeping, `git log -p` / `git show` retrieves it — that is
what version control is for. Delete rather than comment out.

## Edge cases

- **Doc-header and inline comment disagree.** One of the two is stale. Fix
  the drift at its source rather than adding a third comment to reconcile
  them — a reconciliation comment is itself a duplicate rationale.
- **Auto-generated code** (protobuf output, OpenAPI clients, generated
  bindings). The generator owns that file's comments; do not hand-edit them,
  and do not apply the doc-header/inline rules to a file whose header says
  it is generated.
- **Multiple valid docstring conventions in the same language.** Match the
  file, not a global preference — a file already using NumPy-style
  docstrings should not receive one Google-style addition.
