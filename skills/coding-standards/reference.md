# Coding Standards Reference

## Where documentation natively lives, per stack

Documentation of an entry — what a variable, parameter, or output means and
expects — belongs in the language's own native mechanism, not in a comment
duplicating it beside the declaration. The mechanism differs by stack; the
principle (one place, native to the tool) does not.

| Stack | Native mechanism | Notes |
|-------|------------------|-------|
| Terraform / OpenTofu | `variable "x" { description = "..." }`, `output "x" { description = "..." }` | The `description` field IS the documentation; `terraform-docs` and the registry render it directly. A comment above the block repeating it is redundant. |
| TypeScript / JavaScript | JSDoc block (`/** ... */`) with `@param`, `@returns`, `@throws` above the function, class, or exported symbol | Editors, type-checkers, and doc generators read JSDoc directly. A plain `//` comment restating a `@param` line duplicates it. |
| Python | Docstring (`"""..."""`) as the first statement of the module, function, or class | Follow whichever docstring convention (Google, NumPy, reST) the surrounding file already uses — consistency with the file wins over any one style's preference. |
| Go | Doc comment immediately above the declaration, starting with the identifier's own name (`// Foo does ...`) | `go doc`/godoc specifically expects this form to extract documentation. |
| Rust | `///` (item) and `//!` (module) doc comments | Rendered by `rustdoc`; code inside them is compiled and run as doctests. |
| Java / Kotlin | Javadoc / KDoc block above the declaration | Read by the compiler's doc tooling and by IDEs. |
| C# | XML doc comments (`/// <summary>`, `<param>`, `<returns>`) | The compiler extracts these into a documentation XML file. |
| Bash / shell scripts | A header comment block at the top of the script: purpose, usage, required environment variables | Shell has no native docstring construct; the header comment block is the native mechanism for this stack. |
| YAML / Helm values | A comment directly above the key, or the `# --` convention if the project already uses `helm-docs` | Match whatever the repo's existing `values.yaml` already does — do not introduce a new convention mid-file. |

## Stacks and constructs with NO native slot

Where there is no native mechanism, the comment is not a stylistic choice — it
is the only place the contract can live, and deleting it deletes the contract.
The bar inverts here: in a stack from the table above, a comment restating the
native field is redundant; in the rows below, that same comment IS the
documentation, and its absence is the defect.

| Construct with no slot | Consequence |
|------------------------|-------------|
| Terraform `resource`, `locals`, `data`, `module`, `provider`, `dynamic` blocks | Only `variable` and `output` accept `description`. Every rationale attached to a resource or a local has nowhere to go but a comment. |
| Shell scripts (functions and the script itself) | No docstring construct exists; the header block carries purpose, usage, and required environment. |
| YAML and JSON configuration | JSON admits no comments at all, so its contract must live in an adjacent schema or document. |
| SQL migrations and views | No doc construct; the why of a migration exists only as a comment or in the change description. |
| Dockerfile, Makefile, CI pipeline definitions | Directive-only formats. A non-obvious ordering or cache constraint survives only as a comment. |
| CSS / stylesheets | No symbol-level doc mechanism. |

A partial slot behaves like no slot for whatever exceeds it: when a native
field exists but is length-capped or rendered to end users, the rationale that
does not fit belongs in a comment beside it, with the native field carrying the
summary and pointing at it.

## Which checker verifies which rule

A rule a tool can check outweighs one only a human can judge. Bind the rule to
the checker for the stack when one exists.

| Stack | Checker | Rule it enforces |
|-------|---------|------------------|
| Terraform / OpenTofu | `tflint` | `terraform_documented_variables`, `terraform_documented_outputs` |
| Python | `ruff` (pydocstyle rules), `pydocstyle`, `pylint` | Missing module/class/function docstring |
| TypeScript / JavaScript | `eslint` with the JSDoc plugin | Required JSDoc block, required `@param`/`@returns` |
| Go | `revive`, `golint` | Exported symbol must carry a doc comment |
| Rust | compiler lint `missing_docs` | Missing doc on a public item |
| Java | `checkstyle` (Javadoc modules) | Missing or incomplete Javadoc |
| C# | compiler warning `CS1591` | Missing XML comment on a public member |
| Shell | `shellcheck` | Nothing documentation-related — this stack has no checker for it |

**The asymmetry that matters.** Every checker above detects documentation that
is MISSING. None of them detects documentation that is SURPLUS. So the
contract obligation can be delegated to a tool and the redundancy obligation
cannot: no linter will ever tell you a comment restates its code. That half is
enforced by discipline and review alone, which is exactly why it is the half
that erodes.

## Examples: tooling and plan-system traces to strip

Never leave in code:

```
# TASK-142: implement retry per AC-3
# Finding 7 remediation
# as discussed with the user, added on 2026-07-21
```

None of these carry meaning for a reader six months later without the
originating ticket or conversation open beside them, and version history
already carries authorship and timing. Remove the process pointer entirely;
keep only the durable rationale, if any remains once the pointer is stripped —
usually nothing does, and the comment should simply go.

## Dead code is not preservation

Commenting out a block does not preserve it; it leaves clutter that a future
reader must re-verify is truly unused before they can safely delete it. If the
code was worth keeping, version history retrieves it — that is what version
control is for. Delete rather than comment out.

## Edge cases

- **Doc-header and inline comment disagree.** One of the two is stale. Fix the
  drift at its source rather than adding a third comment to reconcile them — a
  reconciliation comment is itself a duplicate rationale.
- **Auto-generated code** (protobuf output, OpenAPI clients, generated
  bindings). The generator owns that file's comments; do not hand-edit them,
  and do not apply the doc-header/inline rules to a file whose header says it
  is generated.
- **Multiple valid docstring conventions in the same language.** Match the
  file, not a global preference — a file already using NumPy-style docstrings
  should not receive one Google-style addition.
- **A protected category with nowhere native to live.** When one of the seven
  protected categories applies to a construct with no native slot, the comment
  is mandatory, not optional. Removing it in a cleanup pass is the failure the
  protected list exists to prevent.
