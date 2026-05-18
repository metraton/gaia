# Orchestrator Approval

This skill is the orchestrator-side complement to `request-approval/`. It enforces presentation discipline (verbatim values, labeled fields, nonce in option label) and the dispatch-vs-resume decision when `mode` was load-bearing.

For the operating rules of the entire approval system -- the 5 empirically validated rules covering `mode` semantics, Gaia bash_validator orthogonality, SendMessage resume behavior, foreground/background defaults, and `batch_scope` activation -- read the canonical reference:

- [`../request-approval/README.md`](../request-approval/README.md) -- approvals operating rules, workflow examples, file map.

This skill assumes those rules are understood; it adds the presentation contract on top.
