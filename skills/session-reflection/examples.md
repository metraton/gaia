# Session Reflection — Examples

## A reflection that closes as much as it opens

User: `reflexionemos y guardemos`.

The session changed the surface-routing matcher and, along the way, made a
pending from two sessions ago moot. Step 3 sweeps the two initiatives the
session touched:

```bash
gaia memory get-relevant --initiative=gaia --json
gaia memory get-relevant --initiative=gaia_system --json
```

The `gaia` corpus returns `thread_routing_keywords_decision` (`carry_forward`),
opened before the matcher stopped scoring keywords. No turn in this session
mentions it — the sweep is what finds it, and the code is what settles it.

The working tables (internal — not what the user sees):

```text
What we settled
| Item | Home | Operation |
| Keywords are no longer a routing signal; commands and artifacts score alone | commit 4c1e9a2 | SKIP |
| Routing has one source of truth: the agent frontmatter the seeder reads | decision_routing_frontmatter_is_source | SAVE (anchor) |

Open work
| Item | Home | Operation |
| Retire the keywords block still present in three agent files | thread_routing_keywords_cleanup | SAVE (thread, carry_forward) |
| "Decide whether keywords stay a routing signal" — answered by the matcher change | thread_routing_keywords_decision | TRANSITION → status=closed |
| Migrate the remaining surfaces | plan 7, task 42 (verified still open) | SKIP |

What Gaia should improve
  Symptom      seed_surface_routing accepts a keywords block and seeds the
               surface without it, emitting no warning
  Component    tools/scan/seed_surface_routing.py
  Evidence     three agents still carry a keywords: block; the surface_routing
               rows for all three seeded with that key absent from signals
  Reproduction gaia install, then read surface_routing for one of the three
  → feedback_seed_surface_routing_silent_keywords · SAVE

Resume point
Task 42 verification.
```

Four things carry the shape:

- The `TRANSITION` exists only because of the sweep. Nothing in the transcript
  named that pending; the corpus did, and closing it is the row that keeps the
  worklist from accumulating.
- The `SKIP` on task 42 was verified by opening the task, not by remembering it.
  Had it shown `done`, the same item would have been a `TRANSITION` instead.
- The two settled rows had to answer the same question the open ones did. The
  decision that will constrain future work takes a home as an anchor; the one
  the commit already records takes `SKIP` naming that commit. Neither could be
  stated without choosing.
- The defect is worked through with its evidence and reproduction, not reduced
  to its slug — a defect whose evidence was never displayed was consented to
  as a slug, and that durable body is what a future session reads instead of
  reopening this one.

All four operations above fall inside the autonomous side of the exception
boundary in `memory/SKILL.md` — a `TRANSITION` on threads, a `SAVE` on an
anchor and a thread, no `type=user` row and no contradicted `decision_*` —
so the orchestrator adjudicates and executes them directly. What the user
actually sees is the report:

```text
- Closed thread_routing_keywords_decision: superseded by the matcher change (commit 4c1e9a2)
- Saved decision_routing_frontmatter_is_source as an anchor
- Opened thread_routing_keywords_cleanup: three agent files still carry the block
- Verified plan 7 / task 42 still open — no change
- Saved feedback_seed_surface_routing_silent_keywords under gaia_system
```

The same flow continues: after this review, the user then asks to compact.
`gaia-compact` builds its own handoff from the resume point — every item above
already has a home, so there is no unsaved transient context to carry.
