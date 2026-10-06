# Memory — Access telemetry

The three counters a read bumps, which surface bumps which, and what reads
them back. Load this when classifying a new read surface, debugging a counter,
or auditing what `gaia memory show` / `list` report. The write path and
retrieval are in `reference.md`; the judgment flow is in `SKILL.md`.

## Reading a row leaves a trace

Three counters bump as a side effect of being read, and two questions classify
any surface, including one not built yet. **Did the caller identify these rows,
or describe a window and take whatever fell in?** A slug identifies them; a
named initiative identifies them; a filter, a search term, a date range or a
dump of the table identifies nothing, however much of each row it prints.
Identified, so reaching them was the point — **deliberate** (`show`, `story`,
`get-relevant --initiative`). **When they were not: did the rows answer the
question their caller asked, or were they assembled into somebody's context?**
Answered — **neither** (`search`, `list`, `gaia query`): what fell in reflects
the phrasing, not the row. Assembled — automatic, and split once more by what
the block reaches: one fixed corpus every time it fires, the same rows whatever
the occasion is about, is **kernel** (today, the dispatch block); a window
picked for this occasion is **injection** (`get-relevant`, however launched).
Rendering is what counts: a row trimmed out of a block was never reached.

Never blend two into one number. A count is worth reading only if a higher one
means the row was worth more, and each axis fires at a rate set by something
other than the row: the kernel's corpus rides on every dispatch, so folded into
injection it heads any ranking by construction and measures dispatch volume, not
usefulness; injection folded into deliberate lets a row pushed at people read as
demand. A surface built tomorrow earns its own axis by that test. None of this
changes what gets injected — selection still orders by `updated_at` and reads no
counter.

## Exact columns and call sites

The `memory` table carries three independent counter/timestamp pairs --
`injection_count` / `last_injected_at`, `deliberate_count` /
`last_deliberate_at`, and `kernel_count` / `last_kernel_at` (v50) -- bumped
by one shared helper, `gaia.store.writer.record_memory_access(workspace,
name, kind, *, db_path=None)`; `kind` is exactly `"injection"`,
`"deliberate"`, or `"kernel"`, anything else raises `ValueError`. The
UPDATE touches only the counter and its timestamp, never `updated_at` or
`body`, so it fires no `memory_history` row and never reorders the digest,
which still sorts by `updated_at` alone. It is best-effort: every failure
is swallowed and reported as `False`, so a locked or unreachable DB never
breaks the read it instruments.

"Reading a row leaves a trace" above carries the property that decides
deliberate from automatic -- whether the CALLER identified the rows, never
whether the answer carried the body. That property alone reaches only two
buckets; it does not by itself split the automatic bucket further. A second,
purely mechanical test does that: an automatic surface is `kernel` when it
renders a FIXED corpus on every subagent dispatch (the same `type=user AND
audience=executor` rows, regardless of what the dispatch is about), and
`injection` when the rendered rows vary with what the caller's window
actually selects (`get-relevant`'s digest/sections/types). Splitting on that
axis is what keeps the dispatch-kernel's fixed rows from dominating a
demand ranking by construction. These are the places code applies both tests
today, and a new surface is classified by them rather than added to this
list:

| Call site | Symbol | Kind |
|---|---|---|
| `get-relevant` (no flag), `--sections=`, `--types=` | `_bump_injection_telemetry` (`bin/cli/memory.py`) | injection |
| Subagent kernel's "How the user works" block | `_record_kernel_telemetry` (`hooks/modules/context/kernel_builder.py`) | kernel |
| `memory show <slug>`, every mode including `--links`/`--history` | `_cmd_curated_show` (`bin/cli/memory.py`) | deliberate |
| `memory story <slug>`, the seed row alone | `_cmd_story` (`bin/cli/memory_story.py`) | deliberate |
| `get-relevant --initiative=<key>`, text and JSON alike | `_render_project_mode` (`bin/cli/memory.py`) | deliberate |

Whatever a window returns writes nothing, whatever it renders of each row:
`search`, `list`, `stats`, `conflicts`, and `gaia query` in every one of its
modes -- table, `--json`, `--count`, `--group-by`. `gaia query` is the
substrate's event reader, auditing memory, episodes and the hook log in one
call; it never names a row, so no shape of its output is a read of one. The
lineage a `story` BFS discovers around its seed is the same case: the caller
named the seed, not what the walk reached from it.

`tests/integration/test_memory_access_telemetry_surfaces.py` pins every CLI
row of that table by running the real command and measuring the counters it
moved, discovering the surface set from the argument parser rather than from
a list -- so a new subcommand or a new flag on a read subcommand fails it
until classified. The kernel row has no subcommand to run: it is context
assembly, not a `gaia memory` verb, so it is pinned instead by two dedicated
recipes that invoke `build_memory_block` directly --
`test_kernel_memory_block_counts_the_rows_it_renders` and
`test_kernel_dispatch_and_context_digest_move_disjoint_axes_on_the_same_row`
(the latter proves the kernel and injection axes move independently on one
row both can reach) -- plus
`test_every_bump_call_site_belongs_to_a_classified_surface`, which scans the
source for `record_memory_access` call sites so a bump wired into an
unclassified module fails too.

Two surfaces read the counters back, and only two of the three pairs:
`gaia memory show <slug>` prints the injection and deliberate pairs on their
own lines (`injection_count`/`last_injected_at`,
`deliberate_count`/`last_deliberate_at`); `gaia memory list` prints an `INJ`
and a `DELIB` column and takes `--sort=injection` or `--sort=deliberate`
(`_cmd_list` -> `gaia.store.writer.list_memory`, `_MEMORY_LIST_ORDERS`), with
`--order=asc|desc` choosing the direction -- default `desc` on a counter,
`asc` on `--sort=name` (`_MEMORY_LIST_DEFAULT_DIRECTIONS`), ties always broken
by name ascending. `--order=asc` on a counter is how "which rows are barely
used" is asked without reading the tail of an untopped list. Neither surface
ever combines two counters into one number or one sort key -- the same
never-merge rule as the write side. `kernel_count`/`last_kernel_at` are
written (see the call-site table above) but neither surface projects them --
`gaia/store/writer.py::get_memory` and `gaia/store/writer.py::list_memory`'s own `SELECT`s name only the injection
and deliberate columns -- so today the kernel pair is readable only by a
direct query against the `memory` table, never through `show` or `list`.

Reading them back is their only consumer, for the two pairs that are read
back at all. Automatic SessionStart selection -- `get-relevant`'s digest,
`--sections=`, `--types=`, and the kernel's "How the user works" block --
still ignores all three counters and orders by `updated_at` alone,
unchanged. Wiring injection SELECTION itself to any counter remains a
separate, undecided step.
